"""Persistencia tenant-scoped de RTM CONNECT A1-S human filing.

Este repositorio es deliberadamente estrecho: solo conoce fixtures sinteticos,
no abre red, no resuelve secretos y no accede a B2.  Toda consulta de dominio
queda ligada al tenant y todas las mutaciones producen un evento append-only.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import text

from rtm_connect import human_filing_contracts as a1s_contracts
from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    RiskClass,
)


RTM_CONNECT_A1S_REPOSITORY_VERSION = (
    "rtm_connect_a1s_human_filing_repository_v1_0"
)
HUMAN_FILING_PARTICIPANT_LIMIT = 100
HUMAN_FILING_PREPARATION_OPTION_LIMIT = 100
HUMAN_FILING_PREPARATION_SCAN_LIMIT = 200
HUMAN_FILING_DETAIL_SUMMARY_LIMIT = 200
HUMAN_FILING_RECEIPT_OPTION_LIMIT = 100
HUMAN_FILING_TENANT_OPTION_LIMIT = 100


class HumanFilingRepositoryError(RuntimeError):
    """Base de errores persistentes A1-S."""


class HumanFilingNotFound(HumanFilingRepositoryError):
    pass


class HumanFilingPermissionDenied(HumanFilingRepositoryError):
    pass


class HumanFilingScopeError(HumanFilingRepositoryError):
    pass


class HumanFilingStateConflict(HumanFilingRepositoryError):
    pass


class HumanFilingReplayConflict(HumanFilingRepositoryError):
    pass


class HumanFilingOptimisticLockError(HumanFilingRepositoryError):
    pass


@dataclass(frozen=True)
class TenantMembership:
    membership_id: str
    tenant_id: str
    operator_id: str
    principal_id: str
    role: str
    permissions: tuple[str, ...]
    version: int


@dataclass(frozen=True)
class IdempotencyClaim:
    created: bool
    replayed: bool
    task_id: str | None
    action_id: str | None
    status: str
    replay_count: int


@dataclass(frozen=True)
class PreparationCandidate:
    projection: Mapping[str, Any]
    action: ConnectActionRequest
    grant: AuthorizationGrant


_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "requester": frozenset({
        "connect.human_filing.read", "connect.human_filing.prepare",
    }),
    "executor": frozenset({
        "connect.human_filing.read", "connect.human_filing.execute",
    }),
    "releaser": frozenset({
        "connect.human_filing.read", "connect.human_filing.release",
    }),
    "verifier": frozenset({
        "connect.human_filing.read", "connect.human_filing.verify",
        "connect.human_filing.reconcile",
    }),
    "supervisor": frozenset({
        "connect.human_filing.read", "connect.human_filing.prepare",
        "connect.human_filing.assign", "connect.human_filing.execute",
        "connect.human_filing.release", "connect.human_filing.verify",
        "connect.human_filing.reconcile", "connect.human_filing.supervise",
    }),
}

_SAFE_PACKAGE_MANIFEST_FIELDS = (
    "contract_version",
    "task_id",
    "tenant_id",
    "case_binding_id",
    "representation_evidence_id",
    "action_id",
    "attempt_id",
    "authorization_id",
    "authorization_version",
    "case_snapshot_sha256",
    "representation_evidence_sha256",
    "request_sha256",
    "document_hashes",
    "destination_ref",
    "due_at",
    "checklist",
    "created_by_operator_id",
    "created_at",
    "synthetic_marker",
    "synthetic_only",
    "network_used",
    "b2_used",
    "provider_contacted",
    "legal_submission_executed",
    "storage_backend",
)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _canonical_sha256(value: Any) -> str:
    return a1s_contracts.canonical_sha256(value)


def _one_mapping(result: Any) -> Mapping[str, Any] | None:
    return result.mappings().first()


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if getattr(value, "isoformat", None):
        return value.isoformat()
    return str(value)


def permissions_for_role(role: str) -> tuple[str, ...]:
    permissions = _ROLE_PERMISSIONS.get(str(role))
    if permissions is None:
        raise HumanFilingPermissionDenied("membership_role_not_admitted")
    return tuple(sorted(permissions))


def eligible_actions_for_role(role: str) -> tuple[str, ...]:
    return tuple(
        permission.rsplit(".", 1)[-1]
        for permission in permissions_for_role(role)
    )


def _safe_package_manifest(value: Any) -> dict[str, Any]:
    source = dict(value or {})
    return {
        field: source[field]
        for field in _SAFE_PACKAGE_MANIFEST_FIELDS
        if field in source
    }


def _task_projection(row: Mapping[str, Any], *, replayed: bool = False) -> dict[str, Any]:
    optional_uuid = lambda name: str(row[name]) if row.get(name) else None
    optional_time = lambda name: (
        row[name].isoformat() if getattr(row.get(name), "isoformat", None)
        else (str(row[name]) if row.get(name) else None)
    )
    return {
        "task_id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "case_binding_id": str(row["case_binding_id"]),
        "case_id": str(row["case_id"]),
        "representation_evidence_id": str(row["representation_evidence_id"]),
        "action_id": str(row["action_id"]),
        "attempt_id": str(row["attempt_id"]),
        "connector_id": str(row["connector_id"]),
        "authorization_id": str(row["authorization_id"]),
        "authorization_version": int(row["authorization_version"]),
        "task_code": str(row["task_code"]),
        "status": str(row["status"]),
        "version": int(row["version"]),
        "status_version": int(row["version"]),
        "requester_membership_id": optional_uuid("requester_membership_id"),
        "requester_principal_id": optional_uuid("requester_principal_id"),
        "requester_operator_id": optional_uuid("requester_operator_id"),
        "assignee_operator_id": optional_uuid("assignee_operator_id"),
        "assignee_membership_id": optional_uuid("assignee_membership_id"),
        "assignee_principal_id": optional_uuid("assignee_principal_id"),
        "release_operator_id": optional_uuid("release_operator_id"),
        "release_membership_id": optional_uuid("release_membership_id"),
        "release_principal_id": optional_uuid("release_principal_id"),
        "verified_by_operator_id": optional_uuid("verified_by_operator_id"),
        "verified_by_membership_id": optional_uuid("verified_by_membership_id"),
        "verified_by_principal_id": optional_uuid("verified_by_principal_id"),
        "due_at": optional_time("due_at"),
        "package_sha256": str(row["package_sha256"]),
        "review_attestation_sha256": row.get("review_attestation_sha256"),
        "release_attestation_sha256": row.get("release_attestation_sha256"),
        "verification_attestation_sha256": row.get(
            "verification_attestation_sha256"
        ),
        "external_reference": row.get("external_reference"),
        "created_at": optional_time("created_at"),
        "updated_at": optional_time("updated_at"),
        "replayed": bool(replayed),
    }


def load_active_membership(
    conn: Any,
    *,
    tenant_id: str,
    operator_id: str,
    for_update: bool = False,
) -> TenantMembership:
    lock = " FOR UPDATE" if for_update else ""
    row = _one_mapping(conn.execute(text(
        """
        SELECT m.id, m.tenant_id, m.operator_id, m.principal_id,
               m.role, m.version
        FROM rtm_connect_a1s_memberships m
        JOIN rtm_connect_a1s_tenants t ON t.id=m.tenant_id
        JOIN rtm_operators o ON o.id=m.operator_id
        WHERE m.tenant_id=CAST(:tenant_id AS UUID)
          AND m.operator_id=CAST(:operator_id AS UUID)
          AND m.status='active'
          AND m.synthetic_only=TRUE
          AND m.revoked_at IS NULL
          AND t.status='active'
          AND t.synthetic_only=TRUE
          AND o.status='active'
          AND o.must_change_password=FALSE
          AND o.mfa_required=FALSE
          AND (o.locked_until IS NULL OR o.locked_until <= NOW())
        """ + lock
    ), {"tenant_id": tenant_id, "operator_id": operator_id}))
    if not row:
        raise HumanFilingPermissionDenied(
            "operator_has_no_active_a1s_tenant_membership"
        )
    role = str(row["role"])
    permissions = _ROLE_PERMISSIONS.get(role)
    if permissions is None:
        raise HumanFilingPermissionDenied("membership_role_not_admitted")
    return TenantMembership(
        membership_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        operator_id=str(row["operator_id"]),
        principal_id=str(row["principal_id"]),
        role=role,
        permissions=tuple(sorted(permissions)),
        version=int(row["version"]),
    )


def require_tenant_permission(
    conn: Any,
    *,
    tenant_id: str,
    operator_id: str,
    permission: str,
    for_update: bool = False,
) -> TenantMembership:
    membership = load_active_membership(
        conn,
        tenant_id=tenant_id,
        operator_id=operator_id,
        for_update=for_update,
    )
    if permission not in membership.permissions:
        raise HumanFilingPermissionDenied(
            f"tenant_permission_required:{permission}"
        )
    return membership


def list_active_tenant_participants(
    conn: Any,
    *,
    tenant_id: str,
    limit: int = HUMAN_FILING_PARTICIPANT_LIMIT,
) -> tuple[list[dict[str, Any]], bool]:
    bounded = max(1, min(int(limit), HUMAN_FILING_PARTICIPANT_LIMIT))
    rows = conn.execute(text(
        """
        SELECT m.id AS membership_id, m.tenant_id, m.principal_id,
               m.operator_id, m.role, m.version, o.display_name
        FROM rtm_connect_a1s_memberships m
        JOIN rtm_connect_a1s_tenants t ON t.id=m.tenant_id
        JOIN rtm_operators o ON o.id=m.operator_id
        WHERE m.tenant_id=CAST(:tenant_id AS UUID)
          AND m.status='active' AND m.synthetic_only=TRUE
          AND m.revoked_at IS NULL
          AND t.status='active' AND t.synthetic_only=TRUE
          AND o.status='active'
          AND o.must_change_password=FALSE
          AND o.mfa_required=FALSE
          AND (o.locked_until IS NULL OR o.locked_until <= NOW())
        ORDER BY LOWER(o.display_name), m.id
        LIMIT :fetch_limit
        """
    ), {
        "tenant_id": tenant_id,
        "fetch_limit": bounded + 1,
    }).mappings().all()
    truncated = len(rows) > bounded
    participants: list[dict[str, Any]] = []
    for row in rows[:bounded]:
        role = str(row["role"])
        try:
            eligible_for = eligible_actions_for_role(role)
        except HumanFilingPermissionDenied:
            continue
        participants.append({
            "membership_id": str(row["membership_id"]),
            "principal_id": str(row["principal_id"]),
            "operator_id": str(row["operator_id"]),
            "display_name": str(row["display_name"]),
            "role": role,
            "eligible_for": list(eligible_for),
            "version": int(row["version"]),
        })
    return participants, truncated


def list_active_operator_tenants(
    conn: Any,
    *,
    operator_id: str,
    limit: int = HUMAN_FILING_TENANT_OPTION_LIMIT,
) -> tuple[list[dict[str, Any]], bool]:
    """Descubre solo memberships A1-S del principal autenticado."""

    bounded = max(1, min(int(limit), HUMAN_FILING_TENANT_OPTION_LIMIT))
    rows = conn.execute(text(
        """
        SELECT t.id AS tenant_id, t.tenant_code, t.display_name,
               m.id AS membership_id, m.principal_id, m.operator_id,
               m.role, m.version
        FROM rtm_connect_a1s_memberships m
        JOIN rtm_connect_a1s_tenants t ON t.id=m.tenant_id
        JOIN rtm_operators o ON o.id=m.operator_id
        WHERE m.operator_id=CAST(:operator_id AS UUID)
          AND m.status='active' AND m.synthetic_only=TRUE
          AND m.revoked_at IS NULL
          AND t.status='active' AND t.synthetic_only=TRUE
          AND o.status='active'
          AND o.must_change_password=FALSE
          AND o.mfa_required=FALSE
          AND (o.locked_until IS NULL OR o.locked_until <= NOW())
        ORDER BY LOWER(t.display_name), t.id
        LIMIT :fetch_limit
        """
    ), {
        "operator_id": operator_id,
        "fetch_limit": bounded + 1,
    }).mappings().all()
    tenants: list[dict[str, Any]] = []
    for row in rows[:bounded]:
        role = str(row["role"])
        try:
            permissions = permissions_for_role(role)
        except HumanFilingPermissionDenied:
            continue
        tenants.append({
            "tenant_id": str(row["tenant_id"]),
            "tenant_code": str(row["tenant_code"]),
            "display_name": str(row["display_name"]),
            "membership_id": str(row["membership_id"]),
            "principal_id": str(row["principal_id"]),
            "operator_id": str(row["operator_id"]),
            "role": role,
            "permissions": list(permissions),
            "version": int(row["version"]),
        })
    return tenants, len(rows) > bounded


def load_case_scope(
    conn: Any,
    *,
    tenant_id: str,
    case_binding_id: str,
    representation_evidence_id: str,
    for_update: bool = False,
) -> dict[str, Any]:
    lock = " FOR UPDATE OF b, r" if for_update else ""
    row = _one_mapping(conn.execute(text(
        """
        SELECT b.id AS case_binding_id, b.tenant_id, b.case_id,
               b.case_snapshot_sha256, b.binding_code,
               r.id AS representation_evidence_id,
               r.evidence_sha256 AS representation_evidence_sha256,
               r.representation_code, r.kind AS representation_kind,
               r.valid_from, r.expires_at
        FROM rtm_connect_a1s_case_bindings b
        JOIN cases c ON c.id=b.case_id
        JOIN rtm_connect_a1s_representation_evidence r
          ON r.case_binding_id=b.id AND r.tenant_id=b.tenant_id
        WHERE b.id=CAST(:binding_id AS UUID)
          AND b.tenant_id=CAST(:tenant_id AS UUID)
          AND b.status='active' AND b.synthetic_only=TRUE
          AND b.revoked_at IS NULL
          AND COALESCE(c.test_mode,FALSE)=TRUE
          AND r.id=CAST(:representation_id AS UUID)
          AND r.status='active' AND r.synthetic_only=TRUE
          AND r.revoked_at IS NULL
          AND r.valid_from <= NOW()
          AND (r.expires_at IS NULL OR r.expires_at > NOW())
        """ + lock
    ), {
        "binding_id": case_binding_id,
        "tenant_id": tenant_id,
        "representation_id": representation_evidence_id,
    }))
    if not row:
        raise HumanFilingScopeError("a1s_case_or_representation_scope_invalid")
    return dict(row)


def assert_frozen_case_document_hashes(
    conn: Any,
    *,
    tenant_id: str,
    case_binding_id: str,
    document_hashes: Sequence[str],
) -> None:
    """Exige que todos los hashes congelados existan en el case sintetico."""

    normalized = tuple(str(value).strip().lower() for value in document_hashes)
    if not normalized or len(set(normalized)) != len(normalized):
        raise HumanFilingScopeError("frozen_document_hashes_invalid")
    found = {
        str(value)
        for value in conn.execute(text(
            """
            SELECT DISTINCT d.sha256
            FROM rtm_connect_a1s_case_bindings b
            JOIN cases c ON c.id=b.case_id
            JOIN documents d ON d.case_id=c.id
            JOIN LATERAL jsonb_array_elements_text(
                CAST(:document_hashes AS JSONB)
            ) required(sha256) ON required.sha256=d.sha256
            WHERE b.id=CAST(:binding_id AS UUID)
              AND b.tenant_id=CAST(:tenant_id AS UUID)
              AND b.status='active' AND b.synthetic_only=TRUE
              AND b.revoked_at IS NULL
              AND b.metadata @> CAST(:test_mode_metadata AS JSONB)
              AND COALESCE(c.test_mode,FALSE)=TRUE
              AND d.sha256 IS NOT NULL
            """
        ), {
            "binding_id": case_binding_id,
            "tenant_id": tenant_id,
            "document_hashes": _json(list(normalized)),
            "test_mode_metadata": _json({"test_mode": True}),
        }).scalars().all()
    }
    if found != set(normalized):
        raise HumanFilingScopeError(
            "frozen_document_hash_missing_from_bound_synthetic_case"
        )


def load_action_and_grant(
    conn: Any,
    *,
    action_id: str,
    authorization_id: str,
    for_update: bool = False,
) -> tuple[ConnectActionRequest, int, AuthorizationGrant, str]:
    lock = " FOR UPDATE OF a, g" if for_update else ""
    row = _one_mapping(conn.execute(text(
        """
        SELECT a.*, g.id AS grant_id,
               g.authorization_version AS grant_version,
               g.authority_code AS grant_authority_code,
               g.authority_version AS grant_authority_version,
               g.decision AS grant_decision,
               g.payload_sha256 AS grant_payload_sha256,
               g.idempotency_key AS grant_idempotency_key,
               g.required_evidence_level,
               g.authorized_connector_modes,
               g.approved_by_operator_ids,
               g.authorized_at AS grant_authorized_at,
               g.expires_at AS grant_expires_at,
               g.revoked_at AS grant_revoked_at,
               g.legal_effect_authorized,
               g.frozen AS grant_frozen
        FROM rtm_connect_actions a
        JOIN rtm_connect_authorizations g ON g.action_id=a.id
        WHERE a.id=CAST(:action_id AS UUID)
          AND g.id=CAST(:authorization_id AS UUID)
          AND NOT EXISTS (
              SELECT 1
              FROM rtm_connect_authorizations newer
              WHERE newer.action_id=g.action_id
                AND newer.authorization_version > g.authorization_version
          )
        """ + lock
    ), {"action_id": action_id, "authorization_id": authorization_id}))
    if not row:
        raise HumanFilingNotFound("a1s_action_or_authorization_not_found")
    timestamp = lambda value: (
        value.isoformat() if getattr(value, "isoformat", None) else str(value)
    )
    action = ConnectActionRequest(
        action_id=str(row["id"]),
        case_id=str(row["case_id"]) if row["case_id"] else None,
        capability=str(row["capability"]),
        satellite=str(row["satellite"]),
        target_type=str(row["target_type"]),
        target_ref=str(row["target_ref"]),
        payload=dict(row["payload"] or {}),
        document_hashes=tuple(row["document_hashes"] or ()),
        requested_by_operator_id=str(row["requested_by_operator_id"]),
        requested_at=timestamp(row["requested_at"]),
        risk_class=RiskClass(str(row["risk_class"])),
        correlation_id=row["correlation_id"],
        requires_dual_control=bool(row["requires_dual_control"]),
        contract_version=str(row["contract_version"]),
    )
    grant = AuthorizationGrant(
        authorization_id=str(row["grant_id"]),
        action_id=action.action_id,
        authority_code=str(row["grant_authority_code"]),
        authority_version=str(row["grant_authority_version"]),
        decision=str(row["grant_decision"]),
        payload_sha256=str(row["grant_payload_sha256"]),
        idempotency_key=str(row["grant_idempotency_key"]),
        required_evidence_level=EvidenceLevel(
            str(row["required_evidence_level"])
        ),
        authorized_connector_modes=tuple(
            ConnectorMode(str(value))
            for value in (row["authorized_connector_modes"] or ())
        ),
        approved_by_operator_ids=tuple(
            str(value) for value in (row["approved_by_operator_ids"] or ())
        ),
        authorized_at=timestamp(row["grant_authorized_at"]),
        expires_at=(
            timestamp(row["grant_expires_at"])
            if row["grant_expires_at"] else None
        ),
        revoked_at=(
            timestamp(row["grant_revoked_at"])
            if row["grant_revoked_at"] else None
        ),
        legal_effect_authorized=bool(row["legal_effect_authorized"]),
        frozen=bool(row["grant_frozen"]),
    )
    return action, int(row["grant_version"]), grant, str(row["status"])


def list_preparation_candidates(
    conn: Any,
    *,
    tenant_id: str,
    requested_by_operator_id: str,
    scan_limit: int = HUMAN_FILING_PREPARATION_SCAN_LIMIT,
) -> list[PreparationCandidate]:
    """Carga un conjunto acotado; el servicio revalida autoridad uno a uno."""

    bounded = max(
        1,
        min(int(scan_limit), HUMAN_FILING_PREPARATION_SCAN_LIMIT),
    )
    rows = conn.execute(text(
        """
        SELECT
            b.id AS binding_id, b.tenant_id, b.case_id, b.binding_code,
            b.case_snapshot_sha256, b.version AS binding_version,
            r.id AS representation_id,
            r.representation_code, r.kind AS representation_kind,
            r.evidence_sha256 AS representation_evidence_sha256,
            r.expires_at AS representation_expires_at,
            r.version AS representation_version,
            a.id AS action_id, a.capability, a.satellite, a.target_type,
            a.target_ref, a.payload, a.document_hashes, a.risk_class,
            a.requested_by_operator_id, a.requested_at,
            a.requires_dual_control, a.contract_version,
            a.correlation_id, a.status AS action_status,
            a.status_version AS action_status_version,
            g.id AS authorization_id,
            g.authorization_version, g.authority_code, g.authority_version,
            g.decision, g.payload_sha256, g.idempotency_key,
            g.required_evidence_level, g.authorized_connector_modes,
            g.approved_by_operator_ids, g.authorized_at,
            g.expires_at AS authorization_expires_at, g.revoked_at,
            g.legal_effect_authorized, g.frozen
        FROM rtm_connect_a1s_case_bindings b
        JOIN cases c ON c.id=b.case_id
        JOIN rtm_connect_a1s_representation_evidence r
          ON r.case_binding_id=b.id AND r.tenant_id=b.tenant_id
        JOIN rtm_connect_actions a ON a.case_id=b.case_id
        JOIN LATERAL (
            SELECT candidate.*
            FROM rtm_connect_authorizations candidate
            WHERE candidate.action_id=a.id
            ORDER BY candidate.authorization_version DESC
            LIMIT 1
        ) g ON TRUE
        WHERE b.tenant_id=CAST(:tenant_id AS UUID)
          AND b.status='active' AND b.synthetic_only=TRUE
          AND b.revoked_at IS NULL
          AND b.metadata @> CAST(:test_mode_metadata AS JSONB)
          AND COALESCE(c.test_mode,FALSE)=TRUE
          AND r.status='active' AND r.synthetic_only=TRUE
          AND r.revoked_at IS NULL
          AND r.valid_from <= NOW()
          AND (r.expires_at IS NULL OR r.expires_at > NOW())
          AND a.requested_by_operator_id=CAST(:operator_id AS UUID)
          AND a.status='authorized'
          AND a.payload->>'case_binding_id'=b.id::text
          AND a.payload->>'representation_evidence_id'=r.id::text
          AND a.payload->>'case_snapshot_sha256'=b.case_snapshot_sha256
          AND g.decision='approved_frozen' AND g.frozen=TRUE
          AND g.revoked_at IS NULL
          AND (g.expires_at IS NULL OR g.expires_at > NOW())
          AND NOT EXISTS (
              SELECT 1 FROM rtm_connect_a1s_human_tasks task
              WHERE task.action_id=a.id AND task.tenant_id=b.tenant_id
          )
        ORDER BY a.requested_at ASC, a.id, b.id, r.id
        LIMIT :scan_limit
        """
    ), {
        "tenant_id": tenant_id,
        "operator_id": requested_by_operator_id,
        "scan_limit": bounded,
        "test_mode_metadata": _json({"test_mode": True}),
    }).mappings().all()

    candidates: list[PreparationCandidate] = []
    for row in rows:
        action = ConnectActionRequest(
            action_id=str(row["action_id"]),
            case_id=str(row["case_id"]),
            capability=str(row["capability"]),
            satellite=str(row["satellite"]),
            target_type=str(row["target_type"]),
            target_ref=str(row["target_ref"]),
            payload=dict(row["payload"] or {}),
            document_hashes=tuple(row["document_hashes"] or ()),
            requested_by_operator_id=str(row["requested_by_operator_id"]),
            requested_at=str(_timestamp(row["requested_at"])),
            risk_class=RiskClass(str(row["risk_class"])),
            correlation_id=row["correlation_id"],
            requires_dual_control=bool(row["requires_dual_control"]),
            contract_version=str(row["contract_version"]),
        )
        grant = AuthorizationGrant(
            authorization_id=str(row["authorization_id"]),
            action_id=action.action_id,
            authority_code=str(row["authority_code"]),
            authority_version=str(row["authority_version"]),
            decision=str(row["decision"]),
            payload_sha256=str(row["payload_sha256"]),
            idempotency_key=str(row["idempotency_key"]),
            required_evidence_level=EvidenceLevel(
                str(row["required_evidence_level"])
            ),
            authorized_connector_modes=tuple(
                ConnectorMode(str(value))
                for value in (row["authorized_connector_modes"] or ())
            ),
            approved_by_operator_ids=tuple(
                str(value)
                for value in (row["approved_by_operator_ids"] or ())
            ),
            authorized_at=str(_timestamp(row["authorized_at"])),
            expires_at=_timestamp(row["authorization_expires_at"]),
            revoked_at=_timestamp(row["revoked_at"]),
            legal_effect_authorized=bool(row["legal_effect_authorized"]),
            frozen=bool(row["frozen"]),
        )
        projection = {
            "case_binding": {
                "id": str(row["binding_id"]),
                "case_id": str(row["case_id"]),
                "code": str(row["binding_code"]),
                "case_snapshot_sha256": str(row["case_snapshot_sha256"]),
                "version": int(row["binding_version"]),
            },
            "representation": {
                "id": str(row["representation_id"]),
                "code": str(row["representation_code"]),
                "kind": str(row["representation_kind"]),
                "evidence_sha256": str(
                    row["representation_evidence_sha256"]
                ),
                "expires_at": _timestamp(row["representation_expires_at"]),
                "version": int(row["representation_version"]),
            },
            "action": {
                "id": action.action_id,
                "version": int(row["action_status_version"]),
                "request_sha256": grant.payload_sha256,
                "document_hashes": list(action.document_hashes),
            },
            "authorization": {
                "id": grant.authorization_id,
                "version": int(row["authorization_version"]),
                "expires_at": grant.expires_at,
            },
        }
        candidates.append(PreparationCandidate(projection, action, grant))
    return candidates


def claim_idempotency(
    conn: Any,
    *,
    tenant_id: str,
    idempotency_key: str,
    scope: str,
    request_sha256: str,
    operator_id: str,
) -> IdempotencyClaim:
    idempotency_key = (
        a1s_contracts.validate_human_filing_idempotency_key(idempotency_key)
    )
    membership = load_active_membership(
        conn,
        tenant_id=tenant_id,
        operator_id=operator_id,
        for_update=False,
    )
    claim_id = str(uuid.uuid4())
    inserted = _one_mapping(conn.execute(text(
        """
        INSERT INTO rtm_connect_a1s_idempotency(
            id, tenant_id, idempotency_key, scope, request_sha256,
            response_sha256, task_id, action_id, status,
            claimed_by_membership_id, claimed_by_principal_id,
            claimed_by_operator_id, replay_count, created_at, completed_at,
            expires_at, metadata
        ) VALUES (
            CAST(:id AS UUID), CAST(:tenant_id AS UUID), :key, :scope,
            :request_sha256, NULL, NULL, NULL, 'claimed',
            CAST(:membership_id AS UUID), CAST(:principal_id AS UUID),
            CAST(:operator_id AS UUID), 0, NOW(), NULL,
            NOW() + INTERVAL '24 hours', CAST(:metadata AS JSONB)
        )
        ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
        RETURNING id, task_id, action_id, status, replay_count
        """
    ), {
        "id": claim_id,
        "tenant_id": tenant_id,
        "key": idempotency_key,
        "scope": scope,
        "request_sha256": request_sha256,
        "membership_id": membership.membership_id,
        "principal_id": membership.principal_id,
        "operator_id": operator_id,
        "metadata": _json({
            "repository_version": RTM_CONNECT_A1S_REPOSITORY_VERSION,
            "synthetic_marker": a1s_contracts.HUMAN_FILING_MARKER,
            "synthetic_only": True,
        }),
    }))
    if inserted:
        return IdempotencyClaim(True, False, None, None, "claimed", 0)

    existing = _one_mapping(conn.execute(text(
        """
        SELECT id, scope, request_sha256, task_id, action_id,
               status, replay_count, expires_at
        FROM rtm_connect_a1s_idempotency
        WHERE tenant_id=CAST(:tenant_id AS UUID)
          AND idempotency_key=:key
        FOR UPDATE
        """
    ), {"tenant_id": tenant_id, "key": idempotency_key}))
    if not existing:
        raise HumanFilingReplayConflict("idempotency_claim_disappeared")
    if (
        str(existing["scope"]) != scope
        or str(existing["request_sha256"]) != request_sha256
    ):
        raise HumanFilingReplayConflict("idempotency_key_payload_conflict")
    if str(existing["status"]) != "completed":
        raise HumanFilingStateConflict("idempotency_command_in_progress")
    conn.execute(text(
        """
        UPDATE rtm_connect_a1s_idempotency
        SET replay_count=replay_count+1
        WHERE id=CAST(:id AS UUID)
        """
    ), {"id": str(existing["id"])})
    return IdempotencyClaim(
        False,
        True,
        str(existing["task_id"]) if existing["task_id"] else None,
        str(existing["action_id"]) if existing["action_id"] else None,
        "completed",
        int(existing["replay_count"] or 0) + 1,
    )


def complete_idempotency(
    conn: Any,
    *,
    tenant_id: str,
    idempotency_key: str,
    response_material: Mapping[str, Any],
    task_id: str,
    action_id: str,
) -> None:
    changed = conn.execute(text(
        """
        UPDATE rtm_connect_a1s_idempotency
        SET response_sha256=:response_sha256,
            task_id=CAST(:task_id AS UUID),
            action_id=CAST(:action_id AS UUID),
            status='completed', completed_at=NOW()
        WHERE tenant_id=CAST(:tenant_id AS UUID)
          AND idempotency_key=:key AND status='claimed'
        """
    ), {
        "tenant_id": tenant_id,
        "key": idempotency_key,
        "response_sha256": _canonical_sha256(response_material),
        "task_id": task_id,
        "action_id": action_id,
    })
    if getattr(changed, "rowcount", 0) != 1:
        raise HumanFilingReplayConflict("idempotency_completion_conflict")


def append_event(
    conn: Any,
    *,
    task_row: Mapping[str, Any],
    event_type: str,
    actor_type: str,
    operator_id: str | None,
    from_status: str | None,
    to_status: str | None,
    reason_code: str,
    payload: Mapping[str, Any] | None = None,
) -> str:
    if actor_type not in {"operator", "connect", "core", "system"}:
        raise ValueError("human_filing_event_actor_type_invalid")
    operator_actor = actor_type == "operator"
    membership = (
        load_active_membership(
            conn,
            tenant_id=str(task_row["tenant_id"]),
            operator_id=operator_id,
            for_update=False,
        )
        if operator_actor and operator_id else None
    )
    if operator_actor and membership is None:
        raise HumanFilingPermissionDenied(
            "operator_event_requires_active_membership"
        )
    conn.execute(text(
        "SELECT id FROM rtm_connect_a1s_human_tasks "
        "WHERE id=CAST(:id AS UUID) FOR UPDATE"
    ), {"id": str(task_row["id"])}).one()
    sequence = int(conn.execute(text(
        """
        SELECT COALESCE(MAX(sequence_number),0)+1
        FROM rtm_connect_a1s_events
        WHERE task_id=CAST(:task_id AS UUID)
        """
    ), {"task_id": str(task_row["id"])}).scalar_one())
    clean_payload = {
        **dict(payload or {}),
        "synthetic_marker": a1s_contracts.HUMAN_FILING_MARKER,
        "synthetic_only": True,
    }
    event_id = str(uuid.uuid4())
    conn.execute(text(
        """
        INSERT INTO rtm_connect_a1s_events(
            id, tenant_id, task_id, action_id, attempt_id,
            sequence_number, event_type, actor_type, membership_id,
            principal_id, operator_id,
            from_status, to_status, reason_code, payload_sha256,
            payload, synthetic_only, created_at
        ) VALUES (
            CAST(:id AS UUID), CAST(:tenant_id AS UUID),
            CAST(:task_id AS UUID), CAST(:action_id AS UUID),
            CAST(:attempt_id AS UUID), :sequence, :event_type, :actor_type,
            CAST(:membership_id AS UUID), CAST(:principal_id AS UUID),
            CAST(:operator_id AS UUID), :from_status, :to_status,
            :reason_code, :payload_sha256, CAST(:payload AS JSONB), TRUE, NOW()
        )
        """
    ), {
        "id": event_id,
        "tenant_id": str(task_row["tenant_id"]),
        "task_id": str(task_row["id"]),
        "action_id": str(task_row["action_id"]),
        "attempt_id": str(task_row["attempt_id"]),
        "sequence": sequence,
        "event_type": event_type,
        "actor_type": actor_type,
        "membership_id": membership.membership_id if membership else None,
        "principal_id": membership.principal_id if membership else None,
        "operator_id": operator_id if operator_actor else None,
        "from_status": from_status,
        "to_status": to_status,
        "reason_code": reason_code,
        "payload_sha256": _canonical_sha256(clean_payload),
        "payload": _json(clean_payload),
    })
    return event_id


def create_task(
    conn: Any,
    *,
    tenant_id: str,
    case_binding_id: str,
    representation_evidence_id: str,
    action_id: str,
    attempt_id: str,
    connector_id: str,
    authorization_id: str,
    authorization_version: int,
    task_id: str,
    task_code: str,
    requester_membership: TenantMembership,
    assignee_membership: TenantMembership | None,
    due_at: str,
    package_manifest: Mapping[str, Any],
    package_sha256: str,
) -> dict[str, Any]:
    if _canonical_sha256(package_manifest) != str(package_sha256):
        raise ValueError("a1s_package_sha256_must_match_canonical_manifest")
    conn.execute(text(
        """
        INSERT INTO rtm_connect_a1s_human_tasks(
            id, tenant_id, case_binding_id, representation_evidence_id,
            action_id, attempt_id, connector_id, authorization_id,
            authorization_version, task_code, status,
            requester_membership_id, requester_principal_id,
            requester_operator_id,
            assignee_operator_id, assignee_membership_id,
            assignee_principal_id, assigned_by_operator_id,
            due_at, assigned_at, package_manifest, package_sha256,
            version, metadata, created_at, updated_at
        ) VALUES (
            CAST(:id AS UUID), CAST(:tenant_id AS UUID),
            CAST(:case_binding_id AS UUID), CAST(:representation_id AS UUID),
            CAST(:action_id AS UUID), CAST(:attempt_id AS UUID),
            CAST(:connector_id AS UUID), CAST(:authorization_id AS UUID),
            :authorization_version, :task_code, 'prepared',
            CAST(:requester_membership_id AS UUID),
            CAST(:requester_principal_id AS UUID),
            CAST(:requester_operator_id AS UUID),
            CAST(:assignee_operator_id AS UUID),
            CAST(:assignee_membership_id AS UUID),
            CAST(:assignee_principal_id AS UUID),
            CAST(:assigned_by_operator_id AS UUID),
            CAST(:due_at AS TIMESTAMPTZ), NULL, CAST(:package AS JSONB),
            :package_sha256, 1, CAST(:metadata AS JSONB), NOW(), NOW()
        )
        """
    ), {
        "id": task_id,
        "tenant_id": tenant_id,
        "case_binding_id": case_binding_id,
        "representation_id": representation_evidence_id,
        "action_id": action_id,
        "attempt_id": attempt_id,
        "connector_id": connector_id,
        "authorization_id": authorization_id,
        "authorization_version": authorization_version,
        "task_code": task_code,
        "requester_membership_id": requester_membership.membership_id,
        "requester_principal_id": requester_membership.principal_id,
        "requester_operator_id": requester_membership.operator_id,
        "assignee_operator_id": (
            assignee_membership.operator_id if assignee_membership else None
        ),
        "assignee_membership_id": (
            assignee_membership.membership_id if assignee_membership else None
        ),
        "assignee_principal_id": (
            assignee_membership.principal_id if assignee_membership else None
        ),
        "assigned_by_operator_id": None,
        "due_at": due_at,
        "package": _json(dict(package_manifest)),
        "package_sha256": package_sha256,
        "metadata": _json({
            "repository_version": RTM_CONNECT_A1S_REPOSITORY_VERSION,
            "synthetic_marker": a1s_contracts.HUMAN_FILING_MARKER,
            "synthetic_only": True,
            "network_used": False,
            "b2_used": False,
            "provider_contacted": False,
            "legal_submission_executed": False,
        }),
    })
    row = task_row(conn, tenant_id=tenant_id, task_id=task_id, for_update=True)
    append_event(
        conn,
        task_row=row,
        event_type="human_filing.prepared",
        actor_type="operator",
        operator_id=requester_membership.operator_id,
        from_status=None,
        to_status="prepared",
        reason_code="a1s_package_frozen",
        payload={"package_sha256": package_sha256},
    )
    return _task_projection(row)


def task_row(
    conn: Any,
    *,
    tenant_id: str,
    task_id: str,
    for_update: bool = False,
) -> Mapping[str, Any]:
    lock = " FOR UPDATE OF t" if for_update else ""
    row = _one_mapping(conn.execute(text(
        """
        SELECT t.*, b.case_id
        FROM rtm_connect_a1s_human_tasks t
        JOIN rtm_connect_a1s_case_bindings b
          ON b.id=t.case_binding_id AND b.tenant_id=t.tenant_id
        WHERE t.id=CAST(:task_id AS UUID)
          AND t.tenant_id=CAST(:tenant_id AS UUID)
          AND t.metadata @> CAST(:synthetic_metadata AS JSONB)
        """ + lock
    ), {
        "task_id": task_id,
        "tenant_id": tenant_id,
        "synthetic_metadata": _json({"synthetic_only": True}),
    }))
    if not row:
        raise HumanFilingNotFound("human_filing_task_not_found")
    return row


def task_snapshot(
    conn: Any,
    *,
    tenant_id: str,
    task_id: str,
    replayed: bool = False,
) -> dict[str, Any]:
    return _task_projection(
        task_row(conn, tenant_id=tenant_id, task_id=task_id),
        replayed=replayed,
    )


def list_tasks(
    conn: Any,
    *,
    tenant_id: str,
    status: str | None,
    assignee_operator_id: str | None,
    overdue_only: bool,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    predicates = ["t.tenant_id=CAST(:tenant_id AS UUID)"]
    params: dict[str, Any] = {
        "tenant_id": tenant_id,
        "limit": limit,
        "offset": offset,
    }
    if status:
        predicates.append("t.status=:status")
        params["status"] = status
    if assignee_operator_id:
        predicates.append(
            "t.assignee_operator_id=CAST(:assignee_operator_id AS UUID)"
        )
        params["assignee_operator_id"] = assignee_operator_id
    if overdue_only:
        predicates.append(
            "t.due_at < NOW() AND t.status NOT IN "
            "('completed','manual_review','permanent_failed')"
        )
    where = " AND ".join(predicates)
    rows = conn.execute(text(
        f"""
        SELECT t.*, b.case_id
        FROM rtm_connect_a1s_human_tasks t
        JOIN rtm_connect_a1s_case_bindings b
          ON b.id=t.case_binding_id AND b.tenant_id=t.tenant_id
        WHERE {where}
        ORDER BY t.due_at ASC, t.created_at ASC, t.id ASC
        LIMIT :limit OFFSET :offset
        """
    ), params).mappings().all()
    total = int(conn.execute(text(
        f"SELECT COUNT(*) FROM rtm_connect_a1s_human_tasks t WHERE {where}"
    ), params).scalar_one())
    return [_task_projection(row) for row in rows], total


def advance_task(
    conn: Any,
    *,
    tenant_id: str,
    task_id: str,
    expected_version: int,
    target_status: str,
    operator_id: str,
    reason_code: str,
    updates: Mapping[str, Any] | None = None,
    event_payload: Mapping[str, Any] | None = None,
    actor_type: str = "operator",
) -> dict[str, Any]:
    row = task_row(
        conn, tenant_id=tenant_id, task_id=task_id, for_update=True
    )
    if int(row["version"]) != int(expected_version):
        raise HumanFilingOptimisticLockError("human_filing_version_conflict")
    current = str(row["status"])
    try:
        a1s_contracts.validate_human_filing_transition(
            a1s_contracts.HumanFilingTaskStatus(current),
            a1s_contracts.HumanFilingTaskStatus(target_status),
        )
    except Exception as exc:
        raise HumanFilingStateConflict(
            f"invalid_human_filing_transition:{current}:{target_status}"
        ) from exc

    allowed_fields = {
        "assignee_operator_id", "assignee_membership_id",
        "assignee_principal_id", "assigned_by_operator_id", "assigned_at",
        "reviewed_at", "ready_at", "release_operator_id",
        "release_membership_id", "release_principal_id", "released_at",
        "started_at", "awaiting_receipt_at", "unknown_at",
        "reconciling_at", "receipt_submitted_at", "verified_by_operator_id",
        "verified_by_membership_id", "verified_by_principal_id", "verified_at",
        "completed_at", "review_attestation_sha256",
        "release_attestation_sha256", "verification_attestation_sha256",
        "external_reference",
    }
    clauses = [
        "status=:target_status", "version=version+1", "updated_at=NOW()"
    ]
    params: dict[str, Any] = {
        "task_id": task_id,
        "tenant_id": tenant_id,
        "expected_version": expected_version,
        "target_status": target_status,
    }
    for field, value in dict(updates or {}).items():
        if field not in allowed_fields:
            raise ValueError(f"human_filing_field_not_mutable:{field}")
        key = f"update_{field}"
        if field.endswith(("_operator_id", "_membership_id", "_principal_id")):
            expression = f"CAST(:{key} AS UUID)"
        elif field.endswith("_at"):
            expression = f"CAST(:{key} AS TIMESTAMPTZ)"
        else:
            expression = f":{key}"
        clauses.append(f"{field}={expression}")
        params[key] = value
    changed = conn.execute(text(
        "UPDATE rtm_connect_a1s_human_tasks SET " + ", ".join(clauses)
        + " WHERE id=CAST(:task_id AS UUID)"
        + " AND tenant_id=CAST(:tenant_id AS UUID)"
        + " AND version=:expected_version"
    ), params)
    if getattr(changed, "rowcount", 0) != 1:
        raise HumanFilingOptimisticLockError("human_filing_version_conflict")
    append_event(
        conn,
        task_row=row,
        event_type=f"human_filing.{target_status}",
        actor_type=actor_type,
        operator_id=operator_id,
        from_status=current,
        to_status=target_status,
        reason_code=reason_code,
        payload=event_payload,
    )
    return task_snapshot(conn, tenant_id=tenant_id, task_id=task_id)


def record_task_checkpoint(
    conn: Any,
    *,
    tenant_id: str,
    task_id: str,
    expected_version: int,
    operator_id: str,
    event_type: str,
    reason_code: str,
    event_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Registra un checkpoint inmutable y mueve el ETag sin cambiar estado."""

    row = task_row(
        conn, tenant_id=tenant_id, task_id=task_id, for_update=True
    )
    if int(row["version"]) != int(expected_version):
        raise HumanFilingOptimisticLockError("human_filing_version_conflict")
    changed = conn.execute(text(
        """
        UPDATE rtm_connect_a1s_human_tasks
        SET version=version+1, updated_at=NOW()
        WHERE id=CAST(:task_id AS UUID)
          AND tenant_id=CAST(:tenant_id AS UUID)
          AND version=:expected_version
        """
    ), {
        "task_id": task_id,
        "tenant_id": tenant_id,
        "expected_version": expected_version,
    })
    if getattr(changed, "rowcount", 0) != 1:
        raise HumanFilingOptimisticLockError("human_filing_version_conflict")
    append_event(
        conn,
        task_row=row,
        event_type=event_type,
        actor_type="operator",
        operator_id=operator_id,
        from_status=str(row["status"]),
        to_status=str(row["status"]),
        reason_code=reason_code,
        payload=event_payload,
    )
    return task_snapshot(conn, tenant_id=tenant_id, task_id=task_id)


