"""Minimal public case recovery, separate from legacy authorization writes."""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import text
from database import get_engine

router = APIRouter(prefix="/cases", tags=["case-recovery"])

@router.get("/continue-lookup")
def continue_lookup(
    response: Response,
    q: str = Query(..., min_length=3),
    email: Optional[str] = Query(default=None),
):
    """
    Recuperación pública mínima para continuar un expediente.

    Permite buscar por:
    - case_id interno
    - expediente_ref administrativo
    - email, si se informa

    Devuelve solo datos mínimos de navegación.
    """
    term = (q or "").strip()
    email_term = (email or "").strip().lower()

    if not term:
        raise HTTPException(status_code=400, detail="Introduce un expediente válido")

    response.headers["Cache-Control"] = "no-store, max-age=0"
    engine = get_engine()

    with engine.begin() as conn:
        # Primero por UUID/case_id exacto
        row = conn.execute(
            text(
                """
                SELECT
                    id,
                    expediente_ref,
                    status,
                    COALESCE(payment_status, '') AS payment_status,
                    COALESCE(authorized, FALSE) AS authorized,
                    COALESCE(contact_email, '') AS contact_email,
                    COALESCE(interested_data, '{}'::jsonb) AS interested_data
                FROM cases
                WHERE CAST(id AS TEXT) = :term
                LIMIT 1
                """
            ),
            {"term": term},
        ).fetchone()

        # Después por expediente_ref administrativo exacto
        if not row:
            if email_term:
                row = conn.execute(
                    text(
                        """
                        SELECT
                            id,
                            expediente_ref,
                            status,
                            COALESCE(payment_status, '') AS payment_status,
                            COALESCE(authorized, FALSE) AS authorized,
                            COALESCE(contact_email, '') AS contact_email,
                            COALESCE(interested_data, '{}'::jsonb) AS interested_data
                        FROM cases
                        WHERE expediente_ref = :term
                          AND (
                            LOWER(COALESCE(contact_email, '')) = :email
                            OR LOWER(COALESCE(interested_data->>'email', '')) = :email
                          )
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """
                    ),
                    {"term": term, "email": email_term},
                ).fetchone()
            else:
                row = conn.execute(
                    text(
                        """
                        SELECT
                            id,
                            expediente_ref,
                            status,
                            COALESCE(payment_status, '') AS payment_status,
                            COALESCE(authorized, FALSE) AS authorized,
                            COALESCE(contact_email, '') AS contact_email,
                            COALESCE(interested_data, '{}'::jsonb) AS interested_data
                        FROM cases
                        WHERE expediente_ref = :term
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """
                    ),
                    {"term": term},
                ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="No hemos encontrado ese expediente")

        interested = row[6] if isinstance(row[6], dict) else {}
        public_email = row[5] or interested.get("email") or ""

        return {
            "ok": True,
            "case_id": str(row[0]),
            "expediente_ref": row[1],
            "status": row[2],
            "payment_status": row[3],
            "authorized": bool(row[4]),
            "has_email": bool(public_email),
        }

