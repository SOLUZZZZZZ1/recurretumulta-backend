from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock
from uuid import UUID

from fastapi import HTTPException

import analyze_expediente
from rtm_core.upload_security import ValidatedUpload


CASE_ID = "11111111-1111-4111-8111-111111111111"
TOKEN = "opaque-case-token"


class _Upload:
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.content_type = "application/pdf"


def _meta(filename: str, digest_char: str) -> ValidatedUpload:
    return ValidatedUpload(
        filename=filename,
        mime="application/pdf",
        extension=".pdf",
        size_bytes=1,
        sha256=digest_char * 64,
    )


class _RecordingConnection:
    def __init__(self, engine: "_RecordingEngine") -> None:
        self.engine = engine
        self.statements: list[str] = []

    def execute(self, statement, _parameters=None):
        if not self.engine.transaction_open:
            raise AssertionError("SQL ejecutado fuera de la transacción")
        self.engine.execute_count += 1
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        if self.engine.fail_on_execute == self.engine.execute_count:
            raise RuntimeError("synthetic database failure")
        return SimpleNamespace()


class _Begin:
    def __init__(self, engine: "_RecordingEngine") -> None:
        self.engine = engine

    def __enter__(self):
        if self.engine.transaction_open:
            raise AssertionError("transacción anidada")
        self.engine.transaction_open = True
        return self.engine.connection

    def __exit__(self, exc_type, _exc, _traceback):
        self.engine.transaction_open = False
        self.engine.rolled_back = exc_type is not None
        self.engine.committed = exc_type is None
        return False


class _RecordingEngine:
    def __init__(self, *, fail_on_execute: int | None = None) -> None:
        self.fail_on_execute = fail_on_execute
        self.begin_count = 0
        self.execute_count = 0
        self.transaction_open = False
        self.rolled_back = False
        self.committed = False
        self.connection = _RecordingConnection(self)

    def begin(self):
        self.begin_count += 1
        return _Begin(self)


class AnalyzeExpedienteAtomicityTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self):
        return await analyze_expediente.analyze_expediente(
            files=[_Upload("front.pdf"), _Upload("back.pdf")],
            ai_processing_consent=True,
            privacy_version=analyze_expediente.ANALYZE_PRIVACY_VERSION,
        )

    async def test_b2_gate_rejects_before_read_token_storage_or_database(self):
        unavailable = HTTPException(status_code=503, detail="B2 disabled")
        with (
            mock.patch.object(
                analyze_expediente, "require_public_case_access_configured"
            ),
            mock.patch.object(
                analyze_expediente,
                "require_http_capability",
                side_effect=unavailable,
            ) as capability,
            mock.patch.object(
                analyze_expediente, "read_upload_limited", new=mock.AsyncMock()
            ) as read_upload,
            mock.patch.object(analyze_expediente, "issue_case_access_token") as token,
            mock.patch.object(analyze_expediente, "upload_bytes") as storage,
            mock.patch.object(analyze_expediente, "get_engine") as database,
        ):
            with self.assertRaises(HTTPException) as raised:
                await self._run()

        self.assertIs(raised.exception, unavailable)
        capability.assert_called_once_with("b2")
        read_upload.assert_not_awaited()
        token.assert_not_called()
        storage.assert_not_called()
        database.assert_not_called()

    async def test_second_upload_failure_compensates_first_without_database(self):
        first_key = f"cases/{CASE_ID}/original/first.pdf"
        with (
            mock.patch.object(
                analyze_expediente, "require_public_case_access_configured"
            ),
            mock.patch.object(analyze_expediente, "require_http_capability"),
            mock.patch.object(
                analyze_expediente,
                "read_upload_limited",
                new=mock.AsyncMock(side_effect=[b"a", b"b"]),
            ),
            mock.patch.object(
                analyze_expediente,
                "validate_document_bytes",
                side_effect=[_meta("front.pdf", "a"), _meta("back.pdf", "b")],
            ),
            mock.patch.object(
                analyze_expediente.uuid, "uuid4", return_value=UUID(CASE_ID)
            ),
            mock.patch.object(
                analyze_expediente,
                "issue_case_access_token",
                return_value=TOKEN,
            ),
            mock.patch.object(
                analyze_expediente,
                "upload_bytes",
                side_effect=[
                    ("private-bucket", first_key),
                    RuntimeError("provider credential must stay private"),
                ],
            ) as storage,
            mock.patch.object(analyze_expediente, "delete_object") as delete,
            mock.patch.object(analyze_expediente, "get_engine") as database,
        ):
            with self.assertRaises(HTTPException) as raised:
                await self._run()

        self.assertEqual(raised.exception.status_code, 502)
        self.assertNotIn("credential", str(raised.exception.detail))
        self.assertEqual(storage.call_count, 2)
        delete.assert_called_once_with("private-bucket", first_key)
        database.assert_not_called()

    async def test_database_failure_rolls_back_once_and_compensates_lifo(self):
        engine = _RecordingEngine(fail_on_execute=3)
        first_key = f"cases/{CASE_ID}/original/first.pdf"
        second_key = f"cases/{CASE_ID}/original/second.pdf"
        with (
            mock.patch.object(
                analyze_expediente, "require_public_case_access_configured"
            ),
            mock.patch.object(analyze_expediente, "require_http_capability"),
            mock.patch.object(
                analyze_expediente,
                "read_upload_limited",
                new=mock.AsyncMock(side_effect=[b"a", b"b"]),
            ),
            mock.patch.object(
                analyze_expediente,
                "validate_document_bytes",
                side_effect=[_meta("front.pdf", "a"), _meta("back.pdf", "b")],
            ),
            mock.patch.object(
                analyze_expediente.uuid, "uuid4", return_value=UUID(CASE_ID)
            ),
            mock.patch.object(
                analyze_expediente,
                "issue_case_access_token",
                return_value=TOKEN,
            ),
            mock.patch.object(
                analyze_expediente,
                "upload_bytes",
                side_effect=[
                    ("private-bucket", first_key),
                    ("private-bucket", second_key),
                ],
            ),
            mock.patch.object(
                analyze_expediente,
                "delete_object",
                side_effect=[RuntimeError("synthetic cleanup failure"), None],
            ) as delete,
            mock.patch.object(analyze_expediente, "get_engine", return_value=engine),
        ):
            with self.assertRaises(HTTPException) as raised:
                await self._run()

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(engine.begin_count, 1)
        self.assertEqual(engine.execute_count, 3)
        self.assertTrue(engine.rolled_back)
        self.assertFalse(engine.committed)
        self.assertEqual(
            delete.call_args_list,
            [
                mock.call("private-bucket", second_key),
                mock.call("private-bucket", first_key),
            ],
        )

    async def test_success_uses_one_transaction_and_five_statements(self):
        engine = _RecordingEngine()
        first_key = f"cases/{CASE_ID}/original/first.pdf"
        second_key = f"cases/{CASE_ID}/original/second.pdf"
        delegated = []
        real_run_in_threadpool = analyze_expediente.run_in_threadpool

        async def recording_dispatch(function, *args, **kwargs):
            delegated.append(function)
            return await real_run_in_threadpool(function, *args, **kwargs)

        def upload(*_args):
            self.assertFalse(engine.transaction_open)
            return [
                ("private-bucket", first_key),
                ("private-bucket", second_key),
            ][upload.calls.pop(0)]

        upload.calls = [0, 1]
        with (
            mock.patch.object(
                analyze_expediente, "require_public_case_access_configured"
            ),
            mock.patch.object(analyze_expediente, "require_http_capability"),
            mock.patch.object(
                analyze_expediente,
                "read_upload_limited",
                new=mock.AsyncMock(side_effect=[b"a", b"b"]),
            ),
            mock.patch.object(
                analyze_expediente,
                "validate_document_bytes",
                side_effect=[_meta("front.pdf", "a"), _meta("back.pdf", "b")],
            ),
            mock.patch.object(
                analyze_expediente.uuid, "uuid4", return_value=UUID(CASE_ID)
            ),
            mock.patch.object(
                analyze_expediente,
                "issue_case_access_token",
                return_value=TOKEN,
            ),
            mock.patch.object(
                analyze_expediente, "upload_bytes", side_effect=upload
            ) as storage,
            mock.patch.object(analyze_expediente, "delete_object") as delete,
            mock.patch.object(analyze_expediente, "get_engine", return_value=engine),
            mock.patch.object(
                analyze_expediente,
                "run_in_threadpool",
                new=recording_dispatch,
            ),
        ):
            result = await self._run()

        self.assertTrue(result["ok"])
        self.assertEqual(result["case_id"], CASE_ID)
        self.assertEqual(result["case_access_token"], TOKEN)
        self.assertEqual(storage.call_count, 2)
        self.assertEqual(engine.begin_count, 1)
        self.assertEqual(engine.execute_count, 5)
        self.assertTrue(engine.committed)
        self.assertFalse(engine.rolled_back)
        delete.assert_not_called()
        statements = engine.connection.statements
        self.assertEqual(sum("INSERT INTO cases" in sql for sql in statements), 1)
        self.assertEqual(sum("INSERT INTO documents" in sql for sql in statements), 2)
        self.assertEqual(sum("INSERT INTO events" in sql for sql in statements), 2)
        self.assertNotIn("private-bucket", repr(result))
        self.assertNotIn(first_key, repr(result))
        self.assertNotIn(second_key, repr(result))
        self.assertEqual(delegated.count(storage), 2)
        self.assertIn(analyze_expediente._persist_expediente, delegated)


if __name__ == "__main__":
    unittest.main()
