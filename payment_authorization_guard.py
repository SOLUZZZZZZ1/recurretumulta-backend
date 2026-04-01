# payment_authorization_guard.py
# Bloqueo backend: no permitir checkout/pago si el caso no está autorizado.

from typing import Any, Dict
from fastapi import HTTPException
from sqlalchemy import text


def require_case_authorized_before_payment(conn, case_id: str) -> Dict[str, Any]:
    row = conn.execute(
        text(
            """
            SELECT
                id,
                COALESCE(authorized, FALSE) AS authorized,
                authorized_at,
                COALESCE(payment_status, '') AS payment_status,
                COALESCE(interested_data, '{}'::jsonb) AS interested_data
            FROM cases
            WHERE id = :id
            """
        ),
        {"id": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")

    interested_data = row[4] if isinstance(row[4], dict) else {}

    missing = []
    if not interested_data.get("full_name"):
        missing.append("full_name")
    if not interested_data.get("dni_nie"):
        missing.append("dni_nie")
    if not interested_data.get("domicilio_notif"):
        missing.append("domicilio_notif")
    if not interested_data.get("email"):
        missing.append("email")

    if missing:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Debes completar los datos del interesado antes de pagar",
                "missing_fields": missing,
            },
        )

    if not bool(row[1]):
        raise HTTPException(
            status_code=409,
            detail="Debes autorizar antes de pagar",
        )

    return {
        "case_id": str(row[0]),
        "authorized": bool(row[1]),
        "authorized_at": row[2],
        "payment_status": row[3],
        "interested_data": interested_data,
    }
