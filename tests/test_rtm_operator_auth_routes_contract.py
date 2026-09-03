from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OperatorAuthRoutesContractTest(unittest.TestCase):
    def test_app_wires_new_router_and_keeps_legacy(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        ops_source = (ROOT / "ops.py").read_text(encoding="utf-8")
        self.assertIn(
            "from rtm_core.operator_auth_router import",
            app_source,
        )
        self.assertIn(
            "app.include_router(rtm_operator_auth_router)",
            app_source,
        )
        self.assertIn('@router.post("/login")', ops_source)

    def test_presenter_context_requires_device_possession_atomically(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        presenter_source = (
            ROOT / "rtm_presenter_router.py"
        ).read_text(encoding="utf-8")
        self.assertIn("app.include_router(rtm_presenter_router)", app_source)
        self.assertNotIn(
            "dependencies=[Depends(require_operator_device_possession)]",
            app_source,
        )
        self.assertIn(
            "load_operator_session_with_device_possession(",
            presenter_source,
        )
        self.assertIn(
            "load_operator_auth_runtime_config(require_enabled=True)",
            presenter_source,
        )
        self.assertIn("with get_engine().begin() as conn:", presenter_source)

    def test_individual_routes_are_staging_feature_gated(self):
        source = (
            ROOT / "rtm_core" / "operator_auth_request.py"
        ).read_text(encoding="utf-8")
        self.assertIn("RTM_ENABLE_OPERATOR_AUTH_V1", source)
        self.assertIn('environment != "staging"', source)
        self.assertIn("RTM_OPERATOR_ACCESS_HMAC_KEY", source)

    def test_router_exposes_only_session_routes(self):
        source = (
            ROOT / "rtm_core" / "operator_auth_router.py"
        ).read_text(encoding="utf-8")
        for route in (
            '@router.get("/status")',
            '@router.post("/login")',
            '@router.get("/me")',
            '@router.post("/heartbeat")',
            '@router.post("/logout")',
        ):
            self.assertIn(route, source)
        self.assertNotIn('@router.post("/operators")', source)
        self.assertIn('"operator_creation_available": False', source)
        self.assertIn('"shared_ops_login_accepted"', source)
        self.assertIn('"shared_ops_login_accepted": False', source)
        self.assertIn('"legacy_login_retired_in_staging": True', source)
        self.assertIn(
            '"non_staging_legacy_login_unchanged": True', source
        )
        self.assertIn("operator_auth_environment_mode()", source)
        self.assertIn("OPERATOR_AUTH_MODE_LEGACY", source)

    def test_raw_password_and_token_are_not_persisted(self):
        service = (
            ROOT / "rtm_core" / "operator_auth_service.py"
        ).read_text(encoding="utf-8")
        repository = (
            ROOT / "rtm_core" / "operator_auth_repository.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("raw_password", service)
        self.assertIn("generate_session_token()", service)
        self.assertIn("hash_session_token(raw_token)", repository)
        self.assertNotIn("raw_token TEXT", repository)

    def test_device_registration_is_concurrency_safe(self):
        source = (
            ROOT
            / "rtm_core"
            / "operator_access_runtime_repository.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "ON CONFLICT (operator_id, device_key_sha256) DO NOTHING",
            source,
        )

    def test_access_history_and_sensitive_evidence_are_separate(self):
        source = (
            ROOT
            / "rtm_core"
            / "operator_access_runtime_repository.py"
        ).read_text(encoding="utf-8")
        self.assertIn("rtm_operator_access_events", source)
        self.assertIn("rtm_operator_access_evidence", source)
        self.assertIn("ip_masked", source)
        self.assertIn("CAST(:ip_address AS INET)", source)

    def test_smoke_is_transactional_and_synthetic(self):
        source = (
            ROOT / "scripts" / "rtm_operator_auth_routes_smoke.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"synthetic_only": True', source)
        self.assertIn('"transactional": True', source)
        self.assertIn("transaction.rollback()", source)
        self.assertIn("database_rolled_back", source)
        self.assertNotIn(":true", source)
        self.assertNotIn(":false", source)

    def test_smoke_uses_http_only_device_cookie_without_json_secret(self):
        source = (
            ROOT / "scripts" / "rtm_operator_auth_routes_smoke.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'login.cookies.get("rtm_presenter_device")',
            source,
        )
        self.assertIn(
            'client.cookies.get("rtm_presenter_device")',
            source,
        )
        self.assertIn('"device_token" not in body', source)
        self.assertIn('"device_token" not in second_body', source)
        self.assertNotIn('body.get("device_token")', source)
        self.assertIn('base_url="https://rtm-staging.test"', source)

    def test_route_payloads_are_bounded(self):
        source = (
            ROOT / "rtm_core" / "operator_auth_router.py"
        ).read_text(encoding="utf-8")
        self.assertIn("email: str = Field(min_length=3, max_length=320)", source)
        self.assertIn(
            "password: str = Field(min_length=1, max_length=256, repr=False)",
            source,
        )

    def test_private_session_routes_require_device_cookie_or_header(self):
        source = (
            ROOT / "rtm_core" / "operator_auth_router.py"
        ).read_text(encoding="utf-8")
        self.assertIn('alias="X-RTM-Device"', source)
        self.assertIn('alias=_DEVICE_COOKIE', source)
        self.assertIn("normalize_device_token(candidate)", source)
        self.assertIn("hash_device_secret(normalized)", source)
        self.assertGreaterEqual(
            source.count("load_operator_session_with_device_possession("),
            5,
        )

    def test_auth_validation_errors_are_generic_and_no_store(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("@app.exception_handler(RequestValidationError)", source)
        self.assertIn("_has_sensitive_operator_validation_input", source)
        self.assertIn('normalized.startswith("/ops/auth/")', source)
        self.assertIn('content={"detail": "Solicitud no válida"}', source)
        self.assertIn("request_validation_exception_handler", source)

    def test_route_smoke_reuses_full_staging_safety_barriers(self):
        source = (
            ROOT / "scripts" / "rtm_operator_auth_routes_smoke.py"
        ).read_text(encoding="utf-8")
        for blocker in (
            "RTM_ENV_must_be_staging",
            "RTM_DATA_NAMESPACE_must_identify_staging",
            "RTM_SIDE_EFFECT_POLICY_must_be_isolated",
            "RTM_ALLOW_REAL_CUSTOMER_DATA_must_be_false",
        ):
            self.assertIn(blocker, source)

    def test_route_smoke_exercises_registered_bridge_and_case_scope(self):
        source = (
            ROOT / "scripts" / "rtm_operator_auth_routes_smoke.py"
        ).read_text(encoding="utf-8")
        for fragment in (
            "import app as backend_app",
            "app = backend_app.app",
            "bridge.legacy_ops_individual_session_bridge",
            "_SharedTransactionEngine",
            "module.get_engine = lambda: shared_engine",
            "module.get_engine = original_get_engine",
            "app.dependency_overrides[",
            "app.dependency_overrides.pop(",
            '"bridge_middleware_registered"',
            '"real_app_handlers_wired"',
            '"bridge_legacy_login_retired"',
            '"bridge_shared_token_rejected"',
            '"bridge_assigned_case_allowed"',
            '"bridge_unassigned_case_hidden"',
            '"bridge_non_operational_role_rejected"',
            '"real_queue_is_assignment_scoped"',
            '"real_case_events_loaded"',
            '"real_payment_status_loaded"',
            "INSERT INTO rtm_work_assignments",
            "INSERT INTO rtm_connect_a1s_tenants",
            "INSERT INTO rtm_connect_a1s_memberships",
            "INSERT INTO rtm_connect_a1s_case_bindings",
            "rtm_operator_auth_routes_smoke_probe",
        ):
            self.assertIn(fragment, source)
        self.assertNotIn("FastAPI()", source)
        self.assertNotIn("synthetic_scoped_case", source)
        self.assertIn('"rtm_operator_auth_routes_smoke_v1_4"', source)

    def test_route_preflight_never_certifies_outside_staging(self):
        source = (
            ROOT / "scripts" / "rtm_operator_auth_routes_preflight.py"
        ).read_text(encoding="utf-8")
        self.assertIn('report["environment"] != "staging"', source)
        self.assertIn("RTM_ENV_must_be_staging", source)

    def test_route_preflight_requires_complete_case_scope_schema(self):
        source = (
            ROOT / "scripts" / "rtm_operator_auth_routes_preflight.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        assignment = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "required"
                for target in node.targets
            )
        )
        required = ast.literal_eval(assignment.value)
        expected = {
            "cases": {"id", "test_mode"},
            "rtm_work_assignments": {
                "id", "case_id", "attention_item_id", "operator_id",
                "assignment_role", "status", "accepted_at", "released_at",
                "metadata",
            },
            "rtm_connect_a1s_tenants": {
                "id", "status", "synthetic_only", "metadata",
            },
            "rtm_connect_a1s_memberships": {
                "id", "tenant_id", "operator_id", "status",
                "synthetic_only", "revoked_at", "metadata",
            },
            "rtm_connect_a1s_case_bindings": {
                "id", "tenant_id", "case_id", "status",
                "synthetic_only", "revoked_at", "metadata",
            },
        }
        for table, columns in expected.items():
            with self.subTest(table=table):
                self.assertIn(table, required)
                self.assertTrue(columns.issubset(required[table]))

    def test_route_preflight_executes_scope_probe_read_only(self):
        source = (
            ROOT / "scripts" / "rtm_operator_auth_routes_preflight.py"
        ).read_text(encoding="utf-8")
        for fragment in (
            "OPS_CASE_SCOPE_SQL",
            'text_factory("SET TRANSACTION READ ONLY")',
            '"rtm_ops_scope_all": False',
            '"case_scope_sql_executable"',
            "operator_auth_case_scope_sql_not_executable",
        ):
            self.assertIn(fragment, source)
        self.assertIn('"rtm_operator_auth_routes_preflight_v1_3"', source)

    def test_route_preflight_requires_legacy_ops_session_bridge(self):
        source = (
            ROOT / "scripts" / "rtm_operator_auth_routes_preflight.py"
        ).read_text(encoding="utf-8")
        self.assertIn("legacy_ops_session_bridge_ready", source)
        self.assertIn('is_legacy_ops_path("/ai/expediente/run")', source)
        self.assertIn(
            'legacy_ops_requires_supervisor("/ops/automation/tick")',
            source,
        )
        self.assertIn("app.user_middleware", source)
        self.assertIn("is expected_dispatch", source)
        self.assertIn(
            '"legacy_ops_session_bridge_registered"',
            source,
        )
        self.assertIn(
            "bridge_registered\n            and is_legacy_ops_path",
            source,
        )
        self.assertIn('"shared_ops_login_accepted": False', source)


if __name__ == "__main__":
    unittest.main()
