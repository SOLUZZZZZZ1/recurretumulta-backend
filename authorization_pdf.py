from __future__ import annotations

import io
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Image
from sqlalchemy import text

from b2_storage import upload_bytes


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def get_request_ip(request) -> str:
    try:
        forwarded = request.headers.get("x-forwarded-for", "").strip()
        if forwarded:
            return forwarded.split(",")[0].strip()
    except Exception:
        pass

    try:
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip
    except Exception:
        pass

    try:
        if request.client and request.client.host:
            return request.client.host
    except Exception:
        pass

    return ""


def _get_case_snapshot(conn, case_id: str) -> Dict[str, Any]:
    row = conn.execute(
        text(
            """
            SELECT
                id,
                organismo,
                expediente_ref,
                contact_email,
                COALESCE(interested_data,'{}'::jsonb) AS interested_data
            FROM cases
            WHERE id = :id
            """
        ),
        {"id": case_id},
    ).fetchone()

    if not row:
        raise ValueError("Case not found")

    interested = row[4] if row[4] else {}
    if not isinstance(interested, dict):
        interested = {}

    return {
        "case_id": str(row[0]),
        "organismo": row[1] or "",
        "expediente_ref": row[2] or "",
        "contact_email": row[3] or "",
        "interested_data": interested,
    }


def _authorization_payload_from_case(case_meta: Dict[str, Any], ip: str, version: str) -> Dict[str, str]:
    interested = case_meta.get("interested_data") or {}

    full_name = interested.get("full_name") or interested.get("contact_name") or interested.get("name") or ""
    dni_nie = interested.get("dni_nie") or interested.get("dni") or interested.get("nie") or ""
    domicilio = interested.get("domicilio_notif") or interested.get("address") or interested.get("domicilio") or ""
    email = interested.get("email") or case_meta.get("contact_email") or ""
    telefono = interested.get("telefono") or interested.get("phone") or ""

    return {
        "case_id": _safe_str(case_meta.get("case_id")),
        "expediente_ref": _safe_str(case_meta.get("expediente_ref")),
        "organismo": _safe_str(case_meta.get("organismo")),
        "full_name": _safe_str(full_name),
        "dni_nie": _safe_str(dni_nie),
        "domicilio_notif": _safe_str(domicilio),
        "email": _safe_str(email),
        "telefono": _safe_str(telefono),
        "ip": _safe_str(ip),
        "version": _safe_str(version or "v1"),
        "authorized_at": _utcnow_iso(),
    }


