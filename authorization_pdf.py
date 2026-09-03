from __future__ import annotations

import hashlib
import html
import io
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy import text

from b2_storage import delete_object, upload_bytes
from case_authority import build_authority_document_issue_attestation
from rtm_core.http_security import trusted_client_ip


DOCUMENT_CUSTODY = "rtm_internal_only"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _pdf_value(value: Any, *, limit: int = 500) -> str:
    """Escapa datos antes de entregarlos al parser de markup de ReportLab."""

    text_value = _safe_str(value).replace("\x00", " ")[:limit]
    return html.escape(text_value, quote=True)


def _fecha_es_larga(iso_value: str) -> str:
    meses = {
        1: "enero",
        2: "febrero",
        3: "marzo",
        4: "abril",
        5: "mayo",
        6: "junio",
        7: "julio",
        8: "agosto",
        9: "septiembre",
        10: "octubre",
        11: "noviembre",
        12: "diciembre",
    }
    try:
        raw = (iso_value or "").strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
    except Exception:
        dt = datetime.now(timezone.utc)
    return f"{dt.day} de {meses.get(dt.month, '')} de {dt.year}"


def _infer_lugar(data: Dict[str, str]) -> str:
    domicilio = (data.get("domicilio_notif") or "").strip()
    if domicilio:
        partes = [p.strip() for p in domicilio.split(",") if p.strip()]
        if partes:
            candidatos = list(reversed(partes))
            for seg in candidatos:
                limpio = re.sub(r"\b\d{5}\b", "", seg).strip(" -")
                if limpio and limpio.lower() not in {"españa", "espana"}:
                    return limpio
    organismo = (data.get("organismo") or "").strip()
    return organismo or "España"


def get_request_ip(request) -> str:
    return trusted_client_ip(request)


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
        content.append(Paragraph(f"<b>Organismo:</b> {_pdf_value(data['organismo'])}", normal))
    if data.get("expediente_ref"):
        content.append(Paragraph(f"<b>Expediente administrativo:</b> {_pdf_value(data['expediente_ref'])}", normal))
    content.append(Paragraph(f"<b>Expediente interno:</b> {_pdf_value(data.get('case_id',''))}", normal))
    content.append(Paragraph("<b>Código de trámite:</b> 00040005001", normal))
    content.append(Paragraph("<b>Nombre del trámite:</b> Presentación de escritos de alegaciones o recursos", normal))
    content.append(Spacer(1, 0.3 * cm))

    content.append(Paragraph(f"<b>Nombre y apellidos:</b> {_pdf_value(data.get('full_name') or '—')}", normal))
    content.append(Paragraph(f"<b>DNI/NIE:</b> {_pdf_value(data.get('dni_nie') or '—')}", normal))
    content.append(Paragraph(f"<b>Domicilio del interesado a efectos de notificaciones:</b> {_pdf_value(data.get('domicilio_notif') or '—')}", normal))
    content.append(Paragraph(f"<b>Email:</b> {_pdf_value(data.get('email') or '—')}", normal))
    content.append(Paragraph(f"<b>Telefono:</b> {_pdf_value(data.get('telefono') or '—')}", normal))
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
    content.append(Spacer(1, 0.4 * cm))

    lugar = _infer_lugar(data)
    fecha_larga = _fecha_es_larga(data.get("authorized_at", ""))

    content.append(Paragraph("<b>ALCANCE DE LA REPRESENTACION</b>", normal))
    content.append(Spacer(1, 0.1 * cm))
    content.append(Paragraph(
        "La persona firmante autoriza expresamente a <b>LA TALAMANQUINA, S.L.</b> para actuar en su nombre ante "
        "la Direccion General de Trafico y organismos competentes en relacion con expedientes sancionadores de "
        "trafico, incluyendo la preparacion y presentacion de alegaciones, recursos y la obtencion del justificante "
        "oficial de presentacion.",
        normal,
    ))
    content.append(Spacer(1, 0.35 * cm))
    content.append(Paragraph(f"En {_pdf_value(lugar)}, a {_pdf_value(fecha_larga)}", normal))
    content.append(Spacer(1, 0.55 * cm))

    content.append(Paragraph(f"<b>Fecha y hora (UTC):</b> {_pdf_value(data.get('authorized_at',''))}", normal))
    content.append(Paragraph(f"<b>IP de origen:</b> {_pdf_value(data.get('ip') or '—')}", normal))
    content.append(Paragraph(f"<b>Version del texto de autorizacion:</b> {_pdf_value(data.get('version') or '—')}", normal))
    content.append(Spacer(1, 1.0 * cm))

    content.append(Paragraph("Firma del representante / autorizado:", normal))
    content.append(Spacer(1, 0.3 * cm))
    # A reusable raster signature is not an authority and is trivially easy to
    # extract or synthesize.  The representative must sign the exact issued
    # document through the approved human/cryptographic process.
    content.append(Paragraph("__________________________________________", normal))
    content.append(Spacer(1, 0.2 * cm))

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
            SELECT id, sha256, mime, size_bytes
            FROM documents
            WHERE case_id = :id
              AND kind = 'authorization_pdf'
              AND sha256 ~ '^[0-9a-f]{64}$'
              AND mime = 'application/pdf'
              AND size_bytes > 0
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
        "sha256": str(row[1]),
        "mime": row[2],
        "size_bytes": int(row[3]),
        "custody": DOCUMENT_CUSTODY,
    }


