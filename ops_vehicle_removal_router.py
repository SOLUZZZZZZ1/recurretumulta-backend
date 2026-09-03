# ops_vehicle_removal_router.py
# OPS PRO para la línea "Eliminar coche" de RecurreTuMulta.
# Módulo separado para no tocar el flujo principal de multas.

import json
import os
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from database import get_engine
from rtm_core.ops_case_scope import (
    load_ops_case_scope,
    ops_case_scope_filter,
    require_case_in_scope,
)

router = APIRouter(prefix="/ops/vehicle-removal", tags=["ops-vehicle-removal"])


_PRIVATE_RESPONSE_KEYS = {
    # Coordenadas y accesos de almacenamiento.
    "b2bucket",
    "b2key",
    "bucket",
    "key",
    "objectkey",
    "originalbucket",
    "originalkey",
    "sourcebucket",
    "sourcekey",
    "sourcekeys",
    "storagebucket",
    "storagecoordinates",
    "storagelocator",
    "storagekey",
    "storagepath",
    "internalpath",
    "downloadendpoint",
    "downloadurl",
    "documenturl",
    "providerurl",
    "presignedurl",
    "signedurl",
    "storageurl",
    # Credenciales y material de autenticación.
    "accesstoken",
    "applicationkey",
    "apikey",
    "authorizationheader",
    "bearer",
    "cookie",
    "credential",
    "credentialref",
    "credentials",
    "httpauthorization",
    "password",
    "portalsession",
    "privatekey",
    "secret",
    "sessiontoken",
    "setcookie",
    "storageref",
    "token",
    # Telemetría identificable que no necesita el flujo operativo.
    "cfconnectingip",
    "clientip",
    "clientipaddress",
    "forwardedfor",
    "ip",
    "ipaddress",
    "rawip",
    "rawuseragent",
    "remoteip",
    "sourceip",
    "ua",
    "useragent",
    "useragentsummary",
    "xforwardedfor",
    # Identidad documental no necesaria para gestionar la retirada.
    "documentnumber",
    "dni",
    "dnie",
    "dninie",
    "identitydocument",
    "identitydocumentnumber",
    "nationalid",
    "nie",
    "nif",
    "passport",
    "passportnumber",
    "taxid",
}
_PRIVATE_RESPONSE_SUFFIXES = (
    "accesstoken",
    "apikey",
    "applicationkey",
    "b2key",
    "bucket",
    "credential",
    "credentialref",
    "objectkey",
    "password",
    "portalsession",
    "presignedurl",
    "privatekey",
    "secret",
    "signedurl",
    "storagekey",
    "storageref",
    "token",
)
_PRIVATE_RESPONSE_VALUE = object()
_PRIVATE_URI_RE = re.compile(
    r"^(?:s3|b2|gs|file|azure|az)://",
    re.IGNORECASE,
)
_PRIVATE_STORAGE_URL_RE = re.compile(
    r"^https?://[^\s/]*(?:"
    r"s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com|"
    r"backblazeb2\.com|storage\.googleapis\.com|"
    r"blob\.core\.windows\.net"
    r")(?:/|$)",
    re.IGNORECASE,
)
_PRIVATE_SIGNED_URL_RE = re.compile(
    r"[?&](?:"
    r"x-amz-(?:algorithm|credential|security-token|signature)|"
    r"x-goog-(?:algorithm|credential|signature)|"
    r"x-ms-(?:date|version|signature)|"
    r"signature|credential|access[_-]?token"
    r")=",
    re.IGNORECASE,
)
_PRIVATE_CREDENTIAL_VALUE_RE = re.compile(
    r"(?:^\s*bearer\s+[a-z0-9._~+/=-]+\s*$|"
    r"^\s*(?:sk|tok|token|secret|key)[-_][a-z0-9_-]{8,}\s*$|"
    r"^\s*eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\s*$)",
    re.IGNORECASE,
)


def _response_key(value: Any) -> str:
    return "".join(
        character
        for character in str(value).strip().casefold()
        if character.isalnum()
    )


def _private_response_key(value: Any) -> bool:
    normalized = _response_key(value)
    return normalized in _PRIVATE_RESPONSE_KEYS or any(
        normalized.endswith(suffix)
        for suffix in _PRIVATE_RESPONSE_SUFFIXES
    )


def _private_response_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    return bool(
        _PRIVATE_URI_RE.search(candidate)
        or _PRIVATE_STORAGE_URL_RE.search(candidate)
        or _PRIVATE_SIGNED_URL_RE.search(candidate)
        or _PRIVATE_CREDENTIAL_VALUE_RE.search(candidate)
    )


