from __future__ import annotations
import asyncio
from dataclasses import replace
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import reanalysis
from rtm_core import reanalysis_execution as execution
from rtm_core import staging_rehearsal_analysis as analysis
from rtm_core import staging_rehearsal_router as routes
from rtm_core.ops_case_scope import OpsCaseScope
from rtm_core.staging_rehearsal import RehearsalGrant, RADAR_SHA, fixture

GRANT = RehearsalGrant("11111111-1111-4111-8111-111111111111")
SCOPE = OpsCaseScope(GRANT.operator_id, "rtm.supervisor", ("ops.view", "ops.supervise"), True, True)
CONTENT = fixture("radar")[1]
DOC = analysis.RehearsalAnalysisDocument(GRANT.case_id, "22222222-2222-4222-8222-222222222222", "fixture-only", "radar.pdf", len(CONTENT))
ROW = dict(id=DOC.document_id, b2_bucket=DOC.bucket, b2_key=DOC.key, mime="application/pdf", size_bytes=len(CONTENT), sha256=RADAR_SHA)
SOURCE = dict(id=DOC.document_id, bucket=DOC.bucket, key=DOC.key, mime="application/pdf", size_bytes=len(CONTENT), sha256=RADAR_SHA)


class RehearsalAnalysisBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.conn.execute.return_value.mappings.return_value.all.return_value = [ROW]
        self.profile = self.enterContext(patch.object(analysis, "require_profile"))
        self.existing = self.enterContext(patch.object(analysis, "existing_case", return_value=True))

    def prepare(self, **changes):
        args = dict(case_id=GRANT.case_id, grant=GRANT, scope=SCOPE) | changes
        return analysis.prepare_rehearsal_analysis_document(self.conn, **args)

    def test_exact_owned_fixture_is_bound_under_transaction(self):
        self.assertEqual(self.prepare(), DOC)
        self.existing.assert_called_once_with(GRANT, self.conn)
        self.assertIn("FOR UPDATE", str(self.conn.execute.call_args.args[0]))

    def test_foreign_case_operator_legacy_and_missing_permission_denied(self):
        for change in (dict(case_id=DOC.document_id), dict(grant=object()),
                dict(scope=replace(SCOPE, operator_id=DOC.document_id)),
                dict(scope=replace(SCOPE, individual_session=False)),
                dict(scope=replace(SCOPE, role_code="rtm.operator")),
                dict(scope=replace(SCOPE, permissions=("ops.view",)))):
            with self.subTest(change=change), self.assertRaises(HTTPException):
                self.prepare(**change)
        self.existing.assert_not_called()
        self.conn.execute.assert_not_called()

    def test_unsafe_runtime_and_changed_case_fail_before_document_lookup(self):
        self.profile.side_effect = HTTPException(503, "closed")
        with self.assertRaises(HTTPException): self.prepare()
        self.profile.side_effect = None
        self.existing.side_effect = HTTPException(409, "case altered")
        with self.assertRaises(HTTPException): self.prepare()
        self.conn.execute.assert_not_called()

    def test_extra_missing_foreign_and_unstored_originals_are_denied(self):
        invalid = [[], [ROW, ROW]] + [[ROW | change] for change in (
            {"sha256":"a"*64}, {"size_bytes":len(CONTENT)+1}, {"mime":"image/png"},
            {"b2_bucket":None}, {"b2_key":""})]
        for rows in invalid:
            self.conn.execute.return_value.mappings.return_value.all.return_value = rows
            with self.subTest(rows=rows), self.assertRaises(HTTPException): self.prepare()

    def test_second_document_read_must_match_the_claimed_original(self):
        DOC.verify_documents(GRANT.case_id, [SOURCE])
        for documents in ([], [SOURCE,SOURCE], [SOURCE|{"id":"other"}], [SOURCE|{"key":"other"}], [SOURCE|{"sha256":"b"*64}]):
            with self.assertRaises(HTTPException): DOC.verify_documents(GRANT.case_id, documents)
        with self.assertRaises(HTTPException): DOC.verify_documents(DOC.document_id, [SOURCE])

    def test_only_exact_bundled_bytes_are_sent_to_provider(self):
        DOC.verify_bytes(CONTENT)
        for content in (b"", CONTENT+b"changed", b"private customer document"):
            with self.assertRaises(HTTPException): DOC.verify_bytes(content)

    def test_verified_completed_snapshot_can_be_reused_but_not_foreign_output(self):
        wrapper = dict(completion_status="completed",source_document_ids=[DOC.document_id],
            size_bytes=DOC.size_bytes,sha256=hashlib.sha256(RADAR_SHA.encode()).hexdigest())
        event = {"ok":True,"reanalysis_run_id":"33333333-3333-4333-8333-333333333333"}
        with patch.object(analysis,"load_latest_reanalysis_snapshot",return_value=(wrapper,event)) as latest:
            self.assertEqual(analysis.previous_rehearsal_analysis(self.conn,DOC),event)
            for change in ({"completion_status":"partial"},{"source_document_ids":["other"]},{"sha256":"a"*64},{"size_bytes":0}):
                latest.return_value=(wrapper|change,event)
                with self.assertRaises(HTTPException): analysis.previous_rehearsal_analysis(self.conn,DOC)
            latest.side_effect=HTTPException(404,"missing")
            self.assertIsNone(analysis.previous_rehearsal_analysis(self.conn,DOC))
            latest.side_effect=HTTPException(409,"incomplete")
            with self.assertRaises(HTTPException): analysis.previous_rehearsal_analysis(self.conn,DOC)


