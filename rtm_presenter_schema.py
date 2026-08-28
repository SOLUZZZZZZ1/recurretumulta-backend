"""Esquema persistente y fail-closed de RTM Presenter.

El modulo define exclusivamente DDL PostgreSQL aditivo e idempotente. No
publica rutas, no crea identidades, no guarda secretos de portal y no se monta
desde :mod:`app`. Los objetos de Backblaze siguen custodiados por ``documents``;
Presenter conserva solo la referencia interna y las huellas necesarias para
congelar exactamente lo que un operador entrega a una sede.
"""

from __future__ import annotations

from typing import Any


RTM_PRESENTER_SCHEMA_VERSION = "rtm_presenter_schema_v1_1"
RTM_PRESENTER_EXTENSION_CLIENT_ID = "rtm.presenter.browser_extension.v1"

PRESENTER_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "rtm_presenter_document_versions": {
        "id",
        "case_id",
        "logical_document_id",
        "version_number",
        "supersedes_version_id",
        "source_document_id",
        "sha256",
        "purpose",
        "state",
        "scan_status",
        "original_filename",
        "detected_mime",
        "size_bytes",
        "source_kind",
        "created_by_operator_id",
        "created_at",
        "metadata",
    },
    "rtm_presenter_destination_profiles": {
        "id",
        "profile_code",
        "version_number",
        "status",
        "authority_code",
        "display_name",
        "portal_origin",
        "requirements",
        "profile_sha256",
        "created_by_operator_id",
        "verified_by_operator_id",
        "verified_at",
        "created_at",
        "metadata",
    },
    "rtm_presenter_filing_packages": {
        "id",
        "case_id",
        "logical_package_id",
        "package_version",
        "supersedes_package_id",
        "destination_profile_id",
        "representation_mode",
        "authorization_document_version_id",
        "status",
        "manifest",
        "manifest_sha256",
        "expected_item_count",
        "created_by_operator_id",
        "frozen_by_operator_id",
        "frozen_at",
        "expires_at",
        "created_at",
        "metadata",
    },
    "rtm_presenter_idempotency_keys": {
        "id",
        "operator_id",
        "idempotency_key",
        "request_sha256",
        "case_id",
        "package_id",
        "created_at",
    },
    "rtm_presenter_package_items": {
        "id",
        "package_id",
        "case_id",
        "item_order",
        "document_version_id",
        "document_sha256",
        "field_code",
        "purpose",
        "portal_filename",
        "required",
        "item_manifest",
        "item_sha256",
        "created_at",
    },
    "rtm_presenter_handoff_tickets": {
        "id",
        "ticket_hash",
        "operator_id",
        "operator_session_id",
        "extension_client_id",
        "case_id",
        "package_id",
        "package_item_id",
        "portal_origin",
        "field_code",
        "issued_at",
        "expires_at",
        "used_at",
        "created_at",
    },
    "rtm_presenter_admin_exports": {
        "id",
        "case_id",
        "package_id",
        "admin_operator_id",
        "reason",
        "reauthenticated_at",
        "reauthentication_evidence_sha256",
        "export_scope",
        "watermark",
        "watermark_sha256",
        "source_hashes",
        "manifest_sha256",
        "export_sha256",
        "export_document_id",
        "created_at",
        "expires_at",
    },
    "rtm_presenter_audit_events": {
        "id",
        "sequence_number",
        "case_id",
        "package_id",
        "package_item_id",
        "handoff_ticket_id",
        "admin_export_id",
        "actor_type",
        "actor_operator_id",
        "event_type",
        "reason_code",
        "payload",
        "payload_sha256",
        "created_at",
    },
}

# ``udt_name`` values reported by ``information_schema.columns``.  Keeping the
# expected type next to the schema contract prevents a same-named view/table or
# a column recreated with a merely coercible type from passing runtime
# readiness.
PRESENTER_REQUIRED_COLUMN_TYPES: dict[str, dict[str, str]] = {
    "rtm_presenter_document_versions": {
        "id": "uuid",
        "case_id": "uuid",
        "logical_document_id": "uuid",
        "version_number": "int4",
        "supersedes_version_id": "uuid",
        "source_document_id": "uuid",
        "sha256": "text",
        "purpose": "text",
        "state": "text",
        "scan_status": "text",
        "original_filename": "text",
        "detected_mime": "text",
        "size_bytes": "int8",
        "source_kind": "text",
        "created_by_operator_id": "uuid",
        "created_at": "timestamptz",
        "metadata": "jsonb",
    },
    "rtm_presenter_destination_profiles": {
        "id": "uuid",
        "profile_code": "text",
        "version_number": "int4",
        "status": "text",
        "authority_code": "text",
        "display_name": "text",
        "portal_origin": "text",
        "requirements": "jsonb",
        "profile_sha256": "text",
        "created_by_operator_id": "uuid",
        "verified_by_operator_id": "uuid",
        "verified_at": "timestamptz",
        "created_at": "timestamptz",
        "metadata": "jsonb",
    },
    "rtm_presenter_filing_packages": {
        "id": "uuid",
        "case_id": "uuid",
        "logical_package_id": "uuid",
        "package_version": "int4",
        "supersedes_package_id": "uuid",
        "destination_profile_id": "uuid",
        "representation_mode": "text",
        "authorization_document_version_id": "uuid",
        "status": "text",
        "manifest": "jsonb",
        "manifest_sha256": "text",
        "expected_item_count": "int4",
        "created_by_operator_id": "uuid",
        "frozen_by_operator_id": "uuid",
        "frozen_at": "timestamptz",
        "expires_at": "timestamptz",
        "created_at": "timestamptz",
        "metadata": "jsonb",
    },
    "rtm_presenter_idempotency_keys": {
        "id": "uuid",
        "operator_id": "uuid",
        "idempotency_key": "text",
        "request_sha256": "text",
        "case_id": "uuid",
        "package_id": "uuid",
        "created_at": "timestamptz",
    },
    "rtm_presenter_package_items": {
        "id": "uuid",
        "package_id": "uuid",
        "case_id": "uuid",
        "item_order": "int4",
        "document_version_id": "uuid",
        "document_sha256": "text",
        "field_code": "text",
        "purpose": "text",
        "portal_filename": "text",
        "required": "bool",
        "item_manifest": "jsonb",
        "item_sha256": "text",
        "created_at": "timestamptz",
    },
    "rtm_presenter_handoff_tickets": {
        "id": "uuid",
        "ticket_hash": "text",
        "operator_id": "uuid",
        "operator_session_id": "uuid",
        "extension_client_id": "text",
        "case_id": "uuid",
        "package_id": "uuid",
        "package_item_id": "uuid",
        "portal_origin": "text",
        "field_code": "text",
        "issued_at": "timestamptz",
        "expires_at": "timestamptz",
        "used_at": "timestamptz",
        "created_at": "timestamptz",
    },
    "rtm_presenter_admin_exports": {
        "id": "uuid",
        "case_id": "uuid",
        "package_id": "uuid",
        "admin_operator_id": "uuid",
        "reason": "text",
        "reauthenticated_at": "timestamptz",
        "reauthentication_evidence_sha256": "text",
        "export_scope": "jsonb",
        "watermark": "text",
        "watermark_sha256": "text",
        "source_hashes": "jsonb",
        "manifest_sha256": "text",
        "export_sha256": "text",
        "export_document_id": "uuid",
        "created_at": "timestamptz",
        "expires_at": "timestamptz",
    },
    "rtm_presenter_audit_events": {
        "id": "uuid",
        "sequence_number": "int8",
        "case_id": "uuid",
        "package_id": "uuid",
        "package_item_id": "uuid",
        "handoff_ticket_id": "uuid",
        "admin_export_id": "uuid",
        "actor_type": "text",
        "actor_operator_id": "uuid",
        "event_type": "text",
        "reason_code": "text",
        "payload": "jsonb",
        "payload_sha256": "text",
        "created_at": "timestamptz",
    },
}

PRESENTER_REQUIRED_INDEXES = {
    "uq_rtm_presenter_document_version",
    "uq_rtm_presenter_document_source",
    "idx_rtm_presenter_document_case_state",
    "uq_rtm_presenter_destination_profile_version",
    "idx_rtm_presenter_destination_profile_resolution",
    "uq_rtm_presenter_package_version",
    "idx_rtm_presenter_package_case_status",
    "idx_rtm_presenter_package_destination",
    "uq_rtm_presenter_idempotency_operator_key",
    "uq_rtm_presenter_package_item_order",
    "uq_rtm_presenter_package_item_document",
    "idx_rtm_presenter_package_item_field",
    "uq_rtm_presenter_handoff_ticket_hash",
    "idx_rtm_presenter_handoff_expiry",
    "idx_rtm_presenter_handoff_session",
    "idx_rtm_presenter_admin_export_case",
    "idx_rtm_presenter_admin_export_admin",
    "uq_rtm_presenter_audit_sequence",
    "idx_rtm_presenter_audit_case",
    "idx_rtm_presenter_audit_package",
}

