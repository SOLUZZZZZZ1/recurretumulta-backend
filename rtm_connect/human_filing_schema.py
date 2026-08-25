"""DDL PostgreSQL aditivo e idempotente de RTM CONNECT A1-S.

Las tablas llevan namespace A1-S para que el ensayo aislado no se convierta
por accidente en un modelo multitenant general. No se insertan fixtures ni se
registra un conector persistente desde este modulo.
"""

from __future__ import annotations


RTM_CONNECT_A1S_SCHEMA_VERSION = "rtm_connect_a1s_human_filing_schema_v1_0"

HUMAN_FILING_TASK_STATUSES = (
    "prepared", "assigned", "reviewing", "ready_for_release", "released",
    "in_progress", "awaiting_receipt", "outcome_unknown", "reconciling",
    "receipt_submitted", "verified", "completed", "manual_review",
    "permanent_failed",
)
HUMAN_FILING_APPROVAL_TYPES = ("release", "verification_preapproval")
HUMAN_FILING_ARTIFACT_KINDS = (
    "authority_snapshot", "representation_evidence", "filing_package",
    "human_review_attestation", "release_attestation",
    "verification_preapproval_attestation", "synthetic_submission_report",
    "synthetic_receipt", "verification_attestation",
    "reconciliation_attestation",
)

CONNECT_A1S_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "rtm_connect_a1s_tenants": {
        "id", "tenant_code", "display_name", "status", "synthetic_only",
        "metadata", "created_at", "updated_at",
    },
    "rtm_connect_a1s_memberships": {
        "id", "tenant_id", "principal_id", "operator_id", "role", "status",
        "synthetic_only", "granted_by_operator_id", "granted_at",
        "revoked_by_operator_id", "revoked_at", "version", "metadata",
    },
    "rtm_connect_a1s_case_bindings": {
        "id", "tenant_id", "case_id", "binding_code", "status",
        "synthetic_only", "case_snapshot_sha256", "bound_by_operator_id",
        "bound_at", "revoked_by_operator_id", "revoked_at", "version",
        "metadata",
    },
    "rtm_connect_a1s_representation_evidence": {
        "id", "tenant_id", "case_binding_id", "representation_code", "kind",
        "subject_ref_sha256", "evidence_sha256", "canonical_evidence",
        "status", "synthetic_only", "recorded_by_membership_id",
        "recorded_by_principal_id", "recorded_by_operator_id", "valid_from",
        "expires_at", "revoked_by_operator_id", "revoked_at", "version",
        "created_at",
    },
    "rtm_connect_a1s_human_tasks": {
        "id", "tenant_id", "case_binding_id", "representation_evidence_id",
        "action_id", "attempt_id", "connector_id", "authorization_id",
        "authorization_version", "task_code", "status",
        "requester_membership_id", "requester_principal_id",
        "requester_operator_id", "assignee_membership_id",
        "assignee_principal_id", "assignee_operator_id",
        "assigned_by_operator_id", "release_membership_id",
        "release_principal_id", "release_operator_id",
        "verified_by_membership_id", "verified_by_principal_id",
        "verified_by_operator_id", "due_at", "assigned_at", "reviewed_at",
        "ready_at", "released_at", "started_at", "awaiting_receipt_at",
        "unknown_at", "reconciling_at", "receipt_submitted_at", "verified_at",
        "completed_at", "package_manifest", "package_sha256",
        "review_attestation_sha256", "release_attestation_sha256",
        "verification_attestation_sha256", "external_reference", "version",
        "status_version", "metadata", "created_at", "updated_at",
    },
    "rtm_connect_a1s_artifacts": {
        "id", "tenant_id", "task_id", "artifact_code", "kind", "media_type",
        "sha256", "canonical_payload", "submitted_by_membership_id",
        "submitted_by_principal_id", "submitted_by_operator_id",
        "verified_by_membership_id", "verified_by_principal_id",
        "verified_by_operator_id", "verified_at", "synthetic_only",
        "storage_backend", "supersedes_artifact_id", "version", "created_at",
    },
    "rtm_connect_a1s_approvals": {
        "id", "tenant_id", "task_id", "approval_type", "decision",
        "membership_id", "principal_id", "operator_id", "attestation_sha256",
        "artifact_id", "approved_at", "synthetic_only", "created_at",
    },
    "rtm_connect_a1s_events": {
        "id", "tenant_id", "task_id", "action_id", "attempt_id",
        "sequence_number", "event_type", "actor_type", "membership_id",
        "principal_id", "operator_id", "from_status", "to_status",
        "reason_code", "payload_sha256", "payload", "synthetic_only",
        "created_at",
    },
    "rtm_connect_a1s_idempotency": {
        "id", "tenant_id", "idempotency_key", "scope", "request_sha256",
        "response_sha256", "task_id", "action_id", "status",
        "claimed_by_membership_id", "claimed_by_principal_id",
        "claimed_by_operator_id", "replay_count", "created_at", "completed_at",
        "expires_at", "metadata",
    },
}

CONNECT_A1S_REQUIRED_INDEXES = {
    "uq_rtm_connect_a1s_tenant_code",
    "uq_rtm_connect_a1s_membership_principal",
    "uq_rtm_connect_a1s_membership_operator",
    "uq_rtm_connect_a1s_membership_identity",
    "uq_rtm_connect_a1s_case_binding_code",
    "uq_rtm_connect_a1s_active_case_binding_case_id",
    "uq_rtm_connect_a1s_representation_code",
    "idx_rtm_connect_a1s_representation_binding",
    "uq_rtm_connect_a1s_task_action", "uq_rtm_connect_a1s_task_attempt",
    "uq_rtm_connect_a1s_task_code", "idx_rtm_connect_a1s_task_queue",
    "idx_rtm_connect_a1s_task_case_binding",
    "uq_rtm_connect_a1s_artifact_code",
    "uq_rtm_connect_a1s_artifact_content",
    "idx_rtm_connect_a1s_artifact_task_kind",
    "uq_rtm_connect_a1s_approval_type",
    "uq_rtm_connect_a1s_approval_principal",
    "idx_rtm_connect_a1s_approval_task",
    "uq_rtm_connect_a1s_event_sequence", "idx_rtm_connect_a1s_event_action",
    "idx_rtm_connect_a1s_event_principal",
    "uq_rtm_connect_a1s_idempotency_key",
    "idx_rtm_connect_a1s_idempotency_expiry",
}

CONNECT_A1S_REQUIRED_TRIGGERS = {
    "trg_rtm_connect_a1s_tenant_frozen",
    "trg_rtm_connect_a1s_membership_guard",
    "trg_rtm_connect_a1s_case_binding_frozen",
    "trg_rtm_connect_a1s_representation_frozen",
    "trg_rtm_connect_a1s_task_guard",
    "trg_rtm_connect_a1s_artifact_scope_guard",
    "trg_rtm_connect_a1s_artifact_append_only",
    "trg_rtm_connect_a1s_approval_scope_guard",
    "trg_rtm_connect_a1s_approval_append_only",
    "trg_rtm_connect_a1s_event_scope_guard",
    "trg_rtm_connect_a1s_event_append_only",
    "trg_rtm_connect_a1s_idempotency_guard",
}

CONNECT_A1S_REQUIRED_CONSTRAINTS = {
    "ck_rtm_connect_a1s_tenant_code", "ck_rtm_connect_a1s_tenant_status",
    "ck_rtm_connect_a1s_tenant_synthetic",
    "ck_rtm_connect_a1s_membership_role",
    "ck_rtm_connect_a1s_membership_status",
    "ck_rtm_connect_a1s_membership_revocation",
    "ck_rtm_connect_a1s_binding_code", "ck_rtm_connect_a1s_binding_status",
    "ck_rtm_connect_a1s_binding_hash",
    "ck_rtm_connect_a1s_representation_code",
    "ck_rtm_connect_a1s_representation_kind",
    "ck_rtm_connect_a1s_representation_hashes",
    "ck_rtm_connect_a1s_representation_vigency",
    "ck_rtm_connect_a1s_task_code", "ck_rtm_connect_a1s_task_status",
    "ck_rtm_connect_a1s_task_package_hash",
    "ck_rtm_connect_a1s_task_package_scope",
    "ck_rtm_connect_a1s_task_assignment", "ck_rtm_connect_a1s_task_review",
    "ck_rtm_connect_a1s_task_release", "ck_rtm_connect_a1s_task_started",
    "ck_rtm_connect_a1s_task_unknown", "ck_rtm_connect_a1s_task_receipt",
    "ck_rtm_connect_a1s_task_verified", "ck_rtm_connect_a1s_task_completed",
    "ck_rtm_connect_a1s_task_separation",
    "ck_rtm_connect_a1s_artifact_kind", "ck_rtm_connect_a1s_artifact_hash",
    "ck_rtm_connect_a1s_artifact_storage",
    "ck_rtm_connect_a1s_approval_type",
    "ck_rtm_connect_a1s_approval_decision",
    "ck_rtm_connect_a1s_event_sequence", "ck_rtm_connect_a1s_event_actor",
    "ck_rtm_connect_a1s_event_hash",
    "ck_rtm_connect_a1s_idempotency_key",
    "ck_rtm_connect_a1s_idempotency_status",
    "ck_rtm_connect_a1s_idempotency_completion",
}


