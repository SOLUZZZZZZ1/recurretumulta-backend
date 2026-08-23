from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from rtm_connect.connectors.manual_handoff import (
    MANUAL_HANDOFF_CAPABILITY,
    MANUAL_HANDOFF_CODE,
    MANUAL_HANDOFF_CONNECTOR_VERSION,
    ManualHandoffConnector,
    ManualHandoffContractError,
    ManualReceiptSubmission,
    ManualReceiptVerificationError,
    assert_manual_handoff_manifest_frozen,
    manual_handoff_manifest_sha256,
)
from rtm_connect.contracts import (
    ConnectActionRequest,
    EvidenceLevel,
    RiskClass,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _action(**overrides):
    values = {
        "action_id": str(uuid.uuid4()),
        "capability": MANUAL_HANDOFF_CAPABILITY,
        "satellite": "administration",
        "target_type": "public_registry",
        "target_ref": "synthetic",
        "payload": {"document_type": "synthetic"},
        "requested_by_operator_id": str(uuid.uuid4()),
        "requested_at": _now(),
        "risk_class": RiskClass.R3_LEGAL_OR_FINANCIAL,
        "document_hashes": ("a" * 64,),
    }
    values.update(overrides)
    return ConnectActionRequest(**values)


class ConnectC3ManualConnectorTest(unittest.TestCase):
    def test_descriptor_is_exact(self):
        d = ManualHandoffConnector.descriptor
        self.assertEqual(d.code, MANUAL_HANDOFF_CODE)
        self.assertEqual(d.version, MANUAL_HANDOFF_CONNECTOR_VERSION)

    def test_descriptor_is_synthetic_and_network_free(self):
        d = ManualHandoffConnector.descriptor
        self.assertTrue(d.synthetic_only)
        self.assertFalse(d.network_used)

    def test_manifest_is_frozen(self):
        assert_manual_handoff_manifest_frozen()
        self.assertEqual(len(manual_handoff_manifest_sha256()), 64)

    def test_package_is_deterministic(self):
        action = _action()
        attempt = str(uuid.uuid4())
        due = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat()
        adapter = ManualHandoffConnector()
        one = adapter.build_package(
            action,
            attempt_id=attempt,
            due_at=due,
            instructions="Presentar paquete sintético y guardar recibo.",
        )
        two = adapter.build_package(
            action,
            attempt_id=attempt,
            due_at=due,
            instructions="Presentar paquete sintético y guardar recibo.",
        )
        self.assertEqual(one.package_sha256, two.package_sha256)

    def test_due_change_changes_package_hash(self):
        action = _action()
        attempt = str(uuid.uuid4())
        adapter = ManualHandoffConnector()
        one = adapter.build_package(
            action,
            attempt_id=attempt,
            due_at=(
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat(),
            instructions="Presentar paquete sintético y guardar recibo.",
        )
        two = adapter.build_package(
            action,
            attempt_id=attempt,
            due_at=(
                datetime.now(timezone.utc) + timedelta(hours=2)
            ).isoformat(),
            instructions="Presentar paquete sintético y guardar recibo.",
        )
        self.assertNotEqual(one.package_sha256, two.package_sha256)

    def test_package_declares_no_effect(self):
        action = _action()
        package = ManualHandoffConnector().build_package(
            action,
            attempt_id=str(uuid.uuid4()),
            due_at=(
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat(),
            instructions="Presentar paquete sintético y guardar recibo.",
        )
        self.assertFalse(package.manifest["network_used"])
        self.assertFalse(package.manifest["external_effects_executed"])

    def test_wrong_capability_is_blocked(self):
        with self.assertRaises(ManualHandoffContractError):
            ManualHandoffConnector().build_package(
                _action(capability="synthetic.echo"),
                attempt_id=str(uuid.uuid4()),
                due_at=(
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
                instructions="Presentar paquete sintético y guardar recibo.",
            )

    def test_r4_is_blocked(self):
        with self.assertRaises(ManualHandoffContractError):
            ManualHandoffConnector().build_package(
                _action(
                    risk_class=RiskClass.R4_CRITICAL_REGULATED,
                    requires_dual_control=True,
                ),
                attempt_id=str(uuid.uuid4()),
                due_at=(
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
                instructions="Presentar paquete sintético y guardar recibo.",
            )

    def test_capture_emits_e3(self):
        evidence = ManualHandoffConnector().capture_receipt(
            _action(),
            attempt_id=str(uuid.uuid4()),
            submission=ManualReceiptSubmission(
                receipt_sha256="b" * 64,
                storage_ref="synthetic://manual-handoff/a/receipt.pdf",
                external_reference="SYN-MANUAL-TEST",
                presented_at=_now(),
                mime="application/pdf",
                size_bytes=1024,
            ),
        )
        self.assertEqual(evidence.level, EvidenceLevel.E3_RECEIPT_CAPTURED)

    def test_verify_emits_e4(self):
        verification = ManualHandoffConnector().verify_receipt(
            _action(),
            attempt_id=str(uuid.uuid4()),
            receipt_sha256="c" * 64,
            storage_ref="synthetic://manual-handoff/a/receipt.pdf",
            external_reference="SYN-MANUAL-TEST",
            observed_receipt_sha256="c" * 64,
            observed_external_reference="SYN-MANUAL-TEST",
            verified_at=_now(),
        )
        self.assertEqual(
            verification.evidence.level,
            EvidenceLevel.E4_RECEIPT_VERIFIED,
        )

    def test_wrong_hash_is_blocked(self):
        with self.assertRaises(ManualReceiptVerificationError):
            ManualHandoffConnector().verify_receipt(
                _action(),
                attempt_id=str(uuid.uuid4()),
                receipt_sha256="c" * 64,
                storage_ref="synthetic://manual-handoff/a/receipt.pdf",
                external_reference="SYN-MANUAL-TEST",
                observed_receipt_sha256="d" * 64,
                observed_external_reference="SYN-MANUAL-TEST",
                verified_at=_now(),
            )

    def test_real_storage_is_blocked(self):
        with self.assertRaises(ValueError):
            ManualReceiptSubmission(
                receipt_sha256="b" * 64,
                storage_ref="https://example.com/receipt.pdf",
                external_reference="SYN-MANUAL-TEST",
                presented_at=_now(),
                mime="application/pdf",
                size_bytes=1024,
            )


if __name__ == "__main__":
    unittest.main()
