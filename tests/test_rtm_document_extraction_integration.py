from __future__ import annotations

import hashlib
import io
import os
import uuid
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import create_engine, text

from app import app
from tests.postgres_security_fixtures import (
    apply_operator_security_schema,
    operator_http_environment,
    seed_supervisor_session,
)
from rtm_core.document_extraction import (
    ProviderDocumentResult,
    ProviderObservation,
    extract_service_documents,
)
from rtm_core.document_extraction_migration import document_extraction_ddl
from rtm_core.document_extraction_repository import (
    persist_document_extraction,
    prepare_document_extraction,
)
from rtm_core.document_extraction_router import (
    RunDocumentExtractionBody,
    run_document_extraction,
)
from rtm_core.migration_router import authority_v1_ddl


RUN_POSTGRES_INTEGRATION = os.getenv("RTM_CORE_INTEGRATION_DB") == "1"
DATABASE_URL = os.getenv("DATABASE_URL", "")


class _FakeProvider:
    version = "fake_cross_service_provider_v1"
    model = "fake-cross-service-model"

    def extract_document(self, *, service, document, content):
        if service == "debt":
            observations = [
                ProviderObservation(
                    field="descripcion_hecho",
                    value="La factura F-2026 está vencida e impagada.",
                    page_index=0,
                    evidence="FACTURA F-2026 — PENDIENTE DE PAGO",
                    confidence=0.99,
                    notes=[],
                ),
                ProviderObservation(
                    field="factura_numero",
                    value="F-2026",
                    page_index=0,
                    evidence="Factura F-2026",
                    confidence=0.99,
                    notes=[],
                ),
                ProviderObservation(
                    field="importe_deuda_eur",
                    value="1.250,50 EUR",
                    page_index=0,
                    evidence="TOTAL PENDIENTE 1.250,50 EUR",
                    confidence=0.99,
                    notes=[],
                ),
            ]
        else:
            observations = [
                ProviderObservation(
                    field="descripcion_hecho",
                    value="Incidencia documental de prueba.",
                    page_index=0,
                    evidence="INCIDENCIA DOCUMENTAL",
                    confidence=0.99,
                    notes=[],
                )
            ]
        return (
            ProviderDocumentResult(
                observations=observations,
                unresolved_fields=[],
                quality_flags=[],
                document_notes=["Extracción controlada de integración."],
            ),
            "document_vision",
            [],
        )


