"""Durable, one-version fulfillment of a fully settled management quote.

Enqueue commits with settlement. Provider work happens outside DB transactions.
Case locks plus expiring leases fence concurrent workers and delayed results.
"""
from datetime import datetime, timezone
import hashlib
import os
import time
from uuid import uuid4
from sqlalchemy import text
from fastapi import HTTPException
from rtm_core.management_contract import ManagementRejected, canonical, digest
from rtm_core.management_store import lock_case, quote_row, quote_projection, obj, jparam, event
from rtm_core.management_payments import obligation, receipt
from rtm_core.management_drafts import version, requirements
from rtm_core.paid_study import read_history
from rtm_core.document_viewer import document_row, read_custody
from rtm_core.management_generation_provider import generate, render

MAX_ATTEMPTS = 5
LEASE_SECONDS = 300


def job_row(conn, case_id):
    row = conn.execute(text('SELECT * FROM rtm_management_generation_jobs WHERE case_id=:case '
        'ORDER BY created_at DESC,id DESC LIMIT 1'), {'case':case_id}).mappings().first()
    if not row:return None
    result = dict(row)
    for key in ('id','case_id','quote_id','obligation_id','lease_token','version_id'):
        if result[key] is not None:result[key] = str(result[key])
    return result


def enqueue(conn, row):
    # Called exclusively after verified settlement in the same transaction.
    if row['state'] != 'confirmed':return
    conn.execute(text('INSERT INTO rtm_management_generation_jobs '
        '(id,case_id,quote_id,obligation_id,mode,account_id,state) '
        "VALUES (:id,:case,:quote,:obligation,:mode,:account,'pending') "
        'ON CONFLICT (obligation_id) DO NOTHING'),
        {'id':str(uuid4()),'case':row['case_id'],'quote':row['quote_id'],
         'obligation':row['id'],'mode':row['mode'],'account':row['account_id']})


def projection(conn, case_id, *, public=False):
    row = job_row(conn, case_id)
    if not row:return {'state':'awaiting_payment'}
    if public:return {'state':row['state']}
    return {k:row[k] for k in ('id','state','attempts','last_code','version_id')}


def material(conn, job, policy):
    case_id = job['case_id']
    if (case_id != policy.case_id or job['mode'] != policy.mode
            or job['account_id'] != policy.account_id):
        raise ManagementRejected('GENERATION_SCOPE_MISMATCH')
    missing = requirements(conn, case_id, policy)
    if missing:raise ManagementRejected(missing[0])
    quote = quote_projection(quote_row(conn, case_id))
    q = quote['snapshot']
    if q['id'] != job['quote_id'] or q['mode'] != policy.mode:
        raise ManagementRejected('DRAFT_CONTRACT_VERSION_CHANGED')
    if q.get('document_type') not in {'Alegaciones','Recurso de reposición','Recurso de alzada'}:
        raise ManagementRejected('GENERATION_PROCEDURAL_ACTION_REQUIRED')
    study = read_history(conn, case_id)['latest']
    if study['sha256'] != q['study_sha256'] or study['snapshot']['operation_id'] != q['study_operation_id']:
        raise ManagementRejected('DRAFT_REVIEWED_STUDY_CHANGED')
    paid = obligation(conn, case_id=case_id, obligation_id=job['obligation_id'])
    initial = receipt(conn, case_id, policy.mode, 'initial_study')
    balance = receipt(conn, case_id, policy.mode, 'management_balance')
    credit = conn.execute(text('SELECT obligation_id,quote_id,applied_cents FROM rtm_management_credit_applications '
        'WHERE receipt_id=:id'), {'id':initial['id'] if initial else None}).mappings().first()
    if (not paid or paid['state'] != 'confirmed' or paid['quote_id'] != q['id']
            or not initial or not balance or not credit
            or initial['id'] != q['study_receipt_id'] or initial['evidence_sha256'] != q['study_receipt_sha256']
            or str(credit['obligation_id']) != paid['id'] or str(credit['quote_id']) != q['id']
            or credit['applied_cents'] != initial['amount_cents']
            or balance['session_id'] != paid['stripe_session_id']
            or balance['amount_cents'] != paid['amount_cents']
            or initial['amount_cents'] + balance['amount_cents'] != q['economics']['total_cents']
            or any(p['account_id'] != policy.account_id or p['provider_status'] != 'succeeded'
                   or p['refunded_cents'] != 0 or p['disputed'] is not False for p in (initial,balance))):
        raise ManagementRejected('GENERATION_FULL_PAYMENT_UNVERIFIED')
    case = dict(conn.execute(text('SELECT id,authorized,interested_data,expediente_ref FROM cases WHERE id=:id'),
        {'id':case_id}).mappings().one())
    case['id'] = str(case['id']);case['interested_data'] = obj(case['interested_data'])
    if case['authorized'] not in (True, 1):raise ManagementRejected('AUTHORIZATION_REVIEW_REQUIRED')
    identity = case['interested_data']
    if not all(isinstance(identity.get(k), str) and identity[k].strip() for k in ('full_name','dni_nie','domicilio_notif')):
        raise ManagementRejected('GENERATION_IDENTITY_REQUIRED')
    if conn.execute(text('SELECT id FROM ops_final_resources WHERE case_id=:case LIMIT 1'), {'case':case_id}).first():
        raise ManagementRejected('EXISTING_RESOURCE_MUST_BE_RECOVERED')
    # Verify the exact reviewed sources again, never silently adopt newer bytes.
    for source in study['snapshot']['sources']:
        _, observed, _ = read_custody(document_row(conn, case_id, source['document_id']))
        if observed != source['observed_sha256']:raise ManagementRejected('DRAFT_SOURCE_CHANGED_REVIEW_REQUIRED')
    return {'case':case,'quote':q,'quote_sha256':quote['sha256'],
            'study':study['snapshot'],'study_sha256':study['sha256'],
            'initial_receipt_sha256':initial['evidence_sha256'], 'balance_receipt_sha256':balance['evidence_sha256']}


