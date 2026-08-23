from __future__ import annotations

import unittest
from pathlib import Path

from rtm_connect.webhook_schema import (
    CONNECT_C4_REQUIRED_COLUMNS,
    CONNECT_C4_REQUIRED_CONSTRAINTS,
    CONNECT_C4_REQUIRED_INDEXES,
    CONNECT_C4_REQUIRED_TRIGGERS,
    RECONCILIATION_RESOLUTIONS,
    WEBHOOK_INBOX_STATUSES,
    connect_c4_webhook_ddl,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "rtm_connect" / "webhook_schema.py"


class ConnectC4WebhookSchemaContractTest(unittest.TestCase):
    def test_schema_declares_four_tables(self):
        self.assertEqual(
            set(CONNECT_C4_REQUIRED_COLUMNS),
            {
                "rtm_connect_webhook_inbox",
                "rtm_connect_webhook_events",
                "rtm_connect_reconciliations",
                "rtm_connect_reconciliation_events",
            },
        )

    def test_schema_is_additive(self):
        source = SOURCE.read_text(encoding="utf-8").upper()
        for forbidden in (
            "DROP TABLE",
            "TRUNCATE",
            "DELETE FROM",
            "DROP COLUMN",
        ):
            self.assertNotIn(forbidden, source)

    def test_inbox_references_ingress_connector(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn(
            "REFERENCES rtm_connect_connectors(id)",
            source,
        )

    def test_exact_event_deduplication_is_unique(self):
        self.assertIn(
            "uq_rtm_connect_webhook_deduplication",
            CONNECT_C4_REQUIRED_INDEXES,
        )
        self.assertIn(
            "uq_rtm_connect_webhook_source_event",
            CONNECT_C4_REQUIRED_INDEXES,
        )

    def test_reconciliation_is_unique_per_webhook(self):
        self.assertIn(
            "uq_rtm_connect_reconciliation_webhook",
            CONNECT_C4_REQUIRED_INDEXES,
        )

    def test_webhook_states_and_dlq_are_explicit(self):
        self.assertEqual(
            set(WEBHOOK_INBOX_STATUSES),
            {
                "received",
                "verified",
                "matched",
                "processed",
                "dead_lettered",
            },
        )
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("dead_letter_reason_code", source)
        self.assertIn("ck_rtm_connect_webhook_dead_lettered", source)

    def test_reconciliation_outcomes_are_frozen(self):
        self.assertEqual(
            set(RECONCILIATION_RESOLUTIONS),
            {
                "confirmed",
                "retryable_failed",
                "unknown",
                "manual_review",
                "permanent_failed",
            },
        )

    def test_confirmed_requires_receipt_and_evidence_constraints(self):
        self.assertIn(
            "ck_rtm_connect_webhook_confirmed_receipt",
            CONNECT_C4_REQUIRED_CONSTRAINTS,
        )
        self.assertIn(
            "ck_rtm_connect_reconciliation_confirmed_evidence",
            CONNECT_C4_REQUIRED_CONSTRAINTS,
        )

    def test_identity_freeze_guards_exist(self):
        self.assertIn(
            "trg_rtm_connect_webhook_identity_frozen",
            CONNECT_C4_REQUIRED_TRIGGERS,
        )
        self.assertIn(
            "trg_rtm_connect_reconciliation_identity_frozen",
            CONNECT_C4_REQUIRED_TRIGGERS,
        )

    def test_both_event_ledgers_are_append_only(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn(
            "trg_rtm_connect_webhook_events_append_only",
            CONNECT_C4_REQUIRED_TRIGGERS,
        )
        self.assertIn(
            "trg_rtm_connect_reconciliation_events_append_only",
            CONNECT_C4_REQUIRED_TRIGGERS,
        )
        self.assertGreaterEqual(source.count("BEFORE UPDATE OR DELETE"), 2)

    def test_parent_and_event_scope_guards_are_database_enforced(self):
        source = SOURCE.read_text(encoding="utf-8")
        for trigger in (
            "trg_rtm_connect_webhook_match_scope_guard",
            "trg_rtm_connect_webhook_event_scope_guard",
            "trg_rtm_connect_reconciliation_event_scope_guard",
        ):
            self.assertIn(trigger, CONNECT_C4_REQUIRED_TRIGGERS)
        for exact_guard in (
            "reconciliation scope is not an exact match",
            "confirmed reconciliation requires exact E4",
            "reconciliation resolution differs from CORE scope",
            "reconciliation event differs from parent scope",
            "NEW.resolution = w.reported_outcome",
            "e.receipt_sha256 = w.receipt_sha256",
            "e.receipt_storage_ref = w.receipt_storage_ref",
            "c.environment = 'staging'",
            "c.synthetic_only = TRUE",
            "c.credential_ref IS NULL",
        ):
            self.assertIn(exact_guard, source)
        self.assertGreaterEqual(source.count("tgrelid ="), 9)

    def test_schema_does_not_seed_a_connector_or_publish_runtime(self):
        rendered = "\n".join(statement for _, statement in connect_c4_webhook_ddl())
        self.assertNotIn("INSERT INTO rtm_connect_connectors", rendered)
        self.assertNotIn("APIRouter", rendered)


if __name__ == "__main__":
    unittest.main()