def _cleanup_authorization_uploads(
    coordinates: List[tuple[str, str]],
) -> None:
    """Retira subidas no confirmadas por SQL sin ocultar la causa original."""

    for bucket, key in reversed(coordinates):
        try:
            delete_object(bucket, key)
        except Exception:
            pass


def ensure_authorization_pdf(
    conn,
    case_id: str,
    request,
    version: str = "v1",
    *,
    authority_payload: Dict[str, Any],
    uploaded_coordinates: Optional[List[tuple[str, str]]] = None,
) -> Dict[str, Any]:
    case_row = conn.execute(
        text("SELECT id FROM cases WHERE id = :id FOR UPDATE"),
        {"id": case_id},
    ).fetchone()
    if not case_row:
        raise ValueError("Case not found")

    ip = get_request_ip(request)
    case_meta = _get_case_snapshot(conn, case_id)
    payload = _authorization_payload_from_case(case_meta, ip=ip, version=version)
    pdf_bytes = generate_authorization_pdf(payload)
    pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()

    owned_coordinates: List[tuple[str, str]] = []
    coordinates = (
        uploaded_coordinates
        if uploaded_coordinates is not None
        else owned_coordinates
    )
    bucket, key = upload_bytes(
        case_id,
        "authorization",
        pdf_bytes,
        ".pdf",
        "application/pdf",
    )
    coordinates.append((bucket, key))

    try:
        document_row = conn.execute(
            text(
                """
                INSERT INTO documents(
                    case_id, kind, b2_bucket, b2_key, mime, size_bytes, sha256, created_at
                )
                VALUES (
                    :id, 'authorization_pdf', :b, :k, 'application/pdf', :s, :sha256, NOW()
                )
                RETURNING id
                """
            ),
            {
                "id": case_id,
                "b": bucket,
                "k": key,
                "s": len(pdf_bytes),
                "sha256": pdf_sha256,
            },
        ).fetchone()
        if not document_row:
            raise RuntimeError("Authorization document was not registered")

        document = {
            "id": str(document_row[0]),
            "sha256": pdf_sha256,
            "mime": "application/pdf",
            "size_bytes": len(pdf_bytes),
            "custody": DOCUMENT_CUSTODY,
        }

        issuance = build_authority_document_issue_attestation(
            case_id=case_id,
            authority_payload=authority_payload,
            document_id=document["id"],
            document_sha256=document["sha256"],
            size_bytes=document["size_bytes"],
            document_version=version,
            document_nonce=str(uuid.uuid4()),
            issued_at=payload["authorized_at"],
        )

        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:id, 'authorization_pdf_issued', CAST(:payload AS JSONB), NOW())
                """
            ),
            {
                "id": case_id,
                "payload": json.dumps(issuance, ensure_ascii=False),
            },
        )
    except Exception:
        if uploaded_coordinates is None:
            _cleanup_authorization_uploads(owned_coordinates)
        raise

    return {
        "ok": True,
        "existing": False,
        "document": document,
        "issuance": issuance,
    }
