"""API protegida de la vista única del expediente RTM para OPS."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import text

from database import get_engine
from rtm_core.security import require_operator_token
from rtm_core.ops_case_scope import load_ops_case_scope, require_case_in_scope
from rtm_core.workspace_policy_ext import (
    WORKSPACE_POLICY_VERSION,
    determine_workspace_stage,
)
from rtm_core.workspace_service_v2 import WORKSPACE_VERSION, build_case_workspace


router = APIRouter(prefix="/ops/core/cases", tags=["rtm-core-workspace"])


@router.get("/{case_id}/workspace")
def get_case_workspace(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        scoped_case_id = require_case_in_scope(
            conn,
            scope=scope,
            case_id=case_id,
        )
        return build_case_workspace(conn, scoped_case_id)


@router.get("/{case_id}/payment-status")
def get_case_payment_status(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    """Estado de pago mínimo para OPS, sin capacidad ni secreto de cliente."""

    # En staging el bridge sustituye el header del cliente por este secreto
    # exclusivamente dentro del servidor. El cliente nunca puede aportarlo.
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        scoped_case_id = require_case_in_scope(
            conn,
            scope=scope,
            case_id=case_id,
        )
        row = conn.execute(
            text(
                """
                SELECT COALESCE(payment_status, ''), paid_at,
                       product_code, COALESCE(status, '')
                FROM cases
                WHERE id = CAST(:case_id AS UUID)
                """
            ),
            {"case_id": scoped_case_id},
        ).fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail="Expediente no encontrado",
                headers={
                    "Cache-Control": "no-store, max-age=0",
                    "Pragma": "no-cache",
                },
            )

    return {
        "ok": True,
        "case_id": scoped_case_id,
        "payment_status": str(row[0] or ""),
        "paid_at": row[1],
        "product_code": row[2],
        "status": str(row[3] or ""),
    }


__all__ = [
    "WORKSPACE_VERSION",
    "WORKSPACE_POLICY_VERSION",
    "determine_workspace_stage",
    "get_case_payment_status",
    "get_case_workspace",
    "router",
]
