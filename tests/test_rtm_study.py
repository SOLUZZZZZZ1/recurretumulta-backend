from contextlib import ExitStack, contextmanager
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rtm_core import study, study_router
from rtm_core.authority_repository import model_digest, validated_model_copy
from rtm_core.preview_repository import LegalPreviewRecord, preview_digest
from tests.test_rtm_parking_preparation import CASE, DOC, snapshot
from tests.test_rtm_velocity_semaforo_specialists import _records, NOW

ACTOR = "operator:33333333-3333-4333-8333-333333333333"


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.facts, self.family = _records(CASE, DOC, snapshot().facts)
        self.facts = self.facts.model_copy(update={"id": str(uuid4())})
        self.family = self.family.model_copy(update={"id": str(uuid4()), "validated_facts_id": self.facts.id})
        self.preview = None
        self.meta = dict(id=CASE, payment_status="paid", authorized=True, status="facts_validation",
                         department="traffic", case_type="fine", category="traffic")
        self.documents = [SimpleNamespace(_mapping={"id": DOC, "sha256": "d" * 64})]
        self.conn = Mock()
        self.conn.execute.return_value.fetchall.side_effect = lambda: self.documents
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        def patched(name, **kwargs):
            return self.stack.enter_context(patch(name, **kwargs))
        self.meta_read = patched("rtm_core.study.authority._case_authority_meta", side_effect=lambda *a, **k: self.meta)
        self.facts_read = patched("rtm_core.study.authority.latest_validated_facts", side_effect=lambda *a, **k: self.facts)
        patched("rtm_core.study.authority.latest_family_resolution", side_effect=lambda *a, **k: self.family)
        patched("rtm_core.study.previews.latest_preview", side_effect=lambda *a, **k: self.preview)
        self.signature = patched("rtm_core.study.verify_signed_case_authority",
                                 return_value={"material_sha256": "e" * 64})
        self.audit = patched("rtm_core.study.authority._append_event")
        self.freeze = patched("rtm_core.study.authority.freeze_validated_facts", side_effect=self.freeze_record)
        self.resolve = patched("rtm_core.study.authority.create_family_resolution", side_effect=self.resolve_record)
        self.lock = patched("rtm_core.study.authority.lock_family_resolution", side_effect=self.lock_record)
        self.build = patched("rtm_core.study.previews.create_preview", side_effect=self.preview_record)

    def freeze_record(self, conn, case_id, facts_id, actor, *, document_review_attestation):
        self.assertEqual((conn, case_id, facts_id, actor), (self.conn, CASE, self.facts.id, ACTOR))
        payload = validated_model_copy(self.facts.facts, frozen=True)
        self.facts = self.facts.model_copy(update={"facts": payload, "frozen": True, "payload_sha256": model_digest(payload)})
        return self.facts

    def resolve_record(self, conn, *, case_id, resolution, created_by, validated_facts_id):
        self.assertEqual((case_id, created_by, validated_facts_id), (CASE, ACTOR, self.facts.id))
        self.family = _records(CASE, DOC, snapshot().facts)[1].model_copy(update={
            "id": str(uuid4()), "validated_facts_id": self.facts.id, "resolution": resolution,
            "payload_sha256": model_digest(resolution), "locked": False})
        return self.family

    def lock_record(self, conn, case_id, family_id, actor):
        self.assertEqual((case_id, family_id, actor), (CASE, self.family.id, ACTOR))
        payload = validated_model_copy(self.family.resolution, locked=True)
        self.family = self.family.model_copy(update={"resolution": payload, "locked": True, "payload_sha256": model_digest(payload)})
        return self.family

    def preview_record(self, conn, *, case_id, preview, created_by, supersedes_id):
        self.assertEqual((case_id, created_by), (CASE, ACTOR))
        self.preview = LegalPreviewRecord(id=str(uuid4()), case_id=CASE, sequence=1,
            validated_facts_id=self.facts.id, family_resolution_id=self.family.id,
            status=preview.status, preview=preview, payload_sha256=preview_digest(preview),
            created_by=created_by, created_at=NOW, supersedes_id=supersedes_id)
        return self.preview

    def draft(self):
        payload = validated_model_copy(self.facts.facts, frozen=False)
        self.facts = self.facts.model_copy(update={"facts": payload, "frozen": False, "payload_sha256": model_digest(payload)})
        self.family = None

    def projection(self):
        return study.load_study(self.conn, CASE)[0]

    def request(self, action=None):
        before = self.projection()
        action = action or before["next_action"]
        review = dict(documents_reviewed=True, facts_reviewed=True, source_document_ids=[DOC],
                      facts_payload_sha256=self.facts.payload_sha256, review_notes="Original completo contrastado")
        return study.StudyActionBody(action=action, confirmed=True, expected_state_sha256=before["state_sha256"],
                                     document_review=review if action == "freeze_facts" else None)

    def advance(self, body):
        return study.advance_study(self.conn, case_id=CASE, body=body, actor=ACTOR)

    def assert_no_writes(self):
        for mutation in (self.freeze, self.resolve, self.lock, self.build, self.audit):
            mutation.assert_not_called()

    def test_complete_four_steps_keep_values_and_build_only_blocked_draft(self):
        self.draft()
        facts_before = deepcopy(self.facts.facts.facts)
        for action in ("freeze_facts", "resolve_family", "lock_family", "build_preview"):
            with self.subTest(action=action):
                body = self.request()
                self.assertEqual(body.action, action)
                result = self.advance(body)
                self.assertEqual(result["completed_action"], action)
                self.assertNotEqual(result["state_sha256"], body.expected_state_sha256)
        self.assertEqual(self.facts.facts.facts, facts_before)
        self.assertEqual(result["stage"], "preview_available")
        self.assertIsNone(result["next_action"])
        self.assertEqual(self.preview.status.value, "draft")
        self.assertEqual(self.preview.preview.legal_arguments, [])
        self.assertEqual(len(self.preview.preview.missing_items), 8)
        self.assertIsNone(self.preview.approved_by)
        self.assertEqual(self.audit.call_count, 4)
        self.assertTrue(self.audit.call_args_list[0].args[3]["document_review"]["facts_reviewed"])
        self.assertEqual({call.args[3]["actor"] for call in self.audit.call_args_list}, {ACTOR})
        self.meta_read.assert_called_with(self.conn, CASE, for_update=True)
        self.assertTrue(any(call.kwargs["for_update"] for call in self.facts_read.call_args_list))

    def test_double_submission_cannot_repeat_a_step(self):
        self.draft()
        body = self.request()
        self.advance(body)
        with self.assertRaises(HTTPException) as caught:
            self.advance(body)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.freeze.call_count, 1)

    def test_changes_in_facts_document_authority_or_payment_make_confirmation_stale(self):
        self.draft()
        mutations = [
            lambda: setattr(self, "facts", self.facts.model_copy(update={"payload_sha256": "b" * 64})),
            lambda: self.documents[0]._mapping.update(sha256="c" * 64),
            lambda: setattr(self.signature, "return_value", {"material_sha256": "f" * 64}),
            lambda: self.meta.update(payment_status="pending"),
        ]
        for mutate in mutations:
            body = self.request("freeze_facts")
            mutate()
            with self.assertRaises(HTTPException) as caught:
                self.advance(body)
            self.assertEqual(caught.exception.status_code, 409)
        self.assert_no_writes()

    def test_unpaid_unsigned_terminal_and_foreign_service_are_blocked(self):
        for changes in (dict(payment_status="pending"), dict(authorized=False), dict(status="closed"), dict(department="claims")):
            old = self.meta.copy()
            self.meta.update(changes)
            projection = self.projection()
            self.assertIsNone(projection["next_action"])
            self.assertTrue(projection["blockers"])
            self.meta = old
        self.signature.side_effect = HTTPException(409, "Firma no verificable")
        self.assertIn("Firma no verificable", self.projection()["blockers"])
        self.assert_no_writes()

    def test_foreign_documents_or_attestation_cannot_close_facts(self):
        self.draft()
        body = self.request()
        for update in (dict(source_document_ids=[str(uuid4())]), dict(facts_payload_sha256="c" * 64)):
            changed = body.model_copy(update={"document_review": body.document_review.model_copy(update=update)})
            with self.assertRaises(HTTPException):
                self.advance(changed)
        self.documents = []
        self.assertIsNone(self.projection()["next_action"])
        self.assert_no_writes()

    def test_unresolved_or_conflicting_facts_cannot_close(self):
        self.draft()
        self.facts.facts.unresolved.append("fecha_limite")
        self.assertIsNone(self.projection()["next_action"])
        self.assert_no_writes()

    def test_unknown_specialist_wrong_chain_and_unresolved_family_are_blocked(self):
        original = self.family
        for update in ({"validated_facts_id": str(uuid4())},
                       {"resolution": self.family.resolution.model_copy(update={"specialist": "unknown"})},
                       {"resolution": self.family.resolution.model_copy(update={"unresolved": ["Pendiente"]})}):
            self.family = original.model_copy(update=update)
            self.assertIsNone(self.projection()["next_action"])
        self.assert_no_writes()

    def test_personal_document_checks_reject_coerced_booleans(self):
        self.draft()
        data = self.request().model_dump()
        for key in ("documents_reviewed", "facts_reviewed"):
            for value in (1, "true", False, None):
                changed = deepcopy(data)
                changed["document_review"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValidationError):
                    study.StudyActionBody.model_validate(changed)

    def test_payload_strictly_rejects_impersonation_missing_review_and_unconfirmed_steps(self):
        self.draft()
        data = self.request().model_dump()
        for update in ({"actor": "forged"}, {"confirmed": False}, {"confirmed": 1},
                       {"action": "approve_preview"}, {"document_review": None},
                       {"expected_state_sha256": "../invalid"}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                study.StudyActionBody.model_validate({**data, **update})


class StudyRouterTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(study_router.router)
        self.client = TestClient(self.app)
        self.scope = SimpleNamespace(individual_session=True, scope_all=True, operator_id="trusted-id")
        self.conn = object()
        self.rolled_back = False
        self.committed = False
        @contextmanager
        def transaction():
            try:
                yield self.conn
            except Exception:
                self.rolled_back = True
                raise
            else:
                self.committed = True
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in [
            ("require_operator_token", Mock()),
            ("load_ops_case_scope", Mock(return_value=self.scope)),
            ("get_engine", Mock(return_value=SimpleNamespace(begin=transaction))),
            ("require_case_in_scope", Mock(return_value=CASE)),
            ("advance_study", Mock(return_value={"ok": True})),
            ("load_study", Mock(return_value=({"ok": True}, None, None, None))),
        ]:
            setattr(self, name, self.stack.enter_context(patch.object(study_router, name, value)))
        self.body = dict(action="resolve_family", confirmed=True, expected_state_sha256="a" * 64)

    def test_supervisor_actor_comes_from_server_and_response_is_not_cached(self):
        response = self.client.post(f"/ops/core/cases/{CASE}/study/actions", json=self.body,
                                   headers={"X-Operator-Actor": "forged"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.advance_study.call_args.kwargs["actor"], "operator:trusted-id")
        self.require_case_in_scope.assert_called_once_with(self.conn, scope=self.scope, case_id=CASE)
        self.assertTrue(self.committed)

    def test_operator_or_legacy_session_rejected_before_database(self):
        for attr in ("scope_all", "individual_session", "operator_id"):
            with self.subTest(attr=attr):
                old = getattr(self.scope, attr)
                setattr(self.scope, attr, False)
                response = self.client.post(f"/ops/core/cases/{CASE}/study/actions", json=self.body)
                self.assertEqual(response.status_code, 403)
                setattr(self.scope, attr, old)
        self.get_engine.assert_not_called()
        self.advance_study.assert_not_called()

    def test_scope_denial_prevents_projection_and_mutation(self):
        self.require_case_in_scope.side_effect = HTTPException(404, "Fuera de alcance")
        for method, path in (("get", "study"), ("post", "study/actions")):
            response = getattr(self.client, method)(f"/ops/core/cases/{CASE}/{path}",
                       **({"json": self.body} if method == "post" else {}))
            self.assertEqual(response.status_code, 404)
        self.advance_study.assert_not_called()
        self.load_study.assert_not_called()
        self.assertTrue(self.rolled_back)

    def test_failure_rolls_back_transaction(self):
        self.advance_study.side_effect = HTTPException(409, "Changed")
        self.assertEqual(self.client.post(f"/ops/core/cases/{CASE}/study/actions", json=self.body).status_code, 409)
        self.assertTrue(self.rolled_back)
        self.assertFalse(self.committed)
