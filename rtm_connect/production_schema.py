"""Plano PostgreSQL inerte de admision y outbox simulada de RTM CONNECT C8.

C8 no publica un dispatcher real. Este esquema conserva releases aprobables y
comandos de *dry run*, pero fuerza en base de datos que no exista red, contacto
con proveedores, activacion live ni efecto externo. Las cuatro tablas son
aditivas; sus identidades quedan congeladas y sus historiales son append-only.
"""

from __future__ import annotations


RTM_CONNECT_C8_PRODUCTION_SCHEMA_VERSION = (
    "rtm_connect_c8_production_schema_v1_0"
)
RTM_CONNECT_C8_SCHEMA_VERSION = RTM_CONNECT_C8_PRODUCTION_SCHEMA_VERSION

PRODUCTION_RELEASE_STATUSES = (
    "proposed",
    "security_approved",
    "operations_approved",
    "ready",
    "simulated_active",
    "halted",
    "rejected",
    "expired",
)

DISPATCH_OUTBOX_STATUSES = (
    "prepared",
    "claimed",
    "dry_run_confirmed",
    "unknown",
    "manual_review",
    "cancelled",
)
PRODUCTION_OUTBOX_STATUSES = DISPATCH_OUTBOX_STATUSES

CONNECT_C8_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "rtm_connect_production_releases": {
        "id", "release_code", "status", "connector_code",
        "connector_version", "source_commit_sha", "manifest_sha256",
        "policy_sha256", "schema_sha256", "build_artifact_sha256",
        "release_binding_sha256", "requested_by_operator_id",
        "security_approved_by_operator_id", "security_approval_sha256",
        "operations_approved_by_operator_id",
        "operations_approval_sha256", "requested_at",
        "security_approved_at", "operations_approved_at", "ready_at",
        "simulated_active_at", "emergency_halt", "halted_at",
        "halted_by_operator_id", "halt_reason_code", "rejected_at",
        "rejected_by_operator_id", "rejection_reason_code", "valid_until",
        "expired_at", "simulation_only", "external_effects_allowed",
        "live_activation_allowed", "human_activation_required",
        "provider_pack_present", "canary_percent", "max_concurrency",
        "daily_action_limit", "version", "metadata", "created_at",
        "updated_at",
    },
    "rtm_connect_production_release_events": {
        "id", "release_id", "release_binding_sha256", "sequence_number",
        "event_type", "actor_type", "operator_id", "from_status",
        "to_status", "reason_code", "payload", "created_at",
    },
    "rtm_connect_dispatch_outbox": {
        "id", "action_id", "authorization_id", "authorization_version",
        "release_id", "status", "business_command_id",
        "production_effect_key", "payload_sha256", "request_sha256",
        "release_manifest_sha256", "release_binding_sha256",
        "dry_run_only", "network_allowed", "provider_contacted",
        "external_effects_allowed", "claim_owner", "claim_token",
        "claim_fence", "claimed_at", "claim_expires_at",
        "dry_run_confirmed_at", "unknown_at", "manual_review_at",
        "cancelled_at", "version", "metadata", "created_at", "updated_at",
    },
    "rtm_connect_dispatch_events": {
        "id", "outbox_id", "action_id", "authorization_id", "release_id",
        "release_binding_sha256", "sequence_number", "event_type",
        "actor_type", "operator_id", "from_status", "to_status",
        "reason_code", "payload", "created_at",
    },
}

CONNECT_C8_REQUIRED_INDEXES = {
    "uq_rtm_connect_production_release_code",
    "uq_rtm_connect_production_release_binding",
    "idx_rtm_connect_production_release_status",
    "uq_rtm_connect_production_release_event_sequence",
    "idx_rtm_connect_production_release_event_status",
    "uq_rtm_connect_dispatch_business_command",
    "uq_rtm_connect_dispatch_production_effect",
    "uq_rtm_connect_dispatch_release_once",
    "uq_rtm_connect_dispatch_claim_token",
    "idx_rtm_connect_dispatch_claim_queue",
    "idx_rtm_connect_dispatch_action",
    "idx_rtm_connect_dispatch_release",
    "uq_rtm_connect_dispatch_event_sequence",
    "idx_rtm_connect_dispatch_event_action",
    "idx_rtm_connect_dispatch_event_release",
}

CONNECT_C8_REQUIRED_TRIGGERS = {
    "trg_rtm_connect_production_release_state_guard",
    "trg_rtm_connect_production_release_frozen_guard",
    "trg_rtm_connect_production_release_delete_guard",
    "trg_rtm_connect_production_release_event_scope_guard",
    "trg_rtm_connect_production_release_events_append_only",
    "trg_rtm_connect_dispatch_outbox_scope_guard",
    "trg_rtm_connect_dispatch_outbox_state_guard",
    "trg_rtm_connect_dispatch_outbox_frozen_guard",
    "trg_rtm_connect_dispatch_outbox_delete_guard",
    "trg_rtm_connect_dispatch_event_scope_guard",
    "trg_rtm_connect_dispatch_events_append_only",
    "trg_rtm_connect_production_release_truncate_guard",
    "trg_rtm_connect_production_release_events_truncate_guard",
    "trg_rtm_connect_dispatch_outbox_truncate_guard",
    "trg_rtm_connect_dispatch_events_truncate_guard",
}

CONNECT_C8_REQUIRED_CONSTRAINTS = {
    "ck_rtm_connect_production_release_code",
    "ck_rtm_connect_production_release_status",
    "ck_rtm_connect_production_release_connector",
    "ck_rtm_connect_production_release_source_commit",
    "ck_rtm_connect_production_release_hashes",
    "ck_rtm_connect_production_release_simulation_only",
    "ck_rtm_connect_production_release_external_effects",
    "ck_rtm_connect_production_release_live_activation",
    "ck_rtm_connect_production_release_human_activation",
    "ck_rtm_connect_production_release_provider_pack",
    "ck_rtm_connect_production_release_canary",
    "ck_rtm_connect_production_release_max_concurrency",
    "ck_rtm_connect_production_release_daily_limit",
    "ck_rtm_connect_production_release_version",
    "ck_rtm_connect_production_release_validity",
    "ck_rtm_connect_production_release_approvals",
    "ck_rtm_connect_production_release_approval_hashes",
    "ck_rtm_connect_production_release_ready",
    "ck_rtm_connect_production_release_simulated_active",
    "ck_rtm_connect_production_release_halt",
    "ck_rtm_connect_production_release_rejection",
    "ck_rtm_connect_production_release_expiry",
    "ck_rtm_connect_production_release_metadata",
    "ck_rtm_connect_production_release_event_sequence",
    "ck_rtm_connect_production_release_event_type",
    "ck_rtm_connect_production_release_event_actor",
    "ck_rtm_connect_production_release_event_statuses",
    "ck_rtm_connect_production_release_event_payload",
    "ck_rtm_connect_dispatch_outbox_status",
    "ck_rtm_connect_dispatch_outbox_business_command",
    "ck_rtm_connect_dispatch_outbox_effect_key",
    "ck_rtm_connect_dispatch_outbox_hashes",
    "ck_rtm_connect_dispatch_outbox_dry_run",
    "ck_rtm_connect_dispatch_outbox_network",
    "ck_rtm_connect_dispatch_outbox_provider",
    "ck_rtm_connect_dispatch_outbox_external_effects",
    "ck_rtm_connect_dispatch_outbox_version",
    "ck_rtm_connect_dispatch_outbox_claim_fence",
    "ck_rtm_connect_dispatch_outbox_claim",
    "ck_rtm_connect_dispatch_outbox_outcomes",
    "ck_rtm_connect_dispatch_outbox_metadata",
    "ck_rtm_connect_dispatch_event_sequence",
    "ck_rtm_connect_dispatch_event_type",
    "ck_rtm_connect_dispatch_event_actor",
    "ck_rtm_connect_dispatch_event_statuses",
    "ck_rtm_connect_dispatch_event_payload",
}


