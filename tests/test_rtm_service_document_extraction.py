from __future__ import annotations

import io
import hashlib
import json
import unittest
from unittest import mock

import requests
from fastapi import HTTPException
from pypdf import PdfWriter

from rtm_core.ai_security import (
    ModelCallBudgetExceeded,
    consume_model_call_budget,
    model_call_budget,
)
from rtm_core.document_extraction import (
    OPENAI_DOCUMENT_PROVIDER_VERSION,
    SERVICE_DOCUMENT_EXTRACTOR_VERSION,
    OpenAIResponsesDocumentProvider,
    ProviderDocumentResult,
    ProviderObservation,
    SourceDocument,
    _convert_tiff_to_png,
    build_responses_payload,
    document_response_schema,
    extraction_limits,
    extract_service_documents,
    parse_provider_response,
)
from rtm_core.document_fact_catalog import registered_fact_keys


class _FakeProvider:
    version = "fake_document_provider_v1"
    model = "fake-model"

    def extract_document(self, *, service, document, content):
        return (
            ProviderDocumentResult(
                observations=[
                    ProviderObservation(
                        field="descripcion_hecho",
                        value="La factura F-22 está vencida e impagada.",
                        page_index=0,
                        evidence="FACTURA F-22 — PENDIENTE",
                        confidence=0.99,
                        notes=[],
                    ),
                    ProviderObservation(
                        field="importe_deuda_eur",
                        value="950,25 EUR",
                        page_index=0,
                        evidence="TOTAL PENDIENTE 950,25 EUR",
                        confidence=0.99,
                        notes=[],
                    ),
                ],
                unresolved_fields=["fecha_vencimiento"],
                quality_flags=[],
                document_notes=["Lectura de prueba."],
            ),
            "document_vision",
            [],
        )


class _BudgetExhaustingProvider(_FakeProvider):
    def extract_document(self, *, service, document, content):
        consume_model_call_budget()
        consume_model_call_budget()
        raise AssertionError("la segunda llamada debía quedar bloqueada")


class _LeakingProvider(_FakeProvider):
    def extract_document(self, *, service, document, content):
        raise RuntimeError("attacker-secret://credential?token=CANARY")


