from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rtm_staging_presenter_schema.py"


def _import_script():
    sys.path.insert(0, str(ROOT))
    try:
        from scripts import rtm_staging_presenter_schema as schema
    finally:
        sys.path.pop(0)
    return schema


class _IdentityRows:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def one(self):
        return self._row


class _IdentityConnection:
    def __init__(self, **overrides):
        self.row = {
            "database_name": "rtm_staging",
            "current_role": "rtm_staging_role",
            "session_role": "rtm_staging_role",
            "explicit_schemas": ["public"],
            "effective_schemas": ["pg_catalog", "public"],
            "temp_schema_oid": 0,
        }
        self.row.update(overrides)

    def exec_driver_sql(self, statement):
        self.statement = statement
        return _IdentityRows(self.row)


class StagingPresenterSchemaScriptTest(unittest.TestCase):
    def test_contract_covers_complete_presenter_schema(self):
        script = _import_script()
        import rtm_presenter_schema as presenter

        contract = script.schema_contract()
        self.assertEqual(
            script.PRESENTER_SCHEMA_SCRIPT_VERSION,
            "rtm_staging_presenter_schema_v1_0",
        )
        self.assertEqual(
            contract["schema_version"],
            presenter.RTM_PRESENTER_SCHEMA_VERSION,
        )
        self.assertRegex(contract["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            contract["ddl_count"], len(presenter.rtm_presenter_schema_ddl())
        )
        self.assertEqual(
            set(script.BASE_REQUIRED_COLUMNS),
            {
                "rtm_management_schema_migrations",
                "cases",
                "documents",
                "rtm_operator_roles",
                "rtm_operators",
                "rtm_operator_sessions",
                "rtm_operator_access_events",
            },
        )

    def test_presenter_ddl_is_additive_and_seed_free(self):
        import rtm_presenter_schema as presenter

        forbidden = re.compile(
            r"(?im)^\s*(?:DROP|TRUNCATE|DELETE|INSERT|UPDATE)\b"
        )
        for name, statement in presenter.rtm_presenter_schema_ddl():
            with self.subTest(name=name):
                self.assertIsNone(forbidden.search(statement))

        source = SCRIPT.read_text(encoding="utf-8")
        insert_targets = re.findall(
            r"INSERT\s+INTO\s+([a-zA-Z0-9_]+)", source, re.IGNORECASE
        )
        self.assertEqual(insert_targets, ["rtm_management_schema_migrations"])
        for forbidden_import in ("b2_storage", "boto3", "get_s3_client"):
            self.assertNotIn(forbidden_import, source)
        for assertion in (
            '"profiles_seeded": False',
            '"documents_seeded": False',
            '"b2_used": False',
            '"external_effects": False',
        ):
            self.assertIn(assertion, source)

    def test_boundary_requires_isolated_synthetic_no_storage_contract(self):
        script = _import_script()
        values = {
            "RTM_ENV": "staging",
            "RTM_DATA_NAMESPACE": "rtm_staging",
            "RTM_SIDE_EFFECT_POLICY": "isolated",
            "RTM_ALLOW_REAL_CUSTOMER_DATA": "false",
            "RTM_PRESENTER_SYNTHETIC_ONLY": "true",
            "RTM_PRESENTER_EXTERNAL_EFFECTS_ALLOWED": "false",
            "RTM_PRESENTER_DIRECT_STORAGE_ALLOWED": "false",
            "DATABASE_URL": "postgresql://rtm_staging_role@db/rtm_staging",
        }
        self.assertEqual(script.safety_blockers(values=values), [])
        for name in (
            "RTM_ALLOW_REAL_CUSTOMER_DATA",
            "RTM_PRESENTER_SYNTHETIC_ONLY",
            "RTM_PRESENTER_EXTERNAL_EFFECTS_ALLOWED",
            "RTM_PRESENTER_DIRECT_STORAGE_ALLOWED",
        ):
            broken = dict(values)
            broken[name] = "true" if values[name] == "false" else "false"
            with self.subTest(name=name):
                self.assertTrue(script.safety_blockers(values=broken))

    def test_apply_requires_literal_confirmation_before_database_access(self):
        env = dict(os.environ)
        env.update(
            {
                "RTM_ENV": "staging",
                "RTM_DATA_NAMESPACE": "rtm_staging",
                "RTM_SIDE_EFFECT_POLICY": "isolated",
                "RTM_ALLOW_REAL_CUSTOMER_DATA": "false",
                "RTM_PRESENTER_SYNTHETIC_ONLY": "true",
                "RTM_PRESENTER_EXTERNAL_EFFECTS_ALLOWED": "false",
                "RTM_PRESENTER_DIRECT_STORAGE_ALLOWED": "false",
                "DATABASE_URL": "postgresql://rtm_staging_role@db/rtm_staging",
            }
        )
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--apply", "--compact"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stdout)
        self.assertIn("invalid_apply_confirmation", payload["blockers"])
        self.assertFalse(payload["database_connection_used"])

    def test_refuses_production_before_database_access(self):
        env = dict(os.environ)
        env.update(
            {
                "RTM_ENV": "production",
                "RTM_DATA_NAMESPACE": "rtm_production",
                "RTM_SIDE_EFFECT_POLICY": "live",
                "RTM_ALLOW_REAL_CUSTOMER_DATA": "true",
                "RTM_PRESENTER_SYNTHETIC_ONLY": "false",
                "RTM_PRESENTER_EXTERNAL_EFFECTS_ALLOWED": "true",
                "RTM_PRESENTER_DIRECT_STORAGE_ALLOWED": "true",
                "DATABASE_URL": "postgresql://rtm@db/rtm_production",
            }
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--apply",
                "--confirmation",
                "STAGING_PRESENTER_SCHEMA_ONLY",
                "--compact",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stdout)
        self.assertIn("RTM_ENV_must_be_staging", payload["blockers"])
        self.assertFalse(payload["database_connection_used"])
        self.assertFalse(payload["schema_changes_applied"])

    def test_database_identity_is_exact_and_fail_closed(self):
        script = _import_script()
        connection = _IdentityConnection()
        self.assertEqual(
            script.assert_database_identity(
                connection,
                expected_database_name="rtm_staging",
                expected_database_role="rtm_staging_role",
            ),
            "rtm_staging",
        )
        for override in (
            {"database_name": "rtm_production"},
            {"current_role": "wrong-role"},
            {"session_role": "wrong-role"},
            {"explicit_schemas": ["public", "unsafe"]},
            {"temp_schema_oid": 123},
        ):
            with self.subTest(override=override):
                with self.assertRaises(script.PresenterSchemaMigrationError):
                    script.assert_database_identity(
                        _IdentityConnection(**override),
                        expected_database_name="rtm_staging",
                        expected_database_role="rtm_staging_role",
                    )

    def test_apply_rejects_substituted_contract_before_any_ddl(self):
        script = _import_script()
        substituted = dict(script.schema_contract())
        substituted["sha256"] = "0" * 64

        class _NoDDLConnection:
            @staticmethod
            def exec_driver_sql(*args, **kwargs):
                raise AssertionError("No DDL may run for a substituted contract")

        with self.assertRaises(script.PresenterSchemaMigrationError):
            script.apply_schema(_NoDDLConnection(), contract=substituted)

    def test_main_applies_inside_engine_transaction_after_identity_check(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("with engine.begin() as conn:", source)
        identity_position = source.index("assert_database_identity(\n", source.index("def main"))
        apply_position = source.index("apply_schema(conn, contract=contract)")
        self.assertLess(identity_position, apply_position)
        self.assertIn("_non_synthetic_case_count(conn)", source)
        self.assertIn("non_synthetic_cases_present", source)
        self.assertIn(
            "presenter_schema_contract_not_ready_after_apply", source
        )


if __name__ == "__main__":
    unittest.main()
