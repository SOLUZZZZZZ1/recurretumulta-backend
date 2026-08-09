"""Persistencia autoritativa de hechos validados y familia jurídica RTM.

Este módulo no extrae, no clasifica y no redacta. Únicamente conserva versiones
inmutables, valida sus enlaces y controla las transiciones que permiten avanzar
hacia el especialista y la Previa Jurídica.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from rtm_core.contracts import (
    FactStatus,
    FamilyResolution,
    LegalPreview,
    PreviewStatus,
    ResolutionStatus,
    ValidatedFacts,
)
from rtm_core.service_catalog import canonical_department


AUTHORITY_STORE_VERSION = "rtm_authority_store_v1_0"
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

ModelT = TypeVar("ModelT", bound=BaseModel)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical_model_json(model: BaseModel) -> str:
    return json.dumps(
        model.model_dump(mode="json", exclude_none=False),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def model_digest(model: BaseModel) -> str:
    return hashlib.sha256(canonical_model_json(model).encode("utf-8")).hexdigest()


def validated_model_copy(model: ModelT, **updates: Any) -> ModelT:
    payload = model.model_dump(mode="python", exclude_none=False)
    payload.update(updates)
    return type(model).model_validate(payload)


class ValidatedFactsRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    case_id: str
    sequence: int
    facts: ValidatedFacts
    payload_sha256: str
    frozen: bool
    created_by: str
    created_at: datetime
    updated_at: datetime
    frozen_by: Optional[str] = None
    frozen_at: Optional[datetime] = None
    invalidated_by: Optional[str] = None
    invalidated_at: Optional[datetime] = None
    invalidation_reason: Optional[str] = None
    supersedes_id: Optional[str] = None


class FamilyResolutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    case_id: str
    validated_facts_id: str
    sequence: int
    resolution: FamilyResolution
    payload_sha256: str
    locked: bool
    created_by: str
    created_at: datetime
    updated_at: datetime
    locked_by: Optional[str] = None
    locked_at: Optional[datetime] = None
    invalidated_by: Optional[str] = None
    invalidated_at: Optional[datetime] = None
    invalidation_reason: Optional[str] = None
    supersedes_id: Optional[str] = None


def _json_payload(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"{label} inválido: {exc}")
        if isinstance(parsed, dict):
            return parsed
    raise HTTPException(status_code=500, detail=f"{label} inválido")


_SELECT_FACTS = """
SELECT id, case_id, sequence, version, service, extractor_version,
       payload, payload_sha256, frozen, created_by, created_at, updated_at,
       frozen_by, frozen_at, invalidated_by, invalidated_at,
       invalidation_reason, supersedes_id
FROM rtm_validated_facts
"""

_SELECT_FAMILY = """
SELECT id, case_id, validated_facts_id, sequence, version, service, status,
       family, specialist, confidence, payload, payload_sha256, locked,
       created_by, created_at, updated_at, locked_by, locked_at,
       invalidated_by, invalidated_at, invalidation_reason, supersedes_id