def _sanitize_vehicle_response_value(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "<truncated>"
    if _private_response_value(value):
        return _PRIVATE_RESPONSE_VALUE
    if isinstance(value, (list, tuple)):
        projected = []
        for item in value:
            child = _sanitize_vehicle_response_value(item, depth + 1)
            if child is not _PRIVATE_RESPONSE_VALUE:
                projected.append(child)
        return projected
    if not isinstance(value, dict):
        return value
    projected = {}
    for key, value_child in value.items():
        if _private_response_key(key):
            continue
        child = _sanitize_vehicle_response_value(value_child, depth + 1)
        if child is _PRIVATE_RESPONSE_VALUE:
            continue
        projected[str(key)] = child
    return projected


def _sanitize_vehicle_response_payload(value: Any, depth: int = 0) -> Any:
    """Retira recursivamente claves y valores privados del payload operativo."""

    projected = _sanitize_vehicle_response_value(value, depth)
    return None if projected is _PRIVATE_RESPONSE_VALUE else projected


def _individual_staging_response(request: Request) -> bool:
    return (
        str(os.getenv("RTM_ENV") or "").strip().casefold() == "staging"
        and getattr(request.state, "rtm_operator_context", None) is not None
    )


def _project_vehicle_response_payload(request: Request, value: Any) -> Any:
    if not _individual_staging_response(request):
        return value
    return _sanitize_vehicle_response_payload(value)


def _env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return value


def _require_operator(x_operator_token: Optional[str]):
    token = (x_operator_token or "").strip()
    expected = _env("OPERATOR_TOKEN")
    if not token or token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized operator")


def _append_event(conn, case_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None):
    conn.execute(
        text(
            """
            INSERT INTO events(case_id, type, payload, created_at)
            VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())
            """
        ),
        {
            "case_id": case_id,
            "type": event_type,
            "payload": json.dumps(payload or {}, ensure_ascii=False),
        },
    )


def _case_or_404(conn, case_id: str):
    row = conn.execute(
        text(
            """
            SELECT id, status, payment_status, contact_email, created_at, updated_at
            FROM cases
            WHERE id = :id
            """
        ),
        {"id": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")

    return {
        "case_id": str(row[0]),
        "status": row[1],
        "payment_status": row[2],
        "contact_email": row[3],
        "created_at": row[4],
        "updated_at": row[5],
    }


def _latest_vehicle_payload(conn, case_id: str) -> Dict[str, Any]:
    row = conn.execute(
        text(
            """
            SELECT payload
            FROM events
            WHERE case_id = :case_id
              AND type IN (
                'vehicle_removal_request_created',
                'vehicle_removal_request',
                'vehicle_removal_paid',
                'vehicle_removal_assigned',
                'vehicle_removal_completed'
              )
            ORDER BY created_at ASC
            """
        ),
        {"case_id": case_id},
    ).fetchall()

    merged: Dict[str, Any] = {}
    for r in row:
        payload = r[0] if isinstance(r[0], dict) else {}
        merged.update(payload)

    return merged


class AssignBody(BaseModel):
    desguace_name: str
    desguace_phone: Optional[str] = None
    desguace_email: Optional[str] = None
    note: Optional[str] = None


class NoteBody(BaseModel):
    note: str


class CompleteBody(BaseModel):
    certificate_ref: Optional[str] = None
    note: Optional[str] = None


@router.get("")
def list_vehicle_removals(
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
    status: str = "all",
    limit: int = 200,
):
    _require_operator(x_operator_token)
    scope = load_ops_case_scope(request)
    scope_sql, scope_params = ops_case_scope_filter(scope)

    allowed_statuses = {
        "all",
        "vehicle_removal_pending_payment",
        "vehicle_removal_paid",
        "vehicle_removal_assigned",
        "vehicle_removal_completed",
        "vehicle_removal_cancelled",
    }
    if status not in allowed_statuses:
        raise HTTPException(status_code=400, detail="Estado no válido")

    limit = max(1, min(int(limit or 200), 500))

    engine = get_engine()
    items = []

    with engine.begin() as conn:
        if status == "all":
            rows = conn.execute(
                text(
                    """
                    SELECT c.id, c.status, c.payment_status, c.contact_email,
                           c.created_at, c.updated_at
                    FROM cases c
                    WHERE (
                        c.category = 'vehicle_removal'
                        OR c.status LIKE 'vehicle_removal%'
                    )
                      AND """ + scope_sql + """
                    ORDER BY c.updated_at DESC
                    LIMIT :limit
                    """
                ),
                {**scope_params, "limit": limit},
            ).fetchall()
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT c.id, c.status, c.payment_status, c.contact_email,
                           c.created_at, c.updated_at
                    FROM cases c
                    WHERE (
                        c.category = 'vehicle_removal'
                        OR c.status LIKE 'vehicle_removal%'
                    )
                      AND c.status = :status
                      AND """ + scope_sql + """
                    ORDER BY c.updated_at DESC
                    LIMIT :limit
                    """
                ),
                {
                    **scope_params,
                    "status": status,
                    "limit": limit,
                },
            ).fetchall()

        for row in rows:
            case_id = str(row[0])
            payload = _project_vehicle_response_payload(
                request,
                _latest_vehicle_payload(conn, case_id),
            )

            items.append(
                {
                    "case_id": case_id,
                    "status": row[1],
                    "payment_status": row[2],
                    "contact_email": row[3],
                    "created_at": row[4],
                    "updated_at": row[5],
                    "name": payload.get("name"),
                    "phone": payload.get("phone"),
                    "email": payload.get("email") or row[3],
                    "plate": payload.get("plate"),
                    "city": payload.get("city"),
                    "notes": payload.get("notes"),
                    "desguace_name": payload.get("desguace_name"),
                    "desguace_phone": payload.get("desguace_phone"),
                    "desguace_email": payload.get("desguace_email"),
                    "certificate_ref": payload.get("certificate_ref"),
                }
            )

    summary = {
        "total": len(items),
        "pending_payment": sum(1 for x in items if x.get("status") == "vehicle_removal_pending_payment"),
        "paid": sum(1 for x in items if x.get("status") == "vehicle_removal_paid"),
        "assigned": sum(1 for x in items if x.get("status") == "vehicle_removal_assigned"),
        "completed": sum(1 for x in items if x.get("status") == "vehicle_removal_completed"),
    }

    return {"ok": True, "status": status, "count": len(items), "summary": summary, "items": items}


@router.get("/{case_id}")
def get_vehicle_removal(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        case = _case_or_404(conn, case_id)
        payload = _latest_vehicle_payload(conn, case_id)

        ev_rows = conn.execute(
            text(
                """
                SELECT type, payload, created_at
                FROM events
                WHERE case_id = :case_id
                ORDER BY created_at DESC
                LIMIT 100
                """
            ),
            {"case_id": case_id},
        ).fetchall()

    response_payload = _project_vehicle_response_payload(request, payload)
    events = [
        {
            "type": row[0],
            "payload": _project_vehicle_response_payload(request, row[1]),
            "created_at": row[2],
        }
        for row in ev_rows
    ]
    response_case = (
        {**response_payload, **case}
        if _individual_staging_response(request)
        else {**case, **response_payload}
    )
    return {
        "ok": True,
        "case": response_case,
        "events": events,
    }


@router.post("/{case_id}/mark-paid")
def mark_vehicle_paid(
    case_id: str,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    """
    Uso opcional de emergencia si el webhook de Stripe no ha actualizado el pago.
    Mantiene auditoría en events.
    """
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        case = _case_or_404(conn, case_id)
        payload = _latest_vehicle_payload(conn, case_id)

        conn.execute(
            text(
                """
                UPDATE cases
                SET status = 'vehicle_removal_paid',
                    payment_status = 'paid',
                    updated_at = NOW()
                WHERE id = :case_id
                """
            ),
            {"case_id": case_id},
        )

        _append_event(
            conn,
            case_id,
            "vehicle_removal_paid",
            {
                **payload,
                "from": case.get("status"),
                "to": "vehicle_removal_paid",
                "source": "operator_manual_mark_paid",
            },
        )

    return {"ok": True, "case_id": case_id, "status": "vehicle_removal_paid", "payment_status": "paid"}


@router.post("/{case_id}/assign")
def assign_vehicle_removal(
    case_id: str,
    body: AssignBody,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        case = _case_or_404(conn, case_id)
        payload = _latest_vehicle_payload(conn, case_id)

        conn.execute(
            text(
                """
                UPDATE cases
                SET status = 'vehicle_removal_assigned',
                    updated_at = NOW()
                WHERE id = :case_id
                """
            ),
            {"case_id": case_id},
        )

        _append_event(
            conn,
            case_id,
            "vehicle_removal_assigned",
            {
                **payload,
                "from": case.get("status"),
                "to": "vehicle_removal_assigned",
                "desguace_name": body.desguace_name.strip(),
                "desguace_phone": (body.desguace_phone or "").strip() or None,
                "desguace_email": (body.desguace_email or "").strip() or None,
                "note": (body.note or "").strip() or None,
            },
        )

    return {"ok": True, "case_id": case_id, "status": "vehicle_removal_assigned"}


@router.post("/{case_id}/complete")
def complete_vehicle_removal(
    case_id: str,
    body: CompleteBody,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        case = _case_or_404(conn, case_id)
        payload = _latest_vehicle_payload(conn, case_id)

        conn.execute(
            text(
                """
                UPDATE cases
                SET status = 'vehicle_removal_completed',
                    updated_at = NOW()
                WHERE id = :case_id
                """
            ),
            {"case_id": case_id},
        )

        _append_event(
            conn,
            case_id,
            "vehicle_removal_completed",
            {
                **payload,
                "from": case.get("status"),
                "to": "vehicle_removal_completed",
                "certificate_ref": (body.certificate_ref or "").strip() or None,
                "note": (body.note or "").strip() or None,
            },
        )

    return {"ok": True, "case_id": case_id, "status": "vehicle_removal_completed"}


@router.post("/{case_id}/note")
def add_vehicle_removal_note(
    case_id: str,
    body: NoteBody,
    request: Request,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    _require_operator(x_operator_token)

    engine = get_engine()
    with engine.begin() as conn:
        scope = load_ops_case_scope(request)
        require_case_in_scope(conn, scope=scope, case_id=case_id)
        _case_or_404(conn, case_id)
        _append_event(conn, case_id, "vehicle_removal_operator_note", {"note": body.note.strip()})

    return {"ok": True, "case_id": case_id}
