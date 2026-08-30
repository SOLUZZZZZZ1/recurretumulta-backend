from __future__ import annotations

import asyncio
import importlib.util
import inspect
import io
import sys
import types
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from uuid import UUID

from fastapi import HTTPException

import ops
from rtm_presenter_contracts import RTM_PRESENTER_MAX_FILE_BYTES, PresenterClientKind
from rtm_presenter_policy import (
    PRESENTER_DOCUMENT_INGEST_PERMISSION,
    PresenterActorContext,
    PresenterPolicyError,
    PresenterRuntimeConfiguration,
)
from rtm_presenter_service import (
    PresenterConflict,
    PresenterExternalDocumentUpload,
    PresenterForbidden,
    PresenterService,
    PresenterServiceError,
    SqlPresenterRepository,
    validate_external_document_upload,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 28, 18, 8, tzinfo=timezone.utc)
CASE_ID = "00000000-0000-4000-8000-000000000001"
SOURCE_DOCUMENT_ID = "00000000-0000-4000-8000-000000000002"
LOGICAL_DOCUMENT_ID = "00000000-0000-4000-8000-000000000003"
DOCUMENT_VERSION_ID = "00000000-0000-4000-8000-000000000004"
PREDECESSOR_ID = "00000000-0000-4000-8000-000000000005"
OPERATOR_ID = "00000000-0000-4000-8000-000000000006"
SESSION_ID = "00000000-0000-4000-8000-000000000007"
PDF = b"%PDF-1.7\nSynthetic external evidence\n%%EOF"


def _runtime() -> PresenterRuntimeConfiguration:
    return PresenterRuntimeConfiguration(
        enabled=True,
        environment="staging",
        synthetic_only=True,
        real_data_allowed=False,
        external_effects_allowed=False,
        direct_storage_allowed=False,
    )


def _actor(*, permission: bool = True) -> PresenterActorContext:
    return PresenterActorContext(
        operator_id=OPERATOR_ID,
        operator_session_id=SESSION_ID,
        permissions=(PRESENTER_DOCUMENT_INGEST_PERMISSION,) if permission else (),
        role_codes=("rtm.operator",),
        client_kind=PresenterClientKind.OPERATOR_UI,
        authenticated_at=NOW - timedelta(hours=1),
    )


def _docx() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            (
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Override PartName="/word/document.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.'
                'wordprocessingml.document.main+xml"/></Types>'
            ),
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
        )
    return buffer.getvalue()


def _png() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + (1).to_bytes(4, "big")
        + (1).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00"
        + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


class _IngestRepository:
    def __init__(self) -> None:
        self.insert_calls: list[dict] = []
        self.audits: list[dict] = []
        self.case_access = True

    @staticmethod
    def presenter_schema_ready(conn) -> bool:
        del conn
        return True

    def has_active_synthetic_case_access(self, conn, *, case_id, operator_id) -> bool:
        del conn
        return (
            self.case_access
            and case_id == CASE_ID
            and operator_id == OPERATOR_ID
        )

    def insert_external_document_version(self, conn, **kwargs):
        del conn
        self.insert_calls.append(dict(kwargs))
        upload: PresenterExternalDocumentUpload = kwargs["upload"]
        return {
            "id": DOCUMENT_VERSION_ID,
            "case_id": CASE_ID,
            "logical_document_id": LOGICAL_DOCUMENT_ID,
            "version_number": 1,
            "sha256": upload.sha256,
            "purpose": upload.purpose,
            "state": "review",
            "scan_status": "pending",
            "original_filename": upload.original_filename,
            "detected_mime": upload.media_type,
            "size_bytes": upload.size_bytes,
            "source_kind": "external_revision",
        }

    def append_audit(self, conn, **kwargs) -> None:
        del conn
        self.audits.append(dict(kwargs))


