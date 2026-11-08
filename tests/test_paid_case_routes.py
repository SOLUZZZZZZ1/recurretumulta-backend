from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rtm_core.casework_router import router,casework_connection
from rtm_core.production_casework import CaseworkPolicy


class RoutesTest(unittest.TestCase):
    def setUp(self):
        self.case=str(uuid4());self.doc=str(uuid4());self.actor=str(uuid4())
        self.session=SimpleNamespace(operator_id=self.actor,must_change_password=False,mfa_required=False,
            role_code='rtm.supervisor',permissions=('ops.view','ops.supervise'))
        self.policy=CaseworkPolicy((self.case,),(self.actor,),'https://www.recurretumulta.eu',datetime.now(timezone.utc)+timedelta(hours=1))
        app=FastAPI();app.include_router(router)
        app.dependency_overrides[casework_connection]=lambda:object()
        self.client=TestClient(app)
        self.patches=[patch('rtm_core.casework_router.configured_policy',return_value=self.policy),
            patch('rtm_core.casework_router.load_operator_session_with_device_possession',return_value=self.session),
            patch('rtm_core.casework_router.document_row',return_value={'kind':'original','mime':'application/pdf'}),
            patch('rtm_core.casework_router.read_custody',return_value=(b'pdf','a'*64,'not_recorded')),
            patch('rtm_core.casework_router.render',return_value={'pages':2,'width':500,'height':700})]
        self.mocks=[p.start() for p in self.patches]
        self.path=f'/ops/core/cases/{self.case}/documents/{self.doc}'

    def tearDown(self):
        self.client.close()
        for p in self.patches:p.stop()

    def test_metadata_private_no_coordinates_and_page_preconditions(self):
        r=self.client.get(self.path+'/view');self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['operator_export_allowed'],False)
        self.assertEqual(r.json()['recorded_hash_state'],'not_recorded')
        self.assertIn('no-store',r.headers['cache-control'])
        self.assertNotIn('b2_',r.text)
        self.assertEqual(self.client.get(self.path+'/pages/1').status_code,428)
        self.assertEqual(self.client.get(self.path+'/pages/1',headers={'If-Match':'"wrong"'}).status_code,412)
        self.mocks[4].return_value=b'\x89PNG\r\n\x1a\nTEST'
        page=self.client.get(self.path+'/pages/1',headers={'If-Match':'"'+'a'*64+'"'})
        self.assertEqual(page.status_code,200);self.assertEqual(page.headers['content-type'],'image/png')
        self.assertTrue(page.headers['content-disposition'].startswith('inline;'))

    def test_authentication_scope_and_legacy_token_rejected(self):
        self.mocks[1].return_value=None
        r=self.client.get(self.path+'/view',headers={'X-Operator-Token':'shared-token-fixture'})
        self.assertEqual(r.status_code,401);self.mocks[3].assert_not_called()
        self.mocks[1].return_value=self.session
        self.assertEqual(self.client.get(self.path.replace(self.case,str(uuid4()))+'/view').status_code,403)
        self.mocks[3].assert_not_called()

    def test_permission_origin_and_exception_sanitization(self):
        self.session.permissions=('ops.view',)
        self.assertEqual(self.client.post(f'/ops/core/cases/{self.case}/study',json={}).status_code,403)
        self.session.permissions=('ops.view','ops.supervise')
        self.assertEqual(self.client.post(f'/ops/core/cases/{self.case}/study',json={}).status_code,403)
        bad=self.client.post(f'/ops/core/cases/{self.case}/study',json={'secret':'DO_NOT_ECHO'},headers={'Origin':self.policy.origin})
        self.assertEqual(bad.status_code,422);self.assertNotIn('DO_NOT_ECHO',bad.text)
        self.mocks[3].side_effect=RuntimeError('DO_NOT_ECHO_PRIVATE_COORDINATES')
        error=self.client.get(self.path+'/view')
        self.assertEqual(error.status_code,503);self.assertNotIn('DO_NOT_ECHO',error.text)
