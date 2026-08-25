"""Servicio de aplicacion A1-S para presentacion humana sintetica.

El servicio no presenta, no abre sedes y no persiste binarios.  Coordina el
kernel C1 con fixtures hash-bound y conserva las mismas barreras de autoridad,
separacion de funciones, UNKNOWN e evidencia E4 del diseño CONNECT.
"""

from __future__ import annotations

import dataclasses
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from sqlalchemy import text

from rtm_connect import human_filing_contracts as a1s_contracts
from rtm_connect import human_filing_policy as a1s_policy
from rtm_connect import human_filing_repository as repository
from rtm_connect.authority import AuthorityValidationError, validate_execution_authority
from rtm_connect.contracts import ConnectorMode, EvidenceLevel, EvidenceRecord
from rtm_connect.kernel import (
    ConnectKernelError,
    begin_reconciliation,
    confirm_action,
    queue_action,
    record_attempt_outcome,
    record_evidence,
    record_reconciliation_outcome,
    register_synthetic_connector,
    start_attempt,
)
from rtm_connect.state_machine import ActionStatus
from rtm_connect.repository import _transition_action as _transition_core_action


RTM_CONNECT_A1S_SERVICE_VERSION = "rtm_connect_a1s_human_filing_service_v1_0"

HUMAN_REVIEW_GATE = "A1S_HUMAN_REVIEW_CONFIRMED"
HUMAN_RELEASE_GATE = "A1S_RELEASE_APPROVED"
HUMAN_VERIFICATION_PREAPPROVAL_GATE = "A1S_VERIFIER_PREAPPROVED"
HUMAN_RECEIPT_VERIFICATION_GATE = "A1S_SYNTHETIC_RECEIPT_VERIFIED"
SYNTHETIC_REFERENCE_PREFIX = "a1s-synthetic-"
_SYNTHETIC_REFERENCE_RE = re.compile(r"^a1s-synthetic-[0-9a-f]{24}$")

_PACKAGE_CHECKLIST = a1s_contracts.HUMAN_FILING_FIXED_CHECKLIST
MANUAL_REVIEW_REASON_CODES = frozenset({
    "synthetic_evidence_inadmissible",
    "authority_or_representation_changed",
    "assignment_or_separation_exception",
    "workflow_inconsistency",
})
_MANUAL_REVIEW_SOURCE_STATUSES = frozenset({
    "reviewing",
    "ready_for_release",
    "in_progress",
    "awaiting_receipt",
    "outcome_unknown",
    "reconciling",
    "receipt_submitted",
})
_MANUAL_REVIEW_EXPECTED_CORE_STATUSES = {
    "reviewing": frozenset({ActionStatus.EXECUTING}),
    "ready_for_release": frozenset({ActionStatus.EXECUTING}),
    "in_progress": frozenset({ActionStatus.EXECUTING}),
    "awaiting_receipt": frozenset({ActionStatus.EXTERNAL_ACCEPTED}),
    "outcome_unknown": frozenset({ActionStatus.UNKNOWN}),
    "reconciling": frozenset({ActionStatus.RECONCILING}),
    "receipt_submitted": frozenset({
        ActionStatus.EVIDENCE_PENDING,
        ActionStatus.RECONCILING,
    }),
}


class HumanFilingServiceError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime | str | None = None) -> str:
    selected = value or _now()
    if isinstance(selected, str):
        parsed = datetime.fromisoformat(selected.replace("Z", "+00:00"))
    else:
        parsed = selected
    if parsed.tzinfo is None:
        raise HumanFilingServiceError(
            "request.validation_failed",
            "El timestamp debe incluir zona horaria",
            status_code=422,
        )
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _future_stamp(value: datetime | str) -> str:
    stamped = _stamp(value)
    parsed = datetime.fromisoformat(stamped.replace("Z", "+00:00"))
    if parsed <= _now():
        raise HumanFilingServiceError(
            "request.validation_failed",
            "due_at debe ser futuro",
            status_code=422,
        )
    return stamped


def _digest(value: Any) -> str:
    return a1s_contracts.canonical_sha256(value)


def _service_error(exc: Exception) -> HumanFilingServiceError:
    if isinstance(exc, HumanFilingServiceError):
        return exc
    if isinstance(exc, repository.HumanFilingNotFound):
        return HumanFilingServiceError(
            "human_filing.not_found", "Recurso A1-S no encontrado",
            status_code=404,
        )
    if isinstance(exc, repository.HumanFilingPermissionDenied):
        return HumanFilingServiceError(
            "human_filing.permission_denied",
            "Permiso tenant requerido",
            status_code=403,
        )
    if isinstance(exc, repository.HumanFilingOptimisticLockError):
        return HumanFilingServiceError(
            "human_filing.version_conflict",
            "La version de la tarea ha cambiado",
            status_code=412,
        )
    if isinstance(exc, repository.HumanFilingReplayConflict):
        return HumanFilingServiceError(
            "human_filing.replay_conflict",
            "La clave idempotente ya esta ligada a otro comando",
            status_code=409,
        )
    if isinstance(exc, repository.HumanFilingScopeError):
        return HumanFilingServiceError(
            "human_filing.scope_invalid",
            "El recurso no pertenece al scope A1-S",
            status_code=409,
        )
    if isinstance(exc, repository.HumanFilingStateConflict):
        return HumanFilingServiceError(
            "human_filing.state_conflict",
            "La tarea no admite esta operacion",
            status_code=409,
        )
    if isinstance(exc, a1s_policy.HumanFilingPolicyError):
        return HumanFilingServiceError(
            "human_filing.authority_invalid",
            "La autoridad congelada A1-S no es admisible",
            status_code=409,
        )
    if isinstance(exc, ConnectKernelError):
        return HumanFilingServiceError(
            "human_filing.core_state_conflict",
            "El estado CORE no admite esta operacion A1-S",
            status_code=409,
        )
    if isinstance(exc, LookupError):
        return HumanFilingServiceError(
            "human_filing.core_resource_not_found",
            "Recurso CORE no encontrado",
            status_code=404,
        )
    if isinstance(exc, (ValueError, TypeError)):
        return HumanFilingServiceError(
            "request.validation_failed", "Contrato A1-S no valido",
            status_code=422,
        )
    return HumanFilingServiceError(
        "human_filing.internal_failure",
        "No se pudo completar la operacion A1-S",
        status_code=500,
    )