FROM rtm_family_resolutions
"""


def _facts_row_to_record(row: Any) -> ValidatedFactsRecord:
    if row is None:
        raise HTTPException(status_code=404, detail="Versión de hechos no encontrada")
    mapping: Mapping[str, Any] = row._mapping if hasattr(row, "_mapping") else row
    try:
        facts = ValidatedFacts.model_validate(
            _json_payload(mapping["payload"], "Payload de hechos")
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Versión de hechos almacenada no válida: {exc}",
        )

    stored_hash = str(mapping["payload_sha256"] or "")
    if not stored_hash or stored_hash != model_digest(facts):
        raise HTTPException(
            status_code=409,
            detail="La integridad de los hechos no coincide con su huella",
        )
    if str(mapping["case_id"]) != facts.case_id:
        raise HTTPException(status_code=409, detail="case_id inconsistente en hechos")
    if str(mapping["version"]) != facts.version:
        raise HTTPException(status_code=409, detail="Versión inconsistente en hechos")
    if str(mapping["service"]) != facts.service:
        raise HTTPException(status_code=409, detail="Servicio inconsistente en hechos")
    if str(mapping["extractor_version"]) != facts.extractor_version:
        raise HTTPException(status_code=409, detail="Extractor inconsistente en hechos")
    if bool(mapping["frozen"]) != bool(facts.frozen):
        raise HTTPException(status_code=409, detail="Estado frozen inconsistente en hechos")

    return ValidatedFactsRecord(
        id=str(mapping["id"]),
        case_id=str(mapping["case_id"]),
        sequence=int(mapping["sequence"]),
        facts=facts,
        payload_sha256=stored_hash,
        frozen=bool(mapping["frozen"]),
        created_by=str(mapping["created_by"] or ""),
        created_at=mapping["created_at"],
        updated_at=mapping["updated_at"],
        frozen_by=mapping.get("frozen_by"),
        frozen_at=mapping.get("frozen_at"),
        invalidated_by=mapping.get("invalidated_by"),
        invalidated_at=mapping.get("invalidated_at"),
        invalidation_reason=mapping.get("invalidation_reason"),
        supersedes_id=(
            str(mapping["supersedes_id"]) if mapping.get("supersedes_id") else None
        ),
    )


def _family_row_to_record(row: Any) -> FamilyResolutionRecord:
    if row is None:
        raise HTTPException(status_code=404, detail="Resolución de familia no encontrada")
    mapping: Mapping[str, Any] = row._mapping if hasattr(row, "_mapping") else row
    try:
        resolution = FamilyResolution.model_validate(
            _json_payload(mapping["payload"], "Payload de familia")
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Resolución de familia almacenada no válida: {exc}",
        )

    stored_hash = str(mapping["payload_sha256"] or "")
    if not stored_hash or stored_hash != model_digest(resolution):
        raise HTTPException(
            status_code=409,
            detail="La integridad de la familia no coincide con su huella",
        )
    if str(mapping["case_id"]) != resolution.case_id:
        raise HTTPException(status_code=409, detail="case_id inconsistente en familia")
    if str(mapping["version"]) != resolution.version:
        raise HTTPException(status_code=409, detail="Versión inconsistente en familia")
    if str(mapping["service"]) != resolution.service:
        raise HTTPException(status_code=409, detail="Servicio inconsistente en familia")
    if str(mapping["status"]) != resolution.status.value:
        raise HTTPException(status_code=409, detail="Estado inconsistente en familia")
    if (mapping["family"] or None) != resolution.family:
        raise HTTPException(status_code=409, detail="Familia inconsistente")
    if (mapping["specialist"] or None) != resolution.specialist:
        raise HTTPException(status_code=409, detail="Especialista inconsistente")
    if bool(mapping["locked"]) != bool(resolution.locked):
        raise HTTPException(status_code=409, detail="Estado locked inconsistente")

    return FamilyResolutionRecord(
        id=str(mapping["id"]),
        case_id=str(mapping["case_id"]),
        validated_facts_id=str(mapping["validated_facts_id"]),
        sequence=int(mapping["sequence"]),
        resolution=resolution,
        payload_sha256=stored_hash,
        locked=bool(mapping["locked"]),
        created_by=str(mapping["created_by"] or ""),
        created_at=mapping["created_at"],
        updated_at=mapping["updated_at"],
        locked_by=mapping.get("locked_by"),
        locked_at=mapping.get("locked_at"),
        invalidated_by=mapping.get("invalidated_by"),
        invalidated_at=mapping.get("invalidated_at"),
        invalidation_reason=mapping.get("invalidation_reason"),
        supersedes_id=(
            str(mapping["supersedes_id"]) if mapping.get("supersedes_id") else None
        ),
    )


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


def _case_authority_meta(conn, case_id: str, *, for_update: bool = False) -> Mapping[str, Any]:
    suffix = " FOR UPDATE" if for_update else ""
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
            """
            + suffix
        ),
        {"case_id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    return row._mapping


def _require_authority_work_allowed(meta: Mapping[str, Any]) -> None:
    if str(meta["payment_status"]) != "paid":
        raise HTTPException(
            status_code=402,
            detail="El estudio debe estar pagado antes de validar hechos",
        )
    if not bool(meta["authorized"]):
        raise HTTPException(status_code=409, detail="Falta autorización del cliente")
    if str(meta["status"]) in _TERMINAL_CASE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="El expediente está en un estado final",
        )


