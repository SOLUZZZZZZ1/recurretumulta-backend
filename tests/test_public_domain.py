"""Domain compatibility on the published OPS application; synthetic data only."""
from contextlib import contextmanager
from rtm_core.management_router import logout_client, LogoutInput
from rtm_core.management_contract import ManagementRejected
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
import unittest

from fastapi import HTTPException
from starlette.requests import Request

from rtm_core.casework_router import require_casework
from rtm_core.environment_contract import _DEFAULT_PRODUCTION_FRONTEND_HOSTS
from rtm_core.production_casework import load_policy, CaseworkConfigurationError
from rtm_core.trusted_origins import trusted_frontend_origin, PUBLIC_CASEWORK_ORIGINS


class PublicDomainTest(unittest.TestCase):
    def environment(self, origin):
        return {
            'RTM_ENABLE_PRODUCTION_CASEWORK': '1',
            'RTM_ENV': 'production',
            'RENDER_SERVICE_NAME': 'recurretumulta-backend',
            'RTM_CASEWORK_PUBLIC_ORIGIN': origin,
            'RTM_CASEWORK_EXPIRES_AT': (
                datetime.now(timezone.utc) + timedelta(minutes=30)
            ).isoformat(),
            'RTM_CASEWORK_ALLOWED_CASE_IDS': str(uuid4()),
            'RTM_CASEWORK_ALLOWED_OPERATOR_IDS': str(uuid4()),
        }

    def test_exact_origins_preserve_case_operator_and_expiry(self):
        for origin in PUBLIC_CASEWORK_ORIGINS:
            with self.subTest(origin=origin):
                env = self.environment(origin)
                policy = load_policy(env)
                self.assertEqual(policy.origin, origin)
                self.assertEqual(policy.case_ids, (env['RTM_CASEWORK_ALLOWED_CASE_IDS'],))
                self.assertEqual(policy.operator_ids, (env['RTM_CASEWORK_ALLOWED_OPERATOR_IDS'],))
                self.assertEqual(policy.expires_at.isoformat(), env['RTM_CASEWORK_EXPIRES_AT'])
                with patch.dict('os.environ', {
                    'FRONTEND_URL': origin, 'FRONTEND_BASE_URL': '', 'RTM_ENV': 'production'
                }, clear=True):
                    self.assertEqual(trusted_frontend_origin(), origin)
        self.assertIn('www.resuelvetusmovidas.es', _DEFAULT_PRODUCTION_FRONTEND_HOSTS)
        self.assertIn('resuelvetusmovidas.es', _DEFAULT_PRODUCTION_FRONTEND_HOSTS)

    def test_unapproved_casework_origins_rejected(self):
        for origin in (
            'https://www.resuelvetusmovidas.es.evil.example',
            'http://www.resuelvetusmovidas.es',
            'https://www.resuelvetusmovidas.es/path',
            'https://preview.vercel.app',
            'https://resuelvetusmovidas.es',
            'https://user@www.resuelvetusmovidas.es',
        ):
            with self.subTest(origin=origin), self.assertRaises(CaseworkConfigurationError):
                load_policy(self.environment(origin))

    def test_expiry_and_backend_identity_still_enforced(self):
        env = self.environment('https://www.resuelvetusmovidas.es')
        for overrides in (
            {'RTM_CASEWORK_EXPIRES_AT': '2020-01-01T00:00:00+00:00'},
            {'RENDER_SERVICE_NAME': 'recurretumulta-backend-1'},
            {'RTM_ENABLE_LOCAL_OPERATOR_AUTH': '1'},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(CaseworkConfigurationError):
                load_policy({**env, **overrides})

    def test_write_guard_accepts_only_active_origin(self):
        for active_origin in PUBLIC_CASEWORK_ORIGINS:
            policy = load_policy(self.environment(active_origin))
            session = SimpleNamespace(
                operator_id=policy.operator_ids[0], must_change_password=False,
                mfa_required=False, role_code='rtm.supervisor',
                permissions={'ops.view', 'ops.supervise'},
            )
            with patch('rtm_core.casework_router.configured_policy', return_value=policy), patch(
                'rtm_core.casework_router.load_operator_session_with_device_possession',
                return_value=session,
            ):
                for request_origin in (*PUBLIC_CASEWORK_ORIGINS, 'https://evil.example', ''):
                    request = Request({'type': 'http', 'method': 'POST', 'headers': [
                        (b'origin', request_origin.encode()),
                    ]})
                    if request_origin == active_origin:
                        result = require_casework(policy.case_ids[0], request, conn=None)
                        self.assertEqual(result.case_id, policy.case_ids[0])
                    else:
                        with self.assertRaises(HTTPException) as caught:
                            require_casework(policy.case_ids[0], request, conn=None)
                        self.assertEqual(caught.exception.status_code, 403)
                        self.assertEqual(caught.exception.detail['code'], 'CASEWORK_ORIGIN_DENIED')

    def test_logout_accepts_only_configured_origin_even_after_expiry(self):
        origin='https://www.resuelvetusmovidas.es';body=LogoutInput(session_ref='a'*64)
        @contextmanager
        def connection():yield None
        with patch.dict('os.environ',{'RTM_CASEWORK_PUBLIC_ORIGIN':origin}),patch('rtm_core.management_router.connection',connection),patch('rtm_core.management_router.client.logout',return_value={'ok':True}) as revoke:
            request=Request({'type':'http','headers':[(b'origin',origin.encode())]})
            self.assertTrue(logout_client(body,request)['ok']);self.assertEqual(revoke.call_count,1)
            request=Request({'type':'http','headers':[(b'origin',b'https://www.recurretumulta.eu')]})
            with self.assertRaises(ManagementRejected):logout_client(body,request)
            self.assertEqual(revoke.call_count,1)

if __name__ == '__main__':
    unittest.main()
