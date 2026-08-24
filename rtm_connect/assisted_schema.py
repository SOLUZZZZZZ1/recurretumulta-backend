"""Esquema aditivo del handoff juridico asistido RTM CONNECT C7.

Las tablas C7 son deliberadamente distintas de las tareas C3: comparten el
kernel C1, pero C7 congela revision, liberacion humana, incertidumbre y triple
separacion de funciones sin relajar los guards ya publicados por C3.
"""

from __future__ import annotations


RTM_CONNECT_C7_ASSISTED_SCHEMA_VERSION = (
    "rtm_connect_c7_assisted_schema_v1_0"
)

ASSISTED_TASK_STATUSES = (
    "prepared",
    "assigned",
    "reviewing",
    "ready_for_release",
    "released",
    "in_progress",
    "awaiting_receipt",
    "outcome_unknown",
    "reconciling",
    "receipt_submitted",
    "verified",
    "completed",
    "manual_review",
    "permanent_failed",
)

CONNECT_C7_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "rtm_connect_assisted_tasks": {
        "id", "action_id", "attempt_id", "connector_id",
        "authorization_id", "authorization_version", "task_code",
        "status", "assignee_operator_id", "assigned_by_operator_id",
        "assigned_at", "release_operator_id", "released_at",
        "verified_by_operator_id", "due_at", "started_at",
        "reviewed_at", "ready_at", "unknown_at", "reconciling_at",
        "receipt_submitted_at", "verified_at", "completed_at",
        "package_manifest", "package_sha256",
        "review_attestation_sha256", "release_attestation_sha256",
        "external_reference", "receipt_evidence_id",
        "verified_evidence_id", "version", "metadata", "created_at",
        "updated_at",
    },
    "rtm_connect_assisted_events": {
        "id", "task_id", "action_id", "attempt_id", "sequence_number",
        "event_type", "actor_type", "operator_id", "from_status",
        "to_status", "reason_code", "payload", "created_at",
    },
}

CONNECT_C7_REQUIRED_INDEXES = {
    "uq_rtm_connect_assisted_task_action",
    "uq_rtm_connect_assisted_task_attempt",
    "uq_rtm_connect_assisted_task_code",
    "idx_rtm_connect_assisted_task_queue",
    "idx_rtm_connect_assisted_task_action",
    "uq_rtm_connect_assisted_event_sequence",
    "idx_rtm_connect_assisted_event_action",
    "idx_rtm_connect_assisted_event_operator",
}

CONNECT_C7_REQUIRED_TRIGGERS = {
    "trg_rtm_connect_assisted_task_scope_guard",
    "trg_rtm_connect_assisted_task_state_guard",
    "trg_rtm_connect_assisted_task_frozen",
    "trg_rtm_connect_assisted_event_scope_guard",
    "trg_rtm_connect_assisted_events_append_only",
}

CONNECT_C7_REQUIRED_CONSTRAINTS = {
    "ck_rtm_connect_assisted_task_code",
    "ck_rtm_connect_assisted_task_status",
    "ck_rtm_connect_assisted_task_package",
    "ck_rtm_connect_assisted_task_package_sha256",
    "ck_rtm_connect_assisted_task_attestations",
    "ck_rtm_connect_assisted_task_version",
    "ck_rtm_connect_assisted_task_assignment",
    "ck_rtm_connect_assisted_task_review",
    "ck_rtm_connect_assisted_task_release",
    "ck_rtm_connect_assisted_task_started",
    "ck_rtm_connect_assisted_task_unknown",
    "ck_rtm_connect_assisted_task_receipt",
    "ck_rtm_connect_assisted_task_verified",
    "ck_rtm_connect_assisted_task_completed",
    "ck_rtm_connect_assisted_task_separation",
    "ck_rtm_connect_assisted_task_due",
    "ck_rtm_connect_assisted_task_metadata",
    "ck_rtm_connect_assisted_event_sequence",
    "ck_rtm_connect_assisted_event_actor",
    "ck_rtm_connect_assisted_event_type",
    "ck_rtm_connect_assisted_event_payload",
}


