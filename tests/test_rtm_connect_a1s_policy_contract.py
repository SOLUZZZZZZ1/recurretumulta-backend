from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "rtm_connect" / "human_filing_policy.py"


class ConnectA1SPolicyContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = POLICY.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(POLICY))

    def test_policy_exists_compiles_and_exports_fail_closed_boundary(self):
        for required in (
            "RTM_CONNECT_A1S_POLICY_VERSION",
            "HumanFilingStagingBoundary",
            "assert_a1s_staging_boundary",
            "assert_a1s_database_identity",
            "load_a1s_runtime_configuration",
            "validate_a1s_action_authority",
        ):
            self.assertIn(required, self.source)

    def test_only_explicit_feature_enable_can_open_synthetic_routes(self):
        for required in (
            "RTM_ENABLE_CONNECT_A1S_HUMAN_FILING",
            "RTM_CONNECT_A1S_NETWORK_ALLOWED",
            "RTM_CONNECT_A1S_B2_ALLOWED",
            "RTM_CONNECT_A1S_PROVIDER_ALLOWED",
            "RTM_CONNECT_A1S_REAL_DATA_ALLOWED",
            "RTM_CONNECT_A1S_EXTERNAL_EFFECTS_ALLOWED",
        ):
            self.assertIn(required, self.source)

    def test_policy_reuses_exhaustive_staging_and_database_identity_guards(self):
        self.assertIn("assert_c7_staging_boundary", self.source)
        self.assertIn("assert_c7_database_identity", self.source)
        self.assertIn("_LOCAL_DISABLED_FLAGS", self.source)

    def test_authority_contract_enforces_synthetic_r4_and_separation(self):
        for required in (
            "HUMAN_FILING_CAPABILITY",
            "HUMAN_FILING_SATELLITE",
            "HUMAN_FILING_TARGET_TYPE",
            "HUMAN_FILING_TARGET_REF",
            "R4_CRITICAL_REGULATED",
            "E4_RECEIPT_VERIFIED",
            "requires_dual_control",
            "approved_by_operator_ids",
        ):
            self.assertIn(required, self.source)

    def test_policy_has_no_network_or_object_storage_import(self):
        imported: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {"requests", "httpx", "socket", "boto3", "urllib.request"}
        self.assertFalse(forbidden & imported)


if __name__ == "__main__":
    unittest.main()
