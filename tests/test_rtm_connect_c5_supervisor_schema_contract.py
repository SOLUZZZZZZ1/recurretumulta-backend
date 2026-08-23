from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "rtm_connect" / "supervisor_schema.py"


def _load_schema_module():
    spec = importlib.util.spec_from_file_location(
        "_rtm_connect_c5_supervisor_schema_under_test",
        SCHEMA,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("No se pudo cargar supervisor_schema.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConnectC5SupervisorSchemaContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = _load_schema_module()

    def test_c5_has_no_ddl_or_schema_migration(self):
        self.assertFalse(self.schema.CONNECT_C5_SCHEMA_CHANGES_REQUIRED)
        self.assertEqual(self.schema.connect_c5_supervisor_ddl(), [])
        source = SCHEMA.read_text(encoding="utf-8").upper()
        for forbidden in (
            "CREATE TABLE",
            "ALTER TABLE",
            "CREATE INDEX",
            "CREATE TRIGGER",
            "DROP TABLE",
            "TRUNCATE",
            "DELETE FROM",
        ):
            self.assertNotIn(forbidden, source)

    def test_contract_reuses_c1_c3_c4_ledgers(self):
        required = set(self.schema.CONNECT_C5_REQUIRED_COLUMNS)
        for table in (
            "rtm_connect_connectors",
            "rtm_connect_actions",
            "rtm_connect_authorizations",
            "rtm_connect_attempts",
            "rtm_connect_evidence",
            "rtm_connect_transitions",
            "rtm_connect_manual_tasks",
            "rtm_connect_webhook_inbox",
            "rtm_connect_reconciliations",
        ):
            self.assertIn(table, required)

    def test_connector_dependencies_can_enforce_synthetic_scope(self):
        columns = self.schema.CONNECT_C5_REQUIRED_COLUMNS[
            "rtm_connect_connectors"
        ]
        for column in (
            "environment",
            "synthetic_only",
            "credential_ref",
            "status",
        ):
            self.assertIn(column, columns)

    def test_live_supervisor_permission_dependencies_are_declared(self):
        operators = self.schema.CONNECT_C5_REQUIRED_COLUMNS["rtm_operators"]
        roles = self.schema.CONNECT_C5_REQUIRED_COLUMNS[
            "rtm_operator_roles"
        ]
        self.assertIn("must_change_password", operators)
        self.assertIn("status", operators)
        self.assertIn("active", roles)
        self.assertIn("permissions", roles)

    def test_existing_append_only_access_audit_is_reused(self):
        required = self.schema.CONNECT_C5_REQUIRED_COLUMNS
        self.assertIn("rtm_operator_access_events", required)
        self.assertIn("rtm_operator_access_evidence", required)
        self.assertIn(
            "retention_until",
            required["rtm_operator_access_evidence"],
        )

    def test_projection_contract_does_not_require_raw_connect_material(self):
        forbidden = {
            "payload",
            "target_ref",
            "document_hashes",
            "configuration",
            "request_metadata",
            "result_metadata",
            "package_manifest",
            "instructions",
            "receipt_storage_ref",
            "metadata",
            "reason_detail",
        }
        connect_columns = set().union(
            *(
                columns
                for table, columns in self.schema.CONNECT_C5_REQUIRED_COLUMNS.items()
                if table.startswith("rtm_connect_")
            )
        )
        self.assertFalse(forbidden & connect_columns)


if __name__ == "__main__":
    unittest.main()
