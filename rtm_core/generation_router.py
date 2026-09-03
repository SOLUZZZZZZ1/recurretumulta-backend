"""API OPS de Generate controlado por Previa Jurídica congelada."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, Request

from database import get_engine
from rtm_core.generation_gateway import (
    approve_resource_for_submission,
    cleanup_generated_uploads,
    generate_from_frozen_preview,
    get_generated_resource,
    list_generated_resources,
)
from rtm_core.ops_case_scope import load_ops_case_scope, require_case_in_scope
from rtm_core.security import normalized_actor, require_operator_token


router = APIRouter(prefix="/ops/core/cases", tags=["rtm-core-generate"])


def _operator(token: Optional[str], actor: Optional[str]) -> str:
    require_operator_token(token)
    return normalized_actor(actor)


def _serialized(record):
    return record.model_dump(mode="json")


@router.get("/{case_id}/generated-resources")
def get_case_generated_resources(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _operator(x_operator_token, None)
    engine = get_engine()
    with engine.begin() as conn:
        case_id = require_case_in_scope(
            conn, scope=load_ops_case_scope(request), case_id=case_id
        )
        records = list_generated_resources(conn, case_id)
    return {
        "ok": True,
        "case_id": case_id,
        "count": len(records),
        "items": [_serialized(record) for record in records],
    }


@router.get("/{case_id}/generated-resources/{resource_id}")
def get_case_generated_resource(
    case_id: str,
    resource_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _operator(x_operator_token, None)
    engine = get_engine()
    with engine.begin() as conn:
        case_id = require_case_in_scope(
            conn, scope=load_ops_case_scope(request), case_id=case_id
        )
        record = get_generated_resource(conn, case_id, resource_id)
    return {"ok": True, "resource": _serialized(record)}


@router.post("/{case_id}/legal-previews/{preview_id}/generate")
def generate_case_from_frozen_preview(
    case_id: str,
    preview_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    x_operator_actor: Optional[str] = Header(default=None, alias="X-Operator-Actor"),
):
    actor = _operator(x_operator_token, x_operator_actor)
    engine = get_engine()
    uploaded_coordinates: list[tuple[str, str]] = []
    try:
        with engine.begin() as conn:
            case_id = require_case_in_scope(
                conn, scope=load_ops_case_scope(request), case_id=case_id
            )
            record = generate_from_frozen_preview(
                conn,
                case_id=case_id,
                preview_id=preview_id,
                generated_by=actor,
                uploaded_coordinates=uploaded_coordinates,
            )
    except Exception:
        cleanup_generated_uploads(uploaded_coordinates)
        raise
    return {"ok": True, "resource": _serialized(record)}


@router.post("/{case_id}/generated-resources/{resource_id}/approve-submission")
def approve_case_resource_for_submission(
    case_id: str,
    resource_id: str,
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
        record = approve_resource_for_submission(
            conn,
            case_id=case_id,
            resource_id=resource_id,
            approved_by=actor,
        )
    return {"ok": True, "resource": _serialized(record)}
