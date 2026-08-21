from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "rtm_core" / "operator_lifecycle_policy.py"
REPOSITORY = ROOT / "rtm_core" / "operator_lifecycle_repository.py"
ROUTER = ROOT / "rtm_core" / "operator_lifecycle_router.py"
PREFLIGHT = ROOT / "scripts" / "rtm_operator_lifecycle_preflight.py"
SMOKE = ROOT / "scripts" / "rtm_operator_lifecycle_smoke.py"


class OperatorLifecycleContractTest(unittest.TestCase):
    def test_app_wires_lifecycle_after_admin(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn(
            "from rtm_core.operator_lifecycle_router import",
            source,
        )
        self.assertIn(
            "app.include_router(rtm_operator_lifecycle_router)",
            source,
        )
        self.assertLess(
            source.index("app.include_router(rtm_operator_admin_router)"),
            source.index(
                "app.include_router(rtm_operator_lifecycle_router)"
            ),
        )

    def test_lifecycle_has_independent_staging_gate(self):
        source = POLICY.read_text(encoding="utf-8")
        self.assertIn("RTM_ENABLE_OPERATOR_LIFECYCLE_V1", source)
        self.assertIn('environment != "staging"', source)
        self.assertIn(
            "El ciclo de vida requiere el panel supervisor activo",
            source,
        )

    def test_status_declares_safety_contract(self):
        source = ROUTER.read_text(encoding="utf-8")
        for declaration in (
            '"synthetic_only": True',
            '"public_registration_available": False',
            '"direct_supervisor_creation_available": False',
            '"passwords_returned": False',
            '"legacy_login_unchanged": True',
        ):
            self.assertIn(declaration, source)

    def test_router_exposes_exact_controlled_routes(self):
        source = ROUTER.read_text(encoding="utf-8")
        for route in (
            '"/ops/admin/lifecycle/status"',
            '"/ops/admin/operators"',
            '"/ops/admin/operators/{operator_id}/suspend"',
            '"/ops/admin/operators/{operator_id}/reactivate"',
            '"/ops/admin/operators/{operator_id}/role"',
            '"/ops/admin/operators/{operator_id}/credentials/rotate"',
            '"/ops/admin/operators/{operator_id}/sessions/revoke-all"',
            '"/ops/auth/password/change"',
        ):
            self.assertIn(route, source)
        self.assertNotIn('"/register"', source)

    def test_password_fields_are_bounded_and_hidden_from_repr(self):
        source = ROUTER.read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("repr=False"), 3)
        self.assertIn("min_length=12", source)
        self.assertIn("max_length=256", source)

    def test_controlled_creation_is_synthetic_operator_only(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn("normalize_synthetic_operator_email(email)", source)
        self.assertIn('_role_row(conn, "rtm.operator")', source)
        self.assertIn('"synthetic": True', source)
        self.assertIn('"environment": "staging"', source)

    def test_direct_supervisor_creation_is_not_available(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        create_start = source.index("def create_controlled_synthetic_operator")
        create_end = source.index("def suspend_operator")
        create_source = source[create_start:create_end]
        self.assertNotIn('"rtm.supervisor"', create_source)
        self.assertIn('"rtm.operator"', create_source)

    def test_allowed_roles_are_explicit_and_closed(self):
        source = POLICY.read_text(encoding="utf-8")
        self.assertIn(
            'ALLOWED_ROLE_CODES = ("rtm.operator", "rtm.supervisor")',
            source,
        )
        repository = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn("if code not in ALLOWED_ROLE_CODES", repository)

    def test_lifecycle_responses_never_return_passwords(self):
        source = ROUTER.read_text(encoding="utf-8")
        self.assertIn('"temporary_password_returned": False', source)
        self.assertIn('"password_returned": False', source)
        self.assertNotIn('"temporary_password": payload.temporary_password', source)
        self.assertNotIn('"new_password": payload.new_password', source)

    def test_repository_hashes_with_argon2id_helper(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn("hash_operator_password(password)", source)
        self.assertIn("password_algorithm='argon2id'", source)
        self.assertNotIn("password_hash=:new_password", source)

    def test_password_reuse_is_blocked(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn("OperatorPasswordReuseError", source)
        self.assertGreaterEqual(
            source.count("verify_operator_password("),
            3,
        )

    def test_self_password_change_verifies_current_password(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        start = source.index("def change_own_password")
        section = source[start:]
        self.assertIn("current_password", section)
        self.assertIn("OperatorCurrentPasswordInvalid", section)
        self.assertIn("must_change_password=FALSE", section)

    def test_sensitive_mutations_increment_auth_epoch(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertGreaterEqual(
            source.count("auth_epoch=auth_epoch+1"),
            6,
        )

    def test_sensitive_mutations_revoke_active_sessions(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn("def _revoke_active_sessions", source)
        self.assertIn("status='revoked'", source)
        self.assertIn("WHERE operator_id=CAST(:operator_id AS UUID)", source)
        self.assertIn("AND status='active'", source)

    def test_suspend_and_reactivate_have_explicit_state_machine(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn("Solo puede suspenderse un operador activo", source)
        self.assertIn("Solo puede reactivarse un operador suspendido", source)
        self.assertIn("status='suspended'", source)
        self.assertIn("status='active'", source)

    def test_last_supervisor_is_protected(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn("def _count_active_supervisors", source)
        self.assertIn("último supervisor activo", source)

    def test_supervisor_self_protections_are_explicit(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertGreaterEqual(
            source.count("operator_id == actor_operator_id"),
            4,
        )
        self.assertIn("OperatorLifecycleSelfProtectionError", source)

    def test_lifecycle_is_soft_and_never_deletes(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        for forbidden in (
            "DELETE FROM rtm_operators",
            "DELETE FROM rtm_operator_sessions",
            "DROP TABLE",
            "TRUNCATE",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("status='suspended'", source)
        self.assertIn("status='revoked'", source)

    def test_privileged_access_requires_changed_password(self):
        source = ROUTER.read_text(encoding="utf-8")
        self.assertIn("session.must_change_password", source)
        self.assertIn(
            "Debe cambiar la contraseña temporal",
            source,
        )
        repository = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn(
            "El operador debe cambiar primero su contraseña temporal",
            repository,
        )

    def test_all_lifecycle_actions_are_audited(self):
        source = ROUTER.read_text(encoding="utf-8")
        for event_type in (
            "admin.operator_created",
            "admin.operator_suspended",
            "admin.operator_reactivated",
            "admin.operator_role_changed",
            "admin.operator_password_rotated",
            "admin.operator_sessions_revoked",
            "auth.password_changed",
        ):
            self.assertIn(event_type, source)
        self.assertIn("record_operator_access_event", source)

    def test_preflight_is_read_only_and_checks_legacy(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        self.assertIn('"read_only": True', source)
        self.assertIn('"schema_changes_required": False', source)
        self.assertIn('"/ops/login" in paths', source)
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
        self.assertIn('"passwords_returned": False', source)

    def test_smoke_reuses_full_staging_barriers_and_checks_secrets(self):
        source = SMOKE.read_text(encoding="utf-8")
        for blocker in (
            "RTM_ENV_must_be_staging",
            "RTM_DATA_NAMESPACE_must_identify_staging",
            "RTM_SIDE_EFFECT_POLICY_must_be_isolated",
            "RTM_ALLOW_REAL_CUSTOMER_DATA_must_be_false",
        ):
            self.assertIn(blocker, source)
        self.assertIn("creation_response_has_no_password", source)
        self.assertIn("rotation_response_has_no_password", source)


if __name__ == "__main__":
    unittest.main()
