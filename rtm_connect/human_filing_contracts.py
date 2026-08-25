"""Contratos fail-closed de A1-S para presentacion humana sintetica.

A1-S ensaya en ``staging`` el control de una operacion humana. No abre una
sede, no transporta documentos, no usa B2 ni credenciales y no contacta a un
proveedor. Los contratos de este modulo solo aceptan fixtures marcados como
sinteticos y producen huellas canonicas reproducibles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping
from uuid import UUID

from rtm_connect.idempotency import canonical_json, sha256_hex


RTM_CONNECT_A1S_CONTRACTS_VERSION = (
    "rtm_connect_a1s_human_filing_contracts_v1_0"
)
HUMAN_FILING_CONTRACT_VERSION = "rtm.connect.a1s.human_filing.v1"
HUMAN_FILING_CODE = "human.filing.a1s"
HUMAN_FILING_CONNECTOR_VERSION = "v1.0"
HUMAN_FILING_CAPABILITY = "administration.submit.human.synthetic"
HUMAN_FILING_SATELLITE = "rtm.human.filing.synthetic"
HUMAN_FILING_TARGET_TYPE = "administration.synthetic.filing"
HUMAN_FILING_TARGET_REF = "synthetic-a1s-administration"
HUMAN_FILING_MARKER = "RTM_A1S_SYNTHETIC_ONLY"
HUMAN_FILING_AUTHORITY_CODE = "rtm.core.authorization"
HUMAN_FILING_AUTHORITY_VERSION = "rtm_core_authority_v1"
HUMAN_FILING_IDEMPOTENCY_PREFIX = "rtma1s:"
HUMAN_FILING_STORAGE_BACKEND = "database_manifest_only"

HUMAN_FILING_FIXED_CHECKLIST = (
    "confirm_synthetic_case_binding",
    "confirm_frozen_core_authority",
    "confirm_synthetic_representation",
    "confirm_exact_package_hash",
    "simulate_human_filing_without_external_contact",
    "capture_synthetic_receipt",
    "verify_receipt_with_independent_principal",
)

HUMAN_FILING_MEMBERSHIP_ROLES = (
    "requester",
    "executor",
    "releaser",
    "verifier",
    "supervisor",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BINDING_CODE_RE = re.compile(r"^rtm-a1s-binding-[0-9a-f]{24}$")
_REPRESENTATION_CODE_RE = re.compile(r"^rtm-a1s-representation-[0-9a-f]{24}$")
_ARTIFACT_CODE_RE = re.compile(r"^rtm-a1s-artifact-[0-9a-f]{24}$")
_IDEMPOTENCY_KEY_RE = re.compile(r"^rtma1s:[0-9a-f]{64}$")
_MEDIA_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]{0,126}$")

_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization_header",
        "b2_bucket",
        "b2_key",
        "client_secret",
        "cookie",
        "credential_ref",
        "endpoint",
        "origin",
        "password",
        "private_key",
        "provider",
        "provider_id",
        "provider_url",
        "raw_token",
        "refresh_token",
        "secret",
        "storage_ref",
    }
)


class HumanFilingContractError(ValueError):
    """Un valor intenta ampliar el alcance sintetico congelado de A1-S."""


class HumanFilingTransitionError(HumanFilingContractError):
    """La maquina de estados A1-S rechaza una transicion."""


class HumanFilingTaskStatus(str, Enum):
    PREPARED = "prepared"
    ASSIGNED = "assigned"
    REVIEWING = "reviewing"
    READY_FOR_RELEASE = "ready_for_release"
    RELEASED = "released"
    IN_PROGRESS = "in_progress"
    AWAITING_RECEIPT = "awaiting_receipt"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RECONCILING = "reconciling"
    RECEIPT_SUBMITTED = "receipt_submitted"
    VERIFIED = "verified"
    COMPLETED = "completed"
    MANUAL_REVIEW = "manual_review"
    PERMANENT_FAILED = "permanent_failed"


class ArtifactKind(str, Enum):
    AUTHORITY_SNAPSHOT = "authority_snapshot"
    REPRESENTATION_EVIDENCE = "representation_evidence"
    FILING_PACKAGE = "filing_package"
    HUMAN_REVIEW_ATTESTATION = "human_review_attestation"
    RELEASE_ATTESTATION = "release_attestation"
    VERIFICATION_PREAPPROVAL_ATTESTATION = (
        "verification_preapproval_attestation"
    )
    SYNTHETIC_SUBMISSION_REPORT = "synthetic_submission_report"
    SYNTHETIC_RECEIPT = "synthetic_receipt"
    VERIFICATION_ATTESTATION = "verification_attestation"
    RECONCILIATION_ATTESTATION = "reconciliation_attestation"


class RepresentationKind(str, Enum):
    SYNTHETIC_POWER_OF_ATTORNEY = "synthetic_power_of_attorney"
    SYNTHETIC_SIGNED_AUTHORIZATION = "synthetic_signed_authorization"
    SYNTHETIC_LEGAL_REPRESENTATIVE_ATTESTATION = (
        "synthetic_legal_representative_attestation"
    )


class HumanFilingApprovalType(str, Enum):
    RELEASE = "release"
    VERIFICATION_PREAPPROVAL = "verification_preapproval"


_HUMAN_FILING_TRANSITIONS: Mapping[HumanFilingTaskStatus, frozenset[HumanFilingTaskStatus]] = {
    HumanFilingTaskStatus.PREPARED: frozenset({HumanFilingTaskStatus.ASSIGNED}),
    HumanFilingTaskStatus.ASSIGNED: frozenset({HumanFilingTaskStatus.REVIEWING}),
    HumanFilingTaskStatus.REVIEWING: frozenset(
        {
            HumanFilingTaskStatus.READY_FOR_RELEASE,
            HumanFilingTaskStatus.MANUAL_REVIEW,
        }
    ),
    HumanFilingTaskStatus.READY_FOR_RELEASE: frozenset(
        {
            HumanFilingTaskStatus.RELEASED,
            HumanFilingTaskStatus.MANUAL_REVIEW,
        }
    ),
    HumanFilingTaskStatus.RELEASED: frozenset(
        {HumanFilingTaskStatus.IN_PROGRESS}
    ),
    HumanFilingTaskStatus.IN_PROGRESS: frozenset(
        {
            HumanFilingTaskStatus.AWAITING_RECEIPT,
            HumanFilingTaskStatus.OUTCOME_UNKNOWN,
            HumanFilingTaskStatus.MANUAL_REVIEW,
        }
    ),
    HumanFilingTaskStatus.AWAITING_RECEIPT: frozenset(
        {
            HumanFilingTaskStatus.RECEIPT_SUBMITTED,
            HumanFilingTaskStatus.OUTCOME_UNKNOWN,
            HumanFilingTaskStatus.MANUAL_REVIEW,
        }
    ),
    HumanFilingTaskStatus.OUTCOME_UNKNOWN: frozenset(
        {
            HumanFilingTaskStatus.RECONCILING,
            HumanFilingTaskStatus.MANUAL_REVIEW,
        }
    ),
    HumanFilingTaskStatus.RECONCILING: frozenset(
        {
            HumanFilingTaskStatus.OUTCOME_UNKNOWN,
            HumanFilingTaskStatus.RECEIPT_SUBMITTED,
            HumanFilingTaskStatus.MANUAL_REVIEW,
            HumanFilingTaskStatus.PERMANENT_FAILED,
        }
    ),
    HumanFilingTaskStatus.RECEIPT_SUBMITTED: frozenset(
        {
            HumanFilingTaskStatus.VERIFIED,
            HumanFilingTaskStatus.MANUAL_REVIEW,
        }
    ),
    HumanFilingTaskStatus.VERIFIED: frozenset(
        {HumanFilingTaskStatus.COMPLETED}
    ),
    HumanFilingTaskStatus.COMPLETED: frozenset(),
    HumanFilingTaskStatus.MANUAL_REVIEW: frozenset(),
    HumanFilingTaskStatus.PERMANENT_FAILED: frozenset(),
}


def _uuid(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise HumanFilingContractError(f"{field_name} debe ser UUID textual")
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HumanFilingContractError(f"{field_name} debe ser UUID") from exc


def _sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise HumanFilingContractError(f"{field_name} debe ser SHA-256 textual")
    normalized = value.strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise HumanFilingContractError(
            f"{field_name} debe ser SHA-256 hexadecimal"
        )
    return normalized


def _timestamp(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HumanFilingContractError(f"{field_name} es obligatorio")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise HumanFilingContractError(
            f"{field_name} debe ser ISO-8601 UTC"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise HumanFilingContractError(f"{field_name} debe estar en UTC")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _exact_true(value: bool, field_name: str) -> None:
    if type(value) is not bool or value is not True:
        raise HumanFilingContractError(f"{field_name} debe ser true")


def _exact_false(value: bool, field_name: str) -> None:
    if type(value) is not bool or value is not False:
        raise HumanFilingContractError(f"{field_name} debe ser false")


def _assert_no_forbidden_payload_fields(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key or "").strip().lower()
            if normalized in _FORBIDDEN_PAYLOAD_KEYS:
                raise HumanFilingContractError(
                    f"{path}.{key} no esta permitido en A1-S"
                )
            _assert_no_forbidden_payload_fields(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_no_forbidden_payload_fields(
                child,
                path=f"{path}[{index}]",
            )


def _synthetic_payload(value: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HumanFilingContractError(f"{field_name} debe ser objeto")
    material = dict(value)
    if material.get("synthetic_marker") != HUMAN_FILING_MARKER:
        raise HumanFilingContractError(
            f"{field_name}.synthetic_marker no coincide"
        )
    if material.get("synthetic_only") is not True:
        raise HumanFilingContractError(
            f"{field_name}.synthetic_only debe ser true"
        )
    _assert_no_forbidden_payload_fields(material, path=field_name)
    try:
        canonical_json(material)
    except (TypeError, ValueError) as exc:
        raise HumanFilingContractError(
            f"{field_name} no es canonicalizable"
        ) from exc
    return material


def canonical_sha256(value: Any) -> str:
    """Huela un valor con la canonicalizacion ya congelada por C1."""

    try:
        return sha256_hex(canonical_json(value))
    except (TypeError, ValueError) as exc:
        raise HumanFilingContractError("Valor no canonicalizable") from exc


def derive_human_filing_idempotency_key(
    scope: str,
    request_material: Mapping[str, Any],
) -> str:
    clean_scope = str(scope or "").strip().lower()
    if not re.fullmatch(r"^[a-z][a-z0-9_.-]{2,95}$", clean_scope):
        raise HumanFilingContractError("scope de idempotencia no valido")
    material = _synthetic_payload(request_material, "request_material")
    digest = canonical_sha256(
        {
            "version": RTM_CONNECT_A1S_CONTRACTS_VERSION,
            "scope": clean_scope,
            "request": material,
        }
    )
    return f"{HUMAN_FILING_IDEMPOTENCY_PREFIX}{digest}"


def validate_human_filing_idempotency_key(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _IDEMPOTENCY_KEY_RE.fullmatch(normalized):
        raise HumanFilingContractError("idempotency_key A1-S no valida")
    return normalized


def _status(value: HumanFilingTaskStatus | str) -> HumanFilingTaskStatus:
    try:
        return value if isinstance(value, HumanFilingTaskStatus) else HumanFilingTaskStatus(value)
    except (TypeError, ValueError) as exc:
        raise HumanFilingTransitionError("Estado A1-S no valido") from exc


def validate_human_filing_transition(
    current: HumanFilingTaskStatus | str,
    target: HumanFilingTaskStatus | str,
) -> HumanFilingTaskStatus:
    """Valida un unico salto; no admite replay ni retry ciego de UNKNOWN."""

    source = _status(current)
    destination = _status(target)
    if destination not in _HUMAN_FILING_TRANSITIONS[source]:
        raise HumanFilingTransitionError(
            f"Transicion A1-S no permitida: {source.value} -> {destination.value}"
        )
    return destination


@dataclass(frozen=True)
class HumanFilingCaseBinding:
    binding_id: str
    tenant_id: str
    case_id: str
    binding_code: str
    case_snapshot_sha256: str
    bound_by_operator_id: str
    bound_at: str
    synthetic_only: bool = True
    contract_version: str = HUMAN_FILING_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in ("binding_id", "tenant_id", "case_id", "bound_by_operator_id"):
            object.__setattr__(self, name, _uuid(getattr(self, name), name))
        code = str(self.binding_code or "").strip().lower()
        if not _BINDING_CODE_RE.fullmatch(code):
            raise HumanFilingContractError("binding_code A1-S no valido")
        object.__setattr__(self, "binding_code", code)
        object.__setattr__(
            self,
            "case_snapshot_sha256",
            _sha256(self.case_snapshot_sha256, "case_snapshot_sha256"),
        )
        object.__setattr__(self, "bound_at", _timestamp(self.bound_at, "bound_at"))
        _exact_true(self.synthetic_only, "synthetic_only")
        if self.contract_version != HUMAN_FILING_CONTRACT_VERSION:
            raise HumanFilingContractError("Version de binding A1-S no admitida")


@dataclass(frozen=True)
class HumanFilingRepresentationEvidence:
    evidence_id: str
    tenant_id: str
    case_binding_id: str
    representation_code: str
    kind: RepresentationKind
    subject_ref_sha256: str
    evidence_sha256: str
    canonical_evidence: Mapping[str, Any]
    recorded_by_operator_id: str
    recorded_at: str
    valid_from: str
    expires_at: str
    synthetic_only: bool = True
    contract_version: str = HUMAN_FILING_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "evidence_id",
            "tenant_id",
            "case_binding_id",
            "recorded_by_operator_id",
        ):
            object.__setattr__(self, name, _uuid(getattr(self, name), name))
        code = str(self.representation_code or "").strip().lower()
        if not _REPRESENTATION_CODE_RE.fullmatch(code):
            raise HumanFilingContractError("representation_code A1-S no valido")
        object.__setattr__(self, "representation_code", code)
        try:
            kind = (
                self.kind
                if isinstance(self.kind, RepresentationKind)
                else RepresentationKind(self.kind)
            )
        except (TypeError, ValueError) as exc:
            raise HumanFilingContractError("kind de representacion no valido") from exc
        object.__setattr__(self, "kind", kind)
        for name in ("subject_ref_sha256", "evidence_sha256"):
            object.__setattr__(self, name, _sha256(getattr(self, name), name))
        material = _synthetic_payload(
            self.canonical_evidence,
            "canonical_evidence",
        )
        if canonical_sha256(material) != self.evidence_sha256:
            raise HumanFilingContractError(
                "evidence_sha256 no coincide con canonical_evidence"
            )
        object.__setattr__(self, "canonical_evidence", material)
        for name in ("recorded_at", "valid_from", "expires_at"):
            object.__setattr__(self, name, _timestamp(getattr(self, name), name))
        valid_from = datetime.fromisoformat(self.valid_from.replace("Z", "+00:00"))
        expires_at = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        recorded_at = datetime.fromisoformat(self.recorded_at.replace("Z", "+00:00"))
        if expires_at <= valid_from or recorded_at >= expires_at:
            raise HumanFilingContractError("Vigencia de representacion no valida")
        _exact_true(self.synthetic_only, "synthetic_only")
        if self.contract_version != HUMAN_FILING_CONTRACT_VERSION:
            raise HumanFilingContractError(
                "Version de evidencia de representacion no admitida"
            )


@dataclass(frozen=True)
class HumanFilingPackage:
    task_id: str
    tenant_id: str
    case_binding_id: str
    representation_evidence_id: str
    action_id: str
    attempt_id: str
    authorization_id: str
    authorization_version: int
    case_snapshot_sha256: str
    representation_evidence_sha256: str
    request_sha256: str
    document_hashes: tuple[str, ...]
    destination_ref: str
    due_at: str
    checklist: tuple[str, ...]
    created_by_operator_id: str
    created_at: str
    synthetic_marker: str = HUMAN_FILING_MARKER
    synthetic_only: bool = True
    network_used: bool = False
    b2_used: bool = False
    provider_contacted: bool = False
    legal_submission_executed: bool = False
    contract_version: str = HUMAN_FILING_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "task_id",
            "tenant_id",
            "case_binding_id",
            "representation_evidence_id",
            "action_id",
            "attempt_id",
            "authorization_id",
            "created_by_operator_id",
        ):
            object.__setattr__(self, name, _uuid(getattr(self, name), name))
        if (
            isinstance(self.authorization_version, bool)
            or not isinstance(self.authorization_version, int)
            or self.authorization_version <= 0
        ):
            raise HumanFilingContractError(
                "authorization_version debe ser entero positivo"
            )
        for name in (
            "case_snapshot_sha256",
            "representation_evidence_sha256",
            "request_sha256",
        ):
            object.__setattr__(self, name, _sha256(getattr(self, name), name))
        hashes = tuple(sorted({_sha256(value, "document_hash") for value in self.document_hashes}))
        if not 1 <= len(hashes) <= 8:
            raise HumanFilingContractError("A1-S exige entre 1 y 8 documentos")
        object.__setattr__(self, "document_hashes", hashes)
        if self.destination_ref != HUMAN_FILING_TARGET_REF:
            raise HumanFilingContractError("destination_ref debe ser sintetico")
        checklist = tuple(str(value or "").strip() for value in self.checklist)
        if checklist != HUMAN_FILING_FIXED_CHECKLIST:
            raise HumanFilingContractError("Checklist A1-S no coincide")
        object.__setattr__(self, "checklist", checklist)
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "due_at", _timestamp(self.due_at, "due_at"))
        created = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        due = datetime.fromisoformat(self.due_at.replace("Z", "+00:00"))
        if due <= created:
            raise HumanFilingContractError("due_at debe ser posterior a created_at")
        if self.synthetic_marker != HUMAN_FILING_MARKER:
            raise HumanFilingContractError("synthetic_marker A1-S no coincide")
        _exact_true(self.synthetic_only, "synthetic_only")
        for name in (
            "network_used",
            "b2_used",
            "provider_contacted",
            "legal_submission_executed",
        ):
            _exact_false(getattr(self, name), name)
        if self.contract_version != HUMAN_FILING_CONTRACT_VERSION:
            raise HumanFilingContractError("Version de paquete A1-S no admitida")


def human_filing_package_material(package: HumanFilingPackage) -> dict[str, Any]:
    if type(package) is not HumanFilingPackage:
        raise HumanFilingContractError("A1-S exige HumanFilingPackage exacto")
    return {
        "contract_version": package.contract_version,
        "task_id": package.task_id,
        "tenant_id": package.tenant_id,
        "case_binding_id": package.case_binding_id,
        "representation_evidence_id": package.representation_evidence_id,
        "action_id": package.action_id,
        "attempt_id": package.attempt_id,
        "authorization_id": package.authorization_id,
        "authorization_version": package.authorization_version,
        "case_snapshot_sha256": package.case_snapshot_sha256,
        "representation_evidence_sha256": package.representation_evidence_sha256,
        "request_sha256": package.request_sha256,
        "document_hashes": list(package.document_hashes),
        "destination_ref": package.destination_ref,
        "due_at": package.due_at,
        "checklist": list(package.checklist),
        "created_by_operator_id": package.created_by_operator_id,
        "created_at": package.created_at,
        "synthetic_marker": package.synthetic_marker,
        "synthetic_only": package.synthetic_only,
        "network_used": package.network_used,
        "b2_used": package.b2_used,
        "provider_contacted": package.provider_contacted,
        "legal_submission_executed": package.legal_submission_executed,
    }


def human_filing_package_sha256(package: HumanFilingPackage) -> str:
    return canonical_sha256(human_filing_package_material(package))


@dataclass(frozen=True)
class HumanFilingArtifact:
    artifact_id: str
    tenant_id: str
    task_id: str
    artifact_code: str
    kind: ArtifactKind
    media_type: str
    sha256: str
    canonical_payload: Mapping[str, Any]
    submitted_by_operator_id: str
    submitted_at: str
    synthetic_only: bool = True
    storage_backend: str = HUMAN_FILING_STORAGE_BACKEND
    contract_version: str = HUMAN_FILING_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in ("artifact_id", "tenant_id", "task_id", "submitted_by_operator_id"):
            object.__setattr__(self, name, _uuid(getattr(self, name), name))
        code = str(self.artifact_code or "").strip().lower()
        if not _ARTIFACT_CODE_RE.fullmatch(code):
            raise HumanFilingContractError("artifact_code A1-S no valido")
        object.__setattr__(self, "artifact_code", code)
        try:
            kind = self.kind if isinstance(self.kind, ArtifactKind) else ArtifactKind(self.kind)
        except (TypeError, ValueError) as exc:
            raise HumanFilingContractError("kind de artefacto no valido") from exc
        object.__setattr__(self, "kind", kind)
        media_type = str(self.media_type or "").strip().lower()
        if not _MEDIA_TYPE_RE.fullmatch(media_type):
            raise HumanFilingContractError("media_type no valido")
        if media_type != "application/json":
            raise HumanFilingContractError(
                "A1-S solo persiste manifiestos JSON sinteticos"
            )
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "sha256", _sha256(self.sha256, "sha256"))
        material = _synthetic_payload(self.canonical_payload, "canonical_payload")
        if canonical_sha256(material) != self.sha256:
            raise HumanFilingContractError(
                "sha256 no coincide con canonical_payload"
            )
        object.__setattr__(self, "canonical_payload", material)
        object.__setattr__(
            self,
            "submitted_at",
            _timestamp(self.submitted_at, "submitted_at"),
        )
        _exact_true(self.synthetic_only, "synthetic_only")
        if self.storage_backend != HUMAN_FILING_STORAGE_BACKEND:
            raise HumanFilingContractError("A1-S prohibe almacenamiento externo")
        if self.contract_version != HUMAN_FILING_CONTRACT_VERSION:
            raise HumanFilingContractError("Version de artefacto A1-S no admitida")


__all__ = [
    "RTM_CONNECT_A1S_CONTRACTS_VERSION",
    "HUMAN_FILING_ARTIFACT_KINDS",
    "HUMAN_FILING_AUTHORITY_CODE",
    "HUMAN_FILING_AUTHORITY_VERSION",
    "HUMAN_FILING_CAPABILITY",
    "HUMAN_FILING_CODE",
    "HUMAN_FILING_CONNECTOR_VERSION",
    "HUMAN_FILING_CONTRACT_VERSION",
    "HUMAN_FILING_FIXED_CHECKLIST",
    "HUMAN_FILING_IDEMPOTENCY_PREFIX",
    "HUMAN_FILING_MARKER",
    "HUMAN_FILING_MEMBERSHIP_ROLES",
    "HUMAN_FILING_SATELLITE",
    "HUMAN_FILING_STORAGE_BACKEND",
    "HUMAN_FILING_TARGET_REF",
    "HUMAN_FILING_TARGET_TYPE",
    "ArtifactKind",
    "HumanFilingArtifact",
    "HumanFilingApprovalType",
    "HumanFilingCaseBinding",
    "HumanFilingContractError",
    "HumanFilingPackage",
    "HumanFilingRepresentationEvidence",
    "HumanFilingTaskStatus",
    "HumanFilingTransitionError",
    "RepresentationKind",
    "canonical_sha256",
    "derive_human_filing_idempotency_key",
    "human_filing_package_material",
    "human_filing_package_sha256",
    "validate_human_filing_idempotency_key",
    "validate_human_filing_transition",
]


HUMAN_FILING_ARTIFACT_KINDS = tuple(kind.value for kind in ArtifactKind)
