"""Servicio transaccional del MVP RTM Presenter.

La UI de operador solo recibe metadatos sanitizados. Los bytes se sirven
exclusivamente a la extension confiable tras consumir atomicamente un ticket
opaco, corto y de un solo uso. La descarga ocurre en memoria y nunca se crea un
ZIP o una carpeta para un operador normal.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol, Sequence

from sqlalchemy import text

from rtm_presenter_contracts import (
    RTM_PRESENTER_CONTRACT_VERSION,
    RTM_PRESENTER_MAX_FILE_BYTES,
    RTM_PRESENTER_MAX_ITEMS,
    RTM_PRESENTER_MAX_TICKET_TTL_SECONDS,
    RTM_PRESENTER_SYNTHETIC_MARKER,
    FrozenPresenterPackage,
    IssuedPresenterTicket,
    PresenterAdminExportPayload,
    PresenterClientKind,
    PresenterContractError,
    PresenterDocumentState,
    PresenterDocumentVersion,
    PresenterFilePayload,
    PresenterPackageItem,
    PresenterTicketBinding,
    build_frozen_package,
    canonical_json,
    canonical_sha256,
    normalize_origin,
    safe_filename,
)
from rtm_presenter_policy import (
    PresenterActorContext,
    PresenterPolicyError,
    PresenterRuntimeConfiguration,
    authorize_admin_export,
    authorize_document_ingest,
    authorize_document_list,
    authorize_handoff_exchange,
    authorize_handoff_exchange_client,
    authorize_handoff_issue,
    authorize_package_freeze,
    require_presenter_runtime,
)


RTM_PRESENTER_SERVICE_VERSION = "rtm_presenter_service_v1_1"
DEFAULT_TICKET_TTL_SECONDS = 90
MAX_PACKAGE_LIFETIME_SECONDS = 24 * 60 * 60
ADMIN_EXPORT_LIFETIME_SECONDS = 15 * 60
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")
RTM_PRESENTER_EXTERNAL_PURPOSES = frozenset(
    {
        "main_filing",
        "representation_authorization",
        "submission_receipt",
        "supporting_evidence",
    }
)
_EXTERNAL_MEDIA_EXTENSIONS = {
    "application/pdf": (".pdf",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        ".docx",
    ),
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
}
_DOCX_CONTENT_TYPE = (
    b"application/vnd.openxmlformats-officedocument."
    b"wordprocessingml.document.main+xml"
)


class PresenterServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = False


class PresenterNotFound(PresenterServiceError):
    def __init__(self, message: str = "Recurso Presenter no encontrado"):
        super().__init__("presenter.not_found", message, status_code=404)


class PresenterConflict(PresenterServiceError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message, status_code=409)


class PresenterForbidden(PresenterServiceError):
    def __init__(self, message: str = "Operacion Presenter no autorizada"):
        super().__init__("presenter.forbidden", message, status_code=403)


class PresenterSchemaNotReady(PresenterServiceError):
    def __init__(self):
        super().__init__(
            "presenter.schema_not_ready",
            "RTM Presenter no esta preparado en este entorno",
            status_code=503,
        )


@dataclass(frozen=True)
class PresenterExternalDocumentUpload:
    content: bytes
    sha256: str
    original_filename: str
    media_type: str
    size_bytes: int
    purpose: str
    extension: str


def validate_external_document_upload(
    *,
    content: bytes,
    original_filename: str,
    declared_mime: str,
    purpose: str,
) -> PresenterExternalDocumentUpload:
    """Valida bytes y metadatos antes de realizar ningun efecto B2."""

    if not isinstance(content, bytes) or not content:
        raise PresenterConflict(
            "presenter.external_document_empty", "El documento esta vacio"
        )
    if len(content) > RTM_PRESENTER_MAX_FILE_BYTES:
        raise PresenterConflict(
            "presenter.external_document_too_large",
            "El documento supera el limite Presenter",
        )
    clean_filename = safe_filename(original_filename)
    clean_purpose = str(purpose or "").strip().lower()
    if clean_purpose not in RTM_PRESENTER_EXTERNAL_PURPOSES:
        raise PresenterConflict(
            "presenter.external_document_purpose_invalid",
            "Purpose documental no admitido",
        )
    clean_declared_mime = str(declared_mime or "").strip().lower()
    if clean_declared_mime not in _EXTERNAL_MEDIA_EXTENSIONS:
        raise PresenterConflict(
            "presenter.external_document_type_invalid",
            "Tipo documental no admitido",
        )

    detected_mime: str | None = None
    if content.startswith(b"%PDF-") and content.rstrip().endswith(b"%%EOF"):
        detected_mime = "application/pdf"
    elif (
        len(content) >= 33
        and content.startswith(b"\x89PNG\r\n\x1a\n")
        and content[8:12] == b"\x00\x00\x00\r"
        and content[12:16] == b"IHDR"
        and int.from_bytes(content[16:20], "big") > 0
        and int.from_bytes(content[20:24], "big") > 0
        and content.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")
    ):
        detected_mime = "image/png"
    elif (
        len(content) >= 4
        and content.startswith(b"\xff\xd8")
        and content.endswith(b"\xff\xd9")
        and b"\xff\xda" in content
        and any(
            marker in content
            for marker in (
                b"\xff\xc0",
                b"\xff\xc1",
                b"\xff\xc2",
                b"\xff\xc3",
                b"\xff\xc5",
                b"\xff\xc6",
                b"\xff\xc7",
                b"\xff\xc9",
                b"\xff\xca",
                b"\xff\xcb",
                b"\xff\xcd",
                b"\xff\xce",
                b"\xff\xcf",
            )
        )
    ):
        detected_mime = "image/jpeg"
    elif content.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(content), mode="r") as archive:
                infos = archive.infolist()
                names = {info.filename for info in infos}
                unsafe_name = any(
                    not info.filename
                    or "\x00" in info.filename
                    or "\\" in info.filename
                    or info.filename.startswith("/")
                    or ".." in info.filename.split("/")
                    for info in infos
                )
                encrypted = any(info.flag_bits & 0x1 for info in infos)
                expanded_bytes = sum(info.file_size for info in infos)
                content_types = archive.getinfo("[Content_Types].xml")
                document_xml = archive.getinfo("word/document.xml")
                if (
                    not infos
                    or len(infos) > 4096
                    or unsafe_name
                    or encrypted
                    or expanded_bytes > 100 * 1024 * 1024
                    or content_types.file_size <= 0
                    or content_types.file_size > 1024 * 1024
                    or document_xml.file_size <= 0
                    or "[Content_Types].xml" not in names
                    or "word/document.xml" not in names
                ):
                    raise PresenterConflict(
                        "presenter.external_document_structure_invalid",
                        "Estructura DOCX no admitida",
                    )
                if _DOCX_CONTENT_TYPE not in archive.read("[Content_Types].xml"):
                    raise PresenterConflict(
                        "presenter.external_document_structure_invalid",
                        "Estructura DOCX no admitida",
                    )
                detected_mime = (
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                )
        except PresenterServiceError:
            raise
        except Exception as exc:
            raise PresenterConflict(
                "presenter.external_document_structure_invalid",
                "Estructura DOCX no admitida",
            ) from exc

    if detected_mime is None or detected_mime != clean_declared_mime:
        raise PresenterConflict(
            "presenter.external_document_signature_mismatch",
            "Firma y tipo documental no coinciden",
        )
    allowed_extensions = _EXTERNAL_MEDIA_EXTENSIONS[detected_mime]
    extension = next(
        (suffix for suffix in allowed_extensions if clean_filename.lower().endswith(suffix)),
        None,
    )
    if extension is None:
        raise PresenterConflict(
            "presenter.external_document_extension_mismatch",
            "Extension y tipo documental no coinciden",
        )
    return PresenterExternalDocumentUpload(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        original_filename=clean_filename,
        media_type=detected_mime,
        size_bytes=len(content),
        purpose=clean_purpose,
        extension=extension,
    )


@dataclass(frozen=True)
class PresenterItemSelection:
    document_version_id: str
    item_order: int
    field_code: str
    portal_filename: str


class PresenterRepository(Protocol):
    def presenter_schema_ready(self, conn: Any) -> bool: ...
    def has_active_synthetic_case_access(self, conn: Any, *, case_id: str, operator_id: str) -> bool: ...
    def list_document_versions(self, conn: Any, *, case_id: str) -> Sequence[Mapping[str, Any]]: ...
    def insert_external_document_version(self, conn: Any, *, case_id: str, created_by_operator_id: str, upload: PresenterExternalDocumentUpload, storage_bucket: str, storage_key: str, supersedes_document_version_id: str | None) -> Mapping[str, Any]: ...
    def list_destination_profiles(self, conn: Any) -> Sequence[Mapping[str, Any]]: ...
    def lock_document_version_lineages(self, conn: Any, *, case_id: str, document_version_ids: Sequence[str]) -> None: ...
    def load_document_version(self, conn: Any, *, case_id: str, document_version_id: str, for_update: bool = False) -> Mapping[str, Any] | None: ...
    def load_destination_profile(self, conn: Any, *, profile_id: str) -> Mapping[str, Any] | None: ...
    def next_package_identity(self, conn: Any, *, case_id: str, destination_profile_id: str, supersedes_package_id: str | None) -> Mapping[str, Any]: ...
    def load_idempotent_frozen_package(self, conn: Any, *, operator_id: str, idempotency_key: str) -> Mapping[str, Any] | None: ...
    def persist_frozen_package(self, conn: Any, *, package: FrozenPresenterPackage, supersedes_package_id: str | None, idempotency_key: str, request_sha256: str) -> None: ...
    def load_frozen_package(self, conn: Any, *, case_id: str, package_id: str, for_update: bool = False) -> Mapping[str, Any] | None: ...
    def insert_ticket(self, conn: Any, *, binding: PresenterTicketBinding) -> None: ...
    def consume_ticket(self, conn: Any, *, ticket_sha256: str, actor: PresenterActorContext, portal_origin: str, used_at: datetime) -> Mapping[str, Any] | None: ...
    def load_document_bytes(self, conn: Any, *, case_id: str, document_version_id: str, expected_sha256: str) -> bytes: ...
    def append_audit(self, conn: Any, *, event_type: str, reason_code: str, actor: PresenterActorContext, case_id: str, package_id: str | None = None, package_item_id: str | None = None, handoff_ticket_id: str | None = None, admin_export_id: str | None = None, payload: Mapping[str, Any] | None = None) -> None: ...
    def insert_admin_export(self, conn: Any, *, export_id: str, package: FrozenPresenterPackage, actor: PresenterActorContext, reason: str, reauthenticated_at: datetime, reauthentication_evidence_sha256: str, export_scope: Mapping[str, Any], watermark: str, source_hashes: Sequence[str], manifest_sha256: str, export_sha256: str, expires_at: datetime) -> None: ...


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _row_mapping(result: Any) -> Mapping[str, Any] | None:
    mappings = getattr(result, "mappings", None)
    if callable(mappings):
        return mappings().first()
    row = result.first()
    return dict(row._mapping) if row is not None else None


class SqlPresenterRepository:
    """Repositorio SQL. Las referencias B2 permanecen dentro de load bytes."""

    def presenter_schema_ready(self, conn: Any) -> bool:
        """Comprueba el contrato ya migrado sin ejecutar DDL en runtime."""

        from rtm_presenter_schema import (
            PRESENTER_REQUIRED_COLUMNS,
            PRESENTER_REQUIRED_COLUMN_TYPES,
            PRESENTER_REQUIRED_CONSTRAINTS,
            PRESENTER_REQUIRED_CONSTRAINT_TABLES,
            PRESENTER_REQUIRED_FUNCTIONS,
            PRESENTER_REQUIRED_INDEXES,
            PRESENTER_REQUIRED_INDEX_TABLES,
            PRESENTER_REQUIRED_TRIGGERS,
            PRESENTER_REQUIRED_TRIGGER_BINDINGS,
            RTM_PRESENTER_SCHEMA_VERSION,
        )
        from scripts.rtm_staging_presenter_schema import (
            PRESENTER_SCHEMA_SCRIPT_VERSION,
            schema_contract,
        )

        try:
            if (
                set(PRESENTER_REQUIRED_COLUMN_TYPES)
                != set(PRESENTER_REQUIRED_COLUMNS)
                or any(
                    set(PRESENTER_REQUIRED_COLUMN_TYPES[table_name])
                    != required_columns
                    for table_name, required_columns
                    in PRESENTER_REQUIRED_COLUMNS.items()
                )
                or set(PRESENTER_REQUIRED_INDEX_TABLES)
                != PRESENTER_REQUIRED_INDEXES
                or set(PRESENTER_REQUIRED_TRIGGER_BINDINGS)
                != PRESENTER_REQUIRED_TRIGGERS
                or set(PRESENTER_REQUIRED_CONSTRAINT_TABLES)
                != PRESENTER_REQUIRED_CONSTRAINTS
            ):
                return False

            column_rows = conn.execute(
                text(
                    """
                    SELECT column_state.table_name,
                           column_state.column_name,
                           column_state.udt_name AS type_name
                    FROM information_schema.columns column_state
                    JOIN information_schema.tables table_state
                      ON table_state.table_schema=column_state.table_schema
                     AND table_state.table_name=column_state.table_name
                     AND table_state.table_type='BASE TABLE'
                    WHERE column_state.table_schema='public'
                      AND column_state.table_name=
                          ANY(CAST(:table_names AS TEXT[]))
                    """
                ),
                {"table_names": sorted(PRESENTER_REQUIRED_COLUMNS)},
            ).mappings().all()
            actual_column_types: dict[str, dict[str, str]] = {
                table_name: {} for table_name in PRESENTER_REQUIRED_COLUMNS
            }
            for row in column_rows:
                table_name = str(row["table_name"])
                if table_name in actual_column_types:
                    actual_column_types[table_name][str(row["column_name"])] = (
                        str(row["type_name"])
                    )
            if any(
                any(
                    actual_column_types[table_name].get(column_name)
                    != expected_type
                    for column_name, expected_type in expected_types.items()
                )
                for table_name, expected_types
                in PRESENTER_REQUIRED_COLUMN_TYPES.items()
            ):
                return False

            index_rows = conn.execute(
                text(
                    """
                    SELECT index_class.relname AS object_name,
                           target_table.relname AS table_name,
                           index_state.indisunique AS is_unique
                    FROM pg_index index_state
                    JOIN pg_class index_class
                      ON index_class.oid=index_state.indexrelid
                    JOIN pg_class target_table
                      ON target_table.oid=index_state.indrelid
                    JOIN pg_namespace index_namespace
                      ON index_namespace.oid=index_class.relnamespace
                    JOIN pg_namespace table_namespace
                      ON table_namespace.oid=target_table.relnamespace
                    WHERE index_namespace.nspname='public'
                      AND table_namespace.nspname='public'
                      AND index_state.indisvalid=TRUE
                      AND index_state.indisready=TRUE
                    """
                )
            ).mappings().all()
            trigger_rows = conn.execute(
                text(
                    """
                    SELECT trigger_state.tgname AS object_name,
                           target_table.relname AS table_name,
                           trigger_function.proname AS function_name
                    FROM pg_trigger trigger_state
                    JOIN pg_class target_table
                      ON target_table.oid=trigger_state.tgrelid
                    JOIN pg_namespace table_namespace
                      ON table_namespace.oid=target_table.relnamespace
                    JOIN pg_proc trigger_function
                      ON trigger_function.oid=trigger_state.tgfoid
                    JOIN pg_namespace function_namespace
                      ON function_namespace.oid=
                         trigger_function.pronamespace
                    WHERE table_namespace.nspname='public'
                      AND function_namespace.nspname='public'
                      AND trigger_state.tgisinternal=FALSE
                      AND trigger_state.tgenabled <> 'D'
                    """
                )
            ).mappings().all()
            constraint_rows = conn.execute(
                text(
                    """
                    SELECT constraint_state.conname AS object_name,
                           target_table.relname AS table_name,
                           constraint_state.contype AS constraint_type
                    FROM pg_constraint constraint_state
                    JOIN pg_class target_table
                      ON target_table.oid=constraint_state.conrelid
                    JOIN pg_namespace table_namespace
                      ON table_namespace.oid=target_table.relnamespace
                    WHERE table_namespace.nspname='public'
                      AND constraint_state.convalidated=TRUE
                    """
                )
            ).mappings().all()
            function_rows = conn.execute(
                text(
                    """
                    SELECT function_state.proname AS object_name,
                           return_type.typname AS return_type,
                           function_language.lanname AS language_name,
                           function_state.pronargs AS argument_count
                    FROM pg_proc function_state
                    JOIN pg_namespace function_namespace
                      ON function_namespace.oid=function_state.pronamespace
                    JOIN pg_type return_type
                      ON return_type.oid=function_state.prorettype
                    JOIN pg_language function_language
                      ON function_language.oid=function_state.prolang
                    WHERE function_namespace.nspname='public'
                      AND function_state.proname=
                          ANY(CAST(:function_names AS TEXT[]))
                    """
                ),
                {"function_names": sorted(PRESENTER_REQUIRED_FUNCTIONS)},
            ).mappings().all()

            actual_indexes = {
                str(row["object_name"]): (
                    str(row["table_name"]),
                    row["is_unique"] is True,
                )
                for row in index_rows
                if str(row["object_name"]) in PRESENTER_REQUIRED_INDEXES
            }
            expected_indexes = {
                name: (table_name, name.startswith("uq_"))
                for name, table_name
                in PRESENTER_REQUIRED_INDEX_TABLES.items()
            }
            if actual_indexes != expected_indexes:
                return False
            actual_triggers = {
                str(row["object_name"]): (
                    str(row["table_name"]),
                    str(row["function_name"]),
                )
                for row in trigger_rows
                if str(row["object_name"]) in PRESENTER_REQUIRED_TRIGGERS
            }
            if actual_triggers != PRESENTER_REQUIRED_TRIGGER_BINDINGS:
                return False
            actual_constraints = {
                str(row["object_name"]): (
                    str(row["table_name"]),
                    str(row["constraint_type"]),
                )
                for row in constraint_rows
                if str(row["object_name"])
                in PRESENTER_REQUIRED_CONSTRAINTS
            }
            expected_constraints = {
                name: (table_name, "c")
                for name, table_name
                in PRESENTER_REQUIRED_CONSTRAINT_TABLES.items()
            }
            if actual_constraints != expected_constraints:
                return False
            actual_functions = {
                (
                    str(row["object_name"]),
                    str(row["return_type"]),
                    str(row["language_name"]),
                    int(row["argument_count"]),
                )
                for row in function_rows
            }
            expected_functions = {
                (function_name, "trigger", "plpgsql", 0)
                for function_name in PRESENTER_REQUIRED_FUNCTIONS
            }
            if not expected_functions.issubset(actual_functions):
                return False

            migration_metadata = conn.execute(
                text(
                    """
                    SELECT metadata
                    FROM rtm_management_schema_migrations
                    WHERE name=:schema_version
                    LIMIT 1
                    """
                ),
                {"schema_version": RTM_PRESENTER_SCHEMA_VERSION},
            ).scalar_one_or_none()
            if isinstance(migration_metadata, str):
                migration_metadata = json.loads(migration_metadata)
            if not isinstance(migration_metadata, Mapping):
                return False
            contract = schema_contract()
            expected_migration = {
                "source": PRESENTER_SCHEMA_SCRIPT_VERSION,
                "schema_version": RTM_PRESENTER_SCHEMA_VERSION,
                "schema_contract_sha256": contract["sha256"],
                "scope": "staging_isolated_synthetic_schema_only",
                "synthetic_only": True,
                "real_data_allowed": False,
                "profiles_seeded": False,
                "documents_seeded": False,
                "cases_seeded": False,
                "operators_seeded": False,
                "b2_used": False,
                "external_effects": False,
                "destructive": False,
            }
            return all(
                migration_metadata.get(key) == value
                for key, value in expected_migration.items()
            )
        except Exception:
            return False

    def has_active_synthetic_case_access(
        self,
        conn: Any,
        *,
        case_id: str,
        operator_id: str,
    ) -> bool:
        """Autoriza por tenant A1-S y asignacion operativa aceptada al caso.

        Es deliberadamente existencial: no devuelve tenant, binding ni
        membership, de modo que un rechazo no puede usarse para enumerarlos.
        Si el esquema A1-S no esta instalado, la conexion falla o cualquier
        UUID es invalido, el resultado sigue siendo una denegacion cerrada.
        """

        try:
            allowed = conn.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM cases c
                        JOIN rtm_connect_a1s_case_bindings b
                          ON b.case_id=c.id
                        JOIN rtm_connect_a1s_tenants t
                          ON t.id=b.tenant_id
                        JOIN rtm_connect_a1s_memberships m
                          ON m.tenant_id=b.tenant_id
                        JOIN rtm_work_assignments w
                          ON w.case_id=c.id
                         AND w.operator_id=CAST(:operator_id AS UUID)
                        WHERE c.id=CAST(:case_id AS UUID)
                          AND COALESCE(c.test_mode,FALSE)=TRUE
                          AND b.status='active'
                          AND b.synthetic_only=TRUE
                          AND b.revoked_at IS NULL
                          AND b.metadata @> '{"synthetic_marker":
                              "RTM_A1S_SYNTHETIC_ONLY", "synthetic_only":true,
                              "test_mode":true}'::jsonb
                          AND t.status='active'
                          AND t.synthetic_only=TRUE
                          AND t.metadata @> '{"synthetic_marker":
                              "RTM_A1S_SYNTHETIC_ONLY", "synthetic_only":true}'::jsonb
                          AND m.operator_id=CAST(:operator_id AS UUID)
                          AND m.status='active'
                          AND m.synthetic_only=TRUE
                          AND m.revoked_at IS NULL
                          AND m.metadata @> '{"synthetic_marker":
                              "RTM_A1S_SYNTHETIC_ONLY", "synthetic_only":true}'::jsonb
                          AND w.status='active'
                          AND w.assignment_role IN (
                              'responsible', 'reviewer', 'supervisor'
                          )
                          AND w.accepted_at IS NOT NULL
                          AND w.released_at IS NULL
                          AND w.metadata @> '{"synthetic_marker":
                              "RTM_PRESENTER_SYNTHETIC_ONLY",
                              "synthetic_only":true}'::jsonb
                    )
                    """
                ),
                {"case_id": case_id, "operator_id": operator_id},
            ).scalar()
        except Exception:
            return False
        return allowed is True

    def list_document_versions(
        self, conn: Any, *, case_id: str
    ) -> Sequence[Mapping[str, Any]]:
        # No seleccionar source_document_id, bucket, key ni metadata.
        return conn.execute(
            text(
                """
                SELECT v.id, v.case_id, v.logical_document_id,
                       v.version_number, v.sha256, v.purpose,
                       CASE
                         WHEN v.state='active' AND v.scan_status='clean'
                          AND EXISTS (
                              SELECT 1
                              FROM rtm_presenter_document_versions newer
                              WHERE newer.case_id=v.case_id
                                AND newer.logical_document_id=v.logical_document_id
                                AND newer.version_number > v.version_number
                                AND newer.metadata @>
                                    '{"synthetic_only":true}'::jsonb
                          )
                         THEN 'superseded'
                         ELSE v.state
                       END AS state,
                       v.scan_status, v.original_filename, v.detected_mime,
                       v.size_bytes, v.source_kind
                FROM rtm_presenter_document_versions v
                WHERE v.case_id=CAST(:case_id AS UUID)
                  AND v.metadata @> '{"synthetic_only":true}'::jsonb
                ORDER BY v.purpose, v.logical_document_id,
                         v.version_number DESC
                LIMIT 500
                """
            ),
            {"case_id": case_id},
        ).mappings().all()

    def insert_external_document_version(
        self,
        conn: Any,
        *,
        case_id: str,
        created_by_operator_id: str,
        upload: PresenterExternalDocumentUpload,
        storage_bucket: str,
        storage_key: str,
        supersedes_document_version_id: str | None,
    ) -> Mapping[str, Any]:
        """Inserta custodia y version pendiente dentro de una sola transaccion."""

        logical_document_id = str(uuid.uuid4())
        version_number = 1
        supersedes_version_id: str | None = None
        if supersedes_document_version_id is not None:
            predecessor = _row_mapping(
                conn.execute(
                    text(
                        """
                        SELECT id, logical_document_id, version_number, purpose
                        FROM rtm_presenter_document_versions
                        WHERE id=CAST(:document_version_id AS UUID)
                          AND case_id=CAST(:case_id AS UUID)
                          AND metadata @> '{"synthetic_only":true}'::jsonb
                        """
                    ),
                    {
                        "document_version_id": supersedes_document_version_id,
                        "case_id": case_id,
                    },
                )
            )
            if not predecessor:
                raise PresenterNotFound("Version documental predecesora no encontrada")
            logical_document_id = str(predecessor["logical_document_id"])
            conn.execute(
                text(
                    """
                    SELECT pg_advisory_xact_lock(
                        hashtextextended(:lock_scope, 0)
                    )
                    """
                ),
                {
                    "lock_scope": (
                        "rtm-presenter-document-lineage:"
                        f"{case_id}:{logical_document_id}"
                    )
                },
            )
            predecessor = _row_mapping(
                conn.execute(
                    text(
                        """
                        SELECT id, logical_document_id, version_number, purpose
                        FROM rtm_presenter_document_versions
                        WHERE id=CAST(:document_version_id AS UUID)
                          AND case_id=CAST(:case_id AS UUID)
                          AND logical_document_id=
                              CAST(:logical_document_id AS UUID)
                          AND metadata @> '{"synthetic_only":true}'::jsonb
                        FOR UPDATE
                        """
                    ),
                    {
                        "document_version_id": supersedes_document_version_id,
                        "case_id": case_id,
                        "logical_document_id": logical_document_id,
                    },
                )
            )
            if not predecessor:
                raise PresenterNotFound("Version documental predecesora no encontrada")
            if str(predecessor["purpose"]) != upload.purpose:
                raise PresenterConflict(
                    "presenter.external_document_purpose_mismatch",
                    "La nueva version debe conservar el purpose documental",
                )
            predecessor_version = int(predecessor["version_number"])
            latest_version = conn.execute(
                text(
                    """
                    SELECT MAX(version_number)
                    FROM rtm_presenter_document_versions
                    WHERE case_id=CAST(:case_id AS UUID)
                      AND logical_document_id=CAST(:logical_document_id AS UUID)
                      AND metadata @> '{"synthetic_only":true}'::jsonb
                    """
                ),
                {
                    "case_id": case_id,
                    "logical_document_id": logical_document_id,
                },
            ).scalar()
            if latest_version is None or int(latest_version) != predecessor_version:
                raise PresenterConflict(
                    "presenter.external_document_predecessor_stale",
                    "La version predecesora ya no es la ultima",
                )
            version_number = predecessor_version + 1
            supersedes_version_id = str(predecessor["id"])

        source = _row_mapping(
            conn.execute(
                text(
                    """
                    INSERT INTO documents(
                        case_id, kind, b2_bucket, b2_key, sha256,
                        mime, size_bytes, created_at
                    ) VALUES (
                        CAST(:case_id AS UUID), 'external_revision',
                        :storage_bucket, :storage_key, :sha256,
                        :mime, :size_bytes, NOW()
                    )
                    RETURNING id
                    """
                ),
                {
                    "case_id": case_id,
                    "storage_bucket": storage_bucket,
                    "storage_key": storage_key,
                    "sha256": upload.sha256,
                    "mime": upload.media_type,
                    "size_bytes": upload.size_bytes,
                },
            )
        )
        if not source:
            raise PresenterConflict(
                "presenter.external_document_not_registered",
                "No se registro la custodia documental",
            )
        metadata = {
            "ingest_channel": "presenter_external_upload",
            "security_disposition": "pending_security_scan",
            "synthetic_marker": RTM_PRESENTER_SYNTHETIC_MARKER,
            "synthetic_only": True,
        }
        version = _row_mapping(
            conn.execute(
                text(
                    """
                    INSERT INTO rtm_presenter_document_versions(
                        case_id, logical_document_id, version_number,
                        supersedes_version_id, source_document_id, sha256,
                        purpose, state, scan_status, original_filename,
                        detected_mime, size_bytes, source_kind,
                        created_by_operator_id, metadata
                    ) VALUES (
                        CAST(:case_id AS UUID),
                        CAST(:logical_document_id AS UUID), :version_number,
                        CAST(:supersedes_version_id AS UUID),
                        CAST(:source_document_id AS UUID), :sha256,
                        :purpose, 'review', 'pending', :original_filename,
                        :detected_mime, :size_bytes, 'external_revision',
                        CAST(:created_by_operator_id AS UUID),
                        CAST(:metadata AS JSONB)
                    )
                    RETURNING id, case_id, logical_document_id, version_number,
                              sha256, purpose, state, scan_status,
                              original_filename, detected_mime, size_bytes,
                              source_kind
                    """
                ),
                {
                    "case_id": case_id,
                    "logical_document_id": logical_document_id,
                    "version_number": version_number,
                    "supersedes_version_id": supersedes_version_id,
                    "source_document_id": str(source["id"]),
                    "sha256": upload.sha256,
                    "purpose": upload.purpose,
                    "original_filename": upload.original_filename,
                    "detected_mime": upload.media_type,
                    "size_bytes": upload.size_bytes,
                    "created_by_operator_id": created_by_operator_id,
                    "metadata": _json(metadata),
                },
            )
        )
        if not version:
            raise PresenterConflict(
                "presenter.external_document_version_not_registered",
                "No se registro la version documental",
            )
        return version

    def list_destination_profiles(self, conn: Any) -> Sequence[Mapping[str, Any]]:
        # Proyeccion de configuracion verificada; no selecciona metadata libre,
        # credenciales, storage ni ningun localizador documental.
        return conn.execute(
            text(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (profile_code)
                           id, profile_code, version_number, status,
                           authority_code, display_name, portal_origin,
                           requirements, profile_sha256,
                           created_by_operator_id, verified_by_operator_id,
                           verified_at,
                           metadata @> '{"synthetic_only":true}'::jsonb
                               AS synthetic_only
                    FROM rtm_presenter_destination_profiles
                    ORDER BY profile_code, version_number DESC, id DESC
                )
                SELECT id, profile_code, version_number, status,
                       authority_code, display_name, portal_origin,
                       requirements, profile_sha256,
                       created_by_operator_id, verified_by_operator_id,
                       verified_at
                FROM latest
                WHERE status='active'
                  AND synthetic_only=TRUE
                  AND created_by_operator_id IS NOT NULL
                  AND verified_by_operator_id IS NOT NULL
                  AND created_by_operator_id <> verified_by_operator_id
                  AND verified_at IS NOT NULL
                ORDER BY authority_code, profile_code, version_number DESC
                LIMIT 100
                """
            )
        ).mappings().all()

    def load_document_version(
        self,
        conn: Any,
        *,
        case_id: str,
        document_version_id: str,
        for_update: bool = False,
    ) -> Mapping[str, Any] | None:
        if for_update:
            # Esta consulta es deliberadamente independiente de la lectura de
            # la version. Si debe esperar a un INSERT concurrente, la siguiente
            # sentencia obtiene un snapshot nuevo y su NOT EXISTS ve cualquier
            # sucesora ya confirmada, incluso si sigue pendiente de seguridad.
            conn.execute(
                text(
                    """
                    SELECT pg_advisory_xact_lock(
                        hashtextextended(
                            'rtm-presenter-document-lineage:'
                            || case_id::TEXT || ':'
                            || logical_document_id::TEXT,
                            0
                        )
                    )
                    FROM rtm_presenter_document_versions
                    WHERE id=CAST(:document_version_id AS UUID)
                      AND case_id=CAST(:case_id AS UUID)
                    LIMIT 1
                    """
                ),
                {
                    "document_version_id": document_version_id,
                    "case_id": case_id,
                },
            )
        lock = " FOR UPDATE" if for_update else ""
        return _row_mapping(
            conn.execute(
                text(
                    """
                    SELECT id, case_id, logical_document_id, version_number,
                           supersedes_version_id, sha256, purpose, state,
                           scan_status, original_filename, detected_mime,
                           size_bytes, source_kind
                    FROM rtm_presenter_document_versions
                    WHERE id=CAST(:document_version_id AS UUID)
                      AND case_id=CAST(:case_id AS UUID)
                      AND metadata @> '{"synthetic_only":true}'::jsonb
                      AND NOT EXISTS (
                          SELECT 1
                          FROM rtm_presenter_document_versions newer
                          WHERE newer.case_id=
                                rtm_presenter_document_versions.case_id
                            AND newer.logical_document_id=
                                rtm_presenter_document_versions.logical_document_id
                            AND newer.version_number >
                                rtm_presenter_document_versions.version_number
                            AND newer.metadata @>
                                '{"synthetic_only":true}'::jsonb
                      )
                    """ + lock
                ),
                {
                    "document_version_id": document_version_id,
                    "case_id": case_id,
                },
            )
        )

    def load_destination_profile(
        self, conn: Any, *, profile_id: str
    ) -> Mapping[str, Any] | None:
        return _row_mapping(
            conn.execute(
                text(
                    """
                    SELECT p.id, p.profile_code, p.version_number, p.status,
                           p.authority_code, p.display_name, p.portal_origin,
                           p.requirements, p.profile_sha256,
                           p.created_by_operator_id,
                           p.verified_by_operator_id, p.verified_at
                    FROM rtm_presenter_destination_profiles p
                    WHERE p.id=CAST(:profile_id AS UUID)
                      AND p.status='active'
                      AND p.created_by_operator_id IS NOT NULL
                      AND p.verified_by_operator_id IS NOT NULL
                      AND p.created_by_operator_id <>
                          p.verified_by_operator_id
                      AND p.verified_at IS NOT NULL
                      AND p.metadata @> '{"synthetic_only":true}'::jsonb
                      AND NOT EXISTS (
                          SELECT 1
                          FROM rtm_presenter_destination_profiles newer
                          WHERE newer.profile_code=p.profile_code
                            AND newer.version_number > p.version_number
                      )
                    LIMIT 1
                    """
                ),
                {"profile_id": profile_id},
            )
        )

    def lock_document_version_lineages(
        self,
        conn: Any,
        *,
        case_id: str,
        document_version_ids: Sequence[str],
    ) -> None:
        """Bloquea lineas documentales en orden estable para un freeze."""

        version_ids = tuple(str(value) for value in document_version_ids)
        if not version_ids:
            return
        lineages = conn.execute(
            text(
                """
                SELECT DISTINCT case_id, logical_document_id
                FROM rtm_presenter_document_versions
                WHERE case_id=CAST(:case_id AS UUID)
                  AND id=ANY(CAST(:document_version_ids AS UUID[]))
                ORDER BY case_id, logical_document_id
                """
            ),
            {
                "case_id": case_id,
                "document_version_ids": list(version_ids),
            },
        ).mappings().all()
        for lineage in lineages:
            conn.execute(
                text(
                    """
                    SELECT pg_advisory_xact_lock(
                        hashtextextended(:lock_scope, 0)
                    )
                    """
                ),
                {
                    "lock_scope": (
                        "rtm-presenter-document-lineage:"
                        f"{lineage['case_id']}:"
                        f"{lineage['logical_document_id']}"
                    )
                },
            )

    def next_package_identity(
        self,
        conn: Any,
        *,
        case_id: str,
        destination_profile_id: str,
        supersedes_package_id: str | None,
    ) -> Mapping[str, Any]:
        if not supersedes_package_id:
            return {
                "logical_package_id": str(uuid.uuid4()),
                "package_version": 1,
                "supersedes_package_id": None,
            }
        prior = _row_mapping(
            conn.execute(
                text(
                    """
                    SELECT id, case_id, logical_package_id, package_version,
                           destination_profile_id, status
                    FROM rtm_presenter_filing_packages
                    WHERE id=CAST(:id AS UUID)
                      AND case_id=CAST(:case_id AS UUID)
                    FOR UPDATE
                    """
                ),
                {"id": supersedes_package_id, "case_id": case_id},
            )
        )
        if not prior:
            raise PresenterNotFound("Paquete anterior no encontrado")
        if str(prior["destination_profile_id"]) != destination_profile_id:
            raise PresenterConflict(
                "presenter.destination_changed",
                "Una nueva version no puede cambiar de perfil de destino",
            )
        if str(prior["status"]) != "frozen":
            raise PresenterConflict(
                "presenter.superseded_package_not_frozen",
                "Solo se puede sustituir un paquete congelado",
            )
        return {
            "logical_package_id": str(prior["logical_package_id"]),
            "package_version": int(prior["package_version"]) + 1,
            "supersedes_package_id": str(prior["id"]),
        }

    def persist_frozen_package(
        self,
        conn: Any,
        *,
        package: FrozenPresenterPackage,
        supersedes_package_id: str | None,
        idempotency_key: str,
        request_sha256: str,
    ) -> None:
        manifest = package.manifest_material()
        inserted = _row_mapping(
            conn.execute(
                text(
                    """
                    INSERT INTO rtm_presenter_filing_packages(
                        id, case_id, logical_package_id, package_version,
                        supersedes_package_id, destination_profile_id,
                        representation_mode, authorization_document_version_id,
                        status, manifest, manifest_sha256, expected_item_count,
                        created_by_operator_id, frozen_by_operator_id, frozen_at,
                        expires_at, created_at, metadata
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:case_id AS UUID),
                        CAST(:logical_package_id AS UUID), :package_version,
                        CAST(:supersedes_package_id AS UUID),
                        CAST(:destination_profile_id AS UUID),
                        :representation_mode,
                        CAST(:authorization_document_version_id AS UUID),
                        'draft', CAST(:manifest AS JSONB), :manifest_sha256,
                        :expected_item_count, CAST(:created_by AS UUID),
                        NULL, NULL, CAST(:expires_at AS TIMESTAMPTZ), NOW(),
                        CAST(:metadata AS JSONB)
                    ) RETURNING id
                    """
                ),
                {
                    "id": package.package_id,
                    "case_id": package.case_id,
                    "logical_package_id": package.logical_package_id,
                    "package_version": package.package_version,
                    "supersedes_package_id": supersedes_package_id,
                    "destination_profile_id": package.destination_profile_id,
                    "representation_mode": package.representation_mode,
                    "authorization_document_version_id": (
                        package.authorization_document_version_id
                    ),
                    "manifest": _json(manifest),
                    "manifest_sha256": package.manifest_sha256,
                    "expected_item_count": len(package.items),
                    "created_by": package.created_by_operator_id,
                    "expires_at": package.expires_at,
                    "metadata": _json(
                        {
                            "contract_version": RTM_PRESENTER_CONTRACT_VERSION,
                            "synthetic_marker": RTM_PRESENTER_SYNTHETIC_MARKER,
                            "synthetic_only": True,
                        }
                    ),
                },
            )
        )
        if not inserted:
            raise PresenterConflict(
                "presenter.package_insert_failed", "No se pudo crear el paquete"
            )
        for item in package.items:
            item_material = item.material()
            conn.execute(
                text(
                    """
                    INSERT INTO rtm_presenter_package_items(
                        id, package_id, case_id, item_order,
                        document_version_id, document_sha256, field_code,
                        purpose, portal_filename, required, item_manifest,
                        item_sha256, created_at
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:package_id AS UUID),
                        CAST(:case_id AS UUID), :item_order,
                        CAST(:document_version_id AS UUID), :document_sha256,
                        :field_code, :purpose, :portal_filename, :required,
                        CAST(:item_manifest AS JSONB), :item_sha256, NOW()
                    )
                    """
                ),
                {
                    "id": item.item_id,
                    "package_id": package.package_id,
                    "case_id": package.case_id,
                    "item_order": item.item_order,
                    "document_version_id": item.document_version_id,
                    "document_sha256": item.document_sha256,
                    "field_code": item.field_code,
                    "purpose": item.purpose,
                    "portal_filename": item.portal_filename,
                    "required": item.required,
                    "item_manifest": _json(item_material),
                    "item_sha256": canonical_sha256(item_material),
                },
            )
        frozen = _row_mapping(
            conn.execute(
                text(
                    """
                    UPDATE rtm_presenter_filing_packages
                    SET status='frozen', frozen_by_operator_id=CAST(:operator AS UUID),
                        frozen_at=CAST(:frozen_at AS TIMESTAMPTZ)
                    WHERE id=CAST(:id AS UUID) AND status='draft'
                    RETURNING id
                    """
                ),
                {
                    "id": package.package_id,
                    "operator": package.frozen_by_operator_id,
                    "frozen_at": package.frozen_at,
                },
            )
        )
        if not frozen:
            raise PresenterConflict(
                "presenter.package_freeze_failed", "No se pudo congelar el paquete"
            )
        conn.execute(
            text(
                """
                INSERT INTO rtm_presenter_idempotency_keys(
                    id, operator_id, idempotency_key, request_sha256,
                    case_id, package_id, created_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:operator_id AS UUID),
                    :idempotency_key, :request_sha256,
                    CAST(:case_id AS UUID), CAST(:package_id AS UUID), NOW()
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "operator_id": package.created_by_operator_id,
                "idempotency_key": idempotency_key,
                "request_sha256": request_sha256,
                "case_id": package.case_id,
                "package_id": package.package_id,
            },
        )

    def load_idempotent_frozen_package(
        self,
        conn: Any,
        *,
        operator_id: str,
        idempotency_key: str,
    ) -> Mapping[str, Any] | None:
        conn.execute(
            text(
                """
                SELECT pg_advisory_xact_lock(
                    hashtextextended(:lock_scope, 0)
                )
                """
            ),
            {"lock_scope": f"rtm-presenter:{operator_id}:{idempotency_key}"},
        )
        binding = _row_mapping(
            conn.execute(
                text(
                    """
                    SELECT case_id, package_id, request_sha256
                    FROM rtm_presenter_idempotency_keys
                    WHERE operator_id=CAST(:operator_id AS UUID)
                      AND idempotency_key=:idempotency_key
                    LIMIT 1
                    """
                ),
                {
                    "operator_id": operator_id,
                    "idempotency_key": idempotency_key,
                },
            )
        )
        if not binding:
            return None
        package = self.load_frozen_package(
            conn,
            case_id=str(binding["case_id"]),
            package_id=str(binding["package_id"]),
            for_update=True,
        )
        if not package:
            raise PresenterConflict(
                "presenter.idempotency_binding_invalid",
                "La clave idempotente no tiene un paquete verificable",
            )
        return {
            **dict(package),
            "idempotency_request_sha256": str(binding["request_sha256"]),
        }

    def load_frozen_package(
        self,
        conn: Any,
        *,
        case_id: str,
        package_id: str,
        for_update: bool = False,
    ) -> Mapping[str, Any] | None:
        lock = " FOR UPDATE OF p" if for_update else ""
        package = _row_mapping(
            conn.execute(
                text(
                    """
                    SELECT p.id, p.case_id, p.logical_package_id,
                           p.package_version, p.destination_profile_id,
                           p.representation_mode,
                           p.authorization_document_version_id, p.status,
                           p.manifest, p.manifest_sha256,
                           p.created_by_operator_id, p.frozen_by_operator_id,
                           p.frozen_at, p.expires_at,
                           d.profile_code, d.version_number AS profile_version,
                           d.portal_origin, d.profile_sha256, d.status AS profile_status
                    FROM rtm_presenter_filing_packages p
                    JOIN rtm_presenter_destination_profiles d
                      ON d.id=p.destination_profile_id
                    WHERE p.id=CAST(:package_id AS UUID)
                      AND p.case_id=CAST(:case_id AS UUID)
                      AND p.status='frozen'
                      AND p.metadata @> '{"synthetic_only":true}'::jsonb
                    """ + lock
                ),
                {"package_id": package_id, "case_id": case_id},
            )
        )
        if not package:
            return None
        items = conn.execute(
            text(
                """
                SELECT i.id, i.package_id, i.case_id, i.item_order,
                       i.document_version_id, i.document_sha256, i.field_code,
                       i.purpose, i.portal_filename, i.required,
                       i.item_manifest, i.item_sha256,
                       v.logical_document_id, v.version_number,
                       v.sha256 AS current_document_sha256,
                       CASE
                         WHEN EXISTS (
                           SELECT 1
                           FROM rtm_presenter_document_versions newer
                           WHERE newer.case_id=v.case_id
                             AND newer.logical_document_id=v.logical_document_id
                             AND newer.version_number > v.version_number
                             AND newer.metadata @>
                                 '{"synthetic_only":true}'::jsonb
                         ) THEN 'superseded'
                         ELSE v.state
                       END AS state,
                       v.scan_status, v.detected_mime, v.size_bytes,
                       v.original_filename
                FROM rtm_presenter_package_items i
                JOIN rtm_presenter_document_versions v
                  ON v.id=i.document_version_id AND v.case_id=i.case_id
                WHERE i.package_id=CAST(:package_id AS UUID)
                  AND i.case_id=CAST(:case_id AS UUID)
                ORDER BY i.item_order
                """
            ),
            {"package_id": package_id, "case_id": case_id},
        ).mappings().all()
        return {**dict(package), "items": list(items)}

    def insert_ticket(self, conn: Any, *, binding: PresenterTicketBinding) -> None:
        conn.execute(
            text(
                """
                INSERT INTO rtm_presenter_handoff_tickets(
                    id, ticket_hash, operator_id, operator_session_id,
                    extension_client_id, case_id, package_id, package_item_id,
                    portal_origin, field_code, issued_at, expires_at,
                    used_at, created_at
                ) VALUES (
                    CAST(:id AS UUID), :ticket_hash, CAST(:operator_id AS UUID),
                    CAST(:operator_session_id AS UUID), :extension_client_id,
                    CAST(:case_id AS UUID), CAST(:package_id AS UUID),
                    CAST(:package_item_id AS UUID), :portal_origin, :field_code,
                    CAST(:issued_at AS TIMESTAMPTZ),
                    CAST(:expires_at AS TIMESTAMPTZ), NULL, NOW()
                )
                """
            ),
            {
                "id": binding.ticket_id,
                "ticket_hash": binding.ticket_sha256,
                "operator_id": binding.operator_id,
                "operator_session_id": binding.operator_session_id,
                "extension_client_id": binding.extension_client_id,
                "case_id": binding.case_id,
                "package_id": binding.package_id,
                "package_item_id": binding.package_item_id,
                "portal_origin": binding.portal_origin,
                "field_code": binding.field_code,
                "issued_at": binding.issued_at,
                "expires_at": binding.expires_at,
            },
        )

    def consume_ticket(
        self,
        conn: Any,
        *,
        ticket_sha256: str,
        actor: PresenterActorContext,
        portal_origin: str,
        used_at: datetime,
    ) -> Mapping[str, Any] | None:
        return _row_mapping(
            conn.execute(
                text(
                    """
                    UPDATE rtm_presenter_handoff_tickets
                    SET used_at=CAST(:used_at AS TIMESTAMPTZ)
                    WHERE ticket_hash=:ticket_hash
                      AND operator_id=CAST(:operator_id AS UUID)
                      AND operator_session_id=CAST(:operator_session_id AS UUID)
                      AND extension_client_id=:extension_client_id
                      AND portal_origin=:portal_origin
                      AND used_at IS NULL
                      AND issued_at <= CAST(:used_at AS TIMESTAMPTZ)
                      AND expires_at > CAST(:used_at AS TIMESTAMPTZ)
                    RETURNING id, ticket_hash, operator_id, operator_session_id,
                              extension_client_id, case_id, package_id,
                              package_item_id, portal_origin, field_code,
                              issued_at, expires_at, used_at
                    """
                ),
                {
                    "ticket_hash": ticket_sha256,
                    "operator_id": actor.operator_id,
                    "operator_session_id": actor.operator_session_id,
                    "extension_client_id": actor.extension_client_id,
                    "portal_origin": portal_origin,
                    "used_at": used_at,
                },
            )
        )

    def load_document_bytes(
        self,
        conn: Any,
        *,
        case_id: str,
        document_version_id: str,
        expected_sha256: str,
    ) -> bytes:
        # Las coordenadas B2 no salen de este metodo ni se incluyen en auditoria.
        row = _row_mapping(
            conn.execute(
                text(
                    """
                    SELECT d.b2_bucket, d.b2_key
                    FROM rtm_presenter_document_versions v
                    JOIN documents d ON d.id=v.source_document_id
                    WHERE v.id=CAST(:document_version_id AS UUID)
                      AND v.case_id=CAST(:case_id AS UUID)
                      AND v.sha256=:sha256
                      AND v.state='active'
                      AND v.scan_status='clean'
                      AND v.metadata @> '{"synthetic_only":true}'::jsonb
                      AND NOT EXISTS (
                          SELECT 1
                          FROM rtm_presenter_document_versions newer
                          WHERE newer.case_id=v.case_id
                            AND newer.logical_document_id=v.logical_document_id
                            AND newer.version_number > v.version_number
                            AND newer.metadata @>
                                '{"synthetic_only":true}'::jsonb
                      )
                      AND d.case_id=v.case_id
                    LIMIT 1
                    """
                ),
                {
                    "document_version_id": document_version_id,
                    "case_id": case_id,
                    "sha256": expected_sha256,
                },
            )
        )
        if not row or not row.get("b2_bucket") or not row.get("b2_key"):
            raise PresenterNotFound("Bytes documentales no disponibles")
        from b2_storage import download_bytes

        content = download_bytes(str(row["b2_bucket"]), str(row["b2_key"]))
        if not isinstance(content, bytes):
            raise PresenterConflict(
                "presenter.invalid_storage_response", "Storage no devolvio bytes"
            )
        return content

    def append_audit(
        self,
        conn: Any,
        *,
        event_type: str,
        reason_code: str,
        actor: PresenterActorContext,
        case_id: str,
        package_id: str | None = None,
        package_item_id: str | None = None,
        handoff_ticket_id: str | None = None,
        admin_export_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        material = dict(payload or {})
        material.update(
            {
                "service_version": RTM_PRESENTER_SERVICE_VERSION,
                "synthetic_marker": RTM_PRESENTER_SYNTHETIC_MARKER,
                "synthetic_only": True,
            }
        )
        conn.execute(
            text(
                """
                INSERT INTO rtm_presenter_audit_events(
                    id, case_id, package_id, package_item_id,
                    handoff_ticket_id, admin_export_id, actor_type,
                    actor_operator_id, event_type, reason_code, payload,
                    payload_sha256, created_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:case_id AS UUID),
                    CAST(:package_id AS UUID), CAST(:package_item_id AS UUID),
                    CAST(:ticket_id AS UUID), CAST(:export_id AS UUID),
                    :actor_type, CAST(:operator_id AS UUID), :event_type,
                    :reason_code, CAST(:payload AS JSONB), :payload_sha256, NOW()
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "case_id": case_id,
                "package_id": package_id,
                "package_item_id": package_item_id,
                "ticket_id": handoff_ticket_id,
                "export_id": admin_export_id,
                "operator_id": actor.operator_id,
                "actor_type": (
                    "admin"
                    if actor.client_kind is PresenterClientKind.ADMIN_EXPORT
                    else "operator"
                ),
                "event_type": event_type,
                "reason_code": reason_code,
                "payload": _json(material),
                "payload_sha256": canonical_sha256(material),
            },
        )

    def insert_admin_export(
        self,
        conn: Any,
        *,
        export_id: str,
        package: FrozenPresenterPackage,
        actor: PresenterActorContext,
        reason: str,
        reauthenticated_at: datetime,
        reauthentication_evidence_sha256: str,
        export_scope: Mapping[str, Any],
        watermark: str,
        source_hashes: Sequence[str],
        manifest_sha256: str,
        export_sha256: str,
        expires_at: datetime,
    ) -> None:
        conn.execute(
            text(
                """
                INSERT INTO rtm_presenter_admin_exports(
                    id, case_id, package_id, admin_operator_id, reason,
                    reauthenticated_at, reauthentication_evidence_sha256,
                    export_scope, watermark, watermark_sha256, source_hashes,
                    manifest_sha256, export_sha256, export_document_id,
                    created_at, expires_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:case_id AS UUID),
                    CAST(:package_id AS UUID), CAST(:operator_id AS UUID),
                    :reason, :reauthenticated_at, :reauth_sha,
                    CAST(:scope AS JSONB), :watermark, :watermark_sha256,
                    CAST(:source_hashes AS JSONB), :manifest_sha256,
                    :export_sha256, NULL, NOW(), :expires_at
                )
                """
            ),
            {
                "id": export_id,
                "case_id": package.case_id,
                "package_id": package.package_id,
                "operator_id": actor.operator_id,
                "reason": reason,
                "reauthenticated_at": reauthenticated_at,
                "reauth_sha": reauthentication_evidence_sha256,
                "scope": _json(dict(export_scope)),
                "watermark": watermark,
                "watermark_sha256": hashlib.sha256(watermark.encode("utf-8")).hexdigest(),
                "source_hashes": _json(list(source_hashes)),
                "manifest_sha256": manifest_sha256,
                "export_sha256": export_sha256,
                "expires_at": expires_at,
            },
        )


def _document_from_row(row: Mapping[str, Any]) -> PresenterDocumentVersion:
    return PresenterDocumentVersion(
        document_version_id=str(row["id"]),
        case_id=str(row["case_id"]),
        logical_document_id=str(row["logical_document_id"]),
        version_number=int(row["version_number"]),
        sha256=str(row["sha256"]),
        purpose=str(row["purpose"]),
        state=str(row["state"]),
        scan_status=str(row["scan_status"]),
        original_filename=str(row["original_filename"]),
        media_type=str(row["detected_mime"]),
        size_bytes=int(row["size_bytes"]),
        source_kind=str(row["source_kind"]),
        synthetic_only=True,
    )


def _requirements_fields(
    requirements: Any,
) -> tuple[dict[str, Mapping[str, Any]], Mapping[str, Any]]:
    if not isinstance(requirements, Mapping):
        raise PresenterConflict(
            "presenter.profile_contract_invalid", "Perfil de destino sin requisitos"
        )
    raw_fields = requirements.get("fields")
    if isinstance(raw_fields, Mapping):
        candidates = (
            [
                {**dict(value), "field_code": key}
                for key, value in raw_fields.items()
            ]
            if all(isinstance(value, Mapping) for value in raw_fields.values())
            else []
        )
    elif isinstance(raw_fields, list):
        candidates = (
            list(raw_fields)
            if all(isinstance(item, Mapping) for item in raw_fields)
            else []
        )
    else:
        candidates = []
    by_code: dict[str, Mapping[str, Any]] = {}
    by_step: dict[int, Mapping[str, Any]] = {}
    for item in candidates:
        code = str(item.get("field_code") or "").strip().lower()
        step_order = item.get("step_order")
        if (
            not code
            or code in by_code
            or type(step_order) is not int
            or step_order < 1
            or step_order in by_step
        ):
            raise PresenterConflict(
                "presenter.profile_contract_invalid", "Campos de perfil no validos"
            )
        contract = {**dict(item), "field_code": code, "step_order": step_order}
        by_code[code] = contract
        by_step[step_order] = contract
    if not by_code:
        raise PresenterConflict(
            "presenter.profile_contract_invalid", "El perfil no define campos"
        )
    if set(by_step) != set(range(1, len(by_code) + 1)):
        raise PresenterConflict(
            "presenter.profile_contract_invalid",
            "El orden de campos debe ser explicito y contiguo",
        )
    fields = {
        str(by_step[step]["field_code"]): by_step[step]
        for step in range(1, len(by_step) + 1)
    }
    return fields, requirements


_WORKSPACE_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
_WORKSPACE_MEDIA_TYPE_RE = re.compile(
    r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]{0,126}$"
)


def _destination_workspace_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce un perfil a los unicos campos necesarios para el checklist UI."""

    if str(row.get("status")) != "active":
        raise PresenterConflict(
            "presenter.profile_not_active", "Perfil de destino no operativo"
        )
    fields, requirements = _requirements_fields(row.get("requirements"))
    modes = sorted(
        {
            str(value).strip().lower()
            for value in requirements.get(
                "representation_modes", ("self", "representative")
            )
        }
    )
    if not modes or any(value not in {"self", "representative"} for value in modes):
        raise PresenterConflict(
            "presenter.profile_contract_invalid", "Modos de representacion no validos"
        )
    checklist: list[dict[str, Any]] = []
    for code, field in fields.items():
        if not _WORKSPACE_CODE_RE.fullmatch(code):
            raise PresenterConflict(
                "presenter.profile_contract_invalid", "Codigo de campo no valido"
            )
        required = field.get("required", False)
        if type(required) is not bool:
            raise PresenterConflict(
                "presenter.profile_contract_invalid", "required debe ser booleano"
            )
        required_for_modes = sorted(
            {
                str(value).strip().lower()
                for value in field.get("required_for_modes", ())
            }
        )
        if any(value not in {"self", "representative"} for value in required_for_modes):
            raise PresenterConflict(
                "presenter.profile_contract_invalid",
                "required_for_modes no valido",
            )
        purposes = sorted({str(value).strip().lower() for value in field.get("purposes", ())})
        media_types = sorted(
            {str(value).strip().lower() for value in field.get("media_types", ())}
        )
        if not purposes or any(not _WORKSPACE_CODE_RE.fullmatch(value) for value in purposes):
            raise PresenterConflict(
                "presenter.profile_contract_invalid", "Purposes de campo no validos"
            )
        if not media_types or any(
            not _WORKSPACE_MEDIA_TYPE_RE.fullmatch(value) for value in media_types
        ):
            raise PresenterConflict(
                "presenter.profile_contract_invalid", "Media types de campo no validos"
            )
        max_files = field.get("max_files", 1)
        max_bytes = field.get("max_bytes", RTM_PRESENTER_MAX_FILE_BYTES)
        if type(max_files) is not int or type(max_bytes) is not int:
            raise PresenterConflict(
                "presenter.profile_contract_invalid", "Limites de campo no validos"
            )
        if not 1 <= max_files <= RTM_PRESENTER_MAX_ITEMS or not 1 <= max_bytes <= RTM_PRESENTER_MAX_FILE_BYTES:
            raise PresenterConflict(
                "presenter.profile_contract_invalid", "Limites de campo fuera de contrato"
            )
        checklist.append(
            {
                "field_code": code,
                "step_order": int(field["step_order"]),
                "required": required,
                "required_for_modes": required_for_modes,
                "purposes": purposes,
                "media_types": media_types,
                "max_files": max_files,
                "max_bytes": max_bytes,
            }
        )
    profile_sha256 = str(row.get("profile_sha256") or "").strip().lower()
    if len(profile_sha256) != 64 or any(
        value not in "0123456789abcdef" for value in profile_sha256
    ):
        raise PresenterConflict(
            "presenter.profile_contract_invalid", "Hash de perfil no valido"
        )
    created_by_operator_id = str(row.get("created_by_operator_id") or "").strip()
    verified_by_operator_id = str(row.get("verified_by_operator_id") or "").strip()
    if (
        not created_by_operator_id
        or not verified_by_operator_id
        or created_by_operator_id == verified_by_operator_id
    ):
        raise PresenterConflict(
            "presenter.profile_contract_invalid",
            "Perfil sin verificacion independiente",
        )
    verified_at = row.get("verified_at")
    if (
        not isinstance(verified_at, datetime)
        or verified_at.tzinfo is None
        or verified_at.utcoffset() is None
    ):
        raise PresenterConflict(
            "presenter.profile_contract_invalid", "Perfil sin verificacion fechada"
        )
    authorization_field_code = str(
        requirements.get(
            "authorization_field_code", "representation_authorization"
        )
    ).strip().lower()
    if "representative" in modes and (
        not _WORKSPACE_CODE_RE.fullmatch(authorization_field_code)
        or authorization_field_code not in fields
    ):
        raise PresenterConflict(
            "presenter.profile_contract_invalid",
            "Perfil representativo sin campo de autorizacion",
        )
    return {
        "destination_profile_id": str(row["id"]),
        "profile_code": str(row["profile_code"]),
        "profile_version": int(row["version_number"]),
        "profile_sha256": profile_sha256,
        "authority_code": str(row["authority_code"]),
        "display_name": str(row["display_name"]),
        "portal_origin": normalize_origin(row["portal_origin"]),
        "representation_modes": modes,
        "authorization_field_code": (
            authorization_field_code if "representative" in modes else None
        ),
        "fields": checklist,
        "verified_at": verified_at.astimezone(timezone.utc).isoformat(),
    }


def _validate_selection_against_field(
    document: PresenterDocumentVersion,
    field: Mapping[str, Any],
) -> None:
    purposes = {str(v).strip().lower() for v in field.get("purposes", ())}
    if purposes and document.purpose not in purposes:
        raise PresenterConflict(
            "presenter.document_purpose_rejected",
            "El documento no corresponde al campo de destino",
        )
    media_types = {str(v).strip().lower() for v in field.get("media_types", ())}
    if media_types and document.media_type not in media_types:
        raise PresenterConflict(
            "presenter.document_media_type_rejected",
            "Formato no admitido por el destino",
        )
    max_bytes = int(field.get("max_bytes") or 0)
    if max_bytes > 0 and document.size_bytes > max_bytes:
        raise PresenterConflict(
            "presenter.document_too_large", "Documento demasiado grande para el destino"
        )


def _package_from_repository(row: Mapping[str, Any], *, now: datetime) -> FrozenPresenterPackage:
    if str(row.get("status")) != "frozen" or str(row.get("profile_status")) != "active":
        raise PresenterConflict("presenter.package_not_active", "Paquete no operativo")
    expires = row.get("expires_at")
    if not isinstance(expires, datetime) or expires.astimezone(timezone.utc) <= now:
        raise PresenterConflict("presenter.package_expired", "Paquete caducado")
    item_rows = list(row.get("items") or ())
    items: list[PresenterPackageItem] = []
    for item in item_rows:
        if (
            str(item.get("current_document_sha256")) != str(item.get("document_sha256"))
            or str(item.get("state")) != "active"
            or str(item.get("scan_status")) != "clean"
        ):
            raise PresenterConflict(
                "presenter.document_version_changed",
                "Una version congelada ya no es utilizable",
            )
        contract_item = PresenterPackageItem(
            item_id=str(item["id"]),
            document_version_id=str(item["document_version_id"]),
            logical_document_id=str(item["logical_document_id"]),
            document_version=int(item["version_number"]),
            document_sha256=str(item["document_sha256"]),
            item_order=int(item["item_order"]),
            field_code=str(item["field_code"]),
            purpose=str(item["purpose"]),
            portal_filename=str(item["portal_filename"]),
            media_type=str(item["detected_mime"]),
            size_bytes=int(item["size_bytes"]),
            required=bool(item["required"]),
        )
        if canonical_sha256(contract_item.material()) != str(item["item_sha256"]):
            raise PresenterConflict(
                "presenter.item_manifest_changed", "Item de paquete no verificable"
            )
        items.append(contract_item)
    package = FrozenPresenterPackage(
        package_id=str(row["id"]),
        logical_package_id=str(row["logical_package_id"]),
        package_version=int(row["package_version"]),
        case_id=str(row["case_id"]),
        destination_profile_id=str(row["destination_profile_id"]),
        destination_profile_code=str(row["profile_code"]),
        destination_profile_version=int(row["profile_version"]),
        destination_profile_sha256=str(row["profile_sha256"]),
        portal_origin=str(row["portal_origin"]),
        representation_mode=str(row["representation_mode"]),
        authorization_document_version_id=(
            str(row["authorization_document_version_id"])
            if row.get("authorization_document_version_id")
            else None
        ),
        created_by_operator_id=str(row["created_by_operator_id"]),
        frozen_by_operator_id=str(row["frozen_by_operator_id"]),
        frozen_at=row["frozen_at"].isoformat(),
        expires_at=expires.isoformat(),
        items=tuple(items),
        manifest_sha256=str(row["manifest_sha256"]),
    )
    persisted_manifest = row.get("manifest")
    if not isinstance(persisted_manifest, Mapping):
        raise PresenterConflict(
            "presenter.package_manifest_missing", "Manifiesto no disponible"
        )
    if canonical_sha256(persisted_manifest) != package.manifest_sha256:
        raise PresenterConflict(
            "presenter.package_manifest_changed", "Manifiesto de paquete no verificable"
        )
    return package


class PresenterService:
    def __init__(
        self,
        *,
        repository: PresenterRepository,
        runtime: PresenterRuntimeConfiguration,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
        watermarker: Callable[[bytes, str, str], bytes] | None = None,
    ) -> None:
        self.repository = repository
        self.runtime = runtime
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.token_factory = token_factory or (lambda: secrets.token_urlsafe(48))
        self.watermarker = watermarker

    def _now(self) -> datetime:
        current = self.clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise PresenterServiceError(
                "presenter.clock_invalid", "Reloj Presenter sin zona", status_code=500
            )
        return current.astimezone(timezone.utc)

    def _open(self, conn: Any) -> None:
        require_presenter_runtime(self.runtime)
        try:
            ready = self.repository.presenter_schema_ready(conn)
        except Exception:
            ready = False
        if ready is not True:
            raise PresenterSchemaNotReady()

    def _authorize_case_scope(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        case_id: str,
    ) -> None:
        """Frontera comun de expediente; nunca revela por que fue denegado."""

        try:
            allowed = self.repository.has_active_synthetic_case_access(
                conn,
                case_id=case_id,
                operator_id=actor.operator_id,
            )
        except Exception:
            allowed = False
        if allowed is not True:
            raise PresenterForbidden()

    def ingest_external_document(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        case_id: str,
        content: bytes,
        original_filename: str,
        declared_mime: str,
        purpose: str,
        synthetic_confirmed: bool,
        supersedes_document_version_id: str | None,
        storage_writer: Callable[
            [PresenterExternalDocumentUpload, Callable[[str, str], None]],
            tuple[str, str],
        ],
        register_rollback_cleanup: Callable[[str, str], None],
    ) -> PresenterDocumentVersion:
        self._open(conn)
        authorize_document_ingest(actor)
        self._authorize_case_scope(conn, actor=actor, case_id=case_id)
        if synthetic_confirmed is not True:
            raise PresenterConflict(
                "presenter.synthetic_confirmation_required",
                "Confirmacion sintetica obligatoria",
            )
        supersedes_id: str | None = None
        if supersedes_document_version_id is not None:
            try:
                supersedes_id = str(uuid.UUID(str(supersedes_document_version_id)))
            except (TypeError, ValueError, AttributeError) as exc:
                raise PresenterConflict(
                    "presenter.external_document_predecessor_invalid",
                    "Version predecesora no valida",
                ) from exc
        upload = validate_external_document_upload(
            content=content,
            original_filename=original_filename,
            declared_mime=declared_mime,
            purpose=purpose,
        )
        registered_coordinates: set[tuple[str, str]] = set()

        def tracked_rollback_registration(bucket: str, key: str) -> None:
            clean_bucket = str(bucket or "").strip()
            clean_key = str(key or "").strip()
            if not clean_bucket or not clean_key:
                raise PresenterServiceError(
                    "presenter.storage_contract_invalid",
                    "Custodia documental no verificable",
                    status_code=502,
                )
            register_rollback_cleanup(clean_bucket, clean_key)
            registered_coordinates.add((clean_bucket, clean_key))

        coordinates = storage_writer(upload, tracked_rollback_registration)
        if not isinstance(coordinates, tuple) or len(coordinates) != 2:
            raise PresenterServiceError(
                "presenter.storage_contract_invalid",
                "Custodia documental no verificable",
                status_code=502,
            )
        storage_bucket = str(coordinates[0] or "").strip()
        storage_key = str(coordinates[1] or "").strip()
        if not storage_bucket or not storage_key:
            raise PresenterServiceError(
                "presenter.storage_contract_invalid",
                "Custodia documental no verificable",
                status_code=502,
            )
        if (storage_bucket, storage_key) not in registered_coordinates:
            # Compatibilidad defensiva para writers defectuosos: al menos se
            # intenta limpiar la coordenada devuelta, pero se rechaza porque
            # no quedo registrada antes del PUT.
            register_rollback_cleanup(storage_bucket, storage_key)
            raise PresenterServiceError(
                "presenter.storage_cleanup_not_pre_registered",
                "Custodia documental sin cleanup previo",
                status_code=502,
            )
        row = self.repository.insert_external_document_version(
            conn,
            case_id=case_id,
            created_by_operator_id=actor.operator_id,
            upload=upload,
            storage_bucket=storage_bucket,
            storage_key=storage_key,
            supersedes_document_version_id=supersedes_id,
        )
        document = _document_from_row(row)
        if (
            document.state is not PresenterDocumentState.REVIEW
            or document.scan_status != "pending"
            or document.source_kind != "external_revision"
        ):
            raise PresenterServiceError(
                "presenter.external_document_state_invalid",
                "El ingreso externo no quedo pendiente de seguridad",
                status_code=500,
            )
        self.repository.append_audit(
            conn,
            event_type="presenter.document.external_ingested",
            reason_code="pending_security_scan",
            actor=actor,
            case_id=case_id,
            payload={
                "document_version_id": document.document_version_id,
                "logical_document_id": document.logical_document_id,
                "version_number": document.version_number,
                "supersedes_document_version_id": supersedes_id,
                "sha256": document.sha256,
                "purpose": document.purpose,
                "state": document.state.value,
                "scan_status": document.scan_status,
                "security_disposition": "pending_security_scan",
                "original_filename": document.original_filename,
                "media_type": document.media_type,
                "size_bytes": document.size_bytes,
                "source_kind": document.source_kind,
                "synthetic_confirmed": True,
                "eligible_for_package": False,
            },
        )
        return document

    def list_documents(
        self, conn: Any, *, actor: PresenterActorContext, case_id: str
    ) -> dict[str, Any]:
        self._open(conn)
        authorize_document_list(actor)
        self._authorize_case_scope(conn, actor=actor, case_id=case_id)
        documents = [
            _document_from_row(row).sanitized()
            for row in self.repository.list_document_versions(conn, case_id=case_id)
        ]
        # Defensa de salida: ni siquiera un repositorio defectuoso puede colar
        # coordenadas de storage o URLs en la proyeccion construida arriba.
        forbidden = {"b2_bucket", "b2_key", "bucket", "key", "url", "presigned_url"}
        if any(forbidden.intersection(item) for item in documents):
            raise PresenterServiceError(
                "presenter.unsafe_projection", "Proyeccion documental insegura", status_code=500
            )
        return {
            "case_id": case_id,
            "documents": documents,
            "download_available": False,
            "zip_available": False,
            "storage_references_exposed": False,
            "synthetic_only": True,
        }

    def workspace(
        self, conn: Any, *, actor: PresenterActorContext, case_id: str
    ) -> dict[str, Any]:
        """Checklist sanitizado para preparar un paquete, nunca para descargar."""

        self._open(conn)
        authorize_document_list(actor)
        self._authorize_case_scope(conn, actor=actor, case_id=case_id)
        documents = [
            _document_from_row(row).sanitized()
            for row in self.repository.list_document_versions(conn, case_id=case_id)
        ]
        destinations = [
            _destination_workspace_projection(row)
            for row in self.repository.list_destination_profiles(conn)
        ]
        return {
            "case_id": case_id,
            "destinations": destinations,
            "documents": documents,
            "actions": {
                "freeze_package": True,
                "operator_download": False,
                "operator_preview": False,
                "operator_zip": False,
                "operator_handoff": False,
            },
            "storage_references_exposed": False,
            "synthetic_only": True,
        }

    def freeze_package(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        case_id: str,
        destination_profile_id: str,
        portal_origin: str,
        representation_mode: str,
        authorization_document_version_id: str | None,
        selections: Sequence[PresenterItemSelection],
        expires_at: datetime,
        idempotency_key: str | None = None,
        supersedes_package_id: str | None = None,
    ) -> FrozenPresenterPackage:
        self._open(conn)
        authorize_package_freeze(actor)
        self._authorize_case_scope(conn, actor=actor, case_id=case_id)
        command_key = str(idempotency_key or "").strip()
        if not _IDEMPOTENCY_KEY_RE.fullmatch(command_key):
            raise PresenterConflict(
                "presenter.idempotency_key_required",
                "La congelacion exige una clave idempotente valida",
            )
        current = self._now()
        if (
            not isinstance(expires_at, datetime)
            or expires_at.tzinfo is None
            or expires_at.utcoffset() is None
        ):
            raise PresenterConflict(
                "presenter.package_expiry_invalid",
                "Vigencia de paquete exige zona horaria",
            )
        expiry = expires_at.astimezone(timezone.utc)
        lifetime = (expiry - current).total_seconds()
        if lifetime <= 0 or lifetime > MAX_PACKAGE_LIFETIME_SECONDS:
            raise PresenterConflict(
                "presenter.package_expiry_invalid", "Vigencia de paquete no permitida"
            )
        exact_origin = normalize_origin(portal_origin)
        choices = tuple(selections)
        mode = str(representation_mode or "").strip().lower()
        request_sha256 = canonical_sha256(
            {
                "case_id": str(case_id),
                "destination_profile_id": str(destination_profile_id),
                "portal_origin": exact_origin,
                "representation_mode": mode,
                "authorization_document_version_id": (
                    str(authorization_document_version_id)
                    if authorization_document_version_id
                    else None
                ),
                "items": [
                    {
                        "document_version_id": str(item.document_version_id),
                        "item_order": item.item_order,
                        "field_code": str(item.field_code or "").strip().lower(),
                        "portal_filename": str(item.portal_filename or "").strip(),
                    }
                    for item in choices
                ],
                "expires_at": expiry.isoformat(),
                "supersedes_package_id": (
                    str(supersedes_package_id)
                    if supersedes_package_id
                    else None
                ),
            }
        )
        replay = self.repository.load_idempotent_frozen_package(
            conn,
            operator_id=actor.operator_id,
            idempotency_key=command_key,
        )
        if replay:
            if str(replay.get("idempotency_request_sha256")) != request_sha256:
                raise PresenterConflict(
                    "presenter.idempotency_key_reused",
                    "La clave idempotente ya pertenece a otra solicitud",
                )
            if str(replay.get("case_id")) != str(case_id):
                raise PresenterConflict(
                    "presenter.idempotency_scope_mismatch",
                    "La clave idempotente pertenece a otro expediente",
                )
            return _package_from_repository(replay, now=current)
        profile = self.repository.load_destination_profile(
            conn, profile_id=destination_profile_id
        )
        if not profile:
            raise PresenterNotFound("Perfil de destino no encontrado")
        # La misma proyeccion cerrada que consume el workspace valida el perfil
        # antes de que sus requisitos puedan gobernar un paquete.
        profile_projection = _destination_workspace_projection(profile)
        if normalize_origin(profile["portal_origin"]) != exact_origin:
            raise PresenterConflict(
                "presenter.origin_mismatch", "El origen no coincide con el perfil"
            )
        fields, requirements = _requirements_fields(profile.get("requirements"))
        allowed_modes = {
            str(value).strip().lower()
            for value in requirements.get("representation_modes", ("self", "representative"))
        }
        if mode not in allowed_modes:
            raise PresenterConflict(
                "presenter.representation_mode_rejected",
                "Modo de representacion no admitido por el destino",
            )
        if not 1 <= len(choices) <= RTM_PRESENTER_MAX_ITEMS:
            raise PresenterConflict(
                "presenter.package_item_count_invalid", "Numero de documentos no admitido"
            )
        if tuple(sorted(item.item_order for item in choices)) != tuple(range(1, len(choices) + 1)):
            raise PresenterConflict(
                "presenter.package_order_invalid", "El orden debe ser contiguo"
            )
        if len({item.document_version_id for item in choices}) != len(choices):
            raise PresenterConflict(
                "presenter.document_version_repeated", "Documento repetido en el paquete"
            )
        ordered_choices = tuple(sorted(choices, key=lambda item: item.item_order))
        self.repository.lock_document_version_lineages(
            conn,
            case_id=case_id,
            document_version_ids=tuple(
                item.document_version_id for item in ordered_choices
            ),
        )
        prior_field_step = 0
        for selection in ordered_choices:
            code = str(selection.field_code or "").strip().lower()
            field_contract = fields.get(code)
            if not field_contract:
                raise PresenterConflict(
                    "presenter.destination_field_unknown",
                    "Campo de destino no reconocido",
                )
            field_step = int(field_contract["step_order"])
            if field_step < prior_field_step:
                raise PresenterConflict(
                    "presenter.package_field_order_invalid",
                    "Los documentos deben seguir el orden de campos del destino",
                )
            prior_field_step = field_step
        per_field: dict[str, int] = {}
        required_by_field = {
            code: bool(contract.get("required", False))
            or mode
            in {
                str(value).strip().lower()
                for value in contract.get("required_for_modes", ())
            }
            for code, contract in fields.items()
        }
        package_items: list[PresenterPackageItem] = []
        for selection in ordered_choices:
            code = str(selection.field_code or "").strip().lower()
            # La primera pasada ya ha validado el codigo y el orden.
            field_contract = fields[code]
            row = self.repository.load_document_version(
                conn,
                case_id=case_id,
                document_version_id=selection.document_version_id,
                for_update=True,
            )
            if not row:
                raise PresenterNotFound("Version documental no encontrada")
            document = _document_from_row(row)
            if document.state is not PresenterDocumentState.ACTIVE:
                raise PresenterConflict(
                    "presenter.document_not_approved", "Documento no aprobado"
                )
            _validate_selection_against_field(document, field_contract)
            per_field[code] = per_field.get(code, 0) + 1
            max_files = int(field_contract.get("max_files") or 1)
            if per_field[code] > max_files:
                raise PresenterConflict(
                    "presenter.destination_field_overflow",
                    "Demasiados documentos para un campo",
                )
            package_items.append(
                PresenterPackageItem(
                    item_id=str(uuid.uuid4()),
                    document_version_id=document.document_version_id,
                    logical_document_id=document.logical_document_id,
                    document_version=document.version_number,
                    document_sha256=document.sha256,
                    item_order=selection.item_order,
                    field_code=code,
                    purpose=document.purpose,
                    portal_filename=safe_filename(selection.portal_filename),
                    media_type=document.media_type,
                    size_bytes=document.size_bytes,
                    required=required_by_field[code],
                )
            )
        missing = sorted(
            code
            for code, contract in fields.items()
            if required_by_field[code] and per_field.get(code, 0) == 0
        )
        if missing:
            raise PresenterConflict(
                "presenter.required_destination_fields_missing",
                "Faltan campos requeridos por el destino",
            )
        selected_ids = {item.document_version_id for item in package_items}
        if mode == "representative":
            if not authorization_document_version_id or authorization_document_version_id not in selected_ids:
                raise PresenterConflict(
                    "presenter.authorization_not_in_package",
                    "La autorizacion exacta debe formar parte del paquete",
                )
            authorization_item = next(
                item
                for item in package_items
                if item.document_version_id == authorization_document_version_id
            )
            if authorization_item.field_code != profile_projection.get(
                "authorization_field_code"
            ):
                raise PresenterConflict(
                    "presenter.authorization_field_mismatch",
                    "La autorizacion debe ocupar el campo exacto del destino",
                )
        elif authorization_document_version_id is not None:
            raise PresenterConflict(
                "presenter.unexpected_authorization",
                "Presentacion propia no debe congelar autorizacion de representante",
            )
        identity = self.repository.next_package_identity(
            conn,
            case_id=case_id,
            destination_profile_id=destination_profile_id,
            supersedes_package_id=supersedes_package_id,
        )
        package = build_frozen_package(
            package_id=str(uuid.uuid4()),
            logical_package_id=str(identity["logical_package_id"]),
            package_version=int(identity["package_version"]),
            case_id=case_id,
            destination_profile_id=destination_profile_id,
            destination_profile_code=str(profile["profile_code"]),
            destination_profile_version=int(profile["version_number"]),
            destination_profile_sha256=str(profile["profile_sha256"]),
            portal_origin=exact_origin,
            representation_mode=mode,
            authorization_document_version_id=authorization_document_version_id,
            created_by_operator_id=actor.operator_id,
            frozen_by_operator_id=actor.operator_id,
            frozen_at=current.isoformat(),
            expires_at=expiry.isoformat(),
            items=package_items,
        )
        self.repository.persist_frozen_package(
            conn,
            package=package,
            supersedes_package_id=(
                str(identity.get("supersedes_package_id"))
                if identity.get("supersedes_package_id")
                else None
            ),
            idempotency_key=command_key,
            request_sha256=request_sha256,
        )
        self.repository.append_audit(
            conn,
            event_type="presenter.package.frozen",
            reason_code="exact_document_versions_hashes_fields_and_origin_frozen",
            actor=actor,
            case_id=case_id,
            package_id=package.package_id,
            payload={
                "manifest_sha256": package.manifest_sha256,
                "item_count": len(package.items),
                "portal_origin": package.portal_origin,
                "destination_profile_id": package.destination_profile_id,
                "destination_profile_version": package.destination_profile_version,
                "supersedes_package_id": supersedes_package_id,
            },
        )
        return package

    def _load_package(
        self, conn: Any, *, case_id: str, package_id: str, for_update: bool = False
    ) -> FrozenPresenterPackage:
        row = self.repository.load_frozen_package(
            conn, case_id=case_id, package_id=package_id, for_update=for_update
        )
        if not row:
            raise PresenterNotFound("Paquete Presenter no encontrado")
        return _package_from_repository(row, now=self._now())

    def issue_ticket(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        case_id: str,
        package_id: str,
        package_item_id: str,
        portal_origin: str,
        ttl_seconds: int = DEFAULT_TICKET_TTL_SECONDS,
    ) -> IssuedPresenterTicket:
        self._open(conn)
        if not self.runtime.managed_extension_attestation_enabled:
            raise PresenterForbidden("Canal Presenter no disponible")
        authorize_handoff_issue(actor)
        self._authorize_case_scope(conn, actor=actor, case_id=case_id)
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= RTM_PRESENTER_MAX_TICKET_TTL_SECONDS:
            raise PresenterConflict("presenter.ticket_ttl_invalid", "TTL no permitido")
        package = self._load_package(
            conn, case_id=case_id, package_id=package_id, for_update=True
        )
        exact_origin = normalize_origin(portal_origin)
        if package.portal_origin != exact_origin:
            raise PresenterForbidden("Origen de ticket no autorizado")
        item = next((value for value in package.items if value.item_id == package_item_id), None)
        if item is None:
            raise PresenterNotFound("Item de paquete no encontrado")
        # Revalidacion del documento justo antes de emitir la capacidad.
        row = self.repository.load_document_version(
            conn,
            case_id=case_id,
            document_version_id=item.document_version_id,
            for_update=True,
        )
        document = _document_from_row(row) if row else None
        if (
            document is None
            or document.state is not PresenterDocumentState.ACTIVE
            or document.sha256 != item.document_sha256
            or document.version_number != item.document_version
        ):
            raise PresenterConflict(
                "presenter.document_revalidation_failed",
                "La version documental ya no coincide",
            )
        raw_token = self.token_factory()
        if not isinstance(raw_token, str) or len(raw_token) < 43 or any(c.isspace() for c in raw_token):
            raise PresenterServiceError(
                "presenter.ticket_entropy_invalid", "Generador de tickets no seguro", status_code=500
            )
        issued_at = self._now()
        expires_at = issued_at + timedelta(seconds=ttl_seconds)
        binding = PresenterTicketBinding(
            ticket_id=str(uuid.uuid4()),
            ticket_sha256=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
            operator_id=actor.operator_id,
            operator_session_id=actor.operator_session_id,
            extension_client_id=str(actor.extension_client_id),
            case_id=case_id,
            package_id=package.package_id,
            package_item_id=item.item_id,
            portal_origin=package.portal_origin,
            field_code=item.field_code,
            issued_at=issued_at.isoformat(),
            expires_at=expires_at.isoformat(),
        )
        self.repository.insert_ticket(conn, binding=binding)
        self.repository.append_audit(
            conn,
            event_type="presenter.handoff.ticket_issued",
            reason_code="single_use_extension_ticket",
            actor=actor,
            case_id=case_id,
            package_id=package.package_id,
            package_item_id=item.item_id,
            handoff_ticket_id=binding.ticket_id,
            payload={
                "operator_session_id": actor.operator_session_id,
                "extension_client_id": actor.extension_client_id,
                "extension_attestation_id": actor.extension_attestation_id,
                "portal_origin": package.portal_origin,
                "field_code": item.field_code,
                "expires_at": binding.expires_at,
            },
        )
        return IssuedPresenterTicket(
            ticket_id=binding.ticket_id,
            token=raw_token,
            expires_at=binding.expires_at,
            package_item_id=item.item_id,
            field_code=item.field_code,
            portal_origin=package.portal_origin,
        )

    def exchange_ticket(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        raw_ticket: str,
        request_origin: str,
    ) -> PresenterFilePayload:
        self._open(conn)
        if not self.runtime.managed_extension_attestation_enabled:
            raise PresenterForbidden("Canal Presenter no disponible")
        # Autorizacion preliminar: evita que UI/admin alcancen consume_ticket.
        authorize_handoff_exchange_client(actor)
        raw = str(raw_ticket or "")
        if len(raw) < 43 or any(char.isspace() for char in raw):
            raise PresenterNotFound("Ticket Presenter no valido")
        exact_origin = normalize_origin(request_origin)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        consumed = self.repository.consume_ticket(
            conn,
            ticket_sha256=digest,
            actor=actor,
            portal_origin=exact_origin,
            used_at=self._now(),
        )
        if not consumed:
            raise PresenterNotFound("Ticket caducado, usado o fuera de contexto")
        binding = PresenterTicketBinding(
            ticket_id=str(consumed["id"]),
            ticket_sha256=str(consumed["ticket_hash"]),
            operator_id=str(consumed["operator_id"]),
            operator_session_id=str(consumed["operator_session_id"]),
            extension_client_id=str(consumed["extension_client_id"]),
            case_id=str(consumed["case_id"]),
            package_id=str(consumed["package_id"]),
            package_item_id=str(consumed["package_item_id"]),
            portal_origin=str(consumed["portal_origin"]),
            field_code=str(consumed["field_code"]),
            issued_at=consumed["issued_at"].isoformat(),
            expires_at=consumed["expires_at"].isoformat(),
            used_at=consumed["used_at"].isoformat(),
        )
        authorize_handoff_exchange(actor, binding, request_origin=exact_origin)
        self._authorize_case_scope(
            conn,
            actor=actor,
            case_id=binding.case_id,
        )
        package = self._load_package(
            conn,
            case_id=binding.case_id,
            package_id=binding.package_id,
            for_update=True,
        )
        if package.portal_origin != binding.portal_origin:
            raise PresenterConflict(
                "presenter.ticket_package_origin_changed", "Origen congelado no coincide"
            )
        item = next(
            (candidate for candidate in package.items if candidate.item_id == binding.package_item_id),
            None,
        )
        if item is None or item.field_code != binding.field_code:
            raise PresenterConflict(
                "presenter.ticket_item_changed", "Ticket no coincide con el item congelado"
            )
        # Todas las comprobaciones y el consumo comparten transaccion. Cualquier
        # fallo posterior provoca rollback; un servicio remoto futuro deberá
        # separar el consumo duradero si exige inutilizar también los intentos
        # fallidos.
        document_row = self.repository.load_document_version(
            conn,
            case_id=package.case_id,
            document_version_id=item.document_version_id,
            for_update=True,
        )
        document = _document_from_row(document_row) if document_row else None
        if (
            document is None
            or document.state is not PresenterDocumentState.ACTIVE
            or document.sha256 != item.document_sha256
            or document.version_number != item.document_version
            or document.media_type != item.media_type
            or document.size_bytes != item.size_bytes
        ):
            raise PresenterConflict(
                "presenter.exchange_revalidation_failed",
                "Documento congelado ya no verificable",
            )
        content = self.repository.load_document_bytes(
            conn,
            case_id=package.case_id,
            document_version_id=item.document_version_id,
            expected_sha256=item.document_sha256,
        )
        if len(content) != item.size_bytes or hashlib.sha256(content).hexdigest() != item.document_sha256:
            raise PresenterConflict(
                "presenter.exchange_bytes_mismatch", "Bytes distintos del paquete congelado"
            )
        payload = PresenterFilePayload(
            content=content,
            filename=item.portal_filename,
            media_type=item.media_type,
            sha256=item.document_sha256,
            package_id=package.package_id,
            package_item_id=item.item_id,
            field_code=item.field_code,
        )
        self.repository.append_audit(
            conn,
            event_type="presenter.handoff.document_served",
            reason_code="one_time_ticket_consumed_and_hash_revalidated",
            actor=actor,
            case_id=package.case_id,
            package_id=package.package_id,
            package_item_id=item.item_id,
            handoff_ticket_id=binding.ticket_id,
            payload={
                "operator_session_id": actor.operator_session_id,
                "extension_client_id": actor.extension_client_id,
                "extension_attestation_id": actor.extension_attestation_id,
                "portal_origin": binding.portal_origin,
                "field_code": item.field_code,
                "document_version_id": item.document_version_id,
                "document_version": item.document_version,
                "document_sha256": item.document_sha256,
                "bytes": len(content),
            },
        )
        return payload

    def export_package_admin(
        self,
        conn: Any,
        *,
        actor: PresenterActorContext,
        case_id: str,
        package_id: str,
        reason: str,
    ) -> PresenterAdminExportPayload:
        self._open(conn)
        try:
            decision = authorize_admin_export(
                actor,
                reason=reason,
                now=self._now(),
            )
        except PresenterPolicyError:
            # La denegacion se intenta auditar sin alterar el error autoritativo.
            try:
                self.repository.append_audit(
                    conn,
                    event_type="presenter.admin_export.denied",
                    reason_code="explicit_export_policy_not_satisfied",
                    actor=actor,
                    case_id=case_id,
                    package_id=package_id,
                    payload={"reason_supplied": bool(str(reason or "").strip())},
                )
            except Exception:
                pass
            raise
        if self.watermarker is None:
            raise PresenterConflict(
                "presenter.watermarker_unavailable",
                "Exportacion bloqueada: no hay motor de marca de agua",
            )
        package = self._load_package(
            conn, case_id=case_id, package_id=package_id, for_update=True
        )
        source_hashes: list[str] = []
        export_items: list[dict[str, Any]] = []
        archive = io.BytesIO()
        with zipfile.ZipFile(
            archive, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as bundle:
            for item in package.items:
                content = self.repository.load_document_bytes(
                    conn,
                    case_id=case_id,
                    document_version_id=item.document_version_id,
                    expected_sha256=item.document_sha256,
                )
                if hashlib.sha256(content).hexdigest() != item.document_sha256:
                    raise PresenterConflict(
                        "presenter.admin_export_source_mismatch",
                        "Fuente de exportacion no verificable",
                    )
                marked = self.watermarker(content, item.media_type, decision.watermark)
                if not isinstance(marked, bytes) or not marked or marked == content:
                    raise PresenterConflict(
                        "presenter.watermark_not_applied",
                        "El documento exportado no acredita marca de agua",
                    )
                archive_name = safe_filename(
                    f"{item.item_order:02d}_{item.field_code}_{item.portal_filename}"
                )
                bundle.writestr(archive_name, marked)
                source_hashes.append(item.document_sha256)
                export_items.append(
                    {
                        "item_id": item.item_id,
                        "document_version_id": item.document_version_id,
                        "document_version": item.document_version,
                        "source_sha256": item.document_sha256,
                        "watermarked_sha256": hashlib.sha256(marked).hexdigest(),
                        "field_code": item.field_code,
                        "archive_name": archive_name,
                    }
                )
            manifest = {
                "format": "rtm.presenter.admin_export.v1",
                "contract_version": RTM_PRESENTER_CONTRACT_VERSION,
                "case_id": case_id,
                "package_id": package.package_id,
                "package_manifest_sha256": package.manifest_sha256,
                "portal_origin": package.portal_origin,
                "admin_operator_id": decision.operator_id,
                "operator_session_id": decision.operator_session_id,
                "reason": decision.reason,
                "reauthenticated_at": decision.reauthenticated_at.isoformat(),
                "reauthentication_event_id": decision.reauthentication_event_id,
                "exceptional_export_grant_id": (
                    decision.exceptional_export_grant_id
                ),
                "reauthentication_evidence_sha256": (
                    decision.reauthentication_evidence_sha256
                ),
                "watermark": decision.watermark,
                "watermark_sha256": hashlib.sha256(
                    decision.watermark.encode("utf-8")
                ).hexdigest(),
                "items": export_items,
                "created_at": decision.authorized_at.isoformat(),
                "synthetic_marker": RTM_PRESENTER_SYNTHETIC_MARKER,
                "synthetic_only": True,
            }
            manifest_bytes = canonical_json(manifest)
            manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
            bundle.writestr("RTM_EXPORT_MANIFEST.json", manifest_bytes)
            bundle.writestr("RTM_EXPORT_WATERMARK.txt", decision.watermark.encode("utf-8"))
        content = archive.getvalue()
        export_sha256 = hashlib.sha256(content).hexdigest()
        export_id = str(uuid.uuid4())
        export_scope = {
            "package_manifest_sha256": package.manifest_sha256,
            "portal_origin": package.portal_origin,
            "operator_session_id": decision.operator_session_id,
            "reauthentication_event_id": decision.reauthentication_event_id,
            "exceptional_export_grant_id": decision.exceptional_export_grant_id,
            "item_ids": [item.item_id for item in package.items],
            "document_version_ids": [item.document_version_id for item in package.items],
            "synthetic_only": True,
        }
        self.repository.insert_admin_export(
            conn,
            export_id=export_id,
            package=package,
            actor=actor,
            reason=decision.reason,
            reauthenticated_at=decision.reauthenticated_at,
            reauthentication_evidence_sha256=(
                decision.reauthentication_evidence_sha256
            ),
            export_scope=export_scope,
            watermark=decision.watermark,
            source_hashes=source_hashes,
            manifest_sha256=manifest_sha256,
            export_sha256=export_sha256,
            expires_at=decision.authorized_at
            + timedelta(seconds=ADMIN_EXPORT_LIFETIME_SECONDS),
        )
        self.repository.append_audit(
            conn,
            event_type="presenter.admin_export.completed",
            reason_code="explicit_permission_reason_recent_reauth_watermark_manifest",
            actor=actor,
            case_id=case_id,
            package_id=package.package_id,
            admin_export_id=export_id,
            payload={
                "manifest_sha256": manifest_sha256,
                "export_sha256": export_sha256,
                "watermark_sha256": hashlib.sha256(
                    decision.watermark.encode("utf-8")
                ).hexdigest(),
                "operator_session_id": decision.operator_session_id,
                "reauthentication_event_id": decision.reauthentication_event_id,
                "exceptional_export_grant_id": (
                    decision.exceptional_export_grant_id
                ),
                "source_hashes": source_hashes,
                "item_count": len(package.items),
            },
        )
        return PresenterAdminExportPayload(
            content=content,
            filename=f"rtm_presenter_export_{package.package_id}.zip",
            manifest_sha256=manifest_sha256,
            export_sha256=export_sha256,
            watermark=decision.watermark,
        )


__all__ = [
    "ADMIN_EXPORT_LIFETIME_SECONDS",
    "DEFAULT_TICKET_TTL_SECONDS",
    "MAX_PACKAGE_LIFETIME_SECONDS",
    "RTM_PRESENTER_EXTERNAL_PURPOSES",
    "RTM_PRESENTER_SERVICE_VERSION",
    "PresenterConflict",
    "PresenterExternalDocumentUpload",
    "PresenterForbidden",
    "PresenterItemSelection",
    "PresenterNotFound",
    "PresenterRepository",
    "PresenterSchemaNotReady",
    "PresenterService",
    "PresenterServiceError",
    "SqlPresenterRepository",
    "validate_external_document_upload",
]
