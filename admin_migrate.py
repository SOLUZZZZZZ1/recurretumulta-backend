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
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN no está configurado en el backend.")
    if not x_admin_token or x_admin_token.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

def _ddl_statements() -> List[Tuple[str, str]]:
    return [
        ("extensions", "CREATE EXTENSION IF NOT EXISTS pgcrypto;"),
        ("cases", '''
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
        '''),
        ("documents", '''
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
        '''),
        ("extractions", '''
            CREATE TABLE IF NOT EXISTS extractions (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
              extracted_json JSONB NOT NULL,
              confidence DOUBLE PRECISION,
              model TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        '''),
        ("events", '''
            CREATE TABLE IF NOT EXISTS events (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              case_id UUID REFERENCES cases(id) ON DELETE CASCADE,
              type TEXT NOT NULL,
              payload JSONB,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        '''),
        ("idx_cases_status", "CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);"),
        ("idx_events_case", "CREATE INDEX IF NOT EXISTS idx_events_case ON events(case_id);"),
    ]

def run_migration(engine: Engine) -> List[str]:
    created: List[str] = []
    with engine.begin() as conn:
        for name, sql in _ddl_statements():
            conn.execute(text(sql))
            created.append(name)
    return created

@router.post("/init", response_model=MigrateResponse)
def migrate_init(x_admin_token: str | None = Header(default=None, alias="x-admin-token")):
    _require_admin_token(x_admin_token)

    from database import get_engine
    engine = get_engine()

    try:
        created = run_migration(engine)
        return MigrateResponse(ok=True, message="Migración inicial aplicada.", created=created)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error migrando: {e}")
