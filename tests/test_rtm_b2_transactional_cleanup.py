from __future__ import annotations

import asyncio
import io
import types
import unittest
from unittest import mock

from fastapi import HTTPException, UploadFile
from pypdf import PdfWriter
from starlette.datastructures import Headers

import authorization_pdf
import cases
import generate
import ops
import ops_automation
import ops_operator_router
import partner
from rtm_core import generation_router


def _pdf_bytes() -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


def _pdf_upload() -> UploadFile:
    return UploadFile(
        io.BytesIO(_pdf_bytes()),
        filename="signed.pdf",
        headers=Headers({"content-type": "application/pdf"}),
    )


class _Result:
    def __init__(self, row=("document-id",)) -> None:
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, *, interested=None) -> None:
        self.interested = interested
        self.calls = 0

    def execute(self, statement, parameters=None):
        self.calls += 1
        sql = " ".join(str(statement).split())
        if "SELECT COALESCE(interested_data" in sql:
            return _Result((self.interested, "traffic", "fine", "authorization_pending"))
        if "INSERT INTO cases" in sql:
            return _Result((parameters["id"],))
        return _Result()


class _Transaction:
    def __init__(self, connection, *, fail_commit=False) -> None:
        self.connection = connection
        self.fail_commit = fail_commit

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, _exc, _traceback):
        if exc_type is None and self.fail_commit:
            raise RuntimeError("synthetic commit failure")
        return False


class _Engine:
    def __init__(self, transactions) -> None:
        self.transactions = list(transactions)
        self.begin_calls = 0

    def begin(self):
        self.begin_calls += 1
        return self.transactions.pop(0)