def create_artifact(
    conn: Any,
    *,
    tenant_id: str,
    task_id: str,
    artifact_id: str,
    artifact_code: str,
    kind: str,
    media_type: str,
    sha256: str,
    canonical_payload: Mapping[str, Any],
    submitted_by_operator_id: str,
) -> dict[str, Any]:
    if media_type != "application/json":
        raise ValueError("a1s_artifact_media_type_must_be_application_json")
    if artifact_code != f"rtm-a1s-artifact-{sha256[:24]}":
        raise ValueError("a1s_artifact_code_must_bind_sha256")
    if _canonical_sha256(canonical_payload) != str(sha256):
        raise ValueError("a1s_artifact_sha256_must_match_canonical_payload")
    if (
        canonical_payload.get("synthetic_marker")
        != a1s_contracts.HUMAN_FILING_MARKER
        or canonical_payload.get("synthetic_only") is not True
    ):
        raise ValueError("a1s_artifact_payload_must_be_synthetic")
    row = task_row(conn, tenant_id=tenant_id, task_id=task_id, for_update=True)
    membership = load_active_membership(
        conn,
        tenant_id=tenant_id,
        operator_id=submitted_by_operator_id,
        for_update=False,
    )
    conn.execute(text(
        """
        INSERT INTO rtm_connect_a1s_artifacts(
            id, tenant_id, task_id, artifact_code, kind, media_type,
            sha256, canonical_payload, submitted_by_membership_id,
            submitted_by_principal_id, submitted_by_operator_id,
            verified_by_membership_id, verified_by_principal_id,
            verified_by_operator_id, verified_at, synthetic_only,
            storage_backend, supersedes_artifact_id, version, created_at
        ) VALUES (
            CAST(:id AS UUID), CAST(:tenant_id AS UUID),
            CAST(:task_id AS UUID), :artifact_code, :kind, :media_type,
            :sha256, CAST(:payload AS JSONB), CAST(:membership_id AS UUID),
            CAST(:principal_id AS UUID), CAST(:operator_id AS UUID),
            NULL, NULL, NULL, NULL, TRUE, 'database_manifest_only',
            NULL, 1, NOW()
        )
        """
    ), {
        "id": artifact_id,
        "tenant_id": tenant_id,
        "task_id": task_id,
        "artifact_code": artifact_code,
        "kind": kind,
        "media_type": media_type,
        "sha256": sha256,
        "payload": _json(dict(canonical_payload)),
        "membership_id": membership.membership_id,
        "principal_id": membership.principal_id,
        "operator_id": submitted_by_operator_id,
    })
    append_event(
        conn,
        task_row=row,
        event_type=f"human_filing.artifact.{kind}",
        actor_type="operator",
        operator_id=submitted_by_operator_id,
        from_status=str(row["status"]),
        to_status=str(row["status"]),
        reason_code="a1s_hash_bound_artifact_recorded",
        payload={"artifact_id": artifact_id, "artifact_sha256": sha256},
    )
    return {
        "artifact_id": artifact_id,
        "artifact_code": artifact_code,
        "kind": kind,
        "media_type": media_type,
        "sha256": sha256,
    }