def _a1s_table_ddl() -> list[tuple[str, str]]:
    return [
        ("a1s_tenants", """
            CREATE TABLE IF NOT EXISTS rtm_connect_a1s_tenants (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_code TEXT NOT NULL,
                display_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                synthetic_only BOOLEAN NOT NULL DEFAULT TRUE,
                metadata JSONB NOT NULL DEFAULT
                    '{"synthetic_marker":"RTM_A1S_SYNTHETIC_ONLY",\
                      "synthetic_only": true}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_connect_a1s_tenant_code CHECK (
                    tenant_code ~ '^a1s-synthetic-[a-z0-9-]{3,48}$'
                ),
                CONSTRAINT ck_rtm_connect_a1s_tenant_status CHECK (
                    status IN ('active', 'suspended', 'disabled')
                ),
                CONSTRAINT ck_rtm_connect_a1s_tenant_synthetic CHECK (
                    synthetic_only = TRUE
                    AND jsonb_typeof(metadata) = 'object'
                    AND metadata @> '{"synthetic_marker":\
                        "RTM_A1S_SYNTHETIC_ONLY",\
                        "synthetic_only": true}'::jsonb
                ),
                CONSTRAINT ck_rtm_connect_a1s_tenant_display_name CHECK (
                    length(display_name) BETWEEN 3 AND 96
                )
            );
        """),
        ("a1s_tenant_code_index", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_a1s_tenant_code
            ON rtm_connect_a1s_tenants(tenant_code);
        """),
        ("a1s_memberships", """
            CREATE TABLE IF NOT EXISTS rtm_connect_a1s_memberships (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES rtm_connect_a1s_tenants(id)
                    ON DELETE RESTRICT,
                principal_id UUID NOT NULL,
                operator_id UUID NOT NULL REFERENCES rtm_operators(id)
                    ON DELETE RESTRICT,
                role TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                synthetic_only BOOLEAN NOT NULL DEFAULT TRUE,
                granted_by_operator_id UUID NOT NULL REFERENCES rtm_operators(id)
                    ON DELETE RESTRICT,
                granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                revoked_by_operator_id UUID REFERENCES rtm_operators(id)
                    ON DELETE RESTRICT,
                revoked_at TIMESTAMPTZ,
                version INTEGER NOT NULL DEFAULT 1,
                metadata JSONB NOT NULL DEFAULT
                    '{"synthetic_marker":"RTM_A1S_SYNTHETIC_ONLY",\
                      "synthetic_only": true}'::jsonb,
                CONSTRAINT ck_rtm_connect_a1s_membership_role CHECK (
                    role IN (
                        'requester', 'executor', 'releaser', 'verifier',
                        'supervisor'
                    )
                ),
                CONSTRAINT ck_rtm_connect_a1s_membership_status CHECK (
                    status IN ('active', 'revoked')
                ),
                CONSTRAINT ck_rtm_connect_a1s_membership_revocation CHECK (
                    (status = 'active' AND revoked_at IS NULL
                        AND revoked_by_operator_id IS NULL)
                    OR (status = 'revoked' AND revoked_at IS NOT NULL
                        AND revoked_by_operator_id IS NOT NULL)
                ),
                CONSTRAINT ck_rtm_connect_a1s_membership_synthetic CHECK (
                    synthetic_only = TRUE
                    AND jsonb_typeof(metadata) = 'object'
                    AND metadata @> '{"synthetic_marker":\
                        "RTM_A1S_SYNTHETIC_ONLY",\
                        "synthetic_only": true}'::jsonb
                ),
                CONSTRAINT ck_rtm_connect_a1s_membership_version CHECK (
                    version > 0
                )
            );
        """),
        ("a1s_membership_principal_index", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_a1s_membership_principal
            ON rtm_connect_a1s_memberships(tenant_id, principal_id);
        """),
        ("a1s_membership_operator_index", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_a1s_membership_operator
            ON rtm_connect_a1s_memberships(tenant_id, operator_id);
        """),
        ("a1s_membership_identity_index", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_a1s_membership_identity
            ON rtm_connect_a1s_memberships(
                id, tenant_id, principal_id, operator_id
            );
        """),
        ("a1s_case_bindings", """
            CREATE TABLE IF NOT EXISTS rtm_connect_a1s_case_bindings (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES rtm_connect_a1s_tenants(id)
                    ON DELETE RESTRICT,
                case_id UUID NOT NULL REFERENCES cases(id) ON DELETE RESTRICT,
                binding_code TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                synthetic_only BOOLEAN NOT NULL DEFAULT TRUE,
                case_snapshot_sha256 TEXT NOT NULL,
                bound_by_operator_id UUID NOT NULL REFERENCES rtm_operators(id)
                    ON DELETE RESTRICT,
                bound_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                revoked_by_operator_id UUID REFERENCES rtm_operators(id)
                    ON DELETE RESTRICT,
                revoked_at TIMESTAMPTZ,
                version INTEGER NOT NULL DEFAULT 1,
                metadata JSONB NOT NULL DEFAULT
                    '{"synthetic_marker":"RTM_A1S_SYNTHETIC_ONLY",\
                      "synthetic_only": true}'::jsonb,
                CONSTRAINT ck_rtm_connect_a1s_binding_code CHECK (
                    binding_code ~ '^rtm-a1s-binding-[0-9a-f]{24}$'
                ),
                CONSTRAINT ck_rtm_connect_a1s_binding_status CHECK (
                    status IN ('active', 'revoked')
                    AND ((status = 'active' AND revoked_at IS NULL
                        AND revoked_by_operator_id IS NULL)
                    OR (status = 'revoked' AND revoked_at IS NOT NULL
                        AND revoked_by_operator_id IS NOT NULL))
                ),
                CONSTRAINT ck_rtm_connect_a1s_binding_hash CHECK (
                    case_snapshot_sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_connect_a1s_binding_synthetic CHECK (
                    synthetic_only = TRUE
                    AND jsonb_typeof(metadata) = 'object'
                    AND metadata @> '{"synthetic_marker":\
                        "RTM_A1S_SYNTHETIC_ONLY",\
                        "synthetic_only": true, "test_mode": true}'::jsonb
                ),
                CONSTRAINT ck_rtm_connect_a1s_binding_version CHECK (
                    version > 0
                )
            );
        """),
        ("a1s_binding_code_index", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_a1s_case_binding_code
            ON rtm_connect_a1s_case_bindings(binding_code);
        """),
        ("a1s_active_case_binding_index", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_a1s_active_case_binding_case_id
            ON rtm_connect_a1s_case_bindings(case_id)
            WHERE status = 'active';
        """),
        ("a1s_representation_evidence", """
            CREATE TABLE IF NOT EXISTS rtm_connect_a1s_representation_evidence (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES rtm_connect_a1s_tenants(id)
                    ON DELETE RESTRICT,
                case_binding_id UUID NOT NULL
                    REFERENCES rtm_connect_a1s_case_bindings(id)
                    ON DELETE RESTRICT,
                representation_code TEXT NOT NULL,
                kind TEXT NOT NULL,
                subject_ref_sha256 TEXT NOT NULL,
                evidence_sha256 TEXT NOT NULL,
                canonical_evidence JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                synthetic_only BOOLEAN NOT NULL DEFAULT TRUE,
                recorded_by_membership_id UUID NOT NULL,
                recorded_by_principal_id UUID NOT NULL,
                recorded_by_operator_id UUID NOT NULL,
                valid_from TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                revoked_by_operator_id UUID REFERENCES rtm_operators(id)
                    ON DELETE RESTRICT,
                revoked_at TIMESTAMPTZ,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT fk_rtm_connect_a1s_representation_recorder
                    FOREIGN KEY (
                        recorded_by_membership_id, tenant_id,
                        recorded_by_principal_id, recorded_by_operator_id
                    ) REFERENCES rtm_connect_a1s_memberships(
                        id, tenant_id, principal_id, operator_id
                    ) ON DELETE RESTRICT,
                CONSTRAINT ck_rtm_connect_a1s_representation_code CHECK (
                    representation_code ~
                        '^rtm-a1s-representation-[0-9a-f]{24}$'
                ),
                CONSTRAINT ck_rtm_connect_a1s_representation_kind CHECK (
                    kind IN (
                        'synthetic_power_of_attorney',
                        'synthetic_signed_authorization',
                        'synthetic_legal_representative_attestation'
                    )
                ),
                CONSTRAINT ck_rtm_connect_a1s_representation_hashes CHECK (
                    subject_ref_sha256 ~ '^[0-9a-f]{64}$'
                    AND evidence_sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_connect_a1s_representation_payload CHECK (
                    jsonb_typeof(canonical_evidence) = 'object'
                    AND canonical_evidence @> '{"synthetic_marker":\
                        "RTM_A1S_SYNTHETIC_ONLY",\
                        "synthetic_only": true}'::jsonb
                ),
                CONSTRAINT ck_rtm_connect_a1s_representation_status CHECK (
                    status IN ('active', 'revoked', 'expired')
                ),
                CONSTRAINT ck_rtm_connect_a1s_representation_vigency CHECK (
                    expires_at > valid_from
                    AND ((status = 'active' AND revoked_at IS NULL)
                        OR (status = 'revoked' AND revoked_at IS NOT NULL)
                        OR status = 'expired')
                ),
                CONSTRAINT ck_rtm_connect_a1s_representation_synthetic CHECK (
                    synthetic_only = TRUE
                ),
                CONSTRAINT ck_rtm_connect_a1s_representation_version CHECK (
                    version > 0
                )
            );
        """),
        ("a1s_representation_code_index", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_a1s_representation_code
            ON rtm_connect_a1s_representation_evidence(representation_code);
        """),
        ("a1s_representation_binding_index", """
            CREATE INDEX IF NOT EXISTS
                idx_rtm_connect_a1s_representation_binding
            ON rtm_connect_a1s_representation_evidence(
                tenant_id, case_binding_id, status, expires_at
            );
        """),
    ]


def _a1s_workflow_table_ddl() -> list[tuple[str, str]]:
    return [
        ("a1s_human_tasks", """
            CREATE TABLE IF NOT EXISTS rtm_connect_a1s_human_tasks (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES rtm_connect_a1s_tenants(id)
                    ON DELETE RESTRICT,
                case_binding_id UUID NOT NULL
                    REFERENCES rtm_connect_a1s_case_bindings(id)
                    ON DELETE RESTRICT,
                representation_evidence_id UUID NOT NULL
                    REFERENCES rtm_connect_a1s_representation_evidence(id)
                    ON DELETE RESTRICT,
                action_id UUID NOT NULL REFERENCES rtm_connect_actions(id)
                    ON DELETE RESTRICT,
                attempt_id UUID NOT NULL REFERENCES rtm_connect_attempts(id)
                    ON DELETE RESTRICT,
                connector_id UUID NOT NULL REFERENCES rtm_connect_connectors(id)
                    ON DELETE RESTRICT,
                authorization_id UUID NOT NULL
                    REFERENCES rtm_connect_authorizations(id)
                    ON DELETE RESTRICT,
                authorization_version INTEGER NOT NULL,
                task_code TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'prepared',
                requester_membership_id UUID NOT NULL,
                requester_principal_id UUID NOT NULL,
                requester_operator_id UUID NOT NULL,
                assignee_membership_id UUID,
                assignee_principal_id UUID,
                assignee_operator_id UUID,
                assigned_by_operator_id UUID REFERENCES rtm_operators(id)
                    ON DELETE RESTRICT,
                release_membership_id UUID,
                release_principal_id UUID,
                release_operator_id UUID,
                verified_by_membership_id UUID,
                verified_by_principal_id UUID,
                verified_by_operator_id UUID,
                due_at TIMESTAMPTZ NOT NULL,
                assigned_at TIMESTAMPTZ,
                reviewed_at TIMESTAMPTZ,
                ready_at TIMESTAMPTZ,
                released_at TIMESTAMPTZ,
                started_at TIMESTAMPTZ,
                awaiting_receipt_at TIMESTAMPTZ,
                unknown_at TIMESTAMPTZ,
                reconciling_at TIMESTAMPTZ,
                receipt_submitted_at TIMESTAMPTZ,
                verified_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                package_manifest JSONB NOT NULL,
                package_sha256 TEXT NOT NULL,
                review_attestation_sha256 TEXT,
                release_attestation_sha256 TEXT,
                verification_attestation_sha256 TEXT,
                external_reference TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                status_version INTEGER GENERATED ALWAYS AS (version) STORED,
                metadata JSONB NOT NULL DEFAULT
                    '{"synthetic_marker":"RTM_A1S_SYNTHETIC_ONLY",\
                      "synthetic_only": true}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT fk_rtm_connect_a1s_task_requester FOREIGN KEY (
                    requester_membership_id, tenant_id,
                    requester_principal_id, requester_operator_id
                ) REFERENCES rtm_connect_a1s_memberships(
                    id, tenant_id, principal_id, operator_id
                ) ON DELETE RESTRICT,
                CONSTRAINT fk_rtm_connect_a1s_task_assignee FOREIGN KEY (
                    assignee_membership_id, tenant_id,
                    assignee_principal_id, assignee_operator_id
                ) REFERENCES rtm_connect_a1s_memberships(
                    id, tenant_id, principal_id, operator_id
                ) ON DELETE RESTRICT,
                CONSTRAINT fk_rtm_connect_a1s_task_releaser FOREIGN KEY (
                    release_membership_id, tenant_id,
                    release_principal_id, release_operator_id
                ) REFERENCES rtm_connect_a1s_memberships(
                    id, tenant_id, principal_id, operator_id
                ) ON DELETE RESTRICT,
                CONSTRAINT fk_rtm_connect_a1s_task_verifier FOREIGN KEY (
                    verified_by_membership_id, tenant_id,
                    verified_by_principal_id, verified_by_operator_id
                ) REFERENCES rtm_connect_a1s_memberships(
                    id, tenant_id, principal_id, operator_id
                ) ON DELETE RESTRICT,
                CONSTRAINT ck_rtm_connect_a1s_task_code CHECK (
                    task_code ~ '^rtm-a1s-human-[0-9a-f]{24}$'
                ),
                CONSTRAINT ck_rtm_connect_a1s_task_status CHECK (
                    status IN (
                        'prepared', 'assigned', 'reviewing',
                        'ready_for_release', 'released', 'in_progress',
                        'awaiting_receipt', 'outcome_unknown', 'reconciling',
                        'receipt_submitted', 'verified', 'completed',
                        'manual_review', 'permanent_failed'
                    )
                ),
                CONSTRAINT ck_rtm_connect_a1s_task_package_hash CHECK (
                    package_sha256 ~ '^[0-9a-f]{64}$'
                    AND (review_attestation_sha256 IS NULL OR
                        review_attestation_sha256 ~ '^[0-9a-f]{64}$')
                    AND (release_attestation_sha256 IS NULL OR
                        release_attestation_sha256 ~ '^[0-9a-f]{64}$')
                    AND (verification_attestation_sha256 IS NULL OR
                        verification_attestation_sha256 ~ '^[0-9a-f]{64}$')
                ),
                CONSTRAINT ck_rtm_connect_a1s_task_package_scope CHECK (
                    jsonb_typeof(package_manifest) = 'object'
                    AND package_manifest @> '{"synthetic_marker":\
                        "RTM_A1S_SYNTHETIC_ONLY", "synthetic_only": true,\
                        "network_used": false, "b2_used": false,\
                        "provider_contacted": false,\
                        "legal_submission_executed": false}'::jsonb
                    AND jsonb_typeof(metadata) = 'object'
                    AND metadata @> '{"synthetic_marker":\
                        "RTM_A1S_SYNTHETIC_ONLY",\
                        "synthetic_only": true}'::jsonb
                ),
                CONSTRAINT ck_rtm_connect_a1s_task_assignment CHECK (
                    (status = 'prepared'
                        AND assignee_membership_id IS NULL
                        AND assignee_principal_id IS NULL
                        AND assignee_operator_id IS NULL
                        AND assigned_by_operator_id IS NULL
                        AND assigned_at IS NULL)
                    OR (status <> 'prepared' AND (
                        assignee_membership_id IS NOT NULL
                        AND assignee_principal_id IS NOT NULL
                        AND assignee_operator_id IS NOT NULL
                        AND assigned_by_operator_id IS NOT NULL
                        AND assigned_at IS NOT NULL
                    ))
                ),
                CONSTRAINT ck_rtm_connect_a1s_task_review CHECK (
                    status NOT IN (
                        'ready_for_release', 'released', 'in_progress',
                        'awaiting_receipt', 'outcome_unknown', 'reconciling',
                        'receipt_submitted', 'verified', 'completed'
                    ) OR (reviewed_at IS NOT NULL AND ready_at IS NOT NULL
                        AND review_attestation_sha256 IS NOT NULL)
                ),
                CONSTRAINT ck_rtm_connect_a1s_task_release CHECK (
                    status NOT IN (
                        'released', 'in_progress', 'awaiting_receipt',
                        'outcome_unknown', 'reconciling', 'receipt_submitted',
                        'verified', 'completed'
                    ) OR (
                        release_membership_id IS NOT NULL
                        AND release_principal_id IS NOT NULL
                        AND release_operator_id IS NOT NULL
                        AND released_at IS NOT NULL
                        AND release_attestation_sha256 IS NOT NULL
                    )
                ),
                CONSTRAINT ck_rtm_connect_a1s_task_started CHECK (
                    status NOT IN (
                        'in_progress', 'awaiting_receipt', 'outcome_unknown',
                        'reconciling', 'receipt_submitted', 'verified',
                        'completed'
                    ) OR started_at IS NOT NULL
                ),
                CONSTRAINT ck_rtm_connect_a1s_task_awaiting CHECK (
                    status NOT IN (
                        'awaiting_receipt', 'receipt_submitted', 'verified',
                        'completed'
                    ) OR awaiting_receipt_at IS NOT NULL
                ),
                CONSTRAINT ck_rtm_connect_a1s_task_unknown CHECK (
                    status NOT IN (
                        'outcome_unknown', 'reconciling', 'permanent_failed'
                    ) OR unknown_at IS NOT NULL
                ),
                CONSTRAINT ck_rtm_connect_a1s_task_reconciling CHECK (
                    status <> 'reconciling' OR reconciling_at IS NOT NULL
                ),
                CONSTRAINT ck_rtm_connect_a1s_task_receipt CHECK (
                    status NOT IN ('receipt_submitted', 'verified', 'completed')
                    OR (receipt_submitted_at IS NOT NULL
                        AND external_reference IS NOT NULL)
                ),
                CONSTRAINT ck_rtm_connect_a1s_task_verified CHECK (
                    status NOT IN ('verified', 'completed') OR (
                        verified_by_membership_id IS NOT NULL
                        AND verified_by_principal_id IS NOT NULL
                        AND verified_by_operator_id IS NOT NULL
                        AND verified_at IS NOT NULL
                        AND verification_attestation_sha256 IS NOT NULL
                    )
                ),
                CONSTRAINT ck_rtm_connect_a1s_task_completed CHECK (
                    status <> 'completed' OR completed_at IS NOT NULL
                ),
                CONSTRAINT ck_rtm_connect_a1s_task_separation CHECK (
                    (release_principal_id IS NULL OR
                        release_principal_id <> requester_principal_id)
                    AND (release_principal_id IS NULL OR
                        assignee_principal_id IS NULL OR
                        release_principal_id <> assignee_principal_id)
                    AND (verified_by_principal_id IS NULL OR
                        verified_by_principal_id <> requester_principal_id)
                    AND (verified_by_principal_id IS NULL OR
                        assignee_principal_id IS NULL OR
                        verified_by_principal_id <> assignee_principal_id)
                    AND (verified_by_principal_id IS NULL OR
                        release_principal_id IS NULL OR
                        verified_by_principal_id <> release_principal_id)
                ),
                CONSTRAINT ck_rtm_connect_a1s_task_external_reference CHECK (
                    external_reference IS NULL OR external_reference ~
                        '^a1s-synthetic-[0-9a-f]{24}$'
                ),
                CONSTRAINT ck_rtm_connect_a1s_task_due CHECK (
                    due_at > created_at
                ),
                CONSTRAINT ck_rtm_connect_a1s_task_version CHECK (
                    version > 0 AND authorization_version > 0
                )
            );
        """),
        ("a1s_task_action_index", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_a1s_task_action
            ON rtm_connect_a1s_human_tasks(action_id);
        """),
        ("a1s_task_attempt_index", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_a1s_task_attempt
            ON rtm_connect_a1s_human_tasks(attempt_id);
        """),
        ("a1s_task_code_index", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_a1s_task_code
            ON rtm_connect_a1s_human_tasks(task_code);
        """),
        ("a1s_task_queue_index", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_a1s_task_queue
            ON rtm_connect_a1s_human_tasks(
                tenant_id, status, due_at, created_at
            );
        """),
        ("a1s_task_binding_index", """
            CREATE INDEX IF NOT EXISTS
                idx_rtm_connect_a1s_task_case_binding
            ON rtm_connect_a1s_human_tasks(tenant_id, case_binding_id);
        """),
    ]


def _a1s_evidence_table_ddl() -> list[tuple[str, str]]:
    return [
        ("a1s_artifacts", """
            CREATE TABLE IF NOT EXISTS rtm_connect_a1s_artifacts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES rtm_connect_a1s_tenants(id)
                    ON DELETE RESTRICT,
                task_id UUID NOT NULL
                    REFERENCES rtm_connect_a1s_human_tasks(id)
                    ON DELETE RESTRICT,
                artifact_code TEXT NOT NULL,
                kind TEXT NOT NULL,
                media_type TEXT NOT NULL DEFAULT 'application/json',
                sha256 TEXT NOT NULL,
                canonical_payload JSONB NOT NULL,
                submitted_by_membership_id UUID NOT NULL,
                submitted_by_principal_id UUID NOT NULL,
                submitted_by_operator_id UUID NOT NULL,
                verified_by_membership_id UUID,
                verified_by_principal_id UUID,
                verified_by_operator_id UUID,
                verified_at TIMESTAMPTZ,
                synthetic_only BOOLEAN NOT NULL DEFAULT TRUE,
                storage_backend TEXT NOT NULL DEFAULT 'database_manifest_only',
                supersedes_artifact_id UUID
                    REFERENCES rtm_connect_a1s_artifacts(id)
                    ON DELETE RESTRICT,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT fk_rtm_connect_a1s_artifact_submitter FOREIGN KEY (
                    submitted_by_membership_id, tenant_id,
                    submitted_by_principal_id, submitted_by_operator_id
                ) REFERENCES rtm_connect_a1s_memberships(
                    id, tenant_id, principal_id, operator_id
                ) ON DELETE RESTRICT,
                CONSTRAINT fk_rtm_connect_a1s_artifact_verifier FOREIGN KEY (
                    verified_by_membership_id, tenant_id,
                    verified_by_principal_id, verified_by_operator_id
                ) REFERENCES rtm_connect_a1s_memberships(
                    id, tenant_id, principal_id, operator_id
                ) ON DELETE RESTRICT,
                CONSTRAINT ck_rtm_connect_a1s_artifact_code CHECK (
                    artifact_code ~ '^rtm-a1s-artifact-[0-9a-f]{24}$'
                ),
                CONSTRAINT ck_rtm_connect_a1s_artifact_kind CHECK (
                    kind IN (
                        'authority_snapshot', 'representation_evidence',
                        'filing_package', 'human_review_attestation',
                        'release_attestation',
                        'verification_preapproval_attestation',
                        'synthetic_submission_report', 'synthetic_receipt',
                        'verification_attestation',
                        'reconciliation_attestation'
                    )
                ),
                CONSTRAINT ck_rtm_connect_a1s_artifact_hash CHECK (
                    sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_connect_a1s_artifact_payload CHECK (
                    jsonb_typeof(canonical_payload) = 'object'
                    AND canonical_payload @> '{"synthetic_marker":\
                        "RTM_A1S_SYNTHETIC_ONLY",\
                        "synthetic_only": true}'::jsonb
                ),
                CONSTRAINT ck_rtm_connect_a1s_artifact_storage CHECK (
                    synthetic_only = TRUE
                    AND storage_backend = 'database_manifest_only'
                    AND media_type = 'application/json'
                ),
                CONSTRAINT ck_rtm_connect_a1s_artifact_verification CHECK (
                    (verified_at IS NULL
                        AND verified_by_membership_id IS NULL
                        AND verified_by_principal_id IS NULL
                        AND verified_by_operator_id IS NULL)
                    OR (verified_at IS NOT NULL
                        AND verified_by_membership_id IS NOT NULL
                        AND verified_by_principal_id IS NOT NULL
                        AND verified_by_operator_id IS NOT NULL)
                ),
                CONSTRAINT ck_rtm_connect_a1s_artifact_version CHECK (
                    version = 1
                )
            );
        """),
        ("a1s_artifact_code_index", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_a1s_artifact_code
            ON rtm_connect_a1s_artifacts(artifact_code);
        """),
        ("a1s_artifact_content_index", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_a1s_artifact_content
            ON rtm_connect_a1s_artifacts(task_id, kind, sha256);
        """),
        ("a1s_artifact_task_kind_index", """
            CREATE INDEX IF NOT EXISTS
                idx_rtm_connect_a1s_artifact_task_kind
            ON rtm_connect_a1s_artifacts(tenant_id, task_id, kind, created_at);
        """),
        ("a1s_approvals", """
            CREATE TABLE IF NOT EXISTS rtm_connect_a1s_approvals (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES rtm_connect_a1s_tenants(id)
                    ON DELETE RESTRICT,
                task_id UUID NOT NULL
                    REFERENCES rtm_connect_a1s_human_tasks(id)
                    ON DELETE RESTRICT,
                approval_type TEXT NOT NULL,
                decision TEXT NOT NULL,
                membership_id UUID NOT NULL,
                principal_id UUID NOT NULL,
                operator_id UUID NOT NULL,
                attestation_sha256 TEXT NOT NULL,
                artifact_id UUID NOT NULL REFERENCES rtm_connect_a1s_artifacts(id)
                    ON DELETE RESTRICT,
                approved_at TIMESTAMPTZ NOT NULL,
                synthetic_only BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT fk_rtm_connect_a1s_approval_actor FOREIGN KEY (
                    membership_id, tenant_id, principal_id, operator_id
                ) REFERENCES rtm_connect_a1s_memberships(
                    id, tenant_id, principal_id, operator_id
                ) ON DELETE RESTRICT,
                CONSTRAINT ck_rtm_connect_a1s_approval_type CHECK (
                    approval_type IN ('release', 'verification_preapproval')
                ),
                CONSTRAINT ck_rtm_connect_a1s_approval_decision CHECK (
                    decision = 'approved_frozen'
                    AND synthetic_only = TRUE
                    AND attestation_sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_connect_a1s_approval_time CHECK (
                    approved_at <= created_at
                )
            );
        """),
        ("a1s_approval_type_index", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_a1s_approval_type
            ON rtm_connect_a1s_approvals(task_id, approval_type);
        """),
        ("a1s_approval_principal_index", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_a1s_approval_principal
            ON rtm_connect_a1s_approvals(task_id, principal_id);
        """),
        ("a1s_approval_task_index", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_a1s_approval_task
            ON rtm_connect_a1s_approvals(tenant_id, task_id, approved_at);
        """),
        ("a1s_events", """
            CREATE TABLE IF NOT EXISTS rtm_connect_a1s_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES rtm_connect_a1s_tenants(id)
                    ON DELETE RESTRICT,
                task_id UUID NOT NULL
                    REFERENCES rtm_connect_a1s_human_tasks(id)
                    ON DELETE RESTRICT,
                action_id UUID NOT NULL REFERENCES rtm_connect_actions(id)
                    ON DELETE RESTRICT,
                attempt_id UUID NOT NULL REFERENCES rtm_connect_attempts(id)
                    ON DELETE RESTRICT,
                sequence_number INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                membership_id UUID,
                principal_id UUID,
                operator_id UUID,
                from_status TEXT,
                to_status TEXT,
                reason_code TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                payload JSONB NOT NULL,
                synthetic_only BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT fk_rtm_connect_a1s_event_actor FOREIGN KEY (
                    membership_id, tenant_id, principal_id, operator_id
                ) REFERENCES rtm_connect_a1s_memberships(
                    id, tenant_id, principal_id, operator_id
                ) ON DELETE RESTRICT,
                CONSTRAINT ck_rtm_connect_a1s_event_sequence CHECK (
                    sequence_number > 0
                ),
                CONSTRAINT ck_rtm_connect_a1s_event_actor CHECK (
                    (actor_type = 'operator' AND membership_id IS NOT NULL
                        AND principal_id IS NOT NULL AND operator_id IS NOT NULL)
                    OR (actor_type IN ('connect', 'core', 'system')
                        AND membership_id IS NULL AND principal_id IS NULL
                        AND operator_id IS NULL)
                ),
                CONSTRAINT ck_rtm_connect_a1s_event_type CHECK (
                    event_type ~ '^[a-z][a-z0-9_.-]{2,95}$'
                    AND reason_code ~ '^[a-z][a-z0-9_.-]{2,95}$'
                ),
                CONSTRAINT ck_rtm_connect_a1s_event_hash CHECK (
                    payload_sha256 ~ '^[0-9a-f]{64}$'
                    AND synthetic_only = TRUE
                    AND jsonb_typeof(payload) = 'object'
                    AND payload @> '{"synthetic_marker":\
                        "RTM_A1S_SYNTHETIC_ONLY",\
                        "synthetic_only": true}'::jsonb
                ),
                CONSTRAINT ck_rtm_connect_a1s_event_states CHECK (
                    (from_status IS NULL OR from_status IN (
                        'prepared', 'assigned', 'reviewing',
                        'ready_for_release', 'released', 'in_progress',
                        'awaiting_receipt', 'outcome_unknown', 'reconciling',
                        'receipt_submitted', 'verified', 'completed',
                        'manual_review', 'permanent_failed'
                    )) AND (to_status IS NULL OR to_status IN (
                        'prepared', 'assigned', 'reviewing',
                        'ready_for_release', 'released', 'in_progress',
                        'awaiting_receipt', 'outcome_unknown', 'reconciling',
                        'receipt_submitted', 'verified', 'completed',
                        'manual_review', 'permanent_failed'
                    ))
                )
            );
        """),
        ("a1s_event_sequence_index", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_a1s_event_sequence
            ON rtm_connect_a1s_events(task_id, sequence_number);
        """),
        ("a1s_event_action_index", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_a1s_event_action
            ON rtm_connect_a1s_events(tenant_id, action_id, created_at);
        """),
        ("a1s_event_principal_index", """
            CREATE INDEX IF NOT EXISTS idx_rtm_connect_a1s_event_principal
            ON rtm_connect_a1s_events(
                tenant_id, principal_id, created_at
            ) WHERE principal_id IS NOT NULL;
        """),
        ("a1s_idempotency", """
            CREATE TABLE IF NOT EXISTS rtm_connect_a1s_idempotency (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES rtm_connect_a1s_tenants(id)
                    ON DELETE RESTRICT,
                idempotency_key TEXT NOT NULL,
                scope TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                response_sha256 TEXT,
                task_id UUID REFERENCES rtm_connect_a1s_human_tasks(id)
                    ON DELETE RESTRICT,
                action_id UUID REFERENCES rtm_connect_actions(id)
                    ON DELETE RESTRICT,
                status TEXT NOT NULL DEFAULT 'claimed',
                claimed_by_membership_id UUID NOT NULL,
                claimed_by_principal_id UUID NOT NULL,
                claimed_by_operator_id UUID NOT NULL,
                replay_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                completed_at TIMESTAMPTZ,
                expires_at TIMESTAMPTZ NOT NULL,
                metadata JSONB NOT NULL DEFAULT
                    '{"synthetic_marker":"RTM_A1S_SYNTHETIC_ONLY",\
                      "synthetic_only": true}'::jsonb,
                CONSTRAINT fk_rtm_connect_a1s_idempotency_actor FOREIGN KEY (
                    claimed_by_membership_id, tenant_id,
                    claimed_by_principal_id, claimed_by_operator_id
                ) REFERENCES rtm_connect_a1s_memberships(
                    id, tenant_id, principal_id, operator_id
                ) ON DELETE RESTRICT,
                CONSTRAINT ck_rtm_connect_a1s_idempotency_key CHECK (
                    idempotency_key ~ '^rtma1s:[0-9a-f]{64}$'
                    AND scope ~ '^[a-z][a-z0-9_.-]{2,95}$'
                ),
                CONSTRAINT ck_rtm_connect_a1s_idempotency_hashes CHECK (
                    request_sha256 ~ '^[0-9a-f]{64}$'
                    AND (response_sha256 IS NULL OR
                        response_sha256 ~ '^[0-9a-f]{64}$')
                ),
                CONSTRAINT ck_rtm_connect_a1s_idempotency_status CHECK (
                    status IN ('claimed', 'completed', 'conflict')
                    AND replay_count >= 0
                ),
                CONSTRAINT ck_rtm_connect_a1s_idempotency_completion CHECK (
                    (status = 'claimed' AND response_sha256 IS NULL
                        AND completed_at IS NULL)
                    OR (status IN ('completed', 'conflict')
                        AND response_sha256 IS NOT NULL
                        AND completed_at IS NOT NULL)
                ),
                CONSTRAINT ck_rtm_connect_a1s_idempotency_expiry CHECK (
                    expires_at > created_at
                ),
                CONSTRAINT ck_rtm_connect_a1s_idempotency_metadata CHECK (
                    jsonb_typeof(metadata) = 'object'
                    AND metadata @> '{"synthetic_marker":\
                        "RTM_A1S_SYNTHETIC_ONLY",\
                        "synthetic_only": true}'::jsonb
                )
            );
        """),
        ("a1s_idempotency_key_index", """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_connect_a1s_idempotency_key
            ON rtm_connect_a1s_idempotency(tenant_id, idempotency_key);
        """),
        ("a1s_idempotency_expiry_index", """
            CREATE INDEX IF NOT EXISTS
                idx_rtm_connect_a1s_idempotency_expiry
            ON rtm_connect_a1s_idempotency(status, expires_at);
        """),
    ]


