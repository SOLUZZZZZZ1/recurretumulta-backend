from __future__ import annotations

import unittest
from unittest.mock import patch

import app


class HealthReadinessTest(unittest.TestCase):
    def test_liveness_does_not_claim_database_readiness(self):
        self.assertTrue(app.health_live().ok)

    def test_database_failure_returns_http_503(self):
        with patch.object(app, "get_engine", side_effect=RuntimeError("private")):
            response = app.health()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.body, b'{"ok":false}')
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_ready_aliases_are_registered(self):
        paths = {str(route.path) for route in app.app.routes}
        self.assertIn("/health/live", paths)
        self.assertIn("/health", paths)
        self.assertIn("/health/ready", paths)


if __name__ == "__main__":
    unittest.main()