def artifact_by_kind(
    conn: Any,
    *,
    tenant_id: str,
    task_id: str,
    kind: str,
    phase: str | None = None,
) -> Mapping[str, Any] | None:
    phase_clause = (
        "AND a.canonical_payload->>'phase'=:phase" if phase else ""
    )
    return _one_mapping(conn.execute(text(
        """
        SELECT a.* FROM rtm_connect_a1s_artifacts a
        WHERE a.tenant_id=CAST(:tenant_id AS UUID)
          AND a.task_id=CAST(:task_id AS UUID)
          AND a.kind=:kind
        """ + phase_clause + " ORDER BY a.created_at DESC LIMIT 1"
    ), {
        "tenant_id": tenant_id,
        "task_id": task_id,
        "kind": kind,
        "phase": phase,
    }))


def create_approval(
    conn: Any,
    *,
    tenant_id: str,
    task_id: str,
    approval_type: str,
    membership: TenantMembership,
    attestation_sha256: str,
    artifact_id: str,
) -> dict[str, Any]:
    if approval_type not in {"release", "verification_preapproval"}:
        raise ValueError("human_filing_approval_type_invalid")
    task_row(conn, tenant_id=tenant_id, task_id=task_id, for_update=True)
    approval_id = str(uuid.uuid4())
    try:
        conn.execute(text(
            """
            INSERT INTO rtm_connect_a1s_approvals(
                id, tenant_id, task_id, approval_type, decision,
                membership_id, principal_id, operator_id,
                attestation_sha256, artifact_id, approved_at,
                synthetic_only, created_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:tenant_id AS UUID),
                CAST(:task_id AS UUID), :approval_type, 'approved_frozen',
                CAST(:membership_id AS UUID), CAST(:principal_id AS UUID),
                CAST(:operator_id AS UUID), :attestation_sha256,
                CAST(:artifact_id AS UUID), NOW(), TRUE, NOW()
            )
            """
        ), {
            "id": approval_id,
            "tenant_id": tenant_id,
            "task_id": task_id,
            "approval_type": approval_type,
            "membership_id": membership.membership_id,
            "principal_id": membership.principal_id,
            "operator_id": membership.operator_id,
            "attestation_sha256": attestation_sha256,
            "artifact_id": artifact_id,
        })
    except Exception as exc:
        raise HumanFilingReplayConflict(
            "human_filing_approval_conflict"
        ) from exc
    return {
        "approval_id": approval_id,
        "approval_type": approval_type,
        "decision": "approved_frozen",
        "principal_id": membership.principal_id,
        "operator_id": membership.operator_id,
        "attestation_sha256": attestation_sha256,
        "artifact_id": artifact_id,
    }


