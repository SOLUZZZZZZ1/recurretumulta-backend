"""Contratos inertes de admisión para RTM CONNECT C8.

C8 no contiene un conector de producción.  Estas estructuras solo describen
un candidato revisable y una intención de outbox simulada.  Los contratos
rechazan de forma estructural cualquier permiso de activación, red, secreto o
efecto externo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from rtm_connect.idempotency import canonical_json, sha256_hex


RTM_CONNECT_C8_PRODUCTION_CONTRACTS_VERSION = (
    "rtm_connect_c8_production_contracts_v1_0"
)
C8_ADMISSION_CONTRACT_VERSION = "rtm.connect.c8.admission.v1"
C8_SIMULATED_OUTBOX_CONTRACT_VERSION = "rtm.connect.c8.simulated_outbox.v1"
C8_SYNTHETIC_MARKER = "RTM_C8_SYNTHETIC_ONLY"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_IDEMPOTENCY_KEY_RE = re.compile(r"^rtmc1:[0-9a-f]{64}$")


class ProductionContractError(ValueError):
    """Un candidato C8 intenta ampliar el plano inerte congelado."""


def _uuid(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ProductionContractError(f"{field_name} debe ser UUID textual")
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProductionContractError(f"{field_name} debe ser UUID") from exc


def _sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ProductionContractError(f"{field_name} debe ser SHA-256 textual")
    normalized = value.strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ProductionContractError(f"{field_name} debe ser SHA-256 hexadecimal")
    return normalized


def _commit_sha40(value: str) -> str:
    if not isinstance(value, str):
        raise ProductionContractError("source_commit_sha40 debe ser texto")
    normalized = value.strip().lower()
    if not _COMMIT_SHA40_RE.fullmatch(normalized):
        raise ProductionContractError(
            "source_commit_sha40 debe contener 40 hexadecimales"
        )
    return normalized


def _utc_timestamp(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProductionContractError(f"{field_name} es obligatorio")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProductionContractError(
            f"{field_name} debe ser ISO-8601 UTC"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ProductionContractError(f"{field_name} debe estar en UTC")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _positive_int(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProductionContractError(f"{field_name} debe ser entero positivo")
    return value


def _exact_bool(value: bool, expected: bool, field_name: str) -> bool:
    if type(value) is not bool or value is not expected:
        literal = "true" if expected else "false"
        raise ProductionContractError(f"{field_name} debe ser {literal}")
    return value


@dataclass(frozen=True)
class ProductionAdmissionCandidate:
    """Candidato hash-bound; nunca una autorización de producción real."""

    candidate_id: str
    requested_by_operator_id: str
    source_commit_sha40: str
    build_artifact_sha256: str
    connector_manifest_sha256: str
    provider_contract_sha256: str
    egress_policy_sha256: str
    credential_reference_sha256: str
    schema_snapshot_sha256: str
    test_report_sha256: str
    created_at: str
    expires_at: str
    canary_percent: int
    concurrency: int
    max_simulated_actions_total: int
    max_simulated_actions_per_day: int
    max_payload_bytes: int
    admission_ttl_seconds: int
    simulation_only: bool = True
    external_effects_allowed: bool = False
    live_activation_allowed: bool = False
    human_activation_required: bool = True
    contract_version: str = C8_ADMISSION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _uuid(self.candidate_id, "candidate_id"))
        object.__setattr__(
            self,
            "requested_by_operator_id",
            _uuid(self.requested_by_operator_id, "requested_by_operator_id"),
        )
        object.__setattr__(
            self, "source_commit_sha40", _commit_sha40(self.source_commit_sha40)
        )
        for name in (
            "build_artifact_sha256",
            "connector_manifest_sha256",
            "provider_contract_sha256",
            "egress_policy_sha256",
            "credential_reference_sha256",
            "schema_snapshot_sha256",
            "test_report_sha256",
        ):
            object.__setattr__(self, name, _sha256(getattr(self, name), name))
        object.__setattr__(
            self, "created_at", _utc_timestamp(self.created_at, "created_at")
        )
        object.__setattr__(
            self, "expires_at", _utc_timestamp(self.expires_at, "expires_at")
        )
        created = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        if expires <= created:
            raise ProductionContractError("expires_at debe ser posterior a created_at")

        canary = _positive_int(self.canary_percent, "canary_percent")
        if canary > 5:
            raise ProductionContractError("canary_percent no puede superar 5")
        object.__setattr__(self, "canary_percent", canary)
        concurrency = _positive_int(self.concurrency, "concurrency")
        if concurrency != 1:
            raise ProductionContractError("C8 congela concurrency=1")
        object.__setattr__(self, "concurrency", concurrency)
        for name in (
            "max_simulated_actions_total",
            "max_simulated_actions_per_day",
            "max_payload_bytes",
            "admission_ttl_seconds",
        ):
            object.__setattr__(self, name, _positive_int(getattr(self, name), name))
        if self.max_simulated_actions_per_day > self.max_simulated_actions_total:
            raise ProductionContractError(
                "El límite diario no puede exceder el límite total"
            )
        if self.max_simulated_actions_per_day != 1:
            raise ProductionContractError(
                "C8 congela max_simulated_actions_per_day=1"
            )
        if self.max_simulated_actions_total != 1:
            raise ProductionContractError(
                "C8 congela max_simulated_actions_total=1"
            )
        if self.max_payload_bytes > 1_048_576:
            raise ProductionContractError(
                "C8 limita max_payload_bytes a 1048576"
            )
        if self.admission_ttl_seconds > 86_400:
            raise ProductionContractError(
                "C8 limita admission_ttl_seconds a 86400"
            )
        if (expires - created).total_seconds() > self.admission_ttl_seconds:
            raise ProductionContractError(
                "La vigencia declarada excede admission_ttl_seconds"
            )

        _exact_bool(self.simulation_only, True, "simulation_only")
        _exact_bool(
            self.external_effects_allowed, False, "external_effects_allowed"
        )
        _exact_bool(self.live_activation_allowed, False, "live_activation_allowed")
        _exact_bool(
            self.human_activation_required, True, "human_activation_required"
        )
        if self.contract_version != C8_ADMISSION_CONTRACT_VERSION:
            raise ProductionContractError("Versión de candidato C8 no admitida")


def candidate_fingerprint_material(
    candidate: ProductionAdmissionCandidate,
) -> dict[str, Any]:
    if type(candidate) is not ProductionAdmissionCandidate:
        raise ProductionContractError("C8 exige ProductionAdmissionCandidate exacto")
    return {
        "contract_version": candidate.contract_version,
        "candidate_id": candidate.candidate_id,
        "requested_by_operator_id": candidate.requested_by_operator_id,
        "source_commit_sha40": candidate.source_commit_sha40,
        "hashes": {
            "build_artifact": candidate.build_artifact_sha256,
            "connector_manifest": candidate.connector_manifest_sha256,
            "provider_contract": candidate.provider_contract_sha256,
            "egress_policy": candidate.egress_policy_sha256,
            "credential_reference": candidate.credential_reference_sha256,
            "schema_snapshot": candidate.schema_snapshot_sha256,
            "test_report": candidate.test_report_sha256,
        },
        "created_at": candidate.created_at,
        "expires_at": candidate.expires_at,
        "limits": {
            "canary_percent": candidate.canary_percent,
            "concurrency": candidate.concurrency,
            "max_simulated_actions_total": candidate.max_simulated_actions_total,
            "max_simulated_actions_per_day": (
                candidate.max_simulated_actions_per_day
            ),
            "max_payload_bytes": candidate.max_payload_bytes,
            "admission_ttl_seconds": candidate.admission_ttl_seconds,
        },
        "inert_flags": {
            "simulation_only": candidate.simulation_only,
            "external_effects_allowed": candidate.external_effects_allowed,
            "live_activation_allowed": candidate.live_activation_allowed,
            "human_activation_required": candidate.human_activation_required,
        },
    }


def candidate_sha256(candidate: ProductionAdmissionCandidate) -> str:
    return sha256_hex(canonical_json(candidate_fingerprint_material(candidate)))


def expected_c8_admission_payload(candidate_digest: str) -> dict[str, object]:
    """Único payload CORE permitido para evaluar un candidato sintético."""

    digest = _sha256(candidate_digest, "candidate_sha256")
    return {
        "contract_version": C8_ADMISSION_CONTRACT_VERSION,
        "candidate_sha256": digest,
        "synthetic_marker": C8_SYNTHETIC_MARKER,
        "simulation_only": True,
        "external_effects_allowed": False,
        "live_activation_allowed": False,
        "human_activation_required": True,
    }


class ProductionApprovalRole(str, Enum):
    SECURITY = "security"
    OPERATIONS = "operations"


@dataclass(frozen=True)
class ProductionReleaseApproval:
    """Una aprobación humana inerte; dual control exige ambos roles."""

    approval_id: str
    candidate_id: str
    candidate_sha256: str
    requested_by_operator_id: str
    approver_operator_id: str
    approval_role: ProductionApprovalRole
    approved_at: str
    expires_at: str
    decision: str = "simulation_admission_approved"
    simulation_only: bool = True
    external_effects_allowed: bool = False
    live_activation_allowed: bool = False
    human_activation_required: bool = True

    def __post_init__(self) -> None:
        for name in (
            "approval_id",
            "candidate_id",
            "requested_by_operator_id",
            "approver_operator_id",
        ):
            object.__setattr__(self, name, _uuid(getattr(self, name), name))
        object.__setattr__(
            self,
            "candidate_sha256",
            _sha256(self.candidate_sha256, "candidate_sha256"),
        )
        try:
            role = (
                self.approval_role
                if isinstance(self.approval_role, ProductionApprovalRole)
                else ProductionApprovalRole(str(self.approval_role))
            )
        except ValueError as exc:
            raise ProductionContractError("approval_role C8 no admitido") from exc
        object.__setattr__(self, "approval_role", role)
        object.__setattr__(
            self, "approved_at", _utc_timestamp(self.approved_at, "approved_at")
        )
        object.__setattr__(
            self, "expires_at", _utc_timestamp(self.expires_at, "expires_at")
        )
        approved = datetime.fromisoformat(self.approved_at.replace("Z", "+00:00"))
        expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        if expires <= approved:
            raise ProductionContractError("La aprobación C8 debe tener vigencia positiva")
        if self.requested_by_operator_id == self.approver_operator_id:
            raise ProductionContractError("El solicitante no puede autoaprobar C8")
        if self.decision != "simulation_admission_approved":
            raise ProductionContractError("C8 solo aprueba admisión de simulación")
        _exact_bool(self.simulation_only, True, "simulation_only")
        _exact_bool(
            self.external_effects_allowed, False, "external_effects_allowed"
        )
        _exact_bool(self.live_activation_allowed, False, "live_activation_allowed")
        _exact_bool(
            self.human_activation_required, True, "human_activation_required"
        )


class SimulatedOutboxStatus(str, Enum):
    PREPARED = "prepared"
    CLAIMED = "claimed"
    DRY_RUN_CONFIRMED = "dry_run_confirmed"
    UNKNOWN = "unknown"
    MANUAL_REVIEW = "manual_review"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class SimulatedOutboxIntent:
    """Identidad auditable sin método de envío ni material sensible."""

    intent_id: str
    candidate_id: str
    action_id: str
    authorization_id: str
    candidate_sha256: str
    request_sha256: str
    idempotency_key: str
    status: SimulatedOutboxStatus
    created_at: str
    reconciliation_required: bool
    simulation_only: bool = True
    external_effects_allowed: bool = False
    network_call_performed: bool = False
    secret_resolution_performed: bool = False
    blind_retry_allowed: bool = False
    contract_version: str = C8_SIMULATED_OUTBOX_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in ("intent_id", "candidate_id", "action_id", "authorization_id"):
            object.__setattr__(self, name, _uuid(getattr(self, name), name))
        object.__setattr__(
            self, "candidate_sha256", _sha256(self.candidate_sha256, "candidate_sha256")
        )
        object.__setattr__(
            self, "request_sha256", _sha256(self.request_sha256, "request_sha256")
        )
        if not isinstance(self.idempotency_key, str) or not _IDEMPOTENCY_KEY_RE.fullmatch(
            self.idempotency_key
        ):
            raise ProductionContractError("idempotency_key C8 no válida")
        try:
            status = (
                self.status
                if isinstance(self.status, SimulatedOutboxStatus)
                else SimulatedOutboxStatus(str(self.status))
            )
        except ValueError as exc:
            raise ProductionContractError("Estado de outbox C8 no admitido") from exc
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self, "created_at", _utc_timestamp(self.created_at, "created_at")
        )
        _exact_bool(self.simulation_only, True, "simulation_only")
        _exact_bool(
            self.external_effects_allowed, False, "external_effects_allowed"
        )
        _exact_bool(self.network_call_performed, False, "network_call_performed")
        _exact_bool(
            self.secret_resolution_performed,
            False,
            "secret_resolution_performed",
        )
        _exact_bool(self.blind_retry_allowed, False, "blind_retry_allowed")
        if type(self.reconciliation_required) is not bool:
            raise ProductionContractError("reconciliation_required debe ser booleano")
        expected_reconciliation = status in {
            SimulatedOutboxStatus.UNKNOWN,
            SimulatedOutboxStatus.MANUAL_REVIEW,
        }
        if self.reconciliation_required is not expected_reconciliation:
            raise ProductionContractError(
                "Solo unknown/manual_review exigen reconciliación"
            )
        if self.contract_version != C8_SIMULATED_OUTBOX_CONTRACT_VERSION:
            raise ProductionContractError("Versión de outbox C8 no admitida")


@dataclass(frozen=True)
class ProductionAdmissionAssessment:
    """Resultado deliberadamente NO-GO de la admisión inerte."""

    candidate_sha256: str
    evaluated_at: str
    blocker_codes: tuple[str, ...]
    verdict: str = "no_go"
    simulation_admitted: bool = True
    live_production_admitted: bool = False
    production_effects_available: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_sha256", _sha256(self.candidate_sha256, "candidate_sha256")
        )
        object.__setattr__(
            self, "evaluated_at", _utc_timestamp(self.evaluated_at, "evaluated_at")
        )
        blockers = tuple(dict.fromkeys(str(value).strip() for value in self.blocker_codes))
        if not blockers or any(not value for value in blockers):
            raise ProductionContractError("NO-GO exige blockers normalizados")
        object.__setattr__(self, "blocker_codes", blockers)
        if self.verdict != "no_go":
            raise ProductionContractError("C8 v1 solo admite verdict=no_go")
        _exact_bool(self.simulation_admitted, True, "simulation_admitted")
        _exact_bool(
            self.live_production_admitted, False, "live_production_admitted"
        )
        _exact_bool(
            self.production_effects_available,
            False,
            "production_effects_available",
        )


__all__ = [
    "RTM_CONNECT_C8_PRODUCTION_CONTRACTS_VERSION",
    "C8_ADMISSION_CONTRACT_VERSION",
    "C8_SIMULATED_OUTBOX_CONTRACT_VERSION",
    "C8_SYNTHETIC_MARKER",
    "ProductionApprovalRole",
    "ProductionAdmissionAssessment",
    "ProductionAdmissionCandidate",
    "ProductionContractError",
    "ProductionReleaseApproval",
    "SimulatedOutboxIntent",
    "SimulatedOutboxStatus",
    "candidate_fingerprint_material",
    "candidate_sha256",
    "expected_c8_admission_payload",
]
