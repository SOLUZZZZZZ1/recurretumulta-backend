import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from sqlalchemy import text

from database import get_engine

router = APIRouter(prefix="/ops", tags=["ops-override"])


def _require_operator(x_operator_token: Optional[str]) -> None:
    expected = (os.getenv("OPERATOR_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="OPERATOR_TOKEN no configurado")
    if not x_operator_token or x_operator_token.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/cases/{case_id}/force-generate")
def force_generate_resource(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token")
):
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id FROM cases WHERE id = :id"),
            {"id": case_id}
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Expediente no encontrado")

        conn.execute(
            text("""
                UPDATE cases
                SET
                  test_mode = TRUE,
                  override_deadlines = TRUE,
                  updated_at = NOW()
                WHERE id = :id
            """),
            {"id": case_id}
        )

    return {"ok": True, "mode": "TEST_ONLY"}
