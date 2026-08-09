"""API OPS para ejecutar el especialista resuelto y crear la Previa Jurídica."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from database import get_engine
from rtm_core.authority_repository import (
    get_validated_facts,
    latest_family_resolution,
)
from rtm_core.preview_repository import create_preview, latest_preview
from rtm_core.security import normalized_actor, require_operator_token
from rtm_core.specialist_registry import (
    SPECIALIST_REGISTRY_VERSION,
    build_legal_preview,
)


router = APIRouter(prefix="/ops/core/cases", tags=["rtm-core-specialist"])


def _operator(token: Optional[str], actor: Optional[str]) -> str:
    require_operator_token(token)
    return normalized_actor(actor)


@router.post("/{case_id}/build-legal-preview")
def build_case_legal_preview(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    x_operator_actor: Optional[str] = Header(default=None, alias="X-Operator-Actor"),
):
    actor = _operator(x_operator_token, x_operator_actor)
    engine = get_engine()

    with engine.begin() as conn:
        family_record = latest_family_resolution(
            conn,
            case_id,
            active_only=True,
            locked_only=True,
            for_update=True,
        )
        if not family_record:
            raise HTTPException(
                status_code=409,
                detail="No existe una resolución de familia activa y bloqueada",
            )
        facts_record = get_validated_facts(
            conn,
            case_id,
            family_record.validated_facts_id,
            for_update=True,
        )
        if facts_record.invalidated_at is not None or not facts_record.frozen:
            raise HTTPException(
                status_code=409,
                detail="Los hechos de la familia ya no están activos y congelados",
            )

        existing = latest_preview(conn, case_id)
        if existing and existing.status.value in {
            "draft",
            "ops_review",
            "approved",
            "frozen",
        }:
            if (
                existing.validated_facts_id != facts_record.id
                or existing.family_resolution_id != family_record.id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="La previa activa no corresponde a la autoridad vigente",
                )
            return {
                "ok": True,
                "reused": True,
                "registry_version": SPECIALIST_REGISTRY_VERSION,
                "preview": existing.model_dump(mode="json"),
            }

        preview = build_legal_preview(facts_record, family_record)
        record = create_preview(
            conn,
            case_id=case_id,
            preview=preview,
            created_by=(
                f"{SPECIALIST_REGISTRY_VERSION}:"
                f"{family_record.resolution.specialist}:{actor}"
            ),
            supersedes_id=(
                existing.id
                if existing and existing.status.value in {"changes_required", "invalidated"}
                else None
            ),
        )

    return {
        "ok": True,
        "reused": False,
        "registry_version": SPECIALIST_REGISTRY_VERSION,
        "preview": record.model_dump(mode="json"),
    }
