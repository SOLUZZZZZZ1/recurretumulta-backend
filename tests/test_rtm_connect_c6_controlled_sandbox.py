from __future__ import annotations

import uuid
import unittest
from datetime import datetime, timedelta, timezone

from rtm_connect.connectors.controlled_sandbox import (
    ControlledSandboxConnector,
    assert_controlled_sandbox_manifest_frozen,
    controlled_sandbox_manifest,
    controlled_sandbox_manifest_sha256,
)
from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    RiskClass,
)
from rtm_connect.idempotency import derive_idempotency_key, payload_sha256
from rtm_connect.provider_sandbox_transport import (
    ControlledSandboxObservation,
    ProviderSandboxAmbiguous,
    SandboxObservationStatus,
)


def action() -> ConnectActionRequest:
    return ConnectActionRequest(
        action_id=str(uuid.uuid4()),
        capability="sandbox.http.probe",
        satellite="rtm.connect.sandbox",
        target_type="sandbox.probe",
        target_ref="synthetic-probe",
        payload={"synthetic_marker": "RTM_C6_SYNTHETIC_ONLY"},
        requested_by_operator_id=str(uuid.uuid4()),
        requested_at=datetime.now(timezone.utc).isoformat(),
        risk_class=RiskClass.R1_LOW_REVERSIBLE,
    )


def grant(item: ConnectActionRequest) -> AuthorizationGrant:
    return AuthorizationGrant(
        authorization_id=str(uuid.uuid4()),
        action_id=item.action_id,
        authority_code="rtm.core.authorization",
        authority_version="rtm_core_authority_v1",
        decision="approved_frozen",
        payload_sha256=payload_sha256(item),
        idempotency_key=derive_idempotency_key(
            item, authority_scope="rtm.core.authorization"
        ),
        required_evidence_level=EvidenceLevel.E2_EXTERNAL_REFERENCE,
        authorized_connector_modes=(ConnectorMode.API,),
        approved_by_operator_ids=(item.requested_by_operator_id,),
        authorized_at=datetime.now(timezone.utc).isoformat(),
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        legal_effect_authorized=False,
    )


class FakeTransport:
    def __init__(self, status=SandboxObservationStatus.ACCEPTED, error=False):
        self.status = status
        self.error = error
        self.posts = 0
        self.gets = 0

    def _result(self, probe):
        if self.error:
            raise ProviderSandboxAmbiguous(
                "redacted",
                network_call_performed=True,
            )
        return ControlledSandboxObservation(
            status=self.status,
            external_reference=probe.expected_external_reference,
            client_reference=probe.client_reference,
            request_sha256=probe.request_sha256,
        )

    def submit(self, probe, *, idempotency_key):
        self.posts += 1
        return self._result(probe)

    def reconcile(self, probe, *, idempotency_key):
        self.gets += 1
        return self._result(probe)


