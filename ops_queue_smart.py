# ops_queue_smart.py — cola inteligente para operador
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List

from fastapi import APIRouter, HTTPException, Header, Query, Request
from sqlalchemy import text

from database import get_engine
from rtm_core.ops_case_scope import (
    load_ops_case_scope,
    ops_case_scope_filter,
)

router = APIRouter(prefix="/ops", tags=["ops-smart-queue"])

_QUEUE_FAMILY_MAX_LENGTH = 96
_QUEUE_ADMISSIBILITY_MAX_LENGTH = 160
_QUEUE_ADMISSIBILITY_KEYS = (
    "admissibility",
    "admisibilidad",
    "admissibility_panel",
)
_QUEUE_PRIVATE_TEXT_RE = re.compile(
    r"(?:"
    r"(?<![a-z0-9])(?:s3|b2|gs|file|azure|az)://|"
    r"https?://\S+|"
    r"[?&](?:x-amz|x-goog)-(?:credential|signature)=|"
    r"\bbearer\s+[a-z0-9._~+/=-]+|"
    r"\b(?:access[_ -]?token|api[_ -]?key|password|private[_ -]?key|secret|credential)\b|"
    r"(?<![a-z0-9])(?:sk|tok|token|secret|key)[-_][a-z0-9_-]{8,}|"
    r"\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b"
    r")",
    re.IGNORECASE,
)
_QUEUE_OPAQUE_CREDENTIAL_RE = re.compile(
    r"^(?=.{24,}$)(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_+/=.-]+$"
)


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


def _safe_queue_text(value: Any, *, max_length: int) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    raw_candidate = str(value)
    if any(ord(character) < 32 for character in raw_candidate):
        return ""
    candidate = " ".join(raw_candidate.strip().split())
    if not candidate or len(candidate) > max_length:
        return ""
    if _QUEUE_PRIVATE_TEXT_RE.search(candidate):
        return ""
    if _QUEUE_OPAQUE_CREDENTIAL_RE.fullmatch(candidate):
        return ""
    return candidate


def _first_safe_queue_text(*values: Any, max_length: int) -> str:
    for value in values:
        safe = _safe_queue_text(value, max_length=max_length)
        if safe:
            return safe
    return ""


def _individual_queue_family(
    ai_payload: Dict[str, Any],
    classifier: Dict[str, Any],
) -> str:
    return _first_safe_queue_text(
        ai_payload.get("tipo_infraccion"),
        ai_payload.get("familia"),
        classifier.get("family"),
        max_length=_QUEUE_FAMILY_MAX_LENGTH,
    )


def _extract_individual_admissibility_value(
    value: Any,
    *,
    depth: int = 0,
) -> Any:
    if depth > 5:
        return None
    if isinstance(value, dict):
        for key in _QUEUE_ADMISSIBILITY_KEYS:
            if key in value:
                found = _extract_individual_admissibility_value(
                    value[key], depth=depth + 1
                )
                if found is not None:
                    return found
        for child in value.values():
            found = _extract_individual_admissibility_value(
                child, depth=depth + 1
            )
            if found is not None:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for child in value:
            found = _extract_individual_admissibility_value(
                child, depth=depth + 1
            )
            if found is not None:
                return found
        return None
    return value


def _individual_queue_admissibility(ai_payload: Dict[str, Any]) -> str:
    for key in _QUEUE_ADMISSIBILITY_KEYS:
        if key not in ai_payload:
            continue
        candidate = _extract_individual_admissibility_value(ai_payload[key])
        safe = _safe_queue_text(
            candidate,
            max_length=_QUEUE_ADMISSIBILITY_MAX_LENGTH,
        )
        if safe:
            return safe
    return ""