def connect_c7_assisted_ddl() -> list[tuple[str, str]]:
    """DDL PostgreSQL idempotente, aditivo y no destructivo de C7."""

    return [
        ("extensions", "CREATE EXTENSION IF NOT EXISTS pgcrypto;"),
        (
            "assisted_tasks",
            """
            CREATE TABLE IF NOT EXISTS rtm_connect_assisted_tasks (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                action_id UUID NOT NULL
                    REFERENCES rtm_connect_actions(id) ON DELETE RESTRICT,
                attempt_id UUID NOT NULL
                    REFERENCES rtm_connect_attempts(id) ON DELETE RESTRICT,
                connector_id UUID NOT NULL
                    REFERENCES rtm_connect_connectors(id) ON DELETE RESTRICT,
                authorization_id UUID NOT NULL
                    REFERENCES rtm_connect_authorizations(id)
                    ON DELETE RESTRICT,
                authorization_version INTEGER NOT NULL,
                task_code TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'prepared',
                assignee_operator_id UUID
                    REFERENCES rtm_operators(id) ON DELETE RESTRICT,
                assigned_by_operator_id UUID
                    REFERENCES rtm_operators(id) ON DELETE RESTRICT,
                assigned_at TIMESTAMPTZ,
                release_operator_id UUID
                    REFERENCES rtm_operators(id) ON DELETE RESTRICT,
                released_at TIMESTAMPTZ,
                verified_by_operator_id UUID
                    REFERENCES rtm_operators(id) ON DELETE RESTRICT,
                due_at TIMESTAMPTZ NOT NULL,
                started_at TIMESTAMPTZ,
                reviewed_at TIMESTAMPTZ,
                ready_at TIMESTAMPTZ,
                unknown_at TIMESTAMPTZ,
                reconciling_at TIMESTAMPTZ,
                receipt_submitted_at TIMESTAMPTZ,
                verified_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                package_manifest JSONB NOT NULL,
                package_sha256 TEXT NOT NULL,
                review_attestation_sha256 TEXT,
                release_attestation_sha256 TEXT,
                external_reference TEXT,
                receipt_evidence_id UUID
                    REFERENCES rtm_connect_evidence(id) ON DELETE RESTRICT,
                verified_evidence_id UUID
                    REFERENCES rtm_connect_evidence(id) ON DELETE RESTRICT,
                version INTEGER NOT NULL DEFAULT 1,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_connect_assisted_task_code CHECK (
                    task_code ~ '^rtm-assisted-[0-9a-f]{24}$'
                ),
                CONSTRAINT ck_rtm_connect_assisted_task_status CHECK (
                    status IN (
                        'prepared', 'assigned', 'reviewing',
                        'ready_for_release', 'released', 'in_progress',
                        'awaiting_receipt', 'outcome_unknown', 'reconciling',
                        'receipt_submitted', 'verified', 'completed',
                        'manual_review', 'permanent_failed'
                    )
                ),
                CONSTRAINT ck_rtm_connect_assisted_task_package CHECK (
                    jsonb_typeof(package_manifest) = 'object'
                ),
                CONSTRAINT ck_rtm_connect_assisted_task_package_sha256 CHECK (
                    package_sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_connect_assisted_task_attestations CHECK (
                    (review_attestation_sha256 IS NULL OR
                        review_attestation_sha256 ~ '^[0-9a-f]{64}$')
                    AND (release_attestation_sha256 IS NULL OR
                        release_attestation_sha256 ~ '^[0-9a-f]{64}$')
                ),
                CONSTRAINT ck_rtm_connect_assisted_task_version CHECK (
                    version > 0 AND authorization_version > 0
                ),
                CONSTRAINT ck_rtm_connect_assisted_task_assignment CHECK (
                    status = 'prepared' OR (
                        assignee_operator_id IS NOT NULL
                        AND assigned_by_operator_id IS NOT NULL
                        AND assigned_at IS NOT NULL
                    )
                ),
                CONSTRAINT ck_rtm_connect_assisted_task_review CHECK (
                    status NOT IN (
                        'ready_for_release', 'released', 'in_progress',
                        'awaiting_receipt', 'outcome_unknown', 'reconciling',
                        'receipt_submitted', 'verified', 'completed',
                        'manual_review', 'permanent_failed'
                    ) OR (
                        reviewed_at IS NOT NULL
                        AND ready_at IS NOT NULL
                        AND review_attestation_sha256 IS NOT NULL
                    )
                ),
                CONSTRAINT ck_rtm_connect_assisted_task_release CHECK (
                    status NOT IN (
                        'released', 'in_progress', 'awaiting_receipt',
                        'outcome_unknown', 'reconciling', 'receipt_submitted',
                        'verified', 'completed', 'manual_review',
                        'permanent_failed'
                    ) OR (
                        release_operator_id IS NOT NULL
                        AND released_at IS NOT NULL
                        AND release_attestation_sha256 IS NOT NULL
                    )
                ),
                CONSTRAINT ck_rtm_connect_assisted_task_started CHECK (
                    status NOT IN (
                        'in_progress', 'awaiting_receipt', 'outcome_unknown',
                        'reconciling', 'receipt_submitted', 'verified',
                        'completed', 'manual_review', 'permanent_failed'
                    ) OR started_at IS NOT NULL
                ),
                CONSTRAINT ck_rtm_connect_assisted_task_unknown CHECK (
                    status NOT IN (
                        'outcome_unknown', 'reconciling', 'manual_review',
                        'permanent_failed'
                    ) OR (
                        unknown_at IS NOT NULL
                        AND external_reference IS NOT NULL
                    )
                ),
                CONSTRAINT ck_rtm_connect_assisted_task_receipt CHECK (
                    status NOT IN ('receipt_submitted', 'verified', 'completed')
                    OR (
                        receipt_submitted_at IS NOT NULL
                        AND external_reference IS NOT NULL
                        AND receipt_evidence_id IS NOT NULL
                    )
                ),
                CONSTRAINT ck_rtm_connect_assisted_task_verified CHECK (
                    status NOT IN ('verified', 'completed') OR (
                        verified_at IS NOT NULL
                        AND verified_by_operator_id IS NOT NULL
                        AND verified_evidence_id IS NOT NULL
                    )
                ),
                CONSTRAINT ck_rtm_connect_assisted_task_completed CHECK (
                    status <> 'completed' OR completed_at IS NOT NULL
                ),
                CONSTRAINT ck_rtm_connect_assisted_task_separation CHECK (
                    (release_operator_id IS NULL OR assignee_operator_id IS NULL
                        OR release_operator_id <> assignee_operator_id)
                    AND (verified_by_operator_id IS NULL
                        OR assignee_operator_id IS NULL
                        OR verified_by_operator_id <> assignee_operator_id)
                    AND (verified_by_operator_id IS NULL
                        OR release_operator_id IS NULL
                        OR verified_by_operator_id <> release_operator_id)
                ),
                CONSTRAINT ck_rtm_connect_assisted_task_due CHECK (
                    due_at > created_at
                ),
                CONSTRAINT ck_rtm_connect_assisted_task_metadata CHECK (
                    jsonb_typeof(metadata) = 'object'
                )
            );
            """,
        ),
        (
            "assisted_events",
            """
            CREATE TABLE IF NOT EXISTS rtm_connect_assisted_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                task_id UUID NOT NULL
                    REFERENCES rtm_connect_assisted_tasks(id)
                    ON DELETE CASCADE,
                action_id UUID NOT NULL
                    REFERENCES rtm_connect_actions(id) ON DELETE RESTRICT,
                attempt_id UUID NOT NULL
                    REFERENCES rtm_connect_attempts(id) ON DELETE RESTRICT,
                sequence_number INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                operator_id UUID
                    REFERENCES rtm_operators(id) ON DELETE SET NULL,
                from_status TEXT,
                to_status TEXT,
                reason_code TEXT NOT NULL,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_connect_assisted_event_sequence CHECK (
                    sequence_number > 0
                ),
                CONSTRAINT ck_rtm_connect_assisted_event_actor CHECK (
                    actor_type IN (
                        'operator', 'connect', 'core', 'reconciliation',
                        'system'
                    )
                ),
                CONSTRAINT ck_rtm_connect_assisted_event_type CHECK (
                    event_type ~ '^[a-z][a-z0-9_.-]{2,95}$'
                ),
                CONSTRAINT ck_rtm_connect_assisted_event_payload CHECK (
                    jsonb_typeof(payload) = 'object'
                )
            );
            """,
        ),
        ("uq_assisted_task_action", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_assisted_task_action
            ON rtm_connect_assisted_tasks(action_id);
        """),
        ("uq_assisted_task_attempt", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_assisted_task_attempt
            ON rtm_connect_assisted_tasks(attempt_id);
        """),
        ("uq_assisted_task_code", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_assisted_task_code
            ON rtm_connect_assisted_tasks(task_code);
        """),
        ("idx_assisted_task_queue", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_assisted_task_queue
            ON rtm_connect_assisted_tasks(
                assignee_operator_id, status, due_at, created_at
            );
        """),
        ("idx_assisted_task_action", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_assisted_task_action
            ON rtm_connect_assisted_tasks(action_id, status, updated_at DESC);
        """),
        ("uq_assisted_event_sequence", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_assisted_event_sequence
            ON rtm_connect_assisted_events(task_id, sequence_number);
        """),
        ("idx_assisted_event_action", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_assisted_event_action
            ON rtm_connect_assisted_events(
                action_id, created_at, sequence_number
            );
        """),
        ("idx_assisted_event_operator", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_assisted_event_operator
            ON rtm_connect_assisted_events(operator_id, created_at DESC);
        """),
        (
            "assisted_task_scope_guard_function",
            """
            CREATE OR REPLACE FUNCTION
                rtm_connect_assisted_task_scope_guard()
            RETURNS trigger AS $$
            DECLARE
                attempt_action_id UUID;
                attempt_connector_id UUID;
                authorization_action_id UUID;
                persisted_authorization_version INTEGER;
            BEGIN
                SELECT action_id, connector_id
                  INTO attempt_action_id, attempt_connector_id
                FROM rtm_connect_attempts
                WHERE id=NEW.attempt_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'assisted task attempt does not exist';
                END IF;
                SELECT action_id, authorization_version
                  INTO authorization_action_id,
                       persisted_authorization_version
                FROM rtm_connect_authorizations
                WHERE id=NEW.authorization_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'assisted task authorization does not exist';
                END IF;
                IF attempt_action_id IS DISTINCT FROM NEW.action_id
                    OR attempt_connector_id IS DISTINCT FROM NEW.connector_id
                    OR authorization_action_id IS DISTINCT FROM NEW.action_id
                    OR persisted_authorization_version
                        IS DISTINCT FROM NEW.authorization_version
                THEN
                    RAISE EXCEPTION
                        'assisted task differs from kernel scope';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "assisted_task_scope_guard_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname='trg_rtm_connect_assisted_task_scope_guard'
                ) THEN
                    EXECUTE 'CREATE TRIGGER
                        trg_rtm_connect_assisted_task_scope_guard
                        BEFORE INSERT OR UPDATE ON rtm_connect_assisted_tasks
                        FOR EACH ROW EXECUTE FUNCTION
                        rtm_connect_assisted_task_scope_guard()';
                END IF;
            END $$;
            """,
        ),
        (
            "assisted_task_state_guard_function",
            """
            CREATE OR REPLACE FUNCTION rtm_connect_assisted_task_state_guard()
            RETURNS trigger AS $$
            DECLARE transition_ok BOOLEAN := FALSE;
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NEW.status <> 'prepared' OR NEW.version <> 1 THEN
                        RAISE EXCEPTION
                            'assisted task must start prepared at version 1';
                    END IF;
                    RETURN NEW;
                END IF;
                IF NEW.version <> OLD.version + 1 THEN
                    RAISE EXCEPTION
                        'assisted task version must increment exactly once';
                END IF;
                transition_ok := CASE
                    WHEN OLD.status='prepared' AND NEW.status='assigned' THEN TRUE
                    WHEN OLD.status='assigned' AND NEW.status='reviewing' THEN TRUE
                    WHEN OLD.status='reviewing'
                        AND NEW.status='ready_for_release' THEN TRUE
                    WHEN OLD.status='ready_for_release'
                        AND NEW.status='released' THEN TRUE
                    WHEN OLD.status='released' AND NEW.status='in_progress' THEN TRUE
                    WHEN OLD.status='in_progress'
                        AND NEW.status IN ('awaiting_receipt','outcome_unknown')
                        THEN TRUE
                    WHEN OLD.status='outcome_unknown'
                        AND NEW.status='reconciling' THEN TRUE
                    WHEN OLD.status='reconciling'
                        AND NEW.status IN (
                            'receipt_submitted', 'outcome_unknown',
                            'manual_review', 'permanent_failed'
                        ) THEN TRUE
                    WHEN OLD.status='awaiting_receipt'
                        AND NEW.status='receipt_submitted' THEN TRUE
                    WHEN OLD.status='receipt_submitted'
                        AND NEW.status='verified' THEN TRUE
                    WHEN OLD.status='verified' AND NEW.status='completed' THEN TRUE
                    ELSE FALSE
                END;
                IF NOT transition_ok THEN
                    RAISE EXCEPTION 'invalid assisted task transition: % -> %',
                        OLD.status, NEW.status;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "assisted_task_state_guard_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname='trg_rtm_connect_assisted_task_state_guard'
                ) THEN
                    EXECUTE 'CREATE TRIGGER
                        trg_rtm_connect_assisted_task_state_guard
                        BEFORE INSERT OR UPDATE ON rtm_connect_assisted_tasks
                        FOR EACH ROW EXECUTE FUNCTION
                        rtm_connect_assisted_task_state_guard()';
                END IF;
            END $$;
            """,
        ),
        (
            "assisted_task_frozen_function",
            """
            CREATE OR REPLACE FUNCTION rtm_connect_assisted_task_frozen()
            RETURNS trigger AS $$
            BEGIN
                IF NEW.action_id IS DISTINCT FROM OLD.action_id
                    OR NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
                    OR NEW.connector_id IS DISTINCT FROM OLD.connector_id
                    OR NEW.authorization_id IS DISTINCT FROM OLD.authorization_id
                    OR NEW.authorization_version
                        IS DISTINCT FROM OLD.authorization_version
                    OR NEW.task_code IS DISTINCT FROM OLD.task_code
                    OR NEW.due_at IS DISTINCT FROM OLD.due_at
                    OR NEW.package_manifest IS DISTINCT FROM OLD.package_manifest
                    OR NEW.package_sha256 IS DISTINCT FROM OLD.package_sha256
                    OR NEW.metadata IS DISTINCT FROM OLD.metadata
                THEN
                    RAISE EXCEPTION 'assisted legal package is frozen';
                END IF;
                IF OLD.status <> 'prepared' AND (
                    NEW.assignee_operator_id
                        IS DISTINCT FROM OLD.assignee_operator_id
                    OR NEW.assigned_by_operator_id
                        IS DISTINCT FROM OLD.assigned_by_operator_id
                    OR NEW.assigned_at IS DISTINCT FROM OLD.assigned_at
                ) THEN
                    RAISE EXCEPTION 'assisted task assignment is frozen';
                END IF;
                IF OLD.started_at IS NOT NULL AND
                    NEW.started_at IS DISTINCT FROM OLD.started_at THEN
                    RAISE EXCEPTION 'assisted execution start is write-once';
                END IF;
                IF OLD.review_attestation_sha256 IS NOT NULL AND (
                    NEW.review_attestation_sha256
                        IS DISTINCT FROM OLD.review_attestation_sha256
                    OR NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at
                    OR NEW.ready_at IS DISTINCT FROM OLD.ready_at
                ) THEN
                    RAISE EXCEPTION 'assisted review attestation is write-once';
                END IF;
                IF OLD.release_attestation_sha256 IS NOT NULL AND (
                    NEW.release_attestation_sha256
                        IS DISTINCT FROM OLD.release_attestation_sha256
                    OR NEW.release_operator_id
                        IS DISTINCT FROM OLD.release_operator_id
                    OR NEW.released_at IS DISTINCT FROM OLD.released_at
                ) THEN
                    RAISE EXCEPTION 'assisted release is write-once';
                END IF;
                IF OLD.unknown_at IS NOT NULL AND (
                    NEW.unknown_at IS DISTINCT FROM OLD.unknown_at
                    OR NEW.external_reference
                        IS DISTINCT FROM OLD.external_reference
                ) THEN
                    RAISE EXCEPTION 'assisted unknown outcome is write-once';
                END IF;
                IF OLD.receipt_evidence_id IS NOT NULL AND (
                    NEW.receipt_evidence_id
                        IS DISTINCT FROM OLD.receipt_evidence_id
                    OR NEW.receipt_submitted_at
                        IS DISTINCT FROM OLD.receipt_submitted_at
                    OR NEW.external_reference
                        IS DISTINCT FROM OLD.external_reference
                ) THEN
                    RAISE EXCEPTION 'assisted receipt evidence is write-once';
                END IF;
                IF OLD.verified_evidence_id IS NOT NULL AND (
                    NEW.verified_evidence_id
                        IS DISTINCT FROM OLD.verified_evidence_id
                    OR NEW.verified_at IS DISTINCT FROM OLD.verified_at
                    OR NEW.verified_by_operator_id
                        IS DISTINCT FROM OLD.verified_by_operator_id
                ) THEN
                    RAISE EXCEPTION 'assisted verified evidence is write-once';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "assisted_task_frozen_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname='trg_rtm_connect_assisted_task_frozen'
                ) THEN
                    EXECUTE 'CREATE TRIGGER trg_rtm_connect_assisted_task_frozen
                        BEFORE UPDATE ON rtm_connect_assisted_tasks
                        FOR EACH ROW EXECUTE FUNCTION
                        rtm_connect_assisted_task_frozen()';
                END IF;
            END $$;
            """,
        ),
        (
            "assisted_event_scope_guard_function",
            """
            CREATE OR REPLACE FUNCTION
                rtm_connect_assisted_event_scope_guard()
            RETURNS trigger AS $$
            DECLARE
                parent_action_id UUID;
                parent_attempt_id UUID;
            BEGIN
                SELECT action_id, attempt_id
                  INTO parent_action_id, parent_attempt_id
                FROM rtm_connect_assisted_tasks
                WHERE id=NEW.task_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'assisted event parent task does not exist';
                END IF;
                IF NEW.action_id IS DISTINCT FROM parent_action_id
                    OR NEW.attempt_id IS DISTINCT FROM parent_attempt_id
                THEN
                    RAISE EXCEPTION
                        'assisted event differs from parent scope';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "assisted_event_scope_guard_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname='trg_rtm_connect_assisted_event_scope_guard'
                ) THEN
                    EXECUTE 'CREATE TRIGGER
                        trg_rtm_connect_assisted_event_scope_guard
                        BEFORE INSERT ON rtm_connect_assisted_events
                        FOR EACH ROW EXECUTE FUNCTION
                        rtm_connect_assisted_event_scope_guard()';
                END IF;
            END $$;
            """,
        ),
        (
            "assisted_events_append_only_function",
            """
            CREATE OR REPLACE FUNCTION rtm_connect_assisted_events_append_only()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'rtm_connect_assisted_events is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "assisted_events_append_only_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname='trg_rtm_connect_assisted_events_append_only'
                ) THEN
                    EXECUTE 'CREATE TRIGGER
                        trg_rtm_connect_assisted_events_append_only
                        BEFORE UPDATE OR DELETE ON rtm_connect_assisted_events
                        FOR EACH ROW EXECUTE FUNCTION
                        rtm_connect_assisted_events_append_only()';
                END IF;
            END $$;
            """,
        ),
    ]


__all__ = [
    "RTM_CONNECT_C7_ASSISTED_SCHEMA_VERSION",
    "ASSISTED_TASK_STATUSES",
    "CONNECT_C7_REQUIRED_COLUMNS",
    "CONNECT_C7_REQUIRED_INDEXES",
    "CONNECT_C7_REQUIRED_TRIGGERS",
    "CONNECT_C7_REQUIRED_CONSTRAINTS",
    "connect_c7_assisted_ddl",
]
