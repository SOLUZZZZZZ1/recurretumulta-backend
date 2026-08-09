from __future__ import annotations

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
from rtm_core.contracts import (
    FactStatus,
    PreviewStatus,
    SourceReference,
    ValidatedFact,
    ValidatedFacts,
)
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
from rtm_core.specialist_dispatch import build_legal_preview
from rtm_core.workspace_service import WORKSPACE_VERSION, build_case_workspace


RUN_POSTGRES_INTEGRATION = os.getenv("RTM_CORE_INTEGRATION_DB") == "1"
DATABASE_URL = os.getenv("DATABASE_URL", "")


@unittest.skipUnless(
    RUN_POSTGRES_INTEGRATION and DATABASE_URL,
    "Requiere PostgreSQL temporal de RTM CORE",
)
class WorkspacePostgresIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
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
                        contact_name TEXT,
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
            for _, statement in authority_v1_ddl():
                conn.execute(text(statement))

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    @staticmethod
    def _source(document_id: str, evidence: str) -> SourceReference:
        return SourceReference(
            document_id=document_id,
            page_index=0,
            extraction_method="workspace_postgres_ci",
            evidence=evidence,
            confidence=0.99,
        )

    @classmethod
    def _fact(cls, document_id: str, value, evidence: str | None = None) -> ValidatedFact:
        return ValidatedFact(
            value=value,
            status=FactStatus.VALIDATED,
            confidence=0.99,
            sources=[cls._source(document_id, evidence or str(value))],
        )

    def test_workspace_projects_complete_authority_and_never_regresses_after_submission(self):
        case_id = str(uuid.uuid4())
        original_id = str(uuid.uuid4())
        interested = (
            '{"full_name":"Persona Workspace",'
            '"dni_nie":"12345678Z",'
            '"domicilio_notif":"Calle de Prueba 1, Manresa",'
            '"email":"workspace@example.invalid",'
            '"telefono":"600000000",'
            '"matricula":"1234 ABC",'
            '"customer_comment":"Solicita revisión de una denuncia manuscrita."}'
        )

        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO cases(
                        id, status, payment_status, authorized, department,
                        case_type, category, interested_data, expediente_ref,
                        organismo, contact_email, contact_name, test_mode,
                        source_module, customer_comment, created_at, updated_at
                    ) VALUES (
                        CAST(:case_id AS UUID), 'core_review_pending', 'paid', TRUE,
                        'traffic', 'fine', 'traffic', CAST(:interested AS JSONB),
                        '02510067072-0', 'Servei Català de Trànsit',
                        'workspace@example.invalid', 'Persona Workspace', FALSE,
                        'rtm_web', 'Solicita revisión de una denuncia manuscrita.',
                        NOW(), NOW()
                    )
                    """
                ),
                {"case_id": case_id, "interested": interested},
            )
            for document_id, kind, mime in (
                (original_id, "original", "image/tiff"),
                (str(uuid.uuid4()), "identity_front", "image/jpeg"),
                (str(uuid.uuid4()), "identity_back", "image/jpeg"),
                (str(uuid.uuid4()), "authorization_signed", "application/pdf"),
            ):
                conn.execute(
                    text(
                        """
                        INSERT INTO documents(
                            id, case_id, kind, b2_bucket, b2_key, mime,
                            size_bytes, created_at
                        ) VALUES (
                            CAST(:document_id AS UUID), CAST(:case_id AS UUID),
                            :kind, 'ci', :key, :mime, 1024, NOW()
                        )
                        """
                    ),
                    {
                        "document_id": document_id,
                        "case_id": case_id,
                        "kind": kind,
                        "key": f"workspace/{kind}",
                        "mime": mime,
                    },
                )

            facts = ValidatedFacts(
                case_id=case_id,
                service="traffic",
                extractor_version="traffic_fine_reanalysis_v1_18+workspace_ci",
                facts={
                    "organismo": self._fact(original_id, "Servei Català de Trànsit"),
                    "expediente_ref": self._fact(original_id, "02510067072-0"),
                    "hecho_denunciado_literal": self._fact(
                        original_id,
                        "Conducir de forma temeraria creando un riesgo grave.",
                    ),
                    "sancion_importe_eur": self._fact(original_id, 500),
                    "puntos_detraccion": self._fact(original_id, 6),
                    "fase_procedimental": self._fact(
                        original_id,
                        "notificación de denuncia e iniciación",
                    ),
                    "fecha_limite": self._fact(original_id, "2026-08-20"),
                    "matricula": self._fact(original_id, "1234 ABC"),
                },
                source_document_ids=[original_id],
            )
            facts_record = create_validated_facts(
                conn,
                case_id=case_id,
                facts=facts,
                created_by="ci:workspace-facts",
            )
            facts_record = freeze_validated_facts(
                conn,
                case_id,
                facts_record.id,
                "ops:ci",
            )

            resolution = resolve_family(facts_record.facts)
            family_record = create_family_resolution(
                conn,
                case_id=case_id,
                resolution=resolution,
                created_by="ci:workspace-family",
                validated_facts_id=facts_record.id,
            )
            family_record = lock_family_resolution(
                conn,
                case_id,
                family_record.id,
                "ops:ci",
            )

            draft = build_legal_preview(facts_record, family_record)
            preview_record = create_preview(
                conn,
                case_id=case_id,
                preview=draft,
                created_by="ci:workspace-specialist",
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
                    return_value=b"DOCX-WORKSPACE-CI",
                ),
                patch(
                    "rtm_core.generation_gateway.build_pdf",
                    return_value=b"%PDF-WORKSPACE-CI",
                ),
                patch(
                    "rtm_core.generation_gateway.upload_bytes",
                    side_effect=[
                        ("ci", "workspace/recurso.docx"),
                        ("ci", "workspace/recurso.pdf"),
                    ],
                ),
            ):
                resource = generate_from_frozen_preview(
                    conn,
                    case_id=case_id,
                    preview_id=preview_record.id,
                    generated_by="ci:workspace-generate",
                )
            approve_resource_for_submission(
                conn,
                case_id=case_id,
                resource_id=resource.id,
                approved_by="ops:ci",
            )

            workspace = build_case_workspace(conn, case_id)
            self.assertEqual(workspace["workspace_version"], WORKSPACE_VERSION)
            self.assertTrue(workspace["readiness"]["ready"])
            self.assertEqual(
                workspace["readiness"]["quote"]["amount_cents"],
                1000,
            )
            self.assertEqual(workspace["next_step"]["stage"], "presentation_ready")
            self.assertEqual(
                workspace["authority"]["family_resolution"]["latest_active"]["resolution"]["family"],
                "temeraria",
            )
            self.assertEqual(
                workspace["authority"]["legal_preview"]["latest_active"]["status"],
                "frozen",
            )
            self.assertEqual(
                workspace["authority"]["generated_resource"]["latest_active"]["status"],
                "final_ready",
            )
            self.assertEqual(workspace["case"]["identity"]["full_name"], "Persona Workspace")
            self.assertTrue(workspace["documents"])
            self.assertTrue(all("payload" not in item for item in workspace["timeline"]))

            conn.execute(
                text(
                    "UPDATE cases SET status='submitted', updated_at=NOW() "
                    "WHERE id=CAST(:case_id AS UUID)"
                ),
                {"case_id": case_id},
            )
            submitted = build_case_workspace(conn, case_id)
            self.assertEqual(submitted["next_step"]["stage"], "submitted_followup")
            self.assertEqual(
                submitted["next_step"]["primary_action"],
                "monitor_followup",
            )
            self.assertNotEqual(
                submitted["next_step"]["stage"],
                "presentation_ready",
            )


if __name__ == "__main__":
    unittest.main()
