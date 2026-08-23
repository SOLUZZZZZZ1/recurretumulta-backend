"""Esquema aditivo de RTM CONNECT C4 webhook y reconciliacion.

Anade un inbox deduplicado, su historial append-only, reconciliaciones
durables y su historial append-only. La DLQ es el estado terminal
``dead_lettered`` del inbox. No publica rutas, no registra conectores
persistentes y no ejecuta efectos externos.
"""

from __future__ import annotations


RTM_CONNECT_C4_WEBHOOK_SCHEMA_VERSION = (
    "rtm_connect_c4_webhook_schema_v1_0"
)

WEBHOOK_INBOX_STATUSES = (
    "received",
    "verified",
    "matched",
    "processed",
    "dead_lettered",
)

RECONCILIATION_STATUSES = (
    "started",
    "resolved",
)

RECONCILIATION_RESOLUTIONS = (
    "confirmed",
    "retryable_failed",
    "unknown",
    "manual_review",
    "permanent_failed",
)

CONNECT_C4_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "rtm_connect_webhook_inbox": {
        "id", "ingress_connector_id", "source_event_id",
        "event_type", "deduplication_key", "origin_connector_code",
        "origin_connector_version", "reported_outcome",
        "claimed_action_id", "claimed_attempt_id", "matched_action_id",
        "matched_attempt_id", "external_reference", "request_sha256",
        "payload", "payload_sha256", "verification_method",
        "verification_sha256", "receipt_sha256", "receipt_storage_ref",
        "status", "occurred_at", "received_at", "matched_at",
        "processed_at", "dead_letter_reason_code",
        "dead_letter_reason_detail",
        "replay_count", "last_seen_at", "version", "metadata",
        "created_at", "updated_at",
    },
    "rtm_connect_webhook_events": {
        "id", "webhook_inbox_id", "action_id", "attempt_id",
        "sequence_number", "event_type", "actor_type", "operator_id",
        "from_status", "to_status", "reason_code", "reason_detail",
        "payload", "created_at",
    },
    "rtm_connect_reconciliations": {
        "id", "action_id", "attempt_id", "webhook_inbox_id",
        "reconciliation_number", "status", "resolution", "request_sha256",
        "external_reference", "evidence_id", "started_at", "resolved_at",
        "resolved_by_operator_id", "resolution_code", "resolution_detail",
        "version", "metadata", "created_at", "updated_at",
    },
    "rtm_connect_reconciliation_events": {
        "id", "reconciliation_id", "action_id", "attempt_id",
        "webhook_inbox_id", "sequence_number", "event_type", "actor_type",
        "operator_id", "from_status", "to_status", "resolution",
        "reason_code", "reason_detail", "evidence_id", "payload",
        "created_at",
    },
}

CONNECT_C4_REQUIRED_INDEXES = {
    "uq_rtm_connect_webhook_deduplication",
    "uq_rtm_connect_webhook_source_event",
    "idx_rtm_connect_webhook_queue",
    "idx_rtm_connect_webhook_action",
    "idx_rtm_connect_webhook_external_reference",
    "idx_rtm_connect_webhook_dead_letter",
    "uq_rtm_connect_webhook_event_sequence",
    "idx_rtm_connect_webhook_event_action",
    "idx_rtm_connect_webhook_event_operator",
    "uq_rtm_connect_reconciliation_webhook",
    "uq_rtm_connect_reconciliation_action_number",
    "uq_rtm_connect_reconciliation_active_action",
    "idx_rtm_connect_reconciliation_action",
    "idx_rtm_connect_reconciliation_external_reference",
    "uq_rtm_connect_reconciliation_event_sequence",
    "idx_rtm_connect_reconciliation_event_action",
    "idx_rtm_connect_reconciliation_event_webhook",
    "idx_rtm_connect_reconciliation_event_operator",
}

CONNECT_C4_REQUIRED_TRIGGERS = {
    "trg_rtm_connect_webhook_state_guard",
    "trg_rtm_connect_webhook_identity_frozen",
    "trg_rtm_connect_webhook_match_scope_guard",
    "trg_rtm_connect_webhook_event_scope_guard",
    "trg_rtm_connect_webhook_events_append_only",
    "trg_rtm_connect_reconciliation_state_guard",
    "trg_rtm_connect_reconciliation_identity_frozen",
    "trg_rtm_connect_reconciliation_event_scope_guard",
    "trg_rtm_connect_reconciliation_events_append_only",
}

CONNECT_C4_REQUIRED_CONSTRAINTS = {
    "ck_rtm_connect_webhook_source_event",
    "ck_rtm_connect_webhook_event_type",
    "ck_rtm_connect_webhook_deduplication",
    "ck_rtm_connect_webhook_origin_code",
    "ck_rtm_connect_webhook_origin_version",
    "ck_rtm_connect_webhook_reported_outcome",
    "ck_rtm_connect_webhook_external_reference",
    "ck_rtm_connect_webhook_request_sha256",
    "ck_rtm_connect_webhook_payload",
    "ck_rtm_connect_webhook_payload_sha256",
    "ck_rtm_connect_webhook_verification_method",
    "ck_rtm_connect_webhook_verification_sha256",
    "ck_rtm_connect_webhook_receipt_sha256",
    "ck_rtm_connect_webhook_receipt_storage_ref",
    "ck_rtm_connect_webhook_status",
    "ck_rtm_connect_webhook_occurred",
    "ck_rtm_connect_webhook_replay_count",
    "ck_rtm_connect_webhook_version",
    "ck_rtm_connect_webhook_verified",
    "ck_rtm_connect_webhook_matched",
    "ck_rtm_connect_webhook_processed",
    "ck_rtm_connect_webhook_dead_lettered",
    "ck_rtm_connect_webhook_confirmed_receipt",
    "ck_rtm_connect_webhook_metadata",
    "ck_rtm_connect_webhook_event_sequence",
    "ck_rtm_connect_webhook_event_type",
    "ck_rtm_connect_webhook_event_actor",
    "ck_rtm_connect_webhook_event_status",
    "ck_rtm_connect_webhook_event_payload",
    "ck_rtm_connect_reconciliation_number",
    "ck_rtm_connect_reconciliation_status",
    "ck_rtm_connect_reconciliation_resolution",
    "ck_rtm_connect_reconciliation_request_sha256",
    "ck_rtm_connect_reconciliation_external_reference",
    "ck_rtm_connect_reconciliation_version",
    "ck_rtm_connect_reconciliation_started",
    "ck_rtm_connect_reconciliation_resolved",
    "ck_rtm_connect_reconciliation_confirmed_evidence",
    "ck_rtm_connect_reconciliation_metadata",
    "ck_rtm_connect_reconciliation_event_sequence",
    "ck_rtm_connect_reconciliation_event_type",
    "ck_rtm_connect_reconciliation_event_actor",
    "ck_rtm_connect_reconciliation_event_status",
    "ck_rtm_connect_reconciliation_event_resolution",
    "ck_rtm_connect_reconciliation_event_payload",
}


