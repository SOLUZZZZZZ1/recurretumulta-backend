import json
import unittest
from unittest.mock import Mock
from scripts.rtm_journey_runtime_readonly import inspect,SERVICE,DB_HOST,MAILBOX


class RuntimeReadonlyTest(unittest.TestCase):
    def environment(self):
        return {'RENDER_SERVICE_ID':SERVICE,'RENDER_SERVICE_NAME':'recurretumulta-backend-1',
            'RTM_ENV':'staging','DATABASE_URL':'postgresql://'+DB_HOST+'/rtm_staging',
            'STRIPE_SECRET_KEY':'sk_test_synthetic_unusable','SMTP_PASSWORD':'synthetic_private_password',
            'SMTP_USER':MAILBOX,'SMTP_HOST':'smtp.serviciodecorreo.es','SMTP_PORT':'465'}

    def test_wrong_service_never_contacts_provider(self):
        reader=Mock()
        result=inspect({**self.environment(),'RENDER_SERVICE_ID':'srv-wrong'},reader)
        self.assertFalse(result['ok']);reader.assert_not_called()

    def test_live_key_never_contacts_provider(self):
        reader=Mock()
        result=inspect({**self.environment(),'STRIPE_SECRET_KEY':'sk_live_synthetic_unusable'},reader)
        self.assertEqual(result['error_code'],'EXISTING_TEST_STRIPE_KEY_REQUIRED');reader.assert_not_called()

    def test_reports_presence_and_account_without_credentials(self):
        env=self.environment();reader=Mock(return_value='acct_fixture')
        result=inspect(env,reader)
        self.assertTrue(result['ok']);self.assertTrue(result['same_saved_mailbox']['SMTP_USER'])
        self.assertEqual(result['stripe_test_account_id'],'acct_fixture')
        serialized=json.dumps(result)
        for key in ('STRIPE_SECRET_KEY','SMTP_PASSWORD','DATABASE_URL'):
            self.assertNotIn(env[key],serialized)
        reader.assert_called_once_with(env['STRIPE_SECRET_KEY'])

    def test_provider_error_details_are_not_exported(self):
        result=inspect(self.environment(),Mock(side_effect=RuntimeError('Authorization: synthetic_private_password')))
        self.assertNotIn('synthetic_private_password',json.dumps(result))
        self.assertFalse(result['ok'])