def _case_service(meta: Mapping[str, Any]) -> str:
    return canonical_department(
        str(meta["department"] or ""),
        str(meta["case_type"] or ""),
        str(meta["category"] or ""),
    )


def _validate_service(meta: Mapping[str, Any], service: str) -> None:
    persisted = _case_service(meta)
    supplied = canonical_department(service)
    if persisted != "other" and supplied != persisted:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "El servicio no coincide con el expediente",
                "case_service": persisted,
                "supplied_service": supplied,
            },
        )


def get_validated_facts(
    conn,
    case_id: str,
    facts_id: str,
    *,
    for_update: bool = False,
) -> ValidatedFactsRecord:
    suffix = " FOR UPDATE" if for_update else ""
    row = conn.execute(
        text(_SELECT_FACTS + " WHERE id=:facts_id AND case_id=:case_id" + suffix),
        {"facts_id": facts_id, "case_id": case_id},
    ).fetchone()
    return _facts_row_to_record(row)


def list_validated_facts(conn, case_id: str) -> list[ValidatedFactsRecord]:
    rows = conn.execute(
        text(_SELECT_FACTS + " WHERE case_id=:case_id ORDER BY sequence DESC"),
        {"case_id": case_id},
    ).fetchall()
    return [_facts_row_to_record(row) for row in rows]


def latest_validated_facts(
    conn,
    case_id: str,
    *,
    active_only: bool = False,
    frozen_only: bool = False,
    for_update: bool = False,
) -> Optional[ValidatedFactsRecord]:
    clauses = ["case_id=:case_id"]
    if active_only:
        clauses.append("invalidated_at IS NULL")
    if frozen_only:
        clauses.append("frozen=TRUE")
    suffix = " FOR UPDATE" if for_update else ""
    row = conn.execute(
        text(
            _SELECT_FACTS
            + " WHERE "
            + " AND ".join(clauses)
            + " ORDER BY sequence DESC LIMIT 1"
            + suffix
        ),
        {"case_id": case_id},
    ).fetchone()
    return _facts_row_to_record(row) if row else None


def validate_facts_for_freeze(
    facts: ValidatedFacts,
    *,
    available_document_ids: Optional[set[str]] = None,
) -> None:
    if facts.frozen:
        return
    if not facts.facts:
        raise HTTPException(
            status_code=409,
            detail="No puede congelarse una versión sin campos de hechos",
        )
    declared_documents = {str(value) for value in facts.source_document_ids if str(value)}
    if not declared_documents:
        raise HTTPException(
            status_code=409,
            detail="La versión de hechos no conserva documentos de procedencia",
        )
    if len(declared_documents) != len(facts.source_document_ids):
        raise HTTPException(
            status_code=409,
            detail="La procedencia contiene documentos duplicados",
        )

    source_documents: set[str] = set()
    for fact_key, fact in facts.facts.items():
        if fact.status is FactStatus.VALIDATED and not fact.sources:
            raise HTTPException(
                status_code=409,
                detail=f"El hecho validado '{fact_key}' no conserva fuente",
            )
        for source in fact.sources:
            source_documents.add(source.document_id)
            if source.document_id not in declared_documents:
                raise HTTPException(
                    status_code=409,
                    detail=f"La fuente de '{fact_key}' no figura en source_document_ids",
                )

    if available_document_ids is not None:
        missing = sorted(declared_documents - available_document_ids)
        if missing:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Hay documentos de procedencia ajenos al expediente",
                    "document_ids": missing,
                },
            )
    if not source_documents and any(
        fact.status is FactStatus.VALIDATED for fact in facts.facts.values()
    ):
        raise HTTPException(
            status_code=409,
            detail="Los hechos validados no conservan fuentes documentales",
        )


def _available_document_ids(conn, case_id: str) -> set[str]:
    rows = conn.execute(
        text("SELECT id FROM documents WHERE case_id=:case_id"),
        {"case_id": case_id},
    ).fetchall()
    return {str(row[0]) for row in rows}


