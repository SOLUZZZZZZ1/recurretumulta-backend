"""Connected, disabled-by-default quote/client/balance/draft HTTP contract."""
import os
from contextlib import contextmanager
from uuid import UUID
from typing import Literal
from fastapi import APIRouter,Depends,Request,HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from database import get_engine
from rtm_core.casework_router import require_casework,HEADERS
from rtm_core.management_contract import ManagementRejected,QuoteInput,study_requirements
from rtm_core.management_policy import configuration
from rtm_core.management_store import (quotes,save_quote,accept_quote,reconcile,quote_row,
    quote_projection,obj)
from rtm_core.management_payments import (receipt,record_receipt,obligation,reserve,unknown,
    apply_provider_read,public_payment)
from rtm_core.management_stripe import StripeAdapter
from rtm_core.management_drafts import DraftInput,save as save_draft,versions,version,requirements
from rtm_core.management_mail import prepare_quote_message,verified_recipient
from rtm_core.case_mail_runtime import B2MailStorage,configured_mailbox
from rtm_core.case_mail import MailRejected
from rtm_core.paid_study import read_history
from rtm_core import management_client_access as client
from rtm_core.casework_policy import origin_allowed
from rtm_core.management_generation import projection as generation_projection, retry as retry_generation
from rtm_core.invoice_requests import (InvoiceDetails, record_request as record_invoice_request,
    cancel_request as cancel_invoice_request)


class ManagementRoute(APIRoute):
    def get_route_handler(self):
        handler=super().get_route_handler()
        async def private(request):
            try:response=await handler(request)
            except ManagementRejected as exc:response=JSONResponse({'detail':{'code':exc.code}},status_code=exc.status)
            except RequestValidationError:response=JSONResponse({'detail':{'code':'MANAGEMENT_REQUEST_INVALID'}},status_code=422)
            except HTTPException as exc:response=JSONResponse({'detail':exc.detail},status_code=exc.status_code)
            except MailRejected:response=JSONResponse({'detail':{'code':'MAIL_CONFIGURATION_OR_CONTENT_UNAVAILABLE'}},status_code=503)
            except SQLAlchemyError:response=JSONResponse({'detail':{'code':'MANAGEMENT_STORAGE_UNAVAILABLE'}},status_code=503)
            except Exception:response=JSONResponse({'detail':{'code':'MANAGEMENT_UNAVAILABLE'}},status_code=503)
            response.headers.update(HEADERS)
            return response
        return private


router=APIRouter(tags=['management-journey'],route_class=ManagementRoute)
OPS='/ops/core/cases/{case_id}/management'
CLIENT='/client/management'


@contextmanager
def connection():
    engine=get_engine()
    try:
        with engine.begin() as conn:yield conn
    finally:engine.dispose()


def individual(context=Depends(require_casework)):
    policy=configuration()
    if context.case_id!=policy.case_id:raise ManagementRejected('MANAGEMENT_SCOPE_DENIED',403)
    return context,policy


@router.get(OPS)
def overview(access=Depends(individual)):
    context,policy=access
    quote=quote_row(context.conn,context.case_id)
    initial=receipt(context.conn,context.case_id,policy.mode,'initial_study')
    try:recipient=verified_recipient(context.conn,context.case_id)
    except ManagementRejected:recipient=None
    return {'ok':True,'case_id':context.case_id,'quotes':quotes(context.conn,context.case_id),
        'initial_payment':{'state':'provider_verified' if initial else 'verification_required',
            'amount_cents':initial['amount_cents'] if initial else None,'currency':'eur','mode':policy.mode},
        'balance':public_payment(obligation(context.conn,case_id=context.case_id,quote_id=str(quote['id'])) if quote else None),
        'drafts':versions(context.conn,context.case_id),'draft_requirements':requirements(context.conn,context.case_id,policy),
        'generation':generation_projection(context.conn,context.case_id),
        'study_requirements':study_requirements(read_history(context.conn,context.case_id)['latest']),
        'verified_recipient':recipient,'agreed_total_cents':policy.agreed_total_cents,
        'tax_basis_points':policy.tax_basis_points,'tax_label':policy.tax_label,'presented':False}


@router.post(OPS+'/quotes')
def prepare_quote(body:QuoteInput,access=Depends(individual)):
    context,policy=access
    case=context.conn.execute(text('SELECT id,payment_status,product_code,stripe_session_id,stripe_payment_intent FROM cases WHERE id=:id'),
        {'id':context.case_id}).mappings().one()
    # Read-only provider verification. Never invokes legacy Checkout or changes cases.
    proof=StripeAdapter(policy).initial_receipt(dict(case))
    admitted=record_receipt(context.conn,proof)
    return {'ok':True,**save_quote(context.conn,case_id=context.case_id,author=context.session,body=body,receipt=admitted,policy=policy)}


class MessageInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    operation_id:UUID
    quote_id:UUID
    quote_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')