def _find_signature_path() -> str:
    candidates = [
        os.getenv("SIGNATURE_PATH", "").strip(),
        os.path.join(os.path.dirname(__file__), "templates", "firma.png"),
        os.path.join(os.getcwd(), "templates", "firma.png"),
        os.path.join(os.getcwd(), "backend", "templates", "firma.png"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return ""


def generate_authorization_pdf(data: Dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2.2 * cm,
        rightMargin=2.2 * cm,
        topMargin=2.0 * cm,
        bottomMargin=2.0 * cm,
        title=f"Autorizacion {data.get('case_id','')}",
        author="RecurreTuMulta / LA TALAMANQUINA S.L.",
    )

    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    normal = styles["BodyText"]
    normal.leading = 16
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=9, leading=12)

    content = []
    content.append(Paragraph("AUTORIZACION DE REPRESENTACION", title_style))
    content.append(Spacer(1, 0.4 * cm))

    if data.get("organismo"):
        content.append(Paragraph(f"<b>Organismo:</b> {data['organismo']}", normal))
    if data.get("expediente_ref"):
        content.append(Paragraph(f"<b>Expediente administrativo:</b> {data['expediente_ref']}", normal))
    content.append(Paragraph(f"<b>Expediente interno:</b> {data.get('case_id','')}", normal))
    content.append(Spacer(1, 0.3 * cm))

    content.append(Paragraph(f"<b>Nombre y apellidos:</b> {data.get('full_name','') or '—'}", normal))
    content.append(Paragraph(f"<b>DNI/NIE:</b> {data.get('dni_nie','') or '—'}", normal))
    content.append(Paragraph(f"<b>Domicilio a efectos de notificaciones:</b> {data.get('domicilio_notif','') or '—'}", normal))
    content.append(Paragraph(f"<b>Email:</b> {data.get('email','') or '—'}", normal))
    content.append(Paragraph(f"<b>Telefono:</b> {data.get('telefono','') or '—'}", normal))
    content.append(Spacer(1, 0.5 * cm))

    content.append(Paragraph(
        "La persona identificada anteriormente <b>autoriza expresamente</b> a "
        "<b>LA TALAMANQUINA, S.L.</b> (RecurreTuMulta) para actuar en su nombre "
        "en la tramitacion administrativa del expediente indicado, incluyendo la "
        "preparacion, presentacion de alegaciones y/o recursos ante la Administracion "
        "competente, asi como la obtencion del justificante oficial de presentacion y "
        "las actuaciones directamente vinculadas a dicho expediente.",
        normal,
    ))
    content.append(Spacer(1, 0.4 * cm))

    content.append(Paragraph(
        "La persona autorizante declara que los datos facilitados son correctos y que "
        "ostenta legitimacion suficiente sobre el expediente asociado a esta autorizacion.",
        normal,
    ))
    content.append(Spacer(1, 0.6 * cm))

    content.append(Paragraph(f"<b>Fecha y hora (UTC):</b> {data.get('authorized_at','')}", normal))
    content.append(Paragraph(f"<b>IP de origen:</b> {data.get('ip','') or '—'}", normal))
    content.append(Paragraph(f"<b>Version del texto de autorizacion:</b> {data.get('version','') or '—'}", normal))
    content.append(Spacer(1, 1.0 * cm))

    content.append(Paragraph("Firma del representante / autorizado:", normal))
    content.append(Spacer(1, 0.3 * cm))

    firma_path = _find_signature_path()
    if firma_path:
        img = Image(firma_path, width=6 * cm, height=2.2 * cm)
        img.hAlign = "LEFT"
        content.append(img)
        content.append(Spacer(1, 0.2 * cm))
    else:
        content.append(Paragraph("__________________________________________", normal))
        content.append(Spacer(1, 0.2 * cm))
        # Línea temporal de diagnóstico. Cuando ya salga la firma, se puede quitar.
        content.append(Paragraph("<font size='8'>[DEBUG] firma.png no encontrada</font>", small))
        content.append(Spacer(1, 0.1 * cm))

    content.append(Paragraph("<b>LA TALAMANQUINA, S.L.</b>", normal))

    content.append(Spacer(1, 1.0 * cm))

    content.append(Paragraph("Firma del representado / cliente:", normal))
    content.append(Spacer(1, 0.8 * cm))
    content.append(Paragraph("__________________________________________", normal))

    content.append(Spacer(1, 0.6 * cm))

    content.append(Paragraph(
        "Documento generado automaticamente por RecurreTuMulta. "
        "La firma manuscrita del cliente valida esta autorizacion.",
        small
    ))

    doc.build(content)
    return buffer.getvalue()


def _existing_authorization_doc(conn, case_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        text(
            """
            SELECT id, b2_bucket, b2_key, mime, created_at
            FROM documents
            WHERE case_id = :id
              AND kind = 'authorization_pdf'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"id": case_id},
    ).fetchone()

    if not row:
        return None

    return {
        "id": str(row[0]),
        "bucket": row[1],
        "key": row[2],
        "mime": row[3],
        "created_at": str(row[4]),
    }


def ensure_authorization_pdf(conn, case_id: str, request, version: str = "v1") -> Dict[str, Any]:
    existing = _existing_authorization_doc(conn, case_id)
    if existing:
        return {"ok": True, "existing": True, "document": existing}

    ip = get_request_ip(request)
    case_meta = _get_case_snapshot(conn, case_id)
    payload = _authorization_payload_from_case(case_meta, ip=ip, version=version)
    pdf_bytes = generate_authorization_pdf(payload)

    bucket, key = upload_bytes(
        case_id,
        "authorization",
        pdf_bytes,
        ".pdf",
        "application/pdf",
    )

    conn.execute(
        text(
            """
            INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime, size_bytes, created_at)
            VALUES (:id, 'authorization_pdf', :b, :k, 'application/pdf', :s, NOW())
            """
        ),
        {"id": case_id, "b": bucket, "k": key, "s": len(pdf_bytes)},
    )

    conn.execute(
        text(
            """
            INSERT INTO events(case_id, type, payload, created_at)
            VALUES (:id, 'authorization_pdf_generated', CAST(:payload AS JSONB), NOW())
            """
        ),
        {
            "id": case_id,
            "payload": json.dumps(
                {
                    "bucket": bucket,
                    "key": key,
                    "ip": ip,
                    "version": version,
                    "generated_at": payload["authorized_at"],
                    "signature_path_found": _find_signature_path(),
                }
            ),
        },
    )

    return {
        "ok": True,
        "existing": False,
        "document": {
            "bucket": bucket,
            "key": key,
            "mime": "application/pdf",
        },
    }
