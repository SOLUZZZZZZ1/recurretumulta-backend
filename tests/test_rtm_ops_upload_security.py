from __future__ import annotations

import hashlib
import io
import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from pypdf import PdfWriter

import ops


def _pdf_bytes() -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


class _Upload:
    def __init__(self, data: bytes, *, filename: str, content_type: str) -> None:
        self.data = data
        self.filename = filename
        self.content_type = content_type
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self.data if size < 0 else self.data[:size]


class _Connection:
    def __init__(self, rows=None) -> None:
        self.calls: list[tuple[object, object]] = []
        self.rows = list(rows or [])

    def execute(self, statement, parameters=None):
        self.calls.append((statement, parameters))
        result = MagicMock()
        result.fetchone.return_value = (
            self.rows.pop(0) if self.rows else ("document-id",)
        )
        return result


class _Transaction:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        return False


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def begin(self):
        return _Transaction(self.connection)


class OpsUploadSecurityTest(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_document_reads_at_most_limit_plus_one_and_canonicalizes(self):
        data = _pdf_bytes()
        upload = _Upload(
            data,
            filename="../../justificante.pdf",
            content_type="application/octet-stream",
        )

        content, metadata = await ops._prepare_ops_document(
            upload,
            fallback_filename="justificante.pdf",
        )

        self.assertEqual(content, data)
        self.assertEqual(upload.read_sizes, [ops.MAX_OPS_DOCUMENT_BYTES + 1])
        self.assertNotIn("/", metadata.filename)
        self.assertNotIn("\\", metadata.filename)
        self.assertEqual(metadata.mime, "application/pdf")
        self.assertEqual(metadata.extension, ".pdf")
        self.assertEqual(metadata.sha256, hashlib.sha256(data).hexdigest())

    async def test_prepare_document_rejects_oversize_with_opaque_error(self):
        upload = _Upload(
            b"x" * (ops.MAX_OPS_DOCUMENT_BYTES + 1),
            filename="too-large.pdf",
            content_type="application/pdf",
        )
        with self.assertRaises(HTTPException) as caught:
            await ops._prepare_ops_document(upload, fallback_filename="documento.pdf")

        self.assertEqual(caught.exception.status_code, 413)
        self.assertEqual(caught.exception.detail["code"], "document_too_large")
        self.assertNotIn(str(ops.MAX_OPS_DOCUMENT_BYTES), str(caught.exception.detail))

    def test_kind_allowlist_rejects_original_and_unknown_values(self):
        self.assertEqual(ops._clean_kind(" Resolucion "), "resolucion")
        for value in ("original", "../../original", "arbitrary_kind"):
            with self.subTest(value=value), self.assertRaises(HTTPException) as caught:
                ops._clean_kind(value)
            self.assertEqual(caught.exception.status_code, 422)

    def test_submitted_at_bounds_raw_whitespace_before_normalization(self):
        with self.assertRaises(HTTPException) as caught:
            ops._validated_submitted_at(" " * (ops.MAX_SUBMITTED_AT_LENGTH + 1))
        self.assertEqual(caught.exception.status_code, 422)

    async def test_invalid_kind_stops_before_file_read_database_and_storage(self):
        upload = _Upload(
            _pdf_bytes(),
            filename="receipt.pdf",
            content_type="application/pdf",
        )
        with (
            patch.dict(os.environ, {"OPERATOR_TOKEN": "operator-secret"}),
            patch.object(ops, "get_engine") as get_engine,
            patch.object(ops, "_upload_bytes") as upload_bytes,
        ):
            with self.assertRaises(HTTPException) as caught:
                await ops.upload_justificante(
                    "case-id",
                    object(),
                    x_operator_token="operator-secret",
                    file=upload,
                    kind="original",
                )

        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(upload.read_sizes, [])
        get_engine.assert_not_called()
        upload_bytes.assert_not_called()

    async def test_upload_uses_canonical_metadata_and_allowlisted_kind(self):
        data = _pdf_bytes()
        upload = _Upload(
            data,
            filename="../receipt.pdf",
            content_type="application/octet-stream",
        )
        connection = _Connection()
        with (
            patch.dict(os.environ, {"OPERATOR_TOKEN": "operator-secret"}),
            patch.object(ops, "get_engine", return_value=_Engine(connection)),
            patch.object(ops, "load_ops_case_scope", return_value=object()),
            patch.object(ops, "require_case_in_scope"),
            patch.object(ops, "_require_paid_and_authorized", return_value={}),
            patch.object(
                ops,
                "_upload_bytes",
                return_value=("private-bucket", "private-key"),
            ) as upload_bytes,
        ):
            response = await ops.upload_justificante(
                "case-id",
                object(),
                x_operator_token="operator-secret",
                file=upload,
                kind="resolucion",
            )

        upload_bytes.assert_called_once_with(
            "case-id",
            "justificantes",
            data,
            ".pdf",
            "application/pdf",
        )
        self.assertEqual(response["kind"], "resolucion")
        self.assertEqual(response["mime"], "application/pdf")
        document_parameters = connection.calls[0][1]
        self.assertEqual(document_parameters["kind"], "resolucion")
        self.assertEqual(document_parameters["mime"], "application/pdf")
        event_payload = connection.calls[1][1]["payload"]
        self.assertNotIn("../receipt.pdf", event_payload)

    async def test_storage_failure_does_not_disclose_exception(self):
        upload = _Upload(
            _pdf_bytes(),
            filename="receipt.pdf",
            content_type="application/pdf",
        )
        connection = _Connection()
        with (
            patch.dict(os.environ, {"OPERATOR_TOKEN": "operator-secret"}),
            patch.object(ops, "get_engine", return_value=_Engine(connection)),
            patch.object(ops, "load_ops_case_scope", return_value=object()),
            patch.object(ops, "require_case_in_scope"),
            patch.object(ops, "_require_paid_and_authorized", return_value={}),
            patch.object(ops, "_upload_bytes", side_effect=RuntimeError("private credential")),
        ):
            with self.assertRaises(HTTPException) as caught:
                await ops.upload_justificante(
                    "case-id",
                    object(),
                    x_operator_token="operator-secret",
                    file=upload,
                    kind="justificante_presentacion",
                )

        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail, {"code": "document_storage_unavailable"})
        self.assertNotIn("private credential", str(caught.exception.detail))
        self.assertEqual(connection.calls, [])

    async def test_manual_submission_bounds_fields_before_read_or_database(self):
        upload = _Upload(
            _pdf_bytes(),
            filename="receipt.pdf",
            content_type="application/pdf",
        )
        with (
            patch.dict(os.environ, {"OPERATOR_TOKEN": "operator-secret"}),
            patch.object(ops, "get_engine") as get_engine,
            patch.object(ops, "_upload_bytes") as upload_bytes,
        ):
            with self.assertRaises(HTTPException) as caught:
                await ops.register_manual_submission(
                    "case-id",
                    object(),
                    x_operator_token="operator-secret",
                    organismo="x" * (ops.MAX_ORGANISMO_LENGTH + 1),
                    registro="ABC-123",
                    csv=None,
                    submitted_at=None,
                    channel="dgt",
                    note=None,
                    file=upload,
                )

        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(upload.read_sizes, [])
        get_engine.assert_not_called()
        upload_bytes.assert_not_called()

    async def test_manual_submission_validates_before_using_canonical_storage_metadata(self):
        data = _pdf_bytes()
        upload = _Upload(
            data,
            filename="../../manual.pdf",
            content_type="application/octet-stream",
        )
        connection = _Connection(
            rows=[("ready_to_submit",), ("document-id",), ("case-id",)]
        )
        with (
            patch.dict(os.environ, {"OPERATOR_TOKEN": "operator-secret"}),
            patch.object(ops, "get_engine", return_value=_Engine(connection)),
            patch.object(ops, "load_ops_case_scope", return_value=object()),
            patch.object(ops, "require_case_in_scope"),
            patch.object(
                ops,
                "_require_paid_and_authorized",
                return_value={"material_sha256": "a" * 64},
            ),
            patch.object(
                ops,
                "_upload_bytes",
                return_value=("private-bucket", "private-key"),
            ) as upload_bytes,
            patch.object(ops, "_append_event") as append_event,
            patch.object(ops, "_ensure_standard_followups_after_manual_submission"),
        ):
            response = await ops.register_manual_submission(
                "case-id",
                object(),
                x_operator_token="operator-secret",
                organismo="Ayuntamiento",
                registro="REG-123",
                csv="CSV-456",
                submitted_at=None,
                channel="registro_electronico",
                note="Presentado por operador",
                file=upload,
            )

        upload_bytes.assert_called_once_with(
            "case-id",
            "manual_submission",
            data,
            ".pdf",
            "application/pdf",
        )
        self.assertEqual(response["document"]["mime"], "application/pdf")
        self.assertNotIn("/", response["document"]["filename"])
        document_parameters = connection.calls[1][1]
        self.assertEqual(document_parameters["mime"], "application/pdf")
        event_payload = append_event.call_args.args[3]
        self.assertEqual(event_payload["note"], "Presentado por operador")
        self.assertEqual(event_payload["document"]["sha256"], hashlib.sha256(data).hexdigest())


if __name__ == "__main__":
    unittest.main()
