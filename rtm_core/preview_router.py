"""API OPS para el ciclo de vida de la Previa Jurídica RTM."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from database import get_engine
from rtm_core.contracts import LegalPreview
from rtm_core.preview_repository import (
    approve_preview,
    create_preview,
    freeze_preview,
    get_preview,
    invalidate_preview,
    latest_preview,
    list_previews,
    request_changes,
    submit_for_review,
)
from rtm_core.ops_case_scope import load_ops_case_scope, require_case_in_scope
from rtm_core.security import normalized_actor, require_operator_token


router = APIRouter(prefix="/ops/core/cases", tags=["rtm-core-preview"])


class CreateLegalPreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview: LegalPreview
    supersedes_id: Optional[str] = None


class PreviewReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=2000)


def _operator(token: Optional[str], actor: Optional[str]) -> str:
    require_operator_token(token)
    return normalized_actor(actor)


def _serialized(record):
    return record.model_dump(mode="json")


@router.get("/{case_id}/legal-previews")
def get_case_legal_previews(
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
        records = list_previews(conn, case_id)
    return {
        "ok": True,
        "case_id": case_id,
        "count": len(records),
        "items": [_serialized(record) for record in records],
    }


@router.get("/{case_id}/legal-previews/latest")
def get_latest_case_legal_preview(
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
        record = latest_preview(conn, case_id)
    return {
        "ok": True,
        "case_id": case_id,
        "preview": _serialized(record) if record else None,
    }


@router.get("/{case_id}/legal-previews/{preview_id}")
def get_case_legal_preview(
    case_id: str,
    preview_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _operator(x_operator_token, None)
    engine = get_engine()
    with engine.begin() as conn:
        case_id = require_case_in_scope(
            conn, scope=load_ops_case_scope(request), case_id=case_id
        )
        record = get_preview(conn, case_id, preview_id)
    return {"ok": True, "preview": _serialized(record)}


@router.post("/{case_id}/legal-previews")
def create_case_legal_preview(
    case_id: str,
    body: CreateLegalPreviewBody,
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
        record = create_preview(
            conn,
            case_id=case_id,
            preview=body.preview,
            created_by=actor,
            supersedes_id=body.supersedes_id,
        )
    return {"ok": True, "preview": _serialized(record)}


@router.post("/{case_id}/legal-previews/{preview_id}/submit-review")
def submit_case_legal_preview_for_review(
    case_id: str,
    preview_id: str,
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
        record = submit_for_review(conn, case_id, preview_id, actor)
    return {"ok": True, "preview": _serialized(record)}


@router.post("/{case_id}/legal-previews/{preview_id}/request-changes")
def request_case_legal_preview_changes(
    case_id: str,
    preview_id: str,
    body: PreviewReasonBody,
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
        record = request_changes(conn, case_id, preview_id, actor, body.reason)
    return {"ok": True, "preview": _serialized(record)}


@router.post("/{case_id}/legal-previews/{preview_id}/approve")
def approve_case_legal_preview(
    case_id: str,
    preview_id: str,
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
        record = approve_preview(conn, case_id, preview_id, actor)
    return {"ok": True, "preview": _serialized(record)}


@router.post("/{case_id}/legal-previews/{preview_id}/freeze")
def freeze_case_legal_preview(
    case_id: str,
    preview_id: str,
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
        record = freeze_preview(conn, case_id, preview_id, actor)
    return {"ok": True, "preview": _serialized(record)}


@router.post("/{case_id}/legal-previews/{preview_id}/invalidate")
def invalidate_case_legal_preview(
    case_id: str,
    preview_id: str,
    body: PreviewReasonBody,
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
        record = invalidate_preview(conn, case_id, preview_id, actor, body.reason)
    return {"ok": True, "preview": _serialized(record)}
