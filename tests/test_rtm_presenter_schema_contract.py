from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "rtm_presenter_schema.py"

EXPECTED_TABLES = {
    "rtm_presenter_document_versions",
    "rtm_presenter_destination_profiles",
    "rtm_presenter_filing_packages",
    "rtm_presenter_idempotency_keys",
    "rtm_presenter_package_items",
    "rtm_presenter_handoff_tickets",
    "rtm_presenter_audit_events",
    "rtm_presenter_admin_exports",
    "rtm_presenter_signer_installations",
}


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement):
        self.statements.append(str(statement))
        return None


class RTMPresenterSchemaContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCHEMA.read_text(encoding="utf-8")
        ast.parse(cls.source, filename=str(SCHEMA))

    def test_exports_complete_idempotent_schema_contract(self):
        import rtm_presenter_schema as schema

        self.assertEqual(set(schema.PRESENTER_REQUIRED_COLUMNS), EXPECTED_TABLES)
        self.assertEqual(
            schema.RTM_PRESENTER_SCHEMA_VERSION,
            "rtm_presenter_schema_v1_2",
        )
        self.assertIsInstance(schema.PRESENTER_REQUIRED_INDEXES, set)
        self.assertIsInstance(schema.PRESENTER_REQUIRED_TRIGGERS, set)
        self.assertIsInstance(schema.PRESENTER_REQUIRED_CONSTRAINTS, set)
        self.assertEqual(
            set(schema.PRESENTER_REQUIRED_COLUMN_TYPES),
            set(schema.PRESENTER_REQUIRED_COLUMNS),
        )
        for table_name, required_columns in (
            schema.PRESENTER_REQUIRED_COLUMNS.items()
        ):
            self.assertEqual(
                set(schema.PRESENTER_REQUIRED_COLUMN_TYPES[table_name]),
                required_columns,
            )
        self.assertEqual(
            set(schema.PRESENTER_REQUIRED_INDEX_TABLES),
            schema.PRESENTER_REQUIRED_INDEXES,
        )
        self.assertEqual(
            set(schema.PRESENTER_REQUIRED_TRIGGER_BINDINGS),
            schema.PRESENTER_REQUIRED_TRIGGERS,
        )
        self.assertEqual(
            set(schema.PRESENTER_REQUIRED_CONSTRAINT_TABLES),
            schema.PRESENTER_REQUIRED_CONSTRAINTS,
        )
        self.assertEqual(
            schema.PRESENTER_REQUIRED_FUNCTIONS,
            {
                function_name
                for _, function_name
                in schema.PRESENTER_REQUIRED_TRIGGER_BINDINGS.values()
            },
        )

        ddl = schema.rtm_presenter_schema_ddl()
        self.assertTrue(ddl)
        self.assertEqual(len(ddl), len({name for name, _ in ddl}))
        ddl_text = "\n".join(statement for _, statement in ddl)
        for table_name in EXPECTED_TABLES:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table_name}", ddl_text)
        for required_names in (
            schema.PRESENTER_REQUIRED_INDEXES,
            schema.PRESENTER_REQUIRED_TRIGGERS,
            schema.PRESENTER_REQUIRED_CONSTRAINTS,
        ):
            for name in required_names:
                self.assertIn(name, ddl_text)

    def test_schema_is_additive_and_has_no_seed_or_router_side_effect(self):
        upper = self.source.upper()
        for forbidden in (
            "DROP TABLE",
            "DROP COLUMN",
            "TRUNCATE",
            "DELETE FROM",
            "INSERT INTO CASES",
            "INSERT INTO RTM_OPERATORS",
            "INCLUDE_ROUTER",
        ):
            self.assertNotIn(forbidden, upper)

    def test_document_versions_are_hash_bound_internal_and_append_only(self):
        import rtm_presenter_schema as schema

        columns = schema.PRESENTER_REQUIRED_COLUMNS[
            "rtm_presenter_document_versions"
        ]
        for required in (
            "logical_document_id",
            "version_number",
            "supersedes_version_id",
            "source_document_id",
            "sha256",
            "purpose",
            "state",
            "scan_status",
        ):
            self.assertIn(required, columns)
        for forbidden_column in (
            "b2_bucket",
            "b2_key",
            "presigned_url",
            "download_url",
        ):
            self.assertNotIn(forbidden_column, columns)

        ddl = dict(schema.rtm_presenter_schema_ddl())
        append_trigger = " ".join(
            ddl["presenter_document_version_append_trigger"].split()
        )
        self.assertIn("BEFORE UPDATE OR DELETE", append_trigger)
        scope = ddl["presenter_document_version_scope_function"]
        self.assertIn("d.id = NEW.source_document_id", scope)
        self.assertIn("d.case_id = NEW.case_id", scope)
        self.assertIn("d.sha256 = NEW.sha256", scope)
        self.assertIn("NEW.version_number - 1", scope)
        self.assertIn("pg_advisory_xact_lock", scope)
        self.assertIn("hashtextextended", scope)
        self.assertIn("rtm-presenter-document-lineage:", scope)
        self.assertIn("NEW.case_id::TEXT", scope)
        self.assertIn("NEW.logical_document_id::TEXT", scope)

    def test_destination_profiles_are_versioned_verified_and_secret_free(self):
        import rtm_presenter_schema as schema

        columns = schema.PRESENTER_REQUIRED_COLUMNS[
            "rtm_presenter_destination_profiles"
        ]
        for required in (
            "profile_code",
            "version_number",
            "portal_origin",
            "requirements",
            "profile_sha256",
            "verified_by_operator_id",
            "verified_at",
        ):
            self.assertIn(required, columns)
        ddl = "\n".join(
            statement for _, statement in schema.rtm_presenter_schema_ddl()
        )
        self.assertIn("status IN ('draft', 'active', 'retired')", ddl)
        self.assertIn("Presenter destination profile version gap", ddl)
        self.assertIn("trg_rtm_presenter_destination_profile_append_only", ddl)

    def test_frozen_packages_bind_profile_authorization_and_exact_items(self):
        import rtm_presenter_schema as schema

        ddl = dict(schema.rtm_presenter_schema_ddl())
        guard = " ".join(
            ddl["presenter_filing_package_guard_function"].split()
        )
        for required in (
            "profile_status IS DISTINCT FROM 'active'",
            "NEW.authorization_document_version_id",
            "v.state = 'active'",
            "v.scan_status = 'clean'",
            "NEW.expected_item_count",
            "actual_item_count",
            "invalid_items",
            "OLD.status = 'frozen'",
            "RTM Presenter frozen package is immutable",
            "representation_authorization",
            "NOT EXISTS",
            "newer.version_number > v.version_number",
            "pg_advisory_xact_lock",
            "rtm-presenter-document-lineage:",
            "ORDER BY v.case_id, v.logical_document_id",
        ):
            self.assertIn(required, guard)

        item_guard = " ".join(
            ddl["presenter_package_item_guard_function"].split()
        )
        for required in (
            "target_package_id := OLD.package_id",
            "parent.status <> 'draft'",
            "document.sha256 IS DISTINCT FROM NEW.document_sha256",
            "document.purpose IS DISTINCT FROM NEW.purpose",
            "document.state IS DISTINCT FROM 'active'",
            "document.scan_status IS DISTINCT FROM 'clean'",
            "NOT EXISTS",
        ):
            self.assertIn(required, item_guard)

    def test_freeze_idempotency_is_persistent_scoped_and_append_only(self):
        import rtm_presenter_schema as schema

        columns = schema.PRESENTER_REQUIRED_COLUMNS[
            "rtm_presenter_idempotency_keys"
        ]
        self.assertEqual(
            columns,
            {
                "id",
                "operator_id",
                "idempotency_key",
                "request_sha256",
                "case_id",
                "package_id",
                "created_at",
            },
        )
        ddl = dict(schema.rtm_presenter_schema_ddl())
        self.assertIn(
            "operator_id, idempotency_key",
            " ".join(ddl["presenter_idempotency_key_index"].split()),
        )
        scope = " ".join(
            ddl["presenter_idempotency_scope_function"].split()
        )
        self.assertIn("package.created_by_operator_id", scope)
        self.assertIn("package.case_id", scope)
        self.assertIn("package.status IS DISTINCT FROM 'frozen'", scope)
        append = " ".join(
            ddl["presenter_idempotency_append_trigger"].split()
        )
        self.assertIn("BEFORE UPDATE OR DELETE", append)

    def test_handoff_ticket_stores_only_hash_and_is_session_single_use(self):
        import rtm_presenter_schema as schema

        columns = schema.PRESENTER_REQUIRED_COLUMNS[
            "rtm_presenter_handoff_tickets"
        ]
        for required in (
            "ticket_hash",
            "operator_id",
            "operator_session_id",
            "extension_client_id",
            "case_id",
            "package_id",
            "package_item_id",
            "portal_origin",
            "field_code",
            "expires_at",
            "used_at",
        ):
            self.assertIn(required, columns)
        for forbidden in (
            "ticket",
            "raw_ticket",
            "token",
            "secret",
            "b2_bucket",
            "b2_key",
        ):
            self.assertNotIn(forbidden, columns - {"ticket_hash"})

        ddl = dict(schema.rtm_presenter_schema_ddl())
        table = ddl["presenter_handoff_tickets"]
        guard = " ".join(
            ddl["presenter_handoff_ticket_guard_function"].split()
        )
        self.assertIn("INTERVAL '15 minutes'", table)
        self.assertIn(schema.RTM_PRESENTER_EXTENSION_CLIENT_ID, table)
        for required in (
            "s.id = NEW.operator_session_id",
            "s.operator_id = NEW.operator_id",
            "s.status = 'active'",
            "s.expires_at > NOW()",
            "package.status IS DISTINCT FROM 'frozen'",
            "profile_origin IS DISTINCT FROM NEW.portal_origin",
            "OLD.used_at IS NOT NULL",
            "ticket is single-use",
            "to_jsonb(NEW) - 'used_at'",
        ):
            self.assertIn(required, guard)

    def test_audit_and_admin_export_evidence_are_append_only(self):
        import rtm_presenter_schema as schema

        export_columns = schema.PRESENTER_REQUIRED_COLUMNS[
            "rtm_presenter_admin_exports"
        ]
        for required in (
            "admin_operator_id",
            "reason",
            "reauthenticated_at",
            "reauthentication_evidence_sha256",
            "export_scope",
            "watermark",
            "watermark_sha256",
            "source_hashes",
            "manifest_sha256",
            "export_sha256",
        ):
            self.assertIn(required, export_columns)

        ddl = dict(schema.rtm_presenter_schema_ddl())
        export_guard = ddl["presenter_admin_export_scope_function"]
        for required in (
            "r.code = 'rtm.admin'",
            "r.permissions ?",
            "ops.documents.export_exceptional",
            "NEW.reauthenticated_at",
            "NEW.created_at - INTERVAL '5 minutes'",
            "NEW.export_scope->>'operator_session_id'",
            "NEW.export_scope->>'reauthentication_event_id'",
            "FROM rtm_operator_sessions s",
            "JOIN rtm_operator_access_events e",
            "e.event_type = 'auth.reauthenticated'",
            "e.reason_code = 'password_reverified'",
            "e.occurred_at = s.last_verified_at",
            "s.absolute_expires_at > NEW.created_at",
            "s.last_verified_at > s.login_at",
            "jsonb_array_elements_text(NEW.source_hashes)",
            "export_doc.sha256 IS DISTINCT FROM",
            "NEW.export_sha256",
        ):
            self.assertIn(required, export_guard)
        self.assertNotIn("r.code IN (", export_guard)

        export_table = ddl["presenter_admin_exports"]
        self.assertIn("INTERVAL '5 minutes'", export_table)

        for name in (
            "presenter_admin_export_append_trigger",
            "presenter_audit_event_append_trigger",
        ):
            trigger = " ".join(ddl[name].split())
            self.assertIn("BEFORE UPDATE OR DELETE", trigger)
            self.assertIn("rtm_presenter_reject_mutation", trigger)

    def test_signer_installation_is_candidate_only_device_bound_and_secret_free(self):
        import rtm_presenter_schema as schema

        columns = schema.PRESENTER_REQUIRED_COLUMNS[
            "rtm_presenter_signer_installations"
        ]
        self.assertEqual(
            columns,
            {
                "id",
                "operator_id",
                "operator_device_id",
                "client_instance_id",
                "client_binding_sha256",
                "station_label",
                "platform",
                "client_version",
                "status",
                "registered_at",
                "metadata",
            },
        )
        for forbidden in (
            "token",
            "secret",
            "certificate",
            "private_key",
            "portal_session",
            "b2_bucket",
            "b2_key",
        ):
            self.assertNotIn(forbidden, columns)

        ddl = dict(schema.rtm_presenter_schema_ddl())
        table = ddl["presenter_signer_installations"]
        guard = ddl["presenter_signer_installation_scope_function"]
        append = ddl["presenter_signer_installation_append_trigger"]
        self.assertIn("status = 'candidate'", table)
        self.assertIn("rtm_presenter_local_station_v1_0", table)
        self.assertIn("managed_attestation_verified", table)
        self.assertIn("external_effects_allowed", table)
        self.assertIn("document_bytes_allowed", table)
        self.assertIn("certificate_access_allowed", table)
        self.assertIn("portal_open_allowed", table)
        self.assertIn("d.id = NEW.operator_device_id", guard)
        self.assertIn("d.operator_id = NEW.operator_id", guard)
        self.assertIn("r.code = 'rtm.signer'", guard)
        self.assertIn("jsonb_array_length(r.permissions) = 3", guard)
        self.assertIn("NOW() - INTERVAL '5 minutes'", guard)
        self.assertIn("NOW() + INTERVAL '1 minute'", guard)
        self.assertIn("BEFORE UPDATE OR DELETE", " ".join(append.split()))

    def test_no_public_view_or_storage_locator_is_created(self):
        import rtm_presenter_schema as schema

        ddl_text = "\n".join(
            statement for _, statement in schema.rtm_presenter_schema_ddl()
        )
        self.assertNotIn("CREATE VIEW", ddl_text.upper())
        self.assertNotRegex(ddl_text, r"\bb2_bucket\s+TEXT\b")
        self.assertNotRegex(ddl_text, r"\bb2_key\s+TEXT\b")
        self.assertNotRegex(ddl_text, r"\bpresigned_url\s+TEXT\b")

    def test_ensure_schema_uses_callers_transaction_and_all_statements(self):
        import rtm_presenter_schema as schema

        connection = _RecordingConnection()
        applied = schema.ensure_rtm_presenter_schema(connection)
        expected = schema.rtm_presenter_schema_ddl()
        self.assertEqual(applied, [name for name, _ in expected])
        self.assertEqual(connection.statements, [sql for _, sql in expected])


if __name__ == "__main__":
    unittest.main()
