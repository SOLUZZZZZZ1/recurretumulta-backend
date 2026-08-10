"""API OPS del extractor documental para satélites RTM no tráfico.

La ejecución produce y persiste un paquete documental intermedio. La promoción a
``ValidatedFacts`` es una acción distinta y explícita; ni la ejecución ni la
vista previa congelan hechos, resuelven familia o habilitan Generate.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from database import get_engine
from rtm_core.authority_repository import create_validated_facts
from rtm_core.document_extraction import (
    SERVICE_DOCUMENT_EXTRACTOR_VERSION,
    extract_service_documents,
)
from rtm_core.document_extraction_repository import (
    DOCUMENT_EXTRACTION_STORE_VERSION,
    get_document_extraction,
    invalidate_document_extraction,
    list_document_extractions,
    persist_document_extraction,
    prepare_document_extraction,
)
from rtm_core.document_normalization import (
    DOCUMENT_NORMALIZATION_VERSION,
    normalize_document_packet,
)
from rtm_core.runtime_capabilities import require_http_capability
from rtm_core.security import normalized_actor, require_operator_token


DOCUMENT_EXTRACTION_ROUTER_VERSION = "rtm_document_extraction_router_v1_0"

router = APIRouter(
    prefix="/ops/core/cases",
    tags=["rtm-core-document-extraction"],
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunDocumentExtractionBody(_StrictModel):
    document_ids: list[str] = Field(default_factory=list, max_length=8)


class ExtractionReasonBody(_StrictModel):
    reason: str = Field(min_length=3, max_length=2000)


def _operator(token: Optional[str], actor: Optional[str]) -> str:
    require_operator_token(token)
    return normalized_actor(actor, fallback="ops:document-extraction")


def _record_payload(record) -> dict[str, Any]:
    return record.model_dump(mode="json")


def _normalization_payload(normalized) -> dict[str, Any]:
    return normalized.model_dump(mode="json")


def _append_promotion_event(
    conn,
    *,
    case_id: str,
    extraction_id: str,
    facts_id: str,
    actor: str,
    normalized,
) -> None:
    payload = {
        "router_version": DOCUMENT_EXTRACTION_ROUTER_VERSION,
        "store_version": DOCUMENT_EXTRACTION_STORE_VERSION,
        "normalization_version": DOCUMENT_NORMALIZATION_VERSION,
        "extraction_id": extraction_id,
        "facts_id": facts_id,
        "actor": actor,
        "accepted_fields": list(normalized.accepted_fields),
        "unresolved_fields": list(normalized.unresolved_fields),
        "conflicted_fields": list(normalized.conflicted_fields),
        "rejected_observation_count": len(normalized.rejected_observations),
        "warning_count": len(normalized.warnings),
    }
    conn.execute(
        text(
            """
            INSERT INTO events(case_id, type, payload, created_at)
            VALUES (
                :case_id,
                'rtm_document_extraction_promoted_to_facts',
                CAST(:payload AS JSONB),
                NOW()
            )
            """
        ),
        {
            "case_id": case_id,
            "payload": json.dumps(payload, ensure_ascii=False),
        },
    )


@router.post("/{case_id}/document-extractions/run")
def run_document_extraction(
    case_id: str,
    body: RunDocumentExtractionBody,
    x_operator_token: Optional[str] = Header(
        default=None,
        alias="X-Operator-Token",
    ),
    x_operator_actor: Optional[str] = Header(
        default=None,
        alias="X-Operator-Actor",
    ),
):
    actor = _operator(x_operator_token, x_operator_actor)
    # La ruta OPS no descarga B2 ni llama al proveedor hasta que el entorno ha
    # habilitado de forma expresa la capacidad documental.
    require_http_capability("document_provider")
    engine = get_engine()

    # La transacción de preparación se cierra antes de descargar documentos o
    # llamar al proveedor externo. No se mantienen bloqueos de base de datos
    # durante una operación de red potencialmente larga.
    with engine.begin() as conn:
        service, documents = prepare_document_extraction(
            conn,
            case_id=case_id,
            requested_document_ids=body.document_ids or None,
        )

    result = extract_service_documents(
        case_id=case_id,
        service=service,
        documents=documents,
    )
    normalized = normalize_document_packet(result.packet)

    with engine.begin() as conn:
        record = persist_document_extraction(
            conn,
            case_id=case_id,
            result=result,
            created_by=actor,
        )

    return {
        "ok": True,
        "case_id": case_id,
        "persisted": True,
        "facts_persisted": False,
        "facts_frozen": False,
        "family_resolved": False,
        "generate_allowed": False,
        "router_version": DOCUMENT_EXTRACTION_ROUTER_VERSION,
        "extractor_version": SERVICE_DOCUMENT_EXTRACTOR_VERSION,
        "extraction": _record_payload(record),
        "facts_preview": _normalization_payload(normalized),
        "next_action": (
            "OPS debe revisar la extracción y la vista previa de hechos. "
            "La creación del borrador es una acción separada."
        ),
    }


@router.get("/{case_id}/document-extractions")
def get_case_document_extractions(
    case_id: str,
    x_operator_token: Optional[str] = Header(
        default=None,
        alias="X-Operator-Token",
    ),
):
    _operator(x_operator_token, None)
    engine = get_engine()
    with engine.begin() as conn:
        records = list_document_extractions(conn, case_id)
    return {
        "ok": True,
        "case_id": case_id,
        "router_version": DOCUMENT_EXTRACTION_ROUTER_VERSION,
        "count": len(records),
        "items": [_record_payload(record) for record in records],
    }


@router.get("/{case_id}/document-extractions/{extraction_id}")
def get_case_document_extraction(
    case_id: str,
    extraction_id: str,
    x_operator_token: Optional[str] = Header(
        default=None,
        alias="X-Operator-Token",
    ),
):
    _operator(x_operator_token, None)
    engine = get_engine()
    with engine.begin() as conn:
        record = get_document_extraction(conn, case_id, extraction_id)
    return {
        "ok": True,
        "router_version": DOCUMENT_EXTRACTION_ROUTER_VERSION,
        "extraction": _record_payload(record),
    }


@router.post(
    "/{case_id}/document-extractions/{extraction_id}/facts-preview"
)
def preview_extracted_document_facts(
    case_id: str,
    extraction_id: str,
    x_operator_token: Optional[str] = Header(
        default=None,
        alias="X-Operator-Token",
    ),
):
    _operator(x_operator_token, None)
    engine = get_engine()
    with engine.begin() as conn:
        record = get_document_extraction(conn, case_id, extraction_id)
        if record.invalidated_at is not None:
            raise HTTPException(
                status_code=409,
                detail="La extracción documental está invalidada.",
            )
        normalized = normalize_document_packet(record.packet)

    return {
        "ok": True,
        "case_id": case_id,
        "extraction_id": extraction_id,
        "persisted": False,
        "facts_frozen": False,
        "family_resolved": False,
        "generate_allowed": False,
        "router_version": DOCUMENT_EXTRACTION_ROUTER_VERSION,
        "result": _normalization_payload(normalized),
        "next_action": (
            "OPS debe revisar fuentes, confianza, conflictos y campos no "
            "resueltos antes de guardar el borrador."
        ),
    }


@router.post(
    "/{case_id}/document-extractions/{extraction_id}/facts-draft"
)
def create_extracted_document_facts_draft(
    case_id: str,
    extraction_id: str,
    x_operator_token: Optional[str] = Header(
        default=None,
        alias="X-Operator-Token",
    ),
    x_operator_actor: Optional[str] = Header(
        default=None,
        alias="X-Operator-Actor",
    ),
):
    actor = _operator(x_operator_token, x_operator_actor)
    engine = get_engine()

    with engine.begin() as conn:
        record = get_document_extraction(
            conn,
            case_id,
            extraction_id,
            for_update=True,
        )
        if record.invalidated_at is not None:
            raise HTTPException(
                status_code=409,
                detail="La extracción documental está invalidada.",
            )
        normalized = normalize_document_packet(record.packet)
        facts_record = create_validated_facts(
            conn,
            case_id=case_id,
            facts=normalized.facts,
            created_by=(
                f"{actor}:{DOCUMENT_NORMALIZATION_VERSION}:"
                f"{extraction_id}"
            ),
        )
        conn.execute(
            text(
                """
                UPDATE rtm_validated_facts
                SET source_extraction_id=:extraction_id, updated_at=NOW()
                WHERE id=:facts_id
                """
            ),
            {
                "extraction_id": extraction_id,
                "facts_id": facts_record.id,
            },
        )
        _append_promotion_event(
            conn,
            case_id=case_id,
            extraction_id=extraction_id,
            facts_id=facts_record.id,
            actor=actor,
            normalized=normalized,
        )

    return {
        "ok": True,
        "case_id": case_id,
        "extraction_id": extraction_id,
        "persisted": True,
        "facts_frozen": False,
        "family_resolved": False,
        "generate_allowed": False,
        "router_version": DOCUMENT_EXTRACTION_ROUTER_VERSION,
        "facts": facts_record.model_dump(mode="json"),
        "diagnostics": {
            "accepted_fields": normalized.accepted_fields,
            "unresolved_fields": normalized.unresolved_fields,
            "conflicted_fields": normalized.conflicted_fields,
            "rejected_observations": [
                item.model_dump(mode="json")
                for item in normalized.rejected_observations
            ],
            "warnings": normalized.warnings,
        },
        "next_action": (
            "OPS debe revisar y congelar expresamente esta versión. "
            "Solo después podrá ejecutarse Family CORE."
        ),
    }


@router.post(
    "/{case_id}/document-extractions/{extraction_id}/invalidate"
)
def invalidate_case_document_extraction(
    case_id: str,
    extraction_id: str,
    body: ExtractionReasonBody,
    x_operator_token: Optional[str] = Header(
        default=None,
        alias="X-Operator-Token",
    ),
    x_operator_actor: Optional[str] = Header(
        default=None,
        alias="X-Operator-Actor",
    ),
):
    actor = _operator(x_operator_token, x_operator_actor)
    engine = get_engine()
    with engine.begin() as conn:
        record = invalidate_document_extraction(
            conn,
            case_id=case_id,
            extraction_id=extraction_id,
            operator=actor,
            reason=body.reason,
        )
    return {
        "ok": True,
        "router_version": DOCUMENT_EXTRACTION_ROUTER_VERSION,
        "extraction": _record_payload(record),
    }
