"""Conector asistido, sintético y sin red de RTM CONNECT C7.

El conector prepara exclusivamente un sobre determinista de identificadores,
hashes y comprobaciones allowlisted. No redacta contenido jurídico, no elige
una Administración, no publica rutas y no realiza el acto final reservado a
una persona. El justificante C7 es una atestación sintética de staging.
"""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from rtm_connect.assisted_legal_policy import (
    ASSISTED_LEGAL_AUTHORITY_CODE,
    ASSISTED_LEGAL_AUTHORITY_VERSION,
    ASSISTED_LEGAL_CAPABILITY,
    ASSISTED_LEGAL_CODE,
    ASSISTED_LEGAL_CONNECTOR_VERSION,
    ASSISTED_LEGAL_CONTRACT_VERSION,
    ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
    ASSISTED_LEGAL_MARKER,
    validate_c7_action_authority,
)
from rtm_connect.authority import (
    assert_connector_output_has_no_legal_decision,
    validate_execution_authority,
)
from rtm_connect.connectors.base import ConnectorDescriptor
from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    EvidenceRecord,
    RiskClass,
)
from rtm_connect.idempotency import canonical_json, payload_sha256


RTM_CONNECT_C7_ASSISTED_LEGAL_CONNECTOR_VERSION = (
    "rtm_connect_c7_assisted_legal_connector_v1_0"
)
ASSISTED_LEGAL_PACKAGE_FORMAT = "rtm.assisted.legal.package.v1"
ASSISTED_LEGAL_HUMAN_GATE_FORMAT = "rtm.assisted.legal.human_gate.v1"
ASSISTED_LEGAL_VERIFICATION_METHOD = "assisted_legal_hash_gate_v1"
ASSISTED_LEGAL_RECEIPT_STORAGE_PREFIX = "synthetic://assisted-legal/"
ASSISTED_LEGAL_REFERENCE_PREFIX = "SYN-C7-ASSISTED-"
ASSISTED_LEGAL_FIXED_CHECKLIST = (
    "verify_core_authorization_frozen",
    "verify_document_hashes_match",
    "verify_assigned_human_identity",
    "verify_human_final_gate",
    "capture_synthetic_receipt",
)

# Congelado y contrastado con ``assisted_legal_manifest_sha256``. Cambiar el
# manifiesto exige una nueva versión, nunca recalcular esta constante en runtime.
ASSISTED_LEGAL_MANIFEST_SHA256 = (
    "349db44c1fa525f79a2344f18ec5591f40e49398baad6fc34ed9a15e1c2e4421"
)

