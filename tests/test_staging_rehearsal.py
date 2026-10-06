from __future__ import annotations
import asyncio
import hashlib
import io
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import FormData, Headers, UploadFile
from starlette.requests import Request

import cases
from rtm_core import staging_rehearsal as policy
from rtm_core import staging_rehearsal_router as routes
from rtm_core.document_input_policy import document_input_policy_block

OPERATOR = "11111111-1111-4111-8111-111111111111"
GRANT = policy.RehearsalGrant(OPERATOR)
ENV = {"RTM_ENV": "staging", "RTM_ENABLE_STAGING_REHEARSAL": "1",
    "RTM_DOCUMENT_INPUT_POLICY": "synthetic_only", "RTM_STRIPE_MODE": "test",
    "RTM_ALLOW_REAL_CUSTOMER_DATA": "0", "RTM_ALLOW_REAL_PAYMENTS": "0",
    "RTM_ENABLE_FINAL_PAYMENTS": "0", "RTM_ENABLE_OUTBOUND_EMAIL": "0",
    "RTM_ENABLE_EXTERNAL_SUBMISSION": "0"}


def fixture_files():
    return {key: (policy.fixture(kind)[0], policy.fixture(kind)[1], "application/pdf")
            for key, kind in (("dni_front", "identity_front"), ("dni_back", "identity_back"))}


def synthetic_row(grant=GRANT):
    return {"id": grant.case_id, "test_mode": True, "department": "traffic", "case_type": "fine",
            "contact_name": policy.PROFILE["full_name"], "contact_email": policy.PROFILE["email"],
            "interested_data": {**policy.FORM_FIELDS, "staging_rehearsal": grant.marker}}


