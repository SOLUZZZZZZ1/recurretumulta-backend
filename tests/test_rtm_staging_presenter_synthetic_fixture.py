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
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rtm_staging_presenter_synthetic_fixture.py"


def _import_script():
    sys.path.insert(0, str(ROOT))
    try:
        from scripts import rtm_staging_presenter_synthetic_fixture as fixture
    finally:
        sys.path.pop(0)
    return fixture


def _safe_env() -> dict[str, str]:
    return {
        "RTM_ENV": "staging",
        "RTM_DATA_NAMESPACE": "rtm_staging",
        "RTM_SIDE_EFFECT_POLICY": "isolated",
        "RTM_ALLOW_REAL_CUSTOMER_DATA": "false",
        "RTM_PRESENTER_SYNTHETIC_ONLY": "true",
        "RTM_PRESENTER_EXTERNAL_EFFECTS_ALLOWED": "false",
        "RTM_PRESENTER_DIRECT_STORAGE_ALLOWED": "false",
        "DATABASE_URL": "postgresql://rtm_staging_role@db/rtm_staging",
    }


def _source_material(script):
    runtime_plan = script._expected_runtime_plan(script.DEFAULT_FIXTURE_KEY)
    creator_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "presenter-creator"))
    verifier_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "presenter-verifier"))
    marker = {
        "synthetic_marker": script.A1S_MARKER,
        "synthetic_only": True,
        "fixture_key": runtime_plan.fixture_key,
    }
    authority = {
        "case_id": runtime_plan.case_id,
        "case_test_mode": True,
        "tenant_id": runtime_plan.tenant_id,
        "tenant_status": "active",
        "tenant_synthetic_only": True,
        "tenant_metadata": marker,
        "binding_id": runtime_plan.case_binding_id,
        "binding_status": "active",
        "binding_synthetic_only": True,
        "binding_revoked_at": None,
        "binding_metadata": {**marker, "test_mode": True},
        "creator_membership_id": runtime_plan.requester_membership_id,
        "creator_membership_role": "supervisor",
        "creator_membership_status": "active",
        "creator_membership_synthetic_only": True,
        "creator_membership_revoked_at": None,
        "creator_membership_metadata": marker,
        "verifier_membership_id": runtime_plan.verifier_membership_id,
        "verifier_membership_role": "verifier",
        "verifier_membership_status": "active",
        "verifier_membership_synthetic_only": True,
        "verifier_membership_revoked_at": None,
        "verifier_membership_metadata": marker,
        "creator_operator_id": creator_id,
        "creator_operator_status": "active",
        "creator_operator_synthetic": True,
        "creator_operator_environment": "staging",
        "creator_operator_ready": True,
        "verifier_operator_id": verifier_id,
        "verifier_operator_status": "active",
        "verifier_operator_synthetic": True,
        "verifier_operator_environment": "staging",
        "verifier_operator_ready": True,
    }
    documents = [
        {
            **row,
            "b2_bucket": None,
            "b2_key": None,
        }
        for row in script._expected_source_documents(runtime_plan)
    ]
    return runtime_plan, authority, documents


def _seed_plan(script):
    _, authority, documents = _source_material(script)
    source = script.validate_source_context(
        fixture_key=script.DEFAULT_FIXTURE_KEY,
        authority=authority,
        documents=documents,
    )
    return script.build_seed_plan(source)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _ColumnConnection:
    def __init__(self, columns, indexes=("uq_rtm_assignment_case_role",)):
        self.columns = columns
        self.indexes = indexes

    def execute(self, statement, params=None):
        del params
        if "pg_indexes" in str(statement):
            return _Rows([{"indexname": value} for value in self.indexes])
        return _Rows([{"column_name": value} for value in self.columns])


class _InsertResult:
    rowcount = 1


class _InsertConnection:
    def __init__(self):
        self.profile_versions = []

    def execute(self, _statement, values):
        if "profile_code" in values:
            self.profile_versions.append(int(values["version_number"]))
        return _InsertResult()


