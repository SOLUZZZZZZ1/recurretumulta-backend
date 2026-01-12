# cases.py — Gestión del expediente (datos interesado + autorización)
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from database import get_engine

router = APIRouter(prefix="/cases", tags=["cases"])


# =========================
# MODELOS
# =========================

class CaseDetailsIn(BaseModel):
    full_name: str = Field(..., description="Nombre y apellidos")
    dni_nie: str = Field(..., description="DNI o NIE")
    domicilio_notif: str = Field(..., description="Domicilio a efectos de notificaciones")
    email: EmailStr
    telefono: Optional[str] = None


class CaseDetailsOut(CaseDetailsIn):
    case_id: str
    authorized: bool
    authorized_at: Optional[datetime] = None


# =========================
# HELPERS
# =========================

def _get_case_or_404(conn, case_id: str):
    row = conn.execute(
        text("SELECT id FROM cases WHERE id = :case_id"),
        {"case_id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="case_id no existe")


# =========================
# ENDPOINTS
# =========================

@router.post("/{case_id}/details")
def save_case_details(case_id: str, data: CaseDetailsIn):
    """
    Guarda los datos del interesado (post-pago, pre-autorización).
    """
    engine = get_engine()
    with engine.begin() as conn:
        _get_case_or_404(conn, case_id)

        # Guardamos en JSON dentro de cases (campo flexible)
        conn.execute(
            text(
                """
                UPDATE cases
                SET interested_data = :data,
                    updated_at = NOW()
                WHERE id = :case_id
                """
            ),
            {
                "case_id": case_id,
                "data": json.dumps(data.dict()),
            },
        )

        # Evento
        conn.execute(
            text(
                """
                INSERT INTO events (case_id, type, payload, created_at)
                VALUES (:case_id, 'details_saved', CAST(:payload AS JSONB), NOW())
                """
            ),
            {
                "case_id": case_id,
                "payload": json.dumps({"fields": list(data.dict().keys())}),
            },
        )

    return {"ok": True}


@router.get("/{case_id}/details", response_model=CaseDetailsOut)
def get_case_details(case_id: str):
    """
    Devuelve los datos del interesado (para OPS y frontend).
    """
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT
                    id,
                    interested_data,
                    authorized,
                    authorized_at
                FROM cases
                WHERE id = :case_id
                """
            ),
            {"case_id": case_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="case_id no existe")

        interested_data = row.interested_data or {}
        return {
            "case_id": row.id,
            **interested_data,
            "authorized": bool(row.authorized),
            "authorized_at": row.authorized_at,
        }


@router.post("/{case_id}/authorize")
def authorize_case(case_id: str):
    """
    Autoriza expresamente a LA TALAMANQUINA, S.L. a presentar el recurso.
    """
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT interested_data, authorized
                FROM cases
                WHERE id = :case_id
                """
            ),
            {"case_id": case_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="case_id no existe")

        if not row.interested_data:
            raise HTTPException(
                status_code=400,
                detail="Faltan los datos del interesado",
            )

        if row.authorized:
            return {"ok": True, "authorized": True}

        conn.execute(
            text(
                """
                UPDATE cases
                SET authorized = TRUE,
                    authorized_at = NOW(),
                    updated_at = NOW()
                WHERE id = :case_id
                """
            ),
            {"case_id": case_id},
        )

        conn.execute(
            text(
                """
                INSERT INTO events (case_id, type, payload, created_at)
                VALUES (:case_id, 'authorized', CAST(:payload AS JSONB), NOW())
                """
            ),
            {
                "case_id": case_id,
                "payload": json.dumps(
                    {
                        "authorized_to": "LA TALAMANQUINA, S.L.",
                        "purpose": "presentacion recurso administrativo",
                    }
                ),
            },
        )

    return {"ok": True, "authorized": True}
