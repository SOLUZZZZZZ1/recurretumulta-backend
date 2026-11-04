"""Per-request Stripe adapter, adapted from October pilot_payments.

Never assigns global stripe.api_key; never charges a saved payment method.
Only the client creates/uses a hosted Checkout. Read results are verified before
any ledger effect. Neither browser returns nor raw webhook claims settle money.
"""
from datetime import datetime, timezone
import time
import re
from urllib.parse import urlsplit
from uuid import uuid4
from rtm_core.management_contract import ManagementRejected, digest

PAYMENT_CONTRACT = 'rtm_management_balance_v1'


def plain(value):
    if isinstance(value,dict):return {k:plain(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [plain(v) for v in value]
    return value


def check_paid(session, *, mode, amount, expected_session_id=None):
    prefix = 'cs_' + mode + '_'
    if (session.get('livemode') is not (mode == 'live') or session.get('mode') != 'payment'
            or not str(session.get('id', '')).startswith(prefix)
            or (expected_session_id and session['id'] != expected_session_id)
            or session.get('status') != 'complete' or session.get('payment_status') != 'paid'
            or session.get('currency') != 'eur' or type(session.get('amount_total')) is not int
            or session['amount_total'] != amount):
        raise ManagementRejected('PROVIDER_PAYMENT_NOT_CONFIRMED')
    intent = session.get('payment_intent')
    if (not isinstance(intent, dict) or intent.get('livemode') is not (mode == 'live')
            or intent.get('status') != 'succeeded' or intent.get('currency') != 'eur'
            or type(intent.get('amount_received')) is not int or intent['amount_received'] != amount
            or not re.fullmatch(r'pi_[A-Za-z0-9]+', str(intent.get('id', '')))):
        raise ManagementRejected('PROVIDER_INTENT_NOT_CONFIRMED')
    charge = intent.get('latest_charge')
    if (not isinstance(charge, dict) or charge.get('livemode') is not (mode == 'live')
            or charge.get('paid') is not True or charge.get('refunded') is not False
            or charge.get('disputed') is not False or charge.get('amount_refunded') != 0
            or type(charge.get('amount')) is not int or charge['amount'] != amount
            or charge.get('currency') != 'eur' or charge.get('payment_intent') != intent['id']
            or not re.fullmatch(r'ch_[A-Za-z0-9]+', str(charge.get('id', '')))):
        raise ManagementRejected('PROVIDER_CHARGE_NOT_CONFIRMED')
    return intent, charge


def obligation_metadata(row):
    return {'contract': PAYMENT_CONTRACT, 'management_case_id': str(row['case_id']),
            'quote_id': str(row['quote_id']), 'obligation_id': str(row['id']),
            'payment_mode': row['mode'], 'material_sha256': row['material_sha256'],
            'stage': 'management_balance'}


def check_session(session, row, *, paid=False):
    if (session.get('livemode') is not (row['mode']=='live')
            or not str(session.get('id', '')).startswith('cs_'+row['mode']+'_')
            or session.get('mode') != 'payment' or session.get('currency') != 'eur'
            or type(session.get('amount_total')) is not int or session['amount_total'] != row['amount_cents']
            or session.get('metadata') != obligation_metadata(row)
            or session.get('client_reference_id') != str(row['id'])
            or (row.get('stripe_session_id') and session['id'] != row['stripe_session_id'])):
        raise ManagementRejected('BALANCE_SESSION_MISMATCH')
    if paid:
        intent, charge = check_paid(session, mode=row['mode'], amount=row['amount_cents'])
        if intent.get('metadata') != obligation_metadata(row):
            raise ManagementRejected('BALANCE_INTENT_MISMATCH')
        return intent, charge


def checkout_url(session):
    try:
        parsed=urlsplit(session.get('url') or '')
        if (session.get('status')!='open' or parsed.scheme!='https' or parsed.hostname!='checkout.stripe.com'
                or parsed.username or parsed.password or parsed.port or not parsed.path.startswith('/c/')):
            raise ValueError()
        return session['url']
    except ValueError:
        raise ManagementRejected('CHECKOUT_URL_UNVERIFIED') from None


class StripeAdapter:
    def __init__(self, config, sdk=None):
        if sdk is None:
            import stripe as sdk
        self.config, self.sdk = config, sdk

    def account(self):
        try:
            account=plain(self.sdk.Account.retrieve(api_key=self.config.api_key))
        except Exception:
            raise ManagementRejected('STRIPE_ACCOUNT_READ_UNAVAILABLE',503) from None
        if account.get('id')!=self.config.account_id:
            raise ManagementRejected('STRIPE_ACCOUNT_MISMATCH',503)

    def retrieve(self, session_id):
        self.account()
        if not str(session_id).startswith('cs_'+self.config.mode+'_'):
            raise ManagementRejected('STRIPE_MODE_MISMATCH')
        try:
            return plain(self.sdk.checkout.Session.retrieve(session_id,api_key=self.config.api_key,
                                                           expand=['payment_intent.latest_charge']))
        except Exception:
            raise ManagementRejected('STRIPE_SESSION_READ_UNAVAILABLE',503) from None

    def initial_receipt(self, case):
        if case.get('payment_status')!='paid' or case.get('product_code')!='REVIEW_BASIC':
            raise ManagementRejected('STUDY_PAYMENT_NOT_RECORDED')
        session=self.retrieve(case.get('stripe_session_id'))
        intent,charge=check_paid(session,mode=self.config.mode,amount=1000,expected_session_id=case.get('stripe_session_id'))
        metadata=session.get('metadata',{})
        if (metadata.get('case_id')!=str(case['id']) or metadata.get('billing_code')!='REVIEW_BASIC'
                or metadata.get('payment_stage')!='review'
                or case.get('stripe_payment_intent')!=intent['id']):
            raise ManagementRejected('STUDY_PROVIDER_CASE_LINK_MISMATCH')
        proof={'id':str(uuid4()),'case_id':str(case['id']),'stage':'initial_study','mode':self.config.mode,
            'account_id':self.config.account_id,'session_id':session['id'],'payment_intent_id':intent['id'],
            'charge_id':charge['id'],'amount_cents':1000,'currency':'eur','provider_status':'succeeded',
            'refunded_cents':0,'disputed':False,'verified_at':datetime.now(timezone.utc).isoformat()}
        return {**proof,'evidence_sha256':digest(proof)}

    def create(self, row):
        self.account()
        reservation=row['reservation']
        # Idempotency may be pruned by Stripe after 24h. Never create again beyond
        # this shorter window: a missing response then requires read reconciliation.
        if time.time()-reservation['created_epoch']>=23*3600:
            raise ManagementRejected('CHECKOUT_UNKNOWN_READ_RECONCILIATION_REQUIRED')
        params=reservation['provider_parameters']
        metadata=obligation_metadata(row)
        expected_items=[{'quantity':1,'price_data':{'currency':'eur','unit_amount':row['amount_cents'],
            'product_data':{'name':params.get('line_items',[{}])[0].get('price_data',{}).get('product_data',{}).get('name','')}}}]
        if (digest(params)!=reservation.get('provider_parameters_sha256') or
                params.get('line_items')!=expected_items or params.get('metadata')!=metadata or
                params.get('payment_intent_data')!={'metadata':metadata} or params.get('client_reference_id')!=row['id'] or
                params.get('mode')!='payment' or params.get('payment_method_types')!=['card'] or
                params.get('success_url')!=self.config.origin+'/#/cliente/expediente/'+row['case_id']+'?pago=consultar' or
                params.get('cancel_url')!=self.config.origin+'/#/cliente/expediente/'+row['case_id']+'?pago=volver' or
                set(params)!={'line_items','metadata','payment_intent_data','client_reference_id','mode','payment_method_types','success_url','cancel_url','expires_at'}):
            raise ManagementRejected('CHECKOUT_RESERVATION_INTEGRITY_FAILED',503)
        try:
            session=plain(self.sdk.checkout.Session.create(api_key=self.config.api_key,
                idempotency_key=row['idempotency_key'],**params))
        except Exception:
            raise ManagementRejected('CHECKOUT_RESULT_UNKNOWN',503) from None
        check_session(session,row)
        return session

    def expire(self, row):
        session=self.retrieve(row['stripe_session_id']);check_session(session,row)
        if session.get('status')=='open':
            try:
                self.sdk.checkout.Session.expire(row['stripe_session_id'],api_key=self.config.api_key,
                    idempotency_key='expire:'+row['idempotency_key'])
            except Exception:
                # A completed payment can win this race. Re-read before deciding.
                pass
            session=self.retrieve(row['stripe_session_id']);check_session(session,row)
        return session

    def webhook(self, raw, signature):
        if len(raw)>65536:
            raise ManagementRejected('WEBHOOK_TOO_LARGE',413)
        try:
            event=plain(self.sdk.Webhook.construct_event(raw,signature,self.config.webhook_secret,tolerance=300))
        except Exception:
            raise ManagementRejected('WEBHOOK_SIGNATURE_INVALID',400) from None
        if event.get('livemode') is not (self.config.mode=='live') or event.get('account'):
            raise ManagementRejected('WEBHOOK_MODE_OR_ACCOUNT_MISMATCH',400)
        if event.get('type') not in {'checkout.session.completed','checkout.session.async_payment_succeeded',
                                    'checkout.session.async_payment_failed','checkout.session.expired'}:
            return None
        session=event.get('data',{}).get('object',{})
        if session.get('metadata',{}).get('contract')!=PAYMENT_CONTRACT:
            return None
        return event['id'],session.get('metadata',{}).get('obligation_id'),session.get('id')
