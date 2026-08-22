from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from rtm_connect.contracts import (
    ConnectActionRequest,
    RiskClass,
)
from rtm_connect.idempotency import (
    canonical_json,
    derive_idempotency_key,
    payload_sha256,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _action(**overrides):
    values = {
        "action_id": str(uuid.uuid4()),
        "capability": "administration.submit_document",
        "satellite": "administration",
        "target_type": "public_registry",
        "target_ref": "synthetic",
        "payload": {"b": 2, "a": 1},
        "requested_by_operator_id": str(uuid.uuid4()),
        "requested_at": _now(),
        "risk_class": RiskClass.R2_BUSINESS_EFFECT,
        "document_hashes": ("b" * 64, "a" * 64),
    }
    values.update(overrides)
    return ConnectActionRequest(**values)


class ConnectC0ContractsTest(unittest.TestCase):
    def test_action_normalizes_and_sorts_hashes(self):
        action = _action()
        self.assertEqual(action.document_hashes, ("a" * 64, "b" * 64))
        self.assertEqual(action.capability, "administration.submit_document")

    def test_action_rejects_embedded_secret(self):
        with self.assertRaises(ValueError):
            _action(payload={"access_token": "secret"})

    def test_r4_requires_dual_control(self):
        with self.assertRaises(ValueError):
            _action(
                risk_class=RiskClass.R4_CRITICAL_REGULATED,
                requires_dual_control=False,
            )

    def test_canonical_json_is_stable(self):
        left = canonical_json({"b": 2, "a": [3, 1]})
        right = canonical_json({"a": [3, 1], "b": 2})
        self.assertEqual(left, right)

    def test_canonical_json_rejects_float(self):
        with self.assertRaises(TypeError):
            canonical_json({"amount": 1.25})

    def test_payload_hash_is_stable(self):
        action = _action()
        self.assertEqual(payload_sha256(action), payload_sha256(action))

    def test_idempotency_is_stable_and_prefixed(self):
        action = _action()
        first = derive_idempotency_key(
            action,
            authority_scope="rtm.core.authorization",
        )
        second = derive_idempotency_key(
            action,
            authority_scope="rtm.core.authorization",
        )
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("rtmc1:"))
        self.assertEqual(len(first), 70)

    def test_payload_change_changes_key(self):
        action = _action()
        changed = _action(
            action_id=action.action_id,
            payload={"a": 1, "b": 3},
            requested_by_operator_id=action.requested_by_operator_id,
            requested_at=action.requested_at,
            document_hashes=action.document_hashes,
        )
        self.assertNotEqual(
            derive_idempotency_key(
                action,
                authority_scope="rtm.core.authorization",
            ),
            derive_idempotency_key(
                changed,
                authority_scope="rtm.core.authorization",
            ),
        )


if __name__ == "__main__":
    unittest.main()
