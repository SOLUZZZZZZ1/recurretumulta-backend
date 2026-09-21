from __future__ import annotations

import copy
import io
import os
import unittest
import uuid
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pypdf import PdfReader

from pdf_builder import build_pdf
from public_case_access import issue_case_access_token
from rtm_core import generic_authorization as generic
from rtm_core import generic_authorization_router as routes


CASE = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
DOC = "33333333-3333-4333-8333-333333333333"
INTERESTED = dict(full_name="PERSONA FICTICIA LOCAL", dni_nie="PRUEBA000",
                  domicilio_notif="Calle de prueba 1", email="local-test@example.com",
                  telefono="000000000", public_service_family="bancos")


def material():
    return dict(case_id=CASE, event=generic.ISSUE_EVENT, snapshot_sha256="a" * 64,
                nonce=str(uuid.uuid4()), document_id=DOC, b2_bucket="rtm-local-documents-v1",
                b2_key=f"cases/{CASE}/rtm_authorization/{uuid.uuid4().hex}.pdf",
                sha256="b" * 64, mime="application/pdf", size_bytes=100)


class GenericEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            "RTM_AUTHORITY_SIGNING_SECRET": "G" * 64,
            "RTM_PUBLIC_CASE_ACCESS_SECRET": "P" * 64,
        }))

    def test_signature_rejects_tampered_evidence_and_cross_case_or_domain(self):
        signed = generic._seal(material())
        self.assertEqual(generic._verify(signed, event=generic.ISSUE_EVENT, case_id=CASE)["document_id"], DOC)
        for field, value in (("case_id", OTHER), ("document_id", OTHER), ("nonce", str(uuid.uuid4())),
                             ("sha256", "f" * 64), ("snapshot_sha256", "d" * 64),
                             ("b2_key", "cases/other/object.pdf"), ("version", "v1_dgt_homologado"),
                             ("authorization_kind", "dgt")):
            altered = copy.deepcopy(signed)
            altered["material"][field] = value
            with self.subTest(field=field), self.assertRaises(HTTPException):
                generic._verify(altered, event=generic.ISSUE_EVENT, case_id=CASE)
        for event, case in ((generic.CANDIDATE_EVENT, CASE), (generic.ISSUE_EVENT, OTHER)):
            with self.assertRaises(HTTPException):
                generic._verify(signed, event=event, case_id=case)
        with patch.dict(os.environ, {"RTM_AUTHORITY_SIGNING_SECRET": "changed" * 12}), self.assertRaises(HTTPException):
            generic._verify(signed, event=generic.ISSUE_EVENT, case_id=CASE)

    def test_browser_binding_rejects_every_stale_or_missing_component(self):
        payload = generic._seal(material())
        binding = generic.binding_for(payload)
        generic.require_binding(payload, binding)
        for field in ("generated_document_id", "generated_document_sha256", "generated_document_version",
                      "document_nonce", "issuance_attestation_sha256"):
            for value in (None, "", OTHER, "ñ" * 32):
                with self.subTest(field=field, value=value), self.assertRaises(HTTPException):
                    generic.require_binding(payload, binding | {field: value})

    def test_signed_generic_evidence_never_claims_authority_or_dgt_version(self):
        response = generic.issue_envelope(generic._seal(material()))
        self.assertIs(response["authorized"], False)
        self.assertIs(response["signed_authority_verified"], False)
        self.assertEqual(response["authorization_kind"], "rtm_generic_local")
        self.assertNotIn("authority_id", response)
        self.assertNotEqual(response["authorization_document_binding"]["generated_document_version"], "v1_dgt_homologado")

    def test_existing_wording_and_visible_test_mark_are_used_in_pdf(self):
        from cases import _rtm_auth_scope
        body = generic.generic_body(CASE, INTERESTED)
        self.assertIn(_rtm_auth_scope("claims"), body)
        self.assertIn("no comprende facultades ajenas a dicho asunto", body)
        self.assertNotIn("actuar ante la DGT", body)
        pdf = build_pdf("AUTORIZACIÓN DE REPRESENTACIÓN RTM", body)
        contents = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
        self.assertIn(generic.LOCAL_MARK, contents)
        self.assertIn(CASE, contents)
        self.assertIn(INTERESTED["full_name"], contents)

    def snapshot_connection(self, changed=None):
        conn = Mock()
        row = dict(interested=dict(INTERESTED), department="claims", case_type="consumer", test_mode=True, authorized=False)
        row.update(changed or {})
        case_result = Mock()
        case_result.mappings.return_value.one_or_none.return_value = row
        docs_result = Mock()
        docs_result.mappings.return_value.all.return_value = [
            dict(id=DOC, kind="identity_front", sha256="c" * 64),
            dict(id=OTHER, kind="identity_back", sha256="d" * 64),
        ]
        conn.execute.side_effect = [case_result, docs_result]
        return conn

    def test_snapshot_rejects_real_dgt_wrong_family_and_existing_authority(self):
        for mutation in ({"test_mode": False}, {"authorized": True}, {"department": "traffic"},
                         {"case_type": "fine"}, {"interested": INTERESTED | {"public_service_family": "trafico"}}):
            with self.subTest(mutation=mutation), self.assertRaises(HTTPException):
                generic.load_snapshot(self.snapshot_connection(mutation), CASE)

    def test_snapshot_changes_with_identity_and_family_without_storing_pii_in_envelope(self):
        _, first = generic.load_snapshot(self.snapshot_connection(), CASE)
        _, second = generic.load_snapshot(self.snapshot_connection({"interested": INTERESTED | {"full_name": "OTRA PRUEBA"}}), CASE)
        _, third = generic.load_snapshot(self.snapshot_connection({"interested": INTERESTED | {"public_service_family": "energia"}}), CASE)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertNotIn(INTERESTED["full_name"], str(generic._seal(material())))

    def test_disabled_local_profile_returns_404_before_storage(self):
        with patch.object(generic, "local_operator_auth_requested", return_value=False), \
             patch.object(generic, "require_http_document_storage") as storage:
            with self.assertRaises(HTTPException) as exc:
                generic.require_local_generic_profile()
        self.assertEqual(exc.exception.status_code, 404)
        storage.assert_not_called()

    def test_download_rejects_changed_custody_bytes(self):
        payload = generic._seal(material())
        engine = Mock()
        conn = engine.begin.return_value.__enter__.return_value if hasattr(engine.begin.return_value, '__enter__') else Mock()
        from contextlib import nullcontext
        engine.begin.return_value = nullcontext(conn)
        with patch.object(generic, "require_local_generic_profile"), patch.object(generic, "get_engine", return_value=engine), \
             patch.object(generic, "load_snapshot", return_value=({}, "a" * 64)), \
             patch.object(generic, "verified_issue", return_value=payload), \
             patch.object(generic, "download_bytes_limited", return_value=b"changed bytes"):
            with self.assertRaises(HTTPException) as exc:
                generic.read_generic_authorization(CASE)
        self.assertEqual(exc.exception.status_code, 409)


class GenericRoutesTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"RTM_PUBLIC_CASE_ACCESS_SECRET": "P" * 64}))
        self.enterContext(patch.object(routes, "require_local_generic_profile"))
        self.app = FastAPI()
        self.app.include_router(routes.router)
        self.client = self.enterContext(TestClient(self.app))
        self.headers = {"X-RTM-Case-Token": issue_case_access_token(CASE)}

    def test_issue_requires_case_capability_and_affirmative_consent(self):
        with patch.object(routes, "issue_generic_authorization", return_value={"ok": True}) as issue:
            path = f"/cases/{CASE}/rtm-authorization"
            self.assertEqual(self.client.post(path, json={"consent": True}).status_code, 401)
            self.assertEqual(self.client.post(f"/cases/{OTHER}/rtm-authorization", json={"consent": True}, headers=self.headers).status_code, 401)
            self.assertEqual(self.client.post(path, json={"consent": False}, headers=self.headers).status_code, 422)
            self.assertEqual(self.client.post(path, json={"consent": True, "authorized": True}, headers=self.headers).status_code, 422)
            issue.assert_not_called()
            self.assertEqual(self.client.post(path, json={"consent": True}, headers=self.headers).status_code, 200)
            issue.assert_called_once_with(CASE)

    def test_candidate_rejects_missing_binding_and_invalid_pdf_before_storage(self):
        binding = dict(generated_document_id=DOC, generated_document_sha256="a" * 64,
                       generated_document_version=generic.VERSION, document_nonce=OTHER,
                       issuance_attestation_sha256="b" * 64)
        path = f"/cases/{CASE}/rtm-authorization-signed"
        file = {"file": ("candidate.pdf", b"not a pdf", "application/pdf")}
        with patch.object(routes, "store_generic_candidate") as store:
            self.assertEqual(self.client.post(path, headers=self.headers, files=file).status_code, 422)
            response = self.client.post(path, headers=self.headers, files=file, data=binding)
            self.assertIn(response.status_code, (415, 422))
            store.assert_not_called()


if __name__ == "__main__":
    unittest.main()