def create_validated_facts(
    conn,
    *,
    case_id: str,
    facts: ValidatedFacts,
    created_by: str,
    supersedes_id: Optional[str] = None,
) -> ValidatedFactsRecord:
    meta = _case_authority_meta(conn, case_id, for_update=True)
    _require_authority_work_allowed(meta)
    if facts.case_id != case_id:
        raise HTTPException(status_code=409, detail="case_id inconsistente en hechos")
    if facts.frozen:
        raise HTTPException(
            status_code=409,
            detail="Una nueva versión de hechos debe nacer sin congelar",
        )
    _validate_service(meta, facts.service)

    active = latest_validated_facts(
        conn,
        case_id,
        active_only=True,
        for_update=True,
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Ya existe una versión activa de hechos",
                "facts_id": active.id,
                "frozen": active.frozen,
            },
        )

    if not supersedes_id:
        previous_latest = latest_validated_facts(conn, case_id)
        if previous_latest and previous_latest.invalidated_at is not None:
            supersedes_id = previous_latest.id

    if supersedes_id:
        previous = get_validated_facts(conn, case_id, supersedes_id, for_update=True)
        if previous.invalidated_at is None:
            raise HTTPException(
                status_code=409,
                detail="La versión sustituida debe estar invalidada",
            )

    sequence_row = conn.execute(
        text(
            "SELECT COALESCE(MAX(sequence), 0) + 1 "
            "FROM rtm_validated_facts WHERE case_id=:case_id"
        ),
        {"case_id": case_id},
    ).fetchone()
    sequence = int(sequence_row[0] or 1)
    payload = canonical_model_json(facts)
    digest = model_digest(facts)

    row = conn.execute(
        text(
            """
            INSERT INTO rtm_validated_facts(
                case_id, sequence, version, service, extractor_version,
                payload, payload_sha256, frozen, created_by, supersedes_id,
                created_at, updated_at
            ) VALUES (
                :case_id, :sequence, :version, :service, :extractor_version,
                CAST(:payload AS JSONB), :payload_sha256, FALSE, :created_by,
                :supersedes_id, NOW(), NOW()
            )
            RETURNING id
            """
        ),
        {
            "case_id": case_id,
            "sequence": sequence,
            "version": facts.version,
            "service": facts.service,
            "extractor_version": facts.extractor_version,
            "payload": payload,
            "payload_sha256": digest,
            "created_by": created_by,
            "supersedes_id": supersedes_id,
        },
    ).fetchone()
    facts_id = str(row[0])
    conn.execute(
        text("UPDATE cases SET status='facts_validation', updated_at=NOW() WHERE id=:case_id"),
        {"case_id": case_id},
    )
    _append_event(
        conn,
        case_id,
        "rtm_validated_facts_created",
        {
            "facts_id": facts_id,
            "sequence": sequence,
            "version": facts.version,
            "extractor_version": facts.extractor_version,
            "payload_sha256": digest,
            "supersedes_id": supersedes_id,
            "store_version": AUTHORITY_STORE_VERSION,
        },
    )
    return get_validated_facts(conn, case_id, facts_id)


def freeze_validated_facts(
    conn,
    case_id: str,
    facts_id: str,
    operator: str,
) -> ValidatedFactsRecord:
    record = get_validated_facts(conn, case_id, facts_id, for_update=True)
    if record.invalidated_at is not None:
        raise HTTPException(status_code=409, detail="La versión de hechos está invalidada")
    if record.frozen:
        return record

    validate_facts_for_freeze(
        record.facts,
        available_document_ids=_available_document_ids(conn, case_id),
    )
    now = utcnow()
    frozen_facts = validated_model_copy(record.facts, frozen=True)
    payload = canonical_model_json(frozen_facts)
    digest = model_digest(frozen_facts)
    conn.execute(
        text(
            """
            UPDATE rtm_validated_facts
            SET frozen=TRUE, payload=CAST(:payload AS JSONB),
                payload_sha256=:payload_sha256, frozen_by=:frozen_by,
                frozen_at=:frozen_at, updated_at=NOW()
            WHERE id=:facts_id
            """
        ),
        {
            "facts_id": facts_id,
            "payload": payload,
            "payload_sha256": digest,
            "frozen_by": operator,
            "frozen_at": now,
        },
    )
    conn.execute(
        text("UPDATE cases SET status='facts_frozen', updated_at=NOW() WHERE id=:case_id"),
        {"case_id": case_id},
    )
    _append_event(
        conn,
        case_id,
        "rtm_validated_facts_frozen",
        {
            "facts_id": facts_id,
            "operator": operator,
            "frozen_at": now.isoformat(),
            "payload_sha256": digest,
        },
    )
    return get_validated_facts(conn, case_id, facts_id)


