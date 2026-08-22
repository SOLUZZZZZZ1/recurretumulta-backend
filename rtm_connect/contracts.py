"""Contratos autoritativos de RTM CONNECT C0.

Las estructuras son inmutables y no contienen credenciales. CORE emite una
orden y una autorización congelada; CONNECT solo puede ejecutar ese alcance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping
from uuid import UUID


RTM_CONNECT_CONTRACT_VERSION = "rtm_connect_contract_v1_0"

_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEYS = {
    "password",
    "raw_token",
    "access_token",
    "refresh_token",
    "api_key",
    "private_key",
    "client_secret",
    "cookie",
    "authorization_header",
}


class ConnectorMode(str, Enum):
    API = "api"
    WEBHOOK = "webhook"
    POLLING = "polling"
    BATCH = "batch"
    ASSISTED = "assisted"
    MANUAL = "manual"


class EvidenceLevel(str, Enum):
    E0_NONE = "E0_none"
    E1_REQUEST_RECORDED = "E1_request_recorded"
    E2_EXTERNAL_REFERENCE = "E2_external_reference"
    E3_RECEIPT_CAPTURED = "E3_receipt_captured"
    E4_RECEIPT_VERIFIED = "E4_receipt_verified"


class RiskClass(str, Enum):
    R0_OBSERVATION = "R0_observation"
    R1_LOW_REVERSIBLE = "R1_low_reversible"
    R2_BUSINESS_EFFECT = "R2_business_effect"
    R3_LEGAL_OR_FINANCIAL = "R3_legal_or_financial"
    R4_CRITICAL_REGULATED = "R4_critical_regulated"


def _parse_uuid(value: str, field_name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{field_name} debe ser UUID") from exc


def _parse_timestamp(value: str, field_name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{field_name} es obligatorio")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} debe ser ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} debe incluir zona horaria")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_sha256(value: str, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} debe ser SHA-256 hexadecimal")
    return normalized


def _assert_no_secret_fields(value: Any, *, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in _SECRET_KEYS:
                raise ValueError(
                    f"{path}.{key} no puede contener una credencial"
                )
            _assert_no_secret_fields(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_no_secret_fields(child, path=f"{path}[{index}]")


@dataclass(frozen=True)
class ConnectActionRequest:
    action_id: str
    capability: str
    satellite: str
    target_type: str
    target_ref: str
    payload: Mapping[str, Any]
    requested_by_operator_id: str
    requested_at: str
    risk_class: RiskClass
    document_hashes: tuple[str, ...] = ()
    case_id: str | None = None
    correlation_id: str | None = None
    requires_dual_control: bool = False
    contract_version: str = RTM_CONNECT_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "action_id",
            _parse_uuid(self.action_id, "action_id"),
        )
        if self.case_id is not None:
            object.__setattr__(
                self,
                "case_id",
                _parse_uuid(self.case_id, "case_id"),
            )
        object.__setattr__(
            self,
            "requested_by_operator_id",
            _parse_uuid(
                self.requested_by_operator_id,
                "requested_by_operator_id",
            ),
        )
        capability = str(self.capability or "").strip().lower()
        if not _CAPABILITY_RE.fullmatch(capability):
            raise ValueError("capability no cumple el formato RTM")
        object.__setattr__(self, "capability", capability)

        satellite = str(self.satellite or "").strip().lower()
        if not _CAPABILITY_RE.fullmatch(satellite):
            raise ValueError("satellite no cumple el formato RTM")
        object.__setattr__(self, "satellite", satellite)

        target_type = str(self.target_type or "").strip().lower()
        if not _CAPABILITY_RE.fullmatch(target_type):
            raise ValueError("target_type no cumple el formato RTM")
        object.__setattr__(self, "target_type", target_type)

        target_ref = str(self.target_ref or "").strip()
        if not target_ref or len(target_ref) > 512:
            raise ValueError("target_ref no válido")
        object.__setattr__(self, "target_ref", target_ref)

        if not isinstance(self.payload, Mapping):
            raise ValueError("payload debe ser un objeto")
        _assert_no_secret_fields(self.payload)
        object.__setattr__(self, "payload", dict(self.payload))

        hashes = tuple(
            sorted(
                {
                    _validate_sha256(value, "document_hash")
                    for value in self.document_hashes
                }
            )
        )
        object.__setattr__(self, "document_hashes", hashes)
        object.__setattr__(
            self,
            "requested_at",
            _parse_timestamp(self.requested_at, "requested_at"),
        )
        if self.contract_version != RTM_CONNECT_CONTRACT_VERSION:
            raise ValueError("Versión de contrato RTM CONNECT no admitida")
        if (
            self.risk_class is RiskClass.R4_CRITICAL_REGULATED
            and not self.requires_dual_control
        ):
            raise ValueError("R4 exige doble control")


@dataclass(frozen=True)
class AuthorizationGrant:
    authorization_id: str
    action_id: str
    authority_code: str
    authority_version: str
    decision: str
    payload_sha256: str
    idempotency_key: str
    required_evidence_level: EvidenceLevel
    authorized_connector_modes: tuple[ConnectorMode, ...]
    approved_by_operator_ids: tuple[str, ...]
    authorized_at: str
    expires_at: str | None = None
    revoked_at: str | None = None
    legal_effect_authorized: bool = False
    frozen: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "authorization_id",
            _parse_uuid(self.authorization_id, "authorization_id"),
        )
        object.__setattr__(
            self,
            "action_id",
            _parse_uuid(self.action_id, "action_id"),
        )
        authority_code = str(self.authority_code or "").strip().lower()
        if not _CAPABILITY_RE.fullmatch(authority_code):
            raise ValueError("authority_code no válido")
        object.__setattr__(self, "authority_code", authority_code)
        if not str(self.authority_version or "").strip():
            raise ValueError("authority_version es obligatoria")
        if self.decision != "approved_frozen":
            raise ValueError("La autorización debe estar aprobada y congelada")
        object.__setattr__(
            self,
            "payload_sha256",
            _validate_sha256(self.payload_sha256, "payload_sha256"),
        )
        key = str(self.idempotency_key or "").strip()
        if not key.startswith("rtmc1:") or len(key) != 70:
            raise ValueError("idempotency_key no válida")
        modes = tuple(dict.fromkeys(self.authorized_connector_modes))
        if not modes:
            raise ValueError("Debe autorizarse al menos un modo de conector")
        object.__setattr__(self, "authorized_connector_modes", modes)
        approvers = tuple(
            dict.fromkeys(
                _parse_uuid(value, "approved_by_operator_id")
                for value in self.approved_by_operator_ids
            )
        )
        if not approvers:
            raise ValueError("Debe existir al menos un aprobador")
        object.__setattr__(self, "approved_by_operator_ids", approvers)
        object.__setattr__(
            self,
            "authorized_at",
            _parse_timestamp(self.authorized_at, "authorized_at"),
        )
        if self.expires_at is not None:
            object.__setattr__(
                self,
                "expires_at",
                _parse_timestamp(self.expires_at, "expires_at"),
            )
        if self.revoked_at is not None:
            object.__setattr__(
                self,
                "revoked_at",
                _parse_timestamp(self.revoked_at, "revoked_at"),
            )
        if not self.frozen:
            raise ValueError("La autorización debe estar congelada")


@dataclass(frozen=True)
class EvidenceRecord:
    level: EvidenceLevel
    request_sha256: str | None = None
    external_reference: str | None = None
    receipt_sha256: str | None = None
    receipt_storage_ref: str | None = None
    verified_at: str | None = None
    verification_method: str | None = None

    def __post_init__(self) -> None:
        for name in ("request_sha256", "receipt_sha256"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self,
                    name,
                    _validate_sha256(value, name),
                )
        if self.verified_at is not None:
            object.__setattr__(
                self,
                "verified_at",
                _parse_timestamp(self.verified_at, "verified_at"),
            )


@dataclass(frozen=True)
class ConnectExecutionResult:
    action_id: str
    attempt_id: str
    connector_code: str
    connector_version: str
    status: str
    evidence: EvidenceRecord
    external_reference: str | None = None
    failure_class: str | None = None
    error_code: str | None = None
    reconciliation_required: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "action_id",
            _parse_uuid(self.action_id, "action_id"),
        )
        object.__setattr__(
            self,
            "attempt_id",
            _parse_uuid(self.attempt_id, "attempt_id"),
        )
        for name in ("connector_code", "connector_version"):
            value = str(getattr(self, name) or "").strip().lower()
            if not _CAPABILITY_RE.fullmatch(value):
                raise ValueError(f"{name} no válido")
            object.__setattr__(self, name, value)
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata debe ser un objeto")
        _assert_no_secret_fields(self.metadata, path="metadata")
        object.__setattr__(self, "metadata", dict(self.metadata))


__all__ = [
    "RTM_CONNECT_CONTRACT_VERSION",
    "AuthorizationGrant",
    "ConnectActionRequest",
    "ConnectExecutionResult",
    "ConnectorMode",
    "EvidenceLevel",
    "EvidenceRecord",
    "RiskClass",
]
