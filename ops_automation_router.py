# ops_automation_router.py
import os
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, Query

from ops_automation import tick  # tu archivo actual con tick()

router = APIRouter(prefix="/ops/automation", tags=["ops-automation"])


def _require_operator(x_operator_token: Optional[str]):
    expected = (os.getenv("OPERATOR_TOKEN") or "").strip()
    token = (x_operator_token or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="OPERATOR_TOKEN no configurado")
    if token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized operator")


@router.post("/tick")
def automation_tick(
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    limit: int = Query(25, ge=1, le=200),
):
    _require_operator(x_operator_token)
    return tick(limit=limit)
