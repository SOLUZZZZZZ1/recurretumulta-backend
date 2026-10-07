from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from rtm_core.contracts import ValidatedFacts, SourceReference
from rtm_core.facts_review import FactCorrection, ReviewFactsBody, corrected_snapshot, review_facts
from rtm_core import authority_router

CASE, DOC = str(uuid4()), str(uuid4())


def body(**changes):
    correction = dict(field="fecha_notificacion", value="2026-09-20", document_id=DOC,
                      page_index=1, evidence="Notificada el 20/09/2026")
    correction.update(changes)
    return ReviewFactsBody(expected_payload_sha256="a" * 64, reason="Contrastado con original",
                           changes=[FactCorrection(**correction)])


def previous():
    facts = ValidatedFacts(case_id=CASE, service="traffic.fine", extractor_version="test", source_document_ids=[DOC],
        facts={
            "fecha_notificacion": {"value": None, "status": "unresolved", "notes": ["Falta notificación"]},
            "matricula": {"value": None, "status": "conflicted", "conflicts": ["Dos matrículas"]},
        }, unresolved=["fecha_notificacion", "matricula"], conflicts=["Dos matrículas", "Conflicto global"])
    return SimpleNamespace(id=str(uuid4()), facts=facts, frozen=False, payload_sha256="a" * 64)


