"""Dedicated mailbox adapters. Disabled unless separately configured and approved.

TLS/host validation follows the reviewed October email_utils transport. Unlike
its bool interface, this adapter preserves DATA uncertainty and original IDs.
"""
from dataclasses import dataclass
import imaplib
import os
import re
import smtplib
import ssl
from fastapi import HTTPException
from sqlalchemy import text
from rtm_core.case_mail import MAILBOX,MAX_MESSAGE,MailRejected,sha,parse_original
from rtm_core.case_mail_repository import claim,finish,expire_leases,ingest,now,obj
from rtm_core.production_casework import load_policy


@dataclass(frozen=True,repr=False)
class MailConfig:
    smtp_host:str
    imap_host:str
    password:str
    allowed_recipients:tuple[str,...]
    folders:tuple[str,...]
    mailbox:str=MAILBOX
    username:str=MAILBOX
    smtp_port:int=465
    smtp_tls:str='implicit'
    authserv_id:str=''
    delivery_senders:tuple[str,...]=()


def configured_mailbox(source=None):
    source=os.environ if source is None else source
    from rtm_core.case_mail import address
    if source.get('RTM_CASE_MAIL_DEDICATED_CONFIRMED')!='1':
        raise MailRejected('MAILBOX_SCOPE_NOT_CONFIRMED')
    return address(source.get('RTM_CASE_MAIL_ADDRESS',''))


def configuration(source=None):
    source=os.environ if source is None else source
    if source.get('RTM_ENABLE_CASE_MAIL')!='1' or source.get('RTM_CASE_MAIL_WORKER_ENABLED')!='1':
        raise MailRejected('MAIL_WORKER_NOT_ENABLED')
    if not load_policy(source):raise MailRejected('MAIL_CASEWORK_PROFILE_REQUIRED')
    mailbox=configured_mailbox(source)
    if source.get('RTM_CASE_MAIL_DEDICATED_CONFIRMED')!='1':
        raise MailRejected('MAILBOX_SCOPE_NOT_CONFIRMED')
    hosts=[]
    allowed=set(source.get('RTM_CASE_MAIL_ALLOWED_HOSTS','').split(','))
    for key in ('RTM_CASE_MAIL_SMTP_HOST','RTM_CASE_MAIL_IMAP_HOST'):
        host=source.get(key,'').strip().lower()
        if not re.fullmatch(r'[a-z0-9.-]+\.[a-z]{2,63}',host) or host not in allowed:
            raise MailRejected('MAIL_HOST_NOT_APPROVED')
        hosts.append(host)
    secret=source.get('RTM_CASE_MAIL_PASSWORD','')
    if not secret:raise MailRejected('MAIL_CREDENTIAL_UNAVAILABLE')
    from rtm_core.case_mail import address
    recipients=tuple(address(v) for v in source.get('RTM_CASE_MAIL_ALLOWED_RECIPIENTS','').split(',') if v)
    if not recipients:raise MailRejected('MAIL_CONTROLLED_RECIPIENTS_REQUIRED')
    folders=tuple(source.get('RTM_CASE_MAIL_FOLDERS','').split(','))
    if not 1<=len(folders)<=3 or any(not re.fullmatch(r'[A-Za-z0-9_. /-]{1,80}',f) for f in folders):
        raise MailRejected('MAIL_FOLDERS_NOT_CONFIGURED')
    tls=source.get('RTM_CASE_MAIL_SMTP_TLS','implicit')
    if tls not in {'implicit','starttls'}:raise MailRejected('MAIL_TLS_CONFIGURATION_INVALID')
    port=465 if tls=='implicit' else 587
    username=source.get('RTM_CASE_MAIL_USERNAME',mailbox).strip()
    if not username or any(c in username for c in '\r\n\0'):raise MailRejected('MAIL_USERNAME_INVALID')
    # The trusted receiving boundary must be verified against the real provider.
    authserv=source.get('RTM_CASE_MAIL_AUTHSERV_ID','').strip().lower()
    reports=tuple(address(v) for v in source.get('RTM_CASE_MAIL_DELIVERY_SENDERS','').split(',') if v)
    return MailConfig(hosts[0],hosts[1],secret,recipients,folders,mailbox,username,port,tls,authserv,reports)


