"""Capacidad opaca y case-scoped para las rutas públicas de RTM.

El UUID del expediente identifica; no autentica. Las lecturas sensibles y los
mutadores públicos deben recibir además un token HMAC emitido al crear el caso.
La rotación del secreto revoca todos los tokens sin persistir credenciales.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
import uuid

from fastapi import HTTPException
from rtm_core.operator_auth_request import (
    OPERATOR_AUTH_MODE_FAIL_CLOSED,
    OPERATOR_AUTH_MODE_LEGACY,
    operator_auth_environment_mode,
)


PUBLIC_CASE_ACCESS_VERSION = "rtm_public_case_access_v2"
PUBLIC_CASE_ACCESS_HEADER = "X-RTM-Case-Token"
_TOKEN_PREFIX = "v2"
_SECRET_ENV = "RTM_PUBLIC_CASE_ACCESS_SECRET"
_TTL_ENV = "RTM_PUBLIC_CASE_TOKEN_TTL_SECONDS"
_LEGACY_UNTIL_ENV = "RTM_ACCEPT_LEGACY_CASE_TOKENS_UNTIL_EPOCH"
_DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60
_MAX_TTL_SECONDS = 90 * 24 * 60 * 60
_MAX_LEGACY_GRACE_SECONDS = 30 * 24 * 60 * 60
_CLOCK_SKEW_SECONDS = 5 * 60


def _shared_operator_token_allowed() -> bool:
    """Conserva el contrato legacy fuera de staging.

    En staging las superficies OPS usan sesiones individuales. El secreto
    compartido puede seguir existiendo para que los routers históricos lo
    reciban de forma interna, pero deja de ser una capacidad aceptable desde
    rutas públicas o legacy ajenas al puente de sesión.
    """

    return operator_auth_environment_mode() == OPERATOR_AUTH_MODE_LEGACY


def _canonical_case_id(case_id: str) -> str:
    try:
        return str(uuid.UUID(str(case_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=401, detail="Capacidad de expediente inválida") from exc


def _secret() -> bytes:
    value = (os.getenv(_SECRET_ENV) or "").strip().encode("utf-8")
    if len(value) < 32:
        raise HTTPException(
            status_code=503,
            detail="El acceso público a expedientes no está disponible.",
        )
    return value


def _ttl_seconds() -> int:
    raw = str(os.getenv(_TTL_ENV) or _DEFAULT_TTL_SECONDS).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="El acceso público a expedientes no está disponible.",
        ) from exc
    if not 300 <= value <= _MAX_TTL_SECONDS:
        raise HTTPException(
            status_code=503,
            detail="El acceso público a expedientes no está disponible.",
        )
    return value


def _material(case_id: str, issued_at: int, nonce: str) -> bytes:
    canonical = _canonical_case_id(case_id)
    return (
        f"{PUBLIC_CASE_ACCESS_VERSION}:{canonical}:{issued_at}:{nonce}"
    ).encode("ascii")


def issue_case_access_token(case_id: str, *, now: int | None = None) -> str:
    issued_at = int(time.time() if now is None else now)
    nonce = secrets.token_hex(16)
    digest = hmac.new(
        _secret(),
        _material(case_id, issued_at, nonce),
        hashlib.sha256,
    ).hexdigest()
    return f"{_TOKEN_PREFIX}.{issued_at}.{nonce}.{digest}"


def require_public_case_access_configured() -> None:
    _secret()


def _legacy_token_allowed(case_id: str, token: str, *, now: int) -> bool:
    raw_cutoff = str(os.getenv(_LEGACY_UNTIL_ENV) or "").strip()
    if not raw_cutoff:
        return False
    try:
        cutoff = int(raw_cutoff)
    except ValueError:
        return False
    if cutoff < now or cutoff > now + _MAX_LEGACY_GRACE_SECONDS:
        return False
    canonical = _canonical_case_id(case_id)
    material = f"rtm_public_case_access_v1:{canonical}".encode("ascii")
    expected = "v1." + hmac.new(_secret(), material, hashlib.sha256).hexdigest()
    return hmac.compare_digest(token, expected)


def verify_case_access_token(
    case_id: str,
    token: str | None,
    *,
    now: int | None = None,
) -> bool:
    candidate = str(token or "").strip()
    current_time = int(time.time() if now is None else now)
    if candidate.startswith("v1."):
        return _legacy_token_allowed(case_id, candidate, now=current_time)
    parts = candidate.split(".")
    if len(parts) != 4 or parts[0] != _TOKEN_PREFIX:
        return False
    _, issued_raw, nonce, supplied_digest = parts
    if (
        not issued_raw.isascii()
        or not issued_raw.isdigit()
        or len(issued_raw) not in {9, 10, 11}
        or len(nonce) != 32
        or len(supplied_digest) != 64
    ):
        return False
    try:
        issued_at = int(issued_raw)
        bytes.fromhex(nonce)
        bytes.fromhex(supplied_digest)
    except ValueError:
        return False
    if issued_at > current_time + _CLOCK_SKEW_SECONDS:
        return False
    if current_time - issued_at > _ttl_seconds():
        return False
    expected = hmac.new(
        _secret(),
        _material(case_id, issued_at, nonce),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(supplied_digest, expected)


def require_case_access_token(case_id: str, token: str | None) -> str:
    if not verify_case_access_token(case_id, token):
        raise HTTPException(status_code=401, detail="Capacidad de expediente inválida")
    return _canonical_case_id(case_id)


def require_case_or_operator_access(
    case_id: str,
    case_token: str | None,
    operator_token: str | None,
) -> str:
    expected_operator = (os.getenv("OPERATOR_TOKEN") or "").strip()
    received_operator = str(operator_token or "").strip()
    if (
        _shared_operator_token_allowed()
        and expected_operator
        and received_operator
        and hmac.compare_digest(received_operator, expected_operator)
    ):
        return _canonical_case_id(case_id)
    return require_case_access_token(case_id, case_token)


def require_operator_case_access(case_id: str, operator_token: str | None) -> str:
    if operator_auth_environment_mode() == OPERATOR_AUTH_MODE_FAIL_CLOSED:
        raise HTTPException(
            status_code=503,
            detail="Autenticación individual no disponible",
        )
    if not _shared_operator_token_allowed():
        raise HTTPException(
            status_code=401,
            detail="Autenticación individual requerida",
        )
    expected = (os.getenv("OPERATOR_TOKEN") or "").strip()
    candidate = str(operator_token or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="OPERATOR_TOKEN no configurado")
    if not candidate or not hmac.compare_digest(candidate, expected):
        raise HTTPException(status_code=401, detail="Token de operador inválido")
    return _canonical_case_id(case_id)


__all__ = [
    "PUBLIC_CASE_ACCESS_HEADER",
    "PUBLIC_CASE_ACCESS_VERSION",
    "issue_case_access_token",
    "require_public_case_access_configured",
    "require_case_or_operator_access",
    "require_case_access_token",
    "require_operator_case_access",
    "verify_case_access_token",
]
