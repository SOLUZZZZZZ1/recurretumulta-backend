from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SCRIPT = ROOT / "scripts" / "rtm_staging_connect_c1_schema.py"
PREFLIGHT = ROOT / "scripts" / "rtm_connect_c1_preflight.py"
SMOKE = ROOT / "scripts" / "rtm_connect_c1_smoke.py"


class ConnectC1ScriptsContractTest(unittest.TestCase):
    def test_schema_apply_requires_literal_confirmation(self):
        source = SCHEMA_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("STAGING_CONNECT_C1_SCHEMA_ONLY", source)
        self.assertIn("invalid_apply_confirmation", source)

    def test_schema_script_refuses_outside_staging_before_database(self):
        env = dict(os.environ)
        env["RTM_ENV"] = "production"
        process = subprocess.run(
            [sys.executable, str(SCHEMA_SCRIPT), "--compact"],
            cwd=ROOT, env=env, text=True, capture_output=True,
            timeout=30, check=False,
        )
        self.assertEqual(process.returncode, 2)
        payload = json.loads(process.stdout)
        self.assertIn("RTM_ENV_must_be_staging", payload["blockers"])

    def test_schema_script_declares_no_runtime_or_effects(self):
        source = SCHEMA_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"routes_published": False', source)
        self.assertIn('"connectors_seeded": False', source)
        self.assertIn('"external_effects_executed": False', source)
        self.assertIn('"destructive": False', source)

    def test_preflight_is_read_only(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        self.assertIn('"read_only": True', source)
        self.assertNotIn("--apply", source)
        self.assertIn("no_real_connectors", source)

    def test_preflight_checks_c0_manifest_and_runtime_absence(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        self.assertIn("assert_manifest_frozen", source)
        self.assertIn("runtime_not_wired", source)
        self.assertIn("rtm_connect_runtime_unexpectedly_wired", source)

    def test_smoke_is_transactional_synthetic_and_network_free(self):
        source = SMOKE.read_text(encoding="utf-8")
        for declaration in (
            '"synthetic_only": True',
            '"transactional": True',
            '"network_used": False',
            '"routes_published": False',
            '"external_effects_executed": False',
        ):
            self.assertIn(declaration, source)
        self.assertIn("transaction.rollback()", source)

    def test_smoke_covers_full_c1_kernel(self):
        source = SMOKE.read_text(encoding="utf-8")
        for check in (
            "idempotent_replay_reused_action",
            "weak_evidence_blocked_confirmation",
            "verified_evidence_confirmed_action",
            "unknown_blind_retry_blocked",
            "unknown_entered_reconciliation",
            "transition_ledger_append_only",
            "evidence_store_append_only",
            "authorization_registry_immutable",
            "rollback_removed_synthetic_records",
        ):
            self.assertIn(check, source)

    def test_all_scripts_reuse_external_effect_barriers(self):
        for path in (SCHEMA_SCRIPT, PREFLIGHT, SMOKE):
            source = path.read_text(encoding="utf-8")
            for name in (
                "RTM_ENABLE_EXTERNAL_SUBMISSION",
                "RTM_ENABLE_OUTBOUND_EMAIL",
                "RTM_ENABLE_STRIPE",
                "RTM_ENABLE_FINAL_PAYMENTS",
                "RTM_ALLOW_REAL_CUSTOMER_DATA",
            ):
                self.assertIn(name, source)

    def test_c1_does_not_modify_app_runtime(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("rtm_connect_router", source)
        self.assertNotIn("include_router(rtm_connect", source)


if __name__ == "__main__":
    unittest.main()
