from pathlib import Path
import hashlib
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException
from rtm_core.contracts import LegalArgument, LegalPreview, SourceReference, ValidatedFact
from rtm_core import generation_gateway
from rtm_core.generation_gateway import (
    GENERATION_GATEWAY_VERSION,
    render_legal_preview,
)


def _preview() -> LegalPreview:
    return LegalPreview(
        case_id="case-1",
        service="traffic",
        family="temeraria",
        specialist="traffic.temeraria",
        facts_version="facts-v1",
        family_resolution_version="family-v1",
        validated_facts_summary=[
            "El boletín atribuye una conducción temeraria.",
            "La cuantía consignada asciende a 500 euros.",
        ],
        source_fact_keys=["hecho_denunciado_literal", "sancion_importe_eur"],
        problem_summary="Se impugna una sanción de tráfico.",
        primary_strategy="Exigir acreditación suficiente del hecho concreto.",
        requested_outcomes=["Que se archive el expediente."],
        destination="Al Servicio Catalán de Tráfico",
        document_type="Alegaciones",
        subject="Alegaciones al expediente 02510067072-0",
        legal_arguments=[
            LegalArgument(
                code="insuficiencia_probatoria",
                title="Insuficiencia probatoria",
                body="La imputación debe apoyarse en prueba suficiente y verificable.",
                source_fact_keys=["hecho_denunciado_literal"],
                legal_basis=["Presunción de inocencia"],
            )
        ],
        additional_requests=["Acceso íntegro al expediente."],
        created_by_component="traffic.temeraria",
    )


