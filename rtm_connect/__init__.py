"""RTM CONNECT — contratos arquitectónicos congelados en C0.

C0 no publica rutas, no crea tablas y no ejecuta efectos externos.
"""

from rtm_connect.authority import (
    AuthorityValidationError,
    validate_execution_authority,
)
from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectExecutionResult,
    ConnectorMode,
    EvidenceLevel,
    RiskClass,
)
from rtm_connect.idempotency import (
    canonical_json,
    derive_idempotency_key,
    payload_sha256,
)
from rtm_connect.manifest import (
    RTM_CONNECT_ARCHITECTURE_VERSION,
    RTM_CONNECT_C0_VERSION,
    architecture_manifest,
    assert_manifest_frozen,
    manifest_sha256,
)
from rtm_connect.state_machine import (
    ActionStatus,
    assert_transition,
    automatic_retry_allowed,
    can_transition,
    next_states,
)


__all__ = [
    "ActionStatus",
    "AuthorityValidationError",
    "AuthorizationGrant",
    "ConnectActionRequest",
    "ConnectExecutionResult",
    "ConnectorMode",
    "EvidenceLevel",
    "RTM_CONNECT_ARCHITECTURE_VERSION",
    "RTM_CONNECT_C0_VERSION",
    "RiskClass",
    "architecture_manifest",
    "assert_manifest_frozen",
    "assert_transition",
    "automatic_retry_allowed",
    "can_transition",
    "canonical_json",
    "derive_idempotency_key",
    "manifest_sha256",
    "next_states",
    "payload_sha256",
    "validate_execution_authority",
]
