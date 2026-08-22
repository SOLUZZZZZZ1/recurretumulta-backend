from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "scripts" / "rtm_connect_c2_preflight.py"
SMOKE = ROOT / "scripts" / "rtm_connect_c2_smoke.py"
DOC = ROOT / "docs" / "rtm_connect" / "RTM_CONNECT_C2_SYNTHETIC_ECHO.md"
ADR = ROOT / "docs" / "rtm_connect" / "adrs" / "0009-c2-synthetic-echo.md"


class ConnectC2ScriptsContractTest(unittest.TestCase):
    def test_preflight_is_read_only_without_schema_changes(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        self.assertIn('"read_only": True', source)
        self.assertIn('"schema_changes_required": False', source)
        self.assertIn('"routes_published": False', source)
        self.assertNotIn("--apply", source)

    def test_preflight_refuses_outside_staging_before_database(self):
        env = dict(os.environ)
        env["RTM_ENV"] = "production"
        process = subprocess.run(
            [sys.executable, str(PREFLIGHT), "--compact"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(process.returncode, 2)
        payload = json.loads(process.stdout)
        self.assertIn("RTM_ENV_must_be_staging", payload["blockers"])

    def test_preflight_requires_c1_and_zero_persistent_echo(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        self.assertIn("c1_schema_ready", source)
        self.assertIn("c1_migration_registered", source)
        self.assertIn("synthetic_echo_not_persistently_seeded", source)
        self.assertIn("no_real_connectors", source)

    def test_smoke_is_transactional_synthetic_and_network_free(self):
        source = SMOKE.read_text(encoding="utf-8")
        for declaration in (
            '"synthetic_only": True',
            '"transactional": True',
            '"network_used": False',
            '"routes_published": False',
            '"schema_changes_applied": False',
            '"external_effects_executed": False',
        ):
            self.assertIn(declaration, source)
        self.assertIn("transaction.rollback()", source)

    def test_smoke_covers_success_replay_unknown_and_failures(self):
        source = SMOKE.read_text(encoding="utf-8")
        for check in (
            "success_action_confirmed",
            "confirmed_replay_reused_action",
            "unknown_action_persisted_as_unknown",
            "unknown_replay_blocked_before_execution",
            "unknown_reconciled_to_confirmed",
            "failure_modes_normalized",
            "rollback_removed_synthetic_records",
        ):
            self.assertIn(check, source)

    def test_docs_freeze_c2_scope_and_no_app_wiring(self):
        self.assertTrue(DOC.exists())
        self.assertTrue(ADR.exists())
        combined = DOC.read_text(encoding="utf-8") + ADR.read_text(encoding="utf-8")
        self.assertIn("synthetic.echo", combined)
        self.assertIn("sin red", combined.lower())
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("rtm_connect_router", app_source)
        self.assertNotIn("include_router(rtm_connect", app_source)


if __name__ == "__main__":
    unittest.main()
