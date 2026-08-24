import hashlib
import inspect
import unittest
import uuid
from datetime import datetime, timedelta, timezone

import rtm_connect.assisted_legal as workflow
from rtm_connect.assisted_legal_policy import (
    ASSISTED_LEGAL_CAPABILITY,
    ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
    ASSISTED_LEGAL_SATELLITE,
    ASSISTED_LEGAL_TARGET_REF,
    ASSISTED_LEGAL_TARGET_TYPE,
    expected_c7_payload,
)
from rtm_connect.connectors.assisted_legal import (
    ASSISTED_LEGAL_REFERENCE_PREFIX,
    AssistedLegalConnector,
    AssistedReceiptSubmission,
)
from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    RiskClass,
)
from rtm_connect.idempotency import derive_idempotency_key, payload_sha256


def _now(offset: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) + offset).isoformat()


def _action() -> ConnectActionRequest:
    return ConnectActionRequest(
        action_id=str(uuid.uuid4()),
        capability=ASSISTED_LEGAL_CAPABILITY,
        satellite=ASSISTED_LEGAL_SATELLITE,
        target_type=ASSISTED_LEGAL_TARGET_TYPE,
        target_ref=ASSISTED_LEGAL_TARGET_REF,
        payload=expected_c7_payload(),
        requested_by_operator_id=str(uuid.uuid4()),
        requested_at=_now(timedelta(minutes=-2)),
        risk_class=RiskClass.R4_CRITICAL_REGULATED,
        document_hashes=(hashlib.sha256(b"RTM C7 synthetic").hexdigest(),),
        requires_dual_control=True,
    )


def _grant(action: ConnectActionRequest) -> AuthorizationGrant:
    return AuthorizationGrant(
        authorization_id=str(uuid.uuid4()),
        action_id=action.action_id,
        authority_code="rtm.core.authorization",
        authority_version="rtm_core_authority_v1",
        decision="approved_frozen",
        payload_sha256=payload_sha256(action),
        idempotency_key=derive_idempotency_key(
            action, authority_scope="rtm.core.authorization"
        ),
        required_evidence_level=EvidenceLevel.E4_RECEIPT_VERIFIED,
        authorized_connector_modes=(ConnectorMode.ASSISTED,),
        approved_by_operator_ids=(str(uuid.uuid4()), str(uuid.uuid4())),
        authorized_at=_now(timedelta(minutes=-1)),
        expires_at=_now(timedelta(hours=1)),
        legal_effect_authorized=True,
    )