@unittest.skipUnless(
    RUN_POSTGRES_INTEGRATION and DATABASE_URL,
    "Requiere PostgreSQL temporal de RTM CORE",
)
class DocumentExtractionIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        cls._reset_schema()
        cls._apply_migrations_twice()
        with cls.engine.begin() as conn:
            apply_operator_security_schema(conn)
            cls.operator = seed_supervisor_session(conn)
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        content = io.BytesIO()
        writer.write(content)
        cls.document_content = content.getvalue()
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.engine.dispose()

    @classmethod
    def _reset_schema(cls):
        with cls.engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
            conn.execute(
                text(
                    """
                    CREATE TABLE cases (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        status TEXT NOT NULL DEFAULT 'core_review_pending',
                        payment_status TEXT,
                        authorized BOOLEAN NOT NULL DEFAULT FALSE,
                        department TEXT,
                        case_type TEXT,
                        category TEXT,
                        interested_data JSONB,
                        expediente_ref TEXT,
                        organismo TEXT,
                        contact_email TEXT,
                        contact_name TEXT,
                        source_module TEXT,
                        customer_comment TEXT,
                        test_mode BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE documents (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                        kind TEXT NOT NULL,
                        b2_bucket TEXT,
                        b2_key TEXT,
                        sha256 TEXT,
                        mime TEXT,
                        size_bytes BIGINT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE extractions (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                        extracted_json JSONB NOT NULL,
                        confidence DOUBLE PRECISION,
                        model TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE events (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        case_id UUID REFERENCES cases(id) ON DELETE CASCADE,
                        type TEXT NOT NULL,
                        payload JSONB,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )

    @classmethod
    def _apply_migrations_twice(cls):
        for _ in range(2):
            with cls.engine.begin() as conn:
                for _, statement in authority_v1_ddl():
                    conn.execute(text(statement))
                for _, statement in document_extraction_ddl():
                    conn.execute(text(statement))

    def _headers(self):
        return self.operator.headers

    def _insert_document(
        self,
        *,
        case_id: str,
        kind: str,
        mime: str,
        key_suffix: str,
    ) -> str:
        document_id = str(uuid.uuid4())
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO documents(
                        id, case_id, kind, b2_bucket, b2_key, sha256,
                        mime, size_bytes, created_at
                    ) VALUES (
                        CAST(:document_id AS UUID), CAST(:case_id AS UUID),
                        :kind, 'ci', :b2_key, :sha256,
                        :mime, :size_bytes, NOW()
                    )
                    """
                ),
                {
                    "document_id": document_id,
                    "case_id": case_id,
                    "kind": kind,
                    "b2_key": f"cases/{case_id}/{key_suffix}",
                    "mime": mime,
                    "sha256": hashlib.sha256(self.document_content).hexdigest(),
                    "size_bytes": len(self.document_content),
                },
            )
        return document_id

    def _insert_case(
        self,
        *,
        department: str = "debt",
        paid: bool = True,
        authorized: bool = True,
        include_original: bool = True,
        original_kind: str = "original",
    ) -> tuple[str, str | None]:
        case_id = str(uuid.uuid4())
        interested = (
            '{"full_name":"Persona de prueba",'
            '"dni_nie":"12345678Z",'
            '"domicilio_notif":"Calle de Prueba 1, Manresa",'
            '"email":"extractor@example.invalid"}'
        )
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO cases(
                        id, status, payment_status, authorized, department,
                        case_type, category, interested_data, contact_email,
                        contact_name, source_module, customer_comment,
                        test_mode, created_at, updated_at
                    ) VALUES (
                        CAST(:case_id AS UUID), 'core_review_pending',
                        :payment_status, :authorized, :department,
                        :case_type, :category, CAST(:interested AS JSONB),
                        'extractor@example.invalid', 'Persona de prueba',
                        'rtm_web', 'Solicita estudio documental del asunto.',
                        FALSE, NOW(), NOW()
                    )
                    """
                ),
                {
                    "case_id": case_id,
                    "payment_status": "paid" if paid else "pending",
                    "authorized": authorized,
                    "department": department,
                    "case_type": (
                        "fine"
                        if department == "traffic"
                        else "invoice"
                        if department == "debt"
                        else department
                    ),
                    "category": department,
                    "interested": interested,
                },
            )

        original_id = None
        if include_original:
            original_id = self._insert_document(
                case_id=case_id,
                kind=original_kind,
                mime="application/pdf",
                key_suffix="original/documento.pdf",
            )
        self._insert_document(
            case_id=case_id,
            kind="identity_front",
            mime="image/jpeg",
            key_suffix="identity/front.jpg",
        )
        self._insert_document(
            case_id=case_id,
            kind="identity_back",
            mime="image/jpeg",
            key_suffix="identity/back.jpg",
        )
        self._insert_document(
            case_id=case_id,
            kind="authorization_signed",
            mime="application/pdf",
            key_suffix="authorization/signed.pdf",
        )
        return case_id, original_id

    def _extract_synthetic_case(self, case_id, original_id):
        # Exercise storage/normalization using only local synthetic content.
        # The public run route remains withdrawn until service-bound consent
        # exists; this is deliberately not a bypass of its HTTP guard.
        with self.engine.begin() as conn:
            service, documents = prepare_document_extraction(
                conn,
                case_id=case_id,
                requested_document_ids=[original_id],
            )
        result = extract_service_documents(
            case_id=case_id,
            service=service,
            documents=documents,
            provider=_FakeProvider(),
            byte_loader=lambda bucket, key: self.document_content,
        )
        with self.engine.begin() as conn:
            return persist_document_extraction(
                conn,
                case_id=case_id,
                result=result,
                created_by=self.operator.actor,
            )

    def test_synthetic_extraction_preview_and_facts_promotion(self):
        case_id, original_id = self._insert_case()
        self.assertIsNotNone(original_id)

        with patch.dict(os.environ, operator_http_environment()):
            before = self.client.get(
                f"/ops/core/cases/{case_id}/workspace",
                headers=self._headers(),
            )
            self.assertEqual(before.status_code, 200, before.text)
            self.assertEqual(
                before.json()["next_step"]["stage"],
                "service_fact_extraction_pending",
            )

            extraction = self._extract_synthetic_case(case_id, original_id)
            extraction_id = extraction.id

            with self.engine.begin() as conn:
                extraction_count = conn.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM rtm_document_extractions
                        WHERE case_id=CAST(:case_id AS UUID)
                        """
                    ),
                    {"case_id": case_id},
                ).scalar_one()
                facts_count = conn.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM rtm_validated_facts
                        WHERE case_id=CAST(:case_id AS UUID)
                        """
                    ),
                    {"case_id": case_id},
                ).scalar_one()
            self.assertEqual(extraction_count, 1)
            self.assertEqual(facts_count, 0)

            after_extraction = self.client.get(
                f"/ops/core/cases/{case_id}/workspace",
                headers=self._headers(),
            )
            self.assertEqual(after_extraction.status_code, 200)
            workspace = after_extraction.json()
            self.assertEqual(
                workspace["next_step"]["stage"],
                "service_facts_preview_pending",
            )
            self.assertEqual(
                workspace["document_extraction"]["latest_active"]["id"],
                extraction_id,
            )

            preview = self.client.post(
                (
                    f"/ops/core/cases/{case_id}/document-extractions/"
                    f"{extraction_id}/facts-preview"
                ),
                headers=self._headers(),
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertFalse(preview.json()["persisted"])
            self.assertIn(
                "descripcion_hecho",
                preview.json()["result"]["accepted_fields"],
            )

            draft = self.client.post(
                (
                    f"/ops/core/cases/{case_id}/document-extractions/"
                    f"{extraction_id}/facts-draft"
                ),
                headers=self._headers(),
            )
            self.assertEqual(draft.status_code, 200, draft.text)
            draft_payload = draft.json()
            self.assertTrue(draft_payload["persisted"])
            self.assertFalse(draft_payload["facts_frozen"])
            self.assertFalse(draft_payload["family_resolved"])
            facts_id = draft_payload["facts"]["id"]

            with self.engine.begin() as conn:
                source_extraction_id = conn.execute(
                    text(
                        """
                        SELECT CAST(source_extraction_id AS TEXT)
                        FROM rtm_validated_facts
                        WHERE id=CAST(:facts_id AS UUID)
                        """
                    ),
                    {"facts_id": facts_id},
                ).scalar_one()
            self.assertEqual(source_extraction_id, extraction_id)

            after_facts = self.client.get(
                f"/ops/core/cases/{case_id}/workspace",
                headers=self._headers(),
            )
            self.assertEqual(
                after_facts.json()["next_step"]["stage"],
                "validated_facts_review",
            )

            invalidate = self.client.post(
                (
                    f"/ops/core/cases/{case_id}/document-extractions/"
                    f"{extraction_id}/invalidate"
                ),
                headers=self._headers(),
                json={"reason": "Nueva lectura necesaria"},
            )
            self.assertEqual(invalidate.status_code, 409)

    def test_duplicate_extraction_is_blocked(self):
        case_id, original_id = self._insert_case()
        with patch.dict(os.environ, operator_http_environment()):
            first = self._extract_synthetic_case(case_id, original_id)
            self.assertEqual(first.status, "completed")
            with self.assertRaises(HTTPException) as duplicate:
                self._extract_synthetic_case(case_id, original_id)
            self.assertEqual(duplicate.exception.status_code, 409)

    def test_external_run_stays_withdrawn_without_service_bound_consent(self):
        case_id, original_id = self._insert_case()
        environment = operator_http_environment()
        environment["RTM_ENABLE_DOCUMENT_PROVIDER"] = "1"
        with (
            patch.dict(os.environ, environment),
            patch("rtm_core.document_extraction.get_document_provider") as provider,
            patch("rtm_core.document_extraction.download_bytes_limited") as download,
            patch("rtm_core.document_extraction_router.prepare_document_extraction") as prepare,
        ):
            response = self.client.post(
                f"/ops/core/cases/{case_id}/document-extractions/run",
                headers=self._headers(),
                json={"document_ids": [original_id]},
            )
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["detail"]["policy"], "synthetic_only")
            # Independently exercise the endpoint's second barrier. The full
            # staging application rejects persisted inputs before reaching it.
            with self.assertRaises(HTTPException) as consent_block:
                run_document_extraction(
                    case_id,
                    RunDocumentExtractionBody(document_ids=[original_id]),
                    Request({"type": "http", "headers": []}),
                    x_operator_token=environment["OPERATOR_TOKEN"],
                    x_operator_actor=self.operator.actor,
                )
            self.assertEqual(consent_block.exception.status_code, 503)
            self.assertIn(
                "autorización firmada específica", consent_block.exception.detail
            )
        prepare.assert_not_called()
        provider.assert_not_called()
        download.assert_not_called()
        with self.engine.begin() as conn:
            for table in ("rtm_document_extractions", "rtm_validated_facts", "events"):
                count = conn.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE case_id=CAST(:id AS UUID)"),
                    {"id": case_id},
                ).scalar_one()
                self.assertEqual(count, 0, table)

    def test_case_and_document_guards(self):
        scenarios = [
            (*self._insert_case(paid=False), 402),
            (*self._insert_case(authorized=False), 409),
            (*self._insert_case(department="traffic"), 409),
            (
                *self._insert_case(
                    include_original=True,
                    original_kind="rtm_generated_pdf",
                ),
                409,
            ),
        ]

        with patch.dict(os.environ, operator_http_environment()):
            for case_id, original_id, expected in scenarios:
                with self.subTest(case_id=case_id, expected=expected):
                    with self.assertRaises(HTTPException) as rejected:
                        self._extract_synthetic_case(case_id, original_id)
                    self.assertEqual(rejected.exception.status_code, expected)


if __name__ == "__main__":
    unittest.main()
