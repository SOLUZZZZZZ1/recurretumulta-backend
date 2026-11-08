"""Durable balance reservations. Transactions never span a Stripe request.

The caller commits reserve() before calling the provider. Unknown results keep
the SAME obligation and key. Only a verified provider read can settle money.
"""
from datetime import datetime, timezone
import time
from uuid import uuid4
from sqlalchemy import text
from rtm_core.management_contract import ManagementRejected, canonical, digest, timestamp
from rtm_core.management_store import (obj, jparam, lock_case, event, quote_row,
    quote_projection, current_client_quote)
from rtm_core.management_mail import verified_recipient
from rtm_core.management_stripe import check_session, obligation_metadata, checkout_url
from rtm_core.invoice_requests import (mark_paid as mark_invoice_paid,
    link_checkout_session as link_invoice_checkout, unlink_expired_session as unlink_invoice_checkout)


def receipt(conn, case_id, mode, stage):
    row=conn.execute(text('SELECT * FROM rtm_management_receipts WHERE case_id=:case AND mode=:mode AND stage=:stage'),
        {'case':case_id,'mode':mode,'stage':stage}).mappings().one_or_none()
    if not row:return None
    proof=obj(row['proof'])
    if (digest(proof)!=row['proof_sha256'] or proof['id']!=str(row['id'])
            or proof['case_id']!=case_id or proof['stage']!=stage or proof['mode']!=mode):
        raise ManagementRejected('PAYMENT_RECORD_INTEGRITY_FAILED',503)
    return {**proof,'evidence_sha256':row['proof_sha256']}


def record_receipt(conn, proof):
    material={k:v for k,v in proof.items() if k!='evidence_sha256'}
    if proof.get('evidence_sha256')!=digest(material):
        raise ManagementRejected('PAYMENT_RECEIPT_INTEGRITY_FAILED',503)
    lock_case(conn,proof['case_id'])
    prior=receipt(conn,proof['case_id'],proof['mode'],proof['stage'])
    if prior:
        # Reverification does not replace the original evidence or its identity.
        keys=('account_id','charge_id','session_id','payment_intent_id','amount_cents',
              'currency','provider_status','refunded_cents','disputed')
        if any(prior[k]!=proof[k] for k in keys):
            raise ManagementRejected('PAYMENT_RECEIPT_CHANGED_REVIEW_REQUIRED')
        return prior
    conn.execute(text('INSERT INTO rtm_management_receipts '
        '(id,case_id,mode,account_id,stage,charge_id,amount_cents,proof,proof_sha256) '
        'VALUES (:id,:case,:mode,:account,:stage,:charge,:amount,'+jparam(conn,'proof')+',:sha)'),
        {'id':proof['id'],'case':proof['case_id'],'mode':proof['mode'],'account':proof['account_id'],
         'stage':proof['stage'],'charge':proof['charge_id'],'amount':proof['amount_cents'],
         'proof':canonical(material),'sha':proof['evidence_sha256']})
    return proof


def obligation(conn, *, case_id=None, quote_id=None, obligation_id=None):
    clauses=[];params={}
    for key,value in (('case_id',case_id),('quote_id',quote_id),('id',obligation_id)):
        if value is not None:clauses.append(key+'=:'+key);params[key]=value
    if not clauses:raise ValueError('Bounded obligation lookup required')
    row=conn.execute(text('SELECT * FROM rtm_management_obligations WHERE '+' AND '.join(clauses)),params).mappings().one_or_none()
    if not row:return None
    result=dict(row)
    for key in ('id','case_id','quote_id'):result[key]=str(result[key])
    result['reservation']=obj(result['reservation'])
    material=result['reservation']['material']
    if (digest(material)!=result['material_sha256'] or
            any(material.get(k)!=result[k] for k in ('case_id','quote_id','mode','account_id','amount_cents')) or
            result['idempotency_key']!='rtm-balance:'+result['id']):
        raise ManagementRejected('OBLIGATION_INTEGRITY_FAILED',503)
    return result


