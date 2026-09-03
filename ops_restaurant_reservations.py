import hmac
import os
import re
from datetime import date, datetime, time as datetime_time, timezone
from typing import Literal, Optional
from uuid import UUID, uuid5

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from database import get_engine
from rtm_core.legacy_ops_session_bridge import (
    LegacyOpsOperatorContext,
    OPS_SUPERVISE_PERMISSION,
    OPS_SUPERVISOR_ROLE,
)
from rtm_core.operator_auth_request import (
    OPERATOR_AUTH_MODE_FAIL_CLOSED,
    OPERATOR_AUTH_MODE_INDIVIDUAL,
    OPERATOR_AUTH_MODE_LEGACY,
    operator_auth_environment_mode,
)
from rtm_core.operator_auth_service import has_recent_reauthentication

router = APIRouter(prefix="/ops", tags=["ops-restaurant-reservations"])
_INVALID_RESTAURANT_CREDENTIALS = "Credenciales de restaurante inválidas."
_RESERVATION_IDEMPOTENCY_NAMESPACE = UUID(
    "3e0863cc-f0ba-4d30-bf1c-720c11da3691"
)
_RESERVATION_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")


def _dummy_pin_check(conn, pin: str) -> None:
    """Mantiene un coste bcrypt comparable sin abrir otra transacción."""

    conn.execute(
        text("SELECT crypt(:pin, gen_salt('bf', 12))"),
        {"pin": pin},
    ).scalar()


def _invalid_restaurant_credentials() -> None:
    raise HTTPException(
        status_code=401,
        detail=_INVALID_RESTAURANT_CREDENTIALS,
    )


# ============================================================
# Seguridad: PIN por restaurante (tabla restaurants)
# ============================================================
def _need_pin(
    conn,
    restaurant_id: str,
    x_reservas_pin: Optional[str],
) -> str:
    """Valida el PIN y mantiene bloqueada su fila durante toda la operación.

    El llamante aporta la misma conexión transaccional con la que leerá o
    mutará reservas. ``FOR SHARE`` impide que una rotación o desactivación del
    restaurante invalide la credencial entre el check y la operación.
    """

    rid = (restaurant_id or "").strip() or "rest_001"
    pin = (x_reservas_pin or "").strip()
    if not pin:
        _dummy_pin_check(conn, "invalid-restaurant-pin")
        _invalid_restaurant_credentials()
    if len(rid) > 64:
        _dummy_pin_check(conn, "invalid-restaurant-pin")
        _invalid_restaurant_credentials()
    if len(pin.encode("utf-8")) > 72:
        _dummy_pin_check(conn, "invalid-restaurant-pin")
        _invalid_restaurant_credentials()

    row = conn.execute(
        text(
            "SELECT pin_hash FROM restaurants "
            "WHERE id = :rid AND active = true FOR SHARE"
        ),
        {"rid": rid},
    ).fetchone()

    if not row:
        _dummy_pin_check(conn, pin)
        _invalid_restaurant_credentials()

    pin_hash = row[0]
    ok = conn.execute(
        text("SELECT crypt(:pin, :hash) = :hash"),
        {"pin": pin, "hash": pin_hash},
    ).scalar()

    if not ok:
        _invalid_restaurant_credentials()

    return rid