def connect_c4_webhook_ddl() -> list[tuple[str, str]]:
    """Devuelve DDL PostgreSQL idempotente, aditivo y no destructivo."""

    return [
        ("extensions", "CREATE EXTENSION IF NOT EXISTS pgcrypto;"),
        (
            "webhook_inbox",
            """
            CREATE TABLE IF NOT EXISTS rtm_connect_webhook_inbox (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                ingress_connector_id UUID NOT NULL
                    REFERENCES rtm_connect_connectors(id) ON DELETE RESTRICT,
                source_event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                deduplication_key TEXT NOT NULL,
                origin_connector_code TEXT NOT NULL,
                origin_connector_version TEXT NOT NULL,
                reported_outcome TEXT NOT NULL,
                claimed_action_id UUID NOT NULL,
                claimed_attempt_id UUID NOT NULL,
                matched_action_id UUID
                    REFERENCES rtm_connect_actions(id) ON DELETE RESTRICT,
                matched_attempt_id UUID
                    REFERENCES rtm_connect_attempts(id) ON DELETE RESTRICT,
                external_reference TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                payload JSONB NOT NULL,
                payload_sha256 TEXT NOT NULL,
                verification_method TEXT,
                verification_sha256 TEXT,
                receipt_sha256 TEXT,
                receipt_storage_ref TEXT,
                status TEXT NOT NULL DEFAULT 'received',
                occurred_at TIMESTAMPTZ NOT NULL,
                received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                matched_at TIMESTAMPTZ,
                processed_at TIMESTAMPTZ,
                dead_letter_reason_code TEXT,
                dead_letter_reason_detail TEXT,
                replay_count INTEGER NOT NULL DEFAULT 0,
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                version INTEGER NOT NULL DEFAULT 1,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_connect_webhook_source_event CHECK (
                    source_event_id
                        ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{2,191}$'
                ),
                CONSTRAINT ck_rtm_connect_webhook_event_type CHECK (
                    event_type ~ '^[a-z][a-z0-9_.-]{2,95}$'
                ),
                CONSTRAINT ck_rtm_connect_webhook_deduplication CHECK (
                    deduplication_key ~ '^rtmwh1:[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_connect_webhook_origin_code CHECK (
                    origin_connector_code
                        ~ '^[a-z][a-z0-9_.-]{2,95}$'
                ),
                CONSTRAINT ck_rtm_connect_webhook_origin_version CHECK (
                    origin_connector_version
                        ~ '^[a-z0-9][a-z0-9_.-]{1,63}$'
                ),
                CONSTRAINT ck_rtm_connect_webhook_reported_outcome CHECK (
                    reported_outcome IN (
                        'confirmed', 'retryable_failed', 'unknown',
                        'manual_review', 'permanent_failed'
                    )
                ),
                CONSTRAINT ck_rtm_connect_webhook_external_reference CHECK (
                    length(btrim(external_reference)) BETWEEN 1 AND 512
                ),
                CONSTRAINT ck_rtm_connect_webhook_request_sha256 CHECK (
                    request_sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_connect_webhook_payload CHECK (
                    jsonb_typeof(payload) = 'object'
                ),
                CONSTRAINT ck_rtm_connect_webhook_payload_sha256 CHECK (
                    payload_sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_connect_webhook_verification_method CHECK (
                    verification_method IS NULL OR verification_method
                        ~ '^[a-z][a-z0-9_.-]{2,95}$'
                ),
                CONSTRAINT ck_rtm_connect_webhook_verification_sha256 CHECK (
                    verification_sha256 IS NULL
                    OR verification_sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_connect_webhook_receipt_sha256 CHECK (
                    receipt_sha256 IS NULL
                    OR receipt_sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_connect_webhook_receipt_storage_ref CHECK (
                    receipt_storage_ref IS NULL
                    OR (
                        receipt_storage_ref ~ '^synthetic://webhook/'
                        AND length(receipt_storage_ref) <= 1024
                    )
                ),
                CONSTRAINT ck_rtm_connect_webhook_status CHECK (
                    status IN (
                        'received', 'verified', 'matched',
                        'processed', 'dead_lettered'
                    )
                ),
                CONSTRAINT ck_rtm_connect_webhook_occurred CHECK (
                    occurred_at <= received_at
                    AND last_seen_at >= received_at
                ),
                CONSTRAINT ck_rtm_connect_webhook_replay_count CHECK (
                    replay_count >= 0
                ),
                CONSTRAINT ck_rtm_connect_webhook_version CHECK (
                    version > 0
                ),
                CONSTRAINT ck_rtm_connect_webhook_verified CHECK (
                    status NOT IN ('verified', 'matched', 'processed')
                    OR (
                        verification_method IS NOT NULL
                        AND verification_sha256 IS NOT NULL
                    )
                ),
                CONSTRAINT ck_rtm_connect_webhook_matched CHECK (
                    status NOT IN ('matched', 'processed')
                    OR (
                        matched_action_id IS NOT NULL
                        AND matched_attempt_id IS NOT NULL
                        AND matched_at IS NOT NULL
                    )
                ),
                CONSTRAINT ck_rtm_connect_webhook_processed CHECK (
                    status <> 'processed' OR processed_at IS NOT NULL
                ),
                CONSTRAINT ck_rtm_connect_webhook_dead_lettered CHECK (
                    status <> 'dead_lettered' OR (
                        processed_at IS NOT NULL
                        AND dead_letter_reason_code IS NOT NULL
                        AND dead_letter_reason_code
                            ~ '^[a-z][a-z0-9_.-]{2,95}$'
                    )
                ),
                CONSTRAINT ck_rtm_connect_webhook_confirmed_receipt CHECK (
                    status <> 'processed'
                    OR reported_outcome <> 'confirmed'
                    OR (
                        receipt_sha256 IS NOT NULL
                        AND receipt_storage_ref IS NOT NULL
                    )
                ),
                CONSTRAINT ck_rtm_connect_webhook_metadata CHECK (
                    jsonb_typeof(metadata) = 'object'
                )
            );
            """,
        ),
        (
            "webhook_events",
            """
            CREATE TABLE IF NOT EXISTS rtm_connect_webhook_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                webhook_inbox_id UUID NOT NULL
                    REFERENCES rtm_connect_webhook_inbox(id)
                    ON DELETE RESTRICT,
                action_id UUID
                    REFERENCES rtm_connect_actions(id) ON DELETE RESTRICT,
                attempt_id UUID
                    REFERENCES rtm_connect_attempts(id) ON DELETE RESTRICT,
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
                CONSTRAINT ck_rtm_connect_webhook_event_sequence CHECK (
                    sequence_number > 0
                ),
                CONSTRAINT ck_rtm_connect_webhook_event_type CHECK (
                    event_type ~ '^[a-z][a-z0-9_.-]{2,95}$'
                ),
                CONSTRAINT ck_rtm_connect_webhook_event_actor CHECK (
                    actor_type IN (
                        'webhook', 'connect', 'reconciliation',
                        'operator', 'system'
                    )
                ),
                CONSTRAINT ck_rtm_connect_webhook_event_status CHECK (
                    (from_status IS NULL OR from_status IN (
                        'received', 'verified', 'matched',
                        'processed', 'dead_lettered'
                    ))
                    AND (to_status IS NULL OR to_status IN (
                        'received', 'verified', 'matched',
                        'processed', 'dead_lettered'
                    ))
                ),
                CONSTRAINT ck_rtm_connect_webhook_event_payload CHECK (
                    jsonb_typeof(payload) = 'object'
                )
            );
            """,
        ),
        (
            "reconciliations",
            """
            CREATE TABLE IF NOT EXISTS rtm_connect_reconciliations (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                action_id UUID NOT NULL
                    REFERENCES rtm_connect_actions(id) ON DELETE RESTRICT,
                attempt_id UUID NOT NULL
                    REFERENCES rtm_connect_attempts(id) ON DELETE RESTRICT,
                webhook_inbox_id UUID NOT NULL
                    REFERENCES rtm_connect_webhook_inbox(id)
                    ON DELETE RESTRICT,
                reconciliation_number INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'started',
                resolution TEXT,
                request_sha256 TEXT NOT NULL,
                external_reference TEXT NOT NULL,
                evidence_id UUID
                    REFERENCES rtm_connect_evidence(id) ON DELETE RESTRICT,
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                resolved_at TIMESTAMPTZ,
                resolved_by_operator_id UUID
                    REFERENCES rtm_operators(id) ON DELETE SET NULL,
                resolution_code TEXT,
                resolution_detail TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_connect_reconciliation_number CHECK (
                    reconciliation_number > 0
                ),
                CONSTRAINT ck_rtm_connect_reconciliation_status CHECK (
                    status IN ('started', 'resolved')
                ),
                CONSTRAINT ck_rtm_connect_reconciliation_resolution CHECK (
                    resolution IS NULL OR resolution IN (
                        'confirmed', 'retryable_failed', 'unknown',
                        'manual_review', 'permanent_failed'
                    )
                ),
                CONSTRAINT ck_rtm_connect_reconciliation_request_sha256 CHECK (
                    request_sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_connect_reconciliation_external_reference CHECK (
                    length(btrim(external_reference)) BETWEEN 1 AND 512
                ),
                CONSTRAINT ck_rtm_connect_reconciliation_version CHECK (
                    version > 0
                ),
                CONSTRAINT ck_rtm_connect_reconciliation_started CHECK (
                    resolved_at IS NULL OR started_at <= resolved_at
                ),
                CONSTRAINT ck_rtm_connect_reconciliation_resolved CHECK (
                    (status = 'started' AND resolution IS NULL
                        AND resolved_at IS NULL)
                    OR (status = 'resolved' AND resolution IS NOT NULL
                        AND resolved_at IS NOT NULL
                        AND resolution_code IS NOT NULL)
                ),
                CONSTRAINT ck_rtm_connect_reconciliation_confirmed_evidence CHECK (
                    resolution <> 'confirmed' OR evidence_id IS NOT NULL
                ),
                CONSTRAINT ck_rtm_connect_reconciliation_metadata CHECK (
                    jsonb_typeof(metadata) = 'object'
                )
            );
            """,
        ),
        (
            "reconciliation_events",
            """
            CREATE TABLE IF NOT EXISTS rtm_connect_reconciliation_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                reconciliation_id UUID NOT NULL
                    REFERENCES rtm_connect_reconciliations(id)
                    ON DELETE RESTRICT,
                action_id UUID NOT NULL
                    REFERENCES rtm_connect_actions(id) ON DELETE RESTRICT,
                attempt_id UUID NOT NULL
                    REFERENCES rtm_connect_attempts(id) ON DELETE RESTRICT,
                webhook_inbox_id UUID NOT NULL
                    REFERENCES rtm_connect_webhook_inbox(id)
                    ON DELETE RESTRICT,
                sequence_number INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                operator_id UUID
                    REFERENCES rtm_operators(id) ON DELETE SET NULL,
                from_status TEXT,
                to_status TEXT,
                resolution TEXT,
                reason_code TEXT NOT NULL,
                reason_detail TEXT,
                evidence_id UUID
                    REFERENCES rtm_connect_evidence(id) ON DELETE RESTRICT,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_connect_reconciliation_event_sequence CHECK (
                    sequence_number > 0
                ),
                CONSTRAINT ck_rtm_connect_reconciliation_event_type CHECK (
                    event_type ~ '^[a-z][a-z0-9_.-]{2,95}$'
                ),
                CONSTRAINT ck_rtm_connect_reconciliation_event_actor CHECK (
                    actor_type IN (
                        'webhook', 'connect', 'reconciliation',
                        'operator', 'system'
                    )
                ),
                CONSTRAINT ck_rtm_connect_reconciliation_event_status CHECK (
                    (from_status IS NULL
                        OR from_status IN ('started', 'resolved'))
                    AND (to_status IS NULL
                        OR to_status IN ('started', 'resolved'))
                ),
                CONSTRAINT ck_rtm_connect_reconciliation_event_resolution CHECK (
                    resolution IS NULL OR resolution IN (
                        'confirmed', 'retryable_failed', 'unknown',
                        'manual_review', 'permanent_failed'
                    )
                ),
                CONSTRAINT ck_rtm_connect_reconciliation_event_payload CHECK (
                    jsonb_typeof(payload) = 'object'
                )
            );
            """,
        ),
        ("uq_webhook_deduplication", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_webhook_deduplication
            ON rtm_connect_webhook_inbox(deduplication_key);
        """),
        ("uq_webhook_source_event", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_webhook_source_event
            ON rtm_connect_webhook_inbox(
                ingress_connector_id, source_event_id
            );
        """),
        ("idx_webhook_queue", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_webhook_queue
            ON rtm_connect_webhook_inbox(
                status, received_at, created_at
            );
        """),
        ("idx_webhook_action", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_webhook_action
            ON rtm_connect_webhook_inbox(
                matched_action_id, status, received_at DESC
            );
        """),
        ("idx_webhook_external_reference", """
            CREATE INDEX IF NOT EXISTS
                idx_rtm_connect_webhook_external_reference
            ON rtm_connect_webhook_inbox(
                origin_connector_code, external_reference,
                request_sha256, received_at DESC
            );
        """),
        ("idx_webhook_dead_letter", """
            CREATE INDEX IF NOT EXISTS
                idx_rtm_connect_webhook_dead_letter
            ON rtm_connect_webhook_inbox(
                dead_letter_reason_code, processed_at, received_at
            )
            WHERE status = 'dead_lettered';
        """),
        ("uq_webhook_event_sequence", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_webhook_event_sequence
            ON rtm_connect_webhook_events(
                webhook_inbox_id, sequence_number
            );
        """),
        ("idx_webhook_event_action", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_webhook_event_action
            ON rtm_connect_webhook_events(
                action_id, created_at, sequence_number
            );
        """),
        ("idx_webhook_event_operator", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_webhook_event_operator
            ON rtm_connect_webhook_events(operator_id, created_at DESC);
        """),
        ("uq_reconciliation_webhook", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_reconciliation_webhook
            ON rtm_connect_reconciliations(webhook_inbox_id);
        """),
        ("uq_reconciliation_action_number", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_reconciliation_action_number
            ON rtm_connect_reconciliations(
                action_id, reconciliation_number
            );
        """),
        ("uq_reconciliation_active_action", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_reconciliation_active_action
            ON rtm_connect_reconciliations(action_id)
            WHERE status = 'started';
        """),
        ("idx_reconciliation_action", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_reconciliation_action
            ON rtm_connect_reconciliations(
                action_id, status, created_at DESC
            );
        """),
        ("idx_reconciliation_external_reference", """
            CREATE INDEX IF NOT EXISTS
                idx_rtm_connect_reconciliation_external_reference
            ON rtm_connect_reconciliations(
                external_reference, request_sha256, created_at DESC
            );
        """),
        ("uq_reconciliation_event_sequence", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_reconciliation_event_sequence
            ON rtm_connect_reconciliation_events(
                reconciliation_id, sequence_number
            );
        """),
        ("idx_reconciliation_event_action", """
            CREATE INDEX IF NOT EXISTS
                idx_rtm_connect_reconciliation_event_action
            ON rtm_connect_reconciliation_events(
                action_id, created_at, sequence_number
            );
        """),
        ("idx_reconciliation_event_webhook", """
            CREATE INDEX IF NOT EXISTS
                idx_rtm_connect_reconciliation_event_webhook
            ON rtm_connect_reconciliation_events(
                webhook_inbox_id, created_at, sequence_number
            );
        """),
        ("idx_reconciliation_event_operator", """
            CREATE INDEX IF NOT EXISTS
                idx_rtm_connect_reconciliation_event_operator
            ON rtm_connect_reconciliation_events(
                operator_id, created_at DESC
            );
        """),
        (
            "webhook_state_guard_function",
            """
            CREATE OR REPLACE FUNCTION rtm_connect_webhook_state_guard()
            RETURNS trigger AS $$
            DECLARE
                transition_ok BOOLEAN := FALSE;
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NEW.status <> 'received' OR NEW.version <> 1
                        OR NEW.replay_count <> 0 THEN
                        RAISE EXCEPTION
                            'webhook must start received at version 1';
                    END IF;
                    RETURN NEW;
                END IF;

                IF NEW.version <> OLD.version + 1 THEN
                    RAISE EXCEPTION
                        'webhook version must increment exactly once';
                END IF;

                IF NEW.status = OLD.status THEN
                    IF NEW.replay_count <> OLD.replay_count + 1
                        OR NEW.last_seen_at < OLD.last_seen_at
                        OR NEW.matched_action_id
                            IS DISTINCT FROM OLD.matched_action_id
                        OR NEW.matched_attempt_id
                            IS DISTINCT FROM OLD.matched_attempt_id
                        OR NEW.matched_at IS DISTINCT FROM OLD.matched_at
                        OR NEW.processed_at IS DISTINCT FROM OLD.processed_at
                        OR NEW.dead_letter_reason_code
                            IS DISTINCT FROM OLD.dead_letter_reason_code
                        OR NEW.dead_letter_reason_detail
                            IS DISTINCT FROM OLD.dead_letter_reason_detail
                        OR NEW.verification_method
                            IS DISTINCT FROM OLD.verification_method
                        OR NEW.verification_sha256
                            IS DISTINCT FROM OLD.verification_sha256
                        OR NEW.receipt_sha256
                            IS DISTINCT FROM OLD.receipt_sha256
                        OR NEW.receipt_storage_ref
                            IS DISTINCT FROM OLD.receipt_storage_ref
                        OR NEW.metadata IS DISTINCT FROM OLD.metadata THEN
                        RAISE EXCEPTION
                            'same-state webhook update must be exact replay';
                    END IF;
                    RETURN NEW;
                END IF;

                IF NEW.replay_count <> OLD.replay_count
                    OR NEW.last_seen_at IS DISTINCT FROM OLD.last_seen_at THEN
                    RAISE EXCEPTION
                        'webhook replay counters change only on replay';
                END IF;

                transition_ok := CASE
                    WHEN OLD.status = 'received'
                        AND NEW.status IN (
                            'verified', 'dead_lettered'
                        ) THEN TRUE
                    WHEN OLD.status = 'verified'
                        AND NEW.status IN (
                            'matched', 'dead_lettered'
                        ) THEN TRUE
                    WHEN OLD.status = 'matched'
                        AND NEW.status IN (
                            'processed', 'dead_lettered'
                        ) THEN TRUE
                    ELSE FALSE
                END;

                IF NOT transition_ok THEN
                    RAISE EXCEPTION
                        'invalid webhook transition: % -> %',
                        OLD.status, NEW.status;
                END IF;

                IF OLD.matched_action_id IS NOT NULL AND (
                    NEW.matched_action_id
                        IS DISTINCT FROM OLD.matched_action_id
                    OR NEW.matched_attempt_id
                        IS DISTINCT FROM OLD.matched_attempt_id
                    OR NEW.matched_at IS DISTINCT FROM OLD.matched_at
                ) THEN
                    RAISE EXCEPTION
                        'webhook resolved correlation is frozen after match';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "webhook_state_guard_trigger",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname = 'trg_rtm_connect_webhook_state_guard'
                      AND tgrelid =
                          'rtm_connect_webhook_inbox'::regclass
                ) THEN
                    EXECUTE '
                        CREATE TRIGGER trg_rtm_connect_webhook_state_guard
                        BEFORE INSERT OR UPDATE
                        ON rtm_connect_webhook_inbox
                        FOR EACH ROW
                        EXECUTE FUNCTION rtm_connect_webhook_state_guard()
                    ';
                END IF;
            END $$;
            """,
        ),
        (
            "webhook_identity_frozen_function",
            """
            CREATE OR REPLACE FUNCTION rtm_connect_webhook_identity_frozen()
            RETURNS trigger AS $$
            BEGIN
                IF (
                    NEW.ingress_connector_id
                        IS DISTINCT FROM OLD.ingress_connector_id
                    OR NEW.source_event_id
                        IS DISTINCT FROM OLD.source_event_id
                    OR NEW.event_type IS DISTINCT FROM OLD.event_type
                    OR NEW.deduplication_key
                        IS DISTINCT FROM OLD.deduplication_key
                    OR NEW.origin_connector_code
                        IS DISTINCT FROM OLD.origin_connector_code
                    OR NEW.origin_connector_version
                        IS DISTINCT FROM OLD.origin_connector_version
                    OR NEW.reported_outcome
                        IS DISTINCT FROM OLD.reported_outcome
                    OR NEW.claimed_action_id
                        IS DISTINCT FROM OLD.claimed_action_id
                    OR NEW.claimed_attempt_id
                        IS DISTINCT FROM OLD.claimed_attempt_id
                    OR NEW.external_reference
                        IS DISTINCT FROM OLD.external_reference
                    OR NEW.request_sha256
                        IS DISTINCT FROM OLD.request_sha256
                    OR NEW.payload IS DISTINCT FROM OLD.payload
                    OR NEW.payload_sha256
                        IS DISTINCT FROM OLD.payload_sha256
                    OR NEW.occurred_at IS DISTINCT FROM OLD.occurred_at
                    OR NEW.received_at IS DISTINCT FROM OLD.received_at
                    OR NEW.created_at IS DISTINCT FROM OLD.created_at
                ) THEN
                    RAISE EXCEPTION
                        'webhook identity and payload are frozen';
                END IF;
                IF (
                    (OLD.verification_method IS NOT NULL AND
                        NEW.verification_method
                            IS DISTINCT FROM OLD.verification_method)
                    OR (OLD.verification_sha256 IS NOT NULL AND
                        NEW.verification_sha256
                            IS DISTINCT FROM OLD.verification_sha256)
                    OR (OLD.receipt_sha256 IS NOT NULL AND
                        NEW.receipt_sha256
                            IS DISTINCT FROM OLD.receipt_sha256)
                    OR (OLD.receipt_storage_ref IS NOT NULL AND
                        NEW.receipt_storage_ref
                            IS DISTINCT FROM OLD.receipt_storage_ref)
                ) THEN
                    RAISE EXCEPTION
                        'webhook verification and receipt are write-once';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "webhook_identity_frozen_trigger",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_webhook_identity_frozen'
                      AND tgrelid =
                          'rtm_connect_webhook_inbox'::regclass
                ) THEN
                    EXECUTE '
                        CREATE TRIGGER
                            trg_rtm_connect_webhook_identity_frozen
                        BEFORE UPDATE
                        ON rtm_connect_webhook_inbox
                        FOR EACH ROW
                        EXECUTE FUNCTION
                            rtm_connect_webhook_identity_frozen()
                    ';
                END IF;
            END $$;
            """,
        ),
        (
            "webhook_match_scope_guard_function",
            """
            CREATE OR REPLACE FUNCTION
                rtm_connect_webhook_match_scope_guard()
            RETURNS trigger AS $$
            DECLARE
                scope_ok BOOLEAN := FALSE;
            BEGIN
                IF NEW.status <> 'matched' OR OLD.status = 'matched' THEN
                    RETURN NEW;
                END IF;

                IF NEW.matched_action_id IS DISTINCT FROM
                        NEW.claimed_action_id
                    OR NEW.matched_attempt_id IS DISTINCT FROM
                        NEW.claimed_attempt_id THEN
                    RAISE EXCEPTION
                        'webhook matched scope differs from claimed scope';
                END IF;

                SELECT EXISTS (
                    SELECT 1
                    FROM rtm_connect_attempts x
                    JOIN rtm_connect_actions a
                        ON a.id = x.action_id
                    JOIN rtm_connect_connectors c
                        ON c.id = x.connector_id
                    WHERE x.id = NEW.matched_attempt_id
                      AND x.action_id = NEW.matched_action_id
                      AND x.status = 'unknown'
                      AND x.reconciliation_required = TRUE
                      AND x.request_sha256 = NEW.request_sha256
                      AND x.external_reference = NEW.external_reference
                      AND c.code = NEW.origin_connector_code
                      AND c.version = NEW.origin_connector_version
                      AND c.status = 'active'
                      AND c.environment = 'staging'
                      AND c.synthetic_only = TRUE
                      AND c.credential_ref IS NULL
                      AND c.supports_reconciliation = TRUE
                      AND c.id IS DISTINCT FROM NEW.ingress_connector_id
                      AND a.status = 'unknown'
                ) INTO scope_ok;

                IF NOT scope_ok THEN
                    RAISE EXCEPTION
                        'webhook matched scope does not correlate exactly';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "webhook_match_scope_guard_trigger",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_webhook_match_scope_guard'
                      AND tgrelid =
                          'rtm_connect_webhook_inbox'::regclass
                ) THEN
                    EXECUTE '
                        CREATE TRIGGER
                            trg_rtm_connect_webhook_match_scope_guard
                        BEFORE UPDATE
                        ON rtm_connect_webhook_inbox
                        FOR EACH ROW
                        EXECUTE FUNCTION
                            rtm_connect_webhook_match_scope_guard()
                    ';
                END IF;
            END $$;
            """,
        ),
        (
            "webhook_event_scope_guard_function",
            """
            CREATE OR REPLACE FUNCTION
                rtm_connect_webhook_event_scope_guard()
            RETURNS trigger AS $$
            DECLARE
                inbox_status TEXT;
                inbox_action_id UUID;
                inbox_attempt_id UUID;
                expected_sequence INTEGER;
            BEGIN
                SELECT status, matched_action_id, matched_attempt_id,
                       version - replay_count
                INTO inbox_status, inbox_action_id, inbox_attempt_id,
                     expected_sequence
                FROM rtm_connect_webhook_inbox
                WHERE id = NEW.webhook_inbox_id;
                IF NOT FOUND
                    OR NEW.to_status IS DISTINCT FROM inbox_status
                    OR NEW.sequence_number <> expected_sequence
                    OR NEW.event_type <>
                        ('webhook.' || inbox_status) THEN
                    RAISE EXCEPTION
                        'webhook event does not match inbox state';
                END IF;
                IF (NEW.action_id IS NULL) <>
                        (NEW.attempt_id IS NULL) THEN
                    RAISE EXCEPTION
                        'webhook event scope must be complete or empty';
                END IF;
                IF NEW.action_id IS NULL OR NEW.attempt_id IS NULL THEN
                    IF inbox_action_id IS NOT NULL
                        OR inbox_attempt_id IS NOT NULL THEN
                        RAISE EXCEPTION
                            'webhook event omits resolved scope';
                    END IF;
                ELSIF NEW.action_id IS DISTINCT FROM inbox_action_id
                    OR NEW.attempt_id IS DISTINCT FROM inbox_attempt_id THEN
                    RAISE EXCEPTION
                        'webhook event scope differs from inbox';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "webhook_event_scope_guard_trigger",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_webhook_event_scope_guard'
                      AND tgrelid =
                          'rtm_connect_webhook_events'::regclass
                ) THEN
                    EXECUTE '
                        CREATE TRIGGER
                            trg_rtm_connect_webhook_event_scope_guard
                        BEFORE INSERT
                        ON rtm_connect_webhook_events
                        FOR EACH ROW
                        EXECUTE FUNCTION
                            rtm_connect_webhook_event_scope_guard()
                    ';
                END IF;
            END $$;
            """,
        ),
        (
            "webhook_events_append_only_function",
            """
            CREATE OR REPLACE FUNCTION
                rtm_connect_webhook_events_append_only()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION
                    'rtm_connect_webhook_events is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "webhook_events_append_only_trigger",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_webhook_events_append_only'
                      AND tgrelid =
                          'rtm_connect_webhook_events'::regclass
                ) THEN
                    EXECUTE '
                        CREATE TRIGGER
                            trg_rtm_connect_webhook_events_append_only
                        BEFORE UPDATE OR DELETE
                        ON rtm_connect_webhook_events
                        FOR EACH ROW
                        EXECUTE FUNCTION
                            rtm_connect_webhook_events_append_only()
                    ';
                END IF;
            END $$;
            """,
        ),
        (
            "reconciliation_state_guard_function",
            """
            CREATE OR REPLACE FUNCTION
                rtm_connect_reconciliation_state_guard()
            RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NEW.status <> 'started' OR NEW.version <> 1
                        OR NEW.resolution IS NOT NULL
                        OR NEW.resolved_at IS NOT NULL THEN
                        RAISE EXCEPTION
                            'reconciliation must start at version 1';
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1
                        FROM rtm_connect_webhook_inbox w
                        JOIN rtm_connect_attempts x
                            ON x.id = NEW.attempt_id
                        JOIN rtm_connect_actions a
                            ON a.id = NEW.action_id
                        JOIN rtm_connect_connectors c
                            ON c.id = x.connector_id
                        WHERE w.id = NEW.webhook_inbox_id
                          AND w.status = 'matched'
                          AND w.matched_action_id = NEW.action_id
                          AND w.matched_attempt_id = NEW.attempt_id
                          AND x.action_id = NEW.action_id
                          AND x.status = 'unknown'
                          AND x.reconciliation_required = TRUE
                          AND c.status = 'active'
                          AND c.environment = 'staging'
                          AND c.synthetic_only = TRUE
                          AND c.credential_ref IS NULL
                          AND c.supports_reconciliation = TRUE
                          AND NEW.request_sha256 = x.request_sha256
                          AND NEW.external_reference = x.external_reference
                          AND a.status = 'unknown'
                    ) THEN
                        RAISE EXCEPTION
                            'reconciliation scope is not an exact match';
                    END IF;
                    RETURN NEW;
                END IF;

                IF OLD.status <> 'started' OR NEW.status <> 'resolved' THEN
                    RAISE EXCEPTION
                        'invalid reconciliation transition: % -> %',
                        OLD.status, NEW.status;
                END IF;
                IF OLD.version <> 1 OR NEW.version <> 2 THEN
                    RAISE EXCEPTION
                        'reconciliation resolves exactly at version 2';
                END IF;
                IF NEW.resolution = 'confirmed' AND NOT EXISTS (
                    SELECT 1
                    FROM rtm_connect_evidence e
                    JOIN rtm_connect_webhook_inbox w
                        ON w.id = NEW.webhook_inbox_id
                    WHERE e.id = NEW.evidence_id
                      AND e.action_id = NEW.action_id
                      AND e.attempt_id = NEW.attempt_id
                      AND e.evidence_level = 'E4_receipt_verified'
                      AND e.request_sha256 = NEW.request_sha256
                      AND e.external_reference = NEW.external_reference
                      AND e.receipt_sha256 = w.receipt_sha256
                      AND e.receipt_storage_ref = w.receipt_storage_ref
                ) THEN
                    RAISE EXCEPTION
                        'confirmed reconciliation requires exact E4';
                END IF;
                IF NEW.resolution <> 'confirmed'
                    AND NEW.evidence_id IS NOT NULL THEN
                    RAISE EXCEPTION
                        'non-confirmed reconciliation cannot bind evidence';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                    FROM rtm_connect_actions a
                    JOIN rtm_connect_attempts x
                        ON x.action_id = a.id
                    JOIN rtm_connect_connectors c
                        ON c.id = x.connector_id
                    JOIN rtm_connect_webhook_inbox w
                        ON w.id = NEW.webhook_inbox_id
                    WHERE a.id = NEW.action_id
                      AND x.id = NEW.attempt_id
                      AND w.status = 'matched'
                      AND w.matched_action_id = NEW.action_id
                      AND w.matched_attempt_id = NEW.attempt_id
                      AND NEW.resolution = w.reported_outcome
                      AND c.status = 'active'
                      AND c.environment = 'staging'
                      AND c.synthetic_only = TRUE
                      AND c.credential_ref IS NULL
                      AND c.supports_reconciliation = TRUE
                      AND a.payload_sha256 = NEW.request_sha256
                      AND x.request_sha256 = NEW.request_sha256
                      AND a.external_reference = NEW.external_reference
                      AND x.external_reference = NEW.external_reference
                      AND a.status = NEW.resolution
                      AND x.status = CASE
                          WHEN NEW.resolution = 'confirmed'
                              THEN 'succeeded'
                          WHEN NEW.resolution = 'unknown'
                              THEN 'unknown'
                          ELSE 'failed'
                      END
                      AND x.retryable =
                          (NEW.resolution = 'retryable_failed')
                      AND x.reconciliation_required =
                          (NEW.resolution = 'unknown')
                ) THEN
                    RAISE EXCEPTION
                        'reconciliation resolution differs from CORE scope';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "reconciliation_state_guard_trigger",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_reconciliation_state_guard'
                      AND tgrelid =
                          'rtm_connect_reconciliations'::regclass
                ) THEN
                    EXECUTE '
                        CREATE TRIGGER
                            trg_rtm_connect_reconciliation_state_guard
                        BEFORE INSERT OR UPDATE
                        ON rtm_connect_reconciliations
                        FOR EACH ROW
                        EXECUTE FUNCTION
                            rtm_connect_reconciliation_state_guard()
                    ';
                END IF;
            END $$;
            """,
        ),
        (
            "reconciliation_identity_frozen_function",
            """
            CREATE OR REPLACE FUNCTION
                rtm_connect_reconciliation_identity_frozen()
            RETURNS trigger AS $$
            BEGIN
                IF (
                    NEW.action_id IS DISTINCT FROM OLD.action_id
                    OR NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
                    OR NEW.webhook_inbox_id
                        IS DISTINCT FROM OLD.webhook_inbox_id
                    OR NEW.reconciliation_number
                        IS DISTINCT FROM OLD.reconciliation_number
                    OR NEW.request_sha256
                        IS DISTINCT FROM OLD.request_sha256
                    OR NEW.external_reference
                        IS DISTINCT FROM OLD.external_reference
                    OR NEW.started_at IS DISTINCT FROM OLD.started_at
                    OR NEW.created_at IS DISTINCT FROM OLD.created_at
                ) THEN
                    RAISE EXCEPTION
                        'reconciliation identity is frozen';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "reconciliation_identity_frozen_trigger",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_reconciliation_identity_frozen'
                      AND tgrelid =
                          'rtm_connect_reconciliations'::regclass
                ) THEN
                    EXECUTE '
                        CREATE TRIGGER
                            trg_rtm_connect_reconciliation_identity_frozen
                        BEFORE UPDATE
                        ON rtm_connect_reconciliations
                        FOR EACH ROW
                        EXECUTE FUNCTION
                            rtm_connect_reconciliation_identity_frozen()
                    ';
                END IF;
            END $$;
            """,
        ),
        (
            "reconciliation_event_scope_guard_function",
            """
            CREATE OR REPLACE FUNCTION
                rtm_connect_reconciliation_event_scope_guard()
            RETURNS trigger AS $$
            DECLARE
                parent_status TEXT;
                parent_resolution TEXT;
                parent_action_id UUID;
                parent_attempt_id UUID;
                parent_webhook_id UUID;
                parent_evidence_id UUID;
                parent_version INTEGER;
            BEGIN
                SELECT status, resolution, action_id, attempt_id,
                       webhook_inbox_id, evidence_id, version
                INTO parent_status, parent_resolution, parent_action_id,
                     parent_attempt_id, parent_webhook_id,
                     parent_evidence_id, parent_version
                FROM rtm_connect_reconciliations
                WHERE id = NEW.reconciliation_id;
                IF NOT FOUND
                    OR NEW.action_id IS DISTINCT FROM parent_action_id
                    OR NEW.attempt_id IS DISTINCT FROM parent_attempt_id
                    OR NEW.webhook_inbox_id
                        IS DISTINCT FROM parent_webhook_id
                    OR NEW.to_status IS DISTINCT FROM parent_status
                    OR NEW.resolution IS DISTINCT FROM parent_resolution
                    OR NEW.evidence_id
                        IS DISTINCT FROM parent_evidence_id
                    OR NEW.sequence_number <> parent_version THEN
                    RAISE EXCEPTION
                        'reconciliation event differs from parent scope';
                END IF;
                IF parent_status = 'started' AND (
                    NEW.from_status IS NOT NULL
                    OR NEW.event_type <> 'reconciliation.started'
                ) THEN
                    RAISE EXCEPTION
                        'invalid reconciliation started event';
                END IF;
                IF parent_status = 'resolved' AND (
                    NEW.from_status <> 'started'
                    OR NEW.event_type <> 'reconciliation.resolved'
                ) THEN
                    RAISE EXCEPTION
                        'invalid reconciliation resolved event';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "reconciliation_event_scope_guard_trigger",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_reconciliation_event_scope_guard'
                      AND tgrelid =
                          'rtm_connect_reconciliation_events'::regclass
                ) THEN
                    EXECUTE '
                        CREATE TRIGGER
                            trg_rtm_connect_reconciliation_event_scope_guard
                        BEFORE INSERT
                        ON rtm_connect_reconciliation_events
                        FOR EACH ROW
                        EXECUTE FUNCTION
                            rtm_connect_reconciliation_event_scope_guard()
                    ';
                END IF;
            END $$;
            """,
        ),
        (
            "reconciliation_events_append_only_function",
            """
            CREATE OR REPLACE FUNCTION
                rtm_connect_reconciliation_events_append_only()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION
                    'rtm_connect_reconciliation_events is append-only';
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "reconciliation_events_append_only_trigger",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_reconciliation_events_append_only'
                      AND tgrelid =
                          'rtm_connect_reconciliation_events'::regclass
                ) THEN
                    EXECUTE '
                        CREATE TRIGGER
                            trg_rtm_connect_reconciliation_events_append_only
                        BEFORE UPDATE OR DELETE
                        ON rtm_connect_reconciliation_events
                        FOR EACH ROW
                        EXECUTE FUNCTION
                            rtm_connect_reconciliation_events_append_only()
                    ';
                END IF;
            END $$;
            """,
        ),
    ]


__all__ = [
    "RTM_CONNECT_C4_WEBHOOK_SCHEMA_VERSION",
    "WEBHOOK_INBOX_STATUSES",
    "RECONCILIATION_STATUSES",
    "RECONCILIATION_RESOLUTIONS",
    "CONNECT_C4_REQUIRED_COLUMNS",
    "CONNECT_C4_REQUIRED_CONSTRAINTS",
    "CONNECT_C4_REQUIRED_INDEXES",
    "CONNECT_C4_REQUIRED_TRIGGERS",
    "connect_c4_webhook_ddl",
]
