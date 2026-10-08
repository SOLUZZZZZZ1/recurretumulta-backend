from contextlib import contextmanager
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import hashlib
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

from rtm_core import working_document as document
from rtm_core import study_router
from rtm_core.contracts import SourceReference, ValidatedFact, ValidatedFacts
from rtm_core.facts_review import ReviewFactsBody, FactCorrection, corrected_snapshot

CASE = "11111111-1111-4111-8111-111111111111"
DOC = "22222222-2222-4222-8222-222222222222"
ACTOR = "33333333-3333-4333-8333-333333333333"
IDENTITY = {"full_name": "PERSONA FICTICIA", "dni_nie": "TEST001", "domicilio_notif": "DOMICILIO FICTICIO", "matricula": "TEST-001"}


def reading():
    values = {
        "organismo": "ORGANISMO DE PRUEBA", "expediente_ref": "REF-PRUEBA",
        "matricula": "TEST-001", "document_type": "otro", "procedural_stage_hint": "unknown",
        "document_title": "PETICIÓN DE DATOS AL TITULAR",
        "hecho_denunciado_literal": "CIRCULAR A 177 KM/H CON LÍMITE DE 120 KM/H",
        "velocidad_medida_kmh": 177, "velocidad_limite_kmh": 120,
        "fecha_infraccion": "2026-10-02", "lugar_infraccion": "LUGAR DE PRUEBA",
    }
    wrapper = {"pages": [{"document_id": DOC, "page_index": 1}],
        "extracted": {"extractor_version": "traffic_fine_reanalysis_v1_19", "source_document_ids": [DOC],
            "raw_text_blob": "PETICIÓN DE DATOS AL TITULAR PARA IDENTIFICAR AL CONDUCTOR. CINEMÓMETRO.",
            "traffic_generic_facts": values,
            "traffic_generic_facts_confidence": {key: .99 for key in values},
            "traffic_generic_facts_evidence": {key: str(value) for key, value in values.items()},
            "traffic_generic_facts_version": "documentary-test-v1"}}
    return wrapper, {"extractor_version": "traffic_fine_reanalysis_v1_19"}


def facts_record(facts):
    return SimpleNamespace(id="44444444-4444-4444-8444-444444444444", payload_sha256="f" * 64,
        facts=ValidatedFacts(case_id=CASE, service="traffic", extractor_version="test", facts=facts,
            source_document_ids=[DOC]))


def reviewed(value, source_type="operator_document_review"):
    return ValidatedFact(value=value, status="validated", sources=[SourceReference(
        document_id=DOC, page_index=0, source_type=source_type,
        extraction_method="ops_document_review_v1" if source_type == "operator_document_review" else "deterministic",
        evidence=str(value), confidence=1.0)], confidence=1.0)


def original_document(content, **changes):
    return {"id": DOC, "case_id": CASE, "kind": "original", "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content), "mime": "application/pdf", "b2_bucket": "test-bucket",
        "b2_key": f"cases/{CASE}/original/source.pdf", **changes}


def fixture_pdf():
    return (Path(__file__).parents[1] / "rtm_core/fixtures/staging_radar_v1/RADAR_IDENTIFICACION_PRUEBA_2026-10-05.pdf").read_bytes()


