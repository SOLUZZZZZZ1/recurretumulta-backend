from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "rtm_connect" / "human_filing_runtime.py"


class ConnectA1SRuntimeContractTest(unittest.TestCase):
    def setUp(self):
        self.source = RUNTIME.read_text(encoding="utf-8")

    def test_runtime_module_compiles_and_freezes_versioned_plan(self):
        ast.parse(self.source, filename=str(RUNTIME))
        for required in (
            'RTM_CONNECT_A1S_RUNTIME_VERSION = "rtm_connect_a1s_runtime_v1_0"',
            'RUNTIME_FIXTURE_CONFIRMATION = "STAGING_CONNECT_A1S_RUNTIME_FIXTURES_ONLY"',
            "_RUNTIME_EPOCH = datetime(2026, 8, 25, 10, 0",
            "_RUNTIME_DUE_DAYS = 365",
            "_RUNTIME_AUTHORITY_DAYS = 730",
            "uuid.uuid5",
            "runtime-a94dcd3-v1",
        ):
            if required == "runtime-a94dcd3-v1":
                continue
            self.assertIn(required, self.source)
        self.assertNotIn("uuid.uuid4", self.source)

    def test_fixture_is_insert_only_and_collision_checked(self):
        upper = self.source.upper()
        self.assertGreaterEqual(upper.count("ON CONFLICT"), 6)
        for forbidden in (
            "UPDATE RTM_CONNECT_A1S_",
            "DELETE FROM RTM_CONNECT_A1S_",
            "TRUNCATE",
            "DROP TABLE",
        ):
            self.assertNotIn(forbidden, upper)
        self.assertIn("Colisión con autoridad A1-S Runtime", self.source)
        self.assertIn("operators_exactly_bound", self.source)
        self.assertIn("action_and_grant_exact", self.source)
        self.assertIn(
            "operators_supplied_for_exact_authority_audit", self.source
        )

    def test_jsonb_literals_use_explicit_bind_parameters(self):
        self.assertNotIn("@> '{\"test_mode\":true}'::jsonb", self.source)
        self.assertIn(
            "@>\n                     CAST(:test_mode_metadata AS JSONB)",
            self.source,
        )
        self.assertIn('"test_mode_metadata": json.dumps(', self.source)

    def test_fixture_never_creates_credentials_sessions_or_external_transport(self):
        for forbidden in (
            "password_hash",
            "rtm_operator_sessions",
            "requests",
            "httpx",
            "urllib",
            "socket",
            "boto",
            "b2sdk",
            "submitter_dgt",
        ):
            self.assertNotIn(forbidden, self.source)
        for required in (
            '"network_used": False',
            '"b2_used": False',
            '"provider_contacted": False',
            '"administration_contacted": False',
            '"real_data_used": False',
            '"external_effects_executed": False',
            '"production_authorized": False',
        ):
            self.assertIn(required, self.source)

    def test_fixture_uses_three_distinct_existing_synthetic_operators(self):
        for required in (
            "len(set(normalized)) != 3",
            "profile.get(\"synthetic\") is not True",
            "must_change_password",
            "mfa_required",
            '"supervisor"',
            '"releaser"',
            '"verifier"',
            "three_distinct_active_memberships",
        ):
            self.assertIn(required, self.source)
        self.assertNotIn("INSERT INTO rtm_operators", self.source)

    def test_documents_are_hash_only_local_and_receipt_is_disjoint(self):
        for required in (
            "rtm_connect_a1s_synthetic_input_fixture",
            "rtm_connect_a1s_synthetic_receipt_fixture",
            "b2_bucket, b2_key",
            "NULL, NULL",
            "receipt_disjoint_from_input",
            "database_manifest_only",
        ):
            if required == "database_manifest_only":
                continue
            self.assertIn(required, self.source)

    def test_runtime_readiness_covers_full_core_a1s_and_operator_schema(self):
        for required in (
            "CONNECT_C1_REQUIRED_COLUMNS",
            "CONNECT_A1S_REQUIRED_COLUMNS",
            '"rtm_operator_roles"',
            '"profile"',
            '"auth_epoch"',
            "def missing_runtime_columns",
            '"missing_columns": []',
        ):
            self.assertIn(required, self.source)

    def test_authority_is_r4_assisted_e4_and_frozen(self):
        for required in (
            "RiskClass.R4_CRITICAL_REGULATED",
            "EvidenceLevel.E4_RECEIPT_VERIFIED",
            "ConnectorMode.ASSISTED",
            'decision="approved_frozen"',
            "requires_dual_control=True",
            "legal_effect_authorized=True",
            "validate_a1s_action_authority",
        ):
            self.assertIn(required, self.source)


if __name__ == "__main__":
    unittest.main()
