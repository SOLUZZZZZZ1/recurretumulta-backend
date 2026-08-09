from __future__ import annotations

import json
import os
import uuid
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text

from rtm_core.authority_repository import (
    create_family_resolution,
    create_validated_facts,
    freeze_validated_facts,
    lock_family_resolution,
)
from rtm_core.contracts import FactStatus, PreviewStatus
from rtm_core.family_core import resolve_family
from rtm_core.generation_gateway import (
    approve_resource_for_submission,
    generate_from_frozen_preview,
)
from rtm_core.migration_router import authority_v1_ddl
from rtm_core.preview_repository import (
    approve_preview,
    create_preview,
    freeze_preview,
    submit_for_review,
)
from rtm_core.reanalysis_adapter import (
    build_validated_facts_from_reanalysis,
    load_latest_reanalysis_snapshot,
)
from rtm_core.specialist_registry import build_legal_preview


RUN_POSTGRES_INTEGRATION = os.getenv("RTM_CORE_INTEGRATION_DB") == "1"
DATABASE_URL = os.getenv("DATABASE_URL", "")


@unittest.skipUnless(
    RUN_POSTGRES_INTEGRATION and DATABASE_URL,
    "Requiere PostgreSQL temporal de RTM CORE",
)
class PostgresAuthorityIntegrationTest(unittest.TestCase):
    """Prueba el circuito real sobre PostgreSQL, sin B2 ni expedientes reales."""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        cls._reset_legacy_schema()
        cls._apply_core_migration_twice()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    @classmethod
    def _reset_legacy_schema(cls):
        with cls.engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
            conn.execute(
                text(
                    """
                    CREATE TABLE cases (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        status TEXT NOT NULL DEFAULT 'uploaded',
                        payment_status TEXT,
                        authorized BOOLEAN NOT NULL DEFAULT FALSE,
                        department TEXT,
                        case_type TEXT,
                        category TEXT,
                        interested_data JSONB,
                        expediente_ref TEXT,
                        organismo TEXT,
                        contact_email TEXT,
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
    def _apply_core_migration_twice(cls):
        # La segunda pasada debe ser inocua: Render puede reintentar una migración.
        for _ in range(2):
            with cls.engine.begin() as conn:
                for _, statement in authority_v1_ddl():
                    conn.execute(text(statement))

        with cls.engine.begin() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema='public'
                        """
                    )
                ).fetchall()
            }
            for expected in (
                "rtm_validated_facts",
                "rtm_family_resolutions",
                "rtm_legal_previews",
                "rtm_generated_resources",
            ):
                if expected not in tables:
                    raise AssertionError(f"Falta tabla migrada: {expected}")

    def test_full_authority_chain_reaches_approved_resource(self):
        case_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        wrapper = {
            "storage": {"source_document_ids": [document_id]},
            "pages": [
                {
                    "page_index": 1,
                    "document_id": document_id,
                    "mime_detected": "image/tiff",
                }
            ],
            "extracted": {
                "extractor_version": "traffic_fine_reanalysis_v1_18",
                "source_document_ids": [document_id],
                # Error legacy deliberado: el adaptador no puede promoverlo.
                "familia_resuelta": "velocidad",
                "tipo_infraccion": "velocidad",
                "raw_text_blob": "CASILLA IMPRESA km/h",
                "velocidad_medida_kmh": 63,
                "velocidad_limite_kmh": 11,
                "hecho_denunciado_literal": (
                    "Conducir de forma temeraria creando un riesgo grave."
                ),
            },
        }
        event_payload = {
            "extractor_version": "traffic_fine_reanalysis_v1_18",
            "handwritten_precision_version": "traffic_handwritten_precision_v1_0",
            "handwritten_precision_values": {
                "hecho_denunciado_literal": (
                    "Conducir de forma temeraria creando un riesgo grave."
                )
            },
            "handwritten_precision_confidence": {
                "hecho_denunciado_literal": 0.99,
            },
            "handwritten_precision_evidence": {
                "hecho_denunciado_literal": "CONDUCIR DE FORMA TEMERARIA",
            },
            "handwritten_precision_quality": {
                "legibility": "high",
                "legibility_score": 0.99,
            },
            "traffic_generic_facts_version": "traffic_generic_facts_v1_2",
            "traffic_generic_facts": {
                "organismo": "Servei Català de Trànsit",
                "expediente_ref": "02510067072-0",
                "document_type": "denuncia",
                "procedural_stage_hint": "initial_notice",
                "sancion_ordinaria_eur": 500,
                "puntos_detraccion": 6,
                "fecha_limite": "2026-08-20",
            },
            "traffic_generic_facts_confidence": {
                "organismo": 0.99,
                "expediente_ref": 0.99,
                "document_type": 0.98,
                "procedural_stage_hint": 0.98,
                "sancion_ordinaria_eur": 0.98,
                "puntos_detraccion": 0.98,
                "fecha_limite": 0.99,
            },
            "traffic_generic_facts_evidence": {
                "organismo": "SERVEI CATALÀ DE TRÀNSIT",
                "expediente_ref": "02510067072-0",
                "document_type": "DENÚNCIA / INICIACIÓ",
                "procedural_stage_hint": "NOTIFICACIÓ DE DENÚNCIA",
                "sancion_ordinaria_eur": "500,00 EUR",
                "puntos_detraccion": "6 PUNTS",
                "fecha_limite": "20-08-2026",
            },
            "unresolved_critical_fields": [],
            "missing_required_fields": [],
            "critical_conflicts_resolved": [],
        }

        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO cases(
                        id, status, payment_status, authorized, department,
                        case_type, category, interested_data, expediente_ref,
                        organismo, contact_email, test_mode, created_at, updated_at
                    ) VALUES (
                        CAST(:case_id AS UUID), 'core_review_pending', 'paid', TRUE,
                        'traffic', 'fine', 'traffic', CAST(:interested AS JSONB),
                        '02510067072-0', 'Servei Català de Trànsit',
                        'test@example.invalid', FALSE, NOW(), NOW()
                    )
                    """
                ),
                {
                    "case_id": case_id,
                    "interested": (
                        '{"full_name":"Persona de prueba",'
                        '"dni_nie":"12345678Z",'
                        '"domicilio_notif":"Calle de Prueba 1, Manresa"}'
                    ),
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO documents(
                        id, case_id, kind, b2_bucket, b2_key, mime,
                        size_bytes, created_at
                    ) VALUES (
                        CAST(:document_id AS UUID), CAST(:case_id AS UUID),
                        'original', 'ci', 'original/manuscrito.tif',
                        'image/tiff', 1024, NOW()
                    )
                    """
                ),
                {"document_id": document_id, "case_id": case_id},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO extractions(
                        case_id, extracted_json, confidence, model, created_at
                    ) VALUES (
                        CAST(:case_id AS UUID), CAST(:payload AS JSONB), 0.99,
                        'rtm_intelligence_core_v1+traffic_fine+v1_18', NOW()
                    )
                    """
                ),
                {"case_id": case_id, "payload": json.dumps(wrapper)},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO events(case_id, type, payload, created_at)
                    VALUES (
                        CAST(:case_id AS UUID), 'case_reanalysis_completed',
                        CAST(:payload AS JSONB), NOW()
                    )
                    """
                ),
                {"case_id": case_id, "payload": json.dumps(event_payload)},
            )

        with self.engine.begin() as conn:
            stored_wrapper, stored_event = load_latest_reanalysis_snapshot(conn, case_id)
            adapted = build_validated_facts_from_reanalysis(
                case_id=case_id,
                wrapper=stored_wrapper,
                event_payload=stored_event,
            )
            self.assertEqual(
                adapted.facts.facts["hecho_denunciado_literal"].status,
                FactStatus.VALIDATED,
            )
            self.assertEqual(
                adapted.facts.facts["velocidad_medida_kmh"].status,
                FactStatus.UNRESOLVED,
            )

            facts_record = create_validated_facts(
                conn,
                case_id=case_id,
                facts=adapted.facts,
                created_by="ci:reanalysis-adapter",
            )
            facts_record = freeze_validated_facts(
                conn,
                case_id,
                facts_record.id,
                "ops:ci",
            )

            resolution = resolve_family(facts_record.facts)
            self.assertEqual(resolution.family, "temeraria")
            self.assertNotEqual(resolution.family, "velocidad")

            family_record = create_family_resolution(
                conn,
                case_id=case_id,
                resolution=resolution,
                created_by="ci:family-core",
                validated_facts_id=facts_record.id,
            )
            family_record = lock_family_resolution(
                conn,
                case_id,
                family_record.id,
                "ops:ci",
            )

            draft = build_legal_preview(facts_record, family_record)
            self.assertFalse(
                [
                    item.code
                    for item in draft.missing_items
                    if item.severity.value == "blocking"
                ]
            )
            preview_record = create_preview(
                conn,
                case_id=case_id,
                preview=draft,
                created_by="ci:traffic.temeraria",
            )
            preview_record = submit_for_review(
                conn,
                case_id,
                preview_record.id,
                "ops:ci",
            )
            preview_record = approve_preview(
                conn,
                case_id,
                preview_record.id,
                "ops:ci",
            )
            preview_record = freeze_preview(
                conn,
                case_id,
                preview_record.id,
                "ops:ci",
            )
            self.assertEqual(preview_record.status, PreviewStatus.FROZEN)

            with (
                patch(
                    "rtm_core.generation_gateway.build_docx",
                    return_value=b"DOCX-CI",
                ),
                patch(
                    "rtm_core.generation_gateway.build_pdf",
                    return_value=b"%PDF-CI",
                ),
                patch(
                    "rtm_core.generation_gateway.upload_bytes",
                    side_effect=[
                        ("ci", "generated/recurso.docx"),
                        ("ci", "generated/recurso.pdf"),
                    ],
                ),
            ):
                resource = generate_from_frozen_preview(
                    conn,
                    case_id=case_id,
                    preview_id=preview_record.id,
                    generated_by="ci:generate",
                )

            resource = approve_resource_for_submission(
                conn,
                case_id=case_id,
                resource_id=resource.id,
                approved_by="ops:ci",
            )
            self.assertEqual(resource.status, "final_ready")
            self.assertEqual(resource.approved_by, "ops:ci")

            status = conn.execute(
                text("SELECT status FROM cases WHERE id=CAST(:id AS UUID)"),
                {"id": case_id},
            ).scalar_one()
            self.assertEqual(status, "ready_to_submit")

            links = conn.execute(
                text(
                    """
                    SELECT fr.validated_facts_id, lp.validated_facts_id,
                           lp.family_resolution_id, gr.legal_preview_id,
                           gr.pdf_document_id, gr.docx_document_id
                    FROM rtm_family_resolutions fr
                    JOIN rtm_legal_previews lp
                      ON lp.family_resolution_id=fr.id
                    JOIN rtm_generated_resources gr
                      ON gr.legal_preview_id=lp.id
                    WHERE fr.case_id=CAST(:case_id AS UUID)
                    """
                ),
                {"case_id": case_id},
            ).one()
            self.assertEqual(str(links[0]), facts_record.id)
            self.assertEqual(str(links[1]), facts_record.id)
            self.assertEqual(str(links[2]), family_record.id)
            self.assertEqual(str(links[3]), preview_record.id)
            self.assertTrue(links[4])
            self.assertTrue(links[5])

            # Generate es idempotente para una misma previa congelada.
            same = generate_from_frozen_preview(
                conn,
                case_id=case_id,
                preview_id=preview_record.id,
                generated_by="ci:generate-retry",
            )
            self.assertEqual(same.id, resource.id)


if __name__ == "__main__":
    unittest.main()
