"""Conector ``controlled.sandbox`` de RTM CONNECT C6.

Es un probe de contrato sobre datos sintéticos, no una integración con una
Administración o proveedor real. Usa red únicamente a través del transporte
inyectado y nunca adopta decisiones jurídicas.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any

from rtm_connect.authority import validate_execution_authority
from rtm_connect.connectors.base import ConnectorDescriptor
from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectExecutionResult,
    ConnectorMode,
    EvidenceLevel,
    EvidenceRecord,
    RiskClass,
)
from rtm_connect.idempotency import canonical_json, payload_sha256
from rtm_connect.provider_sandbox_policy import (
    CONTROLLED_SANDBOX_AUTHORITY_CODE,
    CONTROLLED_SANDBOX_AUTHORITY_VERSION,
    CONTROLLED_SANDBOX_CAPABILITY,
    CONTROLLED_SANDBOX_CODE,
    CONTROLLED_SANDBOX_CONNECTOR_VERSION,
    CONTROLLED_SANDBOX_CONTRACT_VERSION,
    CONTROLLED_SANDBOX_CREDENTIAL_REF,
    validate_c6_probe_authority,
)
from rtm_connect.provider_sandbox_transport import (
    ControlledSandboxProbe,
    ControlledSandboxTransport,
    ProviderSandboxTransportError,
    SandboxObservationStatus,
)


RTM_CONNECT_C6_CONTROLLED_SANDBOX_VERSION = (
    "rtm_connect_c6_controlled_sandbox_v1_0"
)
CONTROLLED_SANDBOX_MANIFEST_SHA256 = (
    "252d23864612213676785091da5d36389019cfb837820fa0d9ea2ec1b527a1c7"
)

_MANIFEST = {
    "connector_code": CONTROLLED_SANDBOX_CODE,
    "connector_version": CONTROLLED_SANDBOX_CONNECTOR_VERSION,
    "runtime_version": RTM_CONNECT_C6_CONTROLLED_SANDBOX_VERSION,
    "contract_version": CONTROLLED_SANDBOX_CONTRACT_VERSION,
    "authority_code": CONTROLLED_SANDBOX_AUTHORITY_CODE,
    "authority_version": CONTROLLED_SANDBOX_AUTHORITY_VERSION,
    "mode": "api",
    "capabilities": [CONTROLLED_SANDBOX_CAPABILITY],
    "risk_ceiling": "R1_low_reversible",
    "maximum_evidence": "E2_external_reference",
    "synthetic_only": True,
    "network_used": True,
    "supports_idempotency": True,
    "supports_reconciliation": True,
    "credential_ref": CONTROLLED_SANDBOX_CREDENTIAL_REF,
    "submit_path": "/v1/probes",
    "reconcile_path": "/v1/probes/by-client-reference/{action_id}",
    "invariants": [
        "no_real_provider_selected_or_contacted",
        "probe_body_is_stable_across_attempts",
        "idempotency_key_is_the_frozen_core_key",
        "authority_is_revalidated_immediately_before_network",
        "core_action_and_authorization_preexist_connect_execution",
        "http_200_alone_never_confirms",
        "accepted_requires_exact_e2_correlation",
        "ambiguous_transport_becomes_unknown",
        "unknown_is_never_blindly_retried",
        "reconciliation_is_get_only",
        "secret_value_is_never_persisted_or_reported",
        "no_case_document_personal_or_legal_payload",
    ],
}


class ControlledSandboxConnectorError(RuntimeError):
    pass


def controlled_sandbox_manifest() -> dict[str, Any]:
    return copy.deepcopy(_MANIFEST)


def controlled_sandbox_manifest_sha256() -> str:
    return hashlib.sha256(
        canonical_json(_MANIFEST).encode("utf-8")
    ).hexdigest()


def assert_controlled_sandbox_manifest_frozen() -> None:
    if controlled_sandbox_manifest_sha256() != CONTROLLED_SANDBOX_MANIFEST_SHA256:
        raise RuntimeError("El manifiesto controlled.sandbox cambió sin versión")


class ControlledSandboxConnector:
    __slots__ = ("_transport", "__sealed")

    descriptor = ConnectorDescriptor(
        code=CONTROLLED_SANDBOX_CODE,
        version=CONTROLLED_SANDBOX_CONNECTOR_VERSION,
        mode=ConnectorMode.API,
        capabilities=(CONTROLLED_SANDBOX_CAPABILITY,),
        risk_ceiling=RiskClass.R1_LOW_REVERSIBLE,
        supports_idempotency=True,
        supports_reconciliation=True,
        synthetic_only=True,
        network_used=True,
        manifest_sha256=CONTROLLED_SANDBOX_MANIFEST_SHA256,
    )

    def __init__(self, transport: ControlledSandboxTransport) -> None:
        object.__setattr__(self, "_transport", transport)
        object.__setattr__(self, "_ControlledSandboxConnector__sealed", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_ControlledSandboxConnector__sealed", False):
            raise AttributeError("ControlledSandboxConnector es inmutable")
        object.__setattr__(self, name, value)

    @property
    def loopback_test_only(self) -> bool:
        return self._transport.loopback_test_only

    def assert_runtime_sealed(self) -> None:
        if type(self) is not ControlledSandboxConnector:
            raise ControlledSandboxConnectorError(
                "Subclase de conector C6 no admitida"
            )
        if type(self._transport) is not ControlledSandboxTransport:
            raise ControlledSandboxConnectorError(
                "C6 exige el transporte sellado exacto"
            )
        self._transport.assert_runtime_sealed()

    @staticmethod
    def _evidence(
        action: ConnectActionRequest,
        *,
        external_reference: str | None,
        observed: bool,
    ) -> EvidenceRecord:
        return EvidenceRecord(
            level=(
                EvidenceLevel.E2_EXTERNAL_REFERENCE
                if observed else EvidenceLevel.E1_REQUEST_RECORDED
            ),
            request_sha256=payload_sha256(action),
            external_reference=(external_reference if observed else None),
        )

    def execute_authorized(
        self,
        action: ConnectActionRequest,
        grant: AuthorizationGrant,
        *,
        attempt_id: str,
    ) -> ConnectExecutionResult:
        validate_c6_probe_authority(action, grant)
        validate_execution_authority(
            action,
            grant,
            connector_mode=ConnectorMode.API,
        )
        request_hash = payload_sha256(action)
        probe = ControlledSandboxProbe(action.action_id, request_hash)
        try:
            observation = self._transport.submit(
                probe,
                idempotency_key=grant.idempotency_key,
            )
        except ProviderSandboxTransportError as exc:
            if not exc.network_call_performed:
                raise ControlledSandboxConnectorError(
                    "C6 bloqueó el transporte antes de enviar el POST"
                ) from None
            return ConnectExecutionResult(
                action_id=action.action_id,
                attempt_id=attempt_id,
                connector_code=CONTROLLED_SANDBOX_CODE,
                connector_version=CONTROLLED_SANDBOX_CONNECTOR_VERSION,
                status="unknown",
                evidence=self._evidence(
                    action,
                    external_reference=None,
                    observed=False,
                ),
                external_reference=probe.expected_external_reference,
                failure_class="ambiguous_transport",
                error_code="c6_sandbox_result_unknown",
                reconciliation_required=True,
                metadata={
                    "contract_version": CONTROLLED_SANDBOX_CONTRACT_VERSION,
                    "network_used": True,
                    "network_call_performed": exc.network_call_performed,
                    "sandbox_only": True,
                    "provider_observed": False,
                },
            )
        status_map = {
            SandboxObservationStatus.ACCEPTED: "external_accepted",
            SandboxObservationStatus.UNKNOWN: "unknown",
            SandboxObservationStatus.REJECTED: "permanent_failed",
        }
        return ConnectExecutionResult(
            action_id=action.action_id,
            attempt_id=attempt_id,
            connector_code=CONTROLLED_SANDBOX_CODE,
            connector_version=CONTROLLED_SANDBOX_CONNECTOR_VERSION,
            status=status_map[observation.status],
            evidence=self._evidence(
                action,
                external_reference=observation.external_reference,
                observed=True,
            ),
            external_reference=observation.external_reference,
            failure_class=(
                "provider_rejected"
                if observation.status is SandboxObservationStatus.REJECTED
                else None
            ),
            error_code=(
                "c6_sandbox_rejected"
                if observation.status is SandboxObservationStatus.REJECTED
                else None
            ),
            reconciliation_required=(
                observation.status is SandboxObservationStatus.UNKNOWN
            ),
            metadata={
                "contract_version": CONTROLLED_SANDBOX_CONTRACT_VERSION,
                "network_used": True,
                "network_call_performed": True,
                "sandbox_only": True,
                "provider_observed": True,
                "provider_outcome": observation.status.value,
            },
        )

    def reconcile_authorized(
        self,
        action: ConnectActionRequest,
        grant: AuthorizationGrant,
        *,
        attempt_id: str,
    ) -> ConnectExecutionResult:
        validate_c6_probe_authority(action, grant)
        validate_execution_authority(
            action,
            grant,
            connector_mode=ConnectorMode.API,
        )
        probe = ControlledSandboxProbe(action.action_id, payload_sha256(action))
        try:
            observation = self._transport.reconcile(
                probe,
                idempotency_key=grant.idempotency_key,
            )
        except ProviderSandboxTransportError as exc:
            if not exc.network_call_performed:
                raise ControlledSandboxConnectorError(
                    "C6 bloqueó el transporte antes de enviar el GET"
                ) from None
            return ConnectExecutionResult(
                action_id=action.action_id,
                attempt_id=attempt_id,
                connector_code=CONTROLLED_SANDBOX_CODE,
                connector_version=CONTROLLED_SANDBOX_CONNECTOR_VERSION,
                status="unknown",
                evidence=self._evidence(
                    action,
                    external_reference=None,
                    observed=False,
                ),
                external_reference=probe.expected_external_reference,
                failure_class="ambiguous_reconciliation",
                error_code="c6_sandbox_reconciliation_unknown",
                reconciliation_required=True,
                metadata={
                    "contract_version": CONTROLLED_SANDBOX_CONTRACT_VERSION,
                    "network_used": True,
                    "network_call_performed": exc.network_call_performed,
                    "sandbox_only": True,
                    "reconciliation_method": "get_only",
                    "provider_observed": False,
                },
            )
        status_map = {
            SandboxObservationStatus.ACCEPTED: "confirmed",
            SandboxObservationStatus.UNKNOWN: "unknown",
            SandboxObservationStatus.REJECTED: "permanent_failed",
        }
        return ConnectExecutionResult(
            action_id=action.action_id,
            attempt_id=attempt_id,
            connector_code=CONTROLLED_SANDBOX_CODE,
            connector_version=CONTROLLED_SANDBOX_CONNECTOR_VERSION,
            status=status_map[observation.status],
            evidence=self._evidence(
                action,
                external_reference=observation.external_reference,
                observed=True,
            ),
            external_reference=observation.external_reference,
            failure_class=(
                "provider_rejected"
                if observation.status is SandboxObservationStatus.REJECTED
                else None
            ),
            error_code=(
                "c6_sandbox_rejected"
                if observation.status is SandboxObservationStatus.REJECTED
                else None
            ),
            reconciliation_required=(
                observation.status is SandboxObservationStatus.UNKNOWN
            ),
            metadata={
                "contract_version": CONTROLLED_SANDBOX_CONTRACT_VERSION,
                "network_used": True,
                "network_call_performed": True,
                "sandbox_only": True,
                "reconciliation_method": "get_only",
                "provider_observed": True,
                "provider_outcome": observation.status.value,
            },
        )


__all__ = [
    "RTM_CONNECT_C6_CONTROLLED_SANDBOX_VERSION",
    "CONTROLLED_SANDBOX_MANIFEST_SHA256",
    "ControlledSandboxConnector",
    "ControlledSandboxConnectorError",
    "assert_controlled_sandbox_manifest_frozen",
    "controlled_sandbox_manifest",
    "controlled_sandbox_manifest_sha256",
]