class ConnectC7AssistedWorkflowContractTest(unittest.TestCase):
    def test_connector_builds_hash_bound_r4_package(self):
        action = _action()
        grant = _grant(action)
        package = AssistedLegalConnector().build_package(
            action,
            grant,
            attempt_id=str(uuid.uuid4()),
            due_at=_now(timedelta(hours=2)),
        )
        self.assertEqual(package.action_id, action.action_id)
        self.assertEqual(package.authorization_id, grant.authorization_id)
        self.assertEqual(package.request_sha256, payload_sha256(action))
        self.assertEqual(package.human_final_gate, ASSISTED_LEGAL_HUMAN_GATE_PHRASE)
        self.assertTrue(package.human_gate_sha256)
        self.assertTrue(package.package_sha256)
        self.assertTrue(package.manifest["synthetic_only"])
        self.assertFalse(package.manifest["network_used"])
        self.assertFalse(package.manifest["legal_submission_executed"])

    def test_receipt_emits_e3_and_hash_bound_verification_emits_e4(self):
        action = _action()
        grant = _grant(action)
        attempt_id = str(uuid.uuid4())
        connector = AssistedLegalConnector()
        package = connector.build_package(
            action, grant, attempt_id=attempt_id,
            due_at=_now(timedelta(hours=2)),
        )
        receipt_hash = hashlib.sha256(b"synthetic receipt C7").hexdigest()
        reference = f"{ASSISTED_LEGAL_REFERENCE_PREFIX}{action.action_id}"
        submission = AssistedReceiptSubmission(
            receipt_sha256=receipt_hash,
            storage_ref=f"synthetic://assisted-legal/{action.action_id}.pdf",
            external_reference=reference,
            package_sha256=package.package_sha256,
            human_gate_sha256=package.human_gate_sha256,
            human_final_gate=ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
            witnessed_at=_now(),
            mime="application/pdf",
            size_bytes=128,
            legal_submission_executed=False,
        )
        e3 = connector.capture_receipt(
            action, grant, attempt_id=attempt_id, submission=submission,
        )
        self.assertEqual(e3.level, EvidenceLevel.E3_RECEIPT_CAPTURED)
        verification = connector.verify_receipt(
            action,
            grant,
            attempt_id=attempt_id,
            receipt_sha256=receipt_hash,
            storage_ref=submission.storage_ref,
            external_reference=reference,
            package_sha256=package.package_sha256,
            human_gate_sha256=package.human_gate_sha256,
            observed_receipt_sha256=receipt_hash,
            observed_external_reference=reference,
            observed_package_sha256=package.package_sha256,
            observed_human_gate_sha256=package.human_gate_sha256,
            human_final_gate=ASSISTED_LEGAL_HUMAN_GATE_PHRASE,
            verified_at=_now(),
        )
        self.assertEqual(
            verification.evidence.level,
            EvidenceLevel.E4_RECEIPT_VERIFIED,
        )
        self.assertEqual(verification.package_sha256, package.package_sha256)
        self.assertEqual(verification.human_gate_sha256, package.human_gate_sha256)

    def test_workflow_exports_full_normal_and_unknown_paths(self):
        for name in (
            "prepare_assisted_legal", "begin_assisted_review",
            "attest_assisted_review", "release_assisted_legal",
            "begin_assisted_execution", "mark_assisted_awaiting_receipt",
            "mark_assisted_outcome_unknown", "begin_assisted_reconciliation",
            "resolve_assisted_reconciliation",
            "submit_assisted_receipt", "verify_assisted_receipt",
            "complete_assisted_legal",
        ):
            self.assertTrue(callable(getattr(workflow, name)))
        self.assertEqual(
            workflow._ASSISTED_TRANSITIONS["in_progress"],
            {"awaiting_receipt", "outcome_unknown"},
        )
        self.assertEqual(
            workflow._ASSISTED_TRANSITIONS["outcome_unknown"],
            {"reconciling"},
        )
        self.assertNotIn("in_progress", workflow._ASSISTED_TRANSITIONS["reconciling"])

    def test_preparation_insert_binds_each_frozen_identity_once(self):
        source = inspect.getsource(workflow.prepare_assisted_legal)
        insert = source.split(
            "INSERT INTO rtm_connect_assisted_tasks(", 1
        )[1].split("\"\"\"", 1)[0]
        self.assertEqual(insert.count("CAST(:attempt_id AS UUID)"), 1)
        self.assertEqual(insert.count("CAST(:connector_id AS UUID)"), 1)
        self.assertEqual(insert.count("CAST(:authorization_id AS UUID)"), 1)
        self.assertIn(":authorization_version", insert)

    def test_receipt_path_revalidates_frozen_authorization(self):
        source = inspect.getsource(workflow.submit_assisted_receipt)
        self.assertIn("_load_latest_grant", source)
        self.assertIn("_assert_same_grant", source)
        self.assertIn("validate_c7_action_authority", source)

    def test_unknown_never_starts_second_attempt_or_resubmits(self):
        unknown_source = inspect.getsource(workflow.mark_assisted_outcome_unknown)
        reconcile_source = inspect.getsource(workflow.begin_assisted_reconciliation)
        resolve_source = inspect.getsource(workflow.resolve_assisted_reconciliation)
        self.assertIn("record_attempt_outcome", unknown_source)
        self.assertIn("ActionStatus.UNKNOWN", unknown_source)
        self.assertNotIn("start_attempt", unknown_source)
        self.assertNotIn("submit_assisted_receipt", unknown_source)
        self.assertIn("begin_reconciliation", reconcile_source)
        self.assertIn('attempt_id=str(row["attempt_id"])', reconcile_source)
        self.assertNotIn("start_attempt", reconcile_source)
        self.assertNotIn("queue_action", reconcile_source)
        self.assertIn("record_reconciliation_outcome", resolve_source)
        self.assertIn('attempt_id=str(row["attempt_id"])', resolve_source)
        self.assertNotIn("start_attempt", resolve_source)
        self.assertNotIn("queue_action", resolve_source)
        self.assertNotIn("record_attempt_outcome", resolve_source)
        for status in (
            "ActionStatus.UNKNOWN", "ActionStatus.MANUAL_REVIEW",
            "ActionStatus.PERMANENT_FAILED",
        ):
            self.assertIn(status, resolve_source)

    def test_prepare_validates_full_execution_authority_before_dml(self):
        source = inspect.getsource(workflow.prepare_assisted_legal)
        policy_at = source.index("validate_c7_action_authority")
        authority_at = source.index("validate_execution_authority")
        register_at = source.index("register_assisted_legal_connector")
        create_at = source.index("create_action")
        self.assertLess(policy_at, authority_at)
        self.assertLess(authority_at, register_at)
        self.assertLess(authority_at, create_at)

    def test_confirmation_is_bound_to_exact_verified_evidence(self):
        source = inspect.getsource(workflow.complete_assisted_legal)
        self.assertIn('evidence_id=str(row["verified_evidence_id"])', source)
        self.assertIn("record_reconciliation_outcome", source)
        self.assertIn("confirm_action", source)

    def test_persistent_connector_supports_manual_reconciliation(self):
        source = inspect.getsource(workflow.register_assisted_legal_connector)
        self.assertIn("supports_reconciliation=True", source)
        self.assertIn("mode=ConnectorMode.ASSISTED", source)
        self.assertIn("risk_ceiling=RiskClass.R4_CRITICAL_REGULATED", source)


if __name__ == "__main__":
    unittest.main()
