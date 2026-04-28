# billing_auto_modo_dios.py — checkout bloqueado por autorización + Modo Dios automático tras pago
import json
import os
import stripe
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from database import get_engine
from ai.expediente_engine import run_expediente_ai
from generate import generate_dgt_for_case
from email_utils import send_email, build_vehicle_removal_paid_email

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


def _pick(mapping, *paths):
    for path in paths:
        current = mapping
        ok = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current.get(part)
            else:
                ok = False
                break
        if ok and current not in (None, "", [], {}):
            return current
    return None


def _as_string(value):
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _as_confidence(value):
    if value in (None, "", [], {}):
        return None
    if isinstance(value, (int, float)):
        val = float(value)
    else:
        try:
            val = float(str(value).replace(",", "."))
        except Exception:
            return None

    if val > 1:
        val = val / 100.0
    if val < 0:
        val = 0.0
    if val > 1:
        val = 1.0
    return round(val, 4)


def _normalize_ai_payload(result):
    familia = _pick(
        result,
        "familia_resuelta",
        "tipo_infraccion",
        "classification.family",
        "classification.familia",
        "classifier_result.family",
        "classifier_result.familia",
        "arguments.family",
        "arguments.familia",
        "result.family",
        "result.familia",
        "extracted.tipo_infraccion",
        "extracted.familia_resuelta",
    )

    confianza = _pick(
        result,
        "tipo_infraccion_confidence",
        "classification.confidence",
        "classification.confianza",
        "classifier_result.confidence",
        "classifier_result.score",
        "arguments.confidence",
        "arguments.score",
        "result.confidence",
        "result.confianza",
        "extracted.tipo_infraccion_confidence",
    )

    hecho = _pick(
        result,
        "hecho_para_recurso",
        "hecho_imputado",
        "hecho_limpio",
        "hecho_reconstruido",
        "hecho_crudo",
        "arguments.hecho",
        "arguments.hecho_imputado",
        "arguments.fact",
        "arguments.facts",
        "result.hecho",
        "result.fact",
        "extracted.hecho_para_recurso",
        "extracted.hecho_imputado",
        "extracted.hecho_limpio",
        "extracted.hecho_denunciado_literal",
        "extracted.hecho_denunciado_resumido",
    )

    admisibilidad = _pick(
        result,
        "resultado_estrategico",
        "admissibility.admissibility",
        "phase.admissibility",
        "result.admissibility",
        "result.admisibilidad",
        "extracted.resultado_estrategico",
        "extracted.admissibility.admissibility",
    )

    accion_raw = _pick(
        result,
        "phase.recommended_action",
        "recommended_action",
        "phase.recommended_action.action",
        "recommended_action.action",
        "result.recommended_action",
        "result.accion_recomendada",
        "modelo_defensa",
        "extracted.modelo_defensa",
    )

    if isinstance(accion_raw, dict):
        accion = _pick({"x": accion_raw}, "x.action", "x.accion", "x.name", "x.tipo") or _as_string(accion_raw)
    else:
        accion = _as_string(accion_raw)

    familia_str = _as_string(familia)
    confianza_num = _as_confidence(confianza)
    hecho_str = _as_string(hecho)
    admisibilidad_str = _as_string(admisibilidad)

    return {
        "familia": familia_str,
        "confianza": confianza_num,
        "hecho": hecho_str,
        "admisibilidad": admisibilidad_str,
        "accion": accion,
        "classifier_result": {
            "family": familia_str,
            "confidence": confianza_num,
        },
        "tipo_infraccion": familia_str,
        "tipo_infraccion_confidence": confianza_num,
        "hecho_imputado": hecho_str,
        "raw_result": result,
    }


def _append_event(conn, case_id: str, event_type: str, payload: dict):
    conn.execute(
        text(
            """
            INSERT INTO events(case_id, type, payload, created_at)
            VALUES (:id, :type, CAST(:payload AS JSONB), NOW())
            """
        ),
        {"id": case_id, "type": event_type, "payload": json.dumps(payload, ensure_ascii=False)},
    )


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
        raise HTTPException(status_code=409, detail="Debes autorizar antes de pagar")

    return {
        "case_id": str(row[0]),
        "authorized": bool(row[1]),
        "authorized_at": row[2],
        "payment_status": row[3],
        "interested_data": interested_data,
    }


