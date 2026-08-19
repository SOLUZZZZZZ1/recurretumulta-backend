from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "rtm_staging_operator_auth_schema.py"


class StagingOperatorAuthSchemaScriptTest(unittest.TestCase):
    def test_contract_covers_operator_and_session_auth(self):
        sys.path.insert(0, str(REPOSITORY_ROOT))
        try:
            from scripts import rtm_staging_operator_auth_schema as schema
        finally:
            sys.path.pop(0)
        self.assertEqual(
            schema.SCHEMA_VERSION,
            "rtm_staging_operator_auth_schema_v1_0",
        )
        self.assertIn("auth_epoch", schema.AUTH_REQUIRED_COLUMNS["rtm_operators"])
        self.assertIn(
            "absolute_expires_at",
            schema.AUTH_REQUIRED_COLUMNS["rtm_operator_sessions"],
        )
        self.assertIn(
            "ck_rtm_operator_password_algorithm",
            schema.REQUIRED_CONSTRAINTS,
        )

    def test_registered_migration_is_additive(self):
        sys.path.insert(0, str(REPOSITORY_ROOT))
        try:
            from rtm_core.operator_auth_schema import operator_auth_v1_ddl
        finally:
            sys.path.pop(0)
        forbidden = re.compile(r"(?im)^\s*(DROP|TRUNCATE|DELETE)\b")
        for name, statement in operator_auth_v1_ddl():
            with self.subTest(name=name):
                self.assertIsNone(forbidden.search(statement))

    def test_lockout_and_auth_epoch_are_explicit(self):
        sys.path.insert(0, str(REPOSITORY_ROOT))
        try:
            from rtm_core.operator_auth_schema import operator_auth_v1_ddl
        finally:
            sys.path.pop(0)
        ddl = "\n".join(statement for _, statement in operator_auth_v1_ddl())
        for value in (
            "failed_login_count",
            "locked_until",
            "password_algorithm",
            "password_version",
            "auth_epoch",
            "absolute_expires_at",
        ):
            self.assertIn(value, ddl)

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
                "STAGING_OPERATOR_AUTH_SCHEMA_ONLY",
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

    def test_script_keeps_current_login_and_routes_untouched(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"login_replaced": False', source)
        self.assertIn('"routes_published": False', source)
        self.assertIn('"operators_created": False', source)

    def test_repository_never_inserts_raw_session_token(self):
        source = (
            REPOSITORY_ROOT / "rtm_core" / "operator_auth_repository.py"
        ).read_text(encoding="utf-8")
        self.assertIn("hash_session_token(raw_token)", source)
        self.assertIn("token_sha256", source)
        self.assertNotIn("raw_token TEXT", source)


if __name__ == "__main__":
    unittest.main()
