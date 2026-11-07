"""Production adaptation of October one-use links and hashed client sessions.

Case scope and verified recipient are server-derived. Credentials never appear
in the OPS projection. The private mail original is retained in protected custody.
"""
import hashlib
import json
import re
import secrets
import time
from uuid import uuid4
from sqlalchemy import text
from rtm_core.management_contract import ManagementRejected,canonical
from rtm_core.management_store import lock_case,jparam,obj
from rtm_core.management_mail import verified_recipient
from rtm_core.case_mail import prepare_original,parse_original,sha
from rtm_core.case_mail_repository import event as mail_event

COOKIE='__Host-rtm_client_session'
TOKEN=re.compile(r'[A-Za-z0-9_-]{43}\Z')
LINK_TTL=900
SESSION_TTL=1800
NEUTRAL='Si el correo corresponde al expediente habilitado, recibirás un enlace privado para entrar.'


def verifier(value):return hashlib.sha256(value.encode()).hexdigest()


def quota(conn,key,now,limit,window):
    row=conn.execute(text('''INSERT INTO rtm_client_throttles(bucket_key,window_start,attempts)
        VALUES (:key,:now,1) ON CONFLICT(bucket_key) DO UPDATE SET
        attempts=CASE WHEN rtm_client_throttles.window_start<=:cutoff THEN 1 ELSE rtm_client_throttles.attempts+1 END,
        window_start=CASE WHEN rtm_client_throttles.window_start<=:cutoff THEN :now ELSE rtm_client_throttles.window_start END
        WHERE rtm_client_throttles.window_start<=:cutoff OR rtm_client_throttles.attempts<:limit RETURNING attempts'''),
        {'key':verifier(key),'now':now,'cutoff':now-window,'limit':limit}).first()
    return row is not None


def request_link(conn,*,case_id,email,peer,policy,storage,mailbox):
    now=int(time.time());email=str(email).strip().lower()
    if not quota(conn,'client-ip:'+peer,now,20,900) or not quota(conn,'client-address:'+email,now,3,900):return
    if case_id!=policy.case_id:return
    lock_case(conn,case_id)
    try:recipient=verified_recipient(conn,case_id)
    except ManagementRejected:return
    if email!=recipient:return
    token=secrets.token_urlsafe(32);mid=str(uuid4())
    conn.execute(text('INSERT INTO rtm_client_links(verifier,recipient_hash,case_ids,issued_at,expires_at) VALUES (:v,:r,:cases,:now,:expiry)'),
        {'v':verifier(token),'r':verifier(recipient),'cases':json.dumps([case_id]),'now':now,'expiry':now+LINK_TTL})
    body=('Has solicitado entrar en tu expediente. Este enlace es privado, de un solo uso y válido durante 15 minutos.\n\n'
          +policy.origin+'/#/cliente/expediente/'+case_id+'?enlace='+token+'\n\n'
          'No lo reenvíes. Si no lo solicitaste, no necesitas hacer nada.\nRTM · Resuelve tus movidas')
    raw=prepare_original(operation_id=mid,recipient=recipient,subject='Tu acceso privado · RTM',body=body,mailbox=mailbox)
    projection=parse_original(raw);key=storage.put(raw)
    relation={'type':'client_access','credential_body_private':True,'expires_at':now+LINK_TTL,'link_verifier':verifier(token)}
    conn.execute(text('INSERT INTO rtm_case_messages '
        '(id,case_id,mailbox,direction,original_id,operation_id,request_sha256,state,payload,relation) '
        "VALUES (:id,:case,:mailbox,'outgoing',:original,:id,:sha,'queued',"+jparam(conn,'payload')+','+jparam(conn,'relation')+')'),
        {'id':mid,'case':case_id,'mailbox':mailbox,'original':projection['original_id'],'sha':sha(raw),
         'payload':canonical(projection),'relation':canonical(relation)})
    conn.execute(text('INSERT INTO rtm_case_mail_originals(id,message_id,sha256,custody_key,source_key,size_bytes) '
        'VALUES (:id,:mid,:sha,:key,:source,:size)'),{'id':str(uuid4()),'mid':mid,'sha':sha(raw),'key':key,'source':'prepared:'+mid,'size':len(raw)})
    conn.execute(text("INSERT INTO rtm_case_mail_jobs(message_id,state) VALUES (:id,'queued')"),{'id':mid})
    mail_event(conn,mid,'queued',evidence={'client_requested_access':True,'credential_not_visible_to_ops':True})


def redeem(conn,*,token,previous,policy):
    now=int(time.time())
    if not TOKEN.fullmatch(token):raise ManagementRejected('CLIENT_LINK_INVALID',401)
    link=conn.execute(text('UPDATE rtm_client_links SET consumed_at=:now WHERE verifier=:v AND consumed_at IS NULL AND expires_at>:now '
        'RETURNING recipient_hash,case_ids'),{'now':now,'v':verifier(token)}).mappings().first()
    if (not link or json.loads(link['case_ids'])!=[policy.case_id]
            or verifier(verified_recipient(conn,policy.case_id))!=link['recipient_hash']):
        raise ManagementRejected('CLIENT_LINK_INVALID',401)
    if TOKEN.fullmatch(previous or ''):
        conn.execute(text('UPDATE rtm_client_sessions SET revoked_at=:now WHERE verifier=:v AND revoked_at IS NULL'),{'now':now,'v':verifier(previous)})
    secret=secrets.token_urlsafe(32)
    conn.execute(text('INSERT INTO rtm_client_sessions(verifier,recipient_hash,case_ids,issued_at,expires_at) VALUES (:v,:r,:cases,:now,:expiry)'),
        {'v':verifier(secret),'r':link['recipient_hash'],'cases':link['case_ids'],'now':now,'expiry':now+SESSION_TTL})
    return secret


def session(conn,*,token,case_id,policy):
    if not TOKEN.fullmatch(token or ''):raise ManagementRejected('CLIENT_SESSION_REQUIRED',401)
    suffix=' FOR SHARE' if conn.dialect.name=='postgresql' else ''
    row=conn.execute(text('SELECT * FROM rtm_client_sessions WHERE verifier=:v AND revoked_at IS NULL AND expires_at>:now'+suffix),
        {'v':verifier(token),'now':int(time.time())}).mappings().one_or_none()
    if not row:raise ManagementRejected('CLIENT_SESSION_REQUIRED',401)
    if (case_id!=policy.case_id or json.loads(row['case_ids'])!=[case_id]
            or row['recipient_hash']!=verifier(verified_recipient(conn,case_id))):
        raise ManagementRejected('CLIENT_CASE_NOT_AVAILABLE',404)
    return {'session_ref':row['verifier'],'recipient_hash':row['recipient_hash'],'expires_at':row['expires_at']}


def logout(conn,*,token,expected_ref):
    # A delayed logout can revoke only the exact session it was initiated for.
    # It never clears a newly set browser cookie or revokes a replacement session.
    if TOKEN.fullmatch(token or '') and verifier(token)==expected_ref:
        conn.execute(text('UPDATE rtm_client_sessions SET revoked_at=:now WHERE verifier=:v AND revoked_at IS NULL'),
            {'now':int(time.time()),'v':expected_ref})
    return {'ok':True,'closed_session_ref':expected_ref}
