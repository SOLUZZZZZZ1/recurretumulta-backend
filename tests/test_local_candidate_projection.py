import copy
import os
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from rtm_core import generic_authorization as generic, intake_router
from tests.test_rtm_generic_authorization import CASE, OTHER, material


class LocalCandidateProjectionTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"RTM_AUTHORITY_SIGNING_SECRET": "G" * 64}))
        self.enterContext(patch.object(generic, "require_local_generic_profile"))
        self.enterContext(patch.object(generic, "load_snapshot", return_value=({}, "a" * 64)))
        self.issue = generic._seal(material())
        self.enterContext(patch.object(generic, "verified_issue", return_value=self.issue))
        self.document = self.enterContext(patch.object(generic, "_document"))
        self.candidate = generic._seal({
            **material(), "event": generic.CANDIDATE_EVENT,
            "issuance_attestation_sha256": self.issue["material_sha256"],
            "generated_document_id": self.issue["material"]["document_id"],
            "document_nonce": self.issue["material"]["nonce"],
            "evidence_status": "pending_review",
        })

    def project(self, payload):
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = (payload,) if payload else None
        return generic.has_pending_local_candidate(conn, CASE)

    def test_current_signed_receipt_is_pending_and_document_is_checked(self):
        self.assertTrue(self.project(self.candidate))
        self.assertEqual(self.document.call_args.args[-1], generic.CANDIDATE_KIND)

    def test_missing_tampered_or_cross_case_receipt_is_not_pending(self):
        self.assertFalse(self.project(None))
        tampered = copy.deepcopy(self.candidate)
        tampered["material"]["sha256"] = "f" * 64
        self.assertFalse(self.project(tampered))
        self.assertFalse(self.project(generic._seal(self.candidate["material"] | {"case_id": OTHER})))

    def test_stale_binding_or_claimed_verification_is_not_pending(self):
        for key in ("snapshot_sha256", "issuance_attestation_sha256", "generated_document_id", "document_nonce", "evidence_status"):
            with self.subTest(key=key):
                self.assertFalse(self.project(generic._seal(self.candidate["material"] | {key: "changed"})))

    def test_missing_document_or_changed_snapshot_is_not_pending(self):
        self.document.side_effect = HTTPException(409, "document mismatch")
        self.assertFalse(self.project(self.candidate))
        with patch.object(generic, "load_snapshot", side_effect=HTTPException(409, "snapshot changed")):
            self.assertFalse(self.project(self.candidate))

    def test_configuration_failure_is_not_disguised_as_missing_receipt(self):
        with patch.object(generic, "require_local_generic_profile", side_effect=HTTPException(503, "unavailable")):
            with self.assertRaises(HTTPException):
                self.project(self.candidate)

    def test_local_projection_never_grants_authority_even_with_authorized_flag(self):
        with patch.object(intake_router, "local_operator_auth_requested", return_value=True), patch.object(generic, "has_pending_local_candidate", return_value=True):
            self.assertEqual(intake_router._authorization_evidence_state(
                Mock(), CASE, authorized=True, document_kinds=[generic.CANDIDATE_KIND]
            ), (False, True, False))

    def test_nonlocal_projection_uses_existing_authority_chain(self):
        with patch.object(intake_router, "local_operator_auth_requested", return_value=False), patch.object(intake_router, "project_case_authorization_evidence", return_value={"authorization_evidence_status": "not_submitted"}), patch.object(generic, "has_pending_local_candidate") as local:
            self.assertEqual(intake_router._authorization_evidence_state(
                Mock(), CASE, authorized=False, document_kinds=[generic.CANDIDATE_KIND]
            ), (False, False, False))
            local.assert_not_called()
