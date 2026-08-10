from __future__ import annotations

import json
import unittest

from fastapi import HTTPException

from rtm_core.document_extraction import (
    OPENAI_DOCUMENT_PROVIDER_VERSION,
    SERVICE_DOCUMENT_EXTRACTOR_VERSION,
    ProviderDocumentResult,
    ProviderObservation,
    SourceDocument,
    build_responses_payload,
    document_response_schema,
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


def _document(
    *,
    mime: str = "application/pdf",
    size_bytes: int = 1200,
) -> SourceDocument:
    return SourceDocument(
        id="doc-1",
        case_id="case-1",
        kind="original",
        mime=mime,
        b2_bucket="bucket",
        b2_key="cases/case-1/original/factura.pdf",
        size_bytes=size_bytes,
        sha256="abc",
    )


class ServiceDocumentExtractionTest(unittest.TestCase):
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
            content=b"%PDF-1.4 fake",
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
            byte_loader=lambda bucket, key: b"%PDF fake",
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
                byte_loader=lambda bucket, key: b"%PDF fake",
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
        self.assertEqual(context.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
