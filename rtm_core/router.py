"""Endpoints internos de observabilidad y preparación del núcleo RTM."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, Request

from database import get_engine
from rtm_core.repository import build_case_review_readiness, load_case_review_snapshot
from rtm_core.ops_case_scope import load_ops_case_scope, require_case_in_scope
from rtm_core.security import require_operator_token
from rtm_core.versioning import build_version_snapshot


router = APIRouter(prefix="/ops/core", tags=["rtm-core"])


@router.get("/version")
def get_core_version(
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    return build_version_snapshot()


@router.get("/cases/{case_id}/review-readiness")
def get_case_review_readiness(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    """Estado documental mínimo y precio autoritativo, solo para OPS."""
    require_operator_token(x_operator_token)
    engine = get_engine()
    with engine.begin() as conn:
        case_id = require_case_in_scope(
            conn, scope=load_ops_case_scope(request), case_id=case_id
        )
        snapshot = load_case_review_snapshot(conn, case_id)
    return build_case_review_readiness(snapshot)
