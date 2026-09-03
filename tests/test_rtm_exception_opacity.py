from __future__ import annotations

import hashlib
import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

import ops_automation
import ops_operator_router
import reanalysis
from rtm_core.ai_security import ModelCallBudgetExceeded
from rtm_core import authority_repository, document_extraction_repository
from rtm_core import preview_repository


_CANARY = "attacker-secret://credential?token=" + ("X" * 20_000)


def _claim() -> tuple[dict, bytes]:
    pdf_bytes = b"%PDF-1.4\n%%EOF"
    return (
        {
            "case_id": "case-1",
            "already_submitted": False,
            "authority_material_sha256": "a" * 64,
            "pdf": {
                "bucket": "private-bucket",
                "key": "private-key",
                "resource_id": "resource-1",
                "preview_id": "preview-1",
                "document_id": "document-1",
                "generator_version": "generator-v1",
                "size_bytes": len(pdf_bytes),
                "content_sha256": hashlib.sha256(b"rendered-text").hexdigest(),
                "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            },
        },
        pdf_bytes,
    )


class AutomationFailureOpacityTests(unittest.TestCase):
    def test_capability_failure_does_not_disclose_runtime_configuration(self):
        state = SimpleNamespace(
            configured=False,
            reason=_CANARY,
            environment="internal-staging-name",
            env_var="SECRET_FEATURE_FLAG",
        )
        with patch.object(ops_automation, "require_http_capability", return_value=state):
            with self.assertRaises(HTTPException) as raised:
                ops_automation._require_external_submission_capability()

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(
            raised.exception.detail,
            {"code": "external_submission_unavailable"},
        )
        self.assertNotIn(_CANARY, json.dumps(raised.exception.detail))

    def test_preflight_exception_is_opaque_in_http_and_persisted_payload(self):
        claim, _pdf_bytes = _claim()
        with (
            patch.object(ops_automation, "_require_external_submission_capability"),
            patch.object(ops_automation, "_claim_case", return_value=claim),
            patch.object(
                ops_automation,
                "download_bytes_limited",
                side_effect=RuntimeError(_CANARY),
            ),
            patch.object(ops_automation, "_reset_claim") as reset_claim,
        ):
            with self.assertRaises(HTTPException) as raised:
                ops_automation.submit_case_fully_automatic("case-1")

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(
            raised.exception.detail,
            {"code": "external_submission_failed"},
        )
        stored_payload = reset_claim.call_args.args[2]
        self.assertEqual(stored_payload["error_code"], "document_preflight_failed")
        self.assertNotIn(_CANARY, json.dumps(stored_payload))

    def test_provider_exception_after_call_is_opaque_and_requires_reconciliation(self):
        claim, pdf_bytes = _claim()
        with (
            patch.object(ops_automation, "_require_external_submission_capability"),
            patch.object(ops_automation, "_claim_case", return_value=claim),
            patch.object(
                ops_automation,
                "download_bytes_limited",
                return_value=pdf_bytes,
            ),
            patch.object(ops_automation, "_revalidate_claim"),
            patch.object(
                ops_automation,
                "submit_pdf",
                side_effect=RuntimeError(_CANARY),
            ),
            patch.object(ops_automation, "_hold_claim_for_reconciliation") as hold,
        ):
            with self.assertRaises(HTTPException) as raised:
                ops_automation.submit_case_fully_automatic("case-1")

        self.assertEqual(
            raised.exception.detail,
            {"code": "external_submission_failed"},
        )
        stored_payload = hold.call_args.kwargs["payload"]
        self.assertEqual(
            stored_payload["error_code"],
            "external_submission_outcome_unknown",
        )
        self.assertNotIn(_CANARY, json.dumps(stored_payload))

    def test_not_configured_exception_text_is_not_persisted(self):
        claim, pdf_bytes = _claim()
        with (
            patch.object(ops_automation, "_require_external_submission_capability"),
            patch.object(ops_automation, "_claim_case", return_value=claim),
            patch.object(
                ops_automation,
                "download_bytes_limited",
                return_value=pdf_bytes,
            ),
            patch.object(ops_automation, "_revalidate_claim"),
            patch.object(
                ops_automation,
                "submit_pdf",
                side_effect=ops_automation.DGTNotConfigured(_CANARY),
            ),
            patch.object(
                ops_automation, "_block_unavailable_submission"
            ) as block_claim,
        ):
            with self.assertRaises(HTTPException) as raised:
                ops_automation.submit_case_fully_automatic("case-1")

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(
            raised.exception.detail,
            {"code": "external_submission_unavailable"},
        )
        stored_payload = block_claim.call_args.kwargs["payload"]
        self.assertEqual(stored_payload["error_code"], "dgt_not_configured")
        self.assertNotIn(_CANARY, json.dumps(stored_payload))

    def test_not_implemented_provider_is_blocked_and_returns_503(self):
        claim, pdf_bytes = _claim()
        with (
            patch.object(ops_automation, "_require_external_submission_capability"),
            patch.object(ops_automation, "_claim_case", return_value=claim),
            patch.object(
                ops_automation,
                "download_bytes_limited",
                return_value=pdf_bytes,
            ),
            patch.object(ops_automation, "_revalidate_claim"),
            patch.object(
                ops_automation,
                "submit_pdf",
                side_effect=NotImplementedError(_CANARY),
            ),
            patch.object(
                ops_automation, "_block_unavailable_submission"
            ) as block_claim,
        ):
            with self.assertRaises(HTTPException) as raised:
                ops_automation.submit_case_fully_automatic("case-1")

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(
            block_claim.call_args.kwargs["payload"]["error_code"],
            "dgt_not_implemented",
        )
        self.assertNotIn(_CANARY, repr(block_claim.call_args))

    def test_unavailable_provider_claim_is_not_requeued(self):
        engine = MagicMock()
        conn = engine.begin.return_value.__enter__.return_value
        with patch.object(ops_automation, "get_engine", return_value=engine):
            ops_automation._block_unavailable_submission(
                "case-1",
                event_type="dgt_submission_unavailable",
                payload={"error_code": "dgt_not_configured", "resource_id": "r-1"},
            )

        statements = "\n".join(str(call.args[0]) for call in conn.execute.call_args_list)
        self.assertIn("status='submission_blocked'", statements)
        self.assertIn("SET status='blocked'", statements)
        self.assertNotIn("status='ready_to_submit'", statements)

    def test_oversized_provider_reference_is_neither_stored_nor_reflected(self):
        claim, pdf_bytes = _claim()
        response = {
            "registro": _CANARY,
            "csv": None,
            "justificante_pdf": b"%PDF-1.4\nreceipt\n%%EOF",
        }
        with (
            patch.object(ops_automation, "_require_external_submission_capability"),
            patch.object(ops_automation, "_claim_case", return_value=claim),
            patch.object(
                ops_automation,
                "download_bytes_limited",
                return_value=pdf_bytes,
            ),
            patch.object(ops_automation, "_revalidate_claim"),
            patch.object(ops_automation, "submit_pdf", return_value=response),
            patch.object(ops_automation, "_hold_claim_for_reconciliation") as hold,
        ):
            with self.assertRaises(HTTPException) as raised:
                ops_automation.submit_case_fully_automatic("case-1")

        self.assertEqual(
            raised.exception.detail,
            {"code": "invalid_external_submission_response"},
        )
        stored_payload = hold.call_args.kwargs["payload"]
        self.assertEqual(stored_payload["error_code"], "invalid_provider_reference")
        self.assertNotIn(_CANARY, json.dumps(stored_payload))

    def test_receipt_storage_exception_is_opaque_in_http_and_database_writes(self):
        claim, pdf_bytes = _claim()
        response = {
            "registro": "REG-123",
            "csv": "CSV-123",
            "justificante_pdf": b"%PDF-1.4\nreceipt\n%%EOF",
        }
        engine = MagicMock()
        conn = engine.begin.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.return_value = ("case-1",)
        with (
            patch.object(ops_automation, "_require_external_submission_capability"),
            patch.object(ops_automation, "_claim_case", return_value=claim),
            patch.object(
                ops_automation,
                "download_bytes_limited",
                return_value=pdf_bytes,
            ),
            patch.object(ops_automation, "_revalidate_claim"),
            patch.object(ops_automation, "submit_pdf", return_value=response),
            patch.object(ops_automation, "get_engine", return_value=engine),
            patch.object(
                ops_automation,
                "upload_bytes",
                side_effect=RuntimeError(_CANARY),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                ops_automation.submit_case_fully_automatic("case-1")

        self.assertEqual(
            raised.exception.detail,
            {"code": "external_submission_receipt_storage_failed"},
        )
        self.assertNotIn(_CANARY, repr(conn.execute.call_args_list))

    def test_exact_pdf_digest_is_used_instead_of_rendered_text_digest(self):
        claim, pdf_bytes = _claim()
        self.assertNotEqual(
            claim["pdf"]["content_sha256"],
            claim["pdf"]["pdf_sha256"],
        )
        with (
            patch.object(ops_automation, "_require_external_submission_capability"),
            patch.object(ops_automation, "_claim_case", return_value=claim),
            patch.object(
                ops_automation,
                "download_bytes_limited",
                return_value=pdf_bytes,
            ),
            patch.object(ops_automation, "_revalidate_claim"),
            patch.object(
                ops_automation,
                "submit_pdf",
                side_effect=ops_automation.DGTNotConfigured("disabled"),
            ) as submit,
            patch.object(ops_automation, "_block_unavailable_submission"),
        ):
            with self.assertRaises(HTTPException) as raised:
                ops_automation.submit_case_fully_automatic("case-1")

        self.assertEqual(raised.exception.status_code, 503)
        metadata = submit.call_args.kwargs["metadata"]
        self.assertEqual(metadata["pdf_sha256"], hashlib.sha256(pdf_bytes).hexdigest())
        self.assertEqual(
            metadata["rendered_content_sha256"],
            claim["pdf"]["content_sha256"],
        )
        self.assertNotIn("content_sha256", metadata)

    def test_legacy_resource_without_pdf_digest_fails_closed(self):
        row = SimpleNamespace(_mapping={"pdf_sha256": None})
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = row

        self.assertIsNone(ops_automation._approved_core_pdf(conn, "case-1"))

    def test_pdf_digest_mismatch_never_reaches_external_submission(self):
        claim, pdf_bytes = _claim()
        claim["pdf"]["pdf_sha256"] = "f" * 64
        with (
            patch.object(ops_automation, "_require_external_submission_capability"),
            patch.object(ops_automation, "_claim_case", return_value=claim),
            patch.object(
                ops_automation,
                "download_bytes_limited",
                return_value=pdf_bytes,
            ),
            patch.object(ops_automation, "_revalidate_claim") as revalidate,
            patch.object(ops_automation, "submit_pdf") as submit,
            patch.object(ops_automation, "_reset_claim") as reset_claim,
        ):
            with self.assertRaises(HTTPException) as raised:
                ops_automation.submit_case_fully_automatic("case-1")

        self.assertEqual(raised.exception.status_code, 502)
        revalidate.assert_not_called()
        submit.assert_not_called()
        self.assertEqual(
            reset_claim.call_args.args[2]["error_code"],
            "document_preflight_failed",
        )

    def test_tick_returns_only_a_bounded_error_code(self):
        engine = MagicMock()
        conn = engine.begin.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [("case-1",)]
        with (
            patch.object(ops_automation, "_require_external_submission_capability"),
            patch.object(ops_automation, "_core_schema_ready", return_value=True),
            patch.object(ops_automation, "get_engine", return_value=engine),
            patch.object(
                ops_automation,
                "submit_case_fully_automatic",
                side_effect=RuntimeError(_CANARY),
            ),
        ):
            result = ops_automation.tick(limit=1)

        self.assertEqual(
            result["results"],
            [
                {
                    "case_id": "case-1",
                    "ok": False,
                    "error_code": "automation_case_failed",
                }
            ],
        )
        self.assertNotIn(_CANARY, json.dumps(result))

    def test_tick_never_counts_a_skipped_result_as_processed(self):
        engine = MagicMock()
        conn = engine.begin.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [("case-1",)]
        with (
            patch.object(ops_automation, "_require_external_submission_capability"),
            patch.object(ops_automation, "_core_schema_ready", return_value=True),
            patch.object(ops_automation, "get_engine", return_value=engine),
            patch.object(
                ops_automation,
                "submit_case_fully_automatic",
                return_value={
                    "ok": True,
                    "case_id": "case-1",
                    "status": "submitted",
                    "skipped": True,
                    "reason": "receipt_already_exists",
                },
            ),
        ):
            result = ops_automation.tick(limit=1)

        self.assertTrue(result["ok"])
        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["skipped"], 1)

    def test_tick_reports_provider_unavailability_as_failure(self):
        engine = MagicMock()
        conn = engine.begin.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = [("case-1",)]
        with (
            patch.object(ops_automation, "_require_external_submission_capability"),
            patch.object(ops_automation, "_core_schema_ready", return_value=True),
            patch.object(ops_automation, "get_engine", return_value=engine),
            patch.object(
                ops_automation,
                "submit_case_fully_automatic",
                side_effect=HTTPException(
                    status_code=503,
                    detail={"code": "external_submission_unavailable"},
                ),
            ),
        ):
            result = ops_automation.tick(limit=1)

        self.assertFalse(result["ok"])
        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["skipped"], 0)

    def test_tick_rejects_unbounded_direct_limits_before_database_access(self):
        with patch.object(ops_automation, "_require_external_submission_capability"):
            for value in (0, 201, True, "25"):
                with self.subTest(value=value):
                    with self.assertRaises(HTTPException) as raised:
                        ops_automation.tick(limit=value)  # type: ignore[arg-type]
                    self.assertEqual(raised.exception.status_code, 422)
                    self.assertEqual(
                        raised.exception.detail,
                        {"code": "invalid_automation_limit"},
                    )

    def test_tick_fails_closed_when_core_schema_is_missing(self):
        engine = MagicMock()
        with (
            patch.object(ops_automation, "_require_external_submission_capability"),
            patch.object(ops_automation, "_core_schema_ready", return_value=False),
            patch.object(ops_automation, "get_engine", return_value=engine),
            self.assertRaises(HTTPException) as raised,
        ):
            ops_automation.tick(limit=1)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, {"code": "core_schema_unavailable"})


