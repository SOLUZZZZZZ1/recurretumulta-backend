"""Migración idempotente de la capa de autoridad RTM CORE.

No borra ni transforma datos legacy. Crea las tablas nuevas y añade únicamente
columnas comunes que pueden faltar en instalaciones anteriores.
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Header
from sqlalchemy import text

from database import get_engine
from rtm_core.security import require_admin_token


router = APIRouter(prefix="/admin/migrate", tags=["admin", "rtm-core"])

RTM_CORE_AUTHORITY_SCHEMA_VERSION = "rtm_core_authority_schema_v1_0"


def authority_v1_ddl() -> list[tuple[str, str]]:
    return [
        ("extensions", "CREATE EXTENSION IF NOT EXISTS pgcrypto;"),
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
            "schema_migrations",
            """
            CREATE TABLE IF NOT EXISTS rtm_core_schema_migrations (
                name TEXT PRIMARY KEY,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
        ),
        (
            "validated_facts",
            """
            CREATE TABLE IF NOT EXISTS rtm_validated_facts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL CHECK (sequence > 0),
                version TEXT NOT NULL,
                service TEXT NOT NULL,
                extractor_version TEXT NOT NULL,
                payload JSONB NOT NULL,
                payload_sha256 TEXT NOT NULL,
                frozen BOOLEAN NOT NULL DEFAULT FALSE,
                created_by TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                invalidated_by TEXT,
                invalidated_at TIMESTAMPTZ,
                invalidation_reason TEXT,
                UNIQUE(case_id, sequence)
            );
            """,
        ),
        (
            "family_resolutions",
            """
            CREATE TABLE IF NOT EXISTS rtm_family_resolutions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                validated_facts_id UUID NOT NULL REFERENCES rtm_validated_facts(id),
                sequence INTEGER NOT NULL CHECK (sequence > 0),
                version TEXT NOT NULL,
                service TEXT NOT NULL,
                status TEXT NOT NULL,
                family TEXT,
                specialist TEXT,
                confidence DOUBLE PRECISION NOT NULL DEFAULT 0
                    CHECK (confidence >= 0 AND confidence <= 1),
                payload JSONB NOT NULL,
                payload_sha256 TEXT NOT NULL,
                locked BOOLEAN NOT NULL DEFAULT FALSE,
                created_by TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                invalidated_by TEXT,
                invalidated_at TIMESTAMPTZ,
                invalidation_reason TEXT,
                UNIQUE(case_id, sequence)
            );
            """,
        ),
        (
            "legal_previews",
            """
            CREATE TABLE IF NOT EXISTS rtm_legal_previews (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL CHECK (sequence > 0),
                status TEXT NOT NULL CHECK (
                    status IN (
                        'draft', 'ops_review', 'changes_required', 'approved',
                        'frozen', 'invalidated'
                    )
                ),
                service TEXT NOT NULL,
                family TEXT NOT NULL,
                specialist TEXT NOT NULL,
                facts_version TEXT NOT NULL,
                family_resolution_version TEXT NOT NULL,
                payload JSONB NOT NULL,
                payload_sha256 TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                approved_by TEXT,
                approved_at TIMESTAMPTZ,
                frozen_by TEXT,
                frozen_at TIMESTAMPTZ,
                invalidated_by TEXT,
                invalidated_at TIMESTAMPTZ,
                invalidation_reason TEXT,
                supersedes_id UUID REFERENCES rtm_legal_previews(id),
                state_reason TEXT,
                UNIQUE(case_id, sequence)
            );
            """,
        ),
        (
            "generated_resources",
            """
            CREATE TABLE IF NOT EXISTS rtm_generated_resources (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                legal_preview_id UUID NOT NULL REFERENCES rtm_legal_previews(id),
                sequence INTEGER NOT NULL CHECK (sequence > 0),
                status TEXT NOT NULL DEFAULT 'generated'
                    CHECK (status IN ('generated', 'final_ready', 'invalidated')),
                family TEXT NOT NULL,
                generator_version TEXT NOT NULL,
                preview_payload_sha256 TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                docx_document_id UUID REFERENCES documents(id),
                pdf_document_id UUID REFERENCES documents(id),
                generated_by TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                invalidated_at TIMESTAMPTZ,
                invalidation_reason TEXT,
                UNIQUE(case_id, sequence)
            );
            """,
        ),
        (
            "idx_validated_facts_case",
            "CREATE INDEX IF NOT EXISTS idx_rtm_validated_facts_case ON rtm_validated_facts(case_id, sequence DESC);",
        ),
        (
            "uq_active_frozen_facts",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_active_frozen_facts
            ON rtm_validated_facts(case_id)
            WHERE frozen = TRUE AND invalidated_at IS NULL;
            """,
        ),
        (
            "idx_family_case",
            "CREATE INDEX IF NOT EXISTS idx_rtm_family_case ON rtm_family_resolutions(case_id, sequence DESC);",
        ),
        (
            "uq_active_locked_family",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_active_locked_family
            ON rtm_family_resolutions(case_id)
            WHERE locked = TRUE AND invalidated_at IS NULL;
            """,
        ),
        (
            "idx_preview_case",
            "CREATE INDEX IF NOT EXISTS idx_rtm_preview_case ON rtm_legal_previews(case_id, sequence DESC);",
        ),
        (
            "uq_active_preview",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_active_preview
            ON rtm_legal_previews(case_id)
            WHERE status IN ('draft', 'ops_review', 'approved', 'frozen');
            """,
        ),
        (
            "idx_generated_case",
            "CREATE INDEX IF NOT EXISTS idx_rtm_generated_case ON rtm_generated_resources(case_id, sequence DESC);",
        ),
        (
            "uq_active_generated_preview",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_active_generated_preview
            ON rtm_generated_resources(legal_preview_id)
            WHERE status <> 'invalidated';
            """,
        ),
        (
            "idx_cases_department_status",
            "CREATE INDEX IF NOT EXISTS idx_cases_department_status ON cases(department, status);",
        ),
    ]


@router.post("/rtm_core_authority_v1")
def migrate_rtm_core_authority_v1(
    x_admin_token: Optional[str] = Header(default=None, alias="x-admin-token"),
):
    require_admin_token(x_admin_token)
    engine = get_engine()
    applied: list[str] = []

    with engine.begin() as conn:
        for name, statement in authority_v1_ddl():
            conn.execute(text(statement))
            applied.append(name)

        conn.execute(
            text(
                """
                INSERT INTO rtm_core_schema_migrations(name, metadata, applied_at)
                VALUES (:name, CAST(:metadata AS JSONB), NOW())
                ON CONFLICT (name)
                DO UPDATE SET metadata=EXCLUDED.metadata, applied_at=NOW()
                """
            ),
            {
                "name": RTM_CORE_AUTHORITY_SCHEMA_VERSION,
                "metadata": json.dumps(
                    {
                        "tables": [
                            "rtm_validated_facts",
                            "rtm_family_resolutions",
                            "rtm_legal_previews",
                            "rtm_generated_resources",
                        ],
                        "destructive": False,
                    }
                ),
            },
        )

    return {
        "ok": True,
        "version": RTM_CORE_AUTHORITY_SCHEMA_VERSION,
        "destructive": False,
        "applied": applied,
    }
