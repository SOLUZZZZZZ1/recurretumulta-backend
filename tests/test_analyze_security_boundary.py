from __future__ import annotations

import io
import inspect
import textwrap
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException
from pypdf import PdfWriter

import analyze
from rtm_core.ai_security import ModelCallBudgetExceeded, consume_model_call_budget


def _pdf_bytes() -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


class _Upload:
    filename = "denuncia.pdf"
    content_type = "application/pdf"

    def __init__(self, data: bytes):
        self.data = data
        self.read_called = False

    async def read(self, _size: int = -1) -> bytes:
        self.read_called = True
        return self.data


class _Result:
    def scalar(self):
        return "00000000-0000-0000-0000-000000000001"


class _Connection:
    def __init__(self, engine):
        self.engine = engine

    def execute(self, *_args, **_kwargs):
        self.engine.execute_count += 1
        return _Result()


class _Engine:
    def __init__(self):
        self.transaction_open = False
        self.begin_count = 0
        self.execute_count = 0

    @contextmanager
    def begin(self):
        self.begin_count += 1
        if self.transaction_open:
            raise AssertionError("nested transaction")
        self.transaction_open = True
        try:
            yield _Connection(self)
        finally:
            self.transaction_open = False


class AnalyzeSecurityBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_public_route_contains_only_the_hardened_delegation(self):
        source = textwrap.dedent(inspect.getsource(analyze.analyze))

        self.assertIn("return await _secure_analyze_request(", source)
        self.assertNotIn("file.read(", source)
        self.assertNotIn("upload_original(", source)
        self.assertNotIn("INSERT INTO", source)

    async def test_existing_case_bridge_contains_only_hardened_delegation(self):
        source = textwrap.dedent(
            inspect.getsource(analyze.analyze_existing_case_document)
        )

        self.assertIn("return _secure_analyze_existing_case_document(", source)
        self.assertNotIn("extract_from_image_bytes(", source)
        self.assertNotIn("INSERT INTO", source)
        self.assertNotIn('"storage"', source)

    async def test_missing_consent_rejects_before_read_or_side_effect(self):
        upload = _Upload(_pdf_bytes())
        with patch.object(analyze, "get_engine") as engine:
            with self.assertRaises(HTTPException) as raised:
                await analyze._secure_analyze_request(
                    upload,
                    ai_processing_consent=False,
                    privacy_version=analyze.ANALYZE_PRIVACY_VERSION,
                )
        self.assertEqual(raised.exception.status_code, 422)
        self.assertFalse(upload.read_called)
        engine.assert_not_called()

    async def test_invalid_content_is_rejected_before_parser_db_or_storage(self):
        upload = _Upload(b"not a document")
        with (
            patch.object(analyze, "require_public_case_access_configured"),
            patch.object(analyze, "require_http_capability"),
            patch.object(analyze, "_extract_untrusted_document") as extractor,
            patch.object(analyze, "upload_original") as storage,
            patch.object(analyze, "get_engine") as engine,
        ):
            with self.assertRaises(HTTPException) as raised:
                await analyze._secure_analyze_request(
                    upload,
                    ai_processing_consent=True,
                    privacy_version=analyze.ANALYZE_PRIVACY_VERSION,
                )
        self.assertEqual(raised.exception.status_code, 415)
        extractor.assert_not_called()
        storage.assert_not_called()
        engine.assert_not_called()

    async def test_model_budget_exhaustion_is_opaque_and_has_no_side_effects(self):
        upload = _Upload(_pdf_bytes())

        def exhaust_budget(*_args):
            for _ in range(analyze.MAX_ANALYZE_MODEL_CALLS + 1):
                consume_model_call_budget()
            raise AssertionError("el presupuesto debía haber detenido la cadena")

        with (
            patch.object(analyze, "require_public_case_access_configured"),
            patch.object(analyze, "require_http_capability"),
            patch.object(
                analyze,
                "_extract_untrusted_document",
                side_effect=exhaust_budget,
            ),
            patch.object(analyze, "issue_case_access_token") as token,
            patch.object(analyze, "upload_original") as storage,
            patch.object(analyze, "get_engine") as database,
        ):
            with self.assertRaises(HTTPException) as raised:
                await analyze._secure_analyze_request(
                    upload,
                    ai_processing_consent=True,
                    privacy_version=analyze.ANALYZE_PRIVACY_VERSION,
                )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("presupuesto", str(raised.exception.detail).lower())
        token.assert_not_called()
        storage.assert_not_called()
        database.assert_not_called()

    async def test_focused_ocr_cannot_swallow_model_budget_exhaustion(self):
        cases = (
            ("image/png", "evidence.png", {"vision_raw_text": "candidate"}),
            ("application/pdf", "evidence.pdf", {"vision_raw_text": "candidate"}),
        )
        for mime, filename, vision_payload in cases:
            with self.subTest(mime=mime):
                with (
                    patch.object(analyze, "extract_text_from_pdf_bytes", return_value=""),
                    patch.object(analyze, "has_enough_text", return_value=False),
                    patch.object(
                        analyze,
                        "extract_from_image_bytes",
                        return_value=vision_payload,
                    ),
                    patch.object(analyze, "_should_run_focused_fet_ocr", return_value=True),
                    patch.object(
                        analyze,
                        "extract_fet_denunciat_focus",
                        side_effect=ModelCallBudgetExceeded("budget exhausted"),
                    ),
                ):
                    with self.assertRaises(ModelCallBudgetExceeded):
                        analyze._extract_untrusted_document(b"safe", mime, filename)

    async def test_provider_and_storage_run_without_database_transaction(self):
        upload = _Upload(_pdf_bytes())
        engine = _Engine()
        delegated = []
        real_run_in_threadpool = analyze.run_in_threadpool

        async def recording_dispatch(function, *args, **kwargs):
            delegated.append(function)
            return await real_run_in_threadpool(function, *args, **kwargs)

        def extract(*_args):
            self.assertFalse(engine.transaction_open)
            return (
                {
                    "needs_operator_review": True,
                    "evidence_status": "candidate_only",
                },
                "fake-model",
                1.0,
            )

        def store(*_args):
            self.assertFalse(engine.transaction_open)
            return "private-bucket", "private-key"

        with (
            patch.object(analyze, "require_public_case_access_configured"),
            patch.object(analyze, "require_http_capability"),
            patch.object(analyze, "_extract_untrusted_document", side_effect=extract),
            patch.object(
                analyze, "upload_original", side_effect=store
            ) as storage,
            patch.object(analyze, "get_engine", return_value=engine),
            patch.object(analyze, "issue_case_access_token", return_value="opaque-token"),
            patch.object(analyze, "run_in_threadpool", new=recording_dispatch),
        ):
            result = await analyze._secure_analyze_request(
                upload,
                ai_processing_consent=True,
                privacy_version=analyze.ANALYZE_PRIVACY_VERSION,
            )

        self.assertEqual(result["extracted"]["evidence_status"], "candidate_only")
        self.assertNotIn("storage", result["extracted"])
        self.assertNotIn("private-bucket", repr(result))
        self.assertTrue(result["extracted"]["extracted"]["needs_operator_review"])
        self.assertEqual(engine.begin_count, 1)
        self.assertEqual(engine.execute_count, 4)
        self.assertIn(analyze._extract_untrusted_document_bounded, delegated)
        self.assertIn(storage, delegated)
        self.assertIn(analyze._persist_new_analysis, delegated)

    async def test_storage_failure_creates_no_database_state(self):
        upload = _Upload(_pdf_bytes())
        with (
            patch.object(analyze, "require_public_case_access_configured"),
            patch.object(analyze, "require_http_capability"),
            patch.object(
                analyze,
                "_extract_untrusted_document",
                return_value=(
                    {"needs_operator_review": True},
                    "fake-model",
                    0.5,
                ),
            ),
            patch.object(
                analyze,
                "upload_original",
                side_effect=RuntimeError("private provider detail"),
            ),
            patch.object(analyze, "get_engine") as database,
            patch.object(analyze, "issue_case_access_token", return_value="token"),
        ):
            with self.assertRaises(HTTPException) as raised:
                await analyze._secure_analyze_request(
                    upload,
                    ai_processing_consent=True,
                    privacy_version=analyze.ANALYZE_PRIVACY_VERSION,
                )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("provider detail", str(raised.exception.detail))
        database.assert_not_called()

    async def test_persistence_failure_rolls_back_once_and_compensates_b2(self):
        upload = _Upload(_pdf_bytes())
        case_id = "11111111-1111-4111-8111-111111111111"
        connection = unittest.mock.MagicMock()
        connection.execute.side_effect = [
            SimpleNamespace(),
            SimpleNamespace(),
            RuntimeError("private database detail"),
        ]
        transaction = unittest.mock.MagicMock()
        transaction.__enter__.return_value = connection
        transaction.__exit__.return_value = False
        engine = unittest.mock.MagicMock()
        engine.begin.return_value = transaction

        with (
            patch.object(analyze, "require_public_case_access_configured"),
            patch.object(analyze, "require_http_capability"),
            patch.object(
                analyze,
                "_extract_untrusted_document",
                return_value=(
                    {"needs_operator_review": True},
                    "fake-model",
                    0.5,
                ),
            ),
            patch.object(analyze.uuid, "uuid4", return_value=UUID(case_id)),
            patch.object(analyze, "issue_case_access_token", return_value="token"),
            patch.object(
                analyze,
                "upload_original",
                return_value=(
                    "private-bucket",
                    f"cases/{case_id}/original/object.pdf",
                ),
            ),
            patch.object(
                analyze,
                "delete_object",
                side_effect=RuntimeError("private cleanup detail"),
            ) as cleanup,
            patch.object(analyze, "get_engine", return_value=engine),
        ):
            with self.assertRaises(HTTPException) as raised:
                await analyze._secure_analyze_request(
                    upload,
                    ai_processing_consent=True,
                    privacy_version=analyze.ANALYZE_PRIVACY_VERSION,
                )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("database detail", str(raised.exception.detail))
        self.assertNotIn("cleanup detail", str(raised.exception.detail))
        engine.begin.assert_called_once_with()
        cleanup.assert_called_once_with(
            "private-bucket",
            f"cases/{case_id}/original/object.pdf",
        )
        self.assertIs(transaction.__exit__.call_args.args[0], RuntimeError)


if __name__ == "__main__":
    unittest.main()
