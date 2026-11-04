"""Scoped review and DSN reception; real DKIM, isolated DNS/IMAP/custody."""
import unittest
from dataclasses import replace
from datetime import datetime,timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime
from uuid import uuid4
from sqlalchemy import text
from fastapi import HTTPException
from tests import test_paid_case_mail as fixture
from tests import test_management_mail_authentication as crypto_fixture
from tests import test_mail_recovery_adverse as adverse
from rtm_core.case_mail import MAILBOX,parse_original
from rtm_core.case_mail_repository import ingest,pending_assignment,assign_pending,conversation,queue,obj
from rtm_core.case_mail_runtime import sync_folder,send_one
from rtm_core.case_mail_authentication import authenticated_delivery_report


class ReviewTest(unittest.TestCase):
    setUp=fixture.MailTest.setUp
    tearDown=fixture.MailTest.tearDown
    prepare=fixture.MailTest.prepare
    reply=fixture.MailTest.reply

    def test_scoped_assignment_independent_evidence_and_idempotent_retry(self):
        with self.engine.begin() as c:
            original=self.prepare(c)
            mid=ingest(c,self.reply(original['message']['original_id']),folder='INBOX',uidvalidity=1,uid=1,direction='incoming',trusted_sender=False,storage=self.store)
            pending=pending_assignment(c,self.case)['messages'];self.assertEqual([p['id'] for p in pending],[mid])
            self.assertEqual(pending_assignment(c,self.other)['messages'],[])
            with self.assertRaises(HTTPException):assign_pending(c,self.other,mid,self.actor,'Comprobación independiente sintética',pending[0]['message']['raw_sha256'])
            with self.assertRaises(HTTPException):assign_pending(c,self.case,mid,self.actor,'solo email',pending[0]['message']['raw_sha256'])
            for _ in range(2):
                assigned=assign_pending(c,self.case,mid,self.actor,'Comprobación independiente sintética',pending[0]['message']['raw_sha256'])
                self.assertEqual(assigned['case_id'],self.case)
            self.assertEqual(c.execute(text("SELECT count(*) FROM rtm_case_mail_events WHERE state='assigned'")).scalar(),1)
            self.assertEqual(c.execute(text('SELECT count(*) FROM rtm_case_mail_attachment_checks')).scalar(),1)
            self.assertEqual(pending_assignment(c,self.case)['messages'],[])

    def test_multi_case_or_unknown_thread_never_disclosed_to_single_case_operator(self):
        with self.engine.begin() as c:
            first=self.prepare(c)
            c.execute(text('INSERT INTO cases VALUES (:id)'),{'id':self.other})
            c.execute(text('INSERT INTO rtm_case_mail_participants VALUES (:id,:c,:e,true)'),{'id':str(uuid4()),'c':self.other,'e':'client@example.com'})
            other=self.prepare(c,case_id=self.other,operation_id=str(uuid4()))
            for refs,uid in [(first['message']['original_id']+' '+other['message']['original_id'],1),('<unknown@example.com>',2)]:
                message=BytesParser(policy=policy.SMTP).parsebytes(self.reply(refs));raw=message.as_bytes()
                mid=ingest(c,raw,folder='INBOX',uidvalidity=1,uid=uid,direction='incoming',trusted_sender=True,storage=self.store)
                for case in (self.case,self.other):
                    self.assertEqual(pending_assignment(c,case)['messages'],[])
                    with self.assertRaises(HTTPException):assign_pending(c,case,mid,self.actor,'Comprobación independiente sintética',parse_original(raw)['raw_sha256'])
            self.assertEqual(c.execute(text("SELECT count(*) FROM rtm_case_messages WHERE state='pending_assignment'")).scalar(),2)


