"""Esquema PostgreSQL aditivo del Kernel RTM CONNECT C1.

C1 instala únicamente persistencia, invariantes y auditoría. No publica rutas,
no registra conectores persistentes y no ejecuta ningún efecto externo.
"""

from __future__ import annotations


RTM_CONNECT_C1_SCHEMA_VERSION = "rtm_connect_c1_schema_v1_0"

ACTION_STATUSES = (
    "draft",
    "authorized",
    "queued",
    "executing",
    "external_accepted",
    "evidence_pending",
    "confirmed",
    "retryable_failed",
    "unknown",
    "reconciling",
    "manual_review",
    "permanent_failed",
    "cancelled",
)

RISK_CLASSES = (
    "R0_observation",
    "R1_low_reversible",
    "R2_business_effect",
    "R3_legal_or_financial",
    "R4_critical_regulated",
)

EVIDENCE_LEVELS = (
    "E0_none",
    "E1_request_recorded",
    "E2_external_reference",
    "E3_receipt_captured",
    "E4_receipt_verified",
)

CONNECTOR_MODES = (
    "api",
    "webhook",
    "polling",
    "batch",
    "assisted",
    "manual",
)

CONNECT_C1_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "rtm_connect_connectors": {
        "id", "code", "version", "mode", "status", "environment",
        "synthetic_only", "capabilities", "risk_ceiling",
        "supports_idempotency", "supports_reconciliation", "credential_ref",
        "configuration", "created_at", "updated_at",
    },
    "rtm_connect_actions": {
        "id", "case_id", "capability", "satellite", "target_type",
        "target_ref", "payload", "payload_sha256", "document_hashes",
        "risk_class", "requires_dual_control", "requested_by_operator_id",
        "requested_at", "contract_version", "correlation_id", "status",
        "status_version", "idempotency_key", "current_connector_id",
        "external_reference", "next_attempt_at", "unknown_since",
        "confirmed_at", "cancelled_at", "metadata", "created_at",
        "updated_at",
    },
    "rtm_connect_authorizations": {
        "id", "action_id", "authorization_version", "supersedes_id",
        "authority_code", "authority_version", "decision",
        "payload_sha256", "idempotency_key", "required_evidence_level",
        "authorized_connector_modes", "approved_by_operator_ids",
        "authorized_at", "expires_at", "revoked_at",
        "legal_effect_authorized", "frozen", "metadata", "created_at",
    },
    "rtm_connect_attempts": {
        "id", "action_id", "connector_id", "attempt_number", "status",
        "started_at", "finished_at", "request_sha256",
        "external_reference", "failure_class", "error_code", "retryable",
        "reconciliation_required", "request_metadata", "result_metadata",
        "created_at", "updated_at",
    },
    "rtm_connect_evidence": {
        "id", "action_id", "attempt_id", "sequence_number",
        "evidence_level", "request_sha256", "external_reference",
        "receipt_sha256", "receipt_storage_ref", "verified_at",
        "verification_method", "verified_by_operator_id", "metadata",
        "created_at",
    },
    "rtm_connect_transitions": {
        "id", "action_id", "attempt_id", "sequence_number",
        "from_status", "to_status", "actor_type", "operator_id",
        "reason_code", "reason_detail", "request_id", "metadata",
        "created_at",
    },
    "rtm_connect_idempotency_claims": {
        "idempotency_key", "action_id", "payload_sha256", "authority_scope",
        "claimed_at", "last_seen_at", "replay_count", "metadata",
    },
}

CONNECT_C1_REQUIRED_INDEXES = {
    "uq_rtm_connect_connector_version",
    "idx_rtm_connect_connector_status",
    "uq_rtm_connect_action_idempotency",
    "idx_rtm_connect_action_queue",
    "idx_rtm_connect_action_case",
    "idx_rtm_connect_action_status",
    "uq_rtm_connect_authorization_version",
    "idx_rtm_connect_authorization_action",
    "uq_rtm_connect_attempt_number",
    "idx_rtm_connect_attempt_action",
    "idx_rtm_connect_attempt_external_reference",
    "uq_rtm_connect_evidence_sequence",
    "idx_rtm_connect_evidence_action",
    "uq_rtm_connect_transition_sequence",
    "idx_rtm_connect_transition_action",
    "idx_rtm_connect_idempotency_action",
}

