from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT / "rtm_connect" / "repository.py"
KERNEL = ROOT / "rtm_connect" / "kernel.py"


class ConnectC1KernelContractTest(unittest.TestCase):
    def test_kernel_has_no_network_clients(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        for forbidden in ("requests.", "httpx.", "urllib.request", "boto3", "stripe."):
            self.assertNotIn(forbidden, source)

    def test_action_creation_claims_idempotency_before_execution(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn("ON CONFLICT (idempotency_key) DO NOTHING", source)
        self.assertIn("rtm_connect_idempotency_claims", source)
        self.assertIn("replay_count=replay_count+1", source)

    def test_authorization_uses_c0_authority_validation(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn("validate_execution_authority", source)
        self.assertIn("AuthorizationGrant", source)

    def test_repository_uses_c0_state_machine(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn("assert_transition(current, target)", source)
        self.assertIn("ActionStatus.UNKNOWN", source)
        self.assertIn("ActionStatus.RECONCILING", source)

    def test_only_synthetic_connector_registration_is_exposed(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn("def register_synthetic_connector", source)
        self.assertIn("synthetic_only", source)
        self.assertNotIn("def register_real_connector", source)

    def test_connector_configuration_rejects_embedded_secrets(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn("_FORBIDDEN_CONFIGURATION_KEYS", source)
        self.assertIn("_assert_no_secrets", source)
        self.assertIn("credential_ref, configuration", source)

    def test_attempt_start_checks_capability_mode_risk_and_idempotency(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        for phrase in (
            "El conector no declara la capacidad",
            "Modo de conector no autorizado",
            "El riesgo excede el techo del conector",
            "C1 exige conector idempotente",
        ):
            self.assertIn(phrase, source)

    def test_confirmation_uses_evidence_gate(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn("confirmation_gate(action, grant, evidence)", source)
        self.assertIn("EvidenceGateError", source)

    def test_weak_evidence_does_not_auto_confirm(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        section = source.split("def record_evidence", 1)[1].split("def _load_authorization", 1)[0]
        self.assertIn("EVIDENCE_PENDING", section)
        self.assertNotIn("ActionStatus.CONFIRMED", section)

    def test_no_delete_drop_or_truncate_in_repository(self):
        source = REPOSITORY.read_text(encoding="utf-8").upper()
        for forbidden in ("DELETE FROM", "DROP TABLE", "TRUNCATE"):
            self.assertNotIn(forbidden, source)

    def test_kernel_exports_small_internal_surface(self):
        source = KERNEL.read_text(encoding="utf-8")
        for symbol in (
            "create_action", "authorize_action", "queue_action",
            "start_attempt", "record_attempt_outcome", "record_evidence",
            "confirm_action", "begin_reconciliation",
        ):
            self.assertIn(f'"{symbol}"', source)

    def test_repository_never_returns_credentials(self):
        source = REPOSITORY.read_text(encoding="utf-8")
        for forbidden in ("raw_token", "access_token", "private_key", "client_secret"):
            self.assertNotIn(f'return {forbidden}', source)


if __name__ == "__main__":
    unittest.main()