class B2MailStorage:
    def __init__(self,mailbox=None):
        from b2_storage import get_b2_bucket,get_s3_client
        self.bucket=get_b2_bucket();self.client=get_s3_client()
        self.mailbox=mailbox or configured_mailbox()
    def put(self,raw):
        if not 0<len(raw)<=MAX_MESSAGE:raise MailRejected('MAIL_SIZE_REJECTED')
        key='case-mail/'+sha(self.mailbox.encode())+'/'+sha(raw)+'.eml'
        # Content-addressed: retries can only write identical bytes to this key.
        self.client.put_object(Bucket=self.bucket,Key=key,Body=raw,ContentType='message/rfc822')
        return key
    def get(self,key,expected):
        prefix='case-mail/'+sha(self.mailbox.encode())+'/'
        if key!=prefix+expected+'.eml':raise MailRejected('MAIL_CUSTODY_SCOPE_INVALID')
        body=None
        try:
            response=self.client.get_object(Bucket=self.bucket,Key=key);body=response['Body']
            if not 0<response.get('ContentLength',0)<=MAX_MESSAGE:raise MailRejected('MAIL_SIZE_REJECTED')
            raw=body.read(MAX_MESSAGE+1)
            if sha(raw)!=expected:raise MailRejected('MAIL_CUSTODY_INTEGRITY_FAILED')
            return raw
        finally:
            if body is not None:body.close()


def send_original(config,raw,recipient,*,smtp_factory=None):
    if recipient not in config.allowed_recipients:return 'failed','RECIPIENT_OUTSIDE_CONTROLLED_PILOT'
    message=parse_original(raw)
    if message['sender']!=config.mailbox or message['recipients']!=[recipient] or message['attachments']:
        return 'failed','OUTGOING_MESSAGE_SCOPE_INVALID'
    smtp=None;data_started=False;accepted=False
    try:
        if config.smtp_tls=='starttls':
            smtp=(smtp_factory or smtplib.SMTP)(config.smtp_host,config.smtp_port,timeout=20)
            smtp.ehlo();smtp.starttls(context=ssl.create_default_context());smtp.ehlo()
        else:
            smtp=(smtp_factory or smtplib.SMTP_SSL)(config.smtp_host,config.smtp_port,timeout=20,context=ssl.create_default_context())
            smtp.ehlo()
        smtp.login(config.username,config.password)
        if smtp.mail(config.mailbox)[0]!=250:return 'failed','SMTP_MAIL_REJECTED'
        if smtp.rcpt(recipient)[0] not in {250,251}:return 'failed','SMTP_RECIPIENT_REJECTED'
        data_started=True
        code,_=smtp.data(raw)
        if code==250:
            accepted=True;return 'provider_accepted','SMTP_DATA_ACCEPTED'
        return 'failed','SMTP_DATA_EXPLICITLY_REJECTED'
    except smtplib.SMTPDataError:
        return 'failed','SMTP_DATA_EXPLICITLY_REJECTED'
    except Exception:
        return ('provider_accepted','SMTP_DATA_ACCEPTED') if accepted else (
            ('unknown','SMTP_DATA_RESULT_UNKNOWN') if data_started else ('failed','SMTP_CONNECTION_OR_AUTH_FAILED'))
    finally:
        if smtp is not None:
            try:smtp.close()
            except Exception:pass


