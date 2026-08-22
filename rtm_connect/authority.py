"""Límite de autoridad CORE ↔ CONNECT congelado en C0."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    RiskClass,
)
from rtm_connect.idempotency import (
    derive_idempotency_key,
    payload_sha256,
)


RTM_CONNECT_AUTHORITY_VERSION = "rtm_connect_authority_v1_0"

FORBIDDEN_CONNECT_DECISION_KEYS = frozenset(
    {
        "family",
        "specialist",
        "legal_strategy",
        "legal_basis",
        "filing_deadline",
        "should_submit",
        "claim_amount_authorized",
        "legal_effect_confirmed",
    }
)


class AuthorityValidationError(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def validate_execution_authority(
    action: ConnectActionRequest,
    grant: AuthorizationGrant,
    *,
    connector_mode: ConnectorMode,
    now: datetime | None = None,
) -> None:
    current = now or _utcnow()
    if grant.action_id != action.action_id:
        raise AuthorityValidationError(
            "La autorización no pertenece a la acción"
        )
    if grant.revoked_at is not None:
        raise AuthorityValidationError("La autorización está revocada")
    if grant.expires_at is not None:
        expiry = datetime.fromisoformat(
            grant.expires_at.replace("Z", "+00:00")
        )
        if expiry <= current:
            raise AuthorityValidationError("La autorización ha caducado")
    expected_payload = payload_sha256(action)
    if grant.payload_sha256 != expected_payload:
        raise AuthorityValidationError(
            "El payload no coincide con la autorización congelada"
        )
    expected_key = derive_idempotency_key(
        action,
        authority_scope=grant.authority_code,
    )
    if grant.idempotency_key != expected_key:
        raise AuthorityValidationError(
            "La clave de idempotencia no coincide"
        )
    if connector_mode not in grant.authorized_connector_modes:
        raise AuthorityValidationError(
            "Modo de conector no autorizado"
        )
    if (
        action.risk_class
        in {
            RiskClass.R3_LEGAL_OR_FINANCIAL,
            RiskClass.R4_CRITICAL_REGULATED,
        }
        and not grant.legal_effect_authorized
    ):
        raise AuthorityValidationError(
            "La actuación sensible carece de autorización de efecto legal"
        )
    if (
        action.requires_dual_control
        and len(grant.approved_by_operator_ids) < 2
    ):
        raise AuthorityValidationError(
            "La actuación exige doble control"
        )


def assert_connector_output_has_no_legal_decision(
    output: Mapping[str, Any],
) -> None:
    found = sorted(
        key
        for key in output
        if str(key).strip().lower()
        in FORBIDDEN_CONNECT_DECISION_KEYS
    )
    if found:
        raise AuthorityValidationError(
            "CONNECT no puede adoptar decisiones jurídicas: "
            + ", ".join(found)
        )


__all__ = [
    "RTM_CONNECT_AUTHORITY_VERSION",
    "FORBIDDEN_CONNECT_DECISION_KEYS",
    "AuthorityValidationError",
    "assert_connector_output_has_no_legal_decision",
    "validate_execution_authority",
]
