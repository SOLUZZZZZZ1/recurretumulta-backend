import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine

router = APIRouter(prefix="/ops", tags=["ops-restaurant-reservations"])


# ============================================================
# Seguridad: PIN por restaurante (tabla restaurants)
# ============================================================
def _need_pin(restaurant_id: str, x_reservas_pin: Optional[str]) -> str:
    rid = (restaurant_id or "").strip() or "rest_001"
    pin = (x_reservas_pin or "").strip()
    if not pin:
        raise HTTPException(status_code=401, detail="PIN requerido.")

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT pin_hash FROM restaurants WHERE id = :rid AND active = true"),
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

    return rid


# ============================================================
# Admin token (solo crear restaurantes)
# ============================================================
def _need_admin(x_admin_token: Optional[str]) -> None:
    expected = (os.getenv("ADMIN_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN no configurado.")
    if not x_admin_token or x_admin_token.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _now() -> datetime:
    return datetime.utcnow()


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


# ============================================================
# ADMIN: crear restaurante (rest_00X automático)
# ============================================================
@router.post("/admin/restaurants/create")
def admin_create_restaurant(
    body: AdminCreateRestaurantBody,
    x_admin_token: Optional[str] = Header(default=None, alias="x-admin-token"),
):
    _need_admin(x_admin_token)

    name = (body.display_name or "").strip()
    pin = (body.pin or "").strip()
    if not name or not pin:
        raise HTTPException(status_code=400, detail="display_name y pin son obligatorios.")

    engine = get_engine()

    # Siguiente id rest_XXX
    with engine.begin() as conn:
        last = conn.execute(
            text("SELECT id FROM restaurants WHERE id LIKE 'rest_%' ORDER BY id DESC LIMIT 1")
        ).fetchone()

    next_num = 1
    if last and last[0]:
        try:
            next_num = int(str(last[0]).split("_")[1]) + 1
        except Exception:
            next_num = 1

    new_id = f"rest_{next_num:03d}"

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO restaurants (id, display_name, pin_hash, active, created_at)
                VALUES (:id, :name, crypt(:pin, gen_salt('bf')), true, NOW())
            """),
            {"id": new_id, "name": name, "pin": pin},
        )

    return {"ok": True, "id": new_id, "display_name": name, "url": f"/#__reservas-restaurante?r={new_id}"}


# ============================================================
# Cambiar PIN (desde la pantalla del restaurante)
# ============================================================
@router.post("/restaurants/change-pin")
def change_restaurant_pin(body: ChangePinBody):
    rid = (body.restaurant_id or "").strip() or "rest_001"
    current_pin = (body.current_pin or "").strip()
    new_pin = (body.new_pin or "").strip()

    if not current_pin or not new_pin:
        raise HTTPException(status_code=400, detail="PIN actual y nuevo PIN son requeridos.")

    engine = get_engine()

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT pin_hash FROM restaurants WHERE id = :rid AND active = true"),
            {"rid": rid},
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Restaurante no encontrado o inactivo.")

    pin_hash = row[0]

    with engine.begin() as conn:
        ok = conn.execute(
            text("SELECT crypt(:pin, :hash) = :hash"),
            {"pin": current_pin, "hash": pin_hash},
        ).scalar()

    if not ok:
        raise HTTPException(status_code=401, detail="PIN actual incorrecto.")

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE restaurants SET pin_hash = crypt(:pin, gen_salt('bf')) WHERE id = :rid"),
            {"pin": new_pin, "rid": rid},
        )

    return {"ok": True, "restaurant_id": rid}


# ============================================================
# GET: listar reservas
# ============================================================
@router.get("/restaurant-reservations")
def list_reservations(
    date: str,
    shift: str,
    restaurant_id: str,
    x_reservas_pin: Optional[str] = Header(default=None, alias="x-reservas-pin"),
):
    rid = _need_pin(restaurant_id, x_reservas_pin)

    engine = get_engine()
    sql = text("""
        SELECT
          id::text AS id,
          restaurant_id,
          reservation_date::text AS reservation_date,
          reservation_time::text AS reservation_time,
          shift,
          COALESCE(table_name,'') AS table_name,
          party_size,
          customer_name,
          COALESCE(phone,'') AS phone,
          extras_dog,
          extras_celiac,
          COALESCE(extras_notes,'') AS extras_notes,
          status,
          COALESCE(created_by,'') AS created_by,
          created_at,
          updated_at,
          status_changed_at,
          COALESCE(status_changed_by,'') AS status_changed_by
        FROM restaurant_reservations
        WHERE restaurant_id = :rid
          AND reservation_date = CAST(:d AS date)
          AND shift = :s
        ORDER BY reservation_time ASC, created_at ASC
    """)

    with engine.begin() as conn:
        rows = conn.execute(sql, {"rid": rid, "d": date, "s": shift}).mappings().all()

    return {"items": [dict(r) for r in rows]}


# ============================================================
# POST: crear reserva
# ============================================================
@router.post("/restaurant-reservations")
def create_reservation(
    body: ReservationCreate,
    restaurant_id: str,
    x_reservas_pin: Optional[str] = Header(default=None, alias="x-reservas-pin"),
):
    rid = _need_pin(restaurant_id, x_reservas_pin)

    now = _now()
    engine = get_engine()

    sql = text("""
        INSERT INTO restaurant_reservations (
          restaurant_id,
          reservation_date,
          reservation_time,
          shift,
          table_name,
          party_size,
          customer_name,
          phone,
          extras_dog,
          extras_celiac,
          extras_notes,
          status,
          created_by,
          created_at,
          updated_at
        )
        VALUES (
          :rid,
          CAST(:d AS date),
          CAST(:t AS time),
          :s,
          NULLIF(:table_name,''),
          :pax,
          :name,
          NULLIF(:phone,''),
          :dog,
          :celiac,
          NULLIF(:notes,''),
          'pendiente',
          NULLIF(:by,''),
          :now,
          :now
        )
        RETURNING id::text
    """)

    params = {
        "rid": rid,
        "d": body.reservation_date,
        "t": body.reservation_time,
        "s": body.shift,
        "table_name": body.table_name or "",
        "pax": body.party_size,
        "name": body.customer_name,
        "phone": body.phone or "",
        "dog": body.extras_dog,
        "celiac": body.extras_celiac,
        "notes": body.extras_notes or "",
        "by": body.created_by or "SALA",
        "now": now,
    }

    with engine.begin() as conn:
        new_id = conn.execute(sql, params).scalar_one()

    return {"ok": True, "id": new_id}


# ============================================================
# PUT: editar reserva (sin ::, todo con CAST)
# ============================================================
@router.put("/restaurant-reservations/{reservation_id}")
def update_reservation(
    reservation_id: str,
    body: ReservationUpdate,
    restaurant_id: str,
    x_reservas_pin: Optional[str] = Header(default=None, alias="x-reservas-pin"),
):
    _need_pin(restaurant_id, x_reservas_pin)

    patch = body.model_dump(exclude_unset=True)
    if not patch:
        return {"ok": True}

    sets = []
    params = {"id": reservation_id, "now": _now()}

    for k, v in patch.items():
        if k == "reservation_time":
            sets.append("reservation_time = CAST(:reservation_time AS time)")
            params["reservation_time"] = v
        elif k == "table_name":
            sets.append("table_name = NULLIF(:table_name,'')")
            params["table_name"] = v or ""
        elif k == "phone":
            sets.append("phone = NULLIF(:phone,'')")
            params["phone"] = v or ""
        elif k == "extras_notes":
            sets.append("extras_notes = NULLIF(:extras_notes,'')")
            params["extras_notes"] = v or ""
        else:
            sets.append(f"{k} = :{k}")
            params[k] = v

    sets.append("updated_at = :now")

    sql = text(f"""
        UPDATE restaurant_reservations
        SET {", ".join(sets)}
        WHERE id = CAST(:id AS uuid)
        RETURNING id
    """)

    engine = get_engine()
    with engine.begin() as conn:
        out = conn.execute(sql, params).fetchone()

    if not out:
        raise HTTPException(status_code=404, detail="Reserva no encontrada.")

    return {"ok": True}


# ============================================================
# Acciones de estado
# ============================================================
def _set_status(res_id: str, status: str, by: str):
    now = _now()
    engine = get_engine()

    sql = text("""
        UPDATE restaurant_reservations
        SET status = :status,
            status_changed_at = :now,
            status_changed_by = :by,
            updated_at = :now
        WHERE id = CAST(:id AS uuid)
        RETURNING id::text
    """)

    with engine.begin() as conn:
        out = conn.execute(sql, {"id": res_id, "status": status, "now": now, "by": by}).scalar_one_or_none()

    if not out:
        raise HTTPException(status_code=404, detail="Reserva no encontrada.")
    return {"ok": True, "id": out, "status": status}


@router.post("/restaurant-reservations/{reservation_id}/arrived")
def mark_arrived(
    reservation_id: str,
    restaurant_id: str,
    x_reservas_pin: Optional[str] = Header(default=None, alias="x-reservas-pin"),
    x_actor: Optional[str] = Header(default=None, alias="x-actor"),
):
    _need_pin(restaurant_id, x_reservas_pin)
    return _set_status(reservation_id, "llego", (x_actor or "SALA"))


@router.post("/restaurant-reservations/{reservation_id}/no-show")
def mark_no_show(
    reservation_id: str,
    restaurant_id: str,
    x_reservas_pin: Optional[str] = Header(default=None, alias="x-reservas-pin"),
    x_actor: Optional[str] = Header(default=None, alias="x-actor"),
):
    _need_pin(restaurant_id, x_reservas_pin)
    return _set_status(reservation_id, "no_show", (x_actor or "SALA"))


@router.post("/restaurant-reservations/{reservation_id}/cancel")
def mark_cancel(
    reservation_id: str,
    restaurant_id: str,
    x_reservas_pin: Optional[str] = Header(default=None, alias="x-reservas-pin"),
    x_actor: Optional[str] = Header(default=None, alias="x-actor"),
):
    _need_pin(restaurant_id, x_reservas_pin)
    return _set_status(reservation_id, "cancelada", (x_actor or "SALA"))
