from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rtm_staging_presenter_training_fixture.py"
OPERATOR_ID = "30000000-0000-4000-8000-000000000003"
GRANTOR_ID = "10000000-0000-4000-8000-000000000001"


def _import_script():
    sys.path.insert(0, str(ROOT))
    try:
        from scripts import rtm_staging_presenter_training_fixture as script
    finally:
        sys.path.pop(0)
    return script


def _safe_env() -> dict[str, str]:
    return {
        "RTM_ENV": "staging",
        "RTM_DATA_NAMESPACE": "rtm_staging",
        "RTM_SIDE_EFFECT_POLICY": "isolated",
        "RTM_ALLOW_REAL_CUSTOMER_DATA": "false",
        "RTM_PRESENTER_SYNTHETIC_ONLY": "true",
        "RTM_PRESENTER_EXTERNAL_EFFECTS_ALLOWED": "false",
        "RTM_PRESENTER_DIRECT_STORAGE_ALLOWED": "false",
        "RTM_ENABLE_OPERATOR_AUTH_V1": "true",
        "RTM_ENABLE_OPERATOR_ADMIN_V1": "true",
        "RTM_ENABLE_OPERATOR_LIFECYCLE_V1": "true",
        "RTM_ENABLE_PRESENTER_MVP": "true",
        "RTM_PRESENTER_MANAGED_EXTENSION_ATTESTATION_ENABLED": "false",
        "RTM_ENABLE_EXTERNAL_SUBMISSION": "false",
        "DATABASE_URL": "postgresql://rtm_staging_role@db/rtm_staging",
    }


def _plan(script):
    return script.build_training_plan(
        operator_id=OPERATOR_ID,
        grantor_operator_id=GRANTOR_ID,
    )


def _ready_state(plan):
    tenant = {
        **plan["tenant"],
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
    }
    case = {
        **plan["case"],
        "authorized": False,
        "channel": "direct",
        "override_deadlines": False,
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
    }
    documents = [
        {**row, "created_at": "2026-09-01T00:00:00Z"}
        for row in plan["documents"]
    ]
    versions = [
        {**row, "created_at": "2026-09-01T00:00:00Z"}
        for row in plan["document_versions"]
    ]
    binding = {
        **plan["binding"],
        "revoked_by_operator_id": None,
        "revoked_at": None,
        "bound_at": "2026-09-01T00:00:00Z",
    }
    memberships = [
        {
            **row,
            "revoked_by_operator_id": None,
            "revoked_at": None,
            "granted_at": "2026-09-01T00:00:00Z",
        }
        for row in plan["memberships"]
    ]
    assignment = {
        **plan["assignment"],
        "accepted_at": "2026-09-01T00:00:00Z",
        "released_at": None,
        "assigned_at": "2026-09-01T00:00:00Z",
    }
    return {
        "tenant_rows": [tenant],
        "case_rows": [case],
        "document_rows": documents,
        "document_version_rows": versions,
        "binding_rows": [binding],
        "membership_rows": memberships,
        "assignment_rows": [assignment],
    }


