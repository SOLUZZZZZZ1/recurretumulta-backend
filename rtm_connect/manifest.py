"""Manifiesto arquitectónico congelado de RTM CONNECT C0."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


RTM_CONNECT_ARCHITECTURE_VERSION = "rtm_connect_architecture_v1_0"
RTM_CONNECT_C0_VERSION = "rtm_connect_c0_v1_0"

_MANIFEST: dict[str, Any] = {'architecture_version': 'rtm_connect_architecture_v1_0', 'c0_version': 'rtm_connect_c0_v1_0', 'authority_rule': 'CORE authorizes; CONNECT executes; evidence confirms; only then CORE may change legal state', 'runtime_published': False, 'database_schema_created': False, 'external_effects_enabled': False, 'components': ['capability_catalog', 'action_ledger', 'authorization_registry', 'attempt_ledger', 'connector_registry', 'idempotency_guard', 'webhook_inbox', 'evidence_store', 'reconciliation_engine', 'manual_handoff', 'dead_letter_queue', 'attention_bridge', 'operator_assignment_bridge', 'secret_resolver', 'observability'], 'connector_modes': ['api', 'webhook', 'polling', 'batch', 'assisted', 'manual'], 'states': ['draft', 'authorized', 'queued', 'executing', 'external_accepted', 'evidence_pending', 'confirmed', 'retryable_failed', 'unknown', 'reconciling', 'manual_review', 'permanent_failed', 'cancelled'], 'evidence_levels': ['E0_none', 'E1_request_recorded', 'E2_external_reference', 'E3_receipt_captured', 'E4_receipt_verified'], 'risk_classes': ['R0_observation', 'R1_low_reversible', 'R2_business_effect', 'R3_legal_or_financial', 'R4_critical_regulated'], 'invariants': ['connect_never_decides_legal_strategy', 'authorization_is_frozen_before_execution', 'payload_hash_must_match_authorization', 'idempotency_precedes_any_external_call', 'unknown_is_never_blindly_retried', 'confirmation_requires_evidence_by_risk', 'r4_requires_dual_control', 'secrets_are_referenced_not_embedded', 'all_attempts_and_transitions_are_audited', 'staging_executes_no_real_external_effect', 'manual_and_assisted_connectors_share_the_same_contract', 'core_changes_legal_state_only_after_confirmed_result'], 'first_implementation_order': ['C0_architecture_freeze', 'C1_kernel_and_schema', 'C2_synthetic_echo', 'C3_manual_handoff', 'C4_webhooks_unknown_reconciliation', 'C5_supervisor_panel', 'C6_first_provider_sandbox', 'C7_assisted_legal_connector', 'C8_controlled_production']}

EXPECTED_MANIFEST_SHA256 = "d82631d0fff1c27cd5ef1d6ae9c53970df1e4adfdc8ae65728a781009384473b"


def architecture_manifest() -> dict[str, Any]:
    return copy.deepcopy(_MANIFEST)


def manifest_sha256() -> str:
    canonical = json.dumps(
        _MANIFEST,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assert_manifest_frozen() -> None:
    actual = manifest_sha256()
    if actual != EXPECTED_MANIFEST_SHA256:
        raise RuntimeError(
            "El manifiesto RTM CONNECT C0 ha cambiado sin nueva versión"
        )


__all__ = [
    "EXPECTED_MANIFEST_SHA256",
    "RTM_CONNECT_ARCHITECTURE_VERSION",
    "RTM_CONNECT_C0_VERSION",
    "architecture_manifest",
    "assert_manifest_frozen",
    "manifest_sha256",
]
