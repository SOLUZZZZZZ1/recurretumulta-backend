from __future__ import annotations

import hashlib
import inspect
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

import generate
import reanalysis
from rtm_core.ai_security import consume_model_call_budget
from rtm_core.ops_case_scope import OpsCaseScope
from rtm_core.reanalysis_adapter import load_latest_reanalysis_snapshot
from rtm_core import reanalysis_execution


class ReanalysisAtomicityTest(unittest.TestCase):
    def test_in_progress_claim_is_single_flight_and_never_reclaimed(self):
        engine = MagicMock()
        conn = engine.begin.return_value.__enter__.return_value
        locked_case = SimpleNamespace(
            _mapping={
                "id": "case-1",
                "payment_status": "paid",
                "authorized": True,
                "status": "reanalysis_in_progress",
                "department": "traffic",
                "case_type": "fine",
                "test_mode": False,
                "facts_table": "rtm_validated_facts",
            }
        )
        conn.execute.return_value.fetchone.return_value = locked_case
        scope = OpsCaseScope(
            operator_id=str(uuid.uuid4()),
            role_code="rtm.supervisor",
            permissions=("ops.view", "ops.supervise"),
            scope_all=True,
            individual_session=True,
        )

        with (
            patch.object(reanalysis_execution, "get_engine", return_value=engine),
            patch.object(
                reanalysis_execution,
                "require_case_in_scope",
                return_value="case-1",
            ) as require_scope,
            patch.object(
                reanalysis_execution,
                "verify_signed_case_authority",
            ) as verify_authority,
        ):
            with self.assertRaises(HTTPException) as raised:
                reanalysis_execution._case_guard("case-1", scope=scope)

        self.assertEqual(raised.exception.status_code, 409)
        require_scope.assert_called_once_with(conn, scope=scope, case_id="case-1")
        verify_authority.assert_not_called()
        statements = [str(call.args[0]) for call in conn.execute.call_args_list]
        self.assertEqual(len(statements), 1)
        self.assertNotIn("UPDATE cases", statements[0])

    def test_budget_exhaustion_never_persists_page_candidates(self):
        contents = [f"synthetic-page-{index}".encode() for index in range(8)]
        documents = [
            {
                "id": f"document-{index}",
                "bucket": "private-bucket",
                "key": f"cases/case-1/original/{index}.pdf",
                "mime": "application/pdf",
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for index, content in enumerate(contents)
        ]
        page_calls = 0

        def page_candidate(content, filename, mime):
            nonlocal page_calls
            page_calls += 1
            for _ in range(3):
                consume_model_call_budget()
            return (
                {
                    "filename": filename,
                    "mime": mime,
                    "evidence_status": "candidate_only",
                    "extracted": {"evidence_status": "candidate_only"},
                },
                0.75,
            )

        def consolidation_attempt(*_args, **_kwargs):
            # Las ocho paginas consumieron exactamente 24 llamadas. La primera
            # llamada consolidada reproduce el fallo que antes dejaba 8 filas.
            consume_model_call_budget()
            raise AssertionError("el presupuesto debia bloquear antes")

        append_event = MagicMock()
        persist = MagicMock()
        with (
            patch.object(reanalysis, "require_capability"),
            patch.object(
                reanalysis,
                "_case_meta",
                return_value={
                    "department": "traffic",
                    "case_type": "fine",
                    "status": "manual_review",
                    "payment_status": "paid",
                },
            ),
            patch.object(reanalysis, "_load_original_documents", return_value=documents),
            patch.object(
                reanalysis,
                "download_bytes_limited",
                side_effect=contents,
            ),
            patch.object(
                reanalysis,
                "validate_document_bytes",
                return_value=SimpleNamespace(mime="application/pdf"),
            ),
            patch.object(
                reanalysis,
                "_normalize_image_for_analysis",
                side_effect=lambda content, mime: (content, mime, {}),
            ),
            patch.object(
                reanalysis,
                "_filename_for_mime",
                side_effect=lambda index, _key, _mime: f"page-{index}.pdf",
            ),
            patch.object(
                reanalysis,
                "_analyze_page_candidate",
                side_effect=page_candidate,
            ),
            patch.object(
                reanalysis,
                "_consolidate_extraction",
                side_effect=consolidation_attempt,
            ),
            patch.object(reanalysis, "_append_event", append_event),
            patch.object(reanalysis, "_persist_completed_reanalysis", persist),
        ):
            with self.assertRaises(HTTPException) as raised:
                reanalysis.reanalyze_traffic_fine_case("case-1")

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(page_calls, 8)
        persist.assert_not_called()
        event_types = [call.args[1] for call in append_event.call_args_list]
        self.assertIn("case_reanalysis_budget_exhausted", event_types)
        self.assertNotIn("case_reanalysis_completed", event_types)
        source = inspect.getsource(reanalysis.reanalyze_traffic_fine_case)
        self.assertNotIn("analyze_existing_case_document", source)
        consolidation_source = inspect.getsource(reanalysis._consolidate_extraction)
        self.assertNotIn("get_engine", consolidation_source)
        self.assertNotIn("INSERT INTO extractions", consolidation_source)

    def test_completed_result_state_and_event_share_one_transaction(self):
        engine = MagicMock()
        conn = engine.begin.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.return_value = ("case-1",)
        run_id = str(uuid.uuid4())

        with patch.object(reanalysis, "get_engine", return_value=engine):
            reanalysis._persist_completed_reanalysis(
                "case-1",
                wrapper={"reanalysis_run_id": run_id, "extracted": {}},
                confidence=0.75,
                event_payload={"reanalysis_run_id": run_id},
            )

        engine.begin.assert_called_once_with()
        statements = [str(call.args[0]) for call in conn.execute.call_args_list]
        self.assertEqual(len(statements), 3)
        self.assertIn("UPDATE cases", statements[0])
        self.assertIn("status, '')='reanalysis_in_progress'", statements[0])
        self.assertIn("INSERT INTO extractions", statements[1])
        self.assertIn("case_reanalysis_completed", statements[2])

    def test_completed_result_rejects_lost_or_replaced_execution_claim(self):
        engine = MagicMock()
        conn = engine.begin.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.return_value = None
        run_id = str(uuid.uuid4())

        with patch.object(reanalysis, "get_engine", return_value=engine):
            with self.assertRaises(HTTPException) as raised:
                reanalysis._persist_completed_reanalysis(
                    "case-1",
                    wrapper={"reanalysis_run_id": run_id, "extracted": {}},
                    confidence=0.75,
                    event_payload={"reanalysis_run_id": run_id},
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(conn.execute.call_count, 1)

    def test_snapshot_requires_same_explicit_completion_run(self):
        run_id = str(uuid.uuid4())
        other_run_id = str(uuid.uuid4())
        wrapper = {
            "reanalysis_run_id": run_id,
            "extracted": {"extractor_version": "traffic_fine_reanalysis_v1_18"},
        }

        for event in (
            None,
            {
                "reanalysis_run_id": other_run_id,
                "extractor_version": "traffic_fine_reanalysis_v1_18",
            },
        ):
            with self.subTest(event=event):
                conn = MagicMock()
                extraction_result = MagicMock()
                extraction_result.fetchone.return_value = (wrapper, "model", "now")
                event_result = MagicMock()
                event_result.fetchone.return_value = (
                    (event, "now") if event is not None else None
                )
                conn.execute.side_effect = [extraction_result, event_result]
                with self.assertRaises(HTTPException) as raised:
                    load_latest_reanalysis_snapshot(conn, "case-1")
                self.assertEqual(raised.exception.status_code, 409)

        conn = MagicMock()
        extraction_result = MagicMock()
        extraction_result.fetchone.return_value = (wrapper, "model", "now")
        event_result = MagicMock()
        event_result.fetchone.return_value = (
            {
                "reanalysis_run_id": run_id,
                "extractor_version": "traffic_fine_reanalysis_v1_18",
            },
            "now",
        )
        conn.execute.side_effect = [extraction_result, event_result]
        loaded_wrapper, loaded_event = load_latest_reanalysis_snapshot(conn, "case-1")
        self.assertEqual(loaded_wrapper, wrapper)
        self.assertEqual(loaded_event["reanalysis_run_id"], run_id)

    def test_legacy_generator_rejects_candidate_latest_row(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (
            {
                "evidence_status": "candidate_only",
                "extracted": {
                    "evidence_status": "candidate_only",
                    "ready_for_generate": True,
                },
            },
        )

        with self.assertRaises(HTTPException) as raised:
            generate.generate_dgt_for_case(conn, "case-1")

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("validación humana", str(raised.exception.detail))
        self.assertEqual(conn.execute.call_count, 1)


if __name__ == "__main__":
    unittest.main()