class FactsReviewTests(unittest.TestCase):
    def test_addition_requires_explicit_operation_and_preserves_previous_snapshot(self):
        old = previous(); before = old.facts.model_dump()
        updated = corrected_snapshot(old, body(field="lugar_infraccion", value="Calle ficticia 1", operation="add"))
        self.assertEqual(old.facts.model_dump(), before)
        self.assertEqual(updated.conflicts, old.facts.conflicts)
        self.assertEqual(updated.unresolved, old.facts.unresolved)
        self.assertFalse(updated.frozen)
        added = updated.facts["lugar_infraccion"]
        self.assertEqual(added.value, "Calle ficticia 1")
        self.assertEqual(added.status.value, "validated")
        self.assertEqual(added.sources[0].document_id, DOC)
        self.assertEqual(added.sources[0].page_index, 1)
        self.assertEqual(added.sources[0].source_type, "operator_document_review")
        self.assertIn("Incorporación", added.notes[0])
        self.assertEqual(updated.facts["matricula"].model_dump(), old.facts.facts["matricula"].model_dump())

    def test_addition_never_overwrites_even_an_unresolved_existing_field(self):
        old = previous(); before = old.facts.model_dump()
        with self.assertRaises(HTTPException): corrected_snapshot(old, body(operation="add"))
        self.assertEqual(old.facts.model_dump(), before)
        for change in (dict(operation="replace"), dict(operation=None), dict(operation="add", field="approved", value=True)):
            with self.assertRaises(ValidationError): body(**change)

    def test_boolean_facts_require_actual_boolean_and_preserve_false(self):
        for field in ("pago_multa_reducido", "fotografia_vehiculo_presente"):
            for value in (None, "false", "true", 0, 1, "no", [], {}):
                with self.subTest(field=field,value=value), self.assertRaises(ValidationError): body(field=field,value=value,operation="add")
            for value in (False, True):
                updated=corrected_snapshot(previous(), body(field=field,value=value,operation="add"))
                self.assertIs(updated.facts[field].value, value)

    def test_addition_keeps_foreign_and_unlisted_sources_closed(self):
        with self.assertRaises(HTTPException):
            corrected_snapshot(previous(), body(field="tipo_documento",value="Denuncia",operation="add",document_id=str(uuid4())))

    def test_correction_preserves_other_pending_fields_and_original(self):
        old = previous()
        before = old.facts.model_dump()
        updated = corrected_snapshot(old, body())
        self.assertEqual(old.facts.model_dump(), before)
        self.assertFalse(updated.frozen)
        self.assertEqual(updated.unresolved, ["matricula"])
        self.assertEqual(updated.conflicts, before["conflicts"])
        source = updated.facts["fecha_notificacion"].sources[0]
        self.assertEqual((source.document_id, source.page_index, source.source_type), (DOC, 1, "operator_document_review"))
        self.assertEqual(updated.facts["fecha_notificacion"].value, "2026-09-20")

    def test_model_page_is_replaced_by_actual_human_source(self):
        old = previous()
        old.facts.facts["fecha_notificacion"].sources = [SourceReference(
            document_id=DOC, page_index=None, source_type="model_inference",
            extraction_method="ai_extraction", confidence=0.3)]
        updated = corrected_snapshot(old, body(page_index=0))
        self.assertEqual(updated.facts["fecha_notificacion"].sources[0].page_index, 0)
        self.assertIsNone(old.facts.facts["fecha_notificacion"].sources[0].page_index)
        self.assertEqual(updated.facts["fecha_notificacion"].sources[0].source_type, "operator_document_review")

    def test_strict_values_reject_dates_booleans_nan_and_unapproved_fields(self):
        invalid = [dict(value="2026-02-30"), dict(value="2026-9-20"), dict(field="puntos_detraccion", value=True),
                   dict(field="puntos_detraccion", value=1.5), dict(field="sancion_importe_eur", value=float("nan")),
                   dict(field="sancion_importe_eur", value="12"), dict(field="familia_resuelta", value="velocidad"),
                   dict(document_id="../file"), dict(page_index=True), dict(evidence=" ")]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValidationError): body(**change)
        self.assertEqual(body(field="puntos_detraccion", value=0).changes[0].value, 0)

    def test_rejects_duplicate_and_client_actor(self):
        data = body().model_dump()
        data["changes"] *= 2
        with self.assertRaises(ValidationError): ReviewFactsBody.model_validate(data)
        data = body().model_dump(); data["actor"] = "forged"
        with self.assertRaises(ValidationError): ReviewFactsBody.model_validate(data)

    def test_unknown_field_or_foreign_source_does_not_change_snapshot(self):
        for change in (dict(field="organismo", value="DGT"), dict(document_id=str(uuid4()))):
            old = previous(); before = old.facts.model_dump()
            with self.assertRaises(HTTPException) as exc: corrected_snapshot(old, body(**change))
            self.assertEqual(exc.exception.status_code, 409)
            self.assertEqual(old.facts.model_dump(), before)

    def test_stale_and_closed_versions_fail_before_mutation(self):
        for frozen, mismatch in ((False, True), (True, False)):
            old = previous(); old.frozen = frozen
            with patch("rtm_core.facts_review.repository._case_authority_meta", return_value={"department": "traffic", "case_type": "fine"}), \
                 patch("rtm_core.facts_review.repository._require_authority_work_allowed"), \
                 patch("rtm_core.facts_review.verify_signed_case_authority"), \
                 patch("rtm_core.facts_review.repository.latest_validated_facts", return_value=old), \
                 patch("rtm_core.facts_review.repository.invalidate_validated_facts") as invalidate:
                with self.assertRaises(HTTPException) as exc:
                    review_facts(Mock(), case_id=CASE, facts_id=str(uuid4()) if mismatch else old.id, body=body(), actor="operator:test")
                self.assertEqual(exc.exception.status_code, 409)
                invalidate.assert_not_called()

    def test_route_rejects_legacy_and_read_only_operator_before_database(self):
        for individual, scope_all in ((False, True), (True, False)):
            scope = SimpleNamespace(individual_session=individual, scope_all=scope_all)
            with patch.object(authority_router, "require_operator_token"), patch.object(authority_router, "load_ops_case_scope", return_value=scope), patch.object(authority_router, "get_engine") as engine:
                with self.assertRaises(HTTPException) as exc:
                    authority_router.review_case_facts(CASE, "version", body(), Mock(), "token")
                self.assertEqual(exc.exception.status_code, 403)
                engine.assert_not_called()

    def test_route_uses_verified_operator_and_one_transaction(self):
        scope = SimpleNamespace(individual_session=True, scope_all=True, operator_id=str(uuid4()))
        conn = object(); engine = Mock(); engine.begin.return_value = nullcontext(conn)
        record = Mock(); record.model_dump.return_value = {"ok": "record"}
        with patch.object(authority_router, "require_operator_token"), patch.object(authority_router, "load_ops_case_scope", return_value=scope), \
             patch.object(authority_router, "get_engine", return_value=engine), patch.object(authority_router, "require_case_in_scope", return_value=CASE), \
             patch.object(authority_router, "review_facts", return_value=record) as review:
            result = authority_router.review_case_facts(CASE, "version", body(), Mock(), "token")
        self.assertEqual(review.call_args.kwargs["actor"], f"operator:{scope.operator_id}")
        self.assertIs(review.call_args.args[0], conn)
        self.assertEqual(result["case_id"], CASE)