def _a1s_guard_function_ddl() -> list[tuple[str, str]]:
    return [
        ("a1s_reject_mutation_function", """
            CREATE OR REPLACE FUNCTION rtm_connect_a1s_reject_mutation()
            RETURNS TRIGGER AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only in A1-S', TG_TABLE_NAME;
            END;
            $$ LANGUAGE plpgsql;
        """),
        ("a1s_tenant_frozen_function", """
            CREATE OR REPLACE FUNCTION rtm_connect_a1s_tenant_frozen_guard()
            RETURNS TRIGGER AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'A1-S tenants cannot be deleted';
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.tenant_code IS DISTINCT FROM OLD.tenant_code
                   OR NEW.display_name IS DISTINCT FROM OLD.display_name
                   OR NEW.synthetic_only IS DISTINCT FROM OLD.synthetic_only
                   OR NEW.metadata IS DISTINCT FROM OLD.metadata
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'A1-S tenant identity is frozen';
                END IF;
                IF NOT (
                    (OLD.status = 'active' AND NEW.status IN (
                        'active', 'suspended', 'disabled'
                    )) OR
                    (OLD.status = 'suspended' AND NEW.status IN (
                        'active', 'suspended', 'disabled'
                    )) OR
                    (OLD.status = 'disabled' AND NEW.status = 'disabled')
                ) THEN
                    RAISE EXCEPTION 'Invalid A1-S tenant state change';
                END IF;
                IF NEW.updated_at < OLD.updated_at THEN
                    RAISE EXCEPTION 'A1-S tenant time cannot move backwards';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """),
        ("a1s_membership_guard_function", """
            CREATE OR REPLACE FUNCTION rtm_connect_a1s_membership_guard()
            RETURNS TRIGGER AS $$
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NOT EXISTS (
                        SELECT 1 FROM rtm_connect_a1s_tenants t
                        JOIN rtm_operators o ON o.id = NEW.operator_id
                        JOIN rtm_operators g ON g.id = NEW.granted_by_operator_id
                        WHERE t.id = NEW.tenant_id AND t.status = 'active'
                          AND t.synthetic_only = TRUE
                          AND o.status = 'active' AND g.status = 'active'
                    ) THEN
                        RAISE EXCEPTION 'Inactive A1-S tenant or operator';
                    END IF;
                    RETURN NEW;
                END IF;
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'A1-S memberships cannot be deleted';
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.principal_id IS DISTINCT FROM OLD.principal_id
                   OR NEW.operator_id IS DISTINCT FROM OLD.operator_id
                   OR NEW.role IS DISTINCT FROM OLD.role
                   OR NEW.synthetic_only IS DISTINCT FROM OLD.synthetic_only
                   OR NEW.granted_by_operator_id IS DISTINCT FROM
                        OLD.granted_by_operator_id
                   OR NEW.granted_at IS DISTINCT FROM OLD.granted_at
                   OR NEW.metadata IS DISTINCT FROM OLD.metadata THEN
                    RAISE EXCEPTION 'A1-S membership identity is frozen';
                END IF;
                IF OLD.status <> 'active' OR NEW.status <> 'revoked'
                   OR NEW.revoked_at IS NULL
                   OR NEW.revoked_by_operator_id IS NULL
                   OR NEW.version <> OLD.version + 1 THEN
                    RAISE EXCEPTION 'Only one-way A1-S membership revocation is allowed';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """),
        ("a1s_binding_frozen_function", """
            CREATE OR REPLACE FUNCTION rtm_connect_a1s_binding_frozen_guard()
            RETURNS TRIGGER AS $$
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NEW.metadata->>'test_mode' <> 'true'
                       OR NOT EXISTS (
                            SELECT 1 FROM rtm_connect_a1s_tenants t
                            JOIN cases c ON c.id = NEW.case_id
                              AND COALESCE(c.test_mode, FALSE) = TRUE
                            JOIN rtm_operators o
                              ON o.id = NEW.bound_by_operator_id
                            WHERE t.id = NEW.tenant_id
                              AND t.status = 'active'
                              AND t.synthetic_only = TRUE
                              AND o.status = 'active'
                       ) THEN
                        RAISE EXCEPTION 'A1-S binding requires active synthetic test_mode';
                    END IF;
                    RETURN NEW;
                END IF;
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'A1-S case bindings cannot be deleted';
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.case_id IS DISTINCT FROM OLD.case_id
                   OR NEW.binding_code IS DISTINCT FROM OLD.binding_code
                   OR NEW.synthetic_only IS DISTINCT FROM OLD.synthetic_only
                   OR NEW.case_snapshot_sha256 IS DISTINCT FROM
                        OLD.case_snapshot_sha256
                   OR NEW.bound_by_operator_id IS DISTINCT FROM
                        OLD.bound_by_operator_id
                   OR NEW.bound_at IS DISTINCT FROM OLD.bound_at
                   OR NEW.metadata IS DISTINCT FROM OLD.metadata THEN
                    RAISE EXCEPTION 'A1-S case binding is frozen';
                END IF;
                IF OLD.status <> 'active' OR NEW.status <> 'revoked'
                   OR NEW.revoked_at IS NULL
                   OR NEW.revoked_by_operator_id IS NULL
                   OR NEW.version <> OLD.version + 1 THEN
                    RAISE EXCEPTION 'Only one-way A1-S binding revocation is allowed';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """),
        ("a1s_representation_frozen_function", """
            CREATE OR REPLACE FUNCTION rtm_connect_a1s_representation_frozen_guard()
            RETURNS TRIGGER AS $$
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM rtm_connect_a1s_case_bindings b
                        JOIN rtm_connect_a1s_memberships m
                          ON m.id = NEW.recorded_by_membership_id
                         AND m.tenant_id = NEW.tenant_id
                         AND m.principal_id = NEW.recorded_by_principal_id
                         AND m.operator_id = NEW.recorded_by_operator_id
                        WHERE b.id = NEW.case_binding_id
                          AND b.tenant_id = NEW.tenant_id
                          AND b.status = 'active' AND b.synthetic_only = TRUE
                          AND b.metadata->>'test_mode' = 'true'
                          AND m.status = 'active' AND m.synthetic_only = TRUE
                    ) THEN
                        RAISE EXCEPTION 'Invalid A1-S representation scope';
                    END IF;
                    RETURN NEW;
                END IF;
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'A1-S representation cannot be deleted';
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.case_binding_id IS DISTINCT FROM OLD.case_binding_id
                   OR NEW.representation_code IS DISTINCT FROM
                        OLD.representation_code
                   OR NEW.kind IS DISTINCT FROM OLD.kind
                   OR NEW.subject_ref_sha256 IS DISTINCT FROM
                        OLD.subject_ref_sha256
                   OR NEW.evidence_sha256 IS DISTINCT FROM OLD.evidence_sha256
                   OR NEW.canonical_evidence IS DISTINCT FROM
                        OLD.canonical_evidence
                   OR NEW.synthetic_only IS DISTINCT FROM OLD.synthetic_only
                   OR NEW.recorded_by_membership_id IS DISTINCT FROM
                        OLD.recorded_by_membership_id
                   OR NEW.recorded_by_principal_id IS DISTINCT FROM
                        OLD.recorded_by_principal_id
                   OR NEW.recorded_by_operator_id IS DISTINCT FROM
                        OLD.recorded_by_operator_id
                   OR NEW.valid_from IS DISTINCT FROM OLD.valid_from
                   OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'A1-S representation evidence is frozen';
                END IF;
                IF OLD.status <> 'active'
                   OR NEW.status NOT IN ('revoked', 'expired')
                   OR NEW.version <> OLD.version + 1
                   OR (NEW.status = 'revoked' AND (
                        NEW.revoked_at IS NULL OR
                        NEW.revoked_by_operator_id IS NULL
                   )) THEN
                    RAISE EXCEPTION 'Invalid A1-S representation closure';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """),
    ]


