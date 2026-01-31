import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine

router = APIRouter(prefix="/ops", tags=["ops-restaurant-reservations"])


def _need_pin(x_reservas_pin: Optional[str]) -> str:
    expected = (os.getenv("RESERVAS_REST_PIN") or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="RESERVAS_REST_PIN no está configurado en el backend.")
    pin = (x_reservas_pin or "").strip()
    if pin != expected:
        raise HTTPException(status_code=401, detail="PIN incorrecto.")
    return pin


class ReservationCreate(BaseModel):
    reservation_date: str = Field(..., description="YYYY-MM-DD")
    reservation_time: str = Field(..., description="HH:MM or HH:MM:SS")
    shift: str = Field(..., description="desayuno|comida|cena")
    table_name: Optional[str] = ""
    party_size: int = 1
    customer_name: str
    phone: Optional[str] = ""
    extras_dog: bool = False
    extras_celiac: bool = False
    extras_notes: Optional[str] = ""
    created_by: Optional[str] = "SALA"


class ReservationUpdate(BaseModel):
    reservation_time: Optional[str] = None
    shift: Optional[str] = None
    table_name: Optional[str] = None
    party_size: Optional[int] = None
    customer_name: Optional[str] = None
    phone: Optional[str] = None
    extras_dog: Optional[bool] = None
    extras_celiac: Optional[bool] = None
    extras_notes: Optional[str] = None


def _now() -> datetime:
    return datetime.utcnow()


@router.get("/restaurant-reservations")
def list_reservations(
    date: str,
    shift: str,
    x_reservas_pin: Optional[str] = Header(default=None, alias="x-reservas-pin"),
):
    _need_pin(x_reservas_pin)
    engine = get_engine()
    sql = text(
        """
        SELECT
          id::text AS id,
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
        WHERE reservation_date = CAST(:date AS date)
          AND shift = :shift
        ORDER BY reservation_time ASC, created_at ASC
        """
    )
    with engine.begin() as conn:
        rows = conn.execute(sql, {"date": date, "shift": shift}).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.post("/restaurant-reservations")
def create_reservation(
    body: ReservationCreate,
    x_reservas_pin: Optional[str] = Header(default=None, alias="x-reservas-pin"),
):
    _need_pin(x_reservas_pin)
    engine = get_engine()
    now = _now()
    sql = text(
        """
        INSERT INTO restaurant_reservations (
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
          CAST(:reservation_date AS date),
          CAST(:reservation_time AS time),
          :shift,
          NULLIF(:table_name,''),
          :party_size,
          :customer_name,
          NULLIF(:phone,''),
          :extras_dog,
          :extras_celiac,
          NULLIF(:extras_notes,''),
          'pendiente',
          NULLIF(:created_by,''),
          :now,
          :now
        )
        RETURNING id::text
        """
    )
    params = body.model_dump()
    params["now"] = now
    with engine.begin() as conn:
        new_id = conn.execute(sql, params).scalar_one()
    return {"ok": True, "id": new_id}


def _set_status(res_id: str, status: str, by: str):
    engine = get_engine()
    now = _now()
    sql = text(
        """
        UPDATE restaurant_reservations
        SET status = :status,
            status_changed_at = :now,
            status_changed_by = :by,
            updated_at = :now
        WHERE id = CAST(:id AS uuid)
        RETURNING id::text
        """
    )
    with engine.begin() as conn:
        out = conn.execute(sql, {"id": res_id, "status": status, "now": now, "by": by}).scalar_one_or_none()
    if not out:
        raise HTTPException(status_code=404, detail="Reserva no encontrada.")
    return {"ok": True, "id": out, "status": status}


@router.post("/restaurant-reservations/{reservation_id}/arrived")
def mark_arrived(
    reservation_id: str,
    x_reservas_pin: Optional[str] = Header(default=None, alias="x-reservas-pin"),
    x_actor: Optional[str] = Header(default=None, alias="x-actor"),
):
    _need_pin(x_reservas_pin)
    return _set_status(reservation_id, "llego", (x_actor or "SALA"))


@router.post("/restaurant-reservations/{reservation_id}/no-show")
def mark_no_show(
    reservation_id: str,
    x_reservas_pin: Optional[str] = Header(default=None, alias="x-reservas-pin"),
    x_actor: Optional[str] = Header(default=None, alias="x-actor"),
):
    _need_pin(x_reservas_pin)
    return _set_status(reservation_id, "no_show", (x_actor or "SALA"))


@router.post("/restaurant-reservations/{reservation_id}/cancel")
def mark_cancel(
    reservation_id: str,
    x_reservas_pin: Optional[str] = Header(default=None, alias="x-reservas-pin"),
    x_actor: Optional[str] = Header(default=None, alias="x-actor"),
):
    _need_pin(x_reservas_pin)
    return _set_status(reservation_id, "cancelada", (x_actor or "SALA"))


@router.put("/restaurant-reservations/{reservation_id}")
def update_reservation(
    reservation_id: str,
    body: ReservationUpdate,
    x_reservas_pin: Optional[str] = Header(default=None, alias="x-reservas-pin"),
):
    _need_pin(x_reservas_pin)

    patch = body.model_dump(exclude_unset=True)
    if not patch:
        return {"ok": True, "id": reservation_id}

    allowed = {
        "reservation_time",
        "shift",
        "table_name",
        "party_size",
        "customer_name",
        "phone",
        "extras_dog",
        "extras_celiac",
        "extras_notes",
    }
    sets = []
    params = {"id": reservation_id, "now": _now()}

    for k, v in patch.items():
        if k not in allowed:
            continue
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

    if not sets:
        return {"ok": True, "id": reservation_id}

    sets.append("updated_at = :now")

    sql = text(
        f"""
        UPDATE restaurant_reservations
        SET {", ".join(sets)}
        WHERE id = CAST(:id AS uuid)
        RETURNING id::text
        """
    )

    engine = get_engine()
    with engine.begin() as conn:
        out = conn.execute(sql, params).scalar_one_or_none()

    if not out:
        raise HTTPException(status_code=404, detail="Reserva no encontrada.")
    return {"ok": True, "id": out}