def _queue_item_from_row(
    conn,
    row,
    *,
    include_interested_data: bool = False,
    individual_projection: bool = False,
) -> Dict[str, Any]:
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
            SELECT id, kind, mime, size_bytes, created_at
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
            "mime": r[2],
            "size_bytes": int(r[3] or 0),
            "created_at": r[4],
            "custody": "rtm_internal_only",
            "operator_export_allowed": False,
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
        "confidence": confidence,
        "familia": (
            _individual_queue_family(ai_payload, classifier)
            if individual_projection
            else ai_payload.get("tipo_infraccion")
            or ai_payload.get("familia")
            or classifier.get("family")
            or ""
        ),
        "admisibilidad": (
            _individual_queue_admissibility(ai_payload)
            if individual_projection
            else ai_payload.get("admisibilidad")
            or ai_payload.get("admissibility")
            or ""
        ),
        "has_generated_pdf": has_generated_pdf,
        "has_generated_docx": has_generated_docx,
        "has_authorization_pdf": has_authorization_pdf,
        "has_generation_error": has_generation_error,
        "next_action": next_action,
        "priority_score": priority_score,
    }
    if include_interested_data:
        item["interested_data"] = row[9] if isinstance(row[9], dict) else {}
    return item


@router.get("/queue-smart")
def queue_smart(
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    limit: int = Query(100, ge=1, le=500),
    only_action: Optional[str] = Query(default=None, description="REVISAR | PRESENTAR | FALTA_AUTORIZACION | FALTA_PAGO | REGENERAR | ABRIR"),
):
    _require_operator(x_operator_token)
    scope = load_ops_case_scope(request)
    individual_session = bool(getattr(scope, "individual_session", False))

    engine = get_engine()
    items: List[Dict[str, Any]] = []

    with engine.begin() as conn:
        if not individual_session:
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
                items.append(
                    _queue_item_from_row(
                        conn,
                        row,
                        include_interested_data=True,
                    )
                )
        else:
            scope_sql, scope_params = ops_case_scope_filter(scope)
            # La paginacion profunda corrige el filtro de la nueva superficie
            # individual de staging. El contrato legacy fuera de ella conserva
            # su unica consulta limitada, sin ampliar trabajo ni latencia en
            # otros entornos durante esta primera migracion.
            scan_filtered_pages = bool(only_action)
            page_size = min(500, max(100, limit)) if scan_filtered_pages else limit
            cursor_updated_at = None
            cursor_case_id = None

            while len(items) < limit:
                cursor_sql = ""
                query_params = {**scope_params, "limit": page_size}
                if cursor_updated_at is not None and cursor_case_id is not None:
                    cursor_sql = """
                  AND (
                        COALESCE(c.updated_at, c.created_at, TIMESTAMPTZ 'epoch') < :cursor_updated_at
                        OR (
                            COALESCE(c.updated_at, c.created_at, TIMESTAMPTZ 'epoch') = :cursor_updated_at
                            AND c.id < CAST(:cursor_case_id AS UUID)
                        )
                  )
                """
                    query_params.update(
                        {
                            "cursor_updated_at": cursor_updated_at,
                            "cursor_case_id": cursor_case_id,
                        }
                    )

                rows = conn.execute(
                    text(
                        """
                SELECT
                    c.id,
                    COALESCE(c.status, 'uploaded') AS status,
                    COALESCE(c.payment_status, '') AS payment_status,
                    COALESCE(c.authorized, FALSE) AS authorized,
                    c.contact_email,
                    c.expediente_ref,
                    c.deadline_main,
                    c.created_at,
                    c.updated_at,
                    COALESCE(c.updated_at, c.created_at, TIMESTAMPTZ 'epoch') AS queue_sort_at
                FROM cases c
                WHERE c.status NOT IN ('closed', 'archived')
                  AND """
                    + scope_sql
                    + cursor_sql
                    + """
                ORDER BY
                    COALESCE(c.updated_at, c.created_at, TIMESTAMPTZ 'epoch') DESC,
                    c.id DESC
                LIMIT :limit
                """
                    ),
                    query_params,
                ).fetchall()

                if not rows:
                    break

                for row in rows:
                    item = _queue_item_from_row(
                        conn,
                        row,
                        individual_projection=True,
                    )
                    if not only_action or item.get("next_action") == only_action:
                        items.append(item)
                        if len(items) >= limit:
                            break

                if (
                    len(items) >= limit
                    or len(rows) < page_size
                    or not scan_filtered_pages
                ):
                    break

                cursor_updated_at = rows[-1][9]
                cursor_case_id = str(rows[-1][0])

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
