"""Individual, case-scoped conversation API; no inline SMTP or public attachments."""
import os
from uuid import UUID,uuid4
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Query,Request,Header
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import text
from fastapi.responses import Response
from rtm_core.casework_router import CaseworkRoute,require_casework
from rtm_core.case_mail import MailRejected,address,extract_attachment
from rtm_core.case_mail_repository import conversation,prepare,queue,message_for_case,public_message,obj,assign_pending
from rtm_core.case_mail_runtime import B2MailStorage,configured_mailbox
from rtm_core.document_viewer import render

router=APIRouter(prefix='/ops/core/cases',tags=['case-communications'],route_class=CaseworkRoute)

def enabled(context=Depends(require_casework)):
    if os.environ.get('RTM_ENABLE_CASE_MAIL')!='1':
        raise HTTPException(503,{'code':'CASE_MAIL_NOT_ENABLED'})
    return context

class Compose(BaseModel):
    model_config=ConfigDict(extra='forbid')
    operation_id:UUID
    recipient:str=Field(min_length=3,max_length=254)
    subject:str=Field(min_length=1,max_length=200)
    body:str=Field(min_length=1,max_length=50000)
    reply_id:UUID|None=None

class Approval(BaseModel):
    model_config=ConfigDict(extra='forbid')
    approve_exact_message:Literal[True]
    original_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')

class Assignment(BaseModel):
    model_config=ConfigDict(extra='forbid')
    original_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    independently_verified:Literal[True]
    evidence:str=Field(min_length=20,max_length=2000)

class ParticipantVerification(BaseModel):
    model_config=ConfigDict(extra='forbid')
    email:str=Field(min_length=3,max_length=254)
    independently_verified:Literal[True]
    verification_reference:str=Field(min_length=20,max_length=500)

@router.post('/{case_id}/communications/participants/verify')
def verify_participant(payload:ParticipantVerification,context=Depends(require_casework)):
    # This audited supervisor action is separate from enabling mail transport.
    # The address must match both the saved case contact and the controlled pilot.
    if os.environ.get('RTM_CASE_MAIL_DEDICATED_CONFIRMED')!='1':
        raise HTTPException(503,{'code':'MAILBOX_SCOPE_NOT_CONFIRMED'})
    try:email=address(payload.email)
    except MailRejected:raise HTTPException(422,{'code':'MAIL_ADDRESS_INVALID'}) from None
    reference=payload.verification_reference.strip()
    if len(reference)<20:raise HTTPException(422,{'code':'MAIL_VERIFICATION_REFERENCE_REQUIRED'})
    allowed={value.strip().lower() for value in os.environ.get('RTM_CASE_MAIL_ALLOWED_RECIPIENTS','').split(',') if value.strip()}
    if email not in allowed:raise HTTPException(403,{'code':'MAIL_RECIPIENT_OUTSIDE_CONTROLLED_PILOT'})
    row=context.conn.execute(text('SELECT contact_email,payment_status,paid_at,authorized FROM cases WHERE id=:case'),
        {'case':context.case_id}).mappings().one_or_none()
    if not row:raise HTTPException(404,{'code':'CASEWORK_CASE_NOT_FOUND'})
    try:saved=address(row['contact_email'] or '')
    except MailRejected:raise HTTPException(409,{'code':'CASE_CONTACT_EMAIL_INVALID'}) from None
    if email!=saved:raise HTTPException(409,{'code':'MAIL_RECIPIENT_NOT_CASE_CONTACT'})
    if row['payment_status']!='paid' or row['paid_at'] is None or not bool(row['authorized']):
        raise HTTPException(409,{'code':'MAIL_PARTICIPANT_CASE_PREREQUISITES_NOT_MET'})
    lock=' FOR UPDATE' if context.conn.dialect.name=='postgresql' else ''
    current=context.conn.execute(text('SELECT id,active FROM rtm_case_mail_participants WHERE case_id=:case AND email=:email'+lock),
        {'case':context.case_id,'email':email}).mappings().one_or_none()
    if current:
        if current['active']:
            return {'ok':True,'case_id':context.case_id,'email':email,'already_verified':True}
        raise HTTPException(409,{'code':'MAIL_PARTICIPANT_INACTIVE_REVIEW_REQUIRED'})
    context.conn.execute(text('''INSERT INTO rtm_case_mail_participants
        (id,case_id,email,verified_at,verified_by,verification_reference,active)
        VALUES (:id,:case,:email,CURRENT_TIMESTAMP,:operator,:reference,true)'''),
        {'id':str(uuid4()),'case':context.case_id,'email':email,
         'operator':context.session.operator_id,'reference':reference})
    return {'ok':True,'case_id':context.case_id,'email':email,'already_verified':False}

@router.post('/{case_id}/communications/{message_id}/assign')
def assign_message(message_id:UUID,payload:Assignment,context=Depends(enabled)):
    return {'ok':True,**assign_pending(context.conn,context.case_id,str(message_id),context.session,
        payload.evidence,payload.original_sha256)}

