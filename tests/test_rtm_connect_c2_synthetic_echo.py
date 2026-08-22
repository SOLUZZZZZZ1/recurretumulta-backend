from __future__ import annotations

import inspect
import unittest
import uuid
from datetime import datetime, timezone

from rtm_connect.connectors.synthetic_echo import (
    SYNTHETIC_ECHO_CAPABILITY,
    SYNTHETIC_ECHO_CODE,
    SYNTHETIC_ECHO_CONNECTOR_VERSION,
    SyntheticEchoConnector,
    SyntheticEchoContractError,
    SyntheticEchoScenario,
    assert_synthetic_echo_manifest_frozen,
    synthetic_echo_manifest,
    synthetic_echo_manifest_sha256,
)
from rtm_connect.contracts import (
    ConnectActionRequest,
    EvidenceLevel,
    RiskClass,
)
from rtm_connect.evidence import validate_evidence_record


def _action(**overrides):
    values = {
        "action_id": str(uuid.uuid4()),
        "capability": SYNTHETIC_ECHO_CAPABILITY,
        "satellite": "synthetic",
        "target_type": "synthetic.endpoint",
        "target_ref": "unit-test",
        "payload": {"message": "hello", "sequence": 1},
        "requested_by_operator_id": str(uuid.uuid4()),
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "risk_class": RiskClass.R2_BUSINESS_EFFECT,
    }
    values.update(overrides)
    return ConnectActionRequest(**values)


