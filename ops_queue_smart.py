# ops_queue_smart.py — cola inteligente para operador
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List

from fastapi import APIRouter, HTTPException, Header, Query
from sqlalchemy import text

from database import get_engine

router = APIRouter(prefix="/ops", tags=["ops-smart-queue"])


def _env(name: str) -> str:
    v = (os.getenv(name) or "").strip()
    if not v:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return v


def _require_operator(x_operator_token: Optional[str]):
    token = (x_operator_token or "").strip()
    expected = _env("OPERATOR_TOKEN")
    if not token or token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized operator")


def _to_dt(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        txt = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(txt)
    except Exception:
        try:
            return datetime.fromisoformat(str(value)[:19])
        except Exception:
            return None


def _days_until(value) -> Optional[int]:
    dt = _to_dt(value)
    if not dt:
        return None
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = dt - now
    return int(delta.total_seconds() // 86400)


def _safe_confidence(value) -> Optional[float]:
    if value in (None, "", [], {}):
        return None
    try:
        num = float(str(value).replace(",", "."))
        if num > 1:
            num = num / 100.0
        if num < 0:
            num = 0.0
        if num > 1:
            num = 1.0
        return round(num, 4)
    except Exception:
        return None


def _human_next_action(
    *,
    authorized: bool,
    payment_status: str,
    confidence: Optional[float],
    has_generated_pdf: bool,
    has_generated_docx: bool,
    status: str,
) -> str:
    if not authorized:
        return "FALTA_AUTORIZACION"
    if payment_status != "paid":
        return "FALTA_PAGO"
    if confidence is None or confidence < 0.80:
        return "REVISAR"
    if not has_generated_pdf or not has_generated_docx:
        return "REGENERAR"
    if status == "ready_to_submit":
        return "PRESENTAR"
    if status == "submitted":
        return "YA_ENVIADO"
    return "ABRIR"


def _priority_score(
    *,
    status: str,
    confidence: Optional[float],
    has_generation_error: bool,
    has_generated_pdf: bool,
    has_generated_docx: bool,
    days_to_deadline: Optional[int],
) -> int:
    score = 0
    if status == "manual_review":
        score += 100
    elif status == "ready_to_submit":
        score += 80
    elif status == "generated":
        score += 60
    elif status in {"pending_review", "uploaded"}:
        score += 40
    elif status == "submitted":
        score += 10

    if confidence is None:
        score += 20
    elif confidence < 0.80:
        score += 30
    elif confidence < 0.90:
        score += 10

    if has_generation_error:
        score += 25
    if not has_generated_pdf:
        score += 10
    if not has_generated_docx:
        score += 8

    if days_to_deadline is not None:
        if days_to_deadline < 0:
            score += 40
        elif days_to_deadline <= 1:
            score += 35
        elif days_to_deadline <= 3:
            score += 25
        elif days_to_deadline <= 7:
            score += 15
        elif days_to_deadline <= 15:
            score += 8

    return score


def _extract_ai_payload(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    ai_events = [e for e in events if e.get("type") == "ai_expediente_result"]
    ai_events.sort(key=lambda e: str(e.get("created_at") or ""), reverse=True)
    if not ai_events:
        return {}
    payload = ai_events[0].get("payload") or {}
    return payload if isinstance(payload, dict) else {}


def _extract_deadline(events: List[Dict[str, Any]], ai_payload: Dict[str, Any], deadline_main) -> Optional[str]:
    deadlines = ai_payload.get("deadlines") if isinstance(ai_payload, dict) else None
    if isinstance(deadlines, dict) and deadlines.get("before_resource_deadline"):
        return str(deadlines.get("before_resource_deadline"))
    if deadline_main:
        return str(deadline_main)

    for ev in events:
        payload = ev.get("payload") or {}
        if isinstance(payload, dict) and payload.get("before_resource_deadline"):
            return str(payload.get("before_resource_deadline"))
    return None


def _bool_has_kind(documents: List[Dict[str, Any]], needles: List[str]) -> bool:
    for d in documents:
        kind = str(d.get("kind") or "").lower()
        if any(n in kind for n in needles):
            return True
    return False


@router.get("/queue-smart")
def queue_smart(
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    limit: int = Query(100, ge=1, le=500),
    only_action: Optional[str] = Query(default=None, description="REVISAR | PRESENTAR | FALTA_AUTORIZACION | FALTA_PAGO | REGENERAR | ABRIR"),
):
    _require_operator(x_operator_token)

    engine = get_engine()
    items: List[Dict[str, Any]] = []

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                '''
                SELECT
                    id,
                    COALESCE(status, 'uploaded') AS status,
                    COALESCE(payment_status, '') AS payment_status,
                    COALESCE(authorized, FALSE) AS authorized,
                    contact_email,
                    expediente_ref,
                    deadline_main,
                    created_at,
                    updated_at,
                    COALESCE(interested_data, '{}'::jsonb) AS interested_data
                FROM cases
                WHERE status NOT IN ('closed', 'archived')
                ORDER BY updated_at DESC
                LIMIT :limit
                '''
            ),
            {"limit": limit},
        ).fetchall()

        for row in rows:
            case_id = str(row[0])

            ev_rows = conn.execute(
                text(
                    '''
                    SELECT type, payload, created_at
                    FROM events
                    WHERE case_id = :case_id
                    ORDER BY created_at DESC
                    LIMIT 100
                    '''
                ),
                {"case_id": case_id},
            ).fetchall()

            doc_rows = conn.execute(
                text(
                    '''
                    SELECT id, kind, b2_bucket, b2_key, mime, size_bytes, created_at
                    FROM documents
                    WHERE case_id = :case_id
                    ORDER BY created_at DESC
                    '''
                ),
                {"case_id": case_id},
            ).fetchall()

            events = [{"type": r[0], "payload": r[1], "created_at": r[2]} for r in ev_rows]
            documents = [
                {
                    "id": str(r[0]),
                    "kind": r[1],
                    "bucket": r[2],
                    "key": r[3],
                    "mime": r[4],
                    "size_bytes": int(r[5] or 0),
                    "created_at": r[6],
                }
                for r in doc_rows
            ]

            ai_payload = _extract_ai_payload(events)
            classifier = ai_payload.get("classifier_result") if isinstance(ai_payload.get("classifier_result"), dict) else {}
            confidence = _safe_confidence(
                classifier.get("confidence")
                or ai_payload.get("tipo_infraccion_confidence")
                or ai_payload.get("confianza")
            )

            has_generated_pdf = _bool_has_kind(documents, ["generated_pdf", "pdf"])
            has_generated_docx = _bool_has_kind(documents, ["generated_docx", "docx"])
            has_authorization_pdf = _bool_has_kind(documents, ["autorizacion_cliente_pdf", "autorizacion"])
            has_generation_error = any(e.get("type") == "resource_generation_failed" for e in events)

            deadline_value = _extract_deadline(events, ai_payload, row[6])
            days_to_deadline = _days_until(deadline_value)

            next_action = _human_next_action(
                authorized=bool(row[3]),
                payment_status=(row[2] or ""),
                confidence=confidence,
                has_generated_pdf=has_generated_pdf,
                has_generated_docx=has_generated_docx,
                status=(row[1] or ""),
            )

            priority_score = _priority_score(
                status=(row[1] or ""),
                confidence=confidence,
                has_generation_error=has_generation_error,
                has_generated_pdf=has_generated_pdf,
                has_generated_docx=has_generated_docx,
                days_to_deadline=days_to_deadline,
            )

            item = {
                "case_id": case_id,
                "status": row[1] or "uploaded",
                "payment_status": row[2] or "",
                "authorized": bool(row[3]),
                "contact_email": row[4],
                "expediente_ref": row[5],
                "deadline_main": row[6],
                "days_to_deadline": days_to_deadline,
                "created_at": row[7],
                "updated_at": row[8],
                "interested_data": row[9] if isinstance(row[9], dict) else {},
                "confidence": confidence,
                "familia": ai_payload.get("tipo_infraccion")
                or ai_payload.get("familia")
                or classifier.get("family")
                or "",
                "admisibilidad": ai_payload.get("admisibilidad")
                or ai_payload.get("admissibility")
                or "",
                "has_generated_pdf": has_generated_pdf,
                "has_generated_docx": has_generated_docx,
                "has_authorization_pdf": has_authorization_pdf,
                "has_generation_error": has_generation_error,
                "next_action": next_action,
                "priority_score": priority_score,
            }
            items.append(item)

    if only_action:
        items = [x for x in items if x.get("next_action") == only_action]

    items.sort(
        key=lambda x: (
            -int(x.get("priority_score") or 0),
            x.get("days_to_deadline") if x.get("days_to_deadline") is not None else 999999,
            str(x.get("created_at") or ""),
        )
    )

    summary = {
        "review": sum(1 for x in items if x.get("next_action") == "REVISAR"),
        "submit": sum(1 for x in items if x.get("next_action") == "PRESENTAR"),
        "blocked": sum(1 for x in items if x.get("next_action") in {"FALTA_AUTORIZACION", "FALTA_PAGO"}),
        "regenerate": sum(1 for x in items if x.get("next_action") == "REGENERAR"),
    }

    return {
        "ok": True,
        "count": len(items),
        "summary": summary,
        "items": items,
    }
