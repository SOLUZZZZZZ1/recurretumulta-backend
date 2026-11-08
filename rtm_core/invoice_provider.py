"""Invoice queue worker for FacturaDirecta. No provider calls unless enabled."""
from datetime import datetime, timezone
from decimal import Decimal
import os
from urllib.parse import quote
from zoneinfo import ZoneInfo
import requests
from sqlalchemy import text

BASE = 'https://app.facturadirecta.com/api'
VERSION = '1.0.10'
RETRY_WINDOW_HOURS = 20  # provider idempotency keys expire at 24h


class ProviderError(Exception):
    def __init__(self, code):
        self.code = code


def _config():
    names = ('RTM_INVOICE_FD_API_KEY','RTM_INVOICE_FD_COMPANY_ID','RTM_INVOICE_FD_BANK_ID',
             'RTM_INVOICE_FD_REVENUE_ACCOUNT','RTM_INVOICE_FD_TAX_ID','RTM_INVOICE_FD_SERIES')
    values = {name:(os.getenv(name) or '').strip() for name in names}
    if any(not value for value in values.values()):
        return None
    if (not values['RTM_INVOICE_FD_API_KEY'].count('.') == 1
            or os.getenv('RTM_INVOICE_FD_VAT_BPS')!='2100'
            or os.getenv('RTM_INVOICE_FD_VERIFACTU_CONFIRMED')!='1'
            or os.getenv('RTM_INVOICE_FD_ISSUER_CONFIRMED')!='1'
            or os.getenv('RTM_INVOICE_FD_MAIL_CONFIRMED')!='1'):
        return None
    return values


def _request(config, method, path, *, body=None, key=None, params=None):
    headers={'facturadirecta-api-key':config['RTM_INVOICE_FD_API_KEY'],
             'accept-version':VERSION,'accept':'application/json'}
    if body is not None: headers['content-type']='application/json'
    if key: headers['Idempotency-Key']=key
    try:
        response=requests.request(method,BASE+'/'+config['RTM_INVOICE_FD_COMPANY_ID']+path,
            headers=headers,json=body,params=params,timeout=(5,20))
    except requests.RequestException:
        raise ProviderError('PROVIDER_NETWORK_OR_TIMEOUT') from None
    if response.status_code >= 400:
        # Never log response bodies: they may contain fiscal data or credentials.
        raise ProviderError('PROVIDER_HTTP_'+str(response.status_code))
    if not response.content:
        return {}
    try:return response.json()
    except ValueError:raise ProviderError('PROVIDER_RESPONSE_INVALID') from None


def _money(cents):
    return float((Decimal(int(cents))/Decimal(100)).quantize(Decimal('0.01')))


def _verify_sales_tax(config):
    catalog=_request(config,'GET','/settings/taxes/sales')
    items=catalog.get('items',[]) if isinstance(catalog,dict) else []
    matches=[item for item in items if item.get('id')==config['RTM_INVOICE_FD_TAX_ID']]
    if len(matches)!=1:
        raise ProviderError('SALES_TAX_ID_NOT_UNIQUE_OR_FOUND')
    tax=matches[0]
    if (tax.get('current') is not True or tax.get('category')!='standard_vat'
            or tax.get('taxGroup')!='IVA' or tax.get('country')!='ES'
            or tax.get('autoApplied') is True or abs(float(tax.get('value',-1))-0.21)>0.000001
            or 'line' not in (tax.get('selectorFlags') or [])):
        raise ProviderError('SALES_TAX_IS_NOT_CURRENT_SPANISH_VAT_21')