class ExternalDocumentValidationTest(unittest.TestCase):
    def test_allowlist_checks_real_signatures_and_basic_structure(self):
        fixtures = (
            (PDF, "resource.pdf", "application/pdf"),
            (_docx(), "resource.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            (
                b"\xff\xd8\xff\xe0synthetic\xff\xc0frame\xff\xdascan\xff\xd9",
                "evidence.jpeg",
                "image/jpeg",
            ),
            (_png(), "evidence.png", "image/png"),
        )
        for content, filename, media_type in fixtures:
            with self.subTest(media_type=media_type):
                upload = validate_external_document_upload(
                    content=content,
                    original_filename=filename,
                    declared_mime=media_type,
                    purpose="supporting_evidence",
                )
                self.assertEqual(upload.media_type, media_type)
                self.assertEqual(upload.size_bytes, len(content))
                self.assertRegex(upload.sha256, r"^[0-9a-f]{64}$")

    def test_spoofed_mime_extension_and_open_purpose_are_rejected(self):
        attempts = (
            {"declared_mime": "image/png", "original_filename": "fake.png", "purpose": "supporting_evidence"},
            {"declared_mime": "application/pdf", "original_filename": "fake.exe", "purpose": "supporting_evidence"},
            {"declared_mime": "application/pdf", "original_filename": "fake.pdf", "purpose": "free_form_note"},
        )
        for attempt in attempts:
            with self.subTest(attempt=attempt):
                with self.assertRaises(PresenterConflict):
                    validate_external_document_upload(content=PDF, **attempt)

    def test_filename_is_sanitized_before_persistence(self):
        upload = validate_external_document_upload(
            content=PDF,
            original_filename="Resolucion_sancionadora_DGT.pdf",
            source_original_filename="../../external revision.pdf",
            declared_mime="application/pdf",
            purpose="main_filing",
        )
        self.assertNotIn("/", upload.original_filename)
        self.assertNotIn("\\", upload.original_filename)
        self.assertTrue(upload.original_filename.endswith(".pdf"))
        self.assertEqual(upload.original_filename, "Resolucion_sancionadora_DGT.pdf")
        self.assertNotIn("/", upload.source_original_filename)
        self.assertNotIn("\\", upload.source_original_filename)
        self.assertTrue(upload.source_original_filename.endswith(".pdf"))

    def test_limit_is_strictly_25_mib(self):
        with self.assertRaises(PresenterConflict) as raised:
            validate_external_document_upload(
                content=b"x" * (RTM_PRESENTER_MAX_FILE_BYTES + 1),
                original_filename="oversized.pdf",
                declared_mime="application/pdf",
                purpose="supporting_evidence",
            )
        self.assertEqual(
            raised.exception.code,
            "presenter.external_document_too_large",
        )


class ExternalDocumentServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = _IngestRepository()
        self.service = PresenterService(repository=self.repository, runtime=_runtime())
        self.storage_calls: list[PresenterExternalDocumentUpload] = []
        self.cleanups: list[tuple[str, str]] = []

    def _store(
        self,
        upload: PresenterExternalDocumentUpload,
        register_rollback_cleanup,
    ) -> tuple[str, str]:
        self.storage_calls.append(upload)
        register_rollback_cleanup("internal-bucket", "internal/case/object.pdf")
        return "internal-bucket", "internal/case/object.pdf"

    def test_ingest_is_pending_ineligible_audited_and_storage_safe(self):
        document = self.service.ingest_external_document(
            object(),
            actor=_actor(),
            case_id=CASE_ID,
            content=PDF,
            original_filename="improved-resource.pdf",
            source_original_filename="IMG_3842.pdf",
            declared_mime="application/pdf",
            purpose="main_filing",
            synthetic_confirmed=True,
            supersedes_document_version_id=None,
            storage_writer=self._store,
            register_rollback_cleanup=lambda bucket, key: self.cleanups.append((bucket, key)),
        )

        self.assertEqual(document.state.value, "review")
        self.assertEqual(document.scan_status, "pending")
        self.assertEqual(document.source_kind, "external_revision")
        self.assertEqual(self.cleanups, [("internal-bucket", "internal/case/object.pdf")])
        self.assertEqual(len(self.repository.insert_calls), 1)
        self.assertEqual(len(self.repository.audits), 1)
        audit = self.repository.audits[0]
        self.assertEqual(audit["reason_code"], "pending_security_scan")
        self.assertFalse(audit["payload"]["eligible_for_package"])
        self.assertTrue(audit["payload"]["synthetic_confirmed"])
        self.assertEqual(
            audit["payload"]["source_original_filename"],
            "IMG_3842.pdf",
        )
        forbidden = {"bucket", "key", "b2_bucket", "b2_key", "url", "note"}
        self.assertFalse(forbidden.intersection(audit["payload"]))

    def test_confirmation_and_permission_fail_before_storage(self):
        for actor, confirmation, expected in (
            (_actor(), False, PresenterConflict),
            (_actor(permission=False), True, PresenterPolicyError),
        ):
            with self.subTest(confirmation=confirmation, permissions=actor.permissions):
                with self.assertRaises(expected):
                    self.service.ingest_external_document(
                        object(),
                        actor=actor,
                        case_id=CASE_ID,
                        content=PDF,
                        original_filename="resource.pdf",
                        declared_mime="application/pdf",
                        purpose="main_filing",
                        synthetic_confirmed=confirmation,
                        supersedes_document_version_id=None,
                        storage_writer=self._store,
                        register_rollback_cleanup=lambda bucket, key: self.cleanups.append((bucket, key)),
                    )
        self.assertEqual(self.storage_calls, [])
        self.assertEqual(self.repository.insert_calls, [])

    def test_case_scope_denial_precedes_storage(self):
        self.repository.case_access = False
        with self.assertRaises(PresenterForbidden):
            self.service.ingest_external_document(
                object(),
                actor=_actor(),
                case_id=CASE_ID,
                content=PDF,
                original_filename="resource.pdf",
                declared_mime="application/pdf",
                purpose="main_filing",
                synthetic_confirmed=True,
                supersedes_document_version_id=None,
                storage_writer=self._store,
                register_rollback_cleanup=lambda bucket, key: self.cleanups.append((bucket, key)),
            )
        self.assertEqual(self.storage_calls, [])
        self.assertEqual(self.cleanups, [])

    def test_writer_that_skips_pre_registration_is_rejected_and_cleaned(self):
        with self.assertRaises(PresenterServiceError) as raised:
            self.service.ingest_external_document(
                object(),
                actor=_actor(),
                case_id=CASE_ID,
                content=PDF,
                original_filename="resource.pdf",
                declared_mime="application/pdf",
                purpose="main_filing",
                synthetic_confirmed=True,
                supersedes_document_version_id=None,
                storage_writer=lambda upload, register: (
                    "internal-bucket",
                    "internal/unregistered-key",
                ),
                register_rollback_cleanup=lambda bucket, key: self.cleanups.append((bucket, key)),
            )
        self.assertEqual(
            getattr(raised.exception, "code", None),
            "presenter.storage_cleanup_not_pre_registered",
        )
        self.assertEqual(
            self.cleanups,
            [("internal-bucket", "internal/unregistered-key")],
        )
        self.assertEqual(self.repository.insert_calls, [])


