"""Customer invoice requests, kept outside Stripe and linked to a payment stage."""
import json
from sqlalchemy import text
from pydantic import BaseModel, ConfigDict, Field, field_validator
from rtm_core.management_contract import ManagementRejected


class InvoiceDetails(BaseModel):
    model_config=ConfigDict(extra='forbid')
    legal_name:str=Field(min_length=1,max_length=200)
    tax_id:str=Field(min_length=1,max_length=40)
    address:str=Field(min_length=1,max_length=240)
    postal_code:str=Field(min_length=1,max_length=20)
    city:str=Field(min_length=1,max_length=120)
    province:str=Field(min_length=1,max_length=120)
    country:str=Field(min_length=2,max_length=2)

    @field_validator('*',mode='before')
    @classmethod
    def trim_values(cls,value):
        return value.strip() if isinstance(value,str) else value

    @field_validator('country')
    @classmethod
    def validate_country(cls,value):
        value=value.upper()
        if not value.isalpha():raise ValueError('ISO country code required')
        return value


def normalize_invoice_data(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ManagementRejected('INVOICE_DETAILS_INVALID', 422)
    keys=('legal_name','tax_id','address','postal_code','city','province','country')
    result={key:' '.join(str(value.get(key,'')).split()) for key in keys}
    limits={'legal_name':200,'tax_id':40,'address':240,'postal_code':20,'city':120,'province':120,'country':2}
    if any(not result[key] or len(result[key])>limits[key] for key in keys):
        raise ManagementRejected('INVOICE_DETAILS_INVALID',422)
    if len(result['country'])!=2 or not result['country'].isalpha():
        raise ManagementRejected('INVOICE_COUNTRY_CODE_INVALID',422)
    result['country']=result['country'].upper()
    return result


def record_request(conn, *, case_id, stage, reference_id, data, recipient_email):
    """Insert once; retries must match the originally supplied fiscal identity."""
    snapshot=normalize_invoice_data(data)
    if snapshot is None:
        return None
    serialized=json.dumps(snapshot,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    email=' '.join(str(recipient_email or '').split())
    if len(email)>320 or '@' not in email:
        raise ManagementRejected('INVOICE_EMAIL_INVALID',422)
    row=conn.execute(text('SELECT id,fiscal_data,state,stripe_session_id FROM rtm_invoice_requests '
        'WHERE case_id=:case AND stage=:stage AND reference_id=:reference'),
        {'case':case_id,'stage':stage,'reference':str(reference_id)}).mappings().one_or_none()
    if row:
        previous=row['fiscal_data']
        if isinstance(previous,str):previous=json.loads(previous)
        if row['state'] not in {'awaiting_payment','void'} and previous!=snapshot:
            raise ManagementRejected('INVOICE_DETAILS_CHANGED_REVIEW_REQUIRED',409)
        if row['stripe_session_id'] and previous!=snapshot:
            raise ManagementRejected('INVOICE_DETAILS_BOUND_TO_PENDING_PAYMENT',409)
        if row['state']=='void':
            conn.execute(text("UPDATE rtm_invoice_requests SET fiscal_data=CAST(:data AS jsonb),recipient_email=:email,state='awaiting_payment',updated_at=CURRENT_TIMESTAMP WHERE id=:id"),
                         {'data':serialized,'email':email,'id':row['id']})
        elif previous!=snapshot:
            conn.execute(text("UPDATE rtm_invoice_requests SET fiscal_data=CAST(:data AS jsonb),recipient_email=:email,updated_at=CURRENT_TIMESTAMP WHERE id=:id AND state='awaiting_payment'"),
                         {'data':serialized,'email':email,'id':row['id']})
        return str(row['id'])
    conn.execute(text('INSERT INTO rtm_invoice_requests '
        '(case_id,stage,reference_id,fiscal_data,recipient_email,state) '
        'VALUES (:case,:stage,:reference,CAST(:data AS jsonb),:email,\'awaiting_payment\')'),
        {'case':case_id,'stage':stage,'reference':str(reference_id),'data':serialized,'email':email})
    row=conn.execute(text('SELECT id FROM rtm_invoice_requests '
        'WHERE case_id=:case AND stage=:stage AND reference_id=:reference'),
        {'case':case_id,'stage':stage,'reference':str(reference_id)}).mappings().one()
    return str(row['id'])


def cancel_request(conn, *, case_id, stage, reference_id):
    changed=conn.execute(text("UPDATE rtm_invoice_requests SET state='void',updated_at=CURRENT_TIMESTAMP "
        "WHERE case_id=:case AND stage=:stage AND reference_id=:reference AND state='awaiting_payment' AND stripe_session_id IS NULL"),
        {'case':case_id,'stage':stage,'reference':str(reference_id)}).rowcount
    if not changed:
        pending=conn.execute(text("SELECT 1 FROM rtm_invoice_requests WHERE case_id=:case AND stage=:stage AND reference_id=:reference AND state='awaiting_payment' AND stripe_session_id IS NOT NULL"),
            {'case':case_id,'stage':stage,'reference':str(reference_id)}).first()
        if pending:raise ManagementRejected('INVOICE_REQUEST_BOUND_TO_PENDING_PAYMENT',409)


def link_checkout_session(conn, *, case_id, stage, reference_id, session_id):
    result=conn.execute(text("UPDATE rtm_invoice_requests SET stripe_session_id=:session,updated_at=CURRENT_TIMESTAMP "
        "WHERE case_id=:case AND stage=:stage AND reference_id=:reference AND state='awaiting_payment' "
        "AND (stripe_session_id IS NULL OR stripe_session_id=:session)"),
        {'case':case_id,'stage':stage,'reference':str(reference_id),'session':session_id})
    if result.rowcount==0:
        pending=conn.execute(text("SELECT stripe_session_id FROM rtm_invoice_requests WHERE case_id=:case AND stage=:stage AND reference_id=:reference AND state='awaiting_payment'"),
            {'case':case_id,'stage':stage,'reference':str(reference_id)}).scalar()
        if pending and pending!=session_id:
            raise ManagementRejected('INVOICE_REQUEST_SESSION_CONFLICT',409)


def unlink_expired_session(conn, *, case_id, stage, reference_id, session_id):
    conn.execute(text("UPDATE rtm_invoice_requests SET stripe_session_id=NULL,updated_at=CURRENT_TIMESTAMP "
        "WHERE case_id=:case AND stage=:stage AND reference_id=:reference AND state='awaiting_payment' AND stripe_session_id=:session"),
        {'case':case_id,'stage':stage,'reference':str(reference_id),'session':session_id})


def mark_paid(conn, *, case_id, stage, reference_id, session_id, payment_intent_id, amount_cents, paid_at=None):
    if not isinstance(amount_cents,int) or amount_cents <= 0:
        return
    conn.execute(text("UPDATE rtm_invoice_requests SET state='paid_pending_issue',"
        'stripe_session_id=COALESCE(stripe_session_id,:session),payment_intent_id=:intent,amount_cents=:amount,paid_at=COALESCE(:paid_at,CURRENT_TIMESTAMP),'
        'updated_at=CURRENT_TIMESTAMP WHERE case_id=:case AND stage=:stage AND reference_id=:reference '
        "AND state IN ('awaiting_payment','paid_pending_issue') AND (stripe_session_id IS NULL OR stripe_session_id=:session)"),
        {'case':case_id,'stage':stage,'reference':str(reference_id),'session':session_id,'intent':payment_intent_id,
         'amount':amount_cents,'paid_at':paid_at})
