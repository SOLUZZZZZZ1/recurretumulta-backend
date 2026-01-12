# cases.py — Gestión del expediente (datos interesado + autorización) ✅ robusto JSONB
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from database import get_engine

router = APIRouter(prefix="/cases", tags=["cases"])


class CaseDetailsIn(BaseModel):
    full_name: str = Field(..., description="Nombre y apellidos")
    dni_nie: str = Field(..., description="DNI o NIE")
    domicilio_notif: str = Field(..., description="Domicilio a efectos de notificaciones")
    email: EmailStr
    telefono: Optional[str] = None


def _case_exists(conn, case_id: str) -> None:
    row = conn.execute(
        text("SELECT 1 FROM cases WHERE id=:id"),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="case_id no existe")


@router.post("/{case_id}/details")
def save_case_details(case_id: str, data: CaseDetailsIn):
    """
    Guarda los datos del interesado (post-pago, pre-autorización).
    Requiere columnas en cases:
      - interested_data JSONB
      - authorized BOOLEAN
      - authorized_at TIMESTAMPTZ
    """
    engine = get_engine()
    with engine.begin() as conn:
        _case_exists(conn, case_id)

        payload = data.dict()

        conn.execute(
            text(
                """
                UPDATE cases
                SET interested_data = CAST(:payload AS JSONB),
                    updated_at = NOW()
                WHERE id = :case_id
                """
            ),
            {"case_id": case_id, "payload": json.dumps(payload)},
        )

        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:case_id, 'details_saved', CAST(:ev AS JSONB), NOW())
                """
            ),
            {"case_id": case_id, "ev": json.dumps({"fields": list(payload.keys())})},
        )

    return {"ok": True}


@router.get("/{case_id}/details")
def get_case_details(case_id: str):
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT interested_data, authorized, authorized_at
                FROM cases
                WHERE id=:case_id
                """
            ),
            {"case_id": case_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="case_id no existe")

        interested = row[0] or {}
        return {
            "ok": True,
            "case_id": case_id,
            "interested_data": interested,
            "authorized": bool(row[1]),
            "authorized_at": row[2],
        }


@router.post("/{case_id}/authorize")
def authorize_case(case_id: str):
    """
    Autoriza expresamente a LA TALAMANQUINA, S.L. a presentar el recurso.
    """
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT interested_data, authorized FROM cases WHERE id=:case_id"),
            {"case_id": case_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="case_id no existe")

        interested_data = row[0] or {}
        already = bool(row[1])

        if not interested_data:
            raise HTTPException(status_code=400, detail="Faltan los datos del interesado")

        if already:
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
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:case_id, 'authorized', CAST(:ev AS JSONB), NOW())
                """
            ),
            {
                "case_id": case_id,
                "ev": json.dumps(
                    {
                        "authorized_to": "LA TALAMANQUINA, S.L.",
                        "purpose": "presentacion recurso administrativo",
                    }
                ),
            },
        )

    return {"ok": True, "authorized": True}