class ConnectC2SyntheticEchoTest(unittest.TestCase):
    def test_manifest_is_frozen(self):
        assert_synthetic_echo_manifest_frozen()
        self.assertEqual(
            synthetic_echo_manifest_sha256(),
            SyntheticEchoConnector.descriptor.manifest_sha256,
        )

    def test_descriptor_is_exact_and_synthetic(self):
        descriptor = SyntheticEchoConnector.descriptor
        self.assertEqual(descriptor.code, SYNTHETIC_ECHO_CODE)
        self.assertEqual(
            descriptor.version,
            SYNTHETIC_ECHO_CONNECTOR_VERSION,
        )
        self.assertEqual(
            descriptor.capabilities,
            (SYNTHETIC_ECHO_CAPABILITY,),
        )
        self.assertTrue(descriptor.synthetic_only)
        self.assertFalse(descriptor.network_used)
        self.assertTrue(descriptor.supports_idempotency)
        self.assertTrue(descriptor.supports_reconciliation)

    def test_manifest_declares_no_credentials_or_network(self):
        manifest = synthetic_echo_manifest()
        self.assertIsNone(manifest["credential_ref"])
        self.assertFalse(manifest["network_used"])
        self.assertTrue(manifest["synthetic_only"])

    def test_success_is_deterministic(self):
        action = _action()
        attempt_id = str(uuid.uuid4())
        connector = SyntheticEchoConnector()
        first = connector.execute(
            action,
            attempt_id=attempt_id,
            scenario=SyntheticEchoScenario.SUCCESS,
        )
        second = connector.execute(
            action,
            attempt_id=attempt_id,
            scenario=SyntheticEchoScenario.SUCCESS,
        )
        self.assertEqual(first, second)

    def test_success_emits_valid_e4_evidence(self):
        result = SyntheticEchoConnector().execute(
            _action(),
            attempt_id=str(uuid.uuid4()),
            scenario=SyntheticEchoScenario.SUCCESS,
        )
        self.assertEqual(result.status, "external_accepted")
        self.assertEqual(
            result.evidence.level,
            EvidenceLevel.E4_RECEIPT_VERIFIED,
        )
        validate_evidence_record(result.evidence)
        self.assertFalse(result.reconciliation_required)
        self.assertFalse(result.metadata["network_used"])

    def test_unknown_emits_e2_and_requires_reconciliation(self):
        result = SyntheticEchoConnector().execute(
            _action(),
            attempt_id=str(uuid.uuid4()),
            scenario=SyntheticEchoScenario.UNKNOWN,
        )
        self.assertEqual(result.status, "unknown")
        self.assertEqual(
            result.evidence.level,
            EvidenceLevel.E2_EXTERNAL_REFERENCE,
        )
        self.assertTrue(result.reconciliation_required)
        validate_evidence_record(result.evidence)

    def test_reconciliation_is_deterministic_and_e4(self):
        action = _action()
        attempt_id = str(uuid.uuid4())
        connector = SyntheticEchoConnector()
        unknown = connector.execute(
            action,
            attempt_id=attempt_id,
            scenario=SyntheticEchoScenario.UNKNOWN,
        )
        first = connector.reconcile(
            action,
            attempt_id=attempt_id,
            external_reference=unknown.external_reference,
        )
        second = connector.reconcile(
            action,
            attempt_id=attempt_id,
            external_reference=unknown.external_reference,
        )
        self.assertEqual(first, second)
        self.assertEqual(first.status, "confirmed")
        self.assertEqual(
            first.evidence.level,
            EvidenceLevel.E4_RECEIPT_VERIFIED,
        )
        validate_evidence_record(first.evidence)

    def test_payload_change_changes_reference(self):
        attempt_id = str(uuid.uuid4())
        first = SyntheticEchoConnector().execute(
            _action(payload={"message": "one"}),
            attempt_id=attempt_id,
            scenario=SyntheticEchoScenario.SUCCESS,
        )
        second = SyntheticEchoConnector().execute(
            _action(
                action_id=first.action_id,
                requested_by_operator_id=str(uuid.uuid4()),
                payload={"message": "two"},
            ),
            attempt_id=attempt_id,
            scenario=SyntheticEchoScenario.SUCCESS,
        )
        self.assertNotEqual(
            first.external_reference,
            second.external_reference,
        )

    def test_attempt_change_changes_reference(self):
        action = _action()
        connector = SyntheticEchoConnector()
        first = connector.execute(
            action,
            attempt_id=str(uuid.uuid4()),
            scenario=SyntheticEchoScenario.SUCCESS,
        )
        second = connector.execute(
            action,
            attempt_id=str(uuid.uuid4()),
            scenario=SyntheticEchoScenario.SUCCESS,
        )
        self.assertNotEqual(
            first.external_reference,
            second.external_reference,
        )

    def test_wrong_capability_is_blocked(self):
        with self.assertRaises(SyntheticEchoContractError):
            SyntheticEchoConnector().execute(
                _action(capability="administration.submit_document"),
                attempt_id=str(uuid.uuid4()),
                scenario=SyntheticEchoScenario.SUCCESS,
            )

    def test_wrong_reference_is_blocked_on_reconciliation(self):
        with self.assertRaises(SyntheticEchoContractError):
            SyntheticEchoConnector().reconcile(
                _action(),
                attempt_id=str(uuid.uuid4()),
                external_reference="OTHER-REF",
            )

    def test_retryable_failure_is_normalized(self):
        result = SyntheticEchoConnector().execute(
            _action(),
            attempt_id=str(uuid.uuid4()),
            scenario=SyntheticEchoScenario.RETRYABLE_FAILURE,
        )
        self.assertEqual(result.status, "retryable_failed")
        self.assertEqual(
            result.evidence.level,
            EvidenceLevel.E1_REQUEST_RECORDED,
        )
        self.assertEqual(result.failure_class, "synthetic_transient")

    def test_permanent_failure_is_normalized(self):
        result = SyntheticEchoConnector().execute(
            _action(),
            attempt_id=str(uuid.uuid4()),
            scenario=SyntheticEchoScenario.PERMANENT_FAILURE,
        )
        self.assertEqual(result.status, "permanent_failed")
        self.assertEqual(result.failure_class, "synthetic_permanent")

    def test_manual_review_is_normalized(self):
        result = SyntheticEchoConnector().execute(
            _action(),
            attempt_id=str(uuid.uuid4()),
            scenario=SyntheticEchoScenario.MANUAL_REVIEW,
        )
        self.assertEqual(result.status, "manual_review")
        self.assertEqual(
            result.failure_class,
            "synthetic_manual_review",
        )

    def test_source_has_no_network_clients(self):
        source = inspect.getsource(
            __import__(
                "rtm_connect.connectors.synthetic_echo",
                fromlist=["*"],
            )
        )
        for forbidden in (
            "import requests",
            "import httpx",
            "import urllib",
            "import socket",
            "import boto3",
            "import stripe",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