class WorkingDocumentProjectionTests(unittest.TestCase):
    def compose(self, *, wrapper=None, event=None, **kwargs):
        base, completion = reading()
        return document.compose_working_document(case_id=CASE, identity=kwargs.pop("identity", IDENTITY),
            wrapper=base if wrapper is None else wrapper, event=completion if event is None else event,
            **kwargs)

    def test_identification_is_visible_before_facts_without_inventing_driver_fine_or_deadline(self):
        result = self.compose()
        self.assertEqual(result["document_kind"], "driver_identification")
        self.assertIn("TEST-001", result["content"])
        self.assertIn("REF-PRUEBA", result["content"])
        self.assertIn("Persona que conducía: [PENDIENTE", result["content"])
        self.assertNotIn("Que se archive", result["content"])
        self.assertNotIn("margen no aplicado", result["content"])
        self.assertNotIn("sanción de", result["content"])
        self.assertIsNone(result["facts_id"])
        self.assertFalse(result["final_resource_generated"])
        self.assertFalse(result["persisted"])
        self.assertTrue(all(f["status"] in {"candidate", "declared"} for f in result["fields"]))

    def test_document_content_depends_on_case_values_not_fixture_identity(self):
        original = self.compose()
        wrapper, event = reading()
        wrapper["extracted"]["traffic_generic_facts"]["expediente_ref"] = "OTRA-REFERENCIA"
        changed = self.compose(wrapper=wrapper, event=event)
        self.assertIn("OTRA-REFERENCIA", changed["content"])
        self.assertNotIn("REF-PRUEBA", changed["content"])
        self.assertNotEqual(original["source_sha256"], changed["source_sha256"])

    def test_reviewed_values_win_without_modifying_input_or_claiming_new_review(self):
        record = facts_record({"expediente_ref": reviewed("REF-CORREGIDA")})
        before = record.facts.model_dump(mode="json")
        result = self.compose(facts_record=record)
        field = next(f for f in result["fields"] if f["key"] == "expediente_ref")
        self.assertEqual((field["value"], field["status"]), ("REF-CORREGIDA", "reviewed"))
        self.assertIn("REF-CORREGIDA", result["content"])
        self.assertEqual(record.facts.model_dump(mode="json"), before)
        self.assertEqual(result["facts_payload_sha256"], record.payload_sha256)
        self.assertIn("older_reading_differs_expediente_ref", {i["code"] for i in result["issues"]})

    def test_machine_verified_fact_is_not_labelled_human_reviewed(self):
        result = self.compose(facts_record=facts_record({"matricula": reviewed("TEST-001", "deterministic_document")}))
        self.assertEqual(next(f for f in result["fields"] if f["key"] == "matricula")["status"], "verified")

    def test_exclusion_remains_absent_despite_old_reading(self):
        wrapper, _ = reading()
        wrapper["extracted"]["apartado_infringido_num"] = "8"
        result = self.compose(wrapper=wrapper, excluded_fields={"apartado_infringido_num"})
        self.assertNotIn("apartado_infringido_num", {f["key"] for f in result["fields"]})

    def test_legacy_semaphore_is_a_conflict_not_a_statement_or_ready_proposal(self):
        wrapper, _ = reading()
        values = wrapper["extracted"]["traffic_generic_facts"]
        values["hecho_denunciado_literal"] = "REBASE DE SEMÁFORO EN ROJO"
        result = self.compose(wrapper=wrapper)
        self.assertNotIn("REBASE DE SEMÁFORO", result["content"])
        field = next(f for f in result["fields"] if f["key"] == "hecho_denunciado_literal")
        self.assertEqual(field["status"], "conflict")
        self.assertIn("conduct_inconsistent_with_speed", {i["code"] for i in result["issues"]})

    def test_declared_mismatch_is_not_interpolated_as_agreed_vehicle(self):
        result = self.compose(identity={**IDENTITY, "matricula": "OTRO-999"})
        field = next(f for f in result["fields"] if f["key"] == "matricula")
        self.assertEqual(field["status"], "conflict")
        self.assertEqual(result["document_kind"], "undetermined")
        self.assertNotIn("Se prepara contestación", result["content"])
        self.assertIn("CORRESPONDENCIA POR RESOLVER", result["content"])
        self.assertIn("TEST-001", result["content"])  # A comparison, never the vehicle of a proposed letter.
        self.assertIn("OTRO-999", result["content"])

    def test_exact_concordance_does_not_promote_or_confuse_different_names(self):
        result = self.compose(identity={**IDENTITY, "matricula": "test 001"})
        field = next(f for f in result["fields"] if f["key"] == "matricula")
        self.assertEqual(field["status"], "candidate")
        self.assertEqual(field["matches"][0]["rule"], "normalized_exact_v1")
        self.assertNotEqual(document._marker("document_subject_name", "Peña"),
                            document._marker("document_subject_name", "Pena"))
        for key in ("document_subject_name", "matricula"):
            self.assertNotEqual(document._marker(key, "A.B"), document._marker(key, "AB"))

    def test_identity_formatting_matches_without_conflating_letters_or_leading_zeros(self):
        for observed, declared, matches in (
            ("00.000.001-R", "00000001R", True),
            ("00000001R", "００．０００．００１－ｒ", True),
            ("00 000 001-r", "00000001R", True),
            ("O0.000.001-R", "00000001R", False),
            ("00.000.001-R", "0000001R", False),
        ):
            with self.subTest(observed=observed, declared=declared):
                wrapper, _ = reading()
                wrapper["extracted"].update(document_subject_name=IDENTITY["full_name"], document_subject_id=observed)
                before = deepcopy(wrapper)
                result = self.compose(wrapper=wrapper, identity={**IDENTITY, "dni_nie": declared})
                field = next(f for f in result["fields"] if f["key"] == "document_subject_id")
                self.assertEqual(field["value"], observed)
                self.assertEqual(field["status"], "candidate" if matches else "conflict")
                self.assertEqual(result["identity_concordance"]["status"], "concordant" if matches else "mismatch")
                self.assertEqual(result["document_kind"], "driver_identification" if matches else "undetermined")
                self.assertEqual(bool(field["matches"]), matches)
                self.assertFalse(result["final_resource_generated"])
                self.assertEqual(wrapper, before)
                self.assertEqual(document._marker("dni_nie", observed) == document._marker("dni_nie", declared), matches)

    def test_document_identity_aliases_detect_other_person_and_do_not_print_claimant(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                wrapper, _ = reading()
                if nested:
                    wrapper["extracted"]["document_subject"] = {"full_name": "OTRA PERSONA", "id_number": "OTHER999"}
                else:
                    wrapper["extracted"].update(document_subject_name="OTRA PERSONA", document_subject_id="OTHER999")
                result = self.compose(wrapper=wrapper)
                self.assertEqual(result["identity_concordance"]["status"], "mismatch")
                self.assertEqual(result["document_kind"], "undetermined")
                self.assertNotIn("Se prepara contestación", result["content"])
                self.assertIn("Interesado/a (dato aportado): [pendiente]", result["content"])
                fields = {field["key"]: field for field in result["fields"]}
                self.assertEqual(fields["document_subject_name"]["status"], "conflict")
                self.assertEqual(fields["full_name"]["status"], "conflict")

    def test_missing_documentary_identity_stays_incomplete_and_is_not_filled_from_form(self):
        result = self.compose()
        self.assertEqual(result["identity_concordance"]["status"], "incomplete")
        self.assertIn("document_identity_not_checked", {issue["code"] for issue in result["issues"]})
        self.assertNotIn("document_subject_name", {field["key"] for field in result["fields"]})
        wrapper, _ = reading()
        wrapper["extracted"]["traffic_generic_facts"]["document_subject"] = {
            "full_name": IDENTITY["full_name"], "id_number": IDENTITY["dni_nie"]}
        matched = self.compose(wrapper=wrapper)
        self.assertEqual(matched["identity_concordance"]["status"], "concordant")
        self.assertFalse(matched["final_resource_generated"])
        self.assertIn("Persona que conducía: [PENDIENTE", matched["content"])

    def test_a_documented_correction_can_resolve_identity_exception_without_resetting_other_facts(self):
        wrapper, _ = reading()
        wrapper["extracted"]["document_subject_id"] = "WRONG999"
        original = facts_record({"document_subject_id": reviewed("WRONG999"), "matricula": reviewed("TEST-001")})
        blocked = self.compose(wrapper=wrapper, facts_record=original)
        self.assertEqual(blocked["identity_concordance"]["status"], "mismatch")
        body = ReviewFactsBody(expected_payload_sha256=original.payload_sha256, reason="Identificador contrastado con el original ficticio",
            changes=[FactCorrection(field="document_subject_id", value="TEST001", document_id=DOC,
                                    page_index=0, evidence="Identificador de la persona: TEST001")])
        updated = corrected_snapshot(original, body)
        after = self.compose(wrapper=wrapper, facts_record=facts_record(updated.facts))
        self.assertNotEqual(after["identity_concordance"]["status"], "mismatch")
        self.assertEqual(after["document_kind"], "driver_identification")
        self.assertEqual(original.facts.facts["document_subject_id"].value, "WRONG999")
        self.assertEqual(updated.facts["matricula"].model_dump(), original.facts.facts["matricula"].model_dump())
        self.assertFalse(after["final_resource_generated"])

    def test_non_traffic_case_is_rejected_before_reading_or_proposing_a_traffic_document(self):
        with patch.object(document.authority, "_case_authority_meta", return_value={"department": "banking", "case_type": "claim"}), \
             patch.object(document.authority, "_require_authority_work_allowed"), \
             patch.object(document, "verify_signed_case_authority") as authorization, \
             patch.object(document.adapter, "load_latest_reanalysis_snapshot") as saved:
            with self.assertRaises(HTTPException) as caught:
                document.load_working_document(Mock(), CASE)
        self.assertEqual(caught.exception.status_code, 409)
        authorization.assert_not_called()
        saved.assert_not_called()

    def test_unknown_labels_and_document_title_use_existing_documentary_context(self):
        result = self.compose()
        self.assertEqual(result["document_kind"], "driver_identification")
        wrapper, _ = reading()
        wrapper["extracted"]["traffic_generic_facts"]["document_type"] = "resolucion"
        wrapper["extracted"]["traffic_generic_facts"]["procedural_stage_hint"] = "final_resolution"
        result = self.compose(wrapper=wrapper)
        self.assertEqual(result["document_kind"], "undetermined")
        self.assertNotIn("Se prepara contestación", result["content"])
        self.assertIn("redactor temprano compatible", result["content"])

    def test_payment_or_resolution_enums_cannot_be_overridden_by_historical_identification(self):
        for phase in ("payment_requirement", "requerimiento_pago", "resolution", "final_resolution"):
            with self.subTest(phase=phase):
                wrapper, _ = reading()
                wrapper["extracted"]["traffic_generic_facts"]["document_type"] = phase
                wrapper["extracted"]["traffic_generic_facts"]["procedural_stage_hint"] = "unknown"
                result = self.compose(wrapper=wrapper)
                self.assertEqual(result["document_kind"], "undetermined")

    def test_none_sentinel_is_absence_and_does_not_generate_a_second_reading(self):
        wrapper, _ = reading()
        wrapper["extracted"]["matricula"] = "None"
        result = self.compose(wrapper=wrapper)
        field = next(f for f in result["fields"] if f["key"] == "matricula")
        self.assertEqual(field["status"], "candidate")
        self.assertNotIn("conflicting_matricula", {i["code"] for i in result["issues"]})

    def test_explicit_issuer_rejection_stays_unknown_without_trusting_old_issuer(self):
        wrapper, event = reading()
        event["traffic_generic_facts"] = {"legacy_issuer_rejected": "ORGANISMO DE PRUEBA", "issuer_status": "unresolved"}
        event["critical_conflicts_resolved"] = [{"field": "organismo", "chosen": None,
            "current_value": "ORGANISMO DE PRUEBA", "reason": "legacy_issuer_not_confirmed_in_current_run",
            "source": "current_run_no_explicit_issuer", "resolved": True}]
        result = self.compose(wrapper=wrapper, event=event)
        field = next(f for f in result["fields"] if f["key"] == "organismo")
        self.assertEqual((field["value"], field["status"]), (None, "missing"))
        self.assertNotIn("ORGANISMO DE PRUEBA", result["content"])
        self.assertIn("issuer_not_confirmed_in_current_reading", {i["code"] for i in result["issues"]})

    def test_declared_plate_without_documentary_reading_is_reused_without_matching_itself(self):
        wrapper, _ = reading()
        del wrapper["extracted"]["traffic_generic_facts"]["matricula"]
        result = self.compose(wrapper=wrapper)
        field = next(f for f in result["fields"] if f["key"] == "matricula")
        self.assertEqual(field["status"], "declared")
        self.assertEqual(field["matches"], [])
        self.assertIn("TEST-001", result["content"])

    def test_json_candidate_envelope_is_not_a_quote_and_multiple_pages_are_not_guessed(self):
        wrapper, _ = reading()
        wrapper["pages"].append({"document_id": DOC, "page_index": 2})
        wrapper["extracted"]["traffic_generic_facts_evidence"]["matricula"] = '{"candidate_only":true}'
        result = self.compose(wrapper=wrapper)
        source = next(f for f in result["fields"] if f["key"] == "matricula")["sources"][0]
        self.assertIsNone(source["page_index"])
        self.assertIsNone(source["evidence"])
        self.assertIsNone(source["evidence_kind"])

    def test_historical_v18_without_raw_anchors_from_original_bytes_without_false_facts(self):
        content = fixture_pdf()
        with patch.object(document.storage, "get_b2_bucket", return_value="test-bucket"), \
             patch.object(document.storage, "download_bytes_limited", return_value=content) as downloaded:
            originals, issues = document._load_original_pdf_texts(CASE, [original_document(content)], [DOC])
        self.assertFalse(issues)
        downloaded.assert_called_once_with("test-bucket", f"cases/{CASE}/original/source.pdf",
            max_bytes=document._MAX_ORIGINAL_PDF_BYTES, case_id=CASE, request_timeout_seconds=5)
        wrapper = {"pages": [{"document_id": DOC, "page_index": 1}], "extracted": {
            "extractor_version": "traffic_fine_reanalysis_v1_18", "source_document_ids": [DOC],
            "document_title": "Petición de datos al titular para identificación del conductor",
            "familia_resuelta": "semaforo", "tipo_infraccion": "semaforo",
            "hecho_imputado": "No respetar la luz roja no intermitente de un semáforo",
            "velocidad_medida_kmh": 177, "velocidad_limite_kmh": 120,
            "expediente_ref": "RTM-RADAR-TEST-001", "matricula": None,
            "fecha_documento": "2026-10-05", "radar_modelo_hint": "multanova", "articulo_infringido_num": "48",
            "apartado_infringido_num": "8"}}
        record = facts_record({key: ValidatedFact(value=None, status="unresolved",
            notes=["Lectura candidata no consolidada: esto nunca es fuente de datos"])
            for key in ("organismo", "matricula", "expediente_ref", "hecho_denunciado_literal",
                        "velocidad_medida_kmh", "velocidad_limite_kmh", "fecha_infraccion",
                        "hora_infraccion", "lugar_infraccion", "articulo_infringido_num", "radar_modelo_hint")})
        original_facts = record.facts.model_dump(mode="json")
        result = self.compose(wrapper=wrapper, event={}, facts_record=record,
            identity={**IDENTITY, "matricula": None}, excluded_fields={"apartado_infringido_num"}, original_texts=originals)
        self.assertEqual(result["document_kind"], "driver_identification")
        self.assertIn("RTM-RADAR-TEST-001", result["content"])
        self.assertNotIn("semáforo", result["content"])
        self.assertEqual(record.facts.model_dump(mode="json"), original_facts)
        fields = {f["key"]: f for f in result["fields"]}
        self.assertNotIn("apartado_infringido_num", fields)
        self.assertEqual(fields["matricula"]["status"], "missing")
        self.assertEqual(fields["hecho_denunciado_literal"]["status"], "conflict")
        reference = fields["expediente_ref"]
        self.assertEqual(reference["status"], "candidate")
        self.assertEqual(reference["sources"][0]["page_index"], 0)
        self.assertIn("RTM-RADAR-TEST-001", reference["sources"][0]["evidence"])
        self.assertEqual(reference["sources"][0]["extraction_method"], "rtm_original_pdf_literal_anchor_v1")
        self.assertEqual(reference["sources"][0]["document_sha256"], hashlib.sha256(content).hexdigest())
        anchored = [field["key"] for field in result["fields"] if field["status"] == "candidate"
            and any(source.get("evidence_kind") == "document_excerpt" for source in field["sources"])]
        self.assertEqual(set(anchored), {"expediente_ref", "fecha_documento", "radar_modelo_hint",
            "velocidad_limite_kmh", "velocidad_medida_kmh", "articulo_infringido_num", "titulo_documento"})
        date_source = fields["fecha_documento"]["sources"][0]
        self.assertIn("05/10/2026", date_source["evidence"])
        self.assertEqual(fields["fecha_documento"]["value"], "2026-10-05")
        pdf_text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(
            document.working_document_pdf(result, result["source_sha256"]))).pages)
        self.assertIn("RTM-RADAR-TEST-001", pdf_text)
        self.assertNotIn("semáforo", pdf_text)

    def test_original_text_anchor_refuses_multiple_occurrences_and_numeric_substrings(self):
        originals = [{"document_id": DOC, "sha256": "a" * 64, "text": "REF-PRUEBA\nREF-PRUEBA\nArtículo 48"}]
        self.assertIsNone(document._anchor_original_text(originals, "expediente_ref", "REF-PRUEBA"))
        self.assertIsNone(document._anchor_original_text(originals, "apartado_infringido_num", "8"))
        originals[0]["text"] = "Fecha del escrito: 05/10/2026\nFecha del escrito: 05/10/2026"
        self.assertIsNone(document._anchor_original_text(originals, "fecha_documento", "2026-10-05"))

    def test_model_evidence_and_flattened_raw_never_become_document_excerpts(self):
        wrapper, _ = reading()
        wrapper["extracted"]["raw_text_blob"] = "===== PÁGINA 1 =====\nexpediente_ref: REF-PRUEBA"
        result = self.compose(wrapper=wrapper)
        self.assertTrue(all(source["evidence"] is None and source["evidence_kind"] is None
            for field in result["fields"] for source in field["sources"]))
        human = self.compose(facts_record=facts_record({"expediente_ref": reviewed("REF-REVISADA")}))
        source = next(f for f in human["fields"] if f["key"] == "expediente_ref")["sources"][0]
        self.assertEqual(source["evidence_kind"], "document_excerpt")
        self.assertEqual(source["evidence"], "REF-REVISADA")
        originals = [{"document_id": DOC, "sha256": "a" * 64, "text": "Referencia: REF-PRUEBA"}]
        anchored = self.compose(wrapper=wrapper, original_texts=originals)
        self.assertNotEqual(anchored["source_sha256"], result["source_sha256"])

    def test_date_anchor_requires_its_own_documentary_role(self):
        original = {"document_id": DOC, "sha256": "a" * 64, "text": "Fecha del hecho: 05/10/2026"}
        self.assertIsNone(document._anchor_original_text([original], "fecha_documento", "2026-10-05"))
        self.assertIsNone(document._anchor_original_text([original], "fecha_notificacion", "2026-10-05"))
        actual_fact_date = document._anchor_original_text([original], "fecha_infraccion", "2026-10-05")
        self.assertEqual(actual_fact_date["evidence"], "Fecha del hecho: 05/10/2026")
        original["text"] = "05/10/2026"
        self.assertIsNone(document._anchor_original_text([original], "fecha_documento", "2026-10-05"))

    def test_pdf_is_marked_draft_matches_text_and_refuses_stale_source(self):
        result = self.compose()
        with self.assertRaises(HTTPException) as caught:
            document.working_document_pdf(result, "0" * 64)
        self.assertEqual(caught.exception.status_code, 409)
        content = document.working_document_pdf(result, result["source_sha256"])
        text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(content)).pages)
        self.assertIn("BORRADOR", text)
        self.assertIn("REF-PRUEBA", text)
        self.assertIn("PENDIENTE DE APORTAR Y CONFIRMAR", text)

    def test_lineage_exclusions_honor_reincorporation_and_use_snapshot_sequence(self):
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = [({"excluded_fields": ["matricula", "apartado_infringido_num"]},),
            ({"added_fields": ["matricula"]},)]
        excluded = document._excluded_fields(conn, CASE, "facts-id")
        self.assertEqual(excluded, {"apartado_infringido_num"})
        sql = str(conn.execute.call_args.args[0])
        self.assertIn("ORDER BY l.sequence", sql)
        self.assertIn("JOIN lineage", sql)

    def test_loader_has_no_writes_and_reuses_saved_reading(self):
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = [SimpleNamespace(_mapping=original_document(fixture_pdf()))]
        with patch.object(document.authority, "_case_authority_meta", return_value={"department": "traffic", "case_type": "fine"}), \
             patch.object(document.authority, "_require_authority_work_allowed"), \
             patch.object(document, "verify_signed_case_authority"), \
             patch.object(document, "load_case_review_snapshot", return_value=SimpleNamespace(interested_data=IDENTITY)), \
             patch.object(document.authority, "latest_validated_facts", return_value=None), \
             patch.object(document, "_load_original_pdf_texts", return_value=([], [])), \
             patch.object(document.adapter, "load_latest_reanalysis_snapshot", return_value=reading()) as saved:
            result = document.load_working_document(conn, CASE)
        saved.assert_called_once_with(conn, CASE)
        self.assertFalse(result["persisted"])
        self.assertTrue(all(str(call.args[0]).lstrip().upper().startswith("SELECT") for call in conn.execute.call_args_list))

    def test_loader_does_not_anchor_a_snapshot_outside_current_facts_sources(self):
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = [SimpleNamespace(_mapping=original_document(fixture_pdf()))]
        current = facts_record({})
        current.facts.source_document_ids = [ACTOR]
        with patch.object(document.authority, "_case_authority_meta", return_value={"department": "traffic", "case_type": "fine"}), \
             patch.object(document.authority, "_require_authority_work_allowed"), \
             patch.object(document, "verify_signed_case_authority"), \
             patch.object(document, "load_case_review_snapshot", return_value=SimpleNamespace(interested_data=IDENTITY)), \
             patch.object(document.authority, "latest_validated_facts", return_value=current), \
             patch.object(document.adapter, "load_latest_reanalysis_snapshot", return_value=reading()), \
             patch.object(document, "_excluded_fields", return_value=set()), \
             patch.object(document, "_load_original_pdf_texts") as originals:
            result = document.load_working_document(conn, CASE)
        originals.assert_not_called()
        self.assertIn("original_literal_text_unavailable", {issue["code"] for issue in result["issues"]})


