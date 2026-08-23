"""Esquema aditivo de RTM CONNECT C3 manual_handoff.

Añade tareas manuales normalizadas y su historial append-only. No publica rutas,
no registra conectores persistentes y no ejecuta efectos externos.
"""

from __future__ import annotations


RTM_CONNECT_C3_MANUAL_SCHEMA_VERSION = (
    "rtm_connect_c3_manual_schema_v1_0"
)

MANUAL_TASK_STATUSES = (
    "prepared",
    "assigned",
    "in_progress",
    "awaiting_receipt",
    "receipt_submitted",
    "verified",
    "completed",
)

CONNECT_C3_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "rtm_connect_manual_tasks": {
        "id", "action_id", "attempt_id", "connector_id", "task_code",
        "status", "assignee_operator_id", "assigned_by_operator_id",
        "assigned_at", "due_at", "started_at", "receipt_submitted_at",
        "verified_at", "verified_by_operator_id", "completed_at",
        "package_manifest", "package_sha256", "instructions",
        "external_reference", "version", "metadata", "created_at",
        "updated_at",
    },
    "rtm_connect_manual_events": {
        "id", "task_id", "action_id", "attempt_id", "sequence_number",
        "event_type", "actor_type", "operator_id", "from_status",
        "to_status", "reason_code", "reason_detail", "payload",
        "created_at",
    },
}

CONNECT_C3_REQUIRED_INDEXES = {
    "uq_rtm_connect_manual_task_action",
    "uq_rtm_connect_manual_task_attempt",
    "uq_rtm_connect_manual_task_code",
    "idx_rtm_connect_manual_task_queue",
    "idx_rtm_connect_manual_task_action",
    "uq_rtm_connect_manual_event_sequence",
    "idx_rtm_connect_manual_event_action",
    "idx_rtm_connect_manual_event_operator",
}

CONNECT_C3_REQUIRED_TRIGGERS = {
    "trg_rtm_connect_manual_task_state_guard",
    "trg_rtm_connect_manual_task_package_frozen",
    "trg_rtm_connect_manual_events_append_only",
}

CONNECT_C3_REQUIRED_CONSTRAINTS = {
    "ck_rtm_connect_manual_task_code",
    "ck_rtm_connect_manual_task_status",
    "ck_rtm_connect_manual_task_package",
    "ck_rtm_connect_manual_task_package_sha256",
    "ck_rtm_connect_manual_task_version",
    "ck_rtm_connect_manual_task_assignment",
    "ck_rtm_connect_manual_task_started",
    "ck_rtm_connect_manual_task_receipt",
    "ck_rtm_connect_manual_task_verified",
    "ck_rtm_connect_manual_task_completed",
    "ck_rtm_connect_manual_task_due",
    "ck_rtm_connect_manual_task_metadata",
    "ck_rtm_connect_manual_event_sequence",
    "ck_rtm_connect_manual_event_actor",
    "ck_rtm_connect_manual_event_type",
    "ck_rtm_connect_manual_event_payload",
}


