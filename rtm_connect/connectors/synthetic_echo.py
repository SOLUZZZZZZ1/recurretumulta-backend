"""Conector determinista ``synthetic.echo`` de RTM CONNECT C2.

No usa red, credenciales, reloj de ejecución ni aleatoriedad propia. Para la
misma acción, intento y escenario produce exactamente la misma referencia,
evidencia y metadatos. Es exclusivo de staging y pruebas sintéticas.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from uuid import UUID

from rtm_connect.connectors.base import ConnectorDescriptor
from rtm_connect.contracts import (
    ConnectActionRequest,
    ConnectExecutionResult,
    ConnectorMode,
    EvidenceLevel,
    EvidenceRecord,
    RiskClass,
)
from rtm_connect.idempotency import canonical_json, payload_sha256


RTM_CONNECT_C2_SYNTHETIC_ECHO_VERSION = (
    "rtm_connect_c2_synthetic_echo_v1_0"
)
SYNTHETIC_ECHO_CODE = "synthetic.echo"
SYNTHETIC_ECHO_CONNECTOR_VERSION = "v1.0"
SYNTHETIC_ECHO_CAPABILITY = "synthetic.echo"
SYNTHETIC_ECHO_MANIFEST_SHA256 = "a75340e99ff10f9b65cdcc1203e020412d47050d1aa9ab9fbb46a8628f4738de"

_SYNTHETIC_ECHO_MANIFEST = {'connector_code': 'synthetic.echo', 'connector_version': 'v1.0', 'runtime_version': 'rtm_connect_c2_synthetic_echo_v1_0', 'mode': 'api', 'synthetic_only': True, 'network_used': False, 'credential_ref': None, 'capabilities': ['synthetic.echo'], 'risk_ceiling': 'R4_critical_regulated', 'supports_idempotency': True, 'supports_reconciliation': True, 'scenarios': ['success', 'unknown', 'retryable_failure', 'permanent_failure', 'manual_review'], 'invariants': ['deterministic_for_same_action_attempt_and_scenario', 'no_network_imports_or_calls', 'no_secret_or_credential_material', 'success_emits_e4_verified_receipt', 'unknown_emits_e2_and_requires_reconciliation', 'reconciliation_can_confirm_only_with_e4', 'confirmed_replay_never_creates_second_attempt', 'nonterminal_replay_never_executes_blindly']}


class SyntheticEchoScenario(str, Enum):
    SUCCESS = "success"
    UNKNOWN = "unknown"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"
    MANUAL_REVIEW = "manual_review"


class SyntheticEchoContractError(RuntimeError):
    pass


def synthetic_echo_manifest() -> dict[str, object]:
    return copy.deepcopy(_SYNTHETIC_ECHO_MANIFEST)


def synthetic_echo_manifest_sha256() -> str:
    canonical = json.dumps(
        _SYNTHETIC_ECHO_MANIFEST,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assert_synthetic_echo_manifest_frozen() -> None:
    if synthetic_echo_manifest_sha256() != SYNTHETIC_ECHO_MANIFEST_SHA256:
        raise RuntimeError(
            "El manifiesto synthetic.echo cambió sin nueva versión"
        )


def _validated_attempt_id(value: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("attempt_id debe ser UUID") from exc


def _normalized_scenario(value: SyntheticEchoScenario | str) -> SyntheticEchoScenario:
    if isinstance(value, SyntheticEchoScenario):
        return value
    try:
        return SyntheticEchoScenario(str(value))
    except ValueError as exc:
        raise SyntheticEchoContractError(
            "Escenario synthetic.echo no admitido"
        ) from exc


def _verified_at(action: ConnectActionRequest, *, seconds: int) -> str:
    parsed = datetime.fromisoformat(
        action.requested_at.replace("Z", "+00:00")
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (
        parsed.astimezone(timezone.utc) + timedelta(seconds=seconds)
    ).isoformat().replace("+00:00", "Z")


def _execution_digest(
    action: ConnectActionRequest,
    *,
    attempt_id: str,
    scenario: SyntheticEchoScenario,
    phase: str,
) -> str:
    material = {
        "connector": SYNTHETIC_ECHO_CODE,
        "version": SYNTHETIC_ECHO_CONNECTOR_VERSION,
        "phase": phase,
        "scenario": scenario.value,
        "action_id": action.action_id,
        "attempt_id": attempt_id,
        "payload_sha256": payload_sha256(action),
        "target_type": action.target_type,
        "target_ref": action.target_ref,
        "document_hashes": list(action.document_hashes),
    }
    return hashlib.sha256(
        canonical_json(material).encode("utf-8")
    ).hexdigest()


def _external_reference(digest: str) -> str:
    return f"SYN-ECHO-{digest[:24].upper()}"


def _receipt_payload(
    action: ConnectActionRequest,
    *,
    attempt_id: str,
    external_reference: str,
    phase: str,
) -> dict[str, object]:
    return {
        "format": "rtm.synthetic.echo.receipt.v1",
        "connector_code": SYNTHETIC_ECHO_CODE,
        "connector_version": SYNTHETIC_ECHO_CONNECTOR_VERSION,
        "phase": phase,
        "action_id": action.action_id,
        "attempt_id": attempt_id,
        "capability": action.capability,
        "target": {
            "type": action.target_type,
            "ref": action.target_ref,
        },
        "payload_sha256": payload_sha256(action),
        "echo": dict(action.payload),
        "document_hashes": list(action.document_hashes),
        "external_reference": external_reference,
        "network_used": False,
        "synthetic_only": True,
    }


def _receipt_sha256(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        canonical_json(payload).encode("utf-8")
    ).hexdigest()


class SyntheticEchoConnector:
    descriptor = ConnectorDescriptor(
        code=SYNTHETIC_ECHO_CODE,
        version=SYNTHETIC_ECHO_CONNECTOR_VERSION,
        mode=ConnectorMode.API,
        capabilities=(SYNTHETIC_ECHO_CAPABILITY,),
        risk_ceiling=RiskClass.R4_CRITICAL_REGULATED,
        supports_idempotency=True,
        supports_reconciliation=True,
        synthetic_only=True,
        network_used=False,
        manifest_sha256=SYNTHETIC_ECHO_MANIFEST_SHA256,
    )

    def _validate_action(self, action: ConnectActionRequest) -> None:
        if action.capability != SYNTHETIC_ECHO_CAPABILITY:
            raise SyntheticEchoContractError(
                "synthetic.echo solo admite su capacidad explícita"
            )

    def execute(
        self,
        action: ConnectActionRequest,
        *,
        attempt_id: str,
        scenario: SyntheticEchoScenario | str,
    ) -> ConnectExecutionResult:
        assert_synthetic_echo_manifest_frozen()
        self._validate_action(action)
        clean_attempt = _validated_attempt_id(attempt_id)
        selected = _normalized_scenario(scenario)
        digest = _execution_digest(
            action,
            attempt_id=clean_attempt,
            scenario=selected,
            phase="execute",
        )
        reference = _external_reference(digest)
        request_hash = payload_sha256(action)

        if selected is SyntheticEchoScenario.SUCCESS:
            receipt = _receipt_payload(
                action,
                attempt_id=clean_attempt,
                external_reference=reference,
                phase="execute",
            )
            evidence = EvidenceRecord(
                level=EvidenceLevel.E4_RECEIPT_VERIFIED,
                request_sha256=request_hash,
                external_reference=reference,
                receipt_sha256=_receipt_sha256(receipt),
                receipt_storage_ref=(
                    f"synthetic://echo/{action.action_id}/"
                    f"{clean_attempt}/receipt.json"
                ),
                verified_at=_verified_at(action, seconds=1),
                verification_method="synthetic_echo_deterministic_v1",
            )
            return ConnectExecutionResult(
                action_id=action.action_id,
                attempt_id=clean_attempt,
                connector_code=SYNTHETIC_ECHO_CODE,
                connector_version=SYNTHETIC_ECHO_CONNECTOR_VERSION,
                status="external_accepted",
                evidence=evidence,
                external_reference=reference,
                reconciliation_required=False,
                metadata={
                    "scenario": selected.value,
                    "execution_sha256": digest,
                    "echo_payload_sha256": request_hash,
                    "network_used": False,
                    "synthetic_only": True,
                },
            )

        if selected is SyntheticEchoScenario.UNKNOWN:
            evidence = EvidenceRecord(
                level=EvidenceLevel.E2_EXTERNAL_REFERENCE,
                request_sha256=request_hash,
                external_reference=reference,
            )
            return ConnectExecutionResult(
                action_id=action.action_id,
                attempt_id=clean_attempt,
                connector_code=SYNTHETIC_ECHO_CODE,
                connector_version=SYNTHETIC_ECHO_CONNECTOR_VERSION,
                status="unknown",
                evidence=evidence,
                external_reference=reference,
                failure_class="synthetic_indeterminate",
                error_code="synthetic_response_lost",
                reconciliation_required=True,
                metadata={
                    "scenario": selected.value,
                    "execution_sha256": digest,
                    "network_used": False,
                    "synthetic_only": True,
                },
            )

        status_map = {
            SyntheticEchoScenario.RETRYABLE_FAILURE: (
                "retryable_failed",
                "synthetic_transient",
                "synthetic_retryable_failure",
            ),
            SyntheticEchoScenario.PERMANENT_FAILURE: (
                "permanent_failed",
                "synthetic_permanent",
                "synthetic_permanent_failure",
            ),
            SyntheticEchoScenario.MANUAL_REVIEW: (
                "manual_review",
                "synthetic_manual_review",
                "synthetic_manual_review_required",
            ),
        }
        status, failure_class, error_code = status_map[selected]
        evidence = EvidenceRecord(
            level=EvidenceLevel.E1_REQUEST_RECORDED,
            request_sha256=request_hash,
        )
        return ConnectExecutionResult(
            action_id=action.action_id,
            attempt_id=clean_attempt,
            connector_code=SYNTHETIC_ECHO_CODE,
            connector_version=SYNTHETIC_ECHO_CONNECTOR_VERSION,
            status=status,
            evidence=evidence,
            failure_class=failure_class,
            error_code=error_code,
            reconciliation_required=False,
            metadata={
                "scenario": selected.value,
                "execution_sha256": digest,
                "network_used": False,
                "synthetic_only": True,
            },
        )

    def reconcile(
        self,
        action: ConnectActionRequest,
        *,
        attempt_id: str,
        external_reference: str,
    ) -> ConnectExecutionResult:
        assert_synthetic_echo_manifest_frozen()
        self._validate_action(action)
        clean_attempt = _validated_attempt_id(attempt_id)
        reference = str(external_reference or "").strip()
        if not reference.startswith("SYN-ECHO-"):
            raise SyntheticEchoContractError(
                "Referencia externa synthetic.echo no reconocida"
            )
        digest = _execution_digest(
            action,
            attempt_id=clean_attempt,
            scenario=SyntheticEchoScenario.UNKNOWN,
            phase="reconcile",
        )
        receipt = _receipt_payload(
            action,
            attempt_id=clean_attempt,
            external_reference=reference,
            phase="reconcile",
        )
        evidence = EvidenceRecord(
            level=EvidenceLevel.E4_RECEIPT_VERIFIED,
            request_sha256=payload_sha256(action),
            external_reference=reference,
            receipt_sha256=_receipt_sha256(receipt),
            receipt_storage_ref=(
                f"synthetic://echo/{action.action_id}/"
                f"{clean_attempt}/reconciliation.json"
            ),
            verified_at=_verified_at(action, seconds=2),
            verification_method="synthetic_echo_reconciliation_v1",
        )
        return ConnectExecutionResult(
            action_id=action.action_id,
            attempt_id=clean_attempt,
            connector_code=SYNTHETIC_ECHO_CODE,
            connector_version=SYNTHETIC_ECHO_CONNECTOR_VERSION,
            status="confirmed",
            evidence=evidence,
            external_reference=reference,
            reconciliation_required=False,
            metadata={
                "scenario": "unknown_reconciled",
                "reconciliation_sha256": digest,
                "network_used": False,
                "synthetic_only": True,
            },
        )


__all__ = [
    "RTM_CONNECT_C2_SYNTHETIC_ECHO_VERSION",
    "SYNTHETIC_ECHO_CAPABILITY",
    "SYNTHETIC_ECHO_CODE",
    "SYNTHETIC_ECHO_CONNECTOR_VERSION",
    "SYNTHETIC_ECHO_MANIFEST_SHA256",
    "SyntheticEchoConnector",
    "SyntheticEchoContractError",
    "SyntheticEchoScenario",
    "assert_synthetic_echo_manifest_frozen",
    "synthetic_echo_manifest",
    "synthetic_echo_manifest_sha256",
]