def _run_post_payment_modo_dios(conn, case_id: str):
    result = run_expediente_ai(case_id)
    if not isinstance(result, dict):
        result = {"raw_result": result}

    ai_payload = _normalize_ai_payload(result)

    _append_event(conn, case_id, "ai_expediente_result", ai_payload)

    try:
        generation_result = generate_dgt_for_case(conn, case_id)
    except Exception as gen_err:
        conn.execute(
            text("UPDATE cases SET status='manual_review', updated_at=NOW() WHERE id=:id"),
            {"id": case_id},
        )
        _append_event(
            conn,
            case_id,
            "resource_generation_failed",
            {
                "ok": False,
                "mode": "auto_post_payment",
                "error": str(gen_err),
            },
        )
        return {
            "ok": False,
            "stage": "generation",
            "error": str(gen_err),
            "ai_payload": ai_payload,
        }

    confidence = ai_payload.get("tipo_infraccion_confidence")
    low_confidence = confidence is None or confidence < 0.80

    if low_confidence:
        conn.execute(
            text("UPDATE cases SET status='manual_review', updated_at=NOW() WHERE id=:id"),
            {"id": case_id},
        )
        _append_event(
            conn,
            case_id,
            "auto_review_required_low_confidence",
            {
                "ok": True,
                "mode": "auto_post_payment",
                "confidence": confidence,
                "threshold": 0.80,
                "message": "Confianza inferior al 80%; revisión obligatoria por operador.",
            },
        )
        return {
            "ok": True,
            "stage": "manual_review",
            "confidence": confidence,
            "ai_payload": ai_payload,
            "generation_result": generation_result,
        }

    conn.execute(
        text("UPDATE cases SET status='generated', updated_at=NOW() WHERE id=:id"),
        {"id": case_id},
    )
    _append_event(
        conn,
        case_id,
        "resource_generated_auto",
        {
            "ok": True,
            "mode": "auto_post_payment",
            "confidence": confidence,
            "threshold": 0.80,
        },
    )
    return {
        "ok": True,
        "stage": "generated",
        "confidence": confidence,
        "ai_payload": ai_payload,
        "generation_result": generation_result,
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

        _append_event(
            conn,
            req.case_id,
            "checkout_started",
            {
                "product": req.product,
                "email": req.email,
                "authorized": True,
                "authorized_at": str(auth_meta["authorized_at"] or ""),
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
        metadata = session.get("metadata") or {}
        case_id = metadata.get("case_id")
        service = metadata.get("service")
        product_code = metadata.get("product_code")

        if not case_id:
            raise HTTPException(status_code=400, detail="Webhook sin case_id en metadata")

        engine = get_engine()
        with engine.begin() as conn:
            # Caso especial: producto "Eliminar coche".
            # No debe lanzar IA ni generar recurso DGT.
            if service == "vehicle_removal" or product_code == "ELIMINAR_COCHE":
                conn.execute(
                    text(
                        """
                        UPDATE cases
                        SET payment_status='paid',
                            status='vehicle_removal_paid',
                            paid_at=NOW(),
                            stripe_session_id=:sid,
                            stripe_payment_intent=:pi,
                            product_code='ELIMINAR_COCHE',
                            updated_at=NOW()
                        WHERE id=:id
                        """
                    ),
                    {"id": case_id, "sid": session["id"], "pi": session.get("payment_intent")},
                )

                row = conn.execute(
                    text(
                        """
                        SELECT contact_email, COALESCE(interested_data, '{}'::jsonb)
                        FROM cases
                        WHERE id=:id
                        """
                    ),
                    {"id": case_id},
                ).fetchone()

                contact_email = row[0] if row else None
                interested_data = row[1] if row and isinstance(row[1], dict) else {}

                ev = conn.execute(
                    text(
                        """
                        SELECT payload
                        FROM events
                        WHERE case_id=:id
                          AND type='vehicle_removal_request_created'
                        ORDER BY created_at DESC
                        LIMIT 1
                        """
                    ),
                    {"id": case_id},
                ).fetchone()

                vehicle_payload = ev[0] if ev and isinstance(ev[0], dict) else {}

                _append_event(
                    conn,
                    case_id,
                    "vehicle_removal_paid",
                    {
                        "session": session["id"],
                        "payment_intent": session.get("payment_intent"),
                        "service": "vehicle_removal",
                        "product_code": "ELIMINAR_COCHE",
                        "email": contact_email or vehicle_payload.get("email") or metadata.get("email"),
                    },
                )

                # Email automático al cliente. Nunca debe romper el webhook.
                try:
                    target_email = (
                        contact_email
                        or vehicle_payload.get("email")
                        or interested_data.get("email")
                        or metadata.get("email")
                    )

                    full_name = (
                        vehicle_payload.get("full_name")
                        or vehicle_payload.get("name")
                        or interested_data.get("full_name")
                        or metadata.get("full_name")
                        or "cliente"
                    )

                    plate = vehicle_payload.get("plate") or metadata.get("plate") or ""
                    city = vehicle_payload.get("city") or metadata.get("city") or ""

                    if target_email:
                        subject, body = build_vehicle_removal_paid_email(
                            case_id=case_id,
                            full_name=full_name,
                            plate=plate,
                            city=city,
                        )
                        sent = send_email(
                            to_email=target_email,
                            subject=subject,
                            body=body,
                        )

                        _append_event(
                            conn,
                            case_id,
                            "vehicle_removal_email_sent" if sent else "vehicle_removal_email_not_sent",
                            {
                                "to": target_email,
                                "sent": bool(sent),
                            },
                        )
                    else:
                        _append_event(
                            conn,
                            case_id,
                            "vehicle_removal_email_not_sent",
                            {"reason": "missing_email"},
                        )

                except Exception as email_err:
                    _append_event(
                        conn,
                        case_id,
                        "vehicle_removal_email_failed",
                        {"error": str(email_err)},
                    )

                return {"ok": True, "case_id": case_id, "service": "vehicle_removal"}

            # Flujo normal DGT / multas.
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
            _append_event(conn, case_id, "paid_ok", {"session": session["id"]})
            _run_post_payment_modo_dios(conn, case_id)

    return {"ok": True}


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
