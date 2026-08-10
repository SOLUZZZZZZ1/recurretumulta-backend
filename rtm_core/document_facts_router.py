"""Gateway OPS de hechos documentales para satélites RTM no tráfico.

El gateway acepta un paquete estructurado de observaciones documentales, valida
expediente, servicio y pertenencia de documentos, muestra una vista previa y,
solo mediante una acción expresa, guarda un borrador de ``ValidatedFacts``.
Nunca congela hechos, resuelve familia, crea una Previa ni llama a Generate.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from database import get_engine
from rtm_core.authority_repository import create_validated_facts
from rtm_core.document_fact_catalog import (
    DOCUMENT_FACT_CATALOG_VERSION,
    canonical_document_service,
    fact_catalog_summary,
)
from rtm_core.document_normalization import (
    DOCUMENT_EXTRACTION_PACKET_VERSION,
    DOCUMENT_NORMALIZATION_VERSION,
    DocumentExtractionPacket,
    DocumentNormalizationResult,
    normalize_document_packet,
    validate_packet_documents,
)
from rtm_core.security import normalized_actor, require_operator_token
from rtm_core.service_catalog import canonical_department


DOCUMENT_FACTS_GATEWAY_VERSION = "rtm_document_facts_gateway_v1_0"

router = APIRouter(prefix="/ops/core", tags=["rtm-core-document-facts"])

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
_EXCLUDED_DOCUMENT_KIND_TOKENS = {
    "generated",
    "rtm_generated",
    "receipt",
    "justificante",
    "submission",
    "authorization",
    "autorizacion",
    "identity_front",
    "identity_back",
    "dni_front",
    "dni_back",
    "payment",
    "stripe",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PreviewDocumentFactsBody(_StrictModel):
    packet: DocumentExtractionPacket


class CreateDocumentFactsDraftBody(_StrictModel):
    packet: DocumentExtractionPacket
    supersedes_id: Optional[str] = None


def _operator(token: Optional[str], actor: Optional[str]) -> str:
    require_operator_token(token)
    return normalized_actor(actor, fallback="ops:document-facts")


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _case_payload(conn, case_id: str, *, for_update: bool) -> dict[str, Any]:
    suffix = " FOR UPDATE" if for_update else ""
    row = conn.execute(
        text(
            "SELECT to_jsonb(c) FROM cases c WHERE c.id=:case_id" + suffix
        ),
        {"case_id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    payload = _mapping(row[0])
    if not payload:
        raise HTTPException(
            status_code=500,
            detail="No puede leerse el expediente para validar los hechos",
        )
    return payload


def _case_service(payload: Mapping[str, Any]) -> str:
    return canonical_department(
        str(payload.get("department") or ""),
        str(payload.get("case_type") or ""),
        str(payload.get("category") or ""),
    )


def _allowed_document_ids(conn, case_id: str) -> set[str]:
    rows = conn.execute(
        text(
            """
            SELECT CAST(id AS TEXT), COALESCE(kind,'')
            FROM documents
            WHERE case_id=:case_id
            """
        ),
        {"case_id": case_id},
    ).fetchall()
    allowed: set[str] = set()
    for document_id, kind in rows:
        kind_low = str(kind or "").strip().lower()
        if any(token in kind_low for token in _EXCLUDED_DOCUMENT_KIND_TOKENS):
            continue
        allowed.add(str(document_id))
    return allowed


def _validate_case_and_packet(
    conn,
    *,
    case_id: str,
    packet: DocumentExtractionPacket,
    for_update: bool,
) -> dict[str, Any]:
    if packet.case_id != case_id:
        raise HTTPException(
            status_code=409,
            detail="El case_id del paquete no coincide con la ruta",
        )

    payload = _case_payload(conn, case_id, for_update=for_update)
    if str(payload.get("payment_status") or "") != "paid":
        raise HTTPException(
            status_code=402,
            detail="El estudio debe estar pagado antes de preparar hechos",
        )
    if not bool(payload.get("authorized")):
        raise HTTPException(status_code=409, detail="Falta autorización del cliente")
    if str(payload.get("status") or "") in _TERMINAL_CASE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="El expediente está en un estado final",
        )

    persisted_service = _case_service(payload)
    try:
        supplied_service = canonical_document_service(packet.service)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if persisted_service == "traffic":
        raise HTTPException(
            status_code=409,
            detail="Tráfico debe utilizar Reanalysis y su adaptador específico",
        )
    if supplied_service != persisted_service:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "El servicio del paquete no coincide con el expediente",
                "case_service": persisted_service,
                "packet_service": supplied_service,
            },
        )

    allowed_documents = _allowed_document_ids(conn, case_id)
    if not allowed_documents:
        raise HTTPException(
            status_code=409,
            detail="El expediente no conserva documentos aptos para extraer hechos",
        )
    validate_packet_documents(
        packet=packet,
        available_document_ids=allowed_documents,
    )
    return payload


def _normalization_payload(result: DocumentNormalizationResult) -> dict[str, Any]:
    return result.model_dump(mode="json")


def _append_gateway_event(
    conn,
    *,
    case_id: str,
    actor: str,
    facts_id: str,
    packet: DocumentExtractionPacket,
    result: DocumentNormalizationResult,
) -> None:
    payload = {
        "gateway_version": DOCUMENT_FACTS_GATEWAY_VERSION,
        "packet_version": DOCUMENT_EXTRACTION_PACKET_VERSION,
        "catalog_version": DOCUMENT_FACT_CATALOG_VERSION,
        "normalization_version": DOCUMENT_NORMALIZATION_VERSION,
        "facts_id": facts_id,
        "service": result.facts.service,
        "actor": actor,
        "source_document_ids": list(packet.source_document_ids),
        "accepted_fields": list(result.accepted_fields),
        "unresolved_fields": list(result.unresolved_fields),
        "conflicted_fields": list(result.conflicted_fields),
        "rejected_observation_count": len(result.rejected_observations),
        "warning_count": len(result.warnings),
    }
    conn.execute(
        text(
            """
            INSERT INTO events(case_id, type, payload, created_at)
            VALUES (:case_id, 'rtm_document_facts_draft_created',
                    CAST(:payload AS JSONB), NOW())
            """
        ),
        {
            "case_id": case_id,
            "payload": json.dumps(payload, ensure_ascii=False),
        },
    )


@router.get("/document-facts/catalog/{service}")
def get_document_fact_catalog(
    service: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _operator(x_operator_token, None)
    try:
        summary = fact_catalog_summary(service)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "ok": True,
        "gateway_version": DOCUMENT_FACTS_GATEWAY_VERSION,
        "catalog": summary,
    }


@router.post("/cases/{case_id}/document-facts/preview")
def preview_document_facts(
    case_id: str,
    body: PreviewDocumentFactsBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _operator(x_operator_token, None)
    engine = get_engine()
    with engine.begin() as conn:
        _validate_case_and_packet(
            conn,
            case_id=case_id,
            packet=body.packet,
            for_update=False,
        )
        result = normalize_document_packet(body.packet)
    return {
        "ok": True,
        "case_id": case_id,
        "persisted": False,
        "frozen": False,
        "family_resolved": False,
        "generate_allowed": False,
        "gateway_version": DOCUMENT_FACTS_GATEWAY_VERSION,
        "result": _normalization_payload(result),
        "next_action": (
            "OPS debe revisar los valores, fuentes, conflictos y campos no "
            "resueltos antes de guardar un borrador."
        ),
    }


@router.post("/cases/{case_id}/document-facts/draft")
def create_document_facts_draft(
    case_id: str,
    body: CreateDocumentFactsDraftBody,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    x_operator_actor: Optional[str] = Header(default=None, alias="X-Operator-Actor"),
):
    actor = _operator(x_operator_token, x_operator_actor)
    engine = get_engine()
    with engine.begin() as conn:
        _validate_case_and_packet(
            conn,
            case_id=case_id,
            packet=body.packet,
            for_update=True,
        )
        normalized = normalize_document_packet(body.packet)
        record = create_validated_facts(
            conn,
            case_id=case_id,
            facts=normalized.facts,
            created_by=f"{actor}:{DOCUMENT_NORMALIZATION_VERSION}",
            supersedes_id=body.supersedes_id,
        )
        _append_gateway_event(
            conn,
            case_id=case_id,
            actor=actor,
            facts_id=record.id,
            packet=body.packet,
            result=normalized,
        )

    return {
        "ok": True,
        "case_id": case_id,
        "persisted": True,
        "frozen": False,
        "family_resolved": False,
        "generate_allowed": False,
        "gateway_version": DOCUMENT_FACTS_GATEWAY_VERSION,
        "facts": record.model_dump(mode="json"),
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
            "OPS debe revisar esta versión y congelarla expresamente. Solo "
            "después podrá ejecutarse Family CORE."
        ),
    }
