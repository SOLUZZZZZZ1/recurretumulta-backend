from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "rtm_staging_management_schema.py"


class StagingManagementSchemaScriptTest(unittest.TestCase):
    def test_contract_covers_management_core(self):
        sys.path.insert(0, str(REPOSITORY_ROOT))
        try:
            from scripts import rtm_staging_management_schema as schema
        finally:
            sys.path.pop(0)

        self.assertEqual(
            schema.SCHEMA_VERSION,
            "rtm_staging_management_schema_v1_0",
        )
        for table_name in (
            "rtm_operators",
            "rtm_operator_roles",
            "rtm_operator_sessions",
            "rtm_work_assignments",
            "rtm_attention_items",
            "rtm_deadlines",
            "rtm_attention_events",
            "rtm_attention_engine_runs",
        ):
            self.assertIn(table_name, schema.MANAGEMENT_REQUIRED_COLUMNS)
        self.assertIn("origin_status", schema.MANAGEMENT_REQUIRED_COLUMNS["rtm_deadlines"])
        self.assertIn("source_document_id", schema.MANAGEMENT_REQUIRED_COLUMNS["rtm_deadlines"])
        self.assertIn("trg_rtm_attention_events_append_only", schema.REQUIRED_TRIGGERS)

    def test_registered_migration_is_additive(self):
        sys.path.insert(0, str(REPOSITORY_ROOT))
        try:
            from rtm_core.management_schema import management_v1_ddl
        finally:
            sys.path.pop(0)

        forbidden_statement = re.compile(r"(?im)^\s*(DROP|TRUNCATE|DELETE)\b")
        for name, statement in management_v1_ddl():
            with self.subTest(name=name):
                self.assertIsNone(forbidden_statement.search(statement))

    def test_deadline_contract_forbids_silent_now_fallback(self):
        sys.path.insert(0, str(REPOSITORY_ROOT))
        try:
            from rtm_core.management_schema import management_v1_ddl
        finally:
            sys.path.pop(0)

        ddl = "\n".join(statement for _, statement in management_v1_ddl())
        self.assertIn("ck_rtm_deadline_missing_origin", ddl)
        self.assertIn("origin_status <> 'missing'", ddl)
        self.assertIn("origin_at IS NULL AND due_at IS NULL", ddl)
        self.assertIn("ck_rtm_deadline_due_has_authority", ddl)
        self.assertIn("origin_at IS NOT NULL", ddl)
        self.assertIn("rule_code IS NOT NULL", ddl)

    def test_attention_audit_is_append_only(self):
        sys.path.insert(0, str(REPOSITORY_ROOT))
        try:
            from rtm_core.management_schema import management_v1_ddl
        finally:
            sys.path.pop(0)

        ddl = "\n".join(statement for _, statement in management_v1_ddl())
        self.assertIn("rtm_guard_attention_events_append_only", ddl)
        self.assertIn("BEFORE UPDATE OR DELETE ON rtm_attention_events", ddl)

    def test_refuses_to_run_outside_staging_before_database_access(self):
        env = dict(os.environ)
        env.update(
            {
                "RTM_ENV": "production",
                "RTM_DATA_NAMESPACE": "rtm_production",
                "RTM_SIDE_EFFECT_POLICY": "live",
                "RTM_ALLOW_REAL_CUSTOMER_DATA": "1",
            }
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--apply",
                "--confirmation",
                "STAGING_MANAGEMENT_SCHEMA_ONLY",
                "--compact",
            ],
            cwd=REPOSITORY_ROOT,
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
        self.assertIn("RTM_ENV_must_be_staging", payload["blockers"])

    def test_apply_requires_literal_confirmation(self):
        env = dict(os.environ)
        env.update(
            {
                "RTM_ENV": "staging",
                "RTM_DATA_NAMESPACE": "rtm_staging",
                "RTM_SIDE_EFFECT_POLICY": "isolated",
                "RTM_ALLOW_REAL_CUSTOMER_DATA": "0",
            }
        )
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--apply", "--compact"],
            cwd=REPOSITORY_ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stdout)
        self.assertIn("invalid_apply_confirmation", payload["blockers"])


if __name__ == "__main__":
    unittest.main()
