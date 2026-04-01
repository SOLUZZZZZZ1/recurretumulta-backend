# billing.py — Stripe Checkout + Webhook + Payment Status (bloqueo por autorización)
import json
import os
import stripe
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from database import get_engine

router = APIRouter(prefix="/billing", tags=["billing"])


def _env(name: str) -> str:
    v = (os.getenv(name) or "").strip()
    if not v:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return v


class CheckoutRequest(BaseModel):
    case_id: str
    product: str
    email: EmailStr
    locale: str | None = "es"


def _require_case_authorized_before_payment(conn, case_id: str):
    row = conn.execute(
        text(
            """
            SELECT
                id,
                COALESCE(authorized, FALSE) AS authorized,
                authorized_at,
                COALESCE(payment_status, '') AS payment_status,
                COALESCE(interested_data, '{}'::jsonb) AS interested_data
            FROM cases
            WHERE id = :id
            """
        ),
        {"id": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="case_id no existe")

    interested_data = row[4] if isinstance(row[4], dict) else {}

    missing = []
    if not interested_data.get("full_name"):
        missing.append("full_name")
    if not interested_data.get("dni_nie"):
        missing.append("dni_nie")
    if not interested_data.get("domicilio_notif"):
        missing.append("domicilio_notif")
    if not interested_data.get("email"):
        missing.append("email")

    if missing:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Debes completar los datos del interesado antes de pagar",
                "missing_fields": missing,
            },
        )

    if not bool(row[1]):
        raise HTTPException(
            status_code=409,
            detail="Debes autorizar antes de pagar",
        )

    return {
        "case_id": str(row[0]),
        "authorized": bool(row[1]),
        "authorized_at": row[2],
        "payment_status": row[3],
        "interested_data": interested_data,
    }


@router.post("/checkout")
def create_checkout(req: CheckoutRequest):
    stripe.api_key = _env("STRIPE_SECRET_KEY")
    frontend_url = _env("FRONTEND_URL").rstrip("/")

    engine = get_engine()
    with engine.begin() as conn:
        auth_meta = _require_case_authorized_before_payment(conn, req.case_id)

        if auth_meta["payment_status"] == "paid":
            return {
                "ok": True,
                "already_paid": True,
                "redirect": f"{frontend_url}/#/resumen?case={req.case_id}",
            }

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
            {"id": req.case_id, "product": req.product, "email": req.email},
        )

        conn.execute(
            text(
                """
                INSERT INTO events(case_id, type, payload, created_at)
                VALUES (:id, 'checkout_started', CAST(:p AS JSONB), NOW())
                """
            ),
            {
                "id": req.case_id,
                "p": json.dumps(
                    {
                        "product": req.product,
                        "email": req.email,
                        "authorized": True,
                        "authorized_at": str(auth_meta["authorized_at"] or ""),
                    }
                ),
            },
        )

    price_id = _env("STRIPE_PRICE_ID_DGT")
    success_url = f"{frontend_url}/#/pago-ok?case={req.case_id}"
    cancel_url = f"{frontend_url}/#/resumen?case={req.case_id}"

    session = stripe.checkout.Session.create(
        mode="payment",
        customer_email=req.email,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"case_id": req.case_id},
        locale=req.locale or "es",
    )

    return {"ok": True, "url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    try:
        stripe.api_key = _env("STRIPE_SECRET_KEY")
        event = stripe.Webhook.construct_event(
            payload, sig_header, _env("STRIPE_WEBHOOK_SECRET")
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Webhook inválido")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        case_id = session["metadata"]["case_id"]
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
                        updated_at=NOW()
                    WHERE id=:id
                    """
                ),
                {"id": case_id, "sid": session["id"], "pi": session.get("payment_intent")},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO events(case_id, type, payload, created_at)
                    VALUES (:id, 'paid_ok', CAST(:p AS JSONB), NOW())
                    """
                ),
                {"id": case_id, "p": json.dumps({"session": session["id"]})},
            )
    return {"ok": True}


@router.get("/status/{case_id}")
def payment_status(case_id: str):
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT payment_status, paid_at, product_code, authorized
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
    }
