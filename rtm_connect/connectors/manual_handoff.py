"""Conector sintético ``manual.handoff`` de RTM CONNECT C3.

Prepara un paquete congelado y convierte un justificante sintético en evidencia
E3/E4. No usa red, B2, credenciales ni efectos externos.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from rtm_connect.connectors.base import ConnectorDescriptor
from rtm_connect.contracts import (
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    EvidenceRecord,
    RiskClass,
)
from rtm_connect.idempotency import canonical_json, payload_sha256


RTM_CONNECT_C3_MANUAL_HANDOFF_VERSION = (
    "rtm_connect_c3_manual_handoff_connector_v1_0"
)
MANUAL_HANDOFF_CODE = "manual.handoff"
MANUAL_HANDOFF_CONNECTOR_VERSION = "v1.0"
MANUAL_HANDOFF_CAPABILITY = "administration.submit_document"
MANUAL_HANDOFF_MANIFEST_SHA256 = "59325e679aceb0673f8667007a4bdb8ee772e120e4e5d8e80fa16d1e36252108"

_MANUAL_HANDOFF_MANIFEST = {'connector_code': 'manual.handoff', 'connector_version': 'v1.0', 'runtime_version': 'rtm_connect_c3_manual_handoff_connector_v1_0', 'mode': 'manual', 'synthetic_only': True, 'network_used': False, 'credential_ref': None, 'capabilities': ['administration.submit_document'], 'risk_ceiling': 'R3_legal_or_financial', 'supports_idempotency': True, 'supports_reconciliation': False, 'package_format': 'rtm.manual.handoff.package.v1', 'verification_method': 'manual_handoff_hash_reference_v1', 'receipt_storage_scheme': 'synthetic://manual-handoff/', 'invariants': ['core_authorization_precedes_task_creation', 'package_manifest_is_frozen', 'one_manual_task_per_action_and_attempt', 'assigned_operator_executes_manual_step', 'verifier_must_differ_from_assignee', 'receipt_capture_emits_e3', 'receipt_verification_emits_e4', 'core_confirmation_follows_e4_only', 'manual_events_are_append_only', 'no_network_or_external_effect_in_c3']}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ManualHandoffContractError(RuntimeError):
    pass


class ManualReceiptVerificationError(ManualHandoffContractError):
    pass


@dataclass(frozen=True)
class ManualHandoffPackage:
    action_id: str
    attempt_id: str
    due_at: str
    instructions: str
    manifest: dict[str, Any]
    package_sha256: str


@dataclass(frozen=True)
class ManualReceiptSubmission:
    receipt_sha256: str
    storage_ref: str
    external_reference: str
    presented_at: str
    mime: str
    size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "receipt_sha256",
            _sha256(self.receipt_sha256, "receipt_sha256"),
        )
        storage = str(self.storage_ref or "").strip()
        if not storage.startswith("synthetic://manual-handoff/"):
            raise ValueError(
                "C3 solo admite almacenamiento synthetic://manual-handoff/"
            )
        if len(storage) > 1024:
            raise ValueError("storage_ref demasiado largo")
        object.__setattr__(self, "storage_ref", storage)

        reference = str(self.external_reference or "").strip()
        if not reference.startswith("SYN-MANUAL-"):
            raise ValueError("Referencia manual sintética no válida")
        if len(reference) > 256:
            raise ValueError("external_reference demasiado larga")
        object.__setattr__(self, "external_reference", reference)

        object.__setattr__(
            self,
            "presented_at",
            _timestamp(self.presented_at, "presented_at"),
        )
        mime = str(self.mime or "").strip().lower()
        if mime not in {"application/pdf", "image/png", "image/jpeg"}:
            raise ValueError("MIME de justificante no admitido")
        object.__setattr__(self, "mime", mime)
        size = int(self.size_bytes)
        if size <= 0 or size > 20 * 1024 * 1024:
            raise ValueError("size_bytes fuera del límite C3")
        object.__setattr__(self, "size_bytes", size)


@dataclass(frozen=True)
class ManualReceiptVerification:
    evidence: EvidenceRecord
    verification_sha256: str


def manual_handoff_manifest() -> dict[str, Any]:
    return copy.deepcopy(_MANUAL_HANDOFF_MANIFEST)


def manual_handoff_manifest_sha256() -> str:
    canonical = json.dumps(
        _MANUAL_HANDOFF_MANIFEST,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assert_manual_handoff_manifest_frozen() -> None:
    if manual_handoff_manifest_sha256() != MANUAL_HANDOFF_MANIFEST_SHA256:
        raise RuntimeError("El manifiesto manual.handoff cambió sin versión")


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


def _instructions(value: str) -> str:
    clean = " ".join(str(value or "").split()).strip()
    if len(clean) < 10 or len(clean) > 4000:
        raise ValueError("Instrucciones manuales no válidas")
    return clean


class ManualHandoffConnector:
    descriptor = ConnectorDescriptor(
        code=MANUAL_HANDOFF_CODE,
        version=MANUAL_HANDOFF_CONNECTOR_VERSION,
        mode=ConnectorMode.MANUAL,
        capabilities=(MANUAL_HANDOFF_CAPABILITY,),
        risk_ceiling=RiskClass.R3_LEGAL_OR_FINANCIAL,
        supports_idempotency=True,
        supports_reconciliation=False,
        synthetic_only=True,
        network_used=False,
        manifest_sha256=MANUAL_HANDOFF_MANIFEST_SHA256,
    )

    def _validate_action(self, action: ConnectActionRequest) -> None:
        if action.capability != MANUAL_HANDOFF_CAPABILITY:
            raise ManualHandoffContractError(
                "manual.handoff solo admite su capacidad explícita"
            )
        if action.risk_class is RiskClass.R4_CRITICAL_REGULATED:
            raise ManualHandoffContractError(
                "C3 no admite riesgo R4 en el flujo manual inicial"
            )

    def build_package(
        self,
        action: ConnectActionRequest,
        *,
        attempt_id: str,
        due_at: str,
        instructions: str,
    ) -> ManualHandoffPackage:
        assert_manual_handoff_manifest_frozen()
        self._validate_action(action)
        clean_attempt = _uuid(attempt_id, "attempt_id")
        clean_due = _timestamp(due_at, "due_at")
        clean_instructions = _instructions(instructions)
        requested = datetime.fromisoformat(
            action.requested_at.replace("Z", "+00:00")
        )
        due = datetime.fromisoformat(clean_due.replace("Z", "+00:00"))
        if due <= requested:
            raise ValueError("due_at debe ser posterior a requested_at")

        manifest = {
            "format": "rtm.manual.handoff.package.v1",
            "connector_code": MANUAL_HANDOFF_CODE,
            "connector_version": MANUAL_HANDOFF_CONNECTOR_VERSION,
            "action_id": action.action_id,
            "attempt_id": clean_attempt,
            "capability": action.capability,
            "satellite": action.satellite,
            "target": {"type": action.target_type, "ref": action.target_ref},
            "payload_sha256": payload_sha256(action),
            "document_hashes": list(action.document_hashes),
            "due_at": clean_due,
            "instructions": clean_instructions,
            "synthetic_only": True,
            "network_used": False,
            "external_effects_executed": False,
        }
        digest = hashlib.sha256(
            canonical_json(manifest).encode("utf-8")
        ).hexdigest()
        return ManualHandoffPackage(
            action_id=action.action_id,
            attempt_id=clean_attempt,
            due_at=clean_due,
            instructions=clean_instructions,
            manifest=manifest,
            package_sha256=digest,
        )

    def capture_receipt(
        self,
        action: ConnectActionRequest,
        *,
        attempt_id: str,
        submission: ManualReceiptSubmission,
    ) -> EvidenceRecord:
        assert_manual_handoff_manifest_frozen()
        self._validate_action(action)
        _uuid(attempt_id, "attempt_id")
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
        *,
        attempt_id: str,
        receipt_sha256: str,
        storage_ref: str,
        external_reference: str,
        observed_receipt_sha256: str,
        observed_external_reference: str,
        verified_at: str,
    ) -> ManualReceiptVerification:
        assert_manual_handoff_manifest_frozen()
        self._validate_action(action)
        clean_attempt = _uuid(attempt_id, "attempt_id")
        expected_hash = _sha256(receipt_sha256, "receipt_sha256")
        observed_hash = _sha256(
            observed_receipt_sha256, "observed_receipt_sha256"
        )
        expected_reference = str(external_reference or "").strip()
        observed_reference = str(observed_external_reference or "").strip()
        if expected_hash != observed_hash:
            raise ManualReceiptVerificationError(
                "El hash observado no coincide con el justificante"
            )
        if expected_reference != observed_reference:
            raise ManualReceiptVerificationError(
                "La referencia observada no coincide"
            )
        clean_storage = str(storage_ref or "").strip()
        if not clean_storage.startswith("synthetic://manual-handoff/"):
            raise ManualReceiptVerificationError(
                "El justificante debe permanecer en almacenamiento sintético"
            )
        clean_verified = _timestamp(verified_at, "verified_at")
        material = {
            "format": "rtm.manual.handoff.verification.v1",
            "action_id": action.action_id,
            "attempt_id": clean_attempt,
            "request_sha256": payload_sha256(action),
            "receipt_sha256": expected_hash,
            "storage_ref": clean_storage,
            "external_reference": expected_reference,
            "verified_at": clean_verified,
            "method": "manual_handoff_hash_reference_v1",
        }
        verification_sha256 = hashlib.sha256(
            canonical_json(material).encode("utf-8")
        ).hexdigest()
        evidence = EvidenceRecord(
            level=EvidenceLevel.E4_RECEIPT_VERIFIED,
            request_sha256=payload_sha256(action),
            external_reference=expected_reference,
            receipt_sha256=expected_hash,
            receipt_storage_ref=clean_storage,
            verified_at=clean_verified,
            verification_method="manual_handoff_hash_reference_v1",
        )
        return ManualReceiptVerification(
            evidence=evidence,
            verification_sha256=verification_sha256,
        )


__all__ = [
    "RTM_CONNECT_C3_MANUAL_HANDOFF_VERSION",
    "MANUAL_HANDOFF_CAPABILITY",
    "MANUAL_HANDOFF_CODE",
    "MANUAL_HANDOFF_CONNECTOR_VERSION",
    "MANUAL_HANDOFF_MANIFEST_SHA256",
    "ManualHandoffConnector",
    "ManualHandoffContractError",
    "ManualHandoffPackage",
    "ManualReceiptSubmission",
    "ManualReceiptVerification",
    "ManualReceiptVerificationError",
    "assert_manual_handoff_manifest_frozen",
    "manual_handoff_manifest",
    "manual_handoff_manifest_sha256",
]
