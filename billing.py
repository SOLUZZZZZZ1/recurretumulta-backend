"""Cobros RTM: tarifa autoritativa, expediente mínimo y activación de revisión.

La revisión inicial se decide exclusivamente desde el expediente persistido.
El navegador no puede elegir la tarifa y el pago no ejecuta clasificadores,
especialistas ni Generate.
"""

from __future__ import annotations

import json
import os

import stripe
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from database import get_engine
from case_authority import verify_signed_case_authority
from public_case_access import require_case_access_token, require_case_or_operator_access
from rtm_core.repository import build_case_review_readiness, load_case_review_snapshot
from rtm_core.runtime_capabilities import require_http_capability
from rtm_core.service_catalog import normalize_code


router = APIRouter(tags=["billing"])

_REVIEW_STAGES = {"review", "revision", "initial", "inicial", "revision_inicial"}
_FINAL_STAGES = {"final", "gestion", "management"}


def _env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return value


class CheckoutRequest(BaseModel):
    case_id: str
    product: str | None = None  # Compatibilidad: nunca decide la tarifa de estudio.
    email: EmailStr | None = None
    locale: str | None = "es"
    payment_stage: str | None = "review"


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


def _resolve_final_stripe_product(product: str | None, payment_stage: str | None) -> dict:
    """Compatibilidad para servicios finales ya existentes.

    La revisión inicial no entra aquí: siempre se resuelve desde el catálogo
    RTM y los datos guardados del expediente.
    """

    product_code = normalize_code(product)
    stage = _normalized_stage(payment_stage)

    if stage not in _FINAL_STAGES:
        raise HTTPException(status_code=400, detail=f"Fase de pago no reconocida: {payment_stage}")

    vehicle_codes = {
        "vehicle",
        "vehiculo",
        "vehiculos",
        "vehicle_removal",
        "eliminacion_vehiculo",
        "eliminacion_vehiculos",
    }
    asnef_codes = {"asnef", "asnef_equifax", "equifax", "badexcug"}
    fine_codes = {"dgt", "fine", "multa", "multas", "trafico", "traffic"}

    if product_code in asnef_codes:
        raise HTTPException(
            status_code=409,
            detail="ASNEF requiere presupuesto después de la revisión inicial",
        )
    if product_code in vehicle_codes:
        return {
            "price_id": _env("STRIPE_PRICE_ID_VEHICLE"),
            "billing_code": "VEHICLE",
            "service_code": product_code,
            "payment_stage": "final",
            "amount_cents": None,
            "currency": "EUR",
            "authority_version": "legacy_final_catalog_v1",
        }
    if product_code in fine_codes:
        return {
            "price_id": _env("STRIPE_PRICE_ID_DGT"),
            "billing_code": "DGT",
            "service_code": product_code,
            "payment_stage": "final",
            "amount_cents": None,
            "currency": "EUR",
            "authority_version": "legacy_final_catalog_v1",
        }

    raise HTTPException(
        status_code=409,
        detail="La gestión final requiere valoración y presupuesto previo",
    )


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


