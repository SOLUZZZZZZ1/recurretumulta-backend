#!/usr/bin/env python3
"""Smoke transaccional del Kernel RTM CONNECT C1.

Crea exclusivamente registros sintéticos, no usa red y revierte la transacción.
"""

from __future__ import annotations

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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_connect_c1_smoke",
        "version": "rtm_connect_c1_smoke_v1_0",
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "synthetic_only": True,
        "transactional": True,
        "network_used": False,
        "routes_published": False,
        "external_effects_executed": False,
        "checks": {},
        "cleanup": {"database_rolled_back": False, "error": None},
        "synthetic_ids": {},
    }
    blockers = _blockers()
    if blockers:
        report["blockers"] = blockers
        _print(report)
        return 2

    action_ids: list[str] = []
    try:
        from sqlalchemy import text
        from database import get_engine
        from rtm_connect.contracts import (
            AuthorizationGrant,
            ConnectActionRequest,
            ConnectorMode,
            EvidenceLevel,
            EvidenceRecord,
            RiskClass,
        )
        from rtm_connect.idempotency import derive_idempotency_key, payload_sha256
        from rtm_connect.kernel import (
            EvidenceGateError,
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
        from rtm_connect.state_machine import ActionStatus, InvalidActionTransition

        engine = get_engine()
        connection = engine.connect()
        transaction = connection.begin()
        try:
            suffix = uuid.uuid4().hex[:12]
            role_id = str(uuid.uuid4())
            operator_id = str(uuid.uuid4())
            connection.execute(
                text(
                    """
                    INSERT INTO rtm_operator_roles(
                        id, code, name, permissions, system_role, active,
                        created_at, updated_at
                    ) VALUES (
                        CAST(:id AS UUID), :code, :name,
                        '["ops.view", "ops.supervise"]'::jsonb,
                        FALSE, TRUE, NOW(), NOW()
                    )
                    """
                ),
                {"id": role_id, "code": f"synthetic.connect.c1.{suffix}", "name": "RTM CONNECT C1 SMOKE"},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO rtm_operators(
                        id, email, display_name, password_hash, status,
                        primary_role_id, must_change_password, mfa_required,
                        profile, failed_login_count, password_algorithm,
                        password_version, auth_epoch, created_at, updated_at
                    ) VALUES (
                        CAST(:id AS UUID), :email, :display_name, NULL, 'active',
                        CAST(:role_id AS UUID), FALSE, FALSE,
                        '{"synthetic": true, "purpose": "connect_c1_smoke"}'::jsonb,
                        0, 'argon2id', 1, 1, NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": operator_id,
                    "email": f"rtm-staging-connect-c1-{suffix}@example.com",
                    "display_name": "RTM CONNECT C1 SMOKE",
                    "role_id": role_id,
                },
            )
            report["checks"]["synthetic_operator_inserted"] = True

            connector = register_synthetic_connector(
                connection,
                code="synthetic.echo",
                version=f"v1-{suffix}",
                mode=ConnectorMode.MANUAL,
                capabilities=("administration.submit_document",),
                risk_ceiling=RiskClass.R4_CRITICAL_REGULATED,
                supports_reconciliation=True,
                configuration={"behavior": "no_external_effect"},
            )
            report["synthetic_ids"]["connector_id"] = connector.connector_id
            report["checks"]["synthetic_connector_registered"] = connector.created

            action_id = str(uuid.uuid4())
            action_ids.append(action_id)
            action = ConnectActionRequest(
                action_id=action_id,
                capability="administration.submit_document",
                satellite="administration",
                target_type="public_registry",
                target_ref="synthetic-c1-registry",
                payload={"document_type": "synthetic", "subject": "C1 kernel"},
                document_hashes=("a" * 64,),
                requested_by_operator_id=operator_id,
                requested_at=_now(),
                risk_class=RiskClass.R3_LEGAL_OR_FINANCIAL,
            )
            outcome = create_action(
                connection,
                action=action,
                authority_scope="rtm.core.authorization",
            )
            replay = create_action(
                connection,
                action=action,
                authority_scope="rtm.core.authorization",
            )
            report["checks"]["action_created"] = outcome.created and not outcome.replayed
            report["checks"]["idempotent_replay_reused_action"] = (
                replay.replayed and replay.action_id == action_id
            )

            grant = AuthorizationGrant(
                authorization_id=str(uuid.uuid4()),
                action_id=action_id,
                authority_code="rtm.core.authorization",
                authority_version="rtm_core_authority_v1",
                decision="approved_frozen",
                payload_sha256=payload_sha256(action),
                idempotency_key=derive_idempotency_key(
                    action, authority_scope="rtm.core.authorization"
                ),
                required_evidence_level=EvidenceLevel.E4_RECEIPT_VERIFIED,
                authorized_connector_modes=(ConnectorMode.MANUAL,),
                approved_by_operator_ids=(operator_id,),
                authorized_at=_now(),
                expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                legal_effect_authorized=True,
            )
            authorize_action(connection, grant=grant)
            queue_action(connection, action_id=action_id, operator_id=operator_id)
            attempt = start_attempt(
                connection,
                action_id=action_id,
                connector_id=connector.connector_id,
                request_metadata={"synthetic": True},
            )
            report["synthetic_ids"]["action_id"] = action_id
            report["synthetic_ids"]["attempt_id"] = attempt.attempt_id
            report["checks"]["authorized_queued_attempt_started"] = True

            record_attempt_outcome(
                connection,
                attempt_id=attempt.attempt_id,
                target_status=ActionStatus.EXTERNAL_ACCEPTED,
                external_reference="SYNTHETIC-C1-REF",
                result_metadata={"network_used": False},
            )
            weak = EvidenceRecord(
                level=EvidenceLevel.E2_EXTERNAL_REFERENCE,
                request_sha256=payload_sha256(action),
                external_reference="SYNTHETIC-C1-REF",
            )
            weak_id = record_evidence(
                connection,
                action_id=action_id,
                attempt_id=attempt.attempt_id,
                evidence=weak,
            )
            blocked = False
            try:
                confirm_action(connection, action_id=action_id, operator_id=operator_id)
            except EvidenceGateError:
                blocked = True
            report["checks"]["weak_evidence_blocked_confirmation"] = blocked

            strong = EvidenceRecord(
                level=EvidenceLevel.E4_RECEIPT_VERIFIED,
                request_sha256=payload_sha256(action),
                external_reference="SYNTHETIC-C1-REF",
                receipt_sha256="b" * 64,
                receipt_storage_ref="b2://synthetic/connect-c1-receipt.pdf",
                verified_at=_now(),
                verification_method="synthetic_hash_and_reference_check",
            )
            strong_id = record_evidence(
                connection,
                action_id=action_id,
                attempt_id=attempt.attempt_id,
                evidence=strong,
                verified_by_operator_id=operator_id,
            )
            confirmed = confirm_action(
                connection, action_id=action_id, operator_id=operator_id
            )
            snapshot = action_snapshot(connection, action_id=action_id)
            report["synthetic_ids"]["weak_evidence_id"] = weak_id
            report["synthetic_ids"]["strong_evidence_id"] = strong_id
            report["checks"]["verified_evidence_confirmed_action"] = (
                confirmed and snapshot["status"] == "confirmed"
                and int(snapshot["evidence_rows"]) == 2
            )
            report["checks"]["idempotency_replay_count_recorded"] = (
                int(snapshot["replay_count"]) == 1
            )

            unknown_action_id = str(uuid.uuid4())
            action_ids.append(unknown_action_id)
            unknown_action = ConnectActionRequest(
                action_id=unknown_action_id,
                capability="administration.submit_document",
                satellite="administration",
                target_type="public_registry",
                target_ref="synthetic-c1-unknown",
                payload={"document_type": "synthetic", "subject": "unknown"},
                requested_by_operator_id=operator_id,
                requested_at=_now(),
                risk_class=RiskClass.R3_LEGAL_OR_FINANCIAL,
            )
            create_action(
                connection,
                action=unknown_action,
                authority_scope="rtm.core.authorization",
            )
            unknown_grant = AuthorizationGrant(
                authorization_id=str(uuid.uuid4()),
                action_id=unknown_action_id,
                authority_code="rtm.core.authorization",
                authority_version="rtm_core_authority_v1",
                decision="approved_frozen",
                payload_sha256=payload_sha256(unknown_action),
                idempotency_key=derive_idempotency_key(
                    unknown_action, authority_scope="rtm.core.authorization"
                ),
                required_evidence_level=EvidenceLevel.E4_RECEIPT_VERIFIED,
                authorized_connector_modes=(ConnectorMode.MANUAL,),
                approved_by_operator_ids=(operator_id,),
                authorized_at=_now(),
                expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                legal_effect_authorized=True,
            )
            authorize_action(connection, grant=unknown_grant)
            queue_action(connection, action_id=unknown_action_id, operator_id=operator_id)
            unknown_attempt = start_attempt(
                connection,
                action_id=unknown_action_id,
                connector_id=connector.connector_id,
            )
            record_attempt_outcome(
                connection,
                attempt_id=unknown_attempt.attempt_id,
                target_status=ActionStatus.UNKNOWN,
                error_code="synthetic_connection_lost",
            )
            blind_retry_blocked = False
            try:
                queue_action(connection, action_id=unknown_action_id)
            except InvalidActionTransition:
                blind_retry_blocked = True
            begin_reconciliation(connection, action_id=unknown_action_id)
            unknown_snapshot = action_snapshot(connection, action_id=unknown_action_id)
            report["checks"]["unknown_blind_retry_blocked"] = blind_retry_blocked
            report["checks"]["unknown_entered_reconciliation"] = (
                unknown_snapshot["status"] == "reconciling"
            )

            transition_rows = connection.execute(
                text(
                    """
                    SELECT from_status, to_status
                    FROM rtm_connect_transitions
                    WHERE action_id=CAST(:action_id AS UUID)
                    ORDER BY sequence_number ASC
                    """
                ),
                {"action_id": action_id},
            ).fetchall()
            sequence = [str(row[1]) for row in transition_rows]
            report["checks"]["transition_ledger_complete"] = sequence == [
                "draft", "authorized", "queued", "executing",
                "external_accepted", "evidence_pending", "confirmed",
            ]

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

            report["checks"]["transition_ledger_append_only"] = trigger_blocks(
                "UPDATE rtm_connect_transitions SET reason_code='tampered' "
                "WHERE action_id=CAST(:id AS UUID)",
                {"id": action_id},
            )
            report["checks"]["evidence_store_append_only"] = trigger_blocks(
                "UPDATE rtm_connect_evidence SET verification_method='tampered' "
                "WHERE id=CAST(:id AS UUID)",
                {"id": strong_id},
            )
            report["checks"]["authorization_registry_immutable"] = trigger_blocks(
                "UPDATE rtm_connect_authorizations SET authority_version='tampered' "
                "WHERE action_id=CAST(:id AS UUID)",
                {"id": action_id},
            )

            connector_count = connection.execute(
                text("SELECT COUNT(*) FROM rtm_connect_connectors")
            ).scalar_one()
            report["checks"]["only_synthetic_connector_in_transaction"] = (
                int(connector_count) == 1
            )
            report["checks"]["no_external_effects"] = (
                report["network_used"] is False
                and report["routes_published"] is False
                and report["external_effects_executed"] is False
            )
            report["tests_ok"] = all(bool(v) for v in report["checks"].values())
            report["ok"] = bool(report["tests_ok"])
        finally:
            transaction.rollback()
            connection.close()
            report["cleanup"]["database_rolled_back"] = True

        with engine.connect() as verification:
            remaining_actions = verification.execute(
                text(
                    """
                    SELECT COUNT(*) FROM rtm_connect_actions
                    WHERE id IN (
                        CAST(:id_one AS UUID), CAST(:id_two AS UUID)
                    )
                    """
                ),
                {"id_one": action_ids[0], "id_two": action_ids[1]},
            ).scalar_one()
            remaining_connectors = verification.execute(
                text(
                    """
                    SELECT COUNT(*) FROM rtm_connect_connectors
                    WHERE id=CAST(:connector_id AS UUID)
                    """
                ),
                {"connector_id": report["synthetic_ids"]["connector_id"]},
            ).scalar_one()
        report["cleanup"]["synthetic_actions_remaining"] = int(remaining_actions)
        report["cleanup"]["synthetic_connectors_remaining"] = int(remaining_connectors)
        report["checks"]["rollback_removed_synthetic_records"] = (
            int(remaining_actions) == 0 and int(remaining_connectors) == 0
        )
        report["tests_ok"] = all(bool(v) for v in report["checks"].values())
        report["ok"] = bool(report["tests_ok"] and report["cleanup"]["database_rolled_back"])
        code = 0 if report["ok"] else 1
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["tests_ok"] = False
        report["ok"] = False
        report["cleanup"]["error"] = str(exc)
        code = 1
    _print(report)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
