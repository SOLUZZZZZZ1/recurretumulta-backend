from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from database import get_engine
import os
import json
import stripe

router = APIRouter(prefix="/vehicle-removal", tags=["vehicle-removal"])

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


def _env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return value


class VehicleRemovalRequest(BaseModel):
    name: str
    phone: str
    email: str | None = None
    plate: str
    city: str
    notes: str | None = None


@router.post("/create-checkout-session")
def create_checkout_session(data: VehicleRemovalRequest):
    """
    Crea un expediente interno tipo vehicle_removal, registra el evento
    y abre sesión de pago Stripe para el servicio Eliminar coche.
    """
    try:
        stripe.api_key = _env("STRIPE_SECRET_KEY")
        price_id = _env("STRIPE_PRICE_ID_ELIMINAR_COCHE")

        engine = get_engine()

        plate_clean = data.plate.strip().upper().replace(" ", "")
        email_clean = (data.email or "").strip() or None

        with engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO cases (
                        status,
                        payment_status,
                        product_code,
                        contact_email,
                        category,
                        updated_at
                    )
                    VALUES (
                        'vehicle_removal_pending_payment',
                        'pending',
                        'ELIMINAR_COCHE',
                        :email,
                        'vehicle_removal',
                        NOW()
                    )
                    RETURNING id
                    """
                ),
                {"email": email_clean},
            ).fetchone()

            if not row:
                raise RuntimeError("No se pudo crear el expediente de eliminación de vehículo")

            case_id = str(row[0])

            payload = {
                "name": data.name.strip(),
                "phone": data.phone.strip(),
                "email": email_clean,
                "plate": plate_clean,
                "city": data.city.strip(),
                "notes": (data.notes or "").strip() or None,
                "service": "vehicle_removal",
                "product_code": "ELIMINAR_COCHE",
                "status": "pending_payment",
                "stripe_price_id": price_id,
            }

            conn.execute(
                text(
                    """
                    INSERT INTO events (case_id, type, payload, created_at)
                    VALUES (:case_id, 'vehicle_removal_request_created', CAST(:payload AS JSONB), NOW())
                    """
                ),
                {
                    "case_id": case_id,
                    "payload": json.dumps(payload, ensure_ascii=False),
                },
            )

        frontend_url = (
            os.getenv("FRONTEND_URL")
            or os.getenv("FRONTEND_BASE_URL")
            or "https://recurretumulta.vercel.app"
        ).rstrip("/")

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="payment",
            customer_email=email_clean,
            line_items=[
                {
                    "price": price_id,
                    "quantity": 1,
                }
            ],
            metadata={
                "case_id": case_id,
                "service": "vehicle_removal",
                "product_code": "ELIMINAR_COCHE",
                "plate": plate_clean,
                "city": data.city.strip(),
                "phone": data.phone.strip(),
            },
            success_url=f"{frontend_url}/eliminar-coche?success=1&case_id={case_id}",
            cancel_url=f"{frontend_url}/eliminar-coche?cancelled=1&case_id={case_id}",
        )

        return {
            "ok": True,
            "case_id": case_id,
            "checkout_url": session.url,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creando sesión de eliminación de vehículo: {e}")
