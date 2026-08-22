"""Canonicalización e idempotencia de RTM CONNECT C0."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

from rtm_connect.contracts import ConnectActionRequest


RTM_CONNECT_IDEMPOTENCY_VERSION = "rtm_connect_idempotency_v1_0"


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        raise TypeError(
            "Los floats no son válidos en contratos idempotentes; use cadena "
            "decimal o entero de unidad mínima"
        )
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(child)
            for key, child in sorted(
                value.items(),
                key=lambda item: str(item[0]),
            )
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(child) for child in value]
    raise TypeError(f"Tipo no canonicalizable: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def action_fingerprint_material(
    action: ConnectActionRequest,
) -> dict[str, Any]:
    return {
        "contract_version": action.contract_version,
        "action_id": action.action_id,
        "capability": action.capability,
        "satellite": action.satellite,
        "case_id": action.case_id,
        "target": {
            "type": action.target_type,
            "ref": action.target_ref,
        },
        "payload": action.payload,
        "document_hashes": list(action.document_hashes),
        "risk_class": action.risk_class.value,
        "requires_dual_control": action.requires_dual_control,
    }


def payload_sha256(action: ConnectActionRequest) -> str:
    return sha256_hex(canonical_json(action_fingerprint_material(action)))


def derive_idempotency_key(
    action: ConnectActionRequest,
    *,
    authority_scope: str,
) -> str:
    scope = str(authority_scope or "").strip().lower()
    if not scope:
        raise ValueError("authority_scope es obligatorio")
    material = {
        "version": RTM_CONNECT_IDEMPOTENCY_VERSION,
        "authority_scope": scope,
        "capability": action.capability,
        "target_type": action.target_type,
        "target_ref": action.target_ref,
        "payload_sha256": payload_sha256(action),
        "document_hashes": list(action.document_hashes),
    }
    return f"rtmc1:{sha256_hex(canonical_json(material))}"


__all__ = [
    "RTM_CONNECT_IDEMPOTENCY_VERSION",
    "action_fingerprint_material",
    "canonical_json",
    "derive_idempotency_key",
    "payload_sha256",
    "sha256_hex",
]
