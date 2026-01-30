import os
from datetime import datetime, date, time
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine

router = APIRouter(prefix="/ops", tags=["ops"])

# =========================
# Auth (PIN)
# =========================

def _require_reservas_pin(x_reservas_pin: str | None) -> None:
    expected = (os.getenv("RESERVAS_REST_PIN") or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="RESERVAS_REST_PIN no está configurado en el backend.")
    if not x_reservas_pin or x_reservas_pin.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


# =========================
# Schemas
# =========================

class ReservationItem(BaseModel):
    id: str
    reservation_date: str
    reservation_time: str
    shift: str
    table_name: Optional[str] = None
    party_size: int
    customer_name: str
    phone: Optional[str] = None
    extras_dog: bool = False
    extras_celiac: bool = False
    extras_notes: Optional[str] = None
    status: str
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class ReservationListResponse(BaseModel):
    ok: bool = True
    items: List[ReservationItem] = []

class CreateReservationRequest(BaseModel):
    reservation_date: str  # YYYY-MM-DD
    reservation_time: str  # HH:MM
    shift: str = Field(pattern="^(desayuno|comida|cena)$")
    table_name: Optional[str] = None
    party_size: int = Field(ge=1, le=50)
    customer_name: str
    phone: Optional[str] = None
    extras_dog: bool = False
    extras_celiac: bool = False
    extras_notes: Optional[str] = None
    created_by: Optional[str] = "SALA"


# =========================
# Helpers
# =========================

def _now() -> datetime:
    return datetime.utcnow()

def _as_item(row) -> dict:
    # row is mapping
    d = dict(row)
    # Normalize time format
    if d.get("reservation_time") is not None:
        d["reservation_time"] = str(d["reservation_time"])
    if d.get("reservation_date") is not None:
        d["reservation_date"] = str(d["reservation_date"])
    if d.get("created_at") is not None:
        d["created_at"] = d["created_at"].isoformat()
    if d.get("updated_at") is not None:
        d["updated_at"] = d["updated_at"].isoformat()
    return d


# =========================
# Endpoints
# =========================

@router.get("/restaurant-reservations", response_model=ReservationListResponse)
def list_reservations(
    date_: str = Query(alias="date"),
    shift: str = Query(default="comida"),
    x_reservas_pin: str | None = Header(default=None, alias="x-reservas-pin"),
):
    _require_reservas_pin(x_reservas_pin)

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id::text,
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
                FROM restaurant_reservations
                WHERE reservation_date = :d
                  AND shift = :s
                ORDER BY reservation_time ASC, created_at ASC
                """
            ),
            {"d": date_, "s": shift},
        ).mappings().all()

    return {"ok": True, "items": [_as_item(r) for r in rows]}


@router.post("/restaurant-reservations")
def create_reservation(
    payload: CreateReservationRequest,
    x_reservas_pin: str | None = Header(default=None, alias="x-reservas-pin"),
):
    _require_reservas_pin(x_reservas_pin)

    engine = get_engine()
    now = _now()

    with engine.begin() as conn:
        row = conn.execute(
            text(
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
            ),
            {**payload.model_dump(), "now": now},
        ).mappings().first()

    return {"ok": True, "id": row["id"]}


def _set_status(res_id: str, new_status: str, by: str | None, conn):
    conn.execute(
        text(
            """
            UPDATE restaurant_reservations
            SET status = :st,
                status_changed_at = :now,
                status_changed_by = NULLIF(:by,''),
                updated_at = :now
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"st": new_status, "now": _now(), "by": (by or ""), "id": res_id},
    )


@router.post("/restaurant-reservations/{reservation_id}/arrived")
def mark_arrived(
    reservation_id: str,
    x_reservas_pin: str | None = Header(default=None, alias="x-reservas-pin"),
    x_actor: str | None = Header(default=None, alias="x-actor"),
):
    _require_reservas_pin(x_reservas_pin)
    engine = get_engine()
    with engine.begin() as conn:
        _set_status(reservation_id, "llego", x_actor, conn)
    return {"ok": True}


@router.post("/restaurant-reservations/{reservation_id}/no-show")
def mark_no_show(
    reservation_id: str,
    x_reservas_pin: str | None = Header(default=None, alias="x-reservas-pin"),
    x_actor: str | None = Header(default=None, alias="x-actor"),
):
    _require_reservas_pin(x_reservas_pin)
    engine = get_engine()
    with engine.begin() as conn:
        _set_status(reservation_id, "no_show", x_actor, conn)
    return {"ok": True}