class StagingPresenterSyntheticFixtureTest(unittest.TestCase):
    def test_default_is_dry_run_and_apply_requires_literal_confirmation(self):
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

    def test_apply_without_confirmation_stops_before_database_configuration(self):
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
        self.assertIn("invalid_apply_confirmation", payload["blockers"])
        self.assertFalse(payload["database_configuration_loaded"])
        self.assertFalse(payload["database_connection_used"])

    def test_production_is_rejected_before_database_access(self):
        env = dict(os.environ)
        env.update(_safe_env())
        env.update(
            {
                "RTM_ENV": "production",
                "RTM_DATA_NAMESPACE": "rtm_production",
                "RTM_SIDE_EFFECT_POLICY": "live",
                "RTM_ALLOW_REAL_CUSTOMER_DATA": "true",
            }
        )
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--compact"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stdout)
        self.assertIn("RTM_ENV_must_be_staging", payload["blockers"])
        self.assertFalse(payload["database_configuration_loaded"])

    def test_source_requires_exact_a1s_synthetic_case_and_hash_only_documents(self):
        script = _import_script()
        runtime_plan, authority, documents = _source_material(script)
        source = script.validate_source_context(
            fixture_key=runtime_plan.fixture_key,
            authority=authority,
            documents=documents,
        )
        self.assertEqual(source["case_id"], runtime_plan.case_id)
        self.assertEqual(len(source["documents"]), 2)

        variants = []
        non_test = copy.deepcopy(authority)
        non_test["case_test_mode"] = False
        variants.append((non_test, copy.deepcopy(documents)))
        same_operator = copy.deepcopy(authority)
        same_operator["verifier_operator_id"] = authority["creator_operator_id"]
        variants.append((same_operator, copy.deepcopy(documents)))
        real_operator = copy.deepcopy(authority)
        real_operator["creator_operator_synthetic"] = False
        variants.append((real_operator, copy.deepcopy(documents)))
        storage_backed = copy.deepcopy(documents)
        storage_backed[0]["b2_key"] = "forbidden/object"
        variants.append((copy.deepcopy(authority), storage_backed))
        invalid_hash = copy.deepcopy(documents)
        invalid_hash[0]["sha256"] = "not-a-sha256"
        variants.append((copy.deepcopy(authority), invalid_hash))
        extra_document = copy.deepcopy(documents)
        extra_document.append(copy.deepcopy(documents[0]))
        extra_document[-1]["id"] = str(uuid.uuid4())
        variants.append((copy.deepcopy(authority), extra_document))

        for candidate_authority, candidate_documents in variants:
            with self.subTest(candidate=candidate_authority):
                with self.assertRaises(script.PresenterFixtureError):
                    script.validate_source_context(
                        fixture_key=runtime_plan.fixture_key,
                        authority=candidate_authority,
                        documents=candidate_documents,
                    )

    def test_plan_references_existing_documents_and_uses_reserved_example_profile(self):
        script = _import_script()
        plan = _seed_plan(script)
        runtime_plan = script._expected_runtime_plan(script.DEFAULT_FIXTURE_KEY)
        self.assertEqual(
            {row["source_document_id"] for row in plan["document_versions"]},
            {
                runtime_plan.input_document_id,
                runtime_plan.receipt_document_id,
            },
        )
        def keys(value):
            if isinstance(value, dict):
                return set(value).union(*(keys(item) for item in value.values()))
            if isinstance(value, (list, tuple)):
                return set().union(*(keys(item) for item in value))
            return set()

        self.assertNotIn("bytes", keys(plan))
        profile = plan["destination_profile"]
        self.assertEqual(
            [row["version_number"] for row in plan["destination_profiles"]],
            [1, 2, 3, 4],
        )
        self.assertEqual(
            plan["destination_profiles"][1]["profile_sha256"],
            "91241e7ac237bd65eaf2ae0fdb77007e2766edb305c301f095d6a6e390ae981a",
        )
        self.assertEqual(profile["profile_code"], "synthetic.example")
        self.assertEqual(profile["portal_origin"], "https://synthetic.example")
        self.assertEqual(
            [field["step_order"] for field in profile["requirements"]["fields"]],
            [1, 2],
        )
        self.assertNotIn(
            "submission_receipt",
            {
                field["field_code"]
                for field in profile["requirements"]["fields"]
            },
        )
        self.assertEqual(
            profile["requirements"]["representation_modes"],
            ["self", "representative"],
        )
        self.assertEqual(
            profile["requirements"]["authorization_field_code"],
            "representation_authorization",
        )
        authorization_field = profile["requirements"]["fields"][1]
        self.assertEqual(
            authorization_field["field_code"], "representation_authorization"
        )
        self.assertEqual(
            authorization_field["required_for_modes"], ["representative"]
        )
        self.assertEqual(
            profile["requirements"]["portal_preparation"]["form_code"],
            "reg_general_v1",
        )
        self.assertEqual(
            [
                field["field_code"]
                for field in profile["requirements"]["portal_preparation"][
                    "fields"
                ]
            ],
            ["subject", "facts", "request"],
        )
        self.assertNotEqual(
            profile["created_by_operator_id"],
            profile["verified_by_operator_id"],
        )
        self.assertRegex(profile["profile_sha256"], r"^[0-9a-f]{64}$")

    def test_assignment_is_active_accepted_case_scope_for_presenter(self):
        script = _import_script()
        plan = _seed_plan(script)
        assignment = plan["work_assignment"]
        self.assertEqual(assignment["case_id"], plan["case_id"])
        self.assertEqual(assignment["status"], "active")
        self.assertIn(
            assignment["assignment_role"],
            {"responsible", "reviewer", "supervisor"},
        )
        self.assertEqual(
            assignment["operator_id"],
            plan["destination_profile"]["created_by_operator_id"],
        )
        self.assertTrue(assignment["metadata"]["synthetic_only"])

        empty = script.reconcile_fixture_state(
            plan=plan,
            document_rows=[],
            profile_rows=[],
            assignment_rows=[],
        )
        self.assertTrue(empty["assignment_missing"])
        self.assertFalse(empty["ready"])

    def test_reconciliation_is_idempotent_and_collision_closed(self):
        script = _import_script()
        plan = _seed_plan(script)
        empty = script.reconcile_fixture_state(
            plan=plan,
            document_rows=[],
            profile_rows=[],
            assignment_rows=[],
        )
        self.assertEqual(empty["would_insert_document_versions"], 2)
        self.assertEqual(empty["would_insert_destination_profiles"], 4)
        self.assertEqual(empty["would_insert_work_assignments"], 1)

        documents = [copy.deepcopy(row) for row in plan["document_versions"]]
        profile = copy.deepcopy(plan["destination_profile"])
        legacy_profile = copy.deepcopy(plan["destination_profiles"][0])
        compat_profile = copy.deepcopy(plan["destination_profiles"][1])
        prior_profile = copy.deepcopy(plan["destination_profiles"][2])
        assignment = copy.deepcopy(plan["work_assignment"])
        assignment.update(
            {
                "attention_item_id": None,
                "accepted_at": "2026-08-28T00:00:00+00:00",
                "released_at": None,
            }
        )
        ready = script.reconcile_fixture_state(
            plan=plan,
            document_rows=documents,
            profile_rows=[
                legacy_profile,
                compat_profile,
                prior_profile,
                profile,
            ],
            assignment_rows=[assignment],
        )
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["would_insert_document_versions"], 0)
        self.assertEqual(ready["would_insert_destination_profiles"], 0)
        self.assertEqual(ready["would_insert_work_assignments"], 0)

        upgrade = script.reconcile_fixture_state(
            plan=plan,
            document_rows=documents,
            profile_rows=[legacy_profile, compat_profile, prior_profile],
            assignment_rows=[assignment],
        )
        self.assertFalse(upgrade["ready"])
        self.assertEqual(upgrade["would_insert_destination_profiles"], 1)

        upgraded = script.reconcile_fixture_state(
            plan=plan,
            document_rows=documents,
            profile_rows=[
                legacy_profile,
                compat_profile,
                prior_profile,
                copy.deepcopy(plan["destination_profile"]),
            ],
            assignment_rows=[assignment],
        )
        self.assertTrue(upgraded["ready"])
        self.assertEqual(upgraded["would_insert_destination_profiles"], 0)

        with self.assertRaisesRegex(
            script.PresenterFixtureError,
            "synthetic_example_profile_version_gap",
        ):
            script.reconcile_fixture_state(
                plan=plan,
                document_rows=documents,
                profile_rows=[profile],
                assignment_rows=[assignment],
            )

        profile["portal_origin"] = "https://collision.example"
        with self.assertRaises(script.PresenterFixtureError):
            script.reconcile_fixture_state(
                plan=plan,
                document_rows=documents,
                profile_rows=[legacy_profile, profile],
                assignment_rows=[assignment],
            )

    def test_fresh_database_inserts_profile_versions_in_trigger_order(self):
        script = _import_script()
        plan = _seed_plan(script)
        before = {
            "missing_source_document_ids": [],
            "missing_profile_versions": [1, 2, 3, 4],
            "assignment_missing": False,
        }
        after = {"ready": True}
        connection = _InsertConnection()

        with mock.patch.object(
            script,
            "load_fixture_state",
            side_effect=[before, after],
        ):
            inserted = script.insert_fixture(connection, plan=plan)

        self.assertEqual(inserted, 4)
        self.assertEqual(connection.profile_versions, [1, 2, 3, 4])

    def test_work_assignment_schema_is_required_fail_closed(self):
        script = _import_script()
        required = {
            "id",
            "case_id",
            "attention_item_id",
            "operator_id",
            "assignment_role",
            "status",
            "assigned_by",
            "assigned_at",
            "accepted_at",
            "released_at",
            "metadata",
            "created_at",
            "updated_at",
        }
        script.assert_work_assignment_schema(_ColumnConnection(required))
        required.remove("accepted_at")
        with self.assertRaises(script.PresenterFixtureError):
            script.assert_work_assignment_schema(_ColumnConnection(required))
        required.add("accepted_at")
        with self.assertRaises(script.PresenterFixtureError):
            script.assert_work_assignment_schema(
                _ColumnConnection(required, indexes=())
            )

    def test_script_is_insert_only_and_has_no_b2_or_network_client(self):
        source = SCRIPT.read_text(encoding="utf-8")
        targets = set(
            re.findall(r"INSERT\s+INTO\s+([a-zA-Z0-9_]+)", source, re.I)
        )
        self.assertEqual(
            targets,
            {
                "rtm_presenter_document_versions",
                "rtm_presenter_destination_profiles",
                "rtm_work_assignments",
            },
        )
        self.assertIsNone(re.search(r"(?im)^\s*(?:UPDATE|DELETE|DROP|TRUNCATE)\b", source))
        for forbidden_import in (
            "import boto3",
            "import requests",
            "import httpx",
            "from b2_storage",
            "get_s3_client",
        ):
            self.assertNotIn(forbidden_import, source)
        self.assertIn("SET TRANSACTION READ ONLY", source)
        self.assertIn("with engine.begin() as conn:", source)
        self.assertGreaterEqual(source.count("ON CONFLICT DO NOTHING"), 3)


if __name__ == "__main__":
    unittest.main()
