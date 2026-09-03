from __future__ import annotations

import asyncio
import io
import unittest
from unittest import mock

from fastapi import HTTPException, UploadFile
from pypdf import PdfWriter
from starlette.datastructures import Headers

import cases


def _pdf_bytes() -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


def _upload(data: bytes, *, name: str, mime: str) -> UploadFile:
    return UploadFile(
        io.BytesIO(data),
        filename=name,
        headers=Headers({"content-type": mime}),
    )


class _Result:
    def fetchone(self):
        return ("document-id",)


class _Connection:
    def execute(self, *_args, **_kwargs):
        return _Result()


class _Begin:
    def __enter__(self):
        return _Connection()

    def __exit__(self, *_args):
        return False


class _Engine:
    def begin(self):
        return _Begin()


class _CaseReadResult:
    def fetchone(self):
        return ("traffic", "fine")


class _CaseReadConnection:
    def execute(self, *_args, **_kwargs):
        return _CaseReadResult()


class _CaseReadBegin:
    def __enter__(self):
        return _CaseReadConnection()

    def __exit__(self, *_args):
        return False


class _CaseReadEngine:
    def begin(self):
        return _CaseReadBegin()


class CasesUploadSecurityTest(unittest.TestCase):
    def test_read_is_bounded_before_content_validation_or_storage(self):
        upload = _upload(
            b"x" * 33,
            name="documento.pdf",
            mime="application/pdf",
        )
        with mock.patch.object(cases, "upload_bytes") as storage:
            with self.assertRaises(HTTPException) as rejected:
                asyncio.run(
                    cases._rtm_store_file(
                        "case-id",
                        upload,
                        "identity_front",
                        "identity",
                        max_bytes=32,
                    )
                )
        self.assertEqual(rejected.exception.status_code, 413)
        storage.assert_not_called()

    def test_valid_upload_uses_canonical_name_extension_and_mime(self):
        data = _pdf_bytes()
        upload = _upload(
            data,
            name="../../Documento.PDF",
            mime="application/octet-stream",
        )
        with (
            mock.patch.object(cases, "upload_bytes", return_value=("bucket", "key")) as storage,
            mock.patch.object(cases, "get_engine", return_value=_Engine()),
        ):
            result = asyncio.run(
                cases._rtm_store_file(
                    "case-id",
                    upload,
                    "identity_front",
                    "identity",
                )
            )

        self.assertEqual(result["mime"], "application/pdf")
        storage.assert_called_once_with(
            "case-id",
            "identity",
            data,
            ".pdf",
            "application/pdf",
        )

    def test_pdf_validation_rejects_structural_and_active_content(self):
        with self.assertRaises(HTTPException) as malformed:
            cases._validate_public_pdf(
                b"%PDF-1.7\nnot-a-pdf\n%%EOF",
                "application/pdf",
                "signed.pdf",
            )
        self.assertEqual(malformed.exception.status_code, 422)

        with self.assertRaises(HTTPException) as active:
            cases._validate_public_pdf(
                _pdf_bytes() + b"\n/JavaScript\n",
                "application/pdf",
                "signed.pdf",
            )
        self.assertIn(active.exception.status_code, {415, 422})

    def test_declared_mime_must_match_content(self):
        with self.assertRaises(HTTPException) as rejected:
            cases._validate_public_pdf(
                _pdf_bytes(),
                "text/html",
                "signed.pdf",
            )
        self.assertEqual(rejected.exception.status_code, 415)

    def test_storage_failures_are_opaque(self):
        data = _pdf_bytes()
        _content, validated = asyncio.run(
            cases._read_validated_upload(
                _upload(data, name="signed.pdf", mime="application/pdf"),
                max_bytes=cases.MAX_PUBLIC_PDF_BYTES,
                allowed_mimes={cases.PDF},
            )
        )
        with mock.patch.object(
            cases,
            "upload_bytes",
            side_effect=RuntimeError("provider-secret-and-bucket-name"),
        ):
            with self.assertRaises(HTTPException) as rejected:
                cases._rtm_store_validated_file(
                    "case-id",
                    content=data,
                    validated=validated,
                    kind="authorization_signed",
                    folder="authorization_signed",
                )
        self.assertEqual(rejected.exception.status_code, 502)
        self.assertNotIn("provider-secret", str(rejected.exception.detail))

    def test_form_text_rejects_oversize_and_control_characters(self):
        with self.assertRaises(HTTPException) as oversized:
            cases._bounded_form_text("a" * 11, field="name", max_length=10)
        self.assertEqual(oversized.exception.status_code, 422)

        with self.assertRaises(HTTPException) as whitespace_oversized:
            cases._bounded_form_text(" " * 11, field="name", max_length=10)
        self.assertEqual(whitespace_oversized.exception.status_code, 422)

        with self.assertRaises(HTTPException) as control:
            cases._bounded_form_text("hello\x00world", field="name", max_length=20)
        self.assertEqual(control.exception.status_code, 422)

    def test_intake_validates_both_identity_files_before_any_effect(self):
        kwargs = {
            "department": "traffic",
            "case_type": "fine",
            "source_module": "rtm_web",
            "public_service_family": "trafico",
            "full_name": "Nombre Apellidos",
            "dni_nie": "12345678Z",
            "domicilio_notif": "Calle Uno 1",
            "street": "Calle Uno",
            "street_number": "1",
            "floor": "",
            "door": "",
            "postal_code": "28001",
            "city": "Madrid",
            "province": "Madrid",
            "email": "person@example.test",
            "telefono": "600000000",
            "preferred_contact": "email",
            "customer_comment": "",
            "representation_confirmed": True,
            "prejudicial_counsel_requested": False,
            "privacy_accepted": True,
            "dni_front": _upload(
                _pdf_bytes(), name="front.pdf", mime="application/pdf"
            ),
            "dni_back": _upload(
                b"not-a-document", name="back.pdf", mime="application/pdf"
            ),
        }
        with (
            mock.patch.object(cases, "require_public_case_access_configured"),
            mock.patch.object(cases, "require_http_capability"),
            mock.patch.object(cases, "get_engine") as database,
            mock.patch.object(cases, "upload_bytes") as storage,
            mock.patch.object(cases, "issue_case_access_token") as token,
        ):
            with self.assertRaises(HTTPException):
                asyncio.run(cases.create_rtm_intake_draft(**kwargs))
        database.assert_not_called()
        storage.assert_not_called()
        token.assert_not_called()

    def test_intake_compensates_first_object_when_second_upload_fails(self):
        kwargs = {
            "department": "traffic",
            "case_type": "fine",
            "source_module": "rtm_web",
            "public_service_family": "trafico",
            "full_name": "Nombre Apellidos",
            "dni_nie": "12345678Z",
            "domicilio_notif": "Calle Uno 1",
            "street": "Calle Uno",
            "street_number": "1",
            "floor": "",
            "door": "",
            "postal_code": "28001",
            "city": "Madrid",
            "province": "Madrid",
            "email": "person@example.test",
            "telefono": "600000000",
            "preferred_contact": "email",
            "customer_comment": "",
            "representation_confirmed": True,
            "prejudicial_counsel_requested": False,
            "privacy_accepted": True,
            "dni_front": _upload(
                _pdf_bytes(), name="front.pdf", mime="application/pdf"
            ),
            "dni_back": _upload(
                _pdf_bytes(), name="back.pdf", mime="application/pdf"
            ),
        }
        first = ("bucket", "cases/case-id/identity/first.pdf")
        with (
            mock.patch.object(cases, "require_public_case_access_configured"),
            mock.patch.object(cases, "require_http_capability"),
            mock.patch.object(
                cases,
                "issue_case_access_token",
                return_value="opaque-token",
            ),
            mock.patch.object(
                cases,
                "upload_bytes",
                side_effect=[first, RuntimeError("ambiguous storage failure")],
            ),
            mock.patch.object(cases, "delete_object") as cleanup,
            mock.patch.object(cases, "get_engine") as database,
        ):
            with self.assertRaises(HTTPException) as rejected:
                asyncio.run(cases.create_rtm_intake_draft(**kwargs))

        self.assertEqual(rejected.exception.status_code, 502)
        cleanup.assert_called_once_with(*first)
        database.assert_not_called()

    def test_intake_database_failure_rolls_back_once_and_compensates_b2(self):
        kwargs = {
            "department": "traffic",
            "case_type": "fine",
            "source_module": "rtm_web",
            "public_service_family": "trafico",
            "full_name": "Nombre Apellidos",
            "dni_nie": "12345678Z",
            "domicilio_notif": "Calle Uno 1",
            "street": "Calle Uno",
            "street_number": "1",
            "floor": "",
            "door": "",
            "postal_code": "28001",
            "city": "Madrid",
            "province": "Madrid",
            "email": "person@example.test",
            "telefono": "600000000",
            "preferred_contact": "email",
            "customer_comment": "",
            "representation_confirmed": True,
            "prejudicial_counsel_requested": False,
            "privacy_accepted": True,
            "dni_front": _upload(
                _pdf_bytes(), name="front.pdf", mime="application/pdf"
            ),
            "dni_back": _upload(
                _pdf_bytes(), name="back.pdf", mime="application/pdf"
            ),
        }
        first = ("bucket", "cases/case-id/identity/first.pdf")
        second = ("bucket", "cases/case-id/identity/second.pdf")
        connection = mock.MagicMock()
        connection.execute.side_effect = RuntimeError("database unavailable")
        transaction = mock.MagicMock()
        transaction.__enter__.return_value = connection
        engine = mock.MagicMock()
        engine.begin.return_value = transaction
        delegated = []
        real_run_in_threadpool = cases.run_in_threadpool

        async def recording_dispatch(function, *args, **kwargs):
            delegated.append(function)
            return await real_run_in_threadpool(function, *args, **kwargs)

        with (
            mock.patch.object(cases, "require_public_case_access_configured"),
            mock.patch.object(cases, "require_http_capability"),
            mock.patch.object(
                cases,
                "issue_case_access_token",
                return_value="opaque-token",
            ),
            mock.patch.object(
                cases,
                "upload_bytes",
                side_effect=[first, second],
            ) as storage,
            mock.patch.object(cases, "delete_object") as cleanup,
            mock.patch.object(cases, "get_engine", return_value=engine),
            mock.patch.object(
                cases,
                "run_in_threadpool",
                new=recording_dispatch,
            ),
        ):
            with self.assertRaises(HTTPException) as rejected:
                asyncio.run(cases.create_rtm_intake_draft(**kwargs))

        self.assertEqual(rejected.exception.status_code, 503)
        engine.begin.assert_called_once_with()
        self.assertEqual(
            cleanup.call_args_list,
            [mock.call(*second), mock.call(*first)],
        )
        self.assertEqual(delegated.count(storage), 2)
        self.assertIn(cases._persist_rtm_intake_draft, delegated)
        self.assertIn(cases._cleanup_b2_objects, delegated)

    def test_append_validates_entire_batch_before_event_or_storage(self):
        uploads = [
            _upload(_pdf_bytes(), name="first.pdf", mime="application/pdf"),
            _upload(b"not-a-document", name="second.pdf", mime="application/pdf"),
        ]
        with (
            mock.patch.object(
                cases, "require_case_access_token", return_value="case-id"
            ),
            mock.patch.object(cases, "_case_exists", return_value={}),
            mock.patch.object(cases, "get_engine", return_value=_CaseReadEngine()),
            mock.patch.object(cases, "_event") as event,
            mock.patch.object(cases, "upload_bytes") as storage,
        ):
            with self.assertRaises(HTTPException):
                asyncio.run(
                    cases.append_documents(
                        "case-id",
                        uploads,
                        "case-token",
                    )
                )
        event.assert_not_called()
        storage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