class OperatorFailureOpacityTests(unittest.TestCase):
    def _assert_generation_failure_is_opaque(self, call) -> None:
        engine = MagicMock()
        with (
            patch.object(ops_operator_router, "require_operator_token"),
            patch.object(
                ops_operator_router, "load_ops_case_scope", return_value=object()
            ),
            patch.object(
                ops_operator_router, "require_case_in_scope", side_effect=lambda _conn, **kwargs: kwargs["case_id"]
            ),
            patch.object(ops_operator_router, "get_engine", return_value=engine),
            patch.object(ops_operator_router, "_case_or_404"),
            patch.object(
                ops_operator_router,
                "_save_ai_overrides_in_interested_data",
                return_value={"saved_at": "2026-09-03T00:00:00Z"},
            ),
            patch.object(ops_operator_router, "_load_interesado", return_value={}),
            patch.object(ops_operator_router, "_append_event"),
            patch.object(
                ops_operator_router,
                "generate_dgt",
                side_effect=RuntimeError(_CANARY),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                call()

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(
            raised.exception.detail,
            {"code": "resource_generation_failed"},
        )
        self.assertNotIn(_CANARY, json.dumps(raised.exception.detail))

    def test_override_regeneration_does_not_reflect_internal_exception(self):
        body = ops_operator_router.OverrideAndRegenerateBody(
            familia="velocidad",
            motivo="correccion autorizada",
        )
        self._assert_generation_failure_is_opaque(
            lambda: ops_operator_router.override_family_and_regenerate(
                "case-1",
                body,
                request=object(),
                x_operator_token="operator-token",
            )
        )

    def test_rewrite_regeneration_does_not_reflect_internal_exception(self):
        body = ops_operator_router.RewriteHechoBody(
            familia="velocidad",
            hecho="Hecho corregido por el operador",
            motivo="correccion autorizada",
        )
        self._assert_generation_failure_is_opaque(
            lambda: ops_operator_router.rewrite_hecho_and_regenerate(
                "case-1",
                body,
                request=object(),
                x_operator_token="operator-token",
            )
        )

    def test_operator_models_reject_huge_or_unknown_fields(self):
        invalid_factories = (
            lambda: ops_operator_router.ManualBody(motivo="x" * 4_001),
            lambda: ops_operator_router.RewriteHechoBody(
                hecho="x" * 20_001,
                motivo="motivo valido",
            ),
            lambda: ops_operator_router.FinalResourceBody(content="x" * 500_001),
            lambda: ops_operator_router.ApproveBody(note="ok", injected=_CANARY),
        )
        for factory in invalid_factories:
            with self.subTest(factory=factory):
                with self.assertRaises(ValidationError):
                    factory()

    def test_missing_operator_secret_does_not_name_configuration(self):
        clean_environment = dict(os.environ)
        clean_environment.pop("OPERATOR_TOKEN", None)
        with patch.dict(os.environ, clean_environment, clear=True):
            with self.assertRaises(HTTPException) as raised:
                ops_operator_router.require_operator_token("candidate")

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(
            raised.exception.detail,
            {"code": "operator_auth_unavailable"},
        )


class ReanalysisFailureOpacityTests(unittest.TestCase):
    @staticmethod
    def _image_page() -> dict:
        output = io.BytesIO()
        reanalysis.Image.new("RGB", (32, 48), "white").save(
            output,
            format="JPEG",
        )
        return {
            "analysis_content": output.getvalue(),
            "analysis_mime": "image/jpeg",
            "page_index": 1,
        }

    def test_provider_exception_text_is_not_returned_as_extraction_metadata(self):
        pages = [
            {
                "analysis_content": b"synthetic-image",
                "analysis_mime": "image/jpeg",
                "page_index": 1,
            }
        ]
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-key"}),
            patch.object(
                reanalysis,
                "_passive_openai_payload",
                side_effect=lambda payload: payload,
            ),
            patch.object(
                reanalysis.requests,
                "post",
                side_effect=RuntimeError(_CANARY),
            ),
        ):
            result = reanalysis._critical_fields_from_images(pages)

        self.assertEqual(result["error"], "provider_processing_failed")
        self.assertNotIn(_CANARY, json.dumps(result))

    def test_zoom_errors_never_expose_parser_type_or_provider_status(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-key"}):
            unreadable = reanalysis._critical_fields_from_zoomed_crops(
                [
                    {
                        "analysis_content": b"not-an-image",
                        "analysis_mime": "image/jpeg",
                        "page_index": 99,
                    }
                ]
            )
        self.assertEqual(unreadable["error"], "document_image_unreadable")
        self.assertNotIn("UnidentifiedImageError", json.dumps(unreadable))

        response = SimpleNamespace(ok=False, status_code=599)
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-key"}),
            patch.object(
                reanalysis,
                "_passive_openai_payload",
                side_effect=lambda payload: payload,
            ),
            patch.object(reanalysis.requests, "post", return_value=response),
        ):
            rejected = reanalysis._critical_fields_from_zoomed_crops(
                [self._image_page()]
            )
        self.assertEqual(rejected["error"], "provider_request_failed")
        self.assertNotIn("599", json.dumps(rejected))
        self.assertNotIn("openai", json.dumps(rejected).lower())

    def test_zoom_focal_does_not_swallow_model_budget_exhaustion(self):
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-key"}),
            patch.object(
                reanalysis,
                "_passive_openai_payload",
                side_effect=ModelCallBudgetExceeded("private budget state"),
            ),
        ):
            with self.assertRaises(ModelCallBudgetExceeded):
                reanalysis._critical_fields_from_zoomed_crops(
                    [self._image_page()]
                )


class StoredPayloadFailureOpacityTests(unittest.TestCase):
    def test_corrupt_stored_json_never_reflects_record_contents(self):
        probes = (
            lambda: authority_repository._json_payload(_CANARY, "hechos"),
            lambda: preview_repository._json_payload(_CANARY),
            lambda: document_extraction_repository._json_object(_CANARY, "paquete"),
        )
        for probe in probes:
            with self.subTest(probe=probe), self.assertRaises(HTTPException) as raised:
                probe()
            self.assertEqual(raised.exception.status_code, 500)
            self.assertNotIn(_CANARY, str(raised.exception.detail))
            self.assertLess(len(str(raised.exception.detail)), 100)


if __name__ == "__main__":
    unittest.main()
