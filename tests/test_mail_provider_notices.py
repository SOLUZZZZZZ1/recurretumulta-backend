"""ARC seals reveal claims in the right case; they do not authenticate senders."""
from dataclasses import replace
from email import policy
from email.parser import BytesParser
from functools import partial
from unittest.mock import patch
import unittest
from sqlalchemy import text
from rtm_core.case_mail import MAILBOX
from rtm_core.case_mail_authentication import sealed_delivery_notice,authenticated_delivery_report
from rtm_core.case_mail_repository import ingest,queue,conversation,obj
from rtm_core.case_mail_runtime import send_one,sync_folder
from tests import test_paid_case_mail as fixture
from tests import test_management_mail_authentication as crypto
from tests import test_mail_assignment_delivery as delivery_fixture
from tests import test_mail_recovery_adverse as adverse_fixture

class ProviderNoticeTest(unittest.TestCase):
    setUp=fixture.MailTest.setUp
    tearDown=fixture.MailTest.tearDown
    prepare=fixture.MailTest.prepare
    imap=adverse_fixture.AdverseMailTest.imap
    required=[b'from',b'to',b'subject',b'date',b'message-id',b'mime-version',b'content-type']

    @classmethod
    def setUpClass(cls):
        crypto.DKIMTest.setUpClass();cls.signer=crypto.DKIMTest()

    def dns(self,name,**kwargs):
        if name.rstrip(b'.') in {b'arc-test._domainkey.serviciodecorreo.es',b'fixture._domainkey.example.com'}:
            return self.signer.dns
        raise AssertionError('Unexpected DNS lookup: '+repr(name))

    def unsigned_report(self,original,**kwargs):
        message=BytesParser(policy=policy.SMTP).parsebytes(delivery_fixture.DeliveryTest.report(self,original,**kwargs))
        del message['DKIM-Signature']
        message['Authentication-Results']='mx.receiver.example; dkim=none; spf=none; dmarc=none'
        return message.as_bytes()

    def seal(self,raw,headers=None):
        import dkim
        return b''.join(dkim.arc_sign(raw,b'arc-test',b'serviciodecorreo.es',self.signer.key,
            b'mx.receiver.example',include_headers=headers or self.required))+raw

    def verify(self,raw,senders=('mailer-daemon@example.com',),domains=('serviciodecorreo.es',),mailbox=MAILBOX):
        return sealed_delivery_notice(raw,senders,domains,mailbox=mailbox,dnsfunc=self.dns)

    def outgoing(self,*,send=True,body=None):
        with self.engine.begin() as c:
            first=self.prepare(c,**({'body':body} if body else {}))
            if send:queue(c,self.case,self.operation,self.actor)
        if send:send_one(self.engine,self.config,self.store,transport=lambda *_:('provider_accepted','SMTP_DATA_ACCEPTED'))
        return self.store.values[first['message']['raw_sha256']]

    def import_notice(self,raw,uid=1,**kw):
        with self.engine.begin() as c:
            return ingest(c,raw,folder='INBOX',uidvalidity=1,uid=uid,direction='incoming',
                trusted_sender=False,storage=self.store,provider_notice=self.verify(raw),**kw)

    def test_valid_seal_is_a_notice_not_authenticated_delivery(self):
        raw=self.seal(self.unsigned_report(self.outgoing()))
        notice=self.verify(raw)
        self.assertEqual(notice['authentication'],'arc_seal')
        self.assertFalse(notice['sender_authenticated'])
        self.assertFalse(authenticated_delivery_report(raw,('mailer-daemon@example.com',),dnsfunc=self.dns))

    def test_modified_body_and_signed_header_are_rejected(self):
        raw=self.seal(self.unsigned_report(self.outgoing()))
        for modified in (raw.replace(b'Status: 5.1.1',b'Status: 5.1.2'),raw.replace(b'Subject: Delivery report',b'Subject: Modified report')):
            self.assertNotEqual(raw,modified);self.assertIsNone(self.verify(modified))

    def test_unapproved_sender_sealer_or_mailbox_are_rejected(self):
        raw=self.seal(self.unsigned_report(self.outgoing()))
        for kwargs in ({'senders':()},{'domains':()},{'domains':('other.example',)},{'mailbox':'other@example.com'}):
            self.assertIsNone(self.verify(raw,**kwargs))

    def test_forged_authentication_result_without_signature_is_rejected(self):
        raw=self.unsigned_report(self.outgoing())
        self.assertIsNone(self.verify(b'Authentication-Results: trusted; arc=pass\r\n'+raw))

    def test_signed_delivery_success_is_not_accepted_as_arc_notice(self):
        raw=self.seal(self.unsigned_report(self.outgoing(),action='delivered'))
        self.assertIsNone(self.verify(raw))

    def test_missing_header_coverage_and_duplicate_fields_are_rejected(self):
        raw=self.unsigned_report(self.outgoing())
        self.assertIsNone(self.verify(self.seal(raw,[h for h in self.required if h!=b'to'])))
        for changed in (b'Subject: Duplicate\r\n'+raw,raw.replace(b'Action: failed',b'Action: failed\r\nAction: delivered')):
            self.assertIsNone(self.verify(self.seal(changed)))

    def test_duplicate_arc_set_is_rejected(self):
        raw=self.seal(self.unsigned_report(self.outgoing()))
        self.assertIsNone(self.verify(b'ARC-Seal: i=1; d=serviciodecorreo.es; cv=none\r\n'+raw))

    def test_inconsistent_failure_status_is_rejected(self):
        raw=self.unsigned_report(self.outgoing()).replace(b'Status: 5.1.1',b'Status: 2.0.0')
        self.assertIsNone(self.verify(self.seal(raw)))

    def test_same_message_id_with_different_sealed_content_is_rejected(self):
        from fastapi import HTTPException
        raw=self.unsigned_report(self.outgoing())
        self.import_notice(self.seal(raw))
        with self.assertRaises(HTTPException) as error:
            self.import_notice(self.seal(raw.replace(b'Status: 5.1.1',b'Status: 5.1.2')),uid=2)
        self.assertEqual(error.exception.status_code,409)

    def test_notice_is_linked_once_without_changing_outgoing_or_job(self):
        raw=self.seal(self.unsigned_report(self.outgoing()))
        first=self.import_notice(raw)
        self.assertEqual(self.import_notice(raw),'duplicate')
        self.assertEqual(self.import_notice(raw,uid=2),first)
        with self.engine.begin() as c:
            self.assertEqual(c.execute(text('SELECT state FROM rtm_case_messages WHERE id=:id'),{'id':self.operation}).scalar(),'provider_accepted')
            self.assertEqual(c.execute(text("SELECT count(*) FROM rtm_case_mail_events WHERE state='bounced'")).scalar(),0)
            self.assertEqual(c.execute(text('SELECT state FROM rtm_case_mail_jobs')).scalar(),'closed')
            messages=conversation(c,self.case)['messages'];self.assertEqual(len(messages),2)
            incoming=next(m for m in messages if m['id']==first)
            self.assertEqual(incoming['relation']['delivery_notice']['state'],'pending_verification')
            self.assertEqual(incoming['relation']['delivery_notice']['outgoing_message_id'],self.operation)

    def test_never_links_an_unsent_original(self):
        raw=self.seal(self.unsigned_report(self.outgoing(send=False)))
        self.import_notice(raw)
        with self.engine.begin() as c:self.assertEqual(len(conversation(c,self.case)['messages']),1)

    def test_returned_body_must_match_stored_original(self):
        original=self.outgoing()
        changed=original.replace(b'No es una',b'Ya es una')
        self.assertNotEqual(changed,original)
        self.import_notice(self.seal(self.unsigned_report(changed)))
        with self.engine.begin() as c:self.assertEqual(len(conversation(c,self.case)['messages']),1)

    def test_report_recipient_must_match_outgoing(self):
        raw=self.seal(self.unsigned_report(self.outgoing(),recipient='other@example.com'))
        self.import_notice(raw)
        with self.engine.begin() as c:self.assertEqual(len(conversation(c,self.case)['messages']),1)

    def test_cross_case_scope_cannot_copy_notice_or_original(self):
        raw=self.seal(self.unsigned_report(self.outgoing()));before=len(self.store.values)
        self.assertEqual(self.import_notice(raw,allowed_case_id=self.other),'outside_pilot_scope')
        self.assertEqual(len(self.store.values),before)

    def test_returned_original_and_secrets_are_hidden_in_public_projection_and_search(self):
        secret='PRIVATE_RETURNED_CONTENT_12345'
        raw=self.seal(self.unsigned_report(self.outgoing(body=secret)))
        mid=self.import_notice(raw)
        with self.engine.begin() as c:
            message=next(m for m in conversation(c,self.case)['messages'] if m['id']==mid)
            self.assertNotIn(secret,str(message));self.assertEqual(message['message']['attachments'],[])
            found=conversation(c,self.case,query=secret)['messages']
            self.assertEqual([m['id'] for m in found],[self.operation])
            self.assertIn(secret,self.store.values[message['message']['raw_sha256']].decode())

    def test_notice_attachment_endpoint_cannot_reveal_returned_original(self):
        from types import SimpleNamespace
        from fastapi import HTTPException
        from rtm_core.case_mail_router import checked_attachment
        mid=self.import_notice(self.seal(self.unsigned_report(self.outgoing())))
        with self.engine.begin() as c:
            with self.assertRaises(HTTPException) as error:
                checked_attachment(mid,1,SimpleNamespace(conn=c,case_id=self.case))
            self.assertEqual(error.exception.status_code,404)

    def test_runtime_imports_sealed_notice_in_scoped_mailbox(self):
        raw=self.seal(self.unsigned_report(self.outgoing()))
        with self.engine.begin() as c:
            c.execute(text("INSERT INTO rtm_case_mail_sync(mailbox,folder,uidvalidity,last_uid) VALUES (:m,'INBOX',1,0)"),{'m':MAILBOX})
        config=replace(self.config,delivery_senders=('mailer-daemon@example.com',),delivery_arc_domains=('serviciodecorreo.es',),case_id=self.case)
        imap,_=self.imap({1:raw})
        verifier=partial(sealed_delivery_notice,dnsfunc=self.dns)
        with patch('rtm_core.case_mail_authentication.sealed_delivery_notice',side_effect=verifier):
            sync_folder(self.engine,config,'INBOX',self.store,imap_factory=imap,sender_verifier=lambda _:False)
        with self.engine.begin() as c:
            self.assertEqual(len(conversation(c,self.case)['messages']),2)
            self.assertEqual(c.execute(text('SELECT state FROM rtm_case_messages WHERE id=:id'),{'id':self.operation}).scalar(),'provider_accepted')

if __name__=='__main__':unittest.main()
