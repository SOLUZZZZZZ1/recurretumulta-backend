"""Persistent conversation/outbox. Caller owns transactions; never sends inline."""
from datetime import datetime,timezone,timedelta
import json
from uuid import uuid4
from sqlalchemy import text
from fastapi import HTTPException
from rtm_core.case_mail import MAILBOX,sha,prepare_original,parse_original,correlate,delivery_report
from rtm_core.paid_study import canonical,digest


def jvalue(conn,name):return f'CAST(:{name} AS jsonb)' if conn.dialect.name=='postgresql' else ':'+name
def obj(value):return json.loads(value) if isinstance(value,str) else value
def now():return datetime.now(timezone.utc)


def event(conn,message_id,state,actor=None,evidence=None):
    conn.execute(text('INSERT INTO rtm_case_mail_events(id,message_id,state,actor_id,evidence) VALUES (:id,:message_id,:state,:actor_id,'+jvalue(conn,'evidence')+')'),
        {'id':str(uuid4()),'message_id':message_id,'state':state,'actor_id':actor,'evidence':canonical(evidence or {})})


def public_message(row):
    return {'id':str(row['id']),'case_id':str(row['case_id']) if row['case_id'] else None,
        'direction':row['direction'],'state':row['state'],'message':obj(row['payload']),
        'relation':obj(row['relation']),'author_id':str(row['author_id']) if row['author_id'] else None,
        'created_at':str(row['created_at'])}


def message_for_case(conn,case_id,message_id):
    row=conn.execute(text('SELECT * FROM rtm_case_messages WHERE case_id=:case_id AND id=:id'),{'case_id':case_id,'id':message_id}).mappings().one_or_none()
    if not row:raise HTTPException(404,{'code':'MAIL_NOT_FOUND_IN_CASE'})
    return row


def conversation(conn,case_id,query=''):
    if len(query)>200:raise HTTPException(422,{'code':'MAIL_SEARCH_TOO_LONG'})
    # Parameterized, literal LIKE search. No message contents in application logs.
    term=query.replace('!','!!').replace('%','!%').replace('_','!_')
    rows=conn.execute(text("SELECT * FROM rtm_case_messages WHERE case_id=:case_id AND LOWER(CAST(payload AS TEXT)) LIKE :query ESCAPE '!' ORDER BY created_at,id LIMIT 201"),{'case_id':case_id,'query':'%'+term.lower()+'%'}).mappings().all()
    participants=conn.execute(text('SELECT email FROM rtm_case_mail_participants WHERE case_id=:case_id AND active=true ORDER BY email'),{'case_id':case_id}).scalars().all()
    sync=conn.execute(text('SELECT folder,last_attempt_at,last_success_at,error_code FROM rtm_case_mail_sync WHERE mailbox=:mailbox'),{'mailbox':MAILBOX}).mappings().all()
    return {'ok':True,'case_id':case_id,'messages':[public_message(r) for r in rows[:200]],
        'truncated':len(rows)>200,'verified_recipients':list(participants),
        'sync':{'state':'unconfigured' if not sync else ('error' if any(r['error_code'] for r in sync) else 'observed'),
                'folders':[{k:str(v) if v is not None else None for k,v in r.items()} for r in sync]},
        'mailbox':MAILBOX,'client_read_receipt':False}


