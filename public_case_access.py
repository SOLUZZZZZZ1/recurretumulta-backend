"""Capacidad opaca y case-scoped para las rutas públicas de RTM.

El UUID del expediente identifica; no autentica. Las lecturas sensibles y los
mutadores públicos deben recibir además un token HMAC emitido al crear el caso.
La rotación del secreto revoca todos los tokens sin persistir credenciales.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid

from fastapi import HTTPException


PUBLIC_CASE_ACCESS_VERSION = "rtm_public_case_access_v1"
PUBLIC_CASE_ACCESS_HEADER = "X-RTM-Case-Token"
_TOKEN_PREFIX = "v1"
_SECRET_ENV = "RTM_PUBLIC_CASE_ACCESS_SECRET"


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
            detail={
                "message": "El acceso público a expedientes no está habilitado.",
                "required_secret": _SECRET_ENV,
                "minimum_bytes": 32,
            },
        )
    return value


def _material(case_id: str) -> bytes:
    canonical = _canonical_case_id(case_id)
    return f"{PUBLIC_CASE_ACCESS_VERSION}:{canonical}".encode("ascii")


def issue_case_access_token(case_id: str) -> str:
    digest = hmac.new(_secret(), _material(case_id), hashlib.sha256).hexdigest()
    return f"{_TOKEN_PREFIX}.{digest}"


def require_public_case_access_configured() -> None:
    _secret()


def verify_case_access_token(case_id: str, token: str | None) -> bool:
    candidate = str(token or "").strip()
    expected = issue_case_access_token(case_id)
    return bool(candidate) and hmac.compare_digest(candidate, expected)


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
        expected_operator
        and received_operator
        and hmac.compare_digest(received_operator, expected_operator)
    ):
        return _canonical_case_id(case_id)
    return require_case_access_token(case_id, case_token)


def require_operator_case_access(case_id: str, operator_token: str | None) -> str:
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
