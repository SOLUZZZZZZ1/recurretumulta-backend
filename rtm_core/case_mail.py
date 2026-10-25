"""Case-linked mail core. No transport, credentials, AI or external side effects.

Original RFC822 bytes belong in protected custody; projections are for OPS only.
No inbound message can modify a case, participant, contract, payment or resource.
"""
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.message import EmailMessage
from email.utils import getaddresses, format_datetime, parsedate_to_datetime
import hashlib
import json
import re
from uuid import UUID

MAILBOX = 'expedientes@resuelvetusmovidas.es'
DISPLAY_NAME = 'RTM · Resuelve tus movidas'
MAX_MESSAGE = 20 * 1024 * 1024
MAX_PART = 12 * 1024 * 1024
MESSAGE_ID = re.compile(r'<[^<>\s\r\n]{1,240}@[^<>\s\r\n]{1,240}>')
SAFE_TYPES = {'application/pdf','image/png','image/jpeg','image/webp','text/plain'}


class MailRejected(ValueError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def address(value):
    value = str(value).strip().lower()
    if len(value)>254 or not re.fullmatch(r'[a-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,63}', value):
        raise MailRejected('MAIL_ADDRESS_INVALID')
    return value


def addresses(values):
    result=[]
    for _,value in getaddresses(values):
        item=address(value)
        if item not in result: result.append(item)
    if len(result)>20: raise MailRejected('MAIL_TOO_MANY_PARTICIPANTS')
    return result


def parse_original(raw):
    if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_MESSAGE:
        raise MailRejected('MAIL_SIZE_REJECTED')
    msg=BytesParser(policy=policy.default).parsebytes(raw)
    if msg.defects: raise MailRejected('MAIL_MIME_INVALID')
    if len(msg.get_all('Message-ID',[]))!=1 or len(msg.get_all('From',[]))!=1:
        raise MailRejected('MAIL_IDENTIFIERS_AMBIGUOUS')
    original_id=str(msg.get('Message-ID','')).strip()
    if not MESSAGE_ID.fullmatch(original_id): raise MailRejected('MAIL_MESSAGE_ID_INVALID')
    senders=addresses(msg.get_all('From',[]))
    if len(senders)!=1:raise MailRejected('MAIL_SENDER_AMBIGUOUS')
    recipients=addresses(msg.get_all('To',[])+msg.get_all('Cc',[]))
    references=[]
    for header in msg.get_all('In-Reply-To',[])+msg.get_all('References',[]):
        references+=MESSAGE_ID.findall(str(header))
    references=list(dict.fromkeys(references))[-50:]
    try:
        sent=parsedate_to_datetime(str(msg.get('Date','')))
        if sent.tzinfo is None:raise ValueError()
        sent=sent.isoformat()
    except (ValueError,TypeError,OverflowError):sent=None
    text_parts=[];attachments=[];parts=list(msg.walk())
    if len(parts)>100:raise MailRejected('MAIL_PART_COUNT_REJECTED')
    for index,part in enumerate(parts):
        if part.is_multipart():continue
        data=part.get_payload(decode=True) or b''
        if len(data)>MAX_PART:raise MailRejected('MAIL_ATTACHMENT_TOO_LARGE')
        mime=part.get_content_type();filename=part.get_filename()
        if part.get_content_disposition()=='attachment' or filename or mime not in {'text/plain','text/html'}:
            # Always quarantined until a separate scanner validates the exact hash.
            attachments.append({'part':index,'name':str(filename or 'Adjunto sin nombre')[:200],
                'mime':mime,'size_bytes':len(data),'sha256':sha(data),'state':'quarantined',
                'supported_view':mime in SAFE_TYPES})
        elif mime=='text/plain':
            try:text_parts.append(data.decode(part.get_content_charset() or 'utf-8',errors='replace'))
            except LookupError:text_parts.append(data.decode('utf-8',errors='replace'))
        # HTML and remote images are never rendered/executed. Original is preserved.
    # Canonical display text only. The exact RFC822 bytes/hash remain in custody.
    body='\n'.join(text_parts).replace('\r\n','\n').replace('\r','\n')
    if len(body)>100000:raise MailRejected('MAIL_BODY_TOO_LARGE')
    return {'original_id':original_id,'sender':senders[0],'recipients':recipients,
        'subject':str(msg.get('Subject',''))[:500], 'sent_at':sent,
        'body':body,'text_available':bool(text_parts), 'references':references,
        'attachments':attachments,'raw_sha256':sha(raw),
        'automation_authority':False,'read_by_client':None}


def correlate(message, known_threads, participants, *, direction, trusted_sender=False):
    """Headers AND approved participants AND trusted reception evidence required.

    trusted_sender must come from the receiver's verified authentication result,
    never from an Authentication-Results header supplied by the sender.
    """
    if direction not in {'incoming','outgoing_external'}:
        raise MailRejected('MAIL_DIRECTION_INVALID')
    candidates={known_threads[ref] for ref in message['references'] if ref in known_threads}
    if len(candidates)!=1:return None,'thread_missing_or_ambiguous'
    case_id=next(iter(candidates));allowed=set(participants.get(case_id,()))
    if direction=='incoming':
        if not trusted_sender:return None,'sender_authentication_unverified'
        if message['sender'] not in allowed or MAILBOX not in message['recipients']:
            return None,'participants_do_not_match'
    elif message['sender']!=MAILBOX or not message['recipients'] or not set(message['recipients']).issubset(allowed):
        return None,'participants_do_not_match'
    return case_id,'thread_and_participants_verified'


def prepare_original(*, operation_id, recipient, subject, body, reply_to_id=None, references=(), now=None):
    operation=str(UUID(str(operation_id)));recipient=address(recipient)
    if not isinstance(subject,str) or not 1<=len(subject)<=200 or any(c in subject for c in '\r\n\0'):
        raise MailRejected('MAIL_SUBJECT_INVALID')
    if not isinstance(body,str) or not 1<=len(body)<=50000 or '\0' in body:
        raise MailRejected('MAIL_BODY_INVALID')
    refs=list(references)[-49:]
    if reply_to_id:refs.append(reply_to_id)
    if any(not MESSAGE_ID.fullmatch(ref) for ref in refs):raise MailRejected('MAIL_REFERENCE_INVALID')
    msg=EmailMessage(policy=policy.SMTP)
    msg['From']=f'{DISPLAY_NAME} <{MAILBOX}>';msg['To']=recipient
    msg['Subject']=subject;msg['Date']=format_datetime(now or datetime.now(timezone.utc))
    msg['Message-ID']=f'<rtm.{operation}@resuelvetusmovidas.es>'
    if reply_to_id:msg['In-Reply-To']=reply_to_id
    if refs:msg['References']=' '.join(dict.fromkeys(refs))
    msg.set_content(body)
    return msg.as_bytes()


def safe_delivery_result(*, accepted=False, smtp_data_started=False, definite_rejection=False):
    if accepted:return 'provider_accepted'
    if definite_rejection:return 'failed'
    return 'unknown' if smtp_data_started else 'failed'


def extract_attachment(raw, part_index, expected_sha):
    parsed=parse_original(raw)
    item=next((p for p in parsed['attachments'] if p['part']==part_index),None)
    if not item or item['sha256']!=expected_sha:raise MailRejected('MAIL_ATTACHMENT_MISMATCH')
    msg=BytesParser(policy=policy.default).parsebytes(raw)
    return list(msg.walk())[part_index].get_payload(decode=True) or b'',item


def delivery_report(raw):
    """Parse a DSN as a claim, never as trusted delivery evidence on its own."""
    msg=BytesParser(policy=policy.default).parsebytes(raw)
    if msg.get_content_type()!='multipart/report' or msg.get_param('report-type')!='delivery-status':return None
    targets=[];references=[]
    for part in msg.walk():
        if part.get_content_type()=='message/delivery-status':
            for block in part.get_payload():
                action=str(block.get('Action','')).lower()
                recipient=str(block.get('Final-Recipient','')).split(';')[-1].strip()
                if action in {'delivered','failed'} and recipient:
                    targets.append({'recipient':address(recipient),'state':'delivered' if action=='delivered' else 'bounced',
                        'status':str(block.get('Status',''))[:20]})
        if part.get_content_type()=='message/rfc822':
            for embedded in part.get_payload():
                mid=str(embedded.get('Message-ID','')).strip()
                if MESSAGE_ID.fullmatch(mid):references.append(mid)
    if len(set(references))!=1 or len(targets)!=1:return None
    return {'original_id':references[0],**targets[0],'raw_sha256':sha(raw)}
