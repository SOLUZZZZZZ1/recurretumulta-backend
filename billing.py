# billing.py — Stripe Checkout + Webhook (MVP pago único por expediente)
import json
import os
from typing import Optional

import stripe
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from database import get_engine

router = APIRouter(prefix="/billing", tags=["billing"])


def _env(name: str) -> str:
    v = (os.getenv(name) or "").strip()
    if not v:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return v


def _optional(name: str, default: str) -> str:
    v = (os.getenv(name) or "").strip()
    return v or default


def _price_for_product(product: str) -> str:
    product = (product or "").strip().upper()
    if product == "DGT_PRESENTACION":
        return _env("STRIPE_PRICE_ID_DGT")
    if product == "AYTO_PRESENTACION":
        return _env("STRIPE_PRICE_ID_AYTO")
    if product == "CASO_COMPLEJO":
        return _env("STRIPE_PRICE_ID_CASO_COMPLEJO")
    raise HTTPException(status_code=400, detail="Producto no válido")


class CheckoutRequest(BaseModel):
    case_id: str = Field(..., description="UUID del expediente interno")
    product: str = Field(
        ..., description="DGT_PRESENTACION | AYTO_PRESENTACION | CASO_COMPLEJO"
    )
    email: EmailStr = Field(..., description="Email del cliente (para recibo y trazabilidad)")
    locale: Optional[str] = Field(default="es")


@router.post("/checkout")
def create_checkout(req: CheckoutRequest):
    try:
        stripe.api_key = _env("STRIPE_SECRET_KEY")

        # ✅ IMPORTANTE: FRONTEND_URL debe ser un string (env en Render)
        # Ejemplo: https://www.recurretumulta.eu
        frontend_url = _optional("FRONTEND_URL", "https://www.recurretumulta.eu").rstrip("/")

        price_id = _price_for_product(req.product)

        engine = get_engine()
        with engine.begin() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM cases WHERE id = :case_id"),
                {"case_id": req.case_id},
            ).fetchone()
            if not exists:
                raise HTTPException(status_code=404, detail="case_id no existe")

            conn.execute(
                text("UPDATE cases SET contact_email=:email, updated_at=NOW() WHERE id=:case_id"),
                {"email": str(req.email), "case_id": req.case_id},
            )

            conn.execute(
                text(
                    """UPDATE cases
                       SET payment_status='pending', product_code=:product, updated_at=NOW()
                       WHERE id=:case_id"""
                ),
                {"case_id": req.case_id, "product": req.product},
            )

        # ✅ HashRouter: /#/pago-ok y /#/pago-cancel
        success_url = f"{frontend_url}/#/pago-ok?case={req.case_id}"
        cancel_url = f"{frontend_url}/#/pago-cancel?case={req.case_id}"

        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{"price": price_id, "quantity": 1}],
            customer_email=str(req.email),
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "case_id": req.case_id,
                "product": req.product,
                "email": str(req.email),
            },
            locale=req.locale or "es",
        )

        return {"ok": True, "url": session.url, "session_id": session.id}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creando checkout: {e}")


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        stripe.api_key = _env("STRIPE_SECRET_KEY")
        webhook_secret = _env("STRIPE_WEBHOOK_SECRET")
        event = stripe.Webhook.construct_event(
            payload=payload, sig_header=sig_header, secret=webhook_secret
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook invalid: {e}")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        meta = session.get("metadata") or {}
        case_id = meta.get("case_id")
        product = meta.get("product")
        payment_intent = session.get("payment_intent")
        session_id = session.get("id")

        if case_id:
            engine = get_engine()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """UPDATE cases
                           SET payment_status='paid',
                               stripe_session_id=:sid,
                               stripe_payment_intent=:pi,
                               paid_at=NOW(),
                               updated_at=NOW()
                           WHERE id=:case_id"""
                    ),
                    {"sid": session_id, "pi": payment_intent, "case_id": case_id},
                )
                conn.execute(
                    text(
                        """INSERT INTO events(case_id, type, payload, created_at)
                           VALUES (:case_id, 'paid_ok', CAST(:payload AS JSONB), NOW())"""
                    ),
                    {
                        "case_id": case_id,
                        "payload": json.dumps(
                            {"product": product, "stripe_session_id": session_id}
                        ),
                    },
                )

    return {"ok": True}
