"""Bandeja idempotente de webhooks sintéticos de RTM CONNECT C4.

La bandeja recibe entregas ya normalizadas por el adaptador C4, congela su
identidad y solo las correlaciona por action/attempt/hash/referencia exactos.
No publica endpoints ni usa red.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import timezone
from typing import Any, Mapping

from sqlalchemy import text

from rtm_connect.connectors.synthetic_webhook import (
    SYNTHETIC_WEBHOOK_CAPABILITY,
    SYNTHETIC_WEBHOOK_CODE,
    SYNTHETIC_WEBHOOK_CONNECTOR_VERSION,
    SYNTHETIC_WEBHOOK_MANIFEST_SHA256,
    SyntheticWebhookConnector,
    SyntheticWebhookDelivery,
    SyntheticWebhookOutcome,
)
from rtm_connect.contracts import ConnectorMode, RiskClass
from rtm_connect.idempotency import canonical_json
from rtm_connect.kernel import register_synthetic_connector


RTM_CONNECT_C4_WEBHOOK_INBOX_VERSION = (
    "rtm_connect_c4_webhook_inbox_v1_0"
)
_REASON_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")


class WebhookWorkflowError(RuntimeError):
    pass


class WebhookReplayConflict(WebhookWorkflowError):
    pass


class WebhookStateError(WebhookWorkflowError):
    pass


class WebhookMatchError(WebhookWorkflowError):
    pass


@dataclass(frozen=True)
class WebhookIntakeOutcome:
    webhook_id: str
    deduplication_key: str
    status: str
    payload_sha256: str
    replay_count: int
    replayed: bool


@dataclass(frozen=True)
class WebhookSnapshot:
    webhook_id: str
    status: str
    source_event_id: str
    action_id: str | None
    attempt_id: str | None
    reported_outcome: str
    payload_sha256: str
    replay_count: int
    version: int
    events: int
    dead_letter_reason_code: str | None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _deduplication_key(ingress_connector_id: str, event_id: str) -> str:
    digest = hashlib.sha256(
        canonical_json(
            {
                "ingress_connector_id": str(ingress_connector_id),
                "source_event_id": str(event_id),
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"rtmwh1:{digest}"


def register_synthetic_webhook_connector(conn):
    """Registra el adaptador solo dentro de la transacción llamante."""

    return register_synthetic_connector(
        conn,
        code=SYNTHETIC_WEBHOOK_CODE,
        version=SYNTHETIC_WEBHOOK_CONNECTOR_VERSION,
        mode=ConnectorMode.WEBHOOK,
        capabilities=(SYNTHETIC_WEBHOOK_CAPABILITY,),
        risk_ceiling=RiskClass.R4_CRITICAL_REGULATED,
        supports_reconciliation=True,
        configuration={
            "runtime_version": RTM_CONNECT_C4_WEBHOOK_INBOX_VERSION,
            "manifest_sha256": SYNTHETIC_WEBHOOK_MANIFEST_SHA256,
            "synthetic_only": True,
            "network_used": False,
            "external_effects": False,
            "integrity_proof_is_provider_signature": False,
        },
    )


def _inbox_row(conn, webhook_id: str, *, for_update: bool = False):
    suffix = " FOR UPDATE" if for_update else ""
    row = conn.execute(
        text(
            f"""
            SELECT * FROM rtm_connect_webhook_inbox
            WHERE id=CAST(:webhook_id AS UUID)
            {suffix}
            """
        ),
        {"webhook_id": webhook_id},
    ).mappings().first()
    if not row:
        raise LookupError("Webhook RTM CONNECT no encontrado")
    return row


def _append_webhook_event(
    conn,
    *,
    webhook_id: str,
    event_type: str,
    actor_type: str,
    from_status: str | None,
    to_status: str | None,
    reason_code: str,
    reason_detail: str | None = None,
    action_id: str | None = None,
    attempt_id: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> str:
    conn.execute(
        text(
            """
            SELECT id FROM rtm_connect_webhook_inbox
            WHERE id=CAST(:webhook_id AS UUID)
            FOR UPDATE
            """
        ),
        {"webhook_id": webhook_id},
    ).one()
    sequence = int(
        conn.execute(
            text(
                """
                SELECT COALESCE(MAX(sequence_number), 0) + 1
                FROM rtm_connect_webhook_events
                WHERE webhook_inbox_id=CAST(:webhook_id AS UUID)
                """
            ),
            {"webhook_id": webhook_id},
        ).scalar_one()
    )
    event_id = str(uuid.uuid4())
    conn.execute(
        text(
            """
            INSERT INTO rtm_connect_webhook_events(
                id, webhook_inbox_id, action_id, attempt_id,
                sequence_number, event_type, actor_type, operator_id,
                from_status, to_status, reason_code, reason_detail,
                payload, created_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:webhook_id AS UUID),
                CAST(:action_id AS UUID), CAST(:attempt_id AS UUID),
                :sequence_number, :event_type, :actor_type, NULL,
                :from_status, :to_status, :reason_code, :reason_detail,
                CAST(:payload AS JSONB), NOW()
            )
            """
        ),
        {
            "id": event_id,
            "webhook_id": webhook_id,
            "action_id": action_id,
            "attempt_id": attempt_id,
            "sequence_number": sequence,
            "event_type": event_type,
            "actor_type": actor_type,
            "from_status": from_status,
            "to_status": to_status,
            "reason_code": reason_code,
            "reason_detail": reason_detail,
            "payload": _json(dict(payload or {})),
        },
    )
    return event_id


def _transition_webhook(
    conn,
    *,
    webhook_id: str,
    target_status: str,
    reason_code: str,
    reason_detail: str | None = None,
    action_id: str | None = None,
    attempt_id: str | None = None,
    event_payload: Mapping[str, Any] | None = None,
) -> bool:
    row = _inbox_row(conn, webhook_id, for_update=True)
    current = str(row["status"])
    if current == target_status:
        return False
    allowed = {
        "received": {"verified", "dead_lettered"},
        "verified": {"matched", "dead_lettered"},
        "matched": {"processed", "dead_lettered"},
        "processed": set(),
        "dead_lettered": set(),
    }
    if target_status not in allowed.get(current, set()):
        raise WebhookStateError(
            f"Transición webhook no permitida: {current} -> {target_status}"
        )
    updates = [
        "status=:target_status",
        "version=version+1",
        "updated_at=NOW()",
    ]
    params: dict[str, Any] = {
        "webhook_id": webhook_id,
        "target_status": target_status,
    }
    if target_status == "matched":
        if not action_id or not attempt_id:
            raise ValueError("matched exige action_id y attempt_id")
        updates.extend(
            [
                "matched_action_id=CAST(:action_id AS UUID)",
                "matched_attempt_id=CAST(:attempt_id AS UUID)",
                "matched_at=NOW()",
            ]
        )
        params.update({"action_id": action_id, "attempt_id": attempt_id})
    elif target_status == "processed":
        updates.append("processed_at=NOW()")
    elif target_status == "dead_lettered":
        clean_reason = str(reason_code or "").strip().lower()
        if not _REASON_RE.fullmatch(clean_reason):
            raise ValueError("reason_code de dead letter no válido")
        updates.extend(
            [
                "processed_at=NOW()",
                "dead_letter_reason_code=:dead_reason",
                "dead_letter_reason_detail=:dead_detail",
            ]
        )
        params.update(
            {"dead_reason": clean_reason, "dead_detail": reason_detail}
        )
    conn.execute(
        text(
            f"""
            UPDATE rtm_connect_webhook_inbox
            SET {", ".join(updates)}
            WHERE id=CAST(:webhook_id AS UUID)
            """
        ),
        params,
    )
    effective_action = action_id or (
        str(row["matched_action_id"])
        if row["matched_action_id"] else None
    )
    effective_attempt = attempt_id or (
        str(row["matched_attempt_id"])
        if row["matched_attempt_id"] else None
    )
    _append_webhook_event(
        conn,
        webhook_id=webhook_id,
        action_id=effective_action,
        attempt_id=effective_attempt,
        event_type=f"webhook.{target_status}",
        actor_type="connect",
        from_status=current,
        to_status=target_status,
        reason_code=reason_code,
        reason_detail=reason_detail,
        payload=event_payload,
    )
    return True


def receive_synthetic_webhook(
    conn,
    *,
    ingress_connector_id: str,
    delivery: SyntheticWebhookDelivery,
) -> WebhookIntakeOutcome:
    """Verifica el contrato sintético y reclama de forma idempotente el evento."""

    observation = SyntheticWebhookConnector().verify_delivery(delivery)
    connector = conn.execute(
        text(
            """
            SELECT id, code, version, mode, status, environment,
                   synthetic_only, capabilities, risk_ceiling,
                   supports_idempotency, supports_reconciliation,
                   credential_ref, configuration
            FROM rtm_connect_connectors
            WHERE id=CAST(:connector_id AS UUID)
            """
        ),
        {"connector_id": ingress_connector_id},
    ).mappings().first()
    if not connector:
        raise LookupError("Conector webhook de entrada no encontrado")
    expected_configuration = {
        "runtime_version": RTM_CONNECT_C4_WEBHOOK_INBOX_VERSION,
        "manifest_sha256": SYNTHETIC_WEBHOOK_MANIFEST_SHA256,
        "synthetic_only": True,
        "network_used": False,
        "external_effects": False,
        "integrity_proof_is_provider_signature": False,
    }
    if (
        str(connector["code"]) != SYNTHETIC_WEBHOOK_CODE
        or str(connector["version"]) != SYNTHETIC_WEBHOOK_CONNECTOR_VERSION
        or str(connector["mode"]) != ConnectorMode.WEBHOOK.value
        or str(connector["status"]) != "active"
        or str(connector["environment"]) != "staging"
        or not bool(connector["synthetic_only"])
        or set(connector["capabilities"] or [])
        != {SYNTHETIC_WEBHOOK_CAPABILITY}
        or str(connector["risk_ceiling"])
        != RiskClass.R4_CRITICAL_REGULATED.value
        or not bool(connector["supports_idempotency"])
        or not bool(connector["supports_reconciliation"])
        or connector["credential_ref"] is not None
        or dict(connector["configuration"] or {})
        != expected_configuration
    ):
        raise WebhookWorkflowError(
            "El conector de entrada no coincide con synthetic.webhook"
        )
    if (
        delivery.ingress_connector_code != str(connector["code"])
        or delivery.ingress_connector_version != str(connector["version"])
    ):
        raise WebhookWorkflowError(
            "La entrega declara otro conector de entrada"
        )

    key = _deduplication_key(ingress_connector_id, delivery.event_id)
    webhook_id = str(uuid.uuid4())
    row = conn.execute(
        text(
            """
            INSERT INTO rtm_connect_webhook_inbox(
                id, ingress_connector_id, deduplication_key,
                source_event_id, origin_connector_code,
                origin_connector_version, event_type, reported_outcome,
                claimed_action_id, claimed_attempt_id,
                matched_action_id, matched_attempt_id,
                external_reference, request_sha256, payload, payload_sha256,
                verification_method, verification_sha256,
                receipt_sha256, receipt_storage_ref, occurred_at,
                received_at, status, replay_count, last_seen_at, version,
                metadata, created_at, updated_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:ingress_connector_id AS UUID),
                :deduplication_key, :source_event_id,
                :origin_connector_code, :origin_connector_version,
                'synthetic.reconciliation.observed', :reported_outcome,
                CAST(:claimed_action_id AS UUID),
                CAST(:claimed_attempt_id AS UUID), NULL, NULL,
                :external_reference, :request_sha256,
                CAST(:payload AS JSONB), :payload_sha256,
                :verification_method, :verification_sha256,
                :receipt_sha256, :receipt_storage_ref, :occurred_at,
                NOW(), 'received', 0, NOW(), 1,
                CAST(:metadata AS JSONB), NOW(), NOW()
            )
            ON CONFLICT (deduplication_key) DO NOTHING
            RETURNING id
            """
        ),
        {
            "id": webhook_id,
            "ingress_connector_id": ingress_connector_id,
            "deduplication_key": key,
            "source_event_id": delivery.event_id,
            "origin_connector_code": delivery.origin_connector_code,
            "origin_connector_version": delivery.origin_connector_version,
            "reported_outcome": delivery.outcome.value,
            "claimed_action_id": delivery.action_id,
            "claimed_attempt_id": delivery.attempt_id,
            "external_reference": delivery.external_reference,
            "request_sha256": delivery.request_sha256,
            "payload": _json(dict(delivery.normalized_payload)),
            "payload_sha256": delivery.delivery_sha256,
            "verification_method": observation.verification_method,
            "verification_sha256": delivery.integrity_proof_sha256,
            "receipt_sha256": delivery.receipt_sha256,
            "receipt_storage_ref": delivery.receipt_storage_ref,
            "occurred_at": delivery.observed_at,
            "metadata": _json(
                {
                    "runtime_version": RTM_CONNECT_C4_WEBHOOK_INBOX_VERSION,
                    "synthetic_only": True,
                    "network_used": False,
                    "external_effects_executed": False,
                    "provider_signature_verified": False,
                }
            ),
        },
    ).first()
    if row:
        _append_webhook_event(
            conn,
            webhook_id=webhook_id,
            event_type="webhook.received",
            actor_type="connect",
            from_status=None,
            to_status="received",
            reason_code="synthetic_delivery_received",
            payload={"source_event_id": delivery.event_id},
        )
        return WebhookIntakeOutcome(
            webhook_id=webhook_id,
            deduplication_key=key,
            status="received",
            payload_sha256=delivery.delivery_sha256,
            replay_count=0,
            replayed=False,
        )

    existing = conn.execute(
        text(
            """
            SELECT * FROM rtm_connect_webhook_inbox
            WHERE ingress_connector_id=CAST(:ingress_connector_id AS UUID)
              AND source_event_id=:source_event_id
            FOR UPDATE
            """
        ),
        {
            "ingress_connector_id": ingress_connector_id,
            "source_event_id": delivery.event_id,
        },
    ).mappings().first()
    if not existing:
        raise WebhookReplayConflict(
            "La identidad webhook colisionó con otra clave de deduplicación"
        )
    frozen = {
        "origin_connector_code": delivery.origin_connector_code,
        "origin_connector_version": delivery.origin_connector_version,
        "reported_outcome": delivery.outcome.value,
        "claimed_action_id": delivery.action_id,
        "claimed_attempt_id": delivery.attempt_id,
        "external_reference": delivery.external_reference,
        "request_sha256": delivery.request_sha256,
        "payload_sha256": delivery.delivery_sha256,
        "verification_sha256": delivery.integrity_proof_sha256,
        "receipt_sha256": delivery.receipt_sha256,
        "receipt_storage_ref": delivery.receipt_storage_ref,
    }
    for field, expected in frozen.items():
        actual = existing[field]
        if actual is not None:
            actual = str(actual)
        if expected is not None:
            expected = str(expected)
        if actual != expected:
            raise WebhookReplayConflict(
                "El mismo event_id intenta cambiar la entrega congelada"
            )
    conn.execute(
        text(
            """
            UPDATE rtm_connect_webhook_inbox
            SET replay_count=replay_count+1, version=version+1,
                last_seen_at=NOW(), updated_at=NOW()
            WHERE id=CAST(:webhook_id AS UUID)
            """
        ),
        {"webhook_id": str(existing["id"])},
    )
    return WebhookIntakeOutcome(
        webhook_id=str(existing["id"]),
        deduplication_key=str(existing["deduplication_key"]),
        status=str(existing["status"]),
        payload_sha256=str(existing["payload_sha256"]),
        replay_count=int(existing["replay_count"]) + 1,
        replayed=True,
    )


def verify_webhook(conn, *, webhook_id: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT i.*, c.code AS ingress_code,
                   c.version AS ingress_version
            FROM rtm_connect_webhook_inbox i
            JOIN rtm_connect_connectors c
              ON c.id=i.ingress_connector_id
            WHERE i.id=CAST(:webhook_id AS UUID)
            FOR UPDATE OF i
            """
        ),
        {"webhook_id": webhook_id},
    ).mappings().first()
    if not row:
        raise LookupError("Webhook RTM CONNECT no encontrado")
    if str(row["status"]) != "received":
        if str(row["status"]) in {"verified", "matched", "processed"}:
            return False
        raise WebhookStateError("El webhook no puede verificarse en su estado")
    reconstructed = SyntheticWebhookDelivery(
        event_id=str(row["source_event_id"]),
        observed_at=(
            row["occurred_at"]
            .astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        ),
        ingress_connector_code=str(row["ingress_code"]),
        ingress_connector_version=str(row["ingress_version"]),
        origin_connector_code=str(row["origin_connector_code"]),
        origin_connector_version=str(row["origin_connector_version"]),
        action_id=str(row["claimed_action_id"]),
        attempt_id=str(row["claimed_attempt_id"]),
        request_sha256=str(row["request_sha256"]),
        external_reference=str(row["external_reference"]),
        outcome=SyntheticWebhookOutcome(str(row["reported_outcome"])),
        normalized_payload=dict(row["payload"]),
        receipt_sha256=(
            str(row["receipt_sha256"])
            if row["receipt_sha256"] else None
        ),
        receipt_storage_ref=(
            str(row["receipt_storage_ref"])
            if row["receipt_storage_ref"] else None
        ),
        delivery_sha256=str(row["payload_sha256"]),
        integrity_proof_sha256=str(row["verification_sha256"]),
    )
    observation = SyntheticWebhookConnector().verify_delivery(reconstructed)
    if str(row["verification_method"]) != observation.verification_method:
        raise WebhookWorkflowError(
            "El método de verificación almacenado no coincide"
        )
    return _transition_webhook(
        conn,
        webhook_id=webhook_id,
        target_status="verified",
        reason_code="synthetic_integrity_verified",
        event_payload={
            "verification_method": "synthetic_integrity_hash_v1",
            "provider_signature_verified": False,
        },
    )


