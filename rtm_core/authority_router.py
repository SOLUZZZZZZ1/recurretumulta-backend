"""API OPS de hechos validados y resolución única de familia RTM."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from database import get_engine
from rtm_core.authority_repository import (
    DocumentReviewAttestation,
    create_family_resolution,
    create_validated_facts,
    freeze_validated_facts,
    get_family_resolution,
    get_validated_facts,
    invalidate_family_resolution,
    invalidate_validated_facts,
    latest_family_resolution,
    latest_validated_facts,
    list_family_resolutions,
    list_validated_facts,
    lock_family_resolution,
)
from rtm_core.contracts import FamilyResolution, ValidatedFacts
from rtm_core.ops_case_scope import load_ops_case_scope, require_case_in_scope
from rtm_core.security import normalized_actor, require_operator_token


router = APIRouter(prefix="/ops/core/cases", tags=["rtm-core-authority"])


class CreateValidatedFactsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facts: ValidatedFacts
    supersedes_id: Optional[str] = None


class CreateFamilyResolutionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: FamilyResolution
    validated_facts_id: Optional[str] = None
    supersedes_id: Optional[str] = None


class AuthorityReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=2000)


def _operator(token: Optional[str], actor: Optional[str]) -> str:
    require_operator_token(token)
    return normalized_actor(actor)


def _serialized(record):
    return record.model_dump(mode="json")


@router.get("/{case_id}/validated-facts")
def get_case_validated_facts(
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
        records = list_validated_facts(conn, case_id)
    return {
        "ok": True,
        "case_id": case_id,
        "count": len(records),
        "items": [_serialized(record) for record in records],
    }


@router.get("/{case_id}/validated-facts/latest")
def get_latest_case_validated_facts(
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
        record = latest_validated_facts(conn, case_id)
    return {
        "ok": True,
        "case_id": case_id,
        "facts": _serialized(record) if record else None,
    }


@router.get("/{case_id}/validated-facts/{facts_id}")
def get_case_validated_facts_version(
    case_id: str,
    facts_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _operator(x_operator_token, None)
    engine = get_engine()
    with engine.begin() as conn:
        case_id = require_case_in_scope(
            conn, scope=load_ops_case_scope(request), case_id=case_id
        )
        record = get_validated_facts(conn, case_id, facts_id)
    return {"ok": True, "facts": _serialized(record)}


@router.post("/{case_id}/validated-facts")
def create_case_validated_facts(
    case_id: str,
    body: CreateValidatedFactsBody,
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
        record = create_validated_facts(
            conn,
            case_id=case_id,
            facts=body.facts,
            created_by=actor,
            supersedes_id=body.supersedes_id,
        )
    return {"ok": True, "facts": _serialized(record)}


@router.post("/{case_id}/validated-facts/{facts_id}/freeze")
def freeze_case_validated_facts(
    case_id: str,
    facts_id: str,
    request: Request,
    body: Optional[DocumentReviewAttestation] = None,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    x_operator_actor: Optional[str] = Header(default=None, alias="X-Operator-Actor"),
):
    actor = _operator(x_operator_token, x_operator_actor)
    engine = get_engine()
    with engine.begin() as conn:
        case_id = require_case_in_scope(
            conn, scope=load_ops_case_scope(request), case_id=case_id
        )
        record = freeze_validated_facts(
            conn,
            case_id,
            facts_id,
            actor,
            document_review_attestation=body,
        )
    return {"ok": True, "facts": _serialized(record)}


@router.post("/{case_id}/validated-facts/{facts_id}/invalidate")
def invalidate_case_validated_facts(
    case_id: str,
    facts_id: str,
    body: AuthorityReasonBody,
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
        record = invalidate_validated_facts(
            conn,
            case_id,
            facts_id,
            actor,
            body.reason,
        )
    return {"ok": True, "facts": _serialized(record)}


@router.get("/{case_id}/family-resolutions")
def get_case_family_resolutions(
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
        records = list_family_resolutions(conn, case_id)
    return {
        "ok": True,
        "case_id": case_id,
        "count": len(records),
        "items": [_serialized(record) for record in records],
    }


@router.get("/{case_id}/family-resolutions/latest")
def get_latest_case_family_resolution(
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
        record = latest_family_resolution(conn, case_id)
    return {
        "ok": True,
        "case_id": case_id,
        "resolution": _serialized(record) if record else None,
    }


@router.get("/{case_id}/family-resolutions/{resolution_id}")
def get_case_family_resolution(
    case_id: str,
    resolution_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _operator(x_operator_token, None)
    engine = get_engine()
    with engine.begin() as conn:
        case_id = require_case_in_scope(
            conn, scope=load_ops_case_scope(request), case_id=case_id
        )
        record = get_family_resolution(conn, case_id, resolution_id)
    return {"ok": True, "resolution": _serialized(record)}


@router.post("/{case_id}/family-resolutions")
def create_case_family_resolution(
    case_id: str,
    body: CreateFamilyResolutionBody,
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
        record = create_family_resolution(
            conn,
            case_id=case_id,
            resolution=body.resolution,
            created_by=actor,
            validated_facts_id=body.validated_facts_id,
            supersedes_id=body.supersedes_id,
        )
    return {"ok": True, "resolution": _serialized(record)}


@router.post("/{case_id}/family-resolutions/{resolution_id}/lock")
def lock_case_family_resolution(
    case_id: str,
    resolution_id: str,
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
        record = lock_family_resolution(conn, case_id, resolution_id, actor)
    return {"ok": True, "resolution": _serialized(record)}


@router.post("/{case_id}/family-resolutions/{resolution_id}/invalidate")
def invalidate_case_family_resolution(
    case_id: str,
    resolution_id: str,
    body: AuthorityReasonBody,
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
        record = invalidate_family_resolution(
            conn,
            case_id,
            resolution_id,
            actor,
            body.reason,
        )
    return {"ok": True, "resolution": _serialized(record)}
