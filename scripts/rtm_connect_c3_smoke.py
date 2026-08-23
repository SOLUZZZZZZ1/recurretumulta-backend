#!/usr/bin/env python3
"""Smoke transaccional de RTM CONNECT C3 manual_handoff.

Crea solo operadores, conector, acción, tarea y justificante sintéticos.
No usa red y revierte toda la transacción.
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
        "authority": "rtm_connect_c3_smoke",
        "version": "rtm_connect_c3_smoke_v1_0",
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
        _print(report)
        return 2

    ids: dict[str, str] = {}
    try:
        from sqlalchemy import text
        from database import get_engine
        from rtm_connect.connectors.manual_handoff import (
            ManualReceiptSubmission,
            ManualReceiptVerificationError,
        )
        from rtm_connect.contracts import (
            AuthorizationGrant,
            ConnectActionRequest,
            ConnectorMode,
            EvidenceLevel,
            RiskClass,
        )
        from rtm_connect.idempotency import (
            derive_idempotency_key,
            payload_sha256,
        )
        from rtm_connect.manual_handoff import (
            ManualHandoffSeparationOfDutiesError,
            begin_manual_work,
            complete_manual_handoff,
            mark_manual_awaiting_receipt,
            prepare_manual_handoff,
            submit_manual_receipt,
            verify_manual_receipt,
        )

        engine = get_engine()
        connection = engine.connect()
        transaction = connection.begin()
        try:
            suffix = uuid.uuid4().hex[:12]
            role_id = str(uuid.uuid4())
            requester_id = str(uuid.uuid4())
            assignee_id = str(uuid.uuid4())
            verifier_id = str(uuid.uuid4())
            ids.update(
                {
                    "role_id": role_id,
                    "requester_id": requester_id,
                    "assignee_id": assignee_id,
                    "verifier_id": verifier_id,
                }
            )
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
                    "code": f"synthetic.connect.c3.{suffix}",
                    "name": "RTM CONNECT C3 SMOKE",
                },
            )
            for operator_id, label in (
                (requester_id, "REQUESTER"),
                (assignee_id, "ASSIGNEE"),
                (verifier_id, "VERIFIER"),
            ):
                connection.execute(
                    text(
                        """
                        INSERT INTO rtm_operators(
                            id, email, display_name, password_hash,
                            status, primary_role_id,
                            must_change_password, mfa_required,
                            profile, failed_login_count,
                            password_algorithm, password_version,
                            auth_epoch, created_at, updated_at
                        ) VALUES (
                            CAST(:id AS UUID), :email, :display_name,
                            NULL, 'active', CAST(:role_id AS UUID),
                            FALSE, FALSE,
                            '{"synthetic": true,
                              "purpose": "connect_c3_smoke"}'::jsonb,
                            0, 'argon2id', 1, 1, NOW(), NOW()
                        )
                        """
                    ),
                    {
                        "id": operator_id,
                        "email": (
                            f"rtm-staging-connect-c3-"
                            f"{label.lower()}-{suffix}@example.com"
                        ),
                        "display_name": f"RTM C3 {label}",
                        "role_id": role_id,
                    },
                )
            report["checks"]["synthetic_operators_inserted"] = True

            action_id = str(uuid.uuid4())
            ids["action_id"] = action_id
            action = ConnectActionRequest(
                action_id=action_id,
                capability="administration.submit_document",
                satellite="administration",
                target_type="public_registry",
                target_ref="synthetic-c3-registry",
                payload={
                    "document_type": "synthetic_submission",
                    "subject": "C3 manual handoff",
                },
                document_hashes=("a" * 64, "b" * 64),
                requested_by_operator_id=requester_id,
                requested_at=_now(),
                risk_class=RiskClass.R3_LEGAL_OR_FINANCIAL,
            )
            grant = AuthorizationGrant(
                authorization_id=str(uuid.uuid4()),
                action_id=action_id,
                authority_code="rtm.core.authorization",
                authority_version="rtm_core_authority_v1",
                decision="approved_frozen",
                payload_sha256=payload_sha256(action),
                idempotency_key=derive_idempotency_key(
                    action,
                    authority_scope="rtm.core.authorization",
                ),
                required_evidence_level=(
                    EvidenceLevel.E4_RECEIPT_VERIFIED
                ),
                authorized_connector_modes=(ConnectorMode.MANUAL,),
                approved_by_operator_ids=(requester_id,),
                authorized_at=_now(),
                expires_at=(
                    datetime.now(timezone.utc)
                    + timedelta(hours=4)
                ).isoformat(),
                legal_effect_authorized=True,
            )
            due_at = (
                datetime.now(timezone.utc) + timedelta(hours=2)
            ).isoformat()
            instructions = (
                "Presentar manualmente el paquete sintético en la "
                "sede ficticia y aportar justificante sintético."
            )
            prepared = prepare_manual_handoff(
                connection,
                action=action,
                grant=grant,
                assignee_operator_id=assignee_id,
                assigned_by_operator_id=requester_id,
                due_at=due_at,
                instructions=instructions,
            )
            ids["task_id"] = prepared.task_id
            ids["connector_id"] = prepared.connector_id
            ids["attempt_id"] = prepared.attempt_id
            report["checks"]["task_prepared_and_assigned"] = (
                prepared.task_status == "assigned"
                and prepared.action_status == "executing"
                and prepared.task_events == 2
                and prepared.attempts == 1
            )
            report["checks"]["deadline_and_package_frozen"] = (
                prepared.overdue is False
                and len(prepared.package_sha256) == 64
                and prepared.task_code.startswith("rtm-manual-")
            )

            replay = prepare_manual_handoff(
                connection,
                action=action,
                grant=grant,
                assignee_operator_id=assignee_id,
                assigned_by_operator_id=requester_id,
                due_at=due_at,
                instructions=instructions,
            )
            report["checks"]["prepare_replay_reused_task"] = (
                replay.replayed
                and replay.task_id == prepared.task_id
                and replay.attempts == 1
                and replay.replay_count == 1
            )

            started = begin_manual_work(
                connection,
                task_id=prepared.task_id,
                operator_id=assignee_id,
            )
            awaiting = mark_manual_awaiting_receipt(
                connection,
                task_id=prepared.task_id,
                operator_id=assignee_id,
            )
            report["checks"]["manual_work_progressed"] = (
                started.task_status == "in_progress"
                and awaiting.task_status == "awaiting_receipt"
            )

            submission = ManualReceiptSubmission(
                receipt_sha256="c" * 64,
                storage_ref=(
                    f"synthetic://manual-handoff/"
                    f"{action_id}/receipt.pdf"
                ),
                external_reference=(
                    f"SYN-MANUAL-{suffix.upper()}"
                ),
                presented_at=_now(),
                mime="application/pdf",
                size_bytes=4096,
            )
            submitted = submit_manual_receipt(
                connection,
                task_id=prepared.task_id,
                operator_id=assignee_id,
                submission=submission,
            )
            report["checks"]["receipt_captured_as_e3"] = (
                submitted.task_status == "receipt_submitted"
                and submitted.action_status == "evidence_pending"
                and submitted.evidence_rows == 1
            )

            same_operator_blocked = False
            try:
                verify_manual_receipt(
                    connection,
                    task_id=prepared.task_id,
                    verifier_operator_id=assignee_id,
                    observed_receipt_sha256=submission.receipt_sha256,
                    observed_external_reference=(
                        submission.external_reference
                    ),
                    verified_at=_now(),
                )
            except ManualHandoffSeparationOfDutiesError:
                same_operator_blocked = True
            report["checks"]["separation_of_duties_enforced"] = (
                same_operator_blocked
            )

            wrong_hash_blocked = False
            try:
                verify_manual_receipt(
                    connection,
                    task_id=prepared.task_id,
                    verifier_operator_id=verifier_id,
                    observed_receipt_sha256="d" * 64,
                    observed_external_reference=(
                        submission.external_reference
                    ),
                    verified_at=_now(),
                )
            except ManualReceiptVerificationError:
                wrong_hash_blocked = True
            report["checks"]["wrong_receipt_hash_blocked"] = (
                wrong_hash_blocked
            )

            verified = verify_manual_receipt(
                connection,
                task_id=prepared.task_id,
                verifier_operator_id=verifier_id,
                observed_receipt_sha256=submission.receipt_sha256,
                observed_external_reference=(
                    submission.external_reference
                ),
                verified_at=_now(),
            )
            report["checks"]["receipt_verified_as_e4"] = (
                verified.task_status == "verified"
                and verified.action_status == "evidence_pending"
                and verified.evidence_rows == 2
            )

            completed = complete_manual_handoff(
                connection,
                task_id=prepared.task_id,
                verifier_operator_id=verifier_id,
            )
            completed_replay = complete_manual_handoff(
                connection,
                task_id=prepared.task_id,
                verifier_operator_id=verifier_id,
            )
            report["checks"]["core_confirmed_after_e4"] = (
                completed.task_status == "completed"
                and completed.action_status == "confirmed"
                and completed_replay.replayed
                and completed_replay.attempts == 1
            )

            action_transitions = [
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
                    {"action_id": action_id},
                ).fetchall()
            ]
            manual_events = [
                str(row[0])
                for row in connection.execute(
                    text(
                        """
                        SELECT event_type
                        FROM rtm_connect_manual_events
                        WHERE task_id=CAST(:task_id AS UUID)
                        ORDER BY sequence_number
                        """
                    ),
                    {"task_id": prepared.task_id},
                ).fetchall()
            ]
            report["checks"]["action_transition_ledger_complete"] = (
                action_transitions
                == [
                    "draft",
                    "authorized",
                    "queued",
                    "executing",
                    "external_accepted",
                    "evidence_pending",
                    "confirmed",
                ]
            )
            report["checks"]["manual_event_ledger_complete"] = (
                manual_events
                == [
                    "manual.prepared",
                    "manual.assigned",
                    "manual.in_progress",
                    "manual.awaiting_receipt",
                    "manual.receipt_submitted",
                    "manual.verified",
                    "manual.completed",
                ]
            )

            def trigger_blocks(
                sql: str,
                params: dict[str, Any],
            ) -> bool:
                nested = connection.begin_nested()
                try:
                    connection.execute(text(sql), params)
                except Exception:
                    nested.rollback()
                    return True
                else:
                    nested.commit()
                    return False

            report["checks"]["package_tampering_blocked"] = (
                trigger_blocks(
                    """
                    UPDATE rtm_connect_manual_tasks
                    SET package_sha256=:tampered,
                        version=version+1
                    WHERE id=CAST(:task_id AS UUID)
                    """,
                    {
                        "tampered": "f" * 64,
                        "task_id": prepared.task_id,
                    },
                )
            )
            report["checks"]["manual_events_append_only"] = (
                trigger_blocks(
                    """
                    UPDATE rtm_connect_manual_events
                    SET reason_code='tampered'
                    WHERE task_id=CAST(:task_id AS UUID)
                    """,
                    {"task_id": prepared.task_id},
                )
            )

            counts = connection.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*)
                         FROM rtm_connect_connectors
                         WHERE code='manual.handoff')
                            AS connector_count,
                        (SELECT COUNT(*)
                         FROM rtm_connect_manual_tasks)
                            AS task_count
                    """
                )
            ).mappings().one()
            report["checks"][
                "single_synthetic_manual_connector_in_transaction"
            ] = (
                int(counts["connector_count"]) == 1
                and int(counts["task_count"]) == 1
            )
            report["checks"]["no_external_effects"] = (
                report["network_used"] is False
                and report["routes_published"] is False
                and report["schema_changes_applied"] is False
                and report["external_effects_executed"] is False
            )
            report["tests_ok"] = all(
                bool(value)
                for value in report["checks"].values()
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
                        (SELECT COUNT(*)
                         FROM rtm_connect_actions
                         WHERE id=CAST(:action_id AS UUID))
                            AS actions,
                        (SELECT COUNT(*)
                         FROM rtm_connect_connectors
                         WHERE id=CAST(:connector_id AS UUID))
                            AS connectors,
                        (SELECT COUNT(*)
                         FROM rtm_connect_manual_tasks
                         WHERE id=CAST(:task_id AS UUID))
                            AS tasks,
                        (SELECT COUNT(*)
                         FROM rtm_connect_manual_events
                         WHERE task_id=CAST(:task_id AS UUID))
                            AS events,
                        (SELECT COUNT(*)
                         FROM rtm_operators
                         WHERE id IN (
                             CAST(:requester AS UUID),
                             CAST(:assignee AS UUID),
                             CAST(:verifier AS UUID)
                         ))
                            AS operators,
                        (SELECT COUNT(*)
                         FROM rtm_operator_roles
                         WHERE id=CAST(:role_id AS UUID))
                            AS roles
                    """
                ),
                {
                    "action_id": ids["action_id"],
                    "connector_id": ids["connector_id"],
                    "task_id": ids["task_id"],
                    "requester": ids["requester_id"],
                    "assignee": ids["assignee_id"],
                    "verifier": ids["verifier_id"],
                    "role_id": ids["role_id"],
                },
            ).mappings().one()
        for key, value in remaining.items():
            report["cleanup"][f"synthetic_{key}_remaining"] = int(
                value
            )
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

    _print(report)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
