"""Motor sintético UNKNOWN → RECONCILING de RTM CONNECT C4.

Consume únicamente webhooks previamente verificados y correlacionados. Nunca
crea un intento, nunca reejecuta el conector de origen y solo confirma CORE
mediante la compuerta E4 pública del kernel.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import text

from rtm_connect.contracts import EvidenceLevel, EvidenceRecord
from rtm_connect.kernel import (
    begin_reconciliation,
    record_evidence,
    record_reconciliation_outcome,
)
from rtm_connect.state_machine import ActionStatus
from rtm_connect.webhooks import mark_webhook_processed


RTM_CONNECT_C4_RECONCILIATION_VERSION = (
    "rtm_connect_c4_reconciliation_v1_0"
)


class ReconciliationWorkflowError(RuntimeError):
    pass


class ReconciliationStateError(ReconciliationWorkflowError):
    pass


class ReconciliationInProgress(ReconciliationStateError):
    pass


@dataclass(frozen=True)
class WebhookReconciliationOutcome:
    reconciliation_id: str
    webhook_id: str
    action_id: str
    attempt_id: str
    status: str
    resolution: str | None
    evidence_id: str | None
    action_status: str
    reconciliation_required: bool
    attempts: int
    events: int
    version: int
    replayed: bool


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _append_reconciliation_event(
    conn,
    *,
    reconciliation_id: str,
    action_id: str,
    attempt_id: str,
    webhook_id: str,
    event_type: str,
    actor_type: str,
    operator_id: str | None,
    from_status: str | None,
    to_status: str | None,
    resolution: str | None,
    reason_code: str,
    reason_detail: str | None = None,
    evidence_id: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> str:
    conn.execute(
        text(
            """
            SELECT id FROM rtm_connect_reconciliations
            WHERE id=CAST(:reconciliation_id AS UUID)
            FOR UPDATE
            """
        ),
        {"reconciliation_id": reconciliation_id},
    ).one()
    sequence = int(
        conn.execute(
            text(
                """
                SELECT COALESCE(MAX(sequence_number), 0) + 1
                FROM rtm_connect_reconciliation_events
                WHERE reconciliation_id=CAST(:reconciliation_id AS UUID)
                """
            ),
            {"reconciliation_id": reconciliation_id},
        ).scalar_one()
    )
    event_id = str(uuid.uuid4())
    conn.execute(
        text(
            """
            INSERT INTO rtm_connect_reconciliation_events(
                id, reconciliation_id, action_id, attempt_id,
                webhook_inbox_id, sequence_number, event_type, actor_type,
                operator_id, from_status, to_status, resolution,
                reason_code, reason_detail, evidence_id, payload, created_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:reconciliation_id AS UUID),
                CAST(:action_id AS UUID), CAST(:attempt_id AS UUID),
                CAST(:webhook_id AS UUID), :sequence_number,
                :event_type, :actor_type, CAST(:operator_id AS UUID),
                :from_status, :to_status, :resolution,
                :reason_code, :reason_detail, CAST(:evidence_id AS UUID),
                CAST(:payload AS JSONB), NOW()
            )
            """
        ),
        {
            "id": event_id,
            "reconciliation_id": reconciliation_id,
            "action_id": action_id,
            "attempt_id": attempt_id,
            "webhook_id": webhook_id,
            "sequence_number": sequence,
            "event_type": event_type,
            "actor_type": actor_type,
            "operator_id": operator_id,
            "from_status": from_status,
            "to_status": to_status,
            "resolution": resolution,
            "reason_code": reason_code,
            "reason_detail": reason_detail,
            "evidence_id": evidence_id,
            "payload": _json(dict(payload or {})),
        },
    )
    return event_id


def reconciliation_snapshot(
    conn,
    *,
    reconciliation_id: str,
    replayed: bool = False,
) -> WebhookReconciliationOutcome:
    row = conn.execute(
        text(
            """
            SELECT r.*, a.status AS action_status,
                   x.reconciliation_required,
                   (SELECT COUNT(*) FROM rtm_connect_attempts ax
                    WHERE ax.action_id=r.action_id) AS attempts,
                   (SELECT COUNT(*)
                    FROM rtm_connect_reconciliation_events re
                    WHERE re.reconciliation_id=r.id) AS events
            FROM rtm_connect_reconciliations r
            JOIN rtm_connect_actions a ON a.id=r.action_id
            JOIN rtm_connect_attempts x ON x.id=r.attempt_id
            WHERE r.id=CAST(:reconciliation_id AS UUID)
            """
        ),
        {"reconciliation_id": reconciliation_id},
    ).mappings().first()
    if not row:
        raise LookupError("Reconciliación RTM CONNECT no encontrada")
    return WebhookReconciliationOutcome(
        reconciliation_id=str(row["id"]),
        webhook_id=str(row["webhook_inbox_id"]),
        action_id=str(row["action_id"]),
        attempt_id=str(row["attempt_id"]),
        status=str(row["status"]),
        resolution=(
            str(row["resolution"])
            if row["resolution"] is not None
            else None
        ),
        evidence_id=str(row["evidence_id"]) if row["evidence_id"] else None,
        action_status=str(row["action_status"]),
        reconciliation_required=bool(row["reconciliation_required"]),
        attempts=int(row["attempts"]),
        events=int(row["events"]),
        version=int(row["version"]),
        replayed=bool(replayed),
    )


def _existing_for_webhook(conn, webhook_id: str):
    return conn.execute(
        text(
            """
            SELECT id, status FROM rtm_connect_reconciliations
            WHERE webhook_inbox_id=CAST(:webhook_id AS UUID)
            FOR UPDATE
            """
        ),
        {"webhook_id": webhook_id},
    ).mappings().first()


def reconcile_webhook(
    conn,
    *,
    webhook_id: str,
    operator_id: str | None = None,
) -> WebhookReconciliationOutcome:
    """Clasifica una observación matched y cierra su expediente auditable."""

    webhook = conn.execute(
        text(
            """
            SELECT * FROM rtm_connect_webhook_inbox
            WHERE id=CAST(:webhook_id AS UUID)
            FOR UPDATE
            """
        ),
        {"webhook_id": webhook_id},
    ).mappings().first()
    if not webhook:
        raise LookupError("Webhook RTM CONNECT no encontrado")
    existing = _existing_for_webhook(conn, webhook_id)
    if existing:
        if str(existing["status"]) == "resolved":
            return reconciliation_snapshot(
                conn,
                reconciliation_id=str(existing["id"]),
                replayed=True,
            )
        if str(existing["status"]) == "started":
            raise ReconciliationInProgress(
                "La reconciliación ya está iniciada y no puede "
                "presentarse como resultado definitivo"
            )
        raise ReconciliationStateError(
            "La reconciliación persistida tiene un estado no admitido"
        )
    if str(webhook["status"]) != "matched":
        raise ReconciliationStateError(
            "Solo un webhook matched puede abrir reconciliación"
        )
    action_id = str(webhook["matched_action_id"])
    attempt_id = str(webhook["matched_attempt_id"])
    scope = conn.execute(
        text(
            """
            SELECT a.status AS action_status, a.payload_sha256,
                   a.external_reference AS action_external_reference,
                   x.status AS attempt_status, x.request_sha256,
                   x.external_reference AS attempt_external_reference,
                   x.reconciliation_required,
                   c.status AS connector_status,
                   c.environment AS connector_environment,
                   c.synthetic_only AS connector_synthetic_only,
                   c.credential_ref AS connector_credential_ref,
                   c.supports_reconciliation
            FROM rtm_connect_actions a
            JOIN rtm_connect_attempts x
              ON x.action_id=a.id
            JOIN rtm_connect_connectors c
              ON c.id=x.connector_id
            WHERE a.id=CAST(:action_id AS UUID)
              AND x.id=CAST(:attempt_id AS UUID)
            FOR UPDATE OF a, x
            """
        ),
        {"action_id": action_id, "attempt_id": attempt_id},
    ).mappings().first()
    if not scope:
        raise ReconciliationStateError(
            "El alcance correlacionado dejó de existir"
        )
    if (
        str(scope["action_status"]) != ActionStatus.UNKNOWN.value
        or str(scope["attempt_status"]) != "unknown"
        or not bool(scope["reconciliation_required"])
        or not bool(scope["supports_reconciliation"])
        or str(scope["connector_status"]) != "active"
        or str(scope["connector_environment"]) != "staging"
        or not bool(scope["connector_synthetic_only"])
        or scope["connector_credential_ref"] is not None
        or str(scope["payload_sha256"])
        != str(webhook["request_sha256"])
        or str(scope["request_sha256"])
        != str(webhook["request_sha256"])
        or str(scope["action_external_reference"])
        != str(webhook["external_reference"])
        or str(scope["attempt_external_reference"])
        != str(webhook["external_reference"])
    ):
        raise ReconciliationStateError(
            "El alcance UNKNOWN cambió antes de iniciar reconciliación"
        )
    number = int(
        conn.execute(
            text(
                """
                SELECT COALESCE(MAX(reconciliation_number), 0) + 1
                FROM rtm_connect_reconciliations
                WHERE action_id=CAST(:action_id AS UUID)
                """
            ),
            {"action_id": action_id},
        ).scalar_one()
    )
    reconciliation_id = str(uuid.uuid4())
    conn.execute(
        text(
            """
            INSERT INTO rtm_connect_reconciliations(
                id, action_id, attempt_id, webhook_inbox_id,
                reconciliation_number, status, resolution,
                request_sha256, external_reference, evidence_id,
                started_at, resolved_at, resolved_by_operator_id,
                resolution_code, resolution_detail, version, metadata,
                created_at, updated_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:action_id AS UUID),
                CAST(:attempt_id AS UUID), CAST(:webhook_id AS UUID),
                :reconciliation_number, 'started', NULL,
                :request_sha256, :external_reference, NULL,
                NOW(), NULL, NULL, NULL, NULL, 1,
                CAST(:metadata AS JSONB), NOW(), NOW()
            )
            """
        ),
        {
            "id": reconciliation_id,
            "action_id": action_id,
            "attempt_id": attempt_id,
            "webhook_id": webhook_id,
            "reconciliation_number": number,
            "request_sha256": str(webhook["request_sha256"]),
            "external_reference": str(webhook["external_reference"]),
            "metadata": _json(
                {
                    "runtime_version": RTM_CONNECT_C4_RECONCILIATION_VERSION,
                    "synthetic_only": True,
                    "network_used": False,
                    "external_effects_executed": False,
                }
            ),
        },
    )
    _append_reconciliation_event(
        conn,
        reconciliation_id=reconciliation_id,
        action_id=action_id,
        attempt_id=attempt_id,
        webhook_id=webhook_id,
        event_type="reconciliation.started",
        actor_type="reconciliation",
        operator_id=operator_id,
        from_status=None,
        to_status="started",
        resolution=None,
        reason_code="matched_webhook_started",
        payload={"reported_outcome": str(webhook["reported_outcome"])},
    )

    begin_reconciliation(
        conn,
        action_id=action_id,
        attempt_id=attempt_id,
        request_id=f"webhook:{webhook_id}",
        metadata={
            "webhook_id": webhook_id,
            "reconciliation_id": reconciliation_id,
        },
    )

    resolution = str(webhook["reported_outcome"])
    try:
        target = ActionStatus(resolution)
    except ValueError as exc:
        raise ReconciliationWorkflowError(
            "Resultado webhook no admitido para reconciliación"
        ) from exc
    if target not in {
        ActionStatus.CONFIRMED,
        ActionStatus.RETRYABLE_FAILED,
        ActionStatus.UNKNOWN,
        ActionStatus.MANUAL_REVIEW,
        ActionStatus.PERMANENT_FAILED,
    }:
        raise ReconciliationWorkflowError(
            "Resultado webhook fuera del contrato C4"
        )

    evidence_id: str | None = None
    if target is ActionStatus.CONFIRMED:
        if not webhook["receipt_sha256"] or not webhook["receipt_storage_ref"]:
            raise ReconciliationWorkflowError(
                "confirmed carece de material E4"
            )
        evidence = EvidenceRecord(
            level=EvidenceLevel.E4_RECEIPT_VERIFIED,
            request_sha256=str(webhook["request_sha256"]),
            external_reference=str(webhook["external_reference"]),
            receipt_sha256=str(webhook["receipt_sha256"]),
            receipt_storage_ref=str(webhook["receipt_storage_ref"]),
            verified_at=webhook["occurred_at"].isoformat(),
            verification_method="synthetic_webhook_receipt_hash_v1",
        )
        evidence_id = record_evidence(
            conn,
            action_id=action_id,
            attempt_id=attempt_id,
            evidence=evidence,
            verified_by_operator_id=operator_id,
            metadata={
                "webhook_id": webhook_id,
                "reconciliation_id": reconciliation_id,
                "synthetic_only": True,
            },
        )

    record_reconciliation_outcome(
        conn,
        action_id=action_id,
        attempt_id=attempt_id,
        target_status=target,
        evidence_id=evidence_id,
        operator_id=operator_id,
        reason_code=f"webhook_{resolution}",
        request_id=f"webhook:{webhook_id}",
        metadata={
            "webhook_id": webhook_id,
            "reconciliation_id": reconciliation_id,
            "reported_outcome": resolution,
        },
    )

    conn.execute(
        text(
            """
            UPDATE rtm_connect_reconciliations
            SET status='resolved', resolution=:resolution,
                evidence_id=CAST(:evidence_id AS UUID), resolved_at=NOW(),
                resolved_by_operator_id=CAST(:operator_id AS UUID),
                resolution_code=:resolution_code,
                version=version+1, updated_at=NOW()
            WHERE id=CAST(:reconciliation_id AS UUID)
            """
        ),
        {
            "reconciliation_id": reconciliation_id,
            "resolution": resolution,
            "evidence_id": evidence_id,
            "operator_id": operator_id,
            "resolution_code": f"synthetic_webhook_{resolution}",
        },
    )
    _append_reconciliation_event(
        conn,
        reconciliation_id=reconciliation_id,
        action_id=action_id,
        attempt_id=attempt_id,
        webhook_id=webhook_id,
        event_type="reconciliation.resolved",
        actor_type="reconciliation",
        operator_id=operator_id,
        from_status="started",
        to_status="resolved",
        resolution=resolution,
        reason_code=f"synthetic_webhook_{resolution}",
        evidence_id=evidence_id,
        payload={"action_status": target.value},
    )
    mark_webhook_processed(
        conn,
        webhook_id=webhook_id,
        resolution=resolution,
    )
    return reconciliation_snapshot(
        conn,
        reconciliation_id=reconciliation_id,
    )


__all__ = [
    "RTM_CONNECT_C4_RECONCILIATION_VERSION",
    "ReconciliationInProgress",
    "ReconciliationStateError",
    "ReconciliationWorkflowError",
    "WebhookReconciliationOutcome",
    "reconcile_webhook",
    "reconciliation_snapshot",
]
