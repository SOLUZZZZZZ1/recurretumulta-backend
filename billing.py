"""Cobros RTM: tarifa autoritativa, expediente mínimo y activación de revisión.

La revisión inicial se decide exclusivamente desde el expediente persistido.
El navegador no puede elegir la tarifa y el pago no ejecuta clasificadores,
especialistas ni Generate.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from urllib.parse import urlsplit
from uuid import UUID

import stripe
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import text

from case_authority import verify_signed_case_authority
from database import get_engine
from public_case_access import require_case_access_token, require_case_or_operator_access
from rtm_core.repository import build_case_review_readiness, load_case_review_snapshot
from rtm_core.runtime_capabilities import require_http_capability
from rtm_core.case_state_policy import TERMINAL_CASE_STATUSES
from rtm_core.service_catalog import normalize_code
from rtm_core.trusted_origins import trusted_frontend_origin
from rtm_core.vehicle_removal_contract import (
    VEHICLE_REMOVAL_AMOUNT_CENTS as _VEHICLE_REMOVAL_AMOUNT_CENTS,
    VEHICLE_REMOVAL_CHECKOUT_CONTRACT as _VEHICLE_REMOVAL_CHECKOUT_CONTRACT,
    VEHICLE_REMOVAL_CURRENCY as _VEHICLE_REMOVAL_CURRENCY,
    VEHICLE_REMOVAL_INTENT_KEYS as _VEHICLE_REMOVAL_INTENT_KEYS,
    VEHICLE_REMOVAL_METADATA_KEYS as _VEHICLE_REMOVAL_METADATA_KEYS,
    VEHICLE_REMOVAL_PRODUCT_CODE as _VEHICLE_REMOVAL_PRODUCT_CODE,
    VEHICLE_REMOVAL_QUOTE_VERSION as _VEHICLE_REMOVAL_QUOTE_VERSION,
    VEHICLE_REMOVAL_SERVICE_CODE as _VEHICLE_REMOVAL_SERVICE_CODE,
    is_exact_vehicle_removal_stripe_metadata,
)


router = APIRouter(tags=["billing"])

_REVIEW_STAGES = {"review", "revision", "initial", "inicial", "revision_inicial"}
_FINAL_STAGES = {"final", "gestion", "management"}
_STRIPE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_]{4,255}$")
_CHECKOUT_CLAIM_PREFIX = "rtm_claim_"
_CHECKOUT_CREATING_PAYMENT_STATUS = "creating"
_CHECKOUT_RECONCILIATION_PAYMENT_STATUSES = {
    "disputed",
    "failed",
    "refunded",
}
_MAX_STRIPE_WEBHOOK_BYTES = 1024 * 1024
_CHECKOUT_SETTLEMENT_EVENTS = {
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
}
_PAYMENT_REVERSAL_EVENTS = {
    "charge.refunded": "refunded",
    "refund.created": "refunded",
    "refund.updated": "refunded",
    "charge.dispute.created": "disputed",
    "charge.dispute.updated": "disputed",
    "charge.dispute.closed": "disputed",
    "charge.dispute.funds_withdrawn": "disputed",
}


def _env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return value


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    case_id: str = Field(min_length=1, max_length=64)
    product: str | None = Field(default=None, max_length=96)
    email: EmailStr | None = Field(default=None, max_length=254)
    locale: str | None = Field(default="es", max_length=16)
    payment_stage: str | None = Field(default="review", max_length=32)


def _normalized_stage(value: str | None) -> str:
    return normalize_code(value) or "review"


def _normalized_currency(value: object) -> str:
    return str(value or "").strip().upper()


def _object_value(value: object, key: str, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    getter = getattr(value, "get", None)
    if callable(getter):
        return getter(key, default)
    return getattr(value, key, default)


def _stripe_object_id(value: object) -> str:
    nested_id = _object_value(value, "id")
    if nested_id not in (None, ""):
        return str(nested_id).strip()
    return str(value or "").strip()


def _payload_dict(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _valid_stripe_id(value: str, prefix: str) -> bool:
    return bool(value.startswith(prefix) and _STRIPE_ID_PATTERN.fullmatch(value))


def _canonical_case_uuid(value: object) -> str:
    candidate = str(value or "")
    try:
        parsed = UUID(candidate)
    except (ValueError, TypeError, AttributeError):
        return ""
    canonical = str(parsed)
    return canonical if candidate == canonical else ""


def _vehicle_removal_metadata_marker(metadata: dict) -> bool:
    """Detecta también intentos parciales para que nunca caigan al flujo legacy."""

    if "checkout_contract" in metadata:
        return True
    return (
        normalize_code(metadata.get("service_code"))
        == _VEHICLE_REMOVAL_SERVICE_CODE
        or normalize_code(metadata.get("product_code"))
        == normalize_code(_VEHICLE_REMOVAL_PRODUCT_CODE)
    )


def _vehicle_removal_case_value(row, index: int, key: str):
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    if isinstance(row, dict):
        return row.get(key)
    return row[index]


def _settle_vehicle_removal_checkout(
    *,
    metadata: dict,
    event_id: str,
    session_id: str,
    payment_intent: str,
    session_mode: str,
    session_payment_status: str,
    session_amount: int,
    session_currency: str,
) -> dict:
    """Concilia el checkout v3 sin otorgar autoridad al retorno del navegador."""

    if (
        not is_exact_vehicle_removal_stripe_metadata(metadata)
    ):
        raise HTTPException(status_code=400, detail="Contrato de checkout no válido")

    case_id = _canonical_case_uuid(metadata.get("case_id"))
    if (
        not case_id
        or not _valid_stripe_id(event_id, "evt_")
        or not _valid_stripe_id(session_id, "cs_")
        or not _valid_stripe_id(payment_intent, "pi_")
    ):
        raise HTTPException(status_code=400, detail="Webhook sin identificadores válidos")
    if (
        session_mode != "payment"
        or session_payment_status != "paid"
        or session_amount != _VEHICLE_REMOVAL_AMOUNT_CENTS
        or session_currency != _VEHICLE_REMOVAL_CURRENCY
    ):
        raise HTTPException(
            status_code=409,
            detail="La sesión no acredita una liquidación válida",
        )

    engine = get_engine()
    with engine.begin() as conn:
        case_row = conn.execute(
            text(
                """
                SELECT payment_status, stripe_session_id, product_code,
                       category, case_type, status, stripe_payment_intent,
                       department
                FROM cases
                WHERE id=:id
                FOR UPDATE
                """
            ),
            {"id": case_id},
        ).fetchone()
        if not case_row:
            raise HTTPException(status_code=404, detail="Expediente no encontrado")

        payment_status = str(
            _vehicle_removal_case_value(case_row, 0, "payment_status") or ""
        ).strip().lower()
        stored_session_id = str(
            _vehicle_removal_case_value(case_row, 1, "stripe_session_id") or ""
        ).strip()
        product_code = str(
            _vehicle_removal_case_value(case_row, 2, "product_code") or ""
        ).strip()
        category = str(
            _vehicle_removal_case_value(case_row, 3, "category") or ""
        ).strip()
        case_type = str(
            _vehicle_removal_case_value(case_row, 4, "case_type") or ""
        ).strip()
        case_status = str(
            _vehicle_removal_case_value(case_row, 5, "status") or ""
        ).strip()
        stored_payment_intent = str(
            _vehicle_removal_case_value(case_row, 6, "stripe_payment_intent") or ""
        ).strip()
        department = str(
            _vehicle_removal_case_value(case_row, 7, "department") or ""
        ).strip()

        if (
            category != _VEHICLE_REMOVAL_SERVICE_CODE
            or case_type != _VEHICLE_REMOVAL_SERVICE_CODE
            or department != "traffic"
            or product_code != _VEHICLE_REMOVAL_PRODUCT_CODE
            or stored_session_id != session_id
        ):
            raise HTTPException(
                status_code=409,
                detail="La liquidación no coincide con el expediente",
            )

        intent_row = conn.execute(
            text(
                """
                SELECT payload
                FROM events
                WHERE case_id=:id
                  AND type='vehicle_removal_checkout_session_created'
                  AND payload->>'session_id'=:session_id
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"id": case_id, "session_id": session_id},
        ).fetchone()
        intent = _payload_dict(intent_row[0] if intent_row else None)
        try:
            intended_amount = int(intent.get("amount_total"))
        except (TypeError, ValueError):
            intended_amount = 0
        if (
            set(intent) != _VEHICLE_REMOVAL_INTENT_KEYS
            or intent.get("session_id") != session_id
            or intended_amount != _VEHICLE_REMOVAL_AMOUNT_CENTS
            or intended_amount != session_amount
            or intent.get("currency") != _VEHICLE_REMOVAL_CURRENCY
            or session_currency != intent.get("currency")
            or intent.get("service_code") != _VEHICLE_REMOVAL_SERVICE_CODE
            or intent.get("product_code") != _VEHICLE_REMOVAL_PRODUCT_CODE
            or intent.get("checkout_contract")
            != _VEHICLE_REMOVAL_CHECKOUT_CONTRACT
            or intent.get("quote_version") != _VEHICLE_REMOVAL_QUOTE_VERSION
        ):
            raise HTTPException(
                status_code=409,
                detail="La liquidación no coincide con la intención guardada",
            )

        if payment_status == "paid":
            if (
                stored_payment_intent != payment_intent
                or case_status
                not in {
                    "vehicle_removal_paid",
                    "vehicle_removal_assigned",
                    "vehicle_removal_completed",
                }
            ):
                raise HTTPException(
                    status_code=409,
                    detail="La repetición no coincide con la liquidación guardada",
                )
            return {
                "ok": True,
                "replayed": True,
                "case_id": case_id,
            }

        if payment_status in _CHECKOUT_RECONCILIATION_PAYMENT_STATUSES:
            return {
                "ok": True,
                "reconciled": True,
                "case_id": case_id,
            }
        if payment_status != "pending" or case_status != "vehicle_removal_pending_payment":
            raise HTTPException(
                status_code=409,
                detail="El expediente no está pendiente de esta liquidación",
            )

        updated = conn.execute(
            text(
                """
                UPDATE cases
                SET payment_status='paid',
                    paid_at=NOW(),
                    stripe_payment_intent=:payment_intent,
                    status='vehicle_removal_paid',
                    updated_at=NOW()
                WHERE id=:id
                  AND stripe_session_id=:session_id
                  AND product_code='ELIMINAR_COCHE'
                  AND category='vehicle_removal'
                  AND case_type='vehicle_removal'
                  AND department='traffic'
                  AND status='vehicle_removal_pending_payment'
                  AND payment_status IS DISTINCT FROM 'paid'
                RETURNING id
                """
            ),
            {
                "id": case_id,
                "session_id": session_id,
                "payment_intent": payment_intent,
            },
        ).fetchone()
        if not updated:
            raise HTTPException(status_code=409, detail="Liquidación concurrente en conflicto")

        _append_event(
            conn,
            case_id,
            "vehicle_removal_payment_confirmed",
            {
                "settlement_reference_sha256": hashlib.sha256(
                    "\x00".join(
                        (event_id, session_id, payment_intent)
                    ).encode("utf-8")
                ).hexdigest(),
                "amount_total": session_amount,
                "currency": session_currency,
                "service_code": _VEHICLE_REMOVAL_SERVICE_CODE,
                "product_code": _VEHICLE_REMOVAL_PRODUCT_CODE,
                "checkout_contract": _VEHICLE_REMOVAL_CHECKOUT_CONTRACT,
                "quote_version": _VEHICLE_REMOVAL_QUOTE_VERSION,
            },
        )

    return {
        "ok": True,
        "processed": True,
        "case_id": case_id,
    }


