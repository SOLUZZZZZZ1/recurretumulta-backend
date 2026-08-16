from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "rtm_staging_core_schema.py"


class StagingCoreSchemaScriptTest(unittest.TestCase):
    def test_contract_covers_complete_authority_chain(self):
        sys.path.insert(0, str(REPOSITORY_ROOT))
        try:
            from scripts import rtm_staging_core_schema as schema
        finally:
            sys.path.pop(0)

        self.assertEqual(
            schema.SCHEMA_VERSION,
            "rtm_staging_core_schema_v1_0",
        )
        for table_name in (
            "rtm_validated_facts",
            "rtm_family_resolutions",
            "rtm_legal_previews",
            "rtm_generated_resources",
            "rtm_document_extractions",
        ):
            self.assertIn(table_name, schema.CORE_REQUIRED_COLUMNS)
        self.assertIn(
            "source_extraction_id",
            schema.CORE_REQUIRED_COLUMNS["rtm_validated_facts"],
        )

    def test_registered_migrations_are_additive(self):
        sys.path.insert(0, str(REPOSITORY_ROOT))
        try:
            from rtm_core.document_extraction_migration import (
                document_extraction_ddl,
            )
            from rtm_core.migration_router import authority_v1_ddl
        finally:
            sys.path.pop(0)

        # Solo se prohíben sentencias destructivas ejecutables. La cláusula
        # referencial ``ON DELETE CASCADE`` no borra nada durante la migración
        # y forma parte de la definición aditiva de las claves foráneas.
        forbidden_statement = re.compile(
            r"(?im)^\s*(DROP|TRUNCATE|DELETE)\b"
        )
        for name, statement in [
            *authority_v1_ddl(),
            *document_extraction_ddl(),
        ]:
            with self.subTest(name=name):
                self.assertIsNone(forbidden_statement.search(statement))

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
                "STAGING_CORE_SCHEMA_ONLY",
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
