"""Niveles de evidencia y puerta de confirmación de RTM CONNECT C0."""

from __future__ import annotations

from dataclasses import dataclass

from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    EvidenceLevel,
    EvidenceRecord,
    RiskClass,
)


RTM_CONNECT_EVIDENCE_VERSION = "rtm_connect_evidence_v1_0"

_LEVEL_ORDER = {
    EvidenceLevel.E0_NONE: 0,
    EvidenceLevel.E1_REQUEST_RECORDED: 1,
    EvidenceLevel.E2_EXTERNAL_REFERENCE: 2,
    EvidenceLevel.E3_RECEIPT_CAPTURED: 3,
    EvidenceLevel.E4_RECEIPT_VERIFIED: 4,
}

_MINIMUM_BY_RISK = {
    RiskClass.R0_OBSERVATION: EvidenceLevel.E1_REQUEST_RECORDED,
    RiskClass.R1_LOW_REVERSIBLE: EvidenceLevel.E2_EXTERNAL_REFERENCE,
    RiskClass.R2_BUSINESS_EFFECT: EvidenceLevel.E3_RECEIPT_CAPTURED,
    RiskClass.R3_LEGAL_OR_FINANCIAL: EvidenceLevel.E4_RECEIPT_VERIFIED,
    RiskClass.R4_CRITICAL_REGULATED: EvidenceLevel.E4_RECEIPT_VERIFIED,
}


@dataclass(frozen=True)
class ConfirmationGate:
    allowed: bool
    minimum_required: EvidenceLevel
    reason: str


def minimum_evidence_for_risk(risk: RiskClass) -> EvidenceLevel:
    return _MINIMUM_BY_RISK[risk]


def evidence_satisfies(
    actual: EvidenceLevel,
    required: EvidenceLevel,
) -> bool:
    return _LEVEL_ORDER[actual] >= _LEVEL_ORDER[required]


def validate_evidence_record(evidence: EvidenceRecord) -> None:
    if _LEVEL_ORDER[evidence.level] >= 1 and not evidence.request_sha256:
        raise ValueError("E1+ exige hash de la solicitud")
    if _LEVEL_ORDER[evidence.level] >= 2 and not evidence.external_reference:
        raise ValueError("E2+ exige referencia externa")
    if _LEVEL_ORDER[evidence.level] >= 3:
        if not evidence.receipt_sha256 or not evidence.receipt_storage_ref:
            raise ValueError("E3+ exige justificante capturado")
    if _LEVEL_ORDER[evidence.level] >= 4:
        if not evidence.verified_at or not evidence.verification_method:
            raise ValueError("E4 exige verificación del justificante")


def confirmation_gate(
    action: ConnectActionRequest,
    authorization: AuthorizationGrant,
    evidence: EvidenceRecord,
) -> ConfirmationGate:
    required = max(
        (
            minimum_evidence_for_risk(action.risk_class),
            authorization.required_evidence_level,
        ),
        key=lambda level: _LEVEL_ORDER[level],
    )
    try:
        validate_evidence_record(evidence)
    except ValueError as exc:
        return ConfirmationGate(False, required, str(exc))
    if not evidence_satisfies(evidence.level, required):
        return ConfirmationGate(
            False,
            required,
            "Nivel de evidencia insuficiente",
        )
    if (
        action.risk_class is RiskClass.R4_CRITICAL_REGULATED
        and len(authorization.approved_by_operator_ids) < 2
    ):
        return ConfirmationGate(
            False,
            required,
            "R4 exige dos aprobadores distintos",
        )
    return ConfirmationGate(True, required, "Evidencia suficiente")


__all__ = [
    "RTM_CONNECT_EVIDENCE_VERSION",
    "ConfirmationGate",
    "confirmation_gate",
    "evidence_satisfies",
    "minimum_evidence_for_risk",
    "validate_evidence_record",
]