class ConnectC6ControlledSandboxTest(unittest.TestCase):
    def test_manifest_is_frozen_and_descriptor_exact(self):
        assert_controlled_sandbox_manifest_frozen()
        self.assertEqual(len(controlled_sandbox_manifest_sha256()), 64)
        descriptor = ControlledSandboxConnector.descriptor
        self.assertEqual(descriptor.code, "controlled.sandbox")
        self.assertEqual(descriptor.version, "v1.0")
        self.assertEqual(descriptor.risk_ceiling, RiskClass.R1_LOW_REVERSIBLE)
        self.assertTrue(descriptor.synthetic_only)
        self.assertTrue(descriptor.network_used)
        manifest = controlled_sandbox_manifest()
        self.assertEqual(manifest["authority_code"], "rtm.core.authorization")
        self.assertEqual(
            manifest["authority_version"],
            "rtm_core_authority_v1",
        )

    def test_accepted_maps_to_exact_e2(self):
        item = action()
        transport = FakeTransport()
        result = ControlledSandboxConnector(transport).execute_authorized(
            item, grant(item), attempt_id=str(uuid.uuid4())
        )
        self.assertEqual(result.status, "external_accepted")
        self.assertEqual(result.evidence.level, EvidenceLevel.E2_EXTERNAL_REFERENCE)
        self.assertEqual(result.evidence.request_sha256, payload_sha256(item))
        self.assertEqual(result.external_reference, f"c6probe-{item.action_id}")

    def test_ambiguous_transport_maps_to_unknown_e1(self):
        item = action()
        result = ControlledSandboxConnector(FakeTransport(error=True)).execute_authorized(
            item, grant(item), attempt_id=str(uuid.uuid4())
        )
        self.assertEqual(result.status, "unknown")
        self.assertTrue(result.reconciliation_required)
        self.assertTrue(result.metadata["network_call_performed"])
        self.assertEqual(result.evidence.level, EvidenceLevel.E1_REQUEST_RECORDED)

        self.assertIsNone(result.evidence.external_reference)

    def test_reconciliation_is_get_only_and_normalized(self):
        item = action()
        transport = FakeTransport()
        result = ControlledSandboxConnector(transport).reconcile_authorized(
            item, grant(item), attempt_id=str(uuid.uuid4())
        )
        self.assertEqual(result.status, "confirmed")
        self.assertEqual(transport.posts, 0)
        self.assertEqual(transport.gets, 1)

    def test_payload_or_risk_expansion_is_rejected_before_transport(self):
        base = action()
        invalid = ConnectActionRequest(
            action_id=base.action_id,
            capability=base.capability,
            satellite=base.satellite,
            target_type=base.target_type,
            target_ref=base.target_ref,
            payload={"synthetic_marker": "RTM_C6_SYNTHETIC_ONLY", "extra": "x"},
            requested_by_operator_id=base.requested_by_operator_id,
            requested_at=base.requested_at,
            risk_class=RiskClass.R1_LOW_REVERSIBLE,
        )
        transport = FakeTransport()
        with self.assertRaises(RuntimeError):
            ControlledSandboxConnector(transport).execute_authorized(
                invalid, grant(invalid), attempt_id=str(uuid.uuid4())
            )
        self.assertEqual(transport.posts, 0)

    def test_free_form_correlation_is_rejected_before_transport(self):
        base = action()
        invalid = ConnectActionRequest(
            action_id=base.action_id,
            capability=base.capability,
            satellite=base.satellite,
            target_type=base.target_type,
            target_ref=base.target_ref,
            payload=base.payload,
            requested_by_operator_id=base.requested_by_operator_id,
            requested_at=base.requested_at,
            risk_class=base.risk_class,
            correlation_id="possible-personal-data",
        )
        transport = FakeTransport()
        with self.assertRaises(RuntimeError):
            ControlledSandboxConnector(transport).execute_authorized(
                invalid,
                grant(invalid),
                attempt_id=str(uuid.uuid4()),
            )
        self.assertEqual(transport.posts, 0)

    def test_expired_authority_is_rejected_before_transport(self):
        item = action()
        expired = AuthorizationGrant(
            authorization_id=str(uuid.uuid4()),
            action_id=item.action_id,
            authority_code="rtm.core.authorization",
            authority_version="rtm_core_authority_v1",
            decision="approved_frozen",
            payload_sha256=payload_sha256(item),
            idempotency_key=derive_idempotency_key(
                item, authority_scope="rtm.core.authorization"
            ),
            required_evidence_level=EvidenceLevel.E2_EXTERNAL_REFERENCE,
            authorized_connector_modes=(ConnectorMode.API,),
            approved_by_operator_ids=(item.requested_by_operator_id,),
            authorized_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            expires_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            legal_effect_authorized=False,
        )
        transport = FakeTransport()
        with self.assertRaises(RuntimeError):
            ControlledSandboxConnector(transport).execute_authorized(
                item, expired, attempt_id=str(uuid.uuid4())
            )
        self.assertEqual(transport.posts, 0)

    def test_untrusted_or_future_core_authority_is_rejected_before_transport(self):
        item = action()
        baseline = grant(item)
        variants = (
            {"authority_code": "attacker.self"},
            {"authority_version": "made_up_v9"},
            {
                "authorized_at": (
                    datetime.now(timezone.utc) + timedelta(days=30)
                ).isoformat(),
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(days=31)
                ).isoformat(),
            },
        )
        for changes in variants:
            with self.subTest(changes=changes):
                values = dict(baseline.__dict__)
                values.update(changes)
                untrusted = AuthorizationGrant(**values)
                transport = FakeTransport()
                with self.assertRaises(RuntimeError):
                    ControlledSandboxConnector(transport).execute_authorized(
                        item,
                        untrusted,
                        attempt_id=str(uuid.uuid4()),
                    )
                self.assertEqual(transport.posts, 0)


if __name__ == "__main__":
    unittest.main()
