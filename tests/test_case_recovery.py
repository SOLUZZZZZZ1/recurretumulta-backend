"""Exercise the deployed app's recovery route without external services."""
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient


class CaseRecoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-not-a-credential'}):
            from app import app
        cls.app = app

    def request(self, row, query):
        class Connection:
            def execute(self, statement, parameters):
                return self

            def fetchone(self):
                return row

        class Engine:
            @contextmanager
            def begin(self):
                yield Connection()

        with patch('case_recovery.get_engine', return_value=Engine()):
            return TestClient(self.app).get('/cases/continue-lookup', params=query)

    def test_recovery_is_registered_without_duplicate_authorization_writes(self):
        for method, path in [
            ('GET', '/cases/continue-lookup'),
            ('POST', '/cases/{case_id}/authorize'),
            ('POST', '/cases/{case_id}/details'),
        ]:
            routes = [r for r in self.app.routes if getattr(r, 'path', None) == path
                      and method in getattr(r, 'methods', set())]
            self.assertEqual(len(routes), 1, (method, path))

    def test_found_reference_returns_only_navigation_data_and_is_not_cached(self):
        case = '00000000-0000-4000-8000-000000000001'
        response = self.request((case, 'TEST-001', 'pending', 'paid', True,
                                 'private@example.com', {'dni': 'PRIVATE'}), {'q': 'TEST-001'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'ok': True, 'case_id': case, 'expediente_ref': 'TEST-001',
            'status': 'pending', 'payment_status': 'paid', 'authorized': True,
            'has_email': True,
        })
        self.assertIn('no-store', response.headers['cache-control'])
        self.assertNotIn('PRIVATE', response.text)
        self.assertNotIn('private@example.com', response.text)

    def test_unknown_reference_has_case_message_instead_of_missing_route(self):
        response = self.request(None, {'q': 'UNKNOWN-001'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['detail'], 'No hemos encontrado ese expediente')

    def test_blank_reference_rejected_before_database_access(self):
        with patch('case_recovery.get_engine') as engine:
            response = TestClient(self.app).get('/cases/continue-lookup', params={'q': '   '})
        self.assertEqual(response.status_code, 400)
        engine.assert_not_called()

    def test_too_short_reference_rejected(self):
        self.assertEqual(self.request(None, {'q': 'x'}).status_code, 422)
