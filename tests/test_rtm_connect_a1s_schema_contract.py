from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "rtm_connect" / "human_filing_schema.py"

EXPECTED_TABLES = {
    "rtm_connect_a1s_tenants",
    "rtm_connect_a1s_memberships",
    "rtm_connect_a1s_case_bindings",
    "rtm_connect_a1s_representation_evidence",
    "rtm_connect_a1s_human_tasks",
    "rtm_connect_a1s_approvals",
    "rtm_connect_a1s_artifacts",
    "rtm_connect_a1s_events",
    "rtm_connect_a1s_idempotency",
}


class ConnectA1SSchemaContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCHEMA.read_text(encoding="utf-8")
        ast.parse(cls.source, filename=str(SCHEMA))

    def test_schema_exports_requirements_and_idempotent_ddl(self):
        from rtm_connect import human_filing_schema as schema

        self.assertTrue(schema.RTM_CONNECT_A1S_SCHEMA_VERSION)
        self.assertEqual(set(schema.CONNECT_A1S_REQUIRED_COLUMNS), EXPECTED_TABLES)
        self.assertIsInstance(schema.CONNECT_A1S_REQUIRED_INDEXES, set)
        self.assertIsInstance(schema.CONNECT_A1S_REQUIRED_TRIGGERS, set)
        self.assertIsInstance(schema.CONNECT_A1S_REQUIRED_CONSTRAINTS, set)
        ddl = schema.connect_a1s_human_filing_ddl()
        self.assertTrue(ddl)
        self.assertEqual(len({name for name, _ in ddl}), len(ddl))
        ddl_text = "\n".join(statement for _, statement in ddl)
        for table_name in EXPECTED_TABLES:
            self.assertIn(
                f"CREATE TABLE IF NOT EXISTS {table_name}",
                ddl_text,
            )
        for names in (
            schema.CONNECT_A1S_REQUIRED_INDEXES,
            schema.CONNECT_A1S_REQUIRED_TRIGGERS,
            schema.CONNECT_A1S_REQUIRED_CONSTRAINTS,
        ):
            for name in names:
                self.assertIn(name, ddl_text)

    def test_ddl_is_additive_and_does_not_seed_identity_or_cases(self):
        upper = self.source.upper()
        for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM"):
            self.assertNotIn(forbidden, upper)
        self.assertNotIn("INSERT INTO RTM_OPERATORS", upper)
        self.assertNotIn("INSERT INTO RTM_CONNECT_A1S_TENANTS", upper)
        self.assertNotIn("INSERT INTO RTM_CONNECT_A1S_CASE_BINDINGS", upper)

    def test_tenant_case_ownership_and_principal_links_are_database_backed(self):
        for required in (
            "tenant_id",
            "case_binding_id",
            "principal_id",
            "operator_id",
            "membership",
            "FOREIGN KEY",
            "UNIQUE",
        ):
            self.assertIn(required, self.source)

    def test_history_and_approvals_are_append_only(self):
        for table in ("rtm_connect_a1s_events", "rtm_connect_a1s_approvals"):
            self.assertIn(table, self.source)
        self.assertIn("append", self.source.lower())
        self.assertIn("TRIGGER", self.source.upper())

    def test_packages_and_artifacts_are_hash_bound_not_b2_objects(self):
        self.assertIn("package_sha256", self.source)
        self.assertIn('"sha256"', self.source)
        self.assertIn("synthetic_only", self.source)
        self.assertIn("test_mode", self.source)
        for forbidden in ("presigned_url",):
            self.assertNotIn(forbidden, self.source.lower())
        self.assertIn("receipt_document.b2_bucket IS NULL", self.source)
        self.assertIn("receipt_document.b2_key IS NULL", self.source)

    def test_due_date_and_assignment_are_frozen_by_the_task_trigger(self):
        from rtm_connect import human_filing_schema as schema

        ddl_text = " ".join(
            "\n".join(
                statement
                for _, statement in schema.connect_a1s_human_filing_ddl()
            ).split()
        )
        self.assertIn(
            "CAST(NEW.package_manifest->>'due_at' AS TIMESTAMPTZ) "
            "= NEW.due_at",
            ddl_text,
        )
        self.assertIn("NEW.due_at IS DISTINCT FROM OLD.due_at", ddl_text)
        self.assertIn("NEW.status <> 'prepared'", ddl_text)
        self.assertIn(
            "OLD.status = 'prepared' AND NEW.status = 'assigned'",
            ddl_text,
        )
        self.assertIn(
            "assigner.role = 'supervisor'",
            ddl_text,
        )
        assignment_fields = (
            "assignee_membership_id",
            "assignee_principal_id",
            "assignee_operator_id",
            "assigned_by_operator_id",
            "assigned_at",
        )
        for field in assignment_fields:
            self.assertIn(f"NEW.{field} IS NOT NULL", ddl_text)
            self.assertIn(
                f"NEW.{field} IS DISTINCT FROM OLD.{field}",
                ddl_text,
            )
        self.assertIn("A1-S assignment is write-once", ddl_text)

    def test_case_binding_is_globally_unique_across_tenants(self):
        from rtm_connect import human_filing_schema as schema

        statements = dict(schema.connect_a1s_human_filing_ddl())
        index = " ".join(statements["a1s_active_case_binding_index"].split())
        self.assertIn(
            "uq_rtm_connect_a1s_active_case_binding_case_id",
            schema.CONNECT_A1S_REQUIRED_INDEXES,
        )
        self.assertIn(
            "ON rtm_connect_a1s_case_bindings(case_id) "
            "WHERE status = 'active'",
            index,
        )
        self.assertNotIn("(tenant_id, case_id)", index)

    def test_task_package_is_bound_to_case_documents_and_exact_checklist(self):
        for required in (
            "NEW.package_manifest->'document_hashes' =",
            "a.document_hashes",
            "jsonb_array_elements_text(a.document_hashes)",
            "source_document.case_id = b.case_id",
            "source_document.sha256 =",
            "requested_document.document_sha256",
            "NEW.package_manifest->'checklist'",
            "confirm_synthetic_case_binding",
            "confirm_frozen_core_authority",
            "confirm_synthetic_representation",
            "confirm_exact_package_hash",
            "simulate_human_filing_without_external_contact",
            "capture_synthetic_receipt",
            "verify_receipt_with_independent_principal",
        ):
            self.assertIn(required, self.source)

    def test_approval_review_and_receipt_guards_are_database_backed(self):
        for required in (
            "frozen_authorization.approved_by_operator_ids",
            "? NEW.operator_id::text",
            "review_artifact.kind =",
            "'human_review_attestation'",
            "review_artifact.sha256 =",
            "NEW.review_attestation_sha256",
            "review_artifact.submitted_by_membership_id =",
            "NEW.assignee_membership_id",
            "A1-S reviewed_at is write-once",
            "A1-S review readiness is write-once",
            "NEW.canonical_payload->>'document_id'",
            "NEW.canonical_payload->>'document_sha256'",
            "rtm_connect_a1s_synthetic_receipt_fixture",
            "receipt_document.mime = 'application/json'",
            "receipt_document.size_bytes BETWEEN 1 AND 65536",
            "receipt_document.b2_bucket IS NULL",
            "receipt_document.b2_key IS NULL",
        ):
            self.assertIn(required, self.source)

    def test_operational_task_requires_latest_authorization_version(self):
        for required in (
            "rtm_connect_authorizations newer_authority",
            "newer_authority.action_id = z.action_id",
            "newer_authority.authorization_version >",
            "z.authorization_version",
            "manual_review_closure OR NOT EXISTS",
        ):
            self.assertIn(required, self.source)

    def test_receipt_is_separate_output_and_e4_revalidates_full_binding(self):
        from rtm_connect import human_filing_schema as schema

        statements = dict(schema.connect_a1s_human_filing_ddl())
        artifact_guard = " ".join(
            statements["a1s_artifact_scope_function"].split()
        )
        task_guard = " ".join(
            statements["a1s_task_guard_function"].split()
        )

        for field in (
            "tenant_id",
            "task_id",
            "case_binding_id",
            "case_id",
            "action_id",
            "attempt_id",
            "authorization_id",
            "authorization_version",
            "request_sha256",
            "package_sha256",
            "external_reference",
        ):
            self.assertIn(f"NEW.canonical_payload->>'{field}'", artifact_guard)
            self.assertIn(field, task_guard)

        self.assertIn(
            "receipt_task.package_manifest->'document_hashes' "
            "? receipt_document.sha256",
            artifact_guard,
        )
        self.assertIn(
            "receipt_task.package_manifest->'document_hashes' ? NEW.sha256",
            artifact_guard,
        )
        self.assertIn("receipt_artifact.canonical_payload", task_guard)
        self.assertIn(
            "f.canonical_payload->>'receipt_artifact_id'",
            task_guard,
        )
        self.assertIn(
            "NEW.package_manifest->'document_hashes' "
            "? receipt_document.sha256",
            task_guard,
        )
        self.assertIn(
            "NEW.package_manifest->'document_hashes' "
            "? receipt_artifact.sha256",
            task_guard,
        )
        self.assertIn(
            "'rtm.a1s.synthetic_receipt_verification.v1'",
            task_guard,
        )

    def test_manual_review_is_available_as_safe_non_operational_closure(self):
        from rtm_connect import human_filing_schema as schema

        task_guard = " ".join(
            dict(schema.connect_a1s_human_filing_ddl())[
                "a1s_task_guard_function"
            ].split()
        )

        self.assertIn("manual_review_closure BOOLEAN := FALSE", task_guard)
        self.assertIn(
            "manual_review_closure := NEW.status = 'manual_review' "
            "AND OLD.status IS DISTINCT FROM NEW.status",
            task_guard,
        )
        self.assertIn(
            "manual_review_closure OR ( b.status = 'active' "
            "AND r.status = 'active' AND r.valid_from <= NOW() "
            "AND r.expires_at > NOW() AND requester.status = 'active'",
            task_guard,
        )
        self.assertIn(
            "NEW.assignee_membership_id IS NULL "
            "OR executor.status = 'active'",
            task_guard,
        )
        self.assertIn(
            "NOT manual_review_closure AND NOT EXISTS ( SELECT 1 FROM "
            "documents source_document",
            task_guard,
        )
        self.assertIn(
            "manual_review_closure OR ( c.status = 'active' "
            "AND z.revoked_at IS NULL AND ( z.expires_at IS NULL "
            "OR z.expires_at > NOW() ) )",
            task_guard,
        )

    def test_manual_review_closure_keeps_identity_synthetic_and_package_frozen(self):
        from rtm_connect import human_filing_schema as schema

        task_guard = " ".join(
            dict(schema.connect_a1s_human_filing_ddl())[
                "a1s_task_guard_function"
            ].split()
        )
        for required in (
            "t.id = NEW.tenant_id AND t.status = 'active'",
            "t.synthetic_only = TRUE",
            "b.synthetic_only = TRUE",
            "b.metadata->>'test_mode' = 'true'",
            "r.synthetic_only = TRUE",
            "requester.id = NEW.requester_membership_id",
            "requester.tenant_id = NEW.tenant_id",
            "requester.principal_id = NEW.requester_principal_id",
            "requester.operator_id = NEW.requester_operator_id",
            "executor.id = NEW.assignee_membership_id",
            "executor.tenant_id = NEW.tenant_id",
            "executor.principal_id = NEW.assignee_principal_id",
            "executor.operator_id = NEW.assignee_operator_id",
            "z.decision = 'approved_frozen' AND z.frozen = TRUE",
            "NEW.package_manifest->'document_hashes' = a.document_hashes",
            "NEW.package_manifest IS DISTINCT FROM OLD.package_manifest",
            "NEW.package_sha256 IS DISTINCT FROM OLD.package_sha256",
        ):
            self.assertIn(required, task_guard)

        self.assertNotIn(
            "OLD.status = 'prepared' AND NEW.status = 'manual_review'",
            task_guard,
        )
        self.assertNotIn(
            "OLD.status = 'assigned' AND NEW.status = 'manual_review'",
            task_guard,
        )


if __name__ == "__main__":
    unittest.main()
