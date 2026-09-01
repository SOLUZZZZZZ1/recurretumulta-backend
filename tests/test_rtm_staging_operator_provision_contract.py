from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PROVISION = ROOT / "scripts" / "rtm_staging_operator_provision.py"
PREFLIGHT = ROOT / "scripts" / "rtm_staging_operator_activation_preflight.py"
DISABLE = ROOT / "scripts" / "rtm_staging_operator_disable.py"
CORE = ROOT / "rtm_core" / "operator_provisioning.py"


class StagingOperatorProvisionContractTest(unittest.TestCase):
    def test_provision_requires_literal_confirmation(self):
        source = PROVISION.read_text(encoding="utf-8")
        self.assertIn("STAGING_SYNTHETIC_OPERATOR_ONLY", source)
        self.assertIn("invalid_provision_confirmation", source)

    def test_provision_requires_auth_feature_to_remain_disabled(self):
        source = PROVISION.read_text(encoding="utf-8")
        self.assertIn(
            "operator_auth_must_be_disabled_during_provisioning",
            source,
        )

    def test_password_is_never_in_json_report(self):
        source = PROVISION.read_text(encoding="utf-8")
        self.assertIn("file=sys.stderr", source)
        self.assertNotRegex(source, r'report\[[^\]]*password[^\]]*\]\s*=\s*password')
        self.assertIn("NO LA PEGUES EN EL CHAT", source)

    def test_core_uses_argon2id_and_synthetic_profile(self):
        source = CORE.read_text(encoding="utf-8")
        self.assertIn("hash_operator_password(password)", source)
        self.assertIn('"synthetic": True', source)
        self.assertIn('"environment": "staging"', source)
        self.assertIn("'argon2id'", source)

    def test_no_destructive_sql_is_registered(self):
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (CORE, PROVISION, PREFLIGHT, DISABLE)
        )
        forbidden = re.compile(r"(?im)^\s*(DROP|TRUNCATE|DELETE)\b")
        self.assertIsNone(forbidden.search(source))

    def test_disable_preserves_history_and_only_revokes(self):
        source = DISABLE.read_text(encoding="utf-8")
        self.assertIn('"history_preserved": True', source)
        self.assertIn("STAGING_SYNTHETIC_OPERATOR_DISABLE_ONLY", source)
        core = CORE.read_text(encoding="utf-8")
        self.assertIn("status='revoked'", core)
        self.assertIn("status='disabled'", core)

    def test_preflight_is_read_only_and_checks_legacy_login(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        self.assertIn('"read_only": True', source)
        self.assertIn('"/ops/login" in paths', source)
        self.assertIn("ready_for_activation", source)

    def test_preflight_queries_every_defined_role_including_signer(self):
        from scripts import rtm_staging_operator_activation_preflight as preflight

        sqlalchemy = ModuleType("sqlalchemy")
        statement = mock.Mock()
        statement.bindparams.return_value = statement
        sqlalchemy.bindparam = mock.Mock(return_value=mock.sentinel.role_codes)
        sqlalchemy.text = mock.Mock(return_value=statement)
        role_definitions = {
            "operator": SimpleNamespace(code="rtm.operator"),
            "supervisor": SimpleNamespace(code="rtm.supervisor"),
            "signer": SimpleNamespace(code="rtm.signer"),
        }
        conn = mock.Mock()
        conn.execute.return_value.mappings.return_value.fetchall.return_value = []
        with mock.patch.dict(sys.modules, {"sqlalchemy": sqlalchemy}):
            rows, expected_codes = preflight._load_defined_role_rows(
                conn,
                role_definitions,
            )

        self.assertEqual(rows, [])
        self.assertIn("rtm.signer", expected_codes)
        conn.execute.assert_called_once_with(
            statement,
            {"role_codes": sorted(expected_codes)},
        )
        sqlalchemy.bindparam.assert_called_once_with(
            "role_codes",
            expanding=True,
        )
        self.assertIn(
            "WHERE code IN :role_codes",
            sqlalchemy.text.call_args.args[0],
        )
        self.assertIn('code="rtm.signer"', CORE.read_text(encoding="utf-8"))

    def test_provision_refuses_outside_staging_before_database_access(self):
        env = dict(os.environ)
        env.update(
            {
                "RTM_ENV": "production",
                "RTM_DATA_NAMESPACE": "rtm_production",
                "RTM_SIDE_EFFECT_POLICY": "live",
                "RTM_ALLOW_REAL_CUSTOMER_DATA": "1",
                "RTM_ENABLE_OPERATOR_AUTH_V1": "0",
            }
        )
        process = subprocess.run(
            [
                sys.executable,
                str(PROVISION),
                "--generate-password",
                "--confirmation",
                "STAGING_SYNTHETIC_OPERATOR_ONLY",
                "--compact",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(process.returncode, 2)
        payload = json.loads(process.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("RTM_ENV_must_be_staging", payload["blockers"])
        self.assertNotIn("ModuleNotFoundError", process.stderr)

    def test_disable_refuses_without_confirmation_before_database_access(self):
        env = dict(os.environ)
        env["RTM_ENV"] = "staging"
        process = subprocess.run(
            [sys.executable, str(DISABLE), "--compact"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(process.returncode, 2)
        payload = json.loads(process.stdout)
        self.assertIn("invalid_disable_confirmation", payload["blockers"])


if __name__ == "__main__":
    unittest.main()
