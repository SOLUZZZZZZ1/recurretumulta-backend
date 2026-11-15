from copy import deepcopy
import unittest
from sqlalchemy import create_engine,text
from scripts.rtm_case_mail_initialize import BaselineRejected,initialize,parse_baseline


class MailInitializeTest(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://')
        with self.engine.begin() as conn:
            conn.execute(text('CREATE TABLE rtm_case_mail_sync(mailbox TEXT,folder TEXT,uidvalidity INTEGER,last_uid INTEGER,PRIMARY KEY(mailbox,folder))'))
        self.reviewed=parse_baseline('INBOX:17:5,Sent:18:2')
        self.report={'configuration_valid':True,'transport_ok':True,'mailbox':'mail@example.com','folders':['INBOX','Sent'],
            'runtime':{'flags_enabled':{'RTM_ENABLE_CASE_MAIL':False,'RTM_CASE_MAIL_WORKER_ENABLED':False}},
            'baseline_candidates':[{'folder':'INBOX','uidvalidity':17,'last_uid':7},{'folder':'Sent','uidvalidity':18,'last_uid':2}]}

    def tearDown(self):self.engine.dispose()

    def rows(self):
        with self.engine.connect() as conn:return [tuple(r) for r in conn.execute(text('SELECT folder,uidvalidity,last_uid FROM rtm_case_mail_sync ORDER BY folder'))]

    def test_initialization_uses_reviewed_boundary_not_newer_remote_uid(self):
        self.assertEqual(initialize(self.engine,self.report,self.reviewed)['created'],2)
        self.assertEqual(self.rows(),[('INBOX',17,5),('Sent',18,2)])

    def test_repeat_preserves_cursor_after_sync_advanced(self):
        initialize(self.engine,self.report,self.reviewed)
        with self.engine.begin() as conn:conn.execute(text("UPDATE rtm_case_mail_sync SET last_uid=7 WHERE folder='INBOX'"))
        self.assertTrue(initialize(self.engine,self.report,self.reviewed)['already_initialized'])
        self.assertEqual(self.rows()[0],('INBOX',17,7))

    def test_partial_or_incompatible_existing_state_is_untouched(self):
        with self.engine.begin() as conn:conn.execute(text("INSERT INTO rtm_case_mail_sync VALUES ('mail@example.com','INBOX',19,1)"))
        before=self.rows()
        with self.assertRaises(BaselineRejected):initialize(self.engine,self.report,self.reviewed)
        self.assertEqual(self.rows(),before)

    def test_rejects_changed_remote_identity_unapproved_folders_and_enabled_worker(self):
        for mutation in ('validity','lower_uid','folder','worker','connection'):
            report=deepcopy(self.report)
            if mutation=='validity':report['baseline_candidates'][0]['uidvalidity']=20
            if mutation=='lower_uid':report['baseline_candidates'][0]['last_uid']=4
            if mutation=='folder':report['folders'].append('Archive')
            if mutation=='worker':report['runtime']['flags_enabled']['RTM_CASE_MAIL_WORKER_ENABLED']=True
            if mutation=='connection':report['transport_ok']=False
            with self.assertRaises(BaselineRejected):initialize(self.engine,report,self.reviewed)
            self.assertEqual(self.rows(),[])

    def test_transaction_rolls_back_all_folders_on_failure(self):
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TRIGGER fail_second BEFORE INSERT ON rtm_case_mail_sync WHEN NEW.folder='Sent' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"))
        with self.assertRaises(Exception):initialize(self.engine,self.report,self.reviewed)
        self.assertEqual(self.rows(),[])

    def test_rejects_duplicate_folders_and_invalid_numbers(self):
        for value in ('INBOX:1:2,INBOX:1:3','INBOX:0:0','INBOX:1:-1','INBOX:1:4294967296','INBOX:one:2'):
            with self.assertRaises(BaselineRejected):parse_baseline(value)