PRESENTER_REQUIRED_TRIGGERS = {
    "trg_rtm_presenter_document_version_scope",
    "trg_rtm_presenter_document_version_append_only",
    "trg_rtm_presenter_destination_profile_scope",
    "trg_rtm_presenter_destination_profile_append_only",
    "trg_rtm_presenter_filing_package_guard",
    "trg_rtm_presenter_idempotency_scope",
    "trg_rtm_presenter_idempotency_append_only",
    "trg_rtm_presenter_package_item_guard",
    "trg_rtm_presenter_handoff_ticket_guard",
    "trg_rtm_presenter_admin_export_scope",
    "trg_rtm_presenter_admin_export_append_only",
    "trg_rtm_presenter_audit_event_scope",
    "trg_rtm_presenter_audit_event_append_only",
}

PRESENTER_REQUIRED_CONSTRAINTS = {
    "ck_rtm_presenter_document_version_number",
    "ck_rtm_presenter_document_hash",
    "ck_rtm_presenter_document_purpose",
    "ck_rtm_presenter_document_state",
    "ck_rtm_presenter_document_scan",
    "ck_rtm_presenter_document_scan_state",
    "ck_rtm_presenter_document_filename",
    "ck_rtm_presenter_document_mime",
    "ck_rtm_presenter_document_size",
    "ck_rtm_presenter_document_source_kind",
    "ck_rtm_presenter_document_metadata",
    "ck_rtm_presenter_profile_code",
    "ck_rtm_presenter_profile_version",
    "ck_rtm_presenter_profile_status",
    "ck_rtm_presenter_profile_origin",
    "ck_rtm_presenter_profile_hash",
    "ck_rtm_presenter_profile_verification",
    "ck_rtm_presenter_profile_payload",
    "ck_rtm_presenter_package_version",
    "ck_rtm_presenter_package_status",
    "ck_rtm_presenter_package_representation",
    "ck_rtm_presenter_package_hash",
    "ck_rtm_presenter_package_freeze",
    "ck_rtm_presenter_package_manifest",
    "ck_rtm_presenter_package_expiry",
    "ck_rtm_presenter_idempotency_key",
    "ck_rtm_presenter_idempotency_hash",
    "ck_rtm_presenter_item_order",
    "ck_rtm_presenter_item_hashes",
    "ck_rtm_presenter_item_field",
    "ck_rtm_presenter_item_filename",
    "ck_rtm_presenter_item_manifest",
    "ck_rtm_presenter_ticket_hash",
    "ck_rtm_presenter_ticket_extension",
    "ck_rtm_presenter_ticket_origin",
    "ck_rtm_presenter_ticket_ttl",
    "ck_rtm_presenter_ticket_use",
    "ck_rtm_presenter_export_reason",
    "ck_rtm_presenter_export_hashes",
    "ck_rtm_presenter_export_payloads",
    "ck_rtm_presenter_export_reauthentication",
    "ck_rtm_presenter_export_expiry",
    "ck_rtm_presenter_audit_actor",
    "ck_rtm_presenter_audit_event_type",
    "ck_rtm_presenter_audit_payload",
}

PRESENTER_REQUIRED_INDEX_TABLES = {
    name: table_name
    for table_name, names in {
        "rtm_presenter_document_versions": {
            "uq_rtm_presenter_document_version",
            "uq_rtm_presenter_document_source",
            "idx_rtm_presenter_document_case_state",
        },
        "rtm_presenter_destination_profiles": {
            "uq_rtm_presenter_destination_profile_version",
            "idx_rtm_presenter_destination_profile_resolution",
        },
        "rtm_presenter_filing_packages": {
            "uq_rtm_presenter_package_version",
            "idx_rtm_presenter_package_case_status",
            "idx_rtm_presenter_package_destination",
        },
        "rtm_presenter_idempotency_keys": {
            "uq_rtm_presenter_idempotency_operator_key",
        },
        "rtm_presenter_package_items": {
            "uq_rtm_presenter_package_item_order",
            "uq_rtm_presenter_package_item_document",
            "idx_rtm_presenter_package_item_field",
        },
        "rtm_presenter_handoff_tickets": {
            "uq_rtm_presenter_handoff_ticket_hash",
            "idx_rtm_presenter_handoff_expiry",
            "idx_rtm_presenter_handoff_session",
        },
        "rtm_presenter_admin_exports": {
            "idx_rtm_presenter_admin_export_case",
            "idx_rtm_presenter_admin_export_admin",
        },
        "rtm_presenter_audit_events": {
            "uq_rtm_presenter_audit_sequence",
            "idx_rtm_presenter_audit_case",
            "idx_rtm_presenter_audit_package",
        },
    }.items()
    for name in names
}

PRESENTER_REQUIRED_TRIGGER_BINDINGS: dict[str, tuple[str, str]] = {
    "trg_rtm_presenter_document_version_scope": (
        "rtm_presenter_document_versions",
        "rtm_presenter_document_version_scope_guard",
    ),
    "trg_rtm_presenter_document_version_append_only": (
        "rtm_presenter_document_versions",
        "rtm_presenter_reject_mutation",
    ),
    "trg_rtm_presenter_destination_profile_scope": (
        "rtm_presenter_destination_profiles",
        "rtm_presenter_destination_profile_scope_guard",
    ),
    "trg_rtm_presenter_destination_profile_append_only": (
        "rtm_presenter_destination_profiles",
        "rtm_presenter_reject_mutation",
    ),
    "trg_rtm_presenter_filing_package_guard": (
        "rtm_presenter_filing_packages",
        "rtm_presenter_filing_package_guard",
    ),
    "trg_rtm_presenter_idempotency_scope": (
        "rtm_presenter_idempotency_keys",
        "rtm_presenter_idempotency_scope_guard",
    ),
    "trg_rtm_presenter_idempotency_append_only": (
        "rtm_presenter_idempotency_keys",
        "rtm_presenter_reject_mutation",
    ),
    "trg_rtm_presenter_package_item_guard": (
        "rtm_presenter_package_items",
        "rtm_presenter_package_item_guard",
    ),
    "trg_rtm_presenter_handoff_ticket_guard": (
        "rtm_presenter_handoff_tickets",
        "rtm_presenter_handoff_ticket_guard",
    ),
    "trg_rtm_presenter_admin_export_scope": (
        "rtm_presenter_admin_exports",
        "rtm_presenter_admin_export_scope_guard",
    ),
    "trg_rtm_presenter_admin_export_append_only": (
        "rtm_presenter_admin_exports",
        "rtm_presenter_reject_mutation",
    ),
    "trg_rtm_presenter_audit_event_scope": (
        "rtm_presenter_audit_events",
        "rtm_presenter_audit_event_scope_guard",
    ),
    "trg_rtm_presenter_audit_event_append_only": (
        "rtm_presenter_audit_events",
        "rtm_presenter_reject_mutation",
    ),
}

