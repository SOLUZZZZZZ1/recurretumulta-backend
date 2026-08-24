from __future__ import annotations

import unittest
from dataclasses import replace

from rtm_connect.assisted_legal_policy import (
    ASSISTED_LEGAL_AUTHORITY_CODE,
    ASSISTED_LEGAL_AUTHORITY_VERSION,
    ASSISTED_LEGAL_CAPABILITY,
    ASSISTED_LEGAL_CODE,
    ASSISTED_LEGAL_CONNECTOR_VERSION,
    ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
    ASSISTED_LEGAL_SATELLITE,
    ASSISTED_LEGAL_TARGET_REF,
    ASSISTED_LEGAL_TARGET_TYPE,
    expected_c7_payload,
)
from rtm_connect.connectors.assisted_legal import (
    ASSISTED_LEGAL_FIXED_CHECKLIST,
    AssistedLegalConnector,
    AssistedReceiptSubmission,
    AssistedReceiptVerificationError,
    assert_assisted_legal_manifest_frozen,
    assisted_legal_manifest,
    assisted_legal_manifest_sha256,
)
from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    RiskClass,
)
from rtm_connect.idempotency import derive_idempotency_key, payload_sha256


ACTION_ID = "11111111-1111-4111-8111-111111111111"
REQUESTER_ID = "22222222-2222-4222-8222-222222222222"
APPROVER_ONE = "33333333-3333-4333-8333-333333333333"
APPROVER_TWO = "44444444-4444-4444-8444-444444444444"
AUTHORIZATION_ID = "55555555-5555-4555-8555-555555555555"
ATTEMPT_ID = "66666666-6666-4666-8666-666666666666"
DUE_AT = "2026-01-02T10:00:00Z"
EXPECTED_REFERENCE = f"SYN-C7-ASSISTED-{ACTION_ID}"


def action(**overrides) -> ConnectActionRequest:
    values = {
        "action_id": ACTION_ID,
        "capability": ASSISTED_LEGAL_CAPABILITY,
        "satellite": ASSISTED_LEGAL_SATELLITE,
        "target_type": ASSISTED_LEGAL_TARGET_TYPE,
        "target_ref": ASSISTED_LEGAL_TARGET_REF,
        "payload": expected_c7_payload(),
        "requested_by_operator_id": REQUESTER_ID,
        "requested_at": "2026-01-01T10:00:00Z",
        "risk_class": RiskClass.R4_CRITICAL_REGULATED,
        "document_hashes": ("a" * 64, "b" * 64),
        "requires_dual_control": True,
    }
    values.update(overrides)
    return ConnectActionRequest(**values)


def grant(item: ConnectActionRequest, **overrides) -> AuthorizationGrant:
    values = {
        "authorization_id": AUTHORIZATION_ID,
        "action_id": item.action_id,
        "authority_code": ASSISTED_LEGAL_AUTHORITY_CODE,
        "authority_version": ASSISTED_LEGAL_AUTHORITY_VERSION,
        "decision": "approved_frozen",
        "payload_sha256": payload_sha256(item),
        "idempotency_key": derive_idempotency_key(
            item,
            authority_scope=ASSISTED_LEGAL_AUTHORITY_CODE,
        ),
        "required_evidence_level": EvidenceLevel.E4_RECEIPT_VERIFIED,
        "authorized_connector_modes": (ConnectorMode.ASSISTED,),
        "approved_by_operator_ids": (APPROVER_ONE, APPROVER_TWO),
        "authorized_at": "2026-01-01T10:05:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
        "legal_effect_authorized": True,
    }
    values.update(overrides)
    return AuthorizationGrant(**values)


def package():
    item = action()
    auth = grant(item)
    return item, auth, AssistedLegalConnector().build_package(
        item,
        auth,
        attempt_id=ATTEMPT_ID,
        due_at=DUE_AT,
    )