def prepare(conn,*,case_id,session,operation_id,recipient,subject,body,reply_id,storage,relation=None):
    request={'case_id':case_id,'recipient':recipient,'subject':subject,'body':body,'reply_id':reply_id,'relation':relation or {}}
    request_sha=digest(request)
    lock=' FOR UPDATE' if conn.dialect.name=='postgresql' else ''
    if not conn.execute(text('SELECT id FROM cases WHERE id=:case_id'+lock),{'case_id':case_id}).scalar():
        raise HTTPException(404,{'code':'CASEWORK_CASE_NOT_FOUND'})
    existing=conn.execute(text('SELECT * FROM rtm_case_messages WHERE case_id=:case_id AND operation_id=:operation_id'),{'case_id':case_id,'operation_id':operation_id}).mappings().one_or_none()
    if existing:
        if existing['request_sha256']!=request_sha or str(existing['author_id'])!=session.operator_id:
            raise HTTPException(409,{'code':'MAIL_OPERATION_CONFLICT'})
        return public_message(existing)
    allowed=conn.execute(text('SELECT email FROM rtm_case_mail_participants WHERE case_id=:case_id AND email=:email AND active=true'),{'case_id':case_id,'email':recipient}).scalar()
    if not allowed:raise HTTPException(409,{'code':'MAIL_RECIPIENT_NOT_VERIFIED'})
    reply=None
    if reply_id:reply=message_for_case(conn,case_id,reply_id)
    if relation:
        # Public callers cannot invent a budget/review approval or delivery link.
        raise HTTPException(409,{'code':'MAIL_VERSION_ADAPTER_REQUIRED'})
    raw=prepare_original(operation_id=operation_id,recipient=allowed,subject=subject,body=body,
        reply_to_id=reply['original_id'] if reply else None,
        references=obj(reply['payload'])['references'] if reply else ())
    projection=parse_original(raw);key=storage.put(raw)
    message_id=operation_id
    conn.execute(text('''INSERT INTO rtm_case_messages
        (id,case_id,mailbox,direction,original_id,operation_id,request_sha256,state,payload,relation,author_id)
        VALUES (:id,:case_id,:mailbox,'outgoing',:original_id,:operation_id,:request_sha,'prepared','''+
        jvalue(conn,'payload')+','+jvalue(conn,'relation')+',:author_id)'),
        {'id':message_id,'case_id':case_id,'mailbox':MAILBOX,'original_id':projection['original_id'],
         'operation_id':operation_id,'request_sha':request_sha,'payload':canonical(projection),
         'relation':'{}','author_id':session.operator_id})
    conn.execute(text('''INSERT INTO rtm_case_mail_originals(id,message_id,sha256,custody_key,source_key,size_bytes)
        VALUES (:id,:message_id,:sha,:key,:source,:size)'''),
        {'id':str(uuid4()),'message_id':message_id,'sha':sha(raw),'key':key,
         'source':'prepared:'+message_id,'size':len(raw)})
    event(conn,message_id,'prepared',session.operator_id,{'operation_id':operation_id})
    return public_message(message_for_case(conn,case_id,message_id))


def queue(conn,case_id,message_id,session):
    lock=' FOR UPDATE' if conn.dialect.name=='postgresql' else ''
    row=conn.execute(text('SELECT * FROM rtm_case_messages WHERE id=:id AND case_id=:case_id'+lock),{'id':message_id,'case_id':case_id}).mappings().one_or_none()
    if not row:raise HTTPException(404,{'code':'MAIL_NOT_FOUND_IN_CASE'})
    if row['state'] in {'queued','sending','provider_accepted','delivered','unknown'}:return public_message(row)
    if row['state']!='prepared':raise HTTPException(409,{'code':'MAIL_STATE_NOT_QUEUEABLE'})
    # Recheck participant immediately before queueing. Worker checks again.
    recipient=obj(row['payload'])['recipients'][0]
    if not conn.execute(text('SELECT id FROM rtm_case_mail_participants WHERE case_id=:case_id AND email=:email AND active=true'),{'case_id':case_id,'email':recipient}).scalar():
        raise HTTPException(409,{'code':'MAIL_RECIPIENT_NOT_VERIFIED'})
    conn.execute(text("UPDATE rtm_case_messages SET state='queued' WHERE id=:id"),{'id':message_id})
    conn.execute(text("INSERT INTO rtm_case_mail_jobs(message_id,state) VALUES (:id,'queued')"),{'id':message_id})
    event(conn,message_id,'queued',session.operator_id,{'explicit_operator_approval':True})
    return public_message(message_for_case(conn,case_id,message_id))


