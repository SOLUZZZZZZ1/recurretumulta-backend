import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

import app as application
import billing
from rtm_core.journey_routes import is_individually_guarded_journey
from rtm_core import management_client_access


class StagingJourneyIntegrationTest(unittest.TestCase):
    def test_exact_journey_routes_keep_their_own_guard(self):
        case = '11111111-1111-4111-8111-111111111111'
        for method, path in (
            ('GET', '/ops/core/cases'),
            ('GET', f'/ops/core/cases/{case}/work'),
            ('POST', f'/ops/core/cases/{case}/management/quotes'),
            ('GET', f'/ops/core/cases/{case}/communications'),
        ):
            with self.subTest(path=path):
                self.assertTrue(is_individually_guarded_journey({'type':'http', 'method':method, 'path':path}))

    def test_legacy_routes_are_not_exempted_by_prefix(self):
        case = '11111111-1111-4111-8111-111111111111'
        for method, suffix in (
            ('GET', 'workspace'), ('GET', 'study'), ('POST', 'study/actions'),
            ('POST', 'reanalysis/run'), ('POST', 'document-extractions/run'),
            ('POST', 'management-not-a-route'), ('DELETE', 'management/quotes'),
        ):
            self.assertFalse(is_individually_guarded_journey(
                {'type':'http', 'method':method, 'path':f'/ops/core/cases/{case}/{suffix}'}))

    def test_management_disabled_does_not_touch_database(self):
        with patch.dict(os.environ, {'RTM_ENABLE_MANAGEMENT_JOURNEY':'0'}, clear=False), \
             patch('rtm_core.management_router.get_engine', side_effect=AssertionError('unexpected DB')):
            response = TestClient(application.app).post('/client/management/access',
                json={'case_id':'11111111-1111-4111-8111-111111111111', 'email':'client@example.com'})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()['detail']['code'], 'MANAGEMENT_JOURNEY_NOT_ENABLED')
            self.assertIn('no-store', response.headers['cache-control'])

    def test_new_client_cookie_is_covered_by_ambiguity_guard(self):
        response = TestClient(application.app).get('/health/live', headers={
            'Cookie':management_client_access.COOKIE+'=one; '+management_client_access.COOKIE+'=two'})
        self.assertEqual(response.status_code, 400)

    def test_document_input_policy_stays_closed_on_public_staging_upload(self):
        with patch.dict(os.environ, {'RTM_ENV':'staging', 'RTM_DOCUMENT_INPUT_POLICY':'synthetic_only'}):
            response = TestClient(application.app).post('/cases/intake-draft', content=b'not-a-document')
            self.assertEqual(response.status_code, 409)

    def test_administrative_http_migrations_remain_absent(self):
        paths = [route.path for route in application.app.routes]
        self.assertFalse(any('migrate' in path for path in paths))
        self.assertIn('/billing/management/webhook/{mode}', paths)
        self.assertIn('/ops/core/cases/{case_id}/management/quotes', paths)
        self.assertIn('/ops/rehearsal/radar/profile', paths)

    def test_verified_balance_notice_never_enters_initial_payment_ledger(self):
        for event_type in ('checkout.session.completed', 'charge.refunded'):
            event = {'id':'evt_synthetic', 'type':event_type, 'data':{'object':{
                'id':'cs_test_synthetic', 'metadata':{'contract':'rtm_management_balance_v1'}}}}
            with self.subTest(event_type=event_type), \
                 patch.dict(os.environ, {'STRIPE_SECRET_KEY':'sk_test_synthetic_only', 'STRIPE_WEBHOOK_SECRET':'whsec_synthetic'}), \
                 patch.object(billing, 'require_http_capability'), \
                 patch.object(billing.stripe.Webhook, 'construct_event', return_value=event), \
                 patch.object(billing, 'get_engine', side_effect=AssertionError('initial ledger touched')):
                response = TestClient(application.app).post('/billing/webhook', content=b'{}',
                    headers={'stripe-signature':'synthetic-verified-by-fixture'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['handled_by'], 'management_balance_webhook')


if __name__ == '__main__':
    unittest.main()
