"""API OPS para ejecutar la única autoridad de familia RTM."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request

from database import get_engine
from rtm_core.authority_repository import (
    create_family_resolution,
    latest_family_resolution,
    latest_validated_facts,
)
from rtm_core.family_dispatch import (
    FAMILY_DISPATCH_VERSION,
    resolve_family,
    resolver_version_for,
)
from rtm_core.ops_case_scope import load_ops_case_scope, require_case_in_scope
from rtm_core.security import normalized_actor, require_operator_token


router = APIRouter(prefix="/ops/core/cases", tags=["rtm-core-family"])


def _operator(token: Optional[str], actor: Optional[str]) -> str:
    require_operator_token(token)
    return normalized_actor(actor)


@router.post("/{case_id}/resolve-family")
def resolve_case_family(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    x_operator_actor: Optional[str] = Header(default=None, alias="X-Operator-Actor"),
):
    actor = _operator(x_operator_token, x_operator_actor)
    engine = get_engine()

    with engine.begin() as conn:
        case_id = require_case_in_scope(
            conn, scope=load_ops_case_scope(request), case_id=case_id
        )
        facts_record = latest_validated_facts(
            conn,
            case_id,
            active_only=True,
            frozen_only=True,
            for_update=True,
        )
        if not facts_record:
            raise HTTPException(
                status_code=409,
                detail="No existe una versión activa y congelada de hechos",
            )

        resolver_version = resolver_version_for(facts_record.facts.service)
        existing = latest_family_resolution(
            conn,
            case_id,
            active_only=True,
            for_update=True,
        )
        if existing:
            if existing.validated_facts_id != facts_record.id:
                raise HTTPException(
                    status_code=409,
                    detail="La familia activa no corresponde a los hechos actuales",
                )
            return {
                "ok": True,
                "reused": True,
                "dispatch_version": FAMILY_DISPATCH_VERSION,
                "resolver_version": resolver_version,
                "resolution": existing.model_dump(mode="json"),
            }

        resolution = resolve_family(facts_record.facts)
        record = create_family_resolution(
            conn,
            case_id=case_id,
            resolution=resolution,
            created_by=f"{FAMILY_DISPATCH_VERSION}:{resolver_version}:{actor}",
            validated_facts_id=facts_record.id,
        )

    return {
        "ok": True,
        "reused": False,
        "dispatch_version": FAMILY_DISPATCH_VERSION,
        "resolver_version": resolver_version,
        "resolution": record.model_dump(mode="json"),
    }