class RehearsalPolicyTests(unittest.TestCase):
    def test_recovery_reuses_verified_issued_document_without_writes(self):
        conn = Mock()
        conn.execute.return_value.scalar_one.return_value = True
        authority = {"material": {"authority_id": OPERATOR}, "material_sha256": "a" * 64}
        issuance = {"material": {"document_id": OPERATOR, "document_sha256": "b" * 64,
            "mime": "application/pdf", "size_bytes": 2500}}
        with patch("case_authority.verify_active_case_authority", return_value=authority), \
             patch("case_authority.verify_active_authority_document_issue", return_value=issuance):
            result = policy.recover_authority_if_issued(conn, GRANT.case_id)
        self.assertIs(result["authority_payload"], authority)
        self.assertIs(result["auth_doc"]["issuance"], issuance)
        self.assertEqual(result["auth_doc"]["document"]["sha256"], "b" * 64)
        self.assertTrue(all(str(call.args[0]).startswith("SELECT") for call in conn.execute.call_args_list))
        conn.execute.return_value.scalar_one.return_value = False
        with patch("case_authority.verify_active_case_authority") as verifier:
            self.assertIsNone(policy.recover_authority_if_issued(conn, GRANT.case_id))
            verifier.assert_not_called()

    def test_issued_rehearsal_pdf_is_marked_but_ordinary_template_is_not(self):
        from authorization_pdf import generate_authorization_pdf
        from pypdf import PdfReader
        data = {**policy.PROFILE, "case_id": GRANT.case_id, "domicilio_notif": policy.FORM_FIELDS["domicilio_notif"]}
        ordinary = generate_authorization_pdf(data)
        trial = generate_authorization_pdf(data | {"synthetic_rehearsal": "true"})
        plain = lambda content: "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(content)).pages)
        self.assertNotIn("ENSAYO RTM - DATOS FICTICIOS - SIN VALIDEZ", plain(ordinary))
        self.assertIn("ENSAYO RTM - DATOS FICTICIOS - SIN VALIDEZ", plain(trial))

    def test_disabled_everywhere_except_explicit_safe_staging(self):
        report = SimpleNamespace(safe=True, capabilities={"b2": True})
        with patch.object(policy, "build_environment_preflight", return_value=report):
            with patch.dict(os.environ, ENV, clear=True):
                policy.require_profile()
            for key, bad in [("RTM_ENV", "production"), ("RTM_ENV", "test"),
                ("RTM_ENABLE_STAGING_REHEARSAL", ""), ("RTM_ALLOW_REAL_CUSTOMER_DATA", "1"),
                ("RTM_ALLOW_REAL_PAYMENTS", "1"), ("RTM_ENABLE_FINAL_PAYMENTS", "1"),
                ("RTM_ENABLE_OUTBOUND_EMAIL", "1"), ("RTM_ENABLE_EXTERNAL_SUBMISSION", "1"),
                ("RTM_STRIPE_MODE", "live"), ("RTM_DOCUMENT_INPUT_POLICY", "customer_documents")]:
                with self.subTest(key=key, bad=bad), patch.dict(os.environ, {**ENV, key: bad}, clear=True):
                    with self.assertRaises(HTTPException): policy.require_profile()
            for broken in [SimpleNamespace(safe=False, capabilities={"b2": True}),
                           SimpleNamespace(safe=True, capabilities={"b2": False})]:
                with patch.dict(os.environ, ENV, clear=True), patch.object(policy, "build_environment_preflight", return_value=broken):
                    with self.assertRaises(HTTPException): policy.require_profile()

    def test_individual_supervisor_required(self):
        base = dict(operator_id=OPERATOR, role_code="rtm.supervisor",
                    permissions=("ops.view", "ops.supervise"), individual_session=True)
        with patch.object(policy, "require_profile"):
            for change in ({"role_code": "rtm.operator"}, {"individual_session": False},
                           {"permissions": ("ops.view",)}, {"permissions": ("ops.supervise",)}):
                with patch.object(policy, "load_ops_case_scope", return_value=SimpleNamespace(**(base | change))):
                    with self.assertRaises(HTTPException): policy.require_supervisor(Request({"type": "http"}))
            with patch.object(policy, "load_ops_case_scope", return_value=SimpleNamespace(**base)):
                self.assertEqual(policy.require_supervisor(Request({"type": "http"})), GRANT)

    def test_request_headers_cannot_mark_public_intake_synthetic(self):
        request = Request({"type": "http", "headers": [(b"x-rtm-rehearsal", b"true")]})
        self.assertIsNone(policy.trusted_intake_grant(request))
        request.state.rtm_rehearsal_grant = {"operator_id": OPERATOR}
        with self.assertRaises(HTTPException): policy.trusted_intake_grant(request)

    def test_fixture_hash_and_deterministic_candidate_no_official_identity(self):
        from pypdf import PdfReader
        for kind in ("identity_front", "identity_back", "radar"):
            name, content = policy.fixture(kind)
            self.assertTrue(name.endswith(".pdf"))
            self.assertEqual(len(PdfReader(io.BytesIO(content)).pages), 1)
        self.assertEqual(hashlib.sha256(policy.fixture("radar")[1]).hexdigest(), policy.RADAR_SHA)
        first = routes.candidate_pdf(policy.fixture("identity_front")[1])
        self.assertEqual(first, routes.candidate_pdf(policy.fixture("identity_front")[1]))
        self.assertIn("SIN VALIDEZ", PdfReader(io.BytesIO(first)).pages[0].extract_text())
        self.assertIn("FIRMA FICTICIA", PdfReader(io.BytesIO(first)).pages[0].extract_text())
        for kind in ("../../cases", "manifest", "real"):
            with self.assertRaises(HTTPException): policy.fixture(kind)

    def test_progress_renews_after_document_invalidation_and_preserves_pending_review(self):
        import case_authority
        from rtm_core import repository
        from unittest.mock import MagicMock
        engine = MagicMock()
        for exists, kinds, authorized, status, expected in (
            (False, (), False, "missing", "intake"),
            (True, (), True, "pending_review", "intake"),
            (True, ("original", "authorization_signed_candidate_stale"), False, "missing", "renewal"),
            (True, ("original", "authorization_signed_candidate"), True, "pending_review", "review"),
            (True, ("original", "authorization_signed"), True, "verified", "review"),
            (True, ("original", "authorization_signed_rejected"), True, "rejected", "renewal"),
        ):
            with self.subTest(status=status, expected=expected), \
                 patch.object(policy, "get_engine", return_value=engine), \
                 patch.object(policy, "existing_case", return_value=exists), \
                 patch.object(repository, "load_case_review_snapshot", return_value=SimpleNamespace(
                     document_kinds=kinds, authorized=authorized)), \
                 patch.object(case_authority, "project_case_authorization_evidence",
                              return_value={"authorization_evidence_status": status}) as project:
                result = policy.rehearsal_progress(GRANT)
                self.assertEqual(result["step"], expected)
                self.assertEqual(result["case_id"], GRANT.case_id if exists else None)
                self.assertEqual(result["main_document_received"], "original" in kinds)
                self.assertEqual(project.call_count, int(exists))

    def test_recovery_accepts_browser_line_endings_but_not_changed_content(self):
        row = synthetic_row()
        comment = policy.FORM_FIELDS["customer_comment"]
        row["interested_data"]["customer_comment"] = comment.replace("\n", "\r\n")
        policy.verify_case_row(row, GRANT)
        for invalid in (comment.replace("\n", "\r"), comment + " changed"):
            row["interested_data"]["customer_comment"] = invalid
            with self.assertRaises(HTTPException):
                policy.verify_case_row(row, GRANT)

    def test_recovery_rejects_real_modified_or_other_operator_case(self):
        policy.verify_case_row(synthetic_row(), GRANT)
        for field, value in [("test_mode", False), ("department", "claims"), ("case_type", "consumer"),
                             ("contact_name", "A real name"), ("contact_email", "changed@example.com")]:
            with self.subTest(field=field), self.assertRaises(HTTPException):
                policy.verify_case_row(synthetic_row() | {field: value}, GRANT)
        for field in ("full_name", "dni_nie", "email", "telefono", "domicilio_notif", "customer_comment", "staging_rehearsal"):
            row = synthetic_row()
            row["interested_data"][field] = "changed"
            with self.subTest(field=field), self.assertRaises(HTTPException):
                policy.verify_case_row(row, GRANT)
        other = policy.RehearsalGrant("22222222-2222-4222-8222-222222222222")
        self.assertNotEqual(other.case_id, GRANT.case_id)
        with self.assertRaises(HTTPException): policy.verify_case_row(synthetic_row(), other)

    def test_public_routes_still_block_documents_and_identity_changes(self):
        for path in ["/cases/intake-draft", f"/cases/{GRANT.case_id}/append-documents",
                     f"/cases/{GRANT.case_id}/upload-authorization-signed",
                     f"/cases/{GRANT.case_id}/contact", f"/cases/{GRANT.case_id}/details",
                     f"/ops/core/cases/{GRANT.case_id}/reanalysis/run"]:
            self.assertEqual(document_input_policy_block(method="POST", path=path, environ=ENV).status_code, 409)

    def test_rehearsal_paths_are_subject_to_individual_ops_session_bridge(self):
        from rtm_core.legacy_ops_session_bridge import legacy_ops_individual_session_bridge
        from tests.test_rtm_legacy_ops_session_bridge import _request
        from starlette.responses import JSONResponse
        request = _request("/ops/rehearsal/radar/intake-draft", method="POST")
        downstream = AsyncMock(return_value=JSONResponse({"unexpected": True}))
        with patch.dict(os.environ, {"RTM_ENV": "staging"}, clear=True):
            response = asyncio.run(legacy_ops_individual_session_bridge(request, downstream))
        self.assertNotEqual(response.status_code, 200)
        downstream.assert_not_called()


class RehearsalHttpTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(routes.router)
        self.client = TestClient(app)
        self.enterContext(patch.object(routes, "require_supervisor", return_value=GRANT))
        self.enterContext(patch.object(policy, "require_supervisor", return_value=GRANT))
        self.enterContext(patch.object(cases, "require_public_case_access_configured"))
        self.enterContext(patch.object(cases, "require_http_capability"))
        self.enterContext(patch.object(cases, "issue_case_access_token", return_value="synthetic-case-token"))
        self.enterContext(patch("public_case_access.issue_case_access_token", return_value="synthetic-case-token"))
        self.enterContext(patch.object(cases, "local_operator_auth_requested", return_value=False))
        self.exists = self.enterContext(patch.object(policy, "existing_case", return_value=False))
        self.storage = self.enterContext(patch.object(cases, "upload_bytes", side_effect=lambda *a: ("synthetic", "key-" + str(len(a[2])))))
        self.persist = self.enterContext(patch.object(cases, "_persist_rtm_intake_draft", return_value=[]))

    def test_exact_intake_runs_ordinary_handler_with_server_grant(self):
        response = self.client.post("/ops/rehearsal/radar/intake-draft", data=policy.FORM_FIELDS, files=fixture_files())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["case_id"], GRANT.case_id)
        self.assertIs(response.json()["test_mode"], True)
        self.assertIs(response.json()["authorized"], False)
        self.assertEqual(self.storage.call_count, 2)
        self.assertEqual(self.persist.call_args.args[-1], GRANT)
        self.assertIs(self.persist.call_args.args[-2]["test_mode"], True)

    def test_browser_multipart_line_endings_reach_ordinary_intake(self):
        fields = dict(policy.FORM_FIELDS)
        fields["customer_comment"] = fields["customer_comment"].replace("\n", "\r\n")
        response = self.client.post("/ops/rehearsal/radar/intake-draft", data=fields, files=fixture_files())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["case_id"], GRANT.case_id)
        self.assertEqual(self.storage.call_count, 2)
        self.assertEqual(self.persist.call_args.args[-1], GRANT)

    def test_mismatches_rejected_before_any_storage_or_case_write(self):
        for change in ({"email": "other@example.com"}, {"full_name": "Another person"},
                       {"privacy_accepted": "false"}, {"representation_confirmed": "false"},
                       {"test_mode": "true"}, {"prejudicial_counsel_requested": "true"}):
            response = self.client.post("/ops/rehearsal/radar/intake-draft", data=policy.FORM_FIELDS | change, files=fixture_files())
            self.assertEqual(response.status_code, 422, response.text)
        for field in ("dni_front", "dni_back"):
            files = fixture_files()
            name, content, mime = files[field]
            files[field] = (name, content + b"private trailing bytes", mime)
            response = self.client.post("/ops/rehearsal/radar/intake-draft", data=policy.FORM_FIELDS, files=files)
            self.assertEqual(response.status_code, 422, response.text)
        self.storage.assert_not_called()
        self.persist.assert_not_called()

    def test_duplicate_form_field_rejected(self):
        entries = [(k, (None, v)) for k, v in policy.FORM_FIELDS.items()]
        entries.append(("email", (None, policy.PROFILE["email"])))
        entries += list(fixture_files().items())
        response = self.client.post("/ops/rehearsal/radar/intake-draft", files=entries)
        self.assertEqual(response.status_code, 422, response.text)
        self.storage.assert_not_called()

    def test_lost_response_recovery_never_uploads_or_persists_again(self):
        self.exists.return_value = True
        response = self.client.post("/ops/rehearsal/radar/intake-draft", data=policy.FORM_FIELDS, files=fixture_files())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["recovered"])
        self.storage.assert_not_called()
        self.persist.assert_not_called()

    def test_competing_commit_cleans_only_speculative_objects_then_recovers(self):
        self.persist.side_effect = policy.RehearsalAlreadyCreated()
        self.exists.side_effect = [False, True]
        with patch.object(cases, "_cleanup_b2_objects") as cleanup:
            response = self.client.post("/ops/rehearsal/radar/intake-draft", data=policy.FORM_FIELDS, files=fixture_files())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["recovered"])
        self.assertEqual(len(cleanup.call_args.args[0]), 2)


class RehearsalUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_checked_upload_is_rewound_for_ordinary_handler(self):
        content = policy.fixture("radar")[1]
        upload = UploadFile(io.BytesIO(content), headers=Headers({"content-type": "application/pdf"}))
        await routes.exact_upload(upload, content)
        self.assertEqual(await upload.read(), content)

    async def test_other_case_blocked_before_database_lookup(self):
        request = Request({"type": "http", "headers": [], "path_params": {"case_id": "other"}})
        with patch.object(routes, "require_supervisor", return_value=GRANT), patch.object(routes, "existing_case") as database:
            with self.assertRaises(HTTPException): await routes.require_case(request)
        database.assert_not_called()

    async def test_append_accepts_only_one_exact_radar_pdf(self):
        for kind in ("radar", "identity_front"):
            content = policy.fixture(kind)[1]
            upload = UploadFile(io.BytesIO(content), headers=Headers({"content-type": "application/pdf"}))
            request = Mock()
            request.form = AsyncMock(return_value=FormData([("files", upload)]))
            with patch.object(routes, "require_case", new=AsyncMock()):
                if kind == "radar": await routes.prepare_append(request)
                else:
                    with self.assertRaises(HTTPException): await routes.prepare_append(request)

    async def test_candidate_requires_exact_generated_fictitious_bytes(self):
        expected = routes.candidate_pdf(policy.fixture("identity_front")[1])
        names = ["authority_material_sha256", "generated_document_id", "generated_document_sha256",
                 "generated_document_version", "document_nonce", "issuance_attestation_sha256"]
        for content in (expected, expected + b"other"):
            upload = UploadFile(io.BytesIO(content), headers=Headers({"content-type": "application/pdf"}))
            request = Mock(path_params={"case_id": GRANT.case_id})
            request.form = AsyncMock(return_value=FormData([(n, "binding") for n in names] + [("file", upload)]))
            with patch.object(routes, "require_case", new=AsyncMock()), patch.object(routes, "current_candidate", new=AsyncMock(return_value=expected)):
                if content == expected: await routes.prepare_candidate(request)
                else:
                    with self.assertRaises(HTTPException): await routes.prepare_candidate(request)
