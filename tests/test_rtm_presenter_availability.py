from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rtm_core import presenter_access, workspace_router

CASE_ID = "11111111-1111-5111-8111-111111111111"
OPERATOR_ID = "22222222-2222-4222-8222-222222222222"


class PresenterAvailabilityTest(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.scope = SimpleNamespace(
            individual_session=True, scope_all=True, operator_id=OPERATOR_ID,
            permissions=("ops.view", "ops.supervise", "presenter.documents.read"),
        )
        self.runtime_patch = patch.object(presenter_access, "load_presenter_runtime_configuration")
        self.runtime = self.runtime_patch.start()
        self.repository_patch = patch.object(presenter_access, "SqlPresenterRepository")
        self.repository = self.repository_patch.start().return_value
        self.access = self.repository.has_active_synthetic_case_access
        self.access.return_value = True
        self.addCleanup(self.runtime_patch.stop)
        self.addCleanup(self.repository_patch.stop)

    def available(self):
        return presenter_access.presenter_available_for_scope(
            self.conn, case_id=CASE_ID, scope=self.scope,
        )

    def test_accepted_assignment_uses_current_case_and_operator(self):
        self.assertTrue(self.available())
        self.access.assert_called_once_with(
            self.conn, case_id=CASE_ID, operator_id=OPERATOR_ID,
        )
        self.conn.begin_nested.assert_called_once_with()

    def test_supervisor_scope_does_not_replace_presenter_assignment(self):
        self.access.return_value = False
        self.assertFalse(self.available())

    def test_revocation_is_rechecked_without_cached_permission(self):
        self.access.side_effect = [True, False]
        self.assertTrue(self.available())
        self.assertFalse(self.available())
        self.assertEqual(self.access.call_count, 2)

    def test_non_individual_session_never_queries_case_access(self):
        self.scope.individual_session = False
        self.assertFalse(self.available())
        self.access.assert_not_called()

    def test_missing_read_permission_never_queries_case_access(self):
        self.scope.permissions = ("ops.view", "ops.supervise")
        self.assertFalse(self.available())
        self.access.assert_not_called()

    def test_missing_scope_fails_closed(self):
        self.scope = None
        self.assertFalse(self.available())
        self.access.assert_not_called()

    def test_disabled_runtime_never_queries_case_access(self):
        self.runtime.side_effect = RuntimeError("runtime closed")
        self.assertFalse(self.available())
        self.access.assert_not_called()

    def test_database_failure_rolls_back_savepoint_and_hides_access(self):
        self.access.side_effect = RuntimeError("schema unavailable")
        self.assertFalse(self.available())
        exit_call = self.conn.begin_nested.return_value.__exit__.call_args
        self.assertIs(exit_call.args[0], RuntimeError)

    def test_non_boolean_permission_projection_fails_closed(self):
        self.access.return_value = "true"
        self.assertFalse(self.available())

    def test_workspace_response_uses_authenticated_scope_on_same_connection(self):
        engine = MagicMock()
        engine.begin.return_value.__enter__.return_value = self.conn
        with (
            patch.object(workspace_router, "get_engine", return_value=engine),
            patch.object(workspace_router, "require_operator_token"),
            patch.object(workspace_router, "load_ops_case_scope", return_value=self.scope),
            patch.object(workspace_router, "require_case_in_scope", return_value=CASE_ID),
            patch.object(workspace_router, "build_case_workspace",
                         return_value={"ok": True, "actions": {"other_action": False}}),
        ):
            payload = workspace_router.get_case_workspace(
                CASE_ID, SimpleNamespace(), x_operator_token="server-side-only",
            )
        self.assertEqual(payload["actions"], {"other_action": False, "presenter_available": True})
        self.access.assert_called_once_with(self.conn, case_id=CASE_ID, operator_id=OPERATOR_ID)


if __name__ == "__main__":
    unittest.main()
