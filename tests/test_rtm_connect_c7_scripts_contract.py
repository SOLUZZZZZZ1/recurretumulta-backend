from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.rtm_connect_c7_preflight import (
    FROZEN_C5_APP_SHA256,
    _runtime_c7_publications,
    _runtime_unwired,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "scripts" / "rtm_staging_connect_c7_schema.py"
PREFLIGHT = ROOT / "scripts" / "rtm_connect_c7_preflight.py"
SMOKE = ROOT / "scripts" / "rtm_connect_c7_smoke.py"


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


class ConnectC7ScriptsContractTest(unittest.TestCase):
    def test_required_scripts_exist_and_compile(self):
        for path in (SCHEMA, PREFLIGHT, SMOKE):
            self.assertTrue(path.exists(), path.name)
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_schema_is_additive_idempotent_and_confirmation_gated(self):
        source = SCHEMA.read_text(encoding="utf-8")
        for required in (
            'add_argument("--apply"',
            'add_argument("--confirmation"',
            "STAGING_CONNECT_C7_SCHEMA_ONLY",
            "RTM_CONNECT_C7_ASSISTED_SCHEMA_VERSION",
            "CONNECT_C7_REQUIRED_COLUMNS",
            "CONNECT_C7_REQUIRED_INDEXES",
            "CONNECT_C7_REQUIRED_TRIGGERS",
            "CONNECT_C7_REQUIRED_CONSTRAINTS",
            "connect_c7_assisted_ddl",
            "INSERT INTO rtm_management_schema_migrations",
            '"destructive": False',
            '"routes_published": False',
            '"connectors_seeded": False',
            '"network_used": False',
            '"external_effects_executed": False',
        ):
            self.assertIn(required, source)
        self.assertNotIn("DROP TABLE", source.upper())
        self.assertNotIn("TRUNCATE", source.upper())
        self.assertNotIn("DELETE FROM", source.upper())

    def test_schema_parser_requires_exact_apply_confirmation(self):
        import scripts.rtm_staging_connect_c7_schema as schema

        wrong = schema._parser().parse_args([
            "--apply", "--confirmation", "WRONG",
        ])
        self.assertIn("invalid_apply_confirmation", schema.safety_blockers(wrong))

    def test_preflight_is_read_only_default_off_and_zero_residue(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        for required in (
            '"read_only": True',
            '"synthetic_only": True',
            '"schema_changes_required": False',
            '"routes_published": False',
            '"connector_seeded": False',
            '"network_used": False',
            '"real_administration_contacted": False',
            '"external_effects_executed": False',
            "feature_closed_by_default",
            "runtime_route_graph_has_no_c7",
            "app_runtime_frozen_from_c5",
            "runtime_not_wired",
            "fd089d1cce4f65ebe6fb84b380dd13eba8a98ba4a16049515f4f513c27eeb7ea",
            "no_dormant_c7_endpoint_or_secret",
            "connector_risk_ceiling_r4",
            "r4_dual_control_declared",
            "connector_supports_reconciliation",
            "reconciliation_is_manual_without_resubmission",
            "assisted_connector_not_persistently_seeded",
            "no_persistent_c7_residue",
            "manual_assisted_exclusive",
            "connected_database_identity_valid",
            "invalid_c7_scope",
            "invalid_c7_event_scope",
            "assisted_event_relations_intact",
            "ASSISTED_LEGAL_CAPABILITY",
        ):
            self.assertIn(required, source)
        self.assertNotIn('add_argument("--apply"', source)

    def test_preflight_detects_c7_in_effective_route_graph(self):
        ordinary_endpoint = type(
            "Endpoint", (), {"__module__": "ordinary.health"},
        )()
        c7_endpoint = type(
            "Endpoint", (), {"__module__": "rtm_connect.assisted_legal"},
        )()
        nested = SimpleNamespace(
            routes=[SimpleNamespace(
                path="/submit",
                name="submit",
                endpoint=c7_endpoint,
                methods={"POST"},
                app=None,
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
        publications = _runtime_c7_publications(root)
        self.assertEqual(
            publications,
            ("route:/mounted/submit:submit",),
        )

    def test_preflight_detects_tags_dependencies_and_middleware_safely(self):
        ordinary_endpoint = type(
            "Endpoint", (), {"__module__": "ordinary.endpoint"},
        )()
        c7_dependency = type(
            "Dependency", (), {"__module__": "rtm_connect.assisted_legal"},
        )()
        dependant = SimpleNamespace(
            call=ordinary_endpoint,
            dependencies=[SimpleNamespace(
                call=c7_dependency,
                dependencies=[],
            )],
        )
        dependency_route = SimpleNamespace(
            path="/ordinary",
            name="ordinary",
            tags=[],
            endpoint=ordinary_endpoint,
            app=None,
            dependant=dependant,
        )
        tag_route = SimpleNamespace(
            path="/tagged",
            name="tagged",
            tags=["connect_c7"],
            endpoint=ordinary_endpoint,
            app=None,
            dependant=None,
        )
        middleware_class = type("AssistedGuard", (), {})
        middleware_class.__module__ = "rtm_connect.assisted_legal_guard"

        class Secret:
            def __repr__(self):  # pragma: no cover - must never run
                raise AssertionError("preflight must not repr middleware values")

        app = SimpleNamespace(
            routes=[dependency_route, tag_route],
            user_middleware=[SimpleNamespace(
                cls=middleware_class,
                args=(),
                kwargs={"opaque": Secret()},
            )],
        )
        self.assertEqual(
            _runtime_c7_publications(app),
            (
                "middleware:AssistedGuard",
                "route:/ordinary:ordinary",
                "route:/tagged:tagged",
            ),
        )

    def test_app_runtime_freeze_records_c5_and_only_a1s_supersedes_it(self):
        self.assertEqual(
            FROZEN_C5_APP_SHA256,
            "fd089d1cce4f65ebe6fb84b380dd13eba8a98ba4a16049515f4f513c27eeb7ea",
        )
        self.assertFalse(_runtime_unwired())
        source = (ROOT / "app.py").read_text(encoding="utf-8").lower()
        self.assertIn("human_filing_router", source)
        self.assertIn("human_filing_gate_middleware", source)
        for forbidden in (
            "/ops/connect/assisted",
            "administration.submit.legal.assisted",
            "assisted_legal",
            "connect_c7",
        ):
            self.assertNotIn(forbidden, source)

    def test_smoke_covers_normal_unknown_and_rolls_back(self):
        source = SMOKE.read_text(encoding="utf-8")
        for required in (
            '"synthetic_only": True',
            '"transactional": True',
            '"network_used": False',
            '"real_administration_contacted": False',
            '"routes_published": False',
            '"schema_changes_applied": False',
            '"external_effects_executed": False',
            "prepare_assisted_legal(",
            "begin_assisted_review(",
            "attest_assisted_review(",
            "release_assisted_legal(",
            "begin_assisted_execution(",
            "mark_assisted_awaiting_receipt(",
            "mark_assisted_outcome_unknown(",
            "begin_assisted_reconciliation(",
            "resolve_assisted_reconciliation(",
            "submit_assisted_receipt(",
            "verify_assisted_receipt(",
            "complete_assisted_legal(",
            "unknown_blocks_blind_retry",
            "unknown_reconciles_original_attempt_after_e4",
            "indeterminate_stays_unknown_same_attempt",
            "triple_separation_enforced",
            "package_tampering_blocked",
            "assisted_events_append_only",
            "task_cross_attempt_scope_blocked",
            "event_cross_parent_scope_blocked",
            "transaction.rollback()",
            "rollback_removed_synthetic_records",
        ):
            self.assertIn(required, source)
        for forbidden in (
            "requests.", "httpx.", "urllib.request", "socket.connect",
            "ThreadingHTTPServer",
        ):
            self.assertNotIn(forbidden, source)

    def test_smoke_uses_final_receipt_and_reconciliation_api(self):
        source = SMOKE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(SMOKE))
        verify_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "verify_assisted_receipt"
        ]
        self.assertGreaterEqual(len(verify_calls), 4)
        required_keywords = {
            "task_id", "grant", "verifier_operator_id",
            "observed_receipt_sha256", "observed_external_reference",
            "observed_package_sha256", "observed_human_gate_sha256",
            "verified_at",
        }
        for call in verify_calls:
            self.assertTrue(
                required_keywords.issubset({
                    keyword.arg for keyword in call.keywords
                })
            )
        self.assertIn("ASSISTED_LEGAL_REFERENCE_PREFIX", source)
        self.assertIn("target_status=ActionStatus.UNKNOWN", source)

    def test_smoke_scope_probes_are_valid_single_statements(self):
        tree = ast.parse(
            SMOKE.read_text(encoding="utf-8"), filename=str(SMOKE)
        )
        statements = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
        ]
        task_updates = [
            statement for statement in statements
            if "UPDATE rtm_connect_assisted_tasks" in statement
        ]
        self.assertGreaterEqual(len(task_updates), 2)
        for statement in task_updates:
            self.assertEqual(
                statement.count("UPDATE rtm_connect_assisted_tasks"),
                1,
            )
        source = SMOKE.read_text(encoding="utf-8")
        self.assertIn(
            "SET attempt_id=CAST(:unknown_attempt_id AS UUID)",
            source,
        )
        self.assertIn(
            "INSERT INTO rtm_connect_assisted_events(",
            source,
        )
        self.assertIn(
            "CAST(:unknown_action_id AS UUID)",
            source,
        )

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

    def test_app_does_not_publish_c7_runtime(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        for forbidden in (
            "assisted_legal", "assisted.legal",
            "administration.submit.legal.assisted",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
