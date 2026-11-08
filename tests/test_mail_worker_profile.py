from datetime import datetime,timedelta,timezone
from uuid import uuid4
import unittest
from rtm_core.production_casework import load_policy,CaseworkConfigurationError
from rtm_core.case_mail_runtime import configuration

class WorkerProfileTest(unittest.TestCase):
    def environment(self):
        return {'RTM_ENABLE_PRODUCTION_CASEWORK':'1','RTM_ENV':'production','RENDER_SERVICE_NAME':'rtm-case-mail-pilot',
            'RTM_CASEWORK_PUBLIC_ORIGIN':'https://www.resuelvetusmovidas.es','RTM_CASEWORK_ALLOWED_CASE_IDS':str(uuid4()),
            'RTM_CASEWORK_ALLOWED_OPERATOR_IDS':str(uuid4()),'RTM_CASEWORK_EXPIRES_AT':(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat(),
            'RTM_ENABLE_CASE_MAIL':'1','RTM_CASE_MAIL_WORKER_ENABLED':'1','RTM_CASE_MAIL_DEDICATED_CONFIRMED':'1',
            'RTM_CASE_MAIL_ADDRESS':'expedientes@operativo.example.com','RTM_CASE_MAIL_ALLOWED_HOSTS':'smtp.example.com,imap.example.com',
            'RTM_CASE_MAIL_SMTP_HOST':'smtp.example.com','RTM_CASE_MAIL_IMAP_HOST':'imap.example.com','RTM_CASE_MAIL_PASSWORD':'synthetic-not-a-credential',
            'RTM_CASE_MAIL_ALLOWED_RECIPIENTS':'client@example.com','RTM_CASE_MAIL_FOLDERS':'INBOX,Sent'}
    def test_dedicated_worker_identity_does_not_enable_web_profile(self):
        env=self.environment();cfg=configuration(env);self.assertEqual(cfg.mailbox,'expedientes@operativo.example.com')
        with self.assertRaises(CaseworkConfigurationError):load_policy(env)
        env['RENDER_SERVICE_NAME']='recurretumulta-backend'
        with self.assertRaises(CaseworkConfigurationError):configuration(env)
    def test_worker_keeps_expiry_and_scope_guards(self):
        env=self.environment();env['RTM_CASEWORK_EXPIRES_AT']='2020-01-01T00:00:00+00:00'
        with self.assertRaises(CaseworkConfigurationError):configuration(env)
        env=self.environment();env['RTM_CASEWORK_ALLOWED_CASE_IDS']+=','+str(uuid4())
        with self.assertRaises(CaseworkConfigurationError):configuration(env)