def get_family_resolution(
    conn,
    case_id: str,
    resolution_id: str,
    *,
    for_update: bool = False,
) -> FamilyResolutionRecord:
    suffix = " FOR UPDATE" if for_update else ""
    row = conn.execute(
        text(
            _SELECT_FAMILY
            + " WHERE id=:resolution_id AND case_id=:case_id"
            + suffix
        ),
        {"resolution_id": resolution_id, "case_id": case_id},
    ).fetchone()
    return _family_row_to_record(row)


def list_family_resolutions(conn, case_id: str) -> list[FamilyResolutionRecord]:
    rows = conn.execute(
        text(_SELECT_FAMILY + " WHERE case_id=:case_id ORDER BY sequence DESC"),
        {"case_id": case_id},
    ).fetchall()
    return [_family_row_to_record(row) for row in rows]


def latest_family_resolution(
    conn,
    case_id: str,
    *,
    active_only: bool = False,
    locked_only: bool = False,
    for_update: bool = False,
) -> Optional[FamilyResolutionRecord]:
    clauses = ["case_id=:case_id"]
    if active_only:
        clauses.append("invalidated_at IS NULL")
    if locked_only:
        clauses.append("locked=TRUE")
    suffix = " FOR UPDATE" if for_update else ""
    row = conn.execute(
        text(
            _SELECT_FAMILY
            + " WHERE "
            + " AND ".join(clauses)
            + " ORDER BY sequence DESC LIMIT 1"
            + suffix
        ),
        {"case_id": case_id},
    ).fetchone()
    return _family_row_to_record(row) if row else None


def validate_resolution_against_facts(
    resolution: FamilyResolution,
    facts: ValidatedFacts,
) -> None:
    if resolution.case_id != facts.case_id:
        raise HTTPException(status_code=409, detail="La familia apunta a otro expediente")
    if canonical_department(resolution.service) != canonical_department(facts.service):
        raise HTTPException(status_code=409, detail="La familia y los hechos usan servicios distintos")
    if resolution.facts_version != facts.version:
        raise HTTPException(
            status_code=409,
            detail="La familia no referencia la versión activa de hechos",
        )
    if resolution.locked:
        raise HTTPException(
            status_code=409,
            detail="Una nueva resolución debe nacer sin bloquear",
        )

    fact_keys = set(facts.facts)
    document_ids = set(facts.source_document_ids)
    for evidence in resolution.evidence:
        if not evidence.source_fact_keys:
            raise HTTPException(
                status_code=409,
                detail=f"La evidencia '{evidence.code}' no identifica hechos de origen",
            )
        unknown_keys = sorted(set(evidence.source_fact_keys) - fact_keys)
        if unknown_keys:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"La evidencia '{evidence.code}' usa hechos inexistentes",
                    "fact_keys": unknown_keys,
                },
            )
        nonvalidated = sorted(
            key
            for key in evidence.source_fact_keys
            if facts.facts[key].status is not FactStatus.VALIDATED
        )
        if resolution.status is ResolutionStatus.RESOLVED and nonvalidated:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Una familia resuelta solo puede apoyarse en hechos validados",
                    "fact_keys": nonvalidated,
                },
            )
        unknown_docs = sorted(set(evidence.source_document_ids) - document_ids)
        if unknown_docs:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"La evidencia '{evidence.code}' usa documentos ajenos",
                    "document_ids": unknown_docs,
                },
            )

    if resolution.status is ResolutionStatus.RESOLVED:
        family = (resolution.family or "").strip().lower()
        if family in _FORBIDDEN_FAMILIES:
            raise HTTPException(status_code=409, detail="La familia no está resuelta")
        if not resolution.specialist:
            raise HTTPException(
                status_code=409,
                detail="Una familia resuelta debe indicar especialista",
            )
        if resolution.conflicts:
            raise HTTPException(
                status_code=409,
                detail="Una familia resuelta no puede conservar conflictos de clasificación",
            )