def connect_c3_manual_ddl() -> list[tuple[str, str]]:
    """Devuelve DDL PostgreSQL idempotente, aditivo y no destructivo."""

    return [
        ("extensions", "CREATE EXTENSION IF NOT EXISTS pgcrypto;"),
        (
            "manual_tasks",
            """
            CREATE TABLE IF NOT EXISTS rtm_connect_manual_tasks (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                action_id UUID NOT NULL
                    REFERENCES rtm_connect_actions(id) ON DELETE RESTRICT,
                attempt_id UUID NOT NULL
                    REFERENCES rtm_connect_attempts(id) ON DELETE RESTRICT,
                connector_id UUID NOT NULL
                    REFERENCES rtm_connect_connectors(id) ON DELETE RESTRICT,
                task_code TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'prepared',
                assignee_operator_id UUID
                    REFERENCES rtm_operators(id) ON DELETE RESTRICT,
                assigned_by_operator_id UUID
                    REFERENCES rtm_operators(id) ON DELETE RESTRICT,
                assigned_at TIMESTAMPTZ,
                due_at TIMESTAMPTZ NOT NULL,
                started_at TIMESTAMPTZ,
                receipt_submitted_at TIMESTAMPTZ,
                verified_at TIMESTAMPTZ,
                verified_by_operator_id UUID
                    REFERENCES rtm_operators(id) ON DELETE RESTRICT,
                completed_at TIMESTAMPTZ,
                package_manifest JSONB NOT NULL,
                package_sha256 TEXT NOT NULL,
                instructions TEXT NOT NULL,
                external_reference TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_connect_manual_task_code CHECK (
                    task_code ~ '^rtm-manual-[0-9a-f]{24}$'
                ),
                CONSTRAINT ck_rtm_connect_manual_task_status CHECK (
                    status IN (
                        'prepared', 'assigned', 'in_progress',
                        'awaiting_receipt', 'receipt_submitted',
                        'verified', 'completed'
                    )
                ),
                CONSTRAINT ck_rtm_connect_manual_task_package CHECK (
                    jsonb_typeof(package_manifest) = 'object'
                ),
                CONSTRAINT ck_rtm_connect_manual_task_package_sha256 CHECK (
                    package_sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_connect_manual_task_version CHECK (
                    version > 0
                ),
                CONSTRAINT ck_rtm_connect_manual_task_assignment CHECK (
                    status = 'prepared'
                    OR (
                        assignee_operator_id IS NOT NULL
                        AND assigned_by_operator_id IS NOT NULL
                        AND assigned_at IS NOT NULL
                    )
                ),
                CONSTRAINT ck_rtm_connect_manual_task_started CHECK (
                    status NOT IN (
                        'in_progress', 'awaiting_receipt',
                        'receipt_submitted', 'verified', 'completed'
                    )
                    OR started_at IS NOT NULL
                ),
                CONSTRAINT ck_rtm_connect_manual_task_receipt CHECK (
                    status NOT IN (
                        'receipt_submitted', 'verified', 'completed'
                    )
                    OR (
                        receipt_submitted_at IS NOT NULL
                        AND external_reference IS NOT NULL
                    )
                ),
                CONSTRAINT ck_rtm_connect_manual_task_verified CHECK (
                    status NOT IN ('verified', 'completed')
                    OR (
                        verified_at IS NOT NULL
                        AND verified_by_operator_id IS NOT NULL
                    )
                ),
                CONSTRAINT ck_rtm_connect_manual_task_completed CHECK (
                    status <> 'completed' OR completed_at IS NOT NULL
                ),
                CONSTRAINT ck_rtm_connect_manual_task_due CHECK (
                    due_at > created_at
                ),
                CONSTRAINT ck_rtm_connect_manual_task_metadata CHECK (
                    jsonb_typeof(metadata) = 'object'
                )
            );
            """,
        ),
        (
            "manual_events",
            """
            CREATE TABLE IF NOT EXISTS rtm_connect_manual_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                task_id UUID NOT NULL
                    REFERENCES rtm_connect_manual_tasks(id)
                    ON DELETE CASCADE,
                action_id UUID NOT NULL
                    REFERENCES rtm_connect_actions(id) ON DELETE RESTRICT,
                attempt_id UUID
                    REFERENCES rtm_connect_attempts(id) ON DELETE SET NULL,
                sequence_number INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                operator_id UUID
                    REFERENCES rtm_operators(id) ON DELETE SET NULL,
                from_status TEXT,
                to_status TEXT,
                reason_code TEXT NOT NULL,
                reason_detail TEXT,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_connect_manual_event_sequence CHECK (
                    sequence_number > 0
                ),
                CONSTRAINT ck_rtm_connect_manual_event_actor CHECK (
                    actor_type IN ('operator', 'connect', 'core', 'system')
                ),
                CONSTRAINT ck_rtm_connect_manual_event_type CHECK (
                    event_type ~ '^[a-z][a-z0-9_.-]{2,95}$'
                ),
                CONSTRAINT ck_rtm_connect_manual_event_payload CHECK (
                    jsonb_typeof(payload) = 'object'
                )
            );
            """,
        ),
        ("uq_manual_task_action", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_manual_task_action
            ON rtm_connect_manual_tasks(action_id);
        """),
        ("uq_manual_task_attempt", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_manual_task_attempt
            ON rtm_connect_manual_tasks(attempt_id);
        """),
        ("uq_manual_task_code", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_manual_task_code
            ON rtm_connect_manual_tasks(task_code);
        """),
        ("idx_manual_task_queue", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_manual_task_queue
            ON rtm_connect_manual_tasks(
                assignee_operator_id, status, due_at, created_at
            );
        """),
        ("idx_manual_task_action", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_manual_task_action
            ON rtm_connect_manual_tasks(action_id, status, updated_at DESC);
        """),
        ("uq_manual_event_sequence", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_manual_event_sequence
            ON rtm_connect_manual_events(task_id, sequence_number);
        """),
        ("idx_manual_event_action", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_manual_event_action
            ON rtm_connect_manual_events(action_id, created_at, sequence_number);
        """),
        ("idx_manual_event_operator", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_manual_event_operator
            ON rtm_connect_manual_events(operator_id, created_at DESC);
        """),
        (
            "manual_task_state_guard_function",
            """
            CREATE OR REPLACE FUNCTION
                rtm_connect_manual_task_state_guard()
            RETURNS trigger AS $$
            DECLARE
                transition_ok BOOLEAN := FALSE;
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NEW.status <> 'prepared' OR NEW.version <> 1 THEN
                        RAISE EXCEPTION
                            'manual task must start prepared at version 1';
                    END IF;
                    RETURN NEW;
                END IF;

                IF NEW.version <> OLD.version + 1 THEN
                    RAISE EXCEPTION
                        'manual task version must increment exactly once';
                END IF;

                transition_ok := CASE
                    WHEN OLD.status = 'prepared'
                        AND NEW.status = 'assigned' THEN TRUE
                    WHEN OLD.status = 'assigned'
                        AND NEW.status = 'in_progress' THEN TRUE
                    WHEN OLD.status = 'in_progress'
                        AND NEW.status = 'awaiting_receipt' THEN TRUE
                    WHEN OLD.status = 'awaiting_receipt'
                        AND NEW.status = 'receipt_submitted' THEN TRUE
                    WHEN OLD.status = 'receipt_submitted'
                        AND NEW.status = 'verified' THEN TRUE
                    WHEN OLD.status = 'verified'
                        AND NEW.status = 'completed' THEN TRUE
                    ELSE FALSE
                END;

                IF NOT transition_ok THEN
                    RAISE EXCEPTION
                        'invalid manual task transition: % -> %',
                        OLD.status, NEW.status;
                END IF;

                IF OLD.status <> 'prepared' AND (
                    NEW.assignee_operator_id
                        IS DISTINCT FROM OLD.assignee_operator_id
                    OR NEW.assigned_by_operator_id
                        IS DISTINCT FROM OLD.assigned_by_operator_id
                    OR NEW.assigned_at IS DISTINCT FROM OLD.assigned_at
                ) THEN
                    RAISE EXCEPTION
                        'manual task assignment is frozen after assignment';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "manual_task_state_guard_trigger",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_manual_task_state_guard'
                ) THEN
                    EXECUTE '
                        CREATE TRIGGER
                            trg_rtm_connect_manual_task_state_guard
                        BEFORE INSERT OR UPDATE
                        ON rtm_connect_manual_tasks
                        FOR EACH ROW
                        EXECUTE FUNCTION
                            rtm_connect_manual_task_state_guard()
                    ';
                END IF;
            END $$;
            """,
        ),
        (
            "manual_task_package_frozen_function",
            """
            CREATE OR REPLACE FUNCTION
                rtm_connect_manual_task_package_frozen()
            RETURNS trigger AS $$
            BEGIN
                IF (
                    NEW.action_id IS DISTINCT FROM OLD.action_id
                    OR NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
                    OR NEW.connector_id IS DISTINCT FROM OLD.connector_id
                    OR NEW.task_code IS DISTINCT FROM OLD.task_code
                    OR NEW.due_at IS DISTINCT FROM OLD.due_at
                    OR NEW.package_manifest
                        IS DISTINCT FROM OLD.package_manifest
                    OR NEW.package_sha256
                        IS DISTINCT FROM OLD.package_sha256
                    OR NEW.instructions IS DISTINCT FROM OLD.instructions
                ) THEN
                    RAISE EXCEPTION 'manual handoff package is frozen';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "manual_task_package_frozen_trigger",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_manual_task_package_frozen'
                ) THEN
                    EXECUTE '
                        CREATE TRIGGER
                            trg_rtm_connect_manual_task_package_frozen
                        BEFORE UPDATE
                        ON rtm_connect_manual_tasks
                        FOR EACH ROW
                        EXECUTE FUNCTION
                            rtm_connect_manual_task_package_frozen()
                    ';
                END IF;
            END $$;
            """,
        ),
        (
            "manual_events_append_only_function",
            """
            CREATE OR REPLACE FUNCTION
                rtm_connect_manual_events_append_only()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION
                    'rtm_connect_manual_events is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "manual_events_append_only_trigger",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_manual_events_append_only'
                ) THEN
                    EXECUTE '
                        CREATE TRIGGER
                            trg_rtm_connect_manual_events_append_only
                        BEFORE UPDATE OR DELETE
                        ON rtm_connect_manual_events
                        FOR EACH ROW
                        EXECUTE FUNCTION
                            rtm_connect_manual_events_append_only()
                    ';
                END IF;
            END $$;
            """,
        ),
    ]


__all__ = [
    "RTM_CONNECT_C3_MANUAL_SCHEMA_VERSION",
    "MANUAL_TASK_STATUSES",
    "CONNECT_C3_REQUIRED_COLUMNS",
    "CONNECT_C3_REQUIRED_CONSTRAINTS",
    "CONNECT_C3_REQUIRED_INDEXES",
    "CONNECT_C3_REQUIRED_TRIGGERS",
    "connect_c3_manual_ddl",
]
