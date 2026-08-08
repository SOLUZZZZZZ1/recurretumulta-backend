"""Controles de acceso internos compartidos por RTM CORE."""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import HTTPException


def require_operator_token(value: Optional[str]) -> str:
    expected = (os.getenv("OPERATOR_TOKEN") or "").strip()
    received = (value or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="OPERATOR_TOKEN no configurado")
    if not received or not hmac.compare_digest(received, expected):
        raise HTTPException(status_code=401, detail="Unauthorized operator")
    return received