def reserve(conn, *, case_id, quote_id, expected_sha, recipient_hash, policy, now=None):
    now=time.time() if now is None else now
    lock_case(conn,case_id)
    existing=obligation(conn,case_id=case_id,quote_id=quote_id)
    if existing:
        if (existing['reservation']['material']['quote_sha256']!=expected_sha
                or existing['mode']!=policy.mode or existing['account_id']!=policy.account_id):
            raise ManagementRejected('QUOTE_VERSION_OR_MODE_CHANGED')
        acceptance=conn.execute(text('SELECT recipient_hash FROM rtm_management_acceptances WHERE quote_id=:id'),{'id':quote_id}).scalar()
        if acceptance!=recipient_hash:raise ManagementRejected('ACCEPTANCE_IDENTITY_MISMATCH')
        return existing
    quote=current_client_quote(conn,case_id,quote_id,expected_sha)
    snapshot=quote['snapshot']
    if snapshot['mode']!=policy.mode or case_id!=policy.case_id:
        raise ManagementRejected('BALANCE_MODE_OR_CASE_MISMATCH')
    accepted=conn.execute(text('SELECT * FROM rtm_management_acceptances WHERE quote_id=:id AND recipient_hash=:recipient'),
        {'id':quote_id,'recipient':recipient_hash}).mappings().one_or_none()
    if not accepted or obj(accepted['snapshot']).get('quote_sha256')!=expected_sha:
        raise ManagementRejected('EXACT_QUOTE_ACCEPTANCE_REQUIRED')
    receipt_email=verified_recipient(conn,case_id)
    from rtm_core.management_client_access import verifier
    if verifier(receipt_email)!=recipient_hash:
        raise ManagementRejected('CLIENT_RECIPIENT_CHANGED_REVIEW_REQUIRED')
    initial=receipt(conn,case_id,policy.mode,'initial_study')
    if (not initial or initial['id']!=snapshot['study_receipt_id']
            or initial['evidence_sha256']!=snapshot['study_receipt_sha256']):
        raise ManagementRejected('STUDY_CREDIT_NOT_VERIFIED')
    if conn.execute(text('SELECT receipt_id FROM rtm_management_credit_applications WHERE receipt_id=:id'),{'id':initial['id']}).first():
        raise ManagementRejected('STUDY_CREDIT_ALREADY_APPLIED')
    if conn.execute(text("SELECT id FROM rtm_management_obligations WHERE case_id=:case AND state!='expired'"),{'case':case_id}).first():
        raise ManagementRejected('PREVIOUS_CHECKOUT_MUST_BE_RECONCILED')
    # A quote is bound to the reviewed study. New study work requires a new quote.
    from rtm_core.paid_study import read_history
    study=read_history(conn,case_id)['latest']
    if not study or study['sha256']!=snapshot['study_sha256']:
        raise ManagementRejected('QUOTE_STUDY_VERSION_CHANGED')
    expiry=min(int(now)+23*3600,int(timestamp(snapshot['expires_at']).timestamp()))
    if expiry<int(now)+1800:raise ManagementRejected('QUOTE_TOO_CLOSE_TO_EXPIRY_FOR_CHECKOUT')
    oid=str(uuid4());amount=snapshot['economics']['balance_cents']
    material={'case_id':case_id,'quote_id':quote_id,'quote_sha256':expected_sha,
        'acceptance_sha256':accepted['snapshot_sha256'],'mode':policy.mode,'account_id':policy.account_id,
        'amount_cents':amount,'currency':'eur','initial_receipt_id':initial['id']}
    row={'id':oid,'case_id':case_id,'quote_id':quote_id,'mode':policy.mode,'account_id':policy.account_id,
         'amount_cents':amount,'material_sha256':digest(material),'idempotency_key':'rtm-balance:'+oid,
         'stripe_session_id':None,'state':'reserved'}
    metadata=obligation_metadata(row)
    params={'mode':'payment','payment_method_types':['card'],'client_reference_id':oid,
        'customer_email':receipt_email,'metadata':metadata,
        'payment_intent_data':{'metadata':metadata,'receipt_email':receipt_email},'expires_at':expiry,
        'success_url':policy.origin+'/#/cliente/expediente/'+case_id+'?pago=consultar',
        'cancel_url':policy.origin+'/#/cliente/expediente/'+case_id+'?pago=volver',
        'line_items':[{'quantity':1,'price_data':{'currency':'eur','unit_amount':amount,
            'product_data':{'name':'Saldo de gestión · presupuesto '+str(snapshot['revision'])}}}]}
    reservation={'created_epoch':int(now),'material':material,'provider_parameters':params,'provider_parameters_sha256':digest(params)}
    conn.execute(text('INSERT INTO rtm_management_obligations '
        '(id,quote_id,case_id,mode,account_id,amount_cents,material_sha256,idempotency_key,state,reservation) '
        "VALUES (:id,:quote,:case,:mode,:account,:amount,:sha,:key,'reserved',"+jparam(conn,'reservation')+')'),
        {'id':oid,'quote':quote_id,'case':case_id,'mode':policy.mode,'account':policy.account_id,
         'amount':amount,'sha':row['material_sha256'],'key':row['idempotency_key'],'reservation':canonical(reservation)})
    event(conn,case_id,'balance.reserved','client:'+recipient_hash,oid,{'quote_id':quote_id,'mode':policy.mode})
    return obligation(conn,obligation_id=oid)


def unknown(conn, row):
    lock_case(conn,row['case_id'])
    conn.execute(text("UPDATE rtm_management_obligations SET state='unknown',updated_at=CURRENT_TIMESTAMP "
        "WHERE id=:id AND state IN ('reserved','open','unknown')"),{'id':row['id']})


