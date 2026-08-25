from __future__ import annotations

import ast
import unittest
from pathlib import Path

from scripts import rtm_staging_connect_a1s_runtime_fixture as fixture_script


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rtm_staging_connect_a1s_runtime_fixture.py"


class ConnectA1SRuntimeFixtureScriptContractTest(unittest.TestCase):
    def setUp(self):
        self.source = SCRIPT.read_text(encoding="utf-8")

    def test_script_compiles_and_freezes_confirmation_and_fixture(self):
        ast.parse(self.source, filename=str(SCRIPT))
        self.assertEqual(
            fixture_script.APPLY_CONFIRMATION,
            "STAGING_CONNECT_A1S_RUNTIME_FIXTURES_ONLY",
        )
        self.assertEqual(
            fixture_script.DEFAULT_FIXTURE_KEY,
            "runtime-a94dcd3-v1",
        )

    def test_apply_requires_confirmation_three_ids_and_disabled_routes(self):
        args = fixture_script._parser().parse_args([
            "--apply", "--confirmation", "WRONG",
        ])
        blockers = fixture_script.safety_blockers(args, values={})
        self.assertIn("invalid_apply_confirmation", blockers)
        self.assertIn("three_operator_ids_required_for_apply", blockers)
        for required in (
            "RTM_ENABLE_CONNECT_A1S_HUMAN_FILING",
            "RTM_ENABLE_OPERATOR_AUTH_V1",
            "_must_be_explicitly_false",
        ):
            self.assertIn(required, self.source)

    def test_audit_is_read_only_and_apply_is_creation_only(self):
        for required in (
            'conn.execute(text("SET TRANSACTION READ ONLY"))',
            '"read_only": not bool(args.apply)',
            '"creation_only": True',
            '"a1s_rows_insert_only": True',
            '"preexisting_rows_mutated": False',
            '"new_core_action_transitions_to_authorized": True',
            '"destructive": False',
            "provision_runtime_fixture(",
            "audit_runtime_fixture(",
            "schema_snapshot(conn)",
            "assert_a1s_database_identity(",
        ):
            self.assertIn(required, self.source)
        upper = self.source.upper()
        for forbidden in ("DELETE FROM", "TRUNCATE", "DROP TABLE"):
            self.assertNotIn(forbidden, upper)

    def test_script_never_creates_operator_credentials_or_sessions(self):
        for forbidden in (
            "INSERT INTO rtm_operators",
            "INSERT INTO rtm_operator_sessions",
            "password_hash",
            "raw_token",
        ):
            self.assertNotIn(forbidden, self.source)
        self.assertIn('"operators_created": False', self.source)
        self.assertIn('"credentials_created": False', self.source)
        self.assertIn('"sessions_created": False', self.source)

    def test_database_and_external_network_are_reported_separately(self):
        for required in (
            '"database_connection_used": False',
            '"database_touched": False',
            '"database_mutated": False',
            '"provider_network_used": False',
            '"administration_network_used": False',
            '"provider_contacted": False',
            '"administration_contacted": False',
            '"b2_used": False',
            '"real_data_used": False',
            '"external_effects_executed": False',
            '"production_authorized": False',
            '"live_verdict": "no_go"',
        ):
            self.assertIn(required, self.source)
        self.assertNotIn('"network_used": False', self.source)

    def test_eligible_listing_exposes_no_email_or_auth_material(self):
        function = self.source.split(
            "def eligible_synthetic_operators", 1
        )[1].split("def _print", 1)[0]
        self.assertNotIn("o.email", function)
        self.assertNotIn('"email"', function)
        self.assertNotIn("password_hash", function)
        self.assertNotIn("token", function)
        self.assertIn('"operator_id"', function)
        self.assertIn('"role_code"', function)


if __name__ == "__main__":
    unittest.main()