def approvals_for_task(
    conn: Any,
    *,
    tenant_id: str,
    task_id: str,
) -> dict[str, Mapping[str, Any]]:
    rows = conn.execute(text(
        """
        SELECT * FROM rtm_connect_a1s_approvals
        WHERE tenant_id=CAST(:tenant_id AS UUID)
          AND task_id=CAST(:task_id AS UUID)
          AND decision='approved_frozen' AND synthetic_only=TRUE
        ORDER BY created_at ASC
        """
    ), {"tenant_id": tenant_id, "task_id": task_id}).mappings().all()
    return {str(row["approval_type"]): row for row in rows}


def task_read_detail(
    conn: Any,
    *,
    tenant_id: str,
    task_id: str,
    summary_limit: int = HUMAN_FILING_DETAIL_SUMMARY_LIMIT,
) -> dict[str, Any]:
    """Proyecta detalle seguro sin payloads de eventos ni artefactos."""

    bounded = max(1, min(int(summary_limit), HUMAN_FILING_DETAIL_SUMMARY_LIMIT))
    row = task_row(conn, tenant_id=tenant_id, task_id=task_id)
    detail = _task_projection(row)
    detail["package_manifest"] = _safe_package_manifest(
        row.get("package_manifest")
    )

    approval_rows = conn.execute(text(
        """
        SELECT id, approval_type, decision, principal_id, operator_id,
               attestation_sha256, artifact_id, approved_at
        FROM rtm_connect_a1s_approvals
        WHERE tenant_id=CAST(:tenant_id AS UUID)
          AND task_id=CAST(:task_id AS UUID)
          AND decision='approved_frozen' AND synthetic_only=TRUE
        ORDER BY approved_at ASC, id ASC
        """
    ), {"tenant_id": tenant_id, "task_id": task_id}).mappings().all()
    detail["approvals"] = [
        {
            "approval_id": str(item["id"]),
            "approval_type": str(item["approval_type"]),
            "decision": str(item["decision"]),
            "principal_id": str(item["principal_id"]),
            "operator_id": str(item["operator_id"]),
            "attestation_sha256": str(item["attestation_sha256"]),
            "artifact_id": str(item["artifact_id"]),
            "approved_at": _timestamp(item["approved_at"]),
        }
        for item in approval_rows
    ]

    artifact_rows = conn.execute(text(
        """
        SELECT id, artifact_code, kind, media_type, sha256,
               submitted_by_principal_id, submitted_by_operator_id,
               verified_by_principal_id, verified_by_operator_id,
               verified_at, version, created_at
        FROM rtm_connect_a1s_artifacts
        WHERE tenant_id=CAST(:tenant_id AS UUID)
          AND task_id=CAST(:task_id AS UUID)
          AND synthetic_only=TRUE
        ORDER BY created_at DESC, id DESC
        LIMIT :fetch_limit
        """
    ), {
        "tenant_id": tenant_id,
        "task_id": task_id,
        "fetch_limit": bounded + 1,
    }).mappings().all()
    detail["artifacts_truncated"] = len(artifact_rows) > bounded
    detail["artifacts"] = list(reversed([
        {
            "artifact_id": str(item["id"]),
            "artifact_code": str(item["artifact_code"]),
            "kind": str(item["kind"]),
            "media_type": str(item["media_type"]),
            "sha256": str(item["sha256"]),
            "submitted_by_principal_id": str(
                item["submitted_by_principal_id"]
            ),
            "submitted_by_operator_id": str(
                item["submitted_by_operator_id"]
            ),
            "verified_by_principal_id": (
                str(item["verified_by_principal_id"])
                if item["verified_by_principal_id"] else None
            ),
            "verified_by_operator_id": (
                str(item["verified_by_operator_id"])
                if item["verified_by_operator_id"] else None
            ),
            "verified_at": _timestamp(item["verified_at"]),
            "version": int(item["version"]),
            "created_at": _timestamp(item["created_at"]),
        }
        for item in artifact_rows[:bounded]
    ]))

    receipt_row = _one_mapping(conn.execute(text(
        """
        SELECT id,
               canonical_payload->>'document_id' AS document_id,
               canonical_payload->>'document_sha256' AS document_sha256,
               canonical_payload->>'external_reference' AS external_reference,
               canonical_payload->>'package_sha256' AS package_sha256,
               canonical_payload->>'witnessed_at' AS witnessed_at,
               created_at
        FROM rtm_connect_a1s_artifacts
        WHERE tenant_id=CAST(:tenant_id AS UUID)
          AND task_id=CAST(:task_id AS UUID)
          AND kind='synthetic_receipt' AND synthetic_only=TRUE
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """
    ), {"tenant_id": tenant_id, "task_id": task_id}))
    detail["receipt_summary"] = (
        {
            "artifact_id": str(receipt_row["id"]),
            "document_id": str(receipt_row["document_id"]),
            "document_sha256": str(receipt_row["document_sha256"]),
            "external_reference": str(receipt_row["external_reference"]),
            "package_sha256": str(receipt_row["package_sha256"]),
            "witnessed_at": str(receipt_row["witnessed_at"]),
            "created_at": _timestamp(receipt_row["created_at"]),
        }
        if receipt_row else None
    )

    event_rows = conn.execute(text(
        """
        SELECT id, sequence_number, event_type, actor_type, principal_id,
               operator_id, from_status, to_status, reason_code,
               payload_sha256, created_at
        FROM rtm_connect_a1s_events
        WHERE tenant_id=CAST(:tenant_id AS UUID)
          AND task_id=CAST(:task_id AS UUID)
          AND synthetic_only=TRUE
        ORDER BY sequence_number DESC
        LIMIT :fetch_limit
        """
    ), {
        "tenant_id": tenant_id,
        "task_id": task_id,
        "fetch_limit": bounded + 1,
    }).mappings().all()
    detail["events_truncated"] = len(event_rows) > bounded
    detail["events"] = list(reversed([
        {
            "event_id": str(item["id"]),
            "sequence_number": int(item["sequence_number"]),
            "event_type": str(item["event_type"]),
            "actor_type": str(item["actor_type"]),
            "principal_id": (
                str(item["principal_id"]) if item["principal_id"] else None
            ),
            "operator_id": (
                str(item["operator_id"]) if item["operator_id"] else None
            ),
            "from_status": item["from_status"],
            "to_status": item["to_status"],
            "reason_code": str(item["reason_code"]),
            "payload_sha256": str(item["payload_sha256"]),
            "created_at": _timestamp(item["created_at"]),
        }
        for item in event_rows[:bounded]
    ]))
    detail["summary_limit"] = bounded
    return detail