class StagingPresenterTrainingFixtureTest(unittest.TestCase):
    def test_default_is_read_only_and_apply_requires_exact_confirmation(self):
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

    def test_auth_lifecycle_and_isolated_staging_flags_fail_closed(self):
        script = _import_script()
        cases = (
            ("RTM_ENV", "production", "RTM_ENV_must_be_staging"),
            (
                "RTM_ENABLE_OPERATOR_AUTH_V1",
                "false",
                "RTM_ENABLE_OPERATOR_AUTH_V1_must_be_true",
            ),
            (
                "RTM_ENABLE_OPERATOR_ADMIN_V1",
                "false",
                "RTM_ENABLE_OPERATOR_ADMIN_V1_must_be_true",
            ),
            (
                "RTM_ENABLE_OPERATOR_LIFECYCLE_V1",
                "false",
                "RTM_ENABLE_OPERATOR_LIFECYCLE_V1_must_be_true",
            ),
            (
                "RTM_ENABLE_EXTERNAL_SUBMISSION",
                "true",
                "RTM_ENABLE_EXTERNAL_SUBMISSION_must_be_false",
            ),
            (
                "RTM_ALLOW_REAL_CUSTOMER_DATA",
                "true",
                "RTM_ALLOW_REAL_CUSTOMER_DATA_must_be_false",
            ),
        )
        for name, value, blocker in cases:
            with self.subTest(name=name):
                env = _safe_env()
                env[name] = value
                self.assertIn(
                    blocker,
                    script.safety_blockers(script._parser().parse_args([]), env),
                )

    def test_rejected_apply_stops_before_database_configuration(self):
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
        self.assertFalse(payload["database_configuration_loaded"])
        self.assertFalse(payload["database_connection_used"])
        self.assertIn("invalid_apply_confirmation", payload["blockers"])

    def test_plan_is_fixed_to_operator_02_and_a_separate_training_case(self):
        script = _import_script()
        plan = _plan(script)
        source_namespace = uuid.UUID("f4ca2e9a-8820-5b45-b74c-42a17fbc78e8")
        original_case_id = str(
            uuid.uuid5(
                source_namespace,
                f"{script.SOURCE_FIXTURE_KEY}:case",
            )
        )
        original_tenant_id = str(
            uuid.uuid5(
                source_namespace,
                f"{script.SOURCE_FIXTURE_KEY}:tenant",
            )
        )
        self.assertEqual(
            script.TARGET_OPERATOR_EMAIL,
            "rtm-staging-operador-02@example.com",
        )
        self.assertNotEqual(plan["case"]["id"], original_case_id)
        self.assertTrue(plan["case"]["test_mode"])
        self.assertNotEqual(plan["tenant"]["id"], original_tenant_id)
        self.assertEqual(
            {row["operator_id"] for row in plan["memberships"]},
            {OPERATOR_ID, GRANTOR_ID},
        )
        self.assertEqual(plan["assignment"]["operator_id"], OPERATOR_ID)
        self.assertEqual(plan["assignment"]["assignment_role"], "responsible")
        self.assertEqual(plan["binding"]["tenant_id"], plan["tenant"]["id"])
        self.assertEqual(len(plan["documents"]), 2)
        self.assertEqual(len(plan["document_versions"]), 2)

        serialized = json.dumps(plan, sort_keys=True).lower()
        for forbidden in (
            "password",
            "access_token",
            "refresh_token",
            "presigned_url",
        ):
            self.assertNotIn(forbidden, serialized)
        for document in plan["documents"]:
            self.assertIsNone(document["b2_bucket"])
            self.assertIsNone(document["b2_key"])
            self.assertRegex(document["sha256"], r"^[0-9a-f]{64}$")

    def test_reconciliation_is_idempotent_and_rejects_cross_scope_collisions(self):
        script = _import_script()
        plan = _plan(script)
        empty = script.reconcile_fixture_state(
            plan=plan,
            state={
                "tenant_rows": [],
                "case_rows": [],
                "document_rows": [],
                "document_version_rows": [],
                "binding_rows": [],
                "membership_rows": [],
                "assignment_rows": [],
            },
        )
        self.assertFalse(empty["ready"])
        self.assertEqual(empty["would_insert_tenants"], 1)
        self.assertEqual(empty["would_insert_cases"], 1)
        self.assertEqual(empty["would_insert_documents"], 2)
        self.assertEqual(empty["would_insert_document_versions"], 2)
        self.assertEqual(empty["would_insert_case_bindings"], 1)
        self.assertEqual(empty["would_insert_memberships"], 2)
        self.assertEqual(empty["would_insert_work_assignments"], 1)

        state = _ready_state(plan)
        ready = script.reconcile_fixture_state(plan=plan, state=state)
        self.assertTrue(ready["ready"])
        self.assertEqual(
            sum(value for key, value in ready.items() if key.startswith("would_insert_")),
            0,
        )

        real_data_collision = copy.deepcopy(state)
        real_data_collision["case_rows"][0]["contact_email"] = "real@example.org"
        with self.assertRaisesRegex(
            script.PresenterTrainingFixtureError,
            "training_case_contains_unexpected_data",
        ):
            script.reconcile_fixture_state(
                plan=plan,
                state=real_data_collision,
            )

        assignment_collision = copy.deepcopy(state)
        assignment_collision["assignment_rows"].append(
            {
                **state["assignment_rows"][0],
                "id": str(uuid.uuid4()),
                "operator_id": str(uuid.uuid4()),
            }
        )
        with self.assertRaisesRegex(
            script.PresenterTrainingFixtureError,
            "training_assignment_not_unique",
        ):
            script.reconcile_fixture_state(
                plan=plan,
                state=assignment_collision,
            )

    def test_source_contains_only_insert_mutations_and_no_external_clients(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(
            len(re.findall(r"INSERT INTO (?:cases|documents|rtm_)", source, re.I)),
            7,
        )
        self.assertEqual(source.upper().count("ON CONFLICT"), 7)
        self.assertNotRegex(source, r"(?i)\bUPDATE\s+(?:cases|documents|rtm_)")
        self.assertNotRegex(source, r"(?i)\bDELETE\s+FROM\s+(?:cases|documents|rtm_)")
        self.assertNotRegex(source, r"(?i)\bINSERT\s+INTO\s+rtm_operators")
        for forbidden in (
            "password_hash",
            "requests.",
            "httpx.",
            "urllib.",
            "b2_storage",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
