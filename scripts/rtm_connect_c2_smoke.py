#!/usr/bin/env python3
"""Smoke transaccional del conector synthetic.echo de RTM CONNECT C2."""

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


def _grant(action, *, operator_id: str, required_evidence, legal_effect=False):
    from rtm_connect.contracts import AuthorizationGrant, ConnectorMode
    from rtm_connect.idempotency import derive_idempotency_key, payload_sha256
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
        required_evidence_level=required_evidence,
        authorized_connector_modes=(ConnectorMode.API,),
        approved_by_operator_ids=(operator_id,),
        authorized_at=_now(),
        expires_at=(
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
        legal_effect_authorized=bool(legal_effect),
    )


def main() -> int:
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_connect_c2_smoke",
        "version": "rtm_connect_c2_smoke_v1_0",
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
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
            "synthetic_actions_remaining": None,
            "synthetic_connectors_remaining": None,
            "synthetic_operators_remaining": None,
            "synthetic_roles_remaining": None,
        },
        "synthetic_ids": {},
    }
    blockers = _blockers()
    if blockers:
        report["blockers"] = blockers
        _print(report)
        return 2

    action_ids: list[str] = []
    role_id = str(uuid.uuid4())
    operator_id = str(uuid.uuid4())
    try:
        from sqlalchemy import text
        from database import get_engine
        from rtm_connect.connectors.synthetic_echo import (
            SyntheticEchoConnector,
            SyntheticEchoScenario,
        )
        from rtm_connect.contracts import (
            ConnectActionRequest,
            EvidenceLevel,
            RiskClass,
        )
        from rtm_connect.execution import (
            ExistingActionReplayBlocked,
            execute_synthetic_echo,
            reconcile_synthetic_echo,
        )

        engine = get_engine()
        connection = engine.connect()
        transaction = connection.begin()
        try:
            suffix = uuid.uuid4().hex[:12]
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
                    "code": f"synthetic.connect.c2.{suffix}",
                    "name": "RTM CONNECT C2 SMOKE",
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
                        '{"synthetic": true, "purpose": "connect_c2_smoke"}'::jsonb,
                        0, 'argon2id', 1, 1, NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": operator_id,
                    "email": f"rtm-staging-connect-c2-{suffix}@example.com",
                    "display_name": "RTM CONNECT C2 SMOKE",
                    "role_id": role_id,
                },
            )
            report["checks"]["synthetic_operator_inserted"] = True

            success_id = str(uuid.uuid4())
            action_ids.append(success_id)
            success_action = ConnectActionRequest(
                action_id=success_id,
                capability="synthetic.echo",
                satellite="synthetic",
                target_type="synthetic.endpoint",
                target_ref="echo-success",
                payload={
                    "message": "RTM CONNECT C2 deterministic success",
                    "sequence": 1,
                },
                document_hashes=("a" * 64,),
                requested_by_operator_id=operator_id,
                requested_at=_now(),
                risk_class=RiskClass.R2_BUSINESS_EFFECT,
            )
            success_grant = _grant(
                success_action,
                operator_id=operator_id,
                required_evidence=EvidenceLevel.E4_RECEIPT_VERIFIED,
            )
            success = execute_synthetic_echo(
                connection,
                action=success_action,
                grant=success_grant,
                scenario=SyntheticEchoScenario.SUCCESS,
                operator_id=operator_id,
            )
            report["synthetic_ids"]["connector_id"] = success.connector_id
            report["synthetic_ids"]["success_action_id"] = success_id
            report["checks"]["success_action_confirmed"] = (
                success.confirmed
                and success.status == "confirmed"
                and success.attempts == 1
                and success.evidence_rows == 1
                and success.evidence_level == "E4_receipt_verified"
            )

            replay = execute_synthetic_echo(
                connection,
                action=success_action,
                grant=success_grant,
                scenario=SyntheticEchoScenario.SUCCESS,
                operator_id=operator_id,
            )
            report["checks"]["confirmed_replay_reused_action"] = (
                replay.replayed
                and replay.attempt_id is None
                and replay.attempts == 1
                and replay.replay_count == 1
                and replay.status == "confirmed"
            )

            attempt_id = success.attempt_id
            pure_one = SyntheticEchoConnector().execute(
                success_action,
                attempt_id=attempt_id,
                scenario=SyntheticEchoScenario.SUCCESS,
            )
            pure_two = SyntheticEchoConnector().execute(
                success_action,
                attempt_id=attempt_id,
                scenario=SyntheticEchoScenario.SUCCESS,
            )
            report["checks"]["pure_connector_is_deterministic"] = (
                pure_one == pure_two
            )

            unknown_id = str(uuid.uuid4())
            action_ids.append(unknown_id)
            unknown_action = ConnectActionRequest(
                action_id=unknown_id,
                capability="synthetic.echo",
                satellite="synthetic",
                target_type="synthetic.endpoint",
                target_ref="echo-unknown",
                payload={
                    "message": "RTM CONNECT C2 unknown then reconcile",
                    "sequence": 2,
                },
                requested_by_operator_id=operator_id,
                requested_at=_now(),
                risk_class=RiskClass.R3_LEGAL_OR_FINANCIAL,
            )
            unknown_grant = _grant(
                unknown_action,
                operator_id=operator_id,
                required_evidence=EvidenceLevel.E4_RECEIPT_VERIFIED,
                legal_effect=True,
            )
            unknown = execute_synthetic_echo(
                connection,
                action=unknown_action,
                grant=unknown_grant,
                scenario=SyntheticEchoScenario.UNKNOWN,
                operator_id=operator_id,
            )
            report["synthetic_ids"]["unknown_action_id"] = unknown_id
            report["checks"]["unknown_action_persisted_as_unknown"] = (
                unknown.status == "unknown"
                and not unknown.confirmed
                and unknown.evidence_level == "E2_external_reference"
                and unknown.attempts == 1
            )
            blocked = False
            try:
                execute_synthetic_echo(
                    connection,
                    action=unknown_action,
                    grant=unknown_grant,
                    scenario=SyntheticEchoScenario.UNKNOWN,
                    operator_id=operator_id,
                )
            except ExistingActionReplayBlocked:
                blocked = True
            report["checks"]["unknown_replay_blocked_before_execution"] = blocked

            reconciled = reconcile_synthetic_echo(
                connection,
                action=unknown_action,
                operator_id=operator_id,
            )
            report["checks"]["unknown_reconciled_to_confirmed"] = (
                reconciled.status == "confirmed"
                and reconciled.confirmed
                and reconciled.evidence_rows == 2
                and reconciled.evidence_level == "E4_receipt_verified"
            )
            unknown_attempt = connection.execute(
                text(
                    """
                    SELECT status, reconciliation_required
                    FROM rtm_connect_attempts
                    WHERE action_id=CAST(:id AS UUID)
                    """
                ),
                {"id": unknown_id},
            ).mappings().one()
            report["checks"]["reconciliation_closed_attempt"] = (
                str(unknown_attempt["status"]) == "succeeded"
                and not bool(unknown_attempt["reconciliation_required"])
            )

            expected_unknown_transitions = [
                "draft", "authorized", "queued", "executing",
                "unknown", "reconciling", "confirmed",
            ]
            transition_values = [
                str(row[0])
                for row in connection.execute(
                    text(
                        """
                        SELECT to_status
                        FROM rtm_connect_transitions
                        WHERE action_id=CAST(:id AS UUID)
                        ORDER BY sequence_number ASC
                        """
                    ),
                    {"id": unknown_id},
                ).fetchall()
            ]
            report["checks"]["unknown_transition_ledger_complete"] = (
                transition_values == expected_unknown_transitions
            )

            failure_expectations = (
                (
                    SyntheticEchoScenario.RETRYABLE_FAILURE,
                    "retryable_failed",
                    True,
                ),
                (
                    SyntheticEchoScenario.PERMANENT_FAILURE,
                    "permanent_failed",
                    False,
                ),
                (
                    SyntheticEchoScenario.MANUAL_REVIEW,
                    "manual_review",
                    False,
                ),
            )
            normalized_failures = True
            for index, (scenario, expected_status, expected_retryable) in enumerate(
                failure_expectations,
                start=3,
            ):
                action_id = str(uuid.uuid4())
                action_ids.append(action_id)
                action = ConnectActionRequest(
                    action_id=action_id,
                    capability="synthetic.echo",
                    satellite="synthetic",
                    target_type="synthetic.endpoint",
                    target_ref=f"echo-{scenario.value}",
                    payload={"scenario": scenario.value, "sequence": index},
                    requested_by_operator_id=operator_id,
                    requested_at=_now(),
                    risk_class=RiskClass.R1_LOW_REVERSIBLE,
                )
                grant = _grant(
                    action,
                    operator_id=operator_id,
                    required_evidence=EvidenceLevel.E1_REQUEST_RECORDED,
                )
                outcome = execute_synthetic_echo(
                    connection,
                    action=action,
                    grant=grant,
                    scenario=scenario,
                    operator_id=operator_id,
                )
                attempt_row = connection.execute(
                    text(
                        """
                        SELECT status, retryable, error_code, result_metadata
                        FROM rtm_connect_attempts
                        WHERE action_id=CAST(:id AS UUID)
                        """
                    ),
                    {"id": action_id},
                ).mappings().one()
                normalized_failures = normalized_failures and (
                    outcome.status == expected_status
                    and outcome.evidence_level == "E1_request_recorded"
                    and str(attempt_row["status"]) == "failed"
                    and bool(attempt_row["retryable"]) is expected_retryable
                    and bool(attempt_row["error_code"])
                    and attempt_row["result_metadata"].get("network_used") is False
                )
            report["checks"]["failure_modes_normalized"] = normalized_failures

            connector_row = connection.execute(
                text(
                    """
                    SELECT code, version, mode, synthetic_only,
                           credential_ref, configuration
                    FROM rtm_connect_connectors
                    WHERE id=CAST(:id AS UUID)
                    """
                ),
                {"id": success.connector_id},
            ).mappings().one()
            connector_count = connection.execute(
                text("SELECT COUNT(*) FROM rtm_connect_connectors")
            ).scalar_one()
            report["checks"]["single_synthetic_echo_connector_in_transaction"] = (
                int(connector_count) == 1
                and str(connector_row["code"]) == "synthetic.echo"
                and str(connector_row["version"]) == "v1.0"
                and str(connector_row["mode"]) == "api"
                and bool(connector_row["synthetic_only"])
                and connector_row["credential_ref"] is None
                and connector_row["configuration"].get("network_used") is False
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
            action_params = {
                f"action_id_{index}": value
                for index, value in enumerate(action_ids)
            }
            action_placeholders = ", ".join(
                f"CAST(:action_id_{index} AS UUID)"
                for index in range(len(action_ids))
            )
            remaining_actions = verification.execute(
                text(
                    "SELECT COUNT(*) FROM rtm_connect_actions "
                    f"WHERE id IN ({action_placeholders})"
                ),
                action_params,
            ).scalar_one()
            remaining_connectors = verification.execute(
                text(
                    """
                    SELECT COUNT(*) FROM rtm_connect_connectors
                    WHERE code='synthetic.echo' AND version='v1.0'
                    """
                )
            ).scalar_one()
            remaining_operators = verification.execute(
                text(
                    """
                    SELECT COUNT(*) FROM rtm_operators
                    WHERE id=CAST(:id AS UUID)
                    """
                ),
                {"id": operator_id},
            ).scalar_one()
            remaining_roles = verification.execute(
                text(
                    """
                    SELECT COUNT(*) FROM rtm_operator_roles
                    WHERE id=CAST(:id AS UUID)
                    """
                ),
                {"id": role_id},
            ).scalar_one()
        report["cleanup"]["synthetic_actions_remaining"] = int(
            remaining_actions
        )
        report["cleanup"]["synthetic_connectors_remaining"] = int(
            remaining_connectors
        )
        report["cleanup"]["synthetic_operators_remaining"] = int(
            remaining_operators
        )
        report["cleanup"]["synthetic_roles_remaining"] = int(
            remaining_roles
        )
        report["checks"]["rollback_removed_synthetic_records"] = (
            int(remaining_actions) == 0
            and int(remaining_connectors) == 0
            and int(remaining_operators) == 0
            and int(remaining_roles) == 0
        )
        report["tests_ok"] = all(
            bool(value) for value in report["checks"].values()
        )
        report["ok"] = bool(
            report["tests_ok"]
            and report["cleanup"]["database_rolled_back"]
        )
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