class DeliveryTest(unittest.TestCase):
    setUp=fixture.MailTest.setUp
    tearDown=fixture.MailTest.tearDown
    prepare=fixture.MailTest.prepare
    imap=adverse.AdverseMailTest.imap

    @classmethod
    def setUpClass(cls):
        crypto_fixture.DKIMTest.setUpClass();cls.signer=crypto_fixture.DKIMTest()

    def report(self,original,recipient='client@example.com',action='failed'):
        message=EmailMessage(policy=policy.SMTP)
        for key,value in {'From':'mailer-daemon@example.com','To':MAILBOX,'Date':format_datetime(datetime.now(timezone.utc)),'Subject':'Delivery report','Message-ID':f'<{uuid4()}@example.com>','MIME-Version':'1.0'}.items():message[key]=value
        message.set_type('multipart/report');message.set_param('report-type','delivery-status')
        human=EmailMessage(policy=policy.SMTP);human.set_content('Informe sintético del proveedor.')
        dsn=EmailMessage(policy=policy.SMTP);dsn.set_type('message/delivery-status')
        general=EmailMessage(policy=policy.SMTP);general['Reporting-MTA']='dns; mx.example.com'
        target=EmailMessage(policy=policy.SMTP);target['Final-Recipient']='rfc822; '+recipient;target['Action']=action;target['Status']='5.1.1' if action=='failed' else '2.0.0'
        dsn.set_payload([general,target]);embedded=EmailMessage(policy=policy.SMTP);embedded.set_type('message/rfc822');embedded.set_payload([BytesParser(policy=policy.SMTP).parsebytes(original)])
        message.attach(human);message.attach(dsn);message.attach(embedded)
        return self.signer.signed(message.as_bytes(),headers=[b'from',b'to',b'subject',b'date',b'message-id',b'content-type',b'mime-version'])

    def verify(self,raw,senders=('mailer-daemon@example.com',)):
        return authenticated_delivery_report(raw,senders,dnsfunc=lambda name,**kw:self.signer.dns if name.rstrip(b'.')==b'fixture._domainkey.example.com' else None)

    def test_authenticated_bounce_imported_once_and_linked_to_exact_outgoing(self):
        with self.engine.begin() as c:
            first=self.prepare(c);queue(c,self.case,self.operation,self.actor)
            c.execute(text("INSERT INTO rtm_case_mail_sync(mailbox,folder,uidvalidity,last_uid) VALUES (:m,'INBOX',1,0)"),{'m':MAILBOX})
        send_one(self.engine,self.config,self.store,transport=lambda *_:('provider_accepted','SMTP_DATA_ACCEPTED'))
        raw=self.report(self.store.values[first['message']['raw_sha256']]);self.assertTrue(self.verify(raw))
        imap,_=self.imap({1:raw,2:raw})
        for _ in range(2):sync_folder(self.engine,self.config,'INBOX',self.store,imap_factory=imap,sender_verifier=lambda _:False,delivery_verifier=self.verify)
        with self.engine.begin() as c:
            self.assertEqual(c.execute(text('SELECT state FROM rtm_case_messages WHERE id=:id'),{'id':self.operation}).scalar(),'bounced')
            self.assertEqual(len(conversation(c,self.case)['messages']),2)
            self.assertEqual(c.execute(text("SELECT count(*) FROM rtm_case_mail_events WHERE state='bounced'")).scalar(),1)
            self.assertEqual(c.execute(text("SELECT state FROM rtm_case_mail_jobs")).scalar(),'closed')
        self.assertFalse(send_one(self.engine,self.config,self.store,transport=lambda *_:self.fail('Must not resend')))

    def test_unapproved_forged_or_wrong_recipient_report_cannot_change_send_state(self):
        with self.engine.begin() as c:
            first=self.prepare(c);queue(c,self.case,self.operation,self.actor)
        send_one(self.engine,self.config,self.store,transport=lambda *_:('provider_accepted','SMTP_DATA_ACCEPTED'))
        original=self.store.values[first['message']['raw_sha256']]
        raw=self.report(original);self.assertFalse(self.verify(raw,()))
        self.assertFalse(self.verify(raw.replace(b'5.1.1',b'2.0.0')))
        unsigned=raw[raw.index(b'From:'):];self.assertFalse(self.verify(b'Authentication-Results: trusted; dkim=pass\r\n'+unsigned))
        for uid,(value,trusted) in enumerate([(unsigned,False),(self.report(original,'other@example.com'),True)],1):
            with self.engine.begin() as c:
                ingest(c,value,folder='INBOX',uidvalidity=1,uid=uid,direction='incoming',trusted_sender=False,trusted_delivery=trusted,storage=self.store)
                self.assertEqual(c.execute(text('SELECT state FROM rtm_case_messages WHERE id=:id'),{'id':self.operation}).scalar(),'provider_accepted')


if __name__=='__main__':unittest.main()
