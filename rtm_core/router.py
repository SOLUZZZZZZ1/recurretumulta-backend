"""Endpoints internos del núcleo RTM."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from rtm_core.versioning import build_version_snapshot


router = APIRouter(prefix="/ops/core", tags=["rtm-core"])


def _require_operator(x_operator_token: Optional[str]) -> None:
    expected = (os.getenv("OPERATOR_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="OPERATOR_TOKEN no configurado")
    if not x_operator_token or x_operator_token.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized operator")


@router.get("/version")
def get_core_version(
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    """Commit y versiones declaradas/descubiertas, sin exponer secretos."""
    _require_operator(x_operator_token)
    return build_version_snapshot()