class _SqlResult:
    def __init__(self, *, row=None, scalar=None) -> None:
        self.row = row
        self.scalar_value = scalar

    def mappings(self):
        return self

    def first(self):
        return self.row

    def scalar(self):
        return self.scalar_value


class _SqlIngestConnection:
    def __init__(self, *, latest_version: int = 2) -> None:
        self.latest_version = latest_version
        self.calls: list[tuple[str, dict]] = []
        self.predecessor = {
            "id": PREDECESSOR_ID,
            "logical_document_id": LOGICAL_DOCUMENT_ID,
            "version_number": 2,
            "purpose": "main_filing",
        }

    def execute(self, statement, parameters=None):
        sql = " ".join(str(statement).split())
        params = dict(parameters or {})
        self.calls.append((sql, params))
        if sql.startswith("SELECT id, logical_document_id"):
            return _SqlResult(row=dict(self.predecessor))
        if "pg_advisory_xact_lock" in sql:
            return _SqlResult()
        if sql.startswith("SELECT MAX(version_number)"):
            return _SqlResult(scalar=self.latest_version)
        if "INSERT INTO documents" in sql:
            return _SqlResult(row={"id": SOURCE_DOCUMENT_ID})
        if "INSERT INTO rtm_presenter_document_versions" in sql:
            return _SqlResult(
                row={
                    "id": DOCUMENT_VERSION_ID,
                    "case_id": CASE_ID,
                    "logical_document_id": LOGICAL_DOCUMENT_ID,
                    "version_number": 3,
                    "sha256": "a" * 64,
                    "purpose": "main_filing",
                    "state": "review",
                    "scan_status": "pending",
                    "original_filename": "resource.pdf",
                    "detected_mime": "application/pdf",
                    "size_bytes": 123,
                    "source_kind": "external_revision",
                }
            )
        raise AssertionError(f"SQL inesperado: {sql}")


