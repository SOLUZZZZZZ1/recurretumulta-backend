# authorize_completo.py — autorización completa (datos + IP + user agent + snapshot + PDF)
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine
from pdf_builder import build_pdf
from b2_storage import upload_bytes, presign_get_url

router = APIRouter(prefix="/cases", tags=["cases"])


def _utcnow():
    return datetime.now(timezone.utc)


def _get_ip(request: Request) -> str:
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[0].strip()
    xri = (request.headers.get("x-real-ip") or "").strip()
    if xri:
        return xri
    if request.client and request.client.host:
        return request.client.host
    return ""


class CaseDetailsBody(BaseModel):
    full_name: str = Field(..., min_length=3)
    dni_nie: str = Field(..., min_length=3)
    domicilio_notif: str = Field(..., min_length=5)
    email: str = Field(..., min_length=5)
    telefono: Optional[str] = None


class AuthorizeBody(BaseModel):
    version: str = Field(default="v1_dgt_homologado", min_length=1)
    accepted_text: bool = True
    confirmed_identity: bool = True


def _case_or_404(conn, case_id: str):
    row = conn.execute(
        text("SELECT id, COALESCE(interested_data, '{}'::jsonb) FROM cases WHERE id=:id"),
        {"id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    data = row[1] if isinstance(row[1], dict) else {}
    return {"id": str(row[0]), "interested_data": data}


def _append_event(conn, case_id: str, event_type: str, payload: Dict[str, Any]):
    conn.execute(
        text(
            """
            INSERT INTO events(case_id, type, payload, created_at)
            VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())
            """
        ),
        {
            "case_id": case_id,
            "type": event_type,
            "payload": json.dumps(payload, ensure_ascii=False),
        },
    )


@router.post("/{case_id}/details")
def save_case_details(case_id: str, body: CaseDetailsBody):
    engine = get_engine()
    with engine.begin() as conn:
        current = _case_or_404(conn, case_id)
        interested = dict(current["interested_data"] or {})
        interested.update(
            {
                "full_name": body.full_name.strip(),
                "dni_nie": body.dni_nie.strip().upper(),
                "domicilio_notif": body.domicilio_notif.strip(),
                "email": body.email.strip(),
                "telefono": (body.telefono or "").strip() or None,
            }
        )

        conn.execute(
            text(
                """
                UPDATE cases
                SET interested_data = CAST(:data AS JSONB),
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": case_id, "data": json.dumps(interested, ensure_ascii=False)},
        )

        _append_event(
            conn,
            case_id,
            "client_details_saved",
            {
                "full_name": interested.get("full_name"),
                "dni_nie": interested.get("dni_nie"),
                "domicilio_notif": interested.get("domicilio_notif"),
                "email": interested.get("email"),
                "telefono": interested.get("telefono"),
                "saved_at": _utcnow().isoformat(),
            },
        )

    return {"ok": True, "case_id": case_id, "interested_data": interested}


@router.post("/{case_id}/authorize")
async def authorize_case(case_id: str, request: Request, body: AuthorizeBody):
    if not body.accepted_text or not body.confirmed_identity:
        raise HTTPException(status_code=400, detail="Debes aceptar el texto y confirmar tu identidad")

    engine = get_engine()

    with engine.begin() as conn:
        current = _case_or_404(conn, case_id)
        data = dict(current["interested_data"] or {})

        required = ["full_name", "dni_nie", "domicilio_notif", "email"]
        missing = [field for field in required if not data.get(field)]
        if missing:
            raise HTTPException(
                status_code=400,
                detail={"message": "Faltan datos del interesado", "missing_fields": missing},
            )

        ip = _get_ip(request)
        user_agent = (request.headers.get("user-agent") or "").strip()
        now = _utcnow().isoformat()

        checks = {
            "accepted_text": bool(body.accepted_text),
            "confirmed_identity": bool(body.confirmed_identity),
        }

        snapshot = {
            "case_id": case_id,
            "version": body.version,
            "authorized_at": now,
            "ip": ip,
            "user_agent": user_agent,
            "checks": checks,
            "full_name": data.get("full_name"),
            "dni_nie": data.get("dni_nie"),
            "domicilio_notif": data.get("domicilio_notif"),
            "email": data.get("email"),
            "telefono": data.get("telefono"),
        }

        conn.execute(
            text(
                """
                UPDATE cases
                SET authorized = TRUE,
                    authorized_at = NOW(),
                    authorization_version = :version,
                    authorization_ip = :ip,
                    authorization_user_agent = :ua,
                    authorization_full_name = :full_name,
                    authorization_dni_nie = :dni_nie,
                    authorization_address = :address,
                    authorization_email = :email,
                    authorization_phone = :phone,
                    authorization_checks = CAST(:checks AS JSONB),
                    authorization_snapshot = CAST(:snapshot AS JSONB),
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": case_id,
                "version": body.version,
                "ip": ip,
                "ua": user_agent,
                "full_name": data.get("full_name"),
                "dni_nie": data.get("dni_nie"),
                "address": data.get("domicilio_notif"),
                "email": data.get("email"),
                "phone": data.get("telefono"),
                "checks": json.dumps(checks, ensure_ascii=False),
                "snapshot": json.dumps(snapshot, ensure_ascii=False),
            },
        )

        _append_event(conn, case_id, "client_authorized", snapshot)

        texto_autorizacion = f"""
AUTORIZACIÓN DE REPRESENTACIÓN

Expediente: {case_id}

Nombre y apellidos: {data.get("full_name")}
DNI/NIE: {data.get("dni_nie")}
Domicilio a efectos de notificaciones: {data.get("domicilio_notif")}
Email: {data.get("email")}
Teléfono: {data.get("telefono") or ""}

Texto autorizado:
Autorizo a LA TALAMANQUINA, S.L. (RecurreTuMulta) a actuar en mi nombre para la tramitación administrativa del expediente asociado a este proceso, incluyendo la preparación y presentación de alegaciones y/o recursos ante la DGT u organismo competente, así como la obtención del justificante oficial de presentación.

Versión del texto: {body.version}
Fecha y hora de autorización: {now}
IP: {ip}
User Agent: {user_agent}
"""

        pdf_bytes = build_pdf("AUTORIZACIÓN DE REPRESENTACIÓN", texto_autorizacion)
        bucket, key = upload_bytes(
            case_id,
            "autorizaciones",
            pdf_bytes,
            ".pdf",
            "application/pdf",
        )

        conn.execute(
            text(
                """
                INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
                VALUES (:case_id, 'autorizacion_cliente_pdf', :bucket, :key, 'application/pdf', :size_bytes, NOW())
                """
            ),
            {
                "case_id": case_id,
                "bucket": bucket,
                "key": key,
                "size_bytes": len(pdf_bytes),
            },
        )

    download_url = presign_get_url(
        bucket,
        key,
        expires_seconds=900,
        filename=f"autorizacion-{case_id}.pdf",
    )

    return {
        "ok": True,
        "case_id": case_id,
        "authorized": True,
        "authorization_version": body.version,
        "authorization_download_url": download_url,
        "download_url": download_url,
    }



@router.get("/{case_id}/authorization/download")
def download_authorization(case_id: str):
    """
    Devuelve una URL temporal de descarga para la última autorización PDF del expediente.
    No expone B2 directamente ni requiere que el frontend conozca bucket/key.
    """
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT b2_bucket, b2_key
                FROM documents
                WHERE case_id = :id
                  AND kind IN ('autorizacion_cliente_pdf', 'authorization_pdf')
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"id": case_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Autorización no encontrada")

        bucket, key = row[0], row[1]

    url = presign_get_url(
        bucket,
        key,
        expires_seconds=900,
        filename=f"autorizacion-{case_id}.pdf",
    )

    return {
        "ok": True,
        "case_id": case_id,
        "url": url,
        "download_url": url,
    }