def _append_event(conn, case_id: str, event_type: str, payload: dict) -> None:
    conn.execute(
        text(
            """
            INSERT INTO events(case_id, type, payload, created_at)
            VALUES (:id, :type, CAST(:payload AS JSONB), NOW())
            """
        ),
        {
            "id": case_id,
            "type": event_type,
            "payload": json.dumps(payload, ensure_ascii=False),
        },
    )


def _review_product(readiness) -> dict:
    quote = readiness.quote
    return {
        "price_id": _env(quote.stripe_price_env),
        "billing_code": quote.billing_code,
        "service_code": quote.service_code,
        "payment_stage": "review",
        "amount_cents": quote.amount_cents,
        "currency": quote.currency,
        "authority_version": quote.version,
    }


def _safe_checkout_email(snapshot, requested_email: str | None) -> str:
    persisted = str(snapshot.contact_email or "").strip()
    interested = snapshot.interested_data if isinstance(snapshot.interested_data, dict) else {}
    interested_email = str(interested.get("email") or "").strip()
    resolved = persisted or interested_email or str(requested_email or "").strip()
    if not resolved:
        raise HTTPException(
            status_code=409,
            detail="El expediente no conserva un email de contacto para el pago",
        )
    return resolved


def _case_row_value(row: object, index: int, key: str, default=""):
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return default