def _activate_post_payment_review(conn, case_id: str, session_id: str) -> dict:
    """El pago activa OPS; no crea hechos, familia, estrategia ni borrador."""

    conn.execute(
        text(
            """
            UPDATE cases
            SET status='manual_review', updated_at=NOW()
            WHERE id=:id
            """
        ),
        {"id": case_id},
    )

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
    readiness = build_case_review_readiness(snapshot)
    return {
        "ok": True,
        "case_id": case_id,
        "readiness": readiness.model_dump(mode="json"),
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

    stripe.api_key = _env("STRIPE_SECRET_KEY")
    frontend_url = _env("FRONTEND_URL").rstrip("/")

    engine = get_engine()
    with engine.begin() as conn:
        snapshot = load_case_review_snapshot(conn, case_id)

        if stage in _REVIEW_STAGES:
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

            if snapshot.payment_status == "paid":
                return {
                    "ok": True,
                    "already_paid": True,
                    "redirect": f"{frontend_url}/#/resumen?case={case_id}",
                    "billing_code": stripe_product["billing_code"],
                    "amount_cents": stripe_product["amount_cents"],
                    "currency": stripe_product["currency"],
                }
        elif stage in _FINAL_STAGES:
            if not snapshot.authorized:
                raise HTTPException(status_code=409, detail="Debes autorizar antes de pagar")
            signed_authority = verify_signed_case_authority(conn, case_id)
            stripe_product = _resolve_final_stripe_product(req.product, stage)
            readiness = None
        else:
            raise HTTPException(status_code=400, detail=f"Fase de pago no reconocida: {req.payment_stage}")

        checkout_email = _safe_checkout_email(snapshot, req.email)
        requested_product = normalize_code(req.product)

    authority_material_sha256 = (
        str(signed_authority.get("material_sha256") or "")
        if stage in _FINAL_STAGES
        else ""
    )
    signed_document_attestation_sha256 = (
        str(
            signed_authority.get("signed_document_attestation", {}).get(
                "material_sha256"
            )
            or ""
        )
        if stage in _FINAL_STAGES
        else ""
    )

    success_url = f"{frontend_url}/#/pago-ok?case={case_id}"
    cancel_url = f"{frontend_url}/#/resumen?case={case_id}"

    metadata = {
        "case_id": case_id,
        "requested_product": normalize_code(req.product),
        "service_code": stripe_product["service_code"],
        "billing_code": stripe_product["billing_code"],
        "payment_stage": stripe_product["payment_stage"],
        "authority_version": stripe_product["authority_version"],
        "amount_cents": str(stripe_product["amount_cents"] or ""),
        "currency": stripe_product["currency"],
        "authority_material_sha256": authority_material_sha256,
        "signed_document_attestation_sha256": signed_document_attestation_sha256,
    }

    idempotency_scope = (
        authority_material_sha256
        or (readiness.version if readiness is not None else "no_authority")
    )

    session = stripe.checkout.Session.create(
        mode="payment",
        customer_email=checkout_email,
        line_items=[{"price": stripe_product["price_id"], "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata,
        locale=req.locale or "es",
        idempotency_key=(
            f"rtm-checkout:{case_id}:{stripe_product['payment_stage']}:"
            f"{stripe_product['billing_code']}:{idempotency_scope}"
        ),
    )

    session_id = str(_object_value(session, "id") or "").strip()
    session_url = str(_object_value(session, "url") or "").strip()
    try:
        session_amount = int(_object_value(session, "amount_total"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=502, detail="Stripe no devolvió un importe verificable")
    session_currency = _normalized_currency(_object_value(session, "currency"))
    session_metadata = _payload_dict(_object_value(session, "metadata") or {})
    if not session_id or not session_url:
        raise HTTPException(status_code=502, detail="Stripe no devolvió una sesión utilizable")
    if (
        stripe_product["amount_cents"] is not None
        and session_amount != int(stripe_product["amount_cents"])
    ):
        raise HTTPException(status_code=502, detail="Importe Stripe distinto de la tarifa RTM")
    if session_currency != _normalized_currency(stripe_product["currency"]):
        raise HTTPException(status_code=502, detail="Moneda Stripe distinta de la tarifa RTM")
    if any(str(session_metadata.get(key) or "") != str(value) for key, value in metadata.items()):
        raise HTTPException(status_code=502, detail="Metadata Stripe distinta de la intención RTM")

    checkout_evidence = {
        "session": session_id,
        "requested_product": requested_product,
        "authoritative_service_code": stripe_product["service_code"],
        "billing_code": stripe_product["billing_code"],
        "payment_stage": stripe_product["payment_stage"],
        "amount_cents": stripe_product["amount_cents"],
        "stripe_amount_total": session_amount,
        "currency": session_currency,
        "authority_version": stripe_product["authority_version"],
        "authority_material_sha256": authority_material_sha256,
        "signed_document_attestation_sha256": signed_document_attestation_sha256,
        "requested_email_matches_persisted": checkout_email.lower()
        == str(req.email or "").strip().lower(),
        "authorized": bool(snapshot.authorized),
        "authorized_at": str(snapshot.authorized_at or ""),
        "readiness_version": readiness.version if readiness is not None else None,
    }
    with engine.begin() as conn:
        current = conn.execute(
            text("SELECT payment_status FROM cases WHERE id=:id FOR UPDATE"),
            {"id": case_id},
        ).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Expediente no encontrado")
        if str(current[0] or "").lower() == "paid":
            raise HTTPException(status_code=409, detail="El expediente ya consta como pagado")
        if stage in _FINAL_STAGES:
            current_authority = verify_signed_case_authority(conn, case_id)
            if (
                str(current_authority.get("material_sha256") or "")
                != authority_material_sha256
                or str(
                    current_authority.get("signed_document_attestation", {}).get(
                        "material_sha256"
                    )
                    or ""
                )
                != signed_document_attestation_sha256
            ):
                raise HTTPException(
                    status_code=409,
                    detail="La autoridad firmada cambió durante la creación del pago",
                )
        conn.execute(
            text(
                """
                UPDATE cases
                SET payment_status='pending',
                    stripe_session_id=:session_id,
                    product_code=:product,
                    contact_email=:email,
                    updated_at=NOW()
                WHERE id=:id
                """
            ),
            {
                "id": case_id,
                "session_id": session_id,
                "product": stripe_product["billing_code"],
                "email": checkout_email,
            },
        )
        _append_event(conn, case_id, "checkout_started", checkout_evidence)
        _append_event(conn, case_id, "checkout_session_created", checkout_evidence)

    return {
        "ok": True,
        "url": session_url,
        "billing_code": stripe_product["billing_code"],
        "payment_stage": stripe_product["payment_stage"],
        "service_code": stripe_product["service_code"],
        "amount_cents": stripe_product["amount_cents"],
        "currency": stripe_product["currency"],
        "authority_version": stripe_product["authority_version"],
    }


@router.post("/billing/webhook")
@router.post("/webhook")
async def stripe_webhook(request: Request):
    require_http_capability("stripe")
    payload = await request.body()
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
    if event_type != "checkout.session.completed":
        return {"ok": True, "ignored": True, "event_type": event_type}

    event_id = str(_object_value(event, "id") or "").strip()
    data_object = _object_value(event, "data") or {}
    session = _object_value(data_object, "object") or {}
    metadata = _payload_dict(_object_value(session, "metadata") or {})
    case_id = str(metadata.get("case_id") or "").strip()
    session_id = str(_object_value(session, "id") or "").strip()
    payment_intent = _stripe_object_id(_object_value(session, "payment_intent"))
    payment_stage = normalize_code(metadata.get("payment_stage"))
    session_payment_status = str(
        _object_value(session, "payment_status") or ""
    ).strip().lower()
    session_mode = str(_object_value(session, "mode") or "").strip().lower()
    session_currency = _normalized_currency(_object_value(session, "currency"))
    try:
        session_amount = int(_object_value(session, "amount_total"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Webhook sin importe verificable")

    if not event_id or not case_id or not session_id:
        raise HTTPException(status_code=400, detail="Webhook sin identificadores obligatorios")
    if session_mode != "payment" or session_payment_status != "paid" or not payment_intent:
        raise HTTPException(status_code=409, detail="La sesión no acredita un pago liquidado")
    if payment_stage not in (_REVIEW_STAGES | _FINAL_STAGES):
        raise HTTPException(status_code=400, detail="Fase de pago no reconocida en webhook")

    engine = get_engine()
    with engine.begin() as conn:
        case_row = conn.execute(
            text(
                "SELECT payment_status, stripe_session_id, product_code "
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

        if str(case_row[0] or "").strip().lower() == "paid":
            return {
                "ok": True,
                "replayed": True,
                "case_id": case_id,
                "session": session_id,
            }

        if intended_stage in _FINAL_STAGES:
            current_authority = verify_signed_case_authority(conn, case_id)
            if (
                str(current_authority.get("material_sha256") or "")
                != str(intent.get("authority_material_sha256") or "")
                or str(
                    current_authority.get("signed_document_attestation", {}).get(
                        "material_sha256"
                    )
                    or ""
                )
                != str(intent.get("signed_document_attestation_sha256") or "")
            ):
                raise HTTPException(
                    status_code=409,
                    detail="La liquidación no conserva la autoridad firmada vigente",
                )

        updated = conn.execute(
            text(
                """
                UPDATE cases
                SET payment_status='paid',
                    paid_at=NOW(),
                    stripe_payment_intent=:pi,
                    updated_at=NOW()
                WHERE id=:id
                  AND stripe_session_id=:sid
                  AND payment_status IS DISTINCT FROM 'paid'
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

        if intended_stage in _REVIEW_STAGES:
            _activate_post_payment_review(conn, case_id, session_id)
        elif intended_stage in _FINAL_STAGES:
            _append_event(conn, case_id, "final_payment_confirmed", settlement_evidence)
        else:
            raise HTTPException(status_code=400, detail="Fase de liquidación no reconocida")

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
