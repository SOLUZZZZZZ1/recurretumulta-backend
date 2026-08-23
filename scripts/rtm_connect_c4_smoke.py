#!/usr/bin/env python3
"""Smoke transaccional y sin red de RTM CONNECT C4.

Prueba webhooks sintéticos, UNKNOWN → RECONCILING, evidencia E4,
deduplicación, conflicto, DLQ y reconciliación indeterminada. Toda la
transacción se revierte y una conexión nueva confirma residuo cero.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TRUE = {"1", "true", "yes", "on", "enabled"}
_FALSE = {"0", "false", "no", "off", "disabled"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    return parser


def _flag(name: str) -> bool | None:
    raw = (os.getenv(name) or "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def _blockers() -> list[str]:
    blockers: list[str] = []
    if (os.getenv("RTM_ENV") or "").strip().lower() != "staging":
        blockers.append("RTM_ENV_must_be_staging")
    if "staging" not in (os.getenv("RTM_DATA_NAMESPACE") or "").lower():
        blockers.append("RTM_DATA_NAMESPACE_must_identify_staging")
    if (os.getenv("RTM_SIDE_EFFECT_POLICY") or "").strip().lower() != "isolated":
        blockers.append("RTM_SIDE_EFFECT_POLICY_must_be_isolated")
    if _flag("RTM_ALLOW_REAL_CUSTOMER_DATA") is not False:
        blockers.append("RTM_ALLOW_REAL_CUSTOMER_DATA_must_be_false")
    for name in (
        "RTM_ENABLE_EXTERNAL_SUBMISSION",
        "RTM_ENABLE_OUTBOUND_EMAIL",
        "RTM_ENABLE_STRIPE",
        "RTM_ENABLE_FINAL_PAYMENTS",
    ):
        if _flag(name) is not False:
            blockers.append(f"{name}_must_be_false")
    return blockers


def _now(*, seconds_ago: int = 0) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    ).isoformat()


def _print(payload: dict[str, Any], compact: bool) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
            default=str,
        )
    )


def _grant(action, *, operator_id: str):
    from rtm_connect.contracts import (
        AuthorizationGrant,
        ConnectorMode,
        EvidenceLevel,
    )
    from rtm_connect.idempotency import (
        derive_idempotency_key,
        payload_sha256,
    )

    return AuthorizationGrant(
        authorization_id=str(uuid.uuid4()),
        action_id=action.action_id,
        authority_code="rtm.core.authorization",
        authority_version="rtm_core_authority_v1",
        decision="approved_frozen",
        payload_sha256=payload_sha256(action),
        idempotency_key=derive_idempotency_key(
            action,
            authority_scope="rtm.core.authorization",
        ),
        required_evidence_level=EvidenceLevel.E4_RECEIPT_VERIFIED,
        authorized_connector_modes=(ConnectorMode.API,),
        approved_by_operator_ids=(operator_id,),
        authorized_at=_now(),
        expires_at=(
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
        legal_effect_authorized=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_connect_c4_smoke",
        "version": "rtm_connect_c4_smoke_v1_0",
        "environment": (
            os.getenv("RTM_ENV") or ""
        ).strip().lower() or "unset",
        "synthetic_only": True,
        "transactional": True,
        "network_used": False,
        "routes_published": False,
        "schema_changes_applied": False,
        "external_effects_executed": False,
        "checks": {},
        "cleanup": {
            "database_rolled_back": False,
            "error": None,
        },
        "synthetic_ids": {},
    }
    blockers = _blockers()
    if blockers:
        report["blockers"] = blockers
        _print(report, args.compact)
        return 2

    ids: dict[str, str] = {}
    action_ids: list[str] = []
    webhook_ids: list[str] = []
    reconciliation_ids: list[str] = []
    try:
        from sqlalchemy import text
        from database import get_engine
        from rtm_connect.connectors.synthetic_echo import (
            SyntheticEchoScenario,
        )
        from rtm_connect.connectors.synthetic_webhook import (
            SyntheticWebhookConnector,
            SyntheticWebhookIntegrityError,
            SyntheticWebhookOutcome,
        )
        from rtm_connect.contracts import (
            ConnectActionRequest,
            RiskClass,
        )
        from rtm_connect.execution import (
            ExistingActionReplayBlocked,
            execute_synthetic_echo,
        )
        from rtm_connect.idempotency import payload_sha256
        from rtm_connect.reconciliation import reconcile_webhook
        from rtm_connect.webhooks import (
            WebhookMatchError,
            WebhookReplayConflict,
            dead_letter_webhook,
            match_webhook,
            receive_synthetic_webhook,
            register_synthetic_webhook_connector,
            verify_webhook,
            webhook_snapshot,
        )

        engine = get_engine()
        connection = engine.connect()
        transaction = connection.begin()
        try:
            suffix = uuid.uuid4().hex[:12]
            role_id = str(uuid.uuid4())
            operator_id = str(uuid.uuid4())
            ids.update({"role_id": role_id, "operator_id": operator_id})
            connection.execute(
                text(
                    """
                    INSERT INTO rtm_operator_roles(
                        id, code, name, permissions,
                        system_role, active, created_at, updated_at
                    ) VALUES (
                        CAST(:id AS UUID), :code, :name,
                        '["ops.view", "ops.supervise"]'::jsonb,
                        FALSE, TRUE, NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": role_id,
                    "code": f"synthetic.connect.c4.{suffix}",
                    "name": "RTM CONNECT C4 SMOKE",
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO rtm_operators(
                        id, email, display_name, password_hash, status,
                        primary_role_id, must_change_password,
                        mfa_required, profile, failed_login_count,
                        password_algorithm, password_version,
                        auth_epoch, created_at, updated_at
                    ) VALUES (
                        CAST(:id AS UUID), :email, :display_name,
                        NULL, 'active', CAST(:role_id AS UUID),
                        FALSE, FALSE,
                        '{"synthetic": true,
                          "purpose": "connect_c4_smoke"}'::jsonb,
                        0, 'argon2id', 1, 1, NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": operator_id,
                    "email": (
                        f"rtm-staging-connect-c4-{suffix}@example.com"
                    ),
                    "display_name": "RTM CONNECT C4 SMOKE",
                    "role_id": role_id,
                },
            )
            report["checks"]["synthetic_operator_inserted"] = True

            def create_unknown(label: str, sequence: int):
                action_id = str(uuid.uuid4())
                action_ids.append(action_id)
                action = ConnectActionRequest(
                    action_id=action_id,
                    capability="synthetic.echo",
                    satellite="synthetic",
                    target_type="synthetic.endpoint",
                    target_ref=f"c4-{label}",
                    payload={
                        "message": f"RTM CONNECT C4 {label}",
                        "sequence": sequence,
                    },
                    requested_by_operator_id=operator_id,
                    requested_at=_now(),
                    risk_class=RiskClass.R3_LEGAL_OR_FINANCIAL,
                )
                outcome = execute_synthetic_echo(
                    connection,
                    action=action,
                    grant=_grant(action, operator_id=operator_id),
                    scenario=SyntheticEchoScenario.UNKNOWN,
                    operator_id=operator_id,
                )
                return action, outcome

            confirmed_action, unknown = create_unknown("confirmed", 1)
            ids["confirmed_action_id"] = confirmed_action.action_id
            ids["confirmed_attempt_id"] = str(unknown.attempt_id)
            ids["echo_connector_id"] = unknown.connector_id
            report["checks"]["c2_action_persisted_unknown"] = (
                unknown.status == "unknown"
                and not unknown.confirmed
                and unknown.attempt_id is not None
                and unknown.attempts == 1
                and unknown.evidence_level == "E2_external_reference"
            )
            blind_retry_blocked = False
            try:
                execute_synthetic_echo(
                    connection,
                    action=confirmed_action,
                    grant=_grant(
                        confirmed_action,
                        operator_id=operator_id,
                    ),
                    scenario=SyntheticEchoScenario.UNKNOWN,
                    operator_id=operator_id,
                )
            except ExistingActionReplayBlocked:
                blind_retry_blocked = True
            report["checks"]["unknown_blind_retry_blocked"] = (
                blind_retry_blocked
            )

            origin = connection.execute(
                text(
                    """
                    SELECT x.id AS attempt_id, x.request_sha256,
                           x.external_reference, x.status,
                           x.reconciliation_required,
                           c.code, c.version
                    FROM rtm_connect_attempts x
                    JOIN rtm_connect_connectors c ON c.id=x.connector_id
                    WHERE x.id=CAST(:attempt_id AS UUID)
                    """
                ),
                {"attempt_id": unknown.attempt_id},
            ).mappings().one()
            webhook_connector = register_synthetic_webhook_connector(
                connection
            )
            ids["webhook_connector_id"] = webhook_connector.connector_id
            report["checks"]["synthetic_webhook_registered_transactionally"] = (
                webhook_connector.connector_id != unknown.connector_id
            )

            adapter = SyntheticWebhookConnector()
            confirmed_delivery = adapter.build_delivery(
                event_key=f"confirmed-{suffix}",
                observed_at=_now(seconds_ago=1),
                origin_connector_code=str(origin["code"]),
                origin_connector_version=str(origin["version"]),
                action_id=confirmed_action.action_id,
                attempt_id=str(unknown.attempt_id),
                request_sha256=payload_sha256(confirmed_action),
                external_reference=str(unknown.external_reference),
                outcome=SyntheticWebhookOutcome.CONFIRMED,
                normalized_payload={
                    "provider_state": "synthetic_confirmed",
                    "sequence": 1,
                },
                receipt_sha256="c" * 64,
                receipt_storage_ref=(
                    f"synthetic://webhook/{confirmed_action.action_id}/receipt"
                ),
            )
            intake = receive_synthetic_webhook(
                connection,
                ingress_connector_id=webhook_connector.connector_id,
                delivery=confirmed_delivery,
            )
            webhook_ids.append(intake.webhook_id)
            ids["confirmed_webhook_id"] = intake.webhook_id
            verified = verify_webhook(
                connection, webhook_id=intake.webhook_id
            )
            connection.execute(
                text(
                    """
                    UPDATE rtm_connect_connectors
                    SET synthetic_only=FALSE, updated_at=NOW()
                    WHERE id=CAST(:connector_id AS UUID)
                    """
                ),
                {"connector_id": unknown.connector_id},
            )
            non_synthetic_origin_blocked = False
            try:
                match_webhook(
                    connection, webhook_id=intake.webhook_id
                )
            except WebhookMatchError:
                non_synthetic_origin_blocked = True
            connection.execute(
                text(
                    """
                    UPDATE rtm_connect_connectors
                    SET synthetic_only=TRUE, updated_at=NOW()
                    WHERE id=CAST(:connector_id AS UUID)
                    """
                ),
                {"connector_id": unknown.connector_id},
            )
            report["checks"]["non_synthetic_origin_blocked"] = (
                non_synthetic_origin_blocked
            )
            matched = match_webhook(
                connection, webhook_id=intake.webhook_id
            )
            report["checks"]["verified_webhook_exactly_correlated"] = (
                verified and matched
            )
            reconciled = reconcile_webhook(
                connection,
                webhook_id=intake.webhook_id,
                operator_id=operator_id,
            )
            reconciliation_ids.append(reconciled.reconciliation_id)
            ids["confirmed_reconciliation_id"] = (
                reconciled.reconciliation_id
            )
            report["checks"]["unknown_reconciled_confirmed_after_e4"] = (
                reconciled.status == "resolved"
                and reconciled.resolution == "confirmed"
                and reconciled.action_status == "confirmed"
                and reconciled.evidence_id is not None
                and reconciled.attempts == 1
                and not reconciled.reconciliation_required
            )
            confirmed_attempt = connection.execute(
                text(
                    """
                    SELECT status, reconciliation_required
                    FROM rtm_connect_attempts
                    WHERE id=CAST(:attempt_id AS UUID)
                    """
                ),
                {"attempt_id": unknown.attempt_id},
            ).mappings().one()
            report["checks"]["confirmed_closed_original_attempt"] = (
                str(confirmed_attempt["status"]) == "succeeded"
                and not bool(confirmed_attempt["reconciliation_required"])
            )

            events_before_replay = webhook_snapshot(
                connection, webhook_id=intake.webhook_id
            ).events
            replay = receive_synthetic_webhook(
                connection,
                ingress_connector_id=webhook_connector.connector_id,
                delivery=confirmed_delivery,
            )
            reconcile_replay = reconcile_webhook(
                connection,
                webhook_id=intake.webhook_id,
                operator_id=operator_id,
            )
            events_after_replay = webhook_snapshot(
                connection, webhook_id=intake.webhook_id
            ).events
            report["checks"]["exact_replay_reused_without_duplicates"] = (
                replay.replayed
                and replay.webhook_id == intake.webhook_id
                and replay.replay_count == 1
                and reconcile_replay.replayed
                and reconcile_replay.reconciliation_id
                == reconciled.reconciliation_id
                and events_after_replay == events_before_replay
                and reconcile_replay.events == reconciled.events
                and reconcile_replay.attempts == 1
            )

            changed_delivery = adapter.build_delivery(
                event_key=f"confirmed-{suffix}",
                observed_at=confirmed_delivery.observed_at,
                origin_connector_code=str(origin["code"]),
                origin_connector_version=str(origin["version"]),
                action_id=confirmed_action.action_id,
                attempt_id=str(unknown.attempt_id),
                request_sha256=payload_sha256(confirmed_action),
                external_reference=str(unknown.external_reference),
                outcome=SyntheticWebhookOutcome.CONFIRMED,
                normalized_payload={
                    "provider_state": "changed_payload",
                    "sequence": 999,
                },
                receipt_sha256="c" * 64,
                receipt_storage_ref=(
                    f"synthetic://webhook/{confirmed_action.action_id}/receipt"
                ),
            )
            conflict_blocked = False
            try:
                receive_synthetic_webhook(
                    connection,
                    ingress_connector_id=webhook_connector.connector_id,
                    delivery=changed_delivery,
                )
            except WebhookReplayConflict:
                conflict_blocked = True
            report["checks"]["changed_replay_conflict_blocked"] = (
                conflict_blocked
                and changed_delivery.event_id == confirmed_delivery.event_id
            )
            tamper_blocked = False
            try:
                receive_synthetic_webhook(
                    connection,
                    ingress_connector_id=webhook_connector.connector_id,
                    delivery=confirmed_delivery.with_changes(
                        normalized_payload={"tampered": True}
                    ),
                )
            except SyntheticWebhookIntegrityError:
                tamper_blocked = True
            report["checks"]["delivery_tampering_blocked"] = tamper_blocked

            unknown_action, indeterminate = create_unknown(
                "indeterminate", 2
            )
            ids["indeterminate_action_id"] = unknown_action.action_id
            ids["indeterminate_attempt_id"] = str(indeterminate.attempt_id)
            indeterminate_origin = connection.execute(
                text(
                    """
                    SELECT x.request_sha256, x.external_reference,
                           c.code, c.version
                    FROM rtm_connect_attempts x
                    JOIN rtm_connect_connectors c ON c.id=x.connector_id
                    WHERE x.id=CAST(:attempt_id AS UUID)
                    """
                ),
                {"attempt_id": indeterminate.attempt_id},
            ).mappings().one()
            indeterminate_delivery = adapter.build_delivery(
                event_key=f"indeterminate-{suffix}",
                observed_at=_now(seconds_ago=1),
                origin_connector_code=str(indeterminate_origin["code"]),
                origin_connector_version=str(indeterminate_origin["version"]),
                action_id=unknown_action.action_id,
                attempt_id=str(indeterminate.attempt_id),
                request_sha256=payload_sha256(unknown_action),
                external_reference=str(indeterminate.external_reference),
                outcome=SyntheticWebhookOutcome.UNKNOWN,
                normalized_payload={
                    "provider_state": "still_indeterminate",
                    "sequence": 2,
                },
            )
            indeterminate_intake = receive_synthetic_webhook(
                connection,
                ingress_connector_id=webhook_connector.connector_id,
                delivery=indeterminate_delivery,
            )
            webhook_ids.append(indeterminate_intake.webhook_id)
            verify_webhook(
                connection, webhook_id=indeterminate_intake.webhook_id
            )
            match_webhook(
                connection, webhook_id=indeterminate_intake.webhook_id
            )
            still_unknown = reconcile_webhook(
                connection,
                webhook_id=indeterminate_intake.webhook_id,
                operator_id=operator_id,
            )
            reconciliation_ids.append(still_unknown.reconciliation_id)
            report["checks"]["indeterminate_reconciliation_stays_unknown"] = (
                still_unknown.resolution == "unknown"
                and still_unknown.action_status == "unknown"
                and still_unknown.reconciliation_required
                and still_unknown.attempts == 1
                and still_unknown.evidence_id is None
            )

            fake_action_id = str(uuid.uuid4())
            fake_attempt_id = str(uuid.uuid4())
            unmatched_delivery = adapter.build_delivery(
                event_key=f"unmatched-{suffix}",
                observed_at=_now(seconds_ago=1),
                origin_connector_code="synthetic.echo",
                origin_connector_version="v1.0",
                action_id=fake_action_id,
                attempt_id=fake_attempt_id,
                request_sha256="d" * 64,
                external_reference=f"SYN-C4-MISSING-{suffix.upper()}",
                outcome=SyntheticWebhookOutcome.UNKNOWN,
                normalized_payload={"provider_state": "unmatched"},
            )
            unmatched = receive_synthetic_webhook(
                connection,
                ingress_connector_id=webhook_connector.connector_id,
                delivery=unmatched_delivery,
            )
            webhook_ids.append(unmatched.webhook_id)
            verify_webhook(connection, webhook_id=unmatched.webhook_id)
            match_blocked = False
            try:
                match_webhook(connection, webhook_id=unmatched.webhook_id)
            except WebhookMatchError:
                match_blocked = True
            dead_lettered = dead_letter_webhook(
                connection,
                webhook_id=unmatched.webhook_id,
                reason_code="exact_match_not_found",
                reason_detail="synthetic unmatched delivery",
            )
            unmatched_snapshot = webhook_snapshot(
                connection, webhook_id=unmatched.webhook_id
            )
            report["checks"]["unmatched_webhook_dead_lettered"] = (
                match_blocked
                and dead_lettered
                and unmatched_snapshot.status == "dead_lettered"
                and unmatched_snapshot.dead_letter_reason_code
                == "exact_match_not_found"
            )

            confirmed_transitions = [
                str(row[0])
                for row in connection.execute(
                    text(
                        """
                        SELECT to_status
                        FROM rtm_connect_transitions
                        WHERE action_id=CAST(:action_id AS UUID)
                        ORDER BY sequence_number
                        """
                    ),
                    {"action_id": confirmed_action.action_id},
                ).fetchall()
            ]
            unknown_transitions = [
                str(row[0])
                for row in connection.execute(
                    text(
                        """
                        SELECT to_status
                        FROM rtm_connect_transitions
                        WHERE action_id=CAST(:action_id AS UUID)
                        ORDER BY sequence_number
                        """
                    ),
                    {"action_id": unknown_action.action_id},
                ).fetchall()
            ]
            report["checks"]["reconciliation_transition_ledgers_complete"] = (
                confirmed_transitions
                == [
                    "draft", "authorized", "queued", "executing",
                    "unknown", "reconciling", "confirmed",
                ]
                and unknown_transitions
                == [
                    "draft", "authorized", "queued", "executing",
                    "unknown", "reconciling", "unknown",
                ]
            )

            def trigger_blocks(sql: str, params: dict[str, Any]) -> bool:
                nested = connection.begin_nested()
                try:
                    connection.execute(text(sql), params)
                except Exception:
                    nested.rollback()
                    return True
                else:
                    nested.commit()
                    return False

            report["checks"]["webhook_identity_frozen"] = trigger_blocks(
                """
                UPDATE rtm_connect_webhook_inbox
                SET payload_sha256=:tampered, version=version+1
                WHERE id=CAST(:webhook_id AS UUID)
                """,
                {"tampered": "f" * 64, "webhook_id": intake.webhook_id},
            )
            report["checks"]["webhook_events_append_only"] = trigger_blocks(
                """
                UPDATE rtm_connect_webhook_events
                SET reason_code='tampered'
                WHERE webhook_inbox_id=CAST(:webhook_id AS UUID)
                """,
                {"webhook_id": intake.webhook_id},
            )
            report["checks"]["reconciliation_identity_frozen"] = (
                trigger_blocks(
                    """
                    UPDATE rtm_connect_reconciliations
                    SET request_sha256=:tampered, version=version+1
                    WHERE id=CAST(:reconciliation_id AS UUID)
                    """,
                    {
                        "tampered": "f" * 64,
                        "reconciliation_id": reconciled.reconciliation_id,
                    },
                )
            )
            report["checks"]["reconciliation_events_append_only"] = (
                trigger_blocks(
                    """
                    UPDATE rtm_connect_reconciliation_events
                    SET reason_code='tampered'
                    WHERE reconciliation_id=CAST(:reconciliation_id AS UUID)
                    """,
                    {"reconciliation_id": reconciled.reconciliation_id},
                )
            )
            report["checks"]["webhook_event_cross_scope_blocked"] = (
                trigger_blocks(
                    """
                    INSERT INTO rtm_connect_webhook_events(
                        id, webhook_inbox_id, action_id, attempt_id,
                        sequence_number, event_type, actor_type,
                        from_status, to_status, reason_code, payload
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:webhook_id AS UUID),
                        CAST(:wrong_action_id AS UUID),
                        CAST(:wrong_attempt_id AS UUID), 999,
                        'webhook.processed', 'system',
                        'matched', 'processed', 'cross_scope_probe',
                        '{}'::jsonb
                    )
                    """,
                    {
                        "id": str(uuid.uuid4()),
                        "webhook_id": intake.webhook_id,
                        "wrong_action_id": unknown_action.action_id,
                        "wrong_attempt_id": indeterminate.attempt_id,
                    },
                )
            )
            report["checks"][
                "reconciliation_event_cross_scope_blocked"
            ] = trigger_blocks(
                """
                INSERT INTO rtm_connect_reconciliation_events(
                    id, reconciliation_id, action_id, attempt_id,
                    webhook_inbox_id, sequence_number, event_type,
                    actor_type, from_status, to_status, resolution,
                    reason_code, evidence_id, payload
                ) VALUES (
                    CAST(:id AS UUID),
                    CAST(:reconciliation_id AS UUID),
                    CAST(:wrong_action_id AS UUID),
                    CAST(:wrong_attempt_id AS UUID),
                    CAST(:wrong_webhook_id AS UUID), 999,
                    'reconciliation.resolved', 'system',
                    'started', 'resolved', 'confirmed',
                    'cross_scope_probe', CAST(:evidence_id AS UUID),
                    '{}'::jsonb
                )
                """,
                {
                    "id": str(uuid.uuid4()),
                    "reconciliation_id": reconciled.reconciliation_id,
                    "wrong_action_id": unknown_action.action_id,
                    "wrong_attempt_id": indeterminate.attempt_id,
                    "wrong_webhook_id": indeterminate_intake.webhook_id,
                    "evidence_id": reconciled.evidence_id,
                },
            )

            counts = connection.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM rtm_connect_connectors
                         WHERE code IN ('synthetic.echo',
                                        'synthetic.webhook')) AS connectors,
                        (SELECT COUNT(*) FROM rtm_connect_attempts
                         WHERE action_id IN (
                             CAST(:confirmed_action_id AS UUID),
                             CAST(:unknown_action_id AS UUID)
                         )) AS attempts,
                        (SELECT COUNT(*) FROM rtm_connect_webhook_inbox
                         WHERE id IN (
                             CAST(:confirmed_webhook_id AS UUID),
                             CAST(:unknown_webhook_id AS UUID),
                             CAST(:unmatched_webhook_id AS UUID)
                         )) AS webhook_inbox,
                        (SELECT COUNT(*) FROM rtm_connect_reconciliations
                         WHERE id IN (
                             CAST(:confirmed_reconciliation_id AS UUID),
                             CAST(:unknown_reconciliation_id AS UUID)
                         )) AS reconciliations
                    """
                ),
                {
                    "confirmed_action_id": confirmed_action.action_id,
                    "unknown_action_id": unknown_action.action_id,
                    "confirmed_webhook_id": intake.webhook_id,
                    "unknown_webhook_id": indeterminate_intake.webhook_id,
                    "unmatched_webhook_id": unmatched.webhook_id,
                    "confirmed_reconciliation_id": reconciled.reconciliation_id,
                    "unknown_reconciliation_id": still_unknown.reconciliation_id,
                },
            ).mappings().one()
            report["checks"]["single_attempt_per_unknown_action"] = (
                int(counts["attempts"]) == 2
            )
            report["checks"]["synthetic_c4_records_transactional"] = (
                int(counts["connectors"]) == 2
                and int(counts["webhook_inbox"]) == 3
                and int(counts["reconciliations"]) == 2
            )
            report["checks"]["no_external_effects"] = (
                report["network_used"] is False
                and report["routes_published"] is False
                and report["schema_changes_applied"] is False
                and report["external_effects_executed"] is False
            )
            report["tests_ok"] = all(
                bool(value) for value in report["checks"].values()
            )
            report["ok"] = bool(report["tests_ok"])
        finally:
            transaction.rollback()
            connection.close()
            report["cleanup"]["database_rolled_back"] = True

        with engine.connect() as verification:
            remaining = verification.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM rtm_connect_actions
                         WHERE id IN (
                             CAST(:action_one AS UUID),
                             CAST(:action_two AS UUID)
                         )) AS actions,
                        (SELECT COUNT(*) FROM rtm_connect_connectors
                         WHERE id IN (
                             CAST(:echo_connector AS UUID),
                             CAST(:webhook_connector AS UUID)
                         )) AS connectors,
                        (SELECT COUNT(*) FROM rtm_connect_webhook_inbox
                         WHERE id IN (
                             CAST(:webhook_one AS UUID),
                             CAST(:webhook_two AS UUID),
                             CAST(:webhook_three AS UUID)
                         )) AS webhook_inbox,
                        (SELECT COUNT(*) FROM rtm_connect_webhook_events
                         WHERE webhook_inbox_id IN (
                             CAST(:webhook_one AS UUID),
                             CAST(:webhook_two AS UUID),
                             CAST(:webhook_three AS UUID)
                         )) AS webhook_events,
                        (SELECT COUNT(*) FROM rtm_connect_reconciliations
                         WHERE id IN (
                             CAST(:reconciliation_one AS UUID),
                             CAST(:reconciliation_two AS UUID)
                         )) AS reconciliations,
                        (SELECT COUNT(*)
                         FROM rtm_connect_reconciliation_events
                         WHERE reconciliation_id IN (
                             CAST(:reconciliation_one AS UUID),
                             CAST(:reconciliation_two AS UUID)
                         )) AS reconciliation_events,
                        (SELECT COUNT(*) FROM rtm_operators
                         WHERE id=CAST(:operator_id AS UUID)) AS operators,
                        (SELECT COUNT(*) FROM rtm_operator_roles
                         WHERE id=CAST(:role_id AS UUID)) AS roles
                    """
                ),
                {
                    "action_one": action_ids[0],
                    "action_two": action_ids[1],
                    "echo_connector": ids["echo_connector_id"],
                    "webhook_connector": ids["webhook_connector_id"],
                    "webhook_one": webhook_ids[0],
                    "webhook_two": webhook_ids[1],
                    "webhook_three": webhook_ids[2],
                    "reconciliation_one": reconciliation_ids[0],
                    "reconciliation_two": reconciliation_ids[1],
                    "operator_id": ids["operator_id"],
                    "role_id": ids["role_id"],
                },
            ).mappings().one()
        for key, value in remaining.items():
            report["cleanup"][f"synthetic_{key}_remaining"] = int(value)
        report["checks"]["rollback_removed_synthetic_records"] = all(
            int(value) == 0 for value in remaining.values()
        )
        report["tests_ok"] = all(
            bool(value) for value in report["checks"].values()
        )
        report["ok"] = bool(
            report["tests_ok"]
            and report["cleanup"]["database_rolled_back"]
        )
        report["synthetic_ids"] = ids
        code = 0 if report["ok"] else 1
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["tests_ok"] = False
        report["ok"] = False
        report["cleanup"]["error"] = str(exc)
        code = 1

    _print(report, args.compact)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
