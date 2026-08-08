"""Cobros RTM: tarifa autoritativa, expediente mínimo y activación de revisión.

La revisión inicial se decide exclusivamente desde el expediente persistido.
El navegador no puede elegir la tarifa y el pago no ejecuta clasificadores,
especialistas ni Generate.
"""

from __future__ import annotations

import json
import os

import stripe
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from database import get_engine
from rtm_core.repository import build_case_review_readiness, load_case_review_snapshot
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
    email: EmailStr
    locale: str | None = "es"
    payment_stage: str | None = "review"


def _normalized_stage(value: str | None) -> str:
    return normalize_code(value) or "review"


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


def _safe_checkout_email(snapshot, requested_email: str) -> str:
    persisted = str(snapshot.contact_email or "").strip()
    interested = snapshot.interested_data if isinstance(snapshot.interested_data, dict) else {}
    interested_email = str(interested.get("email") or "").strip()
    return persisted or interested_email or requested_email.strip()


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
def review_checkout_context(case_id: str):
    """Precio y requisitos del estudio sin exponer datos personales."""

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
def create_checkout(req: CheckoutRequest):
    stripe.api_key = _env("STRIPE_SECRET_KEY")
    frontend_url = _env("FRONTEND_URL").rstrip("/")
    stage = _normalized_stage(req.payment_stage)

    engine = get_engine()
    with engine.begin() as conn:
        snapshot = load_case_review_snapshot(conn, req.case_id)

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
                    "redirect": f"{frontend_url}/#/resumen?case={req.case_id}",
                    "billing_code": stripe_product["billing_code"],
                    "amount_cents": stripe_product["amount_cents"],
                    "currency": stripe_product["currency"],
                }
        elif stage in _FINAL_STAGES:
            if not snapshot.authorized:
                raise HTTPException(status_code=409, detail="Debes autorizar antes de pagar")
            stripe_product = _resolve_final_stripe_product(req.product, stage)
            readiness = None
        else:
            raise HTTPException(status_code=400, detail=f"Fase de pago no reconocida: {req.payment_stage}")

        checkout_email = _safe_checkout_email(snapshot, str(req.email))
        requested_product = normalize_code(req.product)

        conn.execute(
            text(
                """
                UPDATE cases
                SET payment_status='pending',
                    product_code=:product,
                    contact_email=:email,
                    updated_at=NOW()
                WHERE id=:id
                """
            ),
            {
                "id": req.case_id,
                "product": stripe_product["billing_code"],
                "email": checkout_email,
            },
        )

        _append_event(
            conn,
            req.case_id,
            "checkout_started",
            {
                "requested_product": requested_product,
                "authoritative_service_code": stripe_product["service_code"],
                "billing_code": stripe_product["billing_code"],
                "payment_stage": stripe_product["payment_stage"],
                "amount_cents": stripe_product["amount_cents"],
                "currency": stripe_product["currency"],
                "authority_version": stripe_product["authority_version"],
                "requested_email_matches_persisted": checkout_email.lower()
                == str(req.email).strip().lower(),
                "authorized": bool(snapshot.authorized),
                "authorized_at": str(snapshot.authorized_at or ""),
                "readiness_version": readiness.version if readiness is not None else None,
            },
        )

    success_url = f"{frontend_url}/#/pago-ok?case={req.case_id}"
    cancel_url = f"{frontend_url}/#/resumen?case={req.case_id}"

    metadata = {
        "case_id": req.case_id,
        "requested_product": normalize_code(req.product),
        "service_code": stripe_product["service_code"],
        "billing_code": stripe_product["billing_code"],
        "payment_stage": stripe_product["payment_stage"],
        "authority_version": stripe_product["authority_version"],
        "amount_cents": str(stripe_product["amount_cents"] or ""),
        "currency": stripe_product["currency"],
    }

    session = stripe.checkout.Session.create(
        mode="payment",
        customer_email=checkout_email,
        line_items=[{"price": stripe_product["price_id"], "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata,
        locale=req.locale or "es",
    )

    return {
        "ok": True,
        "url": session.url,
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

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        metadata = session.get("metadata") or {}
        case_id = metadata.get("case_id")
        if not case_id:
            raise HTTPException(status_code=400, detail="Webhook sin case_id")

        payment_stage = _normalized_stage(metadata.get("payment_stage"))
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE cases
                    SET payment_status='paid',
                        paid_at=NOW(),
                        stripe_session_id=:sid,
                        stripe_payment_intent=:pi,
                        product_code=COALESCE(:billing_code, product_code),
                        updated_at=NOW()
                    WHERE id=:id
                    """
                ),
                {
                    "id": case_id,
                    "sid": session["id"],
                    "pi": session.get("payment_intent"),
                    "billing_code": metadata.get("billing_code"),
                },
            )
            _append_event(
                conn,
                case_id,
                "paid_ok",
                {
                    "session": session["id"],
                    "payment_stage": payment_stage,
                    "billing_code": metadata.get("billing_code"),
                    "service_code": metadata.get("service_code"),
                    "authority_version": metadata.get("authority_version"),
                },
            )

            if payment_stage in _REVIEW_STAGES:
                _activate_post_payment_review(conn, case_id, session["id"])
            else:
                _append_event(
                    conn,
                    case_id,
                    "final_payment_confirmed",
                    {
                        "session": session["id"],
                        "billing_code": metadata.get("billing_code"),
                    },
                )

    return {"ok": True}


@router.get("/billing/status/{case_id}")
@router.get("/status/{case_id}")
def payment_status(case_id: str):
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
