from __future__ import annotations

import hashlib
import inspect
import io
import json
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from rtm_presenter_contracts import (
    PresenterClientKind,
    PresenterPackageStatus,
    PresenterTicketBinding,
    canonical_sha256,
)
from rtm_presenter_policy import (
    PRESENTER_ADMIN_EXPORT_PERMISSION,
    PRESENTER_ADMIN_ROLE_CODE,
    PRESENTER_DOCUMENT_READ_PERMISSION,
    PRESENTER_HANDOFF_EXCHANGE_PERMISSION,
    PRESENTER_HANDOFF_ISSUE_PERMISSION,
    PRESENTER_PACKAGE_FREEZE_PERMISSION,
    RTM_PRESENTER_EXTENSION_CLIENT_ID,
    PresenterActorContext,
    PresenterPolicyError,
    PresenterRuntimeConfiguration,
    PresenterRuntimeDisabled,
)
from rtm_presenter_service import (
    PresenterConflict,
    PresenterForbidden,
    PresenterItemSelection,
    PresenterNotFound,
    PresenterSchemaNotReady,
    PresenterService,
    SqlPresenterRepository,
)


NOW = datetime(2026, 8, 28, 18, 8, tzinfo=timezone.utc)


def _id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


CASE_ID = _id(1)
DOCUMENT_ID = _id(2)
LOGICAL_DOCUMENT_ID = _id(3)
PROFILE_ID = _id(4)
OPERATOR_ID = _id(5)
OPERATOR_SESSION_ID = _id(6)
ADMIN_ID = _id(7)
ADMIN_SESSION_ID = _id(8)
LOGICAL_PACKAGE_ID = _id(9)
REAUTH_EVENT_ID = _id(10)
EXPORT_GRANT_ID = _id(11)
AUTHORIZATION_DOCUMENT_ID = _id(12)
AUTHORIZATION_LOGICAL_DOCUMENT_ID = _id(13)
TENANT_ID = _id(14)
PORTAL_ORIGIN = "https://portal.synthetic.example"
CONTENT = b"%PDF-1.7\nSynthetic RTM filing document\n%%EOF"
CONTENT_SHA256 = hashlib.sha256(CONTENT).hexdigest()
AUTHORIZATION_CONTENT = b"%PDF-1.7\nSynthetic representation authorization\n%%EOF"
AUTHORIZATION_SHA256 = hashlib.sha256(AUTHORIZATION_CONTENT).hexdigest()
RAW_TICKET = "T" * 64
IDEMPOTENCY_KEY = "rtm-presenter-test-command-0001"