class PendingReadingExclusionTests(unittest.TestCase):
    def test_radar_model_is_reviewable_with_documentary_provenance(self):
        old = previous()
        updated = corrected_snapshot(old, body(field="radar_modelo_hint", operation="add", value="CINEMÓMETRO MULTANOVA"))
        fact = updated.facts["radar_modelo_hint"]
        self.assertEqual(fact.value, "CINEMÓMETRO MULTANOVA")
        self.assertEqual(fact.sources[0].source_type, "operator_document_review")
        self.assertEqual(fact.sources[0].document_id, DOC)

    def test_exclusion_preserves_the_proposal_in_history_without_inventing_a_value(self):
        old = previous()
        before = old.facts.model_dump()
        updated = corrected_snapshot(old, body(operation="exclude", value=None))
        self.assertEqual(old.facts.model_dump(), before)
        self.assertNotIn("fecha_notificacion", updated.facts)
        self.assertNotIn("fecha_notificacion", updated.unresolved)
        self.assertEqual(updated.facts["matricula"].model_dump(), old.facts.facts["matricula"].model_dump())
        self.assertEqual(updated.conflicts, old.facts.conflicts)
        self.assertFalse(updated.frozen)

    def test_exclusion_removes_only_the_conflicts_of_the_discarded_reading(self):
        old = previous()
        updated = corrected_snapshot(old, body(field="matricula", operation="exclude", value=None))
        self.assertNotIn("matricula", updated.facts)
        self.assertEqual(updated.conflicts, ["Conflicto global"])
        self.assertEqual(updated.unresolved, ["fecha_notificacion"])

    def test_exclusion_cannot_delete_confirmed_facts_or_unlisted_sources(self):
        old = previous()
        old.facts = corrected_snapshot(old, body())
        for params in (dict(), dict(field="organismo"), dict(document_id=str(uuid4()))):
            with self.subTest(params=params), self.assertRaises(HTTPException):
                corrected_snapshot(old, body(operation="exclude", value=None, **params))
        for value in ("", False, 0, "No consta"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                body(operation="exclude", value=value)
        for params in (dict(evidence=" "), dict(page_index=-1), dict(field="familia_resuelta")):
            with self.subTest(params=params), self.assertRaises(ValidationError):
                body(operation="exclude", value=None, **params)

    def test_exclusion_audit_binds_actor_versions_document_page_evidence_and_reason(self):
        old = previous()
        request = body(operation="exclude", value=None, evidence="El original no muestra fecha de recepción")
        new = SimpleNamespace(id=str(uuid4()), payload_sha256="b" * 64)
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = [(DOC,)]
        with patch("rtm_core.facts_review.repository._case_authority_meta", return_value={"department": "traffic", "case_type": "fine"}), \
             patch("rtm_core.facts_review.repository._require_authority_work_allowed"), \
             patch("rtm_core.facts_review.verify_signed_case_authority"), \
             patch("rtm_core.facts_review.repository.latest_validated_facts", return_value=old), \
             patch("rtm_core.facts_review.repository.invalidate_validated_facts") as invalidate, \
             patch("rtm_core.facts_review.repository.create_validated_facts", return_value=new) as create, \
             patch("rtm_core.facts_review.repository._append_event") as event:
            saved = review_facts(conn, case_id=CASE, facts_id=old.id, body=request, actor="operator:test")
        self.assertIs(saved, new)
        invalidate.assert_called_once_with(conn, CASE, old.id, "operator:test", request.reason)
        self.assertEqual(create.call_args.kwargs["supersedes_id"], old.id)
        payload = event.call_args.args[3]
        self.assertEqual(payload["actor"], "operator:test")
        self.assertEqual(payload["excluded_fields"], ["fecha_notificacion"])
        self.assertEqual(payload["corrected_fields"], [])
        self.assertEqual(payload["exclusion_evidence"], [{
            "field": "fecha_notificacion", "document_id": DOC, "page_index": 1,
            "evidence": request.changes[0].evidence, "reason": request.reason}])
        self.assertEqual(payload["previous_facts_id"], old.id)
        self.assertEqual(payload["facts_id"], new.id)


if __name__ == "__main__": unittest.main()