def claim(conn):
    lock=' FOR UPDATE SKIP LOCKED' if conn.dialect.name=='postgresql' else ''
    row=conn.execute(text("SELECT message_id FROM rtm_case_mail_jobs WHERE state='queued' ORDER BY message_id LIMIT 1"+lock)).mappings().one_or_none()
    if not row:return None
    token=str(uuid4());mid=str(row['message_id'])
    conn.execute(text("UPDATE rtm_case_mail_jobs SET state='sending',lease_token=:token,lease_until=:until,started_at=:now WHERE message_id=:id AND state='queued'"),{'token':token,'until':now()+timedelta(minutes=2),'now':now(),'id':mid})
    conn.execute(text("UPDATE rtm_case_messages SET state='sending' WHERE id=:id"),{'id':mid})
    event(conn,mid,'sending',evidence={'lease_token':token})
    return mid,token


def finish(conn,message_id,lease_token,state,code):
    if state not in {'provider_accepted','failed','unknown'}:raise ValueError('MAIL_RESULT_INVALID')
    result=conn.execute(text("UPDATE rtm_case_mail_jobs SET state=:state,finished_at=:now WHERE message_id=:id AND lease_token=:token AND state='sending'"),{'state':'unknown' if state=='unknown' else 'closed','now':now(),'id':message_id,'token':lease_token})
    if result.rowcount!=1:return False
    conn.execute(text('UPDATE rtm_case_messages SET state=:state WHERE id=:id'),{'state':state,'id':message_id})
    event(conn,message_id,state,evidence={'code':code})
    return True


def expire_leases(conn):
    rows=conn.execute(text("SELECT message_id,lease_token FROM rtm_case_mail_jobs WHERE state='sending' AND lease_until<:now"),{'now':now()}).mappings().all()
    for row in rows:finish(conn,str(row['message_id']),str(row['lease_token']),'unknown','WORKER_INTERRUPTED_RECONCILE_REQUIRED')
    return len(rows)


def ingest(conn,raw,*,folder,uidvalidity,uid,direction,trusted_sender,storage):
    source=f'{MAILBOX}:{folder}:{uidvalidity}:{uid}'
    if conn.execute(text('SELECT id FROM rtm_case_mail_originals WHERE source_key=:source'),{'source':source}).scalar():return 'duplicate'
    message=parse_original(raw)
    if direction=='incoming' and MAILBOX not in message['recipients']:
        return 'outside_dedicated_mailbox_scope'
    if direction=='outgoing_external' and message['sender']!=MAILBOX:
        return 'outside_dedicated_mailbox_scope'
    known={r['original_id']:str(r['case_id']) for r in conn.execute(text('SELECT original_id,case_id FROM rtm_case_messages WHERE case_id IS NOT NULL')).mappings()}
    participants={}
    for row in conn.execute(text('SELECT case_id,email FROM rtm_case_mail_participants WHERE active=true')).mappings():
        participants.setdefault(str(row['case_id']),[]).append(row['email'])
    case_id,reason=correlate(message,known,participants,direction=direction,trusted_sender=trusted_sender)
    existing=conn.execute(text('SELECT * FROM rtm_case_messages WHERE mailbox=:mailbox AND original_id=:original_id'),{'mailbox':MAILBOX,'original_id':message['original_id']}).mappings().one_or_none()
    if existing:
        # A sent-folder copy confirms provider acceptance only if exact semantic
        # content and verified recipients match; never equates this with delivery.
        old=obj(existing['payload'])
        matches=all(message[k]==old[k] for k in ('sender','recipients','subject','body','attachments'))
        if not matches:raise HTTPException(409,{'code':'MAIL_MESSAGE_ID_COLLISION'})
        mid=str(existing['id'])
        if direction=='outgoing_external' and existing['direction']=='outgoing' and existing['state'] in {'sending','unknown'}:
            conn.execute(text("UPDATE rtm_case_messages SET state='provider_accepted' WHERE id=:id"),{'id':mid})
            conn.execute(text("UPDATE rtm_case_mail_jobs SET state='closed',finished_at=:now WHERE message_id=:id"),{'id':mid,'now':now()})
            event(conn,mid,'provider_accepted',evidence={'source':'matching_sent_folder_copy','delivery_confirmed':False})
    else:
        mid=str(uuid4());state='received' if case_id else 'pending_assignment'
        conn.execute(text('''INSERT INTO rtm_case_messages(id,case_id,mailbox,direction,original_id,state,payload,relation)
            VALUES (:id,:case_id,:mailbox,:direction,:original_id,:state,'''+jvalue(conn,'payload')+','+jvalue(conn,'relation')+')'),
            {'id':mid,'case_id':case_id,'mailbox':MAILBOX,'direction':direction,'original_id':message['original_id'],
             'state':state,'payload':canonical(message),'relation':canonical({'link_reason':reason})})
        event(conn,mid,state,evidence={'link_reason':reason,'no_legal_or_payment_effect':True})
    key=storage.put(raw)
    conn.execute(text('''INSERT INTO rtm_case_mail_originals(id,message_id,sha256,custody_key,source_key,size_bytes)
        VALUES (:id,:mid,:sha,:key,:source,:size)'''),{'id':str(uuid4()),'mid':mid,'sha':sha(raw),'key':key,'source':source,'size':len(raw)})
    return mid