class FakePresenterRepository:
    def __init__(self) -> None:
        self.schema_calls = 0
        self.schema_ready = True
        self.schema_failure: Exception | None = None
        self.case_access_checks = 0
        self.document_list_calls = 0
        self.destination_list_calls = 0
        self.document_lineage_lock_calls = 0
        self.locked_document_version_ids: tuple[str, ...] = ()
        self.document_load_calls = 0
        self.destination_load_calls = 0
        self.package_identity_calls = 0
        self.case_test_mode = True
        self.binding_active = True
        self.binding_synthetic = True
        self.binding_tenant_id = TENANT_ID
        self.membership_active = True
        self.membership_synthetic = True
        self.membership_tenant_id = TENANT_ID
        self.membership_operator_id = OPERATOR_ID
        self.assignment_active = True
        self.assignment_accepted = True
        self.assignment_synthetic = True
        self.assignment_role = "responsible"
        self.assignment_operator_id = OPERATOR_ID
        self.case_access_failure: Exception | None = None
        self.document_rows: dict[str, dict[str, Any]] = {
            DOCUMENT_ID: {
                "id": DOCUMENT_ID,
                "case_id": CASE_ID,
                "logical_document_id": LOGICAL_DOCUMENT_ID,
                "version_number": 2,
                "sha256": CONTENT_SHA256,
                "purpose": "main_filing",
                "state": "active",
                "scan_status": "clean",
                "original_filename": "recurso_v2.pdf",
                "detected_mime": "application/pdf",
                "size_bytes": len(CONTENT),
                "source_kind": "operator_revision",
                # Un repositorio defectuoso puede traer esto. El servicio debe
                # reconstruir una proyeccion cerrada y descartarlo.
                "b2_bucket": "must-never-leave-repository",
                "b2_key": "must-never-leave-repository",
                "presigned_url": "https://storage.invalid/forbidden",
            },
            AUTHORIZATION_DOCUMENT_ID: {
                "id": AUTHORIZATION_DOCUMENT_ID,
                "case_id": CASE_ID,
                "logical_document_id": AUTHORIZATION_LOGICAL_DOCUMENT_ID,
                "version_number": 1,
                "sha256": AUTHORIZATION_SHA256,
                "purpose": "representation_authorization",
                "state": "active",
                "scan_status": "clean",
                "original_filename": "autorizacion.pdf",
                "detected_mime": "application/pdf",
                "size_bytes": len(AUTHORIZATION_CONTENT),
                "source_kind": "external_upload",
            },
        }
        self.profile: dict[str, Any] = {
            "id": PROFILE_ID,
            "profile_code": "dgt_general",
            "version_number": 3,
            "status": "active",
            "authority_code": "dgt",
            "display_name": "DGT synthetic portal",
            "portal_origin": PORTAL_ORIGIN,
            "requirements": {
                "representation_modes": ["self", "representative"],
                "fields": [
                    {
                        "field_code": "representation_authorization",
                        "step_order": 3,
                        "required": False,
                        "required_for_modes": ["representative"],
                        "purposes": ["representation_authorization"],
                        "media_types": ["application/pdf"],
                        "max_files": 1,
                        "max_bytes": 1024 * 1024,
                    },
                    {
                        "field_code": "main_document",
                        "step_order": 1,
                        "required": True,
                        "purposes": ["main_filing"],
                        "media_types": ["application/pdf"],
                        "max_files": 1,
                        "max_bytes": 1024 * 1024,
                    },
                    {
                        "field_code": "supporting_evidence",
                        "step_order": 2,
                        "required": False,
                        "purposes": ["supporting_evidence"],
                        "media_types": ["application/pdf"],
                        "max_files": 5,
                        "max_bytes": 1024 * 1024,
                    },
                ],
                "authorization_field_code": "representation_authorization",
                "unknown_secret": "must-not-be-projected",
            },
            "profile_sha256": "b" * 64,
            "created_by_operator_id": _id(19),
            "verified_by_operator_id": _id(20),
            "verified_at": NOW - timedelta(days=1),
            "metadata": {"secret": "must-not-be-projected"},
        }
        self.document_bytes = {
            DOCUMENT_ID: CONTENT,
            AUTHORIZATION_DOCUMENT_ID: AUTHORIZATION_CONTENT,
        }
        self.package = None
        self.package_supersedes: str | None = None
        self.idempotency_bindings: dict[tuple[str, str], dict[str, str]] = {}
        self.tickets: dict[str, dict[str, Any]] = {}
        self.audits: list[dict[str, Any]] = []
        self.exports: list[dict[str, Any]] = []
        self.byte_loads = 0

    def presenter_schema_ready(self, conn: Any) -> bool:
        del conn
        self.schema_calls += 1
        if self.schema_failure is not None:
            raise self.schema_failure
        return self.schema_ready

    def has_active_synthetic_case_access(
        self,
        conn: Any,
        *,
        case_id: str,
        operator_id: str,
    ) -> bool:
        del conn
        self.case_access_checks += 1
        if self.case_access_failure is not None:
            raise self.case_access_failure
        return bool(
            case_id == CASE_ID
            and self.case_test_mode
            and self.binding_active
            and self.binding_synthetic
            and self.binding_tenant_id == TENANT_ID
            and self.membership_active
            and self.membership_synthetic
            and self.membership_tenant_id == self.binding_tenant_id
            and self.membership_operator_id == operator_id
            and self.assignment_active
            and self.assignment_accepted
            and self.assignment_synthetic
            and self.assignment_role in {"responsible", "reviewer", "supervisor"}
            and self.assignment_operator_id == operator_id
        )

    def list_document_versions(
        self, conn: Any, *, case_id: str
    ) -> list[Mapping[str, Any]]:
        del conn
        self.document_list_calls += 1
        return [dict(row) for row in self.document_rows.values() if row["case_id"] == case_id]

    def list_destination_profiles(self, conn: Any) -> list[Mapping[str, Any]]:
        del conn
        self.destination_list_calls += 1
        return [dict(self.profile)]

    def lock_document_version_lineages(
        self,
        conn: Any,
        *,
        case_id: str,
        document_version_ids: Sequence[str],
    ) -> None:
        del conn
        if case_id != CASE_ID:
            raise AssertionError("lineage lock fuera del expediente")
        self.document_lineage_lock_calls += 1
        self.locked_document_version_ids = tuple(document_version_ids)

    def load_document_version(
        self,
        conn: Any,
        *,
        case_id: str,
        document_version_id: str,
        for_update: bool = False,
    ) -> Mapping[str, Any] | None:
        del conn, for_update
        self.document_load_calls += 1
        row = self.document_rows.get(document_version_id)
        return dict(row) if row and row["case_id"] == case_id else None

    def load_destination_profile(
        self, conn: Any, *, profile_id: str
    ) -> Mapping[str, Any] | None:
        del conn
        self.destination_load_calls += 1
        return dict(self.profile) if profile_id == PROFILE_ID else None

    def next_package_identity(
        self,
        conn: Any,
        *,
        case_id: str,
        destination_profile_id: str,
        supersedes_package_id: str | None,
    ) -> Mapping[str, Any]:
        del conn
        self.package_identity_calls += 1
        if case_id != CASE_ID or destination_profile_id != PROFILE_ID:
            raise AssertionError("package identity fuera del fixture")
        if supersedes_package_id:
            if not self.package or self.package.package_id != supersedes_package_id:
                raise PresenterNotFound()
            return {
                "logical_package_id": self.package.logical_package_id,
                "package_version": self.package.package_version + 1,
                "supersedes_package_id": supersedes_package_id,
            }
        return {
            "logical_package_id": LOGICAL_PACKAGE_ID,
            "package_version": 1,
            "supersedes_package_id": None,
        }

    def persist_frozen_package(
        self,
        conn: Any,
        *,
        package: Any,
        supersedes_package_id: str | None,
        idempotency_key: str,
        request_sha256: str,
    ) -> None:
        del conn
        self.package = package
        self.package_supersedes = supersedes_package_id
        self.idempotency_bindings[(package.created_by_operator_id, idempotency_key)] = {
            "case_id": package.case_id,
            "package_id": package.package_id,
            "request_sha256": request_sha256,
        }

    def load_idempotent_frozen_package(
        self,
        conn: Any,
        *,
        operator_id: str,
        idempotency_key: str,
    ) -> Mapping[str, Any] | None:
        binding = self.idempotency_bindings.get((operator_id, idempotency_key))
        if not binding:
            return None
        package = self.load_frozen_package(
            conn,
            case_id=binding["case_id"],
            package_id=binding["package_id"],
        )
        if not package:
            return None
        return {
            **dict(package),
            "idempotency_request_sha256": binding["request_sha256"],
        }

    def load_frozen_package(
        self,
        conn: Any,
        *,
        case_id: str,
        package_id: str,
        for_update: bool = False,
    ) -> Mapping[str, Any] | None:
        del conn, for_update
        package = self.package
        if not package or package.case_id != case_id or package.package_id != package_id:
            return None
        items: list[dict[str, Any]] = []
        for item in package.items:
            document = self.document_rows[item.document_version_id]
            items.append(
                {
                    "id": item.item_id,
                    "document_version_id": item.document_version_id,
                    "logical_document_id": item.logical_document_id,
                    "version_number": item.document_version,
                    "document_sha256": item.document_sha256,
                    "current_document_sha256": document["sha256"],
                    "state": document["state"],
                    "scan_status": document["scan_status"],
                    "detected_mime": document["detected_mime"],
                    "size_bytes": document["size_bytes"],
                    "item_order": item.item_order,
                    "field_code": item.field_code,
                    "purpose": item.purpose,
                    "portal_filename": item.portal_filename,
                    "required": item.required,
                    "item_sha256": canonical_sha256(item.material()),
                }
            )
        return {
            "id": package.package_id,
            "logical_package_id": package.logical_package_id,
            "package_version": package.package_version,
            "case_id": package.case_id,
            "destination_profile_id": package.destination_profile_id,
            "representation_mode": package.representation_mode,
            "authorization_document_version_id": (
                package.authorization_document_version_id
            ),
            "status": "frozen",
            "manifest": package.manifest_material(),
            "manifest_sha256": package.manifest_sha256,
            "created_by_operator_id": package.created_by_operator_id,
            "frozen_by_operator_id": package.frozen_by_operator_id,
            "frozen_at": datetime.fromisoformat(
                package.frozen_at.replace("Z", "+00:00")
            ),
            "expires_at": datetime.fromisoformat(
                package.expires_at.replace("Z", "+00:00")
            ),
            "profile_code": package.destination_profile_code,
            "profile_version": package.destination_profile_version,
            "portal_origin": package.portal_origin,
            "profile_status": "active",
            "profile_sha256": self.profile["profile_sha256"],
            "items": items,
        }

    def insert_ticket(self, conn: Any, *, binding: PresenterTicketBinding) -> None:
        del conn
        self.tickets[binding.ticket_sha256] = {
            "binding": binding,
            "used_at": None,
        }

    def consume_ticket(
        self,
        conn: Any,
        *,
        ticket_sha256: str,
        actor: PresenterActorContext,
        portal_origin: str,
        used_at: datetime,
    ) -> Mapping[str, Any] | None:
        del conn
        record = self.tickets.get(ticket_sha256)
        if not record or record["used_at"] is not None:
            return None
        binding: PresenterTicketBinding = record["binding"]
        issued = datetime.fromisoformat(binding.issued_at.replace("Z", "+00:00"))
        expires = datetime.fromisoformat(binding.expires_at.replace("Z", "+00:00"))
        if (
            binding.operator_id != actor.operator_id
            or binding.operator_session_id != actor.operator_session_id
            or binding.extension_client_id != actor.extension_client_id
            or binding.portal_origin != portal_origin
            or not issued <= used_at < expires
        ):
            return None
        record["used_at"] = used_at
        return {
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
            "issued_at": issued,
            "expires_at": expires,
            "used_at": used_at,
        }

    def load_document_bytes(
        self,
        conn: Any,
        *,
        case_id: str,
        document_version_id: str,
        expected_sha256: str,
    ) -> bytes:
        del conn
        self.byte_loads += 1
        document = self.document_rows.get(document_version_id)
        if (
            not document
            or document["case_id"] != case_id
            or document["sha256"] != expected_sha256
            or document["state"] != "active"
            or document["scan_status"] != "clean"
        ):
            raise PresenterNotFound("Bytes no disponibles")
        return self.document_bytes[document_version_id]

    def append_audit(self, conn: Any, **kwargs: Any) -> None:
        del conn
        self.audits.append(dict(kwargs))

    def insert_admin_export(self, conn: Any, **kwargs: Any) -> None:
        del conn
        self.exports.append(dict(kwargs))


