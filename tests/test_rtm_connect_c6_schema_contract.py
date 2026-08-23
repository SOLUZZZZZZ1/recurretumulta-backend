from __future__ import annotations

import unittest

from rtm_connect.schema import CONNECT_C1_REQUIRED_CONSTRAINTS
from rtm_connect.provider_sandbox_schema import (
    CONNECT_C6_SCHEMA_CHANGES_REQUIRED,
    connect_c6_provider_ddl,
)
from scripts.rtm_staging_connect_c6_schema import (
    C1_CONSTRAINT_TABLES,
    C1_TRIGGER_BINDINGS,
    C6_SMOKE_OPERATOR_COLUMNS,
    _expected_c1_function_hashes,
    _expected_c1_indexes,
    _expected_c1_constraints,
    _canonical_sql_fragment,
    _normalize_function_body,
)


class ConnectC6SchemaContractTest(unittest.TestCase):
    def test_c6_has_no_ddl_or_migration_requirement(self):
        self.assertFalse(CONNECT_C6_SCHEMA_CHANGES_REQUIRED)
        self.assertEqual(connect_c6_provider_ddl(), [])

    def test_smoke_operator_dependencies_are_fully_audited(self):
        self.assertIn("rtm_operator_roles", C6_SMOKE_OPERATOR_COLUMNS)
        self.assertIn("permissions", C6_SMOKE_OPERATOR_COLUMNS["rtm_operator_roles"])
        self.assertIn("primary_role_id", C6_SMOKE_OPERATOR_COLUMNS["rtm_operators"])
        self.assertIn("profile", C6_SMOKE_OPERATOR_COLUMNS["rtm_operators"])

    def test_c1_trigger_bindings_freeze_table_function_and_type(self):
        self.assertEqual(
            C1_TRIGGER_BINDINGS["trg_rtm_connect_actions_state_guard"],
            ("rtm_connect_actions", "rtm_guard_connect_action_transition", 19),
        )
        self.assertEqual(
            C1_TRIGGER_BINDINGS["trg_rtm_connect_evidence_append_only"],
            ("rtm_connect_evidence", "rtm_guard_connect_append_only", 27),
        )

    def test_c1_guard_bodies_indexes_and_constraints_are_integrity_audited(self):
        function_hashes = _expected_c1_function_hashes()
        self.assertEqual(
            set(function_hashes),
            {
                "rtm_guard_connect_action_transition",
                "rtm_guard_connect_append_only",
            },
        )
        self.assertTrue(all(len(value) == 64 for value in function_hashes.values()))
        indexes = _expected_c1_indexes()
        self.assertEqual(len(indexes), 16)
        self.assertEqual(
            indexes["uq_rtm_connect_action_idempotency"],
            ("rtm_connect_actions", True, ("idempotency_key",), None),
        )
        self.assertEqual(
            C1_CONSTRAINT_TABLES["ck_rtm_connect_authorization_frozen"],
            "rtm_connect_authorizations",
        )
        self.assertEqual(
            set(C1_CONSTRAINT_TABLES),
            set(CONNECT_C1_REQUIRED_CONSTRAINTS),
        )
        constraints = _expected_c1_constraints()
        self.assertEqual(set(constraints), set(CONNECT_C1_REQUIRED_CONSTRAINTS))
        self.assertIn(
            "frozen = true",
            constraints["ck_rtm_connect_authorization_frozen"][1],
        )

    def test_schema_audit_never_case_folds_sql_literal_values(self):
        self.assertNotEqual(
            _canonical_sql_fragment("status = 'CONFIRMED'"),
            _canonical_sql_fragment("status = 'confirmed'"),
        )
        self.assertNotEqual(
            _normalize_function_body("RETURN 'CONFIRMED';"),
            _normalize_function_body("return 'confirmed';"),
        )

    def test_schema_audit_preserves_literal_whitespace_and_dollar_bodies(self):
        self.assertNotEqual(
            _canonical_sql_fragment("note = 'a  b'"),
            _canonical_sql_fragment("note = 'a b'"),
        )
        self.assertEqual(
            _normalize_function_body("BEGIN  PERFORM $$A  B$$; END"),
            "begin perform $$A  B$$; end",
        )


if __name__ == "__main__":
    unittest.main()