def send_one(engine,config,storage,*,transport=send_original):
    with engine.begin() as conn:
        expire_leases(conn);job=claim(conn,config.mailbox)
    if not job:return False
    mid,token=job
    try:
        with engine.begin() as conn:
            row=conn.execute(text('SELECT * FROM rtm_case_messages WHERE id=:id'),{'id':mid}).mappings().one()
            recipient=obj(row['payload'])['recipients'][0]
            approved=conn.execute(text('SELECT id FROM rtm_case_mail_participants WHERE case_id=:case_id AND email=:email AND active=true'),{'case_id':str(row['case_id']),'email':recipient}).scalar()
            relation=obj(row['relation'])
            if relation.get('type')=='management_quote':
                from rtm_core.management_store import quote_row
                current=quote_row(conn,str(row['case_id']))
                approved=approved and current and str(current['id'])==relation['quote_id'] and current['state'] in {'communicated','accepted','settled'}
            elif relation.get('type')=='client_access':
                import time
                approved=approved and bool(conn.execute(text('SELECT verifier FROM rtm_client_links WHERE verifier=:v AND consumed_at IS NULL AND expires_at>:now'),
                    {'v':relation['link_verifier'],'now':int(time.time())}).scalar())
            original=conn.execute(text('SELECT custody_key,sha256 FROM rtm_case_mail_originals WHERE source_key=:source'),{'source':'prepared:'+mid}).mappings().one()
        if not approved:result,code='failed','RECIPIENT_VERIFICATION_REVOKED'
        else:
            raw=storage.get(original['custody_key'],original['sha256'])
            result,code=transport(config,raw,recipient)
    except Exception:
        # A claimed job is never returned automatically to the queue.
        result,code='unknown','WORKER_RESULT_UNKNOWN'
    with engine.begin() as conn:finish(conn,mid,token,result,code)
    return True


def advance_cursor(conn,config,folder,uid):
    conn.execute(text('UPDATE rtm_case_mail_sync SET last_uid=:uid WHERE mailbox=:mailbox AND folder=:folder AND last_uid<:uid'),
        {'uid':uid,'mailbox':config.mailbox,'folder':folder})


def quarantine_uid(engine,config,folder,validity,uid,size,code,storage,raw=None):
    # Size rejections retain the source in the read-only remote mailbox; no body
    # download. Bounded malformed originals are retained privately before commit.
    key=storage.put(raw) if raw is not None else None
    with engine.begin() as conn:
        conn.execute(text('''INSERT INTO rtm_case_mail_ingest_issues
            (mailbox,folder,uidvalidity,uid,code,disposition,size_bytes,raw_sha256,custody_key)
            VALUES (:m,:f,:v,:u,:c,:d,:s,:h,:k) ON CONFLICT(mailbox,folder,uidvalidity,uid) DO NOTHING'''),
            {'m':config.mailbox,'f':folder,'v':validity,'u':uid,'c':code,'s':size,
             'd':'quarantined' if raw is not None else 'remote_retained','h':sha(raw) if raw is not None else None,'k':key})
        advance_cursor(conn,config,folder,uid)


