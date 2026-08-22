from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "scripts" / "rtm_connect_c0_preflight.py"
SMOKE = ROOT / "scripts" / "rtm_connect_c0_smoke.py"


class ConnectC0ScriptsTest(unittest.TestCase):
    def test_preflight_is_read_only_and_architecture_only(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        self.assertIn('"read_only": True', source)
        self.assertIn('"architecture_only": True', source)
        self.assertIn('"routes_published": False', source)
        self.assertIn('"database_schema_created": False', source)

    def test_preflight_checks_external_effect_flags(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        for name in (
            "RTM_ENABLE_EXTERNAL_SUBMISSION",
            "RTM_ENABLE_OUTBOUND_EMAIL",
            "RTM_ENABLE_STRIPE",
            "RTM_ENABLE_FINAL_PAYMENTS",
            "RTM_ALLOW_REAL_CUSTOMER_DATA",
        ):
            self.assertIn(name, source)

    def test_preflight_refuses_outside_staging_before_runtime(self):
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

    def test_smoke_is_pure_and_synthetic(self):
        source = SMOKE.read_text(encoding="utf-8")
        for declaration in (
            '"synthetic_only": True',
            '"network_used": False',
            '"database_touched": False',
            '"routes_published": False',
            '"external_effects_executed": False',
        ):
            self.assertIn(declaration, source)

    def test_smoke_covers_unknown_and_evidence(self):
        source = SMOKE.read_text(encoding="utf-8")
        self.assertIn("unknown_never_blindly_retries", source)
        self.assertIn(
            "insufficient_evidence_blocks_confirmation",
            source,
        )
        self.assertIn(
            "verified_receipt_allows_confirmation",
            source,
        )

    def test_no_app_runtime_wiring_is_packaged(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("rtm_connect_router", source)
        self.assertNotIn("include_router(rtm_connect", source)


if __name__ == "__main__":
    unittest.main()