def apply_provider_read(conn, row, session, *, provider_account_id, event_id=None):
    lock_case(conn,row['case_id'])
    row=obligation(conn,obligation_id=row['id'])
    if provider_account_id!=row['account_id']:raise ManagementRejected('STRIPE_ACCOUNT_MISMATCH')
    check_session(session,row)
    if event_id:
        prior=conn.execute(text('SELECT obligation_id FROM rtm_management_webhooks WHERE event_id=:event AND mode=:mode AND account_id=:account'),
            {'event':event_id,'mode':row['mode'],'account':row['account_id']}).scalar()
        if prior:
            if str(prior)!=row['id']:raise ManagementRejected('WEBHOOK_IDENTITY_CONFLICT')
            return public_payment(row)
    state=row['state'];url=None
    if session.get('status')=='complete' and session.get('payment_status')=='paid':
        intent,charge=check_session(session,row,paid=True)
        proof={'id':str(uuid4()),'case_id':row['case_id'],'stage':'management_balance','mode':row['mode'],
            'account_id':row['account_id'],'session_id':session['id'],'payment_intent_id':intent['id'],
            'charge_id':charge['id'],'amount_cents':row['amount_cents'],'currency':'eur',
            'provider_status':'succeeded','refunded_cents':0,'disputed':False,
            'verified_at':datetime.now(timezone.utc).isoformat()}
        record_receipt(conn,{**proof,'evidence_sha256':digest(proof)})
        mark_invoice_paid(conn,case_id=row['case_id'],stage='management_balance',reference_id=row['quote_id'],
                          session_id=session['id'],payment_intent_id=intent['id'],amount_cents=row['amount_cents'])
        quote=quote_row(conn,row['case_id'],row['quote_id']);latest=quote_row(conn,row['case_id'])
        # A real late/stale payment is recorded, never called cancelled and never
        # automatically authorizes a superseded legal instruction.
        expiry=timestamp(quote_projection(quote)['snapshot']['expires_at']).timestamp()
        valid=(str(latest['id'])==row['quote_id'] and quote['state'] in {'accepted','settled'}
               and type(charge.get('created')) is int and charge['created']<=expiry
               and state not in {'expired','retiring','review_required'})
        state='confirmed' if valid else 'review_required'
        if valid:
            initial=row['reservation']['material']['initial_receipt_id']
            prior=conn.execute(text('SELECT obligation_id FROM rtm_management_credit_applications WHERE receipt_id=:id'),{'id':initial}).scalar()
            if prior and str(prior)!=row['id']:raise ManagementRejected('STUDY_CREDIT_ALREADY_APPLIED')
            if not prior:
                conn.execute(text('INSERT INTO rtm_management_credit_applications(receipt_id,quote_id,obligation_id,applied_cents) VALUES (:receipt,:quote,:id,1000)'),
                    {'receipt':initial,'quote':row['quote_id'],'id':row['id']})
                event(conn,row['case_id'],'balance.confirmed','stripe',row['id'],{'mode':row['mode'],'amount_cents':row['amount_cents']})
            conn.execute(text("UPDATE rtm_management_quotes SET state='settled' WHERE id=:id"),{'id':row['quote_id']})
    elif state in {'confirmed','review_required'}:
        # Never downgrade verified money because of an older notification/read.
        pass
    elif session.get('status')=='expired' and session.get('payment_status')=='unpaid':
        state='expired'
        unlink_invoice_checkout(conn,case_id=row['case_id'],stage='management_balance',
            reference_id=row['quote_id'],session_id=session['id'])
    elif session.get('status')=='open' and session.get('payment_status')=='unpaid':
        state='retiring' if state=='retiring' else 'open'
        link_invoice_checkout(conn,case_id=row['case_id'],stage='management_balance',
            reference_id=row['quote_id'],session_id=session['id'])
        if state=='open':url=checkout_url(session)
    else:
        state='unknown'
    conn.execute(text('UPDATE rtm_management_obligations SET state=:state,stripe_session_id=:session,updated_at=CURRENT_TIMESTAMP WHERE id=:id'),
        {'state':state,'session':session['id'],'id':row['id']})
    if event_id:
        conn.execute(text('INSERT INTO rtm_management_webhooks(event_id,mode,account_id,obligation_id) VALUES (:event,:mode,:account,:id)'),
            {'event':event_id,'mode':row['mode'],'account':row['account_id'],'id':row['id']})
    settled=obligation(conn,obligation_id=row['id'])
    from rtm_core.management_generation import enqueue
    enqueue(conn,settled)
    result=public_payment(settled)
    if url:result['checkout_url']=url
    return result


def public_payment(row):
    if not row:return {'state':'not_started'}
    return {'id':row['id'],'quote_id':row['quote_id'],'state':row['state'],
            'mode':row['mode'],'amount_cents':row['amount_cents'],'currency':'eur',
            'stage':'management_balance','submission_authorized':False}
