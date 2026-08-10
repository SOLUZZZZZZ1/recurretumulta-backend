from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "rtm_staging_smoke.py"


class SyntheticStagingSmokeScriptTest(unittest.TestCase):
    def test_list_mode_runs_from_repository_root_without_provider_or_secrets(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--list"],
            cwd=REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["confirmation_required"], "SYNTHETIC_ONLY")
        self.assertEqual(
            {item["service"] for item in payload["scenarios"]},
            {"debt", "administration", "travel", "claims"},
        )

    def test_list_filter_never_includes_an_unselected_satellite(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--list",
                "--services",
                "travel",
            ],
            cwd=REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(
            [item["service"] for item in payload["scenarios"]],
            ["travel"],
        )


if __name__ == "__main__":
    unittest.main()
