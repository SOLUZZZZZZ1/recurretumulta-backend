from datetime import datetime, timezone
import unittest

from rtm_core.authority_repository import (
    FamilyResolutionRecord,
    ValidatedFactsRecord,
    model_digest,
    validated_model_copy,
)
from rtm_core.contracts import (
    FactStatus,
    PreviewStatus,
    SourceReference,
    ValidatedFact,
    ValidatedFacts,
)
from rtm_core.family_core import resolve_family
from rtm_core.generation_gateway import render_legal_preview
from rtm_core.preview_repository import validated_preview_copy
from rtm_core.specialist_registry import build_legal_preview


NOW = datetime.now(timezone.utc)
DOC_ID = "doc-manuscrito-1"


def _source() -> SourceReference:
    return SourceReference(
        document_id=DOC_ID,
        page_index=0,
        extraction_method="manuscript_precision+operator",
        evidence="Conducción temeraria",
        confidence=0.98,
    )


def _fact(value) -> ValidatedFact:
    return ValidatedFact(
        value=value,
        status=FactStatus.VALIDATED,
        confidence=0.98,
        sources=[_source()],
    )


class CoreEndToEndContractTest(unittest.TestCase):
    def test_manuscript_temeraria_reaches_deterministic_document_without_velocity(self):
        facts = ValidatedFacts(
            case_id="case-1",
            service="traffic",
            extractor_version="traffic_fine_reanalysis_v1_18",
            facts={
                "hecho_denunciado_literal": _fact(
                    "Conducir de forma temeraria creando un riesgo grave. Casilla impresa: km/h."
                ),
                "organismo": _fact("Servei Català de Trànsit"),
                "expediente_ref": _fact("02510067072-0"),
                "sancion_importe_eur": _fact(500),
                "puntos_detraccion": _fact(6),
                "fase_procedimental": _fact("notificación de denuncia e iniciación"),
                "fecha_limite": _fact("2026-08-20"),
            },
            source_document_ids=[DOC_ID],
            frozen=True,
        )
        facts_record = ValidatedFactsRecord(
            id="facts-1",
            case_id="case-1",
            sequence=1,
            facts=facts,
            payload_sha256=model_digest(facts),
            frozen=True,
            created_by="rtm-extractor",
            created_at=NOW,
            updated_at=NOW,
            frozen_by="ops:ramon",
            frozen_at=NOW,
        )

        resolution = resolve_family(facts)
        self.assertEqual(resolution.family, "temeraria")
        self.assertNotEqual(resolution.family, "velocidad")
        locked_resolution = validated_model_copy(resolution, locked=True)
        family_record = FamilyResolutionRecord(
            id="family-1",
            case_id="case-1",
            validated_facts_id=facts_record.id,
            sequence=1,
            resolution=locked_resolution,
            payload_sha256=model_digest(locked_resolution),
            locked=True,
            created_by="rtm-family-core",
            created_at=NOW,
            updated_at=NOW,
            locked_by="ops:ramon",
            locked_at=NOW,
        )

        draft = build_legal_preview(facts_record, family_record)
        approved = validated_preview_copy(
            draft,
            status=PreviewStatus.APPROVED,
            approved_by="ops:ramon",
            approved_at=NOW,
        )
        frozen = validated_preview_copy(
            approved,
            status=PreviewStatus.FROZEN,
            frozen_at=NOW,
        )
        text = render_legal_preview(
            frozen,
            {
                "interested_data": {
                    "full_name": "Persona de prueba",
                    "dni_nie": "12345678Z",
                    "domicilio_notif": "Calle de Prueba 1, Manresa",
                },
                "expediente_ref": "02510067072-0",
            },
        )

        self.assertIn("CONDUCIR DE FORMA TEMERARIA", text.upper())
        self.assertIn("INSUFICIENCIA PROBATORIA", text.upper())
        self.assertNotIn("PRESUNTO EXCESO DE VELOCIDAD", text.upper())
        self.assertNotIn("63/11", text)


if __name__ == "__main__":
    unittest.main()
