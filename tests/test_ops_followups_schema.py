from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ops_followups_schema import (
    REQUIRED_COLUMNS,
    REQUIRED_INDEXES,
    SCHEMA_VERSION,
    ops_followups_ddl,
)
from scripts import rtm_staging_ops_followups_schema as staging_schema


class OpsFollowupsSchemaTest(unittest.TestCase):
    def test_schema_covers_every_column_used_by_ops(self):
        self.assertEqual(SCHEMA_VERSION, "rtm_ops_followups_schema_v1_0")
        self.assertEqual(
            REQUIRED_COLUMNS,
            {
                "id",
                "case_id",
                "kind",
                "status",
                "title",
                "description",
                "due_at",
                "source_event_type",
                "created_by",
                "resolved_at",
                "resolved_by",
                "resolution_note",
                "created_at",
                "updated_at",
            },
        )

    def test_migration_is_additive_and_idempotent(self):
        ddl = "\n".join(statement for _, statement in ops_followups_ddl())
        self.assertIn("CREATE TABLE IF NOT EXISTS ops_followups", ddl)
        self.assertIn("REFERENCES cases(id) ON DELETE CASCADE", ddl)
        for index in REQUIRED_INDEXES:
            self.assertIn(f"CREATE INDEX IF NOT EXISTS {index}", ddl)
        self.assertIsNone(re.search(r"(?im)^\s*(DROP|TRUNCATE|DELETE)\b", ddl))

    def test_status_contract_is_limited_to_pending_and_resolved(self):
        ddl = "\n".join(statement for _, statement in ops_followups_ddl())
        self.assertIn("CHECK (status IN ('pending', 'resolved'))", ddl)
        self.assertIn("WHERE status = 'pending'", ddl)

    def test_apply_requires_literal_confirmation(self):
        args = argparse.Namespace(apply=True, confirmation="incorrect")
        safe_env = {
            "RTM_ENV": "staging",
            "RTM_DATA_NAMESPACE": "rtm_staging",
            "RTM_SIDE_EFFECT_POLICY": "isolated",
            "RTM_ALLOW_REAL_CUSTOMER_DATA": "0",
        }
        with patch.dict(os.environ, safe_env, clear=True):
            blockers = staging_schema._safety_blockers(args)
        self.assertEqual(blockers, ["invalid_apply_confirmation"])

    def test_script_refuses_production_before_database_access(self):
        args = argparse.Namespace(
            apply=True,
            confirmation=staging_schema.APPLY_CONFIRMATION,
        )
        production_env = {
            "RTM_ENV": "production",
            "RTM_DATA_NAMESPACE": "rtm_production",
            "RTM_SIDE_EFFECT_POLICY": "live",
            "RTM_ALLOW_REAL_CUSTOMER_DATA": "1",
        }
        with patch.dict(os.environ, production_env, clear=True):
            blockers = staging_schema._safety_blockers(args)
        self.assertIn("RTM_ENV_must_be_staging", blockers)
        self.assertIn("RTM_DATA_NAMESPACE_must_identify_staging", blockers)
        self.assertIn("RTM_SIDE_EFFECT_POLICY_must_be_isolated", blockers)
        self.assertIn("RTM_ALLOW_REAL_CUSTOMER_DATA_must_be_false", blockers)

    def test_env_file_loads_missing_values_without_overwriting_process(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "# comentario\n"
                "RTM_ENV=staging\n"
                "RTM_DATA_NAMESPACE='rtm_staging'\n"
                'DATABASE_URL="postgresql://example.invalid/db"\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"RTM_ENV": "already-set"}, clear=True):
                loaded = staging_schema._load_env_file(str(env_file))
                self.assertEqual(loaded, 2)
                self.assertEqual(os.environ["RTM_ENV"], "already-set")
                self.assertEqual(os.environ["RTM_DATA_NAMESPACE"], "rtm_staging")
                self.assertEqual(
                    os.environ["DATABASE_URL"],
                    "postgresql://example.invalid/db",
                )

    def test_report_contract_never_contains_environment_values(self):
        source = Path(staging_schema.__file__).read_text(encoding="utf-8")
        self.assertNotIn('report["database_url"]', source.lower())
        report = {
            "authority": "rtm_staging_ops_followups_schema",
            "version": staging_schema.SCHEMA_VERSION,
        }
        self.assertNotIn("DATABASE_URL", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