class B2TransactionalCleanupTest(unittest.TestCase):
    def test_signed_authorization_commit_failure_deletes_uploaded_object(self):
        first = _Transaction(_Connection())
        replay_check = _Transaction(_Connection())
        second = _Transaction(_Connection(), fail_commit=True)
        engine = _Engine([first, replay_check, second])
        coordinate = ("bucket", "cases/case/authorization/signed.pdf")

        with (
            mock.patch.object(cases, "require_case_access_token", return_value="case"),
            mock.patch.object(cases, "get_engine", return_value=engine),
            mock.patch.object(cases, "verify_active_case_authority", return_value={}),
            mock.patch.object(
                cases, "verify_active_authority_document_issue", return_value={}
            ),
            mock.patch.object(cases, "require_authority_document_binding"),
            mock.patch.object(cases, "require_authorization_candidate_digest_unused"),
            mock.patch.object(cases, "_validate_public_pdf", return_value="a" * 64),
            mock.patch.object(cases, "upload_bytes", return_value=coordinate),
            mock.patch.object(
                cases,
                "build_authorization_signature_candidate_attestation",
                return_value={"material_sha256": "c" * 64},
            ),
            mock.patch.object(cases, "_event_on_conn"),
            mock.patch.object(cases, "delete_object") as cleanup,
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    cases._store_authorization_signed(
                        "case",
                        _pdf_upload(),
                        "case-token",
                        authority_material_sha256="a" * 64,
                        generated_document_id="11111111-1111-4111-8111-111111111111",
                        generated_document_sha256="b" * 64,
                        generated_document_version="v1_dgt_homologado",
                        document_nonce="22222222-2222-4222-8222-222222222222",
                        issuance_attestation_sha256="c" * 64,
                    )
                )

        self.assertEqual(raised.exception.status_code, 503)
        cleanup.assert_called_once_with(*coordinate)

    def test_authorize_unit_of_work_cleans_pdf_when_commit_fails(self):
        interested = {
            "full_name": "Persona",
            "dni_nie": "12345678Z",
            "domicilio_notif": "Calle Uno",
            "email": "person@example.test",
        }
        engine = _Engine(
            [_Transaction(_Connection(interested=interested), fail_commit=True)]
        )
        coordinate = ("bucket", "cases/case/authorization/generated.pdf")

        def fake_ensure(_conn, case_id, request, **kwargs):
            del case_id, request
            kwargs["uploaded_coordinates"].append(coordinate)
            return {"document": {"id": "document-id"}}

        with (
            mock.patch.object(cases, "get_request_ip", return_value="203.0.113.10"),
            mock.patch.object(
                cases,
                "build_case_authority_payload",
                return_value={
                    "material": {"authority_id": "authority-id"},
                    "material_sha256": "b" * 64,
                },
            ),
            mock.patch.object(cases, "ensure_authorization_pdf", new=fake_ensure),
            mock.patch.object(cases, "_event_on_conn"),
            mock.patch.object(cases, "delete_object") as cleanup,
        ):
            with self.assertRaises(Exception):
                cases._authorize_case_transaction(
                    engine,
                    case_id="case",
                    request=object(),
                    authority_version="v1",
                )

        cleanup.assert_called_once_with(*coordinate)

    def test_authorization_repository_failure_cleans_its_own_upload(self):
        coordinate = ("bucket", "cases/case/authorization/generated.pdf")

        class FailingConnection(_Connection):
            def execute(self, statement, parameters=None):
                if self.calls:
                    raise RuntimeError("synthetic insert failure")
                self.calls += 1
                return _Result(("case",))

        with (
            mock.patch.object(authorization_pdf, "_existing_authorization_doc", return_value=None),
            mock.patch.object(authorization_pdf, "get_request_ip", return_value="203.0.113.10"),
            mock.patch.object(authorization_pdf, "_get_case_snapshot", return_value={}),
            mock.patch.object(
                authorization_pdf,
                "_authorization_payload_from_case",
                return_value={"authorized_at": "2026-09-03T00:00:00+00:00"},
            ),
            mock.patch.object(authorization_pdf, "generate_authorization_pdf", return_value=_pdf_bytes()),
            mock.patch.object(authorization_pdf, "upload_bytes", return_value=coordinate),
            mock.patch.object(authorization_pdf, "delete_object") as cleanup,
        ):
            with self.assertRaisesRegex(RuntimeError, "insert failure"):
                authorization_pdf.ensure_authorization_pdf(
                    FailingConnection(),
                    "case",
                    object(),
                    authority_payload={"material": {}, "material_sha256": "a" * 64},
                )

        cleanup.assert_called_once_with(*coordinate)

    def test_partner_batch_second_upload_failure_compensates_first(self):
        auth = types.SimpleNamespace(
            extension=".pdf",
            mime="application/pdf",
            filename="authorization.pdf",
            sha256="a" * 64,
        )
        original = types.SimpleNamespace(
            extension=".pdf",
            mime="application/pdf",
            filename="original.pdf",
            sha256="b" * 64,
        )
        coordinate = ("bucket", "cases/case/authorization/signed.pdf")
        engine = mock.Mock()
        with (
            mock.patch.object(
                partner,
                "upload_bytes",
                side_effect=[coordinate, RuntimeError("synthetic B2 failure")],
            ),
            mock.patch.object(partner, "delete_object") as cleanup,
        ):
            with self.assertRaisesRegex(RuntimeError, "B2 failure"):
                partner._persist_partner_case(
                    engine,
                    partner={"id": "partner-id", "name": "Partner"},
                    client_email=None,
                    client_name="",
                    partner_note="",
                    interesado={},
                    auth_data=_pdf_bytes(),
                    auth_meta=auth,
                    prepared_files=[(_pdf_bytes(), original)],
                )

        cleanup.assert_called_once_with(*coordinate)
        engine.begin.assert_not_called()

    def test_partner_commit_failure_rolls_back_once_and_cleans_entire_batch(self):
        auth = types.SimpleNamespace(
            extension=".pdf",
            mime="application/pdf",
            filename="authorization.pdf",
            sha256="a" * 64,
        )
        original = types.SimpleNamespace(
            extension=".pdf",
            mime="application/pdf",
            filename="original.pdf",
            sha256="b" * 64,
        )
        first = ("bucket", "cases/case/authorization/signed.pdf")
        second = ("bucket", "cases/case/original/document.pdf")
        engine = _Engine([_Transaction(_Connection(), fail_commit=True)])
        with (
            mock.patch.object(partner, "upload_bytes", side_effect=[first, second]),
            mock.patch.object(partner, "delete_object") as cleanup,
        ):
            with self.assertRaisesRegex(RuntimeError, "commit failure"):
                partner._persist_partner_case(
                    engine,
                    partner={"id": "partner-id", "name": "Partner"},
                    client_email=None,
                    client_name="",
                    partner_note="",
                    interesado={},
                    auth_data=_pdf_bytes(),
                    auth_meta=auth,
                    prepared_files=[(_pdf_bytes(), original)],
                )

        self.assertEqual(engine.begin_calls, 1)
        self.assertEqual(
            cleanup.call_args_list,
            [mock.call(*second), mock.call(*first)],
        )

    def test_ops_commit_failure_compensates_justificante(self):
        coordinate = ("bucket", "cases/case/justificantes/receipt.pdf")
        engine = _Engine([_Transaction(_Connection(), fail_commit=True)])
        upload = types.SimpleNamespace(
            extension=".pdf",
            mime="application/pdf",
            filename="receipt.pdf",
            sha256="c" * 64,
        )
        with (
            mock.patch.object(ops, "load_ops_case_scope", return_value=object()),
            mock.patch.object(ops, "require_case_in_scope"),
            mock.patch.object(ops, "_require_paid_and_authorized", return_value={}),
            mock.patch.object(ops, "_upload_bytes", return_value=coordinate),
            mock.patch.object(ops, "delete_object") as cleanup,
        ):
            with self.assertRaisesRegex(RuntimeError, "commit failure"):
                ops._persist_ops_justificante(
                    engine,
                    case_id="case",
                    request=object(),
                    kind="resolucion",
                    data=_pdf_bytes(),
                    upload=upload,
                )

        cleanup.assert_called_once_with(*coordinate)

    def test_generation_route_commit_failure_compensates_both_objects(self):
        first = ("bucket", "cases/case/generated/resource.docx")
        second = ("bucket", "cases/case/generated/resource.pdf")
        engine = _Engine([_Transaction(_Connection(), fail_commit=True)])

        def fake_generate(_conn, **kwargs):
            kwargs["uploaded_coordinates"].extend([first, second])
            return types.SimpleNamespace(model_dump=lambda **_kwargs: {})

        with (
            mock.patch.object(generation_router, "_operator", return_value="operator"),
            mock.patch.object(generation_router, "get_engine", return_value=engine),
            mock.patch.object(
                generation_router, "load_ops_case_scope", return_value=object()
            ),
            mock.patch.object(
                generation_router,
                "require_case_in_scope",
                return_value="case",
            ),
            mock.patch.object(
                generation_router,
                "generate_from_frozen_preview",
                new=fake_generate,
            ),
            mock.patch.object(
                generation_router, "cleanup_generated_uploads"
            ) as cleanup,
        ):
            with self.assertRaisesRegex(RuntimeError, "commit failure"):
                generation_router.generate_case_from_frozen_preview(
                    "case",
                    "preview",
                    object(),
                    x_operator_token="token",
                    x_operator_actor="operator",
                )

        cleanup.assert_called_once_with([first, second])

    def test_automation_transaction_commit_failure_compensates_receipt(self):
        coordinate = ("bucket", "cases/case/justificantes/receipt.pdf")
        engine = _Engine([_Transaction(_Connection(), fail_commit=True)])
        with mock.patch.object(ops_automation, "delete_object") as cleanup:
            with self.assertRaisesRegex(RuntimeError, "commit failure"):
                with ops_automation._b2_backed_transaction(engine, coordinate):
                    pass
        cleanup.assert_called_once_with(*coordinate)

    def test_operator_final_resource_partial_upload_is_compensated(self):
        first = ("bucket", "cases/case/final/resource.txt")
        engine = _Engine([_Transaction(_Connection())])
        with (
            mock.patch.object(ops_operator_router, "require_operator_token"),
            mock.patch.object(
                ops_operator_router, "load_ops_case_scope", return_value=object()
            ),
            mock.patch.object(
                ops_operator_router, "require_case_in_scope", side_effect=lambda _conn, **kwargs: kwargs["case_id"]
            ),
            mock.patch.object(
                ops_operator_router,
                "_trusted_operator_actor",
                return_value="operator-id",
            ),
            mock.patch.object(ops_operator_router, "get_engine", return_value=engine),
            mock.patch.object(ops_operator_router, "_case_or_404"),
            mock.patch.object(
                ops_operator_router, "_next_final_resource_version", return_value=1
            ),
            mock.patch.object(ops_operator_router, "build_docx", return_value=b"docx"),
            mock.patch.object(ops_operator_router, "build_pdf", return_value=b"pdf"),
            mock.patch.object(
                ops_operator_router,
                "upload_bytes",
                side_effect=[first, RuntimeError("synthetic B2 failure")],
            ),
            mock.patch.object(ops_operator_router, "delete_object") as cleanup,
        ):
            with self.assertRaisesRegex(RuntimeError, "B2 failure"):
                ops_operator_router.finalize_resource(
                    "case",
                    ops_operator_router.FinalResourceBody(content="Recurso final"),
                    object(),
                    x_operator_token="token",
                )

        cleanup.assert_called_once_with(*first)

    def test_legacy_generator_callable_compensates_partial_resource_pair(self):
        coordinate = ("bucket", "cases/case/generated/resource.docx")
        engine = _Engine([_Transaction(_Connection())])

        def fake_generate(_conn, _case_id, **kwargs):
            kwargs["uploaded_coordinates"].append(coordinate)
            raise RuntimeError("synthetic PDF upload failure")

        with (
            mock.patch.object(generate, "get_engine", return_value=engine),
            mock.patch.object(generate, "generate_dgt_for_case", new=fake_generate),
            mock.patch.object(generate, "delete_object") as cleanup,
        ):
            with self.assertRaisesRegex(RuntimeError, "PDF upload failure"):
                generate.generate_dgt(
                    generate.GenerateRequest(case_id="case", interesado={})
                )

        cleanup.assert_called_once_with(*coordinate)


if __name__ == "__main__":
    unittest.main()
