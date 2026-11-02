"""Nora's adverse boundaries, synthetic messages and isolated repository only."""
import unittest
from unittest.mock import patch
from sqlalchemy import text
from rtm_core.case_mail import MAILBOX,MAX_MESSAGE
from rtm_core.case_mail_repository import ingest,queue,conversation
from rtm_core.case_mail_runtime import tick,send_one,sync_folder
from rtm_core.case_mail_attachments import retry_pending
from tests import test_paid_case_mail as mail_fixture

class AdverseMailTest(unittest.TestCase):
    setUp=mail_fixture.MailTest.setUp
    tearDown=mail_fixture.MailTest.tearDown
    prepare=mail_fixture.MailTest.prepare
    reply=mail_fixture.MailTest.reply

    def imap(self,values):
        fetched=[]
        class IMAP:
            def __init__(self,*a,**k):pass
            def login(self,*a):return 'OK',[]
            def select(self,*a,**k):return 'OK',[]
            def response(self,*a):return 'UIDVALIDITY',[b'1']
            def uid(self,action,*args):
                if action=='search':return 'OK',[b' '.join(str(k).encode() for k in values)]
                uid=int(args[0]);raw=values[uid]
                if args[1]=='(RFC822.SIZE)':return 'OK',[b'1 (RFC822.SIZE '+str(raw if isinstance(raw,int) else len(raw)).encode()+b')']
                fetched.append(uid)
                if isinstance(raw,int):raise AssertionError('Oversized body must not be fetched')
                return 'OK',[(b'1 BODY[]',raw)]
            def logout(self):pass
        return IMAP,fetched

    def test_unprocessable_uid_does_not_starve_valid_incoming_or_approved_outgoing(self):
        with self.engine.begin() as c:
            first=self.prepare(c);queue(c,self.case,self.operation,self.actor)
            c.execute(text("INSERT INTO rtm_case_mail_sync(mailbox,folder,uidvalidity,last_uid) VALUES (:m,'INBOX',1,0)"),{'m':MAILBOX})
        valid=self.reply(first['message']['original_id']);imap,fetched=self.imap({1:MAX_MESSAGE+1,2:b'not an RFC822 message',3:valid})
        sent=[]
        original_sync=sync_folder
        def sync(*args,**kwargs):return original_sync(*args,**kwargs,imap_factory=imap)
        def send(*args,**kwargs):return send_one(*args,**kwargs,transport=lambda *_:(sent.append(1) or 'provider_accepted','SMTP_DATA_ACCEPTED'))
        with patch('rtm_core.case_mail_runtime.configuration',return_value=self.config),patch('rtm_core.case_mail_runtime.sync_folder',side_effect=sync),patch('rtm_core.case_mail_runtime.send_one',side_effect=send):
            tick(self.engine,storage=self.store,sender_verifier=lambda raw:True)
            tick(self.engine,storage=self.store,sender_verifier=lambda raw:True)
        self.assertEqual(sent,[1]);self.assertNotIn(1,fetched)
        with self.engine.begin() as c:
            self.assertEqual(c.execute(text("SELECT last_uid FROM rtm_case_mail_sync WHERE folder='INBOX'")).scalar(),3)
            self.assertEqual(c.execute(text('SELECT count(*) FROM rtm_case_mail_ingest_issues')).scalar(),2)
            self.assertEqual(len(conversation(c,self.case)['messages']),2)

    def test_import_commits_attachment_work_before_scanner_can_start(self):
        with self.engine.begin() as c:
            first=self.prepare(c)
            mid=ingest(c,self.reply(first['message']['original_id']),folder='INBOX',uidvalidity=1,uid=1,direction='incoming',trusted_sender=True,storage=self.store)
            c.execute(text("INSERT INTO rtm_case_mail_sync(mailbox,folder,uidvalidity,last_uid) VALUES (:m,'INBOX',1,1)"),{'m':MAILBOX})
        # The importing transaction has committed. No scanner has run.
        with self.engine.begin() as c:
            self.assertEqual(c.execute(text('SELECT count(*) FROM rtm_case_mail_attachment_checks WHERE message_id=:id'),{'id':mid}).scalar(),1)
        self.assertEqual(retry_pending(self.engine,MAILBOX,self.store)['completed'],1)
        with self.engine.begin() as c:
            self.assertEqual(c.execute(text('SELECT count(*) FROM rtm_case_messages')).scalar(),2)
            self.assertEqual(c.execute(text('SELECT state FROM rtm_case_mail_attachment_checks')).scalar(),'clean')

    def test_transient_imap_failure_preserves_cursor_but_does_not_block_outgoing(self):
        with self.engine.begin() as c:
            self.prepare(c);queue(c,self.case,self.operation,self.actor)
        sent=[]
        with patch('rtm_core.case_mail_runtime.configuration',return_value=self.config),patch('rtm_core.case_mail_runtime.sync_folder',side_effect=ConnectionError()),patch('rtm_core.case_mail_runtime.send_one',side_effect=lambda *a,**k:send_one(*a,**k,transport=lambda *_:(sent.append(1) or 'provider_accepted','SMTP_DATA_ACCEPTED'))):
            result=tick(self.engine,storage=self.store,sender_verifier=lambda _:False)
        self.assertEqual(sent,[1]);self.assertFalse(result['ok'])
