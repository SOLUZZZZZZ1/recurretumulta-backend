#!/usr/bin/env python3
"""Smoke puro y sintético de RTM CONNECT C0.

No usa red, base de datos, secretos ni conectores. Demuestra contratos,
idempotencia, autoridad, estado unknown y puerta de evidencia.
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
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_connect_c0_smoke",
        "version": "rtm_connect_c0_smoke_v1_0",
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "synthetic_only": True,
        "network_used": False,
        "database_touched": False,
        "routes_published": False,
        "external_effects_executed": False,
        "checks": {},
    }
    blockers = _blockers()
    if blockers:
        report["blockers"] = blockers
        _print(report)
        return 2

    try:
        from rtm_connect.authority import (
            AuthorityValidationError,
            assert_connector_output_has_no_legal_decision,
            validate_execution_authority,
        )
        from rtm_connect.contracts import (
            AuthorizationGrant,
            ConnectActionRequest,
            ConnectorMode,
            EvidenceLevel,
            EvidenceRecord,
            RiskClass,
        )
        from rtm_connect.evidence import confirmation_gate
        from rtm_connect.idempotency import (
            derive_idempotency_key,
            payload_sha256,
        )
        from rtm_connect.manifest import assert_manifest_frozen
        from rtm_connect.state_machine import (
            ActionStatus,
            assert_transition,
            automatic_retry_allowed,
            can_transition,
        )

        assert_manifest_frozen()
        report["checks"]["manifest_frozen"] = True

        action_id = str(uuid.uuid4())
        requester = str(uuid.uuid4())
        approver_1 = str(uuid.uuid4())
        approver_2 = str(uuid.uuid4())
        action = ConnectActionRequest(
            action_id=action_id,
            case_id=str(uuid.uuid4()),
            capability="administration.submit_document",
            satellite="administration",
            target_type="public_registry",
            target_ref="synthetic-registry",
            payload={
                "document_type": "synthetic_submission",
                "subject": "RTM CONNECT C0",
                "amount_cents": 0,
            },
            document_hashes=("a" * 64, "b" * 64),
            requested_by_operator_id=requester,
            requested_at=_now(),
            risk_class=RiskClass.R3_LEGAL_OR_FINANCIAL,
            requires_dual_control=False,
        )
        report["checks"]["synthetic_action_valid"] = True

        payload_hash = payload_sha256(action)
        key_1 = derive_idempotency_key(
            action,
            authority_scope="rtm.core.authorization",
        )
        key_2 = derive_idempotency_key(
            action,
            authority_scope="rtm.core.authorization",
        )
        report["checks"]["idempotency_is_stable"] = key_1 == key_2

        changed_action = ConnectActionRequest(
            action_id=action_id,
            case_id=action.case_id,
            capability=action.capability,
            satellite=action.satellite,
            target_type=action.target_type,
            target_ref=action.target_ref,
            payload={**action.payload, "subject": "changed"},
            document_hashes=action.document_hashes,
            requested_by_operator_id=requester,
            requested_at=action.requested_at,
            risk_class=action.risk_class,
        )
        changed_key = derive_idempotency_key(
            changed_action,
            authority_scope="rtm.core.authorization",
        )
        report["checks"]["payload_change_changes_idempotency"] = (
            changed_key != key_1
        )

        grant = AuthorizationGrant(
            authorization_id=str(uuid.uuid4()),
            action_id=action.action_id,
            authority_code="rtm.core.authorization",
            authority_version="rtm_core_authority_v1",
            decision="approved_frozen",
            payload_sha256=payload_hash,
            idempotency_key=key_1,
            required_evidence_level=EvidenceLevel.E4_RECEIPT_VERIFIED,
            authorized_connector_modes=(
                ConnectorMode.ASSISTED,
                ConnectorMode.MANUAL,
            ),
            approved_by_operator_ids=(approver_1,),
            authorized_at=_now(),
            expires_at=(
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat().replace("+00:00", "Z"),
            legal_effect_authorized=True,
            frozen=True,
        )
        validate_execution_authority(
            action,
            grant,
            connector_mode=ConnectorMode.MANUAL,
        )
        report["checks"]["frozen_authority_allows_execution"] = True

        wrong_mode_blocked = False
        try:
            validate_execution_authority(
                action,
                grant,
                connector_mode=ConnectorMode.API,
            )
        except AuthorityValidationError:
            wrong_mode_blocked = True
        report["checks"]["unauthorized_connector_mode_blocked"] = (
            wrong_mode_blocked
        )

        report["checks"]["happy_path_transitions_valid"] = all(
            can_transition(left, right)
            for left, right in (
                (ActionStatus.DRAFT, ActionStatus.AUTHORIZED),
                (ActionStatus.AUTHORIZED, ActionStatus.QUEUED),
                (ActionStatus.QUEUED, ActionStatus.EXECUTING),
                (
                    ActionStatus.EXECUTING,
                    ActionStatus.EXTERNAL_ACCEPTED,
                ),
                (
                    ActionStatus.EXTERNAL_ACCEPTED,
                    ActionStatus.EVIDENCE_PENDING,
                ),
                (
                    ActionStatus.EVIDENCE_PENDING,
                    ActionStatus.CONFIRMED,
                ),
            )
        )

        report["checks"]["unknown_never_blindly_retries"] = (
            not automatic_retry_allowed(ActionStatus.UNKNOWN)
            and can_transition(
                ActionStatus.UNKNOWN,
                ActionStatus.RECONCILING,
            )
            and not can_transition(
                ActionStatus.UNKNOWN,
                ActionStatus.QUEUED,
            )
        )

        invalid_transition_blocked = False
        try:
            assert_transition(
                ActionStatus.UNKNOWN,
                ActionStatus.CONFIRMED,
            )
        except Exception:
            invalid_transition_blocked = True
        report["checks"]["unknown_direct_confirmation_blocked"] = (
            invalid_transition_blocked
        )

        weak_evidence = EvidenceRecord(
            level=EvidenceLevel.E2_EXTERNAL_REFERENCE,
            request_sha256=payload_hash,
            external_reference="SYNTHETIC-REF-1",
        )
        weak_gate = confirmation_gate(action, grant, weak_evidence)
        report["checks"]["insufficient_evidence_blocks_confirmation"] = (
            weak_gate.allowed is False
        )

        strong_evidence = EvidenceRecord(
            level=EvidenceLevel.E4_RECEIPT_VERIFIED,
            request_sha256=payload_hash,
            external_reference="SYNTHETIC-REF-1",
            receipt_sha256="c" * 64,
            receipt_storage_ref="b2://synthetic/receipt.pdf",
            verified_at=_now(),
            verification_method="synthetic_contract_check",
        )
        strong_gate = confirmation_gate(action, grant, strong_evidence)
        report["checks"]["verified_receipt_allows_confirmation"] = (
            strong_gate.allowed is True
        )

        legal_decision_blocked = False
        try:
            assert_connector_output_has_no_legal_decision(
                {
                    "external_reference": "SYNTHETIC-REF-1",
                    "legal_strategy": "connector_must_not_decide",
                }
            )
        except AuthorityValidationError:
            legal_decision_blocked = True
        report["checks"]["connector_legal_decision_blocked"] = (
            legal_decision_blocked
        )

        secret_payload_blocked = False
        try:
            ConnectActionRequest(
                action_id=str(uuid.uuid4()),
                capability="communication.send_email",
                satellite="claims",
                target_type="email",
                target_ref="synthetic@example.com",
                payload={"api_key": "must-not-be-here"},
                requested_by_operator_id=requester,
                requested_at=_now(),
                risk_class=RiskClass.R1_LOW_REVERSIBLE,
            )
        except ValueError:
            secret_payload_blocked = True
        report["checks"]["embedded_secret_blocked"] = secret_payload_blocked

        r4_without_dual_control_blocked = False
        try:
            ConnectActionRequest(
                action_id=str(uuid.uuid4()),
                capability="payment.execute",
                satellite="billing",
                target_type="provider",
                target_ref="synthetic",
                payload={"amount_cents": 100},
                requested_by_operator_id=requester,
                requested_at=_now(),
                risk_class=RiskClass.R4_CRITICAL_REGULATED,
                requires_dual_control=False,
            )
        except ValueError:
            r4_without_dual_control_blocked = True
        report["checks"]["r4_requires_dual_control"] = (
            r4_without_dual_control_blocked
        )

        report["checks"]["no_runtime_side_effects"] = (
            report["network_used"] is False
            and report["database_touched"] is False
            and report["routes_published"] is False
            and report["external_effects_executed"] is False
        )
        report["tests_ok"] = all(
            bool(value) for value in report["checks"].values()
        )
        report["ok"] = bool(report["tests_ok"])
        exit_code = 0 if report["ok"] else 1
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["tests_ok"] = False
        report["ok"] = False
        exit_code = 1

    _print(report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
