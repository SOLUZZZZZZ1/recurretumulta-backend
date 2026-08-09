"""Generate autoritativo de RTM.

Solo transforma una Previa Jurídica congelada en documentos. No extrae, no
clasifica y no altera hechos, familia, especialista ni estrategia.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Mapping, Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from b2_storage import upload_bytes
from docx_builder import build_docx
from pdf_builder import build_pdf
from rtm_core.authority_repository import get_family_resolution, get_validated_facts
from rtm_core.contracts import FactStatus, PreviewStatus
from rtm_core.preview_repository import get_preview


GENERATION_GATEWAY_VERSION = "rtm_generate_gateway_v1_0"
_TERMINAL_CASE_STATUSES = {
    "submitted",
    "closed",
    "archived",
    "resolved",
    "estimado",
    "desestimado",
    "presentado_manual_ayuntamiento",
    "presentado_auto_dgt",
    "presentado_auto_registro",
}


class GeneratedResourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    case_id: str
    legal_preview_id: str
    sequence: int
    status: str
    family: str
    generator_version: str
    preview_payload_sha256: str
    content_sha256: str
    docx_document_id: str
    pdf_document_id: str
    generated_by: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None


_SELECT_RESOURCE = """
SELECT id, case_id, legal_preview_id, sequence, status, family,
       generator_version, preview_payload_sha256, content_sha256,
       docx_document_id, pdf_document_id, generated_by, created_at,
       updated_at, approved_by, approved_at
