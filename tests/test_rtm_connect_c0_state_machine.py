from __future__ import annotations

import unittest

from rtm_connect.state_machine import (
    ActionStatus,
    InvalidActionTransition,
    assert_transition,
    automatic_retry_allowed,
    can_transition,
    is_terminal,
    next_states,
    reconciliation_required,
)


class ConnectC0StateMachineTest(unittest.TestCase):
    def test_happy_path_is_valid(self):
        path = (
            ActionStatus.DRAFT,
            ActionStatus.AUTHORIZED,
            ActionStatus.QUEUED,
            ActionStatus.EXECUTING,
            ActionStatus.EXTERNAL_ACCEPTED,
            ActionStatus.EVIDENCE_PENDING,
            ActionStatus.CONFIRMED,
        )
        self.assertTrue(
            all(
                can_transition(left, right)
                for left, right in zip(path, path[1:])
            )
        )

    def test_unknown_cannot_queue_or_confirm_directly(self):
        self.assertFalse(
            can_transition(ActionStatus.UNKNOWN, ActionStatus.QUEUED)
        )
        self.assertFalse(
            can_transition(ActionStatus.UNKNOWN, ActionStatus.CONFIRMED)
        )

    def test_unknown_can_reconcile_or_require_manual_review(self):
        self.assertTrue(
            can_transition(
                ActionStatus.UNKNOWN,
                ActionStatus.RECONCILING,
            )
        )
        self.assertTrue(
            can_transition(
                ActionStatus.UNKNOWN,
                ActionStatus.MANUAL_REVIEW,
            )
        )

    def test_only_retryable_failed_allows_automatic_retry(self):
        self.assertTrue(
            automatic_retry_allowed(ActionStatus.RETRYABLE_FAILED)
        )
        self.assertFalse(
            automatic_retry_allowed(ActionStatus.UNKNOWN)
        )

    def test_invalid_transition_raises(self):
        with self.assertRaises(InvalidActionTransition):
            assert_transition(
                ActionStatus.CONFIRMED,
                ActionStatus.QUEUED,
            )

    def test_terminal_states_are_explicit(self):
        for status in (
            ActionStatus.CONFIRMED,
            ActionStatus.PERMANENT_FAILED,
            ActionStatus.CANCELLED,
        ):
            self.assertTrue(is_terminal(status))

    def test_reconciliation_states_are_explicit(self):
        self.assertTrue(reconciliation_required(ActionStatus.UNKNOWN))
        self.assertTrue(
            reconciliation_required(ActionStatus.RECONCILING)
        )
        self.assertTrue(
            reconciliation_required(ActionStatus.EVIDENCE_PENDING)
        )
        self.assertFalse(
            reconciliation_required(ActionStatus.CONFIRMED)
        )

    def test_next_states_are_deterministic(self):
        self.assertEqual(
            next_states(ActionStatus.UNKNOWN),
            tuple(
                sorted(
                    (
                        ActionStatus.RECONCILING,
                        ActionStatus.MANUAL_REVIEW,
                    ),
                    key=lambda item: item.value,
                )
            ),
        )


if __name__ == "__main__":
    unittest.main()
