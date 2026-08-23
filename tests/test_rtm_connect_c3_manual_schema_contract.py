from __future__ import annotations

import unittest
from pathlib import Path

from rtm_connect.manual_schema import (
    CONNECT_C3_REQUIRED_COLUMNS,
    CONNECT_C3_REQUIRED_CONSTRAINTS,
    CONNECT_C3_REQUIRED_INDEXES,
    CONNECT_C3_REQUIRED_TRIGGERS,
    connect_c3_manual_ddl,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "rtm_connect" / "manual_schema.py"


class ConnectC3ManualSchemaContractTest(unittest.TestCase):
    def test_schema_declares_two_tables(self):
        self.assertEqual(
            set(CONNECT_C3_REQUIRED_COLUMNS),
            {"rtm_connect_manual_tasks", "rtm_connect_manual_events"},
        )

    def test_schema_is_additive(self):
        source = SOURCE.read_text(encoding="utf-8")
        for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM", "DROP COLUMN"):
            self.assertNotIn(forbidden, source)

    def test_tasks_reference_actions(self):
        self.assertIn(
            "REFERENCES rtm_connect_actions(id)",
            SOURCE.read_text(encoding="utf-8"),
        )

    def test_tasks_reference_attempts(self):
        self.assertIn(
            "REFERENCES rtm_connect_attempts(id)",
            SOURCE.read_text(encoding="utf-8"),
        )

    def test_tasks_reference_connectors(self):
        self.assertIn(
            "REFERENCES rtm_connect_connectors(id)",
            SOURCE.read_text(encoding="utf-8"),
        )

    def test_statuses_are_explicit(self):
        source = SOURCE.read_text(encoding="utf-8")
        for status in (
            "prepared", "assigned", "in_progress", "awaiting_receipt",
            "receipt_submitted", "verified", "completed",
        ):
            self.assertIn(f"'{status}'", source)

    def test_package_hash_constraint_exists(self):
        self.assertIn(
            "ck_rtm_connect_manual_task_package_sha256",
            CONNECT_C3_REQUIRED_CONSTRAINTS,
        )

    def test_one_task_per_action(self):
        self.assertIn(
            "uq_rtm_connect_manual_task_action",
            CONNECT_C3_REQUIRED_INDEXES,
        )

    def test_one_task_per_attempt(self):
        self.assertIn(
            "uq_rtm_connect_manual_task_attempt",
            CONNECT_C3_REQUIRED_INDEXES,
        )

    def test_state_guard_trigger_exists(self):
        self.assertIn(
            "trg_rtm_connect_manual_task_state_guard",
            CONNECT_C3_REQUIRED_TRIGGERS,
        )

    def test_package_frozen_trigger_exists(self):
        self.assertIn(
            "trg_rtm_connect_manual_task_package_frozen",
            CONNECT_C3_REQUIRED_TRIGGERS,
        )

    def test_events_are_append_only_and_unseeded(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("BEFORE UPDATE OR DELETE", source)
        rendered = "\n".join(s for _, s in connect_c3_manual_ddl())
        self.assertNotIn("INSERT INTO rtm_connect_connectors", rendered)


if __name__ == "__main__":
    unittest.main()
