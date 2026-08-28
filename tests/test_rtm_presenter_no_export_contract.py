from __future__ import annotations

import ast
import importlib.util
import json
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from uuid import uuid4

from fastapi import HTTPException, Response

import ops


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_DOCUMENT_KEYS = {
    "b2_bucket",
    "b2_key",
    "bucket",
    "key",
    "object_key",
    "original_bucket",
    "original_key",
    "source_bucket",
    "source_key",
    "source_keys",
    "storage_bucket",
    "storage_coordinates",
    "storage_locator",
    "storage_key",
}


class OperatorEventSanitizerTest(unittest.TestCase):
    def test_recursive_storage_coordinates_are_removed(self):
        payload = {
            "original_key": "private/case/original.pdf",
            "nested": {
                "source_keys": ["private/a", "private/b"],
                "storage_locator": {"bucket": "secret", "key": "secret"},
                "safe": "visible",
            },
        }

        sanitized = ops._sanitize_operator_payload(payload)

        self.assertEqual(sanitized, {"nested": {"safe": "visible"}})
        self.assertFalse(set(_mapping_keys(sanitized)) & FORBIDDEN_DOCUMENT_KEYS)


def _mapping_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).lower()
            yield from _mapping_keys(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _mapping_keys(child)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _DocumentsConnection:
    def __init__(self):
        self.statements = []

    def execute(self, statement, parameters=None):
        self.statements.append((str(statement), parameters or {}))
        return _Rows(
            [
                (
                    "document-1",
                    "original_notice",
                    "a" * 64,
                    "application/pdf",
                    321,
                    datetime(2026, 8, 28, tzinfo=timezone.utc),
                )
            ]
        )


class _Begin:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        return False


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    def begin(self):
        return _Begin(self.connection)


class _Readiness:
    ready = True

    @staticmethod
    def model_dump(*, mode):
        if mode != "json":
            raise AssertionError("El workspace debe serializar readiness como JSON")
        return {"ready": True}


class _PresignResult:
    @staticmethod
    def fetchone():
        return (
            "original_notice",
            "paid",
            "internal-bucket",
            "internal/case/document.pdf",
        )


class _PresignConnection:
    def __init__(self, calls):
        self.calls = calls

    def execute(self, statement, parameters=None):
        self.calls["database_query"].append(
            (str(statement), dict(parameters or {}))
        )
        return _PresignResult()


class _PresignS3:
    def __init__(self, calls):
        self.calls = calls

    def generate_presigned_url(self, **kwargs):
        self.calls.append(kwargs)
        return "https://storage.invalid/synthetic-presigned-result"


def _load_files_module():
    calls = {
        "case_access": [],
        "database": 0,
        "storage": 0,
        "presign": [],
        "database_query": [],
    }

    def require_case_access_token(case_id, token):
        calls["case_access"].append((case_id, token))
        if not token:
            raise HTTPException(status_code=401, detail="case capability required")
        return case_id

    def get_engine():
        calls["database"] += 1
        return _Engine(_PresignConnection(calls))

    def get_s3_client():
        calls["storage"] += 1
        return _PresignS3(calls["presign"])

    storage = types.ModuleType("b2_storage")
    storage.get_s3_client = get_s3_client
    database = types.ModuleType("database")
    database.get_engine = get_engine
    case_access = types.ModuleType("public_case_access")
    case_access.require_case_access_token = require_case_access_token

    module_name = f"files_no_export_contract_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "files.py")
    if spec is None or spec.loader is None:
        raise AssertionError("No se pudo cargar files.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(
        sys.modules,
        {
            "b2_storage": storage,
            "database": database,
            "public_case_access": case_access,
        },
        clear=False,
    ):
        spec.loader.exec_module(module)
    return module, calls


def _load_authorization_pdf_module():
    storage = types.ModuleType("b2_storage")
    storage.upload_bytes = lambda *args, **kwargs: (
        "internal-bucket",
        "internal/case/authorization.pdf",
    )

    module_name = f"authorization_pdf_no_export_contract_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "authorization_pdf.py")
    if spec is None or spec.loader is None:
        raise AssertionError("No se pudo cargar authorization_pdf.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"b2_storage": storage}, clear=False):
        spec.loader.exec_module(module)
    return module


def _function_source(path, name):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"No existe {name} en {path}")


class _AuthorizationResult:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class _AuthorizationConnection:
    def __init__(self, case_id, document_id):
        self.case_id = case_id
        self.document_id = document_id
        self.events = []

    def execute(self, statement, parameters=None):
        sql = " ".join(str(statement).split())
        parameters = dict(parameters or {})
        if sql.startswith("SELECT id FROM cases") and "FOR UPDATE" in sql:
            return _AuthorizationResult((self.case_id,))
        if "SELECT id, sha256, mime, size_bytes" in sql:
            return _AuthorizationResult()
        if "SELECT id, organismo, expediente_ref" in sql:
            return _AuthorizationResult(
                (
                    self.case_id,
                    "DGT",
                    "EXP-2026-1",
                    "persona@example.invalid",
                    {
                        "full_name": "Persona Sintética",
                        "dni_nie": "00000000T",
                        "domicilio_notif": "Dirección sintética",
                    },
                )
            )
        if "INSERT INTO documents" in sql:
            return _AuthorizationResult((self.document_id,))
        if "INSERT INTO events" in sql:
            self.events.append(json.loads(parameters["payload"]))
            return _AuthorizationResult()
        raise AssertionError(f"SQL inesperado: {sql}")


def _load_ops_operator_router_module():
    database = types.ModuleType("database")
    database.get_engine = lambda: None
    case_authority = types.ModuleType("case_authority")
    case_authority.verify_signed_case_authority = lambda *args, **kwargs: None

    generate = types.ModuleType("generate")

    class GenerateRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    generate.GenerateRequest = GenerateRequest
    generate.generate_dgt = lambda *args, **kwargs: None

    storage = types.ModuleType("b2_storage")
    storage.upload_bytes = lambda *args, **kwargs: ("", "")
    docx_builder = types.ModuleType("docx_builder")
    docx_builder.build_docx = lambda *args, **kwargs: b""
    pdf_builder = types.ModuleType("pdf_builder")
    pdf_builder.build_pdf = lambda *args, **kwargs: b""
    reanalysis = types.ModuleType("reanalysis")
    reanalysis.reanalyze_traffic_fine_case = lambda *args, **kwargs: None

    module_name = f"ops_operator_no_export_contract_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(
        module_name, ROOT / "ops_operator_router.py"
    )
    if spec is None or spec.loader is None:
        raise AssertionError("No se pudo cargar ops_operator_router.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(
        sys.modules,
        {
            "database": database,
            "case_authority": case_authority,
            "generate": generate,
            "b2_storage": storage,
            "docx_builder": docx_builder,
            "pdf_builder": pdf_builder,
            "reanalysis": reanalysis,
        },
        clear=False,
    ):
        spec.loader.exec_module(module)
    return module