def load_fixture_document(
    conn: Any,
    *,
    tenant_id: str,
    task_id: str,
    document_id: str,
    expected_sha256: str,
) -> dict[str, Any]:
    row = _one_mapping(conn.execute(text(
        """
        SELECT d.id, d.case_id, d.kind, d.sha256, d.mime, d.size_bytes
        FROM rtm_connect_a1s_human_tasks t
        JOIN rtm_connect_a1s_case_bindings b
          ON b.id=t.case_binding_id AND b.tenant_id=t.tenant_id
        JOIN cases c ON c.id=b.case_id
        JOIN documents d ON d.case_id=c.id
        WHERE t.id=CAST(:task_id AS UUID)
          AND t.tenant_id=CAST(:tenant_id AS UUID)
          AND b.status='active' AND b.synthetic_only=TRUE
          AND b.revoked_at IS NULL
          AND b.metadata @> CAST(:test_mode_metadata AS JSONB)
          AND d.id=CAST(:document_id AS UUID)
          AND d.sha256=:sha256
          AND d.kind='rtm_connect_a1s_synthetic_receipt_fixture'
          AND d.mime='application/json'
          AND d.size_bytes BETWEEN 1 AND 65536
          AND d.b2_bucket IS NULL AND d.b2_key IS NULL
          AND COALESCE(c.test_mode,FALSE)=TRUE
          AND NOT EXISTS (
              SELECT 1
              FROM jsonb_array_elements_text(
                  t.package_manifest->'document_hashes'
              ) AS frozen_input(document_sha256)
              WHERE frozen_input.document_sha256=d.sha256
          )
        """
    ), {
        "task_id": task_id,
        "tenant_id": tenant_id,
        "document_id": document_id,
        "sha256": expected_sha256,
        "test_mode_metadata": _json({"test_mode": True}),
    }))
    if not row:
        raise HumanFilingScopeError("synthetic_fixture_document_mismatch")
    return {
        "document_id": str(row["id"]),
        "case_id": str(row["case_id"]),
        "kind": str(row["kind"] or ""),
        "sha256": str(row["sha256"]),
        "media_type": str(row["mime"] or "application/json"),
        "size_bytes": int(row["size_bytes"] or 0),
    }