CONNECT_C1_REQUIRED_TRIGGERS = {
    "trg_rtm_connect_actions_state_guard",
    "trg_rtm_connect_transitions_append_only",
    "trg_rtm_connect_evidence_append_only",
    "trg_rtm_connect_authorizations_immutable",
}

CONNECT_C1_REQUIRED_CONSTRAINTS = {
    "ck_rtm_connect_connector_mode",
    "ck_rtm_connect_connector_status",
    "ck_rtm_connect_connector_risk",
    "ck_rtm_connect_action_status",
    "ck_rtm_connect_action_payload_sha256",
    "ck_rtm_connect_action_idempotency_key",
    "ck_rtm_connect_action_risk",
    "ck_rtm_connect_action_document_hashes",
    "ck_rtm_connect_authorization_version",
    "ck_rtm_connect_authorization_frozen",
    "ck_rtm_connect_authorization_evidence",
    "ck_rtm_connect_attempt_number",
    "ck_rtm_connect_attempt_status",
    "ck_rtm_connect_evidence_sequence",
    "ck_rtm_connect_evidence_level",
    "ck_rtm_connect_transition_sequence",
    "ck_rtm_connect_idempotency_replay_count",
}


def connect_c1_ddl() -> list[tuple[str, str]]:
    """Devuelve DDL PostgreSQL idempotente, aditivo y no destructivo."""

    return [
        ("extensions", "CREATE EXTENSION IF NOT EXISTS pgcrypto;"),
        (
            "connectors",
            """
            CREATE TABLE IF NOT EXISTS rtm_connect_connectors (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                code TEXT NOT NULL,
                version TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'inactive',
                environment TEXT NOT NULL DEFAULT 'staging' CHECK (
                    environment IN ('staging', 'production')
                ),
                synthetic_only BOOLEAN NOT NULL DEFAULT TRUE,
                capabilities JSONB NOT NULL DEFAULT '[]'::jsonb
                    CHECK (jsonb_typeof(capabilities) = 'array'),
                risk_ceiling TEXT NOT NULL DEFAULT 'R0_observation',
                supports_idempotency BOOLEAN NOT NULL DEFAULT TRUE,
                supports_reconciliation BOOLEAN NOT NULL DEFAULT FALSE,
                credential_ref TEXT,
                configuration JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(configuration) = 'object'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_connect_connector_code CHECK (
                    code ~ '^[a-z][a-z0-9_.-]{2,95}$'
                ),
                CONSTRAINT ck_rtm_connect_connector_version CHECK (
                    version ~ '^[a-z0-9][a-z0-9_.-]{1,63}$'
                ),
                CONSTRAINT ck_rtm_connect_connector_mode CHECK (
                    mode IN ('api', 'webhook', 'polling', 'batch', 'assisted', 'manual')
                ),
                CONSTRAINT ck_rtm_connect_connector_status CHECK (
                    status IN ('inactive', 'active', 'paused', 'disabled')
                ),
                CONSTRAINT ck_rtm_connect_connector_risk CHECK (
                    risk_ceiling IN (
                        'R0_observation', 'R1_low_reversible',
                        'R2_business_effect', 'R3_legal_or_financial',
                        'R4_critical_regulated'
                    )
                )
            );
            """,
        ),
        (
            "actions",
            """
            CREATE TABLE IF NOT EXISTS rtm_connect_actions (
                id UUID PRIMARY KEY,
                case_id UUID REFERENCES cases(id) ON DELETE SET NULL,
                capability TEXT NOT NULL,
                satellite TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_ref TEXT NOT NULL,
                payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
                payload_sha256 TEXT NOT NULL,
                document_hashes JSONB NOT NULL DEFAULT '[]'::jsonb,
                risk_class TEXT NOT NULL,
                requires_dual_control BOOLEAN NOT NULL DEFAULT FALSE,
                requested_by_operator_id UUID NOT NULL
                    REFERENCES rtm_operators(id) ON DELETE RESTRICT,
                requested_at TIMESTAMPTZ NOT NULL,
                contract_version TEXT NOT NULL,
                correlation_id TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                status_version INTEGER NOT NULL DEFAULT 1,
                idempotency_key TEXT NOT NULL,
                current_connector_id UUID REFERENCES rtm_connect_connectors(id)
                    ON DELETE SET NULL,
                external_reference TEXT,
                next_attempt_at TIMESTAMPTZ,
                unknown_since TIMESTAMPTZ,
                confirmed_at TIMESTAMPTZ,
                cancelled_at TIMESTAMPTZ,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(metadata) = 'object'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_connect_action_capability CHECK (
                    capability ~ '^[a-z][a-z0-9_.-]{2,95}$'
                ),
                CONSTRAINT ck_rtm_connect_action_satellite CHECK (
                    satellite ~ '^[a-z][a-z0-9_.-]{2,95}$'
                ),
                CONSTRAINT ck_rtm_connect_action_target_type CHECK (
                    target_type ~ '^[a-z][a-z0-9_.-]{2,95}$'
                ),
                CONSTRAINT ck_rtm_connect_action_status CHECK (
                    status IN (
                        'draft', 'authorized', 'queued', 'executing',
                        'external_accepted', 'evidence_pending', 'confirmed',
                        'retryable_failed', 'unknown', 'reconciling',
                        'manual_review', 'permanent_failed', 'cancelled'
                    )
                ),
                CONSTRAINT ck_rtm_connect_action_payload_sha256 CHECK (
                    payload_sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_connect_action_idempotency_key CHECK (
                    idempotency_key ~ '^rtmc1:[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_connect_action_risk CHECK (
                    risk_class IN (
                        'R0_observation', 'R1_low_reversible',
                        'R2_business_effect', 'R3_legal_or_financial',
                        'R4_critical_regulated'
                    )
                ),
                CONSTRAINT ck_rtm_connect_action_document_hashes CHECK (
                    jsonb_typeof(document_hashes) = 'array'
                ),
                CONSTRAINT ck_rtm_connect_action_status_version CHECK (
                    status_version > 0
                ),
                CONSTRAINT ck_rtm_connect_action_r4_dual_control CHECK (
                    risk_class <> 'R4_critical_regulated'
                    OR requires_dual_control = TRUE
                ),
                CONSTRAINT ck_rtm_connect_action_confirmed_at CHECK (
                    status <> 'confirmed' OR confirmed_at IS NOT NULL
                ),
                CONSTRAINT ck_rtm_connect_action_unknown_at CHECK (
                    status <> 'unknown' OR unknown_since IS NOT NULL
                ),
                CONSTRAINT ck_rtm_connect_action_cancelled_at CHECK (
                    status <> 'cancelled' OR cancelled_at IS NOT NULL
                )
            );
            """,
        ),
        (
            "authorizations",
            """
            CREATE TABLE IF NOT EXISTS rtm_connect_authorizations (
                id UUID PRIMARY KEY,
                action_id UUID NOT NULL REFERENCES rtm_connect_actions(id)
                    ON DELETE RESTRICT,
                authorization_version INTEGER NOT NULL,
                supersedes_id UUID REFERENCES rtm_connect_authorizations(id)
                    ON DELETE SET NULL,
                authority_code TEXT NOT NULL,
                authority_version TEXT NOT NULL,
                decision TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL
                    CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
                idempotency_key TEXT NOT NULL
                    CHECK (idempotency_key ~ '^rtmc1:[0-9a-f]{64}$'),
                required_evidence_level TEXT NOT NULL,
                authorized_connector_modes JSONB NOT NULL,
                approved_by_operator_ids JSONB NOT NULL,
                authorized_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ,
                revoked_at TIMESTAMPTZ,
                legal_effect_authorized BOOLEAN NOT NULL DEFAULT FALSE,
                frozen BOOLEAN NOT NULL DEFAULT TRUE,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(metadata) = 'object'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_connect_authorization_version CHECK (
                    authorization_version > 0
                ),
                CONSTRAINT ck_rtm_connect_authorization_authority CHECK (
                    authority_code ~ '^[a-z][a-z0-9_.-]{2,95}$'
                ),
                CONSTRAINT ck_rtm_connect_authorization_decision CHECK (
                    decision = 'approved_frozen'
                ),
                CONSTRAINT ck_rtm_connect_authorization_frozen CHECK (
                    frozen = TRUE
                ),
                CONSTRAINT ck_rtm_connect_authorization_evidence CHECK (
                    required_evidence_level IN (
                        'E0_none', 'E1_request_recorded',
                        'E2_external_reference', 'E3_receipt_captured',
                        'E4_receipt_verified'
                    )
                ),
                CONSTRAINT ck_rtm_connect_authorization_modes CHECK (
                    jsonb_typeof(authorized_connector_modes) = 'array'
                    AND jsonb_array_length(authorized_connector_modes) > 0
                ),
                CONSTRAINT ck_rtm_connect_authorization_approvers CHECK (
                    jsonb_typeof(approved_by_operator_ids) = 'array'
                    AND jsonb_array_length(approved_by_operator_ids) > 0
                ),
                CONSTRAINT ck_rtm_connect_authorization_expiry CHECK (
                    expires_at IS NULL OR expires_at > authorized_at
                )
            );
            """,
        ),
        (
            "attempts",
            """
            CREATE TABLE IF NOT EXISTS rtm_connect_attempts (
                id UUID PRIMARY KEY,
                action_id UUID NOT NULL REFERENCES rtm_connect_actions(id)
                    ON DELETE RESTRICT,
                connector_id UUID REFERENCES rtm_connect_connectors(id)
                    ON DELETE SET NULL,
                attempt_number INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'started',
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                finished_at TIMESTAMPTZ,
                request_sha256 TEXT NOT NULL
                    CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
                external_reference TEXT,
                failure_class TEXT,
                error_code TEXT,
                retryable BOOLEAN NOT NULL DEFAULT FALSE,
                reconciliation_required BOOLEAN NOT NULL DEFAULT FALSE,
                request_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(request_metadata) = 'object'),
                result_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(result_metadata) = 'object'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_connect_attempt_number CHECK (
                    attempt_number > 0
                ),
                CONSTRAINT ck_rtm_connect_attempt_status CHECK (
                    status IN (
                        'started', 'external_accepted', 'succeeded',
                        'failed', 'unknown', 'cancelled'
                    )
                ),
                CONSTRAINT ck_rtm_connect_attempt_finished CHECK (
                    status = 'started' OR finished_at IS NOT NULL
                )
            );
            """,
        ),
        (
            "evidence",
            """
            CREATE TABLE IF NOT EXISTS rtm_connect_evidence (
                id UUID PRIMARY KEY,
                action_id UUID NOT NULL REFERENCES rtm_connect_actions(id)
                    ON DELETE RESTRICT,
                attempt_id UUID REFERENCES rtm_connect_attempts(id)
                    ON DELETE SET NULL,
                sequence_number INTEGER NOT NULL,
                evidence_level TEXT NOT NULL,
                request_sha256 TEXT,
                external_reference TEXT,
                receipt_sha256 TEXT,
                receipt_storage_ref TEXT,
                verified_at TIMESTAMPTZ,
                verification_method TEXT,
                verified_by_operator_id UUID REFERENCES rtm_operators(id)
                    ON DELETE SET NULL,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(metadata) = 'object'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_connect_evidence_sequence CHECK (
                    sequence_number > 0
                ),
                CONSTRAINT ck_rtm_connect_evidence_level CHECK (
                    evidence_level IN (
                        'E0_none', 'E1_request_recorded',
                        'E2_external_reference', 'E3_receipt_captured',
                        'E4_receipt_verified'
                    )
                ),
                CONSTRAINT ck_rtm_connect_evidence_request_hash CHECK (
                    request_sha256 IS NULL
                    OR request_sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_connect_evidence_receipt_hash CHECK (
                    receipt_sha256 IS NULL
                    OR receipt_sha256 ~ '^[0-9a-f]{64}$'
                )
            );
            """,
        ),
        (
            "transitions",
            """
            CREATE TABLE IF NOT EXISTS rtm_connect_transitions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                action_id UUID NOT NULL REFERENCES rtm_connect_actions(id)
                    ON DELETE RESTRICT,
                attempt_id UUID REFERENCES rtm_connect_attempts(id)
                    ON DELETE SET NULL,
                sequence_number INTEGER NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                operator_id UUID REFERENCES rtm_operators(id)
                    ON DELETE SET NULL,
                reason_code TEXT NOT NULL,
                reason_detail TEXT,
                request_id TEXT,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(metadata) = 'object'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_connect_transition_sequence CHECK (
                    sequence_number > 0
                ),
                CONSTRAINT ck_rtm_connect_transition_actor CHECK (
                    actor_type IN (
                        'core', 'connect', 'operator', 'system',
                        'reconciliation', 'migration'
                    )
                ),
                CONSTRAINT ck_rtm_connect_transition_to_status CHECK (
                    to_status IN (
                        'draft', 'authorized', 'queued', 'executing',
                        'external_accepted', 'evidence_pending', 'confirmed',
                        'retryable_failed', 'unknown', 'reconciling',
                        'manual_review', 'permanent_failed', 'cancelled'
                    )
                )
            );
            """,
        ),
        (
            "idempotency_claims",
            """
            CREATE TABLE IF NOT EXISTS rtm_connect_idempotency_claims (
                idempotency_key TEXT PRIMARY KEY,
                action_id UUID NOT NULL UNIQUE
                    REFERENCES rtm_connect_actions(id) ON DELETE RESTRICT,
                payload_sha256 TEXT NOT NULL
                    CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
                authority_scope TEXT NOT NULL,
                claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                replay_count INTEGER NOT NULL DEFAULT 0,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(metadata) = 'object'),
                CONSTRAINT ck_rtm_connect_idempotency_key CHECK (
                    idempotency_key ~ '^rtmc1:[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_connect_idempotency_replay_count CHECK (
                    replay_count >= 0
                )
            );
            """,
        ),
        (
            "uq_connector_version",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_connect_connector_version
            ON rtm_connect_connectors(code, version);
            """,
        ),
        (
            "idx_connector_status",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_connector_status
            ON rtm_connect_connectors(status, environment, code);
            """,
        ),
        (
            "uq_action_idempotency",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_connect_action_idempotency
            ON rtm_connect_actions(idempotency_key);
            """,
        ),
        (
            "idx_action_queue",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_action_queue
            ON rtm_connect_actions(status, next_attempt_at, created_at);
            """,
        ),
        (
            "idx_action_case",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_action_case
            ON rtm_connect_actions(case_id, created_at DESC);
            """,
        ),
        (
            "idx_action_status",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_action_status
            ON rtm_connect_actions(status, risk_class, updated_at DESC);
            """,
        ),
        (
            "uq_authorization_version",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_connect_authorization_version
            ON rtm_connect_authorizations(action_id, authorization_version);
            """,
        ),
        (
            "idx_authorization_action",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_authorization_action
            ON rtm_connect_authorizations(
                action_id, authorization_version DESC
            );
            """,
        ),
        (
            "uq_attempt_number",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_connect_attempt_number
            ON rtm_connect_attempts(action_id, attempt_number);
            """,
        ),
        (
            "idx_attempt_action",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_attempt_action
            ON rtm_connect_attempts(action_id, started_at DESC);
            """,
        ),
        (
            "idx_attempt_external_reference",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_attempt_external_reference
            ON rtm_connect_attempts(external_reference)
            WHERE external_reference IS NOT NULL;
            """,
        ),
        (
            "uq_evidence_sequence",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_connect_evidence_sequence
            ON rtm_connect_evidence(action_id, sequence_number);
            """,
        ),
        (
            "idx_evidence_action",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_evidence_action
            ON rtm_connect_evidence(action_id, sequence_number DESC);
            """,
        ),
        (
            "uq_transition_sequence",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_connect_transition_sequence
            ON rtm_connect_transitions(action_id, sequence_number);
            """,
        ),
        (
            "idx_transition_action",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_transition_action
            ON rtm_connect_transitions(action_id, sequence_number ASC);
            """,
        ),
        (
            "idx_idempotency_action",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_idempotency_action
            ON rtm_connect_idempotency_claims(action_id);
            """,
        ),
        (
            "action_state_guard_function",
            """
            CREATE OR REPLACE FUNCTION rtm_guard_connect_action_transition()
            RETURNS TRIGGER AS $$
            DECLARE
                allowed BOOLEAN := FALSE;
            BEGIN
                IF NEW.status = OLD.status THEN
                    NEW.updated_at := NOW();
                    RETURN NEW;
                END IF;

                allowed := CASE OLD.status
                    WHEN 'draft' THEN NEW.status IN ('authorized', 'cancelled')
                    WHEN 'authorized' THEN NEW.status IN ('queued', 'cancelled')
                    WHEN 'queued' THEN NEW.status IN ('executing', 'cancelled')
                    WHEN 'executing' THEN NEW.status IN (
                        'external_accepted', 'confirmed', 'retryable_failed',
                        'unknown', 'manual_review', 'permanent_failed'
                    )
                    WHEN 'external_accepted' THEN NEW.status IN (
                        'evidence_pending', 'confirmed', 'unknown',
                        'reconciling', 'manual_review'
                    )
                    WHEN 'evidence_pending' THEN NEW.status IN (
                        'confirmed', 'unknown', 'reconciling', 'manual_review'
                    )
                    WHEN 'retryable_failed' THEN NEW.status IN (
                        'queued', 'reconciling', 'manual_review', 'cancelled'
                    )
                    WHEN 'unknown' THEN NEW.status IN (
                        'reconciling', 'manual_review'
                    )
                    WHEN 'reconciling' THEN NEW.status IN (
                        'confirmed', 'retryable_failed', 'unknown',
                        'manual_review', 'permanent_failed'
                    )
                    WHEN 'manual_review' THEN NEW.status IN (
                        'queued', 'reconciling', 'confirmed',
                        'permanent_failed', 'cancelled'
                    )
                    ELSE FALSE
                END;

                IF NOT allowed THEN
                    RAISE EXCEPTION
                        'Invalid RTM CONNECT transition: % -> %',
                        OLD.status, NEW.status
                        USING ERRCODE = '23514';
                END IF;

                NEW.status_version := OLD.status_version + 1;
                NEW.updated_at := NOW();
                IF NEW.status = 'unknown' AND NEW.unknown_since IS NULL THEN
                    NEW.unknown_since := NOW();
                END IF;
                IF NEW.status = 'confirmed' AND NEW.confirmed_at IS NULL THEN
                    NEW.confirmed_at := NOW();
                END IF;
                IF NEW.status = 'cancelled' AND NEW.cancelled_at IS NULL THEN
                    NEW.cancelled_at := NOW();
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "action_state_guard_trigger",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname = 'trg_rtm_connect_actions_state_guard'
                      AND tgrelid = 'rtm_connect_actions'::regclass
                ) THEN
                    CREATE TRIGGER trg_rtm_connect_actions_state_guard
                    BEFORE UPDATE OF status ON rtm_connect_actions
                    FOR EACH ROW
                    EXECUTE FUNCTION rtm_guard_connect_action_transition();
                END IF;
            END $$;
            """,
        ),
        (
            "append_only_function",
            """
            CREATE OR REPLACE FUNCTION rtm_guard_connect_append_only()
            RETURNS TRIGGER AS $$
            BEGIN
                RAISE EXCEPTION
                    '% is append-only; % is not permitted', TG_TABLE_NAME, TG_OP
                    USING ERRCODE = '55000';
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "transitions_append_only_trigger",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname = 'trg_rtm_connect_transitions_append_only'
                      AND tgrelid = 'rtm_connect_transitions'::regclass
                ) THEN
                    CREATE TRIGGER trg_rtm_connect_transitions_append_only
                    BEFORE UPDATE OR DELETE ON rtm_connect_transitions
                    FOR EACH ROW
                    EXECUTE FUNCTION rtm_guard_connect_append_only();
                END IF;
            END $$;
            """,
        ),
        (
            "evidence_append_only_trigger",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname = 'trg_rtm_connect_evidence_append_only'
                      AND tgrelid = 'rtm_connect_evidence'::regclass
                ) THEN
                    CREATE TRIGGER trg_rtm_connect_evidence_append_only
                    BEFORE UPDATE OR DELETE ON rtm_connect_evidence
                    FOR EACH ROW
                    EXECUTE FUNCTION rtm_guard_connect_append_only();
                END IF;
            END $$;
            """,
        ),
        (
            "authorizations_immutable_trigger",
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname = 'trg_rtm_connect_authorizations_immutable'
                      AND tgrelid = 'rtm_connect_authorizations'::regclass
                ) THEN
                    CREATE TRIGGER trg_rtm_connect_authorizations_immutable
                    BEFORE UPDATE OR DELETE ON rtm_connect_authorizations
                    FOR EACH ROW
                    EXECUTE FUNCTION rtm_guard_connect_append_only();
                END IF;
            END $$;
            """,
        ),
    ]


__all__ = [
    "ACTION_STATUSES",
    "CONNECTOR_MODES",
    "CONNECT_C1_REQUIRED_COLUMNS",
    "CONNECT_C1_REQUIRED_CONSTRAINTS",
    "CONNECT_C1_REQUIRED_INDEXES",
    "CONNECT_C1_REQUIRED_TRIGGERS",
    "EVIDENCE_LEVELS",
    "RISK_CLASSES",
    "RTM_CONNECT_C1_SCHEMA_VERSION",
    "connect_c1_ddl",
]
