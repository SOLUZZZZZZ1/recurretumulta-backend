"""Persistencia y ciclo de vida autoritativo de la Previa Jurídica RTM.

La previa queda enlazada por claves foráneas a una versión concreta de hechos
congelados y a una resolución de familia bloqueada. Ninguna transición permite
reclasificar ni sustituir esos antecedentes.
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
    get_validated_facts,
    latest_family_resolution,
    model_digest,
    validated_model_copy,
)
from rtm_core.contracts import LegalPreview, MissingItemSeverity, PreviewStatus
from rtm_core.service_catalog import canonical_department


LEGAL_PREVIEW_STORE_VERSION = "rtm_legal_preview_store_v1_1"
_FORBIDDEN_FAMILIES = {
    "",
    "generic",
    "unknown",
    "desconocido",
    "unresolved",
    "pendiente",
    "otro",
}
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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical_preview_json(preview: LegalPreview) -> str:
    return canonical_model_json(preview)


def preview_digest(preview: LegalPreview) -> str:
    return model_digest(preview)


def validated_preview_copy(preview: LegalPreview, **updates: Any) -> LegalPreview:
    return validated_model_copy(preview, **updates)


class LegalPreviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    case_id: str
    validated_facts_id: str
    family_resolution_id: str
    sequence: int
    status: PreviewStatus
    preview: LegalPreview
    payload_sha256: str
    created_by: str
    created_at: datetime
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    frozen_by: Optional[str] = None
    frozen_at: Optional[datetime] = None
    invalidated_by: Optional[str] = None
    invalidated_at: Optional[datetime] = None
    invalidation_reason: Optional[str] = None
    supersedes_id: Optional[str] = None
    state_reason: Optional[str] = None


def _json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Payload de previa inválido: {exc}")
        if isinstance(parsed, dict):
            return parsed
    raise HTTPException(status_code=500, detail="Payload de previa inválido")


def _row_to_record(row: Any) -> LegalPreviewRecord:
    if row is None:
        raise HTTPException(status_code=404, detail="Previa Jurídica no encontrada")
    mapping: Mapping[str, Any] = row._mapping if hasattr(row, "_mapping") else row
    try:
        preview = LegalPreview.model_validate(_json_payload(mapping["payload"]))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Previa Jurídica almacenada no válida: {exc}",
        )

    stored_hash = str(mapping["payload_sha256"] or "")
    if not stored_hash or stored_hash != preview_digest(preview):
        raise HTTPException(
            status_code=409,
            detail="La integridad de la Previa Jurídica no coincide con su huella",
        )

    row_status = PreviewStatus(str(mapping["status"]))
    if preview.status is not row_status:
        raise HTTPException(status_code=409, detail="Estado inconsistente de la Previa Jurídica")
    if str(mapping["case_id"]) != preview.case_id:
        raise HTTPException(status_code=409, detail="case_id inconsistente en la Previa Jurídica")
    if str(mapping["family"]) != preview.family:
        raise HTTPException(status_code=409, detail="Familia inconsistente en la Previa Jurídica")
    if str(mapping["specialist"]) != preview.specialist:
        raise HTTPException(status_code=409, detail="Especialista inconsistente en la Previa Jurídica")
    if str(mapping["facts_version"]) != preview.facts_version:
        raise HTTPException(status_code=409, detail="Versión de hechos inconsistente en la previa")
    if str(mapping["family_resolution_version"]) != preview.family_resolution_version:
        raise HTTPException(status_code=409, detail="Versión de familia inconsistente en la previa")
    if not mapping.get("validated_facts_id") or not mapping.get("family_resolution_id"):
        raise HTTPException(status_code=409, detail="La previa no conserva sus enlaces de autoridad")

    return LegalPreviewRecord(
        id=str(mapping["id"]),
        case_id=str(mapping["case_id"]),
        validated_facts_id=str(mapping["validated_facts_id"]),
        family_resolution_id=str(mapping["family_resolution_id"]),
        sequence=int(mapping["sequence"]),
        status=row_status,
        preview=preview,
        payload_sha256=stored_hash,
        created_by=str(mapping["created_by"] or ""),
        created_at=mapping["created_at"],
        approved_by=mapping.get("approved_by"),
        approved_at=mapping.get("approved_at"),
        frozen_by=mapping.get("frozen_by"),
        frozen_at=mapping.get("frozen_at"),
        invalidated_by=mapping.get("invalidated_by"),
        invalidated_at=mapping.get("invalidated_at"),
        invalidation_reason=mapping.get("invalidation_reason"),
        supersedes_id=(
            str(mapping["supersedes_id"]) if mapping.get("supersedes_id") else None
        ),
        state_reason=mapping.get("state_reason"),
    )


_SELECT_PREVIEW = """
SELECT id, case_id, validated_facts_id, family_resolution_id, sequence,
       status, service, family, specialist, facts_version,
       family_resolution_version, payload, payload_sha256, created_by,
       created_at, approved_by, approved_at, frozen_by, frozen_at,
       invalidated_by, invalidated_at, invalidation_reason, supersedes_id,
       state_reason