def _review_checkout_intent(
    *,
    case_id: str,
    stripe_product: dict,
    checkout_email: str,
    authority_material_sha256: str,
    signed_document_attestation_sha256: str,
    success_url: str,
    cancel_url: str,
) -> tuple[dict, str, str, str]:
    """Deriva claim e idempotencia solo de material autoritativo estable."""

    metadata = {
        "case_id": case_id,
        "service_code": stripe_product["service_code"],
        "billing_code": stripe_product["billing_code"],
        "payment_stage": stripe_product["payment_stage"],
        "authority_version": stripe_product["authority_version"],
        "amount_cents": str(stripe_product["amount_cents"] or ""),
        "currency": stripe_product["currency"],
        "authority_material_sha256": authority_material_sha256,
        "signed_document_attestation_sha256": (
            signed_document_attestation_sha256
        ),
    }
    immutable_intent = {
        "mode": "payment",
        "payment_method_types": ["card"],
        "case_id": case_id,
        "customer_email": checkout_email.lower(),
        "price_id": stripe_product["price_id"],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "locale": "es",
        "metadata": metadata,
        "payment_intent_data": {"metadata": metadata},
    }
    digest = hashlib.sha256(
        json.dumps(
            immutable_intent,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return (
        metadata,
        digest,
        f"{_CHECKOUT_CLAIM_PREFIX}{digest}",
        f"rtm-review-v2:{case_id}:{digest}",
    )


def _validated_review_checkout_session(
    session: object,
    *,
    expected_metadata: dict,
    expected_amount: int,
    expected_currency: str,
) -> dict:
    session_id = str(_object_value(session, "id") or "").strip()
    session_url = str(_object_value(session, "url") or "").strip()
    status = str(_object_value(session, "status") or "").strip().lower()
    metadata = _payload_dict(_object_value(session, "metadata") or {})
    try:
        amount = int(_object_value(session, "amount_total"))
    except (TypeError, ValueError):
        amount = -1
    currency = _normalized_currency(_object_value(session, "currency"))
    parsed_url = urlsplit(session_url)
    usable_url = (
        parsed_url.scheme == "https"
        and (parsed_url.hostname or "").lower() == "checkout.stripe.com"
        and parsed_url.path.startswith("/")
    )
    if (
        not _valid_stripe_id(session_id, "cs_")
        or status not in {"open", "complete", "expired"}
        or amount != int(expected_amount)
        or currency != _normalized_currency(expected_currency)
        or metadata != expected_metadata
        or (status == "open" and not usable_url)
    ):
        raise HTTPException(
            status_code=502,
            detail="El proveedor de pago no devolvió una sesión válida",
        )
    return {
        "id": session_id,
        "url": session_url,
        "status": status,
        "amount_total": amount,
        "currency": currency,
    }


def _expire_review_checkout_session(session_id: str) -> None:
    if not _valid_stripe_id(session_id, "cs_"):
        return
    try:
        stripe.checkout.Session.expire(session_id)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="No se pudo cerrar de forma segura la sesión de pago descartada",
        ) from exc


def _release_review_checkout_reference(
    engine,
    *,
    case_id: str,
    expected_reference: str,
    event_type: str,
) -> bool:
    """Libera solo el claim/sesión exactos; nunca pisa un reemplazo o un pago."""

    with engine.begin() as conn:
        released = conn.execute(
            text(
                """
                UPDATE cases
                SET payment_status='unpaid',
                    stripe_session_id=NULL,
                    product_code=NULL,
                    updated_at=NOW()
                WHERE id=:id
                  AND payment_status IN ('creating', 'pending')
                  AND stripe_session_id=:expected_reference
                RETURNING id
                """
            ),
            {"id": case_id, "expected_reference": expected_reference},
        ).fetchone()
        if released:
            _append_event(
                conn,
                case_id,
                event_type,
                {"checkout_reference": expected_reference},
            )
    return bool(released)


def _review_checkout_response(
    *,
    stripe_product: dict,
    session_url: str,
    reused: bool = False,
) -> dict:
    return {
        "ok": True,
        "url": session_url,
        "reused": reused,
        "billing_code": stripe_product["billing_code"],
        "payment_stage": stripe_product["payment_stage"],
        "service_code": stripe_product["service_code"],
        "amount_cents": stripe_product["amount_cents"],
        "currency": stripe_product["currency"],
        "authority_version": stripe_product["authority_version"],
    }


async def _read_stripe_webhook_payload(request: Request) -> bytes:
    """Lee el webhook con límite incremental antes de verificar la firma."""

    stream = getattr(request, "stream", None)
    if callable(stream):
        payload = bytearray()
        async for chunk in stream():
            if len(payload) + len(chunk) > _MAX_STRIPE_WEBHOOK_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="Webhook demasiado grande",
                )
            payload.extend(chunk)
        return bytes(payload)

    # Compatibilidad con dobles de prueba; Starlette siempre expone stream().
    payload = await request.body()
    if len(payload) > _MAX_STRIPE_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="Webhook demasiado grande")
    return payload


def _preserved_terminal_status(status: str) -> bool:
    normalized = str(status or "").strip().lower()
    return (
        normalized in TERMINAL_CASE_STATUSES
        or normalized.startswith("presentado")
        or normalized == "vehicle_removal_completed"
    )


