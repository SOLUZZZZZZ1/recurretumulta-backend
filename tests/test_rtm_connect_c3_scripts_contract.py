from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "scripts" / "rtm_staging_connect_c3_schema.py"
PREFLIGHT = ROOT / "scripts" / "rtm_connect_c3_preflight.py"
SMOKE = ROOT / "scripts" / "rtm_connect_c3_smoke.py"
DOC = ROOT / "docs" / "rtm_connect" / "RTM_CONNECT_C3_MANUAL_HANDOFF.md"
ADR = ROOT / "docs" / "rtm_connect" / "adrs" / "0010-c3-manual-handoff.md"


class ConnectC3ScriptsContractTest(unittest.TestCase):
    def test_schema_requires_confirmation(self):
        source = SCHEMA.read_text(encoding="utf-8")
        self.assertIn("STAGING_CONNECT_C3_SCHEMA_ONLY", source)
        self.assertIn("--apply", source)

    def test_schema_is_non_destructive(self):
        source = SCHEMA.read_text(encoding="utf-8")
        self.assertIn('"destructive": False', source)
        self.assertIn('"connectors_seeded": False', source)

    def test_schema_checks_staging_barriers(self):
        source = SCHEMA.read_text(encoding="utf-8")
        for blocker in (
            "RTM_ENV_must_be_staging",
            "RTM_DATA_NAMESPACE_must_identify_staging",
            "RTM_SIDE_EFFECT_POLICY_must_be_isolated",
            "RTM_ALLOW_REAL_CUSTOMER_DATA_must_be_false",
        ):
            self.assertIn(blocker, source)

    def test_preflight_is_read_only(self):
        self.assertIn(
            '"read_only": True',
            PREFLIGHT.read_text(encoding="utf-8"),
        )

    def test_preflight_requires_zero_residue(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        self.assertIn("manual_tasks_total", source)
        self.assertIn("manual_handoff_not_persistently_seeded", source)

    def test_preflight_refuses_production(self):
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
        self.assertIn(
            "RTM_ENV_must_be_staging",
            json.loads(process.stdout)["blockers"],
        )

    def test_smoke_is_transactional(self):
        source = SMOKE.read_text(encoding="utf-8")
        self.assertIn('"transactional": True', source)
        self.assertIn("transaction.rollback()", source)
        self.assertIn('"network_used": False', source)

    def test_docs_freeze_scope(self):
        combined = DOC.read_text(encoding="utf-8") + ADR.read_text(encoding="utf-8")
        self.assertIn("manual_handoff", combined)
        self.assertIn("E3", combined)
        self.assertIn("E4", combined)
        self.assertIn("sin rutas", combined.lower())


if __name__ == "__main__":
    unittest.main()