def create_family_resolution(
    conn,
    *,
    case_id: str,
    resolution: FamilyResolution,
    created_by: str,
    validated_facts_id: Optional[str] = None,
    supersedes_id: Optional[str] = None,
) -> FamilyResolutionRecord:
    meta = _case_authority_meta(conn, case_id, for_update=True)
    _require_authority_work_allowed(meta)
    _validate_service(meta, resolution.service)

    facts_record = (
        get_validated_facts(conn, case_id, validated_facts_id, for_update=True)
        if validated_facts_id
        else latest_validated_facts(
            conn,
            case_id,
            active_only=True,
            frozen_only=True,
            for_update=True,
        )
    )
    if not facts_record:
        raise HTTPException(
            status_code=409,
            detail="No existe una versión congelada de hechos",
        )
    if facts_record.invalidated_at is not None or not facts_record.frozen:
        raise HTTPException(
            status_code=409,
            detail="La resolución debe partir de hechos activos y congelados",
        )
    validate_resolution_against_facts(resolution, facts_record.facts)

    active = latest_family_resolution(
        conn,
        case_id,
        active_only=True,
        for_update=True,
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Ya existe una resolución de familia activa",
                "resolution_id": active.id,
                "locked": active.locked,
            },
        )

    if not supersedes_id:
        previous_latest = latest_family_resolution(conn, case_id)
        if previous_latest and previous_latest.invalidated_at is not None:
            supersedes_id = previous_latest.id

    if supersedes_id:
        previous = get_family_resolution(conn, case_id, supersedes_id, for_update=True)
        if previous.invalidated_at is None:
            raise HTTPException(
                status_code=409,
                detail="La resolución sustituida debe estar invalidada",
            )

    sequence_row = conn.execute(
        text(
            "SELECT COALESCE(MAX(sequence), 0) + 1 "
            "FROM rtm_family_resolutions WHERE case_id=:case_id"
        ),
        {"case_id": case_id},
    ).fetchone()
    sequence = int(sequence_row[0] or 1)
    payload = canonical_model_json(resolution)
    digest = model_digest(resolution)

    row = conn.execute(
        text(
            """
            INSERT INTO rtm_family_resolutions(
                case_id, validated_facts_id, sequence, version, service, status,
                family, specialist, confidence, payload, payload_sha256, locked,
                created_by, supersedes_id, created_at, updated_at
            ) VALUES (
                :case_id, :validated_facts_id, :sequence, :version, :service,
                :status, :family, :specialist, :confidence,
                CAST(:payload AS JSONB), :payload_sha256, FALSE,
                :created_by, :supersedes_id, NOW(), NOW()
            )
            RETURNING id
            """
        ),
        {
            "case_id": case_id,
            "validated_facts_id": facts_record.id,
            "sequence": sequence,
            "version": resolution.version,
            "service": resolution.service,
            "status": resolution.status.value,
            "family": resolution.family,
            "specialist": resolution.specialist,
            "confidence": resolution.confidence,
            "payload": payload,
            "payload_sha256": digest,
            "created_by": created_by,
            "supersedes_id": supersedes_id,
        },
    ).fetchone()
    resolution_id = str(row[0])
    conn.execute(
        text("UPDATE cases SET status='family_resolution', updated_at=NOW() WHERE id=:case_id"),
        {"case_id": case_id},
    )
    _append_event(
        conn,
        case_id,
        "rtm_family_resolution_created",
        {
            "resolution_id": resolution_id,
            "validated_facts_id": facts_record.id,
            "sequence": sequence,
            "status": resolution.status.value,
            "family": resolution.family,
            "specialist": resolution.specialist,
            "payload_sha256": digest,
            "supersedes_id": supersedes_id,
            "store_version": AUTHORITY_STORE_VERSION,
        },
    )
    return get_family_resolution(conn, case_id, resolution_id)


