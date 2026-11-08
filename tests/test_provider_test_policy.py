from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4
import os
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rtm_core.casework_policy import load_policy, validate_test_boundary, origin_allowed, TEST_ORIGIN, CaseworkConfigurationError
from rtm_core.operator_auth_request import load_operator_auth_runtime_config, operator_auth_environment_mode
from rtm_core.operator_auth_router import router
from rtm_core.management_policy import configuration


def environment():
    return {'RTM_ENABLE_PROVIDER_TEST_CASEWORK':'1','RTM_ENV':'staging','RTM_ENVIRONMENT_CONFIRMATION':'RTM_STAGING_ISOLATED',
        'RTM_SIDE_EFFECT_POLICY':'isolated','RTM_DOCUMENT_INPUT_POLICY':'synthetic_only',
        'RENDER_SERVICE_NAME':'rtm-journey-test-api','RENDER_SERVICE_ID':'srv-synthetic-journey-test',
        'RTM_PROVIDER_TEST_DATABASE_HOST':'dpg-synthetic-journey-test','DATABASE_URL':'postgresql://unused:unused@dpg-synthetic-journey-test/rtm_journey_test',
        'FRONTEND_URL':TEST_ORIGIN,'RTM_CASEWORK_PUBLIC_ORIGIN':TEST_ORIGIN,
        'RTM_STRIPE_MODE':'test','RTM_MANAGEMENT_PAYMENT_MODE':'test','STRIPE_SECRET_KEY':'sk_test_fixture',
        'RTM_MANAGEMENT_STRIPE_TEST_KEY':'sk_test_fixture','B2_BUCKET':'rtm-test-fixture','RTM_PROVIDER_TEST_B2_BUCKET':'rtm-test-fixture',
        'RTM_CASEWORK_ALLOWED_CASE_IDS':str(uuid4()),'RTM_CASEWORK_ALLOWED_OPERATOR_IDS':str(uuid4()),
        'RTM_CASEWORK_EXPIRES_AT':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
        'RTM_ENABLE_OPERATOR_AUTH_V1':'1','RTM_OPERATOR_ACCESS_HMAC_KEY':'synthetic-unusable-'+'a'*40,
        'RTM_ENABLE_MANAGEMENT_JOURNEY':'1','RTM_MANAGEMENT_STRIPE_ACCOUNT_ID':'acct_fixture',
        'RTM_MANAGEMENT_STRIPE_TEST_WEBHOOK_SECRET':'whsec_fixture','RTM_MANAGEMENT_DRAFT_READY':'1','RTM_MANAGEMENT_MAIL_VERIFIED':'1',
        'RTM_MANAGEMENT_TAX_BASIS_POINTS':'2100','RTM_MANAGEMENT_TAX_LABEL':'Impuesto sintético'}


