from __future__ import annotations

import os
import uuid
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app import app
from rtm_core.document_facts_router import DOCUMENT_FACTS_GATEWAY_VERSION
from rtm_core.migration_router import authority_v1_ddl
from rtm_core.workspace_policy_ext import determine_workspace_stage


RUN_POSTGRES_INTEGRATION = os.getenv("RTM_CORE_INTEGRATION_DB") == "1"
DATABASE_URL = os.getenv("DATABASE_URL", "")
OPERATOR_TOKEN = "ci-document-facts-token"


@unittest.skipUnless(
    RUN_POSTGRES_INTEGRATION and DATABASE_URL,
    "Requiere PostgreSQL temporal de RTM CORE",
)
class DocumentFactsGatewayIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        cls._reset_schema()
        cls._apply_migration_twice()
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
                        mime TEXT,
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
    def _apply_migration_twice(cls):
        for _ in range(2):
            with cls.engine.begin() as conn:
                for _, statement in authority_v1_ddl():
                    conn.execute(text(statement))

    def _insert_case(
        self,
        *,
        department: str = "debt",
        paid: bool = True,
        authorized: bool = True,
        document_kind: str = "original",
    ) -> tuple[str, str]:
        case_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO cases(
                        id, status, payment_status, authorized, department,
                        case_type, category, created_at, updated_at
                    ) VALUES (
                        CAST(:case_id AS UUID), 'core_review_pending',
                        :payment_status, :authorized, :department,
                        :case_type, :category, NOW(), NOW()
                    )
                    """
                ),
                {
                    "case_id": case_id,
                    "payment_status": "paid" if paid else "pending",
                    "authorized": authorized,
                    "department": department,
                    "case_type": "invoice" if department == "debt" else department,
                    "category": department,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO documents(id, case_id, kind, mime, created_at)
                    VALUES (
                        CAST(:document_id AS UUID), CAST(:case_id AS UUID),
                        :kind, 'application/pdf', NOW()
                    )
                    """
                ),
                {
                    "document_id": document_id,
                    "case_id": case_id,
                    "kind": document_kind,
                },
            )
        return case_id, document_id

    @staticmethod
    def _body(
        case_id: str,
        document_id: str,
        *,
        service: str = "debt",
    ) -> dict:
        return {
            "packet": {
                "case_id": case_id,
                "service": service,
                "extractor_version": f"{service}_document_extractor_ci_v1",
                "source_document_ids": [document_id],
                "observations": [
                    {
                        "field": "descripcion_hecho",
                        "value": "La factura F-2026 está vencida e impagada.",
                        "document_id": document_id,
                        "page_index": 0,
                        "evidence": "FACTURA F-2026 — PENDIENTE DE PAGO",
                        "confidence": 0.99,
                        "extraction_method": "cross_service_document_ci_v1",
                        "source_type": "document_vision",
                        "notes": [],
                    },
                    {
                        "field": "factura_numero",
                        "value": "F-2026",
                        "document_id": document_id,
                        "page_index": 0,
                        "evidence": "Factura F-2026",
                        "confidence": 0.99,
                        "extraction_method": "cross_service_document_ci_v1",
                        "source_type": "document_vision",
                        "notes": [],
                    },
                    {
                        "field": "importe_deuda_eur",
                        "value": "1.250,50 EUR",
                        "document_id": document_id,
                        "page_index": 0,
                        "evidence": "TOTAL PENDIENTE 1.250,50 EUR",
                        "confidence": 0.99,
                        "extraction_method": "cross_service_document_ci_v1",
                        "source_type": "document_vision",
                        "notes": [],
                    },
                ],
                "declared_unresolved": [],
                "quality_flags": [],
            }
        }

    def _headers(self) -> dict[str, str]:
        return {
            "X-Operator-Token": OPERATOR_TOKEN,
            "X-Operator-Actor": "ops:ci-document-facts",
        }

    def test_catalog_preview_and_draft_are_separate_authorized_steps(self):
        case_id, document_id = self._insert_case()
        with patch.dict(os.environ, {"OPERATOR_TOKEN": OPERATOR_TOKEN}):
            catalog = self.client.get(
                "/ops/core/document-facts/catalog/debt",
                headers=self._headers(),
            )
            self.assertEqual(catalog.status_code, 200, catalog.text)
            self.assertEqual(
                catalog.json()["gateway_version"],
                DOCUMENT_FACTS_GATEWAY_VERSION,
            )

            preview = self.client.post(
                f"/ops/core/cases/{case_id}/document-facts/preview",
                headers=self._headers(),
                json=self._body(case_id, document_id),
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            payload = preview.json()
            self.assertFalse(payload["persisted"])
            self.assertFalse(payload["frozen"])
            self.assertFalse(payload["family_resolved"])
            self.assertFalse(payload["generate_allowed"])
            self.assertIn(
                "descripcion_hecho",
                payload["result"]["accepted_fields"],
            )

            with self.engine.begin() as conn:
                count = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM rtm_validated_facts "
                        "WHERE case_id=CAST(:case_id AS UUID)"
                    ),
                    {"case_id": case_id},
                ).scalar_one()
            self.assertEqual(count, 0)

            draft = self.client.post(
                f"/ops/core/cases/{case_id}/document-facts/draft",
                headers=self._headers(),
                json=self._body(case_id, document_id),
            )
            self.assertEqual(draft.status_code, 200, draft.text)
            stored = draft.json()
            self.assertTrue(stored["persisted"])
            self.assertFalse(stored["facts"]["frozen"])
            self.assertFalse(stored["generate_allowed"])
            self.assertEqual(
                stored["facts"]["facts"]["service"],
                "debt",
            )
            self.assertEqual(
                stored["facts"]["facts"]["facts"]["importe_deuda_eur"]["value"],
                1250.5,
            )

            with self.engine.begin() as conn:
                facts_count = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM rtm_validated_facts "
                        "WHERE case_id=CAST(:case_id AS UUID)"
                    ),
                    {"case_id": case_id},
                ).scalar_one()
                event_payload = conn.execute(
                    text(
                        """
                        SELECT payload
                        FROM events
                        WHERE case_id=CAST(:case_id AS UUID)
                          AND type='rtm_document_facts_draft_created'
                        ORDER BY created_at DESC, id DESC
                        LIMIT 1
                        """
                    ),
                    {"case_id": case_id},
                ).scalar_one()
            self.assertEqual(facts_count, 1)
            self.assertEqual(
                event_payload["normalization_version"],
                "rtm_document_normalization_v1_0",
            )
            self.assertNotIn("evidence", event_payload)

            duplicate = self.client.post(
                f"/ops/core/cases/{case_id}/document-facts/draft",
                headers=self._headers(),
                json=self._body(case_id, document_id),
            )
            self.assertEqual(duplicate.status_code, 409)

    def test_gateway_blocks_wrong_service_payment_authorization_and_documents(self):
        cases = [
            (*self._insert_case(paid=False), "debt", 402),
            (*self._insert_case(authorized=False), "debt", 409),
            (*self._insert_case(department="travel"), "debt", 409),
            (*self._insert_case(document_kind="rtm_generated_pdf"), "debt", 409),
        ]
        with patch.dict(os.environ, {"OPERATOR_TOKEN": OPERATOR_TOKEN}):
            for case_id, document_id, service, expected in cases:
                with self.subTest(case_id=case_id, expected=expected):
                    response = self.client.post(
                        f"/ops/core/cases/{case_id}/document-facts/preview",
                        headers=self._headers(),
                        json=self._body(
                            case_id,
                            document_id,
                            service=service,
                        ),
                    )
                    self.assertEqual(response.status_code, expected, response.text)

            traffic_case, traffic_document = self._insert_case(department="traffic")
            traffic = self.client.post(
                f"/ops/core/cases/{traffic_case}/document-facts/preview",
                headers=self._headers(),
                json=self._body(
                    traffic_case,
                    traffic_document,
                    service="traffic",
                ),
            )
            # El propio contrato impide que Tráfico cruce este gateway.
            self.assertIn(traffic.status_code, {409, 422})

    def test_workspace_advertises_only_the_new_non_traffic_gateway(self):
        stage = determine_workspace_stage(
            case_id="case-debt-gateway",
            case_status="core_review_pending",
            payment_status="paid",
            authorized=True,
            readiness_ready=True,
            reanalysis_available=False,
            service="debt",
            specialist_available=False,
        )
        self.assertEqual(stage["stage"], "service_fact_extraction_pending")
        endpoints = {item["endpoint"] for item in stage["actions"]}
        self.assertIn(
            "/ops/core/cases/case-debt-gateway/document-facts/preview",
            endpoints,
        )
        self.assertIn(
            "/ops/core/cases/case-debt-gateway/document-facts/draft",
            endpoints,
        )
        self.assertNotIn(
            "/ops/core/cases/case-debt-gateway/validated-facts",
            endpoints,
        )


if __name__ == "__main__":
    unittest.main()