class OriginalPdfLiteralTests(unittest.TestCase):
    def load(self, content, *, row=None, source_ids=None):
        with patch.object(document.storage, "get_b2_bucket", return_value="test-bucket"), \
             patch.object(document.storage, "download_bytes_limited", return_value=content):
            return document._load_original_pdf_texts(CASE, [row or original_document(content)], source_ids or [DOC])

    def test_native_literal_parser_does_not_apply_ocr_corrections_or_remove_admin_lines(self):
        content = document.build_pdf("PRUEBA", "CLASIFICACION: DATO VISIBLE\nS. NO\nTrombo")
        expected = PdfReader(BytesIO(content)).pages[0].extract_text()
        result = document.run_parser_isolated("extract_single_page_pdf_literal", {"data": content})
        self.assertEqual(result, {"page_count": 1, "text": expected})
        self.assertIn("CLASIFICACION", result["text"])
        self.assertIn("S. NO", result["text"])
        self.assertIn("Trombo", result["text"])

    def test_hash_and_size_mismatches_stop_before_parser(self):
        content = fixture_pdf()
        for changes in ({"sha256": "0" * 64}, {"size_bytes": len(content) - 1}, {"size_bytes": len(content) + 1}):
            with self.subTest(changes=changes), patch.object(document, "run_parser_isolated") as parser:
                with self.assertRaises(HTTPException) as caught:
                    self.load(content, row=original_document(content, **changes))
                self.assertEqual(caught.exception.status_code, 409)
                parser.assert_not_called()

    def test_foreign_case_id_kind_namespace_or_bucket_never_downloads(self):
        content = fixture_pdf()
        for changes in ({"case_id": ACTOR}, {"id": ACTOR}, {"kind": "authorization_signed"},
                {"b2_key": f"cases/{ACTOR}/original/source.pdf"},
                {"b2_key": f"cases/{CASE}/original/../source.pdf"}, {"b2_bucket": "foreign-bucket"}):
            with self.subTest(changes=changes), patch.object(document.storage, "get_b2_bucket", return_value="test-bucket"), \
                 patch.object(document.storage, "download_bytes_limited") as download:
                with self.assertRaises(HTTPException) as caught:
                    document._load_original_pdf_texts(CASE, [original_document(content, **changes)], [DOC])
                self.assertEqual(caught.exception.status_code, 409)
                download.assert_not_called()

    def test_oversized_non_pdf_or_multiple_originals_remain_pending_without_download(self):
        content = fixture_pdf()
        for row, ids in ((original_document(content, size_bytes=document._MAX_ORIGINAL_PDF_BYTES + 1), [DOC]),
                         (original_document(content, mime="image/jpeg"), [DOC]),
                         (original_document(content), [DOC, ACTOR])):
            with self.subTest(ids=ids, mime=row["mime"]), patch.object(document.storage, "download_bytes_limited") as download:
                originals, issues = document._load_original_pdf_texts(CASE, [row], ids)
                self.assertEqual(originals, [])
                self.assertEqual(issues[0]["code"], "original_literal_text_unavailable")
                download.assert_not_called()

    def test_multipage_and_blank_originals_do_not_guess_a_page_or_run_fallback(self):
        multi = PdfWriter()
        reader = PdfReader(BytesIO(fixture_pdf()))
        multi.add_page(reader.pages[0])
        multi.add_page(reader.pages[0])
        multi_bytes = BytesIO()
        multi.write(multi_bytes)
        blank = PdfWriter()
        blank.add_blank_page(width=100, height=100)
        blank_bytes = BytesIO()
        blank.write(blank_bytes)
        for content in (multi_bytes.getvalue(), blank_bytes.getvalue()):
            with self.subTest(size=len(content)):
                originals, issues = self.load(content)
                self.assertEqual(originals, [])
                self.assertEqual(issues[0]["code"], "original_literal_text_unavailable")

    def test_invalid_pdf_is_rejected_by_actual_isolated_parser(self):
        with self.assertRaises(HTTPException) as caught:
            self.load(b"This is not a PDF")
        self.assertEqual(caught.exception.status_code, 409)

    def test_parser_failure_never_falls_back_to_model_text(self):
        from rtm_core.parser_isolation import ParserIsolationTimeout
        for error, status in ((ParserIsolationTimeout("timeout"), 503),
                              (document.ParserIsolationError("crash"), 503),
                              (document.ParserRejected("rejected", 422), 409)):
            with self.subTest(error=type(error).__name__), patch.object(document, "run_parser_isolated", side_effect=error):
                with self.assertRaises(HTTPException) as caught:
                    self.load(fixture_pdf())
                self.assertEqual(caught.exception.status_code, status)

    def test_literal_size_limit_refuses_truncation_that_could_hide_ambiguity(self):
        from rtm_core import parser_isolation
        from rtm_core.upload_security import UploadSecurityError
        reader = Mock()
        reader.pages = [Mock()]
        reader.pages[0].extract_text.return_value = "x" * (document._MAX_ORIGINAL_TEXT_CHARS + 1)
        with patch("rtm_core.upload_security.validate_pdf_document"), patch("pypdf.PdfReader", return_value=reader):
            with self.assertRaises(UploadSecurityError):
                parser_isolation._execute_operation("extract_single_page_pdf_literal", {"data": b"mocked"}, limits={}, test_hooks=False)


class WorkingDocumentRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(study_router.router)
        self.client = TestClient(self.app)
        self.path = f"/ops/core/cases/{CASE}/study/working-document"

    def test_non_supervisor_cannot_read_case_or_pdf(self):
        with patch.object(study_router, "require_operator_token"), \
             patch.object(study_router, "load_ops_case_scope", return_value=SimpleNamespace(individual_session=False)), \
             patch.object(study_router, "get_engine") as engine:
            self.assertEqual(self.client.get(self.path).status_code, 403)
            self.assertEqual(self.client.get(self.path + "/pdf?source_sha256=" + "0" * 64).status_code, 403)
        engine.assert_not_called()

    def test_scoped_pdf_supplies_hashes_and_never_writes_or_saves_document(self):
        wrapper, event = reading()
        projection = document.compose_working_document(case_id=CASE, identity=IDENTITY, wrapper=wrapper, event=event)
        conn = Mock()
        engine = Mock()
        engine.begin.return_value.__enter__ = Mock(return_value=conn)
        engine.begin.return_value.__exit__ = Mock(return_value=False)
        scope = SimpleNamespace(operator_id=ACTOR)
        with patch.object(study_router, "_supervisor_scope", return_value=scope), \
             patch.object(study_router, "get_engine", return_value=engine), \
             patch.object(study_router, "require_case_in_scope", return_value=CASE) as check, \
             patch.object(study_router, "load_working_document", return_value=projection):
            response = self.client.get(self.path + "/pdf?source_sha256=" + projection["source_sha256"])
            self.assertEqual(response.status_code, 200)
            check.assert_called_once_with(conn, scope=scope, case_id=CASE)
        self.assertEqual(response.headers["x-rtm-source-sha256"], projection["source_sha256"])
        self.assertEqual(response.headers["x-rtm-document-sha256"], hashlib.sha256(response.content).hexdigest())
        self.assertIn("application/pdf", response.headers["content-type"])
        self.assertIn("no-store", response.headers["cache-control"])
        conn.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
