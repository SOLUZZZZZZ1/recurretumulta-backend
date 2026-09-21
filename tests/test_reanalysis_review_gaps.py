"""Regresiones de pérdida de pendientes y procedencia en multas sintéticas."""
import unittest

from fastapi import HTTPException

from rtm_core.contracts import FactStatus, ValidatedFacts
from rtm_core.first_direction import build_first_direction
from rtm_core.reanalysis_adapter import build_validated_facts_from_reanalysis


def wrapper():
    return {
        "storage": {"source_document_ids": ["synthetic-document"]},
        "pages": [{"document_id": "synthetic-document", "page_index": 1}],
        "extracted": {
            "extractor_version": "traffic_fine_reanalysis_v1_18",
            "organismo": "Organismo de prueba",
        },
    }


def adapt(data=None, event=None):
    return build_validated_facts_from_reanalysis(
        case_id="synthetic-case", wrapper=data or wrapper(), event_payload=event,
    )


class ReanalysisReviewGapsTest(unittest.TestCase):
    def test_absent_required_fields_survive_without_invented_evidence(self):
        result = adapt(event={"missing_required_fields": ["expediente_ref", "matricula"]})
        for key in ("expediente_ref", "matricula"):
            fact = result.facts.facts[key]
            self.assertEqual(fact.status, FactStatus.UNRESOLVED)
            self.assertIsNone(fact.value)
            self.assertEqual(fact.confidence, 0)
            self.assertEqual(fact.sources, [])
            self.assertIn(key, result.unresolved_fields)
            self.assertIn(key, result.facts.unresolved)
        self.assertFalse(result.facts.frozen)

    def test_pending_only_extraction_remains_reviewable(self):
        data = wrapper()
        del data["extracted"]["organismo"]
        result = adapt(data, {"unresolved_critical_fields": ["matricula"]})
        self.assertEqual(list(result.facts.facts), ["matricula"])
        self.assertFalse(result.accepted_fields)

    def test_core_and_event_pending_fields_are_combined_and_canonicalized(self):
        data = wrapper()
        data["extracted"]["missing_required_fields"] = [{"field": "numero_expediente"}]
        data["extracted"]["unresolved_critical_fields"] = ["matricula"]
        result = adapt(data, {"missing_required_fields": [
            {"key": "expediente"}, {"code": "notification_date"}, "matricula",
        ]})
        self.assertEqual(result.unresolved_fields.count("expediente_ref"), 1)
        self.assertIn("fecha_notificacion", result.unresolved_fields)
        self.assertIn("matricula", result.unresolved_fields)

    def test_core_pending_marker_prevents_automatic_validation(self):
        data = wrapper()
        data["extracted"]["missing_required_fields"] = ["matricula"]
        result = adapt(data, {
            "critical_fields_detected": {"matricula": "1234 ABC"},
            "critical_fields_detected_confidence": {"matricula": 1},
            "critical_fields_detected_evidence": {"matricula": "1234 ABC"},
        })
        self.assertEqual(result.facts.facts["matricula"].status, FactStatus.UNRESOLVED)
        self.assertIsNone(result.facts.facts["matricula"].value)

    def test_model_notification_date_stays_pending_at_full_confidence(self):
        for alias in ("fecha_notificacion", "notification_date", "fecha_recepcion"):
            with self.subTest(alias=alias):
                result = adapt(event={
                    "traffic_generic_facts": {alias: "2026-09-19"},
                    "traffic_generic_facts_confidence": {alias: 1},
                    "traffic_generic_facts_evidence": {alias: "Notificado el 19/09/2026"},
                })
                fact = result.facts.facts["fecha_notificacion"]
                self.assertEqual(fact.status, FactStatus.UNRESOLVED)
                self.assertIsNone(fact.value)
                self.assertEqual(fact.sources[0].source_type, "model_document_observation")
                self.assertTrue(any("2026-09-19" in note for note in fact.notes))
                self.assertNotIn("fecha_limite", result.facts.facts)

    def test_document_and_notification_dates_remain_separate(self):
        data = wrapper()
        data["extracted"].update(fecha_documento="2026-09-10", fecha_notificacion="2026-09-19")
        result = adapt(data)
        self.assertIn("2026-09-10", result.facts.facts["fecha_documento"].notes[-1])
        self.assertIn("2026-09-19", result.facts.facts["fecha_notificacion"].notes[-1])

    def test_conflict_without_selected_candidate_is_preserved(self):
        conflict = {"field": "puntos", "current_value": 0, "vision_value": 6}
        data = wrapper()
        data["extracted"]["critical_conflicts_resolved"] = [conflict]
        result = adapt(data, {
            "missing_required_fields": ["puntos"],
            "critical_conflicts_resolved": [conflict],
        })
        fact = result.facts.facts["puntos_detraccion"]
        self.assertEqual(fact.status, FactStatus.CONFLICTED)
        self.assertIsNone(fact.value)
        self.assertEqual(fact.sources, [])
        self.assertEqual(len(fact.conflicts), 1)
        self.assertIn("'0'", fact.conflicts[0])
        self.assertIn("'6'", fact.conflicts[0])
        self.assertIn("puntos_detraccion", result.conflicted_fields)
        self.assertNotIn("puntos_detraccion", result.unresolved_fields)

    def test_unsafe_and_unknown_pending_names_do_not_cross_the_allowlist(self):
        data = wrapper()
        del data["extracted"]["organismo"]
        with self.assertRaises(HTTPException) as raised:
            adapt(data, {"missing_required_fields": [
                "familia_resuelta", "strategy", "ocr", "arbitrary_field",
            ]})
        self.assertEqual(raised.exception.status_code, 422)

    def test_multiple_pages_do_not_assign_last_page_to_every_fact(self):
        data = wrapper()
        data["pages"].append({"document_id": "synthetic-document", "page_index": 2})
        result = adapt(data)
        self.assertIsNone(result.facts.facts["organismo"].sources[0].page_index)

    def test_one_unambiguous_page_preserves_zero_based_index(self):
        result = adapt()
        self.assertEqual(result.facts.facts["organismo"].sources[0].page_index, 0)

    def test_invalid_page_numbers_are_not_coerced_to_real_pages(self):
        for index in (-1, True, 1.5, "1.5", None):
            with self.subTest(index=index):
                data = wrapper()
                data["pages"][0]["page_index"] = index
                self.assertIsNone(adapt(data).facts.facts["organismo"].sources[0].page_index)

    def test_missing_page_in_multi_page_input_keeps_location_unknown(self):
        data = wrapper()
        data["pages"].append({"document_id": "synthetic-document"})
        self.assertIsNone(adapt(data).facts.facts["organismo"].sources[0].page_index)

    def test_conflict_only_extraction_stays_reviewable_without_source_invention(self):
        data = wrapper()
        del data["extracted"]["organismo"]
        result = adapt(data, {"critical_conflicts_resolved": [
            {"field": "matricula", "current_value": "1234 ABC", "candidate": "1234 ABD"},
        ]})
        self.assertEqual(result.conflicted_fields, ["matricula"])
        self.assertFalse(result.accepted_fields)
        self.assertFalse(result.facts.frozen)
        self.assertEqual(result.facts.facts["matricula"].sources, [])

    def test_multiple_documents_do_not_attribute_aggregate_fact_to_each_page(self):
        data = wrapper()
        data["pages"].append({"document_id": "synthetic-document-2", "page_index": 4})
        sources = adapt(data).facts.facts["organismo"].sources
        self.assertEqual({item.document_id for item in sources},
                         {"synthetic-document", "synthetic-document-2"})
        self.assertTrue(all(item.page_index is None for item in sources))

    def test_pending_fields_reach_operator_projection_after_serialization(self):
        result = adapt(event={"missing_required_fields": ["fecha_notificacion"]})
        facts = ValidatedFacts.model_validate_json(result.facts.model_dump_json())
        projection = build_first_direction(
            case_id="synthetic-case", case_payload={"department": "traffic", "case_type": "fine"},
            readiness={"ready": True}, latest_facts={"facts": facts.model_dump(mode="json")},
            latest_family=None, latest_preview=None, next_step={}, registered_specialists=[],
        )
        self.assertIn("Hecho pendiente: Fecha de notificación", projection.missing_items)
        self.assertFalse(projection.generation_allowed)
        self.assertFalse(projection.authoritative)
        self.assertEqual(projection.deadlines, [])


if __name__ == "__main__":
    unittest.main()
