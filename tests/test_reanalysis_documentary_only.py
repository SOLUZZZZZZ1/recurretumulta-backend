"""Regresión: el PDF de radar no debe recibir hechos de una familia inferida."""
import copy
import io
import unittest
from unittest.mock import patch

from pypdf import PdfReader
import analyze
import reanalysis
from rtm_core.extraction_policy import select_deep_extraction_route
from rtm_core.reanalysis_adapter import build_validated_facts_from_reanalysis
from rtm_core.staging_rehearsal import fixture

LITERAL = "CIRCULAR A 177 KM/H, TENIENDO LIMITADA LA VELOCIDAD A 120 KM/H. EXISTE UNA LIMITACIÓN GENÉRICA EN VÍA INTERURBANA."

class ReanalysisDocumentaryOnlyTest(unittest.TestCase):
    def setUp(self):
        self.content = fixture("radar")[1]
        self.original_text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(self.content)).pages)
        self.reading = {
            "hecho_denunciado_literal": LITERAL,
            "matricula": "RTM-TEST-002",
            "expediente_ref": "RTM-RADAR-TEST-001",
            "articulo_infringido_num": "48",
            "vision_raw_text": self.original_text,
        }
        self.enterContext(patch.object(analyze, "extract_from_text", side_effect=lambda _text: copy.deepcopy(self.reading)))
        self.enterContext(patch.object(analyze, "extract_from_image_bytes", side_effect=lambda *_args: copy.deepcopy(self.reading)))
        self.enterContext(patch.object(analyze, "_should_run_focused_fet_ocr", return_value=False))

    def page(self):
        return reanalysis._analyze_page_candidate(self.content, "radar.pdf", "application/pdf")[0]

    def test_real_fixture_keeps_documentary_literal_without_invented_semaphore_or_subsection(self):
        core = self.page()["extracted"]
        self.assertEqual(core["hecho_denunciado_literal"], LITERAL)
        self.assertNotIn("hecho_imputado", core)
        self.assertNotIn("apartado_infringido_num", core)
        self.assertNotIn("tipo_infraccion", core)
        self.assertEqual(core["evidence_status"], "candidate_only")
        self.assertTrue(core["needs_operator_review"])

    def test_reanalysis_page_never_runs_legacy_triage(self):
        with patch.object(analyze, "_enrich_with_triage", side_effect=AssertionError("Legacy triage is not documentary evidence")):
            self.assertEqual(self.page()["extracted"]["hecho_denunciado_literal"], LITERAL)

    def test_complete_consolidation_and_adapter_do_not_reintroduce_legacy_facts(self):
        page = self.page()
        analyzed = [{
            "page_index": 1, "document_id": "76f408ea-1224-4a4d-b88a-dc3dc520eb10",
            "wrapper": page, "confidence": 0.8, "size_bytes": len(self.content),
            "sha256": page["sha256"], "mime_detected": "application/pdf",
            "analysis_mime": "application/pdf",
        }]
        before = copy.deepcopy(analyzed)
        with patch.object(reanalysis, "_resolved_traffic_family", side_effect=select_deep_extraction_route), \
             patch.object(reanalysis, "_critical_fields_from_images", return_value={}), \
             patch.object(reanalysis, "_critical_fields_from_zoomed_crops", return_value={}), \
             patch.object(reanalysis, "_velocity_secondary_facts_from_images", return_value={}), \
             patch.object(reanalysis, "_traffic_generic_document_facts_from_images", return_value={}), \
             patch.object(reanalysis, "_traffic_handwritten_precision_from_images", return_value={"skipped": True}):
            wrapper, meta, _ = reanalysis._consolidate_extraction("synthetic-case", analyzed)
        self.assertEqual(analyzed, before)
        self.assertEqual(meta["specialist_dispatch"], "velocidad")
        self.assertNotIn("apartado_infringido_num", wrapper["extracted"])
        self.assertNotIn("tipo_infraccion", wrapper["extracted"])
        adapted = build_validated_facts_from_reanalysis(case_id="synthetic-case", wrapper=wrapper)
        fact = adapted.facts.facts["hecho_denunciado_literal"]
        self.assertEqual(fact.status.value, "unresolved")
        self.assertIsNone(fact.value)
        self.assertTrue(any("177 KM/H" in note for note in fact.notes))
        self.assertFalse(any("semáforo" in note.lower() for note in fact.notes))
        self.assertNotIn("apartado_infringido_num", adapted.facts.facts)

    def test_default_legacy_caller_keeps_existing_contract(self):
        with patch.object(analyze, "_enrich_with_triage", side_effect=lambda core, blob: {**core, "legacy_marker": True}) as legacy:
            core, _, _ = analyze._extract_untrusted_document_bounded(self.content, "application/pdf", "radar.pdf")
        self.assertTrue(core["legacy_marker"])
        self.assertEqual(legacy.call_count, 3)

if __name__ == "__main__":
    unittest.main()
