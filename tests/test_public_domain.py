from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from contextlib import contextmanager
from uuid import uuid4
import unittest
from starlette.requests import Request
from rtm_core.production_casework import load_policy,CaseworkConfigurationError
from rtm_core.trusted_origins import trusted_frontend_origin,PUBLIC_CASEWORK_ORIGINS
from rtm_core.environment_contract import _DEFAULT_PRODUCTION_FRONTEND_HOSTS
from rtm_core.management_router import logout_client,LogoutInput
from rtm_core.management_contract import ManagementRejected

class PublicDomainTest(unittest.TestCase):
    def environment(self,origin):
        return {'RTM_ENABLE_PRODUCTION_CASEWORK':'1','RTM_ENV':'production','RENDER_SERVICE_NAME':'recurretumulta-backend',
            'RTM_CASEWORK_PUBLIC_ORIGIN':origin,'RTM_CASEWORK_EXPIRES_AT':(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat(),
            'RTM_CASEWORK_ALLOWED_CASE_IDS':str(uuid4()),'RTM_CASEWORK_ALLOWED_OPERATOR_IDS':str(uuid4())}
    def test_exact_new_and_old_origin_preserve_case_operator_and_expiry(self):
        for origin in PUBLIC_CASEWORK_ORIGINS:
            env=self.environment(origin);p=load_policy(env)
            self.assertEqual(p.origin,origin);self.assertEqual(p.case_ids,(env['RTM_CASEWORK_ALLOWED_CASE_IDS'],));self.assertEqual(p.operator_ids,(env['RTM_CASEWORK_ALLOWED_OPERATOR_IDS'],));self.assertEqual(p.expires_at.isoformat(),env['RTM_CASEWORK_EXPIRES_AT'])
            with patch.dict('os.environ',{'FRONTEND_URL':origin,'FRONTEND_BASE_URL':'','RTM_ENV':'production'}):self.assertEqual(trusted_frontend_origin(),origin)
        self.assertIn('www.resuelvetusmovidas.es',_DEFAULT_PRODUCTION_FRONTEND_HOSTS)
    def test_origin_suffix_http_path_or_preview_not_admitted(self):
        for value in ('https://www.resuelvetusmovidas.es.evil.example','http://www.resuelvetusmovidas.es','https://www.resuelvetusmovidas.es/path','https://preview.vercel.app','https://resuelvetusmovidas.es'):
            with self.assertRaises(CaseworkConfigurationError):load_policy(self.environment(value))
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
