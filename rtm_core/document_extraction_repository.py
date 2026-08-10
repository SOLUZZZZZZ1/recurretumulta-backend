"""Persistencia de extracciones documentales RTM no tráfico.

La extracción almacenada es una evidencia intermedia inmutable. No es una
versión de hechos y no puede alimentar Family CORE hasta que OPS promueva
expresamente su paquete a un borrador ``ValidatedFacts``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from rtm_core.authority_repository import (
    canonical_model_json,
    model_digest,
)
from rtm_core.document_extraction import (
    ServiceDocumentExtractionResult,
    SourceDocument,
)
from rtm_core.document_normalization import DocumentExtractionPacket
from rtm_core.document_scope import is_extractable_document_kind
from rtm_core.service_catalog import canonical_department


DOCUMENT_EXTRACTION_STORE_VERSION = "rtm_document_extraction_store_v1_0"

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

_SELECT_EXTRACTION = """
SELECT id, case_id, sequence, service, status, extractor_version,
       provider_version, model, packet, packet_sha256, diagnostics,
       created_by, created_at, invalidated_by, invalidated_at,
       invalidation_reason
FROM rtm_document_extractions
"""


class DocumentExtractionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    case_id: str
    sequence: int
    service: str
    status: str
    extractor_version: str
    provider_version: str
    model: str
    packet: DocumentExtractionPacket
    packet_sha256: str
    diagnostics: dict[str, Any]
    created_by: str
    created_at: datetime
    invalidated_by: Optional[str] = None
    invalidated_at: Optional[datetime] = None
    invalidation_reason: Optional[str] = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"{label} almacenado no es JSON válido.",
            ) from exc
        if isinstance(parsed, dict):
            return parsed
    raise HTTPException(status_code=500, detail=f"{label} almacenado no válido.")


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        text("SELECT to_regclass(:table_name)"),
        {"table_name": f"public.{table_name}"},
    ).fetchone()
    return bool(row and row[0])


def document_extraction_schema_available(conn) -> bool:
    return _table_exists(conn, "rtm_document_extractions")


def _row_to_record(row: Any) -> DocumentExtractionRecord:
    if not row:
        raise HTTPException(
            status_code=404,
            detail="Extracción documental no encontrada.",
        )
    mapping: Mapping[str, Any] = row._mapping if hasattr(row, "_mapping") else row
    try:
        packet = DocumentExtractionPacket.model_validate(
            _json_object(mapping["packet"], "Paquete documental")
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Paquete documental almacenado no válido: {exc}",
        ) from exc

    digest = str(mapping["packet_sha256"] or "")
    if not digest or digest != model_digest(packet):
        raise HTTPException(
            status_code=409,
            detail="La huella de la extracción documental no coincide.",
        )
    if str(mapping["case_id"]) != packet.case_id:
        raise HTTPException(
            status_code=409,
            detail="case_id inconsistente en la extracción documental.",
        )
    if str(mapping["service"]) != packet.service:
        raise HTTPException(
            status_code=409,
            detail="Servicio inconsistente en la extracción documental.",
        )
    if str(mapping["extractor_version"]) != packet.extractor_version:
        raise HTTPException(
            status_code=409,
            detail="Versión de extractor inconsistente.",
        )

    diagnostics = _json_object(mapping["diagnostics"], "Diagnósticos")
    return DocumentExtractionRecord(
        id=str(mapping["id"]),
        case_id=str(mapping["case_id"]),
        sequence=int(mapping["sequence"]),
        service=str(mapping["service"]),
        status=str(mapping["status"]),
        extractor_version=str(mapping["extractor_version"]),
        provider_version=str(mapping["provider_version"]),
        model=str(mapping["model"]),
        packet=packet,
        packet_sha256=digest,
        diagnostics=diagnostics,
        created_by=str(mapping["created_by"] or ""),
        created_at=mapping["created_at"],
        invalidated_by=mapping.get("invalidated_by"),
        invalidated_at=mapping.get("invalidated_at"),
        invalidation_reason=mapping.get("invalidation_reason"),
    )


def _case_payload(
    conn,
    case_id: str,
    *,
    for_update: bool,
) -> dict[str, Any]:
    suffix = " FOR UPDATE" if for_update else ""
    row = conn.execute(
        text("SELECT to_jsonb(c) FROM cases c WHERE c.id=:case_id" + suffix),
        {"case_id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado.")
    value = row[0]
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    raise HTTPException(
        status_code=500,
        detail="No puede leerse el expediente para preparar la extracción.",
    )


def _case_service(payload: Mapping[str, Any]) -> str:
    return canonical_department(
        str(payload.get("department") or ""),
        str(payload.get("case_type") or ""),
        str(payload.get("category") or ""),
    )


def _require_case_ready(
    conn,
    case_id: str,
    *,
    for_update: bool,
    require_no_active_extraction: bool,
) -> tuple[dict[str, Any], str]:
    payload = _case_payload(conn, case_id, for_update=for_update)
    if str(payload.get("payment_status") or "") != "paid":
        raise HTTPException(
            status_code=402,
            detail="El estudio debe estar pagado antes de extraer documentos.",
        )
    if not bool(payload.get("authorized")):
        raise HTTPException(
            status_code=409,
            detail="Falta autorización del cliente.",
        )
    if str(payload.get("status") or "") in _TERMINAL_CASE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="El expediente está en un estado final.",
        )

    service = _case_service(payload)
    if service == "traffic":
        raise HTTPException(
            status_code=409,
            detail="Tráfico debe utilizar Reanalysis y su adaptador específico.",
        )
    if not document_extraction_schema_available(conn):
        raise HTTPException(
            status_code=409,
            detail=(
                "La migración del extractor documental RTM todavía no está "
                "aplicada."
            ),
        )
    if not _table_exists(conn, "rtm_validated_facts"):
        raise HTTPException(
            status_code=409,
            detail="La migración de autoridad RTM CORE no está aplicada.",
        )

    active_facts = conn.execute(
        text(
            """
            SELECT id, frozen
            FROM rtm_validated_facts
            WHERE case_id=:case_id AND invalidated_at IS NULL
            LIMIT 1
            """
            + (" FOR UPDATE" if for_update else "")
        ),
        {"case_id": case_id},
    ).fetchone()
    if active_facts:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Ya existe una versión activa de hechos. Debe invalidarse "
                    "expresamente antes de repetir la extracción."
                ),
                "validated_facts_id": str(active_facts[0]),
                "frozen": bool(active_facts[1]),
            },
        )

    if require_no_active_extraction:
        active_extraction = conn.execute(
            text(
                """
                SELECT id
                FROM rtm_document_extractions
                WHERE case_id=:case_id AND invalidated_at IS NULL
                LIMIT 1
                """
                + (" FOR UPDATE" if for_update else "")
            ),
            {"case_id": case_id},
        ).fetchone()
        if active_extraction:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "Ya existe una extracción documental activa. Debe "
                        "promoverse o invalidarse antes de repetirla."
                    ),
                    "extraction_id": str(active_extraction[0]),
                },
            )
    return payload, service


def load_source_documents(
    conn,
    *,
    case_id: str,
    requested_document_ids: Optional[list[str]] = None,
) -> list[SourceDocument]:
    rows = conn.execute(
        text(
            """
            SELECT CAST(id AS TEXT), CAST(case_id AS TEXT), COALESCE(kind,''),
                   COALESCE(mime,''), COALESCE(b2_bucket,''),
                   COALESCE(b2_key,''), COALESCE(size_bytes,0),
                   NULLIF(COALESCE(sha256,''),'')
            FROM documents
            WHERE case_id=:case_id
            ORDER BY created_at ASC, id ASC
            """
        ),
        {"case_id": case_id},
    ).fetchall()

    requested = {
        str(document_id).strip()
        for document_id in (requested_document_ids or [])
        if str(document_id).strip()
    }
    found_ids = {str(row[0]) for row in rows}
    missing_requested = sorted(requested - found_ids)
    if missing_requested:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Se han solicitado documentos ajenos o inexistentes.",
                "document_ids": missing_requested,
            },
        )

    result: list[SourceDocument] = []
    rejected_requested: list[str] = []
    for row in rows:
        document_id = str(row[0])
        if requested and document_id not in requested:
            continue
        kind = str(row[2] or "")
        bucket = str(row[4] or "")
        key = str(row[5] or "")
        if not is_extractable_document_kind(kind) or not bucket or not key:
            if document_id in requested:
                rejected_requested.append(document_id)
            continue
        result.append(
            SourceDocument(
                id=document_id,
                case_id=str(row[1]),
                kind=kind,
                mime=str(row[3] or ""),
                b2_bucket=bucket,
                b2_key=key,
                size_bytes=int(row[6] or 0),
                sha256=str(row[7]) if row[7] else None,
            )
        )

    if rejected_requested:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Uno o más documentos seleccionados no pueden alimentar "
                    "hechos del expediente."
                ),
                "document_ids": sorted(rejected_requested),
            },
        )
    if not result:
        raise HTTPException(
            status_code=409,
            detail="No hay documentos originales aptos para extracción.",
        )
    return result


def prepare_document_extraction(
    conn,
    *,
    case_id: str,
    requested_document_ids: Optional[list[str]] = None,
) -> tuple[str, list[SourceDocument]]:
    _, service = _require_case_ready(
        conn,
        case_id,
        for_update=True,
        require_no_active_extraction=True,
    )
    documents = load_source_documents(
        conn,
        case_id=case_id,
        requested_document_ids=requested_document_ids,
    )
    return service, documents


def get_document_extraction(
    conn,
    case_id: str,
    extraction_id: str,
    *,
    for_update: bool = False,
) -> DocumentExtractionRecord:
    if not document_extraction_schema_available(conn):
        raise HTTPException(
            status_code=409,
            detail="La migración del extractor documental no está aplicada.",
        )
    suffix = " FOR UPDATE" if for_update else ""
    row = conn.execute(
        text(
            _SELECT_EXTRACTION
            + " WHERE id=:extraction_id AND case_id=:case_id"
            + suffix
        ),
        {"extraction_id": extraction_id, "case_id": case_id},
    ).fetchone()
    return _row_to_record(row)


def list_document_extractions(
    conn,
    case_id: str,
) -> list[DocumentExtractionRecord]:
    if not document_extraction_schema_available(conn):
        return []
    rows = conn.execute(
        text(
            _SELECT_EXTRACTION
            + " WHERE case_id=:case_id ORDER BY sequence DESC"
        ),
        {"case_id": case_id},
    ).fetchall()
    return [_row_to_record(row) for row in rows]


def latest_document_extraction(
    conn,
    case_id: str,
    *,
    active_only: bool = False,
    for_update: bool = False,
) -> Optional[DocumentExtractionRecord]:
    if not document_extraction_schema_available(conn):
        return None
    clauses = ["case_id=:case_id"]
    if active_only:
        clauses.append("invalidated_at IS NULL")
    suffix = " FOR UPDATE" if for_update else ""
    row = conn.execute(
        text(
            _SELECT_EXTRACTION
            + " WHERE "
            + " AND ".join(clauses)
            + " ORDER BY sequence DESC LIMIT 1"
            + suffix
        ),
        {"case_id": case_id},
    ).fetchone()
    return _row_to_record(row) if row else None


def _append_event(
    conn,
    *,
    case_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
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


def persist_document_extraction(
    conn,
    *,
    case_id: str,
    result: ServiceDocumentExtractionResult,
    created_by: str,
) -> DocumentExtractionRecord:
    _, persisted_service = _require_case_ready(
        conn,
        case_id,
        for_update=True,
        require_no_active_extraction=True,
    )
    packet = result.packet
    if packet.case_id != case_id:
        raise HTTPException(
            status_code=409,
            detail="El paquete extraído corresponde a otro expediente.",
        )
    if packet.service != persisted_service or result.service != persisted_service:
        raise HTTPException(
            status_code=409,
            detail="El servicio extraído no coincide con el expediente.",
        )

    available = {
        document.id
        for document in load_source_documents(conn, case_id=case_id)
    }
    foreign = sorted(set(packet.source_document_ids) - available)
    if foreign:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "La extracción contiene documentos no aptos.",
                "document_ids": foreign,
            },
        )

    sequence = int(
        conn.execute(
            text(
                """
                SELECT COALESCE(MAX(sequence),0)+1
                FROM rtm_document_extractions
                WHERE case_id=:case_id
                """
            ),
            {"case_id": case_id},
        ).scalar_one()
        or 1
    )
    packet_json = canonical_model_json(packet)
    packet_hash = model_digest(packet)
    diagnostics = {
        "store_version": DOCUMENT_EXTRACTION_STORE_VERSION,
        "result_version": result.version,
        "catalog_version": result.catalog_version,
        "packet_version": result.packet_version,
        "provider_version": result.provider_version,
        "model": result.model,
        "diagnostics": [
            item.model_dump(mode="json")
            for item in result.diagnostics
        ],
        "warnings": list(result.warnings),
    }
    row = conn.execute(
        text(
            """
            INSERT INTO rtm_document_extractions(
                case_id, sequence, service, status, extractor_version,
                provider_version, model, packet, packet_sha256, diagnostics,
                created_by, created_at
            ) VALUES (
                :case_id, :sequence, :service, 'completed',
                :extractor_version, :provider_version, :model,
                CAST(:packet AS JSONB), :packet_sha256,
                CAST(:diagnostics AS JSONB), :created_by, NOW()
            )
            RETURNING id
            """
        ),
        {
            "case_id": case_id,
            "sequence": sequence,
            "service": packet.service,
            "extractor_version": packet.extractor_version,
            "provider_version": result.provider_version,
            "model": result.model,
            "packet": packet_json,
            "packet_sha256": packet_hash,
            "diagnostics": json.dumps(diagnostics, ensure_ascii=False),
            "created_by": created_by,
        },
    ).fetchone()
    extraction_id = str(row[0])

    conn.execute(
        text(
            """
            UPDATE cases
            SET status='document_extraction_ready', updated_at=NOW()
            WHERE id=:case_id
            """
        ),
        {"case_id": case_id},
    )
    _append_event(
        conn,
        case_id=case_id,
        event_type="rtm_document_extraction_completed",
        payload={
            "extraction_id": extraction_id,
            "sequence": sequence,
            "service": packet.service,
            "source_document_ids": list(packet.source_document_ids),
            "observation_count": len(packet.observations),
            "declared_unresolved_count": len(packet.declared_unresolved),
            "quality_flag_count": len(packet.quality_flags),
            "extractor_version": packet.extractor_version,
            "provider_version": result.provider_version,
            "model": result.model,
            "packet_sha256": packet_hash,
            "store_version": DOCUMENT_EXTRACTION_STORE_VERSION,
            "created_by": created_by,
        },
    )
    return get_document_extraction(conn, case_id, extraction_id)


def invalidate_document_extraction(
    conn,
    *,
    case_id: str,
    extraction_id: str,
    operator: str,
    reason: str,
) -> DocumentExtractionRecord:
    record = get_document_extraction(
        conn,
        case_id,
        extraction_id,
        for_update=True,
    )
    if record.invalidated_at is not None:
        return record
    clean_reason = str(reason or "").strip()
    if len(clean_reason) < 3:
        raise HTTPException(
            status_code=400,
            detail="Debe indicarse un motivo de invalidación.",
        )

    linked = conn.execute(
        text(
            """
            SELECT id
            FROM rtm_validated_facts
            WHERE source_extraction_id=:extraction_id
              AND invalidated_at IS NULL
            LIMIT 1
            FOR UPDATE
            """
        ),
        {"extraction_id": extraction_id},
    ).fetchone()
    if linked:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "La extracción alimenta hechos activos. Deben invalidarse "
                    "primero los hechos para mantener la trazabilidad."
                ),
                "validated_facts_id": str(linked[0]),
            },
        )

    now = utcnow()
    conn.execute(
        text(
            """
            UPDATE rtm_document_extractions
            SET status='invalidated', invalidated_by=:operator,
                invalidated_at=:invalidated_at,
                invalidation_reason=:reason
            WHERE id=:extraction_id
            """
        ),
        {
            "extraction_id": extraction_id,
            "operator": operator,
            "invalidated_at": now,
            "reason": clean_reason,
        },
    )
    conn.execute(
        text(
            """
            UPDATE cases
            SET status='core_review_pending', updated_at=NOW()
            WHERE id=:case_id
            """
        ),
        {"case_id": case_id},
    )
    _append_event(
        conn,
        case_id=case_id,
        event_type="rtm_document_extraction_invalidated",
        payload={
            "extraction_id": extraction_id,
            "operator": operator,
            "reason": clean_reason,
            "invalidated_at": now.isoformat(),
        },
    )
    return get_document_extraction(conn, case_id, extraction_id)