def _claim(engine):
    with engine.begin() as conn:
        row=conn.execute(text("""
          SELECT * FROM rtm_invoice_requests
          WHERE state IN ('paid_pending_issue','issuing','issued_pending_payment','paid_pending_send')
            AND (attempt_started_at IS NULL OR attempt_started_at < CURRENT_TIMESTAMP - INTERVAL '5 minutes')
            ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED
        """)).mappings().one_or_none()
        if not row:return None
        paid_at=row['paid_at']
        if paid_at and (datetime.now(timezone.utc)-paid_at.replace(tzinfo=timezone.utc)).total_seconds()>RETRY_WINDOW_HOURS*3600:
            conn.execute(text("UPDATE rtm_invoice_requests SET state='review_required',last_error_code='IDEMPOTENCY_WINDOW_EXPIRED',updated_at=CURRENT_TIMESTAMP WHERE id=:id"),{'id':row['id']})
            return None
        conn.execute(text("UPDATE rtm_invoice_requests SET state=CASE WHEN state='paid_pending_issue' THEN 'issuing' ELSE state END,attempt_started_at=CURRENT_TIMESTAMP,attempts=attempts+1,updated_at=CURRENT_TIMESTAMP WHERE id=:id"),{'id':row['id']})
        return dict(row)


def _save(engine, request_id, **values):
    if not values:return
    allowed={'state','provider_contact_id','provider_invoice_id','provider_invoice_number','provider_invoice_url','provider_payment_recorded_at','provider_sent_at','issued_at','last_error_code'}
    if not set(values)<=allowed:raise ValueError('invoice update field not allowed')
    assignments=','.join(f'{key}=:{key}' for key in values)
    values['id']=request_id
    with engine.begin() as conn:
        conn.execute(text(f'UPDATE rtm_invoice_requests SET {assignments},updated_at=CURRENT_TIMESTAMP WHERE id=:id'),values)


def _contact(config,row,request_id):
    fiscal=row['fiscal_data']
    found=_request(config,'GET','/contacts',params={'fiscalId':fiscal['tax_id'],'isClient':'true','limit':100})
    items=found.get('items',[]) if isinstance(found,dict) else []
    exact=[]
    for item in items:
        content=item.get('content',{});main=content.get('main',{})
        if main.get('fiscalId','').strip().casefold()==fiscal['tax_id'].strip().casefold():
            exact.append((content.get('uuid'),main))
    exact=[item for item in exact if item[0]]
    if len(exact)==1:
        contact_id,main=exact[0]
        pairs=(('name','legal_name'),('address','address'),('zipcode','postal_code'),
               ('city','city'),('region','province'),('country','country'))
        if any(str(main.get(provider_field,'')).strip().casefold()!=str(fiscal[our_field]).strip().casefold()
               for provider_field,our_field in pairs):
            raise ProviderError('FISCAL_CONTACT_DETAILS_MISMATCH_REVIEW')
        return contact_id
    if len(exact)>1:raise ProviderError('DUPLICATE_FISCAL_CONTACT_REVIEW')
    body={'content':{'type':'contact','main':{'name':fiscal['legal_name'],'email':row['recipient_email'],
        'fiscalId':fiscal['tax_id'],'address':fiscal['address'],'zipcode':fiscal['postal_code'],
        'city':fiscal['city'],'region':fiscal['province'],'country':fiscal['country'],
        'fiscalIdCountry':fiscal['country'],'language':'es'}}}
    created=_request(config,'POST','/contacts',body=body,key='rtm-invoice-contact-'+str(request_id))
    contact=created.get('content',{}).get('uuid')
    if not contact:raise ProviderError('PROVIDER_CONTACT_ID_MISSING')
    return contact


