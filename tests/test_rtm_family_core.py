from pathlib import Path
import unittest

from rtm_core.contracts import (
    FactStatus,
    ResolutionStatus,
    SourceReference,
    ValidatedFact,
    ValidatedFacts,
)
from rtm_core.family_core import FAMILY_CORE_VERSION, resolve_family


DOC_ID = "doc-1"


def _source() -> SourceReference:
    return SourceReference(
        document_id=DOC_ID,
        page_index=0,
        extraction_method="vision+operator",
        evidence="evidencia validada",
        confidence=0.98,
    )


def _fact(value, *, confidence: float = 0.98) -> ValidatedFact:
    return ValidatedFact(
        value=value,
        status=FactStatus.VALIDATED,
        confidence=confidence,
        sources=[_source()],
    )


def _facts(mapping: dict[str, ValidatedFact]) -> ValidatedFacts:
    return ValidatedFacts(
        case_id="case-1",
        service="traffic",
        extractor_version="traffic_fine_reanalysis_v1_18",
        facts=mapping,
        source_document_ids=[DOC_ID],
    )


class FamilyCoreTest(unittest.TestCase):
    def test_version_is_explicit(self):
        self.assertEqual(FAMILY_CORE_VERSION, "rtm_family_core_v1_0")

    def test_printed_kmh_label_does_not_activate_velocity(self):
        resolution = resolve_family(
            _facts(
                {
                    "hecho_denunciado_literal": _fact(
                        "km/h velocidad límite radar casilla del formulario"
                    )
                }
            )
        )
        self.assertEqual(resolution.status, ResolutionStatus.UNRESOLVED)
        self.assertIsNone(resolution.family)

    def test_raw_ocr_text_never_decides_family(self):
        resolution = resolve_family(
            _facts(
                {
                    "raw_text_vision": _fact(
                        "Exceso de velocidad: circulaba a 120 km/h, límite 90"
                    )
                }
            )
        )
        self.assertEqual(resolution.status, ResolutionStatus.UNRESOLVED)

    def test_explicit_temeraria_has_priority_over_attention(self):
        resolution = resolve_family(
            _facts(
                {
                    "hecho_denunciado_literal": _fact(
                        "Conducir de forma temeraria sin mantener la atención permanente. km/h"
                    )
                }
            )
        )
        self.assertEqual(resolution.status, ResolutionStatus.RESOLVED)
        self.assertEqual(resolution.family, "temeraria")
        self.assertEqual(resolution.specialist, "traffic.temeraria")

    def test_attention_resolves_only_from_explicit_factual_phrase(self):
        resolution = resolve_family(
            _facts(
                {
                    "hecho_denunciado_literal": _fact(
                        "Conducir de forma negligente sin mantener la atención permanente"
                    )
                }
            )
        )
        self.assertEqual(resolution.status, ResolutionStatus.RESOLVED)
        self.assertEqual(resolution.family, "atencion")

    def test_structured_speed_pair_resolves_velocity(self):
        resolution = resolve_family(
            _facts(
                {
                    "velocidad_medida_kmh": _fact(121),
                    "velocidad_limite_kmh": _fact(90),
                }
            )
        )
        self.assertEqual(resolution.status, ResolutionStatus.RESOLVED)
        self.assertEqual(resolution.family, "velocidad")
        self.assertEqual(resolution.specialist, "traffic.velocidad")

    def test_red_phase_resolves_semaphore(self):
        resolution = resolve_family(
            _facts({"semaforo_fase": _fact("roja no intermitente")})
        )
        self.assertEqual(resolution.status, ResolutionStatus.RESOLVED)
        self.assertEqual(resolution.family, "semaforo")

    def test_two_specific_infractions_return_conflict(self):
        resolution = resolve_family(
            _facts(
                {
                    "hecho_denunciado_literal": _fact(
                        "No respetar la luz roja del semáforo utilizando manualmente el teléfono móvil"
                    )
                }
            )
        )
        self.assertEqual(resolution.status, ResolutionStatus.CONFLICTED)
        self.assertIsNone(resolution.family)
        self.assertEqual(
            set(resolution.conflicts[0].candidate_families),
            {"semaforo", "movil"},
        )

    def test_resolver_has_no_legacy_classifier_imports(self):
        source = Path("rtm_core/family_core.py").read_text(encoding="utf-8")
        self.assertNotIn("from scoring import", source)
        self.assertNotIn("ai.infractions.dispatch", source)
        self.assertNotIn("expediente_engine", source)
        self.assertNotIn("draft_recurso", source)

    def test_family_router_is_mounted(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        self.assertIn("rtm_core.family_router", app_source)
        self.assertIn("app.include_router(rtm_core_family_router)", app_source)


if __name__ == "__main__":
    unittest.main()
