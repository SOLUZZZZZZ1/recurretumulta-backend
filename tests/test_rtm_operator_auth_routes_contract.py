from __future__ import annotations

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
        self.assertIn('path.startswith("/ops/auth/")', source)
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

    def test_route_preflight_never_certifies_outside_staging(self):
        source = (
            ROOT / "scripts" / "rtm_operator_auth_routes_preflight.py"
        ).read_text(encoding="utf-8")
        self.assertIn('report["environment"] != "staging"', source)
        self.assertIn("RTM_ENV_must_be_staging", source)


if __name__ == "__main__":
    unittest.main()
