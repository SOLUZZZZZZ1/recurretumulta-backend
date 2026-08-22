from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from rtm_connect.authority import (
    AuthorityValidationError,
    assert_connector_output_has_no_legal_decision,
    validate_execution_authority,
)
from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    EvidenceRecord,
    RiskClass,
)
from rtm_connect.evidence import confirmation_gate
from rtm_connect.idempotency import (
    derive_idempotency_key,
    payload_sha256,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _action(risk=RiskClass.R3_LEGAL_OR_FINANCIAL):
    return ConnectActionRequest(
        action_id=str(uuid.uuid4()),
        capability="administration.submit_document",
        satellite="administration",
        target_type="public_registry",
        target_ref="synthetic",
        payload={"document_type": "synthetic"},
        requested_by_operator_id=str(uuid.uuid4()),
        requested_at=_now(),
        risk_class=risk,
        requires_dual_control=(
            risk is RiskClass.R4_CRITICAL_REGULATED
        ),
    )


def _grant(action, approvers=1):
    return AuthorizationGrant(
        authorization_id=str(uuid.uuid4()),
        action_id=action.action_id,
        authority_code="rtm.core.authorization",
        authority_version="rtm_core_authority_v1",
        decision="approved_frozen",
        payload_sha256=payload_sha256(action),
        idempotency_key=derive_idempotency_key(
            action,
            authority_scope="rtm.core.authorization",
        ),
        required_evidence_level=EvidenceLevel.E4_RECEIPT_VERIFIED,
        authorized_connector_modes=(ConnectorMode.MANUAL,),
        approved_by_operator_ids=tuple(
            str(uuid.uuid4()) for _ in range(approvers)
        ),
        authorized_at=_now(),
        expires_at=(
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
        legal_effect_authorized=True,
    )


class ConnectC0AuthorityEvidenceTest(unittest.TestCase):
    def test_valid_frozen_authority_allows_mode(self):
        action = _action()
        validate_execution_authority(
            action,
            _grant(action),
            connector_mode=ConnectorMode.MANUAL,
        )

    def test_wrong_connector_mode_is_blocked(self):
        action = _action()
        with self.assertRaises(AuthorityValidationError):
            validate_execution_authority(
                action,
                _grant(action),
                connector_mode=ConnectorMode.API,
            )

    def test_payload_mismatch_is_blocked(self):
        action = _action()
        other = _action()
        grant = _grant(action)
        with self.assertRaises(AuthorityValidationError):
            validate_execution_authority(
                other,
                grant,
                connector_mode=ConnectorMode.MANUAL,
            )

    def test_connector_cannot_claim_legal_decision(self):
        with self.assertRaises(AuthorityValidationError):
            assert_connector_output_has_no_legal_decision(
                {"legal_basis": "connector-decided"}
            )

    def test_weak_evidence_blocks_r3_confirmation(self):
        action = _action()
        grant = _grant(action)
        evidence = EvidenceRecord(
            level=EvidenceLevel.E2_EXTERNAL_REFERENCE,
            request_sha256=payload_sha256(action),
            external_reference="SYNTHETIC",
        )
        self.assertFalse(
            confirmation_gate(action, grant, evidence).allowed
        )

    def test_e4_allows_r3_confirmation(self):
        action = _action()
        grant = _grant(action)
        evidence = EvidenceRecord(
            level=EvidenceLevel.E4_RECEIPT_VERIFIED,
            request_sha256=payload_sha256(action),
            external_reference="SYNTHETIC",
            receipt_sha256="d" * 64,
            receipt_storage_ref="b2://synthetic/receipt.pdf",
            verified_at=_now(),
            verification_method="synthetic_check",
        )
        self.assertTrue(
            confirmation_gate(action, grant, evidence).allowed
        )

    def test_r4_requires_two_approvers_at_confirmation(self):
        action = _action(RiskClass.R4_CRITICAL_REGULATED)
        grant = _grant(action, approvers=1)
        evidence = EvidenceRecord(
            level=EvidenceLevel.E4_RECEIPT_VERIFIED,
            request_sha256=payload_sha256(action),
            external_reference="SYNTHETIC",
            receipt_sha256="e" * 64,
            receipt_storage_ref="b2://synthetic/receipt.pdf",
            verified_at=_now(),
            verification_method="synthetic_check",
        )
        self.assertFalse(
            confirmation_gate(action, grant, evidence).allowed
        )


if __name__ == "__main__":
    unittest.main()
