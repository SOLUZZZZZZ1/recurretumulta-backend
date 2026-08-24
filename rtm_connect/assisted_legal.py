"""Workflow juridico asistido, sintetico y fail-closed de RTM CONNECT C7.

C7 prepara un paquete inmutable, exige revision y liberacion humana distintas,
captura E3 y verifica E4 antes de que CORE confirme. La ruta de incertidumbre
reutiliza el intento original: nunca crea un segundo intento ni reenvia.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import text

from rtm_connect.assisted_legal_policy import (
    ASSISTED_LEGAL_CAPABILITY,
    ASSISTED_LEGAL_CODE,
    ASSISTED_LEGAL_CONNECTOR_VERSION,
    ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
    validate_c7_action_authority,
)
from rtm_connect.connectors.assisted_legal import (
    ASSISTED_LEGAL_MANIFEST_SHA256,
    ASSISTED_LEGAL_REFERENCE_PREFIX,
    AssistedLegalConnector,
    AssistedReceiptSubmission,
)
from rtm_connect.authority import validate_execution_authority
from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    RiskClass,
)
from rtm_connect.idempotency import canonical_json, payload_sha256
from rtm_connect.kernel import (
    action_snapshot,
    authorize_action,
    begin_reconciliation,
    confirm_action,
    create_action,
    queue_action,
    record_attempt_outcome,
    record_evidence,
    record_reconciliation_outcome,
    register_synthetic_connector,
    start_attempt,
)
from rtm_connect.state_machine import ActionStatus


RTM_CONNECT_C7_ASSISTED_WORKFLOW_VERSION = (
    "rtm_connect_c7_assisted_legal_workflow_v1_0"
)
ASSISTED_EXECUTE_PERMISSION = "connect.assisted.execute"
ASSISTED_RELEASE_PERMISSION = "connect.assisted.release"
ASSISTED_VERIFY_PERMISSION = "connect.assisted.verify"

_ASSISTED_TRANSITIONS = {
    "prepared": {"assigned"},
    "assigned": {"reviewing"},
    "reviewing": {"ready_for_release"},
    "ready_for_release": {"released"},
    "released": {"in_progress"},
    "in_progress": {"awaiting_receipt", "outcome_unknown"},
    "outcome_unknown": {"reconciling"},
    "reconciling": {
        "receipt_submitted", "outcome_unknown", "manual_review",
        "permanent_failed",
    },
    "awaiting_receipt": {"receipt_submitted"},
    "receipt_submitted": {"verified"},
    "verified": {"completed"},
    "completed": set(),
    "manual_review": set(),
    "permanent_failed": set(),
}


class AssistedLegalWorkflowError(RuntimeError):
    pass


class AssistedLegalReplayConflict(AssistedLegalWorkflowError):
    pass


class AssistedLegalPermissionError(AssistedLegalWorkflowError):
    pass


class AssistedLegalStateError(AssistedLegalWorkflowError):
    pass


class AssistedLegalSeparationOfDutiesError(AssistedLegalPermissionError):
    pass


@dataclass(frozen=True)
class AssistedLegalOutcome:
    task_id: str
    task_code: str
    action_id: str
    attempt_id: str
    connector_id: str
    authorization_id: str
    task_status: str
    action_status: str
    assignee_operator_id: str
    release_operator_id: str | None
    verified_by_operator_id: str | None
    due_at: str
    package_sha256: str
    external_reference: str | None
    receipt_evidence_id: str | None
    verified_evidence_id: str | None
    attempts: int
    evidence_rows: int
    transitions: int
    replay_count: int
    task_events: int
    task_version: int
    replayed: bool


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _timestamp(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _load_action_contract(conn, action_id: str) -> ConnectActionRequest:
    row = conn.execute(text(
        "SELECT * FROM rtm_connect_actions "
        "WHERE id=CAST(:id AS UUID)"
    ), {"id": action_id}).mappings().first()
    if not row:
        raise LookupError("Accion RTM CONNECT C7 no encontrada")
    return ConnectActionRequest(
        action_id=str(row["id"]),
        case_id=str(row["case_id"]) if row["case_id"] else None,
        capability=str(row["capability"]),
        satellite=str(row["satellite"]),
        target_type=str(row["target_type"]),
        target_ref=str(row["target_ref"]),
        payload=dict(row["payload"]),
        document_hashes=tuple(row["document_hashes"] or ()),
        requested_by_operator_id=str(row["requested_by_operator_id"]),
        requested_at=_timestamp(row["requested_at"]),
        risk_class=RiskClass(str(row["risk_class"])),
        correlation_id=row["correlation_id"],
        requires_dual_control=bool(row["requires_dual_control"]),
        contract_version=str(row["contract_version"]),
    )


def _load_latest_grant(conn, action_id: str) -> tuple[int, AuthorizationGrant]:
    row = conn.execute(text(
        """
        SELECT * FROM rtm_connect_authorizations
        WHERE action_id=CAST(:action_id AS UUID)
        ORDER BY authorization_version DESC LIMIT 1
        """
    ), {"action_id": action_id}).mappings().first()
    if not row:
        raise LookupError("Autorizacion C7 no encontrada")
    grant = AuthorizationGrant(
        authorization_id=str(row["id"]),
        action_id=str(row["action_id"]),
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
            str(value) for value in (row["approved_by_operator_ids"] or ())
        ),
        authorized_at=_timestamp(row["authorized_at"]),
        expires_at=(
            _timestamp(row["expires_at"]) if row["expires_at"] else None
        ),
        revoked_at=(
            _timestamp(row["revoked_at"]) if row["revoked_at"] else None
        ),
        legal_effect_authorized=bool(row["legal_effect_authorized"]),
        frozen=bool(row["frozen"]),
    )
    return int(row["authorization_version"]), grant


def _assert_same_grant(
    persisted_version: int,
    persisted: AuthorizationGrant,
    supplied: AuthorizationGrant,
    *,
    task_row: Mapping[str, Any] | None = None,
) -> None:
    if persisted != supplied:
        raise AssistedLegalReplayConflict(
            "El grant C7 no coincide con el ultimo grant persistido"
        )
    if task_row is not None and (
        str(task_row["authorization_id"]) != supplied.authorization_id
        or int(task_row["authorization_version"]) != persisted_version
    ):
        raise AssistedLegalReplayConflict(
            "La tarea C7 esta ligada a otra version de autorizacion"
        )


def _assert_operator_permission(conn, operator_id: str, permission: str) -> None:
    allowed = conn.execute(text(
        """
        SELECT EXISTS(
            SELECT 1
            FROM rtm_operators o
            JOIN rtm_operator_roles r ON r.id=o.primary_role_id
            WHERE o.id=CAST(:operator_id AS UUID)
              AND o.status='active'
              AND o.must_change_password=FALSE
              AND o.mfa_required=FALSE
              AND (o.locked_until IS NULL OR o.locked_until <= NOW())
              AND o.profile @> CAST(:profile AS JSONB)
              AND r.active=TRUE
              AND r.permissions @> CAST(:permissions AS JSONB)
        )
        """
    ), {
        "operator_id": operator_id,
        "profile": _json({"synthetic": True, "environment": "staging"}),
        "permissions": _json([permission]),
    }).scalar_one()
    if not bool(allowed):
        raise AssistedLegalPermissionError(
            f"Operador C7 sin permiso vigente {permission}"
        )


def _task_row(conn, task_id: str, *, for_update: bool = False):
    lock = " FOR UPDATE" if for_update else ""
    row = conn.execute(text(
        "SELECT * FROM rtm_connect_assisted_tasks "
        "WHERE id=CAST(:id AS UUID)" + lock
    ), {"id": task_id}).mappings().first()
    if not row:
        raise LookupError("Tarea juridica asistida C7 no encontrada")
    return row


def _task_by_action(conn, action_id: str):
    return conn.execute(text(
        "SELECT * FROM rtm_connect_assisted_tasks "
        "WHERE action_id=CAST(:id AS UUID)"
    ), {"id": action_id}).mappings().first()


def _append_event(
    conn,
    *,
    row: Mapping[str, Any],
    event_type: str,
    actor_type: str,
    operator_id: str | None,
    from_status: str | None,
    to_status: str | None,
    reason_code: str,
    payload: Mapping[str, Any] | None = None,
) -> str:
    conn.execute(text(
        "SELECT id FROM rtm_connect_assisted_tasks "
        "WHERE id=CAST(:id AS UUID) FOR UPDATE"
    ), {"id": str(row["id"])}).one()
    sequence = int(conn.execute(text(
        "SELECT COALESCE(MAX(sequence_number),0)+1 "
        "FROM rtm_connect_assisted_events "
        "WHERE task_id=CAST(:id AS UUID)"
    ), {"id": str(row["id"])}).scalar_one())
    event_id = str(uuid.uuid4())
    conn.execute(text(
        """
        INSERT INTO rtm_connect_assisted_events(
            id, task_id, action_id, attempt_id, sequence_number,
            event_type, actor_type, operator_id, from_status, to_status,
            reason_code, payload, created_at
        ) VALUES (
            CAST(:id AS UUID), CAST(:task_id AS UUID),
            CAST(:action_id AS UUID), CAST(:attempt_id AS UUID), :sequence,
            :event_type, :actor_type, CAST(:operator_id AS UUID),
            :from_status, :to_status, :reason_code,
            CAST(:payload AS JSONB), NOW()
        )
        """
    ), {
        "id": event_id,
        "task_id": str(row["id"]),
        "action_id": str(row["action_id"]),
        "attempt_id": str(row["attempt_id"]),
        "sequence": sequence,
        "event_type": event_type,
        "actor_type": actor_type,
        "operator_id": operator_id,
        "from_status": from_status,
        "to_status": to_status,
        "reason_code": reason_code,
        "payload": _json(dict(payload or {})),
    })
    return event_id


def _advance_task(
    conn,
    *,
    task_id: str,
    target_status: str,
    operator_id: str | None,
    reason_code: str,
    actor_type: str = "operator",
    updates: Mapping[str, Any] | None = None,
    event_payload: Mapping[str, Any] | None = None,
) -> None:
    row = _task_row(conn, task_id, for_update=True)
    current = str(row["status"])
    if target_status not in _ASSISTED_TRANSITIONS.get(current, set()):
        raise AssistedLegalStateError(
            f"Transicion C7 no permitida: {current} -> {target_status}"
        )
    clauses = ["status=:status", "version=version+1", "updated_at=NOW()"]
    params: dict[str, Any] = {"task_id": task_id, "status": target_status}
    for name, value in dict(updates or {}).items():
        if name not in {
            "assignee_operator_id", "assigned_by_operator_id", "assigned_at",
            "release_operator_id", "released_at", "verified_by_operator_id",
            "started_at", "reviewed_at", "ready_at", "unknown_at",
            "reconciling_at", "receipt_submitted_at", "verified_at",
            "completed_at", "review_attestation_sha256",
            "release_attestation_sha256", "external_reference",
            "receipt_evidence_id", "verified_evidence_id",
        }:
            raise ValueError(f"Campo C7 no actualizable: {name}")
        param = f"update_{name}"
        cast = ""
        if name.endswith("_operator_id") or name.endswith("_evidence_id"):
            cast = "CAST(:%s AS UUID)" % param
        elif name.endswith("_at"):
            cast = "CAST(:%s AS TIMESTAMPTZ)" % param
        else:
            cast = f":{param}"
        clauses.append(f"{name}={cast}")
        params[param] = value
    conn.execute(text(
        "UPDATE rtm_connect_assisted_tasks SET " + ", ".join(clauses)
        + " WHERE id=CAST(:task_id AS UUID)"
    ), params)
    _append_event(
        conn,
        row=row,
        event_type=f"assisted.{target_status}",
        actor_type=actor_type,
        operator_id=operator_id,
        from_status=current,
        to_status=target_status,
        reason_code=reason_code,
        payload=event_payload,
    )


def register_assisted_legal_connector(conn):
    return register_synthetic_connector(
        conn,
        code=ASSISTED_LEGAL_CODE,
        version=ASSISTED_LEGAL_CONNECTOR_VERSION,
        mode=ConnectorMode.ASSISTED,
        capabilities=(ASSISTED_LEGAL_CAPABILITY,),
        risk_ceiling=RiskClass.R4_CRITICAL_REGULATED,
        supports_reconciliation=True,
        configuration={
            "runtime_version": RTM_CONNECT_C7_ASSISTED_WORKFLOW_VERSION,
            "manifest_sha256": ASSISTED_LEGAL_MANIFEST_SHA256,
            "synthetic_only": True,
            "network_used": False,
            "external_effects": False,
            "human_final_submit_required": True,
        },
    )


def assisted_task_snapshot(
    conn, *, task_id: str, replayed: bool = False,
) -> AssistedLegalOutcome:
    row = conn.execute(text(
        """
        SELECT t.*, a.status AS action_status,
          (SELECT COUNT(*) FROM rtm_connect_attempts x
           WHERE x.action_id=t.action_id) AS attempts,
          (SELECT COUNT(*) FROM rtm_connect_evidence e
           WHERE e.action_id=t.action_id) AS evidence_rows,
          (SELECT COUNT(*) FROM rtm_connect_transitions tr
           WHERE tr.action_id=t.action_id) AS transitions,
          (SELECT replay_count FROM rtm_connect_idempotency_claims i
           WHERE i.action_id=t.action_id) AS replay_count,
          (SELECT COUNT(*) FROM rtm_connect_assisted_events ev
           WHERE ev.task_id=t.id) AS task_events
        FROM rtm_connect_assisted_tasks t
        JOIN rtm_connect_actions a ON a.id=t.action_id
        WHERE t.id=CAST(:id AS UUID)
        """
    ), {"id": task_id}).mappings().one()
    optional = lambda name: str(row[name]) if row[name] else None
    return AssistedLegalOutcome(
        task_id=str(row["id"]), task_code=str(row["task_code"]),
        action_id=str(row["action_id"]), attempt_id=str(row["attempt_id"]),
        connector_id=str(row["connector_id"]),
        authorization_id=str(row["authorization_id"]),
        task_status=str(row["status"]), action_status=str(row["action_status"]),
        assignee_operator_id=str(row["assignee_operator_id"]),
        release_operator_id=optional("release_operator_id"),
        verified_by_operator_id=optional("verified_by_operator_id"),
        due_at=_timestamp(row["due_at"]), package_sha256=str(row["package_sha256"]),
        external_reference=row["external_reference"],
        receipt_evidence_id=optional("receipt_evidence_id"),
        verified_evidence_id=optional("verified_evidence_id"),
        attempts=int(row["attempts"]), evidence_rows=int(row["evidence_rows"]),
        transitions=int(row["transitions"]),
        replay_count=int(row["replay_count"] or 0),
        task_events=int(row["task_events"]), task_version=int(row["version"]),
        replayed=bool(replayed),
    )


def prepare_assisted_legal(
    conn,
    *,
    action: ConnectActionRequest,
    grant: AuthorizationGrant,
    assignee_operator_id: str,
    assigned_by_operator_id: str,
    due_at: str,
) -> AssistedLegalOutcome:
    validate_c7_action_authority(action, grant)
    validate_execution_authority(
        action, grant, connector_mode=ConnectorMode.ASSISTED
    )
    if assignee_operator_id in grant.approved_by_operator_ids:
        raise AssistedLegalSeparationOfDutiesError(
            "El ejecutor C7 no puede ser aprobador CORE"
        )
    if assigned_by_operator_id not in grant.approved_by_operator_ids:
        raise AssistedLegalSeparationOfDutiesError(
            "La asignacion C7 debe proceder de un aprobador CORE"
        )
    _assert_operator_permission(conn, assignee_operator_id, ASSISTED_EXECUTE_PERMISSION)
    _assert_operator_permission(conn, assigned_by_operator_id, ASSISTED_RELEASE_PERMISSION)
    connector = register_assisted_legal_connector(conn)
    created = create_action(conn, action=action, authority_scope=grant.authority_code)
    adapter = AssistedLegalConnector()
    if created.replayed:
        existing = _task_by_action(conn, action.action_id)
        if not existing:
            raise AssistedLegalReplayConflict(
                "La accion C7 existe sin tarea asistida"
            )
        expected = adapter.build_package(
            action, grant, attempt_id=str(existing["attempt_id"]), due_at=due_at
        )
        if (
            expected.package_sha256 != str(existing["package_sha256"])
            or str(existing["assignee_operator_id"]) != assignee_operator_id
            or str(existing["assigned_by_operator_id"]) != assigned_by_operator_id
        ):
            raise AssistedLegalReplayConflict(
                "El replay C7 intenta cambiar paquete o asignacion"
            )
        return assisted_task_snapshot(
            conn, task_id=str(existing["id"]), replayed=True
        )
    authorize_action(conn, grant=grant)
    authorization_version, persisted_grant = _load_latest_grant(
        conn, action.action_id
    )
    _assert_same_grant(authorization_version, persisted_grant, grant)
    queue_action(conn, action_id=action.action_id, operator_id=assigned_by_operator_id)
    attempt = start_attempt(
        conn,
        action_id=action.action_id,
        connector_id=connector.connector_id,
        request_metadata={
            "workflow_version": RTM_CONNECT_C7_ASSISTED_WORKFLOW_VERSION,
            "mode": "assisted", "network_used": False,
            "external_effects": False,
        },
    )
    package = adapter.build_package(
        action, grant, attempt_id=attempt.attempt_id, due_at=due_at
    )
    task_id = str(uuid.uuid4())
    task_code = f"rtm-assisted-{package.package_sha256[:24]}"
    conn.execute(text(
        """
        INSERT INTO rtm_connect_assisted_tasks(
            id, action_id, attempt_id, connector_id, authorization_id,
            authorization_version, task_code, status, due_at,
            package_manifest, package_sha256, version, metadata,
            created_at, updated_at
        ) VALUES (
            CAST(:id AS UUID), CAST(:action_id AS UUID),
            CAST(:attempt_id AS UUID), CAST(:connector_id AS UUID),
            CAST(:authorization_id AS UUID), :authorization_version,
            :task_code, 'prepared', :due_at,
            CAST(:manifest AS JSONB), :package_sha256, 1,
            CAST(:metadata AS JSONB), NOW(), NOW()
        )
        """
    ), {
        "id": task_id, "action_id": action.action_id,
        "attempt_id": attempt.attempt_id, "connector_id": connector.connector_id,
        "authorization_id": grant.authorization_id,
        "authorization_version": authorization_version, "task_code": task_code,
        "due_at": package.due_at, "manifest": _json(package.manifest),
        "package_sha256": package.package_sha256,
        "metadata": _json({
            "synthetic_only": True, "network_used": False,
            "human_final_submit_required": True,
        }),
    })
    row = _task_row(conn, task_id)
    _append_event(
        conn, row=row, event_type="assisted.prepared", actor_type="connect",
        operator_id=assigned_by_operator_id, from_status=None,
        to_status="prepared", reason_code="assisted_package_frozen",
        payload={"package_sha256": package.package_sha256},
    )
    _advance_task(
        conn, task_id=task_id, target_status="assigned",
        operator_id=assigned_by_operator_id,
        reason_code="assisted_task_assigned",
        updates={
            "assignee_operator_id": assignee_operator_id,
            "assigned_by_operator_id": assigned_by_operator_id,
            "assigned_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return assisted_task_snapshot(conn, task_id=task_id)


def begin_assisted_review(conn, *, task_id: str, operator_id: str) -> AssistedLegalOutcome:
    row = _task_row(conn, task_id)
    if str(row["assignee_operator_id"]) != operator_id:
        raise AssistedLegalPermissionError("Solo el ejecutor asignado puede revisar")
    _assert_operator_permission(conn, operator_id, ASSISTED_EXECUTE_PERMISSION)
    _advance_task(
        conn, task_id=task_id, target_status="reviewing", operator_id=operator_id,
        reason_code="assisted_review_started",
        updates={"reviewed_at": datetime.now(timezone.utc).isoformat()},
    )
    return assisted_task_snapshot(conn, task_id=task_id)


def attest_assisted_review(
    conn, *, task_id: str, operator_id: str, human_gate_phrase: str,
) -> AssistedLegalOutcome:
    row = _task_row(conn, task_id)
    if str(row["assignee_operator_id"]) != operator_id:
        raise AssistedLegalPermissionError("Solo el ejecutor puede atestar la revision")
    if str(human_gate_phrase) != ASSISTED_LEGAL_HUMAN_GATE_PHRASE:
        raise AssistedLegalWorkflowError("Frase de puerta humana C7 incorrecta")
    _assert_operator_permission(conn, operator_id, ASSISTED_EXECUTE_PERMISSION)
    attestation = hashlib.sha256(canonical_json({
        "format": "rtm.assisted.legal.review.v1",
        "task_id": task_id,
        "action_id": str(row["action_id"]),
        "package_sha256": str(row["package_sha256"]),
        "operator_id": operator_id,
        "human_gate_phrase": human_gate_phrase,
    }).encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    _advance_task(
        conn, task_id=task_id, target_status="ready_for_release",
        operator_id=operator_id, reason_code="assisted_review_attested",
        updates={
            "reviewed_at": now, "ready_at": now,
            "review_attestation_sha256": attestation,
        }, event_payload={"review_attestation_sha256": attestation},
    )
    return assisted_task_snapshot(conn, task_id=task_id)


def release_assisted_legal(
    conn,
    *,
    task_id: str,
    grant: AuthorizationGrant,
    release_operator_id: str,
) -> AssistedLegalOutcome:
    row = _task_row(conn, task_id)
    action = _load_action_contract(conn, str(row["action_id"]))
    validate_c7_action_authority(action, grant)
    validate_execution_authority(
        action, grant, connector_mode=ConnectorMode.ASSISTED
    )
    version, persisted = _load_latest_grant(conn, action.action_id)
    _assert_same_grant(version, persisted, grant, task_row=row)
    if release_operator_id not in grant.approved_by_operator_ids:
        raise AssistedLegalSeparationOfDutiesError(
            "El liberador C7 debe ser aprobador CORE"
        )
    if release_operator_id == str(row["assignee_operator_id"]):
        raise AssistedLegalSeparationOfDutiesError(
            "El ejecutor C7 no puede liberar su propio paquete"
        )
    _assert_operator_permission(conn, release_operator_id, ASSISTED_RELEASE_PERMISSION)
    released_at = datetime.now(timezone.utc).isoformat()
    attestation = hashlib.sha256(canonical_json({
        "format": "rtm.assisted.legal.release.v1",
        "task_id": task_id, "action_id": action.action_id,
        "authorization_id": grant.authorization_id,
        "authorization_version": version,
        "package_sha256": str(row["package_sha256"]),
        "review_attestation_sha256": str(row["review_attestation_sha256"]),
        "release_operator_id": release_operator_id,
    }).encode("utf-8")).hexdigest()
    _advance_task(
        conn, task_id=task_id, target_status="released",
        operator_id=release_operator_id, reason_code="assisted_release_approved",
        updates={
            "release_operator_id": release_operator_id,
            "released_at": released_at,
            "release_attestation_sha256": attestation,
        }, event_payload={"release_attestation_sha256": attestation},
    )
    return assisted_task_snapshot(conn, task_id=task_id)


def begin_assisted_execution(
    conn, *, task_id: str, operator_id: str,
) -> AssistedLegalOutcome:
    row = _task_row(conn, task_id)
    if str(row["assignee_operator_id"]) != operator_id:
        raise AssistedLegalPermissionError("Solo el ejecutor asignado puede iniciar")
    _assert_operator_permission(conn, operator_id, ASSISTED_EXECUTE_PERMISSION)
    action = _load_action_contract(conn, str(row["action_id"]))
    version, grant = _load_latest_grant(conn, action.action_id)
    _assert_same_grant(version, grant, grant, task_row=row)
    validate_c7_action_authority(action, grant)
    validate_execution_authority(
        action, grant, connector_mode=ConnectorMode.ASSISTED
    )
    _advance_task(
        conn, task_id=task_id, target_status="in_progress", operator_id=operator_id,
        reason_code="assisted_human_execution_started",
        updates={"started_at": datetime.now(timezone.utc).isoformat()},
    )
    return assisted_task_snapshot(conn, task_id=task_id)


def mark_assisted_awaiting_receipt(
    conn, *, task_id: str, operator_id: str,
) -> AssistedLegalOutcome:
    row = _task_row(conn, task_id)
    if str(row["assignee_operator_id"]) != operator_id:
        raise AssistedLegalPermissionError("Solo el ejecutor puede declarar el resultado")
    _assert_operator_permission(conn, operator_id, ASSISTED_EXECUTE_PERMISSION)
    _advance_task(
        conn, task_id=task_id, target_status="awaiting_receipt",
        operator_id=operator_id, reason_code="assisted_receipt_expected",
    )
    return assisted_task_snapshot(conn, task_id=task_id)


def _expected_external_reference(action_id: str) -> str:
    return f"{ASSISTED_LEGAL_REFERENCE_PREFIX}{action_id}"


def mark_assisted_outcome_unknown(
    conn, *, task_id: str, operator_id: str,
) -> AssistedLegalOutcome:
    row = _task_row(conn, task_id, for_update=True)
    if str(row["assignee_operator_id"]) != operator_id:
        raise AssistedLegalPermissionError("Solo el ejecutor puede declarar UNKNOWN")
    _assert_operator_permission(conn, operator_id, ASSISTED_EXECUTE_PERMISSION)
    reference = _expected_external_reference(str(row["action_id"]))
    record_attempt_outcome(
        conn, attempt_id=str(row["attempt_id"]),
        target_status=ActionStatus.UNKNOWN, external_reference=reference,
        failure_class="ambiguous_human_submission",
        error_code="assisted_outcome_unknown",
        result_metadata={
            "assisted_legal": True, "blind_retry_allowed": False,
            "network_used": False,
        },
    )
    _advance_task(
        conn, task_id=task_id, target_status="outcome_unknown",
        operator_id=operator_id, reason_code="assisted_outcome_unknown",
        updates={
            "unknown_at": datetime.now(timezone.utc).isoformat(),
            "external_reference": reference,
        }, event_payload={"blind_retry_allowed": False},
    )
    return assisted_task_snapshot(conn, task_id=task_id)


def begin_assisted_reconciliation(
    conn, *, task_id: str, operator_id: str,
) -> AssistedLegalOutcome:
    row = _task_row(conn, task_id, for_update=True)
    _assert_operator_permission(conn, operator_id, ASSISTED_VERIFY_PERMISSION)
    if operator_id == str(row["assignee_operator_id"]):
        raise AssistedLegalSeparationOfDutiesError(
            "El ejecutor no puede reconciliar su propia incertidumbre"
        )
    begin_reconciliation(
        conn, action_id=str(row["action_id"]),
        attempt_id=str(row["attempt_id"]),
        metadata={"assisted_legal": True, "blind_retry_allowed": False},
    )
    _advance_task(
        conn, task_id=task_id, target_status="reconciling",
        operator_id=operator_id, actor_type="reconciliation",
        reason_code="assisted_reconciliation_started",
        updates={"reconciling_at": datetime.now(timezone.utc).isoformat()},
    )
    return assisted_task_snapshot(conn, task_id=task_id)


def resolve_assisted_reconciliation(
    conn,
    *,
    task_id: str,
    operator_id: str,
    target_status: ActionStatus,
) -> AssistedLegalOutcome:
    """Clasifica lo observado sin crear otro intento ni reenviar."""

    admitted = {
        ActionStatus.UNKNOWN,
        ActionStatus.MANUAL_REVIEW,
        ActionStatus.PERMANENT_FAILED,
    }
    if target_status not in admitted:
        raise ValueError("Resultado de reconciliacion asistida no admitido")
    row = _task_row(conn, task_id, for_update=True)
    if str(row["status"]) != "reconciling":
        raise AssistedLegalStateError(
            "Solo una tarea C7 reconciling puede clasificarse"
        )
    if operator_id == str(row["assignee_operator_id"]):
        raise AssistedLegalSeparationOfDutiesError(
            "El ejecutor no puede resolver solo la incertidumbre"
        )
    _assert_operator_permission(conn, operator_id, ASSISTED_VERIFY_PERMISSION)
    task_status = {
        ActionStatus.UNKNOWN: "outcome_unknown",
        ActionStatus.MANUAL_REVIEW: "manual_review",
        ActionStatus.PERMANENT_FAILED: "permanent_failed",
    }[target_status]
    record_reconciliation_outcome(
        conn,
        action_id=str(row["action_id"]),
        attempt_id=str(row["attempt_id"]),
        target_status=target_status,
        operator_id=operator_id,
        reason_code=f"assisted_reconciliation_{target_status.value}",
        metadata={
            "assisted_legal": True,
            "blind_retry_allowed": False,
            "network_used": False,
        },
    )
    _advance_task(
        conn,
        task_id=task_id,
        target_status=task_status,
        operator_id=operator_id,
        actor_type="reconciliation",
        reason_code=f"assisted_reconciliation_{target_status.value}",
        event_payload={
            "attempt_id": str(row["attempt_id"]),
            "blind_retry_allowed": False,
        },
    )
    return assisted_task_snapshot(conn, task_id=task_id)


def submit_assisted_receipt(
    conn,
    *,
    task_id: str,
    operator_id: str,
    submission: AssistedReceiptSubmission,
) -> AssistedLegalOutcome:
    row = _task_row(conn, task_id, for_update=True)
    current = str(row["status"])
    if current not in {"awaiting_receipt", "reconciling"}:
        raise AssistedLegalStateError("La tarea C7 no admite un justificante ahora")
    if current == "awaiting_receipt":
        if str(row["assignee_operator_id"]) != operator_id:
            raise AssistedLegalPermissionError("Solo el ejecutor aporta el recibo normal")
        _assert_operator_permission(conn, operator_id, ASSISTED_EXECUTE_PERMISSION)
    else:
        if operator_id == str(row["assignee_operator_id"]):
            raise AssistedLegalSeparationOfDutiesError(
                "El ejecutor no puede resolver solo la incertidumbre"
            )
        _assert_operator_permission(conn, operator_id, ASSISTED_VERIFY_PERMISSION)
    action = _load_action_contract(conn, str(row["action_id"]))
    version, grant = _load_latest_grant(conn, action.action_id)
    _assert_same_grant(version, grant, grant, task_row=row)
    validate_c7_action_authority(action, grant)
    expected_reference = _expected_external_reference(action.action_id)
    if submission.external_reference != expected_reference:
        raise AssistedLegalReplayConflict("Referencia C7 no correlacionada")
    manifest = dict(row["package_manifest"])
    expected_gate_sha256 = str(manifest.get("human_gate_sha256") or "")
    if (
        submission.package_sha256 != str(row["package_sha256"])
        or submission.human_gate_sha256 != expected_gate_sha256
        or submission.human_final_gate != ASSISTED_LEGAL_HUMAN_GATE_PHRASE
    ):
        raise AssistedLegalReplayConflict(
            "El justificante C7 no esta ligado al paquete y gate congelados"
        )
    evidence = AssistedLegalConnector().capture_receipt(
        action, grant, attempt_id=str(row["attempt_id"]), submission=submission
    )
    if current == "awaiting_receipt":
        record_attempt_outcome(
            conn, attempt_id=str(row["attempt_id"]),
            target_status=ActionStatus.EXTERNAL_ACCEPTED,
            external_reference=submission.external_reference,
            result_metadata={
                "assisted_legal": True, "network_used": False,
                "human_final_submit_required": True,
            },
        )
    evidence_id = record_evidence(
        conn, action_id=action.action_id, attempt_id=str(row["attempt_id"]),
        evidence=evidence,
        metadata={
            "connector_code": ASSISTED_LEGAL_CODE,
            "connector_version": ASSISTED_LEGAL_CONNECTOR_VERSION,
            "phase": "receipt_captured", "network_used": False,
            "package_sha256": submission.package_sha256,
            "human_gate_sha256": submission.human_gate_sha256,
        },
    )
    _advance_task(
        conn, task_id=task_id, target_status="receipt_submitted",
        operator_id=operator_id,
        actor_type=("operator" if current == "awaiting_receipt" else "reconciliation"),
        reason_code="assisted_receipt_submitted",
        updates={
            "receipt_submitted_at": datetime.now(timezone.utc).isoformat(),
            "external_reference": submission.external_reference,
            "receipt_evidence_id": evidence_id,
        }, event_payload={"evidence_id": evidence_id},
    )
    return assisted_task_snapshot(conn, task_id=task_id)


def verify_assisted_receipt(
    conn,
    *,
    task_id: str,
    grant: AuthorizationGrant,
    verifier_operator_id: str,
    observed_receipt_sha256: str,
    observed_external_reference: str,
    observed_package_sha256: str,
    observed_human_gate_sha256: str,
    verified_at: str,
) -> AssistedLegalOutcome:
    row = _task_row(conn, task_id, for_update=True)
    action = _load_action_contract(conn, str(row["action_id"]))
    version, persisted = _load_latest_grant(conn, action.action_id)
    _assert_same_grant(version, persisted, grant, task_row=row)
    validate_c7_action_authority(action, grant)
    if verifier_operator_id not in grant.approved_by_operator_ids:
        raise AssistedLegalSeparationOfDutiesError(
            "El verificador C7 debe ser aprobador CORE"
        )
    if verifier_operator_id in {
        str(row["assignee_operator_id"]), str(row["release_operator_id"]),
    }:
        raise AssistedLegalSeparationOfDutiesError(
            "C7 exige ejecutor, liberador y verificador distintos"
        )
    _assert_operator_permission(conn, verifier_operator_id, ASSISTED_VERIFY_PERMISSION)
    receipt = conn.execute(text(
        "SELECT * FROM rtm_connect_evidence "
        "WHERE id=CAST(:id AS UUID) AND action_id=CAST(:action_id AS UUID)"
    ), {
        "id": str(row["receipt_evidence_id"]), "action_id": action.action_id,
    }).mappings().first()
    if not receipt or str(receipt["evidence_level"]) != EvidenceLevel.E3_RECEIPT_CAPTURED.value:
        raise AssistedLegalStateError("No existe evidencia E3 exacta para verificar")
    manifest = dict(row["package_manifest"])
    verification = AssistedLegalConnector().verify_receipt(
        action,
        grant,
        attempt_id=str(row["attempt_id"]),
        receipt_sha256=str(receipt["receipt_sha256"]),
        storage_ref=str(receipt["receipt_storage_ref"]),
        external_reference=str(receipt["external_reference"]),
        package_sha256=str(row["package_sha256"]),
        human_gate_sha256=str(manifest["human_gate_sha256"]),
        observed_receipt_sha256=observed_receipt_sha256,
        observed_external_reference=observed_external_reference,
        observed_package_sha256=observed_package_sha256,
        observed_human_gate_sha256=observed_human_gate_sha256,
        human_final_gate=ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
        verified_at=verified_at,
    )
    evidence_id = record_evidence(
        conn, action_id=action.action_id, attempt_id=str(row["attempt_id"]),
        evidence=verification.evidence,
        verified_by_operator_id=verifier_operator_id,
        metadata={
            "connector_code": ASSISTED_LEGAL_CODE,
            "connector_version": ASSISTED_LEGAL_CONNECTOR_VERSION,
            "phase": "receipt_verified",
            "verification_sha256": verification.verification_sha256,
            "network_used": False,
        },
    )
    _advance_task(
        conn, task_id=task_id, target_status="verified",
        operator_id=verifier_operator_id,
        reason_code="assisted_receipt_verified",
        updates={
            "verified_by_operator_id": verifier_operator_id,
            "verified_at": verified_at,
            "verified_evidence_id": evidence_id,
        }, event_payload={"evidence_id": evidence_id},
    )
    return assisted_task_snapshot(conn, task_id=task_id)


def complete_assisted_legal(
    conn, *, task_id: str, verifier_operator_id: str,
) -> AssistedLegalOutcome:
    row = _task_row(conn, task_id, for_update=True)
    if str(row["status"]) == "completed":
        return assisted_task_snapshot(conn, task_id=task_id, replayed=True)
    if str(row["status"]) != "verified":
        raise AssistedLegalStateError("Solo una tarea C7 verificada puede completar")
    if str(row["verified_by_operator_id"]) != verifier_operator_id:
        raise AssistedLegalPermissionError("Debe completar el verificador registrado")
    _assert_operator_permission(conn, verifier_operator_id, ASSISTED_VERIFY_PERMISSION)
    action_state = str(action_snapshot(
        conn, action_id=str(row["action_id"])
    )["status"])
    if action_state == ActionStatus.RECONCILING.value:
        record_reconciliation_outcome(
            conn, action_id=str(row["action_id"]),
            attempt_id=str(row["attempt_id"]),
            target_status=ActionStatus.CONFIRMED,
            evidence_id=str(row["verified_evidence_id"]),
            operator_id=verifier_operator_id,
            reason_code="assisted_reconciliation_confirmed",
            metadata={"assisted_legal": True, "blind_retry_allowed": False},
        )
    else:
        confirm_action(
            conn, action_id=str(row["action_id"]),
            operator_id=verifier_operator_id,
            evidence_id=str(row["verified_evidence_id"]),
        )
    _advance_task(
        conn, task_id=task_id, target_status="completed",
        operator_id=verifier_operator_id, actor_type="core",
        reason_code="assisted_legal_completed",
        updates={"completed_at": datetime.now(timezone.utc).isoformat()},
    )
    return assisted_task_snapshot(conn, task_id=task_id)


__all__ = [
    "RTM_CONNECT_C7_ASSISTED_WORKFLOW_VERSION",
    "ASSISTED_EXECUTE_PERMISSION", "ASSISTED_RELEASE_PERMISSION",
    "ASSISTED_VERIFY_PERMISSION", "AssistedLegalOutcome",
    "AssistedLegalPermissionError", "AssistedLegalReplayConflict",
    "AssistedLegalSeparationOfDutiesError", "AssistedLegalStateError",
    "AssistedLegalWorkflowError", "assisted_task_snapshot",
    "attest_assisted_review", "begin_assisted_execution",
    "begin_assisted_reconciliation", "begin_assisted_review",
    "complete_assisted_legal", "mark_assisted_awaiting_receipt",
    "mark_assisted_outcome_unknown", "prepare_assisted_legal",
    "register_assisted_legal_connector", "release_assisted_legal",
    "resolve_assisted_reconciliation",
    "submit_assisted_receipt", "verify_assisted_receipt",
]
