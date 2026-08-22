"""Orquestación del conector determinista synthetic.echo en C2.

Utiliza exclusivamente la API pública del Kernel C1. No publica endpoints, no
crea tablas y no usa red. El conector solo se registra dentro de una transacción
explícita y sigue marcado como sintético.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from rtm_connect.connectors.synthetic_echo import (
    SYNTHETIC_ECHO_CAPABILITY,
    SYNTHETIC_ECHO_CODE,
    SYNTHETIC_ECHO_CONNECTOR_VERSION,
    SYNTHETIC_ECHO_MANIFEST_SHA256,
    SyntheticEchoConnector,
    SyntheticEchoScenario,
)
from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    RiskClass,
)
from rtm_connect.idempotency import payload_sha256
from rtm_connect.kernel import (
    action_snapshot,
    authorize_action,
    begin_reconciliation,
    confirm_action,
    create_action,
    queue_action,
    record_attempt_outcome,
    record_evidence,
    register_synthetic_connector,
    start_attempt,
)
from rtm_connect.state_machine import ActionStatus


RTM_CONNECT_C2_EXECUTION_VERSION = "rtm_connect_c2_execution_v1_0"


class ExistingActionReplayBlocked(RuntimeError):
    """Impide ejecutar de nuevo a ciegas una acción no terminal."""


class SyntheticEchoExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SyntheticEchoExecutionOutcome:
    action_id: str
    connector_id: str
    attempt_id: str | None
    scenario: str
    status: str
    replayed: bool
    confirmed: bool
    evidence_level: str | None
    external_reference: str | None
    attempts: int
    evidence_rows: int
    transitions: int
    replay_count: int


def _outcome(
    snapshot: dict[str, Any],
    *,
    connector_id: str,
    attempt_id: str | None,
    scenario: str,
    replayed: bool,
    evidence_level: EvidenceLevel | None,
) -> SyntheticEchoExecutionOutcome:
    return SyntheticEchoExecutionOutcome(
        action_id=str(snapshot["id"]),
        connector_id=connector_id,
        attempt_id=attempt_id,
        scenario=scenario,
        status=str(snapshot["status"]),
        replayed=bool(replayed),
        confirmed=str(snapshot["status"]) == ActionStatus.CONFIRMED.value,
        evidence_level=(evidence_level.value if evidence_level else None),
        external_reference=(
            str(snapshot["external_reference"])
            if snapshot.get("external_reference")
            else None
        ),
        attempts=int(snapshot["attempts"]),
        evidence_rows=int(snapshot["evidence_rows"]),
        transitions=int(snapshot["transitions"]),
        replay_count=int(snapshot.get("replay_count") or 0),
    )


def register_synthetic_echo_connector(conn):
    return register_synthetic_connector(
        conn,
        code=SYNTHETIC_ECHO_CODE,
        version=SYNTHETIC_ECHO_CONNECTOR_VERSION,
        mode=ConnectorMode.API,
        capabilities=(SYNTHETIC_ECHO_CAPABILITY,),
        risk_ceiling=RiskClass.R4_CRITICAL_REGULATED,
        supports_reconciliation=True,
        configuration={
            "runtime_version": RTM_CONNECT_C2_EXECUTION_VERSION,
            "manifest_sha256": SYNTHETIC_ECHO_MANIFEST_SHA256,
            "deterministic": True,
            "network_used": False,
            "external_effects": False,
        },
    )


def execute_synthetic_echo(
    conn,
    *,
    action: ConnectActionRequest,
    grant: AuthorizationGrant,
    scenario: SyntheticEchoScenario | str,
    operator_id: str | None = None,
) -> SyntheticEchoExecutionOutcome:
    if action.capability != SYNTHETIC_ECHO_CAPABILITY:
        raise SyntheticEchoExecutionError(
            "La acción no pertenece a synthetic.echo"
        )
    if ConnectorMode.API not in grant.authorized_connector_modes:
        raise SyntheticEchoExecutionError(
            "La autorización no permite el modo API sintético"
        )

    connector = register_synthetic_echo_connector(conn)
    created = create_action(
        conn,
        action=action,
        authority_scope=grant.authority_code,
    )
    selected = (
        scenario
        if isinstance(scenario, SyntheticEchoScenario)
        else SyntheticEchoScenario(str(scenario))
    )
    if created.replayed:
        snapshot = action_snapshot(conn, action_id=created.action_id)
        if str(snapshot["status"]) != ActionStatus.CONFIRMED.value:
            raise ExistingActionReplayBlocked(
                "La acción ya existe y no puede reejecutarse a ciegas"
            )
        return _outcome(
            snapshot,
            connector_id=connector.connector_id,
            attempt_id=None,
            scenario=selected.value,
            replayed=True,
            evidence_level=None,
        )

    authorize_action(conn, grant=grant)
    queue_action(conn, action_id=action.action_id, operator_id=operator_id)
    attempt = start_attempt(
        conn,
        action_id=action.action_id,
        connector_id=connector.connector_id,
        request_metadata={
            "connector_runtime": RTM_CONNECT_C2_EXECUTION_VERSION,
            "scenario": selected.value,
            "network_used": False,
        },
    )
    result = SyntheticEchoConnector().execute(
        action,
        attempt_id=attempt.attempt_id,
        scenario=selected,
    )

    status_map = {
        "external_accepted": ActionStatus.EXTERNAL_ACCEPTED,
        "unknown": ActionStatus.UNKNOWN,
        "retryable_failed": ActionStatus.RETRYABLE_FAILED,
        "permanent_failed": ActionStatus.PERMANENT_FAILED,
        "manual_review": ActionStatus.MANUAL_REVIEW,
    }
    try:
        target = status_map[result.status]
    except KeyError as exc:
        raise SyntheticEchoExecutionError(
            "Resultado synthetic.echo no reconocido por el Kernel"
        ) from exc

    record_attempt_outcome(
        conn,
        attempt_id=attempt.attempt_id,
        target_status=target,
        external_reference=result.external_reference,
        failure_class=result.failure_class,
        error_code=result.error_code,
        result_metadata=dict(result.metadata),
    )
    record_evidence(
        conn,
        action_id=action.action_id,
        attempt_id=attempt.attempt_id,
        evidence=result.evidence,
        verified_by_operator_id=(
            operator_id
            if result.evidence.level is EvidenceLevel.E4_RECEIPT_VERIFIED
            else None
        ),
        metadata={
            "connector_code": result.connector_code,
            "connector_version": result.connector_version,
            "scenario": selected.value,
            "network_used": False,
        },
    )
    if selected is SyntheticEchoScenario.SUCCESS:
        confirm_action(
            conn,
            action_id=action.action_id,
            operator_id=operator_id,
        )

    snapshot = action_snapshot(conn, action_id=action.action_id)
    return _outcome(
        snapshot,
        connector_id=connector.connector_id,
        attempt_id=attempt.attempt_id,
        scenario=selected.value,
        replayed=False,
        evidence_level=result.evidence.level,
    )


def reconcile_synthetic_echo(
    conn,
    *,
    action: ConnectActionRequest,
    operator_id: str | None = None,
) -> SyntheticEchoExecutionOutcome:
    row = conn.execute(
        text(
            """
            SELECT
                a.status,
                a.payload_sha256,
                a.external_reference,
                x.id AS attempt_id,
                x.connector_id,
                c.code AS connector_code,
                c.version AS connector_version,
                c.synthetic_only
            FROM rtm_connect_actions a
            JOIN rtm_connect_attempts x
              ON x.action_id=a.id
            JOIN rtm_connect_connectors c
              ON c.id=x.connector_id
            WHERE a.id=CAST(:action_id AS UUID)
            ORDER BY x.attempt_number DESC
            LIMIT 1
            FOR UPDATE OF a, x
            """
        ),
        {"action_id": action.action_id},
    ).mappings().first()
    if not row:
        raise LookupError("Acción synthetic.echo no encontrada")
    if str(row["status"]) != ActionStatus.UNKNOWN.value:
        raise SyntheticEchoExecutionError(
            "Solo una acción unknown puede reconciliarse en C2"
        )
    if str(row["payload_sha256"]) != payload_sha256(action):
        raise SyntheticEchoExecutionError(
            "El contrato aportado no coincide con la acción persistida"
        )
    if (
        str(row["connector_code"]) != SYNTHETIC_ECHO_CODE
        or str(row["connector_version"])
        != SYNTHETIC_ECHO_CONNECTOR_VERSION
        or not bool(row["synthetic_only"])
    ):
        raise SyntheticEchoExecutionError(
            "El intento no pertenece al conector sintético esperado"
        )

    attempt_id = str(row["attempt_id"])
    connector_id = str(row["connector_id"])
    external_reference = str(row["external_reference"] or "")
    begin_reconciliation(conn, action_id=action.action_id)
    result = SyntheticEchoConnector().reconcile(
        action,
        attempt_id=attempt_id,
        external_reference=external_reference,
    )
    record_evidence(
        conn,
        action_id=action.action_id,
        attempt_id=attempt_id,
        evidence=result.evidence,
        verified_by_operator_id=operator_id,
        metadata={
            "connector_code": result.connector_code,
            "connector_version": result.connector_version,
            "scenario": "unknown_reconciled",
            "network_used": False,
        },
    )
    conn.execute(
        text(
            """
            UPDATE rtm_connect_attempts
            SET reconciliation_required=FALSE,
                result_metadata=result_metadata || CAST(:metadata AS JSONB),
                updated_at=NOW()
            WHERE id=CAST(:attempt_id AS UUID)
            """
        ),
        {
            "attempt_id": attempt_id,
            "metadata": json.dumps(
                dict(result.metadata),
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    )
    confirm_action(
        conn,
        action_id=action.action_id,
        operator_id=operator_id,
    )
    snapshot = action_snapshot(conn, action_id=action.action_id)
    return _outcome(
        snapshot,
        connector_id=connector_id,
        attempt_id=attempt_id,
        scenario="unknown_reconciled",
        replayed=False,
        evidence_level=result.evidence.level,
    )


__all__ = [
    "RTM_CONNECT_C2_EXECUTION_VERSION",
    "ExistingActionReplayBlocked",
    "SyntheticEchoExecutionError",
    "SyntheticEchoExecutionOutcome",
    "execute_synthetic_echo",
    "reconcile_synthetic_echo",
    "register_synthetic_echo_connector",
]
