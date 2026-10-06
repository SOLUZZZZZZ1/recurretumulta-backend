import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse
from rtm_core import staging_rehearsal_router as routes
from rtm_core.staging_rehearsal import RehearsalGrant, VERSION
from rtm_core.legacy_ops_session_bridge import legacy_ops_individual_session_bridge
from tests.test_rtm_legacy_ops_session_bridge import _request

GRANT = RehearsalGrant("11111111-1111-4111-8111-111111111111")
PROGRESS = {"case_id": GRANT.case_id, "step": "review", "main_document_received": True,
            "authorization_evidence_status": "verified"}


class RehearsalPaymentAccessTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(routes.router)
        self.client = TestClient(app)
        self.url = "/ops/rehearsal/radar/cases/" + GRANT.case_id + "/payment-access"
        self.supervisor = self.enterContext(patch.object(routes, "require_supervisor", return_value=GRANT))
        self.progress = self.enterContext(patch.object(routes, "rehearsal_progress", return_value=PROGRESS))
        self.issue = self.enterContext(patch("public_case_access.issue_case_access_token", return_value="synthetic-only-capability"))

    def test_new_tab_recovers_only_existing_verified_owner_trial(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            "ok": True, "version": VERSION, "synthetic_only": True, "case_id": GRANT.case_id,
            "case_access_token_header": "X-RTM-Case-Token", "case_access_token": "synthetic-only-capability",
        })
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertIn("private", response.headers["cache-control"])
        self.progress.assert_called_once_with(GRANT)
        self.issue.assert_called_once_with(GRANT.case_id)

    def test_other_case_denied_before_reading_or_issuing_access(self):
        response = self.client.post(self.url.replace(GRANT.case_id, "22222222-2222-4222-8222-222222222222"))
        self.assertEqual(response.status_code, 404)
        self.progress.assert_not_called()
        self.issue.assert_not_called()

    def test_unverified_or_incomplete_progress_never_issues_access(self):
        for change in (
            {"case_id": None}, {"step": "renewal"}, {"main_document_received": False},
            {"authorization_evidence_status": "pending_review"},
            {"authorization_evidence_status": "rejected"},
            {"authorization_evidence_status": "missing"},
        ):
            with self.subTest(change=change):
                self.progress.return_value = PROGRESS | change
                self.assertEqual(self.client.post(self.url).status_code, 409)
        self.issue.assert_not_called()

    def test_modified_case_or_invalid_authority_is_not_recovered(self):
        self.progress.side_effect = HTTPException(409, "Expediente no coincide")
        self.assertEqual(self.client.post(self.url).status_code, 409)
        self.issue.assert_not_called()

    def test_supervisor_and_safe_profile_are_required_before_lookup(self):
        for status in (403, 404, 503):
            self.supervisor.side_effect = HTTPException(status, "Ensayo no disponible")
            self.assertEqual(self.client.post(self.url).status_code, status)
        self.progress.assert_not_called()
        self.issue.assert_not_called()

    def test_get_does_not_issue_a_capability(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.issue.assert_not_called()

    def test_anonymous_request_cannot_bypass_individual_session_bridge(self):
        import os
        downstream = AsyncMock(return_value=JSONResponse({"unexpected": True}))
        request = _request(self.url, method="POST")
        with patch.dict(os.environ, {"RTM_ENV": "staging"}, clear=True):
            response = asyncio.run(legacy_ops_individual_session_bridge(request, downstream))
        self.assertNotEqual(response.status_code, 200)
        downstream.assert_not_called()
        self.issue.assert_not_called()
