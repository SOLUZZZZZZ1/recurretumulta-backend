from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.rtm_connect_c8_preflight import (
    FROZEN_C5_APP_SHA256,
    _runtime_c8_publications,
    _runtime_unwired,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "scripts" / "rtm_staging_connect_c8_schema.py"
PREFLIGHT = ROOT / "scripts" / "rtm_connect_c8_preflight.py"
SMOKE = ROOT / "scripts" / "rtm_connect_c8_smoke.py"


def _sql_text_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "text":
            continue
        argument = node.args[0]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            values.append(argument.value)
    return values


class ConnectC8ScriptsContractTest(unittest.TestCase):
    def test_required_scripts_exist_and_compile(self):
        for path in (SCHEMA, PREFLIGHT, SMOKE):
            self.assertTrue(path.exists(), path.name)
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_schema_is_additive_insert_only_and_confirmation_gated(self):
        source = SCHEMA.read_text(encoding="utf-8")
        for required in (
            'add_argument("--apply"',
            'add_argument("--confirmation"',
            "STAGING_CONNECT_C8_SCHEMA_ONLY",
            "RTM_CONNECT_C8_PRODUCTION_SCHEMA_VERSION",
            "CONNECT_C8_REQUIRED_COLUMNS",
            "CONNECT_C8_REQUIRED_INDEXES",
            "CONNECT_C8_REQUIRED_TRIGGERS",
            "CONNECT_C8_REQUIRED_CONSTRAINTS",
            "connect_c8_production_ddl",
            "assert_c8_database_identity",
            'report["connected_database_identity_valid"] = True',
            "INSERT INTO rtm_management_schema_migrations",
            '"destructive": False',
            '"routes_published": False',
            '"connectors_seeded": False',
            '"network_used": False',
            '"production_effects_available": False',
            '"live_activation_available": False',
            '"external_effects_executed": False',
        ):
            self.assertIn(required, source)
        upper = source.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)

    def test_schema_parser_requires_exact_apply_confirmation(self):
        import scripts.rtm_staging_connect_c8_schema as schema

        wrong = schema._parser().parse_args([
            "--apply", "--confirmation", "WRONG",
        ])
        self.assertIn("invalid_apply_confirmation", schema.safety_blockers(wrong))

    def test_preflight_is_read_only_unwired_and_zero_residue(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        for required in (
            '"read_only": True',
            '"synthetic_only": True',
            '"schema_changes_required": False',
            '"routes_published": False',
            '"connector_seeded": False',
            '"network_used": False',
            '"real_provider_contacted": False',
            '"secret_resolution_performed": False',
            '"external_effects_executed": False',
            '"production_effects_available": False',
            '"live_activation_available": False',
            "feature_closed_by_default",
            "runtime_route_graph_has_no_c8",
            "app_runtime_frozen_from_c5",
            "runtime_not_wired",
            "fd089d1cce4f65ebe6fb84b380dd13eba8a98ba4a16049515f4f513c27eeb7ea",
            "no_dormant_c8_endpoint_or_secret",
            "connected_database_identity_valid",
            "no_forbidden_connectors",
            "no_invalid_c8_scope",
            "no_persistent_c8_residue",
            "c8_releases",
            "c8_release_events",
            "c8_outbox",
            "c8_dispatch_events",
        ):
            self.assertIn(required, source)
        self.assertNotIn('add_argument("--apply"', source)
        self.assertIn("SET TRANSACTION READ ONLY", source)

    def test_preflight_detects_c8_in_effective_route_graph(self):
        ordinary_endpoint = type(
            "Endpoint", (), {"__module__": "ordinary.health"},
        )()
        c8_endpoint = type(
            "Endpoint", (), {"__module__": "rtm_connect.production_control"},
        )()
        nested = SimpleNamespace(
            routes=[SimpleNamespace(
                path="/production", name="production", endpoint=c8_endpoint,
                methods={"POST"}, app=None,
            )],
            user_middleware=[],
        )
        root = SimpleNamespace(
            routes=[
                SimpleNamespace(
                    path="/health", name="health", endpoint=ordinary_endpoint,
                    methods={"GET"}, app=None,
                ),
                SimpleNamespace(
                    path="/mounted", name="mounted", endpoint=ordinary_endpoint,
                    methods=set(), app=nested,
                ),
            ],
            user_middleware=[],
        )
        self.assertEqual(
            _runtime_c8_publications(root),
            ("route:/mounted/production:production",),
        )

    def test_app_runtime_freeze_uses_c5_baseline(self):
        self.assertEqual(
            FROZEN_C5_APP_SHA256,
            "fd089d1cce4f65ebe6fb84b380dd13eba8a98ba4a16049515f4f513c27eeb7ea",
        )
        self.assertTrue(_runtime_unwired())

    def test_smoke_is_transactional_inert_and_covers_both_outcomes(self):
        source = SMOKE.read_text(encoding="utf-8")
        for required in (
            '"synthetic_only": True',
            '"transactional": True',
            '"production_effects_available": False',
            '"live_activation_available": False',
            '"network_used": False',
            '"real_provider_contacted": False',
            '"secret_resolution_performed": False',
            '"external_effects_executed": False',
            "assert_c8_staging_boundary(",
            "propose_production_release(",
            "approve_production_release(",
            "mark_production_release_ready(",
            "simulate_production_release_activation(",
            "prepare_dispatch_dry_run(",
            "claim_dispatch_dry_run(",
            "confirm_dispatch_dry_run(",
            "mark_dispatch_unknown(",
            "move_dispatch_manual_review(",
            "emergency_halt_production_release(",
            'for label in ("normal", "unknown", "pending")',
            'int(in_tx["releases"]) == 3',
            'int(in_tx["outbox"]) == 3',
            'int(in_tx["min_daily_limit"]) == 1',
            'int(in_tx["max_daily_limit"]) == 1',
            "exact_replay_reuses_dispatch",
            "semantic_replay_reuses_dispatch",
            "changed_replay_conflict_blocked",
            "new_intent_timestamp_mismatch_blocked",
            "unknown_never_blindly_retried",
            "emergency_halt_blocks_new_dispatch",
            "release_binding_tampering_blocked",
            "release_events_append_only",
            "dispatch_identity_tampering_blocked",
            "dispatch_events_append_only",
            "transaction.rollback()",
            "rollback_removed_synthetic_records",
        ):
            self.assertIn(required, source)

    def test_smoke_has_no_network_secret_or_live_execution_surface(self):
        source = SMOKE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(SMOKE))
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots.update(
            str(node.module or "").split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        self.assertTrue(
            imported_roots.isdisjoint(
                {"requests", "httpx", "urllib", "socket", "ssl", "ftplib"}
            )
        )
        function_names = {
            node.name.lower()
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        forbidden_functions = {
            "submit", "send", "execute_provider", "execute_live",
            "activate_live", "live_activation", "resolve_secret",
        }
        self.assertTrue(function_names.isdisjoint(forbidden_functions))
        self.assertIn("assert_live_activation_unavailable", source)
        self.assertNotIn("ThreadingHTTPServer", source)
        self.assertNotIn("socket.connect", source)

    def test_sqlalchemy_json_has_no_phantom_boolean_bind(self):
        try:
            from sqlalchemy import text as sqlalchemy_text
        except ModuleNotFoundError:
            self.skipTest("SQLAlchemy no esta instalado")
        checked = 0
        for path in (SCHEMA, PREFLIGHT, SMOKE):
            for statement in _sql_text_literals(path):
                self.assertNotIn(
                    "true",
                    set(sqlalchemy_text(statement)._bindparams),
                    f"{path.name}: {statement}",
                )
                checked += 1
        self.assertGreaterEqual(checked, 10)

    def test_scripts_fail_closed_before_database_in_production(self):
        env = dict(os.environ)
        env["RTM_ENV"] = "production"
        for path in (SCHEMA, PREFLIGHT, SMOKE):
            with self.subTest(script=path.name):
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
                self.assertTrue(payload.get("blockers"))

    def test_app_does_not_publish_c8_runtime(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8").lower()
        for forbidden in (
            "production_control", "connect/production",
            "connect.production.admission.simulate",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
