"""Local configuration tests; no Render, B2 or payment provider connection."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from rtm_core.casework_policy import (
    STAGING_BUCKET, STAGING_DATABASE_HOST, STAGING_ORIGIN, STAGING_SERVICE,
    TEST_ORIGIN, CaseworkConfigurationError, load_policy, origin_allowed,
)
from rtm_core.environment_contract import build_environment_preflight
from rtm_core.operator_auth_request import load_operator_auth_runtime_config
from rtm_core.operator_auth_router import router
from test_provider_test_policy import environment as new_stack_environment


def environment():
    env = new_stack_environment()
    env.update({
        'RTM_PROVIDER_TEST_TARGET': 'existing_staging',
        'RENDER_SERVICE_NAME': 'recurretumulta-backend-1',
        'RENDER_SERVICE_ID': STAGING_SERVICE,
        'RTM_PROVIDER_TEST_DATABASE_HOST': STAGING_DATABASE_HOST,
        'DATABASE_URL': 'postgresql+psycopg://synthetic:unused@'+STAGING_DATABASE_HOST+'/rtm_staging',
        'B2_BUCKET': STAGING_BUCKET, 'RTM_PROVIDER_TEST_B2_BUCKET': STAGING_BUCKET,
        'RTM_PRODUCTION_B2_BUCKET': 'recurretumulta-prod',
        'FRONTEND_URL': STAGING_ORIGIN, 'RTM_CASEWORK_PUBLIC_ORIGIN': STAGING_ORIGIN,
        'ALLOWED_ORIGINS': STAGING_ORIGIN,
        'RTM_ALLOWED_HOSTS': 'recurretumulta-backend-1.onrender.com',
        'RTM_INSTANCE_ID': 'recurretumulta-staging', 'RTM_DATA_NAMESPACE': 'rtm-staging',
        'RTM_EXPECTED_BRANCH': 'rtm-ai-security-hardening-2026-09-03',
        'RENDER_GIT_BRANCH': 'rtm-ai-security-hardening-2026-09-03',
        'OPERATOR_TOKEN': 'op_A7mQ2vN9kR4xT8pL3sW6cD1hJ5uZ0bY',
        'RTM_PUBLIC_CASE_ACCESS_SECRET': 'case_F9rK3xV7nM2qP8dT4zH6wC1jL5sG0aU',
        'RTM_AUTHORITY_SIGNING_SECRET': 'authority_Z6pD1yW8kQ4mR9vB2tN7cH5xJ3fL0sE',
        'RTM_ENABLE_B2': '0', 'RTM_ENABLE_STRIPE': '0',
        'RTM_ENABLE_DOCUMENT_PROVIDER': '0',
    })
    return env


class ExistingStagingPolicyTest(unittest.TestCase):
    def test_exact_staging_keeps_preflight_scope_and_individual_auth(self):
        env = environment()
        preflight = build_environment_preflight(env)
        self.assertTrue(preflight.safe, preflight.blockers)
        policy = load_policy(env)
        self.assertEqual(policy.origin, STAGING_ORIGIN)
        self.assertEqual(policy.case_ids, (env['RTM_CASEWORK_ALLOWED_CASE_IDS'],))
        auth = load_operator_auth_runtime_config(env)
        self.assertTrue(auth.provider_test_casework)
        self.assertFalse(auth.production_casework)

    def test_target_selection_is_explicit_and_not_a_fallback(self):
        for target in ('new_stack', 'unknown', '', 'EXISTING_STAGING'):
            with self.subTest(target=target), self.assertRaises(CaseworkConfigurationError):
                load_policy({**environment(), 'RTM_PROVIDER_TEST_TARGET': target})
        env = environment()
        del env['RTM_PROVIDER_TEST_TARGET']
        with self.assertRaises(CaseworkConfigurationError):
            load_policy(env)

    def test_resource_mixups_and_production_settings_are_rejected(self):
        for values in (
            {'RENDER_SERVICE_ID': 'srv-d5fu32hr0fns73c3oa0g'},
            {'RENDER_SERVICE_NAME': 'recurretumulta-backend'},
            {'RTM_PROVIDER_TEST_DATABASE_HOST': 'dpg-d5ftkbqli9vc7390hni0-a'},
            {'DATABASE_URL': 'postgresql://unused@'+STAGING_DATABASE_HOST+'/recurretumulta'},
            {'DATABASE_URL': 'postgresql://unused@'+STAGING_DATABASE_HOST+'.evil.invalid/rtm_staging'},
            {'DATABASE_URL': 'postgresql://unused@'+STAGING_DATABASE_HOST+'/rtm_staging?host=elsewhere'},
            {'DATABASE_URL': 'postgresql://unused@'+STAGING_DATABASE_HOST+':invalid/rtm_staging'},
            {'B2_BUCKET': 'recurretumulta-prod'},
            {'B2_BUCKET': 'unverified-staging', 'RTM_PROVIDER_TEST_B2_BUCKET': 'unverified-staging'},
            {'RTM_PRODUCTION_B2_BUCKET': ''},
            {'FRONTEND_URL': 'https://www.resuelvetusmovidas.es'},
            {'RTM_CASEWORK_PUBLIC_ORIGIN': TEST_ORIGIN},
            {'STRIPE_SECRET_KEY': 'sk_live_unused'},
            {'RTM_MANAGEMENT_STRIPE_TEST_KEY': 'sk_live_unused'},
            {'RTM_ENABLE_PRODUCTION_CASEWORK': '1'},
            {'RTM_DOCUMENT_INPUT_POLICY': 'real'},
            {'RTM_ENABLE_OUTBOUND_EMAIL': '1'},
            {'RTM_ENABLE_EXTERNAL_SUBMISSION': '1'},
            {'RTM_CASEWORK_ALLOWED_CASE_IDS': '2af6f88a-f72a-46f9-9fd7-f3c6d44c6aee'},
        ):
            with self.subTest(fields=tuple(values)), self.assertRaises(CaseworkConfigurationError):
                load_policy({**environment(), **values})

    def test_existing_preflight_remains_required(self):
        for key in ('RTM_PUBLIC_CASE_ACCESS_SECRET', 'RTM_AUTHORITY_SIGNING_SECRET',
                    'OPERATOR_TOKEN', 'RTM_ALLOWED_HOSTS', 'RTM_EXPECTED_BRANCH'):
            env = environment()
            del env[key]
            with self.subTest(key=key), self.assertRaises(CaseworkConfigurationError):
                load_policy(env)
        with self.assertRaises(CaseworkConfigurationError):
            load_policy({**environment(), 'ALLOWED_ORIGINS': STAGING_ORIGIN+',https://www.resuelvetusmovidas.es'})

    def test_staging_origin_does_not_cross_targets(self):
        self.assertTrue(origin_allowed(environment(), STAGING_ORIGIN))
        self.assertFalse(origin_allowed(environment(), TEST_ORIGIN))
        self.assertFalse(origin_allowed(new_stack_environment(), STAGING_ORIGIN))
        self.assertFalse(origin_allowed(environment(), 'https://www.resuelvetusmovidas.es'))

    def test_no_worker_identity_is_invented_for_existing_staging(self):
        with self.assertRaises(CaseworkConfigurationError):
            load_policy(environment(), service_name='rtm-case-mail-pilot')

    def test_status_route_works_without_connecting_to_database(self):
        app = FastAPI()
        app.include_router(router)
        with patch.dict(os.environ, environment(), clear=True), patch('rtm_core.operator_auth_router.get_engine') as engine:
            with TestClient(app) as client:
                response = client.get('/ops/auth/status')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['auth_profile'], 'provider_test_casework')
                denied = client.post('/ops/auth/login', headers={'Origin': TEST_ORIGIN},
                                     json={'email': 'fixture@example.com', 'password': 'synthetic'})
                self.assertEqual(denied.status_code, 403)
            engine.assert_not_called()

    def test_restored_preflight_cli_runs_without_app_or_provider(self):
        env = environment()
        env['PYTHONPATH'] = os.pathsep.join(sys.path)
        for key in ('SystemRoot', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'LOCALAPPDATA'):
            if key in os.environ:
                env[key] = os.environ[key]
        result = subprocess.run([sys.executable, '-B', 'scripts/rtm_environment_preflight.py', '--compact'],
                                cwd=Path(__file__).parents[1], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['safe'])
        for key in ('OPERATOR_TOKEN', 'RTM_PUBLIC_CASE_ACCESS_SECRET', 'RTM_AUTHORITY_SIGNING_SECRET'):
            self.assertNotIn(env[key], result.stdout)
