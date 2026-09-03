# ops_vehicle_removal_router.py
# OPS PRO para la línea "Eliminar coche" de RecurreTuMulta.
# Módulo separado para no tocar el flujo principal de multas.

import json
import hmac
import os
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
from sqlalchemy import text

from database import get_engine
from rtm_core.ops_case_scope import (
    load_ops_case_scope,
    ops_case_scope_filter,
    require_case_in_scope,
)
from rtm_core.vehicle_removal_contract import (
    VEHICLE_REMOVAL_PREPARATION_CONSENT_SHA256,
    VEHICLE_REMOVAL_PREPARATION_CONSENT_VERSION,
    build_vehicle_removal_preparation_consent,
    vehicle_removal_preparation_consent_is_exact,
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
_PRIVATE_EVENT_KEYS = _PRIVATE_RESPONSE_KEYS | {
    # La identidad se presenta una sola vez desde cases; nunca se replica
    # dentro del historial de eventos devuelto a OPS.
    "authorization",
    "authorizationsnapshot",
    "contactemail",
    "contactname",
    "certificateref",
    "desguaceemail",
    "desguacename",
    "desguacephone",
    "email",
    "fullname",
    "matricula",
    "name",
    "note",
    "notes",
    "paymentintent",
    "phone",
    "plate",
    "sessionid",
    "stripeeventid",
    "stripepaymentintent",
    "stripesessionid",
    "telefono",
}
_OPERATIONAL_EVENT_KEYS = frozenset(
    {
        "from",
        "plate_verification_status",
        "product_code",
        "quote_version",
        "request_contract",
        "service_code",
        "source",
        "status",
        "target_status",
        "to",
    }
)
_EVENT_RESPONSE_KEYS = frozenset(
    {
        "accepted",
        "amount_total",
        "assignment_recorded",
        "authorization_sha256",
        "authorization_version",
        "checkout_contract",
        "checks",
        "completion_recorded",
        "currency",
        "document_sha256",
        "from",
        "human_review_attested",
        "human_review_required",
        "legal_representation",
        "match_method",
        "note_recorded",
        "plate_verification_status",
        "product_code",
        "preparation_consent_sha256",
        "preparation_consent_version",
        "quote_version",
        "request_contract",
        "service_code",
        "settlement_reference_sha256",
        "source",
        "status",
        "target_status",
        "to",
        "verification_version",
    }
)
_EVENT_MACHINE_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
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


def _private_response_key(
    value: Any,
    *,
    private_keys: set[str] = _PRIVATE_RESPONSE_KEYS,
) -> bool:
    normalized = _response_key(value)
    return normalized in private_keys or any(
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


def _sanitize_vehicle_value(
    value: Any,
    depth: int = 0,
    *,
    private_keys: set[str] = _PRIVATE_RESPONSE_KEYS,
) -> Any:
    if depth > 8:
        return "<truncated>"
    if _private_response_value(value):
        return _PRIVATE_RESPONSE_VALUE
    if isinstance(value, (list, tuple)):
        projected = []
        for item in value:
            child = _sanitize_vehicle_value(
                item,
                depth + 1,
                private_keys=private_keys,
            )
            if child is not _PRIVATE_RESPONSE_VALUE:
                projected.append(child)
        return projected
    if not isinstance(value, dict):
        return value
    projected = {}
    for key, value_child in value.items():
        if _private_response_key(key, private_keys=private_keys):
            continue
        child = _sanitize_vehicle_value(
            value_child,
            depth + 1,
            private_keys=private_keys,
        )
        if child is _PRIVATE_RESPONSE_VALUE:
            continue
        projected[str(key)] = child
    return projected


def _sanitize_vehicle_response_payload(value: Any, depth: int = 0) -> Any:
    """Retira recursivamente claves y valores privados del payload operativo."""

    projected = _sanitize_vehicle_value(value, depth)
    return None if projected is _PRIVATE_RESPONSE_VALUE else projected


def _sanitize_vehicle_event_payload(value: Any) -> Any:
    """Allowlist de auditoría sin identidad, telemetría ni IDs del proveedor."""

    payload = value if isinstance(value, dict) else {}
    allowed: Dict[str, Any] = {}
    for key, child in payload.items():
        if key not in _EVENT_RESPONSE_KEYS:
            continue
        if key == "amount_total":
            if isinstance(child, int) and not isinstance(child, bool) and 0 < child <= 100_000_000:
                allowed[key] = child
            continue
        if key in {
            "accepted",
            "assignment_recorded",
            "completion_recorded",
            "note_recorded",
        }:
            if isinstance(child, bool):
                allowed[key] = child
            continue
        if key == "checks":
            allowed[key] = child
            continue
        if isinstance(child, str) and _EVENT_MACHINE_VALUE_RE.fullmatch(child):
            allowed[key] = child

    checks = allowed.get("checks")
    if isinstance(checks, dict):
        allowed["checks"] = {
            key: child
            for key, child in checks.items()
            if key
            in {
                "data_truthfulness",
                "human_review_required",
                "titular_or_authorized",
                "vehicle_removal_authorization",
            }
            and isinstance(child, bool)
        }
    projected = _sanitize_vehicle_value(
        allowed,
        private_keys=_PRIVATE_EVENT_KEYS,
    )
    return None if projected is _PRIVATE_RESPONSE_VALUE else projected


def _project_vehicle_response_payload(request: Request, value: Any) -> Any:
    del request
    return _sanitize_vehicle_response_payload(value)


def _env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return value


def _require_operator(x_operator_token: Optional[str]):
    token = (x_operator_token or "").strip()
    expected = _env("OPERATOR_TOKEN")
    if not token or not hmac.compare_digest(token, expected):
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


def _payload_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _case_operational_projection(
    *,
    contact_email: Any,
    contact_name: Any,
    interested_data: Any,
) -> Dict[str, Any]:
    interested = _payload_dict(interested_data)

    def _value(*keys: str, fallback: Any = None) -> Optional[str]:
        selected = next(
            (interested.get(key) for key in keys if interested.get(key)),
            fallback,
        )
        return str(selected or "").strip() or None

    consent = interested.get("vehicle_removal_preparation_consent")
    consent_verified = consent == build_vehicle_removal_preparation_consent()

    return {
        "contact_email": str(contact_email or "").strip() or None,
        "name": _value("full_name", fallback=contact_name),
        "phone": _value("telefono", "phone"),
        "plate": _value("matricula", "plate"),
        "city": _value("vehicle_removal_city"),
        "notes": _value("vehicle_removal_notes"),
        "assignment_note": _value("vehicle_removal_assignment_note"),
        "completion_note": _value("vehicle_removal_completion_note"),
        "operator_note": _value("vehicle_removal_operator_note"),
        "desguace_name": _value("vehicle_removal_desguace_name"),
        "desguace_phone": _value("vehicle_removal_desguace_phone"),
        "desguace_email": _value("vehicle_removal_desguace_email"),
        "certificate_ref": _value("vehicle_removal_certificate_ref"),
        "vehicle_preparation_consent": consent_verified,
        # Nombres wire-v3 conservados mientras el cliente migra; solo se
        # proyectan si el marcador específico de preparación es exacto.
        "authorization_version": (
            VEHICLE_REMOVAL_PREPARATION_CONSENT_VERSION
            if consent_verified
            else None
        ),
        "authorization_sha256": (
            VEHICLE_REMOVAL_PREPARATION_CONSENT_SHA256
            if consent_verified
            else None
        ),
    }


def _case_or_404(conn, case_id: str, *, for_update: bool = False):
    lock_clause = " FOR UPDATE" if for_update else ""
    row = conn.execute(
        text(
            """
            SELECT id, status, payment_status, contact_email, contact_name,
                   COALESCE(interested_data, '{}'::jsonb) AS interested_data,
                   created_at, updated_at
            FROM cases
            WHERE id = :id
              AND COALESCE(department, '') = 'traffic'
              AND COALESCE(case_type, '') = 'vehicle_removal'
              AND COALESCE(category, '') = 'vehicle_removal'
            """
            + lock_clause
        ),
        {"id": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")

    return {
        "case_id": str(row[0]),
        "status": row[1],
        "payment_status": row[2],
        "created_at": row[6],
        "updated_at": row[7],
        **_case_operational_projection(
            contact_email=row[3],
            contact_name=row[4],
            interested_data=row[5],
        ),
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
        payload = _sanitize_vehicle_event_payload(_payload_dict(r[0]))
        merged.update(
            {
                key: value
                for key, value in payload.items()
                if key in _OPERATIONAL_EVENT_KEYS
            }
        )

    return merged


class _StrictVehicleOpsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AssignBody(_StrictVehicleOpsInput):
    desguace_name: str = Field(min_length=1, max_length=160)
    desguace_phone: Optional[str] = Field(default=None, max_length=40)
    desguace_email: Optional[EmailStr] = Field(default=None, max_length=254)
    note: Optional[str] = Field(default=None, max_length=4000)
    human_review_attested: bool
    authorization_version: str = Field(min_length=1, max_length=80)
    authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_human_review_attestation(self):
        if not self.human_review_attested or not (
            vehicle_removal_preparation_consent_is_exact(
                self.authorization_version,
                self.authorization_sha256,
            )
        ):
            raise ValueError("La revisión humana no acredita el consentimiento vigente")
        return self


class NoteBody(_StrictVehicleOpsInput):
    note: str = Field(min_length=1, max_length=4000)


class CompleteBody(_StrictVehicleOpsInput):
    certificate_ref: Optional[str] = Field(default=None, max_length=200)
    note: Optional[str] = Field(default=None, max_length=4000)


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
                           c.contact_name,
                           COALESCE(c.interested_data, '{}'::jsonb),
                           c.created_at, c.updated_at
                    FROM cases c
                    WHERE c.category = 'vehicle_removal'
                      AND c.case_type = 'vehicle_removal'
                      AND c.department = 'traffic'
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
                           c.contact_name,
                           COALESCE(c.interested_data, '{}'::jsonb),
                           c.created_at, c.updated_at
                    FROM cases c
                    WHERE c.category = 'vehicle_removal'
                      AND c.case_type = 'vehicle_removal'
                      AND c.department = 'traffic'
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
            case_projection = _case_operational_projection(
                contact_email=row[3],
                contact_name=row[4],
                interested_data=row[5],
            )
            payload = _project_vehicle_response_payload(
                request,
                _latest_vehicle_payload(conn, case_id),
            )

            items.append(
                {
                    "case_id": case_id,
                    "status": row[1],
                    "payment_status": row[2],
                    "created_at": row[6],
                    "updated_at": row[7],
                    **{
                        key: case_projection.get(key) or payload.get(key)
                        for key in case_projection
                    },
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
            "payload": _sanitize_vehicle_event_payload(row[1]),
            "created_at": row[2],
        }
        for row in ev_rows
    ]
    response_case = {
        **response_payload,
        **{key: value for key, value in case.items() if value is not None},
    }
    for authoritative_key in (
        "case_id",
        "status",
        "payment_status",
        "created_at",
        "updated_at",
    ):
        response_case[authoritative_key] = case.get(authoritative_key)
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
    """Retirado: solo el webhook Stripe verificado puede acreditar el pago."""

    del case_id, request
    _require_operator(x_operator_token)
    raise HTTPException(
        status_code=410,
        detail=(
            "Marcado manual de pago retirado; "
            "la conciliación depende del webhook verificado"
        )
    )


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
        case = _case_or_404(conn, case_id, for_update=True)
        if (
            str(case.get("payment_status") or "").strip().casefold() != "paid"
            or case.get("status") != "vehicle_removal_paid"
            or case.get("vehicle_preparation_consent") is not True
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "La retirada no reúne pago, estado y consentimiento de "
                    "preparación verificables"
                ),
            )
        updated = conn.execute(
            text(
                """
                UPDATE cases
                SET status = 'vehicle_removal_assigned',
                    interested_data=(
                        COALESCE(interested_data, '{}'::jsonb)
                        || jsonb_build_object(
                            'vehicle_removal_desguace_name',
                                CAST(:desguace_name AS text),
                            'vehicle_removal_desguace_phone',
                                CAST(:desguace_phone AS text),
                            'vehicle_removal_desguace_email',
                                CAST(:desguace_email AS text),
                            'vehicle_removal_assignment_note',
                                CAST(:note AS text)
                        )
                    ),
                    updated_at = NOW()
                WHERE id = :case_id
                  AND payment_status = 'paid'
                  AND status = 'vehicle_removal_paid'
                  AND COALESCE(interested_data, '{}'::jsonb)
                      -> 'vehicle_removal_preparation_consent'
                      = CAST(:preparation_consent AS JSONB)
                RETURNING id
                """
            ),
            {
                "case_id": case_id,
                "desguace_name": body.desguace_name.strip(),
                "desguace_phone": (body.desguace_phone or "").strip() or None,
                "desguace_email": (body.desguace_email or "").strip() or None,
                "note": (body.note or "").strip() or None,
                "preparation_consent": json.dumps(
                    build_vehicle_removal_preparation_consent(),
                    ensure_ascii=False,
                ),
            },
        ).fetchone()
        if not updated:
            raise HTTPException(
                status_code=409,
                detail="La retirada cambió durante la asignación",
            )

        _append_event(
            conn,
            case_id,
            "vehicle_removal_assigned",
            {
                "from": case.get("status"),
                "to": "vehicle_removal_assigned",
                "assignment_recorded": True,
                "human_review_attested": True,
                "preparation_consent_version": body.authorization_version,
                "preparation_consent_sha256": body.authorization_sha256,
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
        case = _case_or_404(conn, case_id, for_update=True)
        if (
            str(case.get("payment_status") or "").strip().casefold() != "paid"
            or case.get("status") != "vehicle_removal_assigned"
        ):
            raise HTTPException(
                status_code=409,
                detail="La retirada debe estar pagada y asignada antes de completarse",
            )
        updated = conn.execute(
            text(
                """
                UPDATE cases
                SET status = 'vehicle_removal_completed',
                    interested_data=(
                        COALESCE(interested_data, '{}'::jsonb)
                        || jsonb_build_object(
                            'vehicle_removal_certificate_ref',
                                CAST(:certificate_ref AS text),
                            'vehicle_removal_completion_note',
                                CAST(:note AS text)
                        )
                    ),
                    updated_at = NOW()
                WHERE id = :case_id
                  AND payment_status = 'paid'
                  AND status = 'vehicle_removal_assigned'
                RETURNING id
                """
            ),
            {
                "case_id": case_id,
                "certificate_ref": (body.certificate_ref or "").strip() or None,
                "note": (body.note or "").strip() or None,
            },
        ).fetchone()
        if not updated:
            raise HTTPException(
                status_code=409,
                detail="La retirada cambió durante el cierre",
            )

        _append_event(
            conn,
            case_id,
            "vehicle_removal_completed",
            {
                "from": case.get("status"),
                "to": "vehicle_removal_completed",
                "completion_recorded": True,
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
        _case_or_404(conn, case_id, for_update=True)
        updated = conn.execute(
            text(
                """
                UPDATE cases
                SET interested_data=(
                        COALESCE(interested_data, '{}'::jsonb)
                        || jsonb_build_object(
                            'vehicle_removal_operator_note', CAST(:note AS text)
                        )
                    ),
                    updated_at=NOW()
                WHERE id=:case_id
                RETURNING id
                """
            ),
            {"case_id": case_id, "note": body.note.strip()},
        ).fetchone()
        if not updated:
            raise HTTPException(
                status_code=409,
                detail="La retirada cambió durante el registro de la nota",
            )
        _append_event(
            conn,
            case_id,
            "vehicle_removal_operator_note_recorded",
            {"note_recorded": True},
        )

    return {"ok": True, "case_id": case_id}