def _reconcile_payment_reversal(
    *,
    event_type: str,
    event_id: str,
    stripe_object: object,
) -> dict:
    target_payment_status = _PAYMENT_REVERSAL_EVENTS[event_type]
    payment_intent = _stripe_object_id(
        _object_value(stripe_object, "payment_intent")
    )
    if not _valid_stripe_id(event_id, "evt_"):
        raise HTTPException(
            status_code=400,
            detail="Evento de reversión sin identificadores válidos",
        )
    if not _valid_stripe_id(payment_intent, "pi_"):
        return {"ok": True, "ignored": True, "event_type": event_type}

    # Stripe no garantiza orden. La Checkout Session permite correlacionar una
    # reversión aunque llegue antes de que el settlement haya persistido el PI.
    try:
        sessions = stripe.checkout.Session.list(
            payment_intent=payment_intent,
            limit=2,
        )
    except Exception as exc:
        # No hacemos ack+drop: Stripe reintentará el evento firmado.
        raise HTTPException(
            status_code=503,
            detail="No se pudo correlacionar la reversión de pago",
        ) from exc
    session_items = _object_value(sessions, "data", []) or []
    if not isinstance(session_items, (list, tuple)) or len(session_items) != 1:
        # Un PI no originado por Checkout no pertenece a este flujo. Una
        # correlación ambigua, en cambio, requiere reintento/revisión.
        if not session_items:
            return {"ok": True, "ignored": True, "event_type": event_type}
        raise HTTPException(
            status_code=503,
            detail="La reversión de pago tiene una correlación ambigua",
        )
    checkout_session = session_items[0]
    session_id = str(_object_value(checkout_session, "id") or "").strip()
    session_metadata = _payload_dict(
        _object_value(checkout_session, "metadata") or {}
    )
    case_id = _canonical_case_uuid(session_metadata.get("case_id"))
    if not case_id or not _valid_stripe_id(session_id, "cs_"):
        if "case_id" in session_metadata or _vehicle_removal_metadata_marker(
            session_metadata
        ):
            raise HTTPException(
                status_code=503,
                detail="La reversión RTM no tiene una vinculación válida",
            )
        return {"ok": True, "ignored": True, "event_type": event_type}

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, COALESCE(payment_status, '') AS payment_status,
                       COALESCE(status, '') AS status
                FROM cases
                WHERE id=:case_id
                  AND stripe_session_id=:session_id
                  AND (
                      stripe_payment_intent IS NULL
                      OR stripe_payment_intent=''
                      OR stripe_payment_intent=:payment_intent
                  )
                FOR UPDATE
                """
            ),
            {
                "case_id": case_id,
                "session_id": session_id,
                "payment_intent": payment_intent,
            },
        ).fetchone()
        if not row:
            # La sesión llevaba metadata RTM: fallar fuerza un reintento en vez
            # de perder para siempre una reversión adelantada.
            raise HTTPException(
                status_code=503,
                detail="La reversión RTM aún no puede conciliarse",
            )

        case_id = str(_case_row_value(row, 0, "id") or "")
        current_payment = str(
            _case_row_value(row, 1, "payment_status") or ""
        ).strip().lower()
        current_status = str(_case_row_value(row, 2, "status") or "")
        duplicate = conn.execute(
            text(
                """
                SELECT 1
                FROM events
                WHERE case_id=:case_id
                  AND type='payment_entitlement_suspended'
                  AND payload->>'stripe_event_id'=:stripe_event_id
                LIMIT 1
                """
            ),
            {"case_id": case_id, "stripe_event_id": event_id},
        ).fetchone()
        if duplicate:
            return {
                "ok": True,
                "replayed": True,
                "case_id": case_id,
                "event_type": event_type,
            }

        next_status = (
            current_status
            if _preserved_terminal_status(current_status)
            else "payment_reconciliation_required"
        )
        updated = conn.execute(
            text(
                """
                UPDATE cases
                SET payment_status=:target_payment_status,
                    status=:next_status,
                    stripe_payment_intent=:payment_intent,
                    updated_at=NOW()
                WHERE id=:id
                  AND (
                      stripe_payment_intent IS NULL
                      OR stripe_payment_intent=''
                      OR stripe_payment_intent=:payment_intent
                  )
                  AND COALESCE(payment_status, '')=:expected_payment_status
                  AND COALESCE(status, '')=:expected_status
                RETURNING id
                """
            ),
            {
                "id": case_id,
                "payment_intent": payment_intent,
                "target_payment_status": target_payment_status,
                "next_status": next_status,
                "expected_payment_status": current_payment,
                "expected_status": current_status,
            },
        ).fetchone()
        if not updated:
            raise HTTPException(
                status_code=409,
                detail="La reversión perdió la carrera de conciliación",
            )
        _append_event(
            conn,
            case_id,
            "payment_entitlement_suspended",
            {
                "stripe_event_id": event_id,
                "stripe_event_type": event_type,
                "payment_intent": payment_intent,
                "payment_status": target_payment_status,
            },
        )
    return {
        "ok": True,
        "processed": True,
        "case_id": case_id,
        "payment_status": target_payment_status,
    }


def _reconcile_expired_review_checkout(
    *,
    metadata: dict,
    event_id: str,
    session_id: str,
) -> dict:
    if _vehicle_removal_metadata_marker(metadata):
        if not is_exact_vehicle_removal_stripe_metadata(metadata):
            raise HTTPException(status_code=400, detail="Contrato de checkout no válido")
        case_id = _canonical_case_uuid(metadata.get("case_id"))
        if (
            not case_id
            or not _valid_stripe_id(event_id, "evt_")
            or not _valid_stripe_id(session_id, "cs_")
        ):
            raise HTTPException(status_code=400, detail="Sesión expirada no verificable")

        engine = get_engine()
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT COALESCE(payment_status, '') AS payment_status,
                           COALESCE(stripe_session_id, '') AS stripe_session_id,
                           COALESCE(product_code, '') AS product_code,
                           COALESCE(category, '') AS category,
                           COALESCE(case_type, '') AS case_type,
                           COALESCE(status, '') AS status,
                           COALESCE(department, '') AS department
                    FROM cases
                    WHERE id=:case_id
                    FOR UPDATE
                    """
                ),
                {"case_id": case_id},
            ).fetchone()
            if not row:
                return {
                    "ok": True,
                    "ignored": True,
                    "event_type": "checkout.session.expired",
                }

            duplicate = conn.execute(
                text(
                    """
                    SELECT 1
                    FROM events
                    WHERE case_id=:case_id
                      AND type='vehicle_removal_checkout_session_expired'
                      AND payload->>'stripe_event_id'=:stripe_event_id
                    LIMIT 1
                    """
                ),
                {"case_id": case_id, "stripe_event_id": event_id},
            ).fetchone()
            if duplicate:
                return {
                    "ok": True,
                    "replayed": True,
                    "case_id": case_id,
                    "event_type": "checkout.session.expired",
                }

            payment_status = str(
                _case_row_value(row, 0, "payment_status") or ""
            ).strip().lower()
            stored_session = str(
                _case_row_value(row, 1, "stripe_session_id") or ""
            ).strip()
            product_code = str(
                _case_row_value(row, 2, "product_code") or ""
            ).strip()
            category = str(
                _case_row_value(row, 3, "category") or ""
            ).strip()
            case_type = str(
                _case_row_value(row, 4, "case_type") or ""
            ).strip()
            status = str(_case_row_value(row, 5, "status") or "").strip()
            department = str(
                _case_row_value(row, 6, "department") or ""
            ).strip()

            # An old expiry must never clear a replacement or revoke an
            # entitlement that a later, paid event already established.
            if stored_session != session_id or payment_status != "pending":
                return {
                    "ok": True,
                    "replayed": True,
                    "case_id": case_id,
                    "event_type": "checkout.session.expired",
                }
            if (
                product_code != _VEHICLE_REMOVAL_PRODUCT_CODE
                or category != _VEHICLE_REMOVAL_SERVICE_CODE
                or case_type != _VEHICLE_REMOVAL_SERVICE_CODE
                or status != "vehicle_removal_pending_payment"
                or department != "traffic"
            ):
                raise HTTPException(
                    status_code=409,
                    detail="La sesión expirada no coincide con el expediente",
                )

            released = conn.execute(
                text(
                    """
                    UPDATE cases
                    SET payment_status='unpaid',
                        stripe_session_id=NULL,
                        product_code=NULL,
                        status='authorization_pending',
                        updated_at=NOW()
                    WHERE id=:case_id
                      AND payment_status='pending'
                      AND stripe_session_id=:session_id
                      AND product_code='ELIMINAR_COCHE'
                      AND category='vehicle_removal'
                      AND case_type='vehicle_removal'
                      AND department='traffic'
                      AND status='vehicle_removal_pending_payment'
                    RETURNING id
                    """
                ),
                {"case_id": case_id, "session_id": session_id},
            ).fetchone()
            if not released:
                return {
                    "ok": True,
                    "replayed": True,
                    "case_id": case_id,
                    "event_type": "checkout.session.expired",
                }
            _append_event(
                conn,
                case_id,
                "vehicle_removal_checkout_session_expired",
                {
                    "stripe_event_id": event_id,
                    "stripe_event_type": "checkout.session.expired",
                    "session": session_id,
                },
            )
        return {
            "ok": True,
            "processed": True,
            "case_id": case_id,
        }
    case_id = _canonical_case_uuid(metadata.get("case_id"))
    if (
        not case_id
        or not _valid_stripe_id(event_id, "evt_")
        or not _valid_stripe_id(session_id, "cs_")
    ):
        raise HTTPException(status_code=400, detail="Sesión expirada no verificable")

    engine = get_engine()
    released = _release_review_checkout_reference(
        engine,
        case_id=case_id,
        expected_reference=session_id,
        event_type="checkout_session_expired",
    )
    return {
        "ok": True,
        "processed": released,
        "replayed": not released,
        "case_id": case_id,
    }


