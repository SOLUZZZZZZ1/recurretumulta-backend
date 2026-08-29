from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rtm_staging_presenter_case_access.py"
CASE_ID = "105f73f3-bb04-5553-aa00-76bc47e0b4bc"
OPERATOR_ID = "576d0d8a-fe87-4b16-8187-22e24e85400d"
GRANTOR_ID = "10000000-0000-4000-8000-000000000001"
TENANT_ID = "20000000-0000-4000-8000-000000000002"


def _import_script():
    sys.path.insert(0, str(ROOT))
    try:
        from scripts import rtm_staging_presenter_case_access as script
    finally:
        sys.path.pop(0)
    return script


def _safe_env() -> dict[str, str]:
    return {
        "RTM_ENV": "staging",
        "RTM_DATA_NAMESPACE": "rtm_staging",
        "RTM_SIDE_EFFECT_POLICY": "isolated",
        "RTM_ENABLE_OPERATOR_AUTH_V1": "true",
        "RTM_ENABLE_PRESENTER_MVP": "true",
        "RTM_PRESENTER_SYNTHETIC_ONLY": "true",
        "RTM_ALLOW_REAL_CUSTOMER_DATA": "false",
        "RTM_PRESENTER_EXTERNAL_EFFECTS_ALLOWED": "false",
        "RTM_PRESENTER_DIRECT_STORAGE_ALLOWED": "false",
        "RTM_PRESENTER_MANAGED_EXTENSION_ATTESTATION_ENABLED": "false",
        "RTM_ENABLE_EXTERNAL_SUBMISSION": "false",
    }


def _ready_plan(script):
    membership = {
        "id": script._stable_uuid(
            f"{script.DEFAULT_FIXTURE_KEY}:presenter-membership:{OPERATOR_ID}:v1"
        ),
        "tenant_id": TENANT_ID,
        "principal_id": script._stable_uuid(
            f"{script.DEFAULT_FIXTURE_KEY}:presenter-principal:{OPERATOR_ID}:v1"
        ),
        "operator_id": OPERATOR_ID,
        "role": script.MEMBERSHIP_ROLE,
        "status": "active",
        "synthetic_only": True,
        "granted_by_operator_id": GRANTOR_ID,
        "revoked_by_operator_id": None,
        "revoked_at": None,
        "metadata": script.expected_membership_metadata(
            fixture_key=script.DEFAULT_FIXTURE_KEY,
            case_id=CASE_ID,
        ),
    }
    assignment = {
        "id": script._stable_uuid(
            f"{script.DEFAULT_FIXTURE_KEY}:presenter-assignment:"
            f"{OPERATOR_ID}:{script.ASSIGNMENT_ROLE}:v1"
        ),
        "case_id": CASE_ID,
        "attention_item_id": None,
        "operator_id": OPERATOR_ID,
        "assignment_role": script.ASSIGNMENT_ROLE,
        "status": "active",
        "assigned_by": GRANTOR_ID,
        "accepted_at": "2026-08-29T08:00:00+00:00",
        "released_at": None,
        "metadata": script.expected_assignment_metadata(
            fixture_key=script.DEFAULT_FIXTURE_KEY
        ),
    }
    return {
        "expected_membership": {
            key: membership[key]
            for key in (
                "id",
                "tenant_id",
                "principal_id",
                "operator_id",
                "role",
                "status",
                "synthetic_only",
                "granted_by_operator_id",
                "metadata",
            )
        },
        "expected_assignment": {
            key: assignment[key]
            for key in (
                "id",
                "case_id",
                "operator_id",
                "assignment_role",
                "status",
                "assigned_by",
                "metadata",
            )
        },
        "membership": membership,
        "assignments": [assignment],
        "reviewer_slot": assignment,
    }


