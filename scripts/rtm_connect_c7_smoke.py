#!/usr/bin/env python3
"""Smoke transaccional y sin red del handoff juridico asistido C7.

Ejercita la ruta normal y la rama UNKNOWN exclusivamente con identidades,
acciones, paquetes y atestaciones sinteticas. Toda escritura ocurre dentro de
una unica transaccion que siempre se revierte.
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


SMOKE_VERSION = "rtm_connect_c7_smoke_v1_0"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    return parser


def _print(report: dict[str, Any], compact: bool) -> None:
    print(json.dumps(
        report,
        ensure_ascii=False,
        indent=None if compact else 2,
        separators=(",", ":") if compact else None,
        default=str,
    ))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _action_and_grant(
    *,
    action_id: str,
    requester_id: str,
    release_operator_id: str,
    verifier_operator_id: str,
    document_seed: str,
):
    from rtm_connect.assisted_legal_policy import (
        ASSISTED_LEGAL_AUTHORITY_CODE,
        ASSISTED_LEGAL_AUTHORITY_VERSION,
        ASSISTED_LEGAL_CAPABILITY,
        ASSISTED_LEGAL_SATELLITE,
        ASSISTED_LEGAL_TARGET_REF,
        ASSISTED_LEGAL_TARGET_TYPE,
        expected_c7_payload,
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

    action = ConnectActionRequest(
        action_id=action_id,
        capability=ASSISTED_LEGAL_CAPABILITY,
        satellite=ASSISTED_LEGAL_SATELLITE,
        target_type=ASSISTED_LEGAL_TARGET_TYPE,
        target_ref=ASSISTED_LEGAL_TARGET_REF,
        payload=expected_c7_payload(),
        document_hashes=(document_seed * 64, chr(ord(document_seed) + 1) * 64),
        requested_by_operator_id=requester_id,
        requested_at=_now(),
        risk_class=RiskClass.R4_CRITICAL_REGULATED,
        requires_dual_control=True,
    )
    grant = AuthorizationGrant(
        authorization_id=str(uuid.uuid4()),
        action_id=action.action_id,
        authority_code=ASSISTED_LEGAL_AUTHORITY_CODE,
        authority_version=ASSISTED_LEGAL_AUTHORITY_VERSION,
        decision="approved_frozen",
        payload_sha256=payload_sha256(action),
        idempotency_key=derive_idempotency_key(
            action,
            authority_scope=ASSISTED_LEGAL_AUTHORITY_CODE,
        ),
        required_evidence_level=EvidenceLevel.E4_RECEIPT_VERIFIED,
        authorized_connector_modes=(ConnectorMode.ASSISTED,),
        approved_by_operator_ids=(
            release_operator_id,
            verifier_operator_id,
        ),
        authorized_at=_now(),
        expires_at=(
            datetime.now(timezone.utc) + timedelta(hours=4)
        ).isoformat(),
        legal_effect_authorized=True,
    )
    return action, grant


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_connect_c7_smoke",
        "version": SMOKE_VERSION,
        "environment": (
            os.getenv("RTM_ENV") or ""
        ).strip().lower() or "unset",
        "synthetic_only": True,
        "transactional": True,
        "network_used": False,
        "real_administration_contacted": False,
        "routes_published": False,
        "schema_changes_applied": False,
        "external_effects_executed": False,
        "checks": {},
        "blockers": [],
        "cleanup": {
            "database_rolled_back": False,
            "error": None,
        },
        "synthetic_ids": {},
    }

    try:
        from scripts.rtm_staging_connect_c7_schema import safety_blockers

        report["blockers"] = safety_blockers()
    except Exception as exc:
        report["blockers"] = [
            f"connect_c7_boundary_error:{type(exc).__name__}:{exc}"
        ]
    if report["blockers"]:
        _print(report, args.compact)
        return 2

    ids: dict[str, str] = {}
    try:
        from sqlalchemy import text

        from database import get_engine
        from rtm_connect.assisted_legal import (
            ASSISTED_EXECUTE_PERMISSION,
            ASSISTED_RELEASE_PERMISSION,
            ASSISTED_VERIFY_PERMISSION,
            AssistedLegalSeparationOfDutiesError,
            attest_assisted_review,
            begin_assisted_execution,
            begin_assisted_reconciliation,
            begin_assisted_review,
            complete_assisted_legal,
            mark_assisted_awaiting_receipt,
            mark_assisted_outcome_unknown,
            prepare_assisted_legal,
            release_assisted_legal,
            resolve_assisted_reconciliation,
            submit_assisted_receipt,
            verify_assisted_receipt,
        )
        from rtm_connect.assisted_legal_policy import (
            ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
        )
        from rtm_connect.connectors.assisted_legal import (
            ASSISTED_LEGAL_REFERENCE_PREFIX,
            AssistedReceiptSubmission,
            AssistedReceiptVerificationError,
        )
        from rtm_connect.state_machine import (
            ActionStatus,
            automatic_retry_allowed,
        )

        engine = get_engine()
        connection = engine.connect()
        transaction = connection.begin()
        try:
            suffix = uuid.uuid4().hex[:12]
            role_id = str(uuid.uuid4())
            requester_id = str(uuid.uuid4())
            assignee_id = str(uuid.uuid4())
            release_id = str(uuid.uuid4())
            verifier_id = str(uuid.uuid4())
            ids.update({
                "role_id": role_id,
                "requester_id": requester_id,
                "assignee_id": assignee_id,
                "release_id": release_id,
                "verifier_id": verifier_id,
            })
            permissions = [
                ASSISTED_EXECUTE_PERMISSION,
                ASSISTED_RELEASE_PERMISSION,
                ASSISTED_VERIFY_PERMISSION,
            ]
            connection.execute(text(
                """
                INSERT INTO rtm_operator_roles(
                    id, code, name, permissions,
                    system_role, active, created_at, updated_at
                ) VALUES (
                    CAST(:id AS UUID), :code, :name,
                    CAST(:permissions AS JSONB),
                    FALSE, TRUE, NOW(), NOW()
                )
                """
            ), {
                "id": role_id,
                "code": f"synthetic.connect.c7.{suffix}",
                "name": "RTM CONNECT C7 SMOKE",
                "permissions": json.dumps(permissions),
            })
            for operator_id, label in (
                (requester_id, "requester"),
                (assignee_id, "assignee"),
                (release_id, "release"),
                (verifier_id, "verifier"),
            ):
                connection.execute(text(
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
                        FALSE, FALSE, CAST(:profile AS JSONB),
                        0, 'argon2id', 1, 1, NOW(), NOW()
                    )
                    """
                ), {
                    "id": operator_id,
                    "email": (
                        f"rtm-staging-connect-c7-{label}-"
                        f"{suffix}@example.com"
                    ),
                    "display_name": f"RTM C7 {label.upper()}",
                    "role_id": role_id,
                    "profile": json.dumps({
                        "synthetic": True,
                        "environment": "staging",
                        "purpose": "connect_c7_smoke",
                    }),
                })
            report["checks"]["synthetic_r4_operators_inserted"] = True

            def make_task(seed: str):
                action_id = str(uuid.uuid4())
                action, grant = _action_and_grant(
                    action_id=action_id,
                    requester_id=requester_id,
                    release_operator_id=release_id,
                    verifier_operator_id=verifier_id,
                    document_seed=seed,
                )
                due_at = (
                    datetime.now(timezone.utc) + timedelta(hours=2)
                ).isoformat()
                prepared = prepare_assisted_legal(
                    connection,
                    action=action,
                    grant=grant,
                    assignee_operator_id=assignee_id,
                    assigned_by_operator_id=release_id,
                    due_at=due_at,
                )
                reviewed = begin_assisted_review(
                    connection,
                    task_id=prepared.task_id,
                    operator_id=assignee_id,
                )
                attested = attest_assisted_review(
                    connection,
                    task_id=prepared.task_id,
                    operator_id=assignee_id,
                    human_gate_phrase=ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
                )
                released = release_assisted_legal(
                    connection,
                    task_id=prepared.task_id,
                    grant=grant,
                    release_operator_id=release_id,
                )
                started = begin_assisted_execution(
                    connection,
                    task_id=prepared.task_id,
                    operator_id=assignee_id,
                )
                return (
                    action,
                    grant,
                    due_at,
                    prepared,
                    reviewed,
                    attested,
                    released,
                    started,
                )

            (
                normal_action,
                normal_grant,
                normal_due,
                normal_prepared,
                normal_reviewed,
                normal_attested,
                normal_released,
                normal_started,
            ) = make_task("a")
            ids.update({
                "normal_action_id": normal_action.action_id,
                "normal_task_id": normal_prepared.task_id,
                "normal_attempt_id": normal_prepared.attempt_id,
                "connector_id": normal_prepared.connector_id,
            })
            report["checks"]["normal_task_prepared_r4_exact"] = (
                normal_prepared.task_status == "assigned"
                and normal_prepared.action_status == "executing"
                and normal_prepared.attempts == 1
                and normal_prepared.task_events == 2
                and normal_prepared.task_code.startswith("rtm-assisted-")
                and len(normal_prepared.package_sha256) == 64
            )
            report["checks"]["review_release_execution_separated"] = (
                normal_reviewed.task_status == "reviewing"
                and normal_attested.task_status == "ready_for_release"
                and normal_released.task_status == "released"
                and normal_released.release_operator_id == release_id
                and normal_started.task_status == "in_progress"
                and len({assignee_id, release_id, verifier_id}) == 3
            )

            replay = prepare_assisted_legal(
                connection,
                action=normal_action,
                grant=normal_grant,
                assignee_operator_id=assignee_id,
                assigned_by_operator_id=release_id,
                due_at=normal_due,
            )
            report["checks"]["prepare_replay_reuses_task_and_attempt"] = (
                replay.replayed
                and replay.task_id == normal_prepared.task_id
                and replay.attempt_id == normal_prepared.attempt_id
                and replay.attempts == 1
                and replay.replay_count == 1
            )

            task_material = connection.execute(text(
                """
                SELECT package_manifest, package_sha256,
                       review_attestation_sha256,
                       release_attestation_sha256
                FROM rtm_connect_assisted_tasks
                WHERE id=CAST(:task_id AS UUID)
                """
            ), {"task_id": normal_prepared.task_id}).mappings().one()
            package_manifest = dict(task_material["package_manifest"])
            normal_submission = AssistedReceiptSubmission(
                receipt_sha256="c" * 64,
                storage_ref=(
                    "synthetic://assisted-legal/"
                    f"{normal_action.action_id}/attestation.json"
                ),
                external_reference=(
                    f"{ASSISTED_LEGAL_REFERENCE_PREFIX}"
                    f"{normal_action.action_id}"
                ),
                package_sha256=str(task_material["package_sha256"]),
                human_gate_sha256=str(
                    package_manifest["human_gate_sha256"]
                ),
                human_final_gate=ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
                witnessed_at=_now(),
                mime="application/json",
                size_bytes=2048,
            )
            awaiting = mark_assisted_awaiting_receipt(
                connection,
                task_id=normal_prepared.task_id,
                operator_id=assignee_id,
            )
            submitted = submit_assisted_receipt(
                connection,
                task_id=normal_prepared.task_id,
                operator_id=assignee_id,
                submission=normal_submission,
            )
            report["checks"]["normal_receipt_captured_as_e3"] = (
                awaiting.task_status == "awaiting_receipt"
                and submitted.task_status == "receipt_submitted"
                and submitted.action_status == "evidence_pending"
                and submitted.evidence_rows == 1
            )

            same_person_blocked = False
            try:
                verify_assisted_receipt(
                    connection,
                    task_id=normal_prepared.task_id,
                    grant=normal_grant,
                    verifier_operator_id=release_id,
                    observed_receipt_sha256=normal_submission.receipt_sha256,
                    observed_external_reference=(
                        normal_submission.external_reference
                    ),
                    observed_package_sha256=(
                        normal_submission.package_sha256
                    ),
                    observed_human_gate_sha256=(
                        normal_submission.human_gate_sha256
                    ),
                    verified_at=_now(),
                )
            except AssistedLegalSeparationOfDutiesError:
                same_person_blocked = True
            report["checks"]["triple_separation_enforced"] = (
                same_person_blocked
            )

            wrong_hash_blocked = False
            try:
                verify_assisted_receipt(
                    connection,
                    task_id=normal_prepared.task_id,
                    grant=normal_grant,
                    verifier_operator_id=verifier_id,
                    observed_receipt_sha256="d" * 64,
                    observed_external_reference=(
                        normal_submission.external_reference
                    ),
                    observed_package_sha256=(
                        normal_submission.package_sha256
                    ),
                    observed_human_gate_sha256=(
                        normal_submission.human_gate_sha256
                    ),
                    verified_at=_now(),
                )
            except AssistedReceiptVerificationError:
                wrong_hash_blocked = True
            report["checks"]["wrong_receipt_hash_blocked"] = (
                wrong_hash_blocked
            )

            verified = verify_assisted_receipt(
                connection,
                task_id=normal_prepared.task_id,
                grant=normal_grant,
                verifier_operator_id=verifier_id,
                observed_receipt_sha256=normal_submission.receipt_sha256,
                observed_external_reference=normal_submission.external_reference,
                observed_package_sha256=normal_submission.package_sha256,
                observed_human_gate_sha256=(
                    normal_submission.human_gate_sha256
                ),
                verified_at=_now(),
            )
            completed = complete_assisted_legal(
                connection,
                task_id=normal_prepared.task_id,
                verifier_operator_id=verifier_id,
            )
            completed_replay = complete_assisted_legal(
                connection,
                task_id=normal_prepared.task_id,
                verifier_operator_id=verifier_id,
            )
            report["checks"]["normal_e4_precedes_core_confirmation"] = (
                verified.task_status == "verified"
                and verified.action_status == "evidence_pending"
                and verified.evidence_rows == 2
                and completed.task_status == "completed"
                and completed.action_status == "confirmed"
                and completed.verified_by_operator_id == verifier_id
                and completed_replay.replayed
                and completed_replay.attempts == 1
            )

            (
                unknown_action,
                unknown_grant,
                _unknown_due,
                unknown_prepared,
                _unknown_reviewed,
                _unknown_attested,
                _unknown_released,
                _unknown_started,
            ) = make_task("e")
            ids.update({
                "unknown_action_id": unknown_action.action_id,
                "unknown_task_id": unknown_prepared.task_id,
                "unknown_attempt_id": unknown_prepared.attempt_id,
            })
            unknown = mark_assisted_outcome_unknown(
                connection,
                task_id=unknown_prepared.task_id,
                operator_id=assignee_id,
            )
            report["checks"]["unknown_blocks_blind_retry"] = (
                unknown.task_status == "outcome_unknown"
                and unknown.action_status == "unknown"
                and unknown.attempts == 1
                and automatic_retry_allowed(ActionStatus.UNKNOWN) is False
            )
            reconciling = begin_assisted_reconciliation(
                connection,
                task_id=unknown_prepared.task_id,
                operator_id=verifier_id,
            )
            unknown_material = connection.execute(text(
                """
                SELECT package_manifest, package_sha256
                FROM rtm_connect_assisted_tasks
                WHERE id=CAST(:task_id AS UUID)
                """
            ), {"task_id": unknown_prepared.task_id}).mappings().one()
            unknown_manifest = dict(unknown_material["package_manifest"])
            unknown_submission = AssistedReceiptSubmission(
                receipt_sha256="f" * 64,
                storage_ref=(
                    "synthetic://assisted-legal/"
                    f"{unknown_action.action_id}/reconciliation.json"
                ),
                external_reference=(
                    f"{ASSISTED_LEGAL_REFERENCE_PREFIX}"
                    f"{unknown_action.action_id}"
                ),
                package_sha256=str(unknown_material["package_sha256"]),
                human_gate_sha256=str(
                    unknown_manifest["human_gate_sha256"]
                ),
                human_final_gate=ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
                witnessed_at=_now(),
                mime="application/json",
                size_bytes=3072,
            )
            unknown_submitted = submit_assisted_receipt(
                connection,
                task_id=unknown_prepared.task_id,
                operator_id=verifier_id,
                submission=unknown_submission,
            )
            unknown_verified = verify_assisted_receipt(
                connection,
                task_id=unknown_prepared.task_id,
                grant=unknown_grant,
                verifier_operator_id=verifier_id,
                observed_receipt_sha256=unknown_submission.receipt_sha256,
                observed_external_reference=(
                    unknown_submission.external_reference
                ),
                observed_package_sha256=unknown_submission.package_sha256,
                observed_human_gate_sha256=(
                    unknown_submission.human_gate_sha256
                ),
                verified_at=_now(),
            )
            unknown_completed = complete_assisted_legal(
                connection,
                task_id=unknown_prepared.task_id,
                verifier_operator_id=verifier_id,
            )
            report["checks"]["unknown_reconciles_original_attempt_after_e4"] = (
                reconciling.task_status == "reconciling"
                and reconciling.action_status == "reconciling"
                and unknown_submitted.task_status == "receipt_submitted"
                and unknown_verified.task_status == "verified"
                and unknown_completed.task_status == "completed"
                and unknown_completed.action_status == "confirmed"
                and unknown_completed.attempt_id == unknown_prepared.attempt_id
                and unknown_completed.attempts == 1
                and unknown_completed.evidence_rows == 2
            )

            (
                indeterminate_action,
                _indeterminate_grant,
                _indeterminate_due,
                indeterminate_prepared,
                _indeterminate_reviewed,
                _indeterminate_attested,
                _indeterminate_released,
                _indeterminate_started,
            ) = make_task("1")
            ids.update({
                "indeterminate_action_id": indeterminate_action.action_id,
                "indeterminate_task_id": indeterminate_prepared.task_id,
                "indeterminate_attempt_id": indeterminate_prepared.attempt_id,
            })
            mark_assisted_outcome_unknown(
                connection,
                task_id=indeterminate_prepared.task_id,
                operator_id=assignee_id,
            )
            indeterminate_reconciling = begin_assisted_reconciliation(
                connection,
                task_id=indeterminate_prepared.task_id,
                operator_id=verifier_id,
            )
            indeterminate = resolve_assisted_reconciliation(
                connection,
                task_id=indeterminate_prepared.task_id,
                operator_id=verifier_id,
                target_status=ActionStatus.UNKNOWN,
            )
            report["checks"]["indeterminate_stays_unknown_same_attempt"] = (
                indeterminate_reconciling.task_status == "reconciling"
                and indeterminate.task_status == "outcome_unknown"
                and indeterminate.action_status == "unknown"
                and indeterminate.attempt_id == indeterminate_prepared.attempt_id
                and indeterminate.attempts == 1
                and indeterminate.evidence_rows == 0
                and automatic_retry_allowed(ActionStatus.UNKNOWN) is False
            )

            normal_transitions = [
                str(row[0])
                for row in connection.execute(text(
                    """
                    SELECT to_status FROM rtm_connect_transitions
                    WHERE action_id=CAST(:action_id AS UUID)
                    ORDER BY sequence_number
                    """
                ), {"action_id": normal_action.action_id}).fetchall()
            ]
            unknown_transitions = [
                str(row[0])
                for row in connection.execute(text(
                    """
                    SELECT to_status FROM rtm_connect_transitions
                    WHERE action_id=CAST(:action_id AS UUID)
                    ORDER BY sequence_number
                    """
                ), {"action_id": unknown_action.action_id}).fetchall()
            ]
            indeterminate_transitions = [
                str(row[0])
                for row in connection.execute(text(
                    """
                    SELECT to_status FROM rtm_connect_transitions
                    WHERE action_id=CAST(:action_id AS UUID)
                    ORDER BY sequence_number
                    """
                ), {"action_id": indeterminate_action.action_id}).fetchall()
            ]
            report["checks"]["action_ledgers_complete"] = (
                normal_transitions == [
                    "draft", "authorized", "queued", "executing",
                    "external_accepted", "evidence_pending", "confirmed",
                ]
                and unknown_transitions == [
                    "draft", "authorized", "queued", "executing",
                    "unknown", "reconciling", "confirmed",
                ]
                and indeterminate_transitions == [
                    "draft", "authorized", "queued", "executing",
                    "unknown", "reconciling", "unknown",
                ]
            )

            normal_events = [
                str(row[0])
                for row in connection.execute(text(
                    """
                    SELECT event_type FROM rtm_connect_assisted_events
                    WHERE task_id=CAST(:task_id AS UUID)
                    ORDER BY sequence_number
                    """
                ), {"task_id": normal_prepared.task_id}).fetchall()
            ]
            unknown_events = [
                str(row[0])
                for row in connection.execute(text(
                    """
                    SELECT event_type FROM rtm_connect_assisted_events
                    WHERE task_id=CAST(:task_id AS UUID)
                    ORDER BY sequence_number
                    """
                ), {"task_id": unknown_prepared.task_id}).fetchall()
            ]
            indeterminate_events = [
                str(row[0])
                for row in connection.execute(text(
                    """
                    SELECT event_type FROM rtm_connect_assisted_events
                    WHERE task_id=CAST(:task_id AS UUID)
                    ORDER BY sequence_number
                    """
                ), {"task_id": indeterminate_prepared.task_id}).fetchall()
            ]
            report["checks"]["assisted_event_ledgers_complete"] = (
                normal_events == [
                    "assisted.prepared", "assisted.assigned",
                    "assisted.reviewing", "assisted.ready_for_release",
                    "assisted.released", "assisted.in_progress",
                    "assisted.awaiting_receipt",
                    "assisted.receipt_submitted", "assisted.verified",
                    "assisted.completed",
                ]
                and unknown_events == [
                    "assisted.prepared", "assisted.assigned",
                    "assisted.reviewing", "assisted.ready_for_release",
                    "assisted.released", "assisted.in_progress",
                    "assisted.outcome_unknown", "assisted.reconciling",
                    "assisted.receipt_submitted", "assisted.verified",
                    "assisted.completed",
                ]
                and indeterminate_events == [
                    "assisted.prepared", "assisted.assigned",
                    "assisted.reviewing", "assisted.ready_for_release",
                    "assisted.released", "assisted.in_progress",
                    "assisted.outcome_unknown", "assisted.reconciling",
                    "assisted.outcome_unknown",
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

            report["checks"]["package_tampering_blocked"] = trigger_blocks(
                """
                UPDATE rtm_connect_assisted_tasks
                SET package_sha256=:tampered, version=version+1
                WHERE id=CAST(:task_id AS UUID)
                """,
                {
                    "tampered": "0" * 64,
                    "task_id": normal_prepared.task_id,
                },
            )
            report["checks"]["assisted_events_append_only"] = trigger_blocks(
                """
                UPDATE rtm_connect_assisted_events
                SET reason_code='tampered'
                WHERE task_id=CAST(:task_id AS UUID)
                """,
                {"task_id": normal_prepared.task_id},
            )
            report["checks"]["task_cross_attempt_scope_blocked"] = (
                trigger_blocks(
                    """
                    UPDATE rtm_connect_assisted_tasks
                    SET attempt_id=CAST(:unknown_attempt_id AS UUID),
                        version=version+1
                    WHERE id=CAST(:normal_task_id AS UUID)
                    """,
                    {
                        "unknown_attempt_id": unknown_prepared.attempt_id,
                        "normal_task_id": normal_prepared.task_id,
                    },
                )
            )
            report["checks"]["event_cross_parent_scope_blocked"] = (
                trigger_blocks(
                    """
                    INSERT INTO rtm_connect_assisted_events(
                        id, task_id, action_id, attempt_id,
                        sequence_number, event_type, actor_type,
                        operator_id, from_status, to_status,
                        reason_code, payload, created_at
                    ) VALUES (
                        CAST(:event_id AS UUID),
                        CAST(:normal_task_id AS UUID),
                        CAST(:unknown_action_id AS UUID),
                        CAST(:unknown_attempt_id AS UUID),
                        999999, 'assisted.scope_probe', 'system',
                        NULL, NULL, NULL,
                        'synthetic_cross_scope_probe', '{}'::jsonb, NOW()
                    )
                    """,
                    {
                        "event_id": str(uuid.uuid4()),
                        "normal_task_id": normal_prepared.task_id,
                        "unknown_action_id": unknown_action.action_id,
                        "unknown_attempt_id": unknown_prepared.attempt_id,
                    },
                )
            )

            in_tx = connection.execute(text(
                """
                SELECT
                  (SELECT COUNT(*) FROM rtm_connect_connectors
                   WHERE code='assisted.legal') AS connectors,
                  (SELECT COUNT(*) FROM rtm_connect_actions
                   WHERE id IN (CAST(:normal_action AS UUID),
                                CAST(:unknown_action AS UUID),
                                CAST(:indeterminate_action AS UUID))) AS actions,
                  (SELECT COUNT(*) FROM rtm_connect_attempts
                   WHERE action_id IN (CAST(:normal_action AS UUID),
                                       CAST(:unknown_action AS UUID),
                                       CAST(:indeterminate_action AS UUID))) AS attempts,
                  (SELECT COUNT(*) FROM rtm_connect_assisted_tasks
                   WHERE id IN (CAST(:normal_task AS UUID),
                                CAST(:unknown_task AS UUID),
                                CAST(:indeterminate_task AS UUID))) AS tasks,
                  (SELECT COUNT(*) FROM rtm_connect_assisted_events
                   WHERE task_id IN (CAST(:normal_task AS UUID),
                                     CAST(:unknown_task AS UUID),
                                     CAST(:indeterminate_task AS UUID))) AS events
                """
            ), {
                "normal_action": normal_action.action_id,
                "unknown_action": unknown_action.action_id,
                "indeterminate_action": indeterminate_action.action_id,
                "normal_task": normal_prepared.task_id,
                "unknown_task": unknown_prepared.task_id,
                "indeterminate_task": indeterminate_prepared.task_id,
            }).mappings().one()
            report["checks"]["single_connector_three_transactional_flows"] = (
                int(in_tx["connectors"]) == 1
                and int(in_tx["actions"]) == 3
                and int(in_tx["attempts"]) == 3
                and int(in_tx["tasks"]) == 3
                and int(in_tx["events"]) == 30
            )
            report["checks"]["no_network_routes_or_external_effects"] = (
                report["network_used"] is False
                and report["real_administration_contacted"] is False
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
            remaining = verification.execute(text(
                """
                SELECT
                  (SELECT COUNT(*) FROM rtm_connect_actions
                   WHERE id IN (CAST(:normal_action AS UUID),
                                CAST(:unknown_action AS UUID),
                                CAST(:indeterminate_action AS UUID))) AS actions,
                  (SELECT COUNT(*) FROM rtm_connect_connectors
                   WHERE id=CAST(:connector_id AS UUID)) AS connectors,
                  (SELECT COUNT(*) FROM rtm_connect_attempts
                   WHERE id IN (CAST(:normal_attempt AS UUID),
                                CAST(:unknown_attempt AS UUID),
                                CAST(:indeterminate_attempt AS UUID))) AS attempts,
                  (SELECT COUNT(*) FROM rtm_connect_assisted_tasks
                   WHERE id IN (CAST(:normal_task AS UUID),
                                CAST(:unknown_task AS UUID),
                                CAST(:indeterminate_task AS UUID))) AS tasks,
                  (SELECT COUNT(*) FROM rtm_connect_assisted_events
                   WHERE task_id IN (CAST(:normal_task AS UUID),
                                     CAST(:unknown_task AS UUID),
                                     CAST(:indeterminate_task AS UUID))) AS events,
                  (SELECT COUNT(*) FROM rtm_connect_evidence
                   WHERE action_id IN (CAST(:normal_action AS UUID),
                                       CAST(:unknown_action AS UUID),
                                       CAST(:indeterminate_action AS UUID))) AS evidence,
                  (SELECT COUNT(*) FROM rtm_operators
                   WHERE id IN (CAST(:requester AS UUID),
                                CAST(:assignee AS UUID),
                                CAST(:release AS UUID),
                                CAST(:verifier AS UUID))) AS operators,
                  (SELECT COUNT(*) FROM rtm_operator_roles
                   WHERE id=CAST(:role_id AS UUID)) AS roles
                """
            ), {
                "normal_action": ids["normal_action_id"],
                "unknown_action": ids["unknown_action_id"],
                "indeterminate_action": ids["indeterminate_action_id"],
                "connector_id": ids["connector_id"],
                "normal_attempt": ids["normal_attempt_id"],
                "unknown_attempt": ids["unknown_attempt_id"],
                "indeterminate_attempt": ids["indeterminate_attempt_id"],
                "normal_task": ids["normal_task_id"],
                "unknown_task": ids["unknown_task_id"],
                "indeterminate_task": ids["indeterminate_task_id"],
                "requester": ids["requester_id"],
                "assignee": ids["assignee_id"],
                "release": ids["release_id"],
                "verifier": ids["verifier_id"],
                "role_id": ids["role_id"],
            }).mappings().one()
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