def lock_family_resolution(
    conn,
    case_id: str,
    resolution_id: str,
    operator: str,
) -> FamilyResolutionRecord:
    record = get_family_resolution(
        conn,
        case_id,
        resolution_id,
        for_update=True,
    )
    if record.invalidated_at is not None:
        raise HTTPException(status_code=409, detail="La resolución está invalidada")
    if record.locked:
        return record

    facts_record = get_validated_facts(
        conn,
        case_id,
        record.validated_facts_id,
        for_update=True,
    )
    if facts_record.invalidated_at is not None or not facts_record.frozen:
        raise HTTPException(
            status_code=409,
            detail="Los hechos de origen ya no están activos y congelados",
        )
    validate_resolution_against_facts(record.resolution, facts_record.facts)
    if record.resolution.status is not ResolutionStatus.RESOLVED:
        raise HTTPException(
            status_code=409,
            detail="Solo una familia resuelta puede bloquearse",
        )
    if record.resolution.confidence <= 0:
        raise HTTPException(
            status_code=409,
            detail="La resolución no conserva confianza válida",
        )

    now = utcnow()
    locked_resolution = validated_model_copy(record.resolution, locked=True)
    payload = canonical_model_json(locked_resolution)
    digest = model_digest(locked_resolution)
    conn.execute(
        text(
            """
            UPDATE rtm_family_resolutions
            SET locked=TRUE, payload=CAST(:payload AS JSONB),
                payload_sha256=:payload_sha256, locked_by=:locked_by,
                locked_at=:locked_at, updated_at=NOW()
            WHERE id=:resolution_id
            """
        ),
        {
            "resolution_id": resolution_id,
            "payload": payload,
            "payload_sha256": digest,
            "locked_by": operator,
            "locked_at": now,
        },
    )
    conn.execute(
        text("UPDATE cases SET status='family_locked', updated_at=NOW() WHERE id=:case_id"),
        {"case_id": case_id},
    )
    _append_event(
        conn,
        case_id,
        "rtm_family_resolution_locked",
        {
            "resolution_id": resolution_id,
            "validated_facts_id": facts_record.id,
            "operator": operator,
            "locked_at": now.isoformat(),
            "family": locked_resolution.family,
            "specialist": locked_resolution.specialist,
            "payload_sha256": digest,
        },
    )
    return get_family_resolution(conn, case_id, resolution_id)


def _invalidate_downstream_previews(
    conn,
    *,
    case_id: str,
    operator: str,
    reason: str,
) -> list[str]:
    rows = conn.execute(
        text(
            """
            SELECT id, payload, payload_sha256
            FROM rtm_legal_previews
            WHERE case_id=:case_id AND status <> 'invalidated'
            FOR UPDATE
            """
        ),
        {"case_id": case_id},
    ).fetchall()
    invalidated_ids: list[str] = []
    now = utcnow()
    for row in rows:
        preview_id = str(row[0])
        preview = LegalPreview.model_validate(
            _json_payload(row[1], "Payload de previa")
        )
        if str(row[2] or "") != model_digest(preview):
            raise HTTPException(
                status_code=409,
                detail="No puede invalidarse una previa cuya integridad no coincide",
            )
        invalidated = validated_model_copy(
            preview,
            status=PreviewStatus.INVALIDATED,
            invalidated_at=now,
            invalidation_reason=reason,
        )
        conn.execute(
            text(
                """
                UPDATE rtm_legal_previews
                SET status='invalidated', payload=CAST(:payload AS JSONB),
                    payload_sha256=:payload_sha256, invalidated_by=:operator,
                    invalidated_at=:invalidated_at,
                    invalidation_reason=:reason, state_reason=:reason,
                    updated_at=NOW()
                WHERE id=:preview_id
                """
            ),
            {
                "preview_id": preview_id,
                "payload": canonical_model_json(invalidated),
                "payload_sha256": model_digest(invalidated),
                "operator": operator,
                "invalidated_at": now,
                "reason": reason,
            },
        )
        invalidated_ids.append(preview_id)

    if invalidated_ids:
        conn.execute(
            text(
                """
                UPDATE rtm_generated_resources
                SET status='invalidated', invalidated_at=NOW(),
                    invalidation_reason=:reason
                WHERE case_id=:case_id AND status <> 'invalidated'
                """
            ),
            {"case_id": case_id, "reason": reason},
        )
    return invalidated_ids