def claim(conn, policy, now):
    lock_case(conn, policy.case_id)
    job = job_row(conn, policy.case_id)
    if not job or job['state'] in {'completed','blocked'}:return None
    if job['mode'] != policy.mode or job['account_id'] != policy.account_id:
        raise ManagementRejected('GENERATION_SCOPE_MISMATCH')
    if job['state'] == 'running' and (job['lease_until'] or 0) > now:return None
    if job['next_attempt_at'] > now:return None
    if job['attempts'] >= MAX_ATTEMPTS:
        conn.execute(text("UPDATE rtm_management_generation_jobs SET state='blocked',last_code='GENERATION_RETRY_LIMIT',"
            'lease_token=NULL,lease_until=NULL WHERE id=:id'), {'id':job['id']})
        return None
    token = str(uuid4())
    conn.execute(text("UPDATE rtm_management_generation_jobs SET state='running',attempts=attempts+1,"
        'lease_token=:token,lease_until=:until,last_code=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=:id'),
        {'token':token,'until':now+LEASE_SECONDS,'id':job['id']})
    return job_row(conn, policy.case_id)


def owns(current, job):
    return bool(current and current['id']==job['id'] and current['state']=='running'
                and current['lease_token']==job['lease_token'])


def completed(conn, job, stored, code=None):
    if stored['snapshot']['quote_id'] != job['quote_id']:
        raise ManagementRejected('EXISTING_RESOURCE_CONTRACT_REVIEW_REQUIRED')
    conn.execute(text("UPDATE rtm_management_generation_jobs SET state='completed',version_id=:version,"
        'last_code=:code,lease_token=NULL,lease_until=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=:id'),
        {'id':job['id'],'version':stored['snapshot']['id'],'code':code})


