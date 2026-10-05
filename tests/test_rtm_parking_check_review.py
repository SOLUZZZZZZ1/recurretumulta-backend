from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import json
import os
import secrets
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from rtm_core import study, study_router, parking_check_review as review, study_documents
from rtm_core.authority_repository import model_digest, validated_model_copy
from rtm_core.preview_repository import LegalPreviewRecord, preview_digest
from rtm_core.traffic_parking_specialist import build_parking_preview
from tests.test_rtm_parking_preparation import CASE, DOC, snapshot
from tests.test_rtm_velocity_semaforo_specialists import _records, NOW

ACTOR = "operator:33333333-3333-4333-8333-333333333333"


class ParkingCheckReviewTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {"RTM_AUTHORITY_SIGNING_SECRET": secrets.token_urlsafe(48)}))
        facts = snapshot(pago_multa_reducido=False)
        self.facts, self.family = _records(CASE, DOC, facts.facts)
        self.facts = self.facts.model_copy(update={"id": str(uuid4())})
        self.family = self.family.model_copy(update={"id": str(uuid4()), "validated_facts_id": self.facts.id})
        self.docs = [{"id": DOC, "sha256": "d" * 64}]
        self.events = {}
        self.conn = Mock()
        self.conn.execute.side_effect = self.execute
        self.current = self.make_record(build_parking_preview(self.facts, self.family))
        for name, fn in [
            ("_case_authority_meta", lambda *a, **k: dict(id=CASE, payment_status="paid", authorized=True, status="preview_draft", department="traffic", case_type="fine", category="traffic")),
            ("latest_validated_facts", lambda *a, **k: self.facts),
            ("latest_family_resolution", lambda *a, **k: self.family),
        ]:
            self.stack.enter_context(patch.object(study.authority, name, side_effect=fn))
        self.stack.enter_context(patch.object(study.previews, "latest_preview", side_effect=lambda *a, **k: self.current))
        self.stack.enter_context(patch.object(study, "verify_signed_case_authority", return_value={"material_sha256": "f" * 64}))
        self.submit = self.stack.enter_context(patch.object(review.previews, "submit_for_review"))
        self.request_changes = self.stack.enter_context(patch.object(review.previews, "request_changes"))
        self.create = self.stack.enter_context(patch.object(review.previews, "create_preview", side_effect=self.create_record))

    def make_record(self, payload, supersedes=None):
        return LegalPreviewRecord(id=str(uuid4()), case_id=CASE, sequence=1 if not supersedes else 2,
            validated_facts_id=self.facts.id, family_resolution_id=self.family.id,
            status=payload.status, preview=payload, payload_sha256=preview_digest(payload),
            created_by=ACTOR, created_at=NOW, supersedes_id=supersedes)

    def create_record(self, conn, *, case_id, preview, created_by, supersedes_id):
        self.assertEqual((case_id, created_by, supersedes_id), (CASE, ACTOR, self.current.id))
        self.current = self.make_record(preview, supersedes_id)
        return self.current

    def execute(self, sql, params=None):
        sql = str(sql)
        if "SELECT id::text" in sql:
            return SimpleNamespace(fetchall=lambda: [SimpleNamespace(_mapping=d) for d in self.docs])
        if "INSERT INTO events" in sql:
            envelope = json.loads(params["payload"])
            self.events[envelope["material"]["preview_id"]] = (params["id"], envelope)
            return Mock()
        if "SELECT id,payload FROM events" in sql:
            row = self.events.get(params["preview"])
            return SimpleNamespace(fetchall=lambda: [row] if row else [])
        raise AssertionError("Unexpected SQL: " + sql)

    def state(self):
        return study.load_study(self.conn, CASE)[0]

    def save(self, code="payment", result="reviewed", notes="Dato y fuente contrastados para esta comprobación."):
        state = self.state()
        body = review.CheckReviewBody(expected_state_sha256=state["state_sha256"], check_id=code,
                                     result=result, notes=notes, confirmed=True)
        review.save_review(self.conn, case_id=CASE, body=body, actor=ACTOR, before=state,
                           facts=self.facts, family=self.family, previous=self.current, documents=self.docs)
        return self.state()

    def test_missing_information_cannot_be_marked_reviewed(self):
        with self.assertRaises(HTTPException):
            self.save("location")
        self.create.assert_not_called()
        self.submit.assert_not_called()

    def test_documented_false_is_reviewable_and_never_approves_or_calculates(self):
        before = self.current
        facts_digest = self.facts.payload_sha256
        result = self.save()
        self.assertEqual(result["parking_review"]["reviewed_count"], 1)
        self.assertEqual(self.current.supersedes_id, before.id)
        self.assertNotEqual(self.current.id, before.id)
        self.assertEqual(self.facts.payload_sha256, facts_digest)
        self.assertEqual(before.status.value, "draft")
        self.assertEqual(len(before.preview.missing_items), 8)
        self.assertEqual(len(self.current.preview.missing_items), 7)
        self.assertEqual(self.current.preview.legal_arguments, [])
        self.assertEqual(self.current.preview.requested_outcomes, [])
        self.assertIsNone(self.current.preview.approved_at)
        self.assertEqual(self.current.preview.deadlines[0].calculation_status, "unresolved")
        self.assertFalse(result["parking_review"]["approval_enabled"])

    def test_needs_information_records_notes_and_can_restore_a_blocker(self):
        self.save()
        state = self.save(result="needs_information", notes="Debe confirmarse si hubo un pago posterior.")
        self.assertEqual(state["parking_review"]["reviewed_count"], 0)
        self.assertEqual(len(self.current.preview.missing_items), 8)
        payment = next(c for c in state["parking_review"]["checks"] if c["id"] == "payment")
        self.assertIn("posterior", payment["review"]["notes"])
        self.assertEqual(payment["review"]["actor"], ACTOR)

    def test_signed_receipt_rejects_tampering_cross_case_and_changed_document(self):
        self.save()
        original = deepcopy(self.events)
        material = self.events[self.current.id][1]["material"]
        material["reviews"]["payment"]["result"] = "needs_information"
        with self.assertRaises(HTTPException):
            self.state()
        self.events = deepcopy(original)
        self.docs[0]["sha256"] = "b" * 64
        with self.assertRaises(HTTPException):
            self.state()
        self.docs[0]["sha256"] = "d" * 64
        material = deepcopy(original[self.current.id][1]["material"])
        material["case_id"] = str(uuid4())
        from case_authority import _signed_envelope
        self.events[self.current.id] = (material["id"], _signed_envelope(material))
        with self.assertRaises(HTTPException):
            self.state()

    def test_receipt_required_for_reviewed_component(self):
        self.save()
        self.events.clear()
        with self.assertRaises(HTTPException):
            self.state()

    def test_strict_personal_confirmation_and_no_browser_actor(self):
        good = dict(expected_state_sha256="a"*64, check_id="payment", result="reviewed",
                    notes="Original personalmente revisado", confirmed=True)
        for changes in ({"confirmed": False}, {"confirmed": 1}, {"confirmed": "true"}, {"actor": ACTOR},
                        {"check_id": "unknown"}, {"notes": "          "}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                review.CheckReviewBody.model_validate({**good, **changes})
        for value in (False, 1, "true"):
            with self.assertRaises(ValidationError):
                study_router.StudyDocumentBody(expected_state_sha256="a"*64, reason="Documento adicional de prueba", confirmed=value)

    def test_missing_fields_do_not_change_existing_preview(self):
        prior = self.current.model_dump_json()
        with self.assertRaises(HTTPException):
            self.save("deadline")
        self.assertEqual(self.current.model_dump_json(), prior)


class DocumentaryRevisionTests(unittest.TestCase):
    def test_new_revision_preserves_frozen_payload_and_links_new_source(self):
        previous, _ = _records(CASE, DOC, snapshot().facts)
        frozen = previous.model_dump_json()
        extra = str(uuid4())
        saved = SimpleNamespace(id=str(uuid4()), payload_sha256="a"*64)
        with patch.object(study_documents.authority, "invalidate_validated_facts") as invalidate, \
             patch.object(study_documents.authority, "create_validated_facts", return_value=saved) as create, \
             patch.object(study_documents.authority, "_append_event") as audit:
            result = study_documents.replace_facts(object(), case_id=CASE, facts=previous, actor=ACTOR,
                reason="Nueva documentación para completar el estudio", document_ids=[extra])
        self.assertIs(result, saved)
        self.assertEqual(previous.model_dump_json(), frozen)
        new = create.call_args.kwargs["facts"]
        self.assertFalse(new.frozen)
        self.assertEqual(new.facts, previous.facts.facts)
        self.assertEqual(new.source_document_ids, [DOC, extra])
        self.assertEqual(create.call_args.kwargs["supersedes_id"], previous.id)
        self.assertEqual(invalidate.call_count, 1)
        self.assertEqual(audit.call_args.args[3]["new_source_document_ids"], [extra])

    def test_approved_preview_and_blocked_case_do_not_allow_revision(self):
        facts = SimpleNamespace(frozen=True)
        for status in ("approved", "frozen"):
            self.assertFalse(study_documents.revision_available({"blockers": []}, facts, SimpleNamespace(status=status)))
        self.assertFalse(study_documents.revision_available({"blockers": ["Pago"]}, facts, None))

    def test_stale_upload_and_duplicate_fail_before_storage(self):
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = (DOC,)
        state = {"state_sha256": "a"*64, "blockers": []}
        facts = SimpleNamespace(id="facts")
        body = SimpleNamespace(expected_state_sha256="b"*64, reason="Documento de prueba adicional")
        with patch("rtm_core.study.load_study", return_value=(state, facts, None, None)), \
             patch("b2_storage.upload_bytes") as upload:
            with self.assertRaises(HTTPException):
                study_documents.append_document(conn, case_id=CASE, body=body, actor=ACTOR, data=b"PDF",
                    validated=SimpleNamespace(sha256="c"*64), uploaded=[])
            body.expected_state_sha256 = "a"*64
            with self.assertRaises(HTTPException):
                study_documents.append_document(conn, case_id=CASE, body=body, actor=ACTOR, data=b"PDF",
                    validated=SimpleNamespace(sha256="c"*64), uploaded=[])
            upload.assert_not_called()

    def test_ambiguous_commit_never_deletes_a_referenced_or_unverifiable_object(self):
        from unittest.mock import MagicMock
        engine = MagicMock()
        conn = engine.connect.return_value.__enter__.return_value
        with patch("b2_storage.delete_object") as remove:
            conn.execute.return_value.fetchone.return_value = (1,)
            study_router._cleanup_uncommitted_documents(engine, [("bucket", "cases/case/original/key")])
            remove.assert_not_called()
            engine.connect.side_effect = RuntimeError("DB unavailable")
            study_router._cleanup_uncommitted_documents(engine, [("bucket", "cases/case/original/key")])
            remove.assert_not_called()
            engine.connect.side_effect = None
            conn.execute.return_value.fetchone.return_value = None
            study_router._cleanup_uncommitted_documents(engine, [("bucket", "cases/case/original/key")])
            remove.assert_called_once_with("bucket", "cases/case/original/key")


class ReviewRouteTests(unittest.TestCase):
    def setUp(self):
        import tests.test_rtm_study as existing
        existing.StudyRouterTests.setUp(self)

    def test_check_review_is_scoped_and_uses_only_the_server_actor(self):
        before = {"state_sha256": "a"*64, "documents": [], "blockers": []}
        after = {"state_sha256": "b"*64}
        self.load_study.side_effect = [(before, object(), object(), object()), (after, None, None, None)]
        payload = dict(expected_state_sha256="a"*64, check_id="payment", result="needs_information",
                       notes="Comprobar si hay un pago posterior documentado", confirmed=True)
        with patch.object(study_router, "save_review") as save:
            response = self.client.post(f"/ops/core/cases/{CASE}/study/check-reviews", json=payload,
                                        headers={"X-Operator-Actor": "forged"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(save.call_args.kwargs["actor"], "operator:trusted-id")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertTrue(self.committed)

    def test_stale_check_fails_before_any_mutation(self):
        self.load_study.return_value = ({"state_sha256": "b"*64}, None, None, None)
        with patch.object(study_router, "save_review") as save:
            response = self.client.post(f"/ops/core/cases/{CASE}/study/check-reviews",
                json=dict(expected_state_sha256="a"*64, check_id="payment", result="needs_information",
                          notes="Dato pendiente de contraste documental", confirmed=True))
        self.assertEqual(response.status_code, 409)
        save.assert_not_called()
        self.assertTrue(self.rolled_back)

    def test_both_new_mutations_reject_legacy_and_scoped_operator_before_database(self):
        review_body = dict(expected_state_sha256="a"*64, check_id="payment", result="needs_information",
                           notes="Dato pendiente de contraste documental", confirmed=True)
        document_body = dict(expected_state_sha256="a"*64, reason="Documento ficticio adicional", confirmed=True)
        for attr in ("scope_all", "individual_session", "operator_id"):
            prior = getattr(self.scope, attr)
            setattr(self.scope, attr, False)
            self.assertEqual(self.client.post(f"/ops/core/cases/{CASE}/study/check-reviews", json=review_body).status_code, 403)
            self.assertEqual(self.client.post(f"/ops/core/cases/{CASE}/study/documents",
                data={"metadata": json.dumps(document_body)}, files={"file": ("test.pdf", b"%PDF-test", "application/pdf")}).status_code, 403)
            setattr(self.scope, attr, prior)
        self.get_engine.assert_not_called()

    def test_foreign_scope_is_rejected_before_document_validation(self):
        self.require_case_in_scope.side_effect = HTTPException(404, "Fuera de alcance")
        with patch.object(study_router, "validate_document_bytes") as validate:
            response = self.client.post(f"/ops/core/cases/{CASE}/study/documents",
                data={"metadata": json.dumps(dict(expected_state_sha256="a"*64, reason="Documento ficticio adicional", confirmed=True))},
                files={"file": ("test.pdf", b"%PDF-test", "application/pdf")})
        self.assertEqual(response.status_code, 404)
        validate.assert_not_called()