def _a1s_task_guard_ddl() -> list[tuple[str, str]]:
    return [
        ("a1s_task_guard_function", """
            CREATE OR REPLACE FUNCTION rtm_connect_a1s_task_guard()
            RETURNS TRIGGER AS $$
            DECLARE
                scope_ok BOOLEAN;
                release_membership UUID;
                release_principal UUID;
                release_operator UUID;
                release_hash TEXT;
                release_time TIMESTAMPTZ;
                verify_membership UUID;
                verify_principal UUID;
                verify_operator UUID;
                verify_time TIMESTAMPTZ;
                approvals_count INTEGER;
                manual_review_closure BOOLEAN := FALSE;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'A1-S human tasks cannot be deleted';
                END IF;

                IF TG_OP = 'UPDATE' THEN
                    manual_review_closure :=
                        NEW.status = 'manual_review'
                        AND OLD.status IS DISTINCT FROM NEW.status;
                END IF;

                SELECT EXISTS (
                    SELECT 1
                    FROM rtm_connect_a1s_tenants t
                    JOIN rtm_connect_a1s_case_bindings b
                      ON b.id = NEW.case_binding_id
                     AND b.tenant_id = NEW.tenant_id
                    JOIN cases actual_case
                      ON actual_case.id = b.case_id
                     AND COALESCE(actual_case.test_mode, FALSE) = TRUE
                    JOIN rtm_connect_a1s_representation_evidence r
                      ON r.id = NEW.representation_evidence_id
                     AND r.tenant_id = NEW.tenant_id
                     AND r.case_binding_id = b.id
                    JOIN rtm_connect_actions a
                      ON a.id = NEW.action_id AND a.case_id = b.case_id
                    JOIN rtm_connect_attempts x
                      ON x.id = NEW.attempt_id AND x.action_id = a.id
                     AND x.connector_id = NEW.connector_id
                    JOIN rtm_connect_connectors c
                      ON c.id = NEW.connector_id
                    JOIN rtm_connect_authorizations z
                      ON z.id = NEW.authorization_id
                     AND z.action_id = a.id
                     AND z.authorization_version = NEW.authorization_version
                    JOIN rtm_connect_a1s_memberships requester
                      ON requester.id = NEW.requester_membership_id
                     AND requester.tenant_id = NEW.tenant_id
                     AND requester.principal_id = NEW.requester_principal_id
                     AND requester.operator_id = NEW.requester_operator_id
                    LEFT JOIN rtm_connect_a1s_memberships executor
                      ON executor.id = NEW.assignee_membership_id
                     AND executor.tenant_id = NEW.tenant_id
                     AND executor.principal_id = NEW.assignee_principal_id
                     AND executor.operator_id = NEW.assignee_operator_id
                    WHERE t.id = NEW.tenant_id AND t.status = 'active'
                      AND t.synthetic_only = TRUE
                      AND b.synthetic_only = TRUE
                      AND b.metadata->>'test_mode' = 'true'
                      AND r.synthetic_only = TRUE
                      AND requester.synthetic_only = TRUE
                      AND requester.role IN (
                          'requester', 'executor', 'supervisor'
                      )
                      AND (NEW.assignee_membership_id IS NULL OR (
                          executor.synthetic_only = TRUE
                          AND executor.role IN ('executor', 'supervisor')
                      ))
                      AND (
                          manual_review_closure OR (
                              b.status = 'active'
                              AND r.status = 'active'
                              AND r.valid_from <= NOW()
                              AND r.expires_at > NOW()
                              AND requester.status = 'active'
                              AND (
                                  NEW.assignee_membership_id IS NULL
                                  OR executor.status = 'active'
                              )
                          )
                      )
                      AND a.requested_by_operator_id = NEW.requester_operator_id
                      AND a.capability =
                          'administration.submit.human.synthetic'
                      AND a.satellite = 'rtm.human.filing.synthetic'
                      AND a.target_type = 'administration.synthetic.filing'
                      AND a.target_ref = 'synthetic-a1s-administration'
                      AND a.risk_class = 'R4_critical_regulated'
                      AND a.requires_dual_control = TRUE
                      AND a.payload @> '{"contract_version":\
                          "rtm.connect.a1s.human_filing.v1",\
                          "synthetic_marker":"RTM_A1S_SYNTHETIC_ONLY",\
                          "synthetic_only": true,"network_used": false,\
                          "b2_used": false,"provider_contacted": false,\
                          "external_effects_allowed": false}'::jsonb
                      AND a.payload->>'case_binding_id' = b.id::text
                      AND a.payload->>'representation_evidence_id' = r.id::text
                      AND a.payload->>'case_snapshot_sha256' =
                          b.case_snapshot_sha256
                      AND jsonb_typeof(a.document_hashes) = 'array'
                      AND jsonb_array_length(a.document_hashes) BETWEEN 1 AND 8
                      AND NEW.package_manifest->'document_hashes' =
                          a.document_hashes
                      AND NOT EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements_text(a.document_hashes)
                              AS requested_document(document_sha256)
                          WHERE requested_document.document_sha256 !~
                                  '^[0-9a-f]{64}$'
                             OR (
                                NOT manual_review_closure
                                AND NOT EXISTS (
                                  SELECT 1 FROM documents source_document
                                  WHERE source_document.case_id = b.case_id
                                    AND source_document.sha256 =
                                        requested_document.document_sha256
                                )
                             )
                      )
                      AND c.code = 'human.filing.a1s'
                      AND c.version = 'v1.0' AND c.mode = 'assisted'
                      AND c.environment = 'staging'
                      AND c.synthetic_only = TRUE AND c.credential_ref IS NULL
                      AND c.capabilities @>
                          '["administration.submit.human.synthetic"]'::jsonb
                      AND c.configuration @> '{"synthetic_marker":\
                          "RTM_A1S_SYNTHETIC_ONLY",\
                          "synthetic_only": true,"network_used": false,\
                          "b2_used": false,"provider_contacted": false,\
                          "external_effects": false}'::jsonb
                      AND z.authority_code = 'rtm.core.authorization'
                      AND z.authority_version = 'rtm_core_authority_v1'
                      AND z.decision = 'approved_frozen' AND z.frozen = TRUE
                      AND z.required_evidence_level = 'E4_receipt_verified'
                      AND z.authorized_connector_modes = '["assisted"]'::jsonb
                      AND z.legal_effect_authorized = TRUE
                      AND (
                          manual_review_closure OR NOT EXISTS (
                              SELECT 1
                              FROM rtm_connect_authorizations newer_authority
                              WHERE newer_authority.action_id = z.action_id
                                AND newer_authority.authorization_version >
                                    z.authorization_version
                          )
                      )
                      AND (
                          manual_review_closure OR (
                              c.status = 'active'
                              AND z.revoked_at IS NULL
                              AND (
                                  z.expires_at IS NULL
                                  OR z.expires_at > NOW()
                              )
                          )
                      )
                      AND z.payload_sha256 = a.payload_sha256
                      AND x.request_sha256 = a.payload_sha256
                      AND NEW.package_manifest->>'request_sha256' =
                          a.payload_sha256
                      AND NEW.package_manifest->>'tenant_id' = t.id::text
                      AND NEW.package_manifest->>'case_binding_id' = b.id::text
                      AND NEW.package_manifest->>
                          'representation_evidence_id' = r.id::text
                      AND NEW.package_manifest->>'action_id' = a.id::text
                      AND NEW.package_manifest->>'attempt_id' = x.id::text
                      AND NEW.package_manifest->>'authorization_id' = z.id::text
                      AND NEW.package_manifest->'checklist' = '[
                          "confirm_synthetic_case_binding",
                          "confirm_frozen_core_authority",
                          "confirm_synthetic_representation",
                          "confirm_exact_package_hash",
                          "simulate_human_filing_without_external_contact",
                          "capture_synthetic_receipt",
                          "verify_receipt_with_independent_principal"
                      ]'::jsonb
                      AND CAST(NEW.package_manifest->>'due_at' AS TIMESTAMPTZ)
                          = NEW.due_at
                ) INTO scope_ok;
                IF NOT scope_ok THEN
                    RAISE EXCEPTION 'A1-S task scope or authority is invalid';
                END IF;

                IF TG_OP = 'INSERT' THEN
                    IF NEW.version <> 1
                       OR NEW.status <> 'prepared'
                       OR NEW.assignee_membership_id IS NOT NULL
                       OR NEW.assignee_principal_id IS NOT NULL
                       OR NEW.assignee_operator_id IS NOT NULL
                       OR NEW.assigned_by_operator_id IS NOT NULL
                       OR NEW.assigned_at IS NOT NULL THEN
                        RAISE EXCEPTION
                            'A1-S task must start prepared v1 and unassigned';
                    END IF;
                ELSE
                    IF NEW.id IS DISTINCT FROM OLD.id
                       OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                       OR NEW.case_binding_id IS DISTINCT FROM OLD.case_binding_id
                       OR NEW.representation_evidence_id IS DISTINCT FROM
                            OLD.representation_evidence_id
                       OR NEW.action_id IS DISTINCT FROM OLD.action_id
                       OR NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
                       OR NEW.connector_id IS DISTINCT FROM OLD.connector_id
                       OR NEW.authorization_id IS DISTINCT FROM OLD.authorization_id
                       OR NEW.authorization_version IS DISTINCT FROM
                            OLD.authorization_version
                       OR NEW.task_code IS DISTINCT FROM OLD.task_code
                       OR NEW.requester_membership_id IS DISTINCT FROM
                            OLD.requester_membership_id
                       OR NEW.requester_principal_id IS DISTINCT FROM
                            OLD.requester_principal_id
                       OR NEW.requester_operator_id IS DISTINCT FROM
                            OLD.requester_operator_id
                       OR NEW.package_manifest IS DISTINCT FROM OLD.package_manifest
                       OR NEW.package_sha256 IS DISTINCT FROM OLD.package_sha256
                       OR NEW.due_at IS DISTINCT FROM OLD.due_at
                       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                        RAISE EXCEPTION 'A1-S task identity and package are frozen';
                    END IF;
                    IF NEW.version <> OLD.version + 1 THEN
                        RAISE EXCEPTION 'A1-S task version must increment once';
                    END IF;
                    IF OLD.external_reference IS NOT NULL
                       AND NEW.external_reference IS DISTINCT FROM
                            OLD.external_reference THEN
                        RAISE EXCEPTION 'A1-S external reference is write-once';
                    END IF;
                    IF OLD.status = 'prepared' AND NEW.status = 'assigned' THEN
                        IF OLD.assignee_membership_id IS NOT NULL
                           OR OLD.assignee_principal_id IS NOT NULL
                           OR OLD.assignee_operator_id IS NOT NULL
                           OR OLD.assigned_by_operator_id IS NOT NULL
                           OR OLD.assigned_at IS NOT NULL
                           OR NEW.assignee_membership_id IS NULL
                           OR NEW.assignee_principal_id IS NULL
                           OR NEW.assignee_operator_id IS NULL
                           OR NEW.assigned_by_operator_id IS NULL
                           OR NEW.assigned_at IS NULL THEN
                            RAISE EXCEPTION
                                'A1-S assignment must be one atomic null-to-value change';
                        END IF;
                        IF NOT EXISTS (
                            SELECT 1
                            FROM rtm_connect_a1s_memberships assigner
                            WHERE assigner.tenant_id = NEW.tenant_id
                              AND assigner.operator_id =
                                  NEW.assigned_by_operator_id
                              AND assigner.status = 'active'
                              AND assigner.synthetic_only = TRUE
                              AND assigner.role = 'supervisor'
                        ) THEN
                            RAISE EXCEPTION
                                'A1-S assignment requires active tenant supervisor';
                        END IF;
                    ELSIF NEW.assignee_membership_id IS DISTINCT FROM
                              OLD.assignee_membership_id
                       OR NEW.assignee_principal_id IS DISTINCT FROM
                              OLD.assignee_principal_id
                       OR NEW.assignee_operator_id IS DISTINCT FROM
                              OLD.assignee_operator_id
                       OR NEW.assigned_by_operator_id IS DISTINCT FROM
                              OLD.assigned_by_operator_id
                       OR NEW.assigned_at IS DISTINCT FROM OLD.assigned_at THEN
                        RAISE EXCEPTION 'A1-S assignment is write-once';
                    END IF;
                    IF NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at
                       AND NOT (
                           OLD.reviewed_at IS NULL
                           AND NEW.reviewed_at IS NOT NULL
                           AND (
                               (OLD.status = 'assigned'
                                    AND NEW.status = 'reviewing')
                               OR (OLD.status = 'reviewing'
                                    AND NEW.status = 'ready_for_release')
                           )
                       ) THEN
                        RAISE EXCEPTION 'A1-S reviewed_at is write-once';
                    END IF;
                    IF (
                        NEW.ready_at IS DISTINCT FROM OLD.ready_at
                        OR NEW.review_attestation_sha256 IS DISTINCT FROM
                            OLD.review_attestation_sha256
                    ) AND NOT (
                        OLD.status = 'reviewing'
                        AND NEW.status = 'ready_for_release'
                        AND OLD.ready_at IS NULL
                        AND NEW.ready_at IS NOT NULL
                        AND OLD.review_attestation_sha256 IS NULL
                        AND NEW.review_attestation_sha256 IS NOT NULL
                    ) THEN
                        RAISE EXCEPTION
                            'A1-S review readiness is write-once';
                    END IF;
                    IF OLD.status = 'reviewing'
                       AND NEW.status = 'ready_for_release'
                       AND NOT EXISTS (
                           SELECT 1
                           FROM rtm_connect_a1s_artifacts review_artifact
                           WHERE review_artifact.tenant_id = NEW.tenant_id
                             AND review_artifact.task_id = NEW.id
                             AND review_artifact.kind =
                                 'human_review_attestation'
                             AND review_artifact.sha256 =
                                 NEW.review_attestation_sha256
                             AND review_artifact.submitted_by_membership_id =
                                 NEW.assignee_membership_id
                             AND review_artifact.submitted_by_principal_id =
                                 NEW.assignee_principal_id
                             AND review_artifact.submitted_by_operator_id =
                                 NEW.assignee_operator_id
                             AND review_artifact.synthetic_only = TRUE
                       ) THEN
                        RAISE EXCEPTION
                            'A1-S review attestation artifact is missing';
                    END IF;
                    IF NEW.status = OLD.status THEN
                        IF ROW(
                            NEW.assignee_membership_id,
                            NEW.assignee_principal_id,
                            NEW.assignee_operator_id,
                            NEW.assigned_by_operator_id, NEW.assigned_at,
                            NEW.release_membership_id, NEW.release_principal_id,
                            NEW.release_operator_id,
                            NEW.verified_by_membership_id,
                            NEW.verified_by_principal_id,
                            NEW.verified_by_operator_id, NEW.due_at,
                            NEW.reviewed_at, NEW.ready_at, NEW.released_at,
                            NEW.started_at, NEW.awaiting_receipt_at,
                            NEW.unknown_at, NEW.reconciling_at,
                            NEW.receipt_submitted_at, NEW.verified_at,
                            NEW.completed_at, NEW.review_attestation_sha256,
                            NEW.release_attestation_sha256,
                            NEW.verification_attestation_sha256,
                            NEW.external_reference, NEW.metadata
                        ) IS DISTINCT FROM ROW(
                            OLD.assignee_membership_id,
                            OLD.assignee_principal_id,
                            OLD.assignee_operator_id,
                            OLD.assigned_by_operator_id, OLD.assigned_at,
                            OLD.release_membership_id, OLD.release_principal_id,
                            OLD.release_operator_id,
                            OLD.verified_by_membership_id,
                            OLD.verified_by_principal_id,
                            OLD.verified_by_operator_id, OLD.due_at,
                            OLD.reviewed_at, OLD.ready_at, OLD.released_at,
                            OLD.started_at, OLD.awaiting_receipt_at,
                            OLD.unknown_at, OLD.reconciling_at,
                            OLD.receipt_submitted_at, OLD.verified_at,
                            OLD.completed_at, OLD.review_attestation_sha256,
                            OLD.release_attestation_sha256,
                            OLD.verification_attestation_sha256,
                            OLD.external_reference, OLD.metadata
                        ) THEN
                            RAISE EXCEPTION
                                'A1-S checkpoint cannot mutate workflow fields';
                        END IF;
                    ELSIF NOT (
                        (OLD.status = 'prepared' AND NEW.status = 'assigned') OR
                        (OLD.status = 'assigned' AND NEW.status = 'reviewing') OR
                        (OLD.status = 'reviewing' AND NEW.status IN (
                            'ready_for_release', 'manual_review'
                        )) OR
                        (OLD.status = 'ready_for_release' AND NEW.status IN (
                            'released', 'manual_review'
                        )) OR
                        (OLD.status = 'released' AND NEW.status = 'in_progress') OR
                        (OLD.status = 'in_progress' AND NEW.status IN (
                            'awaiting_receipt', 'outcome_unknown', 'manual_review'
                        )) OR
                        (OLD.status = 'awaiting_receipt' AND NEW.status IN (
                            'receipt_submitted', 'outcome_unknown', 'manual_review'
                        )) OR
                        (OLD.status = 'outcome_unknown' AND NEW.status IN (
                            'reconciling', 'manual_review'
                        )) OR
                        (OLD.status = 'reconciling' AND NEW.status IN (
                            'outcome_unknown', 'receipt_submitted',
                            'manual_review', 'permanent_failed'
                        )) OR
                        (OLD.status = 'receipt_submitted' AND NEW.status IN (
                            'verified', 'manual_review'
                        )) OR
                        (OLD.status = 'verified' AND NEW.status = 'completed')
                    ) THEN
                        RAISE EXCEPTION 'Invalid A1-S task transition % -> %',
                            OLD.status, NEW.status;
                    END IF;
                END IF;

                IF NEW.status IN (
                    'released', 'in_progress', 'awaiting_receipt',
                    'outcome_unknown', 'reconciling', 'receipt_submitted',
                    'verified', 'completed', 'permanent_failed'
                ) THEN
                    SELECT COUNT(*) INTO approvals_count
                    FROM rtm_connect_a1s_approvals p
                    WHERE p.task_id = NEW.id AND p.tenant_id = NEW.tenant_id
                      AND p.decision = 'approved_frozen'
                      AND p.approval_type IN (
                          'release', 'verification_preapproval'
                      );
                    IF approvals_count <> 2 THEN
                        RAISE EXCEPTION 'A1-S requires two frozen pre-approvals';
                    END IF;
                    SELECT membership_id, principal_id, operator_id,
                           attestation_sha256, approved_at
                    INTO release_membership, release_principal,
                         release_operator, release_hash, release_time
                    FROM rtm_connect_a1s_approvals
                    WHERE task_id = NEW.id AND approval_type = 'release';
                    SELECT membership_id, principal_id, operator_id, approved_at
                    INTO verify_membership, verify_principal,
                         verify_operator, verify_time
                    FROM rtm_connect_a1s_approvals
                    WHERE task_id = NEW.id
                      AND approval_type = 'verification_preapproval';
                    IF release_principal = verify_principal
                       OR release_principal IN (
                           NEW.requester_principal_id,
                           NEW.assignee_principal_id
                       ) OR verify_principal IN (
                           NEW.requester_principal_id,
                           NEW.assignee_principal_id
                       ) OR NEW.release_membership_id <> release_membership
                       OR NEW.release_principal_id <> release_principal
                       OR NEW.release_operator_id <> release_operator
                       OR NEW.release_attestation_sha256 <> release_hash
                       OR release_time > NEW.released_at
                       OR verify_time > NEW.released_at THEN
                        RAISE EXCEPTION 'A1-S pre-operation separation failed';
                    END IF;
                END IF;

                IF NEW.status IN ('receipt_submitted', 'verified', 'completed')
                   AND NOT EXISTS (
                       SELECT 1 FROM rtm_connect_a1s_artifacts f
                       WHERE f.task_id = NEW.id AND f.tenant_id = NEW.tenant_id
                         AND f.kind = 'synthetic_receipt'
                         AND f.synthetic_only = TRUE
                   ) THEN
                    RAISE EXCEPTION 'A1-S synthetic receipt artifact is missing';
                END IF;

                IF NEW.status IN ('verified', 'completed') THEN
                    IF NEW.verified_by_membership_id <> verify_membership
                       OR NEW.verified_by_principal_id <> verify_principal
                       OR NEW.verified_by_operator_id <> verify_operator
                       OR NOT EXISTS (
                           SELECT 1
                           FROM rtm_connect_a1s_artifacts f
                           JOIN rtm_connect_a1s_artifacts receipt_artifact
                             ON receipt_artifact.id::text =
                                f.canonical_payload->>'receipt_artifact_id'
                            AND receipt_artifact.task_id = NEW.id
                            AND receipt_artifact.tenant_id = NEW.tenant_id
                            AND receipt_artifact.kind = 'synthetic_receipt'
                            AND receipt_artifact.synthetic_only = TRUE
                           JOIN rtm_connect_a1s_case_bindings receipt_binding
                             ON receipt_binding.id = NEW.case_binding_id
                            AND receipt_binding.tenant_id = NEW.tenant_id
                           JOIN rtm_connect_actions receipt_action
                             ON receipt_action.id = NEW.action_id
                            AND receipt_action.case_id = receipt_binding.case_id
                           JOIN rtm_connect_attempts receipt_attempt
                             ON receipt_attempt.id = NEW.attempt_id
                            AND receipt_attempt.action_id = receipt_action.id
                           JOIN rtm_connect_authorizations
                                receipt_authorization
                             ON receipt_authorization.id = NEW.authorization_id
                            AND receipt_authorization.action_id =
                                receipt_action.id
                            AND receipt_authorization.authorization_version =
                                NEW.authorization_version
                           JOIN documents receipt_document
                             ON receipt_document.case_id = receipt_binding.case_id
                            AND receipt_document.id::text =
                                receipt_artifact.canonical_payload->>'document_id'
                           WHERE f.task_id = NEW.id
                             AND f.tenant_id = NEW.tenant_id
                             AND f.kind = 'verification_attestation'
                             AND f.sha256 =
                                 NEW.verification_attestation_sha256
                             AND f.submitted_by_membership_id = verify_membership
                             AND f.submitted_by_principal_id = verify_principal
                             AND f.submitted_by_operator_id = verify_operator
                             AND f.synthetic_only = TRUE
                             AND f.canonical_payload->>'format' =
                                 'rtm.a1s.synthetic_receipt_verification.v1'
                             AND f.canonical_payload->>'task_id' = NEW.id::text
                             AND f.canonical_payload->>'action_id' =
                                 NEW.action_id::text
                             AND f.canonical_payload->>'authorization_id' =
                                 NEW.authorization_id::text
                             AND f.canonical_payload->>'receipt_sha256' =
                                 receipt_document.sha256
                             AND f.canonical_payload->>'external_reference' =
                                 NEW.external_reference
                             AND f.canonical_payload->>'package_sha256' =
                                 NEW.package_sha256
                             AND receipt_artifact.canonical_payload->>'format' =
                                 'rtm.a1s.synthetic_receipt.v1'
                             AND receipt_artifact.canonical_payload->>'tenant_id' =
                                 NEW.tenant_id::text
                             AND receipt_artifact.canonical_payload->>'task_id' =
                                 NEW.id::text
                             AND receipt_artifact.canonical_payload->>
                                 'case_binding_id' = NEW.case_binding_id::text
                             AND receipt_artifact.canonical_payload->>'case_id' =
                                 receipt_binding.case_id::text
                             AND receipt_artifact.canonical_payload->>'action_id' =
                                 receipt_action.id::text
                             AND receipt_artifact.canonical_payload->>'attempt_id' =
                                 receipt_attempt.id::text
                             AND receipt_artifact.canonical_payload->>
                                 'authorization_id' =
                                 receipt_authorization.id::text
                             AND receipt_artifact.canonical_payload->>
                                 'authorization_version' =
                                 receipt_authorization.authorization_version::text
                             AND receipt_artifact.canonical_payload->>
                                 'request_sha256' = receipt_action.payload_sha256
                             AND receipt_attempt.request_sha256 =
                                 receipt_action.payload_sha256
                             AND receipt_authorization.payload_sha256 =
                                 receipt_action.payload_sha256
                             AND receipt_artifact.canonical_payload->>
                                 'package_sha256' = NEW.package_sha256
                             AND receipt_artifact.canonical_payload->>
                                 'external_reference' = NEW.external_reference
                             AND receipt_artifact.canonical_payload->>
                                 'document_sha256' = receipt_document.sha256
                             AND receipt_document.kind =
                                 'rtm_connect_a1s_synthetic_receipt_fixture'
                             AND receipt_document.mime = 'application/json'
                             AND receipt_document.size_bytes BETWEEN 1 AND 65536
                             AND receipt_document.b2_bucket IS NULL
                             AND receipt_document.b2_key IS NULL
                             AND NOT (
                                 NEW.package_manifest->'document_hashes'
                                     ? receipt_document.sha256
                             )
                             AND NOT (
                                 NEW.package_manifest->'document_hashes'
                                     ? receipt_artifact.sha256
                             )
                             AND NOT (
                                 receipt_action.document_hashes
                                     ? receipt_document.sha256
                             )
                       ) THEN
                        RAISE EXCEPTION 'A1-S E4 verifier must match pre-approval';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """),
    ]