class RehearsalAnalysisExecutionTests(unittest.TestCase):
    def setUp(self):
        self.engine=MagicMock()
        self.conn=self.engine.begin.return_value.__enter__.return_value
        self.meta=dict(id=GRANT.case_id,payment_status="paid",authorized=True,status="manual_review",
            department="traffic",case_type="fine",test_mode=True,facts_table="rtm_validated_facts")
        self.enterContext(patch.object(execution,"get_engine",return_value=self.engine))
        self.enterContext(patch.object(execution,"require_case_in_scope",return_value=GRANT.case_id))
        self.doc=self.enterContext(patch.object(analysis,"prepare_rehearsal_analysis_document",return_value=DOC))
        self.previous=self.enterContext(patch.object(analysis,"previous_rehearsal_analysis",return_value=None))
        self.authority=self.enterContext(patch.object(execution,"verify_signed_case_authority",return_value={"material_sha256":"a"*64}))

    def connection(self, **changes):
        self.conn.execute.return_value.fetchone.side_effect=[SimpleNamespace(_mapping=self.meta|changes),None,(GRANT.case_id,)]
        self.conn.execute.return_value.scalar_one.return_value=1

    def test_ordinary_operational_route_still_rejects_test_mode(self):
        self.connection()
        with self.assertRaises(HTTPException) as raised: execution._case_guard(GRANT.case_id,scope=SCOPE)
        self.assertIn("test_mode",str(raised.exception.detail))
        self.doc.assert_not_called()
        self.authority.assert_not_called()

    def test_trial_still_requires_paid_signed_nonterminal_single_flight_state(self):
        for changes in ({"payment_status":"unpaid"},{"authorized":False},{"status":"closed"},{"status":"reanalysis_in_progress"}):
            self.connection(**changes)
            with self.subTest(changes=changes),self.assertRaises(HTTPException):
                execution._case_guard(GRANT.case_id,scope=SCOPE,rehearsal_grant=GRANT)
        self.authority.assert_not_called()

    def test_trial_claim_preserves_signature_validation_and_bound_document(self):
        self.connection()
        claim=execution._case_guard(GRANT.case_id,scope=SCOPE,rehearsal_grant=GRANT)
        self.assertEqual(claim["rehearsal_document"],DOC)
        self.assertEqual(claim["prior_status"],"manual_review")
        self.authority.assert_called_once_with(self.conn,GRANT.case_id)
        self.assertIn("reanalysis_in_progress",str(self.conn.execute.call_args.args[0]))

    def test_completed_retry_does_not_claim_or_call_the_provider(self):
        self.connection()
        self.previous.return_value={"ok":True}
        with patch.object(execution,"require_http_capability"),patch.object(execution.legacy_reanalysis,"reanalyze_traffic_fine_case") as provider:
            result=execution.run_safe_traffic_reanalysis(GRANT.case_id,actor="ops:test",scope=SCOPE,rehearsal_grant=GRANT)
        self.assertTrue(result["reused"])
        provider.assert_not_called()
        self.assertFalse(any("UPDATE cases" in str(c.args[0]) for c in self.conn.execute.call_args_list))

    def test_execution_passes_bound_document_and_resets_failed_claim(self):
        with patch.object(execution,"require_http_capability"),patch.object(execution,"_case_guard",return_value={"prior_status":"manual_review","rehearsal_document":DOC}),patch.object(execution,"install_safe_extraction_policy"),patch.object(execution.legacy_reanalysis,"reanalyze_traffic_fine_case",side_effect=RuntimeError("provider failed")) as provider,patch.object(execution,"_reset_failed_claim") as reset:
            with self.assertRaises(RuntimeError):
                execution.run_safe_traffic_reanalysis(GRANT.case_id,actor="ops:test",scope=SCOPE,rehearsal_grant=GRANT)
        provider.assert_called_once_with(GRANT.case_id,rehearsal_document=DOC)
        reset.assert_called_once_with(GRANT.case_id,"manual_review")


