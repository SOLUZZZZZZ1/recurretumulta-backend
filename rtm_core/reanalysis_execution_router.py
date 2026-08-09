"""API OPS para ejecutar Reanalysis bajo la política conservadora RTM CORE."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header

from rtm_core.reanalysis_execution import (
    extraction_policy_status,
    run_safe_traffic_reanalysis,
)
from rtm_core.security import normalized_actor, require_operator_token


router = APIRouter(prefix="/ops/core/cases", tags=["rtm-core-reanalysis-run"])


def _operator(token: Optional[str], actor: Optional[str]) -> str:
    require_operator_token(token)
    return normalized_actor(actor, fallback="ops:reanalysis-run")


@router.get("/reanalysis/policy-status")
def get_reanalysis_policy_status(
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _operator(x_operator_token, None)
    return extraction_policy_status()


@router.post("/{case_id}/reanalysis/run")
def run_case_reanalysis_core(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    x_operator_actor: Optional[str] = Header(default=None, alias="X-Operator-Actor"),
):
    """Ejecuta extracción segura; no crea ni congela autoridad por sí misma."""

    actor = _operator(x_operator_token, x_operator_actor)
    return run_safe_traffic_reanalysis(case_id, actor=actor)
