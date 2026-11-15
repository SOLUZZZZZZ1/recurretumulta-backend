"""Cryptographic DKIM check before automatic thread assignment.

Never trusts sender-supplied Authentication-Results. Missing/invalid signatures,
unsigned thread headers, body-length truncation and DNS failure remain ambiguous.
"""
from email import policy
from email.parser import BytesParser
from rtm_core.case_mail import parse_original


def authenticated_sender(raw,*,dnsfunc=None,delivery=False):
    try:
        import dkim
        projection=parse_original(raw)
        message=BytesParser(policy=policy.default).parsebytes(raw)
        domain=projection['sender'].rsplit('@',1)[1]
        signatures=message.get_all('DKIM-Signature',[])
        if len(signatures)>5:return False
        required={'from','to','subject','date','message-id'}
        for header in ('cc','in-reply-to','references'):
            if message.get_all(header):required.add(header)
        if delivery:required.update({'content-type','mime-version'})
        elif not required.intersection({'in-reply-to','references'}):return False
        if any(len(message.get_all(h,[]))>1 for h in required):return False
        for index,signature in enumerate(signatures):
            tags={}
            for fragment in str(signature).replace('\r','').replace('\n','').split(';'):
                if '=' not in fragment:continue
                key,value=fragment.strip().split('=',1)
                if key in tags:return False
                tags[key]=value.strip()
            if (tags.get('d','').lower()!=domain or 'l' in tags
                    or not required.issubset({h.strip().lower() for h in tags.get('h','').split(':')})):
                continue
            verifier=dkim.DKIM(raw,minkey=1024,timeout=5)
            if verifier.verify(idx=index,**({'dnsfunc':dnsfunc} if dnsfunc else {})):return True
        return False
    except Exception:
        return False


def authenticated_delivery_report(raw,approved_senders,*,dnsfunc=None):
    from rtm_core.case_mail import delivery_report
    try:
        return bool(approved_senders and parse_original(raw)['sender'] in approved_senders
            and delivery_report(raw) and authenticated_sender(raw,dnsfunc=dnsfunc,delivery=True))
    except Exception:return False


def sealed_delivery_notice(raw,approved_senders,approved_arc_domains,*,mailbox,dnsfunc=None):
    """Verify a provider seal for a *notice*, never grant sender/delivery trust.

    ARC can preserve an unsigned or forged sender's claim. The repository must
    match the returned message to the stored outgoing and leave its state alone.
    Only one complete ARC set is supported; other chains need separate review.
    """
    from rtm_core.case_mail import delivery_report
    try:
        import dkim
        import re
        projection=parse_original(raw);report=delivery_report(raw)
        if (not approved_senders or not approved_arc_domains or not report
                or projection['sender'] not in approved_senders
                or projection['recipients']!=[mailbox] or report['state']!='bounced'
                or not re.fullmatch(r'5\.[0-9]{1,3}\.[0-9]{1,3}',report['status'])):
            return None
        message=BytesParser(policy=policy.default).parsebytes(raw)
        required={'from','to','subject','date','message-id','mime-version','content-type'}
        if any(len(message.get_all(header,[]))!=1 for header in required):return None
        if any(len(message.get_all(header,[]))!=1 for header in
                ('ARC-Seal','ARC-Message-Signature','ARC-Authentication-Results')):return None
        if any(part.defects for part in message.walk()):return None
        for part in message.walk():
            if part.get_content_type()=='message/delivery-status':
                for block in part.get_payload():
                    if any(len(block.get_all(h,[]))>1 for h in ('Action','Status','Final-Recipient')):return None
            if part.get_content_type()=='message/rfc822':
                for original in part.get_payload():
                    if len(original.get_all('Message-ID',[]))!=1:return None
        def tags(header):
            result={}
            for item in str(header).replace('\r','').replace('\n','').split(';'):
                if '=' not in item:continue
                key,value=item.strip().split('=',1)
                if key in result:raise ValueError('duplicate signature tag')
                result[key]=value.strip()
            return result
        ams=tags(message['ARC-Message-Signature']);seal=tags(message['ARC-Seal'])
        domain=ams.get('d','').lower()
        if (domain not in approved_arc_domains or seal.get('d','').lower()!=domain
                or ams.get('i')!='1' or seal.get('i')!='1' or seal.get('cv')!='none'
                or 'l' in ams or not required.issubset({h.strip().lower() for h in ams.get('h','').split(':')})):
            return None
        result,sets,_=dkim.arc_verify(raw,**({'dnsfunc':dnsfunc} if dnsfunc else {}))
        if result!=b'pass' or len(sets)!=1 or not sets[0]['ams-valid'] or not sets[0]['as-valid']:return None
        return {**report,'authentication':'arc_seal','sealer':domain,'sender_authenticated':False}
    except Exception:return None
