"""Flujo normalizado ``manual.handoff`` de RTM CONNECT C3.

Crea una tarea manual, congela el paquete, asigna operador, captura justificante
E3, exige verificador distinto y eleva a E4 antes de confirmar en CORE.
No publica rutas y no ejecuta efectos externos.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import text

from rtm_connect.connectors.manual_handoff import (
    MANUAL_HANDOFF_CAPABILITY,
    MANUAL_HANDOFF_CODE,
    MANUAL_HANDOFF_CONNECTOR_VERSION,
    MANUAL_HANDOFF_MANIFEST_SHA256,
    ManualHandoffConnector,
    ManualReceiptSubmission,
)
from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    RiskClass,
)
from rtm_connect.kernel import (
    authorize_action,
    confirm_action,
    create_action,
    queue_action,
    record_attempt_outcome,
    record_evidence,
    register_synthetic_connector,
    start_attempt,
)
from rtm_connect.state_machine import ActionStatus


RTM_CONNECT_C3_MANUAL_WORKFLOW_VERSION = (
    "rtm_connect_c3_manual_workflow_v1_0"
)

_MANUAL_TRANSITIONS = {
    "prepared": {"assigned"},
    "assigned": {"in_progress"},
    "in_progress": {"awaiting_receipt"},
    "awaiting_receipt": {"receipt_submitted"},
    "receipt_submitted": {"verified"},
    "verified": {"completed"},
}


class ManualHandoffWorkflowError(RuntimeError):
    pass


class ManualHandoffReplayConflict(ManualHandoffWorkflowError):
    pass


class ManualHandoffPermissionError(ManualHandoffWorkflowError):
    pass


class ManualHandoffStateError(ManualHandoffWorkflowError):
    pass


class ManualHandoffSeparationOfDutiesError(
    ManualHandoffPermissionError
):
    pass


@dataclass(frozen=True)
class ManualHandoffOutcome:
    task_id: str
    task_code: str
    action_id: str
    attempt_id: str
    connector_id: str
    task_status: str
    action_status: str
    assignee_operator_id: str
    due_at: str
    package_sha256: str
    external_reference: str | None
    attempts: int
    evidence_rows: int
    transitions: int
    replay_count: int
    task_events: int
    task_version: int
    overdue: bool
    replayed: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load_action_contract(conn, action_id: str) -> ConnectActionRequest:
    row = conn.execute(
        text(
            """
            SELECT * FROM rtm_connect_actions
            WHERE id=CAST(:action_id AS UUID)
            """
        ),
        {"action_id": action_id},
    ).mappings().first()
    if not row:
        raise LookupError("Acción RTM CONNECT no encontrada")
    return ConnectActionRequest(
        action_id=str(row["id"]),
        case_id=str(row["case_id"]) if row["case_id"] else None,
        capability=str(row["capability"]),
        satellite=str(row["satellite"]),
        target_type=str(row["target_type"]),
        target_ref=str(row["target_ref"]),
        payload=dict(row["payload"]),
        document_hashes=tuple(row["document_hashes"] or []),
        requested_by_operator_id=str(row["requested_by_operator_id"]),
        requested_at=row["requested_at"].isoformat(),
        risk_class=RiskClass(str(row["risk_class"])),
        correlation_id=row["correlation_id"],
        requires_dual_control=bool(row["requires_dual_control"]),
        contract_version=str(row["contract_version"]),
    )


def _task_row(conn, task_id: str, *, for_update: bool = False):
    suffix = " FOR UPDATE" if for_update else ""
    row = conn.execute(
        text(
            f"""
            SELECT *
            FROM rtm_connect_manual_tasks
            WHERE id=CAST(:task_id AS UUID)
            {suffix}
            """
        ),
        {"task_id": task_id},
    ).mappings().first()
    if not row:
        raise LookupError("Tarea manual RTM CONNECT no encontrada")
    return row


def _task_by_action(conn, action_id: str):
    return conn.execute(
        text(
            """
            SELECT * FROM rtm_connect_manual_tasks
            WHERE action_id=CAST(:action_id AS UUID)
            """
        ),
        {"action_id": action_id},
    ).mappings().first()


def _append_manual_event(
    conn,
    *,
    task_id: str,
    action_id: str,
    attempt_id: str,
    event_type: str,
    actor_type: str,
    operator_id: str | None,
    from_status: str | None,
    to_status: str | None,
    reason_code: str,
    reason_detail: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> str:
    conn.execute(
        text(
            """
            SELECT id FROM rtm_connect_manual_tasks
            WHERE id=CAST(:task_id AS UUID)
            FOR UPDATE
            """
        ),
        {"task_id": task_id},
    ).one()
    sequence = int(
        conn.execute(
            text(
                """
                SELECT COALESCE(MAX(sequence_number), 0) + 1
                FROM rtm_connect_manual_events
                WHERE task_id=CAST(:task_id AS UUID)
                """
            ),
            {"task_id": task_id},
        ).scalar_one()
    )
    event_id = str(uuid.uuid4())
    conn.execute(
        text(
            """
            INSERT INTO rtm_connect_manual_events(
                id, task_id, action_id, attempt_id, sequence_number,
                event_type, actor_type, operator_id, from_status,
                to_status, reason_code, reason_detail, payload, created_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:task_id AS UUID),
                CAST(:action_id AS UUID), CAST(:attempt_id AS UUID),
                :sequence_number, :event_type, :actor_type,
                CAST(:operator_id AS UUID), :from_status, :to_status,
                :reason_code, :reason_detail, CAST(:payload AS JSONB), NOW()
            )
            """
        ),
        {
            "id": event_id,
            "task_id": task_id,
            "action_id": action_id,
            "attempt_id": attempt_id,
            "sequence_number": sequence,
            "event_type": event_type,
            "actor_type": actor_type,
            "operator_id": operator_id,
            "from_status": from_status,
            "to_status": to_status,
            "reason_code": reason_code,
            "reason_detail": reason_detail,
            "payload": _json(dict(payload or {})),
        },
    )
    return event_id


def _advance_task(
    conn,
    *,
    task_id: str,
    target_status: str,
    operator_id: str,
    reason_code: str,
    reason_detail: str | None = None,
    external_reference: str | None = None,
    assigned_by_operator_id: str | None = None,
    event_payload: Mapping[str, Any] | None = None,
) -> bool:
    row = _task_row(conn, task_id, for_update=True)
    current = str(row["status"])
    if current == target_status:
        return False
    if target_status not in _MANUAL_TRANSITIONS.get(current, set()):
        raise ManualHandoffStateError(
            f"Transición manual no permitida: {current} -> {target_status}"
        )

    updates = [
        "status=:target_status",
        "version=version+1",
        "updated_at=NOW()",
    ]
    params: dict[str, Any] = {
        "task_id": task_id,
        "target_status": target_status,
    }
    if target_status == "assigned":
        if not assigned_by_operator_id:
            raise ValueError("assigned_by_operator_id es obligatorio")
        updates.extend(
            [
                "assignee_operator_id=CAST(:assignee AS UUID)",
                "assigned_by_operator_id=CAST(:assigned_by AS UUID)",
                "assigned_at=NOW()",
            ]
        )
        params["assignee"] = operator_id
        params["assigned_by"] = assigned_by_operator_id
    elif target_status == "in_progress":
        updates.append("started_at=NOW()")
    elif target_status == "receipt_submitted":
        updates.extend(
            [
                "receipt_submitted_at=NOW()",
                "external_reference=:external_reference",
            ]
        )
        params["external_reference"] = external_reference
    elif target_status == "verified":
        updates.extend(
            [
                "verified_at=NOW()",
                "verified_by_operator_id=CAST(:verified_by AS UUID)",
            ]
        )
        params["verified_by"] = operator_id
    elif target_status == "completed":
        updates.append("completed_at=NOW()")

    conn.execute(
        text(
            f"""
            UPDATE rtm_connect_manual_tasks
            SET {", ".join(updates)}
            WHERE id=CAST(:task_id AS UUID)
            """
        ),
        params,
    )
    _append_manual_event(
        conn,
        task_id=task_id,
        action_id=str(row["action_id"]),
        attempt_id=str(row["attempt_id"]),
        event_type=f"manual.{target_status}",
        actor_type="operator",
        operator_id=operator_id,
        from_status=current,
        to_status=target_status,
        reason_code=reason_code,
        reason_detail=reason_detail,
        payload=event_payload,
    )
    return True


def register_manual_handoff_connector(conn):
    return register_synthetic_connector(
        conn,
        code=MANUAL_HANDOFF_CODE,
        version=MANUAL_HANDOFF_CONNECTOR_VERSION,
        mode=ConnectorMode.MANUAL,
        capabilities=(MANUAL_HANDOFF_CAPABILITY,),
        risk_ceiling=RiskClass.R3_LEGAL_OR_FINANCIAL,
        supports_reconciliation=False,
        configuration={
            "runtime_version": RTM_CONNECT_C3_MANUAL_WORKFLOW_VERSION,
            "manifest_sha256": MANUAL_HANDOFF_MANIFEST_SHA256,
            "synthetic_only": True,
            "network_used": False,
            "external_effects": False,
        },
    )


def manual_task_snapshot(
    conn,
    *,
    task_id: str,
    replayed: bool = False,
) -> ManualHandoffOutcome:
    row = conn.execute(
        text(
            """
            SELECT
                t.*,
                a.status AS action_status,
                (SELECT COUNT(*) FROM rtm_connect_attempts x
                 WHERE x.action_id=t.action_id) AS attempts,
                (SELECT COUNT(*) FROM rtm_connect_evidence e
                 WHERE e.action_id=t.action_id) AS evidence_rows,
                (SELECT COUNT(*) FROM rtm_connect_transitions tr
                 WHERE tr.action_id=t.action_id) AS transitions,
                (SELECT replay_count
                 FROM rtm_connect_idempotency_claims i
                 WHERE i.action_id=t.action_id) AS replay_count,
                (SELECT COUNT(*) FROM rtm_connect_manual_events me
                 WHERE me.task_id=t.id) AS task_events
            FROM rtm_connect_manual_tasks t
            JOIN rtm_connect_actions a ON a.id=t.action_id
            WHERE t.id=CAST(:task_id AS UUID)
            """
        ),
        {"task_id": task_id},
    ).mappings().first()
    if not row:
        raise LookupError("Tarea manual RTM CONNECT no encontrada")
    due_at = row["due_at"]
    return ManualHandoffOutcome(
        task_id=str(row["id"]),
        task_code=str(row["task_code"]),
        action_id=str(row["action_id"]),
        attempt_id=str(row["attempt_id"]),
        connector_id=str(row["connector_id"]),
        task_status=str(row["status"]),
        action_status=str(row["action_status"]),
        assignee_operator_id=str(row["assignee_operator_id"]),
        due_at=due_at.isoformat(),
        package_sha256=str(row["package_sha256"]),
        external_reference=(
            str(row["external_reference"])
            if row["external_reference"]
            else None
        ),
        attempts=int(row["attempts"]),
        evidence_rows=int(row["evidence_rows"]),
        transitions=int(row["transitions"]),
        replay_count=int(row["replay_count"] or 0),
        task_events=int(row["task_events"]),
        task_version=int(row["version"]),
        overdue=(
            due_at < _utcnow()
            and str(row["status"]) != "completed"
        ),
        replayed=bool(replayed),
    )


def prepare_manual_handoff(
    conn,
    *,
    action: ConnectActionRequest,
    grant: AuthorizationGrant,
    assignee_operator_id: str,
    assigned_by_operator_id: str,
    due_at: str,
    instructions: str,
) -> ManualHandoffOutcome:
    if action.capability != MANUAL_HANDOFF_CAPABILITY:
        raise ManualHandoffWorkflowError(
            "La acción no pertenece a manual.handoff C3"
        )
    if ConnectorMode.MANUAL not in grant.authorized_connector_modes:
        raise ManualHandoffWorkflowError(
            "La autorización no permite el modo manual"
        )
    connector = register_manual_handoff_connector(conn)
    created = create_action(
        conn,
        action=action,
        authority_scope=grant.authority_code,
    )
    adapter = ManualHandoffConnector()

    if created.replayed:
        existing = _task_by_action(conn, created.action_id)
        if not existing:
            raise ManualHandoffReplayConflict(
                "La acción existe sin tarea manual asociada"
            )
        expected = adapter.build_package(
            action,
            attempt_id=str(existing["attempt_id"]),
            due_at=due_at,
            instructions=instructions,
        )
        if expected.package_sha256 != str(existing["package_sha256"]):
            raise ManualHandoffReplayConflict(
                "El replay intenta cambiar el paquete congelado"
            )
        if str(existing["assignee_operator_id"]) != assignee_operator_id:
            raise ManualHandoffReplayConflict(
                "El replay intenta cambiar el operador asignado"
            )
        return manual_task_snapshot(
            conn,
            task_id=str(existing["id"]),
            replayed=True,
        )

    authorize_action(conn, grant=grant)
    queue_action(
        conn,
        action_id=action.action_id,
        operator_id=assigned_by_operator_id,
    )
    attempt = start_attempt(
        conn,
        action_id=action.action_id,
        connector_id=connector.connector_id,
        request_metadata={
            "connector_runtime": RTM_CONNECT_C3_MANUAL_WORKFLOW_VERSION,
            "mode": "manual",
            "network_used": False,
            "external_effects": False,
        },
    )
    package = adapter.build_package(
        action,
        attempt_id=attempt.attempt_id,
        due_at=due_at,
        instructions=instructions,
    )
    task_id = str(uuid.uuid4())
    task_code = f"rtm-manual-{package.package_sha256[:24]}"
    conn.execute(
        text(
            """
            INSERT INTO rtm_connect_manual_tasks(
                id, action_id, attempt_id, connector_id, task_code,
                status, due_at, package_manifest, package_sha256,
                instructions, version, metadata, created_at, updated_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:action_id AS UUID),
                CAST(:attempt_id AS UUID), CAST(:connector_id AS UUID),
                :task_code, 'prepared', :due_at,
                CAST(:package_manifest AS JSONB), :package_sha256,
                :instructions, 1, CAST(:metadata AS JSONB), NOW(), NOW()
            )
            """
        ),
        {
            "id": task_id,
            "action_id": action.action_id,
            "attempt_id": attempt.attempt_id,
            "connector_id": connector.connector_id,
            "task_code": task_code,
            "due_at": package.due_at,
            "package_manifest": _json(package.manifest),
            "package_sha256": package.package_sha256,
            "instructions": package.instructions,
            "metadata": _json(
                {
                    "workflow_version":
                        RTM_CONNECT_C3_MANUAL_WORKFLOW_VERSION,
                    "synthetic_only": True,
                    "network_used": False,
                }
            ),
        },
    )
    _append_manual_event(
        conn,
        task_id=task_id,
        action_id=action.action_id,
        attempt_id=attempt.attempt_id,
        event_type="manual.prepared",
        actor_type="connect",
        operator_id=assigned_by_operator_id,
        from_status=None,
        to_status="prepared",
        reason_code="manual_package_frozen",
        payload={
            "package_sha256": package.package_sha256,
            "due_at": package.due_at,
        },
    )
    _advance_task(
        conn,
        task_id=task_id,
        target_status="assigned",
        operator_id=assignee_operator_id,
        assigned_by_operator_id=assigned_by_operator_id,
        reason_code="manual_task_assigned",
        event_payload={
            "assigned_by_operator_id": assigned_by_operator_id,
        },
    )
    return manual_task_snapshot(conn, task_id=task_id)


def begin_manual_work(
    conn,
    *,
    task_id: str,
    operator_id: str,
) -> ManualHandoffOutcome:
    row = _task_row(conn, task_id)
    if str(row["assignee_operator_id"]) != operator_id:
        raise ManualHandoffPermissionError(
            "Solo el operador asignado puede iniciar la tarea"
        )
    _advance_task(
        conn,
        task_id=task_id,
        target_status="in_progress",
        operator_id=operator_id,
        reason_code="manual_work_started",
    )
    return manual_task_snapshot(conn, task_id=task_id)


def mark_manual_awaiting_receipt(
    conn,
    *,
    task_id: str,
    operator_id: str,
) -> ManualHandoffOutcome:
    row = _task_row(conn, task_id)
    if str(row["assignee_operator_id"]) != operator_id:
        raise ManualHandoffPermissionError(
            "Solo el operador asignado puede solicitar el justificante"
        )
    _advance_task(
        conn,
        task_id=task_id,
        target_status="awaiting_receipt",
        operator_id=operator_id,
        reason_code="manual_external_step_completed",
    )
    return manual_task_snapshot(conn, task_id=task_id)


def _latest_receipt_evidence(conn, action_id: str):
    return conn.execute(
        text(
            """
            SELECT *
            FROM rtm_connect_evidence
            WHERE action_id=CAST(:action_id AS UUID)
              AND receipt_sha256 IS NOT NULL
            ORDER BY sequence_number DESC
            LIMIT 1
            """
        ),
        {"action_id": action_id},
    ).mappings().first()


def submit_manual_receipt(
    conn,
    *,
    task_id: str,
    operator_id: str,
    submission: ManualReceiptSubmission,
) -> ManualHandoffOutcome:
    row = _task_row(conn, task_id, for_update=True)
    if str(row["assignee_operator_id"]) != operator_id:
        raise ManualHandoffPermissionError(
            "Solo el operador asignado puede aportar el justificante"
        )
    current = str(row["status"])
    if current in {"receipt_submitted", "verified", "completed"}:
        latest = _latest_receipt_evidence(
            conn,
            str(row["action_id"]),
        )
        if (
            latest
            and str(latest["receipt_sha256"]) == submission.receipt_sha256
            and str(latest["external_reference"])
                == submission.external_reference
        ):
            return manual_task_snapshot(
                conn,
                task_id=task_id,
                replayed=True,
            )
        raise ManualHandoffReplayConflict(
            "La tarea ya contiene otro justificante"
        )
    if current != "awaiting_receipt":
        raise ManualHandoffStateError(
            "La tarea no está esperando justificante"
        )

    action = _load_action_contract(conn, str(row["action_id"]))
    evidence = ManualHandoffConnector().capture_receipt(
        action,
        attempt_id=str(row["attempt_id"]),
        submission=submission,
    )
    record_attempt_outcome(
        conn,
        attempt_id=str(row["attempt_id"]),
        target_status=ActionStatus.EXTERNAL_ACCEPTED,
        external_reference=submission.external_reference,
        result_metadata={
            "manual_handoff": True,
            "presented_at": submission.presented_at,
            "mime": submission.mime,
            "size_bytes": submission.size_bytes,
            "network_used": False,
        },
    )
    evidence_id = record_evidence(
        conn,
        action_id=str(row["action_id"]),
        attempt_id=str(row["attempt_id"]),
        evidence=evidence,
        metadata={
            "connector_code": MANUAL_HANDOFF_CODE,
            "connector_version": MANUAL_HANDOFF_CONNECTOR_VERSION,
            "phase": "receipt_captured",
            "presented_at": submission.presented_at,
            "mime": submission.mime,
            "size_bytes": submission.size_bytes,
        },
    )
    _advance_task(
        conn,
        task_id=task_id,
        target_status="receipt_submitted",
        operator_id=operator_id,
        reason_code="manual_receipt_submitted",
        external_reference=submission.external_reference,
        event_payload={
            "evidence_id": evidence_id,
            "receipt_sha256": submission.receipt_sha256,
            "storage_ref": submission.storage_ref,
            "presented_at": submission.presented_at,
        },
    )
    return manual_task_snapshot(conn, task_id=task_id)


def verify_manual_receipt(
    conn,
    *,
    task_id: str,
    verifier_operator_id: str,
    observed_receipt_sha256: str,
    observed_external_reference: str,
    verified_at: str,
) -> ManualHandoffOutcome:
    row = _task_row(conn, task_id, for_update=True)
    if str(row["assignee_operator_id"]) == verifier_operator_id:
        raise ManualHandoffSeparationOfDutiesError(
            "El verificador debe ser distinto del operador asignado"
        )
    current = str(row["status"])
    latest = _latest_receipt_evidence(conn, str(row["action_id"]))
    if not latest:
        raise ManualHandoffStateError(
            "La tarea no contiene justificante capturado"
        )
    if current in {"verified", "completed"}:
        if (
            str(latest["evidence_level"])
                == EvidenceLevel.E4_RECEIPT_VERIFIED.value
            and str(latest["receipt_sha256"])
                == observed_receipt_sha256
            and str(latest["external_reference"])
                == observed_external_reference
        ):
            return manual_task_snapshot(
                conn,
                task_id=task_id,
                replayed=True,
            )
        raise ManualHandoffReplayConflict(
            "La tarea ya fue verificada con otra evidencia"
        )
    if current != "receipt_submitted":
        raise ManualHandoffStateError(
            "La tarea no está preparada para verificación"
        )

    action = _load_action_contract(conn, str(row["action_id"]))
    verification = ManualHandoffConnector().verify_receipt(
        action,
        attempt_id=str(row["attempt_id"]),
        receipt_sha256=str(latest["receipt_sha256"]),
        storage_ref=str(latest["receipt_storage_ref"]),
        external_reference=str(latest["external_reference"]),
        observed_receipt_sha256=observed_receipt_sha256,
        observed_external_reference=observed_external_reference,
        verified_at=verified_at,
    )
    evidence_id = record_evidence(
        conn,
        action_id=str(row["action_id"]),
        attempt_id=str(row["attempt_id"]),
        evidence=verification.evidence,
        verified_by_operator_id=verifier_operator_id,
        metadata={
            "connector_code": MANUAL_HANDOFF_CODE,
            "connector_version": MANUAL_HANDOFF_CONNECTOR_VERSION,
            "phase": "receipt_verified",
            "verification_sha256":
                verification.verification_sha256,
            "network_used": False,
        },
    )
    _advance_task(
        conn,
        task_id=task_id,
        target_status="verified",
        operator_id=verifier_operator_id,
        reason_code="manual_receipt_verified",
        event_payload={
            "evidence_id": evidence_id,
            "verification_sha256":
                verification.verification_sha256,
        },
    )
    return manual_task_snapshot(conn, task_id=task_id)


def complete_manual_handoff(
    conn,
    *,
    task_id: str,
    verifier_operator_id: str,
) -> ManualHandoffOutcome:
    row = _task_row(conn, task_id, for_update=True)
    current = str(row["status"])
    if current == "completed":
        return manual_task_snapshot(
            conn,
            task_id=task_id,
            replayed=True,
        )
    if current != "verified":
        raise ManualHandoffStateError(
            "Solo una tarea verificada puede completarse"
        )
    if str(row["verified_by_operator_id"]) != verifier_operator_id:
        raise ManualHandoffPermissionError(
            "Debe completar la tarea el verificador registrado"
        )
    confirm_action(
        conn,
        action_id=str(row["action_id"]),
        operator_id=verifier_operator_id,
    )
    _advance_task(
        conn,
        task_id=task_id,
        target_status="completed",
        operator_id=verifier_operator_id,
        reason_code="manual_handoff_completed",
    )
    return manual_task_snapshot(conn, task_id=task_id)


__all__ = [
    "RTM_CONNECT_C3_MANUAL_WORKFLOW_VERSION",
    "ManualHandoffOutcome",
    "ManualHandoffPermissionError",
    "ManualHandoffReplayConflict",
    "ManualHandoffSeparationOfDutiesError",
    "ManualHandoffStateError",
    "ManualHandoffWorkflowError",
    "begin_manual_work",
    "complete_manual_handoff",
    "manual_task_snapshot",
    "mark_manual_awaiting_receipt",
    "prepare_manual_handoff",
    "register_manual_handoff_connector",
    "submit_manual_receipt",
    "verify_manual_receipt",
]
