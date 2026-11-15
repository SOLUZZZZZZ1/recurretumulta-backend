import json
import unittest
from scripts.rtm_case_mail_preflight import check
from tests import test_mail_worker_profile as profiles


class MailPreflightTest(unittest.TestCase):
    def environment(self):
        env=profiles.WorkerProfileTest().environment()
        env.update(RENDER_SERVICE_NAME='recurretumulta-backend',RENDER_SERVICE_ID='srv-d5fu32hr0fns73c3oa0g',
            DATABASE_URL='postgresql://synthetic:secret@dpg-d5ftkbqli9vc7390hni0-a/recurretumulta',
            B2_BUCKET='recurretumulta-prod',FRONTEND_URL='https://www.resuelvetusmovidas.es',
            RTM_CASEWORK_ALLOWED_OPERATOR_IDS='dca14cf0-bc54-409e-93cf-53098511b503',
            RTM_ENABLE_CASE_MAIL='0',RTM_CASE_MAIL_WORKER_ENABLED='0')
        return env

    def test_metadata_does_not_connect_or_enable_flags(self):
        env=self.environment();before=dict(env)
        result=check(env,smtp_ssl_factory=lambda *a,**k:self.fail('network'))
        self.assertTrue(result['configuration_valid']);self.assertFalse(result['transport_ok'])
        self.assertEqual(env,before);self.assertNotIn(env['RTM_CASE_MAIL_PASSWORD'],json.dumps(result))

    def test_login_uses_only_read_only_commands(self):
        calls=[]
        class SMTP:
            def __init__(self,*a,**kw):calls.append('tls')
            def ehlo(self):calls.append('ehlo');return 250,b'ok'
            def starttls(self,**kw):calls.append('starttls');return 220,b'ok'
            def login(self,*args):calls.append('auth');return 235,b'ok'
            def noop(self):calls.append('noop');return 250,b'ok'
            def quit(self):calls.append('quit')
        class IMAP:
            def __init__(self,*a,**kw):pass
            def login(self,*a):return 'OK',[]
            def select(self,folder,readonly=False):
                if not readonly:raise AssertionError('must EXAMINE')
                calls.append('examine');return 'OK',[b'3']
            def response(self,key):return key,[b'17' if key=='UIDVALIDITY' else b'4']
            def logout(self):calls.append('logout')
        for tls in ('implicit','starttls'):
            calls.clear();env=self.environment();env['RTM_CASE_MAIL_SMTP_TLS']=tls
            result=check(env,connect=True,smtp_ssl_factory=SMTP,smtp_factory=SMTP,imap_factory=IMAP)
            self.assertTrue(result['transport_ok']);self.assertFalse(result['sends_mail']);self.assertFalse(result['writes_database'])
            self.assertEqual(result['baseline_candidates'][0],{'folder':'INBOX','uidvalidity':17,'last_uid':3})
            if tls=='starttls':self.assertLess(calls.index('starttls'),calls.index('auth'))
            self.assertEqual(calls.count('examine'),2)
            self.assertNotIn(env['RTM_CASE_MAIL_PASSWORD'],json.dumps(result))

    def test_wrong_runtime_or_expiry_prevents_network(self):
        for patch in ({'RENDER_SERVICE_ID':'srv-other'},{'RTM_CASEWORK_EXPIRES_AT':'2020-01-01T00:00:00+00:00'}):
            result=check({**self.environment(),**patch},connect=True,smtp_ssl_factory=lambda *a,**k:self.fail('network'))
            self.assertFalse(result['transport_ok']);self.assertEqual(result['error_code'],'MAIL_RUNTIME_REVIEW_REQUIRED')

    def test_provider_errors_do_not_expose_credentials(self):
        env=self.environment()
        def failing(*a,**kw):raise RuntimeError(env['RTM_CASE_MAIL_PASSWORD'])
        result=check(env,connect=True,smtp_ssl_factory=failing,imap_factory=failing)
        self.assertFalse(result['transport_ok']);self.assertNotIn(env['RTM_CASE_MAIL_PASSWORD'],json.dumps(result))


if __name__=='__main__':unittest.main()