def list_receipt_fixture_options(
    conn: Any,
    *,
    tenant_id: str,
    task_id: str,
    limit: int = HUMAN_FILING_RECEIPT_OPTION_LIMIT,
) -> tuple[list[dict[str, Any]], bool]:
    """Lista metadata mínima de recibos sintéticos elegibles, nunca contenido."""

    bounded = max(1, min(int(limit), HUMAN_FILING_RECEIPT_OPTION_LIMIT))
    rows = conn.execute(text(
        """
        SELECT d.id, d.kind, d.sha256, d.mime, d.size_bytes
        FROM rtm_connect_a1s_human_tasks t
        JOIN rtm_connect_a1s_case_bindings b
          ON b.id=t.case_binding_id AND b.tenant_id=t.tenant_id
        JOIN cases c ON c.id=b.case_id
        JOIN documents d ON d.case_id=c.id
        WHERE t.id=CAST(:task_id AS UUID)
          AND t.tenant_id=CAST(:tenant_id AS UUID)
          AND b.status='active' AND b.synthetic_only=TRUE
          AND b.revoked_at IS NULL
          AND b.metadata @> CAST(:test_mode_metadata AS JSONB)
          AND d.kind='rtm_connect_a1s_synthetic_receipt_fixture'
          AND d.sha256 ~ '^[0-9a-f]{64}$'
          AND d.mime='application/json'
          AND d.size_bytes BETWEEN 1 AND 65536
          AND d.b2_bucket IS NULL AND d.b2_key IS NULL
          AND COALESCE(c.test_mode,FALSE)=TRUE
          AND NOT EXISTS (
              SELECT 1
              FROM jsonb_array_elements_text(
                  t.package_manifest->'document_hashes'
              ) AS frozen_input(document_sha256)
              WHERE frozen_input.document_sha256=d.sha256
          )
        ORDER BY d.id
        LIMIT :fetch_limit
        """
    ), {
        "task_id": task_id,
        "tenant_id": tenant_id,
        "fetch_limit": bounded + 1,
        "test_mode_metadata": _json({"test_mode": True}),
    }).mappings().all()
    return ([
        {
            "document_id": str(row["id"]),
            "document_sha256": str(row["sha256"]),
            "kind": str(row["kind"]),
            "media_type": str(row["mime"]),
            "size_bytes": int(row["size_bytes"]),
        }
        for row in rows[:bounded]
    ], len(rows) > bounded)