FROM rtm_generated_resources
"""


def _resource_record(row: Any) -> GeneratedResourceRecord:
    if not row:
        raise HTTPException(status_code=404, detail="Recurso generado no encontrado")
    mapping: Mapping[str, Any] = row._mapping if hasattr(row, "_mapping") else row
    if not mapping.get("docx_document_id") or not mapping.get("pdf_document_id"):
        raise HTTPException(status_code=409, detail="El recurso no conserva ambos documentos")
    return GeneratedResourceRecord(
        id=str(mapping["id"]),
        case_id=str(mapping["case_id"]),
        legal_preview_id=str(mapping["legal_preview_id"]),
        sequence=int(mapping["sequence"]),
        status=str(mapping["status"]),
        family=str(mapping["family"]),
        generator_version=str(mapping["generator_version"]),
        preview_payload_sha256=str(mapping["preview_payload_sha256"]),
        content_sha256=str(mapping["content_sha256"]),
        docx_document_id=str(mapping["docx_document_id"]),
        pdf_document_id=str(mapping["pdf_document_id"]),
        generated_by=str(mapping["generated_by"]),
        created_at=mapping["created_at"],
        updated_at=mapping.get("updated_at"),
        approved_by=mapping.get("approved_by"),
        approved_at=mapping.get("approved_at"),
    )


def get_generated_resource(
    conn,
    case_id: str,
    resource_id: str,
    *,
    for_update: bool = False,
) -> GeneratedResourceRecord:
    suffix = " FOR UPDATE" if for_update else ""
    row = conn.execute(
        text(_SELECT_RESOURCE + " WHERE id=:resource_id AND case_id=:case_id" + suffix),
        {"resource_id": resource_id, "case_id": case_id},
    ).fetchone()
    return _resource_record(row)


def list_generated_resources(conn, case_id: str) -> list[GeneratedResourceRecord]:
    rows = conn.execute(
        text(_SELECT_RESOURCE + " WHERE case_id=:case_id ORDER BY sequence DESC"),
        {"case_id": case_id},
    ).fetchall()
    return [_resource_record(row) for row in rows]


def _case_meta(conn, case_id: str, *, for_update: bool = False) -> Mapping[str, Any]:
    suffix = " FOR UPDATE" if for_update else ""
    row = conn.execute(
        text(
            """
            SELECT c.id, COALESCE(c.payment_status, '') AS payment_status,
                   COALESCE(c.authorized, FALSE) AS authorized,
                   COALESCE(c.status, '') AS status,
                   COALESCE(c.interested_data, '{}'::jsonb) AS interested_data,
                   COALESCE(c.expediente_ref, '') AS expediente_ref,
                   COALESCE(c.organismo, '') AS organismo,
                   COALESCE(c.contact_email, '') AS contact_email,
                   COALESCE(NULLIF(to_jsonb(c)->>'test_mode', '')::boolean, FALSE)
                       AS test_mode
            FROM cases c WHERE c.id=:case_id
            """
            + suffix
        ),
        {"case_id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    mapping = row._mapping
    if str(mapping["payment_status"]) != "paid":
        raise HTTPException(status_code=402, detail="Pago requerido")
    if not bool(mapping["authorized"]):
        raise HTTPException(status_code=409, detail="Falta autorización")
    if bool(mapping["test_mode"]):
        raise HTTPException(status_code=409, detail="Generate CORE no admite test_mode")
    if str(mapping["status"]) in _TERMINAL_CASE_STATUSES:
        raise HTTPException(status_code=409, detail="El expediente está en un estado final")
    return mapping


def _authority_chain(conn, case_id: str, preview_id: str, *, for_update: bool = False):
    preview_record = get_preview(conn, case_id, preview_id, for_update=for_update)
    if preview_record.status is not PreviewStatus.FROZEN:
        raise HTTPException(status_code=409, detail="La Previa Jurídica no está congelada")
    if preview_record.invalidated_at is not None:
        raise HTTPException(status_code=409, detail="La Previa Jurídica está invalidada")

    facts_record = get_validated_facts(
        conn,
        case_id,
        preview_record.validated_facts_id,
        for_update=for_update,
    )
    family_record = get_family_resolution(
        conn,
        case_id,
        preview_record.family_resolution_id,
        for_update=for_update,
    )
    if facts_record.invalidated_at is not None or not facts_record.frozen:
        raise HTTPException(status_code=409, detail="Los hechos ya no están activos y congelados")
    if family_record.invalidated_at is not None or not family_record.locked:
        raise HTTPException(status_code=409, detail="La familia ya no está activa y bloqueada")
    if family_record.validated_facts_id != facts_record.id:
        raise HTTPException(status_code=409, detail="La cadena de autoridad está rota")

    preview = preview_record.preview
    resolution = family_record.resolution
    if preview.facts_version != facts_record.facts.version:
        raise HTTPException(status_code=409, detail="La previa usa otra versión de hechos")
    if preview.family_resolution_version != resolution.version:
        raise HTTPException(status_code=409, detail="La previa usa otra versión de familia")
    if preview.family != resolution.family or preview.specialist != resolution.specialist:
        raise HTTPException(status_code=409, detail="La previa contradice la familia bloqueada")

    blocked = [
        item.code
        for item in preview.missing_items
        if item.severity.value == "blocking"
    ]
    if blocked:
        raise HTTPException(
            status_code=409,
            detail={"message": "La previa contiene bloqueos", "items": blocked},
        )
    if not (preview.destination or "").strip():
        raise HTTPException(status_code=409, detail="Falta el destinatario del escrito")
    if not preview.legal_arguments:
        raise HTTPException(status_code=409, detail="Faltan argumentos jurídicos estructurados")

    fact_keys = set(facts_record.facts.facts)
    for argument in preview.legal_arguments:
        unknown = sorted(set(argument.source_fact_keys) - fact_keys)
        if unknown:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"El argumento '{argument.title}' usa hechos inexistentes",
                    "fact_keys": unknown,
                },
            )
        nonvalidated = sorted(
            key
            for key in argument.source_fact_keys
            if facts_record.facts.facts[key].status is not FactStatus.VALIDATED
        )
        if nonvalidated:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"El argumento '{argument.title}' usa hechos no validados",
                    "fact_keys": nonvalidated,
                },
            )
    return preview_record, facts_record, family_record


def _value(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def _clean_text(value: str) -> str:
    value = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def render_legal_preview(preview, case_meta: Mapping[str, Any]) -> str:
    """Render determinista: usa exclusivamente previa + identidad persistida."""

    interested = case_meta.get("interested_data")
    if not isinstance(interested, dict):
        interested = {}
    full_name = _value(interested, "full_name", "name")
    dni = _value(interested, "dni_nie", "dni", "identity_number")
    address = _value(interested, "domicilio_notif", "domicilio", "address")
    if not full_name or not dni or not address:
        raise HTTPException(status_code=409, detail="Faltan datos de identidad para redactar")

    destination = _clean_text(preview.destination or "").upper()
    document_type = _clean_text(preview.document_type or "ESCRITO").upper()
    subject = _clean_text(
        preview.subject
        or f"{document_type} — {case_meta.get('expediente_ref') or preview.family}"
    )

    lines: list[str] = [
        destination,
        "",
        document_type,
        "",
        f"ASUNTO: {subject}",
        "",
        (
            f"D./Dña. {full_name}, con DNI/NIE {dni}, y domicilio a efectos de "
            f"notificaciones en {address}, comparece y, como mejor proceda, EXPONE:"
        ),
        "",
        "I. ANTECEDENTES Y HECHOS",
        "",
    ]
    if preview.problem_summary:
        lines.extend([_clean_text(preview.problem_summary), ""])
    for index, fact in enumerate(preview.validated_facts_summary, start=1):
        lines.append(f"{index}) {_clean_text(fact)}")
    lines.extend(["", "II. ALEGACIONES Y FUNDAMENTOS", ""])
    lines.extend([
        "ENFOQUE PRINCIPAL",
        _clean_text(preview.primary_strategy or ""),
        "",
    ])

    for index, argument in enumerate(preview.legal_arguments, start=1):
        lines.append(f"ALEGACIÓN {index} — {_clean_text(argument.title).upper()}")
        lines.append("")
        lines.append(_clean_text(argument.body))
        if argument.legal_basis:
            lines.append("")
            lines.append("Fundamentos normativos indicados en la previa:")
            for basis in argument.legal_basis:
                lines.append(f"• {_clean_text(basis)}")
        lines.append("")

    if preview.secondary_strategies:
        lines.extend(["ENFOQUES SUBSIDIARIOS", ""])
        for index, strategy in enumerate(preview.secondary_strategies, start=1):
            lines.append(f"{index}) {_clean_text(strategy)}")
        lines.append("")

    lines.extend(["III. SOLICITA", ""])
    for index, outcome in enumerate(preview.requested_outcomes, start=1):
        lines.append(f"{index}) {_clean_text(outcome)}")

    if preview.additional_requests:
        lines.extend(["", "OTROSÍ DIGO", ""])
        for index, request in enumerate(preview.additional_requests, start=1):
            lines.append(f"{index}) {_clean_text(request)}")

    lines.extend(["", "Por ser conforme a Derecho, se solicita."])
    return _clean_text("\n".join(lines)) + "\n"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _insert_document(conn, case_id: str, kind: str, bucket: str, key: str, mime: str, size: int) -> str:
    row = conn.execute(
        text(
            """
            INSERT INTO documents(case_id, kind, b2_bucket, b2_key, mime,
                                  size_bytes, created_at)
            VALUES (:case_id, :kind, :bucket, :key, :mime, :size, NOW())
            RETURNING id
            """
        ),
        {
            "case_id": case_id,
            "kind": kind,
            "bucket": bucket,
            "key": key,
            "mime": mime,
            "size": size,
        },
    ).fetchone()
    return str(row[0])


def _append_event(conn, case_id: str, event_type: str, payload: dict[str, Any]) -> None:
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


def generate_from_frozen_preview(
    conn,
    *,
    case_id: str,
    preview_id: str,
    generated_by: str,
) -> GeneratedResourceRecord:
    case = _case_meta(conn, case_id, for_update=True)
    preview_record, facts_record, family_record = _authority_chain(
        conn,
        case_id,
        preview_id,
        for_update=True,
    )

    existing = conn.execute(
        text(
            _SELECT_RESOURCE
            + " WHERE legal_preview_id=:preview_id AND status <> 'invalidated' "
              "ORDER BY sequence DESC LIMIT 1 FOR UPDATE"
        ),
        {"preview_id": preview_id},
    ).fetchone()
    if existing:
        return _resource_record(existing)

    content = render_legal_preview(preview_record.preview, case)
    content_bytes = content.encode("utf-8")
    content_hash = _sha256(content_bytes)
    docx_bytes = build_docx("", content)
    pdf_bytes = build_pdf("", content)

    docx_bucket, docx_key = upload_bytes(
        case_id,
        "rtm_generated",
        docx_bytes,
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    pdf_bucket, pdf_key = upload_bytes(
        case_id,
        "rtm_generated",
        pdf_bytes,
        ".pdf",
        "application/pdf",
    )
    docx_id = _insert_document(
        conn,
        case_id,
        "rtm_generated_docx",
        docx_bucket,
        docx_key,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        len(docx_bytes),
    )
    pdf_id = _insert_document(
        conn,
        case_id,
        "rtm_generated_pdf",
        pdf_bucket,
        pdf_key,
        "application/pdf",
        len(pdf_bytes),
    )

    sequence_row = conn.execute(
        text(
            "SELECT COALESCE(MAX(sequence), 0) + 1 "
            "FROM rtm_generated_resources WHERE case_id=:case_id"
        ),
        {"case_id": case_id},
    ).fetchone()
    sequence = int(sequence_row[0] or 1)
    row = conn.execute(
        text(
            """
            INSERT INTO rtm_generated_resources(
                case_id, legal_preview_id, sequence, status, family,
                generator_version, preview_payload_sha256, content_sha256,
                docx_document_id, pdf_document_id, generated_by,
                created_at, updated_at
            ) VALUES (
                :case_id, :preview_id, :sequence, 'final_ready', :family,
                :generator_version, :preview_hash, :content_hash,
                :docx_id, :pdf_id, :generated_by, NOW(), NOW()
            )
            RETURNING id
            """
        ),
        {
            "case_id": case_id,
            "preview_id": preview_id,
            "sequence": sequence,
            "family": preview_record.preview.family,
            "generator_version": GENERATION_GATEWAY_VERSION,
            "preview_hash": preview_record.payload_sha256,
            "content_hash": content_hash,
            "docx_id": docx_id,
            "pdf_id": pdf_id,
            "generated_by": generated_by,
        },
    ).fetchone()
    resource_id = str(row[0])
    conn.execute(
        text("UPDATE cases SET status='final_ready', updated_at=NOW() WHERE id=:case_id"),
        {"case_id": case_id},
    )
    _append_event(
        conn,
        case_id,
        "rtm_resource_generated_from_frozen_preview",
        {
            "resource_id": resource_id,
            "preview_id": preview_id,
            "validated_facts_id": facts_record.id,
            "family_resolution_id": family_record.id,
            "generator_version": GENERATION_GATEWAY_VERSION,
            "preview_payload_sha256": preview_record.payload_sha256,
            "content_sha256": content_hash,
            "docx_document_id": docx_id,
            "pdf_document_id": pdf_id,
        },
    )
    return get_generated_resource(conn, case_id, resource_id)


def approve_resource_for_submission(
    conn,
    *,
    case_id: str,
    resource_id: str,
    approved_by: str,
) -> GeneratedResourceRecord:
    case = _case_meta(conn, case_id, for_update=True)
    resource = get_generated_resource(conn, case_id, resource_id, for_update=True)
    if resource.status != "final_ready":
        raise HTTPException(status_code=409, detail="El recurso no está listo para aprobar")

    preview_record, _, _ = _authority_chain(
        conn,
        case_id,
        resource.legal_preview_id,
        for_update=True,
    )
    if resource.preview_payload_sha256 != preview_record.payload_sha256:
        raise HTTPException(status_code=409, detail="El recurso no corresponde a la previa vigente")

    pdf = conn.execute(
        text(
            """
            SELECT id, mime FROM documents
            WHERE id=:document_id AND case_id=:case_id
            """
        ),
        {"document_id": resource.pdf_document_id, "case_id": case_id},
    ).fetchone()
    if not pdf or str(pdf[1] or "") != "application/pdf":
        raise HTTPException(status_code=409, detail="El PDF final no está disponible")

    if not resource.approved_at:
        conn.execute(
            text(
                """
                UPDATE rtm_generated_resources
                SET approved_by=:approved_by, approved_at=NOW(), updated_at=NOW()
                WHERE id=:resource_id
                """
            ),
            {"approved_by": approved_by, "resource_id": resource_id},
        )
    if str(case["status"]) != "ready_to_submit":
        conn.execute(
            text("UPDATE cases SET status='ready_to_submit', updated_at=NOW() WHERE id=:case_id"),
            {"case_id": case_id},
        )
        _append_event(
            conn,
            case_id,
            "rtm_resource_approved_for_submission",
            {
                "resource_id": resource_id,
                "preview_id": resource.legal_preview_id,
                "approved_by": approved_by,
                "pdf_document_id": resource.pdf_document_id,
            },
        )
    return get_generated_resource(conn, case_id, resource_id)