class SqlExternalDocumentVersionTest(unittest.TestCase):
    def _upload(self) -> PresenterExternalDocumentUpload:
        return PresenterExternalDocumentUpload(
            content=PDF,
            sha256="a" * 64,
            original_filename="resource.pdf",
            media_type="application/pdf",
            size_bytes=123,
            purpose="main_filing",
            extension=".pdf",
        )

    def test_supersedes_latest_absolute_version_under_advisory_lock(self):
        conn = _SqlIngestConnection(latest_version=2)
        row = SqlPresenterRepository().insert_external_document_version(
            conn,
            case_id=CASE_ID,
            created_by_operator_id=OPERATOR_ID,
            upload=self._upload(),
            storage_bucket="internal-bucket",
            storage_key="internal/key",
            supersedes_document_version_id=PREDECESSOR_ID,
        )

        self.assertEqual(row["version_number"], 3)
        sql = [call[0] for call in conn.calls]
        advisory_index = next(i for i, value in enumerate(sql) if "pg_advisory_xact_lock" in value)
        locked_index = next(i for i, value in enumerate(sql) if value.endswith("FOR UPDATE"))
        self.assertLess(advisory_index, locked_index)
        version_sql, version_params = next(
            call for call in conn.calls if "INSERT INTO rtm_presenter_document_versions" in call[0]
        )
        self.assertIn(":purpose, 'review', 'pending'", version_sql)
        self.assertIn("'external_revision'", version_sql)
        self.assertEqual(version_params["version_number"], 3)
        self.assertEqual(version_params["supersedes_version_id"], PREDECESSOR_ID)
        self.assertNotIn("storage_bucket", version_params["metadata"])

    def test_stale_predecessor_is_rejected_before_document_insert(self):
        conn = _SqlIngestConnection(latest_version=3)
        with self.assertRaises(PresenterConflict) as raised:
            SqlPresenterRepository().insert_external_document_version(
                conn,
                case_id=CASE_ID,
                created_by_operator_id=OPERATOR_ID,
                upload=self._upload(),
                storage_bucket="internal-bucket",
                storage_key="internal/key",
                supersedes_document_version_id=PREDECESSOR_ID,
            )
        self.assertEqual(
            raised.exception.code,
            "presenter.external_document_predecessor_stale",
        )
        self.assertFalse(any("INSERT INTO documents" in sql for sql, _ in conn.calls))


def _load_router_module():
    storage = types.ModuleType("b2_storage")
    storage.get_b2_bucket = lambda: ""
    storage.get_s3_client = lambda: None
    database = types.ModuleType("database")
    database.get_engine = lambda: None
    auth_router = types.ModuleType("rtm_core.operator_auth_router")
    auth_router.load_operator_session_with_device_possession = lambda *args, **kwargs: None
    auth_service = types.ModuleType("rtm_core.operator_auth_service")
    auth_service.has_explicit_reauthentication = lambda session: False

    module_name = f"rtm_presenter_router_external_ingest_{id(storage)}"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "rtm_presenter_router.py")
    if spec is None or spec.loader is None:
        raise AssertionError("No se pudo cargar rtm_presenter_router.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(
        sys.modules,
        {
            module_name: module,
            "b2_storage": storage,
            "database": database,
            "rtm_core.operator_auth_router": auth_router,
            "rtm_core.operator_auth_service": auth_service,
        },
        clear=False,
    ):
        spec.loader.exec_module(module)
    return module


class _SessionResult:
    def first(self):
        return (NOW - timedelta(hours=1), None, None)


class _ContextConnection:
    def execute(self, statement, parameters=None):
        del statement, parameters
        return _SessionResult()


class _CommitFailingBegin:
    def __init__(self, connection) -> None:
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        del exc, traceback
        if exc_type is None:
            raise RuntimeError("synthetic commit failure")
        return False


class _CommitFailingEngine:
    def __init__(self, connection) -> None:
        self.connection = connection

    def begin(self):
        return _CommitFailingBegin(self.connection)


