"""Exact existing staging only; no app import, DB query or provider mutation.

Reports configuration booleans and the ID of the account behind the existing
TEST key. No credentials, messages, customer rows or connection URLs printed.
"""
from datetime import datetime,timezone
import json
import os
import re
from urllib.parse import urlsplit
from email.utils import parseaddr

SERVICE='srv-d9v1t2qjnfac73a14kug'
DB_HOST='dpg-d9v2lab7uimc73da0j9g-a'
MAILBOX='expedientes@resuelvetusmovidas.es'


def account_id(key):
    import requests
    response=requests.get('https://api.stripe.com/v1/account',
        headers={'Authorization':'Bearer '+key},timeout=10,allow_redirects=False)
    if response.status_code!=200:raise RuntimeError('STRIPE_READ_HTTP_'+str(response.status_code))
    value=response.json().get('id','')
    if not re.fullmatch(r'acct_[A-Za-z0-9]+',value):raise RuntimeError('STRIPE_ACCOUNT_ID_INVALID')
    return value


def inspect(source=None,reader=account_id):
    source=os.environ if source is None else source
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'read_only':True,'ok':False,
        'scope':'Configuration metadata + GET Stripe account using existing TEST key; no DB/app/mail/mutation'}
    try:
        db=urlsplit(source.get('DATABASE_URL',''))
        exact=(source.get('RENDER_SERVICE_ID')==SERVICE and source.get('RENDER_SERVICE_NAME')=='recurretumulta-backend-1'
               and source.get('RTM_ENV')=='staging' and db.hostname==DB_HOST and db.path=='/rtm_staging'
               and db.port in (None,5432) and not db.query and not db.fragment)
        result['exact_existing_staging']=exact
        if not exact:raise RuntimeError('EXACT_EXISTING_STAGING_REQUIRED')
        configured=('RTM_CASE_MAIL_PASSWORD','SMTP_PASSWORD','SMTP_PASS',
            'RTM_MANAGEMENT_STRIPE_TEST_KEY','RTM_MANAGEMENT_STRIPE_TEST_WEBHOOK_SECRET',
            'RTM_MANAGEMENT_STRIPE_ACCOUNT_ID','RTM_OPERATOR_ACCESS_HMAC_KEY',
            'OPENAI_API_KEY','OPENAI_MODEL','RTM_MANAGEMENT_GENERATION_MODEL')
        result['configured']={k:bool(source.get(k)) for k in configured}
        result['same_saved_mailbox']={k:parseaddr(source.get(k,''))[1].lower()==MAILBOX
            for k in ('SMTP_USER','SMTP_USERNAME','SMTP_FROM','MAIL_FROM','RTM_CASE_MAIL_USERNAME','RTM_CASE_MAIL_ADDRESS')}
        result['same_saved_smtp_host']={k:source.get(k)=='smtp.serviciodecorreo.es'
            for k in ('SMTP_HOST','RTM_CASE_MAIL_SMTP_HOST')}
        result['saved_smtp_implicit_tls']=source.get('SMTP_PORT')=='465'
        result['flags']={k:source.get(k) in ('1','true') for k in (
            'RTM_ENABLE_PROVIDER_TEST_CASEWORK','RTM_ENABLE_MANAGEMENT_JOURNEY',
            'RTM_ENABLE_CASE_MAIL','RTM_CASE_MAIL_WORKER_ENABLED','RTM_MANAGEMENT_GENERATION_WORKER_ENABLED')}
        key=source.get('STRIPE_SECRET_KEY','')
        result['general_stripe_key_is_test']=key.startswith(('sk_test_','rk_test_'))
        if not result['general_stripe_key_is_test']:raise RuntimeError('EXISTING_TEST_STRIPE_KEY_REQUIRED')
        account=reader(key)
        if not re.fullmatch(r'acct_[A-Za-z0-9]+',account):raise RuntimeError('STRIPE_ACCOUNT_ID_INVALID')
        result['stripe_test_account_id']=account
        result['management_account_matches']=source.get('RTM_MANAGEMENT_STRIPE_ACCOUNT_ID')==account
        result['management_key_equals_general_test_key']=source.get('RTM_MANAGEMENT_STRIPE_TEST_KEY')==key
        result['ok']=True
    except Exception as exc:
        result['error_type']=type(exc).__name__
        # Only our fixed diagnostic codes; never provider/connection messages.
        if type(exc) is RuntimeError and re.fullmatch(r'[A-Z0-9_]+',str(exc)):
            result['error_code']=str(exc)
    return result


if __name__=='__main__':print('RTM_JOURNEY_RUNTIME_READONLY '+json.dumps(inspect(),sort_keys=True))
