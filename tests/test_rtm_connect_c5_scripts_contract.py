from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "scripts" / "rtm_staging_connect_c5_schema.py"
PREFLIGHT = ROOT / "scripts" / "rtm_connect_c5_preflight.py"
SMOKE = ROOT / "scripts" / "rtm_connect_c5_smoke.py"
REPOSITORY = ROOT / "rtm_connect" / "supervisor_repository.py"


def _sql_text_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    statements: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "text":
            continue
        value = node.args[0]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            statements.append(value.value)
    return statements


class ConnectC5ScriptsContractTest(unittest.TestCase):
    def test_required_scripts_exist(self):
        for path in (SCHEMA, PREFLIGHT, SMOKE):
            self.assertTrue(path.exists(), path.name)

    def test_schema_audit_is_read_only_without_apply_or_migration(self):
        source = SCHEMA.read_text(encoding="utf-8")
        self.assertIn('"read_only": True', source)
        self.assertIn('"apply_available": False', source)
        self.assertIn('"schema_changes_required": False', source)
        self.assertNotIn('add_argument("--apply"', source)
        self.assertNotIn("INSERT INTO rtm_management_schema_migrations", source)
        self.assertNotIn("connect_c5_supervisor_ddl()", source.split("def schema_snapshot", 1)[0])
        self.assertIn("unexpected_connect_c5_migration_registered", source)
        for trigger_guard in (
            "pg_get_triggerdef",
            "t.tgenabled",
            "p.proname",
            "definition_valid",
        ):
            self.assertIn(trigger_guard, source)

    def test_preflight_declares_read_only_projection(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        for declaration in (
            '"read_only": True',
            '"synthetic_only": True',
            '"schema_changes_required": False',
            '"execution_runtime_published": False',
            '"network_used": False',
            '"external_effects_executed": False',
        ):
            self.assertIn(declaration, source)
        self.assertNotIn('add_argument("--apply"', source)
        self.assertIn("supervisor_routes_get_only", source)
        self.assertIn("supervisor_openapi_hidden", source)
        self.assertIn("architecture_manifest", source)
        self.assertIn("execution_runtime_unpublished", source)
        self.assertIn("assert_connect_supervisor_database_identity", source)
        self.assertIn("synthetic_connector_scope_clean", source)
        self.assertIn("active_supervisor_present", source)

    def test_smoke_is_synthetic_transactional_and_rolls_back(self):
        if not SMOKE.exists():
            self.fail(f"missing required script: {SMOKE.name}")
        source = SMOKE.read_text(encoding="utf-8")
        for declaration in (
            '"synthetic_only": True',
            '"transactional": True',
            '"network_used": False',
            '"schema_changes_applied": False',
            '"external_effects_executed": False',
        ):
            self.assertIn(declaration, source)
        self.assertIn("transaction.rollback()", source)
        self.assertIn("rollback_removed_synthetic_records", source)
        self.assertIn("all_connect_ledgers_unchanged_by_http_gets", source)
        self.assertIn("every_successful_read_audited_individually", source)
        self.assertIn('"business_operations_read_only": True', source)
        self.assertIn("assert_connect_supervisor_database_identity", source)
        self.assertIn('"socket.create_connection"', source)
        self.assertIn('"socket.socket.connect"', source)

    def test_sqlalchemy_json_booleans_are_bound_not_embedded(self):
        preflight = PREFLIGHT.read_text(encoding="utf-8")
        smoke = SMOKE.read_text(encoding="utf-8")
        for source in (preflight, smoke):
            self.assertNotIn('":true', source)
        self.assertIn("CAST(:synthetic_profile AS JSONB)", preflight)
        self.assertIn("CAST(:profile AS JSONB)", smoke)

    def test_real_sqlalchemy_parser_has_no_true_phantom_bind(self):
        try:
            from sqlalchemy import text as sqlalchemy_text
        except ModuleNotFoundError:
            self.skipTest("SQLAlchemy no esta instalado en este runner")

        checked = 0
        for path in (PREFLIGHT, SMOKE, REPOSITORY):
            for statement in _sql_text_literals(path):
                bind_names = set(sqlalchemy_text(statement)._bindparams)
                self.assertNotIn("true", bind_names, f"{path.name}: {statement}")
                checked += 1
        self.assertGreaterEqual(checked, 10)

    def test_all_scripts_reuse_full_staging_barriers(self):
        for path in (SCHEMA, PREFLIGHT, SMOKE):
            if not path.exists():
                with self.subTest(script=path.name):
                    self.fail(f"missing required script: {path.name}")
                continue
            source = path.read_text(encoding="utf-8")
            if path == PREFLIGHT:
                self.assertIn(
                    "from scripts.rtm_staging_connect_c5_schema import safety_blockers",
                    source,
                )
                self.assertIn('report["blockers"].extend(safety_blockers())', source)
                self.assertLess(
                    source.index("safety_blockers()"),
                    source.index("from sqlalchemy import text"),
                )
                continue
            for required in (
                "RTM_ENV_must_be_staging",
                "assert_connect_supervisor_staging_boundary",
            ):
                self.assertIn(required, source, path.name)

    def test_scripts_refuse_production_before_database_access(self):
        env = dict(os.environ)
        env["RTM_ENV"] = "production"
        for path in (SCHEMA, PREFLIGHT, SMOKE):
            with self.subTest(script=path.name):
                if not path.exists():
                    self.fail(f"missing required script: {path.name}")
                    continue
                process = subprocess.run(
                    [sys.executable, str(path), "--compact"],
                    cwd=ROOT,
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(process.returncode, 2, process.stderr)
                payload = json.loads(process.stdout)
                self.assertIn(
                    "RTM_ENV_must_be_staging",
                    payload["blockers"],
                )


if __name__ == "__main__":
    unittest.main()