_MANIFEST = {
    "connector_code": ASSISTED_LEGAL_CODE,
    "connector_version": ASSISTED_LEGAL_CONNECTOR_VERSION,
    "runtime_version": RTM_CONNECT_C7_ASSISTED_LEGAL_CONNECTOR_VERSION,
    "contract_version": ASSISTED_LEGAL_CONTRACT_VERSION,
    "authority_code": ASSISTED_LEGAL_AUTHORITY_CODE,
    "authority_version": ASSISTED_LEGAL_AUTHORITY_VERSION,
    "mode": "assisted",
    "capabilities": [ASSISTED_LEGAL_CAPABILITY],
    "risk_ceiling": "R4_critical_regulated",
    "required_evidence": "E4_receipt_verified",
    "synthetic_only": True,
    "network_used": False,
    "credential_ref": None,
    "routes_published": False,
    "external_effects_executed": False,
    "legal_submission_executed": False,
    "human_final_submit_required": True,
    "supports_idempotency": True,
    "supports_reconciliation": True,
    "package_format": ASSISTED_LEGAL_PACKAGE_FORMAT,
    "human_gate_format": ASSISTED_LEGAL_HUMAN_GATE_FORMAT,
    "human_gate_phrase": ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
    "due_at_required": True,
    "fixed_checklist": list(ASSISTED_LEGAL_FIXED_CHECKLIST),
    "verification_method": ASSISTED_LEGAL_VERIFICATION_METHOD,
    "receipt_storage_scheme": ASSISTED_LEGAL_RECEIPT_STORAGE_PREFIX,
    "invariants": [
        "core_authorization_precedes_package_creation",
        "package_contains_identifiers_hashes_and_fixed_checklist_only",
        "document_bodies_and_legal_text_are_never_packaged",
        "due_at_is_frozen_in_the_package",
        "human_final_gate_is_required_and_hash_bound",
        "receipt_is_synthetic_and_emits_e3",
        "receipt_verification_emits_e4",
        "reconciliation_is_manual_observation_without_resubmission",
        "no_legal_decision_or_final_submission",
        "no_network_routes_credentials_or_external_effects",
    ],
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_RECEIPT_MIME = frozenset({"application/pdf", "application/json"})


class AssistedLegalContractError(RuntimeError):
    pass


class AssistedReceiptVerificationError(AssistedLegalContractError):
    pass


@dataclass(frozen=True)
class AssistedLegalPackage:
    action_id: str
    attempt_id: str
    authorization_id: str
    request_sha256: str
    document_hashes: tuple[str, ...]
    due_at: str
    checklist: tuple[str, ...]
    human_final_gate: str
    human_gate_sha256: str
    manifest: dict[str, Any]
    package_sha256: str


@dataclass(frozen=True)
class AssistedReceiptSubmission:
    """Atestación sintética; nunca es un recibo de presentación real."""

    receipt_sha256: str
    storage_ref: str
    external_reference: str
    package_sha256: str
    human_gate_sha256: str
    human_final_gate: str
    witnessed_at: str
    mime: str
    size_bytes: int
    legal_submission_executed: bool = False

    def __post_init__(self) -> None:
        for name in (
            "receipt_sha256",
            "package_sha256",
            "human_gate_sha256",
        ):
            object.__setattr__(self, name, _sha256(getattr(self, name), name))
        storage = str(self.storage_ref or "").strip()
        if not storage.startswith(ASSISTED_LEGAL_RECEIPT_STORAGE_PREFIX):
            raise ValueError("C7 solo admite almacenamiento sintético")
        if len(storage) > 1024:
            raise ValueError("storage_ref demasiado largo")
        object.__setattr__(self, "storage_ref", storage)

        reference = str(self.external_reference or "").strip()
        if not reference.startswith(ASSISTED_LEGAL_REFERENCE_PREFIX):
            raise ValueError("Referencia asistida sintética no válida")
        if len(reference) > 256:
            raise ValueError("external_reference demasiado larga")
        object.__setattr__(self, "external_reference", reference)

        gate = str(self.human_final_gate or "").strip()
        if gate != ASSISTED_LEGAL_HUMAN_GATE_PHRASE:
            raise ValueError("Falta el gate humano final congelado")
        object.__setattr__(self, "human_final_gate", gate)
        object.__setattr__(
            self,
            "witnessed_at",
            _timestamp(self.witnessed_at, "witnessed_at"),
        )

        mime = str(self.mime or "").strip().lower()
        if mime not in _ALLOWED_RECEIPT_MIME:
            raise ValueError("MIME de atestación C7 no admitido")
        object.__setattr__(self, "mime", mime)
        size = int(self.size_bytes)
        if size <= 0 or size > 20 * 1024 * 1024:
            raise ValueError("size_bytes fuera del límite C7")
        object.__setattr__(self, "size_bytes", size)
        if self.legal_submission_executed is not False:
            raise ValueError("C7 staging no puede ejecutar presentación legal")


@dataclass(frozen=True)
class AssistedReceiptVerification:
    evidence: EvidenceRecord
    package_sha256: str
    human_gate_sha256: str
    verification_sha256: str


def assisted_legal_manifest() -> dict[str, Any]:
    return copy.deepcopy(_MANIFEST)


def assisted_legal_manifest_sha256() -> str:
    return hashlib.sha256(canonical_json(_MANIFEST).encode("utf-8")).hexdigest()


def assert_assisted_legal_manifest_frozen() -> None:
    if assisted_legal_manifest_sha256() != ASSISTED_LEGAL_MANIFEST_SHA256:
        raise RuntimeError("El manifiesto assisted.legal cambió sin versión")


def _sha256(value: str, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} debe ser SHA-256 hexadecimal")
    return normalized


def _uuid(value: str, field_name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{field_name} debe ser UUID") from exc


def _timestamp(value: str, field_name: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} debe ser ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} debe incluir zona horaria")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _gate_material(
    action: ConnectActionRequest,
    grant: AuthorizationGrant,
    *,
    attempt_id: str,
    due_at: str,
) -> dict[str, Any]:
    return {
        "format": ASSISTED_LEGAL_HUMAN_GATE_FORMAT,
        "action_id": action.action_id,
        "attempt_id": attempt_id,
        "authorization_id": grant.authorization_id,
        "request_sha256": payload_sha256(action),
        "document_hashes": list(action.document_hashes),
        "due_at": due_at,
        "checklist": list(ASSISTED_LEGAL_FIXED_CHECKLIST),
        "human_final_gate": ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
    }


