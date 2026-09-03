from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LegacyCronRetiredTest(unittest.TestCase):
    def test_script_cannot_send_shared_token_or_contact_backend(self):
        source = (ROOT / "cron_tick.sh").read_text(encoding="utf-8")
        self.assertIn("exit 1", source)
        self.assertNotIn("curl", source)
        self.assertNotIn("OPERATOR_TOKEN", source)
        self.assertNotIn("BACKEND_URL", source)


if __name__ == "__main__":
    unittest.main()