def match_webhook(conn, *, webhook_id: str) -> bool:
    row = _inbox_row(conn, webhook_id, for_update=True)
    if str(row["status"]) == "matched":
        return False
    if str(row["status"]) != "verified":
        raise WebhookStateError("Solo un webhook verified puede correlacionarse")
    attempt = conn.execute(
        text(
            """
            SELECT x.id, x.action_id, x.status AS attempt_status,
                   x.request_sha256, x.external_reference,
                   x.reconciliation_required,
                   a.status AS action_status,
                   c.id AS origin_connector_id, c.code AS connector_code,
                   c.version AS connector_version,
                   c.status AS connector_status,
                   c.environment AS connector_environment,
                   c.synthetic_only AS connector_synthetic_only,
                   c.credential_ref AS connector_credential_ref,
                   c.supports_reconciliation
            FROM rtm_connect_attempts x
            JOIN rtm_connect_actions a ON a.id=x.action_id
            JOIN rtm_connect_connectors c ON c.id=x.connector_id
            WHERE x.id=CAST(:attempt_id AS UUID)
            FOR UPDATE OF x, a
            """
        ),
        {"attempt_id": str(row["claimed_attempt_id"])},
    ).mappings().first()
    problems: list[str] = []
    if not attempt:
        problems.append("attempt_not_found")
    else:
        exact = {
            "action_id": (
                str(attempt["action_id"]), str(row["claimed_action_id"])
            ),
            "origin_connector_code": (
                str(attempt["connector_code"]),
                str(row["origin_connector_code"]),
            ),
            "origin_connector_version": (
                str(attempt["connector_version"]),
                str(row["origin_connector_version"]),
            ),
            "request_sha256": (
                str(attempt["request_sha256"]), str(row["request_sha256"])
            ),
            "external_reference": (
                str(attempt["external_reference"]),
                str(row["external_reference"]),
            ),
        }
        problems.extend(
            f"{name}_mismatch"
            for name, (actual, claimed) in exact.items()
            if actual != claimed
        )
        if str(attempt["attempt_status"]) != "unknown":
            problems.append("attempt_not_unknown")
        if str(attempt["action_status"]) != "unknown":
            problems.append("action_not_unknown")
        if not bool(attempt["reconciliation_required"]):
            problems.append("reconciliation_not_required")
        if not bool(attempt["supports_reconciliation"]):
            problems.append("origin_connector_not_reconcilable")
        if str(attempt["connector_status"]) != "active":
            problems.append("origin_connector_not_active")
        if str(attempt["connector_environment"]) != "staging":
            problems.append("origin_connector_not_staging")
        if not bool(attempt["connector_synthetic_only"]):
            problems.append("origin_connector_not_synthetic")
        if attempt["connector_credential_ref"] is not None:
            problems.append("origin_connector_has_credentials")
        if str(attempt["origin_connector_id"]) == str(
            row["ingress_connector_id"]
        ):
            problems.append("ingress_is_origin_connector")
    if problems:
        raise WebhookMatchError(
            "Webhook no correlacionable: " + ",".join(problems)
        )
    return _transition_webhook(
        conn,
        webhook_id=webhook_id,
        target_status="matched",
        reason_code="exact_scope_matched",
        action_id=str(attempt["action_id"]),
        attempt_id=str(attempt["id"]),
        event_payload={
            "origin_connector_code": str(attempt["connector_code"]),
            "origin_connector_version": str(attempt["connector_version"]),
        },
    )


