from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "rtm_connect" / "manual_handoff.py"
CONNECTOR = ROOT / "rtm_connect" / "connectors" / "manual_handoff.py"


class ConnectC3WorkflowContractTest(unittest.TestCase):
    def test_uses_c1_action_lifecycle(self):
        source = SOURCE.read_text(encoding="utf-8")
        for function in (
            "create_action", "authorize_action", "queue_action",
            "start_attempt", "record_attempt_outcome",
            "record_evidence", "confirm_action",
        ):
            self.assertIn(function, source)

    def test_registration_is_manual(self):
        self.assertIn(
            "mode=ConnectorMode.MANUAL",
            SOURCE.read_text(encoding="utf-8"),
        )

    def test_registration_is_synthetic(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('"synthetic_only": True', source)
        self.assertIn('"network_used": False', source)

    def test_prepare_freezes_before_assignment(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertLess(
            source.index("INSERT INTO rtm_connect_manual_tasks"),
            source.index('target_status="assigned"'),
        )

    def test_replay_checks_package(self):
        self.assertIn(
            "El replay intenta cambiar el paquete congelado",
            SOURCE.read_text(encoding="utf-8"),
        )

    def test_replay_checks_assignee(self):
        self.assertIn(
            "El replay intenta cambiar el operador asignado",
            SOURCE.read_text(encoding="utf-8"),
        )

    def test_assignee_controls_execution(self):
        self.assertGreaterEqual(
            SOURCE.read_text(encoding="utf-8").count(
                "Solo el operador asignado"
            ),
            3,
        )

    def test_attempt_outcome_precedes_evidence(self):
        section = SOURCE.read_text(encoding="utf-8")
        section = section[
            section.index("def submit_manual_receipt"):
            section.index("def verify_manual_receipt")
        ]
        self.assertLess(
            section.index("record_attempt_outcome("),
            section.index("record_evidence("),
        )

    def test_capture_is_e3(self):
        self.assertIn(
            "EvidenceLevel.E3_RECEIPT_CAPTURED",
            CONNECTOR.read_text(encoding="utf-8"),
        )

    def test_verification_is_e4(self):
        connector = CONNECTOR.read_text(encoding="utf-8")
        self.assertIn("EvidenceLevel.E4_RECEIPT_VERIFIED", connector)
        self.assertIn("manual_handoff_hash_reference_v1", connector)

    def test_separation_of_duties_is_explicit(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("ManualHandoffSeparationOfDutiesError", source)
        self.assertIn("El verificador debe ser distinto", source)

    def test_no_routes_or_network_clients(self):
        combined = (
            SOURCE.read_text(encoding="utf-8")
            + CONNECTOR.read_text(encoding="utf-8")
        )
        for forbidden in ("APIRouter", "requests.", "httpx.", "boto3", "stripe"):
            self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