def _a1s_evidence_guard_ddl() -> list[tuple[str, str]]:
    return [
        ("a1s_artifact_scope_function", """
            CREATE OR REPLACE FUNCTION rtm_connect_a1s_artifact_scope_guard()
            RETURNS TRIGGER AS $$
            DECLARE
                task_status TEXT;
            BEGIN
                SELECT h.status INTO task_status
                FROM rtm_connect_a1s_human_tasks h
                JOIN rtm_connect_a1s_memberships m
                  ON m.id = NEW.submitted_by_membership_id
                 AND m.tenant_id = NEW.tenant_id
                 AND m.principal_id = NEW.submitted_by_principal_id
                 AND m.operator_id = NEW.submitted_by_operator_id
                WHERE h.id = NEW.task_id AND h.tenant_id = NEW.tenant_id
                  AND m.status = 'active' AND m.synthetic_only = TRUE;
                IF task_status IS NULL THEN
                    RAISE EXCEPTION 'Invalid A1-S artifact tenant or submitter';
                END IF;
                IF NEW.supersedes_artifact_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM rtm_connect_a1s_artifacts prior
                    WHERE prior.id = NEW.supersedes_artifact_id
                      AND prior.task_id = NEW.task_id
                      AND prior.tenant_id = NEW.tenant_id
                      AND prior.kind = NEW.kind
                ) THEN
                    RAISE EXCEPTION 'Invalid A1-S artifact supersession';
                END IF;
                IF NEW.kind IN (
                    'release_attestation',
                    'verification_preapproval_attestation'
                ) AND task_status <> 'ready_for_release' THEN
                    RAISE EXCEPTION 'A1-S approval artifacts require ready task';
                END IF;
                IF NEW.kind = 'synthetic_receipt' AND task_status NOT IN (
                    'awaiting_receipt', 'outcome_unknown', 'reconciling'
                ) THEN
                    RAISE EXCEPTION 'A1-S receipt is not currently expected';
                END IF;
                IF NEW.kind = 'synthetic_receipt' AND NOT EXISTS (
                    SELECT 1
                    FROM rtm_connect_a1s_human_tasks receipt_task
                    JOIN rtm_connect_a1s_case_bindings receipt_binding
                      ON receipt_binding.id = receipt_task.case_binding_id
                     AND receipt_binding.tenant_id = receipt_task.tenant_id
                    JOIN rtm_connect_actions receipt_action
                      ON receipt_action.id = receipt_task.action_id
                     AND receipt_action.case_id = receipt_binding.case_id
                    JOIN rtm_connect_attempts receipt_attempt
                      ON receipt_attempt.id = receipt_task.attempt_id
                     AND receipt_attempt.action_id = receipt_action.id
                    JOIN rtm_connect_authorizations receipt_authorization
                      ON receipt_authorization.id =
                          receipt_task.authorization_id
                     AND receipt_authorization.action_id = receipt_action.id
                     AND receipt_authorization.authorization_version =
                          receipt_task.authorization_version
                    JOIN documents receipt_document
                      ON receipt_document.case_id = receipt_binding.case_id
                    WHERE receipt_task.id = NEW.task_id
                      AND receipt_task.tenant_id = NEW.tenant_id
                      AND NEW.canonical_payload->>'format' =
                          'rtm.a1s.synthetic_receipt.v1'
                      AND NEW.canonical_payload->>'tenant_id' =
                          receipt_task.tenant_id::text
                      AND NEW.canonical_payload->>'task_id' =
                          receipt_task.id::text
                      AND NEW.canonical_payload->>'case_binding_id' =
                          receipt_binding.id::text
                      AND NEW.canonical_payload->>'case_id' =
                          receipt_binding.case_id::text
                      AND NEW.canonical_payload->>'action_id' =
                          receipt_action.id::text
                      AND NEW.canonical_payload->>'attempt_id' =
                          receipt_attempt.id::text
                      AND NEW.canonical_payload->>'authorization_id' =
                          receipt_authorization.id::text
                      AND NEW.canonical_payload->>'authorization_version' =
                          receipt_authorization.authorization_version::text
                      AND NEW.canonical_payload->>'request_sha256' =
                          receipt_action.payload_sha256
                      AND receipt_attempt.request_sha256 =
                          receipt_action.payload_sha256
                      AND receipt_authorization.payload_sha256 =
                          receipt_action.payload_sha256
                      AND NEW.canonical_payload->>'package_sha256' =
                          receipt_task.package_sha256
                      AND NEW.canonical_payload->>'external_reference' =
                          receipt_task.external_reference
                      AND NEW.canonical_payload->>'storage_backend' =
                          'database_manifest_only'
                      AND NEW.canonical_payload->>'b2_used' = 'false'
                      AND NEW.canonical_payload->>'network_used' = 'false'
                      AND NEW.canonical_payload->>
                          'legal_submission_executed' = 'false'
                      AND receipt_document.id::text =
                          NEW.canonical_payload->>'document_id'
                      AND receipt_document.sha256 =
                          NEW.canonical_payload->>'document_sha256'
                      AND receipt_document.kind =
                          'rtm_connect_a1s_synthetic_receipt_fixture'
                      AND receipt_document.mime = 'application/json'
                      AND receipt_document.size_bytes BETWEEN 1 AND 65536
                      AND receipt_document.b2_bucket IS NULL
                      AND receipt_document.b2_key IS NULL
                      AND jsonb_typeof(
                          receipt_task.package_manifest->'document_hashes'
                      ) = 'array'
                      AND NOT (
                          receipt_task.package_manifest->'document_hashes'
                              ? receipt_document.sha256
                      )
                      AND NOT (
                          receipt_task.package_manifest->'document_hashes'
                              ? NEW.sha256
                      )
                      AND NOT (
                          receipt_action.document_hashes
                              ? receipt_document.sha256
                      )
                ) THEN
                    RAISE EXCEPTION
                        'A1-S receipt artifact is not an inline case fixture';
                END IF;
                IF NEW.kind = 'verification_attestation'
                   AND task_status <> 'receipt_submitted' THEN
                    RAISE EXCEPTION 'A1-S E4 requires submitted receipt';
                END IF;
                IF NEW.verified_by_membership_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM rtm_connect_a1s_memberships verifier
                    WHERE verifier.id = NEW.verified_by_membership_id
                      AND verifier.tenant_id = NEW.tenant_id
                      AND verifier.principal_id = NEW.verified_by_principal_id
                      AND verifier.operator_id = NEW.verified_by_operator_id
                      AND verifier.status = 'active'
                      AND verifier.synthetic_only = TRUE
                      AND verifier.role IN ('verifier', 'supervisor')
                ) THEN
                    RAISE EXCEPTION 'Invalid A1-S artifact verifier';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """),
        ("a1s_approval_scope_function", """
            CREATE OR REPLACE FUNCTION rtm_connect_a1s_approval_scope_guard()
            RETURNS TRIGGER AS $$
            DECLARE
                task_row rtm_connect_a1s_human_tasks%ROWTYPE;
                actor_role TEXT;
                expected_kind TEXT;
            BEGIN
                SELECT * INTO task_row
                FROM rtm_connect_a1s_human_tasks
                WHERE id = NEW.task_id AND tenant_id = NEW.tenant_id
                FOR UPDATE;
                IF NOT FOUND OR task_row.status <> 'ready_for_release' THEN
                    RAISE EXCEPTION 'A1-S approvals require ready_for_release';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                    FROM rtm_connect_authorizations frozen_authorization
                    WHERE frozen_authorization.id = task_row.authorization_id
                      AND frozen_authorization.action_id = task_row.action_id
                      AND frozen_authorization.authorization_version =
                          task_row.authorization_version
                      AND frozen_authorization.decision = 'approved_frozen'
                      AND frozen_authorization.frozen = TRUE
                      AND frozen_authorization.revoked_at IS NULL
                      AND (frozen_authorization.expires_at IS NULL OR
                           frozen_authorization.expires_at > NOW())
                      AND jsonb_typeof(
                          frozen_authorization.approved_by_operator_ids
                      ) = 'array'
                      AND frozen_authorization.approved_by_operator_ids
                          ? NEW.operator_id::text
                ) THEN
                    RAISE EXCEPTION
                        'A1-S approval actor is outside frozen CORE authority';
                END IF;
                SELECT role INTO actor_role
                FROM rtm_connect_a1s_memberships
                WHERE id = NEW.membership_id AND tenant_id = NEW.tenant_id
                  AND principal_id = NEW.principal_id
                  AND operator_id = NEW.operator_id
                  AND status = 'active' AND synthetic_only = TRUE;
                IF actor_role IS NULL THEN
                    RAISE EXCEPTION 'Invalid A1-S approval membership';
                END IF;
                IF NEW.approval_type = 'release' THEN
                    expected_kind := 'release_attestation';
                    IF actor_role NOT IN ('releaser', 'supervisor') THEN
                        RAISE EXCEPTION 'A1-S release role required';
                    END IF;
                ELSE
                    expected_kind := 'verification_preapproval_attestation';
                    IF actor_role NOT IN ('verifier', 'supervisor') THEN
                        RAISE EXCEPTION 'A1-S verifier role required';
                    END IF;
                END IF;
                IF NEW.principal_id IN (
                    task_row.requester_principal_id,
                    task_row.assignee_principal_id
                ) THEN
                    RAISE EXCEPTION 'A1-S approval principal is not separated';
                END IF;
                IF NEW.approved_at < task_row.ready_at
                   OR NEW.approved_at > NOW() THEN
                    RAISE EXCEPTION 'A1-S approval timestamp is invalid';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM rtm_connect_a1s_artifacts f
                    WHERE f.id = NEW.artifact_id
                      AND f.tenant_id = NEW.tenant_id
                      AND f.task_id = NEW.task_id
                      AND f.kind = expected_kind
                      AND f.sha256 = NEW.attestation_sha256
                      AND f.submitted_by_membership_id = NEW.membership_id
                      AND f.submitted_by_principal_id = NEW.principal_id
                      AND f.submitted_by_operator_id = NEW.operator_id
                      AND f.synthetic_only = TRUE
                ) THEN
                    RAISE EXCEPTION 'A1-S approval artifact does not match actor';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """),
        ("a1s_event_scope_function", """
            CREATE OR REPLACE FUNCTION rtm_connect_a1s_event_scope_guard()
            RETURNS TRIGGER AS $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM rtm_connect_a1s_human_tasks h
                    WHERE h.id = NEW.task_id AND h.tenant_id = NEW.tenant_id
                      AND h.action_id = NEW.action_id
                      AND h.attempt_id = NEW.attempt_id
                ) THEN
                    RAISE EXCEPTION 'Invalid A1-S event scope';
                END IF;
                IF NEW.actor_type = 'operator' AND NOT EXISTS (
                    SELECT 1 FROM rtm_connect_a1s_memberships m
                    WHERE m.id = NEW.membership_id
                      AND m.tenant_id = NEW.tenant_id
                      AND m.principal_id = NEW.principal_id
                      AND m.operator_id = NEW.operator_id
                      AND m.status = 'active' AND m.synthetic_only = TRUE
                ) THEN
                    RAISE EXCEPTION 'Invalid A1-S event principal';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """),
        ("a1s_idempotency_guard_function", """
            CREATE OR REPLACE FUNCTION rtm_connect_a1s_idempotency_guard()
            RETURNS TRIGGER AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'A1-S idempotency claims cannot be deleted';
                END IF;
                IF TG_OP = 'INSERT' THEN
                    IF NEW.expires_at > NEW.created_at + INTERVAL '24 hours'
                       OR NOT EXISTS (
                            SELECT 1 FROM rtm_connect_a1s_memberships m
                            JOIN rtm_connect_a1s_tenants t
                              ON t.id = NEW.tenant_id
                            WHERE m.id = NEW.claimed_by_membership_id
                              AND m.tenant_id = NEW.tenant_id
                              AND m.principal_id = NEW.claimed_by_principal_id
                              AND m.operator_id = NEW.claimed_by_operator_id
                              AND m.status = 'active'
                              AND m.synthetic_only = TRUE
                              AND t.status = 'active'
                              AND t.synthetic_only = TRUE
                       ) THEN
                        RAISE EXCEPTION 'Invalid A1-S idempotency claim';
                    END IF;
                    IF NEW.task_id IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM rtm_connect_a1s_human_tasks h
                        WHERE h.id = NEW.task_id
                          AND h.tenant_id = NEW.tenant_id
                          AND (NEW.action_id IS NULL
                               OR h.action_id = NEW.action_id)
                    ) THEN
                        RAISE EXCEPTION 'Invalid A1-S idempotency task scope';
                    END IF;
                    RETURN NEW;
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
                   OR NEW.scope IS DISTINCT FROM OLD.scope
                   OR NEW.request_sha256 IS DISTINCT FROM OLD.request_sha256
                   OR NEW.claimed_by_membership_id IS DISTINCT FROM
                        OLD.claimed_by_membership_id
                   OR NEW.claimed_by_principal_id IS DISTINCT FROM
                        OLD.claimed_by_principal_id
                   OR NEW.claimed_by_operator_id IS DISTINCT FROM
                        OLD.claimed_by_operator_id
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
                   OR NEW.metadata IS DISTINCT FROM OLD.metadata
                   OR NEW.replay_count < OLD.replay_count THEN
                    RAISE EXCEPTION 'A1-S idempotency identity is frozen';
                END IF;
                IF OLD.status = 'claimed' THEN
                    IF NEW.status NOT IN ('completed', 'conflict') THEN
                        RAISE EXCEPTION 'Invalid A1-S idempotency completion';
                    END IF;
                    IF OLD.task_id IS NOT NULL OR OLD.action_id IS NOT NULL
                       OR NEW.task_id IS NULL OR NEW.action_id IS NULL
                       OR NOT EXISTS (
                           SELECT 1 FROM rtm_connect_a1s_human_tasks h
                           WHERE h.id = NEW.task_id
                             AND h.tenant_id = NEW.tenant_id
                             AND h.action_id = NEW.action_id
                       ) THEN
                        RAISE EXCEPTION 'Invalid A1-S one-shot claim binding';
                    END IF;
                ELSIF NEW.status <> OLD.status
                      OR NEW.task_id IS DISTINCT FROM OLD.task_id
                      OR NEW.action_id IS DISTINCT FROM OLD.action_id
                      OR NEW.response_sha256 IS DISTINCT FROM
                           OLD.response_sha256
                      OR NEW.completed_at IS DISTINCT FROM OLD.completed_at THEN
                    RAISE EXCEPTION 'Completed A1-S idempotency is frozen';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """),
    ]


