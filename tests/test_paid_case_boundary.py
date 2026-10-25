import os
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from rtm_core.production_casework import load_policy, permits_case, CaseworkConfigurationError
from rtm_core.operator_auth_request import operator_auth_environment_mode, load_operator_auth_runtime_config
from rtm_core.operator_auth_router import router

CASE = '00000000-0000-4000-8000-000000000001'
OPERATOR = '00000000-0000-4000-8000-000000000002'
OTHER = '00000000-0000-4000-8000-000000000003'


def environment():
    return {'RTM_ENV':'production', 'RTM_ENABLE_PRODUCTION_CASEWORK':'1',
            'RENDER_SERVICE_NAME':'recurretumulta-backend',
            'RTM_CASEWORK_PUBLIC_ORIGIN':'https://www.recurretumulta.eu',
            'RTM_CASEWORK_ALLOWED_CASE_IDS':CASE, 'RTM_CASEWORK_ALLOWED_OPERATOR_IDS':OPERATOR,
            'RTM_CASEWORK_EXPIRES_AT':'2099-01-01T00:00:00Z',
            'RTM_ENABLE_OPERATOR_AUTH_V1':'1', 'RTM_OPERATOR_ACCESS_HMAC_KEY':'synthetic-test-key-only-00000000000000'}


class PaidCaseBoundaryTests(unittest.TestCase):
    def test_default_does_not_enable_production(self):
        self.assertIsNone(load_policy({}))
        self.assertEqual(operator_auth_environment_mode({'RTM_ENV':'production'}), 'fail_closed')

    def test_explicit_production_profile_keeps_its_identity(self):
        env = environment()
        policy = load_policy(env)
        self.assertEqual(policy.case_ids, (CASE,))
        cfg = load_operator_auth_runtime_config(env)
        self.assertTrue(cfg.available)
        self.assertEqual(cfg.environment, 'production')
        self.assertTrue(cfg.production_casework)
        self.assertFalse(cfg.local_development)
        self.assertEqual(cfg.allowed_operator_ids, (OPERATOR,))

    def test_invalid_or_broadened_configuration_fails_closed(self):
        for key, value in [
            ('RTM_ENABLE_PRODUCTION_CASEWORK','true'), ('RTM_ENV','staging'),
            ('RTM_ENABLE_LOCAL_OPERATOR_AUTH','1'), ('RENDER_SERVICE_NAME','recurretumulta-backend-1'),
            ('RTM_CASEWORK_PUBLIC_ORIGIN','https://example.com'),
            ('RTM_CASEWORK_ALLOWED_CASE_IDS', CASE+','+OTHER),
            ('RTM_CASEWORK_ALLOWED_OPERATOR_IDS','*'),
            ('RTM_CASEWORK_EXPIRES_AT','2020-01-01T00:00:00Z'),
            ('RTM_CASEWORK_EXPIRES_AT','2099-01-01'),
        ]:
            with self.subTest(key=key, value=value):
                env = {**environment(), key:value}
                with self.assertRaises(CaseworkConfigurationError):
                    load_policy(env)
                self.assertEqual(operator_auth_environment_mode(env), 'fail_closed')

    def test_scope_requires_identity_role_and_permission(self):
        policy = load_policy(environment())
        actor = dict(operator_id=OPERATOR, must_change_password=False, mfa_required=False,
                     role_code='rtm.supervisor', permissions=('ops.view','ops.supervise'))
        self.assertTrue(permits_case(policy, SimpleNamespace(**actor), CASE, write=True))
        self.assertFalse(permits_case(policy, SimpleNamespace(**actor), OTHER))
        for change in [{'operator_id':OTHER}, {'must_change_password':True}, {'mfa_required':True},
                       {'role_code':'rtm.presenter'}, {'permissions':()}]:
            self.assertFalse(permits_case(policy, SimpleNamespace(**{**actor, **change}), CASE))
        read_only = SimpleNamespace(**{**actor, 'permissions':('ops.view',)})
        self.assertTrue(permits_case(policy, read_only, CASE))
        self.assertFalse(permits_case(policy, read_only, CASE, write=True))

    def test_status_does_not_disclose_scope_or_keys(self):
        app = FastAPI(); app.include_router(router)
        with patch.dict(os.environ, environment(), clear=True), TestClient(app) as client:
            r = client.get('/ops/auth/status')
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()['auth_profile'], 'production_casework')
            self.assertTrue(r.json()['individual_login_enabled'])
            self.assertNotIn(CASE, r.text)
            self.assertNotIn(OPERATOR, r.text)
            self.assertNotIn(environment()['RTM_OPERATOR_ACCESS_HMAC_KEY'], r.text)
            self.assertIn('no-store', r.headers['cache-control'])

    def test_wrong_origin_rejected_before_database(self):
        app = FastAPI(); app.include_router(router)
        with patch.dict(os.environ, environment(), clear=True), patch('rtm_core.operator_auth_router.get_engine') as engine, TestClient(app) as client:
            r = client.post('/ops/auth/login', headers={'Origin':'https://example.com'},
                            json={'email':'operator@example.com','password':'synthetic-only'})
            self.assertEqual(r.status_code, 403)
            self.assertNotIn('synthetic-only', r.text)
            engine.assert_not_called()

    def test_disabled_route_rejected_before_database(self):
        app = FastAPI(); app.include_router(router)
        with patch.dict(os.environ, {'RTM_ENV':'production'}, clear=True), patch('rtm_core.operator_auth_router.get_engine') as engine, TestClient(app) as client:
            r = client.post('/ops/auth/login', json={'email':'operator@example.com','password':'synthetic-only'})
            self.assertEqual(r.status_code, 503)
            engine.assert_not_called()


if __name__ == '__main__':
    unittest.main()
