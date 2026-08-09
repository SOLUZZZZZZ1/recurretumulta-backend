from __future__ import annotations

import unittest

from rtm_core.contracts import FactStatus
from rtm_core.family_core import resolve_family
from rtm_core.reanalysis_adapter import (
    REANALYSIS_ADAPTER_VERSION,
    build_validated_facts_from_reanalysis,
)


CASE_ID = "case-adapter-1"
DOC_ID = "doc-adapter-1"


def _wrapper(*, with_documents: bool = True):
    source_ids = [DOC_ID] if with_documents else []
    pages = (
        [
            {
                "page_index": 1,
                "document_id": DOC_ID,
                "mime_detected": "image/tiff",
            }
        ]
        if with_documents
        else []
    )
    return {
        "storage": {"source_document_ids": source_ids},
        "pages": pages,
        "extracted": {
            "extractor_version": "traffic_fine_reanalysis_v1_18",
            "source_document_ids": source_ids,
            # Evidencia legacy/ruido que el puente no puede convertir en autoridad.
            "familia_resuelta": "velocidad",
            "tipo_infraccion": "velocidad",
            "specialist_dispatch": "velocidad",
            "raw_text_blob": "CASILLA IMPRESA km/h RADAR",
            "velocidad_medida_kmh": 63,
            "velocidad_limite_kmh": 11,
            "hecho_denunciado_literal": (
                "Conducir de forma temeraria creando un riesgo grave."
            ),
            "organismo": "Servei Català de Trànsit",
            "expediente_ref": "02510067072-0",
        },
    }


def _event(*, low_legibility: bool = False):
    return {
        "extractor_version": "traffic_fine_reanalysis_v1_18",
        "handwritten_precision_version": "traffic_handwritten_precision_v1_0",
        "handwritten_precision_values": {
            "hecho_denunciado_literal": (
                "Conducir de forma temeraria creando un riesgo grave."
            ),
        },
        "handwritten_precision_confidence": {
            "hecho_denunciado_literal": 0.99,
        },
        "handwritten_precision_evidence": {
            "hecho_denunciado_literal": "CONDUCIR DE FORMA TEMERARIA",
        },
        "handwritten_precision_quality": {
            "legibility": "low" if low_legibility else "high",
            "legibility_score": 0.45 if low_legibility else 0.98,
        },
        "traffic_generic_facts_version": "traffic_generic_facts_v1_2",
        "traffic_generic_facts": {
            "organismo": "Servei Català de Trànsit",
            "expediente_ref": "02510067072-0",
            "document_type": "denuncia",
            "procedural_stage_hint": "initial_notice",
            "sancion_ordinaria_eur": 500,
            "puntos_detraccion": 6,
        },
        "traffic_generic_facts_confidence": {
            "organismo": 0.99,
            "expediente_ref": 0.99,
            "document_type": 0.98,
            "procedural_stage_hint": 0.96,
            "sancion_ordinaria_eur": 0.98,
            "puntos_detraccion": 0.98,
        },
        "traffic_generic_facts_evidence": {
            "organismo": "SERVEI CATALÀ DE TRÀNSIT",
            "expediente_ref": "02510067072-0",
            "document_type": "DENÚNCIA / INICIACIÓ",
            "procedural_stage_hint": "NOTIFICACIÓ DE DENÚNCIA",
            "sancion_ordinaria_eur": "500,00 EUR",
            "puntos_detraccion": "6 PUNTS",
        },
        "unresolved_critical_fields": [],
        "missing_required_fields": [],
        "critical_conflicts_resolved": [],
    }


class ReanalysisAdapterTest(unittest.TestCase):
    def test_legacy_family_and_printed_kmh_never_become_authority(self):
        result = build_validated_facts_from_reanalysis(
            case_id=CASE_ID,
            wrapper=_wrapper(),
            event_payload=_event(),
        )

        self.assertEqual(result.adapter_version, REANALYSIS_ADAPTER_VERSION)
        self.assertIn("familia_resuelta", result.ignored_fields)
        self.assertIn("tipo_infraccion", result.ignored_fields)
        self.assertEqual(
            result.facts.facts["hecho_denunciado_literal"].status,
            FactStatus.VALIDATED,
        )
        self.assertEqual(
            result.facts.facts["velocidad_medida_kmh"].status,
            FactStatus.UNRESOLVED,
        )
        self.assertEqual(
            result.facts.facts["velocidad_limite_kmh"].status,
            FactStatus.UNRESOLVED,
        )

        resolution = resolve_family(result.facts)
        self.assertEqual(resolution.family, "temeraria")
        self.assertNotEqual(resolution.family, "velocidad")

    def test_low_legibility_handwriting_stays_unresolved(self):
        result = build_validated_facts_from_reanalysis(
            case_id=CASE_ID,
            wrapper=_wrapper(),
            event_payload=_event(low_legibility=True),
        )
        fact = result.facts.facts["hecho_denunciado_literal"]
        self.assertEqual(fact.status, FactStatus.UNRESOLVED)
        self.assertIsNone(fact.value)
        self.assertIn("hecho_denunciado_literal", result.unresolved_fields)
        self.assertTrue(any("calidad manuscrita" in note.lower() for note in fact.notes))

    def test_distinct_high_confidence_readings_become_conflict(self):
        event = _event()
        event["semaforo_precision_version"] = "semaforo_precision_v1_0"
        event["semaforo_precision_values"] = {
            "organismo": "Ajuntament de Terrassa",
        }
        event["semaforo_precision_confidence"] = {"organismo": 0.98}
        event["semaforo_precision_evidence"] = {
            "organismo": "AJUNTAMENT DE TERRASSA",
        }

        result = build_validated_facts_from_reanalysis(
            case_id=CASE_ID,
            wrapper=_wrapper(),
            event_payload=event,
        )
        fact = result.facts.facts["organismo"]
        self.assertEqual(fact.status, FactStatus.CONFLICTED)
        self.assertTrue(fact.conflicts)
        self.assertIn("organismo", result.conflicted_fields)

    def test_missing_document_links_prevent_validation(self):
        result = build_validated_facts_from_reanalysis(
            case_id=CASE_ID,
            wrapper=_wrapper(with_documents=False),
            event_payload=_event(),
        )
        self.assertFalse(result.accepted_fields)
        self.assertTrue(result.warnings)
        for fact in result.facts.facts.values():
            self.assertNotEqual(fact.status, FactStatus.VALIDATED)

    def test_nested_normative_reference_is_flattened_with_traceability(self):
        event = _event()
        event["velocity_secondary_facts"] = {
            "normative_reference": {
                "norm": "Reglamento General de Circulación",
                "article": "3.1",
            }
        }
        event["velocity_secondary_facts_confidence"] = {
            "normative_reference": 0.97,
        }
        event["velocity_secondary_facts_evidence"] = {
            "normative_reference": "RGC ART. 3.1",
        }

        result = build_validated_facts_from_reanalysis(
            case_id=CASE_ID,
            wrapper=_wrapper(),
            event_payload=event,
        )
        self.assertEqual(
            result.facts.facts["norma_hint"].status,
            FactStatus.VALIDATED,
        )
        self.assertEqual(
            result.facts.facts["articulo_infringido_num"].value,
            "3.1",
        )
        self.assertEqual(
            result.facts.facts["articulo_infringido_num"].sources[0].document_id,
            DOC_ID,
        )


if __name__ == "__main__":
    unittest.main()
