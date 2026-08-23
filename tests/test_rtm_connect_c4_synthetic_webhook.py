from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from rtm_connect.connectors.synthetic_webhook import (
    SYNTHETIC_WEBHOOK_CAPABILITY,
    SYNTHETIC_WEBHOOK_CODE,
    SYNTHETIC_WEBHOOK_CONNECTOR_VERSION,
    SyntheticWebhookConnector,
    SyntheticWebhookContractError,
    SyntheticWebhookIntegrityError,
    SyntheticWebhookOutcome,
    assert_synthetic_webhook_manifest_frozen,
    synthetic_webhook_manifest,
    synthetic_webhook_manifest_sha256,
)
from rtm_connect.contracts import ConnectorMode


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _delivery(**overrides):
    values = {
        "event_key": "c4.contract.event.001",
        "observed_at": _now(),
        "origin_connector_code": "synthetic.echo",
        "origin_connector_version": "v1.0",
        "action_id": str(uuid.uuid4()),
        "attempt_id": str(uuid.uuid4()),
        "request_sha256": "a" * 64,
        "external_reference": "SYN-ECHO-C4-CONTRACT",
        "outcome": SyntheticWebhookOutcome.UNKNOWN,
        "normalized_payload": {"observation": {"sequence": 1}},
    }
    values.update(overrides)
    return SyntheticWebhookConnector().build_delivery(**values)


class ConnectC4SyntheticWebhookTest(unittest.TestCase):
    def test_descriptor_is_exact_webhook(self):
        descriptor = SyntheticWebhookConnector.descriptor
        self.assertEqual(descriptor.code, SYNTHETIC_WEBHOOK_CODE)
        self.assertEqual(
            descriptor.version,
            SYNTHETIC_WEBHOOK_CONNECTOR_VERSION,
        )
        self.assertEqual(descriptor.mode, ConnectorMode.WEBHOOK)
        self.assertEqual(
            descriptor.capabilities,
            (SYNTHETIC_WEBHOOK_CAPABILITY,),
        )

    def test_descriptor_is_synthetic_network_free_and_reconcilable(self):
        descriptor = SyntheticWebhookConnector.descriptor
        self.assertTrue(descriptor.synthetic_only)
        self.assertFalse(descriptor.network_used)
        self.assertTrue(descriptor.supports_idempotency)
        self.assertTrue(descriptor.supports_reconciliation)

    def test_manifest_is_frozen_and_defensively_copied(self):
        assert_synthetic_webhook_manifest_frozen()
        self.assertEqual(len(synthetic_webhook_manifest_sha256()), 64)
        manifest = synthetic_webhook_manifest()
        manifest["network_used"] = True
        self.assertFalse(synthetic_webhook_manifest()["network_used"])

    def test_delivery_is_deterministic(self):
        action_id = str(uuid.uuid4())
        attempt_id = str(uuid.uuid4())
        observed_at = _now()
        one = _delivery(
            action_id=action_id,
            attempt_id=attempt_id,
            observed_at=observed_at,
        )
        two = _delivery(
            action_id=action_id,
            attempt_id=attempt_id,
            observed_at=observed_at,
        )
        self.assertEqual(one, two)

    def test_event_identity_does_not_change_with_payload(self):
        action_id = str(uuid.uuid4())
        attempt_id = str(uuid.uuid4())
        observed_at = _now()
        one = _delivery(
            action_id=action_id,
            attempt_id=attempt_id,
            observed_at=observed_at,
            normalized_payload={"observation": {"sequence": 1}},
        )
        changed = _delivery(
            action_id=action_id,
            attempt_id=attempt_id,
            observed_at=observed_at,
            normalized_payload={"observation": {"sequence": 2}},
        )
        self.assertEqual(one.event_id, changed.event_id)
        self.assertNotEqual(one.delivery_sha256, changed.delivery_sha256)

    def test_verified_delivery_detects_tampering(self):
        adapter = SyntheticWebhookConnector()
        delivery = _delivery()
        self.assertTrue(adapter.verify_delivery(delivery).verified)
        with self.assertRaises(SyntheticWebhookIntegrityError):
            adapter.verify_delivery(
                delivery.with_changes(external_reference="SYN-TAMPERED")
            )

    def test_ingress_cannot_be_the_origin_connector(self):
        with self.assertRaises(SyntheticWebhookContractError):
            _delivery(origin_connector_code=SYNTHETIC_WEBHOOK_CODE)

    def test_nested_secret_material_is_blocked(self):
        with self.assertRaises(ValueError):
            _delivery(
                normalized_payload={
                    "safe": [{"nested": {"client_secret": "forbidden"}}]
                }
            )

    def test_camel_case_and_spaced_secret_names_are_blocked(self):
        for secret_key in (
            "accessToken",
            "refresh-token",
            "private key",
            "authorizationHeader",
            "x-access-token",
            "oauth_access_token",
            "providerClientSecret",
            "auth-cookie",
        ):
            with self.subTest(secret_key=secret_key):
                with self.assertRaises(ValueError):
                    _delivery(normalized_payload={secret_key: "forbidden"})

    def test_confirmed_requires_exact_e4_material(self):
        with self.assertRaises(ValueError):
            _delivery(outcome=SyntheticWebhookOutcome.CONFIRMED)
        confirmed = _delivery(
            outcome=SyntheticWebhookOutcome.CONFIRMED,
            receipt_sha256="b" * 64,
            receipt_storage_ref=(
                "synthetic://webhook/c4/receipt-confirmed.pdf"
            ),
        )
        self.assertEqual(confirmed.receipt_sha256, "b" * 64)
        self.assertTrue(
            str(confirmed.receipt_storage_ref).startswith(
                "synthetic://webhook/"
            )
        )

    def test_non_confirmed_cannot_smuggle_receipt_material(self):
        with self.assertRaises(ValueError):
            _delivery(
                outcome=SyntheticWebhookOutcome.UNKNOWN,
                receipt_sha256="c" * 64,
                receipt_storage_ref="synthetic://webhook/c4/receipt.pdf",
            )

    def test_real_receipt_storage_is_blocked(self):
        with self.assertRaises(ValueError):
            _delivery(
                outcome=SyntheticWebhookOutcome.CONFIRMED,
                receipt_sha256="d" * 64,
                receipt_storage_ref="https://provider.example/receipt.pdf",
            )


if __name__ == "__main__":
    unittest.main()