def _document(
    *,
    mime: str = "application/pdf",
    size_bytes: int | None = None,
) -> SourceDocument:
    content = _valid_pdf_bytes()
    return SourceDocument(
        id="doc-1",
        case_id="case-1",
        kind="original",
        mime=mime,
        b2_bucket="bucket",
        b2_key="cases/case-1/original/factura.pdf",
        size_bytes=len(content) if size_bytes is None else size_bytes,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _valid_pdf_bytes() -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


class ServiceDocumentExtractionTest(unittest.TestCase):
    def test_configured_limits_can_only_tighten_absolute_limits(self):
        for value in ("not-an-int", "0", "9", "999999"):
            with self.subTest(value=value), mock.patch.dict(
                "os.environ",
                {"RTM_DOCUMENT_MAX_FILES": value},
                clear=True,
            ):
                with self.assertRaises(RuntimeError):
                    extraction_limits()

    def test_stored_hash_mismatch_blocks_before_provider(self):
        provider = mock.Mock(wraps=_FakeProvider())
        document = _document().model_copy(update={"sha256": "0" * 64})
        with self.assertRaises(HTTPException) as raised:
            extract_service_documents(
                case_id="case-1",
                service="debt",
                documents=[document],
                provider=provider,
                byte_loader=lambda bucket, key: _valid_pdf_bytes(),
            )
        self.assertEqual(raised.exception.status_code, 409)
        provider.extract_document.assert_not_called()

    def test_versions_are_explicit(self):
        self.assertEqual(
            SERVICE_DOCUMENT_EXTRACTOR_VERSION,
            "rtm_service_document_extractor_v1_0",
        )
        self.assertEqual(
            OPENAI_DOCUMENT_PROVIDER_VERSION,
            "rtm_openai_responses_document_provider_v1_0",
        )

    def test_schema_is_limited_to_registered_document_facts(self):
        schema = document_response_schema("debt")
        enum = schema["properties"]["observations"]["items"]["properties"][
            "field"
        ]["enum"]
        self.assertEqual(set(enum), set(registered_fact_keys("debt")))
        serialised = json.dumps(schema, ensure_ascii=False).lower()
        self.assertNotIn('"familia"', serialised)
        self.assertNotIn('"strategy"', serialised)
        self.assertNotIn('"borrador"', serialised)

    def test_responses_payload_is_non_stored_and_schema_strict(self):
        payload, mode, mime, _ = build_responses_payload(
            service="debt",
            document=_document(),
            content=_valid_pdf_bytes(),
            model="gpt-test",
        )
        self.assertFalse(payload["store"])
        self.assertEqual(
            payload["text"]["format"]["type"],
            "json_schema",
        )
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertEqual(mode, "document_vision")
        self.assertEqual(mime, "application/pdf")
        user_items = payload["input"][1]["content"]
        file_item = next(
            item for item in user_items if item["type"] == "input_file"
        )
        self.assertTrue(
            file_item["file_data"].startswith(
                "data:application/pdf;base64,"
            )
        )

    def test_parser_discards_unregistered_fields(self):
        response = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "observations": [
                                        {
                                            "field": "descripcion_hecho",
                                            "value": "Factura vencida e impagada.",
                                            "page_index": 0,
                                            "evidence": "PENDIENTE DE PAGO",
                                            "confidence": 0.99,
                                            "notes": [],
                                        },
                                        {
                                            "field": "familia",
                                            "value": "factura_impagada",
                                            "page_index": 0,
                                            "evidence": "FACTURA",
                                            "confidence": 1,
                                            "notes": [],
                                        },
                                    ],
                                    "unresolved_fields": ["fecha_vencimiento"],
                                    "quality_flags": [],
                                    "document_notes": [],
                                }
                            ),
                        }
                    ],
                }
            ]
        }
        parsed = parse_provider_response(response, service="debt")
        self.assertEqual(len(parsed.observations), 1)
        self.assertEqual(
            parsed.observations[0].field,
            "descripcion_hecho",
        )
        self.assertEqual(
            parsed.unresolved_fields,
            ["fecha_vencimiento"],
        )

    def test_fake_provider_builds_packet_with_server_document_identity(self):
        result = extract_service_documents(
            case_id="case-1",
            service="debt",
            documents=[_document()],
            provider=_FakeProvider(),
            byte_loader=lambda bucket, key: _valid_pdf_bytes(),
        )
        self.assertEqual(result.packet.case_id, "case-1")
        self.assertEqual(result.packet.service, "debt")
        self.assertEqual(result.packet.source_document_ids, ["doc-1"])
        self.assertIn("fecha_vencimiento", result.packet.declared_unresolved)
        self.assertEqual(len(result.packet.observations), 2)
        for observation in result.packet.observations:
            self.assertEqual(observation.document_id, "doc-1")
            self.assertEqual(observation.source_type, "document_vision")
            self.assertIn(
                SERVICE_DOCUMENT_EXTRACTOR_VERSION,
                observation.extraction_method,
            )

    def test_traffic_cannot_use_cross_service_extractor(self):
        with self.assertRaises(ValueError):
            extract_service_documents(
                case_id="case-1",
                service="traffic",
                documents=[_document()],
                provider=_FakeProvider(),
                byte_loader=lambda bucket, key: _valid_pdf_bytes(),
            )

    def test_empty_document_is_blocked(self):
        with self.assertRaises(HTTPException) as context:
            extract_service_documents(
                case_id="case-1",
                service="debt",
                documents=[_document()],
                provider=_FakeProvider(),
                byte_loader=lambda bucket, key: b"",
            )
        self.assertEqual(context.exception.status_code, 422)

    def test_model_budget_exhaustion_is_never_downgraded_to_partial_success(self):
        with model_call_budget(1), self.assertRaises(ModelCallBudgetExceeded):
            extract_service_documents(
                case_id="case-1",
                service="debt",
                documents=[_document()],
                provider=_BudgetExhaustingProvider(),
                byte_loader=lambda bucket, key: _valid_pdf_bytes(),
            )

    def test_unexpected_provider_error_is_opaque_in_diagnostics(self):
        with self.assertRaises(HTTPException) as raised:
            extract_service_documents(
                case_id="case-1",
                service="debt",
                documents=[_document()],
                provider=_LeakingProvider(),
                byte_loader=lambda bucket, key: _valid_pdf_bytes(),
            )

        rendered = json.dumps(raised.exception.detail)
        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("document_processing_failed", rendered)
        self.assertNotIn("attacker-secret", rendered)
        self.assertNotIn("RuntimeError", rendered)

    def test_openai_transport_and_http_failures_expose_only_stable_codes(self):
        provider = OpenAIResponsesDocumentProvider(
            api_key="synthetic-test-key",
            model="gpt-test",
            timeout_seconds=5,
        )
        with (
            model_call_budget(1),
            mock.patch(
                "rtm_core.document_extraction.require_http_capability"
            ),
            mock.patch(
                "rtm_core.document_extraction.requests.post",
                side_effect=requests.Timeout(
                    "attacker-secret://credential?token=CANARY"
                ),
            ),
        ):
            with self.assertRaises(HTTPException) as transport:
                provider.extract_document(
                    service="debt",
                    document=_document(),
                    content=_valid_pdf_bytes(),
                )
        self.assertEqual(
            transport.exception.detail["code"],
            "document_provider_unavailable",
        )
        self.assertNotIn("attacker-secret", json.dumps(transport.exception.detail))
        self.assertNotIn("Timeout", json.dumps(transport.exception.detail))

        response = mock.Mock(ok=False, status_code=418, headers={})
        with (
            model_call_budget(1),
            mock.patch(
                "rtm_core.document_extraction.require_http_capability"
            ),
            mock.patch(
                "rtm_core.document_extraction.requests.post",
                return_value=response,
            ),
        ):
            with self.assertRaises(HTTPException) as rejected:
                provider.extract_document(
                    service="debt",
                    document=_document(),
                    content=_valid_pdf_bytes(),
                )
        self.assertEqual(
            rejected.exception.detail["code"],
            "document_provider_rejected",
        )
        self.assertNotIn("418", json.dumps(rejected.exception.detail))

    def test_rate_limit_preserves_only_bounded_retry_delay(self):
        provider = OpenAIResponsesDocumentProvider(
            api_key="synthetic-test-key",
            model="gpt-test",
            timeout_seconds=5,
        )
        response = mock.Mock(
            ok=False,
            status_code=429,
            headers={"Retry-After": "3.5"},
        )
        with (
            model_call_budget(1),
            mock.patch(
                "rtm_core.document_extraction.require_http_capability"
            ),
            mock.patch(
                "rtm_core.document_extraction.requests.post",
                return_value=response,
            ),
        ):
            with self.assertRaises(HTTPException) as limited:
                provider.extract_document(
                    service="debt",
                    document=_document(),
                    content=_valid_pdf_bytes(),
                )
        self.assertEqual(
            limited.exception.detail,
            {
                "code": "document_provider_rate_limited",
                "message": "El proveedor documental no pudo completar la solicitud.",
                "retry_after_seconds": 3.5,
            },
        )

    def test_tiff_conversion_failure_does_not_expose_parser_details(self):
        with mock.patch(
            "PIL.Image.open",
            side_effect=RuntimeError(
                "attacker-secret://credential?token=CANARY"
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                _convert_tiff_to_png(b"synthetic-tiff")

        rendered = str(raised.exception.detail)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertNotIn("attacker-secret", rendered)
        self.assertNotIn("RuntimeError", rendered)


if __name__ == "__main__":
    unittest.main()
