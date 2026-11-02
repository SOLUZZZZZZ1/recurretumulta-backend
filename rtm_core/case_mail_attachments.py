"""Bounded passive viewing checks. Never promotes an attachment into case documents.

This is not an antivirus certificate: originals remain quarantined from exports
and active execution. Only text or re-encoded pixels may be returned to OPS.
"""
from sqlalchemy import text
from rtm_core.case_mail import extract_attachment,MailRejected
from rtm_core.case_mail_repository import obj
from rtm_core.document_viewer import render

SCANNER='rtm_passive_view_validation_v1'


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
    if not original:return 0
    raw=storage.get(original['custody_key'],original['sha256']);count=0
    for part in payload['attachments']:
        with engine.begin() as conn:
            prior=conn.execute(text('SELECT state FROM rtm_case_mail_attachment_checks WHERE message_id=:id AND part=:part AND sha256=:sha'),
                {'id':message_id,'part':part['part'],'sha':part['sha256']}).scalar()
        if prior:continue
        try:
            data,meta=extract_attachment(raw,part['part'],part['sha256']);inspect(data,meta['mime']);state='clean'
        except (MailRejected,UnicodeError):state='rejected'
        except Exception:state='unavailable'
        with engine.begin() as conn:
            conn.execute(text('INSERT INTO rtm_case_mail_attachment_checks(message_id,part,sha256,state,scanner) VALUES (:id,:part,:sha,:state,:scanner) '
                'ON CONFLICT(message_id,part,sha256) DO NOTHING'),{'id':message_id,'part':part['part'],'sha':part['sha256'],'state':state,'scanner':SCANNER})
        count+=1
    return count