def _run(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return operation()
    except Exception as exc:
        raise _service_error(exc) from exc


def _command_claim(
    conn: Any,
    *,
    tenant_id: str,
    task_id: str | None,
    action_id: str | None,
    operator_id: str,
    idempotency_key: str,
    scope: str,
    material: Mapping[str, Any],
) -> repository.IdempotencyClaim:
    request_material = {
        "contract": a1s_contracts.HUMAN_FILING_CONTRACT_VERSION,
        "tenant_id": tenant_id,
        "task_id": task_id,
        "action_id": action_id,
        "operator_id": operator_id,
        "scope": scope,
        "material": dict(material),
    }
    return repository.claim_idempotency(
        conn,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        scope=scope,
        request_sha256=_digest(request_material),
        operator_id=operator_id,
    )


def _complete_command(
    conn: Any,
    *,
    tenant_id: str,
    idempotency_key: str,
    task: Mapping[str, Any],
) -> dict[str, Any]:
    repository.complete_idempotency(
        conn,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        response_material=task,
        task_id=str(task["task_id"]),
        action_id=str(task["action_id"]),
    )
    return dict(task)


def _replayed_task(
    conn: Any,
    *,
    tenant_id: str,
    claim: repository.IdempotencyClaim,
) -> dict[str, Any] | None:
    if not claim.replayed:
        return None
    if not claim.task_id:
        raise repository.HumanFilingReplayConflict(
            "completed_command_has_no_task"
        )
    return repository.task_snapshot(
        conn,
        tenant_id=tenant_id,
        task_id=claim.task_id,
        replayed=True,
    )


def _task_membership(
    conn: Any,
    *,
    tenant_id: str,
    task_id: str,
    operator_id: str,
    permission: str,
) -> tuple[Mapping[str, Any], repository.TenantMembership]:
    row = repository.task_row(
        conn, tenant_id=tenant_id, task_id=task_id, for_update=True
    )
    membership = repository.require_tenant_permission(
        conn,
        tenant_id=tenant_id,
        operator_id=operator_id,
        permission=permission,
        for_update=True,
    )
    return row, membership


def _assert_assignee(
    row: Mapping[str, Any], membership: repository.TenantMembership
) -> None:
    if (
        str(row["assignee_operator_id"]) != membership.operator_id
        or str(row["assignee_membership_id"]) != membership.membership_id
        or str(row["assignee_principal_id"]) != membership.principal_id
    ):
        raise repository.HumanFilingPermissionDenied(
            "only_frozen_assignee_may_execute"
        )


def _assert_distinct_from_task_principals(
    row: Mapping[str, Any],
    membership: repository.TenantMembership,
    *names: str,
) -> None:
    for name in names:
        value = row.get(name)
        if value and str(value) == membership.principal_id:
            raise repository.HumanFilingPermissionDenied(
                "human_filing_separation_of_duties"
            )


def _task_authority(
    conn: Any, row: Mapping[str, Any]
) -> tuple[Any, int, Any]:
    action, version, grant, _status = repository.load_action_and_grant(
        conn,
        action_id=str(row["action_id"]),
        authorization_id=str(row["authorization_id"]),
        for_update=True,
    )
    if version != int(row["authorization_version"]):
        raise repository.HumanFilingStateConflict(
            "authorization_version_changed"
        )
    a1s_policy.validate_a1s_action_authority(action, grant)
    validate_execution_authority(
        action, grant, connector_mode=ConnectorMode.ASSISTED
    )
    return action, version, grant


def _grant_allows_operator(grant: Any, operator_id: str) -> None:
    if operator_id not in set(grant.approved_by_operator_ids):
        raise repository.HumanFilingPermissionDenied(
            "operator_not_in_frozen_core_approvals"
        )


def prepare_human_filing(
    conn: Any,
    *,
    tenant_id: str,
    case_binding_id: str,
    representation_evidence_id: str,
    action_id: str,
    authorization_id: str,
    due_at: str,
    operator_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        due_stamp = _future_stamp(due_at)
        requester = repository.require_tenant_permission(
            conn,
            tenant_id=tenant_id,
            operator_id=operator_id,
            permission=a1s_policy.HUMAN_FILING_PREPARE_PERMISSION,
            for_update=True,
        )
        material = {
            "case_binding_id": case_binding_id,
            "representation_evidence_id": representation_evidence_id,
            "action_id": action_id,
            "authorization_id": authorization_id,
            "due_at": due_stamp,
        }
        claim = _command_claim(
            conn,
            tenant_id=tenant_id,
            task_id=None,
            action_id=action_id,
            operator_id=operator_id,
            idempotency_key=idempotency_key,
            scope="human_filing.prepare",
            material=material,
        )
        replay = _replayed_task(conn, tenant_id=tenant_id, claim=claim)
        if replay:
            return replay

        case_scope = repository.load_case_scope(
            conn,
            tenant_id=tenant_id,
            case_binding_id=case_binding_id,
            representation_evidence_id=representation_evidence_id,
            for_update=True,
        )
        action, authorization_version, grant, action_status = (
            repository.load_action_and_grant(
                conn,
                action_id=action_id,
                authorization_id=authorization_id,
                for_update=True,
            )
        )
        a1s_policy.validate_a1s_action_authority(action, grant)
        validate_execution_authority(
            action, grant, connector_mode=ConnectorMode.ASSISTED
        )
        if action_status != ActionStatus.AUTHORIZED.value:
            raise repository.HumanFilingStateConflict(
                "action_must_be_authorized_before_prepare"
            )
        if action.case_id != str(case_scope["case_id"]):
            raise repository.HumanFilingScopeError(
                "action_case_differs_from_tenant_binding"
            )
        if action.requested_by_operator_id != operator_id:
            raise repository.HumanFilingPermissionDenied(
                "prepare_actor_must_match_action_requester"
            )
        repository.assert_frozen_case_document_hashes(
            conn,
            tenant_id=tenant_id,
            case_binding_id=case_binding_id,
            document_hashes=action.document_hashes,
        )
        connector = register_synthetic_connector(
            conn,
            code=a1s_contracts.HUMAN_FILING_CODE,
            version=a1s_contracts.HUMAN_FILING_CONNECTOR_VERSION,
            mode=ConnectorMode.ASSISTED,
            capabilities=(a1s_contracts.HUMAN_FILING_CAPABILITY,),
            risk_ceiling=action.risk_class,
            supports_reconciliation=True,
            configuration={
                "contract_version": (
                    a1s_contracts.HUMAN_FILING_CONTRACT_VERSION
                ),
                "synthetic_marker": a1s_contracts.HUMAN_FILING_MARKER,
                "synthetic_only": True,
                "network_used": False,
                "b2_used": False,
                "provider_contacted": False,
                "external_effects": False,
                "legal_submission_executed": False,
            },
        )
        queue_action(conn, action_id=action.action_id, operator_id=operator_id)
        attempt = start_attempt(
            conn,
            action_id=action.action_id,
            connector_id=connector.connector_id,
            request_metadata={
                "a1s": True,
                "tenant_id": tenant_id,
                "network_used": False,
                "b2_used": False,
                "external_effects": False,
            },
        )
        task_id = str(uuid.uuid4())
        created_at = _stamp()
        package = a1s_contracts.HumanFilingPackage(
            task_id=task_id,
            tenant_id=tenant_id,
            case_binding_id=case_binding_id,
            representation_evidence_id=representation_evidence_id,
            action_id=action.action_id,
            attempt_id=attempt.attempt_id,
            authorization_id=grant.authorization_id,
            authorization_version=authorization_version,
            case_snapshot_sha256=str(case_scope["case_snapshot_sha256"]),
            representation_evidence_sha256=str(
                case_scope["representation_evidence_sha256"]
            ),
            request_sha256=grant.payload_sha256,
            document_hashes=tuple(action.document_hashes),
            destination_ref=action.target_ref,
            due_at=due_stamp,
            checklist=_PACKAGE_CHECKLIST,
            created_by_operator_id=operator_id,
            created_at=created_at,
        )
        package_sha256 = a1s_contracts.human_filing_package_sha256(package)
        package_manifest = a1s_contracts.human_filing_package_material(package)
        task_code = f"rtm-a1s-human-{package_sha256[:24]}"
        task = repository.create_task(
            conn,
            tenant_id=tenant_id,
            case_binding_id=case_binding_id,
            representation_evidence_id=representation_evidence_id,
            action_id=action.action_id,
            attempt_id=attempt.attempt_id,
            connector_id=connector.connector_id,
            authorization_id=grant.authorization_id,
            authorization_version=authorization_version,
            task_id=task_id,
            task_code=task_code,
            requester_membership=requester,
            assignee_membership=None,
            due_at=package.due_at,
            package_manifest=package_manifest,
            package_sha256=package_sha256,
        )
        repository.create_artifact(
            conn,
            tenant_id=tenant_id,
            task_id=task_id,
            artifact_id=str(uuid.uuid4()),
            artifact_code=f"rtm-a1s-artifact-{package_sha256[:24]}",
            kind=a1s_contracts.ArtifactKind.FILING_PACKAGE.value,
            media_type="application/json",
            sha256=package_sha256,
            canonical_payload=package_manifest,
            submitted_by_operator_id=operator_id,
        )
        return _complete_command(
            conn,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            task=task,
        )

    return _run(operation)


def list_human_filings(
    conn: Any,
    *,
    tenant_id: str,
    operator_id: str,
    status: str | None,
    assignee_operator_id: str | None,
    overdue_only: bool,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        repository.require_tenant_permission(
            conn,
            tenant_id=tenant_id,
            operator_id=operator_id,
            permission=a1s_policy.HUMAN_FILING_READ_PERMISSION,
        )
        if status is not None:
            a1s_contracts.HumanFilingTaskStatus(status)
        items, total = repository.list_tasks(
            conn,
            tenant_id=tenant_id,
            status=status,
            assignee_operator_id=assignee_operator_id,
            overdue_only=overdue_only,
            limit=limit,
            offset=offset,
        )
        return {
            "items": items,
            "pagination": {
                "limit": limit, "offset": offset, "total": total,
            },
        }

    return _run(operation)


def get_human_filing_context(
    conn: Any,
    *,
    tenant_id: str,
    operator_id: str,
) -> dict[str, Any]:
    """Devuelve identidades operativas tenant-scoped sin correo ni secretos."""

    def operation() -> dict[str, Any]:
        membership = repository.require_tenant_permission(
            conn,
            tenant_id=tenant_id,
            operator_id=operator_id,
            permission=a1s_policy.HUMAN_FILING_READ_PERMISSION,
        )
        participants, truncated = repository.list_active_tenant_participants(
            conn,
            tenant_id=tenant_id,
            limit=repository.HUMAN_FILING_PARTICIPANT_LIMIT,
        )
        return {
            "tenant_id": tenant_id,
            "current_membership": {
                "membership_id": membership.membership_id,
                "principal_id": membership.principal_id,
                "operator_id": membership.operator_id,
                "role": membership.role,
                "permissions": list(membership.permissions),
                "version": membership.version,
            },
            "participants": participants,
            "participants_limit": repository.HUMAN_FILING_PARTICIPANT_LIMIT,
            "participants_truncated": truncated,
            "read_only": True,
        }

    return _run(operation)


def list_human_filing_tenants(
    conn: Any,
    *,
    operator_id: str,
) -> dict[str, Any]:
    """Bootstrap seguro: nunca enumera tenants ajenos a la sesión."""

    def operation() -> dict[str, Any]:
        tenants, truncated = repository.list_active_operator_tenants(
            conn,
            operator_id=operator_id,
            limit=repository.HUMAN_FILING_TENANT_OPTION_LIMIT,
        )
        return {
            "items": tenants,
            "items_limit": repository.HUMAN_FILING_TENANT_OPTION_LIMIT,
            "items_truncated": truncated,
            "read_only": True,
        }

    return _run(operation)


def list_human_filing_preparation_options(
    conn: Any,
    *,
    tenant_id: str,
    operator_id: str,
) -> dict[str, Any]:
    """Lista opciones congeladas y revalidadas sin reclamar ninguna accion."""

    def operation() -> dict[str, Any]:
        repository.require_tenant_permission(
            conn,
            tenant_id=tenant_id,
            operator_id=operator_id,
            permission=a1s_policy.HUMAN_FILING_PREPARE_PERMISSION,
        )
        candidates = repository.list_preparation_candidates(
            conn,
            tenant_id=tenant_id,
            requested_by_operator_id=operator_id,
            scan_limit=repository.HUMAN_FILING_PREPARATION_SCAN_LIMIT,
        )
        valid: list[dict[str, Any]] = []
        for candidate in candidates:
            try:
                a1s_policy.validate_a1s_action_authority(
                    candidate.action,
                    candidate.grant,
                )
                repository.assert_frozen_case_document_hashes(
                    conn,
                    tenant_id=tenant_id,
                    case_binding_id=str(
                        candidate.projection["case_binding"]["id"]
                    ),
                    document_hashes=candidate.action.document_hashes,
                )
            except (
                a1s_policy.HumanFilingPolicyError,
                repository.HumanFilingScopeError,
                TypeError,
                ValueError,
            ):
                continue
            valid.append(dict(candidate.projection))
            if len(valid) > repository.HUMAN_FILING_PREPARATION_OPTION_LIMIT:
                break
        truncated = (
            len(valid) > repository.HUMAN_FILING_PREPARATION_OPTION_LIMIT
            or (
                len(candidates)
                == repository.HUMAN_FILING_PREPARATION_SCAN_LIMIT
                and len(valid)
                >= repository.HUMAN_FILING_PREPARATION_OPTION_LIMIT
            )
        )
        return {
            "tenant_id": tenant_id,
            "options": valid[
                :repository.HUMAN_FILING_PREPARATION_OPTION_LIMIT
            ],
            "options_limit": (
                repository.HUMAN_FILING_PREPARATION_OPTION_LIMIT
            ),
            "options_truncated": truncated,
            "read_only": True,
        }

    return _run(operation)


def _allowed_actions_hint(
    task: Mapping[str, Any],
    membership: repository.TenantMembership,
    *,
    authority_valid: bool,
    core_approver: bool,
) -> list[str]:
    """Calcula ayudas de UI; los comandos conservan toda su revalidacion."""

    status = str(task["status"])
    permissions = set(membership.permissions)
    principal_id = membership.principal_id
    operator_id = membership.operator_id
    is_assignee = (
        task.get("assignee_membership_id") == membership.membership_id
        and task.get("assignee_principal_id") == principal_id
        and task.get("assignee_operator_id") == operator_id
    )
    approvals = {
        str(item["approval_type"]): item
        for item in task.get("approvals", ())
    }
    verifier = approvals.get("verification_preapproval")
    releaser = approvals.get("release")
    is_preapproved_verifier = bool(
        verifier
        and verifier.get("principal_id") == principal_id
        and verifier.get("operator_id") == operator_id
    )
    separated_from_requester_executor = principal_id not in {
        task.get("requester_principal_id"),
        task.get("assignee_principal_id"),
    }
    both_approvals = bool(
        verifier
        and releaser
        and verifier.get("principal_id") != releaser.get("principal_id")
    )

    allowed: list[str] = []
    if (
        status == "prepared"
        and authority_valid
        and a1s_policy.HUMAN_FILING_ASSIGN_PERMISSION in permissions
    ):
        allowed.append("assign_human_filing")
    if (
        status == "assigned"
        and is_assignee
        and a1s_policy.HUMAN_FILING_EXECUTE_PERMISSION in permissions
    ):
        allowed.append("begin_review")
    if (
        status == "reviewing"
        and is_assignee
        and a1s_policy.HUMAN_FILING_EXECUTE_PERMISSION in permissions
    ):
        allowed.append("attest_review")
    if (
        status == "ready_for_release"
        and a1s_policy.HUMAN_FILING_VERIFY_PERMISSION in permissions
        and separated_from_requester_executor
        and authority_valid
        and core_approver
        and verifier is None
    ):
        allowed.append("preapprove_verifier")
    if (
        status == "ready_for_release"
        and a1s_policy.HUMAN_FILING_RELEASE_PERMISSION in permissions
        and separated_from_requester_executor
        and authority_valid
        and core_approver
        and verifier is not None
        and verifier.get("principal_id") != principal_id
        and releaser is None
    ):
        allowed.append("release_human_filing")
    if (
        status == "released"
        and is_assignee
        and both_approvals
        and authority_valid
        and a1s_policy.HUMAN_FILING_EXECUTE_PERMISSION in permissions
    ):
        allowed.append("begin_execution")
    if (
        status == "in_progress"
        and is_assignee
        and a1s_policy.HUMAN_FILING_EXECUTE_PERMISSION in permissions
    ):
        allowed.append("record_outcome")
    if (
        status == "awaiting_receipt"
        and is_assignee
        and authority_valid
        and a1s_policy.HUMAN_FILING_EXECUTE_PERMISSION in permissions
    ) or (
        status == "reconciling"
        and is_preapproved_verifier
        and authority_valid
        and a1s_policy.HUMAN_FILING_RECONCILE_PERMISSION in permissions
    ):
        allowed.append("submit_receipt_fixture")
    if (
        status == "receipt_submitted"
        and is_preapproved_verifier
        and authority_valid
        and a1s_policy.HUMAN_FILING_VERIFY_PERMISSION in permissions
        and principal_id != task.get("release_principal_id")
    ):
        allowed.append("verify_receipt_and_complete")
    if (
        status == "outcome_unknown"
        and is_preapproved_verifier
        and a1s_policy.HUMAN_FILING_RECONCILE_PERMISSION in permissions
    ):
        allowed.append("begin_human_reconciliation")
    if (
        status == "reconciling"
        and is_preapproved_verifier
        and a1s_policy.HUMAN_FILING_RECONCILE_PERMISSION in permissions
    ):
        allowed.append("resolve_human_reconciliation")
    if (
        status in _MANUAL_REVIEW_SOURCE_STATUSES
        and a1s_policy.HUMAN_FILING_SUPERVISE_PERMISSION in permissions
    ):
        allowed.append("escalate_to_manual_review")
    return allowed


def get_human_filing(
    conn: Any, *, tenant_id: str, task_id: str, operator_id: str,
) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        membership = repository.require_tenant_permission(
            conn,
            tenant_id=tenant_id,
            operator_id=operator_id,
            permission=a1s_policy.HUMAN_FILING_READ_PERMISSION,
        )
        detail = repository.task_read_detail(
            conn,
            tenant_id=tenant_id,
            task_id=task_id,
            summary_limit=repository.HUMAN_FILING_DETAIL_SUMMARY_LIMIT,
        )
        authority_valid = False
        core_approver = False
        try:
            action, version, grant, _status = (
                repository.load_action_and_grant(
                    conn,
                    action_id=str(detail["action_id"]),
                    authorization_id=str(detail["authorization_id"]),
                    for_update=False,
                )
            )
            authority_valid = version == int(detail["authorization_version"])
            if authority_valid:
                a1s_policy.validate_a1s_action_authority(action, grant)
                core_approver = operator_id in set(
                    grant.approved_by_operator_ids
                )
        except (
            a1s_policy.HumanFilingPolicyError,
            repository.HumanFilingRepositoryError,
            AuthorityValidationError,
            TypeError,
            ValueError,
        ):
            authority_valid = False
            core_approver = False
        detail["allowed_actions"] = _allowed_actions_hint(
            detail,
            membership,
            authority_valid=authority_valid,
            core_approver=core_approver,
        )
        detail["allowed_actions_authoritative"] = False
        detail["commands_revalidate"] = True
        return detail

    return _run(operation)


def list_human_filing_receipt_options(
    conn: Any,
    *,
    tenant_id: str,
    task_id: str,
    operator_id: str,
) -> dict[str, Any]:
    """Expone solo UUID/hash de fixtures E4 elegibles del mismo case."""

    def operation() -> dict[str, Any]:
        repository.require_tenant_permission(
            conn,
            tenant_id=tenant_id,
            operator_id=operator_id,
            permission=a1s_policy.HUMAN_FILING_READ_PERMISSION,
        )
        row = repository.task_row(
            conn, tenant_id=tenant_id, task_id=task_id, for_update=False
        )
        if str(row["status"]) not in {"awaiting_receipt", "reconciling"}:
            raise repository.HumanFilingStateConflict(
                "receipt_options_not_admitted_in_current_state"
            )
        options, truncated = repository.list_receipt_fixture_options(
            conn,
            tenant_id=tenant_id,
            task_id=task_id,
            limit=repository.HUMAN_FILING_RECEIPT_OPTION_LIMIT,
        )
        return {
            "tenant_id": tenant_id,
            "task_id": task_id,
            "options": options,
            "options_limit": repository.HUMAN_FILING_RECEIPT_OPTION_LIMIT,
            "options_truncated": truncated,
            "read_only": True,
        }

    return _run(operation)


def assign_human_filing(
    conn: Any,
    *,
    tenant_id: str,
    task_id: str,
    assignee_operator_id: str,
    operator_id: str,
    idempotency_key: str,
    expected_version: int,
) -> dict[str, Any]:
    """Fija al ejecutor mediante permiso assign y CAS explícitos."""

    def operation() -> dict[str, Any]:
        row, assigner = _task_membership(
            conn,
            tenant_id=tenant_id,
            task_id=task_id,
            operator_id=operator_id,
            permission=a1s_policy.HUMAN_FILING_ASSIGN_PERMISSION,
        )
        assignee = repository.require_tenant_permission(
            conn,
            tenant_id=tenant_id,
            operator_id=assignee_operator_id,
            permission=a1s_policy.HUMAN_FILING_EXECUTE_PERMISSION,
            for_update=True,
        )
        claim = _command_claim(
            conn,
            tenant_id=tenant_id,
            task_id=task_id,
            action_id=str(row["action_id"]),
            operator_id=operator_id,
            idempotency_key=idempotency_key,
            scope="human_filing.assign",
            material={
                "expected_version": expected_version,
                "assignee_operator_id": assignee_operator_id,
                "assignee_membership_id": assignee.membership_id,
                "assignee_principal_id": assignee.principal_id,
            },
        )
        replay = _replayed_task(conn, tenant_id=tenant_id, claim=claim)
        if replay:
            return replay
        if str(row["status"]) != "prepared":
            raise repository.HumanFilingStateConflict(
                "assignment_requires_prepared"
            )
        if int(row["version"]) != expected_version:
            raise repository.HumanFilingOptimisticLockError(
                "human_filing_version_conflict"
            )
        if (
            str(row["requester_principal_id"]) == assignee.principal_id
            and str(row["requester_operator_id"]) != assignee.operator_id
        ):
            raise repository.HumanFilingPermissionDenied(
                "duplicate_accounts_for_same_principal_not_allowed"
            )
        _action, _authority_version, grant = _task_authority(conn, row)
        if assignee.operator_id in set(grant.approved_by_operator_ids):
            raise repository.HumanFilingPermissionDenied(
                "executor_cannot_be_core_approver"
            )
        task = repository.advance_task(
            conn,
            tenant_id=tenant_id,
            task_id=task_id,
            expected_version=expected_version,
            target_status="assigned",
            operator_id=operator_id,
            reason_code="a1s_executor_assigned",
            updates={
                "assignee_operator_id": assignee.operator_id,
                "assignee_membership_id": assignee.membership_id,
                "assignee_principal_id": assignee.principal_id,
                "assigned_by_operator_id": assigner.operator_id,
                "assigned_at": _stamp(),
            },
            event_payload={
                "assignee_membership_id": assignee.membership_id,
                "assignee_principal_id": assignee.principal_id,
                "assignee_operator_id": assignee.operator_id,
            },
        )
        return _complete_command(
            conn,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            task=task,
        )

    return _run(operation)


def _simple_transition(
    conn: Any,
    *,
    tenant_id: str,
    task_id: str,
    operator_id: str,
    idempotency_key: str,
    expected_version: int,
    permission: str,
    scope: str,
    target_status: str,
    reason_code: str,
    material: Mapping[str, Any],
    guard: Callable[[Mapping[str, Any], repository.TenantMembership], None],
    updates: Callable[
        [Mapping[str, Any], repository.TenantMembership], Mapping[str, Any]
    ] | None = None,
    event_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    row, membership = _task_membership(
        conn,
        tenant_id=tenant_id,
        task_id=task_id,
        operator_id=operator_id,
        permission=permission,
    )
    claim = _command_claim(
        conn,
        tenant_id=tenant_id,
        task_id=task_id,
        action_id=str(row["action_id"]),
        operator_id=operator_id,
        idempotency_key=idempotency_key,
        scope=scope,
        material={"expected_version": expected_version, **dict(material)},
    )
    replay = _replayed_task(conn, tenant_id=tenant_id, claim=claim)
    if replay:
        return replay
    guard(row, membership)
    task = repository.advance_task(
        conn,
        tenant_id=tenant_id,
        task_id=task_id,
        expected_version=expected_version,
        target_status=target_status,
        operator_id=operator_id,
        reason_code=reason_code,
        updates=(updates(row, membership) if updates else {}),
        event_payload=event_payload,
    )
    return _complete_command(
        conn,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        task=task,
    )


def begin_review(
    conn: Any, *, tenant_id: str, task_id: str, operator_id: str,
    idempotency_key: str, expected_version: int,
) -> dict[str, Any]:
    return _run(lambda: _simple_transition(
        conn,
        tenant_id=tenant_id, task_id=task_id, operator_id=operator_id,
        idempotency_key=idempotency_key, expected_version=expected_version,
        permission=a1s_policy.HUMAN_FILING_EXECUTE_PERMISSION,
        scope="human_filing.review.begin",
        target_status="reviewing", reason_code="a1s_human_review_started",
        material={}, guard=_assert_assignee,
    ))


def attest_review(
    conn: Any, *, tenant_id: str, task_id: str, operator_id: str,
    idempotency_key: str, expected_version: int, package_sha256: str,
    attestation: str,
) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        row, membership = _task_membership(
            conn, tenant_id=tenant_id, task_id=task_id,
            operator_id=operator_id,
            permission=a1s_policy.HUMAN_FILING_EXECUTE_PERMISSION,
        )
        claim = _command_claim(
            conn, tenant_id=tenant_id, task_id=task_id,
            action_id=str(row["action_id"]), operator_id=operator_id,
            idempotency_key=idempotency_key,
            scope="human_filing.review.attest",
            material={
                "expected_version": expected_version,
                "package_sha256": package_sha256,
                "attestation": attestation,
            },
        )
        replay = _replayed_task(conn, tenant_id=tenant_id, claim=claim)
        if replay:
            return replay
        _assert_assignee(row, membership)
        if package_sha256 != str(row["package_sha256"]):
            raise repository.HumanFilingReplayConflict(
                "review_package_sha256_mismatch"
            )
        if attestation != HUMAN_REVIEW_GATE:
            raise repository.HumanFilingStateConflict(
                "human_review_gate_mismatch"
            )
        attestation_material = {
            "format": "rtm.a1s.human_review.v1",
            "task_id": task_id,
            "tenant_id": tenant_id,
            "package_sha256": package_sha256,
            "principal_id": membership.principal_id,
            "operator_id": operator_id,
            "attestation": attestation,
            "synthetic_marker": a1s_contracts.HUMAN_FILING_MARKER,
            "synthetic_only": True,
        }
        digest = _digest(attestation_material)
        repository.create_artifact(
            conn, tenant_id=tenant_id, task_id=task_id,
            artifact_id=str(uuid.uuid4()),
            artifact_code=f"rtm-a1s-artifact-{digest[:24]}",
            kind=a1s_contracts.ArtifactKind.HUMAN_REVIEW_ATTESTATION.value,
            media_type="application/json",
            sha256=digest, canonical_payload=attestation_material,
            submitted_by_operator_id=operator_id,
        )
        now = _stamp()
        task = repository.advance_task(
            conn, tenant_id=tenant_id, task_id=task_id,
            expected_version=expected_version,
            target_status="ready_for_release", operator_id=operator_id,
            reason_code="a1s_human_review_attested",
            updates={
                "reviewed_at": now, "ready_at": now,
                "review_attestation_sha256": digest,
            }, event_payload={"attestation_sha256": digest},
        )
        return _complete_command(
            conn, tenant_id=tenant_id, idempotency_key=idempotency_key,
            task=task,
        )

    return _run(operation)


def preapprove_verifier(
    conn: Any, *, tenant_id: str, task_id: str, operator_id: str,
    idempotency_key: str, expected_version: int, package_sha256: str,
    attestation: str,
) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        row, membership = _task_membership(
            conn, tenant_id=tenant_id, task_id=task_id,
            operator_id=operator_id,
            permission=a1s_policy.HUMAN_FILING_VERIFY_PERMISSION,
        )
        claim = _command_claim(
            conn, tenant_id=tenant_id, task_id=task_id,
            action_id=str(row["action_id"]), operator_id=operator_id,
            idempotency_key=idempotency_key,
            scope="human_filing.verification.preapprove",
            material={
                "expected_version": expected_version,
                "package_sha256": package_sha256,
                "attestation": attestation,
            },
        )
        replay = _replayed_task(conn, tenant_id=tenant_id, claim=claim)
        if replay:
            return replay
        if str(row["status"]) != "ready_for_release":
            raise repository.HumanFilingStateConflict(
                "verification_preapproval_requires_ready_for_release"
            )
        if int(row["version"]) != expected_version:
            raise repository.HumanFilingOptimisticLockError(
                "human_filing_version_conflict"
            )
        _assert_distinct_from_task_principals(
            row, membership,
            "requester_principal_id", "assignee_principal_id",
        )
        action, _version, grant = _task_authority(conn, row)
        _grant_allows_operator(grant, operator_id)
        if package_sha256 != str(row["package_sha256"]):
            raise repository.HumanFilingReplayConflict(
                "preapproval_package_sha256_mismatch"
            )
        if attestation != HUMAN_VERIFICATION_PREAPPROVAL_GATE:
            raise repository.HumanFilingStateConflict(
                "verification_preapproval_gate_mismatch"
            )
        material = {
            "format": "rtm.a1s.verification_preapproval.v1",
            "phase": "preapproval",
            "task_id": task_id,
            "action_id": action.action_id,
            "package_sha256": package_sha256,
            "membership_id": membership.membership_id,
            "principal_id": membership.principal_id,
            "operator_id": operator_id,
            "attestation": attestation,
            "synthetic_marker": a1s_contracts.HUMAN_FILING_MARKER,
            "synthetic_only": True,
        }
        digest = _digest(material)
        artifact_id = str(uuid.uuid4())
        repository.create_artifact(
            conn, tenant_id=tenant_id, task_id=task_id,
            artifact_id=artifact_id,
            artifact_code=f"rtm-a1s-artifact-{digest[:24]}",
            kind=(
                a1s_contracts.ArtifactKind
                .VERIFICATION_PREAPPROVAL_ATTESTATION.value
            ),
            media_type="application/json",
            sha256=digest, canonical_payload=material,
            submitted_by_operator_id=operator_id,
        )
        repository.create_approval(
            conn, tenant_id=tenant_id, task_id=task_id,
            approval_type="verification_preapproval",
            membership=membership, attestation_sha256=digest,
            artifact_id=artifact_id,
        )
        task = repository.record_task_checkpoint(
            conn,
            tenant_id=tenant_id,
            task_id=task_id,
            expected_version=expected_version,
            operator_id=operator_id,
            event_type="human_filing.verification_preapproved",
            reason_code="a1s_verifier_preapproval_frozen",
            event_payload={
                "approval_type": "verification_preapproval",
                "attestation_sha256": digest,
                "principal_id": membership.principal_id,
            },
        )
        return _complete_command(
            conn, tenant_id=tenant_id, idempotency_key=idempotency_key,
            task=task,
        )

    return _run(operation)


def release_human_filing(
    conn: Any, *, tenant_id: str, task_id: str, operator_id: str,
    idempotency_key: str, expected_version: int, package_sha256: str,
    attestation: str,
) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        row, membership = _task_membership(
            conn, tenant_id=tenant_id, task_id=task_id,
            operator_id=operator_id,
            permission=a1s_policy.HUMAN_FILING_RELEASE_PERMISSION,
        )
        claim = _command_claim(
            conn, tenant_id=tenant_id, task_id=task_id,
            action_id=str(row["action_id"]), operator_id=operator_id,
            idempotency_key=idempotency_key, scope="human_filing.release",
            material={
                "expected_version": expected_version,
                "package_sha256": package_sha256,
                "attestation": attestation,
            },
        )
        replay = _replayed_task(conn, tenant_id=tenant_id, claim=claim)
        if replay:
            return replay
        if str(row["status"]) != "ready_for_release":
            raise repository.HumanFilingStateConflict(
                "release_requires_ready_for_release"
            )
        _assert_distinct_from_task_principals(
            row, membership,
            "requester_principal_id", "assignee_principal_id",
        )
        action, _version, grant = _task_authority(conn, row)
        _grant_allows_operator(grant, operator_id)
        approvals = repository.approvals_for_task(
            conn, tenant_id=tenant_id, task_id=task_id
        )
        verifier_approval = approvals.get("verification_preapproval")
        if not verifier_approval:
            raise repository.HumanFilingStateConflict(
                "verification_preapproval_required_before_release"
            )
        if str(verifier_approval["principal_id"]) == membership.principal_id:
            raise repository.HumanFilingPermissionDenied(
                "release_and_verifier_preapproval_principals_must_differ"
            )
        if package_sha256 != str(row["package_sha256"]):
            raise repository.HumanFilingReplayConflict(
                "release_package_sha256_mismatch"
            )
        if attestation != HUMAN_RELEASE_GATE:
            raise repository.HumanFilingStateConflict(
                "release_gate_mismatch"
            )
        material = {
            "format": "rtm.a1s.release.v1",
            "task_id": task_id,
            "action_id": action.action_id,
            "authorization_id": grant.authorization_id,
            "package_sha256": package_sha256,
            "review_attestation_sha256": str(
                row["review_attestation_sha256"]
            ),
            "verification_preapproval_sha256": str(
                verifier_approval["attestation_sha256"]
            ),
            "membership_id": membership.membership_id,
            "principal_id": membership.principal_id,
            "operator_id": operator_id,
            "attestation": attestation,
            "synthetic_marker": a1s_contracts.HUMAN_FILING_MARKER,
            "synthetic_only": True,
        }
        digest = _digest(material)
        artifact_id = str(uuid.uuid4())
        repository.create_artifact(
            conn, tenant_id=tenant_id, task_id=task_id,
            artifact_id=artifact_id,
            artifact_code=f"rtm-a1s-artifact-{digest[:24]}",
            kind=a1s_contracts.ArtifactKind.RELEASE_ATTESTATION.value,
            media_type="application/json",
            sha256=digest, canonical_payload=material,
            submitted_by_operator_id=operator_id,
        )
        repository.create_approval(
            conn, tenant_id=tenant_id, task_id=task_id,
            approval_type="release", membership=membership,
            attestation_sha256=digest, artifact_id=artifact_id,
        )
        approvals = repository.approvals_for_task(
            conn, tenant_id=tenant_id, task_id=task_id
        )
        if set(approvals) != {"release", "verification_preapproval"}:
            raise repository.HumanFilingStateConflict(
                "two_frozen_preoperation_approvals_required"
            )
        task = repository.advance_task(
            conn, tenant_id=tenant_id, task_id=task_id,
            expected_version=expected_version, target_status="released",
            operator_id=operator_id, reason_code="a1s_release_approved",
            updates={
                "release_operator_id": operator_id,
                "release_membership_id": membership.membership_id,
                "release_principal_id": membership.principal_id,
                "released_at": _stamp(),
                "release_attestation_sha256": digest,
            }, event_payload={
                "release_approval_id": approvals["release"]["id"],
                "verification_preapproval_id": (
                    approvals["verification_preapproval"]["id"]
                ),
            },
        )
        return _complete_command(
            conn, tenant_id=tenant_id, idempotency_key=idempotency_key,
            task=task,
        )

    return _run(operation)


def begin_execution(
    conn: Any, *, tenant_id: str, task_id: str, operator_id: str,
    idempotency_key: str, expected_version: int,
) -> dict[str, Any]:
    def guard(row: Mapping[str, Any], membership: repository.TenantMembership) -> None:
        _assert_assignee(row, membership)
        _task_authority(conn, row)
        approvals = repository.approvals_for_task(
            conn, tenant_id=tenant_id, task_id=task_id
        )
        if set(approvals) != {"release", "verification_preapproval"}:
            raise repository.HumanFilingStateConflict(
                "two_frozen_preoperation_approvals_required"
            )
        if len({str(value["principal_id"]) for value in approvals.values()}) != 2:
            raise repository.HumanFilingPermissionDenied(
                "preoperation_approval_principals_must_differ"
            )

    return _run(lambda: _simple_transition(
        conn,
        tenant_id=tenant_id, task_id=task_id, operator_id=operator_id,
        idempotency_key=idempotency_key, expected_version=expected_version,
        permission=a1s_policy.HUMAN_FILING_EXECUTE_PERMISSION,
        scope="human_filing.execution.begin",
        target_status="in_progress",
        reason_code="a1s_human_execution_simulation_started",
        material={}, guard=guard,
        updates=lambda _row, _membership: {"started_at": _stamp()},
        event_payload={
            "synthetic_only": True, "legal_submission_executed": False,
        },
    ))


def record_outcome(
    conn: Any, *, tenant_id: str, task_id: str, operator_id: str,
    idempotency_key: str, expected_version: int, outcome: str,
    external_reference: str | None, witnessed_at: str,
) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        row, membership = _task_membership(
            conn, tenant_id=tenant_id, task_id=task_id,
            operator_id=operator_id,
            permission=a1s_policy.HUMAN_FILING_EXECUTE_PERMISSION,
        )
        claim = _command_claim(
            conn, tenant_id=tenant_id, task_id=task_id,
            action_id=str(row["action_id"]), operator_id=operator_id,
            idempotency_key=idempotency_key, scope="human_filing.outcome",
            material={
                "expected_version": expected_version,
                "outcome": outcome,
                "external_reference": external_reference,
                "witnessed_at": _stamp(witnessed_at),
            },
        )
        replay = _replayed_task(conn, tenant_id=tenant_id, claim=claim)
        if replay:
            return replay
        _assert_assignee(row, membership)
        if str(row["status"]) != "in_progress":
            raise repository.HumanFilingStateConflict(
                "outcome_requires_in_progress"
            )
        if int(row["version"]) != expected_version:
            raise repository.HumanFilingOptimisticLockError(
                "human_filing_version_conflict"
            )
        if outcome not in {"submitted", "unknown"}:
            raise ValueError("a1s_outcome_not_admitted")
        if outcome == "unknown" and external_reference is not None:
            raise ValueError(
                "a1s_unknown_outcome_must_omit_external_reference"
            )
        if outcome == "submitted":
            reference = str(external_reference or "").strip()
            if not _SYNTHETIC_REFERENCE_RE.fullmatch(reference):
                raise ValueError("a1s_reference_must_be_synthetic")
            report = {
                "format": "rtm.a1s.synthetic_submission_report.v1",
                "task_id": task_id,
                "action_id": str(row["action_id"]),
                "attempt_id": str(row["attempt_id"]),
                "external_reference": reference,
                "witnessed_at": _stamp(witnessed_at),
                "operator_id": operator_id,
                "principal_id": membership.principal_id,
                "synthetic_marker": a1s_contracts.HUMAN_FILING_MARKER,
                "synthetic_only": True,
                "network_used": False,
                "legal_submission_executed": False,
            }
            digest = _digest(report)
            repository.create_artifact(
                conn, tenant_id=tenant_id, task_id=task_id,
                artifact_id=str(uuid.uuid4()),
                artifact_code=f"rtm-a1s-artifact-{digest[:24]}",
                kind=a1s_contracts.ArtifactKind.SYNTHETIC_SUBMISSION_REPORT.value,
                media_type="application/json",
                sha256=digest, canonical_payload=report,
                submitted_by_operator_id=operator_id,
            )
            record_attempt_outcome(
                conn,
                attempt_id=str(row["attempt_id"]),
                target_status=ActionStatus.EXTERNAL_ACCEPTED,
                external_reference=reference,
                result_metadata={
                    "a1s": True,
                    "synthetic_only": True,
                    "network_used": False,
                    "legal_submission_executed": False,
                },
            )
            target = "awaiting_receipt"
            updates = {
                "awaiting_receipt_at": _stamp(),
                "external_reference": reference,
            }
            reason = "a1s_synthetic_receipt_expected"
        else:
            reference = (
                f"{SYNTHETIC_REFERENCE_PREFIX}"
                f"{_digest({'action_id': str(row['action_id']), 'task_id': task_id})[:24]}"
            )
            record_attempt_outcome(
                conn,
                attempt_id=str(row["attempt_id"]),
                target_status=ActionStatus.UNKNOWN,
                external_reference=reference,
                failure_class="ambiguous_synthetic_human_step",
                error_code="a1s_outcome_unknown",
                result_metadata={
                    "a1s": True,
                    "blind_retry_allowed": False,
                    "network_used": False,
                    "legal_submission_executed": False,
                },
            )
            target = "outcome_unknown"
            updates = {
                "unknown_at": _stamp(),
                "external_reference": reference,
            }
            reason = "a1s_outcome_unknown"
        task = repository.advance_task(
            conn, tenant_id=tenant_id, task_id=task_id,
            expected_version=expected_version, target_status=target,
            operator_id=operator_id, reason_code=reason, updates=updates,
            event_payload={
                "blind_retry_allowed": False,
                "legal_submission_executed": False,
            },
        )
        return _complete_command(
            conn, tenant_id=tenant_id, idempotency_key=idempotency_key,
            task=task,
        )

    return _run(operation)


def submit_receipt_fixture(
    conn: Any, *, tenant_id: str, task_id: str, operator_id: str,
    idempotency_key: str, expected_version: int, document_id: str,
    document_sha256: str, external_reference: str, witnessed_at: str,
) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        row = repository.task_row(
            conn, tenant_id=tenant_id, task_id=task_id, for_update=True
        )
        current = str(row["status"])
        permission = (
            a1s_policy.HUMAN_FILING_EXECUTE_PERMISSION
            if current == "awaiting_receipt"
            else a1s_policy.HUMAN_FILING_RECONCILE_PERMISSION
        )
        membership = repository.require_tenant_permission(
            conn, tenant_id=tenant_id, operator_id=operator_id,
            permission=permission, for_update=True,
        )
        claim = _command_claim(
            conn, tenant_id=tenant_id, task_id=task_id,
            action_id=str(row["action_id"]), operator_id=operator_id,
            idempotency_key=idempotency_key, scope="human_filing.receipt",
            material={
                "expected_version": expected_version,
                "document_id": document_id,
                "document_sha256": document_sha256,
                "external_reference": external_reference,
                "witnessed_at": _stamp(witnessed_at),
            },
        )
        replay = _replayed_task(conn, tenant_id=tenant_id, claim=claim)
        if replay:
            return replay
        if current not in {"awaiting_receipt", "reconciling"}:
            raise repository.HumanFilingStateConflict(
                "receipt_not_admitted_in_current_state"
            )
        if current == "awaiting_receipt":
            _assert_assignee(row, membership)
        else:
            approvals = repository.approvals_for_task(
                conn, tenant_id=tenant_id, task_id=task_id
            )
            preapproval = approvals.get("verification_preapproval")
            if (
                not preapproval
                or str(preapproval["principal_id"]) != membership.principal_id
            ):
                raise repository.HumanFilingPermissionDenied(
                    "only_preapproved_verifier_may_reconcile_receipt"
                )
        if int(row["version"]) != expected_version:
            raise repository.HumanFilingOptimisticLockError(
                "human_filing_version_conflict"
            )
        if external_reference != str(row["external_reference"]):
            raise repository.HumanFilingReplayConflict(
                "receipt_external_reference_mismatch"
            )
        fixture = repository.load_fixture_document(
            conn,
            tenant_id=tenant_id,
            task_id=task_id,
            document_id=document_id,
            expected_sha256=document_sha256,
        )
        _action, _authority_version, grant = _task_authority(conn, row)
        payload = {
            "format": "rtm.a1s.synthetic_receipt.v1",
            "tenant_id": tenant_id,
            "task_id": task_id,
            "case_binding_id": str(row["case_binding_id"]),
            "case_id": fixture["case_id"],
            "action_id": str(row["action_id"]),
            "attempt_id": str(row["attempt_id"]),
            "authorization_id": str(row["authorization_id"]),
            "authorization_version": int(row["authorization_version"]),
            "request_sha256": grant.payload_sha256,
            "package_sha256": str(row["package_sha256"]),
            "document_id": fixture["document_id"],
            "document_sha256": fixture["sha256"],
            "external_reference": external_reference,
            "witnessed_at": _stamp(witnessed_at),
            "synthetic_marker": a1s_contracts.HUMAN_FILING_MARKER,
            "synthetic_only": True,
            "storage_backend": "database_manifest_only",
            "b2_used": False,
            "network_used": False,
            "legal_submission_executed": False,
        }
        artifact_sha = _digest(payload)
        artifact_id = str(uuid.uuid4())
        repository.create_artifact(
            conn, tenant_id=tenant_id, task_id=task_id,
            artifact_id=artifact_id,
            artifact_code=f"rtm-a1s-artifact-{artifact_sha[:24]}",
            kind=a1s_contracts.ArtifactKind.SYNTHETIC_RECEIPT.value,
            media_type="application/json",
            sha256=artifact_sha, canonical_payload=payload,
            submitted_by_operator_id=operator_id,
        )
        evidence_id = record_evidence(
            conn,
            action_id=str(row["action_id"]),
            attempt_id=str(row["attempt_id"]),
            evidence=EvidenceRecord(
                level=EvidenceLevel.E3_RECEIPT_CAPTURED,
                request_sha256=grant.payload_sha256,
                external_reference=external_reference,
                receipt_sha256=fixture["sha256"],
                receipt_storage_ref=f"fixture://documents/{document_id}",
            ),
            metadata={
                "a1s": True,
                "artifact_id": artifact_id,
                "artifact_sha256": artifact_sha,
                "synthetic_only": True,
                "b2_used": False,
            },
        )
        task = repository.advance_task(
            conn, tenant_id=tenant_id, task_id=task_id,
            expected_version=expected_version,
            target_status="receipt_submitted", operator_id=operator_id,
            reason_code="a1s_synthetic_receipt_submitted",
            updates={
                "receipt_submitted_at": _stamp(),
                "awaiting_receipt_at": (
                    row.get("awaiting_receipt_at") or _stamp()
                ),
            },
            event_payload={
                "artifact_id": artifact_id,
                "evidence_id": evidence_id,
                "receipt_sha256": fixture["sha256"],
            }, actor_type="operator",
        )
        return _complete_command(
            conn, tenant_id=tenant_id, idempotency_key=idempotency_key,
            task=task,
        )

    return _run(operation)


def verify_receipt_and_complete(
    conn: Any, *, tenant_id: str, task_id: str, operator_id: str,
    idempotency_key: str, expected_version: int,
    observed_receipt_sha256: str, observed_external_reference: str,
    observed_package_sha256: str, attestation: str,
) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        row, membership = _task_membership(
            conn, tenant_id=tenant_id, task_id=task_id,
            operator_id=operator_id,
            permission=a1s_policy.HUMAN_FILING_VERIFY_PERMISSION,
        )
        claim = _command_claim(
            conn, tenant_id=tenant_id, task_id=task_id,
            action_id=str(row["action_id"]), operator_id=operator_id,
            idempotency_key=idempotency_key, scope="human_filing.verify",
            material={
                "expected_version": expected_version,
                "observed_receipt_sha256": observed_receipt_sha256,
                "observed_external_reference": observed_external_reference,
                "observed_package_sha256": observed_package_sha256,
                "attestation": attestation,
            },
        )
        replay = _replayed_task(conn, tenant_id=tenant_id, claim=claim)
        if replay:
            return replay
        if str(row["status"]) != "receipt_submitted":
            raise repository.HumanFilingStateConflict(
                "verification_requires_receipt_submitted"
            )
        if int(row["version"]) != expected_version:
            raise repository.HumanFilingOptimisticLockError(
                "human_filing_version_conflict"
            )
        approvals = repository.approvals_for_task(
            conn, tenant_id=tenant_id, task_id=task_id
        )
        preapproval = approvals.get("verification_preapproval")
        if (
            not preapproval
            or str(preapproval["principal_id"]) != membership.principal_id
            or str(preapproval["operator_id"]) != operator_id
        ):
            raise repository.HumanFilingPermissionDenied(
                "e4_verifier_must_match_frozen_preapproval"
            )
        _assert_distinct_from_task_principals(
            row, membership,
            "requester_principal_id", "assignee_principal_id",
            "release_principal_id",
        )
        if attestation != HUMAN_RECEIPT_VERIFICATION_GATE:
            raise repository.HumanFilingStateConflict(
                "receipt_verification_gate_mismatch"
            )
        receipt = repository.artifact_by_kind(
            conn,
            tenant_id=tenant_id,
            task_id=task_id,
            kind=a1s_contracts.ArtifactKind.SYNTHETIC_RECEIPT.value,
        )
        if not receipt:
            raise repository.HumanFilingStateConflict(
                "synthetic_receipt_artifact_missing"
            )
        receipt_payload = dict(receipt["canonical_payload"] or {})
        action, _version, grant = _task_authority(conn, row)
        fixture = repository.load_fixture_document(
            conn,
            tenant_id=tenant_id,
            task_id=task_id,
            document_id=str(receipt_payload.get("document_id") or ""),
            expected_sha256=str(
                receipt_payload.get("document_sha256") or ""
            ),
        )
        expected_receipt_fields = {
            "format", "tenant_id", "task_id", "case_binding_id", "case_id",
            "action_id", "attempt_id", "authorization_id",
            "authorization_version", "request_sha256", "package_sha256",
            "document_id", "document_sha256", "external_reference",
            "witnessed_at", "synthetic_marker", "synthetic_only",
            "storage_backend", "b2_used", "network_used",
            "legal_submission_executed",
        }
        if (
            set(receipt_payload) != expected_receipt_fields
            or _digest(receipt_payload) != str(receipt["sha256"])
            or observed_receipt_sha256
            != str(receipt_payload.get("document_sha256"))
            or observed_external_reference != str(row["external_reference"])
            or observed_package_sha256 != str(row["package_sha256"])
            or str(receipt_payload.get("tenant_id")) != tenant_id
            or str(receipt_payload.get("task_id")) != task_id
            or str(receipt_payload.get("case_binding_id"))
            != str(row["case_binding_id"])
            or str(receipt_payload.get("case_id")) != str(row["case_id"])
            or str(receipt_payload.get("action_id")) != str(row["action_id"])
            or str(receipt_payload.get("attempt_id"))
            != str(row["attempt_id"])
            or str(receipt_payload.get("authorization_id"))
            != str(row["authorization_id"])
            or receipt_payload.get("authorization_version")
            != int(row["authorization_version"])
            or str(receipt_payload.get("request_sha256"))
            != grant.payload_sha256
            or str(receipt_payload.get("package_sha256"))
            != str(row["package_sha256"])
            or str(receipt_payload.get("document_id"))
            != fixture["document_id"]
            or str(receipt_payload.get("document_sha256"))
            != fixture["sha256"]
            or str(receipt_payload.get("external_reference"))
            != str(row["external_reference"])
            or receipt_payload.get("format")
            != "rtm.a1s.synthetic_receipt.v1"
            or receipt_payload.get("synthetic_marker")
            != a1s_contracts.HUMAN_FILING_MARKER
            or receipt_payload.get("synthetic_only") is not True
            or receipt_payload.get("storage_backend")
            != "database_manifest_only"
            or receipt_payload.get("b2_used") is not False
            or receipt_payload.get("network_used") is not False
            or receipt_payload.get("legal_submission_executed") is not False
        ):
            raise repository.HumanFilingReplayConflict(
                "receipt_verification_observation_mismatch"
            )
        material = {
            "format": "rtm.a1s.synthetic_receipt_verification.v1",
            "phase": "e4_verification",
            "task_id": task_id,
            "action_id": action.action_id,
            "authorization_id": grant.authorization_id,
            "receipt_artifact_id": str(receipt["id"]),
            "receipt_sha256": observed_receipt_sha256,
            "external_reference": observed_external_reference,
            "package_sha256": observed_package_sha256,
            "preapproval_sha256": str(preapproval["attestation_sha256"]),
            "membership_id": membership.membership_id,
            "principal_id": membership.principal_id,
            "operator_id": operator_id,
            "attestation": attestation,
            "synthetic_marker": a1s_contracts.HUMAN_FILING_MARKER,
            "synthetic_only": True,
        }
        verification_sha = _digest(material)
        verification_artifact_id = str(uuid.uuid4())
        repository.create_artifact(
            conn, tenant_id=tenant_id, task_id=task_id,
            artifact_id=verification_artifact_id,
            artifact_code=f"rtm-a1s-artifact-{verification_sha[:24]}",
            kind=a1s_contracts.ArtifactKind.VERIFICATION_ATTESTATION.value,
            media_type="application/json",
            sha256=verification_sha, canonical_payload=material,
            submitted_by_operator_id=operator_id,
        )
        evidence_id = record_evidence(
            conn,
            action_id=action.action_id,
            attempt_id=str(row["attempt_id"]),
            evidence=EvidenceRecord(
                level=EvidenceLevel.E4_RECEIPT_VERIFIED,
                request_sha256=grant.payload_sha256,
                external_reference=observed_external_reference,
                receipt_sha256=observed_receipt_sha256,
                receipt_storage_ref=(
                    f"fixture://documents/{receipt_payload['document_id']}"
                ),
                verified_at=_stamp(),
                verification_method="a1s_fixture_hash_gate_v1",
            ),
            verified_by_operator_id=operator_id,
            metadata={
                "a1s": True,
                "artifact_id": verification_artifact_id,
                "verification_sha256": verification_sha,
                "synthetic_only": True,
            },
        )
        if row.get("reconciling_at") is not None:
            record_reconciliation_outcome(
                conn,
                action_id=action.action_id,
                attempt_id=str(row["attempt_id"]),
                target_status=ActionStatus.CONFIRMED,
                evidence_id=evidence_id,
                operator_id=operator_id,
                reason_code="a1s_reconciliation_e4_confirmed",
                metadata={
                    "a1s": True,
                    "blind_retry_allowed": False,
                    "network_used": False,
                    "synthetic_only": True,
                },
            )
        else:
            confirm_action(
                conn,
                action_id=action.action_id,
                operator_id=operator_id,
                evidence_id=evidence_id,
            )
        verified = repository.advance_task(
            conn, tenant_id=tenant_id, task_id=task_id,
            expected_version=expected_version, target_status="verified",
            operator_id=operator_id, reason_code="a1s_receipt_e4_verified",
            updates={
                "verified_by_operator_id": operator_id,
                "verified_by_membership_id": membership.membership_id,
                "verified_by_principal_id": membership.principal_id,
                "verified_at": _stamp(),
                "verification_attestation_sha256": verification_sha,
            }, event_payload={"evidence_id": evidence_id},
        )
        completed = repository.advance_task(
            conn, tenant_id=tenant_id, task_id=task_id,
            expected_version=int(verified["version"]),
            target_status="completed", operator_id=operator_id,
            reason_code="a1s_human_filing_completed",
            updates={"completed_at": _stamp()},
            event_payload={"evidence_id": evidence_id}, actor_type="core",
        )
        return _complete_command(
            conn, tenant_id=tenant_id, idempotency_key=idempotency_key,
            task=completed,
        )

    return _run(operation)


def begin_human_reconciliation(
    conn: Any, *, tenant_id: str, task_id: str, operator_id: str,
    idempotency_key: str, expected_version: int,
) -> dict[str, Any]:
    def guard(row: Mapping[str, Any], membership: repository.TenantMembership) -> None:
        approvals = repository.approvals_for_task(
            conn, tenant_id=tenant_id, task_id=task_id
        )
        preapproval = approvals.get("verification_preapproval")
        if not preapproval or str(preapproval["principal_id"]) != membership.principal_id:
            raise repository.HumanFilingPermissionDenied(
                "only_preapproved_verifier_may_reconcile"
            )
        begin_reconciliation(
            conn,
            action_id=str(row["action_id"]),
            attempt_id=str(row["attempt_id"]),
            metadata={
                "a1s": True, "blind_retry_allowed": False,
                "network_used": False,
            },
        )

    return _run(lambda: _simple_transition(
        conn,
        tenant_id=tenant_id, task_id=task_id, operator_id=operator_id,
        idempotency_key=idempotency_key, expected_version=expected_version,
        permission=a1s_policy.HUMAN_FILING_RECONCILE_PERMISSION,
        scope="human_filing.reconciliation.begin",
        target_status="reconciling",
        reason_code="a1s_human_reconciliation_started",
        material={}, guard=guard,
        updates=lambda _row, _membership: {"reconciling_at": _stamp()},
        event_payload={"blind_retry_allowed": False},
    ))


def resolve_human_reconciliation(
    conn: Any, *, tenant_id: str, task_id: str, operator_id: str,
    idempotency_key: str, expected_version: int, resolution: str,
) -> dict[str, Any]:
    targets = {
        "remains_unknown": (
            "outcome_unknown", ActionStatus.UNKNOWN,
        ),
        "manual_review": (
            "manual_review", ActionStatus.MANUAL_REVIEW,
        ),
        "permanent_failed": (
            "permanent_failed", ActionStatus.PERMANENT_FAILED,
        ),
    }

    def operation() -> dict[str, Any]:
        if resolution not in targets:
            raise ValueError("reconciliation_resolution_not_admitted")
        row, membership = _task_membership(
            conn, tenant_id=tenant_id, task_id=task_id,
            operator_id=operator_id,
            permission=a1s_policy.HUMAN_FILING_RECONCILE_PERMISSION,
        )
        claim = _command_claim(
            conn, tenant_id=tenant_id, task_id=task_id,
            action_id=str(row["action_id"]), operator_id=operator_id,
            idempotency_key=idempotency_key,
            scope="human_filing.reconciliation.resolve",
            material={
                "expected_version": expected_version,
                "resolution": resolution,
            },
        )
        replay = _replayed_task(conn, tenant_id=tenant_id, claim=claim)
        if replay:
            return replay
        if str(row["status"]) != "reconciling":
            raise repository.HumanFilingStateConflict(
                "resolution_requires_reconciling"
            )
        approvals = repository.approvals_for_task(
            conn, tenant_id=tenant_id, task_id=task_id
        )
        preapproval = approvals.get("verification_preapproval")
        if not preapproval or str(preapproval["principal_id"]) != membership.principal_id:
            raise repository.HumanFilingPermissionDenied(
                "only_preapproved_verifier_may_resolve_reconciliation"
            )
        task_status, action_status = targets[resolution]
        record_reconciliation_outcome(
            conn,
            action_id=str(row["action_id"]),
            attempt_id=str(row["attempt_id"]),
            target_status=action_status,
            operator_id=operator_id,
            reason_code=f"a1s_reconciliation_{resolution}",
            metadata={
                "a1s": True,
                "blind_retry_allowed": False,
                "network_used": False,
            },
        )
        task = repository.advance_task(
            conn, tenant_id=tenant_id, task_id=task_id,
            expected_version=expected_version,
            target_status=task_status, operator_id=operator_id,
            reason_code=f"a1s_reconciliation_{resolution}",
            event_payload={"blind_retry_allowed": False},
            actor_type="operator",
        )
        return _complete_command(
            conn, tenant_id=tenant_id, idempotency_key=idempotency_key,
            task=task,
        )

    return _run(operation)


def escalate_to_manual_review(
    conn: Any,
    *,
    tenant_id: str,
    task_id: str,
    operator_id: str,
    idempotency_key: str,
    expected_version: int,
    reason_code: str,
) -> dict[str, Any]:
    """Cierra de forma segura una incidencia sintética bajo supervisor."""

    def operation() -> dict[str, Any]:
        row, membership = _task_membership(
            conn,
            tenant_id=tenant_id,
            task_id=task_id,
            operator_id=operator_id,
            permission=a1s_policy.HUMAN_FILING_SUPERVISE_PERMISSION,
        )
        if reason_code not in MANUAL_REVIEW_REASON_CODES:
            raise ValueError("manual_review_reason_not_admitted")
        claim = _command_claim(
            conn,
            tenant_id=tenant_id,
            task_id=task_id,
            action_id=str(row["action_id"]),
            operator_id=operator_id,
            idempotency_key=idempotency_key,
            scope="human_filing.manual_review",
            material={
                "expected_version": expected_version,
                "reason_code": reason_code,
            },
        )
        replay = _replayed_task(conn, tenant_id=tenant_id, claim=claim)
        if replay:
            return replay
        if str(row["status"]) not in _MANUAL_REVIEW_SOURCE_STATUSES:
            raise repository.HumanFilingStateConflict(
                "manual_review_not_admitted_in_current_state"
            )
        if membership.role != "supervisor":
            raise repository.HumanFilingPermissionDenied(
                "manual_review_requires_active_tenant_supervisor"
            )
        _escalate_core_to_manual_review(
            conn,
            row=row,
            operator_id=operator_id,
            reason_code=reason_code,
        )
        task = repository.advance_task(
            conn,
            tenant_id=tenant_id,
            task_id=task_id,
            expected_version=expected_version,
            target_status="manual_review",
            operator_id=operator_id,
            reason_code=f"a1s_{reason_code}",
            event_payload={
                "manual_review_reason_code": reason_code,
                "authority_revalidation_required": True,
                "blind_retry_allowed": False,
                "synthetic_only": True,
                "core_action_manual_review": True,
            },
        )
        return _complete_command(
            conn,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            task=task,
        )

    return _run(operation)


def _escalate_core_to_manual_review(
    conn: Any,
    *,
    row: Mapping[str, Any],
    operator_id: str,
    reason_code: str,
) -> None:
    """Sincroniza action/attempt C1 sin crear intento ni permitir retry."""

    task_status = str(row["status"])
    expected = _MANUAL_REVIEW_EXPECTED_CORE_STATUSES.get(task_status)
    if expected is None:
        raise repository.HumanFilingStateConflict(
            "manual_review_not_admitted_in_current_state"
        )
    core = conn.execute(text(
        """
        SELECT a.status AS action_status, x.status AS attempt_status,
               x.reconciliation_required
        FROM rtm_connect_actions a
        JOIN rtm_connect_attempts x
          ON x.id=CAST(:attempt_id AS UUID) AND x.action_id=a.id
        WHERE a.id=CAST(:action_id AS UUID)
        FOR UPDATE OF a, x
        """
    ), {
        "action_id": str(row["action_id"]),
        "attempt_id": str(row["attempt_id"]),
    }).mappings().first()
    if not core:
        raise repository.HumanFilingNotFound(
            "manual_review_core_scope_not_found"
        )
    action_status = ActionStatus(str(core["action_status"]))
    if action_status not in expected:
        raise repository.HumanFilingStateConflict(
            "manual_review_core_state_mismatch"
        )
    metadata = {
        "a1s": True,
        "manual_review_reason_code": reason_code,
        "supervisor_operator_id": operator_id,
        "blind_retry_allowed": False,
        "network_used": False,
        "legal_submission_executed": False,
        "synthetic_only": True,
    }
    if action_status is ActionStatus.EXECUTING:
        if str(core["attempt_status"]) != "started":
            raise repository.HumanFilingStateConflict(
                "manual_review_started_attempt_required"
            )
        record_attempt_outcome(
            conn,
            attempt_id=str(row["attempt_id"]),
            target_status=ActionStatus.MANUAL_REVIEW,
            failure_class="a1s_manual_review",
            error_code=reason_code,
            result_metadata=metadata,
        )
        return
    if action_status is ActionStatus.UNKNOWN:
        begin_reconciliation(
            conn,
            action_id=str(row["action_id"]),
            attempt_id=str(row["attempt_id"]),
            metadata=metadata,
        )
        action_status = ActionStatus.RECONCILING
    if action_status is ActionStatus.RECONCILING:
        record_reconciliation_outcome(
            conn,
            action_id=str(row["action_id"]),
            attempt_id=str(row["attempt_id"]),
            target_status=ActionStatus.MANUAL_REVIEW,
            operator_id=operator_id,
            reason_code=f"a1s_manual_review_{reason_code}",
            metadata=metadata,
        )
        return
    if action_status not in {
        ActionStatus.EXTERNAL_ACCEPTED,
        ActionStatus.EVIDENCE_PENDING,
    } or str(core["attempt_status"]) != "external_accepted":
        raise repository.HumanFilingStateConflict(
            "manual_review_finished_attempt_state_mismatch"
        )
    conn.execute(text(
        """
        UPDATE rtm_connect_attempts
        SET status='failed', retryable=FALSE,
            reconciliation_required=FALSE,
            failure_class='a1s_manual_review', error_code=:reason_code,
            result_metadata=(
                COALESCE(result_metadata, '{}'::jsonb)
                || CAST(:metadata AS JSONB)
            ),
            finished_at=COALESCE(finished_at, NOW()), updated_at=NOW()
        WHERE id=CAST(:attempt_id AS UUID)
        """
    ), {
        "attempt_id": str(row["attempt_id"]),
        "reason_code": reason_code,
        "metadata": json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    })
    _transition_core_action(
        conn,
        action_id=str(row["action_id"]),
        target=ActionStatus.MANUAL_REVIEW,
        actor_type="operator",
        operator_id=operator_id,
        attempt_id=str(row["attempt_id"]),
        reason_code=f"a1s_manual_review_{reason_code}",
        metadata=metadata,
    )


class HumanFilingService:
    """Fachada explicita para inyeccion y auditoria del servicio A1-S."""

    prepare_human_filing = staticmethod(prepare_human_filing)
    list_human_filings = staticmethod(list_human_filings)
    get_human_filing_context = staticmethod(get_human_filing_context)
    list_human_filing_tenants = staticmethod(list_human_filing_tenants)
    list_human_filing_preparation_options = staticmethod(
        list_human_filing_preparation_options
    )
    get_human_filing = staticmethod(get_human_filing)
    list_human_filing_receipt_options = staticmethod(
        list_human_filing_receipt_options
    )
    assign_human_filing = staticmethod(assign_human_filing)
    begin_review = staticmethod(begin_review)
    attest_review = staticmethod(attest_review)
    preapprove_verifier = staticmethod(preapprove_verifier)
    release_human_filing = staticmethod(release_human_filing)
    begin_execution = staticmethod(begin_execution)
    record_outcome = staticmethod(record_outcome)
    submit_receipt_fixture = staticmethod(submit_receipt_fixture)
    verify_receipt_and_complete = staticmethod(verify_receipt_and_complete)
    begin_human_reconciliation = staticmethod(begin_human_reconciliation)
    resolve_human_reconciliation = staticmethod(resolve_human_reconciliation)
    escalate_to_manual_review = staticmethod(escalate_to_manual_review)


__all__ = [
    "HUMAN_RECEIPT_VERIFICATION_GATE",
    "HUMAN_RELEASE_GATE",
    "HUMAN_REVIEW_GATE",
    "HUMAN_VERIFICATION_PREAPPROVAL_GATE",
    "MANUAL_REVIEW_REASON_CODES",
    "RTM_CONNECT_A1S_SERVICE_VERSION",
    "SYNTHETIC_REFERENCE_PREFIX",
    "HumanFilingServiceError",
    "HumanFilingService",
    "assign_human_filing",
    "attest_review",
    "begin_execution",
    "begin_human_reconciliation",
    "begin_review",
    "get_human_filing",
    "get_human_filing_context",
    "list_human_filings",
    "list_human_filing_tenants",
    "list_human_filing_preparation_options",
    "list_human_filing_receipt_options",
    "preapprove_verifier",
    "prepare_human_filing",
    "record_outcome",
    "release_human_filing",
    "resolve_human_reconciliation",
    "escalate_to_manual_review",
    "submit_receipt_fixture",
    "verify_receipt_and_complete",
]