# ============================================================
# Admin token (solo crear restaurantes)
# ============================================================
def _need_admin(x_admin_token: Optional[str]) -> None:
    expected = (os.getenv("ADMIN_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN no configurado.")
    if not x_admin_token or not hmac.compare_digest(
        x_admin_token.strip(),
        expected,
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _need_verified_individual_supervisor(request: Request) -> None:
    """Confía solo en el contexto que instala el bridge tras validar sesión."""

    context = getattr(request.state, "rtm_operator_context", None)
    if not isinstance(context, LegacyOpsOperatorContext):
        raise HTTPException(
            status_code=401,
            detail="Autenticación individual requerida",
        )
    if (
        context.role_code != OPS_SUPERVISOR_ROLE
        or OPS_SUPERVISE_PERMISSION not in context.permissions
    ):
        raise HTTPException(
            status_code=403,
            detail="Permiso de supervisor requerido",
        )
    if not has_recent_reauthentication(
        context,
        max_age_seconds=context.reauthentication_max_age_seconds,
    ):
        raise HTTPException(
            status_code=403,
            detail="Reautenticación reciente requerida",
        )


def _need_restaurant_admin(
    request: Request,
    x_admin_token: Optional[str],
) -> None:
    """Aplica la misma frontera de migración que el bridge de operaciones.

    El token compartido queda limitado a ejecución local/pruebas. Producción y
    cualquier entorno desplegado o ambiguo se cierran aunque este handler se
    monte sin el middleware global.
    """

    mode = operator_auth_environment_mode()
    if mode == OPERATOR_AUTH_MODE_INDIVIDUAL:
        _need_verified_individual_supervisor(request)
        return
    if mode == OPERATOR_AUTH_MODE_LEGACY:
        _need_admin(x_admin_token)
        return
    if mode == OPERATOR_AUTH_MODE_FAIL_CLOSED:
        raise HTTPException(
            status_code=503,
            detail="Administración de restaurantes no disponible",
        )
    # Defensa adicional si una futura implementación introduce un modo nuevo.
    raise HTTPException(
        status_code=503,
        detail="Administración de restaurantes no disponible",
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _reservation_id_from_idempotency(restaurant_id: str, key: str) -> str:
    candidate = str(key or "").strip()
    if not _RESERVATION_IDEMPOTENCY_RE.fullmatch(candidate):
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key inválida o ausente.",
        )
    return str(
        uuid5(
            _RESERVATION_IDEMPOTENCY_NAMESPACE,
            f"rtm:restaurant-reservation:v1:{restaurant_id}:{candidate}",
        )
    )


def _validated_new_pin(value: str) -> str:
    pin = str(value or "").strip()
    if (
        len(pin) < 8
        or len(pin) > 64
        or len(pin.encode("utf-8")) > 72
    ):
        raise HTTPException(
            status_code=400,
            detail="El PIN nuevo debe tener entre 8 y 64 caracteres.",
        )
    return pin


# ============================================================
# Schemas
# ============================================================
class _StrictReservationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReservationCreate(_StrictReservationInput):
    reservation_date: date
    reservation_time: datetime_time
    shift: Literal["desayuno", "comida", "cena"]
    table_name: Optional[str] = Field(default="", max_length=40)
    party_size: int = Field(ge=1, le=50)
    customer_name: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    )
    phone: Optional[str] = Field(
        default="",
        max_length=32,
        pattern=r"^[0-9+(). -]*$",
    )
    extras_dog: bool = False
    extras_celiac: bool = False
    extras_notes: Optional[str] = Field(default="", max_length=500)
    created_by: Literal["SALA"] = "SALA"


class ReservationUpdate(_StrictReservationInput):
    reservation_time: Optional[datetime_time] = None
    table_name: Optional[str] = Field(default=None, max_length=40)
    party_size: Optional[int] = Field(default=None, ge=1, le=50)
    customer_name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    )
    phone: Optional[str] = Field(
        default=None,
        max_length=32,
        pattern=r"^[0-9+(). -]*$",
    )
    extras_dog: Optional[bool] = None
    extras_celiac: Optional[bool] = None
    extras_notes: Optional[str] = Field(default=None, max_length=500)


class ChangePinBody(_StrictReservationInput):
    restaurant_id: str = Field(..., min_length=1, max_length=64)
    current_pin: str = Field(..., min_length=1, max_length=128)
    new_pin: str = Field(..., min_length=8, max_length=64)


class AdminCreateRestaurantBody(_StrictReservationInput):
    display_name: str = Field(..., min_length=1, max_length=80)
    pin: str = Field(..., min_length=8, max_length=64)


