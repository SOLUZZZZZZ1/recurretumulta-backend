#!/usr/bin/env python3
"""Smoke PostgreSQL transaccional del plano inerte RTM CONNECT C8.

El smoke solo persiste identidades sintéticas, tres admisiones ``NO-GO`` y
tres outboxes de *dry-run* aisladas por release para respetar la cuota diaria
congelada a uno. No registra conectores, no crea intentos de ejecución, no
resuelve secretos, no abre red y no dispone de activación live. Toda escritura
se revierte y una conexión nueva verifica residuo cero.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SMOKE_VERSION = "rtm_connect_c8_smoke_v1_0"


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


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _synthetic_candidate(
    *,
    candidate_id: str,
    requester_id: str,
    base_time: datetime,
):
    from rtm_connect.production_contracts import ProductionAdmissionCandidate

    created = base_time - timedelta(minutes=10)
    expires = base_time + timedelta(hours=4)
    return ProductionAdmissionCandidate(
        candidate_id=candidate_id,
        requested_by_operator_id=requester_id,
        source_commit_sha40="6e8bc77e28cae4779cabc0d659086c1b4d06529b",
        build_artifact_sha256="1" * 64,
        connector_manifest_sha256="2" * 64,
        provider_contract_sha256="3" * 64,
        egress_policy_sha256="4" * 64,
        credential_reference_sha256="5" * 64,
        schema_snapshot_sha256="6" * 64,
        test_report_sha256="7" * 64,
        created_at=_stamp(created),
        expires_at=_stamp(expires),
        canary_percent=1,
        concurrency=1,
        max_simulated_actions_total=1,
        max_simulated_actions_per_day=1,
        max_payload_bytes=4096,
        admission_ttl_seconds=18000,
    )


def _synthetic_action_and_grant(
    *,
    candidate,
    action_id: str,
    authorization_id: str,
    security_operator_id: str,
    operations_operator_id: str,
    base_time: datetime,
):
    from rtm_connect.contracts import (
        AuthorizationGrant,
        ConnectActionRequest,
        EvidenceLevel,
        RiskClass,
    )
    from rtm_connect.idempotency import derive_idempotency_key, payload_sha256
    from rtm_connect.production_contracts import (
        candidate_sha256,
        expected_c8_admission_payload,
    )
    from rtm_connect.production_policy import (
        C8_ADMISSION_AUTHORITY_CODE,
        C8_ADMISSION_AUTHORITY_VERSION,
        C8_ADMISSION_CAPABILITY,
        C8_ADMISSION_MODE,
        C8_ADMISSION_SATELLITE,
        C8_ADMISSION_TARGET_REF,
        C8_ADMISSION_TARGET_TYPE,
    )

    action = ConnectActionRequest(
        action_id=action_id,
        capability=C8_ADMISSION_CAPABILITY,
        satellite=C8_ADMISSION_SATELLITE,
        target_type=C8_ADMISSION_TARGET_TYPE,
        target_ref=C8_ADMISSION_TARGET_REF,
        payload=expected_c8_admission_payload(candidate_sha256(candidate)),
        requested_by_operator_id=candidate.requested_by_operator_id,
        requested_at=_stamp(base_time - timedelta(minutes=8)),
        risk_class=RiskClass.R4_CRITICAL_REGULATED,
        requires_dual_control=True,
    )
    grant = AuthorizationGrant(
        authorization_id=authorization_id,
        action_id=action.action_id,
        authority_code=C8_ADMISSION_AUTHORITY_CODE,
        authority_version=C8_ADMISSION_AUTHORITY_VERSION,
        decision="approved_frozen",
        payload_sha256=payload_sha256(action),
        idempotency_key=derive_idempotency_key(
            action,
            authority_scope=C8_ADMISSION_AUTHORITY_CODE,
        ),
        required_evidence_level=EvidenceLevel.E4_RECEIPT_VERIFIED,
        authorized_connector_modes=(C8_ADMISSION_MODE,),
        approved_by_operator_ids=(
            security_operator_id,
            operations_operator_id,
        ),
        authorized_at=_stamp(base_time - timedelta(minutes=7)),
        expires_at=_stamp(base_time + timedelta(hours=3)),
        legal_effect_authorized=False,
    )
    return action, grant


def _persist_inert_action_and_grant(conn, action, grant) -> None:
    """Persiste el ledger C1 sin llamar al validador de ejecución sensible.

    C8 no autoriza efecto legal y, por diseño, no puede usar la ruta genérica
    que prepara ejecución R4. La política C8 valida el par exacto antes y
    después de persistirlo; aquí solo se registra la admisión sintética.
    """

    from sqlalchemy import text

    from rtm_connect.production_policy import validate_c8_admission_authority
    from rtm_connect.repository import create_action

    validate_c8_admission_authority(action, grant)
    created = create_action(
        conn,
        action=action,
        authority_scope=grant.authority_code,
    )
    if not created.created or created.action_id != action.action_id:
        raise RuntimeError("La acción sintética C8 no se creó una sola vez")
    conn.execute(text(
        """
        INSERT INTO rtm_connect_authorizations(
            id, action_id, authorization_version, supersedes_id,
            authority_code, authority_version, decision, payload_sha256,
            idempotency_key, required_evidence_level,
            authorized_connector_modes, approved_by_operator_ids,
            authorized_at, expires_at, revoked_at,
            legal_effect_authorized, frozen, metadata, created_at
        ) VALUES (
            CAST(:id AS UUID), CAST(:action_id AS UUID), 1, NULL,
            :authority_code, :authority_version, :decision, :payload_sha256,
            :idempotency_key, :required_evidence_level,
            CAST(:authorized_modes AS JSONB), CAST(:approvers AS JSONB),
            CAST(:authorized_at AS TIMESTAMPTZ),
            CAST(:expires_at AS TIMESTAMPTZ), NULL,
            FALSE, TRUE, CAST(:metadata AS JSONB), NOW()
        )
        """
    ), {
        "id": grant.authorization_id,
        "action_id": grant.action_id,
        "authority_code": grant.authority_code,
        "authority_version": grant.authority_version,
        "decision": grant.decision,
        "payload_sha256": grant.payload_sha256,
        "idempotency_key": grant.idempotency_key,
        "required_evidence_level": grant.required_evidence_level.value,
        "authorized_modes": json.dumps([
            mode.value for mode in grant.authorized_connector_modes
        ]),
        "approvers": json.dumps(list(grant.approved_by_operator_ids)),
        "authorized_at": grant.authorized_at,
        "expires_at": grant.expires_at,
        "metadata": json.dumps({
            "synthetic": True,
            "environment": "staging",
            "purpose": "connect_c8_admission_smoke",
            "legal_effect_authorized": False,
        }),
    })
    conn.execute(text(
        """
        UPDATE rtm_connect_actions
        SET status='authorized', status_version=status_version + 1,
            updated_at=NOW()
        WHERE id=CAST(:action_id AS UUID) AND status='draft'
        """
    ), {"action_id": action.action_id})
    conn.execute(text(
        """
        INSERT INTO rtm_connect_transitions(
            id, action_id, attempt_id, sequence_number,
            from_status, to_status, actor_type, operator_id,
            reason_code, reason_detail, request_id, metadata, created_at
        ) VALUES (
            CAST(:id AS UUID), CAST(:action_id AS UUID), NULL, 2,
            'draft', 'authorized', 'core', CAST(:operator_id AS UUID),
            'c8_simulation_authorization_frozen', NULL, NULL,
            CAST(:metadata AS JSONB), NOW()
        )
        """
    ), {
        "id": str(uuid.uuid4()),
        "action_id": action.action_id,
        "operator_id": grant.approved_by_operator_ids[0],
        "metadata": json.dumps({
            "authorization_id": grant.authorization_id,
            "simulation_only": True,
            "legal_effect_authorized": False,
        }),
    })


def _intent(
    *,
    intent_id: str,
    candidate,
    action,
    grant,
    created_at: datetime,
):
    from rtm_connect.idempotency import payload_sha256
    from rtm_connect.production_contracts import (
        SimulatedOutboxIntent,
        SimulatedOutboxStatus,
        candidate_sha256,
    )

    return SimulatedOutboxIntent(
        intent_id=intent_id,
        candidate_id=candidate.candidate_id,
        action_id=action.action_id,
        authorization_id=grant.authorization_id,
        candidate_sha256=candidate_sha256(candidate),
        request_sha256=payload_sha256(action),
        idempotency_key=grant.idempotency_key,
        status=SimulatedOutboxStatus.PREPARED,
        created_at=_stamp(created_at),
        reconciliation_required=False,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_connect_c8_smoke",
        "version": SMOKE_VERSION,
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "synthetic_only": True,
        "transactional": True,
        "production_effects_available": False,
        "live_activation_available": False,
        "network_used": False,
        "real_provider_contacted": False,
        "secret_resolution_performed": False,
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

    boundary = None
    try:
        from rtm_connect.production_policy import assert_c8_staging_boundary
        from scripts.rtm_staging_connect_c8_schema import safety_blockers

        boundary = assert_c8_staging_boundary(os.environ)
        report["blockers"].extend(safety_blockers())
    except Exception as exc:
        report["blockers"].append(
            f"connect_c8_boundary_error:{type(exc).__name__}:{exc}"
        )
    if report["blockers"]:
        _print(report, args.compact)
        return 2

    ids: dict[str, Any] = {}
    try:
        from sqlalchemy import text

        from database import get_engine
        from rtm_connect.production_contracts import (
            ProductionApprovalRole,
            ProductionReleaseApproval,
            SimulatedOutboxStatus,
            candidate_sha256,
        )
        from rtm_connect.production_control import (
            C8_HUMAN_GATE_PHRASE,
            ProductionDispatchReplayConflict,
            ProductionDispatchStateError,
            approve_production_release,
            claim_dispatch_dry_run,
            confirm_dispatch_dry_run,
            emergency_halt_production_release,
            mark_dispatch_unknown,
            mark_production_release_ready,
            move_dispatch_manual_review,
            prepare_dispatch_dry_run,
            propose_production_release,
            simulate_production_release_activation,
        )
        from rtm_connect.production_policy import (
            ProductionLiveActivationUnavailable,
            assert_c8_database_identity,
            assert_live_activation_unavailable,
            validate_c8_admission_authority,
        )
        from scripts.rtm_staging_connect_c8_schema import schema_snapshot

        live_blocked = False
        try:
            assert_live_activation_unavailable()
        except ProductionLiveActivationUnavailable:
            live_blocked = True
        report["checks"]["live_activation_guard_unconditional"] = live_blocked

        engine = get_engine()
        connection = engine.connect()
        transaction = connection.begin()
        try:
            assert boundary is not None
            report["connected_database"] = assert_c8_database_identity(
                connection,
                expected_database_name=boundary.database_name,
                expected_database_role=boundary.database_role,
            )
            snapshot = schema_snapshot(connection)
            if not snapshot.get("ready"):
                raise RuntimeError("Esquema C8 no está listo")
            report["checks"]["postgresql_identity_and_schema_ready"] = True

            suffix = uuid.uuid4().hex[:12]
            role_id = str(uuid.uuid4())
            requester_id = str(uuid.uuid4())
            security_id = str(uuid.uuid4())
            operations_id = str(uuid.uuid4())
            release_manager_id = str(uuid.uuid4())
            candidate_ids = {
                label: str(uuid.uuid4())
                for label in ("normal", "unknown", "pending")
            }
            action_ids = {
                label: str(uuid.uuid4())
                for label in ("normal", "unknown", "pending")
            }
            authorization_ids = {
                label: str(uuid.uuid4())
                for label in ("normal", "unknown", "pending")
            }
            ids.update({
                "role_id": role_id,
                "requester_id": requester_id,
                "security_id": security_id,
                "operations_id": operations_id,
                "release_manager_id": release_manager_id,
                "candidate_ids": candidate_ids,
                "action_ids": action_ids,
                "authorization_ids": authorization_ids,
            })

            baseline = dict(connection.execute(text(
                """
                SELECT
                  (SELECT COUNT(*) FROM rtm_connect_connectors) AS connectors,
                  (SELECT COUNT(*) FROM rtm_connect_attempts) AS attempts
                """
            )).mappings().one())

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
                "code": f"synthetic.connect.c8.{suffix}",
                "name": "RTM CONNECT C8 SMOKE",
                "permissions": json.dumps([
                    "connect.production.admission.request",
                    "connect.production.admission.security",
                    "connect.production.admission.operations",
                    "connect.production.admission.simulate",
                    "connect.production.admission.halt",
                ]),
            })
            for operator_id, label in (
                (requester_id, "requester"),
                (security_id, "security"),
                (operations_id, "operations"),
                (release_manager_id, "release-manager"),
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
                    "email": f"rtm-connect-c8-{label}-{suffix}@example.com",
                    "display_name": f"RTM C8 {label.upper()}",
                    "role_id": role_id,
                    "profile": json.dumps({
                        "synthetic": True,
                        "environment": "staging",
                        "purpose": "connect_c8_smoke",
                    }),
                })
            report["checks"]["synthetic_four_party_separation_inserted"] = (
                len({requester_id, security_id, operations_id, release_manager_id})
                == 4
            )

            base_time = datetime.now(timezone.utc)
            candidates: dict[str, Any] = {}
            actions: dict[str, Any] = {}
            grants: dict[str, Any] = {}
            for label in ("normal", "unknown", "pending"):
                candidate = _synthetic_candidate(
                    candidate_id=candidate_ids[label],
                    requester_id=requester_id,
                    base_time=base_time,
                )
                action, grant = _synthetic_action_and_grant(
                    candidate=candidate,
                    action_id=action_ids[label],
                    authorization_id=authorization_ids[label],
                    security_operator_id=security_id,
                    operations_operator_id=operations_id,
                    base_time=base_time,
                )
                validate_c8_admission_authority(
                    action,
                    grant,
                    candidate=candidate,
                    now=base_time,
                )
                _persist_inert_action_and_grant(connection, action, grant)
                candidates[label] = candidate
                actions[label] = action
                grants[label] = grant
            ids["idempotency_keys"] = {
                label: grants[label].idempotency_key
                for label in ("normal", "unknown", "pending")
            }
            report["checks"]["r4_e4_authority_frozen_without_legal_effect"] = (
                len(set(ids["idempotency_keys"].values())) == 3
                and all(
                    actions[label].risk_class.value == "R4_critical_regulated"
                    and grants[label].required_evidence_level.value
                    == "E4_receipt_verified"
                    and len(grants[label].approved_by_operator_ids) == 2
                    and grants[label].legal_effect_authorized is False
                    for label in ("normal", "unknown", "pending")
                )
            )

            release_paths: dict[str, dict[str, Any]] = {}

            def activate_simulated_release(label: str) -> dict[str, Any]:
                selected = candidates[label]
                proposed = propose_production_release(
                    connection,
                    selected,
                    now=base_time,
                    policy_values=os.environ,
                )
                digest = candidate_sha256(selected)
                security = ProductionReleaseApproval(
                    approval_id=str(uuid.uuid4()),
                    candidate_id=selected.candidate_id,
                    candidate_sha256=digest,
                    requested_by_operator_id=requester_id,
                    approver_operator_id=security_id,
                    approval_role=ProductionApprovalRole.SECURITY,
                    approved_at=_stamp(base_time - timedelta(minutes=2)),
                    expires_at=_stamp(base_time + timedelta(hours=2)),
                )
                operations = ProductionReleaseApproval(
                    approval_id=str(uuid.uuid4()),
                    candidate_id=selected.candidate_id,
                    candidate_sha256=digest,
                    requested_by_operator_id=requester_id,
                    approver_operator_id=operations_id,
                    approval_role=ProductionApprovalRole.OPERATIONS,
                    approved_at=_stamp(base_time - timedelta(minutes=1)),
                    expires_at=_stamp(base_time + timedelta(hours=2)),
                )
                security_approved = approve_production_release(
                    connection,
                    selected.candidate_id,
                    security,
                    expected_version=int(proposed["version"]),
                    now=base_time,
                    policy_values=os.environ,
                )
                operations_approved = approve_production_release(
                    connection,
                    selected.candidate_id,
                    operations,
                    expected_version=int(security_approved["version"]),
                    now=base_time,
                    policy_values=os.environ,
                )
                ready = mark_production_release_ready(
                    connection,
                    selected.candidate_id,
                    operator_id=operations_id,
                    expected_version=int(operations_approved["version"]),
                    now=base_time,
                    policy_values=os.environ,
                )
                simulated = simulate_production_release_activation(
                    connection,
                    selected.candidate_id,
                    operator_id=release_manager_id,
                    expected_version=int(ready["version"]),
                    human_gate_phrase=C8_HUMAN_GATE_PHRASE,
                    now=base_time,
                    policy_values=os.environ,
                )
                return {
                    "proposed": proposed,
                    "security": security_approved,
                    "operations": operations_approved,
                    "ready": ready,
                    "simulated": simulated,
                }

            for label in ("normal", "unknown", "pending"):
                release_paths[label] = activate_simulated_release(label)
            report["checks"]["release_four_party_path_simulated_only"] = all(
                path["proposed"]["status"] == "proposed"
                and path["security"]["status"] == "security_approved"
                and path["operations"]["status"] == "operations_approved"
                and path["ready"]["status"] == "ready"
                and path["simulated"]["status"] == "simulated_active"
                and path["simulated"]["simulation_only"] is True
                and path["simulated"]["external_effects_allowed"] is False
                and path["simulated"]["live_activation_allowed"] is False
                and path["simulated"]["provider_pack_present"] is False
                for path in release_paths.values()
            )

            normal_id = str(uuid.uuid4())
            unknown_id = str(uuid.uuid4())
            pending_id = str(uuid.uuid4())
            blocked_id = str(uuid.uuid4())
            ids["dispatch_ids"] = [normal_id, unknown_id, pending_id, blocked_id]

            normal_candidate = candidates["normal"]
            normal_action = actions["normal"]
            normal_grant = grants["normal"]
            normal_request = _intent(
                intent_id=normal_id,
                candidate=normal_candidate,
                action=normal_action,
                grant=normal_grant,
                created_at=base_time,
            )
            normal_prepared = prepare_dispatch_dry_run(
                connection,
                normal_action,
                normal_grant,
                normal_request,
                normal_candidate.candidate_id,
                now=base_time,
                policy_values=os.environ,
            )
            exact_replay = prepare_dispatch_dry_run(
                connection,
                normal_action,
                normal_grant,
                normal_request,
                normal_candidate.candidate_id,
                now=base_time,
                policy_values=os.environ,
            )
            report["checks"]["exact_replay_reuses_dispatch"] = (
                normal_prepared == exact_replay
                and exact_replay.status is SimulatedOutboxStatus.PREPARED
                and int(connection.execute(text(
                    """
                    SELECT COUNT(*) FROM rtm_connect_dispatch_outbox
                    WHERE id=CAST(:dispatch_id AS UUID)
                    """
                ), {"dispatch_id": normal_id}).scalar_one()) == 1
            )
            changed_replay_blocked = False
            try:
                prepare_dispatch_dry_run(
                    connection,
                    normal_action,
                    normal_grant,
                    replace(
                        normal_request,
                        intent_id=blocked_id,
                    ),
                    normal_candidate.candidate_id,
                    now=base_time,
                    policy_values=os.environ,
                )
            except ProductionDispatchReplayConflict:
                changed_replay_blocked = True
            report["checks"]["changed_replay_conflict_blocked"] = (
                changed_replay_blocked
            )

            normal_token = "8" * 64
            normal_claimed = claim_dispatch_dry_run(
                connection,
                normal_id,
                expected_version=1,
                claim_token_sha256=normal_token,
                claim_owner="rtm-c8-smoke-worker",
                now=base_time,
                policy_values=os.environ,
            )
            normal_done = confirm_dispatch_dry_run(
                connection,
                normal_id,
                expected_version=2,
                claim_owner="rtm-c8-smoke-worker",
                claim_token_sha256=normal_token,
                claim_fence=1,
                now=base_time,
                policy_values=os.environ,
            )
            report["checks"]["normal_dry_run_confirmed_without_effect"] = (
                normal_claimed.status is SimulatedOutboxStatus.CLAIMED
                and normal_done.status is SimulatedOutboxStatus.DRY_RUN_CONFIRMED
                and normal_done.external_effects_allowed is False
                and normal_done.network_call_performed is False
                and normal_done.secret_resolution_performed is False
            )

            unknown_candidate = candidates["unknown"]
            unknown_action = actions["unknown"]
            unknown_grant = grants["unknown"]
            unknown_request = _intent(
                intent_id=unknown_id,
                candidate=unknown_candidate,
                action=unknown_action,
                grant=unknown_grant,
                created_at=base_time + timedelta(seconds=2),
            )
            prepare_dispatch_dry_run(
                connection,
                unknown_action,
                unknown_grant,
                unknown_request,
                unknown_candidate.candidate_id,
                now=base_time,
                policy_values=os.environ,
            )
            unknown_token = "9" * 64
            claim_dispatch_dry_run(
                connection,
                unknown_id,
                expected_version=1,
                claim_token_sha256=unknown_token,
                claim_owner="rtm-c8-smoke-worker-unknown",
                now=base_time,
                policy_values=os.environ,
            )
            unknown = mark_dispatch_unknown(
                connection,
                unknown_id,
                expected_version=2,
                claim_owner="rtm-c8-smoke-worker-unknown",
                claim_token_sha256=unknown_token,
                claim_fence=1,
                now=base_time,
                policy_values=os.environ,
            )
            blind_retry_blocked = False
            try:
                claim_dispatch_dry_run(
                    connection,
                    unknown_id,
                    expected_version=3,
                    claim_token_sha256="a" * 64,
                    claim_owner="rtm-c8-smoke-blind-retry",
                    now=base_time,
                    policy_values=os.environ,
                )
            except ProductionDispatchStateError:
                blind_retry_blocked = True
            manual_review = move_dispatch_manual_review(
                connection,
                unknown_id,
                operator_id=release_manager_id,
                expected_version=3,
                claim_owner="rtm-c8-smoke-worker-unknown",
                claim_token_sha256=unknown_token,
                claim_fence=1,
                now=base_time,
                policy_values=os.environ,
            )
            report["checks"]["unknown_never_blindly_retried"] = (
                unknown.status is SimulatedOutboxStatus.UNKNOWN
                and unknown.reconciliation_required
                and unknown.blind_retry_allowed is False
                and blind_retry_blocked
                and manual_review.status is SimulatedOutboxStatus.MANUAL_REVIEW
                and manual_review.reconciliation_required
            )

            pending_candidate = candidates["pending"]
            pending_action = actions["pending"]
            pending_grant = grants["pending"]
            pending_request = _intent(
                intent_id=pending_id,
                candidate=pending_candidate,
                action=pending_action,
                grant=pending_grant,
                created_at=base_time + timedelta(seconds=3),
            )
            prepare_dispatch_dry_run(
                connection,
                pending_action,
                pending_grant,
                pending_request,
                pending_candidate.candidate_id,
                now=base_time,
                policy_values=os.environ,
            )
            halted = emergency_halt_production_release(
                connection,
                pending_candidate.candidate_id,
                operator_id=release_manager_id,
                reason_code="synthetic_emergency_stop",
                expected_version=int(
                    release_paths["pending"]["simulated"]["version"]
                ),
                now=base_time,
                policy_values=os.environ,
            )
            claim_after_halt_blocked = False
            try:
                claim_dispatch_dry_run(
                    connection,
                    pending_id,
                    expected_version=1,
                    claim_token_sha256="b" * 64,
                    claim_owner="rtm-c8-smoke-after-halt",
                    now=base_time,
                    policy_values=os.environ,
                )
            except ProductionDispatchStateError:
                claim_after_halt_blocked = True
            prepare_after_halt_blocked = False
            try:
                prepare_dispatch_dry_run(
                    connection,
                    pending_action,
                    pending_grant,
                    _intent(
                        intent_id=blocked_id,
                        candidate=pending_candidate,
                        action=pending_action,
                        grant=pending_grant,
                        created_at=base_time + timedelta(seconds=4),
                    ),
                    pending_candidate.candidate_id,
                    now=base_time,
                    policy_values=os.environ,
                )
            except ProductionDispatchStateError:
                prepare_after_halt_blocked = True
            report["checks"]["emergency_halt_blocks_new_dispatch"] = (
                halted["status"] == "halted"
                and bool(halted["emergency_halt"])
                and claim_after_halt_blocked
                and prepare_after_halt_blocked
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

            report["checks"]["release_binding_tampering_blocked"] = (
                trigger_blocks(
                    """
                    UPDATE rtm_connect_production_releases
                    SET release_binding_sha256=:tampered
                    WHERE id=CAST(:release_id AS UUID)
                    """,
                    {
                        "tampered": "0" * 64,
                        "release_id": normal_candidate.candidate_id,
                    },
                )
            )
            report["checks"]["release_events_append_only"] = trigger_blocks(
                """
                UPDATE rtm_connect_production_release_events
                SET reason_code='tampered'
                WHERE release_id=CAST(:release_id AS UUID)
                """,
                {"release_id": normal_candidate.candidate_id},
            )
            report["checks"]["dispatch_identity_tampering_blocked"] = (
                trigger_blocks(
                    """
                    UPDATE rtm_connect_dispatch_outbox
                    SET request_sha256=:tampered
                    WHERE id=CAST(:dispatch_id AS UUID)
                    """,
                    {"tampered": "0" * 64, "dispatch_id": normal_id},
                )
            )
            report["checks"]["dispatch_events_append_only"] = trigger_blocks(
                """
                UPDATE rtm_connect_dispatch_events
                SET reason_code='tampered'
                WHERE outbox_id=CAST(:dispatch_id AS UUID)
                """,
                {"dispatch_id": normal_id},
            )

            in_tx = dict(connection.execute(text(
                """
                SELECT
                  (SELECT COUNT(*) FROM rtm_connect_production_releases
                   WHERE id IN (
                     CAST(:normal_release_id AS UUID),
                     CAST(:unknown_release_id AS UUID),
                     CAST(:pending_release_id AS UUID)
                   )) AS releases,
                  (SELECT COUNT(*) FROM rtm_connect_production_release_events
                   WHERE release_id IN (
                     CAST(:normal_release_id AS UUID),
                     CAST(:unknown_release_id AS UUID),
                     CAST(:pending_release_id AS UUID)
                   )) AS release_events,
                  (SELECT COUNT(*) FROM rtm_connect_dispatch_outbox
                   WHERE release_id IN (
                     CAST(:normal_release_id AS UUID),
                     CAST(:unknown_release_id AS UUID),
                     CAST(:pending_release_id AS UUID)
                   )) AS outbox,
                  (SELECT COUNT(*) FROM rtm_connect_dispatch_events
                   WHERE release_id IN (
                     CAST(:normal_release_id AS UUID),
                     CAST(:unknown_release_id AS UUID),
                     CAST(:pending_release_id AS UUID)
                   )) AS dispatch_events,
                  (SELECT COUNT(*) FROM rtm_connect_actions
                   WHERE id IN (
                     CAST(:normal_action_id AS UUID),
                     CAST(:unknown_action_id AS UUID),
                     CAST(:pending_action_id AS UUID)
                   )) AS actions,
                  (SELECT COUNT(*) FROM rtm_connect_authorizations
                   WHERE id IN (
                     CAST(:normal_authorization_id AS UUID),
                     CAST(:unknown_authorization_id AS UUID),
                     CAST(:pending_authorization_id AS UUID)
                   )) AS authorizations,
                  (SELECT COUNT(*) FROM rtm_connect_idempotency_claims
                   WHERE idempotency_key IN (
                     :normal_idempotency_key,
                     :unknown_idempotency_key,
                     :pending_idempotency_key
                   )) AS idempotency_claims,
                  (SELECT COUNT(*) FROM rtm_connect_transitions
                   WHERE action_id IN (
                     CAST(:normal_action_id AS UUID),
                     CAST(:unknown_action_id AS UUID),
                     CAST(:pending_action_id AS UUID)
                   )) AS transitions,
                  (SELECT COUNT(*) FROM rtm_connect_attempts
                   WHERE action_id IN (
                     CAST(:normal_action_id AS UUID),
                     CAST(:unknown_action_id AS UUID),
                     CAST(:pending_action_id AS UUID)
                   )) AS action_attempts,
                  (SELECT MIN(daily_action_limit)
                   FROM rtm_connect_production_releases
                   WHERE id IN (
                     CAST(:normal_release_id AS UUID),
                     CAST(:unknown_release_id AS UUID),
                     CAST(:pending_release_id AS UUID)
                   )) AS min_daily_limit,
                  (SELECT MAX(daily_action_limit)
                   FROM rtm_connect_production_releases
                   WHERE id IN (
                     CAST(:normal_release_id AS UUID),
                     CAST(:unknown_release_id AS UUID),
                     CAST(:pending_release_id AS UUID)
                   )) AS max_daily_limit,
                  (SELECT COUNT(*) FROM rtm_connect_connectors) AS connectors,
                  (SELECT COUNT(*) FROM rtm_connect_attempts) AS attempts
                """
            ), {
                "normal_release_id": candidate_ids["normal"],
                "unknown_release_id": candidate_ids["unknown"],
                "pending_release_id": candidate_ids["pending"],
                "normal_action_id": action_ids["normal"],
                "unknown_action_id": action_ids["unknown"],
                "pending_action_id": action_ids["pending"],
                "normal_authorization_id": authorization_ids["normal"],
                "unknown_authorization_id": authorization_ids["unknown"],
                "pending_authorization_id": authorization_ids["pending"],
                "normal_idempotency_key": grants["normal"].idempotency_key,
                "unknown_idempotency_key": grants["unknown"].idempotency_key,
                "pending_idempotency_key": grants["pending"].idempotency_key,
            }).mappings().one())
            report["checks"]["single_transaction_contains_only_inert_ledgers"] = (
                int(in_tx["releases"]) == 3
                and int(in_tx["release_events"]) == 16
                and int(in_tx["outbox"]) == 3
                and int(in_tx["dispatch_events"]) == 8
                and int(in_tx["actions"]) == 3
                and int(in_tx["authorizations"]) == 3
                and int(in_tx["idempotency_claims"]) == 3
                and int(in_tx["transitions"]) == 6
                and int(in_tx["action_attempts"]) == 0
                and int(in_tx["min_daily_limit"]) == 1
                and int(in_tx["max_daily_limit"]) == 1
                and int(in_tx["connectors"]) == int(baseline["connectors"])
                and int(in_tx["attempts"]) == int(baseline["attempts"])
            )
            report["checks"]["no_network_secret_routes_or_effects"] = (
                report["network_used"] is False
                and report["real_provider_contacted"] is False
                and report["secret_resolution_performed"] is False
                and report["routes_published"] is False
                and report["schema_changes_applied"] is False
                and report["external_effects_executed"] is False
                and report["production_effects_available"] is False
                and report["live_activation_available"] is False
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
            remaining = dict(verification.execute(text(
                """
                SELECT
                  (SELECT COUNT(*) FROM rtm_connect_production_releases
                   WHERE id IN (
                     CAST(:normal_release_id AS UUID),
                     CAST(:unknown_release_id AS UUID),
                     CAST(:pending_release_id AS UUID)
                   )) AS releases,
                  (SELECT COUNT(*) FROM rtm_connect_production_release_events
                   WHERE release_id IN (
                     CAST(:normal_release_id AS UUID),
                     CAST(:unknown_release_id AS UUID),
                     CAST(:pending_release_id AS UUID)
                   )) AS release_events,
                  (SELECT COUNT(*) FROM rtm_connect_dispatch_outbox
                   WHERE release_id IN (
                     CAST(:normal_release_id AS UUID),
                     CAST(:unknown_release_id AS UUID),
                     CAST(:pending_release_id AS UUID)
                   )) AS outbox,
                  (SELECT COUNT(*) FROM rtm_connect_dispatch_events
                   WHERE release_id IN (
                     CAST(:normal_release_id AS UUID),
                     CAST(:unknown_release_id AS UUID),
                     CAST(:pending_release_id AS UUID)
                   )) AS dispatch_events,
                  (SELECT COUNT(*) FROM rtm_connect_actions
                   WHERE id IN (
                     CAST(:normal_action_id AS UUID),
                     CAST(:unknown_action_id AS UUID),
                     CAST(:pending_action_id AS UUID)
                   )) AS actions,
                  (SELECT COUNT(*) FROM rtm_connect_authorizations
                   WHERE id IN (
                     CAST(:normal_authorization_id AS UUID),
                     CAST(:unknown_authorization_id AS UUID),
                     CAST(:pending_authorization_id AS UUID)
                   )) AS authorizations,
                  (SELECT COUNT(*) FROM rtm_connect_idempotency_claims
                   WHERE idempotency_key IN (
                     :normal_idempotency_key,
                     :unknown_idempotency_key,
                     :pending_idempotency_key
                   )) AS idempotency_claims,
                  (SELECT COUNT(*) FROM rtm_connect_transitions
                   WHERE action_id IN (
                     CAST(:normal_action_id AS UUID),
                     CAST(:unknown_action_id AS UUID),
                     CAST(:pending_action_id AS UUID)
                   )) AS transitions,
                  (SELECT COUNT(*) FROM rtm_connect_attempts
                   WHERE action_id IN (
                     CAST(:normal_action_id AS UUID),
                     CAST(:unknown_action_id AS UUID),
                     CAST(:pending_action_id AS UUID)
                   )) AS attempts,
                  (SELECT COUNT(*) FROM rtm_connect_evidence
                   WHERE action_id IN (
                     CAST(:normal_action_id AS UUID),
                     CAST(:unknown_action_id AS UUID),
                     CAST(:pending_action_id AS UUID)
                   )) AS evidence,
                  (SELECT COUNT(*) FROM rtm_operators
                   WHERE id IN (
                       CAST(:requester_id AS UUID), CAST(:security_id AS UUID),
                       CAST(:operations_id AS UUID),
                       CAST(:release_manager_id AS UUID)
                   )) AS operators,
                  (SELECT COUNT(*) FROM rtm_operator_roles
                   WHERE id=CAST(:role_id AS UUID)) AS roles
                """
            ), {
                "normal_release_id": ids["candidate_ids"]["normal"],
                "unknown_release_id": ids["candidate_ids"]["unknown"],
                "pending_release_id": ids["candidate_ids"]["pending"],
                "normal_action_id": ids["action_ids"]["normal"],
                "unknown_action_id": ids["action_ids"]["unknown"],
                "pending_action_id": ids["action_ids"]["pending"],
                "normal_authorization_id": (
                    ids["authorization_ids"]["normal"]
                ),
                "unknown_authorization_id": (
                    ids["authorization_ids"]["unknown"]
                ),
                "pending_authorization_id": (
                    ids["authorization_ids"]["pending"]
                ),
                "normal_idempotency_key": (
                    ids["idempotency_keys"]["normal"]
                ),
                "unknown_idempotency_key": (
                    ids["idempotency_keys"]["unknown"]
                ),
                "pending_idempotency_key": (
                    ids["idempotency_keys"]["pending"]
                ),
                "requester_id": ids["requester_id"],
                "security_id": ids["security_id"],
                "operations_id": ids["operations_id"],
                "release_manager_id": ids["release_manager_id"],
                "role_id": ids["role_id"],
            }).mappings().one())
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
