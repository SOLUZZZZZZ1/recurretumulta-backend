from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RetiredLegacyModulesTest(unittest.TestCase):
    def test_unmounted_duplicate_and_debug_routers_are_absent(self):
        retired = {
            "admin_migrate.py",
            "admin_migrate_payments.py",
            "ai/ops_operator_router.py",
            "app_include_snippet.py",
            "authorize.py",
            "billing_dynamic.py",
            "debug_generate_preview.py",
            "debug_test_classifier.py",
            "ops_override.py",
            "ops_patched.py",
            "partner_cases.py",
            "patch_b2_storage.py.diff",
            "patch_ops.py.diff",
            "patches_ops_b2.diff",
            "router_ops_regenerate.py",
        }
        self.assertEqual(
            [name for name in sorted(retired) if (ROOT / name).exists()],
            [],
        )


if __name__ == "__main__":
    unittest.main()
