"""Cryptographic DKIM check before automatic thread assignment.

Never trusts sender-supplied Authentication-Results. Missing/invalid signatures,
unsigned thread headers, body-length truncation and DNS failure remain ambiguous.
"""
from email import policy
from email.parser import BytesParser
from rtm_core.case_mail import parse_original


def authenticated_sender(raw,*,dnsfunc=None):
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
        if not required.intersection({'in-reply-to','references'}):return False
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
