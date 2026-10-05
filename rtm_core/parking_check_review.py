"""Supervised review of parking preparation, linked to exact authority and preview."""
from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid4
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

from case_authority import _signed_envelope, _verify_envelope
from rtm_core import authority_repository as authority, preview_repository as previews
from rtm_core.contracts import MissingItem, MissingItemSeverity, PreviewStatus
from rtm_core.traffic_parking_preparation import CHECKS, build_parking_preparation
from rtm_core.traffic_parking_specialist import PARKING_SPECIALIST_VERSION

VERSION = "rtm_parking_check_review_v1_0"
EVENT = "rtm_parking_check_review_saved"
CHECK_IDS = {entry[0] for entry in CHECKS}


class CheckReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    expected_state_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    check_id: Literal["procedure", "deadline", "location", "rule", "conditions", "evidence", "payment", "defense"]
    result: Literal["reviewed", "needs_information"]
    notes: str = Field(min_length=10, max_length=2000)
    confirmed: bool

    @field_validator("confirmed")
    @classmethod
    def personal_confirmation(cls, value):
        if value is not True:
            raise ValueError("Confirma personalmente la revisión")
        return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), default=str).encode()).hexdigest()


def source_stamp(facts, family, documents):
    return digest({"facts_id": facts.id, "facts_sha256": facts.payload_sha256,
                   "family_id": family.id, "family_sha256": family.payload_sha256,
                   "documents": sorted((d["id"], d["sha256"]) for d in documents)})


def load_reviews(conn, case_id, facts, family, preview, documents):
    if preview.preview.created_by_component == PARKING_SPECIALIST_VERSION:
        return {}, None
    if preview.preview.created_by_component != VERSION:
        raise HTTPException(409, "La previa no pertenece a este circuito de revisión")
    rows = conn.execute(text("""
        SELECT id,payload FROM events WHERE case_id=:case AND type=:kind
          AND payload->'material'->>'preview_id'=:preview
        ORDER BY created_at,id LIMIT 2
    """), {"case": case_id, "kind": EVENT, "preview": preview.id}).fetchall()
    if len(rows) != 1:
        raise HTTPException(409, "No se puede verificar la revisión de esta previa")
    material = _verify_envelope(authority._json_payload(rows[0][1], "Revisión de previa"),
                                detail="La revisión de la previa no es verificable")
    if (material.get("format") != VERSION or material.get("id") != str(rows[0][0])
            or material.get("case_id") != case_id or material.get("preview_id") != preview.id
            or material.get("preview_sha256") != preview.payload_sha256
            or material.get("source_sha256") != source_stamp(facts, family, documents)):
        raise HTTPException(409, "La revisión no corresponde a las fuentes vigentes")
    reviews = material.get("reviews")
    if not isinstance(reviews, dict) or set(reviews) - CHECK_IDS:
        raise HTTPException(409, "Las comprobaciones guardadas no son verificables")
    for code, review in reviews.items():
        if (review.get("check_id") != code or review.get("result") not in {"reviewed", "needs_information"}
                or not isinstance(review.get("notes"), str) or len(review["notes"].strip()) < 10
                or not str(review.get("actor", "")).startswith("operator:")):
            raise HTTPException(409, "La identidad o el resultado de revisión no son verificables")
    return reviews, material["id"]


def projection(conn, case_id, facts, family, preview, documents):
    if (not facts or not family or not preview or preview.status != PreviewStatus.DRAFT
            or preview.preview.family != "estacionamiento"
            or preview.preview.created_by_component not in {PARKING_SPECIALIST_VERSION, VERSION}):
        return None
    guide = build_parking_preparation(facts.facts)
    if guide["status"] != "review_required":
        return None
    reviews, event_id = load_reviews(conn, case_id, facts, family, preview, documents)
    checks = []
    for item in guide["checks"]:
        review = reviews.get(item["id"])
        missing = [field["key"] for field in item["fields"] if field["value"] is None]
        if review and review["result"] == "reviewed" and missing:
            raise HTTPException(409, "La revisión guardada ha perdido sus hechos documentales")
        checks.append({**item, "review": review, "missing_fact_keys": missing,
                       "can_mark_reviewed": not missing})
    return {"version": VERSION, "preview_id": preview.id, "event_id": event_id,
            "source_sha256": source_stamp(facts, family, documents), "checks": checks,
            "reviewed_count": sum(c["review"] is not None and c["review"]["result"] == "reviewed" for c in checks),
            "approval_enabled": False}


def save_review(conn, *, case_id, body, actor, before, facts, family, previous, documents):
    """Called after locking and validating the full study snapshot in one transaction."""
    state = before.get("parking_review")
    if before["blockers"] or not state or previous.status != PreviewStatus.DRAFT:
        raise HTTPException(409, "Esta previa no admite una revisión por comprobaciones")
    if not actor.startswith("operator:") or str(UUID(actor[9:])) != actor[9:]:
        raise HTTPException(403, "La revisión requiere identidad individual")
    check = next(c for c in state["checks"] if c["id"] == body.check_id)
    if body.result == "reviewed" and not check["can_mark_reviewed"]:
        raise HTTPException(409, "Incorpora y contrasta los datos que faltan antes de dar el punto por revisado")
    now = authority.utcnow()
    review = {"check_id": body.check_id, "result": body.result, "notes": body.notes,
              "actor": actor, "reviewed_at": now.isoformat(),
              "source_fact_keys": [field["key"] for field in check["fields"] if field["value"] is not None]}
    reviews = {c["id"]: c["review"] for c in state["checks"] if c["review"]}
    reviews[body.check_id] = review
    missing = [item for item in previous.preview.missing_items if item.code not in {"parking_" + code for code in CHECK_IDS}]
    missing += [MissingItem(code="parking_" + c["id"], description=c["instruction"],
                            severity=MissingItemSeverity.BLOCKING)
                for c in state["checks"] if reviews.get(c["id"], {}).get("result") != "reviewed"]
    # Other fields and approval guards remain unchanged. Reviewing a point never
    # invents arguments, a deadline, an approval or a final resource.
    updated = previews.validated_preview_copy(previous.preview, missing_items=missing,
                                              created_at=now, created_by_component=VERSION)
    previews.submit_for_review(conn, case_id, previous.id, actor)
    previews.request_changes(conn, case_id, previous.id, actor,
                             "Revisión de comprobación " + body.check_id + ": " + body.notes)
    saved = previews.create_preview(conn, case_id=case_id, preview=updated,
                                    created_by=actor, supersedes_id=previous.id)
    event_id = str(uuid4())
    material = {"format": VERSION, "id": event_id, "case_id": case_id,
                "previous_event_id": state["event_id"], "previous_preview_id": previous.id,
                "previous_preview_sha256": previous.payload_sha256,
                "preview_id": saved.id, "preview_sha256": saved.payload_sha256,
                "source_sha256": source_stamp(facts, family, documents),
                "facts_id": facts.id, "family_id": family.id, "reviews": reviews,
                "actor": actor, "created_at": now.isoformat()}
    conn.execute(text("""
        INSERT INTO events(id,case_id,type,payload,created_at)
        VALUES (:id,:case,:kind,CAST(:payload AS JSONB),:created)
    """), {"id": event_id, "case": case_id, "kind": EVENT,
           "payload": json.dumps(_signed_envelope(material), ensure_ascii=False), "created": now})
    return saved