def _process(engine,config,row):
    request_id=str(row['id'])
    fiscal=row['fiscal_data']
    province=' '.join(str(fiscal.get('province','')).casefold().split())
    special_regions=('canarias','tenerife','gran canaria','las palmas','santa cruz','ceuta','melilla')
    if fiscal.get('country')!='ES' or any(term in province for term in special_regions):
        raise ProviderError('TAX_JURISDICTION_REVIEW_REQUIRED')
    if row['provider_invoice_id']:
        invoice_id=row['provider_invoice_id']
    else:
        _verify_sales_tax(config)
        contact_id=row['provider_contact_id'] or _contact(config,row,request_id)
        if not row['provider_contact_id']:_save(engine,request_id,provider_contact_id=contact_id)
        now=datetime.now(ZoneInfo('Europe/Madrid')).date().isoformat()
        amount=_money(row['amount_cents'])
        stage_text='Estudio inicial' if row['stage']=='initial_study' else 'Gestión del expediente'
        body={'content':{'main':{'contact':contact_id,'date':now,'dueDate':now,'currency':'EUR',
            'docNumber':{'series':config['RTM_INVOICE_FD_SERIES']},'fiscalPosition':'general',
            'account':config['RTM_INVOICE_FD_REVENUE_ACCOUNT'],'draft':False,'voided':False,
            'simplified':False,'taxIncludedPrices':True,'emails':row['recipient_email'],
            'lines':[{'account':config['RTM_INVOICE_FD_REVENUE_ACCOUNT'],'quantity':1,
                'unitPrice':amount,'discount':0,'discountRate':0,'lineTotal':amount,
                'tax':[config['RTM_INVOICE_FD_TAX_ID']], 'text':stage_text}]}}}
        created=_request(config,'POST','/invoices',body=body,key='rtm-invoice-issue-'+request_id)
        content=created.get('content',{});invoice_id=content.get('uuid')
        if not invoice_id:raise ProviderError('PROVIDER_INVOICE_ID_MISSING')
        main=content.get('main',{})
        doc=main.get('docNumber') or {}
        number=doc.get('formatted') or doc.get('number')
        _save(engine,request_id,provider_invoice_id=invoice_id,
              provider_invoice_number=str(number) if number is not None else None,
              provider_invoice_url=created.get('webUrl'),issued_at=datetime.now(timezone.utc),state='issued_pending_payment')

    # State persisted after each external side effect. Stable keys protect the
    # short timeout gap; after 20h ambiguous operations stop for human review.
    if row['provider_payment_recorded_at'] is None:
        _request(config,'POST',f'/invoices/{quote(invoice_id,safe="")}/payments',
            body={'payments':[{'date':row['paid_at'].astimezone(ZoneInfo('Europe/Madrid')).date().isoformat(),'bank':config['RTM_INVOICE_FD_BANK_ID'],
                'amount':_money(row['amount_cents']),'description':'Cobro Stripe '+row['stage']}]},
            key='rtm-invoice-payment-'+request_id)
        _save(engine,request_id,provider_payment_recorded_at=datetime.now(timezone.utc),state='paid_pending_send')
    if row['provider_sent_at'] is None:
        _request(config,'PUT',f'/invoices/{quote(invoice_id,safe="")}/send',
            body={'to':row['recipient_email'],'continueWithoutFacturae':True},key='rtm-invoice-send-'+request_id)
        _save(engine,request_id,provider_sent_at=datetime.now(timezone.utc),state='sent',last_error_code=None)


def tick(engine, limit=5):
    if os.getenv('RTM_INVOICE_WORKER_ENABLED')!='1':return {'ok':True,'disabled':True,'processed':0}
    config=_config()
    if not config:return {'ok':False,'configuration_missing':True,'processed':0}
    processed=0
    for _ in range(max(1,min(int(limit),20))):
        row=_claim(engine)
        if row is None:break
        try:
            _process(engine,config,row)
            processed+=1
        except ProviderError as exc:
            # Retry only transient provider/network responses with the same key.
            transient={'PROVIDER_NETWORK_OR_TIMEOUT','PROVIDER_HTTP_429','PROVIDER_HTTP_500',
                       'PROVIDER_HTTP_502','PROVIDER_HTTP_503','PROVIDER_HTTP_504'}
            if exc.code in transient:
                _save(engine,row['id'],state='paid_pending_issue' if not row['provider_invoice_id'] else ('issued_pending_payment' if not row['provider_payment_recorded_at'] else 'paid_pending_send'),last_error_code=exc.code)
            else:
                _save(engine,row['id'],state='review_required',last_error_code=exc.code)
        except Exception:
            _save(engine,row['id'],state='review_required',last_error_code='WORKER_UNEXPECTED_ERROR')
    return {'ok':True,'processed':processed}