class ConnectC7AssistedConnectorTest(unittest.TestCase):
    def test_descriptor_is_exact_r4_assisted_and_network_free(self):
        descriptor = AssistedLegalConnector.descriptor
        self.assertEqual(descriptor.code, ASSISTED_LEGAL_CODE)
        self.assertEqual(descriptor.version, ASSISTED_LEGAL_CONNECTOR_VERSION)
        self.assertEqual(descriptor.mode, ConnectorMode.ASSISTED)
        self.assertEqual(descriptor.risk_ceiling, RiskClass.R4_CRITICAL_REGULATED)
        self.assertEqual(descriptor.capabilities, (ASSISTED_LEGAL_CAPABILITY,))
        self.assertTrue(descriptor.synthetic_only)
        self.assertFalse(descriptor.network_used)
        self.assertTrue(descriptor.supports_reconciliation)

    def test_manifest_is_frozen_and_defensively_copied(self):
        assert_assisted_legal_manifest_frozen()
        self.assertEqual(len(assisted_legal_manifest_sha256()), 64)
        first = assisted_legal_manifest()
        first["fixed_checklist"].append("untrusted")
        self.assertNotIn("untrusted", assisted_legal_manifest()["fixed_checklist"])

    def test_package_is_deterministic_and_hash_bound(self):
        item = action()
        auth = grant(item)
        connector = AssistedLegalConnector()
        one = connector.build_package(
            item, auth, attempt_id=ATTEMPT_ID, due_at=DUE_AT
        )
        two = connector.build_package(
            item, auth, attempt_id=ATTEMPT_ID, due_at=DUE_AT
        )
        self.assertEqual(one, two)
        self.assertEqual(one.request_sha256, payload_sha256(item))
        self.assertEqual(len(one.package_sha256), 64)
        self.assertEqual(len(one.human_gate_sha256), 64)

    def test_package_contains_hashes_fixed_checklist_and_no_free_text(self):
        item, _auth, built = package()
        self.assertEqual(built.document_hashes, item.document_hashes)
        self.assertEqual(built.checklist, ASSISTED_LEGAL_FIXED_CHECKLIST)
        self.assertEqual(
            built.human_final_gate,
            ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
        )
        self.assertNotIn("payload", built.manifest)
        self.assertNotIn("instructions", built.manifest)
        self.assertNotIn("document_body", built.manifest)
        self.assertFalse(built.manifest["network_used"])
        self.assertFalse(built.manifest["routes_published"])
        self.assertIsNone(built.manifest["credential_ref"])
        self.assertFalse(built.manifest["legal_submission_executed"])
        self.assertFalse(built.manifest["external_effects_executed"])

    def test_due_attempt_and_document_changes_change_package_hash(self):
        item = action()
        auth = grant(item)
        connector = AssistedLegalConnector()
        baseline = connector.build_package(
            item, auth, attempt_id=ATTEMPT_ID, due_at=DUE_AT
        )
        later = connector.build_package(
            item,
            auth,
            attempt_id=ATTEMPT_ID,
            due_at="2026-01-03T10:00:00Z",
        )
        other_attempt = connector.build_package(
            item,
            auth,
            attempt_id="77777777-7777-4777-8777-777777777777",
            due_at=DUE_AT,
        )
        changed = action(document_hashes=("c" * 64,))
        changed_package = connector.build_package(
            changed,
            grant(changed),
            attempt_id=ATTEMPT_ID,
            due_at=DUE_AT,
        )
        self.assertNotEqual(baseline.package_sha256, later.package_sha256)
        self.assertNotEqual(baseline.package_sha256, other_attempt.package_sha256)
        self.assertNotEqual(baseline.package_sha256, changed_package.package_sha256)

    def test_authority_tuple_is_checked_before_package(self):
        item = action()
        auth = grant(item)
        with self.assertRaises(RuntimeError):
            AssistedLegalConnector().build_package(
                item,
                replace(auth, authorized_connector_modes=(ConnectorMode.MANUAL,)),
                attempt_id=ATTEMPT_ID,
                due_at=DUE_AT,
            )

    def test_capture_emits_e3_tied_to_synthetic_receipt(self):
        item, auth, built = package()
        evidence = AssistedLegalConnector().capture_receipt(
            item,
            auth,
            attempt_id=ATTEMPT_ID,
            submission=AssistedReceiptSubmission(
                receipt_sha256="c" * 64,
                storage_ref="synthetic://assisted-legal/test/receipt.json",
                external_reference=EXPECTED_REFERENCE,
                package_sha256=built.package_sha256,
                human_gate_sha256=built.human_gate_sha256,
                human_final_gate=ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
                witnessed_at="2026-01-02T10:01:00Z",
                mime="application/json",
                size_bytes=512,
            ),
        )
        self.assertEqual(evidence.level, EvidenceLevel.E3_RECEIPT_CAPTURED)
        self.assertEqual(evidence.request_sha256, payload_sha256(item))

    def test_capture_rejects_synthetic_reference_from_another_action(self):
        item, auth, built = package()
        submission = AssistedReceiptSubmission(
            receipt_sha256="c" * 64,
            storage_ref="synthetic://assisted-legal/test/receipt.json",
            external_reference=(
                "SYN-C7-ASSISTED-77777777-7777-4777-8777-777777777777"
            ),
            package_sha256=built.package_sha256,
            human_gate_sha256=built.human_gate_sha256,
            human_final_gate=ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
            witnessed_at="2026-01-02T10:01:00Z",
            mime="application/json",
            size_bytes=512,
        )
        with self.assertRaises(RuntimeError):
            AssistedLegalConnector().capture_receipt(
                item,
                auth,
                attempt_id=ATTEMPT_ID,
                submission=submission,
            )

    def test_verify_emits_e4_and_binds_package_and_gate(self):
        item, auth, built = package()
        verification = AssistedLegalConnector().verify_receipt(
            item,
            auth,
            attempt_id=ATTEMPT_ID,
            receipt_sha256="c" * 64,
            storage_ref="synthetic://assisted-legal/test/receipt.json",
            external_reference=EXPECTED_REFERENCE,
            package_sha256=built.package_sha256,
            human_gate_sha256=built.human_gate_sha256,
            observed_receipt_sha256="c" * 64,
            observed_external_reference=EXPECTED_REFERENCE,
            observed_package_sha256=built.package_sha256,
            observed_human_gate_sha256=built.human_gate_sha256,
            human_final_gate=ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
            verified_at="2026-01-02T10:02:00Z",
        )
        self.assertEqual(
            verification.evidence.level,
            EvidenceLevel.E4_RECEIPT_VERIFIED,
        )
        self.assertEqual(verification.package_sha256, built.package_sha256)
        self.assertEqual(
            verification.human_gate_sha256,
            built.human_gate_sha256,
        )

    def test_wrong_receipt_package_gate_or_reference_is_blocked(self):
        item, auth, built = package()
        baseline = {
            "attempt_id": ATTEMPT_ID,
            "receipt_sha256": "c" * 64,
            "storage_ref": "synthetic://assisted-legal/test/receipt.json",
            "external_reference": EXPECTED_REFERENCE,
            "package_sha256": built.package_sha256,
            "human_gate_sha256": built.human_gate_sha256,
            "observed_receipt_sha256": "c" * 64,
            "observed_external_reference": EXPECTED_REFERENCE,
            "observed_package_sha256": built.package_sha256,
            "observed_human_gate_sha256": built.human_gate_sha256,
            "human_final_gate": ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
            "verified_at": "2026-01-02T10:02:00Z",
        }
        variants = (
            {"observed_receipt_sha256": "d" * 64},
            {"observed_package_sha256": "e" * 64},
            {"observed_human_gate_sha256": "f" * 64},
            {"observed_external_reference": "SYN-C7-ASSISTED-OTHER"},
            {"human_final_gate": "UNTRUSTED"},
        )
        for mutation in variants:
            with self.subTest(mutation=mutation):
                values = dict(baseline)
                values.update(mutation)
                with self.assertRaises(AssistedReceiptVerificationError):
                    AssistedLegalConnector().verify_receipt(
                        item,
                        auth,
                        **values,
                    )

    def test_real_storage_reference_and_effect_are_blocked(self):
        _item, _auth, built = package()
        baseline = {
            "receipt_sha256": "c" * 64,
            "storage_ref": "synthetic://assisted-legal/test/receipt.json",
            "external_reference": EXPECTED_REFERENCE,
            "package_sha256": built.package_sha256,
            "human_gate_sha256": built.human_gate_sha256,
            "human_final_gate": ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
            "witnessed_at": "2026-01-02T10:01:00Z",
            "mime": "application/json",
            "size_bytes": 512,
        }
        for mutation in (
            {"storage_ref": "https://example.com/receipt.json"},
            {"external_reference": "REAL-RECEIPT"},
            {"legal_submission_executed": True},
        ):
            with self.subTest(mutation=mutation):
                values = dict(baseline)
                values.update(mutation)
                with self.assertRaises(ValueError):
                    AssistedReceiptSubmission(**values)


if __name__ == "__main__":
    unittest.main()
