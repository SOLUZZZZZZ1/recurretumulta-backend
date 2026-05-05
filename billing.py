import os
import json

import stripe
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from database import get_engine

router = APIRouter(tags=["billing"])


class CheckoutRequest(BaseModel):
    case_id: str
    product: str = "dgt"
    email: EmailStr
    locale: str = "es"


class ConfirmPaymentRequest(BaseModel):
    case_id: str
    session_id: str | None = None


def _env(name: str, default: str | None = None) -> str:
    v = (os.getenv(name) or "").strip()
    if not v:
        if default is not None:
            return default
        raise HTTPException(status_code=500, detail=f"Falta variable de entorno: {name}")
    return v


def _append_event(conn, case_id: str, event_type: str, payload: dict | None = None) -> None:
    try:
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
    except Exception:
        pass


def _require_case_authorized_before_payment(conn, case_id: str) -> dict:
    row = conn.execute(
        text(
            """
            SELECT payment_status, authorized, authorized_at, contact_email,
                   COALESCE(interested_data, '{}'::jsonb) AS interested_data
            FROM cases
            WHERE id=:id
            """
        ),
        {"id": case_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")

    interested = row[4] if isinstance(row[4], dict) else {}
    authorized = bool(row[1])

    if not authorized:
        raise HTTPException(
            status_code=400,
            detail="Primero debes completar y subir la autorización firmada.",
        )

    return {
        "payment_status": row[0] or "",
        "authorized": authorized,
        "authorized_at": row[2],
        "contact_email": row[3] or interested.get("email") or "",
    }


def _mark_paid_core(
    conn,
    case_id: str,
    *,
    session_id: str | None = None,
    payment_intent: str | None = None,
    email: str | None = None,
    source: str = "unknown",
) -> None:
    """
    Marca el pago como confirmado de forma segura.
    No depende de que la generación del recurso salga bien.
    """
    conn.execute(
        text(
            """
            UPDATE cases
            SET payment_status='paid',
                authorized=TRUE,
                status=CASE
                    WHEN status IN ('generated', 'submitted', 'vehicle_removal_paid') THEN status
                    ELSE 'manual_review'
                END,
                stripe_checkout_session_id=COALESCE(:session_id, stripe_checkout_session_id),
                stripe_payment_intent_id=COALESCE(:payment_intent, stripe_payment_intent_id),
                contact_email=COALESCE(:email, contact_email),
                paid_at=COALESCE(paid_at, NOW()),
                updated_at=NOW()
            WHERE id=:id
            """
        ),
        {
            "id": case_id,
            "session_id": session_id,
            "payment_intent": payment_intent,
            "email": email,
        },
    )

    _append_event(
        conn,
        case_id,
        "payment_confirmed",
        {
            "source": source,
            "session_id": session_id,
            "payment_intent": payment_intent,
            "email": email,
        },
    )


def _try_generate_after_payment(conn, case_id: str) -> None:
    """
    Intenta generar DOCX/PDF. Si falla, no rompe el pago.
    El caso queda en manual_review para reintento desde OPS.
    """
    try:
        from generate import generate_dgt_for_case

        generate_dgt_for_case(conn, case_id)

        conn.execute(
            text(
                """
                UPDATE cases
                SET status='generated',
                    updated_at=NOW()
                WHERE id=:id
                """
            ),
            {"id": case_id},
        )

        _append_event(conn, case_id, "resource_generated_after_payment", {"ok": True})

    except Exception as gen_err:
        _append_event(
            conn,
            case_id,
            "resource_generation_after_payment_failed",
            {"error": f"{type(gen_err).__name__}: {gen_err}"},
        )


@router.get("/billing/status/{case_id}")
@router.get("/status/{case_id}")
def billing_status(case_id: str):
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT payment_status, authorized, status, contact_email
                FROM cases
                WHERE id=:id
                """
            ),
            {"id": case_id},
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")

    return {
        "ok": True,
        "case_id": case_id,
        "payment_status": row[0] or "",
        "authorized": bool(row[1]),
        "status": row[2] or "",
        "email": row[3] or "",
    }


@router.post("/billing/checkout")
@router.post("/checkout")
def create_checkout(req: CheckoutRequest):
    try:
        stripe.api_key = _env("STRIPE_SECRET_KEY")

        frontend_url = (
            os.getenv("FRONTEND_URL")
            or os.getenv("FRONTEND_BASE_URL")
            or "https://www.recurretumulta.eu"
        ).strip().rstrip("/")

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
                {"id": req.case_id, "product": req.product, "email": str(req.email)},
            )

            _append_event(
                conn,
                req.case_id,
                "checkout_started",
                {
                    "product": req.product,
                    "email": str(req.email),
                    "authorized": True,
                    "authorized_at": str(auth_meta["authorized_at"] or ""),
                },
            )

        price_id = _env("STRIPE_PRICE_ID_DGT")

        # Doble seguridad: Stripe devolverá el session_id a pago-ok.
        success_url = f"{frontend_url}/#/pago-ok?case={req.case_id}&session_id={{CHECKOUT_SESSION_ID}}"
        cancel_url = f"{frontend_url}/#/resumen?case={req.case_id}"

        session = stripe.checkout.Session.create(
            mode="payment",
            customer_email=str(req.email),
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"case_id": req.case_id},
            locale=req.locale or "es",
        )

        return {"ok": True, "url": session.url}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error en /billing/checkout: {type(e).__name__}: {e}",
        )


@router.post("/billing/confirm")
@router.post("/confirm")
def confirm_payment(req: ConfirmPaymentRequest):
    """
    Doble seguridad: pago-ok llama aquí al volver de Stripe.
    Verifica en Stripe y marca paid aunque el webhook falle o tarde.
    """
    stripe.api_key = _env("STRIPE_SECRET_KEY")

    session_id = (req.session_id or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="Falta session_id")

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"No se pudo verificar Stripe: {type(e).__name__}: {e}")

    metadata = getattr(session, "metadata", None) or {}
    stripe_case_id = metadata.get("case_id")
    payment_status = getattr(session, "payment_status", "") or ""
    payment_intent = getattr(session, "payment_intent", None)
    customer_email = getattr(session, "customer_email", None)

    if stripe_case_id and stripe_case_id != req.case_id:
        raise HTTPException(status_code=400, detail="La sesión de Stripe no corresponde a este expediente")

    if payment_status != "paid":
        raise HTTPException(status_code=400, detail=f"Stripe aún no confirma pago paid. Estado: {payment_status}")

    engine = get_engine()
    with engine.begin() as conn:
        _mark_paid_core(
            conn,
            req.case_id,
            session_id=session_id,
            payment_intent=str(payment_intent or ""),
            email=str(customer_email or ""),
            source="frontend_confirm",
        )
        _try_generate_after_payment(conn, req.case_id)

    return {"ok": True, "case_id": req.case_id, "payment_status": "paid"}


@router.post("/billing/webhook")
@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    webhook_secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()

    try:
        if webhook_secret:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        else:
            event = json.loads(payload.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook inválido: {type(e).__name__}: {e}")

    event_type = event.get("type")
    data_object = (event.get("data") or {}).get("object") or {}

    if event_type == "checkout.session.completed":
        case_id = (data_object.get("metadata") or {}).get("case_id")
        session_id = data_object.get("id")
        payment_intent = data_object.get("payment_intent")
        customer_email = data_object.get("customer_email") or (data_object.get("customer_details") or {}).get("email")
        payment_status = data_object.get("payment_status") or "paid"

        if not case_id:
            return {"ok": True, "ignored": "missing_case_id"}

        engine = get_engine()
        with engine.begin() as conn:
            _mark_paid_core(
                conn,
                case_id,
                session_id=session_id,
                payment_intent=payment_intent,
                email=customer_email,
                source="stripe_webhook",
            )

            if payment_status == "paid":
                _try_generate_after_payment(conn, case_id)

    return {"ok": True}