PRESENTER_REQUIRED_CONSTRAINT_TABLES = {
    name: table_name
    for table_name, names in {
        "rtm_presenter_document_versions": {
            "ck_rtm_presenter_document_version_number",
            "ck_rtm_presenter_document_hash",
            "ck_rtm_presenter_document_purpose",
            "ck_rtm_presenter_document_state",
            "ck_rtm_presenter_document_scan",
            "ck_rtm_presenter_document_scan_state",
            "ck_rtm_presenter_document_filename",
            "ck_rtm_presenter_document_mime",
            "ck_rtm_presenter_document_size",
            "ck_rtm_presenter_document_source_kind",
            "ck_rtm_presenter_document_metadata",
        },
        "rtm_presenter_destination_profiles": {
            "ck_rtm_presenter_profile_code",
            "ck_rtm_presenter_profile_version",
            "ck_rtm_presenter_profile_status",
            "ck_rtm_presenter_profile_origin",
            "ck_rtm_presenter_profile_hash",
            "ck_rtm_presenter_profile_verification",
            "ck_rtm_presenter_profile_payload",
        },
        "rtm_presenter_filing_packages": {
            "ck_rtm_presenter_package_version",
            "ck_rtm_presenter_package_status",
            "ck_rtm_presenter_package_representation",
            "ck_rtm_presenter_package_hash",
            "ck_rtm_presenter_package_freeze",
            "ck_rtm_presenter_package_manifest",
            "ck_rtm_presenter_package_expiry",
        },
        "rtm_presenter_idempotency_keys": {
            "ck_rtm_presenter_idempotency_key",
            "ck_rtm_presenter_idempotency_hash",
        },
        "rtm_presenter_package_items": {
            "ck_rtm_presenter_item_order",
            "ck_rtm_presenter_item_hashes",
            "ck_rtm_presenter_item_field",
            "ck_rtm_presenter_item_filename",
            "ck_rtm_presenter_item_manifest",
        },
        "rtm_presenter_handoff_tickets": {
            "ck_rtm_presenter_ticket_hash",
            "ck_rtm_presenter_ticket_extension",
            "ck_rtm_presenter_ticket_origin",
            "ck_rtm_presenter_ticket_ttl",
            "ck_rtm_presenter_ticket_use",
        },
        "rtm_presenter_admin_exports": {
            "ck_rtm_presenter_export_reason",
            "ck_rtm_presenter_export_hashes",
            "ck_rtm_presenter_export_payloads",
            "ck_rtm_presenter_export_reauthentication",
            "ck_rtm_presenter_export_expiry",
        },
        "rtm_presenter_audit_events": {
            "ck_rtm_presenter_audit_actor",
            "ck_rtm_presenter_audit_event_type",
            "ck_rtm_presenter_audit_payload",
        },
    }.items()
    for name in names
}

PRESENTER_REQUIRED_FUNCTIONS = {
    function_name
    for _, function_name in PRESENTER_REQUIRED_TRIGGER_BINDINGS.values()
}


