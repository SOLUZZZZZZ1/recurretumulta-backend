# admin_migrate.py — migraciones admin (init + ampliaciones)
import os
from typing import List, Tuple
from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Engine
from schemas import MigrateResponse

router = APIRouter(prefix="/admin/migrate", tags=["admin"])


def _require_admin_token(x_admin_token: str | None) -> None:
    expected = os.getenv("ADMIN_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=500,
            detail="ADMIN_TOKEN no está configurado en el backend.",
        )
    if not x_admin_token or x_admin_token.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


# =========================
# MIGRACIÓN INICIAL (YA EXISTENTE)
# =========================

def _ddl_init() -> List[Tuple[str, str]]:
    return [
        ("extensions", "CREATE EXTENSION IF NOT EXISTS pgcrypto;"),
        (
            "cases",
            """
            CREATE TABLE IF NOT EXISTS cases (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              contact_email TEXT,
              status TEXT NOT NULL DEFAULT 'uploaded',
              category TEXT,
              organismo TEXT,
              expediente_ref TEXT,
              notified_at DATE,
              deadline_main DATE,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
        ),
        (
            "documents",
            """
            CREATE TABLE IF NOT EXISTS documents (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
              kind TEXT NOT NULL,
              b2_bucket TEXT,
              b2_key TEXT,
              sha256 TEXT,
              mime TEXT,
              size_bytes BIGINT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
        ),
        (
            "extractions",
            """
            CREATE TABLE IF NOT EXISTS extractions (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
              extracted_json JSONB NOT NULL,
              confidence DOUBLE PRECISION,
              model TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
        ),
        (
            "events",
            """
            CREATE TABLE IF NOT EXISTS events (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              case_id UUID REFERENCES cases(id) ON DELETE CASCADE,
              type TEXT NOT NULL,
              payload JSONB,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
        ),
        ("idx_cases_status", "CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);"),
        ("idx_events_case", "CREATE INDEX IF NOT EXISTS idx_events_case ON events(case_id);"),
    ]


def _run(engine: Engine, ddl: List[Tuple[str, str]]) -> List[str]:
    applied: List[str] = []
    with engine.begin() as conn:
        for name, sql in ddl:
            conn.execute(text(sql))
            applied.append(name)
    return applied


@router.post("/init", response_model=MigrateResponse)
def migrate_init(x_admin_token: str | None = Header(default=None, alias="x-admin-token")):
    _require_admin_token(x_admin_token)

    from database import get_engine
    engine = get_engine()

    try:
        created = _run(engine, _ddl_init())
        return MigrateResponse(ok=True, message="Migración inicial aplicada.", created=created)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error migrando init: {e}")


# =========================
# NUEVA MIGRACIÓN: DATOS DEL INTERESADO + AUTORIZACIÓN
# =========================

@router.post("/cases_details", response_model=MigrateResponse)
def migrate_cases_details(
    x_admin_token: str | None = Header(default=None, alias="x-admin-token")
):
    """
    Añade columnas necesarias para:
    - Datos del interesado (post-pago)
    - Autorización expresa
    SAFE: usa IF NOT EXISTS
    """
    _require_admin_token(x_admin_token)

    from database import get_engine
    engine = get_engine()

    ddl = [
        (
            "cases_interested_data",
            "ALTER TABLE cases ADD COLUMN IF NOT EXISTS interested_data JSONB;",
        ),
        (
            "cases_authorized",
            "ALTER TABLE cases ADD COLUMN IF NOT EXISTS authorized BOOLEAN NOT NULL DEFAULT FALSE;",
        ),
        (
            "cases_authorized_at",
            "ALTER TABLE cases ADD COLUMN IF NOT EXISTS authorized_at TIMESTAMPTZ;",
        ),
    ]

    try:
        applied = _run(engine, ddl)
        return MigrateResponse(
            ok=True,
            message="Migración cases_details aplicada.",
            created=applied,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error migrando cases_details: {e}")


# =========================
# NUEVA MIGRACIÓN: PARTNERS (GESTORÍAS) + CANAL EN CASES
# =========================

@router.post("/partners_channel", response_model=MigrateResponse)
def migrate_partners_channel(
    x_admin_token: str | None = Header(default=None, alias="x-admin-token")
):
    """Añade tabla partners y columnas de canal partner en cases. SAFE: IF NOT EXISTS."""
    _require_admin_token(x_admin_token)

    from database import get_engine
    engine = get_engine()

    ddl = [
        (
            "partners_table",
            """CREATE TABLE IF NOT EXISTS partners (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              name TEXT NOT NULL,
              email TEXT UNIQUE NOT NULL,
              password_salt TEXT NOT NULL,
              password_hash TEXT NOT NULL,
              api_token TEXT UNIQUE,
              active BOOLEAN NOT NULL DEFAULT TRUE,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );""",
        ),
        ("cases_channel", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS channel TEXT NOT NULL DEFAULT 'direct';"),
        ("cases_partner_id", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS partner_id UUID NULL REFERENCES partners(id);"),
        ("cases_partner_name", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS partner_name TEXT;"),
        ("idx_partners_email", "CREATE INDEX IF NOT EXISTS idx_partners_email ON partners(email);"),
        ("idx_cases_partner", "CREATE INDEX IF NOT EXISTS idx_cases_partner ON cases(partner_id);"),
    ]

    try:
        applied = _run(engine, ddl)
        return MigrateResponse(ok=True, message="Migración partners_channel aplicada.", created=applied)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error migrando partners_channel: {e}")


# =========================
# NUEVA MIGRACIÓN: MULTI-RESTAURANTE (restaurant_id)
# =========================

@router.post("/restaurant_id", response_model=MigrateResponse)
def migrate_restaurant_id(
    x_admin_token: str | None = Header(default=None, alias="x-admin-token")
):
    """Añade la columna restaurant_id a restaurant_reservations para multi-restaurante.
    SAFE: IF NOT EXISTS + backfill + índice compuesto.
    No afecta a RecurreTuMulta (tabla aislada).
    """
    _require_admin_token(x_admin_token)

    from database import get_engine
    engine = get_engine()

    ddl = [
        ("restaurant_id_col", "ALTER TABLE IF EXISTS restaurant_reservations ADD COLUMN IF NOT EXISTS restaurant_id TEXT;"),
        ("restaurant_id_backfill", "UPDATE restaurant_reservations SET restaurant_id = 'rest_001' WHERE restaurant_id IS NULL;"),
        ("restaurant_id_default", "ALTER TABLE IF EXISTS restaurant_reservations ALTER COLUMN restaurant_id SET DEFAULT 'rest_001';"),
        ("restaurant_id_not_null", "ALTER TABLE IF EXISTS restaurant_reservations ALTER COLUMN restaurant_id SET NOT NULL;"),
        ("idx_rest_res_rest_day_shift_time", "CREATE INDEX IF NOT EXISTS idx_rest_res_rest_day_shift_time ON restaurant_reservations(restaurant_id, reservation_date, shift, reservation_time);"),
    ]

    try:
        applied = _run(engine, ddl)
        return MigrateResponse(ok=True, message="Migración restaurant_id aplicada.", created=applied)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error migrando restaurant_id: {e}")


# =========================
# MIGRACIÓN: TABLA restaurants
# =========================

@router.post("/restaurants", response_model=MigrateResponse)
def migrate_restaurants(
    x_admin_token: str | None = Header(default=None, alias="x-admin-token")
):
    """
    Crea la tabla restaurants para multi-restaurante con PIN por restaurante.
    SAFE:
    - CREATE TABLE IF NOT EXISTS
    - Seed rest_001 si no existe
    No afecta a RecurreTuMulta.
    """
    _require_admin_token(x_admin_token)

    from database import get_engine
    engine = get_engine()

    pin = (os.getenv("RESERVAS_REST_PIN") or "").strip()
    if not pin:
        raise HTTPException(status_code=500, detail="RESERVAS_REST_PIN no está configurado en el backend.")

    applied: List[str] = []

    try:
        with engine.begin() as conn:
            # Asegura pgcrypto (por si acaso)
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto;"))
            applied.append("restaurants_pgcrypto")

            # Tabla
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS restaurants (
                  id TEXT PRIMARY KEY,
                  display_name TEXT NOT NULL,
                  pin_hash TEXT NOT NULL,
                  active BOOLEAN NOT NULL DEFAULT true,
                  created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
            """))
            applied.append("restaurants_table")

            # Seed rest_001 (PIN hasheado)
            conn.execute(
                text("""
                    INSERT INTO restaurants (id, display_name, pin_hash)
                    VALUES (
                      'rest_001',
                      'Restaurante principal',
                      crypt(:pin, gen_salt('bf'))
                    )
                    ON CONFLICT (id) DO NOTHING;
                """),
                {"pin": pin},
            )
            applied.append("restaurants_seed_rest_001")

        return MigrateResponse(ok=True, message="Migración restaurants aplicada.", created=applied)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error migrando restaurants: {e}")

        ),
    ]

    try:
        applied = _run(engine, ddl, params={"pin": (os.getenv("RESERVAS_REST_PIN") or "").strip()})
        return MigrateResponse(ok=True, message="Migración restaurants aplicada.", created=applied)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error migrando restaurants: {e}")
