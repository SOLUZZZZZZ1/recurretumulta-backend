# authorize.py — autorización completa (legal + trazabilidad)

import json
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from database import get_engine
from pdf_builder import build_pdf
from b2_storage import upload_bytes

router = APIRouter(prefix="/cases", tags=["cases"])


def _utcnow():
    return datetime.now(timezone.utc)


def _get_ip(request: Request):
    return (
        request.headers.get("x-forwarded-for")
        or request.headers.get("x-real-ip")
        or request.client.host
    )


@router.post("/{case_id}/authorize")
async def authorize_case(case_id: str, request: Request):

    engine = get_engine()

    with engine.begin() as conn:

        # 1. Cargar datos del interesado
        row = conn.execute(
            text("SELECT interested_data FROM cases WHERE id=:id"),
            {"id": case_id}
        ).fetchone()

        if not row:
            raise HTTPException(404, "Expediente no encontrado")

        data = row[0] or {}

        # 2. Validar datos obligatorios
        required = ["full_name", "dni_nie", "domicilio_notif", "email"]
        for field in required:
            if not data.get(field):
                raise HTTPException(400, f"Falta campo obligatorio: {field}")

        # 3. Datos técnicos
        ip = _get_ip(request)
        user_agent = request.headers.get("user-agent", "")

        now = _utcnow().isoformat()

        # 4. Snapshot completo
        snapshot = {
            "case_id": case_id,
            "data": data,
            "ip": ip,
            "user_agent": user_agent,
            "authorized_at": now,
            "version": "v1_dgt_homologado"
        }

        # 5. Guardar en cases
        conn.execute(
            text("""
            UPDATE cases
            SET authorized = TRUE,
                authorized_at = NOW(),
                authorization_version = :version,
                authorization_ip = :ip,
                authorization_user_agent = :ua,
                authorization_full_name = :name,
                authorization_dni_nie = :dni,
                authorization_address = :address,
                authorization_email = :email,
                authorization_phone = :phone,
                authorization_checks = :checks,
                authorization_snapshot = CAST(:snapshot AS JSONB),
                updated_at = NOW()
            WHERE id = :id
            """),
            {
                "id": case_id,
                "version": "v1_dgt_homologado",
                "ip": ip,
                "ua": user_agent,
                "name": data.get("full_name"),
                "dni": data.get("dni_nie"),
                "address": data.get("domicilio_notif"),
                "email": data.get("email"),
                "phone": data.get("telefono"),
                "checks": json.dumps({
                    "accepted_text": True,
                    "confirmed_identity": True
                }),
                "snapshot": json.dumps(snapshot),
            }
        )

        # 6. Evento
        conn.execute(
            text("""
            INSERT INTO events(case_id, type, payload, created_at)
            VALUES (:case_id, 'client_authorized', CAST(:payload AS JSONB), NOW())
            """),
            {
                "case_id": case_id,
                "payload": json.dumps(snapshot)
            }
        )

        # 7. Generar PDF de autorización
        contenido = f"""
AUTORIZACIÓN DE REPRESENTACIÓN

Expediente: {case_id}

Nombre: {data.get("full_name")}
DNI/NIE: {data.get("dni_nie")}
Domicilio: {data.get("domicilio_notif")}
Email: {data.get("email")}
Teléfono: {data.get("telefono")}

Autorizo a LA TALAMANQUINA S.L. (RecurreTuMulta)
a actuar en mi nombre para la tramitación administrativa.

Fecha: {now}
IP: {ip}
User Agent: {user_agent}
"""

        pdf_bytes = build_pdf("AUTORIZACIÓN", contenido)

        bucket, key = upload_bytes(
            case_id,
            "autorizaciones",
            pdf_bytes,
            ".pdf",
            "application/pdf"
        )

        # 8. Guardar documento
        conn.execute(
            text("""
            INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
            VALUES (:id, 'autorizacion_cliente_pdf', :b, :k, :mime, :size, NOW())
            """),
            {
                "id": case_id,
                "b": bucket,
                "k": key,
                "mime": "application/pdf",
                "size": len(pdf_bytes)
            }
        )

    return {
        "ok": True,
        "case_id": case_id,
        "authorized": True
    }