@router.get('/{case_id}/communications')
def read_conversation(q:str=Query('',max_length=200),context=Depends(enabled)):
    return conversation(context.conn,context.case_id,q,mailbox=configured_mailbox())

@router.get('/{case_id}/communications/{message_id}')
def read_message(message_id:UUID,context=Depends(enabled)):
    return {'ok':True,**public_message(message_for_case(context.conn,context.case_id,str(message_id)))}

@router.post('/{case_id}/communications')
def prepare_message(payload:Compose,context=Depends(enabled)):
    try:
        result=prepare(context.conn,case_id=context.case_id,session=context.session,
            operation_id=str(payload.operation_id),recipient=payload.recipient,
            subject=payload.subject,body=payload.body,reply_id=str(payload.reply_id) if payload.reply_id else None,
            storage=B2MailStorage(),mailbox=configured_mailbox())
    except MailRejected as exc:
        raise HTTPException(422,{'code':str(exc)}) from None
    return {'ok':True,**result}

@router.post('/{case_id}/communications/{message_id}/queue')
def queue_message(message_id:UUID,payload:Approval,context=Depends(enabled)):
    row=message_for_case(context.conn,context.case_id,str(message_id))
    relation=obj(row['relation'])
    if relation.get('type')=='management_quote':
        from rtm_core.management_mail import approve_quote_message
        return {'ok':True,**approve_quote_message(context.conn,case_id=context.case_id,message_id=str(message_id),
            original_sha256=payload.original_sha256,author=context.session)}
    if relation.get('type')=='client_access':raise HTTPException(409,{'code':'CLIENT_ACCESS_NOT_OPERATOR_RESENDABLE'})
    if obj(row['payload'])['raw_sha256']!=payload.original_sha256:
        raise HTTPException(409,{'code':'MAIL_APPROVAL_CONTENT_MISMATCH'})
    return {'ok':True,**queue(context.conn,context.case_id,str(message_id),context.session)}

def checked_attachment(message_id,part,context):
    row=message_for_case(context.conn,context.case_id,str(message_id))
    attachment=next((a for a in obj(row['payload'])['attachments'] if a['part']==part),None)
    if not attachment:raise HTTPException(404,{'code':'MAIL_ATTACHMENT_NOT_FOUND'})
    check=context.conn.execute(text('''SELECT state FROM rtm_case_mail_attachment_checks
        WHERE message_id=:id AND part=:part AND sha256=:sha'''),
        {'id':str(message_id),'part':part,'sha':attachment['sha256']}).scalar()
    if check!='clean':raise HTTPException(409,{'code':'MAIL_ATTACHMENT_QUARANTINED'})
    original=context.conn.execute(text('SELECT custody_key,sha256 FROM rtm_case_mail_originals WHERE message_id=:id AND sha256=:sha'),
        {'id':str(message_id),'sha':obj(row['payload'])['raw_sha256']}).mappings().first()
    if not original:raise HTTPException(503,{'code':'MAIL_ORIGINAL_UNAVAILABLE'})
    raw=B2MailStorage(row['mailbox']).get(original['custody_key'],original['sha256'])
    data,meta=extract_attachment(raw,part,attachment['sha256'])
    return data,meta

@router.get('/{case_id}/communications/{message_id}/attachments/{part}/view')
def view_attachment(message_id:UUID,part:int,context=Depends(enabled)):
    data,meta=checked_attachment(message_id,part,context)
    if meta['mime']=='text/plain':
        from rtm_core.case_mail_attachments import inspect
        inspect(data,meta['mime'])
        return {'ok':True,'case_id':context.case_id,'document_id':str(message_id)+':'+str(part),
            'title':meta['name'],'mime':'text/plain','text':data.decode('utf-8'),'observed_sha256':meta['sha256'],
            'view_only':True,'operator_export_allowed':False}
    return {'ok':True,'contract':'rtm_ops_document_view_v1','case_id':context.case_id,
        'document_id':str(message_id)+':'+str(part),'title':meta['name'],
        'observed_sha256':meta['sha256'],'recorded_hash_state':'verified',
        'view_only':True,'operator_export_allowed':False,**render(data,meta['mime'])}

@router.get('/{case_id}/communications/{message_id}/attachments/{part}/pages/{page}')
def read_attachment_page(message_id:UUID,part:int,page:int,
        if_match:str|None=Header(None,alias='If-Match'),context=Depends(enabled)):
    data,meta=checked_attachment(message_id,part,context)
    if if_match!='"'+meta['sha256']+'"':raise HTTPException(412,{'code':'MAIL_ATTACHMENT_HASH_MISMATCH'})
    return Response(render(data,meta['mime'],page=page),media_type='image/png',
        headers={'Content-Disposition':f'inline; filename="attachment-page-{page}.png"'})