def record_delivery_claim(conn,raw,*,trusted_provider=False):
    report=delivery_report(raw)
    if not report or not trusted_provider:return False
    row=conn.execute(text("SELECT * FROM rtm_case_messages WHERE mailbox=:mailbox AND original_id=:id AND direction='outgoing'"),
        {'mailbox':MAILBOX,'id':report['original_id']}).mappings().one_or_none()
    if not row or obj(row['payload'])['recipients']!=[report['recipient']]:return False
    if row['state'] not in {'provider_accepted','unknown','sending'}:return False
    conn.execute(text('UPDATE rtm_case_messages SET state=:state WHERE id=:id'),{'state':report['state'],'id':str(row['id'])})
    conn.execute(text("UPDATE rtm_case_mail_jobs SET state='closed',finished_at=:now WHERE message_id=:id"),{'now':now(),'id':str(row['id'])})
    event(conn,str(row['id']),report['state'],evidence={'verified_delivery_report_sha256':report['raw_sha256'],'status':report['status']})
    return True


def pending_assignment(conn):
    rows=conn.execute(text("SELECT * FROM rtm_case_messages WHERE mailbox=:mailbox AND case_id IS NULL AND state='pending_assignment' ORDER BY created_at,id LIMIT 101"),{'mailbox':MAILBOX}).mappings().all()
    return {'ok':True,'messages':[public_message(r) for r in rows[:100]],'truncated':len(rows)>100}


def assign_pending(conn,case_id,message_id,session,evidence):
    lock=' FOR UPDATE' if conn.dialect.name=='postgresql' else ''
    row=conn.execute(text('SELECT * FROM rtm_case_messages WHERE id=:id'+lock),{'id':message_id}).mappings().one_or_none()
    if not row:raise HTTPException(404,{'code':'MAIL_PENDING_NOT_FOUND'})
    if row['case_id'] is not None or row['state']!='pending_assignment':raise HTTPException(409,{'code':'MAIL_ASSIGNMENT_CHANGED'})
    message=obj(row['payload']);participant=message['sender'] if row['direction']=='incoming' else message['recipients'][0]
    if not conn.execute(text('SELECT id FROM rtm_case_mail_participants WHERE case_id=:case_id AND email=:email AND active=true'),{'case_id':case_id,'email':participant}).scalar():
        raise HTTPException(409,{'code':'MAIL_PARTICIPANT_REQUIRES_VERIFICATION'})
    relation={'link_reason':'operator_verified_assignment','verification':evidence}
    conn.execute(text("UPDATE rtm_case_messages SET case_id=:case_id,state='received',relation="+jvalue(conn,'relation')+' WHERE id=:id'),{'case_id':case_id,'relation':canonical(relation),'id':message_id})
    event(conn,message_id,'assigned',session.operator_id,{'case_id':case_id,'verification':evidence,'no_legal_or_payment_effect':True})
    return public_message(message_for_case(conn,case_id,message_id))
