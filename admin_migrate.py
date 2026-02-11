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


def _run(engine: Engine, ddl: List[Tuple[str, str]]) -> List[str]:
    applied: List[str] = []
    with engine.begin() as conn:
        for name, sql in ddl:
            conn.execute(text(sql))
            applied.append(name)
    return applied


# =========================================================
# MIGRACIÓN INICIAL
# =========================================================

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


@router.post("/init", response_model=MigrateResponse)
def migrate_init(x_admin_token: str | None = Header(default=None, alias="x-admin-token")):
    _require_admin_token(x_admin_token)
    from database import get_engine
    engine = get_engine()
    created = _run(engine, _ddl_init())
    return MigrateResponse(ok=True, message="Migración inicial aplicada.", created=created)


# =========================================================
# MIGRACIÓN: DATOS INTERESADO + AUTORIZACIÓN
# =========================================================

@router.post("/cases_details", response_model=MigrateResponse)
def migrate_cases_details(x_admin_token: str | None = Header(default=None, alias="x-admin-token")):
    _require_admin_token(x_admin_token)
    from database import get_engine
    engine = get_engine()

    ddl = [
        ("cases_interested_data", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS interested_data JSONB;"),
        ("cases_authorized", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS authorized BOOLEAN NOT NULL DEFAULT FALSE;"),
        ("cases_authorized_at", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS authorized_at TIMESTAMPTZ;"),
    ]

    applied = _run(engine, ddl)
    return MigrateResponse(ok=True, message="Migración cases_details aplicada.", created=applied)


# =========================================================
# MIGRACIÓN: PARTNERS + CANAL
# =========================================================

@router.post("/partners_channel", response_model=MigrateResponse)
def migrate_partners_channel(x_admin_token: str | None = Header(default=None, alias="x-admin-token")):
    _require_admin_token(x_admin_token)

    from database import get_engine
    engine = get_engine()

    ddl = [
        # Crear tabla si no existe (sin billing todavía)
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

        # Añadir columnas billing si no existen
        (
            "partners_billing_mode",
            "ALTER TABLE partners ADD COLUMN IF NOT EXISTS billing_mode TEXT NOT NULL DEFAULT 'monthly';",
        ),
        (
            "partners_billing_status",
            "ALTER TABLE partners ADD COLUMN IF NOT EXISTS billing_status TEXT NOT NULL DEFAULT 'current';",
        ),

        # Índices
        ("idx_partners_email", "CREATE INDEX IF NOT EXISTS idx_partners_email ON partners(email);"),
        ("idx_cases_partner", "CREATE INDEX IF NOT EXISTS idx_cases_partner ON cases(partner_id);"),
        ("idx_partners_billing_status", "CREATE INDEX IF NOT EXISTS idx_partners_billing_status ON partners(billing_status);"),

        # Canal en cases
        ("cases_channel", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS channel TEXT NOT NULL DEFAULT 'direct';"),
        ("cases_partner_id", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS partner_id UUID NULL REFERENCES partners(id);"),
        ("cases_partner_name", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS partner_name TEXT;"),
    ]

    applied = _run(engine, ddl)
    return MigrateResponse(ok=True, message="Migración partners_channel aplicada.", created=applied)
# =========================
# MIGRACIÓN: partners must_change_password
# =========================

@router.post("/partners_must_change_password", response_model=MigrateResponse)
def migrate_partners_must_change_password(
    x_admin_token: str | None = Header(default=None, alias="x-admin-token")
):
    """
    Añade columna partners.must_change_password para forzar cambio de contraseña en primer login.
    SAFE: IF NOT EXISTS
    """
    _require_admin_token(x_admin_token)

    from database import get_engine
    engine = get_engine()

    ddl = [
        (
            "partners_must_change_password",
            "ALTER TABLE partners ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE;",
        ),
    ]

    try:
        applied = _run(engine, ddl)
        return MigrateResponse(
            ok=True,
            message="Migración partners_must_change_password aplicada.",
            created=applied,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error migrando partners_must_change_password: {e}")