class RtmPresenterBackendNoExportContractTest(unittest.TestCase):
    def test_presenter_route_failures_raise_for_transaction_rollback(self):
        source = (ROOT / "rtm_presenter_router.py").read_text(encoding="utf-8")
        self.assertNotIn("def _failure(", source)
        self.assertNotIn("return _failure(", source)
        self.assertGreaterEqual(
            source.count("raise _as_http_exception(context, exc) from exc"),
            6,
        )
        self.assertIn("with get_engine().begin() as conn", source)
        self.assertIn("session.must_change_password", source)
        self.assertIn("session.mfa_required", source)

    def test_freeze_requires_and_forwards_persistent_idempotency_key(self):
        router = (ROOT / "rtm_presenter_router.py").read_text(encoding="utf-8")
        service = (ROOT / "rtm_presenter_service.py").read_text(encoding="utf-8")
        schema = (ROOT / "rtm_presenter_schema.py").read_text(encoding="utf-8")
        self.assertIn('alias="Idempotency-Key"', router)
        self.assertIn("idempotency_key=idempotency_key", router)
        self.assertIn("pg_advisory_xact_lock", service)
        self.assertIn("presenter.idempotency_key_reused", service)
        self.assertIn("rtm_presenter_idempotency_keys", schema)
        self.assertIn("request_sha256", schema)

    def test_all_ops_responses_are_marked_no_store(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("def no_store_private_ops", source)
        self.assertIn('path == "/ops" or path.startswith("/ops/")', source)
        self.assertIn('response.headers["Cache-Control"] = "no-store, max-age=0"', source)
        self.assertIn('response.headers["Pragma"] = "no-cache"', source)

    def test_secondary_ops_surfaces_do_not_project_storage_coordinates(self):
        queue_source = (ROOT / "ops_queue_smart.py").read_text(encoding="utf-8")
        document_query = queue_source[
            queue_source.index("SELECT id, kind, mime"):
            queue_source.index("ai_payload =", queue_source.index("SELECT id, kind, mime"))
        ]
        self.assertNotIn("b2_bucket", document_query)
        self.assertNotIn("b2_key", document_query)
        self.assertNotIn('"bucket"', document_query)
        self.assertNotIn('"key"', document_query)

    def test_finalized_resource_response_and_event_keep_storage_internal(self):
        ops_operator_router = _load_ops_operator_router_module()
        class Result:
            def __init__(self, row=None):
                self.row = row

            def fetchone(self):
                return self.row

        class Connection:
            def __init__(self):
                self.document_number = 0

            def execute(self, statement, parameters=None):
                sql = " ".join(str(statement).split())
                if "INSERT INTO ops_final_resources" in sql:
                    return Result(
                        (
                            "11111111-1111-4111-8111-111111111111",
                            datetime(2026, 8, 28, tzinfo=timezone.utc),
                            datetime(2026, 8, 28, tzinfo=timezone.utc),
                        )
                    )
                if "INSERT INTO documents" in sql:
                    self.document_number += 1
                    return Result(
                        (
                            f"00000000-0000-4000-8000-{self.document_number:012d}",
                        )
                    )
                return Result()

        connection = Connection()
        event_payloads = []
        upload_number = 0

        def upload(*args, **kwargs):
            nonlocal upload_number
            del args, kwargs
            upload_number += 1
            return "internal-bucket", f"internal-key-{upload_number}"

        with (
            mock.patch.object(ops_operator_router, "require_operator_token"),
            mock.patch.object(
                ops_operator_router,
                "get_engine",
                return_value=_Engine(connection),
            ),
            mock.patch.object(ops_operator_router, "_case_or_404"),
            mock.patch.object(
                ops_operator_router,
                "_next_final_resource_version",
                return_value=2,
            ),
            mock.patch.object(ops_operator_router, "upload_bytes", side_effect=upload),
            mock.patch.object(ops_operator_router, "build_docx", return_value=b"docx"),
            mock.patch.object(ops_operator_router, "build_pdf", return_value=b"pdf"),
            mock.patch.object(ops_operator_router, "_set_status"),
            mock.patch.object(
                ops_operator_router,
                "_append_event",
                side_effect=lambda _conn, _case, _type, payload: event_payloads.append(
                    payload
                ),
            ),
            mock.patch.object(
                ops_operator_router,
                "_get_status",
                return_value="final_ready",
            ),
        ):
            payload = ops_operator_router.finalize_resource(
                case_id="22222222-2222-4222-8222-222222222222",
                body=ops_operator_router.FinalResourceBody(content="Recurso final"),
                x_operator_token="synthetic-token",
            )

        self.assertEqual(len(payload["documents"]), 3)
        self.assertEqual(len(event_payloads), 1)
        for projection in (*payload["documents"], *event_payloads[0]["documents"]):
            self.assertEqual(
                set(_mapping_keys(projection)) & FORBIDDEN_DOCUMENT_KEYS,
                set(),
            )
            self.assertEqual(projection["custody"], "rtm_internal_only")
            self.assertFalse(projection["operator_export_allowed"])
            self.assertRegex(projection["sha256"], r"^[0-9a-f]{64}$")

    def test_ops_list_documents_never_projects_bucket_or_key(self):
        connection = _DocumentsConnection()
        with (
            mock.patch.object(ops, "_require_operator", return_value=None),
            mock.patch.object(ops, "get_engine", return_value=_Engine(connection)),
        ):
            payload = ops.list_documents(
                case_id="synthetic-case",
                x_operator_token="synthetic-operator-token",
            )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["documents"])
        self.assertEqual(set(_mapping_keys(payload)) & FORBIDDEN_DOCUMENT_KEYS, set())
        statement = "\n".join(sql for sql, _ in connection.statements).lower()
        self.assertNotIn("b2_bucket", statement)
        self.assertNotIn("b2_key", statement)

    def test_operator_download_route_is_fail_closed_without_b2_access(self):
        route = next(
            route
            for route in ops.router.routes
            if route.path == "/ops/documents/{doc_id}/download"
        )
        self.assertEqual(route.methods, {"GET"})
        self.assertIs(route.endpoint, ops.download_document)

        b2_calls = []

        def forbidden_b2(*args, **kwargs):
            b2_calls.append((args, kwargs))
            raise AssertionError("La ruta fail-closed no debe invocar B2")

        fake_b2 = types.ModuleType("b2_storage")
        for name in (
            "download_bytes",
            "get_s3_client",
            "presign_get_url",
            "upload_bytes",
        ):
            setattr(fake_b2, name, forbidden_b2)

        with (
            mock.patch.dict(sys.modules, {"b2_storage": fake_b2}, clear=False),
            mock.patch.object(ops, "_require_operator", return_value=None),
            mock.patch.object(ops, "get_engine", side_effect=forbidden_b2),
            mock.patch.object(ops, "_upload_bytes", side_effect=forbidden_b2),
        ):
            with self.assertRaises(HTTPException) as raised:
                route.endpoint(
                    doc_id="synthetic-document",
                    x_operator_token="synthetic-operator-token",
                )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(b2_calls, [])

    def test_workspace_omits_download_endpoint(self):
        source = (ROOT / "rtm_core" / "workspace_service.py").read_text(
            encoding="utf-8"
        )
        start = source.index("def _document_rows")
        end = source.index("\ndef _timeline", start)
        projection = source[start:end]
        self.assertNotIn("download_endpoint", projection)
        self.assertIn('"custody": "rtm_internal_only"', projection)
        self.assertIn('"operator_export_allowed": False', projection)

    def test_files_presign_requires_case_capability_before_b2(self):
        module, calls = _load_files_module()
        route = next(route for route in module.router.routes if route.path == "/files/presign")
        header = next(
            field
            for field in route.dependant.header_params
            if field.name == "x_case_token"
        )
        self.assertEqual(header.alias, "X-RTM-Case-Token")
        query_names = {field.name for field in route.dependant.query_params}
        self.assertIn("document_id", query_names)
        self.assertNotIn("bucket", query_names)
        self.assertNotIn("key", query_names)

        with self.assertRaises(HTTPException) as raised:
            module.presign(
                response=Response(),
                case_id="synthetic-case",
                document_id="11111111-1111-4111-8111-111111111111",
                expires=300,
                x_case_token=None,
            )

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(calls["case_access"], [("synthetic-case", None)])
        self.assertEqual(calls["database"], 0)
        self.assertEqual(calls["storage"], 0)
        self.assertEqual(calls["presign"], [])
        self.assertIn("no-store", raised.exception.headers["Cache-Control"])

    def test_files_presign_resolves_storage_server_side_with_short_no_store_url(self):
        module, calls = _load_files_module()
        route = next(route for route in module.router.routes if route.path == "/files/presign")
        expires = next(
            field
            for field in route.dependant.query_params
            if field.name == "expires"
        )
        upper_bounds = {
            getattr(constraint, "le", None)
            for constraint in expires.field_info.metadata
            if getattr(constraint, "le", None) is not None
        }
        self.assertEqual(expires.default, 300)
        self.assertEqual(upper_bounds, {300})

        response = Response()
        payload = module.presign(
            response=response,
            case_id="synthetic-case",
            document_id="11111111-1111-4111-8111-111111111111",
            expires=300,
            x_case_token="synthetic-case-token",
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(len(calls["presign"]), 1)
        self.assertEqual(calls["presign"][0]["ExpiresIn"], 300)
        self.assertEqual(
            calls["presign"][0]["Params"],
            {
                "Bucket": "internal-bucket",
                "Key": "internal/case/document.pdf",
            },
        )
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(len(calls["database_query"]), 1)
        statement, parameters = calls["database_query"][0]
        self.assertIn("d.case_id = :case_id", statement)
        self.assertIn("d.id = CAST(:document_id AS UUID)", statement)
        self.assertIn(
            "COALESCE(d.kind, '') <> 'external_revision'",
            statement,
        )
        self.assertEqual(
            parameters,
            {
                "case_id": "synthetic-case",
                "document_id": "11111111-1111-4111-8111-111111111111",
            },
        )

    def test_newer_pending_version_invalidates_frozen_and_byte_access(self):
        source = (ROOT / "rtm_presenter_service.py").read_text(
            encoding="utf-8"
        )
        repository_start = source.index("class SqlPresenterRepository")
        frozen_start = source.index("    def load_frozen_package(", repository_start)
        frozen_end = source.index("\n    def insert_ticket(", frozen_start)
        frozen_projection = source[frozen_start:frozen_end]
        bytes_start = source.index("    def load_document_bytes(", repository_start)
        bytes_end = source.index("\n    def append_audit(", bytes_start)
        bytes_projection = source[bytes_start:bytes_end]
        for projection in (frozen_projection, bytes_projection):
            self.assertIn("newer.logical_document_id=v.logical_document_id", projection)
            self.assertIn("newer.version_number > v.version_number", projection)
            self.assertIn("'{\"synthetic_only\":true}'::jsonb", projection)
        self.assertIn("THEN 'superseded'", frozen_projection)
        self.assertIn("AND NOT EXISTS", bytes_projection)

    def test_files_presign_rejects_invalid_document_identity_before_database(self):
        module, calls = _load_files_module()

        with self.assertRaises(HTTPException) as raised:
            module.presign(
                response=Response(),
                case_id="synthetic-case",
                document_id="not-a-document-id",
                expires=300,
                x_case_token="synthetic-case-token",
            )

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(calls["database"], 0)
        self.assertEqual(calls["storage"], 0)
        self.assertIn("no-store", raised.exception.headers["Cache-Control"])

    def test_authorization_document_projection_contains_only_safe_custody_metadata(self):
        module = _load_authorization_pdf_module()
        case_id = "22222222-2222-4222-8222-222222222222"
        document_id = "33333333-3333-4333-8333-333333333333"
        connection = _AuthorizationConnection(case_id, document_id)
        request = types.SimpleNamespace(
            headers={"x-forwarded-for": "203.0.113.1"},
            client=None,
        )
        pdf_bytes = b"%PDF-1.4\nsynthetic authorization\n%%EOF"

        with mock.patch.object(
            module,
            "generate_authorization_pdf",
            return_value=pdf_bytes,
        ):
            payload = module.ensure_authorization_pdf(
                connection,
                case_id,
                request,
                version="v1_dgt_homologado",
            )

        expected_keys = {"id", "sha256", "mime", "size_bytes", "custody"}
        self.assertEqual(set(payload["document"]), expected_keys)
        self.assertEqual(payload["document"]["id"], document_id)
        self.assertRegex(payload["document"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(payload["document"]["size_bytes"], len(pdf_bytes))
        self.assertEqual(payload["document"]["custody"], "rtm_internal_only")
        self.assertEqual(set(_mapping_keys(payload)) & FORBIDDEN_DOCUMENT_KEYS, set())
        self.assertEqual(len(connection.events), 1)
        self.assertEqual(
            set(_mapping_keys(connection.events[0])) & FORBIDDEN_DOCUMENT_KEYS,
            set(),
        )

    def test_existing_authorization_is_projected_without_storage_coordinates(self):
        module = _load_authorization_pdf_module()
        document_id = "33333333-3333-4333-8333-333333333333"
        digest = "a" * 64

        class ExistingConnection:
            @staticmethod
            def execute(statement, parameters=None):
                del parameters
                sql = " ".join(str(statement).split())
                if "SELECT id, sha256, mime, size_bytes" not in sql:
                    raise AssertionError(f"SQL inesperado: {sql}")
                return _AuthorizationResult(
                    (document_id, digest, "application/pdf", 1234)
                )

        projection = module._existing_authorization_doc(
            ExistingConnection(),
            "22222222-2222-4222-8222-222222222222",
        )

        self.assertEqual(
            projection,
            {
                "id": document_id,
                "sha256": digest,
                "mime": "application/pdf",
                "size_bytes": 1234,
                "custody": "rtm_internal_only",
            },
        )
        self.assertEqual(
            set(_mapping_keys(projection)) & FORBIDDEN_DOCUMENT_KEYS,
            set(),
        )

    def test_legacy_authorization_responses_do_not_advertise_download_urls(self):
        cases_path = ROOT / "cases.py"
        intake = _function_source(cases_path, "create_rtm_intake_draft")
        authorize = _function_source(cases_path, "authorize_case")
        signed = _function_source(cases_path, "_store_authorization_signed")
        projection = _function_source(cases_path, "_document_projection")

        self.assertNotIn("authorization_download_url", intake)
        self.assertNotIn('"download_url"', authorize)
        self.assertIn("_document_projection", signed)
        for forbidden in ('"bucket"', '"key"', '"url"'):
            self.assertNotIn(forbidden, projection)
        for required in ('"id"', '"sha256"', '"mime"', '"size_bytes"', '"custody"'):
            self.assertIn(required, projection)

    def test_authorization_pdf_download_responses_are_no_store(self):
        cases_path = ROOT / "cases.py"
        for function_name in (
            "download_rtm_authorization_pdf",
            "download_authorization_pdf",
        ):
            source = _function_source(cases_path, function_name)
            self.assertIn("PRIVATE_DOCUMENT_HEADERS", source)

    def test_document_flow_smoke_uses_case_scoped_identity_and_expects_ops_denial(self):
        source = (ROOT / "scripts" / "rtm_document_flow_smoke.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"document_id": original_doc["id"]', source)
        self.assertIn('headers=case_headers', source)
        self.assertIn('"X-RTM-Case-Token"', source)
        self.assertIn('ops_download.status_code == 403', source)
        self.assertNotIn('"bucket": original_doc["bucket"]', source)
        self.assertNotIn('"key": original_doc["key"]', source)


if __name__ == "__main__":
    unittest.main()