def dead_letter_webhook(
    conn,
    *,
    webhook_id: str,
    reason_code: str,
    reason_detail: str | None = None,
) -> bool:
    return _transition_webhook(
        conn,
        webhook_id=webhook_id,
        target_status="dead_lettered",
        reason_code=reason_code,
        reason_detail=reason_detail,
    )


def mark_webhook_processed(
    conn,
    *,
    webhook_id: str,
    resolution: str,
) -> bool:
    return _transition_webhook(
        conn,
        webhook_id=webhook_id,
        target_status="processed",
        reason_code="reconciliation_processed",
        event_payload={"resolution": resolution},
    )


def webhook_snapshot(conn, *, webhook_id: str) -> WebhookSnapshot:
    row = conn.execute(
        text(
            """
            SELECT i.*,
                   (SELECT COUNT(*) FROM rtm_connect_webhook_events e
                    WHERE e.webhook_inbox_id=i.id) AS events
            FROM rtm_connect_webhook_inbox i
            WHERE i.id=CAST(:webhook_id AS UUID)
            """
        ),
        {"webhook_id": webhook_id},
    ).mappings().first()
    if not row:
        raise LookupError("Webhook RTM CONNECT no encontrado")
    return WebhookSnapshot(
        webhook_id=str(row["id"]),
        status=str(row["status"]),
        source_event_id=str(row["source_event_id"]),
        action_id=(
            str(row["matched_action_id"])
            if row["matched_action_id"] else None
        ),
        attempt_id=(
            str(row["matched_attempt_id"])
            if row["matched_attempt_id"] else None
        ),
        reported_outcome=str(row["reported_outcome"]),
        payload_sha256=str(row["payload_sha256"]),
        replay_count=int(row["replay_count"]),
        version=int(row["version"]),
        events=int(row["events"]),
        dead_letter_reason_code=(
            str(row["dead_letter_reason_code"])
            if row["dead_letter_reason_code"] else None
        ),
    )


__all__ = [
    "RTM_CONNECT_C4_WEBHOOK_INBOX_VERSION",
    "WebhookIntakeOutcome",
    "WebhookMatchError",
    "WebhookReplayConflict",
    "WebhookSnapshot",
    "WebhookStateError",
    "WebhookWorkflowError",
    "dead_letter_webhook",
    "mark_webhook_processed",
    "match_webhook",
    "receive_synthetic_webhook",
    "register_synthetic_webhook_connector",
    "verify_webhook",
    "webhook_snapshot",
]
