from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "rtm_connect" / "execution.py"
ECHO = ROOT / "rtm_connect" / "connectors" / "synthetic_echo.py"


class ConnectC2ExecutionContractTest(unittest.TestCase):
    def test_execution_uses_public_c1_kernel_api(self):
        source = EXECUTION.read_text(encoding="utf-8")
        self.assertIn("from rtm_connect.kernel import", source)
        self.assertNotIn("from rtm_connect.repository import", source)
        self.assertNotIn("_load_action_contract", source)
        self.assertNotIn("_transition_action", source)

    def test_registration_is_exact_and_synthetic(self):
        source = EXECUTION.read_text(encoding="utf-8")
        self.assertIn("code=SYNTHETIC_ECHO_CODE", source)
        self.assertIn("version=SYNTHETIC_ECHO_CONNECTOR_VERSION", source)
        self.assertIn("mode=ConnectorMode.API", source)
        self.assertIn("supports_reconciliation=True", source)
        self.assertIn('"network_used": False', source)

    def test_success_pipeline_records_outcome_evidence_and_confirmation(self):
        source = EXECUTION.read_text(encoding="utf-8")
        self.assertIn("record_attempt_outcome(", source)
        self.assertIn("record_evidence(", source)
        self.assertIn("confirm_action(", source)
        self.assertIn("SyntheticEchoScenario.SUCCESS", source)

    def test_confirmed_replay_short_circuits_before_attempt(self):
        source = EXECUTION.read_text(encoding="utf-8")
        replay_index = source.index("if created.replayed:")
        attempt_index = source.index("attempt = start_attempt(")
        self.assertLess(replay_index, attempt_index)
        self.assertIn("attempt_id=None", source[replay_index:attempt_index])

    def test_nonterminal_replay_is_blocked(self):
        source = EXECUTION.read_text(encoding="utf-8")
        self.assertIn("ExistingActionReplayBlocked", source)
        self.assertIn("no puede reejecutarse a ciegas", source)

    def test_unknown_reconciliation_is_explicit(self):
        source = EXECUTION.read_text(encoding="utf-8")
        self.assertIn("def reconcile_synthetic_echo", source)
        self.assertIn("begin_reconciliation(", source)
        self.assertIn("reconciliation_required=FALSE", source)
        self.assertIn('scenario="unknown_reconciled"', source)

    def test_failure_modes_are_normalized(self):
        source = ECHO.read_text(encoding="utf-8")
        for status in (
            '"retryable_failed"',
            '"permanent_failed"',
            '"manual_review"',
        ):
            self.assertIn(status, source)

    def test_c2_does_not_publish_routes_or_schema(self):
        for path in (EXECUTION, ECHO):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("APIRouter", source)
            self.assertNotIn("CREATE TABLE", source)
            self.assertNotIn("include_router", source)

    def test_c2_does_not_embed_credentials(self):
        combined = (
            EXECUTION.read_text(encoding="utf-8")
            + ECHO.read_text(encoding="utf-8")
        ).lower()
        for forbidden in (
            "database_url=",
            "api_key=",
            "client_secret=",
            "access_token=",
            "private_key=",
        ):
            self.assertNotIn(forbidden, combined)
        self.assertIn("credential_ref", ECHO.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