class AssistedLegalConnector:
    """Adaptador sellado que prepara y verifica artefactos sintéticos C7."""

    __slots__ = ()

    descriptor = ConnectorDescriptor(
        code=ASSISTED_LEGAL_CODE,
        version=ASSISTED_LEGAL_CONNECTOR_VERSION,
        mode=ConnectorMode.ASSISTED,
        capabilities=(ASSISTED_LEGAL_CAPABILITY,),
        risk_ceiling=RiskClass.R4_CRITICAL_REGULATED,
        supports_idempotency=True,
        supports_reconciliation=True,
        synthetic_only=True,
        network_used=False,
        manifest_sha256=ASSISTED_LEGAL_MANIFEST_SHA256,
    )

    @staticmethod
    def _validate(
        action: ConnectActionRequest,
        grant: AuthorizationGrant,
    ) -> None:
        assert_assisted_legal_manifest_frozen()
        validate_c7_action_authority(action, grant)
        validate_execution_authority(
            action,
            grant,
            connector_mode=ConnectorMode.ASSISTED,
        )

    def build_package(
        self,
        action: ConnectActionRequest,
        grant: AuthorizationGrant,
        *,
        attempt_id: str,
        due_at: str,
    ) -> AssistedLegalPackage:
        self._validate(action, grant)
        clean_attempt = _uuid(attempt_id, "attempt_id")
        clean_due = _timestamp(due_at, "due_at")
        requested = datetime.fromisoformat(
            action.requested_at.replace("Z", "+00:00")
        )
        due = datetime.fromisoformat(clean_due.replace("Z", "+00:00"))
        if due <= requested:
            raise ValueError("due_at debe ser posterior a requested_at")

        gate_material = _gate_material(
            action,
            grant,
            attempt_id=clean_attempt,
            due_at=clean_due,
        )
        human_gate_sha256 = hashlib.sha256(
            canonical_json(gate_material).encode("utf-8")
        ).hexdigest()
        manifest = {
            "format": ASSISTED_LEGAL_PACKAGE_FORMAT,
            "contract_version": ASSISTED_LEGAL_CONTRACT_VERSION,
            "connector_code": ASSISTED_LEGAL_CODE,
            "connector_version": ASSISTED_LEGAL_CONNECTOR_VERSION,
            "action_id": action.action_id,
            "attempt_id": clean_attempt,
            "authorization_id": grant.authorization_id,
            "request_sha256": payload_sha256(action),
            "document_hashes": list(action.document_hashes),
            "due_at": clean_due,
            "checklist": list(ASSISTED_LEGAL_FIXED_CHECKLIST),
            "human_final_gate": ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
            "human_gate_sha256": human_gate_sha256,
            "synthetic_marker": ASSISTED_LEGAL_MARKER,
            "synthetic_only": True,
            "network_used": False,
            "credential_ref": None,
            "routes_published": False,
            "legal_submission_executed": False,
            "external_effects_executed": False,
        }
        assert_connector_output_has_no_legal_decision(manifest)
        package_sha256 = hashlib.sha256(
            canonical_json(manifest).encode("utf-8")
        ).hexdigest()
        return AssistedLegalPackage(
            action_id=action.action_id,
            attempt_id=clean_attempt,
            authorization_id=grant.authorization_id,
            request_sha256=payload_sha256(action),
            document_hashes=tuple(action.document_hashes),
            due_at=clean_due,
            checklist=ASSISTED_LEGAL_FIXED_CHECKLIST,
            human_final_gate=ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
            human_gate_sha256=human_gate_sha256,
            manifest=manifest,
            package_sha256=package_sha256,
        )

    def capture_receipt(
        self,
        action: ConnectActionRequest,
        grant: AuthorizationGrant,
        *,
        attempt_id: str,
        submission: AssistedReceiptSubmission,
    ) -> EvidenceRecord:
        self._validate(action, grant)
        _uuid(attempt_id, "attempt_id")
        if submission.legal_submission_executed:
            raise AssistedLegalContractError(
                "C7 no acepta efectos legales ejecutados"
            )
        expected_reference = (
            f"{ASSISTED_LEGAL_REFERENCE_PREFIX}{action.action_id}"
        )
        if submission.external_reference != expected_reference:
            raise AssistedLegalContractError(
                "La atestación C7 no está correlacionada con la acción"
            )
        return EvidenceRecord(
            level=EvidenceLevel.E3_RECEIPT_CAPTURED,
            request_sha256=payload_sha256(action),
            external_reference=submission.external_reference,
            receipt_sha256=submission.receipt_sha256,
            receipt_storage_ref=submission.storage_ref,
        )

    def verify_receipt(
        self,
        action: ConnectActionRequest,
        grant: AuthorizationGrant,
        *,
        attempt_id: str,
        receipt_sha256: str,
        storage_ref: str,
        external_reference: str,
        package_sha256: str,
        human_gate_sha256: str,
        observed_receipt_sha256: str,
        observed_external_reference: str,
        observed_package_sha256: str,
        observed_human_gate_sha256: str,
        human_final_gate: str,
        verified_at: str,
    ) -> AssistedReceiptVerification:
        self._validate(action, grant)
        clean_attempt = _uuid(attempt_id, "attempt_id")
        expected_receipt = _sha256(receipt_sha256, "receipt_sha256")
        observed_receipt = _sha256(
            observed_receipt_sha256,
            "observed_receipt_sha256",
        )
        expected_package = _sha256(package_sha256, "package_sha256")
        observed_package = _sha256(
            observed_package_sha256,
            "observed_package_sha256",
        )
        expected_gate = _sha256(human_gate_sha256, "human_gate_sha256")
        observed_gate = _sha256(
            observed_human_gate_sha256,
            "observed_human_gate_sha256",
        )
        expected_reference = str(external_reference or "").strip()
        observed_reference = str(observed_external_reference or "").strip()
        correlated_reference = (
            f"{ASSISTED_LEGAL_REFERENCE_PREFIX}{action.action_id}"
        )
        if expected_reference != correlated_reference:
            raise AssistedReceiptVerificationError(
                "La referencia C7 no está correlacionada con la acción"
            )
        if expected_receipt != observed_receipt:
            raise AssistedReceiptVerificationError(
                "El hash observado no coincide con la atestación"
            )
        if expected_reference != observed_reference:
            raise AssistedReceiptVerificationError(
                "La referencia sintética observada no coincide"
            )
        if expected_package != observed_package:
            raise AssistedReceiptVerificationError(
                "La atestación no está ligada al paquete esperado"
            )
        if expected_gate != observed_gate:
            raise AssistedReceiptVerificationError(
                "La atestación no está ligada al gate humano esperado"
            )
        if str(human_final_gate or "").strip() != ASSISTED_LEGAL_HUMAN_GATE_PHRASE:
            raise AssistedReceiptVerificationError(
                "El gate humano final no coincide"
            )
        clean_storage = str(storage_ref or "").strip()
        if not clean_storage.startswith(ASSISTED_LEGAL_RECEIPT_STORAGE_PREFIX):
            raise AssistedReceiptVerificationError(
                "La atestación debe permanecer en almacenamiento sintético"
            )
        if not expected_reference.startswith(ASSISTED_LEGAL_REFERENCE_PREFIX):
            raise AssistedReceiptVerificationError(
                "La referencia C7 debe permanecer sintética"
            )
        clean_verified = _timestamp(verified_at, "verified_at")
        material = {
            "format": "rtm.assisted.legal.verification.v1",
            "action_id": action.action_id,
            "attempt_id": clean_attempt,
            "authorization_id": grant.authorization_id,
            "request_sha256": payload_sha256(action),
            "receipt_sha256": expected_receipt,
            "storage_ref": clean_storage,
            "external_reference": expected_reference,
            "package_sha256": expected_package,
            "human_gate_sha256": expected_gate,
            "human_final_gate": ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
            "verified_at": clean_verified,
            "method": ASSISTED_LEGAL_VERIFICATION_METHOD,
        }
        assert_connector_output_has_no_legal_decision(material)
        verification_sha256 = hashlib.sha256(
            canonical_json(material).encode("utf-8")
        ).hexdigest()
        evidence = EvidenceRecord(
            level=EvidenceLevel.E4_RECEIPT_VERIFIED,
            request_sha256=payload_sha256(action),
            external_reference=expected_reference,
            receipt_sha256=expected_receipt,
            receipt_storage_ref=clean_storage,
            verified_at=clean_verified,
            verification_method=ASSISTED_LEGAL_VERIFICATION_METHOD,
        )
        return AssistedReceiptVerification(
            evidence=evidence,
            package_sha256=expected_package,
            human_gate_sha256=expected_gate,
            verification_sha256=verification_sha256,
        )


__all__ = [
    "RTM_CONNECT_C7_ASSISTED_LEGAL_CONNECTOR_VERSION",
    "ASSISTED_LEGAL_FIXED_CHECKLIST",
    "ASSISTED_LEGAL_MANIFEST_SHA256",
    "ASSISTED_LEGAL_PACKAGE_FORMAT",
    "ASSISTED_LEGAL_RECEIPT_STORAGE_PREFIX",
    "ASSISTED_LEGAL_REFERENCE_PREFIX",
    "ASSISTED_LEGAL_VERIFICATION_METHOD",
    "AssistedLegalConnector",
    "AssistedLegalContractError",
    "AssistedLegalPackage",
    "AssistedReceiptSubmission",
    "AssistedReceiptVerification",
    "AssistedReceiptVerificationError",
    "assert_assisted_legal_manifest_frozen",
    "assisted_legal_manifest",
    "assisted_legal_manifest_sha256",
]
