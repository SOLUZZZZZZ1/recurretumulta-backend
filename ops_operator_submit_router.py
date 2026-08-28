"""Rutas legacy de operador retiradas de forma fail-closed.

Este módulo ya no está montado por la aplicación. Se conserva únicamente para
que una inclusión accidental no reactive descargas arbitrarias, submitters sin
capability gate ni transiciones ``submitted`` sin justificante verificable.
"""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel


router = APIRouter(prefix="/ops/cases", tags=["ops-operator-legacy-disabled"])


class ApproveBody(BaseModel):
    note: Optional[str] = None


class SubmitBody(BaseModel):
    document_url: Optional[str] = None
    force: bool = False


def require_operator_token(token: str | None) -> None:
    expected = (os.getenv("OPERATOR_TOKEN") or "").strip()
    candidate = (token or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="OPERATOR_TOKEN no configurado")
    if not candidate or not hmac.compare_digest(candidate, expected):
        raise HTTPException(status_code=401, detail="Unauthorized operator")


def _retired() -> None:
    raise HTTPException(
        status_code=410,
        detail=(
            "Router legacy retirado. Use el flujo OPS activo con autoridad y "
            "evidencia de presentación verificables."
        ),
    )


@router.post("/{case_id}/approve")
def approve_case(
    case_id: str,
    body: ApproveBody,
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    _retired()


@router.post("/{case_id}/submit")
def submit_case(
    case_id: str,
    body: SubmitBody,
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    _retired()


__all__ = ["router"]
