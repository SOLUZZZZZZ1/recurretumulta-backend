from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from database import get_engine
import os
import json
import stripe

router = APIRouter(prefix="/vehicle-removal", tags=["vehicle-removal"])

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")


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
        engine = get_engine()

        with engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO cases (status, contact_email, category, updated_at)
                    VALUES ('vehicle_removal_pending_payment', :email, 'vehicle_removal', NOW())
                    RETURNING id
                    """
                ),
                {"email": data.email},
            ).fetchone()

            if not row:
                raise RuntimeError("No se pudo crear el expediente de eliminación de vehículo")

            case_id = str(row[0])

            payload = {
                "name": data.name.strip(),
                "phone": data.phone.strip(),
                "email": (data.email or "").strip() or None,
                "plate": data.plate.strip().upper().replace(" ", ""),
                "city": data.city.strip(),
                "notes": (data.notes or "").strip() or None,
                "price_eur": 39,
                "service": "vehicle_removal",
                "status": "pending_payment",
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

        frontend_url = os.getenv("FRONTEND_URL", "https://recurretumulta.vercel.app").rstrip("/")

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="payment",
            customer_email=data.email if data.email else None,
            line_items=[
                {
                    "price_data": {
                        "currency": "eur",
                        "product_data": {
                            "name": "Eliminar coche - RecurreTuMulta",
                            "description": "Gestión de baja definitiva de vehículo a través de centro autorizado",
                        },
                        "unit_amount": 3900,
                    },
                    "quantity": 1,
                }
            ],
            metadata={
                "case_id": case_id,
                "service": "vehicle_removal",
                "plate": data.plate.strip().upper().replace(" ", ""),
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