def sync_folder(engine,config,folder,storage,*,imap_factory=imaplib.IMAP4_SSL,sender_verifier=None,delivery_verifier=None):
    with engine.begin() as conn:
        cursor=conn.execute(text('SELECT * FROM rtm_case_mail_sync WHERE mailbox=:mailbox AND folder=:folder'),{'mailbox':config.mailbox,'folder':folder}).mappings().one_or_none()
        if not cursor:raise MailRejected('MAIL_SYNC_BASELINE_REQUIRED')
        conn.execute(text('UPDATE rtm_case_mail_sync SET last_attempt_at=:now WHERE mailbox=:mailbox AND folder=:folder'),{'now':now(),'mailbox':config.mailbox,'folder':folder})
    connection=None
    try:
        connection=imap_factory(config.imap_host,993,ssl_context=ssl.create_default_context(),timeout=20)
        connection.login(config.username,config.password)
        if connection.select('"'+folder+'"',readonly=True)[0]!='OK':raise MailRejected('MAIL_FOLDER_UNAVAILABLE')
        validity=connection.response('UIDVALIDITY')[1]
        if not validity or int(validity[0])!=cursor['uidvalidity']:raise MailRejected('MAIL_UIDVALIDITY_CHANGED_REVIEW_REQUIRED')
        status,data=connection.uid('search',None,'UID',f"{cursor['last_uid']+1}:*")
        if status!='OK':raise MailRejected('MAIL_SEARCH_FAILED')
        uids=sorted({int(v) for v in (data[0] or b'').split() if int(v)>cursor['last_uid']})[:50]
        for uid in uids:
            status,size_data=connection.uid('fetch',str(uid),'(RFC822.SIZE)')
            match=re.search(rb'RFC822.SIZE (\d+)',b' '.join(v for v in size_data if isinstance(v,bytes)))
            if status!='OK' or not match:raise MailRejected('MAIL_MESSAGE_SIZE_UNAVAILABLE')
            size=int(match[1])
            if not 0<size<=MAX_MESSAGE:
                quarantine_uid(engine,config,folder,cursor['uidvalidity'],uid,size,'MAIL_SIZE_REJECTED',storage)
                continue
            status,body_data=connection.uid('fetch',str(uid),'(BODY.PEEK[])')
            raws=[part[1] for part in body_data if isinstance(part,tuple) and isinstance(part[1],bytes)]
            if status!='OK' or len(raws)!=1:raise MailRejected('MAIL_FETCH_FAILED')
            raw=raws[0]
            if len(raw)!=size:raise MailRejected('MAIL_FETCH_SIZE_MISMATCH')
            try:parse_original(raw)
            except MailRejected as exc:
                quarantine_uid(engine,config,folder,cursor['uidvalidity'],uid,size,str(exc),storage,raw)
                continue
            verified=bool(sender_verifier and sender_verifier(raw))
            direction='incoming' if folder.upper()=='INBOX' else 'outgoing_external'
            if delivery_verifier is None:
                from rtm_core.case_mail_authentication import authenticated_delivery_report
                delivery_verified=authenticated_delivery_report(raw,config.delivery_senders)
            else:delivery_verified=bool(delivery_verifier(raw))
            try:
                with engine.begin() as conn:
                    ingest(conn,raw,folder=folder,uidvalidity=cursor['uidvalidity'],uid=uid,
                        direction=direction,trusted_sender=verified,storage=storage,mailbox=config.mailbox,trusted_delivery=delivery_verified)
                    advance_cursor(conn,config,folder,uid)
            except HTTPException as exc:
                if exc.status_code!=409 or exc.detail!={'code':'MAIL_MESSAGE_ID_COLLISION'}:raise
                quarantine_uid(engine,config,folder,cursor['uidvalidity'],uid,size,'MAIL_MESSAGE_ID_COLLISION',storage,raw)
            # Heavy validation is a separate, durable phase. Its markers already
            # committed with import/cursor; an interruption here cannot lose work.
        with engine.begin() as conn:
            conn.execute(text('UPDATE rtm_case_mail_sync SET last_success_at=:now,error_code=NULL WHERE mailbox=:mailbox AND folder=:folder'),{'now':now(),'mailbox':config.mailbox,'folder':folder})
        return len(uids)
    except Exception as exc:
        code=str(exc) if isinstance(exc,MailRejected) else 'MAIL_SYNC_FAILED'
        with engine.begin() as conn:
            conn.execute(text('UPDATE rtm_case_mail_sync SET error_code=:code WHERE mailbox=:mailbox AND folder=:folder'),{'code':code,'mailbox':config.mailbox,'folder':folder})
        raise MailRejected(code) from None
    finally:
        if connection:
            try:connection.logout()
            except Exception:pass


def tick(engine,*,source=None,storage=None,sender_verifier=None):
    config=configuration(source);storage=storage or B2MailStorage(config.mailbox)
    from rtm_core.case_mail_attachments import retry_pending
    result={'ok':True,'folders':{},'attachments':None,'outgoing':None}
    if sender_verifier is None:
        from rtm_core.case_mail_authentication import authenticated_sender
        sender_verifier=authenticated_sender
    for folder in config.folders:
        try:result['folders'][folder]={'ok':True,'processed':sync_folder(engine,config,folder,storage,sender_verifier=sender_verifier)}
        except Exception:
            result['ok']=False;result['folders'][folder]={'ok':False,'code':'MAIL_SYNC_INCOMPLETE'}
    try:
        result['attachments']=retry_pending(engine,config.mailbox,storage)
        if result['attachments']['unavailable']:result['ok']=False
    except Exception:result['ok']=False;result['attachments']={'code':'MAIL_VALIDATION_INCOMPLETE'}
    try:result['outgoing']=send_one(engine,config,storage)
    except Exception:result['ok']=False;result['outgoing']={'code':'MAIL_OUTBOX_INCOMPLETE'}
    return result