class ProviderTestPolicyTest(unittest.TestCase):
    def test_disabled_profile_does_not_replace_any_existing_profile(self):
        self.assertIsNone(load_policy({}))
        self.assertIsNone(load_policy({'RTM_ENV':'staging'}))

    def test_test_scope_and_auth_do_not_masquerade_as_production(self):
        env=environment();policy=load_policy(env);auth=load_operator_auth_runtime_config(env)
        self.assertEqual(policy.case_ids,(env['RTM_CASEWORK_ALLOWED_CASE_IDS'],))
        self.assertEqual(policy.operator_ids,auth.allowed_operator_ids)
        self.assertTrue(auth.provider_test_casework);self.assertFalse(auth.production_casework)
        self.assertTrue(auth.available);self.assertEqual(operator_auth_environment_mode(env),'individual')
        self.assertEqual(configuration(env).mode,'test')

    def test_forbidden_cross_environment_configurations_fail_closed(self):
        cases=[{'RTM_ENABLE_PROVIDER_TEST_CASEWORK':'true'}, {'RTM_ENABLE_PRODUCTION_CASEWORK':'1'},
            {'RTM_ENV':'production'}, {'RENDER_SERVICE_NAME':'recurretumulta-backend'},
            {'RENDER_SERVICE_ID':'srv-d5fu32hr0fns73c3oa0g'}, {'RENDER_SERVICE_ID':'srv-d9v1t2qjnfac73a14kug'},
            {'RTM_PROVIDER_TEST_DATABASE_HOST':'dpg-d9v2lab7uimc73da0j9g-a'},
            {'DATABASE_URL':'postgresql://unused@dpg-synthetic-journey-test/recurretumulta'},
            {'DATABASE_URL':'postgresql://unused@dpg-synthetic-journey-test.evil.example/rtm_journey_test'},
            {'FRONTEND_URL':'https://www.resuelvetusmovidas.es'}, {'RTM_CASEWORK_PUBLIC_ORIGIN':TEST_ORIGIN+'/evil'},
            {'RTM_STRIPE_MODE':'live'}, {'RTM_MANAGEMENT_PAYMENT_MODE':'live'},
            {'STRIPE_SECRET_KEY':'sk_live_unusable'}, {'RTM_MANAGEMENT_STRIPE_TEST_KEY':'sk_live_unusable'},
            {'RTM_ENABLE_EXTERNAL_SUBMISSION':'1'}, {'RTM_ENABLE_OUTBOUND_EMAIL':'1'},
            {'RTM_DOCUMENT_INPUT_POLICY':'real'}, {'B2_BUCKET':'production'},
            {'RTM_PRODUCTION_B2_BUCKET':'rtm-test-fixture'},
            {'RTM_CASEWORK_ALLOWED_CASE_IDS':'2af6f88a-f72a-46f9-9fd7-f3c6d44c6aee'},
            {'RTM_CASEWORK_EXPIRES_AT':'2020-01-01T00:00:00Z'},
            {'RTM_CASEWORK_EXPIRES_AT':(datetime.now(timezone.utc)+timedelta(days=8)).isoformat()}]
        for override in cases:
            with self.subTest(override=tuple(override)),self.assertRaises(CaseworkConfigurationError):load_policy({**environment(),**override})

    def test_worker_has_a_separate_identity(self):
        env=environment()
        with self.assertRaises(CaseworkConfigurationError):load_policy(env,service_name='rtm-case-mail-pilot')
        env['RENDER_SERVICE_NAME']='rtm-journey-test-mail'
        self.assertEqual(load_policy(env,service_name='rtm-case-mail-pilot').origin,TEST_ORIGIN)
        self.assertEqual(configuration(env,for_worker=True).mode,'test')
        with self.assertRaises(CaseworkConfigurationError):load_policy(env)

    def test_reconciliation_retains_boundaries_after_access_expires(self):
        env=environment();env['RTM_CASEWORK_EXPIRES_AT']='2020-01-01T00:00:00Z'
        env['RTM_ENABLE_MANAGEMENT_JOURNEY']='0';env['RTM_ENABLE_MANAGEMENT_RECONCILIATION']='1'
        self.assertEqual(configuration(env,for_notification=True).mode,'test')
        self.assertTrue(origin_allowed(env,TEST_ORIGIN))
        self.assertFalse(origin_allowed(env,'https://www.resuelvetusmovidas.es'))
        env['STRIPE_SECRET_KEY']='sk_live_unusable'
        self.assertFalse(origin_allowed(env,TEST_ORIGIN))

    def test_status_and_origin_checks_are_real_routes_without_database_writes(self):
        app=FastAPI();app.include_router(router)
        with patch.dict(os.environ,environment(),clear=True),patch('rtm_core.operator_auth_router.get_engine') as engine,TestClient(app) as client:
            response=client.get('/ops/auth/status');self.assertEqual(response.status_code,200)
            body=response.json();self.assertEqual(body['auth_environment'],'staging')
            self.assertEqual(body['auth_profile'],'provider_test_casework');self.assertEqual(body['payment_mode'],'test')
            self.assertEqual(body['device_logout_contract'],'rtm_device_logout_v1')
            denied=client.post('/ops/auth/login',headers={'Origin':'https://www.resuelvetusmovidas.es'},json={'email':'fixture@example.com','password':'synthetic'})
            self.assertEqual(denied.status_code,403);engine.assert_not_called()

    def test_new_entrypoint_stays_closed_until_enabled_and_rechecks_credentials(self):
        fake=FastAPI();writes=[]
        @fake.get('/health')
        def health():return {'ok':True}
        @fake.post('/billing/checkout')
        def checkout():writes.append('called');return {'ok':True}
        with patch.dict(os.environ,environment(),clear=True),patch.dict(sys.modules,{'app':SimpleNamespace(app=fake)}):
            runpy.run_path(str(Path(__file__).parents[1]/'provider_test_app.py'))
            with TestClient(fake) as client:
                self.assertEqual(client.get('/health').status_code,200)
                self.assertEqual(client.post('/billing/checkout').status_code,503)
                self.assertEqual(writes,[])
                os.environ['RTM_PROVIDER_TEST_HTTP_ENABLED']='1'
                self.assertEqual(client.post('/billing/checkout').status_code,200)
                self.assertEqual(writes,['called'])
                os.environ['STRIPE_SECRET_KEY']='sk_live_unusable'
                self.assertEqual(client.post('/billing/checkout').status_code,503)
                self.assertEqual(writes,['called'])

    def test_new_worker_is_idle_without_mail_authorization(self):
        from rtm_core.provider_test_worker import main
        env=environment();env['RENDER_SERVICE_NAME']='rtm-journey-test-mail'
        with patch.dict(os.environ,env,clear=True),patch('rtm_core.provider_test_worker.threading.Event') as event,patch('rtm_core.provider_test_worker.signal.signal'),patch('rtm_core.case_mail_worker.main') as mail,patch('builtins.print'):
            main();event.return_value.wait.assert_called_once_with();mail.assert_not_called()

    def test_generation_worker_can_run_with_mail_disabled(self):
        from rtm_core.provider_test_worker import main
        env=environment();env.update(RENDER_SERVICE_NAME='rtm-journey-test-mail',RTM_CASE_MAIL_WORKER_ENABLED='0',RTM_MANAGEMENT_GENERATION_WORKER_ENABLED='1')
        with patch.dict(os.environ,env,clear=True),patch('rtm_core.management_worker.main') as worker:
            main();worker.assert_called_once_with()
