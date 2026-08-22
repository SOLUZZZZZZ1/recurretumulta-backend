from __future__ import annotations

import unittest
from pathlib import Path

from rtm_connect.schema import (
    CONNECT_C1_REQUIRED_COLUMNS,
    CONNECT_C1_REQUIRED_CONSTRAINTS,
    CONNECT_C1_REQUIRED_INDEXES,
    CONNECT_C1_REQUIRED_TRIGGERS,
    connect_c1_ddl,
)

ROOT = Path(__file__).resolve().parents[1]


class ConnectC1SchemaContractTest(unittest.TestCase):
    def test_schema_defines_seven_kernel_tables(self):
        self.assertEqual(len(CONNECT_C1_REQUIRED_COLUMNS), 7)
        self.assertEqual(
            set(CONNECT_C1_REQUIRED_COLUMNS),
            {
                "rtm_connect_connectors", "rtm_connect_actions",
                "rtm_connect_authorizations", "rtm_connect_attempts",
                "rtm_connect_evidence", "rtm_connect_transitions",
                "rtm_connect_idempotency_claims",
            },
        )

    def test_schema_is_additive_and_non_destructive(self):
        source = (ROOT / "rtm_connect" / "schema.py").read_text(encoding="utf-8")
        upper = source.upper()
        for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM"):
            self.assertNotIn(forbidden, upper)
        self.assertIn("CREATE TABLE IF NOT EXISTS", upper)
        self.assertIn("CREATE INDEX IF NOT EXISTS", upper)

    def test_action_ledger_contains_frozen_contract_fields(self):
        columns = CONNECT_C1_REQUIRED_COLUMNS["rtm_connect_actions"]
        for field in (
            "payload_sha256", "idempotency_key", "risk_class",
            "requires_dual_control", "status", "status_version",
        ):
            self.assertIn(field, columns)

    def test_authorization_registry_contains_authority_and_approvers(self):
        columns = CONNECT_C1_REQUIRED_COLUMNS["rtm_connect_authorizations"]
        for field in (
            "authority_code", "authority_version", "decision", "frozen",
            "approved_by_operator_ids", "authorized_connector_modes",
            "authorization_version", "supersedes_id",
        ):
            self.assertIn(field, columns)

    def test_attempt_ledger_separates_failure_and_reconciliation(self):
        columns = CONNECT_C1_REQUIRED_COLUMNS["rtm_connect_attempts"]
        self.assertIn("failure_class", columns)
        self.assertIn("retryable", columns)
        self.assertIn("reconciliation_required", columns)

    def test_evidence_store_has_receipt_and_verification_fields(self):
        columns = CONNECT_C1_REQUIRED_COLUMNS["rtm_connect_evidence"]
        for field in (
            "sequence_number", "receipt_sha256", "receipt_storage_ref", "verified_at",
            "verification_method", "verified_by_operator_id",
        ):
            self.assertIn(field, columns)

    def test_state_guard_and_append_only_triggers_are_required(self):
        self.assertEqual(len(CONNECT_C1_REQUIRED_TRIGGERS), 4)
        self.assertIn("trg_rtm_connect_actions_state_guard", CONNECT_C1_REQUIRED_TRIGGERS)
        self.assertIn("trg_rtm_connect_evidence_append_only", CONNECT_C1_REQUIRED_TRIGGERS)

    def test_schema_requires_idempotency_indexes(self):
        self.assertIn("uq_rtm_connect_action_idempotency", CONNECT_C1_REQUIRED_INDEXES)
        self.assertIn("idx_rtm_connect_idempotency_action", CONNECT_C1_REQUIRED_INDEXES)
        self.assertIn("uq_rtm_connect_evidence_sequence", CONNECT_C1_REQUIRED_INDEXES)
        self.assertIn("uq_rtm_connect_transition_sequence", CONNECT_C1_REQUIRED_INDEXES)

    def test_constraints_cover_hashes_risk_and_evidence(self):
        for name in (
            "ck_rtm_connect_action_payload_sha256",
            "ck_rtm_connect_action_risk",
            "ck_rtm_connect_authorization_evidence",
            "ck_rtm_connect_evidence_level",
        ):
            self.assertIn(name, CONNECT_C1_REQUIRED_CONSTRAINTS)

    def test_ddl_names_are_unique(self):
        names = [name for name, _ in connect_c1_ddl()]
        self.assertEqual(len(names), len(set(names)))

    def test_unknown_transition_never_allows_direct_queue(self):
        source = (ROOT / "rtm_connect" / "schema.py").read_text(encoding="utf-8")
        unknown_section = source.split("WHEN 'unknown'", 1)[1].split("WHEN 'reconciling'", 1)[0]
        self.assertIn("'reconciling'", unknown_section)
        self.assertIn("'manual_review'", unknown_section)
        self.assertNotIn("'queued'", unknown_section)
        self.assertNotIn("'confirmed'", unknown_section)

    def test_no_connector_seed_is_present(self):
        source = (ROOT / "rtm_connect" / "schema.py").read_text(encoding="utf-8")
        self.assertNotIn("INSERT INTO rtm_connect_connectors", source)


if __name__ == "__main__":
    unittest.main()
