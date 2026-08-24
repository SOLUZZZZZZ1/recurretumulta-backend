from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.rtm_connect_c6_preflight import _runtime_c6_publications


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "scripts" / "rtm_staging_connect_c6_schema.py"
PREFLIGHT = ROOT / "scripts" / "rtm_connect_c6_preflight.py"
SMOKE = ROOT / "scripts" / "rtm_connect_c6_smoke.py"


class ConnectC6ScriptsContractTest(unittest.TestCase):
    def test_scripts_exist(self):
        for path in (SCHEMA, PREFLIGHT, SMOKE):
            self.assertTrue(path.exists(), path.name)

    def test_schema_is_read_only_zero_ddl_no_apply_or_migration(self):
        source = SCHEMA.read_text(encoding="utf-8")
        for required in (
            '"read_only": True', '"apply_available": False',
            '"schema_changes_required": False', '"destructive": False',
            '"connectors_seeded": False', '"external_effects_executed": False',
            "unexpected_connect_c6_migration_registered",
            "assert_c6_database_identity",
            "C6_SMOKE_OPERATOR_COLUMNS",
            "C1_TRIGGER_BINDINGS",
            "_c1_object_integrity",
            "expected_body_sha256",
            "indisunique", "indisvalid", "indisready", "convalidated",
            "indoption[key_number - 1]", "key_options",
            "_canonical_catalog_index_key",
            "c3_schema_snapshot",
            "c4_schema_snapshot",
        ):
            self.assertIn(required, source)
        self.assertNotIn('add_argument("--apply"', source)
        self.assertNotIn("INSERT INTO rtm_management_schema_migrations", source)

    def test_preflight_is_read_only_no_network_and_zero_residue(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        for required in (
            '"read_only": True', '"synthetic_only": True',
            '"network_used": False', '"real_provider_contacted": False',
            '"external_effects_executed": False',
            "no_persistent_c6_connector_or_secret_ref",
            "c5_synthetic_connector_scope_clean", "no_persistent_c6_residue",
            "no_dormant_c6_endpoint_or_secret", "runtime_not_wired",
            "c6_migration_absent", "c0_manifest_frozen", "c6_manifest_frozen",
            "connected_database_identity_valid",
            "core_authority_tuple_exact",
            "runtime_route_graph_has_no_c6", "runtime_publications",
        ):
            self.assertIn(required, source)

    def test_preflight_inspects_effective_routes_and_middleware(self):
        def ordinary_endpoint():
            return None

        ordinary_endpoint.__module__ = "health_endpoint"
        ordinary = SimpleNamespace(
            path="/health",
            name="health",
            tags=[],
            endpoint=ordinary_endpoint,
            app=None,
        )
        hidden_endpoint = type(
            "Endpoint",
            (),
            {"__module__": "rtm_connect.provider_sandbox_router"},
        )()
        published = SimpleNamespace(
            path="/internal/probe",
            name="probe",
            tags=[],
            endpoint=hidden_endpoint,
            app=None,
        )
        middleware_class = type("Guard", (), {})
        middleware_class.__module__ = "rtm_connect.controlled_sandbox_guard"
        app = SimpleNamespace(
            routes=[ordinary, published],
            user_middleware=[SimpleNamespace(cls=middleware_class)],
        )
        self.assertEqual(
            _runtime_c6_publications(app),
            (
                "middleware:Guard",
                "route:/internal/probe:probe",
            ),
        )
        self.assertEqual(
            _runtime_c6_publications(SimpleNamespace(
                routes=[ordinary],
                user_middleware=[],
            )),
            (),
        )

    def test_preflight_descends_mounts_dependencies_and_middleware_options(self):
        ordinary_endpoint = type(
            "Endpoint", (), {"__module__": "ordinary_endpoint"},
        )()
        c6_dependency = type(
            "Dependency", (), {"__module__": "rtm_connect.provider_sandbox"},
        )()
        dependant = SimpleNamespace(
            call=ordinary_endpoint,
            dependencies=[SimpleNamespace(
                call=c6_dependency,
                dependencies=[],
            )],
        )
        nested_route = SimpleNamespace(
            path="/run", name="run", tags=[], endpoint=ordinary_endpoint,
            app=None, dependant=dependant,
        )
        nested_app = SimpleNamespace(
            routes=[nested_route], user_middleware=[],
        )
        mount = SimpleNamespace(
            path="/mounted", name="mounted", tags=[],
            endpoint=ordinary_endpoint, app=nested_app, dependant=None,
        )
        generic_middleware = type("GenericMiddleware", (), {})
        generic_middleware.__module__ = "ordinary_middleware"
        callback = type(
            "Callback", (), {"__module__": "rtm_connect.controlled_sandbox"},
        )()
        app = SimpleNamespace(
            routes=[mount],
            user_middleware=[SimpleNamespace(
                cls=generic_middleware,
                args=(),
                kwargs={"callback": callback},
            )],
        )
        self.assertEqual(
            _runtime_c6_publications(app),
            (
                "middleware:GenericMiddleware",
                "route:/mounted/run:run",
            ),
        )

    def test_smoke_uses_real_loopback_and_rolls_back(self):
        source = SMOKE.read_text(encoding="utf-8")
        for required in (
            "ThreadingHTTPServer", '("127.0.0.1", 0)',
            '"network_used": True', '"loopback_only": True',
            '"external_network_used": False', '"real_provider_contacted": False',
            "transaction.rollback()", "unknown_blind_post_retry_blocked",
            "unknown_reconciled_by_get_only", "read_timeout_becomes_unknown_e1",
            "secret_value_absent_from_all_ledgers",
            "rollback_removed_synthetic_records", "server.shutdown()",
            "assert_c6_database_identity",
            "provider_idempotency_reuse_and_conflict",
            "out_of_band_completions",
            "configured_target_is_literal_loopback",
            "loopback_server_observed_local_peer_only",
            "to_jsonb(rtm_connect_authorizations)",
            "to_jsonb(rtm_connect_idempotency_claims)",
            "create_action(", "authorize_action(",
        ):
            self.assertIn(required, source)

    def test_all_scripts_reuse_full_c6_staging_boundary(self):
        for path in (SCHEMA, PREFLIGHT, SMOKE):
            source = path.read_text(encoding="utf-8")
            self.assertIn("safety_blockers", source)
        source = SCHEMA.read_text(encoding="utf-8")
        self.assertIn("assert_c6_staging_boundary", source)

    def test_scripts_refuse_production_before_database_or_socket(self):
        env = dict(os.environ)
        env["RTM_ENV"] = "production"
        for path in (SCHEMA, PREFLIGHT, SMOKE):
            with self.subTest(path=path.name):
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
                self.assertTrue(any(
                    "RTM_ENV_must_be_staging" in item
                    for item in payload["blockers"]
                ))

    def test_no_c6_runtime_is_imported_by_app(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        for forbidden in (
            "provider_sandbox", "controlled_sandbox", "sandbox.http.probe"
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