class HumanFilingRepository:
    """Fachada explicita para inyeccion y auditoria del repositorio A1-S."""

    load_active_membership = staticmethod(load_active_membership)
    require_tenant_permission = staticmethod(require_tenant_permission)
    list_active_tenant_participants = staticmethod(
        list_active_tenant_participants
    )
    list_active_operator_tenants = staticmethod(list_active_operator_tenants)
    list_preparation_candidates = staticmethod(list_preparation_candidates)
    load_case_scope = staticmethod(load_case_scope)
    assert_frozen_case_document_hashes = staticmethod(
        assert_frozen_case_document_hashes
    )
    load_action_and_grant = staticmethod(load_action_and_grant)
    claim_idempotency = staticmethod(claim_idempotency)
    complete_idempotency = staticmethod(complete_idempotency)
    append_event = staticmethod(append_event)
    create_task = staticmethod(create_task)
    task_row = staticmethod(task_row)
    task_snapshot = staticmethod(task_snapshot)
    list_tasks = staticmethod(list_tasks)
    advance_task = staticmethod(advance_task)
    record_task_checkpoint = staticmethod(record_task_checkpoint)
    create_artifact = staticmethod(create_artifact)
    artifact_by_kind = staticmethod(artifact_by_kind)
    create_approval = staticmethod(create_approval)
    approvals_for_task = staticmethod(approvals_for_task)
    task_read_detail = staticmethod(task_read_detail)
    load_fixture_document = staticmethod(load_fixture_document)
    list_receipt_fixture_options = staticmethod(list_receipt_fixture_options)


