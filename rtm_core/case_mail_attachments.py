"""Bounded passive viewing checks. Never promotes an attachment into case documents.

This is not an antivirus certificate: originals remain quarantined from exports
and active execution. Only text or re-encoded pixels may be returned to OPS.
"""
from sqlalchemy import text
from rtm_core.case_mail import extract_attachment,MailRejected
from rtm_core.case_mail_repository import obj,now
from rtm_core.document_viewer import render

SCANNER='rtm_passive_view_validation_v1'


def schedule_checks(conn,message_id,payload):
    """Caller commits these markers together with the message and its IMAP UID."""
    for part in payload['attachments']:
        conn.execute(text('INSERT INTO rtm_case_mail_attachment_checks(message_id,part,sha256,state,scanner) '
            "VALUES (:id,:part,:sha,'unavailable',:scanner) ON CONFLICT(message_id,part,sha256) DO NOTHING"),
            {'id':message_id,'part':part['part'],'sha':part['sha256'],'scanner':SCANNER})


def inspect(data,mime):
    if mime=='text/plain':
        if len(data)>65536:raise MailRejected('MAIL_TEXT_TOO_LARGE')
        value=data.decode('utf-8')
        if any(ord(c)<32 and c not in '\n\t\r' for c in value):raise MailRejected('MAIL_TEXT_CONTROL_CHARACTERS')
        return {'view':'escaped_text'}
    if mime not in {'application/pdf','image/jpeg','image/png','image/webp'}:
        raise MailRejected('MAIL_ACTIVE_OR_UNSUPPORTED_TYPE')
    metadata=render(data,mime)
    render(data,mime,page=1,width=400)
    return {'view':'raster_pages','pages':metadata['pages']}


def scan_message(engine,message_id,storage):
    with engine.begin() as conn:
        row=conn.execute(text('SELECT payload FROM rtm_case_messages WHERE id=:id AND case_id IS NOT NULL'),{'id':message_id}).mappings().one_or_none()
        if not row:return 0
        payload=obj(row['payload'])
        original=conn.execute(text('SELECT custody_key,sha256 FROM rtm_case_mail_originals WHERE message_id=:id AND sha256=:sha'),
            {'id':message_id,'sha':payload['raw_sha256']}).mappings().first()
    if not payload['attachments']:return 0
    # Commit retry markers before fetching custody. An IMAP cursor may already
    # have advanced; an interrupted fetch must remain discoverable after restart.
    with engine.begin() as conn:
        schedule_checks(conn,message_id,payload)
        conn.execute(text("UPDATE rtm_case_mail_attachment_checks SET scanned_at=:now WHERE message_id=:id AND state='unavailable'"),
            {'now':now(),'id':message_id})
    if not original:raise MailRejected('MAIL_ORIGINAL_UNAVAILABLE')
    raw=storage.get(original['custody_key'],original['sha256']);count=0
    for part in payload['attachments']:
        with engine.begin() as conn:
            prior=conn.execute(text('SELECT state FROM rtm_case_mail_attachment_checks WHERE message_id=:id AND part=:part AND sha256=:sha'),
                {'id':message_id,'part':part['part'],'sha':part['sha256']}).scalar()
        if prior in {'clean','rejected'}:continue
        try:
            data,meta=extract_attachment(raw,part['part'],part['sha256']);inspect(data,meta['mime']);state='clean'
        except (MailRejected,UnicodeError):state='rejected'
        except Exception:state='unavailable'
        with engine.begin() as conn:
            conn.execute(text("UPDATE rtm_case_mail_attachment_checks SET state=:state,scanner=:scanner,scanned_at=:now "
                "WHERE message_id=:id AND part=:part AND sha256=:sha AND state='unavailable'"),
                {'id':message_id,'part':part['part'],'sha':part['sha256'],'state':state,'scanner':SCANNER,'now':now()})
        count+=1
    return count


def retry_pending(engine,mailbox,storage):
    """Oldest unresolved validation first; bounded and independent of IMAP UID."""
    with engine.begin() as conn:
        pending=conn.execute(text("SELECT a.message_id FROM rtm_case_mail_attachment_checks a "
            "JOIN rtm_case_messages m ON m.id=a.message_id WHERE a.state='unavailable' "
            "AND m.case_id IS NOT NULL AND m.mailbox=:mailbox GROUP BY a.message_id "
            "ORDER BY MIN(a.scanned_at),a.message_id LIMIT 25"),{'mailbox':mailbox}).scalars().all()
    result={'attempted':len(pending),'completed':0,'unavailable':0}
    for mid in pending:
        try:
            scan_message(engine,str(mid),storage)
            with engine.begin() as conn:
                unresolved=conn.execute(text("SELECT COUNT(*) FROM rtm_case_mail_attachment_checks WHERE message_id=:id AND state='unavailable'"),{'id':str(mid)}).scalar()
            result['unavailable' if unresolved else 'completed']+=1
        except Exception:result['unavailable']+=1
    return result
