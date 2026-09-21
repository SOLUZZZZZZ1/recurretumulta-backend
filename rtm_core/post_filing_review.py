"""Append-only human reviews for the explicitly synthetic local filing clock."""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from case_authority import _signed_envelope, _verify_envelope

REVIEW_EVENT = "rtm_local_post_filing_reviewed"
REVIEW_VERSION = "rtm_local_post_filing_review_v1"


class CalendarReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    from_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    to_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    holidays: list[str] = Field(max_length=366)
    source: str = Field(min_length=10, max_length=1500)
    territory: str = Field(min_length=3, max_length=300)

    @model_validator(mode="after")
    def valid_calendar(self):
        start, end = date.fromisoformat(self.from_date), date.fromisoformat(self.to_date)
        if start > end or (end-start).days > 731:
            raise ValueError("Cobertura de calendario no válida")
        if len(set(self.holidays)) != len(self.holidays):
            raise ValueError("Hay festivos repetidos")
        for item in self.holidays:
            if len(item) != 10 or not start <= date.fromisoformat(item) <= end:
                raise ValueError("Un festivo está fuera de la cobertura indicada")
        return self


class DeadlineReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    expected_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_review_id: str | None
    rule_checked: bool
    calendar: CalendarReview | None
    procedural_status: Literal["pending", "no_changes", "has_changes"]
    notes: str = Field(min_length=10, max_length=3000)
    attested: bool

    @model_validator(mode="after")
    def valid_attestation(self):
        if self.attested is not True:
            raise ValueError("Es necesaria la confirmación personal de la revisión")
        if self.expected_review_id is not None and str(UUID(self.expected_review_id)) != self.expected_review_id:
            raise ValueError("Revisión anterior no válida")
        return self


def source_fingerprint(conn, case_id, material):
    # New documents or case events invalidate the applicability of an older
    # review. Reviews themselves are versioned separately for concurrency.
    docs = [list(row) for row in conn.execute(text("""
        SELECT id,kind,sha256,size_bytes FROM documents WHERE case_id=:id ORDER BY id
    """), {"id": case_id})]
    events = [list(row) for row in conn.execute(text("""
        SELECT id,type,created_at FROM events WHERE case_id=:id AND type<>:review ORDER BY id
    """), {"id": case_id, "review": REVIEW_EVENT})]
    return hashlib.sha256(json.dumps([material, docs, events], default=str,
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def apply_review(material, review):
    updated = {**material, "calendar": None, "procedural_events_reviewed": False}
    if review and review.calendar:
        cal = review.calendar
        updated["calendar"] = {"reviewed": True, "from": cal.from_date, "to": cal.to_date,
                               "holidays": cal.holidays, "source": cal.source}
    if review:
        updated["procedural_events_reviewed"] = review.rule_checked and review.procedural_status == "no_changes"
    return updated


def review_projection(conn, case_id, material):
    from rtm_core.post_filing_deadlines import calculate_followup
    fingerprint = source_fingerprint(conn, case_id, material)
    history, previous_id, latest = [], None, None
    for event in conn.execute(text("""
        SELECT id,payload FROM events WHERE case_id=:id AND type=:kind ORDER BY created_at,id
    """), {"id": case_id, "kind": REVIEW_EVENT}):
        payload = _verify_envelope(event[1], detail="Revision de plazos no verificable")
        body = DeadlineReviewBody.model_validate(payload["review"])
        if (payload.get("format") != REVIEW_VERSION or payload.get("case_id") != case_id
                or payload.get("synthetic_only") is not True
                or body.expected_review_id != previous_id
                or not str(payload.get("actor", "")).startswith("operator:")):
            raise ValueError("Cadena de revision no valida")
        previous_id, latest = str(event[0]), body
        history.append({"id": previous_id, "reviewed_at": payload["reviewed_at"],
            "actor": payload["actor"], "notes": body.notes,
            "rule_checked": body.rule_checked, "procedural_status": body.procedural_status,
            "calendar": body.calendar.model_dump() if body.calendar else None,
            "applies_to_current_source": body.expected_source_sha256 == fingerprint})
    applicable = latest if latest and latest.expected_source_sha256 == fingerprint else None
    calculation = calculate_followup(apply_review(material, applicable))
    if latest and not applicable:
        calculation["review_reasons"].insert(0, "El expediente ha cambiado desde la última revisión; hay que revisarlo de nuevo.")
    if applicable:
        if not applicable.rule_checked:
            calculation["review_reasons"].append("Falta contrastar la regla y el origen del plazo con el expediente.")
        if applicable.procedural_status == "has_changes":
            calculation["review_reasons"].append("Hay incidencias que pueden alterar el plazo; requieren valoración individual antes de confirmar una fecha.")
    return calculation, {"source_sha256": fingerprint, "latest_id": previous_id,
                         "history": list(reversed(history)), "stale": bool(latest and not applicable)}


def save_deadline_review(conn, *, case_id, body, actor):
    from rtm_core.post_filing_deadlines import local_post_filing_projection, calculate_followup, EVENT
    # Caller also applies individual supervisor authentication and case scope.
    conn.execute(text("SELECT id FROM cases WHERE id=:id FOR UPDATE"), {"id": case_id}).one()
    projection = local_post_filing_projection(conn, case_id)
    if projection["status"] != "presented_simulated":
        raise HTTPException(409, "La presentación no permite una revisión verificable")
    current = projection["review"]
    if body.expected_source_sha256 != current["source_sha256"] or body.expected_review_id != current["latest_id"]:
        raise HTTPException(409, "El expediente o su revisión han cambiado. Recarga antes de guardar")
    # Recompute on the server. A client may not choose the date or legal effect.
    payload = conn.execute(text("SELECT payload FROM events WHERE id=:id AND case_id=:case AND type=:kind"),
        {"id": projection["event_id"], "case": case_id, "kind": EVENT}).scalar_one()
    material = _verify_envelope(payload, detail="Presentacion no verificable")
    try:
        calculate_followup(apply_review(material, body))
    except ValueError as exc:
        raise HTTPException(422, "El calendario debe cubrir la referencia y el siguiente día hábil") from exc
    if not actor.startswith("operator:"):
        raise HTTPException(403, "Identidad individual requerida")
    reviewed_at = datetime.now(timezone.utc)
    event_id = str(uuid4())
    envelope = _signed_envelope({"format": REVIEW_VERSION, "case_id": case_id, "synthetic_only": True,
        "source_event_id": projection["event_id"], "actor": actor, "reviewed_at": reviewed_at.isoformat(),
        "review": body.model_dump()})
    conn.execute(text("""
        INSERT INTO events(id,case_id,type,payload,created_at)
        VALUES (:id,:case,:kind,CAST(:payload AS JSONB),:now)
    """), {"id": event_id, "case": case_id, "kind": REVIEW_EVENT,
             "payload": json.dumps(envelope), "now": reviewed_at})
    result = local_post_filing_projection(conn, case_id)
    if result["status"] != "presented_simulated" or result["review"]["latest_id"] != event_id:
        raise RuntimeError("No se puede verificar la revision guardada")
    return result