def _a1s_trigger_ddl() -> list[tuple[str, str]]:
    return [
        ("a1s_tenant_frozen_trigger", """
            CREATE OR REPLACE TRIGGER trg_rtm_connect_a1s_tenant_frozen
            BEFORE UPDATE OR DELETE ON rtm_connect_a1s_tenants
            FOR EACH ROW EXECUTE FUNCTION rtm_connect_a1s_tenant_frozen_guard();
        """),
        ("a1s_membership_guard_trigger", """
            CREATE OR REPLACE TRIGGER trg_rtm_connect_a1s_membership_guard
            BEFORE INSERT OR UPDATE OR DELETE ON rtm_connect_a1s_memberships
            FOR EACH ROW EXECUTE FUNCTION rtm_connect_a1s_membership_guard();
        """),
        ("a1s_binding_frozen_trigger", """
            CREATE OR REPLACE TRIGGER trg_rtm_connect_a1s_case_binding_frozen
            BEFORE INSERT OR UPDATE OR DELETE
            ON rtm_connect_a1s_case_bindings
            FOR EACH ROW EXECUTE FUNCTION rtm_connect_a1s_binding_frozen_guard();
        """),
        ("a1s_representation_frozen_trigger", """
            CREATE OR REPLACE TRIGGER
                trg_rtm_connect_a1s_representation_frozen
            BEFORE INSERT OR UPDATE OR DELETE
            ON rtm_connect_a1s_representation_evidence
            FOR EACH ROW EXECUTE FUNCTION
                rtm_connect_a1s_representation_frozen_guard();
        """),
        ("a1s_task_guard_trigger", """
            CREATE OR REPLACE TRIGGER trg_rtm_connect_a1s_task_guard
            BEFORE INSERT OR UPDATE OR DELETE
            ON rtm_connect_a1s_human_tasks
            FOR EACH ROW EXECUTE FUNCTION rtm_connect_a1s_task_guard();
        """),
        ("a1s_artifact_scope_trigger", """
            CREATE OR REPLACE TRIGGER
                trg_rtm_connect_a1s_artifact_scope_guard
            BEFORE INSERT ON rtm_connect_a1s_artifacts
            FOR EACH ROW EXECUTE FUNCTION
                rtm_connect_a1s_artifact_scope_guard();
        """),
        ("a1s_artifact_append_trigger", """
            CREATE OR REPLACE TRIGGER
                trg_rtm_connect_a1s_artifact_append_only
            BEFORE UPDATE OR DELETE ON rtm_connect_a1s_artifacts
            FOR EACH ROW EXECUTE FUNCTION rtm_connect_a1s_reject_mutation();
        """),
        ("a1s_approval_scope_trigger", """
            CREATE OR REPLACE TRIGGER
                trg_rtm_connect_a1s_approval_scope_guard
            BEFORE INSERT ON rtm_connect_a1s_approvals
            FOR EACH ROW EXECUTE FUNCTION
                rtm_connect_a1s_approval_scope_guard();
        """),
        ("a1s_approval_append_trigger", """
            CREATE OR REPLACE TRIGGER
                trg_rtm_connect_a1s_approval_append_only
            BEFORE UPDATE OR DELETE ON rtm_connect_a1s_approvals
            FOR EACH ROW EXECUTE FUNCTION rtm_connect_a1s_reject_mutation();
        """),
        ("a1s_event_scope_trigger", """
            CREATE OR REPLACE TRIGGER trg_rtm_connect_a1s_event_scope_guard
            BEFORE INSERT ON rtm_connect_a1s_events
            FOR EACH ROW EXECUTE FUNCTION rtm_connect_a1s_event_scope_guard();
        """),
        ("a1s_event_append_trigger", """
            CREATE OR REPLACE TRIGGER trg_rtm_connect_a1s_event_append_only
            BEFORE UPDATE OR DELETE ON rtm_connect_a1s_events
            FOR EACH ROW EXECUTE FUNCTION rtm_connect_a1s_reject_mutation();
        """),
        ("a1s_idempotency_guard_trigger", """
            CREATE OR REPLACE TRIGGER trg_rtm_connect_a1s_idempotency_guard
            BEFORE INSERT OR UPDATE OR DELETE ON rtm_connect_a1s_idempotency
            FOR EACH ROW EXECUTE FUNCTION rtm_connect_a1s_idempotency_guard();
        """),
    ]


def connect_a1s_human_filing_ddl() -> list[tuple[str, str]]:
    """Devuelve DDL A1-S aditivo, repetible y sin seeds persistentes."""

    return [
        ("extensions", "CREATE EXTENSION IF NOT EXISTS pgcrypto;"),
        *_a1s_table_ddl(),
        *_a1s_workflow_table_ddl(),
        *_a1s_evidence_table_ddl(),
        *_a1s_guard_function_ddl(),
        *_a1s_task_guard_ddl(),
        *_a1s_evidence_guard_ddl(),
        *_a1s_trigger_ddl(),
    ]


__all__ = [
    "RTM_CONNECT_A1S_SCHEMA_VERSION",
    "CONNECT_A1S_REQUIRED_COLUMNS",
    "CONNECT_A1S_REQUIRED_CONSTRAINTS",
    "CONNECT_A1S_REQUIRED_INDEXES",
    "CONNECT_A1S_REQUIRED_TRIGGERS",
    "HUMAN_FILING_APPROVAL_TYPES",
    "HUMAN_FILING_ARTIFACT_KINDS",
    "HUMAN_FILING_TASK_STATUSES",
    "connect_a1s_human_filing_ddl",
]
