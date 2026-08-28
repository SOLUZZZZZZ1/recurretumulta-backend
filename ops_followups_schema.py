"""Esquema persistente de seguimientos operativos de RTM OPS.

El módulo solo define DDL aditivo e idempotente. La aplicación deliberada en
staging se realiza mediante ``scripts/rtm_staging_ops_followups_schema.py``.
"""

from __future__ import annotations


SCHEMA_VERSION = "rtm_ops_followups_schema_v1_0"

REQUIRED_COLUMNS = {
    "id",
    "case_id",
    "kind",
    "status",
    "title",
    "description",
    "due_at",
    "source_event_type",
    "created_by",
    "resolved_at",
    "resolved_by",
    "resolution_note",
    "created_at",
    "updated_at",
}

REQUIRED_INDEXES = {
    "idx_ops_followups_case_due",
    "idx_ops_followups_pending_due",
    "idx_ops_followups_source_event",
}


def ops_followups_ddl() -> list[tuple[str, str]]:
    """Devuelve la migración aditiva necesaria para la bandeja OPS."""

    return [
        ("extensions", "CREATE EXTENSION IF NOT EXISTS pgcrypto;"),
        (
            "ops_followups",
            """
            CREATE TABLE IF NOT EXISTS ops_followups (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
              kind TEXT NOT NULL DEFAULT 'seguimiento',
              status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'resolved')),
              title TEXT NOT NULL,
              description TEXT,
              due_at TIMESTAMPTZ,
              source_event_type TEXT,
              created_by TEXT NOT NULL DEFAULT 'ops',
              resolved_at TIMESTAMPTZ,
              resolved_by TEXT,
              resolution_note TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
        ),
        (
            "idx_ops_followups_case_due",
            """
            CREATE INDEX IF NOT EXISTS idx_ops_followups_case_due
              ON ops_followups(case_id, due_at, created_at DESC);
            """,
        ),
        (
            "idx_ops_followups_pending_due",
            """
            CREATE INDEX IF NOT EXISTS idx_ops_followups_pending_due
              ON ops_followups(due_at, updated_at DESC)
              WHERE status = 'pending';
            """,
        ),
        (
            "idx_ops_followups_source_event",
            """
            CREATE INDEX IF NOT EXISTS idx_ops_followups_source_event
              ON ops_followups(case_id, source_event_type);
            """,
        ),
        (
            "ops_followups_comment",
            f"COMMENT ON TABLE ops_followups IS '{SCHEMA_VERSION}';",
        ),
    ]
