import unittest
from unittest.mock import Mock, patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from rtm_core import generic_authorization as generic, generic_authorization_router as routes
from rtm_core.ops_case_scope import OpsCaseScope

CASE = "35567a72-bb37-40e3-9456-8bc8fb9cb4f9"


class LocalRecoveryTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(routes.ops_router)
        self.client = TestClient(app)
        self.profile = self.enterContext(patch.object(routes, "require_local_generic_profile"))
        self.scope = self.enterContext(patch("rtm_core.ops_case_scope.load_ops_case_scope", return_value=OpsCaseScope(
            "11111111-1111-4111-8111-111111111111", "rtm.supervisor", ("ops.view", "ops.supervise"), True, True)))
        self.enterContext(patch("rtm_core.ops_case_scope.require_case_in_scope", return_value=CASE))
        engine = Mock()
        engine.begin.return_value.__enter__ = Mock(return_value=Mock())
        engine.begin.return_value.__exit__ = Mock(return_value=False)
        self.engine = self.enterContext(patch("database.get_engine", return_value=engine))
        self.database = self.enterContext(patch("scripts.rtm_local_operator_setup.require_local_database"))
        self.snapshot = self.enterContext(patch.object(generic, "load_snapshot"))
        self.audit = self.enterContext(patch.object(generic, "_append_event"))
        self.issue = self.enterContext(patch("public_case_access.issue_case_access_token", return_value="synthetic-token"))

    def post(self):
        return self.client.post(f"/ops/core/cases/{CASE}/recover-local-access")

    def test_recovery_is_private_and_audit_contains_no_token(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertEqual(response.json()["case_id"], CASE)
        self.assertTrue(response.json()["local_only"])
        self.database.assert_called_once()
        self.assertTrue(self.snapshot.call_args.kwargs["mutate"])
        self.assertNotIn("synthetic-token", str(self.audit.call_args))
        self.assertEqual(self.audit.call_args.args[2], "local_case_access_recovered")

    def test_nonlocal_environment_has_no_recovery(self):
        self.profile.side_effect = HTTPException(404, "Not found")
        self.assertEqual(self.post().status_code, 404)
        self.engine.assert_not_called()

    def test_missing_session_is_rejected_before_database(self):
        self.scope.side_effect = HTTPException(401, "Session required")
        self.assertEqual(self.post().status_code, 401)
        self.engine.assert_not_called()

    def test_legacy_and_non_supervisors_are_rejected(self):
        for role, permissions, individual in [("rtm.operator", ("ops.view",), True), ("rtm.supervisor", ("ops.view",), True), ("rtm.supervisor", ("ops.supervise",), False)]:
            self.scope.return_value = OpsCaseScope("operator", role, permissions, True, individual)
            self.assertEqual(self.post().status_code, 403)
        self.issue.assert_not_called()

    def test_real_or_ineligible_case_cannot_receive_access(self):
        self.snapshot.side_effect = HTTPException(409, "Not a local synthetic case")
        self.assertEqual(self.post().status_code, 409)
        self.issue.assert_not_called()
        self.audit.assert_not_called()

    def test_wrong_database_cannot_receive_access(self):
        self.database.side_effect = HTTPException(503, "Wrong database")
        self.assertEqual(self.post().status_code, 503)
        self.issue.assert_not_called()

    def test_audit_failure_does_not_deliver_token(self):
        self.audit.side_effect = RuntimeError("Audit unavailable")
        with self.assertRaises(RuntimeError):
            self.post()
