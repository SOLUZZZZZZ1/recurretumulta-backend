"""Frontera de salida fail-closed para las proyecciones C5.

Las consultas usan listas explicitas de columnas. Esta segunda barrera evita
que una ampliacion futura del repositorio publique por accidente material
operativo, identificadores de seguridad o campos libres no revisados.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


RTM_CONNECT_C5_SUPERVISOR_CONTRACTS_VERSION = (
    "rtm_connect_c5_supervisor_contracts_v1_0"
)

_FORBIDDEN_EXACT_KEYS = {
    "audit_event_id",
    "approved_by_operator_ids",
    "authority_code",
    "authority_version",
    "authorized_connector_modes",
    "claimed_action_id",
    "claimed_attempt_id",
    "configuration",
    "credential_ref",
    "document_hashes",
    "error_code",
    "event_key",
    "failure_class",
    "idempotency_key",
    "instructions",
    "normalized_payload",
    "package_manifest",
    "payload",
    "raw_headers",
    "raw_payload",
    "reason_code",
    "reason_detail",
    "receipt_sha256",
    "receipt_storage_ref",
    "request_metadata",
    "resolution_code",
    "result_metadata",
    "signature",
    "source_event_id",
    "target_ref",
    "verification_method",
}


class ConnectSupervisorProjectionError(RuntimeError):
    """La proyeccion contiene una clave no publicable por C5."""


def _assert_key_allowed(key: Any, value: Any) -> None:
    normalized = str(key or "").strip().lower()
    if (
        normalized.endswith(("_exposed", "_available"))
        and value is False
    ):
        return
    if (
        not normalized
        or normalized in _FORBIDDEN_EXACT_KEYS
        or normalized.endswith("_sha256")
        or normalized.endswith("_hash")
        or normalized.startswith(
            ("raw_", "claimed_", "credential_", "secret_")
        )
        or "payload" in normalized
        or "metadata" in normalized
        or "document_hash" in normalized
        or "reason_detail" in normalized
    ):
        raise ConnectSupervisorProjectionError(
            "La proyeccion C5 contiene material no publicable"
        )


def assert_sanitized_supervisor_projection(value: Any) -> None:
    """Recorre la respuesta completa y falla ante una clave prohibida."""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            _assert_key_allowed(key, nested)
            assert_sanitized_supervisor_projection(nested)
        return
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for nested in value:
            assert_sanitized_supervisor_projection(nested)


__all__ = [
    "RTM_CONNECT_C5_SUPERVISOR_CONTRACTS_VERSION",
    "ConnectSupervisorProjectionError",
    "assert_sanitized_supervisor_projection",
]
