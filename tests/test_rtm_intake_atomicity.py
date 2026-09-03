from __future__ import annotations

import unittest
from unittest import mock

from fastapi import HTTPException

from rtm_core import intake_router
from rtm_core.upload_security import ValidatedUpload


CASE_ID = "11111111-1111-4111-8111-111111111111"


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


class _Result:
    def __init__(self, row=None) -> None:
        self.row = row

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, engine: "_Engine") -> None:
        self.engine = engine

    def execute(self, statement, _parameters=None):
        if not self.engine.transaction_open:
            raise AssertionError("SQL fuera de la transacción")
        self.engine.execute_count += 1
        sql = " ".join(str(statement).split())
        self.engine.statements.append(sql)
        if self.engine.fail_on_execute == self.engine.execute_count:
            raise RuntimeError("synthetic final transaction failure")
        if "SELECT COALESCE(payment_status" in sql:
            return _Result(("", "documents_received"))
        if "SELECT id, mime, size_bytes" in sql:
            return _Result(None)
        if "INSERT INTO documents" in sql:
            self.engine.document_count += 1
            return _Result((f"document-{self.engine.document_count}",))
        return _Result()


class _Begin:
    def __init__(self, engine: "_Engine") -> None:
        self.engine = engine

    def __enter__(self):
        self.engine.transaction_open = True
        return self.engine.connection

    def __exit__(self, exc_type, _exc, _traceback):
        self.engine.transaction_open = False
        self.engine.rolled_back = exc_type is not None
        self.engine.committed = exc_type is None
        return False


class _Engine:
    def __init__(self, *, fail_on_execute: int | None = None) -> None:
        self.fail_on_execute = fail_on_execute
        self.begin_count = 0
        self.execute_count = 0
        self.document_count = 0
        self.transaction_open = False
        self.rolled_back = False
        self.committed = False
        self.statements: list[str] = []
        self.connection = _Connection(self)

    def begin(self):
        self.begin_count += 1
        return _Begin(self)


class IntakeAtomicityTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self):
        return await intake_router.append_documents_core(
            CASE_ID,
            [_Upload("first.pdf"), _Upload("second.pdf")],
            "case-token",
        )

    async def test_second_upload_failure_compensates_first_without_commit(self):
        first = ("private-bucket", f"cases/{CASE_ID}/original/first.pdf")
        with (
            mock.patch.object(
                intake_router, "require_case_access_token", return_value=CASE_ID
            ),
            mock.patch.object(intake_router, "require_http_capability"),
            mock.patch.object(
                intake_router,
                "read_upload_limited",
                new=mock.AsyncMock(side_effect=[b"a", b"b"]),
            ),
            mock.patch.object(
                intake_router,
                "_validate_upload",
                side_effect=[_meta("first.pdf", "a"), _meta("second.pdf", "b")],
            ),
            mock.patch.object(
                intake_router, "_existing_original_hashes", return_value=set()
            ),
            mock.patch.object(
                intake_router,
                "upload_bytes",
                side_effect=[first, RuntimeError("private B2 detail")],
            ),
            mock.patch.object(intake_router, "delete_object") as cleanup,
            mock.patch.object(intake_router, "_commit_appended_documents") as commit,
        ):
            with self.assertRaises(HTTPException) as raised:
                await self._run()

        self.assertEqual(raised.exception.status_code, 502)
        self.assertNotIn("B2 detail", str(raised.exception.detail))
        cleanup.assert_called_once_with(*first)
        commit.assert_not_called()

    async def test_final_transaction_failure_rolls_back_and_cleans_lifo(self):
        first = ("private-bucket", f"cases/{CASE_ID}/original/first.pdf")
        second = ("private-bucket", f"cases/{CASE_ID}/original/second.pdf")
        # case lock + 2*(duplicate check + document insert + document event)
        # + status update; the final batch event is execute number 9.
        engine = _Engine(fail_on_execute=9)
        delegated = []
        real_run_in_threadpool = intake_router.run_in_threadpool

        async def recording_dispatch(function, *args, **kwargs):
            delegated.append(function)
            return await real_run_in_threadpool(function, *args, **kwargs)

        with (
            mock.patch.object(
                intake_router, "require_case_access_token", return_value=CASE_ID
            ),
            mock.patch.object(intake_router, "require_http_capability"),
            mock.patch.object(
                intake_router,
                "read_upload_limited",
                new=mock.AsyncMock(side_effect=[b"a", b"b"]),
            ),
            mock.patch.object(
                intake_router,
                "_validate_upload",
                side_effect=[_meta("first.pdf", "a"), _meta("second.pdf", "b")],
            ),
            mock.patch.object(
                intake_router, "_existing_original_hashes", return_value=set()
            ) as preflight,
            mock.patch.object(
                intake_router, "upload_bytes", side_effect=[first, second]
            ) as storage,
            mock.patch.object(intake_router, "delete_object") as cleanup,
            mock.patch.object(
                intake_router, "_invalidate_authority_after_new_document", return_value=[]
            ),
            mock.patch.object(intake_router, "get_engine", return_value=engine),
            mock.patch.object(
                intake_router, "run_in_threadpool", new=recording_dispatch
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await self._run()

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(engine.begin_count, 1)
        self.assertTrue(engine.rolled_back)
        self.assertFalse(engine.committed)
        self.assertEqual(
            cleanup.call_args_list,
            [mock.call(*second), mock.call(*first)],
        )
        self.assertIn(preflight, delegated)
        self.assertEqual(delegated.count(storage), 2)
        self.assertIn(intake_router._commit_appended_documents, delegated)
        self.assertIn(intake_router._cleanup_b2_objects, delegated)


if __name__ == "__main__":
    unittest.main()
