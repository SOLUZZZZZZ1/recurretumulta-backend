import io
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from pypdf import PdfReader
import cases
from public_case_access import issue_case_access_token
from rtm_core import staging_client as policy, staging_client_router as routes, staging_rehearsal as legacy

OPERATOR='11111111-1111-4111-8111-111111111111'
GRANT=policy.ClientRehearsalGrant(OPERATOR,'22222222-2222-4222-8222-222222222222','soluzziona@gmail.com')
SECRET='synthetic-test-secret-longer-than-thirty-two'


class ClientScopeTests(unittest.TestCase):
    def test_new_run_is_distinct_but_repeated_run_recovers_same_case(self):
        self.assertNotEqual(GRANT.case_id,legacy.RehearsalGrant(OPERATOR).case_id)
        other=policy.ClientRehearsalGrant(OPERATOR,'33333333-3333-4333-8333-333333333333',GRANT.recipient)
        self.assertNotEqual(GRANT.case_id,other.case_id)
        self.assertEqual(GRANT.case_id,policy.ClientRehearsalGrant(OPERATOR,GRANT.run_id,GRANT.recipient).case_id)

    def test_configuration_requires_the_new_scope_controlled_contact_and_activation(self):
        cfg=SimpleNamespace(operator_ids=(OPERATOR,),case_ids=(GRANT.case_id,))
        env={'RTM_ENABLE_STAGING_CLIENT_INTAKE':'1','RTM_PROVIDER_TEST_TARGET':'existing_staging',
             'RTM_STAGING_CLIENT_RUN_ID':GRANT.run_id,'RTM_CASE_MAIL_ALLOWED_RECIPIENTS':GRANT.recipient}
        with patch.object(policy,'require_profile'),patch.object(policy,'load_policy',return_value=cfg):
            with patch.dict(os.environ,env,clear=True):self.assertEqual(policy.configured_grant(),GRANT)
            for change in ({'RTM_ENABLE_STAGING_CLIENT_INTAKE':'0'},{'RTM_STAGING_CLIENT_RUN_ID':'invalid'},
                           {'RTM_PROVIDER_TEST_TARGET':'new_stack'},{'RTM_CASE_MAIL_ALLOWED_RECIPIENTS':'different@example.com'}):
                with self.subTest(change=change),patch.dict(os.environ,{**env,**change},clear=True),self.assertRaises(HTTPException):policy.configured_grant()
            with patch.dict(os.environ,env,clear=True):
                cfg.case_ids=(legacy.RehearsalGrant(OPERATOR).case_id,)
                with self.assertRaises(HTTPException):policy.configured_grant()

    def test_expired_or_invalid_boundary_fails_closed(self):
        with patch.object(policy,'require_profile'),patch.dict(os.environ,{'RTM_ENABLE_STAGING_CLIENT_INTAKE':'1','RTM_PROVIDER_TEST_TARGET':'existing_staging'}),patch.object(policy,'load_policy',side_effect=policy.CaseworkConfigurationError('expired')):
            with self.assertRaises(HTTPException) as result:policy.configured_grant()
            self.assertEqual(result.exception.status_code,503)

    def test_real_boundary_checks_scope_expiry_and_test_resources(self):
        from test_existing_staging_policy import environment
        env=environment()
        env.update(RTM_ENABLE_STAGING_CLIENT_INTAKE='1',RTM_STAGING_CLIENT_RUN_ID=GRANT.run_id,
                   RTM_CASEWORK_ALLOWED_CASE_IDS=GRANT.case_id,RTM_CASEWORK_ALLOWED_OPERATOR_IDS=OPERATOR,
                   RTM_CASE_MAIL_ALLOWED_RECIPIENTS=GRANT.recipient)
        with patch.object(policy,'require_profile'),patch.dict(os.environ,env,clear=True):
            self.assertEqual(policy.configured_grant(),GRANT)
        for change in ({'RTM_CASEWORK_EXPIRES_AT':(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()},
                       {'RTM_CASEWORK_EXPIRES_AT':(datetime.now(timezone.utc)+timedelta(days=8)).isoformat()},
                       {'STRIPE_SECRET_KEY':'sk_live_invalid_fixture'}, {'B2_BUCKET':'recurretumulta-prod'}):
            with self.subTest(fields=tuple(change)),patch.object(policy,'require_profile'),patch.dict(os.environ,{**env,**change},clear=True),self.assertRaises(HTTPException):
                policy.configured_grant()

    def test_pass_cannot_be_used_as_ops_or_other_case_capability(self):
        with patch.dict(os.environ,{'RTM_PUBLIC_CASE_ACCESS_SECRET':SECRET}),patch.object(policy,'configured_grant',return_value=GRANT):
            token=issue_case_access_token(GRANT.case_id)
            def request(path,token):return Request({'type':'http','method':'GET','path':path,'headers':[(b'x-rtm-client-pass',token.encode())]})
            self.assertEqual(policy.require_client_pass(request('/staging/client/profile',token)),GRANT)
            for path,value in [('/ops/rehearsal/client/access',token),('/staging/client/profile',token[:-1]+'X'),('/staging/client/profile',issue_case_access_token(OPERATOR))]:
                with self.subTest(path=path),self.assertRaises(HTTPException):policy.require_client_pass(request(path,value))

    def test_recovery_rejects_old_or_modified_immutable_profile(self):
        row={'id':GRANT.case_id,'test_mode':True,'department':'traffic','case_type':'fine','contact_name':GRANT.form_fields['full_name'],
             'contact_email':GRANT.recipient,'interested_data':{**GRANT.form_fields,'staging_rehearsal':GRANT.marker}}
        legacy.verify_case_row(row,GRANT)
        for change in ({'test_mode':False},{'contact_email':'other@example.com'},{'id':legacy.RehearsalGrant(OPERATOR).case_id}):
            with self.assertRaises(HTTPException):legacy.verify_case_row({**row,**change},GRANT)

    def test_notice_contains_reviewable_fiction_and_no_paid_or_approved_fixture(self):
        content=policy.fixture('original')[1]
        text=' '.join(p.extract_text() for p in PdfReader(io.BytesIO(content)).pages)
        for expected in ('SIN VALIDEZ','RTM-PRUEBA-2026-1106','6 de noviembre de 2026','200 EUR','26 de noviembre de 2026','no acredita autorización'):
            self.assertIn(expected,text)


class ClientHttpTests(unittest.TestCase):
    def setUp(self):
        app=FastAPI();app.include_router(routes.router);app.include_router(routes.ops_router)
        self.client=TestClient(app)
        self.enterContext(patch.dict(os.environ,{'RTM_PUBLIC_CASE_ACCESS_SECRET':SECRET}))
        self.enterContext(patch.object(policy,'configured_grant',return_value=GRANT))
        self.enterContext(patch.object(routes,'configured_grant',return_value=GRANT))
        self.enterContext(patch.object(cases,'require_http_capability'))
        self.enterContext(patch.object(cases,'local_operator_auth_requested',return_value=False))
        self.exists=self.enterContext(patch.object(legacy,'existing_case',return_value=False))
        self.enterContext(patch.object(routes,'existing_case',return_value=False))
        self.storage=self.enterContext(patch.object(cases,'upload_bytes',side_effect=lambda *a:('synthetic',str(len(a[2])))))
        self.persist=self.enterContext(patch.object(cases,'_persist_rtm_intake_draft',return_value=[]))
        self.headers={'X-RTM-Client-Pass':issue_case_access_token(GRANT.case_id)}

    def files(self):
        return {name:(*policy.fixture(kind),'application/pdf') for name,kind in [('dni_front','identity_front'),('dni_back','identity_back')]}

    def test_exact_client_intake_uses_ordinary_unpaid_unauthorized_transaction(self):
        response=self.client.post('/staging/client/intake-draft',data=GRANT.form_fields,files=self.files(),headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['case_id'],GRANT.case_id)
        self.assertFalse(response.json()['authorized'])
        self.assertTrue(response.json()['test_mode'])
        self.assertNotIn('payment_status',self.persist.call_args.args[-2])
        self.assertEqual(self.persist.call_args.args[-1],GRANT)
        self.assertEqual(self.storage.call_count,2)

    def test_missing_wrong_pass_or_extra_field_never_uploads(self):
        for headers in ({},{'X-RTM-Client-Pass':issue_case_access_token(OPERATOR)}):
            response=self.client.post('/staging/client/intake-draft',data=GRANT.form_fields,files=self.files(),headers=headers)
            self.assertEqual(response.status_code,401)
        response=self.client.post('/staging/client/intake-draft',data={**GRANT.form_fields,'test_mode':'true'},files=self.files(),headers=self.headers)
        self.assertEqual(response.status_code,422)
        self.storage.assert_not_called();self.persist.assert_not_called()

    def test_changed_person_consent_or_file_never_uploads(self):
        for change in ({'full_name':'REAL PERSON'},{'email':'other@example.com'},{'privacy_accepted':'false'},{'representation_confirmed':'false'}):
            response=self.client.post('/staging/client/intake-draft',data={**GRANT.form_fields,**change},files=self.files(),headers=self.headers)
            self.assertEqual(response.status_code,422,response.text)
        files=self.files();name,raw,mime=files['dni_front'];files['dni_front']=(name,raw+b'changed',mime)
        self.assertEqual(self.client.post('/staging/client/intake-draft',data=GRANT.form_fields,files=files,headers=self.headers).status_code,422)
        self.storage.assert_not_called()

    def test_profile_is_private_and_does_not_create_case(self):
        self.assertEqual(self.client.get('/staging/client/profile').status_code,401)
        response=self.client.get('/staging/client/profile',headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()['existing'])
        self.assertIn('no-store',response.headers['cache-control'])
        self.persist.assert_not_called();self.storage.assert_not_called()

    def test_other_case_rejected_before_read_or_mutation(self):
        response=self.client.post(f'/staging/client/cases/{OPERATOR}/authorize',json={'authority_version':'v1_dgt_homologado','consent':True,'representation_confirmed':True},headers=self.headers)
        self.assertEqual(response.status_code,404)
        self.persist.assert_not_called()

    def test_access_issued_only_by_configured_supervisor_without_creating_case(self):
        with patch.object(routes,'require_supervisor',return_value=legacy.RehearsalGrant(OPERATOR)):
            response=self.client.post('/ops/rehearsal/client/access')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['case_id'],GRANT.case_id)
            self.assertEqual(self.client.post('/ops/rehearsal/client/access',json={}).status_code,422)
        with patch.object(routes,'require_supervisor',return_value=legacy.RehearsalGrant(GRANT.run_id)):
            self.assertEqual(self.client.post('/ops/rehearsal/client/access').status_code,403)
        self.persist.assert_not_called()