class GenerationGatewayTest(unittest.TestCase):
    def test_gateway_version_is_explicit(self):
        self.assertEqual(GENERATION_GATEWAY_VERSION, "rtm_generate_gateway_v1_1")

    def test_render_is_deterministic_and_uses_preview_content(self):
        preview = _preview()
        case = {
            "interested_data": {
                "full_name": "Ramón Ejemplo",
                "dni_nie": "12345678Z",
                "domicilio_notif": "Calle Ejemplo 1, Manresa",
            },
            "expediente_ref": "02510067072-0",
        }
        first = render_legal_preview(preview, case)
        second = render_legal_preview(preview, case)
        self.assertEqual(first, second)
        self.assertIn("AL SERVICIO CATALÁN DE TRÁFICO", first)
        self.assertIn("INSUFICIENCIA PROBATORIA", first)
        self.assertIn("Que se archive el expediente.", first)
        self.assertIn("Acceso íntegro al expediente.", first)

    def test_gateway_does_not_import_legacy_classifiers(self):
        source = Path("rtm_core/generation_gateway.py").read_text(encoding="utf-8")
        self.assertNotIn("from scoring import", source)
        self.assertNotIn("ai.infractions.dispatch", source)
        self.assertNotIn("generate_dgt_for_case", source)
        self.assertNotIn("run_expediente_ai", source)

    def test_generation_router_is_mounted(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        self.assertIn("rtm_core.generation_router", app_source)
        self.assertIn("app.include_router(rtm_core_generation_router)", app_source)

    def test_document_registration_persists_exact_digest(self):
        connection = mock.MagicMock()
        connection.execute.return_value.fetchone.return_value = ("document-id",)
        digest = hashlib.sha256(b"exact-document-bytes").hexdigest()

        document_id = generation_gateway._insert_document(
            connection,
            "case-id",
            "rtm_generated_pdf",
            "bucket",
            "cases/case-id/generated.pdf",
            "application/pdf",
            20,
            digest,
        )

        self.assertEqual(document_id, "document-id")
        statement, parameters = connection.execute.call_args.args
        self.assertIn("sha256", str(statement))
        self.assertEqual(parameters["sha256"], digest)

    def test_generation_keeps_rendered_text_and_pdf_digests_distinct(self):
        rendered = "contenido juridico renderizado"
        docx_bytes = b"DOCX-exact-bytes"
        pdf_bytes = b"%PDF-1.4\nexact-pdf-bytes\n%%EOF"
        expected_text_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        expected_docx_hash = hashlib.sha256(docx_bytes).hexdigest()
        expected_pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
        self.assertNotEqual(expected_text_hash, expected_pdf_hash)

        preview_record = SimpleNamespace(
            preview=_preview(),
            payload_sha256="p" * 64,
        )
        facts_record = SimpleNamespace(id="facts-id", facts=SimpleNamespace(facts={}))
        family_record = SimpleNamespace(id="family-id")
        case = {
            "_active_case_authority": {
                "material": {
                    "authority_id": "authority-id",
                    "authority_version": "authority-version",
                },
                "material_sha256": "a" * 64,
            }
        }

        connection = mock.MagicMock()
        connection.execute.return_value.fetchone.side_effect = [
            None,
            (1,),
            ("resource-id",),
            None,
            None,
        ]
        result = SimpleNamespace(id="resource-id")

        with (
            mock.patch.object(generation_gateway, "_case_meta", return_value=case),
            mock.patch.object(
                generation_gateway,
                "_authority_chain",
                return_value=(preview_record, facts_record, family_record),
            ),
            mock.patch.object(
                generation_gateway,
                "render_legal_preview",
                return_value=rendered,
            ),
            mock.patch.object(generation_gateway, "build_docx", return_value=docx_bytes),
            mock.patch.object(generation_gateway, "build_pdf", return_value=pdf_bytes),
            mock.patch.object(
                generation_gateway,
                "upload_bytes",
                side_effect=[("bucket", "document.docx"), ("bucket", "document.pdf")],
            ),
            mock.patch.object(
                generation_gateway,
                "_insert_document",
                side_effect=["docx-id", "pdf-id"],
            ) as insert_document,
            mock.patch.object(
                generation_gateway,
                "get_generated_resource",
                return_value=result,
            ),
        ):
            generated = generation_gateway.generate_from_frozen_preview(
                connection,
                case_id="case-id",
                preview_id="preview-id",
                generated_by="operator-id",
            )

        self.assertIs(generated, result)
        self.assertEqual(insert_document.call_args_list[0].args[-1], expected_docx_hash)
        self.assertEqual(insert_document.call_args_list[1].args[-1], expected_pdf_hash)

        resource_insert = next(
            call
            for call in connection.execute.call_args_list
            if "INSERT INTO rtm_generated_resources" in str(call.args[0])
        )
        self.assertEqual(resource_insert.args[1]["content_hash"], expected_text_hash)
        event_insert = next(
            call
            for call in connection.execute.call_args_list
            if "INSERT INTO events" in str(call.args[0])
        )
        event_payload = event_insert.args[1]["payload"]
        self.assertIn(expected_text_hash, event_payload)
        self.assertIn(expected_pdf_hash, event_payload)


class GenerationIdentityBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.case = {"status": "final_ready", "interested_data": {
            "full_name": "PERSONA DE PRUEBA PEÑA", "dni_nie": "00000001R",
            "domicilio_notif": "DOMICILIO DE PRUEBA",
        }}

    def record(self, **values):
        facts = {key: ValidatedFact(value=value, status="validated", confidence=1.0,
            sources=[SourceReference(document_id="document-identity", page_index=0,
                source_type="operator_document_review", extraction_method="ops_document_review_v1",
                evidence=str(value), confidence=1.0)])
            for key, value in values.items()}
        return SimpleNamespace(id="facts-id", facts=SimpleNamespace(facts=facts))

    def test_formatting_and_renderer_identity_aliases_are_supported(self):
        record = self.record(document_subject_name="  persona de prueba PEÑA  ", document_subject_id="00.000.001-R")
        case = {"interested_data": {"name": "PERSONA DE PRUEBA PEÑA", "identity_number": "00000001R"}}
        generation_gateway._require_document_subject_consistency(record, case)

    def test_different_identifier_or_material_name_is_rejected_without_exposing_values(self):
        for field, value in (("document_subject_id", "00000002W"),
                             ("document_subject_name", "PERSONA DE PRUEBA PENA")):
            with self.subTest(field=field):
                with self.assertRaises(HTTPException) as caught:
                    generation_gateway._require_document_subject_consistency(self.record(**{field: value}), self.case)
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual(caught.exception.detail["items"], [{"field": field, "reason": "identity_mismatch"}])
                self.assertNotIn(value, str(caught.exception.detail))

    def test_recorded_but_unresolved_identity_cannot_be_finalized(self):
        record = self.record(document_subject_id="00000001R")
        record.facts.facts["document_subject_id"] = ValidatedFact(value=None, status="unresolved")
        with self.assertRaises(HTTPException) as caught:
            generation_gateway._require_document_subject_consistency(record, self.case)
        self.assertEqual(caught.exception.detail["items"][0]["reason"], "identity_unresolved")

    def test_other_roles_and_missing_document_identity_are_not_assumed_to_be_the_applicant(self):
        record = self.record(conductor_nombre="OTRA PERSONA", acreedor="ENTIDAD DE PRUEBA", deudor="TERCERA PERSONA")
        generation_gateway._require_document_subject_consistency(record, self.case)
        self.assertNotIn("document_subject_name", record.facts.facts)

    def test_mismatch_stops_generation_before_render_upload_or_existing_resource_lookup(self):
        record = self.record(document_subject_id="00000002W")
        connection = mock.MagicMock()
        # A previous generated row must not let a contradictory case bypass the guard.
        connection.execute.return_value.fetchone.return_value = {"id": "previous-resource"}
        with (
            mock.patch.object(generation_gateway, "_case_meta", return_value=self.case),
            mock.patch.object(generation_gateway, "_authority_chain",
                return_value=(SimpleNamespace(preview=_preview()), record, SimpleNamespace(id="family-id"))),
            mock.patch.object(generation_gateway, "render_legal_preview") as render,
            mock.patch.object(generation_gateway, "build_pdf") as pdf,
            mock.patch.object(generation_gateway, "build_docx") as docx,
            mock.patch.object(generation_gateway, "upload_bytes") as upload,
            mock.patch.object(generation_gateway, "_insert_document") as insert,
            self.assertRaises(HTTPException),
        ):
            generation_gateway.generate_from_frozen_preview(connection, case_id="case-id",
                preview_id="preview-id", generated_by="operator-id")
        connection.execute.assert_not_called()
        for operation in (render, pdf, docx, upload, insert):
            operation.assert_not_called()

    def test_previously_generated_resource_with_identity_conflict_cannot_be_approved(self):
        record = self.record(document_subject_id="00000002W")
        resource = SimpleNamespace(status="final_ready", legal_preview_id="preview-id")
        connection = mock.MagicMock()
        with (
            mock.patch.object(generation_gateway, "_case_meta", return_value=self.case),
            mock.patch.object(generation_gateway, "get_generated_resource", return_value=resource),
            mock.patch.object(generation_gateway, "_authority_chain",
                return_value=(SimpleNamespace(preview=_preview()), record, SimpleNamespace(id="family-id"))),
            self.assertRaises(HTTPException),
        ):
            generation_gateway.approve_resource_for_submission(connection, case_id="case-id",
                resource_id="resource-id", approved_by="operator-id")
        connection.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
