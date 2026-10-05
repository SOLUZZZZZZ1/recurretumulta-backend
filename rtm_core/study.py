"""Supervised continuation of reviewed facts into a specialist preview.

The caller owns one transaction. Lock the case before its authority records,
bind confirmations to the complete snapshot, and never re-extract documents.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text

from case_authority import verify_signed_case_authority
from rtm_core import authority_repository as authority
from rtm_core import preview_repository as previews
from rtm_core.family_dispatch import resolve_family
from rtm_core.specialist_dispatch import build_legal_preview, registered_specialists

STUDY_VERSION = "rtm_ops_study_v1"
Action = Literal["freeze_facts", "resolve_family", "lock_family", "build_preview", "reopen_facts"]


class StudyActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Action
    expected_state_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: bool
    document_review: authority.DocumentReviewAttestation | None = None
    reason: str | None = Field(default=None, min_length=10, max_length=2000)

    @field_validator("document_review", mode="before")
    @classmethod
    def strict_personal_checks(cls, value):
        if isinstance(value, dict) and (
            value.get("documents_reviewed") is not True or value.get("facts_reviewed") is not True
        ):
            raise ValueError("Las confirmaciones documentales deben ser booleanos explícitos")
        return value

    @model_validator(mode="after")
    def validate_confirmation(self):
        if self.confirmed is not True:
            raise ValueError("Confirma expresamente el paso que vas a realizar")
        if (self.action == "freeze_facts") != (self.document_review is not None):
            raise ValueError("El cierre de hechos requiere su revisión documental")
        if (self.action == "reopen_facts") != (self.reason is not None):
            raise ValueError("La nueva revisión requiere su motivo explícito")
        if self.reason is not None and len(self.reason.strip()) < 10:
            raise ValueError("Describe el motivo de la nueva revisión")
        return self


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), default=str).encode()).hexdigest()


def _dump(record):
    return record.model_dump(mode="json") if record else None


def _stage(facts, family, preview):
    if not facts:
        return "facts_missing", None, ["Todavía no hay hechos revisados para continuar."]
    if family and (family.validated_facts_id != facts.id or not facts.frozen):
        return "authority_conflict", None, ["La clasificación no corresponde a los hechos vigentes."]
    if preview and preview.status.value not in {"invalidated", "changes_required"}:
        if (not family or not family.locked or preview.validated_facts_id != facts.id
                or preview.family_resolution_id != family.id):
            return "authority_conflict", None, ["La previa no corresponde a la autoridad vigente."]
        return "preview_available", None, []
    if not facts.frozen:
        pending = (not facts.facts.facts or facts.facts.unresolved or facts.facts.conflicts
                   or any(f.status.value != "validated" or f.conflicts
                          for f in facts.facts.facts.values()))
        return "facts_review", (None if pending else "freeze_facts"), (
            ["Revisa los datos pendientes y los conflictos antes de cerrar los hechos."] if pending else [])
    if not family:
        return "family_pending", "resolve_family", []
    if (family.resolution.status.value != "resolved" or family.resolution.conflicts
            or family.resolution.unresolved or family.resolution.confidence <= 0):
        return "family_review", None, ["La clasificación requiere resolver sus dudas o conflictos."]
    if family.resolution.specialist not in registered_specialists():
        return "specialist_missing", None, ["Esta clasificación todavía no dispone de un especialista disponible."]
    if not family.locked:
        return "family_confirmation", "lock_family", []
    return "preview_pending", "build_preview", []


def load_study(conn, case_id: str, *, for_update=False):
    # Consistent with guarded writers; writes always acquire this lock first.
    meta = authority._case_authority_meta(conn, case_id, for_update=True)
    facts = authority.latest_validated_facts(conn, case_id, active_only=True, for_update=for_update)
    family = authority.latest_family_resolution(conn, case_id, active_only=True, for_update=for_update)
    preview = previews.latest_preview(conn, case_id)
    documents = [dict(row._mapping) for row in conn.execute(text(
        "SELECT id::text AS id, sha256 FROM documents "
        "WHERE case_id=:id AND kind='original' ORDER BY id"
    ), {"id": case_id}).fetchall()]
    stage, action, blockers = _stage(facts, family, preview)
    signed_digest = None
    try:
        authority._require_authority_work_allowed(meta)
        if meta["department"] != "traffic" or meta["case_type"] != "fine":
            raise HTTPException(409, "Esta continuación corresponde a recursos de multa.")
        signed_digest = _digest(verify_signed_case_authority(conn, case_id))
    except HTTPException as exc:
        blockers.append(exc.detail if isinstance(exc.detail, str) else "La autorización necesita revisión.")
    if facts and (not facts.facts.source_document_ids or
                  not set(facts.facts.source_document_ids).issubset({doc["id"] for doc in documents})):
        blockers.append("Faltan documentos originales de la versión revisada.")
    if blockers:
        action = None
    payload = {
        "ok": True, "study_version": STUDY_VERSION, "case_id": case_id,
        "case_status": str(meta["status"]), "stage": stage,
        "next_action": action, "blockers": blockers,
        "facts": _dump(facts), "family": _dump(family), "preview": _dump(preview),
    }
    from rtm_core.parking_check_review import projection as parking_projection
    from rtm_core.study_documents import revision_available
    from rtm_core.traffic_parking_preparation import build_parking_preparation
    payload["documents"] = documents
    payload["can_add_document"] = revision_available(payload, facts, preview)
    payload["can_reopen_facts"] = payload["can_add_document"] and facts.frozen
    payload["available_actions"] = ([action] if action else []) + (["reopen_facts"] if payload["can_reopen_facts"] else [])
    payload["parking_review"] = (parking_projection(conn, case_id, facts, family, preview, documents)
                                 if stage == "preview_available" else None)
    payload["parking_preparation"] = build_parking_preparation(facts.facts) if facts else None
    payload["state_sha256"] = _digest({
        "projection": payload, "case": dict(meta), "documents": documents,
        "signed_authority_sha256": signed_digest,
    })
    return payload, facts, family, preview


def advance_study(conn, *, case_id: str, body: StudyActionBody, actor: str):
    before, facts, family, previous_preview = load_study(conn, case_id, for_update=True)
    if not hmac.compare_digest(before["state_sha256"], body.expected_state_sha256):
        raise HTTPException(409, "El expediente ha cambiado. Recarga el estudio antes de continuar.")
    if body.action not in before["available_actions"] or before["blockers"]:
        raise HTTPException(409, "Este paso no está disponible en el estado actual del expediente.")
    review = body.document_review
    if body.action == "freeze_facts":
        if (review.facts_payload_sha256 != facts.payload_sha256
                or set(review.source_document_ids) != set(facts.facts.source_document_ids)):
            raise HTTPException(409, "La confirmación documental no corresponde a estos hechos.")
        authority.freeze_validated_facts(conn, case_id, facts.id, actor,
                                        document_review_attestation=review)
    elif body.action == "resolve_family":
        authority.create_family_resolution(conn, case_id=case_id,
            resolution=resolve_family(facts.facts), created_by=actor, validated_facts_id=facts.id)
    elif body.action == "lock_family":
        authority.lock_family_resolution(conn, case_id, family.id, actor)
    elif body.action == "reopen_facts":
        from rtm_core.study_documents import replace_facts
        replace_facts(conn, case_id=case_id, facts=facts, actor=actor, reason=body.reason)
    elif body.action == "build_preview":
        previews.create_preview(conn, case_id=case_id,
            preview=build_legal_preview(facts, family), created_by=actor,
            supersedes_id=previous_preview.id if previous_preview else None)
    after, *_ = load_study(conn, case_id)
    authority._append_event(conn, case_id, "rtm_ops_study_advanced", {
        "study_version": STUDY_VERSION, "action": body.action, "actor": actor,
        "previous_state_sha256": before["state_sha256"], "state_sha256": after["state_sha256"],
        "facts_id": facts.id, "new_facts_id": after["facts"]["id"], "family_resolution_id": after["family"]["id"] if after["family"] else None,
        "preview_id": after["preview"]["id"] if after["preview"] else None,
        # Also preserve the personal attestation for non-model synthetic facts.
        "document_review": review.model_dump(mode="json") if review else None,
    })
    return {**after, "completed_action": body.action, "previous_state_sha256": before["state_sha256"]}
