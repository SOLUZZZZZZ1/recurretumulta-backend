import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine

router = APIRouter(prefix="/ops", tags=["ops-restaurant-reservations"])


# ============================================================
# Seguridad: PIN por restaurante
# ============================================================
def _need_pin(restaurant_id: str, x_reservas_pin: Optional[str]) -> None:
    rid = (restaurant_id or "").strip() or "rest_001"
    pin = (x_reservas_pin or "").strip()
    if not pin:
        raise HTTPException(status_code=401, detail="PIN requerido.")

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT pin_hash FROM restaurants WHERE id=:rid AND active=true"),
            {"rid": rid},
        ).fetchone()

    if not row:
        raise HTTPException(status_code=401, detail="Restaurante no válido o inactivo.")

    pin_hash = row[0]

    with engine.begin() as conn:
        ok = conn.execute(
            text("SELECT crypt(:pin, :hash) = :hash"),
            {"pin": pin, "hash": pin_hash},
        ).scalar()

    if not ok:
        raise HTTPException(status_code=401, detail="PIN incorrecto.")


# ============================================================
# ADMIN TOKEN (mini admin)
# ============================================================
def _need_admin(x_admin_token: Optional[str]) -> None:
    expected = (os.getenv("ADMIN_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN no configurado.")
    if not x_admin_token or x_admin_token.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ============================================================
# Schemas
# ============================================================
class ReservationCreate(BaseModel):
    reservation_date: str
    reservation_time: str
    shift: str
    table_name: Optional[str] = ""
    party_size: int
    customer_name: str
    phone: Optional[str] = ""
    extras_dog: bool = False
    extras_celiac: bool = False
    extras_notes: Optional[str] = ""
    created_by: Optional[str] = "SALA"


class ReservationUpdate(BaseModel):
    reservation_time: Optional[str] = None
    table_name: Optional[str] = None
    party_size: Optional[int] = None
    customer_name: Optional[str] = None
    phone: Optional[str] = None
    extras_dog: Optional[bool] = None
    extras_celiac: Optional[bool] = None
    extras_notes: Optional[str] = None


class ChangePinBody(BaseModel):
    restaurant_id: str
    current_pin: str
    new_pin: str


class AdminCreateRestaurantBody(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=80)
    pin: str = Field(..., min_length=1, max_length=32)


def _now() -> datetime:
    return datetime.utcnow()


# ============================================================
# ADMIN: crear restaurante
# ============================================================
@router.post("/admin/restaurants/create")
def admin_create_restaurant(
    body: AdminCreateRestaurantBody,
    x_admin_token: Optional[str] = Header(default=None, alias="x-admin-token"),
):
    _need_admin(x_admin_token)

    engine = get_engine()

    with engine.begin() as conn:
        last = conn.execute(
            text("SELECT id FROM restaurants WHERE id LIKE 'rest_%' ORDER BY id DESC LIMIT 1")
        ).fetchone()

    next_num = 1
    if last and last[0]:
        try:
            next_num = int(last[0].split("_")[1]) + 1
        except:
            pass

    new_id = f"rest_{next_num:03d}"

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO restaurants (id, display_name, pin_hash, active, created_at)
                VALUES (:id, :name, crypt(:pin, gen_salt('bf')), true, NOW())
            """),
            {"id": new_id, "name": body.display_name, "pin": body.pin},
        )

    return {
        "ok": True,
        "id": new_id,
        "display_name": body.display_name,
        "url": f"/#__reservas-restaurante?r={new_id}",
    }


# ============================================================
# Cambiar PIN
# ============================================================
@router.post("/restaurants/change-pin")
def change_restaurant_pin(body: ChangePinBody):
    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT pin_hash FROM restaurants WHERE id=:rid AND active=true"),
            {"rid": body.restaurant_id},
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Restaurante no encontrado.")

    with engine.begin() as conn:
        ok = conn.execute(
            text("SELECT crypt(:pin, :hash) = :hash"),
            {"pin": body.current_pin, "hash": row[0]},
        ).scalar()

    if not ok:
        raise HTTPException(status_code=401, detail="PIN actual incorrecto.")

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE restaurants SET pin_hash = crypt(:pin, gen_salt('bf')) WHERE id=:rid"),
            {"pin": body.new_pin, "rid": body.restaurant_id},
        )

    return {"ok": True}


# ============================================================
# Reservas (GET / POST / PUT / acciones)
# ============================================================
@router.get("/restaurant-reservations")
def list_reservations(
    date: str,
    shift: str,
    restaurant_id: str,
    x_reservas_pin: Optional[str] = Header(default=None, alias="x-reservas-pin"),
):
    _need_pin(restaurant_id, x_reservas_pin)

    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT *, id::text
                FROM restaurant_reservations
                WHERE restaurant_id=:rid
                  AND reservation_date=:d::date
                  AND shift=:s
                ORDER BY reservation_time
            """),
            {"rid": restaurant_id, "d": date, "s": shift},
        ).mappings().all()

    return {"items": [dict(r) for r in rows]}