FROM rtm_legal_previews
"""


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


def _case_for_preview(conn, case_id: str) -> Mapping[str, Any]:
    row = conn.execute(
        text(
            """
            SELECT id, COALESCE(payment_status, '') AS payment_status,
                   COALESCE(authorized, FALSE) AS authorized,
                   COALESCE(status, '') AS status,
                   COALESCE(department, '') AS department,
                   COALESCE(case_type, '') AS case_type,
                   COALESCE(category, '') AS category
            FROM cases WHERE id=:case_id
            FOR UPDATE
            """
        ),
        {"case_id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    mapping = row._mapping
    if str(mapping["payment_status"]) != "paid":
        raise HTTPException(
            status_code=402,
            detail="El estudio debe estar pagado antes de crear la Previa Jurídica",
        )
    if not bool(mapping["authorized"]):
        raise HTTPException(status_code=409, detail="Falta autorización del cliente")
    if str(mapping["status"]) in _TERMINAL_CASE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="El expediente está en un estado final y no admite una nueva previa",
        )
    return mapping


def _active_authority_chain(conn, case_id: str, *, for_update: bool = False):
    family_record = latest_family_resolution(
        conn,
        case_id,
        active_only=True,
        locked_only=True,
        for_update=for_update,
    )
    if not family_record:
        raise HTTPException(
            status_code=409,
            detail="No existe una resolución de familia activa y bloqueada",
        )
    if family_record.invalidated_at is not None or not family_record.locked:
        raise HTTPException(status_code=409, detail="La resolución de familia no está activa")

    facts_record = get_validated_facts(
        conn,
        case_id,
        family_record.validated_facts_id,
        for_update=for_update,
    )
    if facts_record.invalidated_at is not None or not facts_record.frozen:
        raise HTTPException(
            status_code=409,
            detail="Los hechos enlazados ya no están activos y congelados",
        )
    return facts_record, family_record


def _validate_preview_against_authority(
    preview: LegalPreview,
    facts_record,
    family_record,
) -> None:
    resolution = family_record.resolution
    if preview.case_id != facts_record.case_id or preview.case_id != family_record.case_id:
        raise HTTPException(status_code=409, detail="La previa apunta a otro expediente")
    if canonical_department(preview.service) != canonical_department(facts_record.facts.service):
        raise HTTPException(status_code=409, detail="La previa y los hechos usan servicios distintos")
    if canonical_department(preview.service) != canonical_department(resolution.service):
        raise HTTPException(status_code=409, detail="La previa y la familia usan servicios distintos")
    if preview.facts_version != facts_record.facts.version:
        raise HTTPException(status_code=409, detail="La previa no usa la versión activa de hechos")
    if preview.family_resolution_version != resolution.version:
        raise HTTPException(status_code=409, detail="La previa no usa la resolución activa de familia")
    if preview.family != resolution.family:
        raise HTTPException(status_code=409, detail="La previa contradice la familia bloqueada")
    if preview.specialist != resolution.specialist:
        raise HTTPException(status_code=409, detail="La previa contradice el especialista resuelto")
    if preview.family.strip().lower() in _FORBIDDEN_FAMILIES:
        raise HTTPException(status_code=409, detail="La familia jurídica todavía no está resuelta")


def get_preview(
    conn,
    case_id: str,
    preview_id: str,
    *,
    for_update: bool = False,
) -> LegalPreviewRecord:
    suffix = " FOR UPDATE" if for_update else ""
    row = conn.execute(
        text(_SELECT_PREVIEW + " WHERE id=:preview_id AND case_id=:case_id" + suffix),
        {"preview_id": preview_id, "case_id": case_id},
    ).fetchone()
    return _row_to_record(row)


def list_previews(conn, case_id: str) -> list[LegalPreviewRecord]:
    rows = conn.execute(
        text(_SELECT_PREVIEW + " WHERE case_id=:case_id ORDER BY sequence DESC"),
        {"case_id": case_id},
    ).fetchall()
    return [_row_to_record(row) for row in rows]


def latest_preview(conn, case_id: str) -> Optional[LegalPreviewRecord]:
    row = conn.execute(
        text(_SELECT_PREVIEW + " WHERE case_id=:case_id ORDER BY sequence DESC LIMIT 1"),
        {"case_id": case_id},
    ).fetchone()
    return _row_to_record(row) if row else None


def create_preview(
    conn,
    *,
    case_id: str,
    preview: LegalPreview,
    created_by: str,
    supersedes_id: Optional[str] = None,
) -> LegalPreviewRecord:
    case_meta = _case_for_preview(conn, case_id)
    if preview.case_id != case_id:
        raise HTTPException(status_code=409, detail="El case_id de la previa no coincide con la ruta")
    if preview.status is not PreviewStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Una nueva Previa Jurídica debe nacer en draft")

    facts_record, family_record = _active_authority_chain(
        conn,
        case_id,
        for_update=True,
    )
    _validate_preview_against_authority(preview, facts_record, family_record)

    case_service = canonical_department(
        str(case_meta["department"] or ""),
        str(case_meta["case_type"] or ""),
        str(case_meta["category"] or ""),
    )
    preview_service = canonical_department(preview.service)
    if case_service != "other" and preview_service != case_service:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "El servicio de la previa no coincide con el expediente",
                "case_service": case_service,
                "preview_service": preview_service,
            },
        )

    active = conn.execute(
        text(
            """
            SELECT id, status FROM rtm_legal_previews
            WHERE case_id=:case_id
              AND status IN ('draft','ops_review','approved','frozen')
            LIMIT 1
            FOR UPDATE
            """
        ),
        {"case_id": case_id},
    ).fetchone()
    if active:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Ya existe una Previa Jurídica activa",
                "preview_id": str(active[0]),
                "status": str(active[1]),
            },
        )

    previous_latest = latest_preview(conn, case_id)
    if (
        not supersedes_id
        and previous_latest
        and previous_latest.status
        in (PreviewStatus.CHANGES_REQUIRED, PreviewStatus.INVALIDATED)
    ):
        supersedes_id = previous_latest.id

    if supersedes_id:
        previous = get_preview(conn, case_id, supersedes_id, for_update=True)
        if previous.status not in (
            PreviewStatus.CHANGES_REQUIRED,
            PreviewStatus.INVALIDATED,
        ):
            raise HTTPException(
                status_code=409,
                detail="La versión sustituida debe estar cerrada o invalidada",
            )

    sequence_row = conn.execute(
        text(
            "SELECT COALESCE(MAX(sequence), 0) + 1 "
            "FROM rtm_legal_previews WHERE case_id=:case_id"
        ),
        {"case_id": case_id},
    ).fetchone()
    sequence = int(sequence_row[0] or 1)
    payload = canonical_preview_json(preview)
    digest = preview_digest(preview)
    creator = (created_by or preview.created_by_component or "rtm-core").strip()

    row = conn.execute(
        text(
            """
            INSERT INTO rtm_legal_previews(
                case_id, validated_facts_id, family_resolution_id, sequence,
                status, service, family, specialist, facts_version,
                family_resolution_version, payload, payload_sha256, created_by,
                supersedes_id, created_at, updated_at
            ) VALUES (
                :case_id, :validated_facts_id, :family_resolution_id, :sequence,
                'draft', :service, :family, :specialist, :facts_version,
                :family_resolution_version, CAST(:payload AS JSONB),
                :payload_sha256, :created_by, :supersedes_id, NOW(), NOW()
            )
            RETURNING id
            """
        ),
        {
            "case_id": case_id,
            "validated_facts_id": facts_record.id,
            "family_resolution_id": family_record.id,
            "sequence": sequence,
            "service": preview.service,
            "family": preview.family,
            "specialist": preview.specialist,
            "facts_version": preview.facts_version,
            "family_resolution_version": preview.family_resolution_version,
            "payload": payload,
            "payload_sha256": digest,
            "created_by": creator,
            "supersedes_id": supersedes_id,
        },
    ).fetchone()
    preview_id = str(row[0])
    conn.execute(
        text("UPDATE cases SET status='preview_draft', updated_at=NOW() WHERE id=:case_id"),
        {"case_id": case_id},
    )
    _append_event(
        conn,
        case_id,
        "rtm_legal_preview_created",
        {
            "preview_id": preview_id,
            "validated_facts_id": facts_record.id,
            "family_resolution_id": family_record.id,
            "sequence": sequence,
            "family": preview.family,
            "specialist": preview.specialist,
            "facts_version": preview.facts_version,
            "family_resolution_version": preview.family_resolution_version,
            "payload_sha256": digest,
            "supersedes_id": supersedes_id,
            "store_version": LEGAL_PREVIEW_STORE_VERSION,
        },
    )
    return get_preview(conn, case_id, preview_id)


def _write_transition(
    conn,
    record: LegalPreviewRecord,
    preview: LegalPreview,
    *,
    operator: str,
    state_reason: Optional[str] = None,
) -> LegalPreviewRecord:
    payload = canonical_preview_json(preview)
    digest = preview_digest(preview)
    fields: dict[str, Any] = {
        "preview_id": record.id,
        "status": preview.status.value,
        "payload": payload,
        "payload_sha256": digest,
        "state_reason": state_reason,
        "approved_by": preview.approved_by,
        "approved_at": preview.approved_at,
        "frozen_by": operator if preview.status is PreviewStatus.FROZEN else record.frozen_by,
        "frozen_at": preview.frozen_at,
        "invalidated_by": (
            operator if preview.status is PreviewStatus.INVALIDATED else record.invalidated_by
        ),
        "invalidated_at": preview.invalidated_at,
        "invalidation_reason": preview.invalidation_reason,
    }
    conn.execute(
        text(
            """
            UPDATE rtm_legal_previews
            SET status=:status, payload=CAST(:payload AS JSONB),
                payload_sha256=:payload_sha256, state_reason=:state_reason,
                approved_by=:approved_by, approved_at=:approved_at,
                frozen_by=:frozen_by, frozen_at=:frozen_at,
                invalidated_by=:invalidated_by,
                invalidated_at=:invalidated_at,
                invalidation_reason=:invalidation_reason,
                updated_at=NOW()
            WHERE id=:preview_id
            """
        ),
        fields,
    )
    return get_preview(conn, record.case_id, record.id)


def submit_for_review(
    conn,
    case_id: str,
    preview_id: str,
    operator: str,
) -> LegalPreviewRecord:
    record = get_preview(conn, case_id, preview_id, for_update=True)
    if record.status is not PreviewStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Solo una previa draft puede revisarse")
    preview = validated_preview_copy(record.preview, status=PreviewStatus.OPS_REVIEW)
    updated = _write_transition(conn, record, preview, operator=operator)
    conn.execute(
        text("UPDATE cases SET status='preview_ops_review', updated_at=NOW() WHERE id=:case_id"),
        {"case_id": case_id},
    )
    _append_event(
        conn,
        case_id,
        "rtm_legal_preview_submitted_for_review",
        {"preview_id": preview_id, "operator": operator},
    )
    return updated


def request_changes(
    conn,
    case_id: str,
    preview_id: str,
    operator: str,
    reason: str,
) -> LegalPreviewRecord:
    record = get_preview(conn, case_id, preview_id, for_update=True)
    if record.status not in (PreviewStatus.OPS_REVIEW, PreviewStatus.APPROVED):
        raise HTTPException(status_code=409, detail="La previa no está en una fase revisable")
    clean_reason = (reason or "").strip()
    if len(clean_reason) < 3:
        raise HTTPException(status_code=400, detail="Debe indicarse el motivo de los cambios")
    preview = validated_preview_copy(
        record.preview,
        status=PreviewStatus.CHANGES_REQUIRED,
        approved_by=None,
        approved_at=None,
        frozen_at=None,
    )
    updated = _write_transition(
        conn,
        record,
        preview,
        operator=operator,
        state_reason=clean_reason,
    )
    conn.execute(
        text(
            "UPDATE cases SET status='preview_changes_required', "
            "updated_at=NOW() WHERE id=:case_id"
        ),
        {"case_id": case_id},
    )
    _append_event(
        conn,
        case_id,
        "rtm_legal_preview_changes_required",
        {"preview_id": preview_id, "operator": operator, "reason": clean_reason},
    )
    return updated


def approve_preview(
    conn,
    case_id: str,
    preview_id: str,
    operator: str,
) -> LegalPreviewRecord:
    record = get_preview(conn, case_id, preview_id, for_update=True)
    if record.status is not PreviewStatus.OPS_REVIEW:
        raise HTTPException(status_code=409, detail="Solo una previa en ops_review puede aprobarse")

    facts_record, family_record = _active_authority_chain(
        conn,
        case_id,
        for_update=True,
    )
    if facts_record.id != record.validated_facts_id or family_record.id != record.family_resolution_id:
        raise HTTPException(
            status_code=409,
            detail="La autoridad del expediente cambió; la previa debe invalidarse",
        )
    _validate_preview_against_authority(record.preview, facts_record, family_record)

    now = utcnow()
    preview = validated_preview_copy(
        record.preview,
        status=PreviewStatus.APPROVED,
        approved_by=operator,
        approved_at=now,
    )
    updated = _write_transition(conn, record, preview, operator=operator)
    conn.execute(
        text("UPDATE cases SET status='preview_approved', updated_at=NOW() WHERE id=:case_id"),
        {"case_id": case_id},
    )
    _append_event(
        conn,
        case_id,
        "rtm_legal_preview_approved",
        {
            "preview_id": preview_id,
            "operator": operator,
            "approved_at": now.isoformat(),
        },
    )
    return updated


def freeze_preview(
    conn,
    case_id: str,
    preview_id: str,
    operator: str,
) -> LegalPreviewRecord:
    record = get_preview(conn, case_id, preview_id, for_update=True)
    if record.status is not PreviewStatus.APPROVED:
        raise HTTPException(status_code=409, detail="Solo una previa aprobada puede congelarse")

    facts_record, family_record = _active_authority_chain(
        conn,
        case_id,
        for_update=True,
    )
    if facts_record.id != record.validated_facts_id or family_record.id != record.family_resolution_id:
        raise HTTPException(
            status_code=409,
            detail="La previa ya no coincide con la autoridad activa",
        )
    _validate_preview_against_authority(record.preview, facts_record, family_record)

    blocking = [
        item.code
        for item in record.preview.missing_items
        if item.severity is MissingItemSeverity.BLOCKING
    ]
    if blocking:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "La previa contiene elementos bloqueantes",
                "blocking_items": blocking,
            },
        )

    now = utcnow()
    frozen = validated_preview_copy(
        record.preview,
        status=PreviewStatus.FROZEN,
        frozen_at=now,
    )
    updated = _write_transition(conn, record, frozen, operator=operator)
    conn.execute(
        text("UPDATE cases SET status='preview_frozen', updated_at=NOW() WHERE id=:case_id"),
        {"case_id": case_id},
    )
    _append_event(
        conn,
        case_id,
        "rtm_legal_preview_frozen",
        {
            "preview_id": preview_id,
            "validated_facts_id": facts_record.id,
            "family_resolution_id": family_record.id,
            "operator": operator,
            "frozen_at": now.isoformat(),
            "payload_sha256": updated.payload_sha256,
            "family": updated.preview.family,
        },
    )
    return updated


def invalidate_preview(
    conn,
    case_id: str,
    preview_id: str,
    operator: str,
    reason: str,
) -> LegalPreviewRecord:
    record = get_preview(conn, case_id, preview_id, for_update=True)
    if record.status is PreviewStatus.INVALIDATED:
        return record
    clean_reason = (reason or "").strip()
    if len(clean_reason) < 3:
        raise HTTPException(status_code=400, detail="Debe indicarse el motivo de invalidación")

    now = utcnow()
    invalidated = validated_preview_copy(
        record.preview,
        status=PreviewStatus.INVALIDATED,
        invalidated_at=now,
        invalidation_reason=clean_reason,
    )
    updated = _write_transition(
        conn,
        record,
        invalidated,
        operator=operator,
        state_reason=clean_reason,
    )
    conn.execute(
        text(
            """
            UPDATE rtm_generated_resources
            SET status='invalidated', invalidated_at=NOW(),
                invalidation_reason=:reason
            WHERE legal_preview_id=:preview_id AND status <> 'invalidated'
            """
        ),
        {"preview_id": preview_id, "reason": clean_reason},
    )
    case_row = conn.execute(
        text("SELECT COALESCE(status,'') FROM cases WHERE id=:case_id"),
        {"case_id": case_id},
    ).fetchone()
    if case_row and str(case_row[0]) not in _TERMINAL_CASE_STATUSES:
        conn.execute(
            text("UPDATE cases SET status='manual_review', updated_at=NOW() WHERE id=:case_id"),
            {"case_id": case_id},
        )
    _append_event(
        conn,
        case_id,
        "rtm_legal_preview_invalidated",
        {
            "preview_id": preview_id,
            "operator": operator,
            "reason": clean_reason,
            "invalidated_at": now.isoformat(),
        },
    )
    return updated
