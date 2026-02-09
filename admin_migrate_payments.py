# admin_migrate_payments.py — migración de columnas de pago (MVP)
import os
from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import text

from database import get_engine

router = APIRouter(prefix="/admin/migrate", tags=["admin"])

def _require_admin_token(x_admin_token: str | None) -> None:
    expected = (os.getenv("ADMIN_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN no está configurado.")
    if not x_admin_token or x_admin_token.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

@router.post("/payments")
def migrate_payments(x_admin_token: str | None = Header(default=None, alias="x-admin-token")):
    _require_admin_token(x_admin_token)

    engine = get_engine()
    created = []
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE cases ADD COLUMN IF NOT EXISTS payment_status TEXT;"))
        conn.execute(text("ALTER TABLE cases ADD COLUMN IF NOT EXISTS product_code TEXT;"))
        conn.execute(text("ALTER TABLE cases ADD COLUMN IF NOT EXISTS stripe_session_id TEXT;"))
        conn.execute(text("ALTER TABLE cases ADD COLUMN IF NOT EXISTS stripe_payment_intent TEXT;"))
        conn.execute(text("ALTER TABLE cases ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ;"))
        created.append("cases.payment_columns")

        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_cases_payment_status ON cases(payment_status);"))
        created.append("idx_cases_payment_status")

    return {"ok": True, "message": "Migración de pagos aplicada.", "created": created}
