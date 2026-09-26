"""Individual, scoped OPS continuation; no actor supplied by the browser."""
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, Response

from database import get_engine
from rtm_core.ops_case_scope import load_ops_case_scope, require_case_in_scope
from rtm_core.security import require_operator_token
from rtm_core.study import StudyActionBody, advance_study, load_study

router = APIRouter(prefix="/ops/core/cases", tags=["rtm-core-study"])


@router.get("/{case_id}/study")
def get_case_study(case_id: str, request: Request, response: Response,
                   x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token")):
    require_operator_token(x_operator_token)
    scope = load_ops_case_scope(request)
    response.headers["Cache-Control"] = "no-store"
    with get_engine().begin() as conn:
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        result, *_ = load_study(conn, case_id)
    return result


@router.post("/{case_id}/study/actions")
def post_case_study(case_id: str, body: StudyActionBody, request: Request, response: Response,
                    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token")):
    require_operator_token(x_operator_token)
    scope = load_ops_case_scope(request)
    if not scope.individual_session or not scope.scope_all or not scope.operator_id:
        raise HTTPException(403, "Se requiere una sesión individual de supervisor.")
    response.headers["Cache-Control"] = "no-store"
    with get_engine().begin() as conn:
        case_id = require_case_in_scope(conn, scope=scope, case_id=case_id)
        return advance_study(conn, case_id=case_id, body=body, actor=f"operator:{scope.operator_id}")