def persist(conn, job, inputs, result, content):
    snapshot = {'contract':'rtm_management_working_draft_v1','id':job['id'],'case_id':job['case_id'],
        'revision':1,'previous_id':None,'previous_sha256':None,
        'quote_id':job['quote_id'],'quote_sha256':inputs['quote_sha256'],
        'study_operation_id':inputs['study']['operation_id'],'study_sha256':inputs['study_sha256'],
        'sources':inputs['study']['sources'],'reviewed_legal_inputs':None,'legacy_source':None,
        'content':content,'content_sha256':hashlib.sha256(content.encode()).hexdigest(),
        'author':{'id':None,'name':'RTM · generación automática'},
        'created_at':datetime.now(timezone.utc).isoformat(),
        'correction_reason':'Generación automática tras confirmar el pago íntegro del presupuesto.',
        'status':'working_draft','signed':False,'approved':False,'presented':False,
        'generation':{'job_id':job['id'],'obligation_id':job['obligation_id'],'input_sha256':digest(inputs),
                      'provider_result':result,'review_required':True}}
    conn.execute(text('INSERT INTO rtm_management_draft_versions '
        '(id,case_id,operation_id,revision,previous_id,based_on_id,quote_id,author_id,snapshot,snapshot_sha256) '
        'VALUES (:id,:case,:id,1,NULL,NULL,:quote,NULL,'+jparam(conn,'snapshot')+',:sha)'),
        {'id':job['id'],'case':job['case_id'],'quote':job['quote_id'],
         'snapshot':canonical(snapshot),'sha':digest(snapshot)})
    stored = version(conn, job['case_id'], job['id'])
    completed(conn, job, stored)
    event(conn, job['case_id'],'draft.automatically_generated','system:management-generation',job['id'],
          {'version_id':job['id'],'sha256':stored['sha256'],'presented':False})


def fail(engine, job, code, retryable, now):
    with engine.begin() as conn:
        lock_case(conn, job['case_id'])
        current = job_row(conn, job['case_id'])
        if not owns(current, job):return
        state = 'retry' if retryable and current['attempts'] < MAX_ATTEMPTS else 'blocked'
        conn.execute(text('UPDATE rtm_management_generation_jobs SET state=:state,last_code=:code,'
            'next_attempt_at=:next,lease_token=NULL,lease_until=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=:id'),
            {'id':job['id'],'state':state,'code':code,'next':now+min(60*2**(current['attempts']-1),1800)})
        event(conn,job['case_id'],'draft.generation_'+state,'system:management-generation',job['id'],{'code':code})


def run_one(engine, policy, *, provider=generate, now=None):
    now = int(time.time()) if now is None else now
    with engine.begin() as conn:job = claim(conn, policy, now)
    if not job:return False
    try:
        with engine.begin() as conn:
            lock_case(conn, job['case_id'])
            if not owns(job_row(conn,job['case_id']),job):return True
            existing = version(conn, job['case_id'])
            if existing:
                completed(conn,job,existing,'EXISTING_VERSION_PRESERVED');return True
            inputs = material(conn,job,policy)
        result = provider(inputs)  # No database transaction or lock during model call.
        content = render(inputs,result)
        with engine.begin() as conn:
            lock_case(conn, job['case_id'])
            if not owns(job_row(conn,job['case_id']),job):return True
            existing = version(conn, job['case_id'])
            if existing:
                completed(conn,job,existing,'EXISTING_VERSION_PRESERVED');return True
            current = material(conn,job,policy)
            if digest(current) != digest(inputs):raise ManagementRejected('GENERATION_INPUT_CHANGED')
            persist(conn,job,inputs,result,content)
    except ManagementRejected as exc:
        fail(engine,job,exc.code,exc.status>=500,now)
    except HTTPException as exc:
        fail(engine,job,'GENERATION_SOURCE_UNAVAILABLE',exc.status_code>=500,now)
    except Exception:
        # Provider payloads, legal text and credentials never enter error logs.
        fail(engine,job,'GENERATION_PROVIDER_UNAVAILABLE',True,now)
    return True


def retry(conn, case_id, job_id, policy, actor):
    lock_case(conn,case_id)
    job = job_row(conn,case_id)
    if not job or job['id'] != job_id:raise ManagementRejected('GENERATION_JOB_NOT_FOUND',404)
    if job['state'] in {'completed','running','pending'}:return projection(conn,case_id)
    material(conn,job,policy)  # Correct the underlying requirement before retrying.
    conn.execute(text("UPDATE rtm_management_generation_jobs SET state='pending',attempts=0,next_attempt_at=0,"
        'last_code=NULL,lease_token=NULL,lease_until=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=:id'),{'id':job_id})
    event(conn,case_id,'draft.generation_retry_requested','operator:'+actor,job_id,{})
    return projection(conn,case_id)


def tick(engine):
    if os.getenv('RTM_MANAGEMENT_GENERATION_WORKER_ENABLED') != '1':return False
    from rtm_core.management_policy import configuration
    return run_one(engine,configuration(for_worker=True))