__all__ = [
    "RTM_CONNECT_A1S_REPOSITORY_VERSION",
    "HUMAN_FILING_DETAIL_SUMMARY_LIMIT",
    "HUMAN_FILING_PARTICIPANT_LIMIT",
    "HUMAN_FILING_PREPARATION_OPTION_LIMIT",
    "HUMAN_FILING_PREPARATION_SCAN_LIMIT",
    "HUMAN_FILING_RECEIPT_OPTION_LIMIT",
    "HUMAN_FILING_TENANT_OPTION_LIMIT",
    "HumanFilingNotFound",
    "HumanFilingOptimisticLockError",
    "HumanFilingPermissionDenied",
    "HumanFilingReplayConflict",
    "HumanFilingRepository",
    "HumanFilingRepositoryError",
    "HumanFilingScopeError",
    "HumanFilingStateConflict",
    "IdempotencyClaim",
    "PreparationCandidate",
    "TenantMembership",
    "advance_task",
    "append_event",
    "assert_frozen_case_document_hashes",
    "approvals_for_task",
    "artifact_by_kind",
    "claim_idempotency",
    "complete_idempotency",
    "create_approval",
    "create_artifact",
    "create_task",
    "eligible_actions_for_role",
    "list_tasks",
    "list_active_tenant_participants",
    "list_active_operator_tenants",
    "list_preparation_candidates",
    "load_action_and_grant",
    "load_active_membership",
    "load_case_scope",
    "load_fixture_document",
    "list_receipt_fixture_options",
    "record_task_checkpoint",
    "permissions_for_role",
    "require_tenant_permission",
    "task_row",
    "task_read_detail",
    "task_snapshot",
]