class StagingPresenterCaseAccessTest(unittest.TestCase):
    def test_default_is_read_only_and_apply_requires_literal_confirmation(self):
        script = _import_script()
        dry_run = script._parser().parse_args([])
        self.assertFalse(dry_run.apply)
        self.assertEqual(script.safety_blockers(dry_run, _safe_env()), [])

        apply = script._parser().parse_args(["--apply"])
        self.assertIn(
            "invalid_apply_confirmation",
            script.safety_blockers(apply, _safe_env()),
        )
        confirmed = script._parser().parse_args(
            ["--apply", "--confirmation", script.APPLY_CONFIRMATION]
        )
        self.assertEqual(script.safety_blockers(confirmed, _safe_env()), [])

    def test_scope_is_the_exact_operator_and_runtime_fixture(self):
        script = _import_script()
        other_email = script._parser().parse_args(
            ["--email", "rtm-staging-presenter-other@example.com"]
        )
        self.assertIn(
            "operator_email_must_match_presenter_fixture",
            script.safety_blockers(other_email, _safe_env()),
        )
        other_fixture = script._parser().parse_args(
            ["--fixture-key", "runtime-other-v1"]
        )
        self.assertIn(
            "fixture_key_must_match_presenter_fixture",
            script.safety_blockers(other_fixture, _safe_env()),
        )

        runtime_namespace = uuid.UUID("f4ca2e9a-8820-5b45-b74c-42a17fbc78e8")
        expected_case = uuid.uuid5(
            runtime_namespace,
            f"{script.DEFAULT_FIXTURE_KEY}:case",
        )
        self.assertEqual(str(expected_case), CASE_ID)
        self.assertEqual(
            script.DEFAULT_OPERATOR_EMAIL,
            "rtm-staging-presenter-ramon@example.com",
        )

    def test_real_data_external_effects_or_production_fail_closed(self):
        script = _import_script()
        for name, value, blocker in (
            ("RTM_ENV", "production", "RTM_ENV_must_be_staging"),
            (
                "RTM_ALLOW_REAL_CUSTOMER_DATA",
                "true",
                "RTM_ALLOW_REAL_CUSTOMER_DATA_must_be_false",
            ),
            (
                "RTM_PRESENTER_EXTERNAL_EFFECTS_ALLOWED",
                "true",
                "RTM_PRESENTER_EXTERNAL_EFFECTS_ALLOWED_must_be_false",
            ),
            (
                "RTM_PRESENTER_DIRECT_STORAGE_ALLOWED",
                "true",
                "RTM_PRESENTER_DIRECT_STORAGE_ALLOWED_must_be_false",
            ),
            (
                "RTM_ENABLE_EXTERNAL_SUBMISSION",
                "true",
                "RTM_ENABLE_EXTERNAL_SUBMISSION_must_be_false",
            ),
        ):
            with self.subTest(name=name):
                env = _safe_env()
                env[name] = value
                args = script._parser().parse_args([])
                self.assertIn(blocker, script.safety_blockers(args, env))

    def test_rejected_apply_stops_before_loading_database_modules(self):
        env = dict(os.environ)
        env.update(_safe_env())
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--apply", "--compact"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["safe"])
        self.assertIn("invalid_apply_confirmation", payload["blockers"])
        self.assertNotIn("error", payload)

    def test_membership_and_assignment_reconciliation_is_idempotent(self):
        script = _import_script()
        plan = _ready_plan(script)
        self.assertTrue(script._membership_ready(plan))
        self.assertTrue(script._assignment_ready(plan))

        missing = dict(plan)
        missing["membership"] = None
        missing["assignments"] = []
        missing["reviewer_slot"] = None
        self.assertFalse(script._membership_ready(missing))
        self.assertFalse(script._assignment_ready(missing))

    def test_collisions_do_not_rewrite_existing_authority(self):
        script = _import_script()
        plan = _ready_plan(script)
        wrong_membership = dict(plan["membership"])
        wrong_membership["role"] = "supervisor"
        plan["membership"] = wrong_membership
        with self.assertRaisesRegex(
            script.PresenterCaseAccessError,
            "presenter_membership_collision",
        ):
            script._membership_ready(plan)

        plan = _ready_plan(script)
        plan["assignments"] = []
        plan["reviewer_slot"] = {"operator_id": str(uuid.uuid4())}
        with self.assertRaisesRegex(
            script.PresenterCaseAccessError,
            "presenter_reviewer_slot_in_use",
        ):
            script._assignment_ready(plan)

    def test_markers_and_public_report_disclose_no_internal_coordinates(self):
        script = _import_script()
        membership = script.expected_membership_metadata(
            fixture_key=script.DEFAULT_FIXTURE_KEY,
            case_id=CASE_ID,
        )
        assignment = script.expected_assignment_metadata(
            fixture_key=script.DEFAULT_FIXTURE_KEY
        )
        self.assertEqual(membership["synthetic_marker"], script.A1S_MARKER)
        self.assertEqual(
            assignment["synthetic_marker"], script.PRESENTER_MARKER
        )
        for payload in (membership, assignment):
            self.assertTrue(payload["synthetic_only"])
            self.assertFalse(payload["real_data_used"])
            self.assertFalse(payload["external_effects_executed"])

        report = script._public_report(
            {
                "fixture_key": script.DEFAULT_FIXTURE_KEY,
                "case_id": CASE_ID,
                "email": script.DEFAULT_OPERATOR_EMAIL,
                "membership_ready": True,
                "assignment_ready": True,
                "case_access": True,
                "ready": True,
                "would_insert_memberships": 0,
                "would_insert_work_assignments": 0,
                "inserted_rows": 0,
            }
        )
        serialized = json.dumps(report, sort_keys=True).lower()
        for forbidden in (
            "tenant_id",
            "grantor_operator_id",
            "principal_id",
            "membership_id",
            "assignment_id",
            "bucket",
            "object_key",
            "presigned",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertFalse(report["document_bytes_read"])
        self.assertFalse(report["storage_coordinates_read"])
        self.assertFalse(report["operator_role_changed"])

    def test_mutation_surface_is_two_insert_only_statements(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            "ROOT = Path(__file__).resolve().parents[1]",
            source,
        )
        self.assertEqual(
            len(re.findall(r"INSERT INTO rtm_", source, flags=re.IGNORECASE)),
            2,
        )
        self.assertIn("INSERT INTO rtm_connect_a1s_memberships", source)
        self.assertIn("INSERT INTO rtm_work_assignments", source)
        self.assertEqual(source.upper().count("ON CONFLICT DO NOTHING"), 2)
        self.assertNotRegex(source, r"(?i)\bUPDATE\s+rtm_")
        self.assertNotRegex(source, r"(?i)\bDELETE\s+FROM\s+rtm_")
        self.assertNotRegex(
            source,
            r"(?i)\bINSERT\s+INTO\s+rtm_operator_roles",
        )
        for forbidden in (
            "b2_storage",
            "b2_bucket",
            "b2_key",
            "requests.",
            "httpx.",
            "urllib.",
            "password_hash",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