def _runtime(
    *, enabled: bool = True, managed_extension_attestation_enabled: bool = False
) -> PresenterRuntimeConfiguration:
    return PresenterRuntimeConfiguration(
        enabled=enabled,
        environment="staging" if enabled else None,
        synthetic_only=True,
        real_data_allowed=False,
        external_effects_allowed=False,
        direct_storage_allowed=False,
        managed_extension_attestation_enabled=(
            managed_extension_attestation_enabled
        ),
    )


def _operator_actor() -> PresenterActorContext:
    return PresenterActorContext(
        operator_id=OPERATOR_ID,
        operator_session_id=OPERATOR_SESSION_ID,
        permissions=(
            PRESENTER_DOCUMENT_READ_PERMISSION,
            PRESENTER_PACKAGE_FREEZE_PERMISSION,
        ),
        role_codes=("rtm.operator",),
        client_kind=PresenterClientKind.OPERATOR_UI,
        authenticated_at=NOW - timedelta(hours=1),
    )


def _extension_actor(
    *, permissions: tuple[str, ...] | None = None
) -> PresenterActorContext:
    return PresenterActorContext(
        operator_id=OPERATOR_ID,
        operator_session_id=OPERATOR_SESSION_ID,
        permissions=permissions
        or (
            PRESENTER_HANDOFF_ISSUE_PERMISSION,
            PRESENTER_HANDOFF_EXCHANGE_PERMISSION,
        ),
        role_codes=("rtm.operator",),
        client_kind=PresenterClientKind.TRUSTED_EXTENSION,
        authenticated_at=NOW - timedelta(hours=1),
        extension_client_id=RTM_PRESENTER_EXTENSION_CLIENT_ID,
        managed_extension_attested=True,
        extension_attestation_id="a" * 64,
    )


def _admin_actor(
    *,
    include_permission: bool = True,
    role: str = PRESENTER_ADMIN_ROLE_CODE,
    verified_at: datetime | None = None,
    authenticated_at: datetime | None = None,
    grant_id: str | None = EXPORT_GRANT_ID,
) -> PresenterActorContext:
    return PresenterActorContext(
        operator_id=ADMIN_ID,
        operator_session_id=ADMIN_SESSION_ID,
        permissions=(PRESENTER_ADMIN_EXPORT_PERMISSION,) if include_permission else (),
        role_codes=(role,),
        client_kind=PresenterClientKind.ADMIN_EXPORT,
        authenticated_at=authenticated_at or NOW - timedelta(hours=1),
        reauthenticated_at=verified_at or NOW - timedelta(seconds=30),
        reauthentication_event_id=REAUTH_EVENT_ID,
        exceptional_export_grant_id=grant_id,
    )


class RTMPresenterServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakePresenterRepository()
        self.service = PresenterService(
            repository=self.repository,
            runtime=_runtime(managed_extension_attestation_enabled=True),
            clock=lambda: NOW,
            token_factory=lambda: RAW_TICKET,
            watermarker=lambda content, media_type, watermark: (
                content
                + b"\nRTM-WATERMARK["
                + media_type.encode("ascii")
                + b"]:"
                + watermark.encode("utf-8")
            ),
        )
        self.conn = object()

    def _freeze(self):
        return self.service.freeze_package(
            self.conn,
            actor=_operator_actor(),
            case_id=CASE_ID,
            destination_profile_id=PROFILE_ID,
            portal_origin=PORTAL_ORIGIN,
            representation_mode="self",
            authorization_document_version_id=None,
            selections=(
                PresenterItemSelection(
                    document_version_id=DOCUMENT_ID,
                    item_order=1,
                    field_code="main_document",
                    portal_filename="recurso_mejorado.pdf",
                ),
            ),
            expires_at=NOW + timedelta(hours=2),
            idempotency_key=IDEMPOTENCY_KEY,
        )

    def _issue(self, package):
        return self.service.issue_ticket(
            self.conn,
            actor=_extension_actor(),
            case_id=CASE_ID,
            package_id=package.package_id,
            package_item_id=package.items[0].item_id,
            portal_origin=PORTAL_ORIGIN,
            ttl_seconds=90,
        )

    def _invoke_case_operation(
        self,
        operation: str,
        *,
        service: PresenterService,
    ) -> Any:
        if operation == "list_documents":
            return service.list_documents(
                self.conn,
                actor=_operator_actor(),
                case_id=CASE_ID,
            )
        if operation == "workspace":
            return service.workspace(
                self.conn,
                actor=_operator_actor(),
                case_id=CASE_ID,
            )
        if operation == "freeze_package":
            return service.freeze_package(
                self.conn,
                actor=_operator_actor(),
                case_id=CASE_ID,
                destination_profile_id=PROFILE_ID,
                portal_origin=PORTAL_ORIGIN,
                representation_mode="self",
                authorization_document_version_id=None,
                selections=(
                    PresenterItemSelection(
                        document_version_id=DOCUMENT_ID,
                        item_order=1,
                        field_code="main_document",
                        portal_filename="recurso_mejorado.pdf",
                    ),
                ),
                expires_at=NOW + timedelta(hours=2),
                idempotency_key=IDEMPOTENCY_KEY,
            )
        raise AssertionError(f"Operacion desconocida: {operation}")

    @staticmethod
    def _fresh_service(repository: FakePresenterRepository) -> PresenterService:
        return PresenterService(
            repository=repository,
            runtime=_runtime(managed_extension_attestation_enabled=True),
            clock=lambda: NOW,
            token_factory=lambda: RAW_TICKET,
        )

    def test_case_scope_gate_precedes_every_case_data_operation(self):
        for operation in ("list_documents", "workspace", "freeze_package"):
            with self.subTest(operation=operation):
                repository = FakePresenterRepository()
                repository.case_test_mode = False
                service = self._fresh_service(repository)

                with self.assertRaises(PresenterForbidden) as denied:
                    self._invoke_case_operation(operation, service=service)

                self.assertEqual(denied.exception.code, "presenter.forbidden")
                self.assertEqual(denied.exception.status_code, 403)
                self.assertEqual(repository.case_access_checks, 1)
                self.assertEqual(repository.document_list_calls, 0)
                self.assertEqual(repository.destination_list_calls, 0)
                self.assertEqual(repository.document_load_calls, 0)
                self.assertEqual(repository.destination_load_calls, 0)
                self.assertEqual(repository.package_identity_calls, 0)
                self.assertIsNone(repository.package)

    def test_case_scope_gate_denies_all_a1s_scope_failures_without_enumeration(self):
        failures = {
            "case_not_test_mode": ("case_test_mode", False),
            "binding_missing_or_inactive": ("binding_active", False),
            "binding_not_synthetic": ("binding_synthetic", False),
            "membership_missing_or_inactive": ("membership_active", False),
            "membership_not_synthetic": ("membership_synthetic", False),
            "membership_other_tenant": ("membership_tenant_id", _id(90)),
            "membership_other_operator": ("membership_operator_id", _id(91)),
            "assignment_missing_or_inactive": ("assignment_active", False),
            "assignment_not_accepted": ("assignment_accepted", False),
            "assignment_not_synthetic": ("assignment_synthetic", False),
            "assignment_observer_only": ("assignment_role", "observer"),
            "assignment_other_operator": ("assignment_operator_id", _id(92)),
        }
        observed_errors: set[tuple[str, str, int]] = set()
        for reason, (attribute, value) in failures.items():
            with self.subTest(reason=reason):
                repository = FakePresenterRepository()
                setattr(repository, attribute, value)
                service = self._fresh_service(repository)

                with self.assertRaises(PresenterForbidden) as denied:
                    service.workspace(
                        self.conn,
                        actor=_operator_actor(),
                        case_id=CASE_ID,
                    )

                observed_errors.add(
                    (
                        denied.exception.code,
                        denied.exception.message,
                        denied.exception.status_code,
                    )
                )
                self.assertEqual(repository.document_list_calls, 0)
                self.assertEqual(repository.destination_list_calls, 0)
        self.assertEqual(
            observed_errors,
            {("presenter.forbidden", "Operacion Presenter no autorizada", 403)},
        )

    def test_case_scope_gate_fails_closed_when_a1s_repository_is_unavailable(self):
        repository = FakePresenterRepository()
        repository.case_access_failure = RuntimeError("missing a1s relation")
        service = self._fresh_service(repository)

        with self.assertRaises(PresenterForbidden) as denied:
            service.list_documents(
                self.conn,
                actor=_operator_actor(),
                case_id=CASE_ID,
            )

        self.assertEqual(denied.exception.code, "presenter.forbidden")
        self.assertNotIn("a1s", denied.exception.message.lower())
        self.assertEqual(repository.document_list_calls, 0)

    def test_sql_case_scope_is_one_existential_tenant_and_assignment_query(self):
        class ScalarResult:
            def scalar(self) -> bool:
                return True

        class CapturingConnection:
            def __init__(self) -> None:
                self.sql = ""
                self.params: dict[str, str] = {}

            def execute(self, statement: Any, params: Mapping[str, str]) -> ScalarResult:
                self.sql = str(statement)
                self.params = dict(params)
                return ScalarResult()

        conn = CapturingConnection()
        allowed = SqlPresenterRepository().has_active_synthetic_case_access(
            conn,
            case_id=CASE_ID,
            operator_id=OPERATOR_ID,
        )

        self.assertTrue(allowed)
        self.assertEqual(
            conn.params,
            {"case_id": CASE_ID, "operator_id": OPERATOR_ID},
        )
        normalized = " ".join(conn.sql.split()).lower()
        self.assertIn("select exists", normalized)
        self.assertIn("rtm_connect_a1s_case_bindings", normalized)
        self.assertIn("rtm_connect_a1s_tenants", normalized)
        self.assertIn("rtm_connect_a1s_memberships", normalized)
        self.assertIn("rtm_work_assignments", normalized)
        self.assertIn("coalesce(c.test_mode,false)=true", normalized)
        self.assertIn("m.operator_id=cast(:operator_id as uuid)", normalized)
        self.assertIn("m.tenant_id=b.tenant_id", normalized)
        self.assertIn("w.operator_id=cast(:operator_id as uuid)", normalized)
        self.assertIn("w.status='active'", normalized)
        self.assertIn("w.accepted_at is not null", normalized)
        self.assertIn("'responsible', 'reviewer', 'supervisor'", normalized)
        self.assertIn("rtm_presenter_synthetic_only", normalized)
        self.assertIn("synthetic_only", normalized)

        class MissingA1STables:
            def execute(self, statement: Any, params: Mapping[str, str]) -> Any:
                del statement, params
                raise RuntimeError("undefined table")

        self.assertFalse(
            SqlPresenterRepository().has_active_synthetic_case_access(
                MissingA1STables(),
                case_id=CASE_ID,
                operator_id=OPERATOR_ID,
            )
        )

    def test_workspace_is_a_sanitized_checklist_without_binary_actions(self):
        payload = self.service.workspace(
            self.conn, actor=_operator_actor(), case_id=CASE_ID
        )

        self.assertEqual(payload["case_id"], CASE_ID)
        self.assertEqual(payload["actions"]["freeze_package"], True)
        for forbidden_action in (
            "operator_download",
            "operator_preview",
            "operator_zip",
            "operator_handoff",
        ):
            self.assertIs(payload["actions"][forbidden_action], False)
        self.assertFalse(payload["storage_references_exposed"])
        fields = payload["destinations"][0]["fields"]
        self.assertEqual(
            [field["field_code"] for field in fields],
            [
                "main_document",
                "supporting_evidence",
                "representation_authorization",
            ],
        )
        self.assertEqual([field["step_order"] for field in fields], [1, 2, 3])
        serialized = json.dumps(payload, sort_keys=True).lower()
        for forbidden in (
            "b2_bucket",
            "b2_key",
            "presigned_url",
            "unknown_secret",
            "must-not-be-projected",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(self.repository.byte_loads, 0)

    def test_profile_step_order_must_be_explicit_unique_positive_and_contiguous(self):
        mutations = {
            "missing": lambda fields: fields[0].pop("step_order"),
            "boolean": lambda fields: fields[0].update(step_order=True),
            "string": lambda fields: fields[0].update(step_order="3"),
            "zero": lambda fields: fields[0].update(step_order=0),
            "duplicate": lambda fields: fields[1].update(step_order=3),
            "gap": lambda fields: fields[1].update(step_order=4),
        }
        for reason, mutate in mutations.items():
            with self.subTest(reason=reason):
                repository = FakePresenterRepository()
                mutate(repository.profile["requirements"]["fields"])
                service = self._fresh_service(repository)

                with self.assertRaises(PresenterConflict) as invalid:
                    service.workspace(
                        self.conn,
                        actor=_operator_actor(),
                        case_id=CASE_ID,
                    )

                self.assertEqual(
                    invalid.exception.code, "presenter.profile_contract_invalid"
                )

    def test_profile_requires_independent_creator_and_verifier(self):
        self.repository.profile["created_by_operator_id"] = self.repository.profile[
            "verified_by_operator_id"
        ]

        with self.assertRaises(PresenterConflict) as invalid:
            self.service.workspace(
                self.conn,
                actor=_operator_actor(),
                case_id=CASE_ID,
            )

        self.assertEqual(invalid.exception.code, "presenter.profile_contract_invalid")

    def test_sql_profile_resolution_uses_only_latest_independently_verified_revision(self):
        class MappingResult:
            def mappings(self) -> "MappingResult":
                return self

            def all(self) -> list[Mapping[str, Any]]:
                return []

            def first(self) -> Mapping[str, Any] | None:
                return None

        class CapturingConnection:
            def __init__(self) -> None:
                self.calls: list[tuple[str, Mapping[str, Any]]] = []

            def execute(
                self,
                statement: Any,
                params: Mapping[str, Any] | None = None,
            ) -> MappingResult:
                self.calls.append((str(statement), dict(params or {})))
                return MappingResult()

        conn = CapturingConnection()
        repository = SqlPresenterRepository()
        self.assertEqual(repository.list_destination_profiles(conn), [])
        self.assertIsNone(
            repository.load_destination_profile(conn, profile_id=PROFILE_ID)
        )

        list_sql = " ".join(conn.calls[0][0].split()).lower()
        self.assertIn("distinct on (profile_code)", list_sql)
        self.assertIn(
            "order by profile_code, version_number desc, id desc", list_sql
        )
        self.assertIn("from latest where status='active'", list_sql)
        self.assertIn(
            "created_by_operator_id <> verified_by_operator_id", list_sql
        )
        load_sql = " ".join(conn.calls[1][0].split()).lower()
        self.assertIn("not exists", load_sql)
        self.assertIn("newer.profile_code=p.profile_code", load_sql)
        self.assertIn("newer.version_number > p.version_number", load_sql)
        self.assertIn(
            "p.created_by_operator_id <> p.verified_by_operator_id", load_sql
        )
        self.assertEqual(conn.calls[1][1], {"profile_id": PROFILE_ID})

    def test_sql_document_resolution_marks_history_and_rejects_stale_freeze(self):
        class MappingResult:
            def mappings(self) -> "MappingResult":
                return self

            def all(self) -> list[Mapping[str, Any]]:
                return []

            def first(self) -> Mapping[str, Any] | None:
                return None

        class CapturingConnection:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def execute(
                self,
                statement: Any,
                params: Mapping[str, Any] | None = None,
            ) -> MappingResult:
                del params
                self.calls.append(" ".join(str(statement).split()).lower())
                return MappingResult()

        conn = CapturingConnection()
        repository = SqlPresenterRepository()
        self.assertEqual(repository.list_document_versions(conn, case_id=CASE_ID), [])
        self.assertIsNone(
            repository.load_document_version(
                conn,
                case_id=CASE_ID,
                document_version_id=DOCUMENT_ID,
                for_update=True,
            )
        )

        self.assertIn("then 'superseded'", conn.calls[0])
        self.assertIn("newer.version_number > v.version_number", conn.calls[0])
        self.assertIn("pg_advisory_xact_lock", conn.calls[1])
        self.assertIn("hashtextextended", conn.calls[1])
        self.assertIn("rtm-presenter-document-lineage:", conn.calls[1])
        self.assertIn("case_id::text", conn.calls[1])
        self.assertIn("logical_document_id::text", conn.calls[1])
        self.assertIn("and not exists", conn.calls[2])
        self.assertNotIn("newer.state='active'", conn.calls[0])
        self.assertNotIn("newer.scan_status='clean'", conn.calls[0])
        self.assertNotIn("newer.state='active'", conn.calls[2])
        self.assertNotIn("newer.scan_status='clean'", conn.calls[2])
        self.assertTrue(conn.calls[2].endswith("for update"))

    def test_sql_freeze_locks_document_lineages_in_stable_database_order(self):
        second_logical_id = _id(31)

        class MappingResult:
            def __init__(self, rows=()) -> None:
                self.rows = list(rows)

            def mappings(self) -> "MappingResult":
                return self

            def all(self) -> list[Mapping[str, Any]]:
                return list(self.rows)

        class CapturingConnection:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def execute(
                self,
                statement: Any,
                params: Mapping[str, Any] | None = None,
            ) -> MappingResult:
                sql = " ".join(str(statement).split()).lower()
                self.calls.append((sql, dict(params or {})))
                if "select distinct case_id, logical_document_id" in sql:
                    return MappingResult(
                        (
                            {
                                "case_id": CASE_ID,
                                "logical_document_id": LOGICAL_DOCUMENT_ID,
                            },
                            {
                                "case_id": CASE_ID,
                                "logical_document_id": second_logical_id,
                            },
                        )
                    )
                return MappingResult()

        conn = CapturingConnection()
        SqlPresenterRepository().lock_document_version_lineages(
            conn,
            case_id=CASE_ID,
            document_version_ids=(DOCUMENT_ID, AUTHORIZATION_DOCUMENT_ID),
        )

        self.assertIn("order by case_id, logical_document_id", conn.calls[0][0])
        self.assertEqual(
            conn.calls[0][1]["document_version_ids"],
            [DOCUMENT_ID, AUTHORIZATION_DOCUMENT_ID],
        )
        self.assertEqual(len(conn.calls), 3)
        for sql, _ in conn.calls[1:]:
            self.assertIn("pg_advisory_xact_lock", sql)
            self.assertIn("hashtextextended", sql)
        self.assertEqual(
            [call[1]["lock_scope"] for call in conn.calls[1:]],
            [
                (
                    "rtm-presenter-document-lineage:"
                    f"{CASE_ID}:{LOGICAL_DOCUMENT_ID}"
                ),
                (
                    "rtm-presenter-document-lineage:"
                    f"{CASE_ID}:{second_logical_id}"
                ),
            ],
        )

    def test_freeze_binds_exact_version_hash_order_field_and_origin(self):
        package = self._freeze()

        self.assertEqual(package.status, PresenterPackageStatus.FROZEN)
        self.assertEqual(package.portal_origin, PORTAL_ORIGIN)
        self.assertEqual(package.package_version, 1)
        self.assertEqual(package.destination_profile_sha256, "b" * 64)
        self.assertEqual(len(package.items), 1)
        item = package.items[0]
        self.assertEqual(item.document_version_id, DOCUMENT_ID)
        self.assertEqual(item.document_version, 2)
        self.assertEqual(item.document_sha256, CONTENT_SHA256)
        self.assertEqual(item.item_order, 1)
        self.assertEqual(item.field_code, "main_document")
        self.assertEqual(item.portal_filename, "recurso_mejorado.pdf")
        self.assertEqual(
            canonical_sha256(package.manifest_material()), package.manifest_sha256
        )
        self.assertIs(self.repository.package, package)
        self.assertEqual(self.repository.byte_loads, 0)
        self.assertEqual(self.repository.document_lineage_lock_calls, 1)
        self.assertEqual(
            self.repository.locked_document_version_ids,
            (DOCUMENT_ID,),
        )
        self.assertEqual(
            self.repository.audits[-1]["event_type"], "presenter.package.frozen"
        )

    def test_freeze_replays_same_persisted_idempotency_command(self):
        first = self._freeze()
        audit_count = len(self.repository.audits)

        replay = self._freeze()

        self.assertEqual(replay.package_id, first.package_id)
        self.assertEqual(replay.manifest_sha256, first.manifest_sha256)
        self.assertEqual(len(self.repository.audits), audit_count)

    def test_freeze_rejects_idempotency_key_reused_with_other_payload(self):
        self._freeze()

        with self.assertRaises(PresenterConflict) as conflict:
            self.service.freeze_package(
                self.conn,
                actor=_operator_actor(),
                case_id=CASE_ID,
                destination_profile_id=PROFILE_ID,
                portal_origin=PORTAL_ORIGIN,
                representation_mode="self",
                authorization_document_version_id=None,
                selections=(
                    PresenterItemSelection(
                        document_version_id=DOCUMENT_ID,
                        item_order=1,
                        field_code="main_document",
                        portal_filename="otro_nombre.pdf",
                    ),
                ),
                expires_at=NOW + timedelta(hours=2),
                idempotency_key=IDEMPOTENCY_KEY,
            )

        self.assertEqual(
            conflict.exception.code,
            "presenter.idempotency_key_reused",
        )

    def test_freeze_requires_valid_idempotency_key(self):
        with self.assertRaises(PresenterConflict) as missing:
            self.service.freeze_package(
                self.conn,
                actor=_operator_actor(),
                case_id=CASE_ID,
                destination_profile_id=PROFILE_ID,
                portal_origin=PORTAL_ORIGIN,
                representation_mode="self",
                authorization_document_version_id=None,
                selections=(
                    PresenterItemSelection(
                        document_version_id=DOCUMENT_ID,
                        item_order=1,
                        field_code="main_document",
                        portal_filename="recurso.pdf",
                    ),
                ),
                expires_at=NOW + timedelta(hours=2),
            )

        self.assertEqual(
            missing.exception.code,
            "presenter.idempotency_key_required",
        )

    def test_sql_idempotency_uses_transaction_advisory_lock(self):
        source = inspect.getsource(
            SqlPresenterRepository.load_idempotent_frozen_package
        )
        self.assertIn("pg_advisory_xact_lock", source)
        self.assertIn("hashtextextended", source)
        self.assertIn("operator_id", source)
        self.assertIn("idempotency_key", source)

    def test_freeze_rejects_documents_that_move_backwards_across_profile_steps(self):
        with self.assertRaises(PresenterConflict) as invalid:
            self.service.freeze_package(
                self.conn,
                actor=_operator_actor(),
                case_id=CASE_ID,
                destination_profile_id=PROFILE_ID,
                portal_origin=PORTAL_ORIGIN,
                representation_mode="representative",
                authorization_document_version_id=AUTHORIZATION_DOCUMENT_ID,
                selections=(
                    PresenterItemSelection(
                        document_version_id=AUTHORIZATION_DOCUMENT_ID,
                        item_order=1,
                        field_code="representation_authorization",
                        portal_filename="autorizacion.pdf",
                    ),
                    PresenterItemSelection(
                        document_version_id=DOCUMENT_ID,
                        item_order=2,
                        field_code="main_document",
                        portal_filename="recurso.pdf",
                    ),
                ),
                expires_at=NOW + timedelta(hours=2),
                idempotency_key=IDEMPOTENCY_KEY,
            )

        self.assertEqual(
            invalid.exception.code, "presenter.package_field_order_invalid"
        )
        self.assertEqual(self.repository.document_load_calls, 0)

    def test_freeze_allows_consecutive_multiple_documents_in_one_profile_step(self):
        second_document_id = _id(92)
        second_logical_document_id = _id(93)
        second_content = b"%PDF-1.7\nSecond synthetic filing document\n%%EOF"
        second_row = dict(self.repository.document_rows[DOCUMENT_ID])
        second_row.update(
            {
                "id": second_document_id,
                "logical_document_id": second_logical_document_id,
                "sha256": hashlib.sha256(second_content).hexdigest(),
                "original_filename": "anexo_recurso.pdf",
                "size_bytes": len(second_content),
            }
        )
        self.repository.document_rows[second_document_id] = second_row
        self.repository.document_bytes[second_document_id] = second_content
        main_field = next(
            field
            for field in self.repository.profile["requirements"]["fields"]
            if field["field_code"] == "main_document"
        )
        main_field["max_files"] = 2

        package = self.service.freeze_package(
            self.conn,
            actor=_operator_actor(),
            case_id=CASE_ID,
            destination_profile_id=PROFILE_ID,
            portal_origin=PORTAL_ORIGIN,
            representation_mode="self",
            authorization_document_version_id=None,
            selections=(
                PresenterItemSelection(
                    document_version_id=DOCUMENT_ID,
                    item_order=1,
                    field_code="main_document",
                    portal_filename="recurso.pdf",
                ),
                PresenterItemSelection(
                    document_version_id=second_document_id,
                    item_order=2,
                    field_code="main_document",
                    portal_filename="anexo_recurso.pdf",
                ),
            ),
            expires_at=NOW + timedelta(hours=2),
            idempotency_key=IDEMPOTENCY_KEY,
        )

        self.assertEqual(len(package.items), 2)
        self.assertEqual(
            [item.field_code for item in package.items],
            ["main_document", "main_document"],
        )

    def test_representative_package_requires_exact_authorization_field(self):
        with self.assertRaises(PresenterConflict) as wrong_field:
            self.service.freeze_package(
                self.conn,
                actor=_operator_actor(),
                case_id=CASE_ID,
                destination_profile_id=PROFILE_ID,
                portal_origin=PORTAL_ORIGIN,
                representation_mode="representative",
                authorization_document_version_id=DOCUMENT_ID,
                selections=(
                    PresenterItemSelection(
                        document_version_id=DOCUMENT_ID,
                        item_order=1,
                        field_code="main_document",
                        portal_filename="recurso.pdf",
                    ),
                    PresenterItemSelection(
                        document_version_id=AUTHORIZATION_DOCUMENT_ID,
                        item_order=2,
                        field_code="representation_authorization",
                        portal_filename="autorizacion.pdf",
                    ),
                ),
                expires_at=NOW + timedelta(hours=2),
                idempotency_key=IDEMPOTENCY_KEY,
            )
        self.assertEqual(
            wrong_field.exception.code, "presenter.authorization_field_mismatch"
        )

        package = self.service.freeze_package(
            self.conn,
            actor=_operator_actor(),
            case_id=CASE_ID,
            destination_profile_id=PROFILE_ID,
            portal_origin=PORTAL_ORIGIN,
            representation_mode="representative",
            authorization_document_version_id=AUTHORIZATION_DOCUMENT_ID,
            selections=(
                PresenterItemSelection(
                    document_version_id=DOCUMENT_ID,
                    item_order=1,
                    field_code="main_document",
                    portal_filename="recurso.pdf",
                ),
                PresenterItemSelection(
                    document_version_id=AUTHORIZATION_DOCUMENT_ID,
                    item_order=2,
                    field_code="representation_authorization",
                    portal_filename="autorizacion.pdf",
                ),
            ),
            expires_at=NOW + timedelta(hours=2),
            idempotency_key=IDEMPOTENCY_KEY,
        )
        self.assertEqual(
            package.authorization_document_version_id,
            AUTHORIZATION_DOCUMENT_ID,
        )
        self.assertTrue(package.items[1].required)

    def test_ui_operator_cannot_issue_or_exchange_handoff_even_with_permission(self):
        package = self._freeze()
        ui_with_handoff_permissions = PresenterActorContext(
            operator_id=OPERATOR_ID,
            operator_session_id=OPERATOR_SESSION_ID,
            permissions=(
                PRESENTER_HANDOFF_ISSUE_PERMISSION,
                PRESENTER_HANDOFF_EXCHANGE_PERMISSION,
            ),
            role_codes=("rtm.operator",),
            client_kind=PresenterClientKind.OPERATOR_UI,
            authenticated_at=NOW,
        )

        with self.assertRaises(PresenterPolicyError):
            self.service.issue_ticket(
                self.conn,
                actor=ui_with_handoff_permissions,
                case_id=CASE_ID,
                package_id=package.package_id,
                package_item_id=package.items[0].item_id,
                portal_origin=PORTAL_ORIGIN,
            )
        with self.assertRaises(PresenterPolicyError):
            self.service.exchange_ticket(
                self.conn,
                actor=ui_with_handoff_permissions,
                raw_ticket=RAW_TICKET,
                request_origin=PORTAL_ORIGIN,
            )
        self.assertEqual(self.repository.tickets, {})
        self.assertEqual(self.repository.byte_loads, 0)

    def test_extension_transport_is_default_off_even_for_attested_actor(self):
        package = self._freeze()
        default_off = PresenterService(
            repository=self.repository,
            runtime=_runtime(),
            clock=lambda: NOW,
            token_factory=lambda: RAW_TICKET,
        )
        with self.assertRaises(PresenterForbidden):
            default_off.issue_ticket(
                self.conn,
                actor=_extension_actor(),
                case_id=CASE_ID,
                package_id=package.package_id,
                package_item_id=package.items[0].item_id,
                portal_origin=PORTAL_ORIGIN,
            )
        self.assertEqual(self.repository.tickets, {})

    def test_ticket_is_hash_only_context_bound_and_single_use(self):
        package = self._freeze()
        issued = self._issue(package)
        digest = hashlib.sha256(RAW_TICKET.encode("utf-8")).hexdigest()

        self.assertEqual(issued.token, RAW_TICKET)
        self.assertIn(digest, self.repository.tickets)
        stored: PresenterTicketBinding = self.repository.tickets[digest]["binding"]
        self.assertEqual(stored.operator_id, OPERATOR_ID)
        self.assertEqual(stored.operator_session_id, OPERATOR_SESSION_ID)
        self.assertEqual(stored.extension_client_id, RTM_PRESENTER_EXTENSION_CLIENT_ID)
        self.assertEqual(stored.case_id, CASE_ID)
        self.assertEqual(stored.package_id, package.package_id)
        self.assertEqual(stored.package_item_id, package.items[0].item_id)
        self.assertEqual(stored.portal_origin, PORTAL_ORIGIN)
        self.assertEqual(stored.field_code, "main_document")
        self.assertNotIn(RAW_TICKET, repr(stored))
        self.assertNotIn(RAW_TICKET, json.dumps(stored.__dict__, sort_keys=True))

        with self.assertRaises(PresenterNotFound):
            self.service.exchange_ticket(
                self.conn,
                actor=_extension_actor(),
                raw_ticket=RAW_TICKET,
                request_origin="https://other.synthetic.example",
            )
        self.assertIsNone(self.repository.tickets[digest]["used_at"])

        payload = self.service.exchange_ticket(
            self.conn,
            actor=_extension_actor(
                permissions=(PRESENTER_HANDOFF_EXCHANGE_PERMISSION,)
            ),
            raw_ticket=RAW_TICKET,
            request_origin=PORTAL_ORIGIN,
        )
        self.assertEqual(payload.content, CONTENT)
        self.assertEqual(payload.sha256, CONTENT_SHA256)
        self.assertEqual(payload.field_code, "main_document")
        self.assertEqual(
            payload.headers["Content-Disposition"],
            'attachment; filename="recurso_mejorado.pdf"',
        )
        self.assertEqual(payload.headers["Cache-Control"], "no-store, max-age=0")
        self.assertIsNotNone(self.repository.tickets[digest]["used_at"])
        self.assertEqual(self.repository.byte_loads, 1)

        with self.assertRaises(PresenterNotFound):
            self.service.exchange_ticket(
                self.conn,
                actor=_extension_actor(),
                raw_ticket=RAW_TICKET,
                request_origin=PORTAL_ORIGIN,
            )
        self.assertEqual(self.repository.byte_loads, 1)

    def test_exchange_consumes_then_blocks_if_frozen_document_hash_changed(self):
        package = self._freeze()
        self._issue(package)
        digest = hashlib.sha256(RAW_TICKET.encode("utf-8")).hexdigest()
        self.repository.document_rows[DOCUMENT_ID]["sha256"] = "e" * 64

        with self.assertRaises(PresenterConflict):
            self.service.exchange_ticket(
                self.conn,
                actor=_extension_actor(),
                raw_ticket=RAW_TICKET,
                request_origin=PORTAL_ORIGIN,
            )

        self.assertIsNotNone(self.repository.tickets[digest]["used_at"])
        self.assertEqual(self.repository.byte_loads, 0)

    def test_admin_export_requires_exact_role_permission_and_recent_session_reauth(self):
        package = self._freeze()
        denied = (
            _admin_actor(include_permission=False),
            _admin_actor(role="rtm.supervisor"),
            _admin_actor(grant_id=None),
            _admin_actor(verified_at=NOW - timedelta(minutes=6)),
            _admin_actor(verified_at=NOW, authenticated_at=NOW),
        )
        for actor in denied:
            with self.subTest(role=actor.role_codes, permissions=actor.permissions):
                with self.assertRaises(PresenterPolicyError):
                    self.service.export_package_admin(
                        self.conn,
                        actor=actor,
                        case_id=CASE_ID,
                        package_id=package.package_id,
                        reason="Exportacion excepcional sintetica autorizada",
                    )
        self.assertEqual(self.repository.exports, [])
        self.assertEqual(self.repository.byte_loads, 0)
        denied_audits = [
            audit
            for audit in self.repository.audits
            if audit["event_type"] == "presenter.admin_export.denied"
        ]
        self.assertEqual(len(denied_audits), 5)

    def test_authorized_admin_export_is_watermarked_manifested_and_audited(self):
        package = self._freeze()
        actor = _admin_actor()
        payload = self.service.export_package_admin(
            self.conn,
            actor=actor,
            case_id=CASE_ID,
            package_id=package.package_id,
            reason="Exportacion excepcional sintetica autorizada",
        )

        self.assertEqual(payload.headers["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(hashlib.sha256(payload.content).hexdigest(), payload.export_sha256)
        with zipfile.ZipFile(io.BytesIO(payload.content), "r") as bundle:
            names = set(bundle.namelist())
            self.assertIn("RTM_EXPORT_MANIFEST.json", names)
            self.assertIn("RTM_EXPORT_WATERMARK.txt", names)
            document_name = next(name for name in names if name.startswith("01_"))
            marked = bundle.read(document_name)
            self.assertNotEqual(marked, CONTENT)
            self.assertIn(b"RTM-WATERMARK", marked)
            manifest_bytes = bundle.read("RTM_EXPORT_MANIFEST.json")
            manifest = json.loads(manifest_bytes)
            self.assertEqual(manifest["package_id"], package.package_id)
            self.assertEqual(manifest["items"][0]["source_sha256"], CONTENT_SHA256)
            self.assertEqual(
                hashlib.sha256(manifest_bytes).hexdigest(), payload.manifest_sha256
            )
        self.assertEqual(len(self.repository.exports), 1)
        export_record = self.repository.exports[0]
        expected_evidence = canonical_sha256(
            {
                "evidence_type": "rtm.presenter.session_reauthentication.v1",
                "operator_id": ADMIN_ID,
                "operator_session_id": ADMIN_SESSION_ID,
                "reauthenticated_at": actor.reauthenticated_at.isoformat(),
                "reauthentication_event_id": REAUTH_EVENT_ID,
            }
        )
        self.assertEqual(
            export_record["reauthentication_evidence_sha256"], expected_evidence
        )
        self.assertEqual(
            export_record["export_scope"]["exceptional_export_grant_id"],
            EXPORT_GRANT_ID,
        )
        self.assertEqual(
            self.repository.audits[-1]["event_type"],
            "presenter.admin_export.completed",
        )

    def test_runtime_disabled_fails_before_repository_or_data_access(self):
        service = PresenterService(
            repository=self.repository,
            runtime=_runtime(enabled=False),
            clock=lambda: NOW,
        )
        with self.assertRaises(PresenterRuntimeDisabled):
            service.workspace(self.conn, actor=_operator_actor(), case_id=CASE_ID)
        self.assertEqual(self.repository.schema_calls, 0)
        self.assertEqual(self.repository.byte_loads, 0)

    def test_schema_not_ready_fails_before_case_or_document_access(self):
        self.repository.schema_ready = False

        with self.assertRaises(PresenterSchemaNotReady) as denied:
            self.service.workspace(
                self.conn,
                actor=_operator_actor(),
                case_id=CASE_ID,
            )

        self.assertEqual(denied.exception.code, "presenter.schema_not_ready")
        self.assertEqual(denied.exception.status_code, 503)
        self.assertEqual(self.repository.schema_calls, 1)
        self.assertEqual(self.repository.case_access_checks, 0)
        self.assertEqual(self.repository.document_list_calls, 0)
        self.assertEqual(self.repository.destination_list_calls, 0)

    def test_schema_readiness_error_fails_closed_without_data_access(self):
        self.repository.schema_failure = RuntimeError("catalog unavailable")

        with self.assertRaises(PresenterSchemaNotReady):
            self.service.list_documents(
                self.conn,
                actor=_operator_actor(),
                case_id=CASE_ID,
            )

        self.assertEqual(self.repository.case_access_checks, 0)
        self.assertEqual(self.repository.document_list_calls, 0)

    def test_sql_repository_runtime_readiness_is_read_only(self):
        source = inspect.getsource(SqlPresenterRepository.presenter_schema_ready)
        self.assertNotIn("ensure_rtm_presenter_schema", source)
        self.assertNotIn("CREATE TABLE", source.upper())
        self.assertNotIn("CREATE INDEX", source.upper())
        self.assertNotIn("CREATE TRIGGER", source.upper())
        self.assertIn("rtm_management_schema_migrations", source)
        self.assertIn("schema_contract", source)

    def test_router_has_no_ui_binary_or_handoff_route_and_no_password_body(self):
        router_source = (
            Path(__file__).resolve().parents[1] / "rtm_presenter_router.py"
        ).read_text(encoding="utf-8")
        self.assertIn('@router.get("/cases/{case_id}/workspace")', router_source)
        self.assertIn('"/extension/tickets/exchange"', router_source)
        self.assertNotIn('@router.get("/cases/{case_id}/download', router_source)
        self.assertNotIn('@router.post("/cases/{case_id}/handoff', router_source)
        self.assertNotIn("password:", router_source.lower())
        self.assertNotIn("reauthentication_evidence_sha256: str = Field", router_source)
        self.assertIn("e.event_type='auth.reauthenticated'", router_source)
        self.assertIn("e.occurred_at=s.last_verified_at", router_source)
        self.assertIn("has_explicit_reauthentication(session)", router_source)


if __name__ == "__main__":
    unittest.main()
