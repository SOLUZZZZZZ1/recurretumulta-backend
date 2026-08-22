"""Máquina de estados congelada de RTM CONNECT C0."""

from __future__ import annotations

from enum import Enum


RTM_CONNECT_STATE_MACHINE_VERSION = "rtm_connect_state_machine_v1_0"


class ActionStatus(str, Enum):
    DRAFT = "draft"
    AUTHORIZED = "authorized"
    QUEUED = "queued"
    EXECUTING = "executing"
    EXTERNAL_ACCEPTED = "external_accepted"
    EVIDENCE_PENDING = "evidence_pending"
    CONFIRMED = "confirmed"
    RETRYABLE_FAILED = "retryable_failed"
    UNKNOWN = "unknown"
    RECONCILING = "reconciling"
    MANUAL_REVIEW = "manual_review"
    PERMANENT_FAILED = "permanent_failed"
    CANCELLED = "cancelled"


_TRANSITIONS: dict[ActionStatus, frozenset[ActionStatus]] = {
    ActionStatus.DRAFT: frozenset(
        {ActionStatus.AUTHORIZED, ActionStatus.CANCELLED}
    ),
    ActionStatus.AUTHORIZED: frozenset(
        {ActionStatus.QUEUED, ActionStatus.CANCELLED}
    ),
    ActionStatus.QUEUED: frozenset(
        {ActionStatus.EXECUTING, ActionStatus.CANCELLED}
    ),
    ActionStatus.EXECUTING: frozenset(
        {
            ActionStatus.EXTERNAL_ACCEPTED,
            ActionStatus.CONFIRMED,
            ActionStatus.RETRYABLE_FAILED,
            ActionStatus.UNKNOWN,
            ActionStatus.MANUAL_REVIEW,
            ActionStatus.PERMANENT_FAILED,
        }
    ),
    ActionStatus.EXTERNAL_ACCEPTED: frozenset(
        {
            ActionStatus.EVIDENCE_PENDING,
            ActionStatus.CONFIRMED,
            ActionStatus.UNKNOWN,
            ActionStatus.RECONCILING,
            ActionStatus.MANUAL_REVIEW,
        }
    ),
    ActionStatus.EVIDENCE_PENDING: frozenset(
        {
            ActionStatus.CONFIRMED,
            ActionStatus.UNKNOWN,
            ActionStatus.RECONCILING,
            ActionStatus.MANUAL_REVIEW,
        }
    ),
    ActionStatus.RETRYABLE_FAILED: frozenset(
        {
            ActionStatus.QUEUED,
            ActionStatus.RECONCILING,
            ActionStatus.MANUAL_REVIEW,
            ActionStatus.CANCELLED,
        }
    ),
    ActionStatus.UNKNOWN: frozenset(
        {ActionStatus.RECONCILING, ActionStatus.MANUAL_REVIEW}
    ),
    ActionStatus.RECONCILING: frozenset(
        {
            ActionStatus.CONFIRMED,
            ActionStatus.RETRYABLE_FAILED,
            ActionStatus.UNKNOWN,
            ActionStatus.MANUAL_REVIEW,
            ActionStatus.PERMANENT_FAILED,
        }
    ),
    ActionStatus.MANUAL_REVIEW: frozenset(
        {
            ActionStatus.QUEUED,
            ActionStatus.RECONCILING,
            ActionStatus.CONFIRMED,
            ActionStatus.PERMANENT_FAILED,
            ActionStatus.CANCELLED,
        }
    ),
    ActionStatus.CONFIRMED: frozenset(),
    ActionStatus.PERMANENT_FAILED: frozenset(),
    ActionStatus.CANCELLED: frozenset(),
}


class InvalidActionTransition(RuntimeError):
    pass


def next_states(status: ActionStatus) -> tuple[ActionStatus, ...]:
    return tuple(sorted(_TRANSITIONS[status], key=lambda item: item.value))


def can_transition(
    current: ActionStatus,
    target: ActionStatus,
) -> bool:
    return target in _TRANSITIONS[current]


def assert_transition(
    current: ActionStatus,
    target: ActionStatus,
) -> None:
    if not can_transition(current, target):
        raise InvalidActionTransition(
            f"Transición RTM CONNECT no permitida: "
            f"{current.value} -> {target.value}"
        )


def is_terminal(status: ActionStatus) -> bool:
    return not _TRANSITIONS[status]


def automatic_retry_allowed(status: ActionStatus) -> bool:
    # UNKNOWN jamás se reintenta a ciegas: requiere reconciliación.
    return status is ActionStatus.RETRYABLE_FAILED


def reconciliation_required(status: ActionStatus) -> bool:
    return status in {
        ActionStatus.UNKNOWN,
        ActionStatus.RECONCILING,
        ActionStatus.EVIDENCE_PENDING,
    }


__all__ = [
    "RTM_CONNECT_STATE_MACHINE_VERSION",
    "ActionStatus",
    "InvalidActionTransition",
    "assert_transition",
    "automatic_retry_allowed",
    "can_transition",
    "is_terminal",
    "next_states",
    "reconciliation_required",
]
