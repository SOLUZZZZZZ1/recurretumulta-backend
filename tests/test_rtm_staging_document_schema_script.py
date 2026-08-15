from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "rtm_staging_document_schema.py"


class StagingDocumentSchemaScriptTest(unittest.TestCase):
    def test_document_flow_contract_includes_contact_name_and_runtime_flags(self):
        sys.path.insert(0, str(REPOSITORY_ROOT))
        try:
            from scripts import rtm_staging_document_schema as schema
        finally:
            sys.path.pop(0)

        required = schema.REQUIRED_COLUMNS["cases"]
        self.assertIn("contact_name", required)
        self.assertIn("test_mode", required)
        self.assertIn("override_deadlines", required)
        ddl_names = {name for name, _ in schema.ADDITIVE_DDL}
        self.assertIn("cases.contact_name", ddl_names)

    def test_refuses_to_run_outside_staging_without_touching_database(self):
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
                "STAGING_SCHEMA_ONLY",
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
        payload = json.loads(completed.stderr)
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
        payload = json.loads(completed.stderr)
        self.assertIn("invalid_apply_confirmation", payload["blockers"])


if __name__ == "__main__":
    unittest.main()
