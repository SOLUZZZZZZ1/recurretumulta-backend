from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPOSITORY_ROOT
    / "scripts"
    / "rtm_staging_operator_access_schema.py"
)


class StagingOperatorAccessSchemaScriptTest(unittest.TestCase):
    def test_contract_covers_access_history_and_devices(self):
        sys.path.insert(0, str(REPOSITORY_ROOT))
        try:
            from scripts import rtm_staging_operator_access_schema as schema
        finally:
            sys.path.pop(0)

        self.assertEqual(
            schema.SCHEMA_VERSION,
            "rtm_staging_operator_access_schema_v1_0",
        )
        for table_name in (
            "rtm_operator_devices",
            "rtm_operator_access_events",
            "rtm_operator_access_evidence",
        ):
            self.assertIn(table_name, schema.ACCESS_REQUIRED_COLUMNS)
        for column in (
            "device_id",
            "login_access_event_id",
            "ip_source",
            "ip_trusted",
            "risk_flags",
        ):
            self.assertIn(
                column,
                schema.ACCESS_REQUIRED_COLUMNS["rtm_operator_sessions"],
            )
        self.assertIn(
            "trg_rtm_operator_access_events_append_only",
            schema.REQUIRED_TRIGGERS,
        )
        self.assertIn(
            "trg_rtm_operator_access_evidence_retention",
            schema.REQUIRED_TRIGGERS,
        )

    def test_registered_migration_is_additive(self):
        sys.path.insert(0, str(REPOSITORY_ROOT))
        try:
            from rtm_core.operator_access_schema import operator_access_v1_ddl
        finally:
            sys.path.pop(0)

        forbidden_statement = re.compile(
            r"(?im)^\s*(DROP|TRUNCATE|DELETE)\b"
        )
        for name, statement in operator_access_v1_ddl():
            with self.subTest(name=name):
                self.assertIsNone(forbidden_statement.search(statement))

    def test_access_history_is_append_only(self):
        sys.path.insert(0, str(REPOSITORY_ROOT))
        try:
            from rtm_core.operator_access_schema import operator_access_v1_ddl
        finally:
            sys.path.pop(0)

        ddl = "\n".join(
            statement for _, statement in operator_access_v1_ddl()
        )
        self.assertIn(
            "rtm_guard_operator_access_events_append_only",
            ddl,
        )
        self.assertIn(
            "BEFORE UPDATE OR DELETE ON rtm_operator_access_events",
            ddl,
        )

    def test_raw_ip_is_separated_and_retention_protected(self):
        sys.path.insert(0, str(REPOSITORY_ROOT))
        try:
            from rtm_core.operator_access_schema import operator_access_v1_ddl
        finally:
            sys.path.pop(0)

        ddl = dict(operator_access_v1_ddl())
        normalized = ddl["operator_access_events"]
        evidence = ddl["operator_access_evidence"]
        retention = ddl["operator_access_evidence_retention_function"]

        self.assertIn("ip_masked", normalized)
        self.assertIn("ip_hash_sha256", normalized)
        self.assertNotIn("ip_address INET", normalized)
        self.assertIn("ip_address INET", evidence)
        self.assertIn("retention_until", evidence)
        self.assertIn(
            "rtm.operator_access_evidence_purge",
            retention,
        )
        self.assertIn("retention-protected", retention)

    def test_device_contract_uses_opaque_hash_not_hardware_fingerprint(self):
        sys.path.insert(0, str(REPOSITORY_ROOT))
        try:
            from rtm_core.operator_access_schema import operator_access_v1_ddl
        finally:
            sys.path.pop(0)

        ddl = "\n".join(
            statement for _, statement in operator_access_v1_ddl()
        ).lower()
        self.assertIn("device_key_sha256", ddl)
        for forbidden in (
            "mac_address",
            "imei",
            "serial_number",
            "gps_latitude",
            "gps_longitude",
            "canvas_fingerprint",
        ):
            self.assertNotIn(forbidden, ddl)

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
                "STAGING_OPERATOR_ACCESS_SCHEMA_ONLY",
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
        self.assertIn(
            "RTM_ENV_must_be_staging",
            payload["blockers"],
        )

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
            [
                sys.executable,
                str(SCRIPT),
                "--apply",
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
        self.assertIn(
            "invalid_apply_confirmation",
            payload["blockers"],
        )

    def test_script_does_not_replace_current_login(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"login_replaced": False', source)
        self.assertIn('"raw_ip_separated": True', source)
        self.assertIn('"opaque_device_id_only": True', source)


if __name__ == "__main__":
    unittest.main()
