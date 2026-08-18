"""Esquema aditivo de RTM Management Core V1.

Este módulo define únicamente DDL idempotente y no destructivo para la base
transversal de operadores, asignaciones, atención y plazos. No sustituye el
login OPS actual, no crea operadores reales y no ejecuta efectos externos.
"""

from __future__ import annotations


RTM_MANAGEMENT_SCHEMA_VERSION = "rtm_management_core_schema_v1_0"


def management_v1_ddl() -> list[tuple[str, str]]:
    """Devuelve DDL PostgreSQL aditivo e idempotente.

    Las cláusulas ``ON DELETE`` pertenecen a claves foráneas y no ejecutan
    borrados durante la migración. No se registran sentencias DROP, TRUNCATE ni
    DELETE ejecutables.
    """

    return [
        ("extensions", "CREATE EXTENSION IF NOT EXISTS pgcrypto;"),
        (
            "management_schema_migrations",
            """
            CREATE TABLE IF NOT EXISTS rtm_management_schema_migrations (
                name TEXT PRIMARY KEY,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(metadata) = 'object'),
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
        ),
        (
            "operator_roles",
            """
            CREATE TABLE IF NOT EXISTS rtm_operator_roles (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                permissions JSONB NOT NULL DEFAULT '[]'::jsonb
                    CHECK (jsonb_typeof(permissions) = 'array'),
                system_role BOOLEAN NOT NULL DEFAULT FALSE,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_operator_role_code
                    CHECK (code ~ '^[a-z][a-z0-9_.-]{2,63}$')
            );
            """,
        ),
        (
            "operators",
            """
            CREATE TABLE IF NOT EXISTS rtm_operators (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email TEXT NOT NULL,
                display_name TEXT NOT NULL,
                password_hash TEXT,
                status TEXT NOT NULL DEFAULT 'invited'
                    CHECK (status IN ('invited', 'active', 'suspended', 'disabled')),
                primary_role_id UUID REFERENCES rtm_operator_roles(id)
                    ON DELETE SET NULL,
                must_change_password BOOLEAN NOT NULL DEFAULT TRUE,
                mfa_required BOOLEAN NOT NULL DEFAULT FALSE,
                profile JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(profile) = 'object'),
                last_login_at TIMESTAMPTZ,
                created_by UUID REFERENCES rtm_operators(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                disabled_by UUID REFERENCES rtm_operators(id) ON DELETE SET NULL,
                disabled_at TIMESTAMPTZ,
                CONSTRAINT ck_rtm_operator_disabled_state CHECK (
                    (status = 'disabled' AND disabled_at IS NOT NULL)
                    OR (status <> 'disabled')
                )
            );
            """,
        ),
        (
            "operator_sessions",
            """
            CREATE TABLE IF NOT EXISTS rtm_operator_sessions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                operator_id UUID NOT NULL REFERENCES rtm_operators(id)
                    ON DELETE CASCADE,
                token_sha256 TEXT NOT NULL UNIQUE
                    CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'closed', 'revoked', 'expired')),
                login_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                logout_at TIMESTAMPTZ,
                revoked_at TIMESTAMPTZ,
                revoked_by UUID REFERENCES rtm_operators(id) ON DELETE SET NULL,
                close_reason TEXT,
                ip_address TEXT,
                user_agent TEXT,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(metadata) = 'object'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_operator_session_expiry
                    CHECK (expires_at > login_at),
                CONSTRAINT ck_rtm_operator_session_closed CHECK (
                    (status = 'active' AND logout_at IS NULL AND revoked_at IS NULL)
                    OR status <> 'active'
                )
            );
            """,
        ),
        (
            "attention_items",
            """
            CREATE TABLE IF NOT EXISTS rtm_attention_items (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                case_id UUID REFERENCES cases(id) ON DELETE CASCADE,
                satellite TEXT NOT NULL DEFAULT 'other',
                attention_class TEXT NOT NULL CHECK (
                    attention_class IN (
                        'deadline', 'document', 'workflow', 'data_quality',
                        'assignment', 'system_health', 'security'
                    )
                ),
                code TEXT NOT NULL,
                dedupe_key TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT,
                severity TEXT NOT NULL DEFAULT 'attention' CHECK (
                    severity IN (
                        'informational', 'attention', 'upcoming', 'urgent', 'critical'
                    )
                ),
                status TEXT NOT NULL DEFAULT 'new' CHECK (
                    status IN ('new', 'seen', 'assigned', 'in_review', 'resolved')
                ),
                source_event_id UUID REFERENCES events(id) ON DELETE SET NULL,
                source_document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
                source_entity_type TEXT,
                source_entity_id UUID,
                due_at TIMESTAMPTZ,
                assigned_operator_id UUID REFERENCES rtm_operators(id)
                    ON DELETE SET NULL,
                assigned_at TIMESTAMPTZ,
                seen_by UUID REFERENCES rtm_operators(id) ON DELETE SET NULL,
                seen_at TIMESTAMPTZ,
                in_review_by UUID REFERENCES rtm_operators(id) ON DELETE SET NULL,
                in_review_at TIMESTAMPTZ,
                resolved_by UUID REFERENCES rtm_operators(id) ON DELETE SET NULL,
                resolved_at TIMESTAMPTZ,
                resolution_code TEXT,
                resolution_note TEXT,
                version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
                first_detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(metadata) = 'object'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_attention_resolution_state CHECK (
                    (status = 'resolved' AND resolved_at IS NOT NULL)
                    OR (status <> 'resolved' AND resolved_at IS NULL)
                ),
                CONSTRAINT ck_rtm_attention_assignment_state CHECK (
                    assigned_operator_id IS NULL OR assigned_at IS NOT NULL
                )
            );
            """,
        ),
        (
            "deadlines",
            """
            CREATE TABLE IF NOT EXISTS rtm_deadlines (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                case_id UUID REFERENCES cases(id) ON DELETE CASCADE,
                attention_item_id UUID REFERENCES rtm_attention_items(id)
                    ON DELETE CASCADE,
                deadline_class TEXT NOT NULL CHECK (
                    deadline_class IN (
                        'operational', 'legal_candidate', 'legal_validated', 'system'
                    )
                ),
                code TEXT NOT NULL,
                title TEXT NOT NULL,
                source_event_id UUID REFERENCES events(id) ON DELETE SET NULL,
                source_document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
                origin_at TIMESTAMPTZ,
                origin_status TEXT NOT NULL DEFAULT 'unverified' CHECK (
                    origin_status IN ('missing', 'unverified', 'verified', 'conflicted')
                ),
                origin_timezone TEXT NOT NULL DEFAULT 'Europe/Madrid',
                rule_code TEXT,
                rule_version TEXT,
                computation_basis TEXT NOT NULL DEFAULT 'none' CHECK (
                    computation_basis IN (
                        'none', 'natural_days', 'business_days',
                        'calendar_date', 'manual'
                    )
                ),
                calendar_code TEXT,
                quantity INTEGER CHECK (quantity IS NULL OR quantity >= 0),
                due_at TIMESTAMPTZ,
                confidence DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (
                    confidence >= 0 AND confidence <= 1
                ),
                validation_status TEXT NOT NULL DEFAULT 'pending' CHECK (
                    validation_status IN (
                        'pending', 'validated', 'rejected', 'superseded'
                    )
                ),
                validated_by UUID REFERENCES rtm_operators(id) ON DELETE SET NULL,
                validated_at TIMESTAMPTZ,
                validation_note TEXT,
                supersedes_id UUID REFERENCES rtm_deadlines(id) ON DELETE SET NULL,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(metadata) = 'object'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_deadline_missing_origin CHECK (
                    origin_status <> 'missing'
                    OR (origin_at IS NULL AND due_at IS NULL)
                ),
                CONSTRAINT ck_rtm_deadline_due_has_authority CHECK (
                    due_at IS NULL
                    OR (
                        origin_at IS NOT NULL
                        AND rule_code IS NOT NULL
                        AND computation_basis <> 'none'
                    )
                ),
                CONSTRAINT ck_rtm_deadline_validated_state CHECK (
                    validation_status <> 'validated'
                    OR (
                        validated_at IS NOT NULL
                        AND due_at IS NOT NULL
                        AND origin_status = 'verified'
                    )
                )
            );
            """,
        ),
        (
            "work_assignments",
            """
            CREATE TABLE IF NOT EXISTS rtm_work_assignments (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                case_id UUID REFERENCES cases(id) ON DELETE CASCADE,
                attention_item_id UUID REFERENCES rtm_attention_items(id)
                    ON DELETE CASCADE,
                operator_id UUID NOT NULL REFERENCES rtm_operators(id)
                    ON DELETE RESTRICT,
                assignment_role TEXT NOT NULL CHECK (
                    assignment_role IN (
                        'responsible', 'reviewer', 'supervisor', 'observer'
                    )
                ),
                status TEXT NOT NULL DEFAULT 'active' CHECK (
                    status IN ('active', 'released', 'completed', 'reassigned')
                ),
                team_code TEXT,
                assigned_by UUID REFERENCES rtm_operators(id) ON DELETE SET NULL,
                assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                accepted_at TIMESTAMPTZ,
                released_at TIMESTAMPTZ,
                release_reason TEXT,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(metadata) = 'object'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_assignment_target CHECK (
                    case_id IS NOT NULL OR attention_item_id IS NOT NULL
                ),
                CONSTRAINT ck_rtm_assignment_release_state CHECK (
                    (status = 'active' AND released_at IS NULL)
                    OR status <> 'active'
                )
            );
            """,
        ),
        (
            "attention_events",
            """
            CREATE TABLE IF NOT EXISTS rtm_attention_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                -- Identificadores históricos sin FK: el registro append-only
                -- debe sobrevivir a limpiezas sintéticas y no bloquearlas.
                attention_item_id UUID,
                case_id UUID,
                operator_id UUID,
                session_id UUID,
                actor_type TEXT NOT NULL CHECK (
                    actor_type IN ('operator', 'system', 'integration', 'migration')
                ),
                event_type TEXT NOT NULL,
                result TEXT NOT NULL DEFAULT 'success' CHECK (
                    result IN ('success', 'failure', 'denied', 'noop')
                ),
                reason TEXT,
                request_id TEXT,
                previous_state JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(previous_state) = 'object'),
                new_state JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(new_state) = 'object'),
                payload JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(payload) = 'object'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
        ),
        (
            "attention_engine_runs",
            """
            CREATE TABLE IF NOT EXISTS rtm_attention_engine_runs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                run_key TEXT NOT NULL UNIQUE,
                engine_version TEXT NOT NULL,
                environment TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running' CHECK (
                    status IN ('running', 'succeeded', 'failed', 'partial', 'skipped')
                ),
                triggered_by TEXT NOT NULL DEFAULT 'system',
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                finished_at TIMESTAMPTZ,
                scanned_count INTEGER NOT NULL DEFAULT 0 CHECK (scanned_count >= 0),
                created_count INTEGER NOT NULL DEFAULT 0 CHECK (created_count >= 0),
                updated_count INTEGER NOT NULL DEFAULT 0 CHECK (updated_count >= 0),
                resolved_count INTEGER NOT NULL DEFAULT 0 CHECK (resolved_count >= 0),
                error_count INTEGER NOT NULL DEFAULT 0 CHECK (error_count >= 0),
                error_summary TEXT,
                metrics JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(metrics) = 'object'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_engine_run_finished CHECK (
                    (status = 'running' AND finished_at IS NULL)
                    OR (status <> 'running' AND finished_at IS NOT NULL)
                )
            );
            """,
        ),
        (
            "uq_operator_email",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_operator_email
            ON rtm_operators(lower(btrim(email)));
            """,
        ),
        (
            "uq_operator_role_code",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_operator_role_code
            ON rtm_operator_roles(code);
            """,
        ),
        (
            "idx_operator_status",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_operator_status
            ON rtm_operators(status, updated_at DESC);
            """,
        ),
        (
            "idx_operator_sessions_active",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_operator_sessions_active
            ON rtm_operator_sessions(operator_id, status, expires_at);
            """,
        ),
        (
            "uq_attention_active_dedupe",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_attention_active_dedupe
            ON rtm_attention_items(dedupe_key)
            WHERE status <> 'resolved';
            """,
        ),
        (
            "idx_attention_priority",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_attention_priority
            ON rtm_attention_items(status, severity, due_at, created_at);
            """,
        ),
        (
            "idx_attention_case",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_attention_case
            ON rtm_attention_items(case_id, status, updated_at DESC);
            """,
        ),
        (
            "idx_attention_assignee",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_attention_assignee
            ON rtm_attention_items(assigned_operator_id, status, due_at);
            """,
        ),
        (
            "idx_deadlines_due",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_deadlines_due
            ON rtm_deadlines(validation_status, due_at, deadline_class);
            """,
        ),
        (
            "idx_deadlines_case",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_deadlines_case
            ON rtm_deadlines(case_id, created_at DESC);
            """,
        ),
        (
            "uq_assignment_attention_role",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_assignment_attention_role
            ON rtm_work_assignments(attention_item_id, assignment_role)
            WHERE status = 'active'
              AND attention_item_id IS NOT NULL
              AND assignment_role <> 'observer';
            """,
        ),
        (
            "uq_assignment_case_role",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_assignment_case_role
            ON rtm_work_assignments(case_id, assignment_role)
            WHERE status = 'active'
              AND attention_item_id IS NULL
              AND case_id IS NOT NULL
              AND assignment_role <> 'observer';
            """,
        ),
        (
            "idx_assignments_operator",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_assignments_operator
            ON rtm_work_assignments(operator_id, status, assigned_at DESC);
            """,
        ),
        (
            "idx_attention_events_item",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_attention_events_item
            ON rtm_attention_events(attention_item_id, created_at DESC);
            """,
        ),
        (
            "idx_attention_events_case",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_attention_events_case
            ON rtm_attention_events(case_id, created_at DESC);
            """,
        ),
        (
            "idx_engine_runs_health",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_engine_runs_health
            ON rtm_attention_engine_runs(status, started_at DESC, heartbeat_at DESC);
            """,
        ),
        (
            "attention_events_append_only_function",
            """
            CREATE OR REPLACE FUNCTION rtm_guard_attention_events_append_only()
            RETURNS TRIGGER AS $$
            BEGIN
                RAISE EXCEPTION
                    'rtm_attention_events is append-only; % is not permitted',
                    TG_OP
                    USING ERRCODE = '55000';
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "attention_events_append_only_trigger",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_trigger
                    WHERE tgname = 'trg_rtm_attention_events_append_only'
                      AND tgrelid = 'rtm_attention_events'::regclass
                ) THEN
                    CREATE TRIGGER trg_rtm_attention_events_append_only
                    BEFORE UPDATE OR DELETE ON rtm_attention_events
                    FOR EACH ROW
                    EXECUTE FUNCTION rtm_guard_attention_events_append_only();
                END IF;
            END $$;
            """,
        ),
    ]


__all__ = [
    "RTM_MANAGEMENT_SCHEMA_VERSION",
    "management_v1_ddl",
]