def connect_c8_production_ddl() -> list[tuple[str, str]]:
    """Devuelve DDL PostgreSQL idempotente, aditivo y no destructivo."""

    return [
        ("extensions", "CREATE EXTENSION IF NOT EXISTS pgcrypto;"),
        (
            "production_releases",
            """
            CREATE TABLE IF NOT EXISTS public.rtm_connect_production_releases (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                release_code TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'proposed',
                connector_code TEXT NOT NULL,
                connector_version TEXT NOT NULL,
                source_commit_sha TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL,
                policy_sha256 TEXT NOT NULL,
                schema_sha256 TEXT NOT NULL,
                build_artifact_sha256 TEXT NOT NULL,
                release_binding_sha256 TEXT NOT NULL,
                requested_by_operator_id UUID NOT NULL
                    REFERENCES public.rtm_operators(id) ON DELETE RESTRICT,
                security_approved_by_operator_id UUID
                    REFERENCES public.rtm_operators(id) ON DELETE RESTRICT,
                security_approval_sha256 TEXT,
                operations_approved_by_operator_id UUID
                    REFERENCES public.rtm_operators(id) ON DELETE RESTRICT,
                operations_approval_sha256 TEXT,
                requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                security_approved_at TIMESTAMPTZ,
                operations_approved_at TIMESTAMPTZ,
                ready_at TIMESTAMPTZ,
                simulated_active_at TIMESTAMPTZ,
                emergency_halt BOOLEAN NOT NULL DEFAULT FALSE,
                halted_at TIMESTAMPTZ,
                halted_by_operator_id UUID
                    REFERENCES public.rtm_operators(id) ON DELETE RESTRICT,
                halt_reason_code TEXT,
                rejected_at TIMESTAMPTZ,
                rejected_by_operator_id UUID
                    REFERENCES public.rtm_operators(id) ON DELETE RESTRICT,
                rejection_reason_code TEXT,
                valid_until TIMESTAMPTZ NOT NULL,
                expired_at TIMESTAMPTZ,
                simulation_only BOOLEAN NOT NULL DEFAULT TRUE,
                external_effects_allowed BOOLEAN NOT NULL DEFAULT FALSE,
                live_activation_allowed BOOLEAN NOT NULL DEFAULT FALSE,
                human_activation_required BOOLEAN NOT NULL DEFAULT TRUE,
                provider_pack_present BOOLEAN NOT NULL DEFAULT FALSE,
                canary_percent NUMERIC(5,2) NOT NULL DEFAULT 1.00,
                max_concurrency INTEGER NOT NULL DEFAULT 1,
                daily_action_limit INTEGER NOT NULL DEFAULT 1,
                version INTEGER NOT NULL DEFAULT 1,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_connect_production_release_code CHECK (
                    release_code ~ '^rtmc8-release-[0-9a-f]{24}$'
                    AND release_code = 'rtmc8-release-' ||
                        SUBSTRING(release_binding_sha256 FROM 1 FOR 24)
                ),
                CONSTRAINT ck_rtm_connect_production_release_status CHECK (
                    status IN (
                        'proposed', 'security_approved',
                        'operations_approved', 'ready', 'simulated_active',
                        'halted', 'rejected', 'expired'
                    )
                ),
                CONSTRAINT ck_rtm_connect_production_release_connector CHECK (
                    connector_code ~ '^[a-z][a-z0-9_.-]{2,95}$'
                    AND connector_version ~ '^v[0-9]+\\.[0-9]+$'
                ),
                CONSTRAINT ck_rtm_connect_production_release_source_commit
                    CHECK (source_commit_sha ~ '^[0-9a-f]{40}$'),
                CONSTRAINT ck_rtm_connect_production_release_hashes CHECK (
                    manifest_sha256 ~ '^[0-9a-f]{64}$'
                    AND policy_sha256 ~ '^[0-9a-f]{64}$'
                    AND schema_sha256 ~ '^[0-9a-f]{64}$'
                    AND build_artifact_sha256 ~ '^[0-9a-f]{64}$'
                    AND release_binding_sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_connect_production_release_simulation_only
                    CHECK (simulation_only = TRUE),
                CONSTRAINT ck_rtm_connect_production_release_external_effects
                    CHECK (external_effects_allowed = FALSE),
                CONSTRAINT ck_rtm_connect_production_release_live_activation
                    CHECK (live_activation_allowed = FALSE),
                CONSTRAINT ck_rtm_connect_production_release_human_activation
                    CHECK (human_activation_required = TRUE),
                CONSTRAINT ck_rtm_connect_production_release_provider_pack
                    CHECK (provider_pack_present = FALSE),
                CONSTRAINT ck_rtm_connect_production_release_canary CHECK (
                    canary_percent > 0 AND canary_percent <= 5
                ),
                CONSTRAINT ck_rtm_connect_production_release_max_concurrency
                    CHECK (max_concurrency = 1),
                CONSTRAINT ck_rtm_connect_production_release_daily_limit
                    CHECK (daily_action_limit = 1),
                CONSTRAINT ck_rtm_connect_production_release_version CHECK (
                    version > 0
                ),
                CONSTRAINT ck_rtm_connect_production_release_validity CHECK (
                    valid_until > requested_at
                ),
                CONSTRAINT ck_rtm_connect_production_release_approvals CHECK (
                    (
                        security_approved_by_operator_id IS NULL
                        OR security_approved_by_operator_id
                            <> requested_by_operator_id
                    )
                    AND (
                        operations_approved_by_operator_id IS NULL
                        OR operations_approved_by_operator_id
                            <> requested_by_operator_id
                    )
                    AND (
                        security_approved_by_operator_id IS NULL
                        OR operations_approved_by_operator_id IS NULL
                        OR security_approved_by_operator_id
                            <> operations_approved_by_operator_id
                    )
                    AND (
                        status NOT IN (
                            'security_approved', 'operations_approved',
                            'ready', 'simulated_active'
                        )
                        OR security_approved_by_operator_id IS NOT NULL
                    )
                    AND (
                        status NOT IN (
                            'operations_approved', 'ready', 'simulated_active'
                        )
                        OR operations_approved_by_operator_id IS NOT NULL
                    )
                ),
                CONSTRAINT ck_rtm_connect_production_release_approval_hashes
                    CHECK (
                        ((
                            (
                                security_approved_by_operator_id IS NULL
                                AND security_approved_at IS NULL
                                AND security_approval_sha256 IS NULL
                            ) OR (
                                security_approved_by_operator_id IS NOT NULL
                                AND security_approved_at IS NOT NULL
                                AND security_approved_at >= requested_at
                                AND security_approval_sha256 IS NOT NULL
                                AND security_approval_sha256
                                    ~ '^[0-9a-f]{64}$'
                            )
                        ) AND (
                            (
                                operations_approved_by_operator_id IS NULL
                                AND operations_approved_at IS NULL
                                AND operations_approval_sha256 IS NULL
                            ) OR (
                                operations_approved_by_operator_id IS NOT NULL
                                AND operations_approved_at IS NOT NULL
                                AND security_approved_at IS NOT NULL
                                AND operations_approved_at
                                    >= security_approved_at
                                AND operations_approval_sha256 IS NOT NULL
                                AND operations_approval_sha256
                                    ~ '^[0-9a-f]{64}$'
                            )
                        )) IS TRUE
                    ),
                CONSTRAINT ck_rtm_connect_production_release_ready CHECK (
                    status NOT IN ('ready', 'simulated_active') OR (
                        security_approved_by_operator_id IS NOT NULL
                        AND operations_approved_by_operator_id IS NOT NULL
                        AND ready_at IS NOT NULL
                        AND ready_at >= operations_approved_at
                    )
                ),
                CONSTRAINT ck_rtm_connect_production_release_simulated_active
                    CHECK (
                        status <> 'simulated_active'
                        OR (
                            simulated_active_at IS NOT NULL
                            AND ready_at IS NOT NULL
                            AND simulated_active_at >= ready_at
                        )
                    ),
                CONSTRAINT ck_rtm_connect_production_release_halt CHECK (
                    (status = 'halted') = emergency_halt
                    AND (
                        (status <> 'halted' AND halted_at IS NULL
                            AND halted_by_operator_id IS NULL
                            AND halt_reason_code IS NULL)
                        OR (
                            status = 'halted'
                            AND
                            halted_at IS NOT NULL
                            AND halted_at >= requested_at
                            AND halted_by_operator_id IS NOT NULL
                            AND halt_reason_code IS NOT NULL
                        )
                    )
                ),
                CONSTRAINT ck_rtm_connect_production_release_rejection CHECK (
                    status <> 'rejected' OR (
                        rejected_at IS NOT NULL
                        AND rejected_at >= requested_at
                        AND rejected_by_operator_id IS NOT NULL
                        AND rejection_reason_code IS NOT NULL
                    )
                ),
                CONSTRAINT ck_rtm_connect_production_release_expiry CHECK (
                    status <> 'expired' OR (
                        expired_at IS NOT NULL
                        AND expired_at >= requested_at
                    )
                ),
                CONSTRAINT ck_rtm_connect_production_release_metadata CHECK (
                    (jsonb_typeof(metadata) = 'object'
                    AND metadata ?& ARRAY[
                        'candidate', 'assessment',
                        'expected_admission_payload', 'control_version'
                    ]
                    AND metadata - ARRAY[
                        'candidate', 'assessment',
                        'expected_admission_payload', 'control_version'
                    ] = '{}'::jsonb
                    AND metadata->>'control_version' =
                        'rtm_connect_c8_production_control_v1_0'
                    AND jsonb_typeof(metadata->'candidate') = 'object'
                    AND metadata->'candidate' ?& ARRAY[
                        'candidate_id', 'requested_by_operator_id',
                        'source_commit_sha40', 'build_artifact_sha256',
                        'connector_manifest_sha256',
                        'provider_contract_sha256', 'egress_policy_sha256',
                        'credential_reference_sha256',
                        'schema_snapshot_sha256', 'test_report_sha256',
                        'created_at', 'expires_at', 'canary_percent',
                        'concurrency', 'max_simulated_actions_total',
                        'max_simulated_actions_per_day', 'max_payload_bytes',
                        'admission_ttl_seconds', 'simulation_only',
                        'external_effects_allowed',
                        'live_activation_allowed',
                        'human_activation_required', 'contract_version'
                    ]
                    AND metadata->'candidate' - ARRAY[
                        'candidate_id', 'requested_by_operator_id',
                        'source_commit_sha40', 'build_artifact_sha256',
                        'connector_manifest_sha256',
                        'provider_contract_sha256', 'egress_policy_sha256',
                        'credential_reference_sha256',
                        'schema_snapshot_sha256', 'test_report_sha256',
                        'created_at', 'expires_at', 'canary_percent',
                        'concurrency', 'max_simulated_actions_total',
                        'max_simulated_actions_per_day', 'max_payload_bytes',
                        'admission_ttl_seconds', 'simulation_only',
                        'external_effects_allowed',
                        'live_activation_allowed',
                        'human_activation_required', 'contract_version'
                    ] = '{}'::jsonb
                    AND metadata->'candidate'->>'candidate_id' =
                        CAST(id AS TEXT)
                    AND metadata->'candidate'->>'requested_by_operator_id' =
                        CAST(requested_by_operator_id AS TEXT)
                    AND metadata->'candidate'->>'source_commit_sha40' =
                        source_commit_sha
                    AND metadata->'candidate'->>'build_artifact_sha256' =
                        build_artifact_sha256
                    AND metadata->'candidate'->>'connector_manifest_sha256' =
                        manifest_sha256
                    AND metadata->'candidate'->>'egress_policy_sha256' =
                        policy_sha256
                    AND metadata->'candidate'->>'schema_snapshot_sha256' =
                        schema_sha256
                    AND metadata->'candidate'->>'provider_contract_sha256'
                        ~ '^[0-9a-f]{64}$'
                    AND metadata->'candidate'->>'credential_reference_sha256'
                        ~ '^[0-9a-f]{64}$'
                    AND metadata->'candidate'->>'test_report_sha256'
                        ~ '^[0-9a-f]{64}$'
                    AND CAST(metadata->'candidate'->>'created_at'
                        AS TIMESTAMPTZ) = requested_at
                    AND CAST(metadata->'candidate'->>'expires_at'
                        AS TIMESTAMPTZ) = valid_until
                    AND CAST(metadata->'candidate'->>'canary_percent'
                        AS NUMERIC) = canary_percent
                    AND CAST(metadata->'candidate'->>'concurrency'
                        AS INTEGER) = max_concurrency
                    AND CAST(metadata->'candidate'->>
                        'max_simulated_actions_total' AS INTEGER) = 1
                    AND CAST(metadata->'candidate'->>
                        'max_simulated_actions_per_day' AS INTEGER) =
                        daily_action_limit
                    AND CAST(metadata->'candidate'->>'max_payload_bytes'
                        AS INTEGER) BETWEEN 1 AND 1048576
                    AND CAST(metadata->'candidate'->>'admission_ttl_seconds'
                        AS INTEGER) BETWEEN 1 AND 86400
                    AND (valid_until - requested_at) <=
                        CAST(metadata->'candidate'->>
                            'admission_ttl_seconds' AS INTEGER)
                            * INTERVAL '1 second'
                    AND metadata->'candidate'->'simulation_only' =
                        'true'::jsonb
                    AND metadata->'candidate'->'external_effects_allowed' =
                        'false'::jsonb
                    AND metadata->'candidate'->'live_activation_allowed' =
                        'false'::jsonb
                    AND metadata->'candidate'->'human_activation_required' =
                        'true'::jsonb
                    AND metadata->'candidate'->>'contract_version' =
                        'rtm.connect.c8.admission.v1'
                    AND jsonb_typeof(metadata->'assessment') = 'object'
                    AND metadata->'assessment' ?& ARRAY[
                        'candidate_sha256', 'evaluated_at', 'blocker_codes',
                        'verdict', 'simulation_admitted',
                        'live_production_admitted',
                        'production_effects_available'
                    ]
                    AND metadata->'assessment' - ARRAY[
                        'candidate_sha256', 'evaluated_at', 'blocker_codes',
                        'verdict', 'simulation_admitted',
                        'live_production_admitted',
                        'production_effects_available'
                    ] = '{}'::jsonb
                    AND metadata->'assessment'->>'candidate_sha256' =
                        release_binding_sha256
                    AND CAST(metadata->'assessment'->>'evaluated_at'
                        AS TIMESTAMPTZ) >= requested_at
                    AND CAST(metadata->'assessment'->>'evaluated_at'
                        AS TIMESTAMPTZ) < valid_until
                    AND metadata->'assessment'->>'verdict' = 'no_go'
                    AND metadata->'assessment'->'blocker_codes' =
                        jsonb_build_array(
                            'provider_specific_pack_missing',
                            'production_transport_absent',
                            'live_activation_unavailable',
                            'external_effects_forbidden'
                        )
                    AND metadata->'assessment'->'simulation_admitted' =
                        'true'::jsonb
                    AND metadata->'assessment'->'live_production_admitted' =
                        'false'::jsonb
                    AND metadata->'assessment'->
                        'production_effects_available' = 'false'::jsonb
                    AND metadata->'expected_admission_payload' =
                        jsonb_build_object(
                            'contract_version',
                                'rtm.connect.c8.admission.v1',
                            'candidate_sha256', release_binding_sha256,
                            'synthetic_marker', 'RTM_C8_SYNTHETIC_ONLY',
                            'simulation_only', TRUE,
                            'external_effects_allowed', FALSE,
                            'live_activation_allowed', FALSE,
                            'human_activation_required', TRUE
                        )
                    ) IS TRUE
                )
            );
            """,
        ),
        (
            "production_release_events",
            """
            CREATE TABLE IF NOT EXISTS public.rtm_connect_production_release_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                release_id UUID NOT NULL
                    REFERENCES public.rtm_connect_production_releases(id)
                    ON DELETE RESTRICT,
                release_binding_sha256 TEXT NOT NULL,
                sequence_number INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                operator_id UUID
                    REFERENCES public.rtm_operators(id) ON DELETE RESTRICT,
                from_status TEXT,
                to_status TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_connect_production_release_event_sequence
                    CHECK (sequence_number > 0),
                CONSTRAINT ck_rtm_connect_production_release_event_type CHECK (
                    event_type IN (
                        'release_proposed',
                        'security_approval_recorded',
                        'operations_approval_recorded',
                        'simulation_release_ready',
                        'simulation_activation_recorded',
                        'emergency_halt_recorded'
                    )
                    AND reason_code ~ '^[a-z][a-z0-9_.-]{2,95}$'
                ),
                CONSTRAINT ck_rtm_connect_production_release_event_actor CHECK (
                    actor_type IN (
                        'requester', 'security', 'operations', 'system'
                    )
                    AND (actor_type = 'system' OR operator_id IS NOT NULL)
                ),
                CONSTRAINT ck_rtm_connect_production_release_event_statuses
                    CHECK (
                        (from_status IS NULL OR from_status IN (
                            'proposed', 'security_approved',
                            'operations_approved', 'ready',
                            'simulated_active', 'halted', 'rejected', 'expired'
                        ))
                        AND to_status IN (
                            'proposed', 'security_approved',
                            'operations_approved', 'ready',
                            'simulated_active', 'halted', 'rejected', 'expired'
                        )
                        AND (
                            (sequence_number = 1 AND from_status IS NULL)
                            OR (sequence_number > 1 AND from_status IS NOT NULL)
                        )
                    ),
                CONSTRAINT ck_rtm_connect_production_release_event_payload
                    CHECK (
                        release_binding_sha256 ~ '^[0-9a-f]{64}$'
                        AND jsonb_typeof(payload) = 'object'
                    )
            );
            """,
        ),
        (
            "dispatch_outbox",
            """
            CREATE TABLE IF NOT EXISTS public.rtm_connect_dispatch_outbox (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                action_id UUID NOT NULL
                    REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT,
                authorization_id UUID NOT NULL
                    REFERENCES public.rtm_connect_authorizations(id)
                    ON DELETE RESTRICT,
                authorization_version INTEGER NOT NULL,
                release_id UUID NOT NULL
                    REFERENCES public.rtm_connect_production_releases(id)
                    ON DELETE RESTRICT,
                status TEXT NOT NULL DEFAULT 'prepared',
                business_command_id TEXT NOT NULL,
                production_effect_key TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                release_manifest_sha256 TEXT NOT NULL,
                release_binding_sha256 TEXT NOT NULL,
                dry_run_only BOOLEAN NOT NULL DEFAULT TRUE,
                network_allowed BOOLEAN NOT NULL DEFAULT FALSE,
                provider_contacted BOOLEAN NOT NULL DEFAULT FALSE,
                external_effects_allowed BOOLEAN NOT NULL DEFAULT FALSE,
                claim_owner TEXT,
                claim_token UUID,
                claim_fence BIGINT NOT NULL DEFAULT 0,
                claimed_at TIMESTAMPTZ,
                claim_expires_at TIMESTAMPTZ,
                dry_run_confirmed_at TIMESTAMPTZ,
                unknown_at TIMESTAMPTZ,
                manual_review_at TIMESTAMPTZ,
                cancelled_at TIMESTAMPTZ,
                version INTEGER NOT NULL DEFAULT 1,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_connect_dispatch_outbox_status CHECK (
                    status IN (
                        'prepared', 'claimed', 'dry_run_confirmed',
                        'unknown', 'manual_review', 'cancelled'
                    )
                ),
                CONSTRAINT ck_rtm_connect_dispatch_outbox_business_command
                    CHECK (
                        business_command_id
                            ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{7,255}$'
                    ),
                CONSTRAINT ck_rtm_connect_dispatch_outbox_effect_key CHECK (
                    production_effect_key
                        ~ '^[A-Za-z0-9][A-Za-z0-9:._-]{7,255}$'
                ),
                CONSTRAINT ck_rtm_connect_dispatch_outbox_hashes CHECK (
                    payload_sha256 ~ '^[0-9a-f]{64}$'
                    AND request_sha256 ~ '^[0-9a-f]{64}$'
                    AND release_manifest_sha256 ~ '^[0-9a-f]{64}$'
                    AND release_binding_sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_connect_dispatch_outbox_dry_run
                    CHECK (dry_run_only = TRUE),
                CONSTRAINT ck_rtm_connect_dispatch_outbox_network
                    CHECK (network_allowed = FALSE),
                CONSTRAINT ck_rtm_connect_dispatch_outbox_provider
                    CHECK (provider_contacted = FALSE),
                CONSTRAINT ck_rtm_connect_dispatch_outbox_external_effects
                    CHECK (external_effects_allowed = FALSE),
                CONSTRAINT ck_rtm_connect_dispatch_outbox_version CHECK (
                    version > 0 AND authorization_version > 0
                ),
                CONSTRAINT ck_rtm_connect_dispatch_outbox_claim_fence CHECK (
                    claim_fence >= 0
                ),
                CONSTRAINT ck_rtm_connect_dispatch_outbox_claim CHECK (
                    (
                        status IN ('prepared', 'cancelled')
                        AND claim_owner IS NULL
                        AND claim_token IS NULL
                        AND claim_fence = 0
                        AND claimed_at IS NULL
                        AND claim_expires_at IS NULL
                    ) OR (
                        status IN (
                            'claimed', 'dry_run_confirmed',
                            'unknown', 'manual_review', 'cancelled'
                        )
                        AND claim_owner IS NOT NULL
                        AND claim_token IS NOT NULL
                        AND claim_fence > 0
                        AND claimed_at IS NOT NULL
                        AND claim_expires_at > claimed_at
                    )
                ),
                CONSTRAINT ck_rtm_connect_dispatch_outbox_outcomes CHECK (
                    (status <> 'dry_run_confirmed'
                        OR dry_run_confirmed_at IS NOT NULL)
                    AND (status <> 'unknown' OR unknown_at IS NOT NULL)
                    AND (status <> 'manual_review'
                        OR manual_review_at IS NOT NULL)
                    AND (status <> 'cancelled' OR cancelled_at IS NOT NULL)
                ),
                CONSTRAINT ck_rtm_connect_dispatch_outbox_metadata CHECK (
                    jsonb_typeof(metadata) = 'object'
                )
            );
            """,
        ),
        (
            "dispatch_events",
            """
            CREATE TABLE IF NOT EXISTS public.rtm_connect_dispatch_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                outbox_id UUID NOT NULL
                    REFERENCES public.rtm_connect_dispatch_outbox(id)
                    ON DELETE RESTRICT,
                action_id UUID NOT NULL
                    REFERENCES public.rtm_connect_actions(id) ON DELETE RESTRICT,
                authorization_id UUID NOT NULL
                    REFERENCES public.rtm_connect_authorizations(id)
                    ON DELETE RESTRICT,
                release_id UUID NOT NULL
                    REFERENCES public.rtm_connect_production_releases(id)
                    ON DELETE RESTRICT,
                release_binding_sha256 TEXT NOT NULL,
                sequence_number INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                operator_id UUID
                    REFERENCES public.rtm_operators(id) ON DELETE RESTRICT,
                from_status TEXT,
                to_status TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_connect_dispatch_event_sequence CHECK (
                    sequence_number > 0
                ),
                CONSTRAINT ck_rtm_connect_dispatch_event_type CHECK (
                    event_type IN (
                        'dispatch_dry_run_prepared',
                        'dispatch_dry_run_claimed',
                        'dispatch_dry_run_confirmed',
                        'dispatch_simulation_unknown',
                        'dispatch_manual_review_recorded'
                    )
                    AND reason_code ~ '^[a-z][a-z0-9_.-]{2,95}$'
                ),
                CONSTRAINT ck_rtm_connect_dispatch_event_actor CHECK (
                    actor_type IN ('connect', 'operator', 'system')
                    AND (actor_type <> 'operator' OR operator_id IS NOT NULL)
                ),
                CONSTRAINT ck_rtm_connect_dispatch_event_statuses CHECK (
                    (from_status IS NULL OR from_status IN (
                        'prepared', 'claimed', 'dry_run_confirmed',
                        'unknown', 'manual_review', 'cancelled'
                    ))
                    AND to_status IN (
                        'prepared', 'claimed', 'dry_run_confirmed',
                        'unknown', 'manual_review', 'cancelled'
                    )
                    AND (
                        (sequence_number = 1 AND from_status IS NULL)
                        OR (sequence_number > 1 AND from_status IS NOT NULL)
                    )
                ),
                CONSTRAINT ck_rtm_connect_dispatch_event_payload CHECK (
                    release_binding_sha256 ~ '^[0-9a-f]{64}$'
                    AND jsonb_typeof(payload) = 'object'
                )
            );
            """,
        ),
        ("uq_production_release_code", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_production_release_code
            ON public.rtm_connect_production_releases(release_code);
        """),
        ("uq_production_release_binding", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_production_release_binding
            ON public.rtm_connect_production_releases(release_binding_sha256);
        """),
        ("idx_production_release_status", """
            CREATE INDEX IF NOT EXISTS
                idx_rtm_connect_production_release_status
            ON public.rtm_connect_production_releases(
                status, valid_until, created_at DESC
            );
        """),
        ("uq_production_release_event_sequence", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_production_release_event_sequence
            ON public.rtm_connect_production_release_events(
                release_id, sequence_number
            );
        """),
        ("idx_production_release_event_status", """
            CREATE INDEX IF NOT EXISTS
                idx_rtm_connect_production_release_event_status
            ON public.rtm_connect_production_release_events(
                release_id, to_status, sequence_number
            );
        """),
        ("uq_dispatch_business_command", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_dispatch_business_command
            ON public.rtm_connect_dispatch_outbox(business_command_id);
        """),
        ("uq_dispatch_production_effect", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_dispatch_production_effect
            ON public.rtm_connect_dispatch_outbox(production_effect_key);
        """),
        ("uq_dispatch_release_once", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_dispatch_release_once
            ON public.rtm_connect_dispatch_outbox(release_id);
        """),
        ("uq_dispatch_claim_token", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_dispatch_claim_token
            ON public.rtm_connect_dispatch_outbox(claim_token)
            WHERE claim_token IS NOT NULL;
        """),
        ("idx_dispatch_claim_queue", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_dispatch_claim_queue
            ON public.rtm_connect_dispatch_outbox(status, created_at, id)
            WHERE status = 'prepared';
        """),
        ("idx_dispatch_action", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_dispatch_action
            ON public.rtm_connect_dispatch_outbox(action_id, created_at DESC);
        """),
        ("idx_dispatch_release", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_dispatch_release
            ON public.rtm_connect_dispatch_outbox(
                release_id, status, created_at DESC
            );
        """),
        ("uq_dispatch_event_sequence", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_dispatch_event_sequence
            ON public.rtm_connect_dispatch_events(outbox_id, sequence_number);
        """),
        ("idx_dispatch_event_action", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_dispatch_event_action
            ON public.rtm_connect_dispatch_events(action_id, sequence_number);
        """),
        ("idx_dispatch_event_release", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_dispatch_event_release
            ON public.rtm_connect_dispatch_events(
                release_id, created_at, sequence_number
            );
        """),
        (
            "production_release_state_guard_function",
            """
            CREATE OR REPLACE FUNCTION
                public.rtm_connect_production_release_state_guard()
            RETURNS trigger AS $$
            DECLARE
                transition_ok BOOLEAN := FALSE;
                guard_now TIMESTAMPTZ;
            BEGIN
                guard_now := clock_timestamp();
                IF TG_OP = 'INSERT' THEN
                    IF NEW.status <> 'proposed' OR NEW.version <> 1 THEN
                        RAISE EXCEPTION
                            'production release must start proposed at version 1';
                    END IF;
                    IF NEW.requested_at > guard_now
                        OR NEW.created_at > guard_now
                        OR NEW.updated_at > guard_now
                        OR NEW.valid_until <= guard_now
                    THEN
                        RAISE EXCEPTION
                            'production release validity must include the current database time';
                    END IF;
                    IF NEW.security_approved_by_operator_id IS NOT NULL
                        OR NEW.security_approval_sha256 IS NOT NULL
                        OR NEW.security_approved_at IS NOT NULL
                        OR NEW.operations_approved_by_operator_id IS NOT NULL
                        OR NEW.operations_approval_sha256 IS NOT NULL
                        OR NEW.operations_approved_at IS NOT NULL
                        OR NEW.ready_at IS NOT NULL
                        OR NEW.simulated_active_at IS NOT NULL
                        OR NEW.emergency_halt = TRUE
                        OR NEW.halted_at IS NOT NULL
                        OR NEW.halted_by_operator_id IS NOT NULL
                        OR NEW.halt_reason_code IS NOT NULL
                        OR NEW.rejected_at IS NOT NULL
                        OR NEW.rejected_by_operator_id IS NOT NULL
                        OR NEW.rejection_reason_code IS NOT NULL
                        OR NEW.expired_at IS NOT NULL
                    THEN
                        RAISE EXCEPTION
                            'proposed production release must have no decisions';
                    END IF;
                    RETURN NEW;
                END IF;
                IF NEW.status = OLD.status THEN
                    RAISE EXCEPTION
                        'production release update requires a status transition';
                END IF;
                IF NEW.version <> OLD.version + 1 THEN
                    RAISE EXCEPTION
                        'production release version must increment exactly once';
                END IF;
                IF NEW.updated_at < OLD.updated_at
                    OR NEW.updated_at > guard_now
                    OR (
                        NEW.security_approved_at IS NOT NULL
                        AND NEW.security_approved_at > guard_now
                    )
                    OR (
                        NEW.operations_approved_at IS NOT NULL
                        AND NEW.operations_approved_at > guard_now
                    )
                    OR (NEW.ready_at IS NOT NULL AND NEW.ready_at > guard_now)
                    OR (
                        NEW.simulated_active_at IS NOT NULL
                        AND NEW.simulated_active_at > guard_now
                    )
                    OR (NEW.halted_at IS NOT NULL AND NEW.halted_at > guard_now)
                    OR (
                        NEW.rejected_at IS NOT NULL
                        AND NEW.rejected_at > guard_now
                    )
                    OR (NEW.expired_at IS NOT NULL AND NEW.expired_at > guard_now)
                THEN
                    RAISE EXCEPTION
                        'production release transition timestamps cannot be in the future';
                END IF;
                IF (
                    NEW.security_approved_by_operator_id IS DISTINCT FROM
                        OLD.security_approved_by_operator_id
                    OR NEW.security_approval_sha256 IS DISTINCT FROM
                        OLD.security_approval_sha256
                    OR NEW.security_approved_at IS DISTINCT FROM
                        OLD.security_approved_at
                ) AND NEW.status <> 'security_approved' THEN
                    RAISE EXCEPTION
                        'security identity may only be set by security approval';
                END IF;
                IF (
                    NEW.operations_approved_by_operator_id IS DISTINCT FROM
                        OLD.operations_approved_by_operator_id
                    OR NEW.operations_approval_sha256 IS DISTINCT FROM
                        OLD.operations_approval_sha256
                    OR NEW.operations_approved_at IS DISTINCT FROM
                        OLD.operations_approved_at
                ) AND NEW.status <> 'operations_approved' THEN
                    RAISE EXCEPTION
                        'operations identity may only be set by operations approval';
                END IF;
                IF NEW.ready_at IS DISTINCT FROM OLD.ready_at
                    AND NEW.status <> 'ready' THEN
                    RAISE EXCEPTION
                        'ready timestamp may only be set on ready transition';
                END IF;
                IF NEW.simulated_active_at
                        IS DISTINCT FROM OLD.simulated_active_at
                    AND NEW.status <> 'simulated_active' THEN
                    RAISE EXCEPTION
                        'simulated activation timestamp has wrong transition';
                END IF;
                IF (
                    NEW.emergency_halt IS DISTINCT FROM OLD.emergency_halt
                    OR NEW.halted_at IS DISTINCT FROM OLD.halted_at
                    OR NEW.halted_by_operator_id
                        IS DISTINCT FROM OLD.halted_by_operator_id
                    OR NEW.halt_reason_code IS DISTINCT FROM OLD.halt_reason_code
                ) AND NEW.status <> 'halted' THEN
                    RAISE EXCEPTION
                        'emergency halt fields have wrong transition';
                END IF;
                IF (
                    NEW.rejected_at IS DISTINCT FROM OLD.rejected_at
                    OR NEW.rejected_by_operator_id
                        IS DISTINCT FROM OLD.rejected_by_operator_id
                    OR NEW.rejection_reason_code
                        IS DISTINCT FROM OLD.rejection_reason_code
                ) AND NEW.status <> 'rejected' THEN
                    RAISE EXCEPTION 'rejection fields have wrong transition';
                END IF;
                IF NEW.expired_at IS DISTINCT FROM OLD.expired_at
                    AND NEW.status <> 'expired' THEN
                    RAISE EXCEPTION 'expiry field has wrong transition';
                END IF;
                transition_ok := CASE
                    WHEN NEW.status = 'halted'
                        AND OLD.status NOT IN ('halted', 'rejected', 'expired')
                        THEN TRUE
                    WHEN OLD.status = 'proposed'
                        AND NEW.status IN (
                            'security_approved', 'rejected', 'expired'
                        ) THEN TRUE
                    WHEN OLD.status = 'security_approved'
                        AND NEW.status IN (
                            'operations_approved', 'rejected', 'expired'
                        ) THEN TRUE
                    WHEN OLD.status = 'operations_approved'
                        AND NEW.status IN ('ready', 'rejected', 'expired')
                        THEN TRUE
                    WHEN OLD.status = 'ready'
                        AND NEW.status IN (
                            'simulated_active', 'rejected', 'expired'
                        ) THEN TRUE
                    WHEN OLD.status = 'simulated_active'
                        AND NEW.status = 'expired' THEN TRUE
                    ELSE FALSE
                END;
                IF NOT transition_ok THEN
                    RAISE EXCEPTION
                        'invalid production release transition: % -> %',
                        OLD.status, NEW.status;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            SET search_path = pg_catalog, public, pg_temp;
            """,
        ),
        (
            "production_release_state_guard_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_production_release_state_guard'
                      AND tgrelid =
                        'public.rtm_connect_production_releases'::regclass
                ) THEN
                    CREATE TRIGGER
                        trg_rtm_connect_production_release_state_guard
                    BEFORE INSERT OR UPDATE ON
                        public.rtm_connect_production_releases
                    FOR EACH ROW EXECUTE FUNCTION
                        public.rtm_connect_production_release_state_guard();
                END IF;
            END $$;
            """,
        ),
        (
            "production_release_frozen_guard_function",
            """
            CREATE OR REPLACE FUNCTION
                public.rtm_connect_production_release_frozen_guard()
            RETURNS trigger AS $$
            BEGIN
                IF NEW.id IS DISTINCT FROM OLD.id
                    OR NEW.release_code IS DISTINCT FROM OLD.release_code
                    OR NEW.connector_code IS DISTINCT FROM OLD.connector_code
                    OR NEW.connector_version
                        IS DISTINCT FROM OLD.connector_version
                    OR NEW.source_commit_sha
                        IS DISTINCT FROM OLD.source_commit_sha
                    OR NEW.manifest_sha256 IS DISTINCT FROM OLD.manifest_sha256
                    OR NEW.policy_sha256 IS DISTINCT FROM OLD.policy_sha256
                    OR NEW.schema_sha256 IS DISTINCT FROM OLD.schema_sha256
                    OR NEW.build_artifact_sha256
                        IS DISTINCT FROM OLD.build_artifact_sha256
                    OR NEW.release_binding_sha256
                        IS DISTINCT FROM OLD.release_binding_sha256
                    OR NEW.requested_by_operator_id
                        IS DISTINCT FROM OLD.requested_by_operator_id
                    OR NEW.requested_at IS DISTINCT FROM OLD.requested_at
                    OR NEW.valid_until IS DISTINCT FROM OLD.valid_until
                    OR NEW.simulation_only IS DISTINCT FROM OLD.simulation_only
                    OR NEW.external_effects_allowed
                        IS DISTINCT FROM OLD.external_effects_allowed
                    OR NEW.live_activation_allowed
                        IS DISTINCT FROM OLD.live_activation_allowed
                    OR NEW.human_activation_required
                        IS DISTINCT FROM OLD.human_activation_required
                    OR NEW.provider_pack_present
                        IS DISTINCT FROM OLD.provider_pack_present
                    OR NEW.canary_percent IS DISTINCT FROM OLD.canary_percent
                    OR NEW.max_concurrency IS DISTINCT FROM OLD.max_concurrency
                    OR NEW.daily_action_limit
                        IS DISTINCT FROM OLD.daily_action_limit
                    OR NEW.metadata IS DISTINCT FROM OLD.metadata
                    OR NEW.created_at IS DISTINCT FROM OLD.created_at
                THEN
                    RAISE EXCEPTION
                        'production release binding and inert limits are frozen';
                END IF;
                IF OLD.security_approved_by_operator_id IS NOT NULL AND (
                    NEW.security_approved_by_operator_id IS DISTINCT FROM
                        OLD.security_approved_by_operator_id
                    OR NEW.security_approval_sha256 IS DISTINCT FROM
                        OLD.security_approval_sha256
                    OR NEW.security_approved_at IS DISTINCT FROM
                        OLD.security_approved_at
                ) THEN
                    RAISE EXCEPTION
                        'production security approval is write-once';
                END IF;
                IF OLD.operations_approved_by_operator_id IS NOT NULL AND (
                    NEW.operations_approved_by_operator_id IS DISTINCT FROM
                        OLD.operations_approved_by_operator_id
                    OR NEW.operations_approval_sha256 IS DISTINCT FROM
                        OLD.operations_approval_sha256
                    OR NEW.operations_approved_at IS DISTINCT FROM
                        OLD.operations_approved_at
                ) THEN
                    RAISE EXCEPTION
                        'production operations approval is write-once';
                END IF;
                IF OLD.ready_at IS NOT NULL
                    AND NEW.ready_at IS DISTINCT FROM OLD.ready_at THEN
                    RAISE EXCEPTION 'production ready timestamp is write-once';
                END IF;
                IF OLD.simulated_active_at IS NOT NULL
                    AND NEW.simulated_active_at
                        IS DISTINCT FROM OLD.simulated_active_at THEN
                    RAISE EXCEPTION
                        'production simulated activation is write-once';
                END IF;
                IF OLD.emergency_halt = TRUE AND (
                    NEW.emergency_halt IS DISTINCT FROM OLD.emergency_halt
                    OR NEW.halted_at IS DISTINCT FROM OLD.halted_at
                    OR NEW.halted_by_operator_id
                        IS DISTINCT FROM OLD.halted_by_operator_id
                    OR NEW.halt_reason_code IS DISTINCT FROM OLD.halt_reason_code
                ) THEN
                    RAISE EXCEPTION 'production emergency halt is terminal';
                END IF;
                IF OLD.rejected_at IS NOT NULL AND (
                    NEW.rejected_at IS DISTINCT FROM OLD.rejected_at
                    OR NEW.rejected_by_operator_id
                        IS DISTINCT FROM OLD.rejected_by_operator_id
                    OR NEW.rejection_reason_code
                        IS DISTINCT FROM OLD.rejection_reason_code
                ) THEN
                    RAISE EXCEPTION 'production rejection is write-once';
                END IF;
                IF OLD.expired_at IS NOT NULL
                    AND NEW.expired_at IS DISTINCT FROM OLD.expired_at THEN
                    RAISE EXCEPTION 'production expiry is write-once';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            SET search_path = pg_catalog, public, pg_temp;
            """,
        ),
        (
            "production_release_frozen_guard_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_production_release_frozen_guard'
                      AND tgrelid =
                        'public.rtm_connect_production_releases'::regclass
                ) THEN
                    CREATE TRIGGER
                        trg_rtm_connect_production_release_frozen_guard
                    BEFORE UPDATE ON public.rtm_connect_production_releases
                    FOR EACH ROW EXECUTE FUNCTION
                        public.rtm_connect_production_release_frozen_guard();
                END IF;
            END $$;
            """,
        ),
        (
            "c8_delete_guard_function",
            """
            CREATE OR REPLACE FUNCTION public.rtm_connect_c8_delete_guard()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% cannot be deleted', TG_TABLE_NAME;
            END;
            $$ LANGUAGE plpgsql
            SET search_path = pg_catalog, public, pg_temp;
            """,
        ),
        (
            "production_release_delete_guard_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_production_release_delete_guard'
                      AND tgrelid =
                        'public.rtm_connect_production_releases'::regclass
                ) THEN
                    CREATE TRIGGER
                        trg_rtm_connect_production_release_delete_guard
                    BEFORE DELETE ON public.rtm_connect_production_releases
                    FOR EACH ROW EXECUTE FUNCTION
                        public.rtm_connect_c8_delete_guard();
                END IF;
            END $$;
            """,
        ),
        (
            "production_release_truncate_guard_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_production_release_truncate_guard'
                      AND tgrelid =
                        'public.rtm_connect_production_releases'::regclass
                ) THEN
                    CREATE TRIGGER
                        trg_rtm_connect_production_release_truncate_guard
                    BEFORE TRUNCATE ON
                        public.rtm_connect_production_releases
                    FOR EACH STATEMENT EXECUTE FUNCTION
                        public.rtm_connect_c8_delete_guard();
                END IF;
            END $$;
            """,
        ),
        (
            "production_release_events_truncate_guard_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_production_release_events_truncate_guard'
                      AND tgrelid =
                        'public.rtm_connect_production_release_events'::regclass
                ) THEN
                    CREATE TRIGGER
                        trg_rtm_connect_production_release_events_truncate_guard
                    BEFORE TRUNCATE ON
                        public.rtm_connect_production_release_events
                    FOR EACH STATEMENT EXECUTE FUNCTION
                        public.rtm_connect_c8_delete_guard();
                END IF;
            END $$;
            """,
        ),
        (
            "dispatch_outbox_truncate_guard_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_dispatch_outbox_truncate_guard'
                      AND tgrelid =
                        'public.rtm_connect_dispatch_outbox'::regclass
                ) THEN
                    CREATE TRIGGER
                        trg_rtm_connect_dispatch_outbox_truncate_guard
                    BEFORE TRUNCATE ON public.rtm_connect_dispatch_outbox
                    FOR EACH STATEMENT EXECUTE FUNCTION
                        public.rtm_connect_c8_delete_guard();
                END IF;
            END $$;
            """,
        ),
        (
            "dispatch_events_truncate_guard_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_dispatch_events_truncate_guard'
                      AND tgrelid =
                        'public.rtm_connect_dispatch_events'::regclass
                ) THEN
                    CREATE TRIGGER
                        trg_rtm_connect_dispatch_events_truncate_guard
                    BEFORE TRUNCATE ON public.rtm_connect_dispatch_events
                    FOR EACH STATEMENT EXECUTE FUNCTION
                        public.rtm_connect_c8_delete_guard();
                END IF;
            END $$;
            """,
        ),
        (
            "production_release_event_scope_guard_function",
            """
            CREATE OR REPLACE FUNCTION
                public.rtm_connect_production_release_event_scope_guard()
            RETURNS trigger AS $$
            DECLARE
                parent_binding TEXT;
                parent_status TEXT;
                parent_metadata JSONB;
                parent_requested_at TIMESTAMPTZ;
                parent_requester_id UUID;
                parent_security_id UUID;
                parent_operations_id UUID;
                parent_halted_by_id UUID;
                parent_halt_reason TEXT;
                parent_version INTEGER;
                expected_sequence INTEGER;
                previous_status TEXT;
                previous_created_at TIMESTAMPTZ;
                guard_now TIMESTAMPTZ;
            BEGIN
                SELECT release_binding_sha256, status, metadata, requested_at,
                       requested_by_operator_id,
                       security_approved_by_operator_id,
                       operations_approved_by_operator_id,
                       halted_by_operator_id, halt_reason_code, version
                  INTO parent_binding, parent_status, parent_metadata,
                       parent_requested_at, parent_requester_id,
                       parent_security_id, parent_operations_id,
                       parent_halted_by_id, parent_halt_reason, parent_version
                FROM public.rtm_connect_production_releases
                WHERE id = NEW.release_id
                FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'production release event parent missing';
                END IF;
                SELECT COALESCE(MAX(sequence_number), 0) + 1
                  INTO expected_sequence
                FROM public.rtm_connect_production_release_events
                WHERE release_id = NEW.release_id;
                guard_now := clock_timestamp();
                SELECT to_status, created_at
                  INTO previous_status, previous_created_at
                FROM public.rtm_connect_production_release_events
                WHERE release_id = NEW.release_id
                ORDER BY sequence_number DESC
                LIMIT 1;
                IF NEW.release_binding_sha256 IS DISTINCT FROM parent_binding
                    OR NEW.to_status IS DISTINCT FROM parent_status
                    OR NEW.sequence_number IS DISTINCT FROM expected_sequence
                    OR NEW.sequence_number IS DISTINCT FROM parent_version
                    OR (expected_sequence = 1 AND NEW.from_status IS NOT NULL)
                    OR (expected_sequence > 1 AND
                        NEW.from_status IS DISTINCT FROM previous_status)
                    OR NEW.created_at > guard_now
                    OR NEW.created_at < parent_requested_at
                    OR (
                        previous_created_at IS NOT NULL
                        AND NEW.created_at < previous_created_at
                    )
                    OR NOT (
                        (
                            NEW.event_type = 'release_proposed'
                            AND NEW.from_status IS NULL
                            AND NEW.to_status = 'proposed'
                            AND NEW.actor_type = 'requester'
                            AND NEW.operator_id = parent_requester_id
                            AND NEW.reason_code =
                                'simulation_candidate_recorded'
                        ) OR (
                            NEW.event_type = 'security_approval_recorded'
                            AND NEW.from_status = 'proposed'
                            AND NEW.to_status = 'security_approved'
                            AND NEW.actor_type = 'security'
                            AND NEW.operator_id = parent_security_id
                            AND NEW.reason_code =
                                'simulation_admission_approved'
                        ) OR (
                            NEW.event_type = 'operations_approval_recorded'
                            AND NEW.from_status = 'security_approved'
                            AND NEW.to_status = 'operations_approved'
                            AND NEW.actor_type = 'operations'
                            AND NEW.operator_id = parent_operations_id
                            AND NEW.reason_code =
                                'simulation_admission_approved'
                        ) OR (
                            NEW.event_type = 'simulation_release_ready'
                            AND NEW.from_status = 'operations_approved'
                            AND NEW.to_status = 'ready'
                            AND NEW.actor_type = 'operations'
                            AND NEW.operator_id = parent_operations_id
                            AND NEW.reason_code = 'simulation_release_ready'
                        ) OR (
                            NEW.event_type = 'simulation_activation_recorded'
                            AND NEW.from_status = 'ready'
                            AND NEW.to_status = 'simulated_active'
                            AND NEW.actor_type = 'system'
                            AND NEW.operator_id IS NOT NULL
                            AND NEW.operator_id <> parent_requester_id
                            AND NEW.operator_id <> parent_security_id
                            AND NEW.operator_id <> parent_operations_id
                            AND NEW.reason_code =
                                'simulation_activation_recorded'
                        ) OR (
                            NEW.event_type = 'emergency_halt_recorded'
                            AND NEW.from_status IN (
                                'proposed', 'security_approved',
                                'operations_approved', 'ready',
                                'simulated_active'
                            )
                            AND NEW.to_status = 'halted'
                            AND NEW.actor_type = 'system'
                            AND NEW.operator_id = parent_halted_by_id
                            AND NEW.reason_code = parent_halt_reason
                        )
                    )
                    OR (
                        NEW.event_type = 'release_proposed' AND (
                            NOT NEW.payload ?& ARRAY[
                                'candidate_sha256', 'assessment'
                            ]
                            OR NEW.payload - ARRAY[
                                'candidate_sha256', 'assessment'
                            ] IS DISTINCT FROM '{}'::jsonb
                        )
                    )
                    OR (
                        NEW.event_type IN (
                            'security_approval_recorded',
                            'operations_approval_recorded'
                        ) AND (
                            NOT NEW.payload ?& ARRAY[
                                'candidate_sha256', 'approval_id',
                                'approval_sha256', 'approval'
                            ]
                            OR NEW.payload - ARRAY[
                                'candidate_sha256', 'approval_id',
                                'approval_sha256', 'approval'
                            ] IS DISTINCT FROM '{}'::jsonb
                        )
                    )
                    OR (
                        NEW.event_type IN (
                            'simulation_release_ready',
                            'emergency_halt_recorded'
                        ) AND (
                            NOT NEW.payload ? 'candidate_sha256'
                            OR NEW.payload - 'candidate_sha256'
                                IS DISTINCT FROM '{}'::jsonb
                        )
                    )
                    OR (
                        NEW.event_type = 'simulation_activation_recorded'
                        AND (
                            NOT NEW.payload ?& ARRAY[
                                'candidate_sha256', 'human_gate_sha256',
                                'live_activation_allowed',
                                'external_effects_allowed'
                            ]
                            OR NEW.payload - ARRAY[
                                'candidate_sha256', 'human_gate_sha256',
                                'live_activation_allowed',
                                'external_effects_allowed'
                            ] IS DISTINCT FROM '{}'::jsonb
                        )
                    )
                    OR NEW.payload->>'candidate_sha256'
                        IS DISTINCT FROM parent_binding
                    OR NEW.payload - ARRAY[
                        'candidate_sha256', 'assessment', 'approval_id',
                        'approval_sha256', 'approval',
                        'human_gate_sha256', 'live_activation_allowed',
                        'external_effects_allowed'
                    ] IS DISTINCT FROM '{}'::jsonb
                    OR (
                        NEW.payload ? 'assessment'
                        AND NEW.payload->'assessment' IS DISTINCT FROM
                            parent_metadata->'assessment'
                    )
                    OR (
                        NEW.payload ? 'approval' AND (
                            jsonb_typeof(NEW.payload->'approval')
                                IS DISTINCT FROM 'object'
                            OR NOT NEW.payload->'approval' ?& ARRAY[
                                'approval_id', 'candidate_id',
                                'candidate_sha256',
                                'requested_by_operator_id',
                                'approver_operator_id', 'approval_role',
                                'approved_at', 'expires_at', 'decision',
                                'simulation_only',
                                'external_effects_allowed',
                                'live_activation_allowed',
                                'human_activation_required'
                            ]
                            OR NEW.payload->'approval' - ARRAY[
                                'approval_id', 'candidate_id',
                                'candidate_sha256',
                                'requested_by_operator_id',
                                'approver_operator_id', 'approval_role',
                                'approved_at', 'expires_at', 'decision',
                                'simulation_only',
                                'external_effects_allowed',
                                'live_activation_allowed',
                                'human_activation_required'
                            ] IS DISTINCT FROM '{}'::jsonb
                            OR NEW.payload->'approval'->>'candidate_id'
                                IS DISTINCT FROM CAST(NEW.release_id AS TEXT)
                            OR NEW.payload->'approval'->>'candidate_sha256'
                                IS DISTINCT FROM parent_binding
                            OR NEW.payload->'approval'->>'approval_id'
                                IS DISTINCT FROM
                                    NEW.payload->>'approval_id'
                            OR NEW.payload->'approval'->
                                'requested_by_operator_id' IS DISTINCT FROM
                                    parent_metadata->'candidate'->
                                        'requested_by_operator_id'
                            OR NEW.payload->'approval'->>'approver_operator_id'
                                IS DISTINCT FROM CAST(NEW.operator_id AS TEXT)
                            OR NEW.payload->'approval'->>'approval_role' IS NULL
                            OR NEW.payload->'approval'->>'approval_role'
                                NOT IN ('security', 'operations')
                            OR NEW.payload->'approval'->>'decision'
                                IS DISTINCT FROM
                                    'simulation_admission_approved'
                            OR NEW.payload->'approval'->'simulation_only'
                                IS DISTINCT FROM 'true'::jsonb
                            OR NEW.payload->'approval'->
                                'external_effects_allowed'
                                IS DISTINCT FROM 'false'::jsonb
                            OR NEW.payload->'approval'->
                                'live_activation_allowed'
                                IS DISTINCT FROM 'false'::jsonb
                            OR NEW.payload->'approval'->
                                'human_activation_required'
                                IS DISTINCT FROM 'true'::jsonb
                        )
                    )
                    OR (
                        NEW.payload ? 'approval_id' AND (
                            NEW.payload->>'approval_id' IS NULL
                            OR NEW.payload->>'approval_id'
                                !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
                            OR NEW.payload->>'approval_sha256' IS NULL
                            OR NEW.payload->>'approval_sha256'
                                !~ '^[0-9a-f]{64}$'
                            OR NOT NEW.payload ? 'approval'
                        )
                    )
                    OR (
                        NEW.payload ? 'live_activation_allowed'
                        AND NEW.payload->'live_activation_allowed'
                            IS DISTINCT FROM 'false'::jsonb
                    )
                    OR (
                        NEW.payload ? 'external_effects_allowed'
                        AND NEW.payload->'external_effects_allowed'
                            IS DISTINCT FROM 'false'::jsonb
                    )
                    OR (
                        NEW.payload ? 'human_gate_sha256'
                        AND (
                            NEW.payload->>'human_gate_sha256' IS NULL
                            OR NEW.payload->>'human_gate_sha256'
                                !~ '^[0-9a-f]{64}$'
                        )
                    )
                THEN
                    RAISE EXCEPTION
                        'production release event differs from parent scope or sequence';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            SET search_path = pg_catalog, public, pg_temp;
            """,
        ),
        (
            "production_release_event_scope_guard_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_production_release_event_scope_guard'
                      AND tgrelid =
                        'public.rtm_connect_production_release_events'::regclass
                ) THEN
                    CREATE TRIGGER
                        trg_rtm_connect_production_release_event_scope_guard
                    BEFORE INSERT ON
                        public.rtm_connect_production_release_events
                    FOR EACH ROW EXECUTE FUNCTION
                        public.rtm_connect_production_release_event_scope_guard();
                END IF;
            END $$;
            """,
        ),
        (
            "c8_append_only_function",
            """
            CREATE OR REPLACE FUNCTION public.rtm_connect_c8_append_only_guard()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
            END;
            $$ LANGUAGE plpgsql
            SET search_path = pg_catalog, public, pg_temp;
            """,
        ),
        (
            "production_release_events_append_only_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_production_release_events_append_only'
                      AND tgrelid =
                        'public.rtm_connect_production_release_events'::regclass
                ) THEN
                    CREATE TRIGGER
                        trg_rtm_connect_production_release_events_append_only
                    BEFORE UPDATE OR DELETE ON
                        public.rtm_connect_production_release_events
                    FOR EACH ROW EXECUTE FUNCTION
                        public.rtm_connect_c8_append_only_guard();
                END IF;
            END $$;
            """,
        ),
        (
            "dispatch_outbox_scope_guard_function",
            """
            CREATE OR REPLACE FUNCTION
                public.rtm_connect_dispatch_outbox_scope_guard()
            RETURNS trigger AS $$
            DECLARE
                action_capability TEXT;
                action_satellite TEXT;
                action_target_type TEXT;
                action_target_ref TEXT;
                action_payload JSONB;
                action_payload_sha256 TEXT;
                action_document_hashes JSONB;
                action_risk_class TEXT;
                action_requires_dual_control BOOLEAN;
                action_requester_id UUID;
                action_case_id UUID;
                action_correlation_id TEXT;
                action_idempotency_key TEXT;
                action_requested_at TIMESTAMPTZ;
                action_contract_version TEXT;
                action_status TEXT;
                authorization_action_id UUID;
                persisted_authorization_version INTEGER;
                authorized_payload_sha256 TEXT;
                authorization_idempotency_key TEXT;
                authorization_authority_code TEXT;
                authorization_authority_version TEXT;
                authorization_decision TEXT;
                authorization_frozen BOOLEAN;
                authorization_authorized_at TIMESTAMPTZ;
                authorization_revoked_at TIMESTAMPTZ;
                authorization_expires_at TIMESTAMPTZ;
                authorization_evidence_level TEXT;
                authorization_modes JSONB;
                authorization_approvers JSONB;
                authorization_legal_effect BOOLEAN;
                parent_manifest_sha256 TEXT;
                parent_binding_sha256 TEXT;
                parent_release_status TEXT;
                parent_emergency_halt BOOLEAN;
                parent_requester_id UUID;
                parent_security_id UUID;
                parent_operations_id UUID;
                parent_simulation_only BOOLEAN;
                parent_external_effects BOOLEAN;
                parent_live_activation BOOLEAN;
                parent_human_activation BOOLEAN;
                parent_provider_pack BOOLEAN;
                parent_requested_at TIMESTAMPTZ;
                parent_valid_until TIMESTAMPTZ;
                parent_simulated_active_at TIMESTAMPTZ;
                parent_daily_action_limit INTEGER;
                parent_max_concurrency INTEGER;
                parent_metadata JSONB;
                candidate_total_limit INTEGER;
                candidate_payload_limit INTEGER;
                existing_total_count BIGINT;
                existing_daily_count BIGINT;
                existing_active_claims BIGINT;
                guard_now TIMESTAMPTZ;
            BEGIN
                SELECT capability, satellite, target_type, target_ref,
                       payload, payload_sha256, document_hashes, risk_class,
                       requires_dual_control, requested_by_operator_id,
                       case_id, correlation_id, idempotency_key,
                       requested_at, contract_version, status
                  INTO action_capability, action_satellite,
                       action_target_type, action_target_ref, action_payload,
                       action_payload_sha256, action_document_hashes,
                       action_risk_class, action_requires_dual_control,
                       action_requester_id, action_case_id,
                       action_correlation_id, action_idempotency_key,
                       action_requested_at, action_contract_version,
                       action_status
                FROM public.rtm_connect_actions
                WHERE id = NEW.action_id
                FOR SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'dispatch action does not exist';
                END IF;
                SELECT action_id, authorization_version, payload_sha256,
                       idempotency_key, authority_code, authority_version,
                       decision, frozen, authorized_at, revoked_at, expires_at,
                       required_evidence_level, authorized_connector_modes,
                       approved_by_operator_ids, legal_effect_authorized
                  INTO authorization_action_id,
                       persisted_authorization_version,
                       authorized_payload_sha256,
                       authorization_idempotency_key,
                       authorization_authority_code,
                       authorization_authority_version,
                       authorization_decision,
                       authorization_frozen, authorization_authorized_at,
                       authorization_revoked_at,
                       authorization_expires_at,
                       authorization_evidence_level, authorization_modes,
                       authorization_approvers,
                       authorization_legal_effect
                FROM public.rtm_connect_authorizations
                WHERE id = NEW.authorization_id
                FOR SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'dispatch authorization does not exist';
                END IF;
                SELECT manifest_sha256, release_binding_sha256, status,
                       emergency_halt, requested_by_operator_id,
                       security_approved_by_operator_id,
                       operations_approved_by_operator_id,
                       simulation_only, external_effects_allowed,
                       live_activation_allowed, human_activation_required,
                       provider_pack_present, requested_at, valid_until,
                       simulated_active_at,
                       daily_action_limit, max_concurrency, metadata
                  INTO parent_manifest_sha256, parent_binding_sha256,
                       parent_release_status, parent_emergency_halt,
                       parent_requester_id, parent_security_id,
                       parent_operations_id, parent_simulation_only,
                       parent_external_effects, parent_live_activation,
                       parent_human_activation, parent_provider_pack,
                       parent_requested_at, parent_valid_until,
                       parent_simulated_active_at,
                       parent_daily_action_limit, parent_max_concurrency,
                       parent_metadata
                FROM public.rtm_connect_production_releases
                WHERE id = NEW.release_id
                FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'dispatch release does not exist';
                END IF;
                guard_now := clock_timestamp();
                candidate_total_limit := NULLIF(
                    parent_metadata->'candidate'->>
                        'max_simulated_actions_total',
                    ''
                )::INTEGER;
                candidate_payload_limit := NULLIF(
                    parent_metadata->'candidate'->>'max_payload_bytes',
                    ''
                )::INTEGER;
                IF candidate_total_limit IS NULL
                    OR candidate_total_limit <> 1
                    OR candidate_payload_limit IS NULL
                    OR candidate_payload_limit < 1
                    OR octet_length(regexp_replace(
                        CAST(action_payload AS TEXT),
                        '[[:space:]]+', '', 'g'
                    ))
                        > candidate_payload_limit
                THEN
                    RAISE EXCEPTION
                        'dispatch exceeds frozen candidate limits';
                END IF;
                IF TG_OP = 'INSERT' THEN
                    IF (NEW.created_at AT TIME ZONE 'UTC')::DATE
                        IS DISTINCT FROM
                        (guard_now AT TIME ZONE 'UTC')::DATE
                    THEN
                        RAISE EXCEPTION
                            'dispatch creation day must be current UTC day';
                    END IF;
                    SELECT COUNT(*), COUNT(*) FILTER (
                        WHERE (created_at AT TIME ZONE 'UTC')::DATE =
                            (guard_now AT TIME ZONE 'UTC')::DATE
                    )
                      INTO existing_total_count, existing_daily_count
                    FROM public.rtm_connect_dispatch_outbox
                    WHERE release_id = NEW.release_id;
                    IF existing_total_count >= candidate_total_limit
                        OR existing_daily_count
                            >= parent_daily_action_limit
                    THEN
                        RAISE EXCEPTION
                            'dispatch frozen release quota exhausted';
                    END IF;
                END IF;
                IF NEW.status = 'claimed' THEN
                    IF NEW.claimed_at IS NULL
                        OR NEW.claim_expires_at IS NULL
                        OR NEW.claimed_at <
                            guard_now - INTERVAL '5 minutes'
                        OR NEW.claimed_at >
                            guard_now + INTERVAL '1 minute'
                        OR NEW.claim_expires_at <= guard_now
                        OR NEW.claim_expires_at >
                            NEW.claimed_at + INTERVAL '300 seconds'
                        OR NEW.claim_expires_at > authorization_expires_at
                        OR NEW.claim_expires_at > parent_valid_until
                    THEN
                        RAISE EXCEPTION
                            'dispatch claim exceeds frozen lease bounds';
                    END IF;
                    SELECT COUNT(*)
                      INTO existing_active_claims
                    FROM public.rtm_connect_dispatch_outbox
                    WHERE release_id = NEW.release_id
                      AND id <> NEW.id
                      AND status = 'claimed'
                      AND claim_expires_at > guard_now;
                    IF existing_active_claims >= parent_max_concurrency THEN
                        RAISE EXCEPTION
                            'dispatch frozen concurrency exhausted';
                    END IF;
                END IF;
                IF action_capability IS DISTINCT FROM
                        'connect.production.admission.simulate'
                    OR action_satellite IS DISTINCT FROM
                        'rtm.connect.production.admission'
                    OR action_target_type IS DISTINCT FROM
                        'production.admission.candidate'
                    OR action_target_ref IS DISTINCT FROM
                        'synthetic-c8-admission'
                    OR action_risk_class IS DISTINCT FROM
                        'R4_critical_regulated'
                    OR action_requires_dual_control IS DISTINCT FROM TRUE
                    OR action_contract_version IS DISTINCT FROM
                        'rtm_connect_contract_v1_0'
                    OR action_requested_at IS NULL
                    OR action_requested_at > guard_now
                    OR NEW.created_at > guard_now
                    OR NEW.updated_at > guard_now
                    OR NEW.updated_at < NEW.created_at
                    OR (
                        NEW.dry_run_confirmed_at IS NOT NULL
                        AND NEW.dry_run_confirmed_at > guard_now
                    )
                    OR (NEW.unknown_at IS NOT NULL AND NEW.unknown_at > guard_now)
                    OR (
                        NEW.manual_review_at IS NOT NULL
                        AND NEW.manual_review_at > guard_now
                    )
                    OR (
                        NEW.cancelled_at IS NOT NULL
                        AND NEW.cancelled_at > guard_now
                    )
                    OR action_requester_id IS DISTINCT FROM parent_requester_id
                    OR action_case_id IS NOT NULL
                    OR action_correlation_id IS NOT NULL
                    OR jsonb_typeof(action_document_hashes)
                        IS DISTINCT FROM 'array'
                    OR jsonb_array_length(action_document_hashes) <> 0
                    OR action_payload IS DISTINCT FROM jsonb_build_object(
                        'contract_version', 'rtm.connect.c8.admission.v1',
                        'candidate_sha256', parent_binding_sha256,
                        'synthetic_marker', 'RTM_C8_SYNTHETIC_ONLY',
                        'simulation_only', TRUE,
                        'external_effects_allowed', FALSE,
                        'live_activation_allowed', FALSE,
                        'human_activation_required', TRUE
                    )
                    OR action_payload_sha256
                        IS DISTINCT FROM NEW.payload_sha256
                    OR action_payload_sha256
                        IS DISTINCT FROM NEW.request_sha256
                    OR authorization_action_id IS DISTINCT FROM NEW.action_id
                    OR persisted_authorization_version
                        IS DISTINCT FROM NEW.authorization_version
                    OR authorized_payload_sha256
                        IS DISTINCT FROM NEW.payload_sha256
                    OR authorization_idempotency_key
                        IS DISTINCT FROM action_idempotency_key
                    OR authorization_authority_code IS DISTINCT FROM
                        'rtm.core.authorization'
                    OR authorization_authority_version IS DISTINCT FROM
                        'rtm_core_authority_v1'
                    OR authorization_decision IS DISTINCT FROM
                        'approved_frozen'
                    OR authorization_frozen IS DISTINCT FROM TRUE
                    OR authorization_authorized_at IS NULL
                    OR authorization_authorized_at < action_requested_at
                    OR authorization_authorized_at > guard_now
                    OR authorization_expires_at
                        > parent_valid_until
                    OR authorization_evidence_level IS DISTINCT FROM
                        'E4_receipt_verified'
                    OR authorization_modes IS DISTINCT FROM
                        jsonb_build_array('assisted')
                    OR jsonb_array_length(authorization_approvers) <> 2
                    OR parent_security_id IS NULL
                    OR parent_operations_id IS NULL
                    OR NOT authorization_approvers @> jsonb_build_array(
                        CAST(parent_security_id AS TEXT),
                        CAST(parent_operations_id AS TEXT)
                    )
                    OR authorization_legal_effect IS DISTINCT FROM FALSE
                    OR parent_manifest_sha256
                        IS DISTINCT FROM NEW.release_manifest_sha256
                    OR parent_binding_sha256
                        IS DISTINCT FROM NEW.release_binding_sha256
                    OR parent_simulation_only IS DISTINCT FROM TRUE
                    OR parent_external_effects IS DISTINCT FROM FALSE
                    OR parent_live_activation IS DISTINCT FROM FALSE
                    OR parent_human_activation IS DISTINCT FROM TRUE
                    OR parent_provider_pack IS DISTINCT FROM FALSE
                    OR NOT NEW.metadata ?& ARRAY[
                        'intent', 'dispatch_binding_sha256',
                        'production_effect_sha256',
                        'expected_admission_payload',
                        'network_call_performed',
                        'secret_resolution_performed',
                        'blind_retry_allowed'
                    ]
                    OR NEW.metadata - ARRAY[
                        'intent', 'dispatch_binding_sha256',
                        'production_effect_sha256',
                        'expected_admission_payload',
                        'network_call_performed',
                        'secret_resolution_performed',
                        'blind_retry_allowed'
                    ] IS DISTINCT FROM '{}'::jsonb
                    OR jsonb_typeof(NEW.metadata->'intent')
                        IS DISTINCT FROM 'object'
                    OR NOT NEW.metadata->'intent' ?& ARRAY[
                        'intent_id', 'candidate_id', 'action_id',
                        'authorization_id', 'candidate_sha256',
                        'request_sha256', 'idempotency_key', 'status',
                        'created_at', 'reconciliation_required',
                        'simulation_only', 'external_effects_allowed',
                        'network_call_performed',
                        'secret_resolution_performed',
                        'blind_retry_allowed', 'contract_version'
                    ]
                    OR NEW.metadata->'intent' - ARRAY[
                        'intent_id', 'candidate_id', 'action_id',
                        'authorization_id', 'candidate_sha256',
                        'request_sha256', 'idempotency_key', 'status',
                        'created_at', 'reconciliation_required',
                        'simulation_only', 'external_effects_allowed',
                        'network_call_performed',
                        'secret_resolution_performed',
                        'blind_retry_allowed', 'contract_version'
                    ] IS DISTINCT FROM '{}'::jsonb
                    OR NEW.metadata->'intent'->>'intent_id'
                        IS DISTINCT FROM CAST(NEW.id AS TEXT)
                    OR NEW.metadata->'intent'->>'candidate_id'
                        IS DISTINCT FROM CAST(NEW.release_id AS TEXT)
                    OR NEW.metadata->'intent'->>'action_id'
                        IS DISTINCT FROM CAST(NEW.action_id AS TEXT)
                    OR NEW.metadata->'intent'->>'authorization_id'
                        IS DISTINCT FROM CAST(NEW.authorization_id AS TEXT)
                    OR NEW.metadata->'intent'->>'candidate_sha256'
                        IS DISTINCT FROM NEW.release_binding_sha256
                    OR NEW.metadata->'intent'->>'request_sha256'
                        IS DISTINCT FROM NEW.request_sha256
                    OR NEW.metadata->'intent'->>'idempotency_key'
                        IS DISTINCT FROM action_idempotency_key
                    OR NEW.metadata->'intent'->>'status'
                        IS DISTINCT FROM 'prepared'
                    OR CAST(NEW.metadata->'intent'->>'created_at'
                        AS TIMESTAMPTZ) IS DISTINCT FROM NEW.created_at
                    OR NEW.metadata->'intent'->>'contract_version'
                        IS DISTINCT FROM
                            'rtm.connect.c8.simulated_outbox.v1'
                    OR NEW.metadata->'intent'->'reconciliation_required'
                        IS DISTINCT FROM 'false'::jsonb
                    OR NEW.metadata->'intent'->'simulation_only'
                        IS DISTINCT FROM 'true'::jsonb
                    OR NEW.metadata->'intent'->'external_effects_allowed'
                        IS DISTINCT FROM 'false'::jsonb
                    OR NEW.metadata->'intent'->'network_call_performed'
                        IS DISTINCT FROM 'false'::jsonb
                    OR NEW.metadata->'intent'->'secret_resolution_performed'
                        IS DISTINCT FROM 'false'::jsonb
                    OR NEW.metadata->'intent'->'blind_retry_allowed'
                        IS DISTINCT FROM 'false'::jsonb
                    OR NEW.metadata->'expected_admission_payload'
                        IS DISTINCT FROM action_payload
                    OR NEW.metadata->'network_call_performed'
                        IS DISTINCT FROM 'false'::jsonb
                    OR NEW.metadata->'secret_resolution_performed'
                        IS DISTINCT FROM 'false'::jsonb
                    OR NEW.metadata->'blind_retry_allowed'
                        IS DISTINCT FROM 'false'::jsonb
                    OR NEW.metadata->>'dispatch_binding_sha256' IS NULL
                    OR NEW.metadata->>'dispatch_binding_sha256'
                        !~ '^[0-9a-f]{64}$'
                    OR NEW.metadata->>'production_effect_sha256' IS NULL
                    OR NEW.metadata->>'production_effect_sha256'
                        !~ '^[0-9a-f]{64}$'
                    OR NEW.business_command_id IS DISTINCT FROM
                        'rtmc8:command:' ||
                        (NEW.metadata->>'production_effect_sha256')
                    OR NEW.production_effect_key IS DISTINCT FROM
                        'rtmc8:dry-run:' ||
                        (NEW.metadata->>'production_effect_sha256')
                    OR (
                        (TG_OP = 'INSERT' OR NEW.status = 'claimed') AND (
                            action_status IS DISTINCT FROM 'authorized'
                            OR authorization_revoked_at IS NOT NULL
                            OR authorization_expires_at IS NULL
                            OR authorization_expires_at <= guard_now
                            OR parent_release_status <> 'simulated_active'
                            OR parent_emergency_halt IS DISTINCT FROM FALSE
                            OR parent_requested_at IS NULL
                            OR parent_requested_at > guard_now
                            OR parent_valid_until IS NULL
                            OR parent_valid_until <= guard_now
                            OR parent_simulated_active_at IS NULL
                            OR parent_simulated_active_at > guard_now
                        )
                    )
                    OR (
                        TG_OP = 'UPDATE'
                        AND NEW.status = 'dry_run_confirmed'
                        AND (
                            OLD.status <> 'claimed'
                            OR OLD.claim_expires_at IS NULL
                            OR OLD.claim_expires_at <= guard_now
                        )
                    )
                THEN
                    RAISE EXCEPTION
                        'dispatch outbox differs from exact action, authorization, or release scope';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            SET search_path = pg_catalog, public, pg_temp;
            """,
        ),
        (
            "dispatch_outbox_scope_guard_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_dispatch_outbox_scope_guard'
                      AND tgrelid =
                        'public.rtm_connect_dispatch_outbox'::regclass
                ) THEN
                    CREATE TRIGGER
                        trg_rtm_connect_dispatch_outbox_scope_guard
                    BEFORE INSERT OR UPDATE ON
                        public.rtm_connect_dispatch_outbox
                    FOR EACH ROW EXECUTE FUNCTION
                        public.rtm_connect_dispatch_outbox_scope_guard();
                END IF;
            END $$;
            """,
        ),
        (
            "dispatch_outbox_state_guard_function",
            """
            CREATE OR REPLACE FUNCTION
                public.rtm_connect_dispatch_outbox_state_guard()
            RETURNS trigger AS $$
            DECLARE transition_ok BOOLEAN := FALSE;
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NEW.status <> 'prepared'
                        OR NEW.version <> 1
                        OR NEW.claim_fence <> 0
                    THEN
                        RAISE EXCEPTION
                            'dispatch outbox must start prepared, unfenced, at version 1';
                    END IF;
                    IF NEW.dry_run_confirmed_at IS NOT NULL
                        OR NEW.unknown_at IS NOT NULL
                        OR NEW.manual_review_at IS NOT NULL
                        OR NEW.cancelled_at IS NOT NULL
                    THEN
                        RAISE EXCEPTION
                            'prepared dispatch outbox must have no outcome';
                    END IF;
                    RETURN NEW;
                END IF;
                IF NEW.status = OLD.status THEN
                    RAISE EXCEPTION
                        'dispatch outbox update requires a status transition';
                END IF;
                IF NEW.version <> OLD.version + 1 THEN
                    RAISE EXCEPTION
                        'dispatch outbox version must increment exactly once';
                END IF;
                IF (
                    NEW.claim_owner IS DISTINCT FROM OLD.claim_owner
                    OR NEW.claim_token IS DISTINCT FROM OLD.claim_token
                    OR NEW.claim_fence IS DISTINCT FROM OLD.claim_fence
                    OR NEW.claimed_at IS DISTINCT FROM OLD.claimed_at
                    OR NEW.claim_expires_at IS DISTINCT FROM
                        OLD.claim_expires_at
                ) AND NEW.status <> 'claimed' THEN
                    RAISE EXCEPTION
                        'dispatch claim identity may only be set on claim';
                END IF;
                IF NEW.dry_run_confirmed_at
                        IS DISTINCT FROM OLD.dry_run_confirmed_at
                    AND NEW.status <> 'dry_run_confirmed' THEN
                    RAISE EXCEPTION
                        'dry-run timestamp has wrong transition';
                END IF;
                IF NEW.unknown_at IS DISTINCT FROM OLD.unknown_at
                    AND NEW.status <> 'unknown' THEN
                    RAISE EXCEPTION 'UNKNOWN timestamp has wrong transition';
                END IF;
                IF NEW.manual_review_at IS DISTINCT FROM OLD.manual_review_at
                    AND NEW.status <> 'manual_review' THEN
                    RAISE EXCEPTION
                        'manual review timestamp has wrong transition';
                END IF;
                IF NEW.cancelled_at IS DISTINCT FROM OLD.cancelled_at
                    AND NEW.status <> 'cancelled' THEN
                    RAISE EXCEPTION
                        'cancellation timestamp has wrong transition';
                END IF;
                IF OLD.status = 'unknown'
                    AND NEW.status IN ('prepared', 'claimed') THEN
                    RAISE EXCEPTION
                        'UNKNOWN dispatch outcome must never be retried or reclaimed';
                END IF;
                transition_ok := CASE
                    WHEN OLD.status = 'prepared'
                        AND NEW.status IN (
                            'claimed', 'cancelled'
                        ) THEN TRUE
                    WHEN OLD.status = 'claimed'
                        AND NEW.status IN (
                            'dry_run_confirmed', 'unknown',
                            'manual_review', 'cancelled'
                        ) THEN TRUE
                    WHEN OLD.status = 'unknown'
                        AND NEW.status = 'manual_review' THEN TRUE
                    ELSE FALSE
                END;
                IF NOT transition_ok THEN
                    RAISE EXCEPTION
                        'invalid dispatch outbox transition: % -> %',
                        OLD.status, NEW.status;
                END IF;
                IF OLD.status = 'prepared' AND NEW.status = 'claimed'
                    AND NEW.claim_fence <> OLD.claim_fence + 1 THEN
                    RAISE EXCEPTION
                        'dispatch claim fence must increment exactly once';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            SET search_path = pg_catalog, public, pg_temp;
            """,
        ),
        (
            "dispatch_outbox_state_guard_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_dispatch_outbox_state_guard'
                      AND tgrelid =
                        'public.rtm_connect_dispatch_outbox'::regclass
                ) THEN
                    CREATE TRIGGER
                        trg_rtm_connect_dispatch_outbox_state_guard
                    BEFORE INSERT OR UPDATE ON
                        public.rtm_connect_dispatch_outbox
                    FOR EACH ROW EXECUTE FUNCTION
                        public.rtm_connect_dispatch_outbox_state_guard();
                END IF;
            END $$;
            """,
        ),
        (
            "dispatch_outbox_frozen_guard_function",
            """
            CREATE OR REPLACE FUNCTION
                public.rtm_connect_dispatch_outbox_frozen_guard()
            RETURNS trigger AS $$
            BEGIN
                IF NEW.id IS DISTINCT FROM OLD.id
                    OR NEW.action_id IS DISTINCT FROM OLD.action_id
                    OR NEW.authorization_id
                        IS DISTINCT FROM OLD.authorization_id
                    OR NEW.authorization_version
                        IS DISTINCT FROM OLD.authorization_version
                    OR NEW.release_id IS DISTINCT FROM OLD.release_id
                    OR NEW.business_command_id
                        IS DISTINCT FROM OLD.business_command_id
                    OR NEW.production_effect_key
                        IS DISTINCT FROM OLD.production_effect_key
                    OR NEW.payload_sha256 IS DISTINCT FROM OLD.payload_sha256
                    OR NEW.request_sha256 IS DISTINCT FROM OLD.request_sha256
                    OR NEW.release_manifest_sha256
                        IS DISTINCT FROM OLD.release_manifest_sha256
                    OR NEW.release_binding_sha256
                        IS DISTINCT FROM OLD.release_binding_sha256
                    OR NEW.dry_run_only IS DISTINCT FROM OLD.dry_run_only
                    OR NEW.network_allowed IS DISTINCT FROM OLD.network_allowed
                    OR NEW.provider_contacted
                        IS DISTINCT FROM OLD.provider_contacted
                    OR NEW.external_effects_allowed
                        IS DISTINCT FROM OLD.external_effects_allowed
                    OR NEW.metadata IS DISTINCT FROM OLD.metadata
                    OR NEW.created_at IS DISTINCT FROM OLD.created_at
                THEN
                    RAISE EXCEPTION
                        'dispatch identity, hashes, release, and inert flags are frozen';
                END IF;
                IF OLD.claim_token IS NOT NULL AND (
                    NEW.claim_owner IS DISTINCT FROM OLD.claim_owner
                    OR NEW.claim_token IS DISTINCT FROM OLD.claim_token
                    OR NEW.claim_fence IS DISTINCT FROM OLD.claim_fence
                    OR NEW.claimed_at IS DISTINCT FROM OLD.claimed_at
                    OR NEW.claim_expires_at IS DISTINCT FROM OLD.claim_expires_at
                ) THEN
                    RAISE EXCEPTION
                        'dispatch claim identity and fence are write-once';
                END IF;
                IF OLD.dry_run_confirmed_at IS NOT NULL
                    AND NEW.dry_run_confirmed_at
                        IS DISTINCT FROM OLD.dry_run_confirmed_at THEN
                    RAISE EXCEPTION 'dry-run confirmation is write-once';
                END IF;
                IF OLD.unknown_at IS NOT NULL
                    AND NEW.unknown_at IS DISTINCT FROM OLD.unknown_at THEN
                    RAISE EXCEPTION 'dispatch UNKNOWN timestamp is write-once';
                END IF;
                IF OLD.manual_review_at IS NOT NULL
                    AND NEW.manual_review_at
                        IS DISTINCT FROM OLD.manual_review_at THEN
                    RAISE EXCEPTION 'manual review timestamp is write-once';
                END IF;
                IF OLD.cancelled_at IS NOT NULL
                    AND NEW.cancelled_at IS DISTINCT FROM OLD.cancelled_at THEN
                    RAISE EXCEPTION 'dispatch cancellation is write-once';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            SET search_path = pg_catalog, public, pg_temp;
            """,
        ),
        (
            "dispatch_outbox_frozen_guard_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_dispatch_outbox_frozen_guard'
                      AND tgrelid =
                        'public.rtm_connect_dispatch_outbox'::regclass
                ) THEN
                    CREATE TRIGGER
                        trg_rtm_connect_dispatch_outbox_frozen_guard
                    BEFORE UPDATE ON public.rtm_connect_dispatch_outbox
                    FOR EACH ROW EXECUTE FUNCTION
                        public.rtm_connect_dispatch_outbox_frozen_guard();
                END IF;
            END $$;
            """,
        ),
        (
            "dispatch_outbox_delete_guard_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_dispatch_outbox_delete_guard'
                      AND tgrelid =
                        'public.rtm_connect_dispatch_outbox'::regclass
                ) THEN
                    CREATE TRIGGER
                        trg_rtm_connect_dispatch_outbox_delete_guard
                    BEFORE DELETE ON public.rtm_connect_dispatch_outbox
                    FOR EACH ROW EXECUTE FUNCTION
                        public.rtm_connect_c8_delete_guard();
                END IF;
            END $$;
            """,
        ),
        (
            "dispatch_event_scope_guard_function",
            """
            CREATE OR REPLACE FUNCTION
                public.rtm_connect_dispatch_event_scope_guard()
            RETURNS trigger AS $$
            DECLARE
                parent_action_id UUID;
                parent_authorization_id UUID;
                parent_release_id UUID;
                parent_binding_sha256 TEXT;
                parent_status TEXT;
                parent_created_at TIMESTAMPTZ;
                parent_metadata JSONB;
                parent_claim_owner TEXT;
                parent_claim_fence BIGINT;
                parent_claimed_at TIMESTAMPTZ;
                parent_claim_expires_at TIMESTAMPTZ;
                parent_version INTEGER;
                expected_sequence INTEGER;
                previous_status TEXT;
                previous_created_at TIMESTAMPTZ;
                guard_now TIMESTAMPTZ;
            BEGIN
                SELECT action_id, authorization_id, release_id,
                       release_binding_sha256, status, created_at, metadata,
                       claim_owner, claim_fence, claimed_at, claim_expires_at,
                       version
                  INTO parent_action_id, parent_authorization_id,
                       parent_release_id, parent_binding_sha256, parent_status,
                       parent_created_at, parent_metadata,
                       parent_claim_owner, parent_claim_fence,
                       parent_claimed_at, parent_claim_expires_at,
                       parent_version
                FROM public.rtm_connect_dispatch_outbox
                WHERE id = NEW.outbox_id
                FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'dispatch event parent outbox missing';
                END IF;
                SELECT COALESCE(MAX(sequence_number), 0) + 1
                  INTO expected_sequence
                FROM public.rtm_connect_dispatch_events
                WHERE outbox_id = NEW.outbox_id;
                guard_now := clock_timestamp();
                SELECT to_status, created_at
                  INTO previous_status, previous_created_at
                FROM public.rtm_connect_dispatch_events
                WHERE outbox_id = NEW.outbox_id
                ORDER BY sequence_number DESC
                LIMIT 1;
                IF NEW.action_id IS DISTINCT FROM parent_action_id
                    OR NEW.authorization_id
                        IS DISTINCT FROM parent_authorization_id
                    OR NEW.release_id IS DISTINCT FROM parent_release_id
                    OR NEW.release_binding_sha256
                        IS DISTINCT FROM parent_binding_sha256
                    OR NEW.to_status IS DISTINCT FROM parent_status
                    OR NEW.sequence_number IS DISTINCT FROM expected_sequence
                    OR NEW.sequence_number IS DISTINCT FROM parent_version
                    OR (expected_sequence = 1 AND NEW.from_status IS NOT NULL)
                    OR (expected_sequence > 1 AND
                        NEW.from_status IS DISTINCT FROM previous_status)
                    OR NEW.created_at > guard_now
                    OR NEW.created_at < parent_created_at
                    OR (
                        previous_created_at IS NOT NULL
                        AND NEW.created_at < previous_created_at
                    )
                    OR NOT (
                        (
                            NEW.event_type = 'dispatch_dry_run_prepared'
                            AND NEW.from_status IS NULL
                            AND NEW.to_status = 'prepared'
                            AND NEW.actor_type = 'connect'
                            AND NEW.operator_id IS NULL
                            AND NEW.reason_code = 'simulation_only_recorded'
                        ) OR (
                            NEW.event_type = 'dispatch_dry_run_claimed'
                            AND NEW.from_status = 'prepared'
                            AND NEW.to_status = 'claimed'
                            AND NEW.actor_type = 'connect'
                            AND NEW.operator_id IS NULL
                            AND NEW.reason_code = 'simulation_claim_fenced'
                        ) OR (
                            NEW.event_type = 'dispatch_dry_run_confirmed'
                            AND NEW.from_status = 'claimed'
                            AND NEW.to_status = 'dry_run_confirmed'
                            AND NEW.actor_type = 'connect'
                            AND NEW.operator_id IS NULL
                            AND NEW.reason_code =
                                'simulation_completed_without_effect'
                        ) OR (
                            NEW.event_type = 'dispatch_simulation_unknown'
                            AND NEW.from_status = 'claimed'
                            AND NEW.to_status = 'unknown'
                            AND NEW.actor_type = 'connect'
                            AND NEW.operator_id IS NULL
                            AND NEW.reason_code =
                                'manual_reconciliation_required'
                        ) OR (
                            NEW.event_type =
                                'dispatch_manual_review_recorded'
                            AND NEW.from_status = 'unknown'
                            AND NEW.to_status = 'manual_review'
                            AND NEW.actor_type = 'operator'
                            AND NEW.operator_id IS NOT NULL
                        )
                    )
                    OR (
                        NEW.event_type = 'dispatch_dry_run_prepared' AND (
                            NOT NEW.payload ?& ARRAY[
                                'dispatch_binding_sha256',
                                'production_effect_sha256', 'dry_run_only',
                                'network_allowed', 'provider_contacted',
                                'external_effects_allowed'
                            ]
                            OR NEW.payload - ARRAY[
                                'dispatch_binding_sha256',
                                'production_effect_sha256', 'dry_run_only',
                                'network_allowed', 'provider_contacted',
                                'external_effects_allowed'
                            ] IS DISTINCT FROM '{}'::jsonb
                            OR NEW.payload->>'dispatch_binding_sha256'
                                IS DISTINCT FROM
                                    parent_metadata->>
                                        'dispatch_binding_sha256'
                            OR NEW.payload->>'production_effect_sha256'
                                IS DISTINCT FROM
                                    parent_metadata->>
                                        'production_effect_sha256'
                        )
                    )
                    OR (
                        NEW.event_type = 'dispatch_dry_run_claimed' AND (
                            NOT NEW.payload ?& ARRAY[
                                'claim_owner', 'claim_token_sha256',
                                'claim_fence', 'claim_ttl_seconds',
                                'claim_expires_at'
                            ]
                            OR NEW.payload - ARRAY[
                                'claim_owner', 'claim_token_sha256',
                                'claim_fence', 'claim_ttl_seconds',
                                'claim_expires_at'
                            ] IS DISTINCT FROM '{}'::jsonb
                            OR NEW.payload->>'claim_owner'
                                IS DISTINCT FROM parent_claim_owner
                            OR CAST(NEW.payload->>'claim_fence' AS BIGINT)
                                IS DISTINCT FROM parent_claim_fence
                            OR NEW.payload->>'claim_ttl_seconds' IS NULL
                            OR CAST(NEW.payload->>'claim_ttl_seconds' AS INTEGER)
                                NOT BETWEEN 1 AND 300
                            OR (parent_claim_expires_at - parent_claimed_at)
                                IS DISTINCT FROM
                                    CAST(NEW.payload->>'claim_ttl_seconds'
                                        AS INTEGER) * INTERVAL '1 second'
                            OR CAST(NEW.payload->>'claim_expires_at'
                                AS TIMESTAMPTZ) IS DISTINCT FROM
                                    parent_claim_expires_at
                            OR parent_claimed_at IS NULL
                            OR parent_claim_expires_at <= parent_claimed_at
                        )
                    )
                    OR (
                        NEW.event_type IN (
                            'dispatch_dry_run_confirmed',
                            'dispatch_simulation_unknown'
                        ) AND (
                            NOT NEW.payload ?& ARRAY[
                                'claim_token_sha256', 'claim_fence',
                                'network_call_performed',
                                'external_effects_allowed',
                                'reconciliation_required'
                            ]
                            OR NEW.payload - ARRAY[
                                'claim_token_sha256', 'claim_fence',
                                'network_call_performed',
                                'external_effects_allowed',
                                'reconciliation_required'
                            ] IS DISTINCT FROM '{}'::jsonb
                            OR CAST(NEW.payload->>'claim_fence' AS BIGINT)
                                IS DISTINCT FROM parent_claim_fence
                            OR (
                                NEW.event_type =
                                    'dispatch_dry_run_confirmed'
                                AND NEW.payload->'reconciliation_required'
                                    IS DISTINCT FROM 'false'::jsonb
                            )
                            OR (
                                NEW.event_type =
                                    'dispatch_simulation_unknown'
                                AND NEW.payload->'reconciliation_required'
                                    IS DISTINCT FROM 'true'::jsonb
                            )
                        )
                    )
                    OR (
                        NEW.event_type =
                            'dispatch_manual_review_recorded' AND (
                            NOT NEW.payload ?& ARRAY[
                                'claim_token_sha256', 'claim_fence',
                                'reconciliation_required',
                                'blind_retry_allowed'
                            ]
                            OR NEW.payload - ARRAY[
                                'claim_token_sha256', 'claim_fence',
                                'reconciliation_required',
                                'blind_retry_allowed'
                            ] IS DISTINCT FROM '{}'::jsonb
                            OR CAST(NEW.payload->>'claim_fence' AS BIGINT)
                                IS DISTINCT FROM parent_claim_fence
                            OR NEW.payload->'reconciliation_required'
                                IS DISTINCT FROM 'true'::jsonb
                            OR NEW.payload->'blind_retry_allowed'
                                IS DISTINCT FROM 'false'::jsonb
                        )
                    )
                    OR NEW.payload - ARRAY[
                        'dispatch_binding_sha256',
                        'production_effect_sha256', 'dry_run_only',
                        'network_allowed', 'provider_contacted',
                        'external_effects_allowed', 'claim_owner',
                        'claim_token_sha256', 'claim_fence',
                        'claim_ttl_seconds', 'claim_expires_at',
                        'network_call_performed',
                        'reconciliation_required', 'blind_retry_allowed'
                    ] IS DISTINCT FROM '{}'::jsonb
                    OR (
                        NEW.payload ? 'dispatch_binding_sha256'
                        AND (
                            NEW.payload->>'dispatch_binding_sha256' IS NULL
                            OR NEW.payload->>'dispatch_binding_sha256'
                                !~ '^[0-9a-f]{64}$'
                        )
                    )
                    OR (
                        NEW.payload ? 'production_effect_sha256'
                        AND (
                            NEW.payload->>'production_effect_sha256' IS NULL
                            OR NEW.payload->>'production_effect_sha256'
                                !~ '^[0-9a-f]{64}$'
                        )
                    )
                    OR (
                        NEW.payload ? 'claim_token_sha256'
                        AND (
                            NEW.payload->>'claim_token_sha256' IS NULL
                            OR NEW.payload->>'claim_token_sha256'
                                !~ '^[0-9a-f]{64}$'
                        )
                    )
                    OR (
                        NEW.payload ? 'claim_owner'
                        AND (
                            NEW.payload->>'claim_owner' IS NULL
                            OR NEW.payload->>'claim_owner'
                                !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$'
                        )
                    )
                    OR (
                        NEW.payload ? 'dry_run_only'
                        AND NEW.payload->'dry_run_only'
                            IS DISTINCT FROM 'true'::jsonb
                    )
                    OR (
                        NEW.payload ? 'network_allowed'
                        AND NEW.payload->'network_allowed'
                            IS DISTINCT FROM 'false'::jsonb
                    )
                    OR (
                        NEW.payload ? 'provider_contacted'
                        AND NEW.payload->'provider_contacted'
                            IS DISTINCT FROM 'false'::jsonb
                    )
                    OR (
                        NEW.payload ? 'external_effects_allowed'
                        AND NEW.payload->'external_effects_allowed'
                            IS DISTINCT FROM 'false'::jsonb
                    )
                    OR (
                        NEW.payload ? 'network_call_performed'
                        AND NEW.payload->'network_call_performed'
                            IS DISTINCT FROM 'false'::jsonb
                    )
                    OR (
                        NEW.payload ? 'blind_retry_allowed'
                        AND NEW.payload->'blind_retry_allowed'
                            IS DISTINCT FROM 'false'::jsonb
                    )
                THEN
                    RAISE EXCEPTION
                        'dispatch event differs from parent scope or sequence';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            SET search_path = pg_catalog, public, pg_temp;
            """,
        ),
        (
            "dispatch_event_scope_guard_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_dispatch_event_scope_guard'
                      AND tgrelid =
                        'public.rtm_connect_dispatch_events'::regclass
                ) THEN
                    CREATE TRIGGER trg_rtm_connect_dispatch_event_scope_guard
                    BEFORE INSERT ON public.rtm_connect_dispatch_events
                    FOR EACH ROW EXECUTE FUNCTION
                        public.rtm_connect_dispatch_event_scope_guard();
                END IF;
            END $$;
            """,
        ),
        (
            "dispatch_events_append_only_trigger",
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_trigger
                    WHERE tgname =
                        'trg_rtm_connect_dispatch_events_append_only'
                      AND tgrelid =
                        'public.rtm_connect_dispatch_events'::regclass
                ) THEN
                    CREATE TRIGGER
                        trg_rtm_connect_dispatch_events_append_only
                    BEFORE UPDATE OR DELETE ON
                        public.rtm_connect_dispatch_events
                    FOR EACH ROW EXECUTE FUNCTION
                        public.rtm_connect_c8_append_only_guard();
                END IF;
            END $$;
            """,
        ),
    ]


__all__ = [
    "RTM_CONNECT_C8_PRODUCTION_SCHEMA_VERSION",
    "RTM_CONNECT_C8_SCHEMA_VERSION",
    "PRODUCTION_RELEASE_STATUSES",
    "DISPATCH_OUTBOX_STATUSES",
    "PRODUCTION_OUTBOX_STATUSES",
    "CONNECT_C8_REQUIRED_COLUMNS",
    "CONNECT_C8_REQUIRED_INDEXES",
    "CONNECT_C8_REQUIRED_TRIGGERS",
    "CONNECT_C8_REQUIRED_CONSTRAINTS",
    "connect_c8_production_ddl",
]