# ============================================================
# ADMIN: crear restaurante (rest_00X automático)
# ============================================================
@router.post("/admin/restaurants/create")
def admin_create_restaurant(
    body: AdminCreateRestaurantBody,
    request: Request,
    x_admin_token: Optional[str] = Header(default=None, alias="x-admin-token"),
):
    _need_restaurant_admin(request, x_admin_token)

    name = (body.display_name or "").strip()
    pin = _validated_new_pin(body.pin)
    if not name:
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
                VALUES (:id, :name, crypt(:pin, gen_salt('bf', 12)), true, NOW())
            """),
            {"id": new_id, "name": name, "pin": pin},
        )

    return {
        "ok": True,
        "id": new_id,
        "display_name": name,
        "url": f"/__reservas-restaurante?r={new_id}",
    }


# ============================================================
# Cambiar PIN (desde la pantalla del restaurante)
# ============================================================
@router.post("/restaurants/change-pin")
def change_restaurant_pin(body: ChangePinBody):
    rid = (body.restaurant_id or "").strip() or "rest_001"
    current_pin = (body.current_pin or "").strip()
    new_pin = _validated_new_pin(body.new_pin)

    engine = get_engine()
    with engine.begin() as conn:
        if (
            not current_pin
            or len(rid) > 64
            or len(current_pin.encode("utf-8")) > 72
        ):
            _dummy_pin_check(conn, "invalid-restaurant-pin")
            _invalid_restaurant_credentials()

        # PostgreSQL reevalúa el predicado sobre la versión vigente de la fila
        # tras esperar un lock concurrente. Por ello dos rotaciones con el PIN
        # antiguo no pueden ganar: la segunda deja de satisfacer ``crypt``.
        updated = conn.execute(
            text(
                """
                WITH credential AS MATERIALIZED (
                    SELECT
                        candidate.id,
                        candidate.pin_hash,
                        CASE
                            WHEN candidate.id IS NULL THEN
                                crypt(:current_pin, gen_salt('bf', 12))
                            ELSE candidate.pin_hash
                        END AS timing_probe
                    FROM (VALUES (TRUE)) AS seed(one)
                    LEFT JOIN LATERAL (
                        SELECT restaurant.id, restaurant.pin_hash
                        FROM restaurants AS restaurant
                        WHERE restaurant.id = :rid
                          AND restaurant.active = TRUE
                    ) AS candidate ON TRUE
                )
                UPDATE restaurants AS target
                SET pin_hash = crypt(:new_pin, gen_salt('bf', 12))
                FROM credential
                WHERE credential.timing_probe IS NOT NULL
                  AND target.id = :rid
                  AND target.id = credential.id
                  AND target.active = TRUE
                  AND target.pin_hash = credential.pin_hash
                  AND crypt(:current_pin, target.pin_hash) = target.pin_hash
                RETURNING target.id
                """
            ),
            {
                "rid": rid,
                "current_pin": current_pin,
                "new_pin": new_pin,
            },
        ).fetchone()
        if not updated:
            _invalid_restaurant_credentials()

    return {"ok": True, "restaurant_id": rid}


# ============================================================
# GET: listar reservas
# ============================================================
@router.get("/restaurant-reservations")
def list_reservations(
    date: date,
    shift: Literal["desayuno", "comida", "cena"],
    restaurant_id: str,
    x_reservas_pin: Optional[str] = Header(default=None, alias="x-reservas-pin"),
):
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
        rid = _need_pin(conn, restaurant_id, x_reservas_pin)
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
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    now = _now()
    engine = get_engine()

    sql = text("""
        INSERT INTO restaurant_reservations (
          id,
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
          CAST(:reservation_id AS uuid),
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
        ON CONFLICT (id) DO NOTHING
        RETURNING id::text
    """)

    with engine.begin() as conn:
        rid = _need_pin(conn, restaurant_id, x_reservas_pin)
        reservation_id = _reservation_id_from_idempotency(
            rid,
            idempotency_key or "",
        )
        params = {
            "reservation_id": reservation_id,
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
            "by": f"restaurant:{rid}",
            "now": now,
        }
        new_id = conn.execute(sql, params).scalar_one_or_none()
        if new_id is None:
            payload_matches = conn.execute(
                text(
                    """
                    SELECT (
                      reservation_date = CAST(:d AS date)
                      AND reservation_time = CAST(:t AS time)
                      AND shift = :s
                      AND COALESCE(table_name, '') = :table_name
                      AND party_size = :pax
                      AND customer_name = :name
                      AND COALESCE(phone, '') = :phone
                      AND extras_dog = :dog
                      AND extras_celiac = :celiac
                      AND COALESCE(extras_notes, '') = :notes
                      AND COALESCE(created_by, '') = :by
                    )
                    FROM restaurant_reservations
                    WHERE id = CAST(:reservation_id AS uuid)
                      AND restaurant_id = :rid
                    """
                ),
                params,
            ).scalar_one_or_none()
            if payload_matches is not True:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key ya utilizada para otra operación.",
                )
            return {"ok": True, "id": reservation_id, "replayed": True}

    return {"ok": True, "id": new_id, "replayed": False}


# ============================================================
# PUT: editar reserva (sin ::, todo con CAST)
# ============================================================
@router.put("/restaurant-reservations/{reservation_id}")
def update_reservation(
    reservation_id: UUID,
    body: ReservationUpdate,
    restaurant_id: str,
    x_reservas_pin: Optional[str] = Header(default=None, alias="x-reservas-pin"),
):
    patch = body.model_dump(exclude_unset=True)
    sets = []
    params = {"id": str(reservation_id), "now": _now()}

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

    engine = get_engine()
    with engine.begin() as conn:
        rid = _need_pin(conn, restaurant_id, x_reservas_pin)
        if not patch:
            return {"ok": True}

        params["rid"] = rid
        sets.append("updated_at = :now")
        sql = text(f"""
            UPDATE restaurant_reservations
            SET {", ".join(sets)}
            WHERE id = CAST(:id AS uuid)
              AND restaurant_id = :rid
            RETURNING id
        """)
        out = conn.execute(sql, params).fetchone()

    if not out:
        raise HTTPException(status_code=404, detail="Reserva no encontrada.")

    return {"ok": True}


# ============================================================
# Acciones de estado
# ============================================================
def _set_status(conn, res_id: str, restaurant_id: str, status: str):
    now = _now()
    actor = f"restaurant:{restaurant_id}"

    sql = text("""
        UPDATE restaurant_reservations
        SET status = :status,
            status_changed_at = :now,
            status_changed_by = :by,
            updated_at = :now
        WHERE id = CAST(:id AS uuid)
          AND restaurant_id = :rid
        RETURNING id::text
    """)

    out = conn.execute(
        sql,
        {
            "id": res_id,
            "rid": restaurant_id,
            "status": status,
            "now": now,
            "by": actor,
        },
    ).scalar_one_or_none()

    if not out:
        raise HTTPException(status_code=404, detail="Reserva no encontrada.")
    return {"ok": True, "id": out, "status": status}


@router.post("/restaurant-reservations/{reservation_id}/arrived")
def mark_arrived(
    reservation_id: UUID,
    restaurant_id: str,
    x_reservas_pin: Optional[str] = Header(default=None, alias="x-reservas-pin"),
):
    engine = get_engine()
    with engine.begin() as conn:
        rid = _need_pin(conn, restaurant_id, x_reservas_pin)
        return _set_status(conn, str(reservation_id), rid, "llego")


@router.post("/restaurant-reservations/{reservation_id}/no-show")
def mark_no_show(
    reservation_id: UUID,
    restaurant_id: str,
    x_reservas_pin: Optional[str] = Header(default=None, alias="x-reservas-pin"),
):
    engine = get_engine()
    with engine.begin() as conn:
        rid = _need_pin(conn, restaurant_id, x_reservas_pin)
        return _set_status(conn, str(reservation_id), rid, "no_show")


@router.post("/restaurant-reservations/{reservation_id}/cancel")
def mark_cancel(
    reservation_id: UUID,
    restaurant_id: str,
    x_reservas_pin: Optional[str] = Header(default=None, alias="x-reservas-pin"),
):
    engine = get_engine()
    with engine.begin() as conn:
        rid = _need_pin(conn, restaurant_id, x_reservas_pin)
        return _set_status(conn, str(reservation_id), rid, "cancelada")
