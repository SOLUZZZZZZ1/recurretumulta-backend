"""Fachada del Kernel RTM CONNECT C1.

La fachada conserva una API interna pequeña y explícita. No usa red, no publica
endpoints y no decide jurídicamente.
"""

from __future__ import annotations

from rtm_connect.repository import (
    ActionCreateOutcome,
    AttemptStart,
    ConnectKernelError,
    ConnectorNotEligible,
    ConnectorRegistration,
    EvidenceGateError,
    IdempotencyConflict,
    action_snapshot,
    authorize_action,
    begin_reconciliation,
    confirm_action,
    create_action,
    queue_action,
    record_attempt_outcome,
    record_reconciliation_outcome,
    record_evidence,
    register_synthetic_connector,
    start_attempt,
)


RTM_CONNECT_C1_KERNEL_VERSION = "rtm_connect_c1_kernel_v1_0"


__all__ = [
    "RTM_CONNECT_C1_KERNEL_VERSION",
    "ActionCreateOutcome",
    "AttemptStart",
    "ConnectKernelError",
    "ConnectorNotEligible",
    "ConnectorRegistration",
    "EvidenceGateError",
    "IdempotencyConflict",
    "action_snapshot",
    "authorize_action",
    "begin_reconciliation",
    "confirm_action",
    "create_action",
    "queue_action",
    "record_attempt_outcome",
    "record_reconciliation_outcome",
    "record_evidence",
    "register_synthetic_connector",
    "start_attempt",
]
