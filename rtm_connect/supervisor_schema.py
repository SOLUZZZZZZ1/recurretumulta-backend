"""Contrato de dependencias persistentes de RTM CONNECT C5.

C5 construye proyecciones supervisoras sanitizadas sobre los ledgers de
C1, C3 y C4 y sobre la auditoria de acceso ya existente. No introduce tablas,
indices, triggers ni una migracion nueva.
"""

from __future__ import annotations


RTM_CONNECT_C5_SUPERVISOR_SCHEMA_VERSION = (
    "rtm_connect_c5_supervisor_schema_v1_0"
)
CONNECT_C5_SCHEMA_CHANGES_REQUIRED = False

CONNECT_C5_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "cases": {"id", "test_mode"},
    "rtm_operators": {
        "id", "email", "display_name", "password_hash", "status",
        "primary_role_id", "must_change_password", "mfa_required",
        "locked_until", "auth_epoch", "profile",
    },
    "rtm_operator_roles": {
        "id", "code", "permissions", "active",
    },
    "rtm_operator_sessions": {
        "id", "operator_id", "device_id", "token_sha256", "status",
        "expires_at", "absolute_expires_at", "auth_epoch",
    },
    "rtm_operator_access_events": {
        "id", "operator_id", "session_id", "device_id", "event_type",
        "result", "auth_method", "occurred_at", "login_identifier_sha256",
        "ip_masked", "ip_hash_sha256", "ip_family", "ip_source",
        "ip_trusted", "device_key_sha256", "device_type", "os_family",
        "os_version", "browser_family", "browser_version", "country_code",
        "region", "city", "timezone", "location_source", "request_id",
        "reason_code", "reason_detail", "risk_flags", "metadata",
        "created_at",
    },
    "rtm_operator_access_evidence": {
        "id", "access_event_id", "ip_address", "raw_user_agent",
        "trusted_headers", "retention_until", "created_at",
    },
    "rtm_connect_connectors": {
        "id", "code", "version", "mode", "status", "environment",
        "synthetic_only",
        "capabilities", "risk_ceiling", "supports_reconciliation",
        "credential_ref",
        "created_at", "updated_at",
    },
    "rtm_connect_actions": {
        "id", "case_id", "capability", "satellite", "target_type",
        "risk_class", "requires_dual_control", "requested_by_operator_id",
        "requested_at", "status", "status_version", "current_connector_id",
        "external_reference", "confirmed_at", "unknown_since",
        "cancelled_at", "created_at", "updated_at",
    },
    "rtm_connect_authorizations": {
        "id", "action_id", "authorization_version", "decision",
        "required_evidence_level", "authorized_at", "expires_at", "revoked_at",
        "legal_effect_authorized", "frozen", "created_at",
    },
    "rtm_connect_attempts": {
        "id", "action_id", "connector_id", "attempt_number", "status",
        "started_at", "finished_at", "external_reference", "retryable",
        "reconciliation_required",
        "created_at", "updated_at",
    },
    "rtm_connect_evidence": {
        "id", "action_id", "attempt_id", "sequence_number",
        "evidence_level", "verified_at",
        "verified_by_operator_id", "created_at",
    },
    "rtm_connect_transitions": {
        "id", "action_id", "attempt_id", "sequence_number", "from_status",
        "to_status", "actor_type", "operator_id",
        "created_at",
    },
    "rtm_connect_manual_tasks": {
        "id", "action_id", "attempt_id", "connector_id", "task_code",
        "status", "assignee_operator_id", "assigned_by_operator_id",
        "assigned_at", "due_at", "started_at", "receipt_submitted_at",
        "verified_at", "verified_by_operator_id", "completed_at",
        "version", "created_at", "updated_at",
    },
    "rtm_connect_webhook_inbox": {
        "id", "ingress_connector_id", "event_type", "reported_outcome",
        "matched_action_id", "matched_attempt_id", "status", "occurred_at",
        "received_at", "matched_at", "processed_at",
        "dead_letter_reason_code", "replay_count", "last_seen_at",
        "created_at", "updated_at",
    },
    "rtm_connect_reconciliations": {
        "id", "action_id", "attempt_id", "webhook_inbox_id",
        "reconciliation_number", "status", "resolution", "evidence_id",
        "started_at", "resolved_at", "resolved_by_operator_id",
        "created_at", "updated_at",
    },
}


def connect_c5_supervisor_ddl() -> list[tuple[str, str]]:
    """C5 no aplica DDL: devuelve una lista vacia de forma expresa."""

    return []


__all__ = [
    "RTM_CONNECT_C5_SUPERVISOR_SCHEMA_VERSION",
    "CONNECT_C5_REQUIRED_COLUMNS",
    "CONNECT_C5_SCHEMA_CHANGES_REQUIRED",
    "connect_c5_supervisor_ddl",
]
