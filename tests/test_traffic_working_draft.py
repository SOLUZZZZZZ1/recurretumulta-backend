import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import ValidationError
from pypdf import PdfReader
from rtm_core import traffic_working_draft as draft


def body(**changes):
    return draft.WorkingDraftBody.model_validate({"expected_source_sha256": "a"*64,
        "expected_latest_id": None, "change_reason": "Preparación del borrador de prueba",
        "draft_acknowledged": True, **changes})


class WorkingDraftTests(unittest.TestCase):
    def test_supported_additional_facts_enter_the_pdf_without_requiring_unknown_values(self):
        from rtm_core.facts_review import REVIEW_FIELDS
        self.assertEqual(set(draft.FIELDS) | set(draft.EXTRA_FIELDS), REVIEW_FIELDS)
        state = {"case_id": str(uuid4()), "identity": {"full_name": "PRUEBA", "dni_nie": "RTMTEST", "domicilio_notif": "Calle ficticia"},
            "reviewed_facts": [{"key": key, "label": label, "value": "Valor ficticio"} for key,label in draft.FIELDS.items()] + [
                {"key":"lugar_infraccion","label":"Lugar de la infracción","value":"Calle ficticia 1"},
                {"key":"pago_multa_reducido","label":"Multa pagada con reducción","value":False}]}
        content = draft.render_draft(state, body(), 1)
        rendered=" ".join(page.extract_text() for page in PdfReader(io.BytesIO(draft.build_pdf("BORRADOR",content))).pages)
        self.assertIn("Calle ficticia 1", rendered)
        self.assertIn("Multa pagada con reducción: No", rendered)
        self.assertNotIn("None", rendered)
        self.assertIn("PENDIENTE DE REVISION JURIDICA", rendered)

    def test_explicit_acknowledgment_strict_text_and_no_client_authority(self):
        for value in ({"draft_acknowledged": False}, {"draft_acknowledged": "true"},
            {"actor": "operator:forged"}, {"approved": True}, {"case_id": str(uuid4())},
            {"expected_latest_id": "bad"}, {"grounds": "bad\x00text"}, {"grounds": "x"*20001}):
            with self.subTest(value=value), self.assertRaises(ValidationError): body(**value)
        self.assertEqual(body().grounds, "")

    def test_fixture_is_closed_outside_explicit_local_runtime(self):
        with patch.object(draft, "local_operator_auth_requested", return_value=False), \
                patch.object(draft.storage, "assert_local_document_storage_ready") as storage, self.assertRaises(HTTPException):
            draft.require_fixture(MagicMock(), str(uuid4()))
        storage.assert_not_called()

    def test_server_rejects_missing_review_and_concurrent_sources_without_upload(self):
        for state in ({"source_sha256": "b"*64, "latest_id": None, "can_prepare": True},
                      {"source_sha256": "a"*64, "latest_id": None, "can_prepare": False},
                      {"source_sha256": "a"*64, "latest_id": str(uuid4()), "can_prepare": True}):
            with patch.object(draft, "projection", return_value=state), patch.object(draft.storage, "upload_bytes") as upload, self.assertRaises(HTTPException):
                draft.save_draft(MagicMock(), case_id=str(uuid4()), body=body(), actor="operator:"+str(uuid4()), uploaded=[])
            upload.assert_not_called()

    def test_blank_draft_pdf_is_labelled_and_does_not_invent_grounds(self):
        state = {"case_id": str(uuid4()), "identity": {"full_name": "PRUEBA", "dni_nie": "RTMTEST", "domicilio_notif": "Calle ficticia"},
            "reviewed_facts": [{"key": key, "label": label, "value": "Valor ficticio"} for key,label in draft.FIELDS.items()]}
        text = draft.render_draft(state, body(), 1)
        self.assertIn("[PENDIENTE: completar", text)
        self.assertNotIn("presunción de inocencia", text)
        pdf = PdfReader(io.BytesIO(draft.build_pdf("BORRADOR", text)))
        rendered = " ".join(p.extract_text() for p in pdf.pages)
        self.assertIn("SIMULACION LOCAL", rendered)
        self.assertIn("PENDIENTE DE REVISION JURIDICA", rendered)
        self.assertIn("sin".lower(), rendered.lower())

    def test_routes_require_individual_supervisor_and_matching_context_before_sql(self):
        import ops_operator_router as router
        request=Request({"type": "http"})
        case_id=str(uuid4())
        for individual,role,permissions in [(False,"rtm.supervisor",("ops.supervise",)),
                (True,"rtm.operator",("ops.supervise",)),(True,"rtm.supervisor",())]:
            scope=SimpleNamespace(individual_session=individual,role_code=role,permissions=permissions)
            with patch.object(router,"require_operator_token"), patch.object(router,"load_ops_case_scope",return_value=scope), patch.object(router,"get_engine") as engine:
                for call in (lambda: router.get_working_draft(case_id,request,"test"),
                    lambda: router.save_working_draft(case_id,body(),request,"test"),
                    lambda: router.get_working_draft_pdf(case_id,str(uuid4()),request,"test")):
                    with self.assertRaises(HTTPException) as error: call()
                    self.assertEqual(error.exception.status_code,403)
                engine.assert_not_called()
        scope=SimpleNamespace(individual_session=True,role_code="rtm.supervisor",permissions=("ops.supervise",),operator_id=str(uuid4()))
        with patch.object(router,"require_operator_token"), patch.object(router,"load_ops_case_scope",return_value=scope), patch.object(router,"_reviewer_identity",return_value=(str(uuid4()),str(uuid4()),"operator:forged")), patch.object(router,"get_engine") as engine:
            with self.assertRaises(HTTPException): router.get_working_draft(case_id,request,"test")
            engine.assert_not_called()

    def test_failed_transaction_removes_only_uploaded_pdf(self):
        import ops_operator_router as router
        request=Request({"type":"http"})
        engine=MagicMock()
        engine.begin.return_value.__exit__.side_effect=RuntimeError("commit failed")
        def saved(*args, uploaded, **kwargs):
            uploaded.append(("local-test", "one-new-file"))
            return {"latest_id": str(uuid4())}
        with patch.object(router,"_working_draft_supervisor",return_value=(object(),"operator:"+str(uuid4()))), \
                patch.object(router,"require_case_in_scope",return_value=str(uuid4())), \
                patch.object(router,"get_engine",return_value=engine), \
                patch.object(draft,"save_draft",side_effect=saved), \
                patch.object(draft.storage,"delete_object") as cleanup:
            with self.assertRaisesRegex(RuntimeError,"commit failed"):
                router.save_working_draft(str(uuid4()),body(),request,"test")
            cleanup.assert_called_once_with("local-test","one-new-file")

if __name__ == "__main__": unittest.main()
