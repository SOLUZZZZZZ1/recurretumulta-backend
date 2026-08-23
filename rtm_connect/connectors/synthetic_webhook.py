"""Adaptador determinista ``synthetic.webhook`` de RTM CONNECT C4.

Modela una entrega webhook exclusivamente sintética. No usa red, no publica
una ruta HTTP y no conoce secretos. La prueba de integridad es un hash
determinista para comprobar el contrato en staging; no representa una firma
criptográfica de proveedor.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping
from uuid import UUID

from rtm_connect.connectors.base import ConnectorDescriptor
from rtm_connect.contracts import ConnectorMode, RiskClass
from rtm_connect.idempotency import canonical_json


RTM_CONNECT_C4_SYNTHETIC_WEBHOOK_VERSION = (
    "rtm_connect_c4_synthetic_webhook_v1_0"
)
SYNTHETIC_WEBHOOK_CODE = "synthetic.webhook"
SYNTHETIC_WEBHOOK_CONNECTOR_VERSION = "v1.0"
SYNTHETIC_WEBHOOK_CAPABILITY = "synthetic.webhook.ingress"
SYNTHETIC_WEBHOOK_MANIFEST_SHA256 = (
    "14c09a29cc4fb8cf36bd131e26b6b518bb76f7b9623c58dd1fcf2877bb4433df"
)

_SYNTHETIC_WEBHOOK_MANIFEST = {
    "connector_code": SYNTHETIC_WEBHOOK_CODE,
    "connector_version": SYNTHETIC_WEBHOOK_CONNECTOR_VERSION,
    "runtime_version": RTM_CONNECT_C4_SYNTHETIC_WEBHOOK_VERSION,
    "mode": "webhook",
    "synthetic_only": True,
    "network_used": False,
    "credential_ref": None,
    "capabilities": [SYNTHETIC_WEBHOOK_CAPABILITY],
    "risk_ceiling": "R4_critical_regulated",
    "supports_idempotency": True,
    "supports_reconciliation": True,
    "delivery_format": "rtm.synthetic.webhook.delivery.v1",
    "verification_method": "synthetic_integrity_hash_v1",
    "invariants": [
        "ingress_connector_is_not_the_origin_attempt_connector",
        "origin_action_attempt_and_request_hash_are_explicit",
        "event_identity_and_normalized_payload_are_frozen",
        "same_event_id_with_changed_payload_is_a_conflict",
        "integrity_hash_is_not_a_provider_signature",
        "confirmed_observation_carries_e4_material",
        "no_network_route_secret_or_external_effect",
    ],
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,63}$")
_EVENT_KEY_RE = re.compile(r"^[a-zA-Z0-9_.:-]{3,96}$")
_FORBIDDEN_KEY_TOKENS = {
    "password", "rawtoken", "accesstoken", "refreshtoken", "apikey",
    "privatekey", "clientsecret", "cookie", "authorizationheader",
    "signature", "rawsignature", "secret", "token", "authorization",
}
_FORBIDDEN_KEY_MARKERS = (
    "password", "token", "apikey", "privatekey", "clientsecret",
    "authorization", "cookie", "signature", "secret",
)


class SyntheticWebhookOutcome(str, Enum):
    CONFIRMED = "confirmed"
    RETRYABLE_FAILED = "retryable_failed"
    UNKNOWN = "unknown"
    MANUAL_REVIEW = "manual_review"
    PERMANENT_FAILED = "permanent_failed"


class SyntheticWebhookContractError(RuntimeError):
    pass


class SyntheticWebhookIntegrityError(SyntheticWebhookContractError):
    pass


@dataclass(frozen=True)
class SyntheticWebhookDelivery:
    event_id: str
    observed_at: str
    ingress_connector_code: str
    ingress_connector_version: str
    origin_connector_code: str
    origin_connector_version: str
    action_id: str
    attempt_id: str
    request_sha256: str
    external_reference: str
    outcome: SyntheticWebhookOutcome
    normalized_payload: Mapping[str, Any]
    receipt_sha256: str | None
    receipt_storage_ref: str | None
    delivery_sha256: str
    integrity_proof_sha256: str

    def with_changes(self, **changes: Any) -> "SyntheticWebhookDelivery":
        """Ayuda explícita para pruebas de manipulación del contrato."""

        return replace(self, **changes)


@dataclass(frozen=True)
class VerifiedWebhookObservation:
    delivery: SyntheticWebhookDelivery
    verification_method: str
    verified: bool = True


def synthetic_webhook_manifest() -> dict[str, Any]:
    return copy.deepcopy(_SYNTHETIC_WEBHOOK_MANIFEST)


def synthetic_webhook_manifest_sha256() -> str:
    return hashlib.sha256(
        canonical_json(_SYNTHETIC_WEBHOOK_MANIFEST).encode("utf-8")
    ).hexdigest()


def assert_synthetic_webhook_manifest_frozen() -> None:
    if synthetic_webhook_manifest_sha256() != SYNTHETIC_WEBHOOK_MANIFEST_SHA256:
        raise RuntimeError("El manifiesto synthetic.webhook cambió sin versión")


def _uuid(value: str, field_name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{field_name} debe ser UUID") from exc


def _sha256(value: str, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} debe ser SHA-256 hexadecimal")
    return normalized


def _timestamp(value: str, field_name: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} debe ser ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} debe incluir zona horaria")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _code(value: str, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _CODE_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} no cumple el formato RTM")
    return normalized


def _version(value: str, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _VERSION_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} no cumple el formato RTM")
    return normalized


def _assert_no_secrets(value: Any, *, path: str = "normalized_payload") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = re.sub(
                r"[^a-z0-9]+",
                "",
                str(key).strip().lower(),
            )
            if (
                normalized in _FORBIDDEN_KEY_TOKENS
                or any(
                    marker in normalized
                    for marker in _FORBIDDEN_KEY_MARKERS
                )
            ):
                raise ValueError(f"{path}.{key} no puede contener secretos")
            _assert_no_secrets(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_no_secrets(child, path=f"{path}[{index}]")


def _delivery_material(delivery: SyntheticWebhookDelivery) -> dict[str, Any]:
    return {
        "format": "rtm.synthetic.webhook.delivery.v1",
        "event_id": delivery.event_id,
        "observed_at": delivery.observed_at,
        "ingress_connector_code": delivery.ingress_connector_code,
        "ingress_connector_version": delivery.ingress_connector_version,
        "origin_connector_code": delivery.origin_connector_code,
        "origin_connector_version": delivery.origin_connector_version,
        "action_id": delivery.action_id,
        "attempt_id": delivery.attempt_id,
        "request_sha256": delivery.request_sha256,
        "external_reference": delivery.external_reference,
        "outcome": delivery.outcome.value,
        "normalized_payload": dict(delivery.normalized_payload),
        "receipt_sha256": delivery.receipt_sha256,
        "receipt_storage_ref": delivery.receipt_storage_ref,
        "synthetic_only": True,
        "network_used": False,
    }


def _material_sha256(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def synthetic_webhook_delivery_sha256(
    delivery: SyntheticWebhookDelivery,
) -> str:
    """Calcula el hash canónico sin confiar en el declarado."""

    return _material_sha256(_delivery_material(delivery))


def _integrity_proof(delivery_sha256: str) -> str:
    return hashlib.sha256(
        (
            "rtm.synthetic.webhook.integrity.v1:"
            + SYNTHETIC_WEBHOOK_MANIFEST_SHA256
            + ":"
            + delivery_sha256
        ).encode("utf-8")
    ).hexdigest()


def synthetic_webhook_integrity_proof_sha256(
    delivery_sha256: str,
) -> str:
    return _integrity_proof(_sha256(delivery_sha256, "delivery_sha256"))


class SyntheticWebhookConnector:
    descriptor = ConnectorDescriptor(
        code=SYNTHETIC_WEBHOOK_CODE,
        version=SYNTHETIC_WEBHOOK_CONNECTOR_VERSION,
        mode=ConnectorMode.WEBHOOK,
        capabilities=(SYNTHETIC_WEBHOOK_CAPABILITY,),
        risk_ceiling=RiskClass.R4_CRITICAL_REGULATED,
        supports_idempotency=True,
        supports_reconciliation=True,
        synthetic_only=True,
        network_used=False,
        manifest_sha256=SYNTHETIC_WEBHOOK_MANIFEST_SHA256,
    )

    def build_delivery(
        self,
        *,
        event_key: str,
        observed_at: str,
        origin_connector_code: str,
        origin_connector_version: str,
        action_id: str,
        attempt_id: str,
        request_sha256: str,
        external_reference: str,
        outcome: SyntheticWebhookOutcome | str,
        normalized_payload: Mapping[str, Any] | None = None,
        receipt_sha256: str | None = None,
        receipt_storage_ref: str | None = None,
    ) -> SyntheticWebhookDelivery:
        assert_synthetic_webhook_manifest_frozen()
        key = str(event_key or "").strip()
        if not _EVENT_KEY_RE.fullmatch(key):
            raise ValueError("event_key no cumple el formato sintético")
        try:
            selected = (
                outcome if isinstance(outcome, SyntheticWebhookOutcome)
                else SyntheticWebhookOutcome(str(outcome))
            )
        except ValueError as exc:
            raise SyntheticWebhookContractError(
                "Resultado webhook sintético no admitido"
            ) from exc
        reference = str(external_reference or "").strip()
        if not reference or len(reference) > 512:
            raise ValueError("external_reference no válida")
        payload = dict(normalized_payload or {})
        _assert_no_secrets(payload)
        clean_receipt = (
            _sha256(receipt_sha256, "receipt_sha256")
            if receipt_sha256 is not None else None
        )
        clean_storage = (
            str(receipt_storage_ref or "").strip()
            if receipt_storage_ref is not None else None
        )
        if selected is SyntheticWebhookOutcome.CONFIRMED:
            if not clean_receipt or not clean_storage:
                raise ValueError("confirmed exige material de justificante E4")
            if not clean_storage.startswith("synthetic://webhook/"):
                raise ValueError("C4 solo admite storage synthetic://webhook/")
            if len(clean_storage) > 1024:
                raise ValueError("receipt_storage_ref demasiado larga")
        elif clean_receipt is not None or clean_storage is not None:
            raise ValueError("Solo confirmed puede incluir justificante")

        base = SyntheticWebhookDelivery(
            event_id="pending",
            observed_at=_timestamp(observed_at, "observed_at"),
            ingress_connector_code=SYNTHETIC_WEBHOOK_CODE,
            ingress_connector_version=SYNTHETIC_WEBHOOK_CONNECTOR_VERSION,
            origin_connector_code=_code(
                origin_connector_code, "origin_connector_code"
            ),
            origin_connector_version=_version(
                origin_connector_version, "origin_connector_version"
            ),
            action_id=_uuid(action_id, "action_id"),
            attempt_id=_uuid(attempt_id, "attempt_id"),
            request_sha256=_sha256(request_sha256, "request_sha256"),
            external_reference=reference,
            outcome=selected,
            normalized_payload=payload,
            receipt_sha256=clean_receipt,
            receipt_storage_ref=clean_storage,
            delivery_sha256="0" * 64,
            integrity_proof_sha256="0" * 64,
        )
        if base.origin_connector_code == SYNTHETIC_WEBHOOK_CODE:
            raise SyntheticWebhookContractError(
                "El conector de entrada no puede ser el conector de origen"
            )
        identity = _material_sha256(
            {
                "format": "rtm.synthetic.webhook.event-identity.v1",
                "ingress_connector_code": SYNTHETIC_WEBHOOK_CODE,
                "ingress_connector_version": (
                    SYNTHETIC_WEBHOOK_CONNECTOR_VERSION
                ),
                "event_key": key,
            }
        )
        event_id = f"SYN-WH-{identity[:32].upper()}"
        with_id = replace(base, event_id=event_id)
        digest = _material_sha256(_delivery_material(with_id))
        return replace(
            with_id,
            delivery_sha256=digest,
            integrity_proof_sha256=_integrity_proof(digest),
        )

    def verify_delivery(
        self,
        delivery: SyntheticWebhookDelivery,
    ) -> VerifiedWebhookObservation:
        assert_synthetic_webhook_manifest_frozen()
        if not isinstance(delivery, SyntheticWebhookDelivery):
            raise SyntheticWebhookContractError(
                "La entrega no cumple el contrato synthetic.webhook"
            )
        if not _EVENT_KEY_RE.fullmatch(str(delivery.event_id or "")):
            raise SyntheticWebhookContractError("event_id no válido")
        if (
            delivery.ingress_connector_code != SYNTHETIC_WEBHOOK_CODE
            or delivery.ingress_connector_version
            != SYNTHETIC_WEBHOOK_CONNECTOR_VERSION
        ):
            raise SyntheticWebhookContractError(
                "La identidad del adaptador de entrada no coincide"
            )
        origin_code = _code(
            delivery.origin_connector_code, "origin_connector_code"
        )
        _version(
            delivery.origin_connector_version, "origin_connector_version"
        )
        if origin_code == SYNTHETIC_WEBHOOK_CODE:
            raise SyntheticWebhookContractError(
                "El conector de entrada no puede ser el conector de origen"
            )
        _uuid(delivery.action_id, "action_id")
        _uuid(delivery.attempt_id, "attempt_id")
        _sha256(delivery.request_sha256, "request_sha256")
        _timestamp(delivery.observed_at, "observed_at")
        reference = str(delivery.external_reference or "").strip()
        if not reference or len(reference) > 512:
            raise SyntheticWebhookContractError(
                "external_reference no válida"
            )
        if not isinstance(delivery.outcome, SyntheticWebhookOutcome):
            raise SyntheticWebhookContractError("outcome no válido")
        if not isinstance(delivery.normalized_payload, Mapping):
            raise SyntheticWebhookContractError(
                "normalized_payload debe ser un objeto"
            )
        _assert_no_secrets(delivery.normalized_payload)
        if delivery.outcome is SyntheticWebhookOutcome.CONFIRMED:
            _sha256(delivery.receipt_sha256 or "", "receipt_sha256")
            storage = str(delivery.receipt_storage_ref or "").strip()
            if not storage.startswith("synthetic://webhook/"):
                raise SyntheticWebhookContractError(
                    "confirmed exige storage sintético"
                )
            if len(storage) > 1024:
                raise SyntheticWebhookContractError(
                    "receipt_storage_ref demasiado larga"
                )
        elif (
            delivery.receipt_sha256 is not None
            or delivery.receipt_storage_ref is not None
        ):
            raise SyntheticWebhookContractError(
                "Solo confirmed puede incluir justificante"
            )
        _sha256(delivery.delivery_sha256, "delivery_sha256")
        _sha256(
            delivery.integrity_proof_sha256,
            "integrity_proof_sha256",
        )
        expected_digest = synthetic_webhook_delivery_sha256(delivery)
        expected_proof = _integrity_proof(expected_digest)
        if delivery.delivery_sha256 != expected_digest:
            raise SyntheticWebhookIntegrityError(
                "El hash de la entrega webhook no coincide"
            )
        if delivery.integrity_proof_sha256 != expected_proof:
            raise SyntheticWebhookIntegrityError(
                "La prueba de integridad webhook no coincide"
            )
        return VerifiedWebhookObservation(
            delivery=delivery,
            verification_method="synthetic_integrity_hash_v1",
        )


__all__ = [
    "RTM_CONNECT_C4_SYNTHETIC_WEBHOOK_VERSION",
    "SYNTHETIC_WEBHOOK_CAPABILITY",
    "SYNTHETIC_WEBHOOK_CODE",
    "SYNTHETIC_WEBHOOK_CONNECTOR_VERSION",
    "SYNTHETIC_WEBHOOK_MANIFEST_SHA256",
    "SyntheticWebhookConnector",
    "SyntheticWebhookContractError",
    "SyntheticWebhookDelivery",
    "SyntheticWebhookIntegrityError",
    "SyntheticWebhookOutcome",
    "VerifiedWebhookObservation",
    "assert_synthetic_webhook_manifest_frozen",
    "synthetic_webhook_manifest",
    "synthetic_webhook_manifest_sha256",
    "synthetic_webhook_delivery_sha256",
    "synthetic_webhook_integrity_proof_sha256",
]