def _table_ddl() -> list[tuple[str, str]]:
    return [
        (
            "presenter_document_versions",
            """
            CREATE TABLE IF NOT EXISTS rtm_presenter_document_versions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                case_id UUID NOT NULL REFERENCES cases(id) ON DELETE RESTRICT,
                logical_document_id UUID NOT NULL,
                version_number INTEGER NOT NULL,
                supersedes_version_id UUID
                    REFERENCES rtm_presenter_document_versions(id)
                    ON DELETE RESTRICT,
                source_document_id UUID NOT NULL
                    REFERENCES documents(id) ON DELETE RESTRICT,
                sha256 TEXT NOT NULL,
                purpose TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'draft',
                scan_status TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                detected_mime TEXT NOT NULL,
                size_bytes BIGINT NOT NULL,
                source_kind TEXT NOT NULL,
                created_by_operator_id UUID NOT NULL
                    REFERENCES rtm_operators(id) ON DELETE RESTRICT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                CONSTRAINT ck_rtm_presenter_document_version_number CHECK (
                    version_number > 0
                    AND ((version_number = 1 AND supersedes_version_id IS NULL)
                        OR (version_number > 1
                            AND supersedes_version_id IS NOT NULL))
                ),
                CONSTRAINT ck_rtm_presenter_document_hash CHECK (
                    sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_presenter_document_purpose CHECK (
                    purpose ~ '^[a-z][a-z0-9_.-]{2,63}$'
                ),
                CONSTRAINT ck_rtm_presenter_document_state CHECK (
                    state IN (
                        'draft', 'review', 'active', 'superseded',
                        'rejected', 'quarantined'
                    )
                ),
                CONSTRAINT ck_rtm_presenter_document_scan CHECK (
                    scan_status IN ('pending', 'clean', 'blocked', 'error')
                ),
                CONSTRAINT ck_rtm_presenter_document_scan_state CHECK (
                    (state = 'active' AND scan_status = 'clean')
                    OR (state = 'quarantined'
                        AND scan_status IN ('blocked', 'error'))
                    OR state IN ('draft', 'review', 'superseded', 'rejected')
                ),
                CONSTRAINT ck_rtm_presenter_document_filename CHECK (
                    length(original_filename) BETWEEN 1 AND 255
                    AND original_filename !~ '[\\/\\x00-\\x1f]'
                ),
                CONSTRAINT ck_rtm_presenter_document_mime CHECK (
                    detected_mime ~
                        '^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]{0,126}$'
                ),
                CONSTRAINT ck_rtm_presenter_document_size CHECK (
                    size_bytes > 0 AND size_bytes <= 52428800
                ),
                CONSTRAINT ck_rtm_presenter_document_source_kind CHECK (
                    source_kind IN (
                        'customer_upload', 'operator_upload', 'generated',
                        'external_revision', 'derived_for_portal',
                        'authorization', 'receipt', 'legacy_backfill'
                    )
                ),
                CONSTRAINT ck_rtm_presenter_document_metadata CHECK (
                    jsonb_typeof(metadata) = 'object'
                    AND NOT metadata ?| ARRAY[
                        'b2_bucket', 'b2_key', 'presigned_url', 'password',
                        'access_token', 'refresh_token', 'cookie', 'secret'
                    ]
                )
            );
            """,
        ),
        (
            "presenter_document_version_index",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_presenter_document_version
            ON rtm_presenter_document_versions(
                case_id, logical_document_id, version_number
            );
            """,
        ),
        (
            "presenter_document_source_index",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_presenter_document_source
            ON rtm_presenter_document_versions(case_id, source_document_id);
            """,
        ),
        (
            "presenter_document_case_index",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_presenter_document_case_state
            ON rtm_presenter_document_versions(
                case_id, state, purpose, created_at DESC
            );
            """,
        ),
        (
            "presenter_destination_profiles",
            """
            CREATE TABLE IF NOT EXISTS rtm_presenter_destination_profiles (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                profile_code TEXT NOT NULL,
                version_number INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                authority_code TEXT NOT NULL,
                display_name TEXT NOT NULL,
                portal_origin TEXT NOT NULL,
                requirements JSONB NOT NULL,
                profile_sha256 TEXT NOT NULL,
                created_by_operator_id UUID NOT NULL
                    REFERENCES rtm_operators(id) ON DELETE RESTRICT,
                verified_by_operator_id UUID
                    REFERENCES rtm_operators(id) ON DELETE RESTRICT,
                verified_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                CONSTRAINT ck_rtm_presenter_profile_code CHECK (
                    profile_code ~ '^[a-z][a-z0-9_.-]{2,95}$'
                    AND authority_code ~ '^[a-z][a-z0-9_.-]{2,95}$'
                ),
                CONSTRAINT ck_rtm_presenter_profile_version CHECK (
                    version_number > 0
                ),
                CONSTRAINT ck_rtm_presenter_profile_status CHECK (
                    status IN ('draft', 'active', 'retired')
                ),
                CONSTRAINT ck_rtm_presenter_profile_origin CHECK (
                    portal_origin ~
                        '^https://[A-Za-z0-9.-]+(:[0-9]{2,5})?$'
                    AND portal_origin !~ '[/?#]$'
                ),
                CONSTRAINT ck_rtm_presenter_profile_hash CHECK (
                    profile_sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_presenter_profile_verification CHECK (
                    (status = 'draft' AND verified_by_operator_id IS NULL
                        AND verified_at IS NULL)
                    OR (status IN ('active', 'retired')
                        AND verified_by_operator_id IS NOT NULL
                        AND verified_at IS NOT NULL)
                ),
                CONSTRAINT ck_rtm_presenter_profile_payload CHECK (
                    length(display_name) BETWEEN 3 AND 160
                    AND jsonb_typeof(requirements) = 'object'
                    AND jsonb_typeof(metadata) = 'object'
                    AND NOT requirements ?| ARRAY[
                        'password', 'access_token', 'refresh_token', 'cookie',
                        'secret', 'private_key', 'credential_ref',
                        'b2_bucket', 'b2_key', 'presigned_url'
                    ]
                    AND NOT metadata ?| ARRAY[
                        'password', 'access_token', 'refresh_token', 'cookie',
                        'secret', 'private_key', 'credential_ref',
                        'b2_bucket', 'b2_key', 'presigned_url'
                    ]
                )
            );
            """,
        ),
        (
            "presenter_destination_profile_version_index",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_presenter_destination_profile_version
            ON rtm_presenter_destination_profiles(
                profile_code, version_number
            );
            """,
        ),
        (
            "presenter_destination_profile_resolution_index",
            """
            CREATE INDEX IF NOT EXISTS
                idx_rtm_presenter_destination_profile_resolution
            ON rtm_presenter_destination_profiles(
                profile_code, status, version_number DESC
            );
            """,
        ),
        (
            "presenter_filing_packages",
            """
            CREATE TABLE IF NOT EXISTS rtm_presenter_filing_packages (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                case_id UUID NOT NULL REFERENCES cases(id) ON DELETE RESTRICT,
                logical_package_id UUID NOT NULL,
                package_version INTEGER NOT NULL,
                supersedes_package_id UUID
                    REFERENCES rtm_presenter_filing_packages(id)
                    ON DELETE RESTRICT,
                destination_profile_id UUID NOT NULL
                    REFERENCES rtm_presenter_destination_profiles(id)
                    ON DELETE RESTRICT,
                representation_mode TEXT NOT NULL,
                authorization_document_version_id UUID
                    REFERENCES rtm_presenter_document_versions(id)
                    ON DELETE RESTRICT,
                status TEXT NOT NULL DEFAULT 'draft',
                manifest JSONB NOT NULL,
                manifest_sha256 TEXT NOT NULL,
                expected_item_count INTEGER NOT NULL DEFAULT 0,
                created_by_operator_id UUID NOT NULL
                    REFERENCES rtm_operators(id) ON DELETE RESTRICT,
                frozen_by_operator_id UUID
                    REFERENCES rtm_operators(id) ON DELETE RESTRICT,
                frozen_at TIMESTAMPTZ,
                expires_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                CONSTRAINT ck_rtm_presenter_package_version CHECK (
                    package_version > 0
                    AND ((package_version = 1
                            AND supersedes_package_id IS NULL)
                        OR (package_version > 1
                            AND supersedes_package_id IS NOT NULL))
                ),
                CONSTRAINT ck_rtm_presenter_package_status CHECK (
                    status IN ('draft', 'frozen', 'cancelled')
                ),
                CONSTRAINT ck_rtm_presenter_package_representation CHECK (
                    (representation_mode = 'self'
                        AND authorization_document_version_id IS NULL)
                    OR (representation_mode = 'representative'
                        AND authorization_document_version_id IS NOT NULL)
                ),
                CONSTRAINT ck_rtm_presenter_package_hash CHECK (
                    manifest_sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_presenter_package_freeze CHECK (
                    expected_item_count BETWEEN 0 AND 50
                    AND ((status = 'frozen'
                        AND expected_item_count > 0
                        AND frozen_by_operator_id IS NOT NULL
                        AND frozen_at IS NOT NULL)
                    OR (status IN ('draft', 'cancelled')
                        AND frozen_by_operator_id IS NULL
                        AND frozen_at IS NULL))
                ),
                CONSTRAINT ck_rtm_presenter_package_manifest CHECK (
                    jsonb_typeof(manifest) = 'object'
                    AND jsonb_typeof(metadata) = 'object'
                    AND NOT manifest ?| ARRAY[
                        'b2_bucket', 'b2_key', 'presigned_url', 'password',
                        'access_token', 'refresh_token', 'cookie', 'secret',
                        'private_key', 'credential_ref'
                    ]
                    AND NOT metadata ?| ARRAY[
                        'b2_bucket', 'b2_key', 'presigned_url', 'password',
                        'access_token', 'refresh_token', 'cookie', 'secret',
                        'private_key', 'credential_ref'
                    ]
                ),
                CONSTRAINT ck_rtm_presenter_package_expiry CHECK (
                    expires_at IS NULL OR expires_at > created_at
                )
            );
            """,
        ),
        (
            "presenter_package_version_index",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_presenter_package_version
            ON rtm_presenter_filing_packages(
                case_id, logical_package_id, package_version
            );
            """,
        ),
        (
            "presenter_package_case_status_index",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_presenter_package_case_status
            ON rtm_presenter_filing_packages(
                case_id, status, created_at DESC
            );
            """,
        ),
        (
            "presenter_package_destination_index",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_presenter_package_destination
            ON rtm_presenter_filing_packages(
                destination_profile_id, status, created_at DESC
            );
            """,
        ),
        (
            "presenter_idempotency_keys",
            """
            CREATE TABLE IF NOT EXISTS rtm_presenter_idempotency_keys (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                operator_id UUID NOT NULL
                    REFERENCES rtm_operators(id) ON DELETE RESTRICT,
                idempotency_key TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                case_id UUID NOT NULL REFERENCES cases(id) ON DELETE RESTRICT,
                package_id UUID NOT NULL
                    REFERENCES rtm_presenter_filing_packages(id)
                    ON DELETE RESTRICT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_presenter_idempotency_key CHECK (
                    idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$'
                ),
                CONSTRAINT ck_rtm_presenter_idempotency_hash CHECK (
                    request_sha256 ~ '^[0-9a-f]{64}$'
                )
            );
            """,
        ),
        (
            "presenter_idempotency_key_index",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_presenter_idempotency_operator_key
            ON rtm_presenter_idempotency_keys(operator_id, idempotency_key);
            """,
        ),
        (
            "presenter_package_items",
            """
            CREATE TABLE IF NOT EXISTS rtm_presenter_package_items (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                package_id UUID NOT NULL
                    REFERENCES rtm_presenter_filing_packages(id)
                    ON DELETE RESTRICT,
                case_id UUID NOT NULL REFERENCES cases(id) ON DELETE RESTRICT,
                item_order INTEGER NOT NULL,
                document_version_id UUID NOT NULL
                    REFERENCES rtm_presenter_document_versions(id)
                    ON DELETE RESTRICT,
                document_sha256 TEXT NOT NULL,
                field_code TEXT NOT NULL,
                purpose TEXT NOT NULL,
                portal_filename TEXT NOT NULL,
                required BOOLEAN NOT NULL DEFAULT TRUE,
                item_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
                item_sha256 TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_presenter_item_order CHECK (
                    item_order BETWEEN 1 AND 50
                ),
                CONSTRAINT ck_rtm_presenter_item_hashes CHECK (
                    document_sha256 ~ '^[0-9a-f]{64}$'
                    AND item_sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_presenter_item_field CHECK (
                    field_code ~ '^[a-z][a-z0-9_.-]{1,95}$'
                    AND purpose ~ '^[a-z][a-z0-9_.-]{2,63}$'
                ),
                CONSTRAINT ck_rtm_presenter_item_filename CHECK (
                    length(portal_filename) BETWEEN 1 AND 160
                    AND portal_filename !~ '[\\/\\x00-\\x1f]'
                ),
                CONSTRAINT ck_rtm_presenter_item_manifest CHECK (
                    jsonb_typeof(item_manifest) = 'object'
                    AND NOT item_manifest ?| ARRAY[
                        'b2_bucket', 'b2_key', 'presigned_url', 'password',
                        'access_token', 'refresh_token', 'cookie', 'secret'
                    ]
                )
            );
            """,
        ),
        (
            "presenter_package_item_order_index",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_presenter_package_item_order
            ON rtm_presenter_package_items(package_id, item_order);
            """,
        ),
        (
            "presenter_package_item_document_index",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_presenter_package_item_document
            ON rtm_presenter_package_items(package_id, document_version_id);
            """,
        ),
        (
            "presenter_package_item_field_index",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_presenter_package_item_field
            ON rtm_presenter_package_items(package_id, field_code, item_order);
            """,
        ),
        (
            "presenter_handoff_tickets",
            f"""
            CREATE TABLE IF NOT EXISTS rtm_presenter_handoff_tickets (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                ticket_hash TEXT NOT NULL,
                operator_id UUID NOT NULL
                    REFERENCES rtm_operators(id) ON DELETE RESTRICT,
                operator_session_id UUID NOT NULL
                    REFERENCES rtm_operator_sessions(id) ON DELETE RESTRICT,
                extension_client_id TEXT NOT NULL,
                case_id UUID NOT NULL REFERENCES cases(id) ON DELETE RESTRICT,
                package_id UUID NOT NULL
                    REFERENCES rtm_presenter_filing_packages(id)
                    ON DELETE RESTRICT,
                package_item_id UUID NOT NULL
                    REFERENCES rtm_presenter_package_items(id)
                    ON DELETE RESTRICT,
                portal_origin TEXT NOT NULL,
                field_code TEXT NOT NULL,
                issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                used_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_presenter_ticket_hash CHECK (
                    ticket_hash ~ '^[0-9a-f]{{64}}$'
                ),
                CONSTRAINT ck_rtm_presenter_ticket_extension CHECK (
                    extension_client_id =
                        '{RTM_PRESENTER_EXTENSION_CLIENT_ID}'
                ),
                CONSTRAINT ck_rtm_presenter_ticket_origin CHECK (
                    portal_origin ~
                        '^https://[A-Za-z0-9.-]+(:[0-9]{{2,5}})?$'
                    AND field_code ~ '^[a-z][a-z0-9_.-]{{1,95}}$'
                ),
                CONSTRAINT ck_rtm_presenter_ticket_ttl CHECK (
                    expires_at > issued_at
                    AND expires_at <= issued_at + INTERVAL '15 minutes'
                    AND created_at >= issued_at
                ),
                CONSTRAINT ck_rtm_presenter_ticket_use CHECK (
                    used_at IS NULL
                    OR (used_at >= issued_at AND used_at <= expires_at)
                )
            );
            """,
        ),
        (
            "presenter_handoff_ticket_hash_index",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_rtm_presenter_handoff_ticket_hash
            ON rtm_presenter_handoff_tickets(ticket_hash);
            """,
        ),
        (
            "presenter_handoff_expiry_index",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_presenter_handoff_expiry
            ON rtm_presenter_handoff_tickets(
                expires_at, used_at, case_id, package_id
            );
            """,
        ),
        (
            "presenter_handoff_session_index",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_presenter_handoff_session
            ON rtm_presenter_handoff_tickets(
                operator_session_id, used_at, expires_at
            );
            """,
        ),
        (
            "presenter_admin_exports",
            """
            CREATE TABLE IF NOT EXISTS rtm_presenter_admin_exports (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                case_id UUID NOT NULL REFERENCES cases(id) ON DELETE RESTRICT,
                package_id UUID
                    REFERENCES rtm_presenter_filing_packages(id)
                    ON DELETE RESTRICT,
                admin_operator_id UUID NOT NULL
                    REFERENCES rtm_operators(id) ON DELETE RESTRICT,
                reason TEXT NOT NULL,
                reauthenticated_at TIMESTAMPTZ NOT NULL,
                reauthentication_evidence_sha256 TEXT NOT NULL,
                export_scope JSONB NOT NULL,
                watermark TEXT NOT NULL,
                watermark_sha256 TEXT NOT NULL,
                source_hashes JSONB NOT NULL,
                manifest_sha256 TEXT NOT NULL,
                export_sha256 TEXT NOT NULL,
                export_document_id UUID
                    REFERENCES documents(id) ON DELETE RESTRICT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                CONSTRAINT ck_rtm_presenter_export_reason CHECK (
                    length(reason) BETWEEN 8 AND 500
                ),
                CONSTRAINT ck_rtm_presenter_export_hashes CHECK (
                    reauthentication_evidence_sha256 ~ '^[0-9a-f]{64}$'
                    AND watermark_sha256 ~ '^[0-9a-f]{64}$'
                    AND manifest_sha256 ~ '^[0-9a-f]{64}$'
                    AND export_sha256 ~ '^[0-9a-f]{64}$'
                ),
                CONSTRAINT ck_rtm_presenter_export_payloads CHECK (
                    jsonb_typeof(export_scope) = 'object'
                    AND jsonb_typeof(source_hashes) = 'array'
                    AND jsonb_array_length(source_hashes) BETWEEN 1 AND 50
                    AND length(watermark) BETWEEN 8 AND 500
                    AND NOT export_scope ?| ARRAY[
                        'b2_bucket', 'b2_key', 'presigned_url', 'password',
                        'access_token', 'refresh_token', 'cookie', 'secret'
                    ]
                ),
                CONSTRAINT ck_rtm_presenter_export_reauthentication CHECK (
                    reauthenticated_at <= created_at
                    AND reauthenticated_at >=
                        created_at - INTERVAL '5 minutes'
                ),
                CONSTRAINT ck_rtm_presenter_export_expiry CHECK (
                    expires_at > created_at
                    AND expires_at <= created_at + INTERVAL '1 hour'
                )
            );
            """,
        ),
        (
            "presenter_admin_export_case_index",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_presenter_admin_export_case
            ON rtm_presenter_admin_exports(case_id, created_at DESC);
            """,
        ),
        (
            "presenter_admin_export_admin_index",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_presenter_admin_export_admin
            ON rtm_presenter_admin_exports(
                admin_operator_id, created_at DESC
            );
            """,
        ),
        (
            "presenter_audit_events",
            """
            CREATE TABLE IF NOT EXISTS rtm_presenter_audit_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                sequence_number BIGINT GENERATED BY DEFAULT AS IDENTITY,
                case_id UUID NOT NULL REFERENCES cases(id) ON DELETE RESTRICT,
                package_id UUID
                    REFERENCES rtm_presenter_filing_packages(id)
                    ON DELETE RESTRICT,
                package_item_id UUID
                    REFERENCES rtm_presenter_package_items(id)
                    ON DELETE RESTRICT,
                handoff_ticket_id UUID
                    REFERENCES rtm_presenter_handoff_tickets(id)
                    ON DELETE RESTRICT,
                admin_export_id UUID
                    REFERENCES rtm_presenter_admin_exports(id)
                    ON DELETE RESTRICT,
                actor_type TEXT NOT NULL,
                actor_operator_id UUID
                    REFERENCES rtm_operators(id) ON DELETE RESTRICT,
                event_type TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                payload JSONB NOT NULL,
                payload_sha256 TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT ck_rtm_presenter_audit_actor CHECK (
                    (actor_type = 'system' AND actor_operator_id IS NULL)
                    OR (actor_type IN ('operator', 'admin')
                        AND actor_operator_id IS NOT NULL)
                ),
                CONSTRAINT ck_rtm_presenter_audit_event_type CHECK (
                    event_type ~ '^[a-z][a-z0-9_.-]{2,95}$'
                    AND reason_code ~ '^[a-z][a-z0-9_.-]{2,95}$'
                ),
                CONSTRAINT ck_rtm_presenter_audit_payload CHECK (
                    payload_sha256 ~ '^[0-9a-f]{64}$'
                    AND jsonb_typeof(payload) = 'object'
                    AND NOT payload ?| ARRAY[
                        'b2_bucket', 'b2_key', 'presigned_url', 'password',
                        'access_token', 'refresh_token', 'cookie', 'secret',
                        'raw_ticket'
                    ]
                )
            );
            """,
        ),
        (
            "presenter_audit_sequence_index",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rtm_presenter_audit_sequence
            ON rtm_presenter_audit_events(sequence_number);
            """,
        ),
        (
            "presenter_audit_case_index",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_presenter_audit_case
            ON rtm_presenter_audit_events(
                case_id, sequence_number DESC
            );
            """,
        ),
        (
            "presenter_audit_package_index",
            """
            CREATE INDEX IF NOT EXISTS idx_rtm_presenter_audit_package
            ON rtm_presenter_audit_events(
                package_id, sequence_number DESC
            );
            """,
        ),
    ]


def _guard_function_ddl() -> list[tuple[str, str]]:
    return [
        (
            "presenter_reject_mutation_function",
            """
            CREATE OR REPLACE FUNCTION rtm_presenter_reject_mutation()
            RETURNS TRIGGER AS $$
            BEGIN
                RAISE EXCEPTION 'RTM Presenter append-only row cannot mutate';
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "presenter_document_version_scope_function",
            """
            CREATE OR REPLACE FUNCTION
                rtm_presenter_document_version_scope_guard()
            RETURNS TRIGGER AS $$
            DECLARE
                source_ok BOOLEAN := FALSE;
                predecessor RECORD;
            BEGIN
                -- La misma clave se toma antes de congelar desde el servicio.
                -- Al ser un xact lock, insertar y congelar una linea documental
                -- quedan ordenados hasta commit, incluso cuando aun no existe
                -- una fila nueva que se pueda bloquear con FOR UPDATE.
                PERFORM pg_advisory_xact_lock(
                    hashtextextended(
                        'rtm-presenter-document-lineage:'
                        || NEW.case_id::TEXT || ':'
                        || NEW.logical_document_id::TEXT,
                        0
                    )
                );

                SELECT EXISTS (
                    SELECT 1
                    FROM documents d
                    WHERE d.id = NEW.source_document_id
                      AND d.case_id = NEW.case_id
                      AND d.sha256 = NEW.sha256
                      AND d.sha256 ~ '^[0-9a-f]{64}$'
                      AND COALESCE(d.size_bytes, 0) = NEW.size_bytes
                ) INTO source_ok;
                IF NOT source_ok THEN
                    RAISE EXCEPTION
                        'Presenter document source/case/hash/size mismatch';
                END IF;

                IF NEW.version_number = 1 THEN
                    IF NEW.supersedes_version_id IS NOT NULL THEN
                        RAISE EXCEPTION
                            'Presenter document v1 cannot supersede another row';
                    END IF;
                ELSE
                    SELECT case_id, logical_document_id, version_number
                    INTO predecessor
                    FROM rtm_presenter_document_versions
                    WHERE id = NEW.supersedes_version_id
                    FOR UPDATE;
                    IF predecessor IS NULL
                       OR predecessor.case_id IS DISTINCT FROM NEW.case_id
                       OR predecessor.logical_document_id
                            IS DISTINCT FROM NEW.logical_document_id
                       OR predecessor.version_number
                            IS DISTINCT FROM NEW.version_number - 1 THEN
                        RAISE EXCEPTION
                            'Presenter document predecessor mismatch';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "presenter_destination_profile_scope_function",
            """
            CREATE OR REPLACE FUNCTION
                rtm_presenter_destination_profile_scope_guard()
            RETURNS TRIGGER AS $$
            DECLARE
                previous_version INTEGER;
            BEGIN
                IF NEW.version_number > 1 THEN
                    SELECT MAX(version_number) INTO previous_version
                    FROM rtm_presenter_destination_profiles
                    WHERE profile_code = NEW.profile_code;
                    IF previous_version IS DISTINCT FROM
                            NEW.version_number - 1 THEN
                        RAISE EXCEPTION
                            'Presenter destination profile version gap';
                    END IF;
                END IF;
                IF NEW.verified_at IS NOT NULL
                   AND NEW.verified_at > NEW.created_at THEN
                    RAISE EXCEPTION
                        'Presenter profile verification cannot be future';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "presenter_filing_package_guard_function",
            """
            CREATE OR REPLACE FUNCTION rtm_presenter_filing_package_guard()
            RETURNS TRIGGER AS $$
            DECLARE
                profile_status TEXT;
                authorization_ok BOOLEAN := FALSE;
                predecessor RECORD;
                actual_item_count INTEGER;
                invalid_items INTEGER;
                locked_document RECORD;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'RTM Presenter package cannot be deleted';
                END IF;

                IF TG_OP = 'UPDATE' THEN
                    IF OLD.status = 'frozen' THEN
                        RAISE EXCEPTION
                            'RTM Presenter frozen package is immutable';
                    END IF;
                    IF OLD.status <> 'draft' THEN
                        RAISE EXCEPTION
                            'RTM Presenter non-draft package is immutable';
                    END IF;
                    IF NEW.id IS DISTINCT FROM OLD.id
                       OR NEW.case_id IS DISTINCT FROM OLD.case_id
                       OR NEW.logical_package_id
                            IS DISTINCT FROM OLD.logical_package_id
                       OR NEW.package_version
                            IS DISTINCT FROM OLD.package_version
                       OR NEW.supersedes_package_id
                            IS DISTINCT FROM OLD.supersedes_package_id
                       OR NEW.destination_profile_id
                            IS DISTINCT FROM OLD.destination_profile_id
                       OR NEW.representation_mode
                            IS DISTINCT FROM OLD.representation_mode
                       OR NEW.authorization_document_version_id
                            IS DISTINCT FROM
                                OLD.authorization_document_version_id
                       OR NEW.created_by_operator_id
                            IS DISTINCT FROM OLD.created_by_operator_id
                       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                        RAISE EXCEPTION
                            'RTM Presenter package identity is write-once';
                    END IF;
                    IF NEW.status NOT IN ('draft', 'frozen', 'cancelled') THEN
                        RAISE EXCEPTION
                            'RTM Presenter package transition rejected';
                    END IF;
                END IF;

                IF NEW.status = 'frozen' THEN
                    -- Orden comun con el servicio para evitar ciclos entre
                    -- freezes que compartan mas de una linea documental.
                    FOR locked_document IN
                        SELECT DISTINCT v.case_id, v.logical_document_id
                        FROM rtm_presenter_package_items i
                        JOIN rtm_presenter_document_versions v
                          ON v.id = i.document_version_id
                        WHERE i.package_id = NEW.id
                        ORDER BY v.case_id, v.logical_document_id
                    LOOP
                        PERFORM pg_advisory_xact_lock(
                            hashtextextended(
                                'rtm-presenter-document-lineage:'
                                || locked_document.case_id::TEXT || ':'
                                || locked_document.logical_document_id::TEXT,
                                0
                            )
                        );
                    END LOOP;
                END IF;

                SELECT status INTO profile_status
                FROM rtm_presenter_destination_profiles
                WHERE id = NEW.destination_profile_id;
                IF profile_status IS DISTINCT FROM 'active' THEN
                    RAISE EXCEPTION
                        'RTM Presenter requires active destination profile';
                END IF;

                IF NEW.representation_mode = 'representative' THEN
                    SELECT EXISTS (
                        SELECT 1
                        FROM rtm_presenter_document_versions v
                        WHERE v.id = NEW.authorization_document_version_id
                          AND v.case_id = NEW.case_id
                          AND v.purpose IN (
                              'representation', 'signed_authorization',
                              'representation_authorization'
                          )
                          AND v.state = 'active'
                          AND v.scan_status = 'clean'
                          AND NOT EXISTS (
                              SELECT 1
                              FROM rtm_presenter_document_versions newer
                              WHERE newer.case_id = v.case_id
                                AND newer.logical_document_id =
                                      v.logical_document_id
                                AND newer.version_number > v.version_number
                                AND newer.state = 'active'
                                AND newer.scan_status = 'clean'
                          )
                    ) INTO authorization_ok;
                    IF NOT authorization_ok THEN
                        RAISE EXCEPTION
                            'Presenter representation authorization invalid';
                    END IF;
                END IF;

                IF TG_OP = 'INSERT' AND NEW.package_version > 1 THEN
                    SELECT case_id, logical_package_id, package_version, status
                    INTO predecessor
                    FROM rtm_presenter_filing_packages
                    WHERE id = NEW.supersedes_package_id;
                    IF predecessor IS NULL
                       OR predecessor.case_id IS DISTINCT FROM NEW.case_id
                       OR predecessor.logical_package_id
                            IS DISTINCT FROM NEW.logical_package_id
                       OR predecessor.package_version
                            IS DISTINCT FROM NEW.package_version - 1
                       OR predecessor.status IS DISTINCT FROM 'frozen' THEN
                        RAISE EXCEPTION
                            'RTM Presenter package predecessor mismatch';
                    END IF;
                END IF;

                IF NEW.status = 'frozen' THEN
                    SELECT COUNT(*), COUNT(*) FILTER (
                        WHERE v.case_id IS DISTINCT FROM NEW.case_id
                           OR v.sha256 IS DISTINCT FROM i.document_sha256
                           OR v.state IS DISTINCT FROM 'active'
                           OR v.scan_status IS DISTINCT FROM 'clean'
                           OR EXISTS (
                               SELECT 1
                               FROM rtm_presenter_document_versions newer
                               WHERE newer.case_id = v.case_id
                                 AND newer.logical_document_id =
                                       v.logical_document_id
                                 AND newer.version_number > v.version_number
                                 AND newer.state = 'active'
                                 AND newer.scan_status = 'clean'
                           )
                    )
                    INTO actual_item_count, invalid_items
                    FROM rtm_presenter_package_items i
                    JOIN rtm_presenter_document_versions v
                      ON v.id = i.document_version_id
                    WHERE i.package_id = NEW.id;
                    IF actual_item_count IS DISTINCT FROM
                            NEW.expected_item_count
                       OR actual_item_count < 1
                       OR invalid_items <> 0 THEN
                        RAISE EXCEPTION
                            'RTM Presenter package items are not freeze-ready';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "presenter_idempotency_scope_function",
            """
            CREATE OR REPLACE FUNCTION rtm_presenter_idempotency_scope_guard()
            RETURNS TRIGGER AS $$
            DECLARE
                package RECORD;
            BEGIN
                SELECT case_id, created_by_operator_id, status
                INTO package
                FROM rtm_presenter_filing_packages
                WHERE id = NEW.package_id;
                IF package IS NULL
                   OR package.case_id IS DISTINCT FROM NEW.case_id
                   OR package.created_by_operator_id
                        IS DISTINCT FROM NEW.operator_id
                   OR package.status IS DISTINCT FROM 'frozen' THEN
                    RAISE EXCEPTION
                        'RTM Presenter idempotency scope mismatch';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "presenter_package_item_guard_function",
            """
            CREATE OR REPLACE FUNCTION rtm_presenter_package_item_guard()
            RETURNS TRIGGER AS $$
            DECLARE
                parent RECORD;
                document RECORD;
                target_package_id UUID;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    target_package_id := OLD.package_id;
                ELSE
                    target_package_id := NEW.package_id;
                END IF;
                SELECT case_id, status INTO parent
                FROM rtm_presenter_filing_packages
                WHERE id = target_package_id
                FOR UPDATE;
                IF parent IS NULL THEN
                    RAISE EXCEPTION 'RTM Presenter package not found';
                END IF;
                IF parent.status <> 'draft' THEN
                    RAISE EXCEPTION
                        'RTM Presenter frozen package items are immutable';
                END IF;
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                IF NEW.case_id IS DISTINCT FROM parent.case_id THEN
                    RAISE EXCEPTION 'Presenter package item case mismatch';
                END IF;
                SELECT case_id, sha256, purpose, state, scan_status
                INTO document
                FROM rtm_presenter_document_versions
                WHERE id = NEW.document_version_id
                  AND NOT EXISTS (
                      SELECT 1
                      FROM rtm_presenter_document_versions newer
                      WHERE newer.case_id =
                              rtm_presenter_document_versions.case_id
                        AND newer.logical_document_id =
                              rtm_presenter_document_versions.logical_document_id
                        AND newer.version_number >
                              rtm_presenter_document_versions.version_number
                        AND newer.state = 'active'
                        AND newer.scan_status = 'clean'
                  );
                IF document IS NULL
                   OR document.case_id IS DISTINCT FROM NEW.case_id
                   OR document.sha256 IS DISTINCT FROM NEW.document_sha256
                   OR document.purpose IS DISTINCT FROM NEW.purpose
                   OR document.state IS DISTINCT FROM 'active'
                   OR document.scan_status IS DISTINCT FROM 'clean' THEN
                    RAISE EXCEPTION
                        'Presenter package item document binding invalid';
                END IF;
                IF TG_OP = 'UPDATE' AND (
                    NEW.id IS DISTINCT FROM OLD.id
                    OR NEW.package_id IS DISTINCT FROM OLD.package_id
                    OR NEW.case_id IS DISTINCT FROM OLD.case_id
                    OR NEW.created_at IS DISTINCT FROM OLD.created_at
                ) THEN
                    RAISE EXCEPTION
                        'RTM Presenter package item identity is write-once';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "presenter_handoff_ticket_guard_function",
            """
            CREATE OR REPLACE FUNCTION rtm_presenter_handoff_ticket_guard()
            RETURNS TRIGGER AS $$
            DECLARE
                package RECORD;
                item RECORD;
                profile_origin TEXT;
                session_ok BOOLEAN := FALSE;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION
                        'RTM Presenter handoff ticket cannot be deleted';
                END IF;

                SELECT p.case_id, p.status, p.destination_profile_id
                INTO package
                FROM rtm_presenter_filing_packages p
                WHERE p.id = NEW.package_id;
                SELECT i.package_id, i.case_id, i.field_code
                INTO item
                FROM rtm_presenter_package_items i
                WHERE i.id = NEW.package_item_id;
                SELECT portal_origin INTO profile_origin
                FROM rtm_presenter_destination_profiles
                WHERE id = package.destination_profile_id;
                SELECT EXISTS (
                    SELECT 1
                    FROM rtm_operator_sessions s
                    JOIN rtm_operators o ON o.id = s.operator_id
                    WHERE s.id = NEW.operator_session_id
                      AND s.operator_id = NEW.operator_id
                      AND s.status = 'active'
                      AND s.expires_at > NOW()
                      AND o.status = 'active'
                ) INTO session_ok;

                IF package IS NULL OR item IS NULL
                   OR package.status IS DISTINCT FROM 'frozen'
                   OR package.case_id IS DISTINCT FROM NEW.case_id
                   OR item.package_id IS DISTINCT FROM NEW.package_id
                   OR item.case_id IS DISTINCT FROM NEW.case_id
                   OR item.field_code IS DISTINCT FROM NEW.field_code
                   OR profile_origin IS DISTINCT FROM NEW.portal_origin
                   OR NOT session_ok THEN
                    RAISE EXCEPTION
                        'RTM Presenter handoff scope/session mismatch';
                END IF;

                IF TG_OP = 'INSERT' THEN
                    IF NEW.used_at IS NOT NULL THEN
                        RAISE EXCEPTION
                            'RTM Presenter ticket must be issued unused';
                    END IF;
                    RETURN NEW;
                END IF;

                IF OLD.used_at IS NOT NULL THEN
                    RAISE EXCEPTION
                        'RTM Presenter handoff ticket is single-use';
                END IF;
                IF NEW.used_at IS NULL OR NEW.used_at > NEW.expires_at
                   OR NOW() > NEW.expires_at THEN
                    RAISE EXCEPTION
                        'RTM Presenter handoff ticket expired or not consumed';
                END IF;
                IF (to_jsonb(NEW) - 'used_at') IS DISTINCT FROM
                        (to_jsonb(OLD) - 'used_at') THEN
                    RAISE EXCEPTION
                        'RTM Presenter handoff ticket fields are immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "presenter_admin_export_scope_function",
            """
            CREATE OR REPLACE FUNCTION
                rtm_presenter_admin_export_scope_guard()
            RETURNS TRIGGER AS $$
            DECLARE
                package_case UUID;
                export_doc RECORD;
                admin_ok BOOLEAN := FALSE;
                reauthentication_ok BOOLEAN := FALSE;
                scope_session_id UUID;
                scope_event_id UUID;
                value TEXT;
            BEGIN
                IF NEW.package_id IS NOT NULL THEN
                    SELECT case_id INTO package_case
                    FROM rtm_presenter_filing_packages
                    WHERE id = NEW.package_id AND status = 'frozen';
                    IF package_case IS DISTINCT FROM NEW.case_id THEN
                        RAISE EXCEPTION
                            'RTM Presenter admin export package mismatch';
                    END IF;
                END IF;

                SELECT EXISTS (
                    SELECT 1
                    FROM rtm_operators o
                    JOIN rtm_operator_roles r
                      ON r.id = o.primary_role_id
                    WHERE o.id = NEW.admin_operator_id
                      AND o.status = 'active'
                      AND r.active = TRUE
                      AND r.code = 'rtm.admin'
                      AND r.permissions ? 'ops.documents.export_exceptional'
                ) INTO admin_ok;
                IF NOT admin_ok THEN
                    RAISE EXCEPTION
                        'RTM Presenter admin export permission missing';
                END IF;

                IF NEW.reauthenticated_at > NEW.created_at
                   OR NEW.reauthenticated_at <
                        NEW.created_at - INTERVAL '5 minutes' THEN
                    RAISE EXCEPTION
                        'RTM Presenter admin export reauthentication stale';
                END IF;

                IF COALESCE(
                        NEW.export_scope->>'operator_session_id', ''
                    ) !~ '^[0-9a-fA-F-]{36}$'
                   OR COALESCE(
                        NEW.export_scope->>'reauthentication_event_id', ''
                    ) !~ '^[0-9a-fA-F-]{36}$' THEN
                    RAISE EXCEPTION
                        'RTM Presenter admin export reauthentication scope missing';
                END IF;
                scope_session_id := (
                    NEW.export_scope->>'operator_session_id'
                )::UUID;
                scope_event_id := (
                    NEW.export_scope->>'reauthentication_event_id'
                )::UUID;
                SELECT EXISTS (
                    SELECT 1
                    FROM rtm_operator_sessions s
                    JOIN rtm_operator_access_events e
                      ON e.id = scope_event_id
                     AND e.session_id = s.id
                     AND e.operator_id = s.operator_id
                    WHERE s.id = scope_session_id
                      AND s.operator_id = NEW.admin_operator_id
                      AND s.status = 'active'
                      AND s.expires_at > NEW.created_at
                      AND (
                          s.absolute_expires_at IS NULL
                          OR s.absolute_expires_at > NEW.created_at
                      )
                      AND s.last_verified_at > s.login_at
                      AND s.last_verified_at = NEW.reauthenticated_at
                      AND e.event_type = 'auth.reauthenticated'
                      AND e.result = 'success'
                      AND e.reason_code = 'password_reverified'
                      AND e.occurred_at = s.last_verified_at
                ) INTO reauthentication_ok;
                IF NOT reauthentication_ok THEN
                    RAISE EXCEPTION
                        'RTM Presenter admin export reauthentication evidence invalid';
                END IF;

                FOR value IN
                    SELECT jsonb_array_elements_text(NEW.source_hashes)
                LOOP
                    IF value !~ '^[0-9a-f]{64}$' THEN
                        RAISE EXCEPTION
                            'RTM Presenter export source hash invalid';
                    END IF;
                END LOOP;

                IF NEW.export_document_id IS NOT NULL THEN
                    SELECT case_id, sha256 INTO export_doc
                    FROM documents
                    WHERE id = NEW.export_document_id;
                    IF export_doc IS NULL
                       OR export_doc.case_id IS DISTINCT FROM NEW.case_id
                       OR export_doc.sha256 IS DISTINCT FROM
                            NEW.export_sha256 THEN
                        RAISE EXCEPTION
                            'RTM Presenter export document/hash mismatch';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        (
            "presenter_audit_event_scope_function",
            """
            CREATE OR REPLACE FUNCTION
                rtm_presenter_audit_event_scope_guard()
            RETURNS TRIGGER AS $$
            DECLARE
                related_case UUID;
            BEGIN
                IF NEW.package_id IS NOT NULL THEN
                    SELECT case_id INTO related_case
                    FROM rtm_presenter_filing_packages
                    WHERE id = NEW.package_id;
                    IF related_case IS DISTINCT FROM NEW.case_id THEN
                        RAISE EXCEPTION
                            'RTM Presenter audit package scope mismatch';
                    END IF;
                END IF;
                IF NEW.package_item_id IS NOT NULL THEN
                    SELECT case_id INTO related_case
                    FROM rtm_presenter_package_items
                    WHERE id = NEW.package_item_id;
                    IF related_case IS DISTINCT FROM NEW.case_id THEN
                        RAISE EXCEPTION
                            'RTM Presenter audit item scope mismatch';
                    END IF;
                END IF;
                IF NEW.handoff_ticket_id IS NOT NULL THEN
                    SELECT case_id INTO related_case
                    FROM rtm_presenter_handoff_tickets
                    WHERE id = NEW.handoff_ticket_id;
                    IF related_case IS DISTINCT FROM NEW.case_id THEN
                        RAISE EXCEPTION
                            'RTM Presenter audit ticket scope mismatch';
                    END IF;
                END IF;
                IF NEW.admin_export_id IS NOT NULL THEN
                    SELECT case_id INTO related_case
                    FROM rtm_presenter_admin_exports
                    WHERE id = NEW.admin_export_id;
                    IF related_case IS DISTINCT FROM NEW.case_id THEN
                        RAISE EXCEPTION
                            'RTM Presenter audit export scope mismatch';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
    ]


def _trigger_ddl() -> list[tuple[str, str]]:
    return [
        (
            "presenter_document_version_scope_trigger",
            """
            CREATE OR REPLACE TRIGGER
                trg_rtm_presenter_document_version_scope
            BEFORE INSERT ON rtm_presenter_document_versions
            FOR EACH ROW EXECUTE FUNCTION
                rtm_presenter_document_version_scope_guard();
            """,
        ),
        (
            "presenter_document_version_append_trigger",
            """
            CREATE OR REPLACE TRIGGER
                trg_rtm_presenter_document_version_append_only
            BEFORE UPDATE OR DELETE ON rtm_presenter_document_versions
            FOR EACH ROW EXECUTE FUNCTION rtm_presenter_reject_mutation();
            """,
        ),
        (
            "presenter_destination_profile_scope_trigger",
            """
            CREATE OR REPLACE TRIGGER
                trg_rtm_presenter_destination_profile_scope
            BEFORE INSERT ON rtm_presenter_destination_profiles
            FOR EACH ROW EXECUTE FUNCTION
                rtm_presenter_destination_profile_scope_guard();
            """,
        ),
        (
            "presenter_destination_profile_append_trigger",
            """
            CREATE OR REPLACE TRIGGER
                trg_rtm_presenter_destination_profile_append_only
            BEFORE UPDATE OR DELETE ON rtm_presenter_destination_profiles
            FOR EACH ROW EXECUTE FUNCTION rtm_presenter_reject_mutation();
            """,
        ),
        (
            "presenter_filing_package_guard_trigger",
            """
            CREATE OR REPLACE TRIGGER trg_rtm_presenter_filing_package_guard
            BEFORE INSERT OR UPDATE OR DELETE
            ON rtm_presenter_filing_packages
            FOR EACH ROW EXECUTE FUNCTION
                rtm_presenter_filing_package_guard();
            """,
        ),
        (
            "presenter_package_item_guard_trigger",
            """
            CREATE OR REPLACE TRIGGER trg_rtm_presenter_package_item_guard
            BEFORE INSERT OR UPDATE OR DELETE
            ON rtm_presenter_package_items
            FOR EACH ROW EXECUTE FUNCTION
                rtm_presenter_package_item_guard();
            """,
        ),
        (
            "presenter_idempotency_scope_trigger",
            """
            CREATE OR REPLACE TRIGGER trg_rtm_presenter_idempotency_scope
            BEFORE INSERT ON rtm_presenter_idempotency_keys
            FOR EACH ROW EXECUTE FUNCTION
                rtm_presenter_idempotency_scope_guard();
            """,
        ),
        (
            "presenter_idempotency_append_trigger",
            """
            CREATE OR REPLACE TRIGGER
                trg_rtm_presenter_idempotency_append_only
            BEFORE UPDATE OR DELETE ON rtm_presenter_idempotency_keys
            FOR EACH ROW EXECUTE FUNCTION rtm_presenter_reject_mutation();
            """,
        ),
        (
            "presenter_handoff_ticket_guard_trigger",
            """
            CREATE OR REPLACE TRIGGER trg_rtm_presenter_handoff_ticket_guard
            BEFORE INSERT OR UPDATE OR DELETE
            ON rtm_presenter_handoff_tickets
            FOR EACH ROW EXECUTE FUNCTION
                rtm_presenter_handoff_ticket_guard();
            """,
        ),
        (
            "presenter_admin_export_scope_trigger",
            """
            CREATE OR REPLACE TRIGGER trg_rtm_presenter_admin_export_scope
            BEFORE INSERT ON rtm_presenter_admin_exports
            FOR EACH ROW EXECUTE FUNCTION
                rtm_presenter_admin_export_scope_guard();
            """,
        ),
        (
            "presenter_admin_export_append_trigger",
            """
            CREATE OR REPLACE TRIGGER
                trg_rtm_presenter_admin_export_append_only
            BEFORE UPDATE OR DELETE ON rtm_presenter_admin_exports
            FOR EACH ROW EXECUTE FUNCTION rtm_presenter_reject_mutation();
            """,
        ),
        (
            "presenter_audit_event_scope_trigger",
            """
            CREATE OR REPLACE TRIGGER trg_rtm_presenter_audit_event_scope
            BEFORE INSERT ON rtm_presenter_audit_events
            FOR EACH ROW EXECUTE FUNCTION
                rtm_presenter_audit_event_scope_guard();
            """,
        ),
        (
            "presenter_audit_event_append_trigger",
            """
            CREATE OR REPLACE TRIGGER
                trg_rtm_presenter_audit_event_append_only
            BEFORE UPDATE OR DELETE ON rtm_presenter_audit_events
            FOR EACH ROW EXECUTE FUNCTION rtm_presenter_reject_mutation();
            """,
        ),
    ]


def rtm_presenter_schema_ddl() -> list[tuple[str, str]]:
    """Devuelve el DDL Presenter completo, repetible y sin datos sembrados."""

    return [
        ("extensions", "CREATE EXTENSION IF NOT EXISTS pgcrypto;"),
        *_table_ddl(),
        *_guard_function_ddl(),
        *_trigger_ddl(),
    ]


def ensure_rtm_presenter_schema(connection: Any) -> list[str]:
    """Aplica el DDL en la transaccion del llamador y devuelve sus nombres.

    El llamador conserva el control de la transaccion y de la frontera de
    entorno; esta funcion no crea motores ni confirma cambios por su cuenta.
    """

    from sqlalchemy import text

    applied: list[str] = []
    for name, statement in rtm_presenter_schema_ddl():
        connection.execute(text(statement))
        applied.append(name)
    return applied


__all__ = [
    "RTM_PRESENTER_EXTENSION_CLIENT_ID",
    "RTM_PRESENTER_SCHEMA_VERSION",
    "PRESENTER_REQUIRED_COLUMNS",
    "PRESENTER_REQUIRED_COLUMN_TYPES",
    "PRESENTER_REQUIRED_CONSTRAINTS",
    "PRESENTER_REQUIRED_CONSTRAINT_TABLES",
    "PRESENTER_REQUIRED_FUNCTIONS",
    "PRESENTER_REQUIRED_INDEXES",
    "PRESENTER_REQUIRED_INDEX_TABLES",
    "PRESENTER_REQUIRED_TRIGGERS",
    "PRESENTER_REQUIRED_TRIGGER_BINDINGS",
    "ensure_rtm_presenter_schema",
    "rtm_presenter_schema_ddl",
]
