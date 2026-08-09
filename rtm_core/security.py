"""Controles de acceso internos compartidos por RTM CORE."""

from __future__ import annotations

import hmac
import os
import re
from typing import Optional

from fastapi import HTTPException


def _require_secret(value: Optional[str], env_name: str, unauthorized_detail: str) -> str:
    expected = (os.getenv(env_name) or "").strip()
    received = (value or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail=f"{env_name} no configurado")
    if not received or not hmac.compare_digest(received, expected):
        raise HTTPException(status_code=401, detail=unauthorized_detail)
    return received


def require_operator_token(value: Optional[str]) -> str:
    return _require_secret(value, "OPERATOR_TOKEN", "Unauthorized operator")


def require_admin_token(value: Optional[str]) -> str:
    return _require_secret(value, "ADMIN_TOKEN", "Unauthorized admin")


def normalized_actor(value: Optional[str], *, fallback: str = "ops:operator") -> str:
    """Identificador auditable sin guardar tokens ni aceptar texto arbitrario."""

    raw = (value or "").strip()
    if not raw:
        return fallback
    clean = re.sub(r"[^a-zA-Z0-9_.:@-]+", "_", raw).strip("_:")
    if not clean:
        return fallback
    return clean[:120]
