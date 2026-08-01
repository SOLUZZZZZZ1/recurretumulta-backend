# admin_migrate.py — migraciones admin (init + ampliaciones + autorización reforzada)
import os
import json
from typing import List, Tuple
from fastapi import APIRouter, Header, HTTPException, Query
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
# MIGRACIÓN: DATOS INTERESADO + AUTORIZACIÓN BASE
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
# MIGRACIÓN: AUTORIZACIÓN REFORZADA
# =========================================================

@router.post("/authorization_full", response_model=MigrateResponse)
def migrate_authorization_full(x_admin_token: str | None = Header(default=None, alias="x-admin-token")):
    _require_admin_token(x_admin_token)
    from database import get_engine
    engine = get_engine()

    ddl = [
        ("cases_authorization_version", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS authorization_version TEXT;"),
        ("cases_authorization_ip", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS authorization_ip TEXT;"),
        ("cases_authorization_user_agent", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS authorization_user_agent TEXT;"),
        ("cases_authorization_full_name", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS authorization_full_name TEXT;"),
        ("cases_authorization_dni_nie", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS authorization_dni_nie TEXT;"),
        ("cases_authorization_address", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS authorization_address TEXT;"),
        ("cases_authorization_email", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS authorization_email TEXT;"),
        ("cases_authorization_phone", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS authorization_phone TEXT;"),
        ("cases_authorization_checks", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS authorization_checks JSONB;"),
        ("cases_authorization_snapshot", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS authorization_snapshot JSONB;"),
        ("idx_cases_authorized", "CREATE INDEX IF NOT EXISTS idx_cases_authorized ON cases(authorized);"),
        ("idx_cases_authorized_at", "CREATE INDEX IF NOT EXISTS idx_cases_authorized_at ON cases(authorized_at);"),
    ]

    applied = _run(engine, ddl)
    return MigrateResponse(ok=True, message="Migración authorization_full aplicada.", created=applied)


# =========================================================
# MIGRACIÓN: PARTNERS + CANAL
# =========================================================

@router.post("/partners_channel", response_model=MigrateResponse)
def migrate_partners_channel(x_admin_token: str | None = Header(default=None, alias="x-admin-token")):
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
        (
            "partners_billing_mode",
            "ALTER TABLE partners ADD COLUMN IF NOT EXISTS billing_mode TEXT NOT NULL DEFAULT 'monthly';",
        ),
        (
            "partners_billing_status",
            "ALTER TABLE partners ADD COLUMN IF NOT EXISTS billing_status TEXT NOT NULL DEFAULT 'current';",
        ),
        ("cases_channel", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS channel TEXT NOT NULL DEFAULT 'direct';"),
        ("cases_partner_id", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS partner_id UUID NULL REFERENCES partners(id);"),
        ("cases_partner_name", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS partner_name TEXT;"),
        ("idx_partners_email", "CREATE INDEX IF NOT EXISTS idx_partners_email ON partners(email);"),
        ("idx_partners_billing_status", "CREATE INDEX IF NOT EXISTS idx_partners_billing_status ON partners(billing_status);"),
        ("idx_cases_partner", "CREATE INDEX IF NOT EXISTS idx_cases_partner ON cases(partner_id);"),
    ]

    applied = _run(engine, ddl)
    return MigrateResponse(ok=True, message="Migración partners_channel aplicada.", created=applied)


# =========================================================
# MIGRACIÓN: partners must_change_password
# =========================================================

@router.post("/partners_must_change_password", response_model=MigrateResponse)
def migrate_partners_must_change_password(
    x_admin_token: str | None = Header(default=None, alias="x-admin-token")
):
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


# =========================================================
# MIGRACIÓN: DGT/DEV submissions + submission_events
# =========================================================

@router.post("/dgt_dev_submissions", response_model=MigrateResponse)
def migrate_dgt_dev_submissions(x_admin_token: str | None = Header(default=None, alias="x-admin-token")):
    _require_admin_token(x_admin_token)

    from database import get_engine
    engine = get_engine()

    ddl = [
        (
            "submissions_table",
            """
            CREATE TABLE IF NOT EXISTS submissions (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
              channel TEXT NOT NULL DEFAULT 'DGT_DEV',
              remesa_id TEXT,
              notification_id TEXT,
              status TEXT NOT NULL DEFAULT 'queued',
              context_intensity TEXT,
              dry_run BOOLEAN NOT NULL DEFAULT TRUE,
              retry_count INT NOT NULL DEFAULT 0,
              last_error TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
        ),
        ("idx_submissions_case", "CREATE INDEX IF NOT EXISTS idx_submissions_case ON submissions(case_id);"),
        ("idx_submissions_status", "CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(status);"),
        ("idx_submissions_channel", "CREATE INDEX IF NOT EXISTS idx_submissions_channel ON submissions(channel);"),
        (
            "submission_events_table",
            """
            CREATE TABLE IF NOT EXISTS submission_events (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              submission_id UUID NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
              type TEXT NOT NULL,
              payload JSONB,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
        ),
        ("idx_submission_events_sub", "CREATE INDEX IF NOT EXISTS idx_submission_events_sub ON submission_events(submission_id);"),
    ]

    applied = _run(engine, ddl)
    return MigrateResponse(ok=True, message="Migración dgt_dev_submissions aplicada.", created=applied)



# =========================================================
# MIGRACIÓN: RTM CORE V1
# =========================================================

@router.post("/rtm_core_v1", response_model=MigrateResponse)
def migrate_rtm_core_v1(
    x_admin_token: str | None = Header(default=None, alias="x-admin-token")
):
    """
    Añade a cases los campos comunes del nuevo RTM CORE.
    Es segura: usa IF NOT EXISTS y no borra datos.
    """
    _require_admin_token(x_admin_token)

    from database import get_engine
    engine = get_engine()

    ddl = [
        (
            "cases_department",
            "ALTER TABLE cases ADD COLUMN IF NOT EXISTS department TEXT;",
        ),
        (
            "cases_case_type",
            "ALTER TABLE cases ADD COLUMN IF NOT EXISTS case_type TEXT;",
        ),
        (
            "cases_customer_comment",
            "ALTER TABLE cases ADD COLUMN IF NOT EXISTS customer_comment TEXT;",
        ),
        (
            "cases_source_module",
            "ALTER TABLE cases ADD COLUMN IF NOT EXISTS source_module TEXT;",
        ),
        (
            "idx_cases_department",
            "CREATE INDEX IF NOT EXISTS idx_cases_department ON cases(department);",
        ),
        (
            "idx_cases_case_type",
            "CREATE INDEX IF NOT EXISTS idx_cases_case_type ON cases(case_type);",
        ),
        (
            "idx_cases_department_status",
            "CREATE INDEX IF NOT EXISTS idx_cases_department_status ON cases(department, status);",
        ),
    ]

    applied = _run(engine, ddl)

    return MigrateResponse(
        ok=True,
        message="Migración RTM CORE v1 aplicada.",
        created=applied,
    )


# =========================================================
# OPS: LIMPIEZA OPERATIVA — MARCAR PRUEBAS ANTERIORES COMO LAB
# =========================================================

@router.post("/ops_clean_start_from_real_case", response_model=MigrateResponse)
def ops_clean_start_from_real_case(
    keep_case_id: str = Query(..., description="Case ID real que se conserva como expediente operativo"),
    expediente_ref: str = Query("V250274524", description="Referencia de expediente real de arranque"),
    dry_run: bool = Query(True, description="Si true, no actualiza; solo informa"),
    x_admin_token: str | None = Header(default=None, alias="x-admin-token"),
):
    """
    Limpieza segura: NO borra nada.

    Marca como archived_test los expedientes de laboratorio anteriores al primer caso real,
    y también duplicados del mismo expediente_ref, conservando el keep_case_id indicado.

    Uso recomendado:
    1) Ejecutar primero con dry_run=true.
    2) Si el resultado cuadra, ejecutar con dry_run=false.

    Ejemplo:
    /admin/migrate/ops_clean_start_from_real_case?keep_case_id=0dcd7bdc-4b81-450d-a274-0294bf708917&expediente_ref=V250274524&dry_run=true
    """
    _require_admin_token(x_admin_token)

    from database import get_engine
    engine = get_engine()

    with engine.begin() as conn:
        baseline = conn.execute(
            text(
                """
                SELECT id, expediente_ref, created_at
                FROM cases
                WHERE id = :keep_case_id
                """
            ),
            {"keep_case_id": keep_case_id},
        ).fetchone()

        if not baseline:
            raise HTTPException(status_code=404, detail="No se encuentra el expediente real indicado en keep_case_id")

        baseline_created_at = baseline[2]

        candidates = conn.execute(
            text(
                """
                SELECT id, expediente_ref, status, contact_email, created_at, updated_at
                FROM cases
                WHERE id <> :keep_case_id
                  AND COALESCE(status, '') NOT IN (
                    'presentado_manual_ayuntamiento',
                    'presentado_auto_dgt',
                    'presentado_auto_registro',
                    'submitted',
                    'closed',
                    'archived',
                    'resolved',
                    'estimado',
                    'desestimado'
                  )
                  AND (
                    created_at < :baseline_created_at
                    OR expediente_ref = :expediente_ref
                  )
                ORDER BY created_at ASC
                """
            ),
            {
                "keep_case_id": keep_case_id,
                "baseline_created_at": baseline_created_at,
                "expediente_ref": expediente_ref,
            },
        ).fetchall()

        candidate_ids = [str(r[0]) for r in candidates]

        if not dry_run and candidate_ids:
            try:
                conn.execute(
                    text(
                        """
                        UPDATE cases
                        SET status = 'archived_test',
                            updated_at = NOW()
                        WHERE id = ANY(CAST(:ids AS uuid[]))
                        """
                    ),
                    {"ids": candidate_ids},
                )

                for cid in candidate_ids:
                    conn.execute(
                        text(
                            """
                            INSERT INTO events(case_id, type, payload, created_at)
                            VALUES (
                              :case_id,
                              'ops_archived_as_test',
                              CAST(:payload AS JSONB),
                              NOW()
                            )
                            """
                        ),
                        {
                            "case_id": cid,
                            "payload": json.dumps(
                                {
                                    "reason": "Limpieza operativa: inicio desde primer expediente real",
                                    "kept_case_id": keep_case_id,
                                    "expediente_ref": expediente_ref,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    )
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Error aplicando limpieza operativa: {e}",
                )

        message = (
            f"Dry run: {len(candidate_ids)} expedientes serían marcados como archived_test."
            if dry_run
            else f"Limpieza aplicada: {len(candidate_ids)} expedientes marcados como archived_test."
        )

        created = [
            f"keep_case_id={keep_case_id}",
            f"expediente_ref={expediente_ref}",
            f"dry_run={dry_run}",
            f"candidates={len(candidate_ids)}",
        ]

        return MigrateResponse(ok=True, message=message, created=created)

