from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "rtm_connect" / "supervisor_router.py"
APP = ROOT / "app.py"


def _declared_routes(source: str) -> dict[str, str]:
    routes: dict[str, str] = {}
    tree = ast.parse(source, filename=str(ROUTER))
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            function = decorator.func
            if not isinstance(function, ast.Attribute):
                continue
            if not isinstance(function.value, ast.Name):
                continue
            if function.value.id != "router" or not decorator.args:
                continue
            path = decorator.args[0]
            if isinstance(path, ast.Constant) and isinstance(path.value, str):
                routes[path.value] = function.attr.lower()
    return routes


class ConnectC5SupervisorRoutesContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = ROUTER.read_text(encoding="utf-8")
        cls.routes = _declared_routes(cls.source)

    def test_router_prefix_is_domain_specific(self):
        self.assertIn('prefix="/ops/connect/supervisor"', self.source)

    def test_surface_is_get_only(self):
        self.assertEqual(
            self.routes,
            {
                "/status": "get",
                "/overview": "get",
                "/attention": "get",
                "/actions": "get",
                "/actions/{action_id}": "get",
                "/manual-tasks": "get",
                "/webhook-dlq": "get",
            },
        )
        self.assertEqual(set(self.routes.values()), {"get"})

    def test_protected_context_is_fail_closed_and_uses_live_permission(self):
        for required in (
            "extract_bearer_token",
            "load_operator_session_with_device_possession",
            'alias="X-RTM-Device"',
            'alias="__Host-rtm_presenter_device"',
            "touch=False",
            "session.must_change_password",
            "session.mfa_required",
            "session_has_connect_supervisor_permission",
            "current_operator_can_supervise",
            "assert_synthetic_supervisor_scope",
            "status_code=401",
            "status_code=403",
            "status_code=503",
        ):
            self.assertIn(required, self.source)
        self.assertNotIn(
            "from rtm_core.operator_auth_service import load_operator_session",
            self.source,
        )
        self.assertGreaterEqual(self.source.count("field(repr=False)"), 7)

    def test_runtime_flag_disabled_is_hidden_as_not_found(self):
        self.assertIn("ConnectSupervisorRoutesDisabled", self.source)
        self.assertIn("status_code=404", self.source)
        self.assertIn('detail="Not found"', self.source)
        self.assertIn("connect_supervisor_gate_middleware", self.source)

    def test_surface_is_hidden_from_openapi_and_db_identity_is_bound(self):
        self.assertIn("include_in_schema=False", self.source)
        self.assertIn(
            "assert_connect_supervisor_database_identity",
            self.source,
        )

    def test_every_successful_read_is_audited_append_only(self):
        self.assertIn("record_operator_access_event", self.source)
        self.assertIn('risk_flags=("supervisor_read", "connect_c5")', self.source)
        self.assertNotIn('"audit_event_id"', self.source)
        for event in (
            "connect.supervisor.status_viewed",
            "connect.supervisor.overview_viewed",
            "connect.supervisor.attention_viewed",
            "connect.supervisor.actions_viewed",
            "connect.supervisor.action_viewed",
            "connect.supervisor.manual_tasks_viewed",
            "connect.supervisor.dlq_viewed",
        ):
            self.assertIn(event, self.source)

    def test_responses_are_not_cacheable(self):
        self.assertIn('response.headers["Cache-Control"]', self.source)
        self.assertIn("no-store, max-age=0", self.source)
        self.assertIn('response.headers["Pragma"]', self.source)

    def test_runtime_projection_allowlist_fails_closed(self):
        self.assertIn("assert_sanitized_supervisor_projection", self.source)
        self.assertEqual(self.source.count("return _safe_projection({"), 7)

    def test_filters_and_pagination_are_bounded(self):
        self.assertIn("pattern=_ACTION_STATUS_PATTERN", self.source)
        self.assertIn("pattern=_RISK_PATTERN", self.source)
        self.assertIn("pattern=_MANUAL_STATUS_PATTERN", self.source)
        self.assertIn("limit: int = Query(default=50, ge=1, le=100)", self.source)
        self.assertIn("offset: int = Query(default=0, ge=0, le=10000)", self.source)
        self.assertIn(
            "history_limit: int = Query(default=100, ge=1, le=200)",
            self.source,
        )

    def test_router_does_not_import_or_expose_execution_controls(self):
        for forbidden in (
            "queue_action",
            "start_attempt",
            "confirm_action",
            "complete_manual_handoff",
            "reconcile_webhook",
            "execute_synthetic_echo",
            "receive_synthetic_webhook",
            "verify_webhook",
            "dead_letter_webhook",
        ):
            self.assertNotIn(forbidden, self.source)
        self.assertIn('"execution_controls_available": False', self.source)

    def test_app_wires_projection_after_individual_auth(self):
        source = APP.read_text(encoding="utf-8")
        self.assertIn("from rtm_connect.supervisor_router import", source)
        self.assertIn("app.include_router(connect_supervisor_router)", source)
        self.assertLess(
            source.index("app.include_router(rtm_operator_auth_router)"),
            source.index("app.include_router(connect_supervisor_router)"),
        )
        self.assertLess(
            source.index("app.add_middleware("),
            source.index(
                'app.middleware("http")(connect_supervisor_gate_middleware)'
            ),
        )


if __name__ == "__main__":
    unittest.main()
