"""Deterministic post-filing clocks, initially exposed for the local fine fixture.

A filing clock and the procedure's legal anchor are separate. Reference dates
never imply legal expiry, silence, approval or a change of case status.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import re
from zoneinfo import ZoneInfo

from sqlalchemy import text
from case_authority import _verify_envelope
from rtm_core import local_document_storage as storage
from rtm_core.local_operator_auth import local_operator_auth_requested

VERSION = "rtm_local_post_filing_v1"
EVENT = "rtm_local_submission_simulated"
MADRID = ZoneInfo("Europe/Madrid")
TRAFFIC_LAW = "https://www.boe.es/buscar/act.php?id=BOE-A-2015-11722"
COMPUTATION_LAW = "https://www.boe.es/buscar/act.php?id=BOE-A-2015-10565#a30"


def strict_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Fecha no valida")
    return date.fromisoformat(value)


def add_months(anchor: date, months: int) -> date:
    year, index = divmod(anchor.year * 12 + anchor.month - 1 + months, 12)
    month = index + 1
    return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def calculate_followup(material: dict, *, today: date | None = None) -> dict:
    today = today or datetime.now(MADRID).date()
    submitted = datetime.fromisoformat(material["submitted_at"])
    if submitted.tzinfo is None:
        raise ValueError("La presentacion requiere zona horaria")
    filing_date = submitted.astimezone(MADRID).date()
    if filing_date > today:
        raise ValueError("La presentacion no puede ser futura")
    kind = material["filing_kind"]
    if kind == "traffic_allegations":
        anchor = strict_date(material["procedure_started_on"])
        if anchor > filing_date:
            raise ValueError("La incoacion no puede ser posterior a las alegaciones")
        raw_due = add_months(anchor, 12)
        rule = "Caducidad del procedimiento sancionador de tráfico"
        period = "Un año desde el inicio del procedimiento"
        anchor_label = "Inicio del procedimiento (simulado)"
        basis = TRAFFIC_LAW + "#a112"
        explanation = "Las alegaciones no reinician el plazo de caducidad. No se aplica un plazo general de respuesta de un mes."
    elif kind == "traffic_reposition":
        anchor = strict_date(material["effective_filing_on"])
        if anchor < filing_date or anchor > today:
            raise ValueError("Fecha efectiva de registro no valida")
        raw_due = add_months(anchor, 1)
        rule = "Control de respuesta al recurso de reposición de tráfico"
        period = "Un mes desde la interposición"
        anchor_label = "Fecha efectiva de interposición (simulada)"
        basis = TRAFFIC_LAW + "#a96"
        explanation = "El mes se cuenta de fecha a fecha; no se sustituye por treinta días. El efecto del silencio requiere revisión."
    else:
        raise ValueError("Tipo de escrito sin regla juridica incorporada")
    blockers = []
    due = raw_due
    configured = material.get("calendar")
    if not isinstance(configured, dict) or configured.get("reviewed") is not True:
        blockers.append("Falta verificar el calendario de días inhábiles aplicable al vencimiento.")
    else:
        start, end = strict_date(configured["from"]), strict_date(configured["to"])
        holidays = {strict_date(item) for item in configured["holidays"]}
        if not configured.get("source") or start > raw_due or raw_due > end:
            raise ValueError("El calendario no cubre el vencimiento")
        while due.weekday() >= 5 or due in holidays:
            due += timedelta(days=1)
            if due > end:
                raise ValueError("El calendario no cubre la prorroga al dia habil")
    if material.get("procedural_events_reviewed") is not True:
        blockers.append("Falta contrastar resoluciones, notificaciones, suspensiones y ampliaciones que afecten al cómputo.")
    remaining = (due - today).days
    return {
        "as_of": today.isoformat(), "filing_kind": kind,
        "submitted_at": submitted.isoformat(), "filing_date": filing_date.isoformat(),
        "elapsed_calendar_days": (today - filing_date).days,
        "rule": rule, "period": period, "anchor_label": anchor_label,
        "anchor_on": anchor.isoformat(), "reference_due_on": raw_due.isoformat(),
        "legal_due_on": due.isoformat() if not blockers else None,
        "calculation_status": "requires_review" if blockers else "reviewed_inputs",
        "days_to_reference": (raw_due - today).days,
        "days_to_legal_due": remaining if not blockers else None,
        "reference_reached": today >= raw_due, "review_reasons": blockers,
        "explanation": explanation, "legal_basis_url": basis,
        "computation_basis_url": COMPUTATION_LAW,
        "automatic_legal_consequence": False,
    }


def local_post_filing_projection(conn, case_id: str) -> dict:
    """No state changes. Never promote a synthetic receipt into real submission."""
    pending = {"version": VERSION, "status": "not_presented", "synthetic": False,
               "detail": "El seguimiento comenzará cuando conste una presentación con justificante verificable."}
    if not local_operator_auth_requested():
        return {**pending, "status": "integration_pending",
                "detail": "La conexión del cómputo jurídico con presentaciones reales está pendiente."}
    row = conn.execute(text("SELECT test_mode,interested_data FROM cases WHERE id=:id"), {"id": case_id}).one()
    local_test = (row[1] or {}).get("local_test", {}) if isinstance(row[1], dict) else {}
    if row[0] is not True or local_test.get("fixture") != "rtm_local_fine_fixture_v1":
        return {**pending, "status": "integration_pending",
                "detail": "La prueba del contador está disponible en el expediente ficticio de multa."}
    event = conn.execute(text("""
        SELECT id,payload FROM events WHERE case_id=:id AND type=:kind
        ORDER BY created_at DESC,id DESC LIMIT 1
    """), {"id": case_id, "kind": EVENT}).first()
    if not event:
        return pending
    try:
        material = _verify_envelope(event[1], detail="Presentacion simulada no verificable")
        if (material.get("format") != VERSION or material.get("case_id") != case_id
                or material.get("synthetic_only") is not True
                or material.get("legal_submission_executed") is not False):
            raise ValueError("Evidencia fuera de esta prueba")
        # Verify the actual retained documents, including the explicit fictional
        # initiation date; the original fine's document date is never substituted.
        for prefix, kind in (("receipt", "rtm_local_submission_receipt"),
                             ("initiation", "rtm_local_initiation_notice")):
            doc = conn.execute(text("""
                SELECT b2_bucket,b2_key,sha256,size_bytes,mime FROM documents
                WHERE case_id=:case AND id=CAST(:document AS UUID) AND kind=:kind
            """), {"case": case_id, "document": material[prefix + "_document_id"], "kind": kind}).one()
            data = storage.download_bytes_limited(doc[0], doc[1], max_bytes=1024 * 1024, case_id=case_id)
            digest = hashlib.sha256(data).hexdigest()
            if (doc[4] != "application/pdf" or len(data) != doc[3]
                    or not hmac.compare_digest(digest, doc[2])
                    or not hmac.compare_digest(digest, material[prefix + "_sha256"])):
                raise ValueError("Justificante modificado")
        from rtm_core.post_filing_review import review_projection
        calculation, review = review_projection(conn, case_id, material)
    except Exception:
        return {**pending, "status": "evidence_unverifiable", "synthetic": True,
                "detail": "No se puede verificar la presentación simulada. El contador permanece bloqueado."}
    return {"version": VERSION, "status": "presented_simulated", "synthetic": True,
            "event_id": str(event[0]), "registration_number": material["registration_number"],
            "receipt_document_id": material["receipt_document_id"],
            "receipt_sha256": material["receipt_sha256"], "calculation": calculation, "review": review,
            "detail": "Presentación simulada registrada. No se ha enviado nada a una Administración."}