@router.post(OPS+'/quote-message')
def prepare_message(body:MessageInput,access=Depends(individual)):
    context,policy=access
    return {'ok':True,**prepare_quote_message(context.conn,case_id=context.case_id,quote_id=str(body.quote_id),
        expected_sha=body.quote_sha256,operation_id=str(body.operation_id),author=context.session,
        storage=B2MailStorage(),mailbox=configured_mailbox(),origin=policy.origin)}


@router.post(OPS+'/drafts')
def write_draft(body:DraftInput,access=Depends(individual)):
    context,policy=access
    return {'ok':True,**save_draft(context.conn,case_id=context.case_id,author=context.session,body=body,policy=policy)}


@router.get(OPS+'/drafts/{version_id}')
def read_draft(version_id:UUID,access=Depends(individual)):
    context,_=access;result=version(context.conn,context.case_id,str(version_id))
    if not result:raise ManagementRejected('DRAFT_NOT_FOUND_IN_CASE',404)
    return {'ok':True,'version':result}


@router.post(OPS+'/generation/{job_id}/retry')
def resume_generation(job_id:UUID,access=Depends(individual)):
    context,policy=access
    return {'ok':True,'generation':retry_generation(context.conn,context.case_id,str(job_id),policy,context.session.operator_id)}


@router.post(OPS+'/operations/{kind}/{operation_id}/reconcile')
def reconcile_write(kind:Literal['quote','draft'],operation_id:UUID,access=Depends(individual)):
    context,_=access
    return {'ok':True,**reconcile(context.conn,context.case_id,str(operation_id),kind,'operator:'+context.session.operator_id)}


class LinkRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    case_id:UUID
    email:str=Field(min_length=3,max_length=254)


class RedeemInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    token:str=Field(min_length=43,max_length=43)


def public_policy(request:Request):
    policy=configuration()
    if request.method not in {'GET','HEAD'} and request.headers.get('origin')!=policy.origin:
        raise ManagementRejected('CLIENT_ORIGIN_DENIED',403)
    return policy


@router.post(CLIENT+'/access')
def request_access(body:LinkRequest,request:Request,policy=Depends(public_policy)):
    with connection() as conn:
        client.request_link(conn,case_id=str(body.case_id),email=body.email,
            peer=request.client.host if request.client else 'unknown',policy=policy,
            storage=B2MailStorage(),mailbox=configured_mailbox())
    return {'ok':True,'message':client.NEUTRAL}


@router.post(CLIENT+'/redeem')
def redeem_access(body:RedeemInput,request:Request,policy=Depends(public_policy)):
    with connection() as conn:
        if not client.quota(conn,'redeem:'+(request.client.host if request.client else 'unknown'),__import__('time').time(),20,60):
            raise ManagementRejected('CLIENT_RATE_LIMIT',429)
    with connection() as conn:secret=client.redeem(conn,token=body.token,previous=request.cookies.get(client.COOKIE),policy=policy)
    response=JSONResponse({'ok':True,'case_id':policy.case_id})
    response.set_cookie(client.COOKIE,secret,max_age=client.SESSION_TTL,httponly=True,secure=True,samesite='strict',path='/')
    return response


class LogoutInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    session_ref:str=Field(pattern=r'^[0-9a-f]{64}$')


@router.post(CLIENT+'/logout')
def logout_client(body:LogoutInput,request:Request):
    # Revocation remains possible when new sales/access have been disabled or
    # the pilot expires. It cannot create a session or extend any permission.
    configured_origin=os.environ.get('RTM_CASEWORK_PUBLIC_ORIGIN')
    if (not origin_allowed(os.environ, configured_origin) or request.headers.get('origin')!=configured_origin):
        raise ManagementRejected('CLIENT_ORIGIN_DENIED',403)
    with connection() as conn:return client.logout(conn,token=request.cookies.get(client.COOKIE),expected_ref=body.session_ref)


def client_context(case_id:str,request:Request,policy=Depends(public_policy)):
    if str(UUID(case_id))!=case_id:raise ManagementRejected('CLIENT_CASE_INVALID',422)
    with connection() as conn:
        grant=client.session(conn,token=request.cookies.get(client.COOKIE),case_id=case_id,policy=policy)
        yield conn,grant,policy,case_id


@router.get(CLIENT+'/cases/{case_id}')
def read_client(access=Depends(client_context)):
    conn,grant,policy,case_id=access
    row=quote_row(conn,case_id)
    quote=quote_projection(row) if row and row['state'] in {'communicated','accepted','settled'} else None
    if quote:
        # Deliberate client projection: no operator IDs, internal study text,
        # document custody coordinates, provider IDs or pending private drafts.
        q=quote['snapshot']
        quote={'id':q['id'],'revision':q['revision'],'sha256':quote['sha256'],'state':row['state'],
            **{k:q[k] for k in ('title','scope','exclusions','client_summary','economics','expires_at','mode')},
            'document_type':q.get('document_type')}
    initial=receipt(conn,case_id,policy.mode,'initial_study')
    return {'ok':True,'case_id':case_id,'session_ref':grant['session_ref'],'expires_at':grant['expires_at'],
        'quote':quote,'initial_study':{'state':'provider_verified' if initial else 'verification_required',
            'amount_cents':initial['amount_cents'] if initial else None,'currency':'eur'},
        'balance':public_payment(obligation(conn,case_id=case_id,quote_id=str(row['id'])) if quote else None),
        'generation':generation_projection(conn,case_id,public=True),'presented':False}


class AcceptanceInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    operation_id:UUID
    quote_id:UUID
    quote_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    accept_exact_version:Literal[True]
    expected_session_ref:str=Field(pattern=r'^[a-f0-9]{64}$')


def exact_client_session(body,grant):
    import hmac
    if not hmac.compare_digest(body.expected_session_ref,grant['session_ref']):
        raise ManagementRejected('CLIENT_SESSION_CHANGED_REVIEW_REQUIRED',403)


@router.post(CLIENT+'/cases/{case_id}/accept')
def accept(body:AcceptanceInput,access=Depends(client_context)):
    conn,grant,_,case_id=access
    exact_client_session(body,grant)
    return {'ok':True,**accept_quote(conn,case_id=case_id,quote_id=str(body.quote_id),expected_sha=body.quote_sha256,
        operation_id=str(body.operation_id),recipient_hash=grant['recipient_hash'],confirmed=body.accept_exact_version)}


class CheckoutInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    quote_id:UUID
    quote_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    expected_session_ref:str=Field(pattern=r'^[a-f0-9]{64}$')
    invoice_requested:bool=False
    invoice_data:InvoiceDetails|None=None


def reconcile_provider(policy,row,*,create=False,retire=False):
    adapter=StripeAdapter(policy)
    try:
        if retire and row['stripe_session_id']:session=adapter.expire(row)
        elif row['stripe_session_id']:session=adapter.retrieve(row['stripe_session_id'])
        elif create:session=adapter.create(row)
        else:raise ManagementRejected('CHECKOUT_UNKNOWN_READ_RECONCILIATION_REQUIRED')
    except ManagementRejected:
        with connection() as conn:unknown(conn,row)
        raise
    with connection() as conn:return apply_provider_read(conn,row,session,provider_account_id=policy.account_id)


@router.post(CLIENT+'/cases/{case_id}/checkout')
def checkout(body:CheckoutInput,access=Depends(client_context)):
    conn,grant,policy,case_id=access
    exact_client_session(body,grant)
    if body.invoice_requested!=(body.invoice_data is not None):
        raise ManagementRejected('INVOICE_DETAILS_INVALID',422)
    row=reserve(conn,case_id=case_id,quote_id=str(body.quote_id),expected_sha=body.quote_sha256,
                recipient_hash=grant['recipient_hash'],policy=policy)
    if body.invoice_requested:
        record_invoice_request(conn,case_id=case_id,stage='management_balance',reference_id=str(body.quote_id),
                               data=body.invoice_data.model_dump(),recipient_email=verified_recipient(conn,case_id))
    else:
        cancel_invoice_request(conn,case_id=case_id,stage='management_balance',reference_id=str(body.quote_id))
    conn.commit()  # Durable reservation BEFORE any external creation; no DB lock during Stripe.
    if row['state'] in {'confirmed','review_required','expired','retiring'}:
        return {'ok':True,'balance':public_payment(row)}
    return {'ok':True,'balance':reconcile_provider(policy,row,create=True)}


@router.post(CLIENT+'/cases/{case_id}/payment-status')
def payment_status(access=Depends(client_context)):
    conn,_,policy,case_id=access
    quote=quote_row(conn,case_id)
    row=obligation(conn,case_id=case_id,quote_id=str(quote['id'])) if quote else None
    conn.commit()
    return {'ok':True,'balance':reconcile_provider(policy,row) if row else public_payment(None)}


@router.post(OPS+'/checkout/{obligation_id}/retire')
def retire_checkout(obligation_id:UUID,access=Depends(individual)):
    context,policy=access
    row=obligation(context.conn,case_id=context.case_id,obligation_id=str(obligation_id))
    if not row:raise ManagementRejected('OBLIGATION_NOT_FOUND_IN_CASE',404)
    context.conn.commit()
    # Stripe expiry can lose a race to payment. A paid result remains paid.
    return {'ok':True,'balance':reconcile_provider(policy,row,retire=True)}


@router.post('/billing/management/webhook/{mode}')
async def balance_webhook(mode:Literal['test','live'],request:Request):
    policy=configuration(for_notification=True,mode=mode);adapter=StripeAdapter(policy)
    raw=await request.body()
    notice=adapter.webhook(raw,request.headers.get('stripe-signature',''))
    if notice is None:return {'ok':True,'ignored':True}
    eid,oid,sid=notice
    with connection() as conn:row=obligation(conn,obligation_id=oid,case_id=policy.case_id)
    if not row:raise ManagementRejected('WEBHOOK_OBLIGATION_NOT_FOUND',409)
    session=adapter.retrieve(sid)
    with connection() as conn:
        result=apply_provider_read(conn,row,session,provider_account_id=policy.account_id,event_id=eid)
    return {'ok':True,'balance_state':result['state']}