def _record_unsettled_checkout_event(
    *,
    metadata: dict,
    event_id: str,
    event_type: str,
    session_id: str,
    payment_intent: str,
    failed: bool,
) -> dict:
    case_id = _canonical_case_uuid(metadata.get("case_id"))
    if (
        not case_id
        or not _valid_stripe_id(event_id, "evt_")
        or not _valid_stripe_id(session_id, "cs_")
    ):
        raise HTTPException(status_code=400, detail="Checkout no verificable")

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT COALESCE(payment_status, '') AS payment_status,
                       COALESCE(stripe_session_id, '') AS stripe_session_id,
                       COALESCE(status, '') AS status
                FROM cases
                WHERE id=:id
                FOR UPDATE
                """
            ),
            {"id": case_id},
        ).fetchone()
        if not row:
            return {"ok": True, "ignored": True, "event_type": event_type}
        payment_status = str(
            _case_row_value(row, 0, "payment_status") or ""
        ).strip().lower()
        stored_session = str(
            _case_row_value(row, 1, "stripe_session_id") or ""
        ).strip()
        status = str(_case_row_value(row, 2, "status") or "")
        if stored_session != session_id or payment_status == "paid":
            return {
                "ok": True,
                "replayed": True,
                "case_id": case_id,
                "event_type": event_type,
            }

        duplicate = conn.execute(
            text(
                """
                SELECT 1
                FROM events
                WHERE case_id=:case_id
                  AND type IN (
                      'checkout_async_payment_pending',
                      'checkout_async_payment_failed'
                  )
                  AND payload->>'stripe_event_id'=:stripe_event_id
                LIMIT 1
                """
            ),
            {"case_id": case_id, "stripe_event_id": event_id},
        ).fetchone()
        if duplicate:
            return {
                "ok": True,
                "replayed": True,
                "case_id": case_id,
                "event_type": event_type,
            }

        if failed:
            next_status = (
                status
                if _preserved_terminal_status(status)
                else "payment_reconciliation_required"
            )
            updated = conn.execute(
                text(
                    """
                    UPDATE cases
                    SET payment_status='failed',
                        status=:next_status,
                        updated_at=NOW()
                    WHERE id=:id
                      AND payment_status='pending'
                      AND stripe_session_id=:session_id
                      AND COALESCE(status, '')=:expected_status
                    RETURNING id
                    """
                ),
                {
                    "id": case_id,
                    "session_id": session_id,
                    "next_status": next_status,
                    "expected_status": status,
                },
            ).fetchone()
            if not updated:
                return {
                    "ok": True,
                    "replayed": True,
                    "case_id": case_id,
                    "event_type": event_type,
                }
        _append_event(
            conn,
            case_id,
            (
                "checkout_async_payment_failed"
                if failed
                else "checkout_async_payment_pending"
            ),
            {
                "stripe_event_id": event_id,
                "stripe_event_type": event_type,
                "session": session_id,
                "payment_intent": payment_intent,
            },
        )
    return {
        "ok": True,
        "processed": True,
        "case_id": case_id,
        "payment_status": "failed" if failed else "pending",
    }


def _activate_post_payment_review(conn, case_id: str, session_id: str) -> dict:
    """Registra la activación OPS ya materializada por el CAS de cobro."""

    payload = {
        "ok": True,
        "mode": "post_payment_review",
        "session": session_id,
        "analysis_deferred": True,
        "classification_deferred": True,
        "strategy_deferred": True,
        "generation_deferred": True,
        "next_authority": "rtm_intelligence_core",
        "message": (
            "Pago confirmado. El expediente queda en revisión OPS. "
            "Ningún clasificador, especialista o generador se ejecuta desde billing."
        ),
    }
    _append_event(conn, case_id, "rtm_core_review_queued", payload)
    return payload


@router.get("/billing/review-context/{case_id}")
def review_checkout_context(
    case_id: str,
    x_case_token: str | None = Header(default=None, alias="X-RTM-Case-Token"),
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
):
    """Precio y requisitos del estudio sin exponer datos personales."""

    case_id = require_case_or_operator_access(
        case_id, x_case_token, x_operator_token
    )
    engine = get_engine()
    with engine.begin() as conn:
        snapshot = load_case_review_snapshot(conn, case_id)
        signed_authority_verified = False
        if snapshot.authorized and "authorization_signed" in set(
            snapshot.document_kinds
        ):
            try:
                verify_signed_case_authority(conn, case_id)
                signed_authority_verified = True
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
    readiness = build_case_review_readiness(snapshot)
    readiness_payload = readiness.model_dump(mode="json")
    if readiness.ready and not signed_authority_verified:
        readiness_payload["ready"] = False
        readiness_payload["blocking_issues"].append(
            {
                "code": "authorization_signature_review",
                "message": "La firma requiere revisión humana verificable",
                "area": "authorization",
                "blocking": True,
            }
        )
    return {
        "ok": True,
        "case_id": case_id,
        "signed_authority_verified": signed_authority_verified,
        "readiness": readiness_payload,
    }


@router.post("/billing/checkout")
@router.post("/checkout")
def create_checkout(
    req: CheckoutRequest,
    x_case_token: str | None = Header(default=None, alias="X-RTM-Case-Token"),
):
    case_id = require_case_access_token(req.case_id, x_case_token)
    # Ninguna clave ni llamada Stripe se toca antes de comprobar el interruptor
    # del entorno. Los pagos finales conservan un segundo permiso independiente.
    require_http_capability("stripe")
    stage = _normalized_stage(req.payment_stage)
    if stage in _FINAL_STAGES:
        require_http_capability("final_payments")
        # Retirado fail-closed: el contrato legacy permitía al navegador elegir
        # el producto/precio y no conservaba una cotización final aprobada por
        # OPS.  Las sesiones ya emitidas aún pueden conciliarse en el webhook,
        # pero no se abrirán otras hasta disponer de un presupuesto persistido,
        # versionado y ligado al expediente.
        raise HTTPException(
            status_code=409,
            detail=(
                "El pago final requiere un presupuesto aprobado por RTM; "
                "la creación legacy está retirada"
            ),
        )
    if stage not in _REVIEW_STAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Fase de pago no reconocida: {req.payment_stage}",
        )

    stripe.api_key = _env("STRIPE_SECRET_KEY")
    frontend_url = trusted_frontend_origin()
    engine = get_engine()

    # Fase 1: reclama de forma durable la intención bajo lock. Ninguna llamada
    # remota ocurre dentro de esta transacción. Dos peticiones idénticas ven el
    # mismo claim y usarán la misma clave Stripe; una intención distinta no
    # puede sustituir el checkout en curso.
    with engine.begin() as conn:
        gate = conn.execute(
            text(
                """
                SELECT COALESCE(payment_status, '') AS payment_status,
                       COALESCE(stripe_session_id, '') AS stripe_session_id,
                       COALESCE(product_code, '') AS product_code,
                       COALESCE(status, '') AS status
                FROM cases
                WHERE id=:id
                FOR UPDATE
                """
            ),
            {"id": case_id},
        ).fetchone()
        if not gate:
            raise HTTPException(status_code=404, detail="Expediente no encontrado")

        snapshot = load_case_review_snapshot(conn, case_id)
        readiness = build_case_review_readiness(snapshot)
        if not readiness.ready:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "El expediente no está completo para pagar el estudio",
                    "readiness": readiness.model_dump(mode="json"),
                },
            )
        stripe_product = _review_product(readiness)

        current_payment_status = str(
            _case_row_value(gate, 0, "payment_status") or ""
        ).strip().lower()
        stored_checkout_reference = str(
            _case_row_value(gate, 1, "stripe_session_id") or ""
        ).strip()
        stored_product_code = str(
            _case_row_value(gate, 2, "product_code") or ""
        ).strip()
        expected_case_status = str(
            _case_row_value(gate, 3, "status") or ""
        )
        if current_payment_status == "paid":
            return {
                "ok": True,
                "already_paid": True,
                "redirect": f"{frontend_url}/resumen?case={case_id}",
                "billing_code": stripe_product["billing_code"],
                "amount_cents": stripe_product["amount_cents"],
                "currency": stripe_product["currency"],
            }
        if current_payment_status in _CHECKOUT_RECONCILIATION_PAYMENT_STATUSES:
            raise HTTPException(
                status_code=409,
                detail="El pago requiere conciliación antes de abrir otro checkout",
            )
        if expected_case_status != "ready_for_review_payment":
            raise HTTPException(
                status_code=409,
                detail="El expediente no está habilitado para abrir el checkout",
            )

        signed_authority = verify_signed_case_authority(conn, case_id)
        checkout_email = _safe_checkout_email(snapshot, req.email)
        requested_product = normalize_code(req.product)

        authority_material_sha256 = str(
            signed_authority.get("material_sha256") or ""
        )
        signed_document_attestation_sha256 = str(
            signed_authority.get("signed_document_attestation", {}).get(
                "material_sha256"
            )
            or ""
        )
        success_url = f"{frontend_url}/pago-ok?case={case_id}"
        cancel_url = f"{frontend_url}/resumen?case={case_id}"
        (
            metadata,
            checkout_intent_sha256,
            claim_reference,
            idempotency_key,
        ) = _review_checkout_intent(
            case_id=case_id,
            stripe_product=stripe_product,
            checkout_email=checkout_email,
            authority_material_sha256=authority_material_sha256,
            signed_document_attestation_sha256=(
                signed_document_attestation_sha256
            ),
            success_url=success_url,
            cancel_url=cancel_url,
        )

        if current_payment_status == _CHECKOUT_CREATING_PAYMENT_STATUS:
            if (
                stored_checkout_reference != claim_reference
                or stored_product_code != stripe_product["billing_code"]
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Existe otra creación de checkout en curso",
                )
            checkout_action = "create"
        elif current_payment_status == "pending":
            if (
                not _valid_stripe_id(stored_checkout_reference, "cs_")
                or stored_product_code != stripe_product["billing_code"]
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Existe otro checkout pendiente para el expediente",
                )
            checkout_action = "retrieve"
        elif stored_checkout_reference:
            # Nunca sustituimos silenciosamente una referencia remota previa:
            # podría seguir siendo cobrable aunque el estado local sea anómalo.
            raise HTTPException(
                status_code=409,
                detail="Existe una sesión previa que requiere conciliación",
            )
        else:
            claimed = conn.execute(
                text(
                    """
                    UPDATE cases
                    SET payment_status='creating',
                        stripe_session_id=:claim_reference,
                        product_code=:product,
                        contact_email=:email,
                        updated_at=NOW()
                    WHERE id=:id
                      AND COALESCE(payment_status, '')=:expected_payment_status
                      AND COALESCE(stripe_session_id, '')=:expected_reference
                      AND COALESCE(status, '')='ready_for_review_payment'
                    RETURNING id
                    """
                ),
                {
                    "id": case_id,
                    "claim_reference": claim_reference,
                    "product": stripe_product["billing_code"],
                    "email": checkout_email,
                    "expected_payment_status": current_payment_status,
                    "expected_reference": stored_checkout_reference,
                },
            ).fetchone()
            if not claimed:
                raise HTTPException(
                    status_code=409,
                    detail="El expediente cambió al reclamar el checkout",
                )
            _append_event(
                conn,
                case_id,
                "checkout_creation_claimed",
                {
                    "checkout_intent_sha256": checkout_intent_sha256,
                    "billing_code": stripe_product["billing_code"],
                    "payment_stage": stripe_product["payment_stage"],
                    "authority_material_sha256": authority_material_sha256,
                    "signed_document_attestation_sha256": (
                        signed_document_attestation_sha256
                    ),
                },
            )
            checkout_action = "create"

    # Fase 2: creación/recuperación remota sin mantener conexiones ni locks SQL.
    try:
        if checkout_action == "retrieve":
            session = stripe.checkout.Session.retrieve(stored_checkout_reference)
        else:
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                mode="payment",
                customer_email=checkout_email,
                line_items=[{"price": stripe_product["price_id"], "quantity": 1}],
                success_url=success_url,
                cancel_url=cancel_url,
                metadata=metadata,
                payment_intent_data={"metadata": metadata},
                locale="es",
                idempotency_key=idempotency_key,
            )
    except HTTPException:
        raise
    except Exception as exc:
        # El claim queda recuperable: repetir la misma intención usa la misma
        # idempotency key y recupera una sesión que Stripe hubiera creado antes
        # de un timeout de red.
        raise HTTPException(
            status_code=502,
            detail="No se pudo abrir la sesión de pago",
        ) from exc

    remote_session_id = str(_object_value(session, "id") or "").strip()
    try:
        validated_session = _validated_review_checkout_session(
            session,
            expected_metadata=metadata,
            expected_amount=int(stripe_product["amount_cents"]),
            expected_currency=stripe_product["currency"],
        )
    except HTTPException:
        if checkout_action == "create" and _valid_stripe_id(
            remote_session_id, "cs_"
        ):
            _expire_review_checkout_session(remote_session_id)
            _release_review_checkout_reference(
                engine,
                case_id=case_id,
                expected_reference=claim_reference,
                event_type="checkout_creation_rejected",
            )
        raise

    session_id = validated_session["id"]
    session_status = validated_session["status"]
    if session_status == "expired":
        expected_reference = (
            stored_checkout_reference
            if checkout_action == "retrieve"
            else claim_reference
        )
        _release_review_checkout_reference(
            engine,
            case_id=case_id,
            expected_reference=expected_reference,
            event_type="checkout_session_expired_reconciled",
        )
        raise HTTPException(
            status_code=409,
            detail="La sesión de pago expiró; vuelve a iniciar el checkout",
        )

    checkout_evidence = {
        "session": session_id,
        "checkout_intent_sha256": checkout_intent_sha256,
        "requested_product": requested_product,
        "authoritative_service_code": stripe_product["service_code"],
        "billing_code": stripe_product["billing_code"],
        "payment_stage": stripe_product["payment_stage"],
        "amount_cents": stripe_product["amount_cents"],
        "stripe_amount_total": validated_session["amount_total"],
        "currency": validated_session["currency"],
        "authority_version": stripe_product["authority_version"],
        "authority_material_sha256": authority_material_sha256,
        "signed_document_attestation_sha256": (
            signed_document_attestation_sha256
        ),
        "requested_email_matches_persisted": checkout_email.lower()
        == str(req.email or "").strip().lower(),
        "authorized": bool(snapshot.authorized),
        "authorized_at": str(snapshot.authorized_at or ""),
        "readiness_version": readiness.version if readiness is not None else None,
    }

    # Fase 3: publica claim -> session por CAS, o reconoce a un ganador que
    # publicó exactamente la misma sesión idempotente. Jamás sobrescribe otra.
    binding_outcome = "conflict"
    current_reference = ""
    with engine.begin() as conn:
        current = conn.execute(
            text(
                """
                SELECT COALESCE(payment_status, '') AS payment_status,
                       COALESCE(stripe_session_id, '') AS stripe_session_id,
                       COALESCE(product_code, '') AS product_code,
                       COALESCE(status, '') AS status
                FROM cases
                WHERE id=:id
                FOR UPDATE
                """
            ),
            {"id": case_id},
        ).fetchone()
        if current:
            current_payment = str(
                _case_row_value(current, 0, "payment_status") or ""
            ).strip().lower()
            current_reference = str(
                _case_row_value(current, 1, "stripe_session_id") or ""
            ).strip()
            current_product = str(
                _case_row_value(current, 2, "product_code") or ""
            ).strip()
            current_case_status = str(
                _case_row_value(current, 3, "status") or ""
            )

            if current_payment == "paid" and current_reference == session_id:
                binding_outcome = "already_paid"
            elif (
                current_payment == "pending"
                and current_reference == session_id
                and current_product == stripe_product["billing_code"]
                and current_case_status == expected_case_status
            ):
                binding_outcome = "reused"
            elif (
                session_status == "open"
                and current_payment == _CHECKOUT_CREATING_PAYMENT_STATUS
                and current_reference == claim_reference
                and current_product == stripe_product["billing_code"]
                and current_case_status == expected_case_status
            ):
                try:
                    current_authority = verify_signed_case_authority(conn, case_id)
                    current_authority_sha256 = str(
                        current_authority.get("material_sha256") or ""
                    )
                    current_signed_sha256 = str(
                        current_authority.get(
                            "signed_document_attestation", {}
                        ).get("material_sha256")
                        or ""
                    )
                except HTTPException:
                    current_authority_sha256 = ""
                    current_signed_sha256 = ""
                if hmac.compare_digest(
                    current_authority_sha256, authority_material_sha256
                ) and hmac.compare_digest(
                    current_signed_sha256,
                    signed_document_attestation_sha256,
                ):
                    published = conn.execute(
                        text(
                            """
                            UPDATE cases
                            SET payment_status='pending',
                                stripe_session_id=:session_id,
                                updated_at=NOW()
                            WHERE id=:id
                              AND payment_status='creating'
                              AND stripe_session_id=:claim_reference
                              AND product_code=:product
                              AND COALESCE(status, '')='ready_for_review_payment'
                            RETURNING id
                            """
                        ),
                        {
                            "id": case_id,
                            "session_id": session_id,
                            "claim_reference": claim_reference,
                            "product": stripe_product["billing_code"],
                        },
                    ).fetchone()
                    if published:
                        _append_event(
                            conn, case_id, "checkout_started", checkout_evidence
                        )
                        _append_event(
                            conn,
                            case_id,
                            "checkout_session_created",
                            checkout_evidence,
                        )
                        binding_outcome = "published"

    if binding_outcome == "already_paid":
        return {
            "ok": True,
            "already_paid": True,
            "redirect": f"{frontend_url}/resumen?case={case_id}",
            "billing_code": stripe_product["billing_code"],
            "amount_cents": stripe_product["amount_cents"],
            "currency": stripe_product["currency"],
        }
    if binding_outcome in {"published", "reused"} and session_status == "open":
        return _review_checkout_response(
            stripe_product=stripe_product,
            session_url=validated_session["url"],
            reused=binding_outcome == "reused",
        )
    if binding_outcome == "reused" and session_status == "complete":
        raise HTTPException(
            status_code=409,
            detail="El pago está pendiente de conciliación",
        )

    # Solo se expira una sesión abierta que no sea ya la referencia ganadora.
    # Si Stripe devolvió el mismo objeto idempotente publicado por otra petición,
    # se conserva y la petición se limita a fallar cerrada.
    if session_status == "open" and current_reference != session_id:
        _expire_review_checkout_session(session_id)
        if current_reference == claim_reference:
            _release_review_checkout_reference(
                engine,
                case_id=case_id,
                expected_reference=claim_reference,
                event_type="checkout_creation_lost",
            )
    raise HTTPException(
        status_code=409,
        detail="La sesión de pago perdió la carrera de publicación",
    )


@router.post("/billing/webhook")
@router.post("/webhook")
async def stripe_webhook(request: Request):
    require_http_capability("stripe")
    payload = await _read_stripe_webhook_payload(request)
    sig_header = request.headers.get("stripe-signature")
    try:
        stripe.api_key = _env("STRIPE_SECRET_KEY")
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            _env("STRIPE_WEBHOOK_SECRET"),
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Webhook inválido")

    event_type = str(_object_value(event, "type") or "")
    event_id = str(_object_value(event, "id") or "").strip()
    data_object = _object_value(event, "data") or {}
    stripe_object = _object_value(data_object, "object") or {}

    if event_type in _PAYMENT_REVERSAL_EVENTS:
        return _reconcile_payment_reversal(
            event_type=event_type,
            event_id=event_id,
            stripe_object=stripe_object,
        )

    checkout_events = _CHECKOUT_SETTLEMENT_EVENTS | {
        "checkout.session.async_payment_failed",
        "checkout.session.expired",
    }
    if event_type not in checkout_events:
        return {"ok": True, "ignored": True, "event_type": event_type}

    session = stripe_object
    metadata = _payload_dict(_object_value(session, "metadata") or {})
    case_id = _canonical_case_uuid(metadata.get("case_id"))
    session_id = str(_object_value(session, "id") or "").strip()
    payment_intent = _stripe_object_id(_object_value(session, "payment_intent"))
    payment_stage = normalize_code(metadata.get("payment_stage"))
    session_payment_status = str(
        _object_value(session, "payment_status") or ""
    ).strip().lower()
    session_mode = str(_object_value(session, "mode") or "").strip().lower()

    if event_type == "checkout.session.expired":
        return _reconcile_expired_review_checkout(
            metadata=metadata,
            event_id=event_id,
            session_id=session_id,
        )
    if session_mode != "payment":
        raise HTTPException(status_code=409, detail="El evento no corresponde a un pago")
    if event_type == "checkout.session.async_payment_failed":
        return _record_unsettled_checkout_event(
            metadata=metadata,
            event_id=event_id,
            event_type=event_type,
            session_id=session_id,
            payment_intent=payment_intent,
            failed=True,
        )
    if (
        event_type == "checkout.session.completed"
        and session_payment_status != "paid"
    ):
        # En sesiones legacy con métodos demorados, completed no acredita el
        # pago. Conservamos el vínculo congelado hasta succeeded/failed.
        return _record_unsettled_checkout_event(
            metadata=metadata,
            event_id=event_id,
            event_type=event_type,
            session_id=session_id,
            payment_intent=payment_intent,
            failed=False,
        )
    if session_payment_status != "paid" or not payment_intent:
        raise HTTPException(status_code=409, detail="La sesión no acredita un pago liquidado")

    session_currency = _normalized_currency(_object_value(session, "currency"))
    try:
        session_amount = int(_object_value(session, "amount_total"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Webhook sin importe verificable")

    if _vehicle_removal_metadata_marker(metadata):
        if (
            metadata.get("checkout_contract")
            != _VEHICLE_REMOVAL_CHECKOUT_CONTRACT
        ):
            raise HTTPException(status_code=400, detail="Contrato de checkout no reconocido")
        return _settle_vehicle_removal_checkout(
            metadata=metadata,
            event_id=event_id,
            session_id=session_id,
            payment_intent=payment_intent,
            session_mode=session_mode,
            session_payment_status=session_payment_status,
            session_amount=session_amount,
            session_currency=session_currency,
        )

    if not event_id or not case_id or not session_id:
        raise HTTPException(status_code=400, detail="Webhook sin identificadores obligatorios")
    if payment_stage in _FINAL_STAGES:
        # Las sesiones finales legacy nacieron de una tarifa elegida por el
        # cliente, sin presupuesto OPS persistido. Incluso un webhook Stripe
        # auténtico solo prueba que se pagó *esa* sesión, no que el importe o
        # servicio fueran los aprobados. Se envían a conciliación manual y no
        # alteran el estado del expediente.
        raise HTTPException(
            status_code=409,
            detail=(
                "El pago final legacy requiere conciliación manual contra "
                "un presupuesto aprobado"
            ),
        )
    if payment_stage not in (_REVIEW_STAGES | _FINAL_STAGES):
        raise HTTPException(status_code=400, detail="Fase de pago no reconocida en webhook")

    engine = get_engine()
    with engine.begin() as conn:
        case_row = conn.execute(
            text(
                "SELECT payment_status, stripe_session_id, product_code, "
                "stripe_payment_intent, COALESCE(status, '') AS status "
                "FROM cases WHERE id=:id FOR UPDATE"
            ),
            {"id": case_id},
        ).fetchone()
        if not case_row:
            raise HTTPException(status_code=404, detail="Expediente de webhook no encontrado")
        stored_session_id = str(case_row[1] or "").strip()
        if not stored_session_id or stored_session_id != session_id:
            raise HTTPException(status_code=409, detail="Sesión Stripe no vinculada al expediente")

        intent_row = conn.execute(
            text(
                """
                SELECT payload FROM events
                WHERE case_id=:id
                  AND type='checkout_session_created'
                  AND payload->>'session'=:session_id
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"id": case_id, "session_id": session_id},
        ).fetchone()
        intent = _payload_dict(intent_row[0] if intent_row else None)
        if not intent:
            raise HTTPException(status_code=409, detail="Sesión Stripe sin intención RTM persistida")

        intended_stage = normalize_code(intent.get("payment_stage"))
        intended_billing = normalize_code(intent.get("billing_code"))
        intended_service = normalize_code(intent.get("authoritative_service_code"))
        intended_currency = _normalized_currency(intent.get("currency"))
        try:
            intended_amount = int(intent.get("stripe_amount_total"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=409, detail="Intención RTM sin importe verificable")

        metadata_billing = normalize_code(metadata.get("billing_code"))
        metadata_service = normalize_code(metadata.get("service_code"))
        if (
            intended_stage != payment_stage
            or intended_billing != metadata_billing
            or intended_service != metadata_service
            or str(intent.get("authority_version") or "")
            != str(metadata.get("authority_version") or "")
            or intended_amount != session_amount
            or intended_currency != session_currency
            or normalize_code(case_row[2]) != intended_billing
            or str(intent.get("authority_material_sha256") or "")
            != str(metadata.get("authority_material_sha256") or "")
            or str(intent.get("signed_document_attestation_sha256") or "")
            != str(metadata.get("signed_document_attestation_sha256") or "")
        ):
            raise HTTPException(status_code=409, detail="Liquidación Stripe no coincide con la intención RTM")

        metadata_amount = str(metadata.get("amount_cents") or "").strip()
        if metadata_amount:
            try:
                amount_from_metadata = int(metadata_amount)
            except ValueError:
                raise HTTPException(status_code=400, detail="Importe de metadata inválido")
            authoritative_amount = intent.get("amount_cents")
            if authoritative_amount is None or amount_from_metadata != int(authoritative_amount):
                raise HTTPException(status_code=409, detail="Tarifa de metadata no autoritativa")
        if _normalized_currency(metadata.get("currency")) != intended_currency:
            raise HTTPException(status_code=409, detail="Moneda de metadata no autoritativa")

        current_authority = verify_signed_case_authority(conn, case_id)
        current_authority_sha256 = str(
            current_authority.get("material_sha256") or ""
        )
        current_signed_sha256 = str(
            current_authority.get("signed_document_attestation", {}).get(
                "material_sha256"
            )
            or ""
        )
        if (
            not hmac.compare_digest(
                current_authority_sha256,
                str(intent.get("authority_material_sha256") or ""),
            )
            or not hmac.compare_digest(
                current_signed_sha256,
                str(intent.get("signed_document_attestation_sha256") or ""),
            )
        ):
            raise HTTPException(
                status_code=409,
                detail="La autorización ya no es válida para liquidar este pago",
            )

        current_payment_status = str(case_row[0] or "").strip().lower()
        stored_payment_intent = str(case_row[3] or "").strip()
        current_case_status = str(
            _case_row_value(case_row, 4, "status") or ""
        ).strip()
        if current_payment_status == "paid":
            if not hmac.compare_digest(stored_payment_intent, payment_intent):
                raise HTTPException(
                    status_code=409,
                    detail="La repetición no coincide con el pago guardado",
                )
            return {
                "ok": True,
                "replayed": True,
                "case_id": case_id,
                "session": session_id,
            }
        if current_payment_status in _CHECKOUT_RECONCILIATION_PAYMENT_STATUSES:
            return {
                "ok": True,
                "reconciled": True,
                "case_id": case_id,
                "session": session_id,
            }
        if current_payment_status != "pending":
            raise HTTPException(
                status_code=409,
                detail="El expediente no está pendiente de esta liquidación",
            )
        if current_case_status != "ready_for_review_payment":
            raise HTTPException(
                status_code=409,
                detail="El expediente cambió y el pago requiere conciliación",
            )

        updated = conn.execute(
            text(
                """
                UPDATE cases
                SET payment_status='paid',
                    paid_at=NOW(),
                    stripe_payment_intent=:pi,
                    status='manual_review',
                    updated_at=NOW()
                WHERE id=:id
                  AND stripe_session_id=:sid
                  AND payment_status='pending'
                  AND status='ready_for_review_payment'
                RETURNING id
                """
            ),
            {"id": case_id, "sid": session_id, "pi": payment_intent},
        ).fetchone()
        if not updated:
            raise HTTPException(status_code=409, detail="Liquidación concurrente en conflicto")

        settlement_evidence = {
            "stripe_event_id": event_id,
            "session": session_id,
            "payment_intent": payment_intent,
            "payment_stage": intended_stage,
            "billing_code": intent.get("billing_code"),
            "service_code": intent.get("authoritative_service_code"),
            "authority_version": intent.get("authority_version"),
            "authority_material_sha256": intent.get("authority_material_sha256"),
            "signed_document_attestation_sha256": intent.get(
                "signed_document_attestation_sha256"
            ),
            "amount_total": session_amount,
            "currency": session_currency,
        }
        _append_event(conn, case_id, "paid_ok", settlement_evidence)

        if intended_stage not in _REVIEW_STAGES:
            raise HTTPException(status_code=400, detail="Fase de liquidación no reconocida")
        _activate_post_payment_review(conn, case_id, session_id)

    return {"ok": True, "processed": True, "case_id": case_id, "session": session_id}


@router.get("/billing/status/{case_id}")
@router.get("/status/{case_id}")
def payment_status(
    case_id: str,
    x_case_token: str | None = Header(default=None, alias="X-RTM-Case-Token"),
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
):
    case_id = require_case_or_operator_access(
        case_id, x_case_token, x_operator_token
    )
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT payment_status, paid_at, product_code, authorized, status
                FROM cases WHERE id=:id
                """
            ),
            {"id": case_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="case_id no existe")

    return {
        "ok": True,
        "payment_status": row.payment_status,
        "paid_at": row.paid_at,
        "product_code": row.product_code,
        "authorized": bool(row.authorized),
        "status": row.status,
    }
