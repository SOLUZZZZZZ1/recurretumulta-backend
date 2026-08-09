"""API protegida de la vista única del expediente RTM para OPS."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header

from database import get_engine
from rtm_core.security import require_operator_token
from rtm_core.workspace_policy_ext import (
    WORKSPACE_POLICY_VERSION,
    determine_workspace_stage,
)
from rtm_core.workspace_service_v2 import WORKSPACE_VERSION, build_case_workspace


router = APIRouter(prefix="/ops/core/cases", tags=["rtm-core-workspace"])


@router.get("/{case_id}/workspace")
def get_case_workspace(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        return build_case_workspace(conn, case_id)


__all__ = [
    "WORKSPACE_VERSION",
    "WORKSPACE_POLICY_VERSION",
    "determine_workspace_stage",
    "get_case_workspace",
    "router",
]