class RehearsalAnalysisProviderBoundaryTests(unittest.TestCase):
    def test_changed_storage_bytes_or_source_set_never_reach_provider(self):
        with patch.object(reanalysis,"require_capability"),patch.object(reanalysis,"_case_meta",return_value={"department":"traffic","case_type":"fine"}),patch.object(reanalysis,"_load_original_documents",return_value=[SOURCE]) as documents,patch.object(reanalysis,"download_bytes_limited",return_value=b"private unexpected content") as download,patch.object(reanalysis,"_append_event"),patch.object(reanalysis,"_analyze_page_candidate") as provider,patch.object(reanalysis,"_persist_completed_reanalysis") as persist:
            with self.assertRaises(HTTPException): reanalysis.reanalyze_traffic_fine_case(GRANT.case_id,rehearsal_document=DOC)
            download.reset_mock()
            documents.return_value=[SOURCE,SOURCE]
            with self.assertRaises(HTTPException): reanalysis.reanalyze_traffic_fine_case(GRANT.case_id,rehearsal_document=DOC)
            download.assert_not_called()
        provider.assert_not_called()
        persist.assert_not_called()


class RehearsalAnalysisRouteTests(unittest.TestCase):
    def test_route_accepts_only_authenticated_owner_without_overrides(self):
        app=FastAPI();app.include_router(routes.router);client=TestClient(app)
        url="/ops/rehearsal/radar/cases/"+GRANT.case_id+"/analysis"
        with patch.object(routes,"require_supervisor",return_value=GRANT),patch("rtm_core.ops_case_scope.load_ops_case_scope",return_value=SCOPE),patch.object(execution,"run_safe_traffic_reanalysis",return_value={"ok":True,"reused":True}) as run:
            response=client.post(url)
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(response.json()["requires_human_review"])
            self.assertTrue(response.json()["reused"])
            self.assertIn("no-store",response.headers["cache-control"])
            run.assert_called_once_with(GRANT.case_id,actor="ops:"+GRANT.operator_id,scope=SCOPE,rehearsal_grant=GRANT)
            run.reset_mock()
            self.assertEqual(client.post(url,json={"test_mode":False}).status_code,422)
            self.assertEqual(client.post(url.replace(GRANT.case_id,DOC.document_id)).status_code,404)
            run.assert_not_called()

