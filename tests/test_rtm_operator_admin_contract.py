from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "rtm_core" / "operator_admin_policy.py"
REPOSITORY = ROOT / "rtm_core" / "operator_admin_repository.py"
ROUTER = ROOT / "rtm_core" / "operator_admin_router.py"
PREFLIGHT = ROOT / "scripts" / "rtm_operator_admin_preflight.py"
SMOKE = ROOT / "scripts" / "rtm_operator_admin_smoke.py"


class OperatorAdminContractTest(unittest.TestCase):
    def test_app_wires_admin_router_after_auth_router(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn(
            "from rtm_core.operator_admin_router import",
            source,
        )
        self.assertIn(
            "app.include_router(rtm_operator_admin_router)",
            source,
        )
        self.assertLess(
            source.index("app.include_router(rtm_operator_auth_router)"),
            source.index("app.include_router(rtm_operator_admin_router)"),
        )

    def test_admin_has_independent_staging_feature_gate(self):
        source = POLICY.read_text(encoding="utf-8")
        self.assertIn("RTM_ENABLE_OPERATOR_ADMIN_V1", source)
        self.assertIn('environment != "staging"', source)
        self.assertIn(
            "La administración requiere autenticación individual activa",
            source,
        )

    def test_supervisor_permission_is_explicit(self):
        policy = POLICY.read_text(encoding="utf-8")
        router = ROUTER.read_text(encoding="utf-8")
        self.assertIn('SUPERVISOR_PERMISSION = "ops.supervise"', policy)
        self.assertIn("session_has_supervisor_permission", router)
        self.assertIn("Permiso de supervisor requerido", router)

    def test_admin_requires_bearer_and_device_possession(self):
        source = ROUTER.read_text(encoding="utf-8")
        self.assertIn(
            "load_operator_session_with_device_possession(",
            source,
        )
        self.assertIn('alias="X-RTM-Device"', source)
        self.assertIn('alias="__Host-rtm_presenter_device"', source)
        self.assertNotIn(
            "session = load_operator_session(\n",
            source,
        )
        self.assertIn("session.must_change_password", source)
        self.assertIn("session.mfa_required", source)
        self.assertGreaterEqual(source.count("field(repr=False)"), 2)

    def test_router_exposes_observability_and_revocation_only(self):
        source = ROUTER.read_text(encoding="utf-8")
        for route in (
            '@router.get("/status")',
            '@router.get("/operators")',
            '@router.get("/operators/{operator_id}")',
            '@router.get("/operators/{operator_id}/sessions")',
            '@router.get("/operators/{operator_id}/devices")',
            '@router.get("/operators/{operator_id}/access-events")',
            '@router.post("/sessions/{session_id}/revoke")',
            '@router.post("/devices/{device_id}/revoke")',
        ):
            self.assertIn(route, source)
        for forbidden in (
            '@router.post("/operators")',
            "password/reset",
            "rotate-password",
            "roles/update",
        ):
            self.assertNotIn(forbidden, source)

    def test_read_models_never_select_sensitive_secrets(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertNotIn("token_sha256", source)
        self.assertNotIn("device_key_sha256", source)
        self.assertNotIn("rtm_operator_access_evidence", source)
        self.assertIn("ip_address AS ip_masked", source)

    def test_access_events_are_sanitized(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn("a.ip_masked", source)
        self.assertNotIn("a.login_identifier_sha256", source)
        self.assertNotIn("a.device_key_sha256", source)
        self.assertNotIn("raw_user_agent", source)

    def test_self_session_and_device_revocation_are_blocked(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn("session_id == actor_session_id", source)
        self.assertIn("str(actor_device) == device_id", source)
        self.assertIn("OperatorAdminSelfProtectionError", source)

    def test_mutations_are_soft_revocations_not_deletes(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn("status='revoked'", source)
        self.assertNotIn("DELETE FROM rtm_operator_sessions", source)
        self.assertNotIn("DELETE FROM rtm_operator_devices", source)
        self.assertNotIn("DROP TABLE", source)
        self.assertNotIn("TRUNCATE", source)

    def test_admin_actions_write_append_only_access_events(self):
        source = ROUTER.read_text(encoding="utf-8")
        self.assertIn("record_operator_access_event", source)
        self.assertIn('"admin.session_revoked"', source)
        self.assertIn('"admin.device_revoked"', source)
        self.assertIn('"supervisor_action"', source)

    def test_admin_mutations_require_recent_persisted_reauthentication(self):
        source = ROUTER.read_text(encoding="utf-8")
        self.assertIn("has_recent_reauthentication", source)
        self.assertEqual(
            source.count("Depends(require_recent_supervisor_context)"),
            2,
        )

    def test_payload_and_pagination_are_bounded(self):
        source = ROUTER.read_text(encoding="utf-8")
        self.assertIn(
            "reason: str = Field(min_length=3, max_length=240)",
            source,
        )
        self.assertIn("le=100", source)
        self.assertIn("le=200", source)
        self.assertIn("le=10000", source)

    def test_preflight_is_read_only_and_checks_legacy(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        self.assertIn('"read_only": True', source)
        self.assertIn('"/ops/login" in paths', source)
        self.assertIn('"raw_evidence_available": False', source)
        self.assertIn('"shared_ops_login_accepted": False', source)
        self.assertIn('"legacy_login_retired_in_staging": True', source)
        self.assertIn(
            '"non_staging_legacy_login_unchanged": True', source
        )
        self.assertNotIn("--apply", source)

    def test_preflight_refuses_outside_staging_before_database_access(self):
        env = dict(os.environ)
        env["RTM_ENV"] = "production"
        process = subprocess.run(
            [sys.executable, str(PREFLIGHT), "--compact"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(process.returncode, 2)
        payload = json.loads(process.stdout)
        self.assertIn("RTM_ENV_must_be_staging", payload["blockers"])
        self.assertNotIn("ModuleNotFoundError", process.stderr)

    def test_smoke_is_synthetic_transactional_and_rolls_back(self):
        source = SMOKE.read_text(encoding="utf-8")
        self.assertIn('"synthetic_only": True', source)
        self.assertIn('"transactional": True', source)
        self.assertIn("transaction.rollback()", source)
        self.assertIn('"database_rolled_back"', source)
        self.assertIn("operator_creation_public", source)

    def test_smoke_reuses_full_staging_barriers(self):
        source = SMOKE.read_text(encoding="utf-8")
        for blocker in (
            "RTM_ENV_must_be_staging",
            "RTM_DATA_NAMESPACE_must_identify_staging",
            "RTM_SIDE_EFFECT_POLICY_must_be_isolated",
            "RTM_ALLOW_REAL_CUSTOMER_DATA_must_be_false",
        ):
            self.assertIn(blocker, source)
        self.assertIn("supervisor_without_device_denied", source)
        self.assertIn("no_device.status_code == 401", source)
        self.assertIn("operators.status_code == 200", source)


if __name__ == "__main__":
    unittest.main()