class PresenterRouterIngressContractTest(unittest.TestCase):
    def test_context_binds_device_and_cleans_object_when_commit_fails(self):
        module = _load_router_module()
        connection = _ContextConnection()
        bound_session = types.SimpleNamespace(
            operator_id=OPERATOR_ID,
            session_id=SESSION_ID,
            permissions=(PRESENTER_DOCUMENT_INGEST_PERMISSION,),
            role_code="rtm.operator",
            must_change_password=False,
            mfa_required=False,
        )
        load_bound = mock.Mock(return_value=bound_session)
        cleaned: list[str] = []

        with (
            mock.patch.object(module, "get_engine", return_value=_CommitFailingEngine(connection)),
            mock.patch.object(module, "load_presenter_runtime_configuration", return_value=_runtime()),
            mock.patch.object(module, "load_operator_auth_runtime_config"),
            mock.patch.object(module, "load_operator_session_with_device_possession", load_bound),
        ):
            dependency = module.require_presenter_context(
                authorization="Bearer " + "t" * 48,
                x_rtm_device="D" * 32,
                rtm_presenter_device=None,
                x_request_id=None,
            )
            context = next(dependency)
            context.rollback_cleanups.append(lambda: cleaned.append("deleted"))
            with self.assertRaisesRegex(RuntimeError, "commit failure"):
                next(dependency)

        self.assertEqual(cleaned, ["deleted"])
        load_bound.assert_called_once_with(
            connection,
            authorization="Bearer " + "t" * 48,
            x_rtm_device="D" * 32,
            rtm_presenter_device=None,
            touch=True,
        )

    def test_route_and_legacy_contracts_are_closed(self):
        module = _load_router_module()
        router_source = (ROOT / "rtm_presenter_router.py").read_text(encoding="utf-8")
        self.assertIn('@router.post("/cases/{case_id}/documents/external")', router_source)
        self.assertIn("synthetic_confirmed: Literal[True] = Form(...)", router_source)
        self.assertIn("RTM_PRESENTER_MAX_FILE_BYTES + 1", router_source)
        self.assertIn("await file.close()", router_source)
        self.assertIn("context.register_storage_rollback", router_source)
        route_signature = inspect.signature(module.ingest_external_document_route)
        self.assertNotIn("note", route_signature.parameters)
        self.assertNotIn("kind", route_signature.parameters)
        self.assertNotIn("x_operator_token", route_signature.parameters)

        signature = inspect.signature(ops.upload_external_document)
        self.assertEqual(tuple(signature.parameters), ("case_id",))
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(ops.upload_external_document(CASE_ID))
        self.assertEqual(raised.exception.status_code, 410)
        self.assertEqual(
            raised.exception.detail["replacement"],
            f"/ops/presenter/cases/{CASE_ID}/documents/external",
        )

    def test_false_synthetic_confirmation_never_reads_or_uploads(self):
        module = _load_router_module()

        class Upload:
            filename = "resource.pdf"
            content_type = "application/pdf"
            size = len(PDF)

            def __init__(self) -> None:
                self.read_called = False
                self.closed = False

            async def read(self, size: int) -> bytes:
                del size
                self.read_called = True
                return PDF

            async def close(self) -> None:
                self.closed = True

        upload = Upload()
        context = module.PresenterRequestContext(
            connection=object(),
            actor=_actor(),
            request_id="request-id",
        )
        request = types.SimpleNamespace(headers={})
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(
                module.ingest_external_document_route(
                    case_id=UUID(CASE_ID),
                    request=request,
                    file=upload,
                    purpose="main_filing",
                    synthetic_confirmed=False,
                    supersedes_document_version_id=None,
                    context=context,
                )
            )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertFalse(upload.read_called)
        self.assertTrue(upload.closed)

    def test_storage_cleanup_is_registered_before_put_ack_failure(self):
        module = _load_router_module()

        class Upload:
            filename = "resource.pdf"
            content_type = "application/pdf"
            size = len(PDF)

            def __init__(self) -> None:
                self.closed = False

            async def read(self, size: int) -> bytes:
                self.asserted_size = size
                return PDF

            async def close(self) -> None:
                self.closed = True

        class Service:
            @staticmethod
            def ingest_external_document(conn, **kwargs):
                del conn
                prepared = validate_external_document_upload(
                    content=kwargs["content"],
                    original_filename=kwargs["original_filename"],
                    declared_mime=kwargs["declared_mime"],
                    purpose=kwargs["purpose"],
                )
                return kwargs["storage_writer"](
                    prepared,
                    kwargs["register_rollback_cleanup"],
                )

        class LostAckClient:
            @staticmethod
            def put_object(**kwargs):
                del kwargs
                raise RuntimeError("synthetic lost B2 acknowledgement")

        upload = Upload()
        context = module.PresenterRequestContext(
            connection=object(),
            actor=_actor(),
            request_id="request-id",
        )
        request = types.SimpleNamespace(headers={})
        with (
            mock.patch.object(module, "_service", return_value=Service()),
            mock.patch.object(module, "get_b2_bucket", return_value="internal-bucket"),
            mock.patch.object(module, "get_s3_client", return_value=LostAckClient()),
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    module.ingest_external_document_route(
                        case_id=UUID(CASE_ID),
                        request=request,
                        file=upload,
                        purpose="main_filing",
                        synthetic_confirmed=True,
                        supersedes_document_version_id=None,
                        context=context,
                    )
                )

        self.assertEqual(raised.exception.status_code, 500)
        self.assertTrue(upload.closed)
        self.assertEqual(len(context.rollback_cleanups), 1)
        deleted: list[tuple[str, str]] = []
        with mock.patch.object(
            module,
            "_delete_presenter_object",
            side_effect=lambda bucket, key: deleted.append((bucket, key)),
        ):
            module._run_rollback_cleanups(context.rollback_cleanups)
        self.assertEqual(len(deleted), 1)
        self.assertEqual(deleted[0][0], "internal-bucket")
        self.assertTrue(deleted[0][1].startswith(f"cases/{CASE_ID}/presenter_external/"))


if __name__ == "__main__":
    unittest.main()
