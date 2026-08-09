from datetime import datetime, timezone
from pathlib import Path
import unittest

from fastapi import HTTPException

from rtm_core.authority_repository import (
    FamilyResolutionRecord,
    ValidatedFactsRecord,
    model_digest,
)
from rtm_core.contracts import (
    FactStatus,
    FamilyEvidence,
    FamilyResolution,
    ResolutionStatus,
    SourceReference,
    ValidatedFact,
    ValidatedFacts,
)
from rtm_core.specialist_registry import (
    SPECIALIST_REGISTRY_VERSION,
    _sanitized_core,
    build_legal_preview,
    registered_specialists,
)


NOW = datetime.now(timezone.utc)
DOC_ID = "doc-1"


def _source() -> SourceReference:
    return SourceReference(
        document_id=DOC_ID,
        page_index=0,
        extraction_method="vision+operator",
        evidence="Conducción temeraria",
        confidence=0.99,
    )


def _fact(value) -> ValidatedFact:
    return ValidatedFact(
        value=value,
        status=FactStatus.VALIDATED,
        confidence=0.99,
        sources=[_source()],
    )


def _facts_record(*, complete: bool = True) -> ValidatedFactsRecord:
    mapping = {
        "hecho_denunciado_literal": _fact(
            "Conducir de forma temeraria creando un riesgo grave para otros usuarios"
        ),
        "raw_text_vision": _fact(
            "TEXTO IMPRESO DEL FORMULARIO: km/h radar exceso de velocidad"
        ),
        "organismo": _fact("Servei Català de Trànsit"),
        "expediente_ref": _fact("02510067072-0"),
        "sancion_importe_eur": _fact(500),
        "puntos_detraccion": _fact(6),
    }
    if complete:
        mapping.update(
            {
                "fase_procedimental": _fact("notificación de denuncia e iniciación"),
                "fecha_limite": _fact("2026-08-20"),
            }
        )
    facts = ValidatedFacts(
        case_id="case-1",
        service="traffic",
        extractor_version="traffic_fine_reanalysis_v1_18",
        facts=mapping,
        source_document_ids=[DOC_ID],
        frozen=True,
    )
    return ValidatedFactsRecord(
        id="facts-1",
        case_id="case-1",
        sequence=1,
        facts=facts,
        payload_sha256=model_digest(facts),
        frozen=True,
        created_by="rtm-test",
        created_at=NOW,
        updated_at=NOW,
        frozen_by="ops:ramon",
        frozen_at=NOW,
    )


def _family_record(facts_record: ValidatedFactsRecord, family: str = "temeraria") -> FamilyResolutionRecord:
    specialist = f"traffic.{family}"
    resolution = FamilyResolution(
        case_id="case-1",
        service="traffic",
        facts_version=facts_record.facts.version,
        status=ResolutionStatus.RESOLVED,
        family=family,
        confidence=0.995,
        evidence=[
            FamilyEvidence(
                code="explicit_fact",
                description="El hecho validado identifica la familia.",
                source_fact_keys=["hecho_denunciado_literal"],
                source_document_ids=[DOC_ID],
                confidence=0.995,
            )
        ],
        specialist=specialist,
        locked=True,
        resolved_at=NOW,
    )
    return FamilyResolutionRecord(
        id="family-1",
        case_id="case-1",
        validated_facts_id=facts_record.id,
        sequence=1,
        resolution=resolution,
        payload_sha256=model_digest(resolution),
        locked=True,
        created_by="rtm-family-core",
        created_at=NOW,
        updated_at=NOW,
        locked_by="ops:ramon",
        locked_at=NOW,
    )


class TemerariaSpecialistTest(unittest.TestCase):
    def test_registry_version_and_temeraria_registration(self):
        self.assertEqual(SPECIALIST_REGISTRY_VERSION, "rtm_specialist_registry_v1_1")
        self.assertIn("traffic.temeraria", registered_specialists())

    def test_raw_and_legacy_fields_never_reach_specialist(self):
        facts = _facts_record()
        family = _family_record(facts)
        core = _sanitized_core(facts, family)
        self.assertNotIn("raw_text_vision", core)
        self.assertEqual(core["familia_resuelta"], "temeraria")
        self.assertEqual(core["tipo_infraccion"], "temeraria")

    def test_temeraria_builds_structured_preview(self):
        facts = _facts_record()
        preview = build_legal_preview(facts, _family_record(facts))
        self.assertEqual(preview.family, "temeraria")
        self.assertEqual(preview.specialist, "traffic.temeraria")
        self.assertEqual(preview.document_type, "ESCRITO DE ALEGACIONES")
        self.assertTrue(preview.legal_arguments)
        self.assertTrue(any(arg.priority == "primary" for arg in preview.legal_arguments))
        self.assertIn("hecho_denunciado_literal", preview.source_fact_keys)
        self.assertFalse(
            any(item.severity.value == "blocking" for item in preview.missing_items)
        )
        self.assertNotIn("TEXTO IMPRESO DEL FORMULARIO", repr(preview))

    def test_unresolved_phase_and_deadline_block_freeze(self):
        facts = _facts_record(complete=False)
        preview = build_legal_preview(facts, _family_record(facts))
        blocking = {
            item.code
            for item in preview.missing_items
            if item.severity.value == "blocking"
        }
        self.assertIn("procedural_phase_unresolved", blocking)
        self.assertIn("deadline_unresolved", blocking)

    def test_unregistered_specialist_never_falls_back_to_generic(self):
        facts = _facts_record()
        with self.assertRaises(HTTPException) as ctx:
            build_legal_preview(facts, _family_record(facts, family="velocidad"))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertTrue(ctx.exception.detail["requires_operator_review"])

    def test_specialist_router_is_mounted_and_does_not_use_attention(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        source = Path("rtm_core/specialist_registry.py").read_text(encoding="utf-8")
        self.assertIn("rtm_core.specialist_router", app_source)
        self.assertIn("app.include_router(rtm_core_specialist_router)", app_source)
        self.assertIn("ai.infractions.temeraria", source)
        self.assertNotIn("ai.infractions.atencion", source)


if __name__ == "__main__":
    unittest.main()