def invalidate_family_resolution(
    conn,
    case_id: str,
    resolution_id: str,
    operator: str,
    reason: str,
) -> FamilyResolutionRecord:
    record = get_family_resolution(
        conn,
        case_id,
        resolution_id,
        for_update=True,
    )
    if record.invalidated_at is not None:
        return record
    clean_reason = (reason or "").strip()
    if len(clean_reason) < 3:
        raise HTTPException(status_code=400, detail="Debe indicarse el motivo")

    now = utcnow()
    conn.execute(
        text(
            """
            UPDATE rtm_family_resolutions
            SET invalidated_by=:operator, invalidated_at=:invalidated_at,
                invalidation_reason=:reason, updated_at=NOW()
            WHERE id=:resolution_id
            """
        ),
        {
            "resolution_id": resolution_id,
            "operator": operator,
            "invalidated_at": now,
            "reason": clean_reason,
        },
    )
    previews = _invalidate_downstream_previews(
        conn,
        case_id=case_id,
        operator=operator,
        reason=f"Familia invalidada: {clean_reason}",
    )
    conn.execute(
        text("UPDATE cases SET status='facts_frozen', updated_at=NOW() WHERE id=:case_id"),
        {"case_id": case_id},
    )
    _append_event(
        conn,
        case_id,
        "rtm_family_resolution_invalidated",
        {
            "resolution_id": resolution_id,
            "operator": operator,
            "reason": clean_reason,
            "invalidated_at": now.isoformat(),
            "downstream_preview_ids": previews,
        },
    )
    return get_family_resolution(conn, case_id, resolution_id)


def invalidate_validated_facts(
    conn,
    case_id: str,
    facts_id: str,
    operator: str,
    reason: str,
) -> ValidatedFactsRecord:
    record = get_validated_facts(conn, case_id, facts_id, for_update=True)
    if record.invalidated_at is not None:
        return record
    clean_reason = (reason or "").strip()
    if len(clean_reason) < 3:
        raise HTTPException(status_code=400, detail="Debe indicarse el motivo")

    active_families = conn.execute(
        text(
            """
            SELECT id FROM rtm_family_resolutions
            WHERE case_id=:case_id AND validated_facts_id=:facts_id
              AND invalidated_at IS NULL
            FOR UPDATE
            """
        ),
        {"case_id": case_id, "facts_id": facts_id},
    ).fetchall()
    family_ids = [str(row[0]) for row in active_families]
    for family_id in family_ids:
        invalidate_family_resolution(
            conn,
            case_id,
            family_id,
            operator,
            f"Hechos invalidados: {clean_reason}",
        )

    now = utcnow()
    conn.execute(
        text(
            """
            UPDATE rtm_validated_facts
            SET invalidated_by=:operator, invalidated_at=:invalidated_at,
                invalidation_reason=:reason, updated_at=NOW()
            WHERE id=:facts_id
            """
        ),
        {
            "facts_id": facts_id,
            "operator": operator,
            "invalidated_at": now,
            "reason": clean_reason,
        },
    )
    conn.execute(
        text("UPDATE cases SET status='manual_review', updated_at=NOW() WHERE id=:case_id"),
        {"case_id": case_id},
    )
    _append_event(
        conn,
        case_id,
        "rtm_validated_facts_invalidated",
        {
            "facts_id": facts_id,
            "operator": operator,
            "reason": clean_reason,
            "invalidated_at": now.isoformat(),
            "downstream_family_ids": family_ids,
        },
    )
    return get_validated_facts(conn, case_id, facts_id)
