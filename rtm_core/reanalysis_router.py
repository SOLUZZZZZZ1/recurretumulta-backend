"""API OPS del puente Reanalysis -> ValidatedFacts.

Las rutas no ejecutan el extractor ni congelan datos. Permiten inspeccionar la
transformación conservadora y, de forma explícita, guardar un borrador que OPS
debe revisar antes de su congelación.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict

from database import get_engine
from rtm_core.authority_repository import create_validated_facts
from rtm_core.reanalysis_adapter import (
    REANALYSIS_ADAPTER_VERSION,
    assert_reanalysis_draft_is_safe,
    build_validated_facts_from_reanalysis,
    load_latest_reanalysis_snapshot,
)
from rtm_core.ops_case_scope import load_ops_case_scope, require_case_in_scope
from rtm_core.security import normalized_actor, require_operator_token


router = APIRouter(prefix="/ops/core/cases", tags=["rtm-core-reanalysis"])


class PromoteReanalysisFactsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supersedes_id: Optional[str] = None


def _operator(token: Optional[str], actor: Optional[str]) -> str:
    require_operator_token(token)
    return normalized_actor(actor, fallback="ops:reanalysis-review")


@router.get("/{case_id}/reanalysis/facts-preview")
def preview_reanalysis_validated_facts(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    """Vista sin persistencia: muestra qué se validaría y qué queda pendiente."""

    _operator(x_operator_token, None)
    engine = get_engine()
    with engine.begin() as conn:
        case_id = require_case_in_scope(
            conn, scope=load_ops_case_scope(request), case_id=case_id
        )
        wrapper, event = load_latest_reanalysis_snapshot(conn, case_id)
        result = build_validated_facts_from_reanalysis(
            case_id=case_id,
            wrapper=wrapper,
            event_payload=event,
        )
    return {
        "ok": True,
        "case_id": case_id,
        "persisted": False,
        "adapter_version": REANALYSIS_ADAPTER_VERSION,
        "result": result.model_dump(mode="json"),
    }


@router.post("/{case_id}/reanalysis/facts-draft")
def promote_reanalysis_to_validated_facts_draft(
    case_id: str,
    body: PromoteReanalysisFactsBody,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    x_operator_actor: Optional[str] = Header(default=None, alias="X-Operator-Actor"),
):
    """Guarda un borrador versionado; nunca lo congela automáticamente."""

    actor = _operator(x_operator_token, x_operator_actor)
    engine = get_engine()
    with engine.begin() as conn:
        case_id = require_case_in_scope(
            conn, scope=load_ops_case_scope(request), case_id=case_id
        )
        wrapper, event = load_latest_reanalysis_snapshot(conn, case_id)
        adapted = build_validated_facts_from_reanalysis(
            case_id=case_id,
            wrapper=wrapper,
            event_payload=event,
        )
        # Defensa en profundidad en el límite de persistencia: aunque cambie el
        # adaptador, esta ruta nunca guardará como VALIDATED una salida de modelo.
        assert_reanalysis_draft_is_safe(adapted.facts)
        record = create_validated_facts(
            conn,
            case_id=case_id,
            facts=adapted.facts,
            created_by=f"{actor}:{REANALYSIS_ADAPTER_VERSION}",
            supersedes_id=body.supersedes_id,
        )

    return {
        "ok": True,
        "case_id": case_id,
        "persisted": True,
        "adapter_version": REANALYSIS_ADAPTER_VERSION,
        "facts": record.model_dump(mode="json"),
        "diagnostics": {
            "accepted_fields": adapted.accepted_fields,
            "unresolved_fields": adapted.unresolved_fields,
            "conflicted_fields": adapted.conflicted_fields,
            "ignored_fields": adapted.ignored_fields,
            "warnings": adapted.warnings,
        },
        "authority_requirements": {
            "model_derived_fields": "unresolved_until_operator_document_review",
            "freeze_requires_document_review_attestation": True,
            "attestation_must_bind": [
                "facts_payload_sha256",
                "source_document_ids",
            ],
        },
        "next_action": (
            "OPS debe revisar los documentos, crear fuentes de revisión para los "
            "hechos confirmados y atestar la versión exacta antes de congelarla."
        ),
    }
