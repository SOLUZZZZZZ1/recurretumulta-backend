from __future__ import annotations

import hashlib
import inspect
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from pydantic import ValidationError

from rtm_presenter_contracts import PresenterClientKind, canonical_sha256
from rtm_presenter_policy import (
    PRESENTER_DELIVERY_PREPARE_PERMISSION,
    PRESENTER_HANDOFF_EXCHANGE_PERMISSION,
    PRESENTER_RECEIPT_VERIFY_PERMISSION,
    RTM_PRESENTER_EXTENSION_CLIENT_ID,
    PresenterActorContext,
    PresenterPolicyError,
    PresenterRuntimeConfiguration,
)
from rtm_presenter_portal_session import (
    RTM_PRESENTER_DEADLINE_SOURCE_EVENT,
    PresenterPortalSessionService,
    _attachment_manifest,
)
from rtm_presenter_router import (
    RecordSyntheticPortalAttachmentBody,
    VerifyPortalReceiptBody,
    capture_presenter_receipt_pending_route,
)
from rtm_presenter_service import PresenterConflict, PresenterForbidden


NOW = datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc)


def _id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


CASE_ID = _id(1)
PROFILE_ID = _id(2)
OPERATOR_ID = _id(3)
SESSION_ID = _id(4)
MAIN_DOCUMENT_ID = _id(5)
RECEIPT_DOCUMENT_ID = _id(6)
LOGICAL_MAIN_ID = _id(7)
LOGICAL_RECEIPT_ID = _id(8)
REVIEWER_OPERATOR_ID = _id(9)
REVIEWER_SESSION_ID = _id(10)
CAPTURED_RECEIPT_DOCUMENT_ID = _id(11)
LOGICAL_CAPTURED_RECEIPT_ID = _id(12)
ORIGIN = "https://portal.synthetic.example"
FIELD_FINGERPRINT = hashlib.sha256(b"main-input").hexdigest()
ADAPTER_SHA256 = hashlib.sha256(b"synthetic-adapter-v1").hexdigest()
PROFILE_SHA256 = hashlib.sha256(b"synthetic-profile-v1").hexdigest()
MAIN_SHA256 = hashlib.sha256(b"main-document").hexdigest()


def _synthetic_pdf() -> bytes:
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Contents 4 0 R >>"
        ),
        b"<< /Length 0 >>\nstream\n\nendstream",
    )
    document = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode("ascii"))
        document.extend(body)
        document.extend(b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    document.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF"
        ).encode("ascii")
    )
    return bytes(document)


RECEIPT_PDF = _synthetic_pdf()
RECEIPT_SHA256 = hashlib.sha256(RECEIPT_PDF).hexdigest()


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _runtime(*, bridge: bool = True) -> PresenterRuntimeConfiguration:
    return PresenterRuntimeConfiguration(
        enabled=True,
        environment="staging",
        synthetic_only=True,
        real_data_allowed=False,
        external_effects_allowed=False,
        direct_storage_allowed=False,
        managed_extension_attestation_enabled=bridge,
    )


def _ui_actor(*, receipt_verify: bool = False) -> PresenterActorContext:
    permissions = [PRESENTER_DELIVERY_PREPARE_PERMISSION]
    if receipt_verify:
        permissions.append(PRESENTER_RECEIPT_VERIFY_PERMISSION)
    return PresenterActorContext(
        operator_id=OPERATOR_ID,
        operator_session_id=SESSION_ID,
        permissions=tuple(permissions),
        role_codes=("rtm.operator",),
        client_kind=PresenterClientKind.OPERATOR_UI,
        authenticated_at=NOW - timedelta(hours=1),
    )


def _reviewer_actor() -> PresenterActorContext:
    return PresenterActorContext(
        operator_id=REVIEWER_OPERATOR_ID,
        operator_session_id=REVIEWER_SESSION_ID,
        permissions=(PRESENTER_RECEIPT_VERIFY_PERMISSION,),
        role_codes=("rtm.reviewer",),
        client_kind=PresenterClientKind.OPERATOR_UI,
        authenticated_at=NOW - timedelta(hours=1),
    )


def _extension_actor(*, attested: bool = True) -> PresenterActorContext:
    return PresenterActorContext(
        operator_id=OPERATOR_ID,
        operator_session_id=SESSION_ID,
        permissions=(PRESENTER_HANDOFF_EXCHANGE_PERMISSION,),
        role_codes=("rtm.operator",),
        client_kind=PresenterClientKind.TRUSTED_EXTENSION,
        authenticated_at=NOW - timedelta(hours=1),
        extension_client_id=RTM_PRESENTER_EXTENSION_CLIENT_ID,
        managed_extension_attested=attested,
        extension_attestation_id=("a" * 64 if attested else None),
    )


class FakePortalRepository:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.followup_signals: list[dict[str, Any]] = []
        self.external_document_inserts: list[dict[str, Any]] = []
        self.profile: dict[str, Any] = {
            "id": PROFILE_ID,
            "profile_code": "synthetic.portal",
            "version_number": 1,
            "status": "active",
            "authority_code": "synthetic.authority",
            "display_name": "Portal sintético verificado",
            "portal_origin": ORIGIN,
            "profile_sha256": PROFILE_SHA256,
            "created_by_operator_id": _id(90),
            "verified_by_operator_id": _id(91),
            "verified_at": NOW - timedelta(days=1),
            "requirements": {
                "synthetic_only": True,
                "representation_modes": ["self"],
                "fields": [
                    {
                        "step_order": 1,
                        "field_code": "main_document",
                        "required": True,
                        "purposes": ["main_filing"],
                        "media_types": ["application/pdf"],
                        "max_files": 1,
                        "max_bytes": 1024,
                        "portal_adapter": {
                            "adapter_id": "synthetic.portal.main",
                            "adapter_version": 1,
                            "adapter_sha256": ADAPTER_SHA256,
                            "input_selector": "input[name='main_document']",
                            "input_fingerprint_sha256": FIELD_FINGERPRINT,
                        },
                    }
                ],
            },
        }
        self.documents: dict[str, dict[str, Any]] = {
            MAIN_DOCUMENT_ID: {
                "id": MAIN_DOCUMENT_ID,
                "case_id": CASE_ID,
                "logical_document_id": LOGICAL_MAIN_ID,
                "version_number": 1,
                "supersedes_version_id": None,
                "sha256": MAIN_SHA256,
                "purpose": "main_filing",
                "state": "active",
                "scan_status": "clean",
                "original_filename": "recurso.pdf",
                "detected_mime": "application/pdf",
                "size_bytes": 128,
                "source_kind": "generated",
                "metadata": {"synthetic_only": True},
            },
            RECEIPT_DOCUMENT_ID: {
                "id": RECEIPT_DOCUMENT_ID,
                "case_id": CASE_ID,
                "logical_document_id": LOGICAL_RECEIPT_ID,
                "version_number": 1,
                "supersedes_version_id": None,
                "sha256": RECEIPT_SHA256,
                "purpose": "submission_receipt",
                "state": "active",
                "scan_status": "clean",
                "original_filename": "justificante.json",
                "detected_mime": "application/json",
                "size_bytes": 256,
                "source_kind": "receipt",
                "metadata": {"synthetic_only": True},
            },
        }

    def presenter_schema_ready(self, conn: Any) -> bool:
        del conn
        return True

    def has_active_synthetic_case_access(
        self, conn: Any, *, case_id: str, operator_id: str
    ) -> bool:
        del conn
        return case_id == CASE_ID and operator_id in {
            OPERATOR_ID,
            REVIEWER_OPERATOR_ID,
        }

    def lock_portal_session(
        self, conn: Any, *, case_id: str, portal_session_id: str
    ) -> None:
        del conn, case_id, portal_session_id

    def list_portal_session_events(
        self, conn: Any, *, case_id: str, portal_session_id: str
    ) -> list[Mapping[str, Any]]:
        del conn
        return [
            event
            for event in self.events
            if event["case_id"] == case_id
            and event["payload"].get("portal_session_id") == portal_session_id
        ]

    def append_audit(self, conn: Any, **kwargs: Any) -> None:
        del conn
        self.events.append(dict(kwargs))

    def load_destination_profile(
        self, conn: Any, *, profile_id: str
    ) -> Mapping[str, Any] | None:
        del conn
        return dict(self.profile) if profile_id == PROFILE_ID else None

    def load_document_version(
        self,
        conn: Any,
        *,
        case_id: str,
        document_version_id: str,
        for_update: bool = False,
    ) -> Mapping[str, Any] | None:
        del conn, for_update
        row = self.documents.get(document_version_id)
        return dict(row) if row and case_id == CASE_ID else None

    def insert_external_document_version(
        self,
        conn: Any,
        *,
        case_id: str,
        created_by_operator_id: str,
        upload: Any,
        storage_bucket: str,
        storage_key: str,
        supersedes_document_version_id: str | None,
    ) -> Mapping[str, Any]:
        del conn
        row = {
            "id": CAPTURED_RECEIPT_DOCUMENT_ID,
            "case_id": case_id,
            "logical_document_id": LOGICAL_CAPTURED_RECEIPT_ID,
            "version_number": 1,
            "supersedes_version_id": supersedes_document_version_id,
            "sha256": upload.sha256,
            "purpose": upload.purpose,
            "state": "review",
            "scan_status": "pending",
            "original_filename": upload.original_filename,
            "detected_mime": upload.media_type,
            "size_bytes": upload.size_bytes,
            "source_kind": "external_revision",
            "metadata": {"synthetic_only": True},
        }
        self.documents[CAPTURED_RECEIPT_DOCUMENT_ID] = row
        self.external_document_inserts.append(
            {
                "case_id": case_id,
                "created_by_operator_id": created_by_operator_id,
                "upload": upload,
                "storage_bucket": storage_bucket,
                "storage_key": storage_key,
                "supersedes_document_version_id": (
                    supersedes_document_version_id
                ),
            }
        )
        return dict(row)

    def emit_deadline_tracking_event(
        self,
        conn: Any,
        *,
        case_id: str,
        portal_session_id: str,
        payload: Mapping[str, Any],
    ) -> bool:
        del conn
        if any(
            item["portal_session_id"] == portal_session_id
            for item in self.followup_signals
        ):
            return False
        self.followup_signals.append({"case_id": case_id, **dict(payload)})
        return True


class PresenterPortalSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakePortalRepository()
        self.now = [NOW]
        self.rollback_cleanups: list[tuple[str, str]] = []
        self.storage_writes: list[dict[str, Any]] = []
        self.service = PresenterPortalSessionService(
            repository=self.repository,
            runtime=_runtime(),
            clock=lambda: self.now[0],
        )
        self.conn = object()

    def _open(self) -> dict[str, Any]:
        return self.service.open_session(
            self.conn,
            actor=_ui_actor(),
            case_id=CASE_ID,
            destination_profile_id=PROFILE_ID,
            portal_origin=ORIGIN,
            representation_mode="self",
            idempotency_key="portal-session-command-0001",
        )

    def _intent(self, session_id: str) -> dict[str, Any]:
        return self.service.prepare_attachment_intent(
            self.conn,
            actor=_ui_actor(),
            case_id=CASE_ID,
            portal_session_id=session_id,
            field_code="main_document",
            portal_field_fingerprint_sha256=FIELD_FINGERPRINT,
            document_version_id=MAIN_DOCUMENT_ID,
            portal_filename=None,
            idempotency_key="portal-attachment-command-0001",
        )

    def _record(self, session_id: str, intent_id: str) -> dict[str, Any]:
        return self.service.record_synthetic_attachment(
            self.conn,
            actor=_extension_actor(),
            case_id=CASE_ID,
            portal_session_id=session_id,
            attachment_intent_id=intent_id,
            request_origin=ORIGIN,
            portal_field_fingerprint_sha256=FIELD_FINGERPRINT,
            observed_document_sha256=MAIN_SHA256,
        )

    def _capture(
        self,
        session_id: str,
        attachment: Mapping[str, Any],
        *,
        idempotency_key: str = "portal-receipt-capture-command-0001",
    ) -> dict[str, Any]:
        _, attachment_manifest_sha256 = _attachment_manifest([attachment])

        def storage_writer(upload: Any, register_cleanup: Any) -> tuple[str, str]:
            coordinates = (
                "synthetic-presenter-receipts",
                f"cases/{CASE_ID}/receipts/{upload.sha256}.pdf",
            )
            register_cleanup(*coordinates)
            self.storage_writes.append(
                {"upload": upload, "coordinates": coordinates}
            )
            return coordinates

        return self.service.capture_receipt_pending(
            self.conn,
            actor=_extension_actor(),
            case_id=CASE_ID,
            portal_session_id=session_id,
            request_origin=ORIGIN,
            capture_source="portal_download",
            attachment_manifest_sha256=attachment_manifest_sha256,
            content=RECEIPT_PDF,
            original_filename="justificante-sintetico.pdf",
            declared_mime="application/pdf",
            synthetic_confirmed=True,
            idempotency_key=idempotency_key,
            storage_writer=storage_writer,
            register_rollback_cleanup=(
                lambda bucket, key: self.rollback_cleanups.append((bucket, key))
            ),
        )

    def test_individual_intent_is_profile_bound_and_never_sets_sent_at(self):
        session = self._open()
        self.assertIsNone(session["sent_at"])
        self.assertTrue(session["container_documents_remain_individual"])
        self.assertFalse(session["operator_download_available"])
        self.assertFalse(session["archive_created"])

        intent = self._intent(session["portal_session_id"])
        self.assertEqual(intent["document_count"], 1)
        self.assertEqual(intent["document_version_id"], MAIN_DOCUMENT_ID)
        self.assertEqual(intent["document_sha256"], MAIN_SHA256)
        self.assertEqual(intent["portal_adapter_sha256"], ADAPTER_SHA256)
        self.assertIsNone(intent["sent_at"])
        self.assertEqual(self.repository.followup_signals, [])

        with self.assertRaises(PresenterConflict) as mismatch:
            self.service.prepare_attachment_intent(
                self.conn,
                actor=_ui_actor(),
                case_id=CASE_ID,
                portal_session_id=session["portal_session_id"],
                field_code="main_document",
                portal_field_fingerprint_sha256="f" * 64,
                document_version_id=MAIN_DOCUMENT_ID,
                portal_filename=None,
                idempotency_key="portal-attachment-command-0002",
            )
        self.assertEqual(
            mismatch.exception.code,
            "presenter.portal_field_fingerprint_mismatch",
        )

    def test_header_equivalent_without_managed_attestation_stays_closed(self):
        session = self._open()
        intent = self._intent(session["portal_session_id"])
        with self.assertRaises(PresenterPolicyError):
            self.service.record_synthetic_attachment(
                self.conn,
                actor=_extension_actor(attested=False),
                case_id=CASE_ID,
                portal_session_id=session["portal_session_id"],
                attachment_intent_id=intent["attachment_intent_id"],
                request_origin=ORIGIN,
                portal_field_fingerprint_sha256=FIELD_FINGERPRINT,
                observed_document_sha256=MAIN_SHA256,
            )
        self.assertFalse(
            any(
                event["event_type"].endswith("synthetic_attachment_recorded")
                for event in self.repository.events
            )
        )

    def test_extension_reports_the_observed_portal_origin_in_the_strict_body(self):
        body = RecordSyntheticPortalAttachmentBody.model_validate(
            {
                "observed_portal_origin": ORIGIN,
                "portal_field_fingerprint_sha256": FIELD_FINGERPRINT,
                "observed_document_sha256": MAIN_SHA256,
            }
        )
        self.assertEqual(body.observed_portal_origin, ORIGIN)
        with self.assertRaises(ValidationError):
            RecordSyntheticPortalAttachmentBody.model_validate(
                {
                    "portal_field_fingerprint_sha256": FIELD_FINGERPRINT,
                    "observed_document_sha256": MAIN_SHA256,
                }
            )

    def test_portal_receipt_capture_stays_pending_without_followup(self):
        session = self._open()
        intent = self._intent(session["portal_session_id"])
        attachment = self._record(
            session["portal_session_id"], intent["attachment_intent_id"]
        )

        capture = self._capture(session["portal_session_id"], attachment)

        self.assertEqual(capture["state"], "receipt_pending")
        self.assertEqual(capture["capture_source"], "portal_download")
        self.assertEqual(
            capture["captured_document_version_id"],
            CAPTURED_RECEIPT_DOCUMENT_ID,
        )
        self.assertEqual(capture["captured_document_sha256"], RECEIPT_SHA256)
        self.assertEqual(capture["captured_document_state"], "review")
        self.assertEqual(capture["captured_document_scan_status"], "pending")
        self.assertTrue(capture["receipt_bytes_captured"])
        self.assertTrue(capture["capture_requires_explicit_human_action"])
        self.assertFalse(capture["native_download_observed"])
        self.assertFalse(capture["download_is_submission"])
        self.assertIsNone(capture["sent_at"])
        self.assertFalse(capture["receipt_verified"])
        self.assertFalse(capture["followup_activation_ready"])
        self.assertFalse(capture["followups_created"])
        self.assertFalse(capture["legal_deadline_calculated"])
        self.assertFalse(capture["case_status_changed"])
        self.assertFalse(capture["storage_references_exposed"])
        self.assertEqual(self.repository.followup_signals, [])
        self.assertEqual(len(self.repository.external_document_inserts), 1)
        self.assertEqual(len(self.storage_writes), 1)
        self.assertEqual(len(self.rollback_cleanups), 1)

    def test_receipt_capture_is_idempotent_and_rejects_second_candidate(self):
        session = self._open()
        intent = self._intent(session["portal_session_id"])
        attachment = self._record(
            session["portal_session_id"], intent["attachment_intent_id"]
        )

        first = self._capture(session["portal_session_id"], attachment)
        replay = self._capture(session["portal_session_id"], attachment)
        self.assertEqual(replay["receipt_capture_id"], first["receipt_capture_id"])
        self.assertEqual(len(self.repository.external_document_inserts), 1)
        self.assertEqual(len(self.storage_writes), 1)

        with self.assertRaises(PresenterConflict) as duplicate:
            self._capture(
                session["portal_session_id"],
                attachment,
                idempotency_key="portal-receipt-capture-command-0002",
            )
        self.assertEqual(
            duplicate.exception.code,
            "presenter.receipt_pending_already_exists",
        )
        self.assertEqual(len(self.repository.external_document_inserts), 1)
        self.assertEqual(len(self.storage_writes), 1)

    def test_receipt_capture_router_is_raw_and_gates_before_streaming(self):
        source = inspect.getsource(capture_presenter_receipt_pending_route)
        self.assertNotIn("UploadFile", source)
        self.assertNotIn("Form(", source)
        self.assertNotIn("file.read", source)
        self.assertNotIn("request.body", source)
        stream_at = source.index("async for chunk in request.stream()")
        for gate in (
            "authorize_handoff_exchange_client(actor)",
            "presenter.receipt_email_capture_not_ready",
            "presenter.synthetic_confirmation_required",
            "presenter.receipt_content_type_invalid",
            "presenter.receipt_length_required",
            "presenter.receipt_transfer_encoding_forbidden",
            "presenter.receipt_content_encoding_forbidden",
            "presenter.portal_idempotency_key_required",
        ):
            self.assertLess(source.index(gate), stream_at, gate)
        for header in (
            "X-RTM-Receipt-Capture-Source",
            "X-RTM-Observed-Portal-Origin",
            "X-RTM-Attachment-Manifest-SHA256",
            "X-RTM-Receipt-Filename",
            "X-RTM-Receipt-Media-Type",
            "X-RTM-Synthetic-Confirmed",
            "Content-Length",
        ):
            self.assertIn(header, source)
        self.assertIn('body_buffer[:] = b"\\x00"', source)

    def test_receipt_capture_rejects_unattested_and_email_without_storage(self):
        session = self._open()
        intent = self._intent(session["portal_session_id"])
        attachment = self._record(
            session["portal_session_id"], intent["attachment_intent_id"]
        )
        _, attachment_manifest_sha256 = _attachment_manifest([attachment])

        def unexpected_storage(*args: Any, **kwargs: Any) -> tuple[str, str]:
            del args, kwargs
            self.fail("No debe iniciarse custodia para una captura rechazada")

        def unexpected_cleanup(bucket: str, key: str) -> None:
            del bucket, key
            self.fail("No debe registrarse cleanup para una captura rechazada")

        with self.assertRaises(PresenterPolicyError):
            self.service.capture_receipt_pending(
                self.conn,
                actor=_extension_actor(attested=False),
                case_id=CASE_ID,
                portal_session_id=session["portal_session_id"],
                request_origin=ORIGIN,
                capture_source="portal_download",
                attachment_manifest_sha256=attachment_manifest_sha256,
                content=RECEIPT_PDF,
                original_filename="justificante-sintetico.pdf",
                declared_mime="application/pdf",
                synthetic_confirmed=True,
                idempotency_key="portal-receipt-capture-unattested",
                storage_writer=unexpected_storage,
                register_rollback_cleanup=unexpected_cleanup,
            )

        with self.assertRaises(PresenterConflict) as email_capture:
            self.service.capture_receipt_pending(
                self.conn,
                actor=_extension_actor(),
                case_id=CASE_ID,
                portal_session_id=session["portal_session_id"],
                request_origin=ORIGIN,
                capture_source="email_attachment",
                attachment_manifest_sha256=attachment_manifest_sha256,
                content=RECEIPT_PDF,
                original_filename="justificante-sintetico.pdf",
                declared_mime="application/pdf",
                synthetic_confirmed=True,
                idempotency_key="portal-receipt-capture-email",
                storage_writer=unexpected_storage,
                register_rollback_cleanup=unexpected_cleanup,
            )
        self.assertEqual(
            email_capture.exception.code,
            "presenter.receipt_email_capture_not_ready",
        )
        self.assertEqual(self.repository.external_document_inserts, [])
        self.assertFalse(
            any(
                event["event_type"]
                == "presenter.portal_session.receipt_captured"
                for event in self.repository.events
            )
        )

    def test_verification_rejects_a_different_receipt_version_after_capture(self):
        session = self._open()
        intent = self._intent(session["portal_session_id"])
        attachment = self._record(
            session["portal_session_id"], intent["attachment_intent_id"]
        )
        _, attachment_manifest_sha256 = _attachment_manifest([attachment])
        capture = self._capture(session["portal_session_id"], attachment)
        material = {
            "format": "rtm.presenter.synthetic_submission_receipt.v1",
            "case_id": CASE_ID,
            "portal_session_id": session["portal_session_id"],
            "destination_profile_id": PROFILE_ID,
            "portal_origin": ORIGIN,
            "registration_number": "SYN-REG-2026-0001",
            "submitted_at": _stamp(NOW + timedelta(minutes=10)),
            "verification_reference": "SYN-CSV-0001",
            "receipt_document_version_id": RECEIPT_DOCUMENT_ID,
            "receipt_sha256": RECEIPT_SHA256,
            "receipt_capture_id": capture["receipt_capture_id"],
            "captured_document_version_id": capture[
                "captured_document_version_id"
            ],
            "captured_document_sha256": capture["captured_document_sha256"],
            "attachment_manifest_sha256": attachment_manifest_sha256,
            "authority_hash_algorithm": "sha-256",
            "authority_hash_scope": "attachment_manifest",
            "authority_hash_value": attachment_manifest_sha256,
            "synthetic_only": True,
            "legal_submission_executed": False,
        }
        self.repository.documents[RECEIPT_DOCUMENT_ID]["metadata"] = {
            "synthetic_only": True,
            "synthetic_submission_receipt": {
                **material,
                "material_sha256": canonical_sha256(material),
            },
        }

        with self.assertRaises(PresenterConflict) as mismatch:
            self.service.verify_receipt_and_enable_tracking(
                self.conn,
                actor=_reviewer_actor(),
                case_id=CASE_ID,
                portal_session_id=session["portal_session_id"],
                receipt_document_version_id=RECEIPT_DOCUMENT_ID,
                expected_receipt_sha256=RECEIPT_SHA256,
                idempotency_key="portal-receipt-command-version-mismatch",
            )
        self.assertEqual(
            mismatch.exception.code,
            "presenter.receipt_capture_bytes_mismatch",
        )
        self.assertEqual(self.repository.followup_signals, [])

    def test_verified_receipt_metadata_anchors_followup_after_session_ttl(self):
        session = self._open()
        intent = self._intent(session["portal_session_id"])
        attachment = self._record(
            session["portal_session_id"], intent["attachment_intent_id"]
        )
        self.assertIsNone(attachment["sent_at"])
        self.assertEqual(self.repository.followup_signals, [])

        _, attachment_manifest_sha256 = _attachment_manifest([attachment])
        capture = self._capture(session["portal_session_id"], attachment)
        captured_document_id = capture["captured_document_version_id"]
        captured_document = self.repository.documents[captured_document_id]
        captured_document.update(
            {
                "state": "active",
                "scan_status": "clean",
                "source_kind": "receipt",
            }
        )
        submitted_at = NOW + timedelta(minutes=10)
        material = {
            "format": "rtm.presenter.synthetic_submission_receipt.v1",
            "case_id": CASE_ID,
            "portal_session_id": session["portal_session_id"],
            "destination_profile_id": PROFILE_ID,
            "portal_origin": ORIGIN,
            "registration_number": "SYN-REG-2026-0001",
            "submitted_at": _stamp(submitted_at),
            "verification_reference": "SYN-CSV-0001",
            "receipt_document_version_id": captured_document_id,
            "receipt_sha256": RECEIPT_SHA256,
            "receipt_capture_id": capture["receipt_capture_id"],
            "captured_document_version_id": capture[
                "captured_document_version_id"
            ],
            "captured_document_sha256": capture["captured_document_sha256"],
            "attachment_manifest_sha256": attachment_manifest_sha256,
            "authority_hash_algorithm": "sha-256",
            "authority_hash_scope": "attachment_manifest",
            "authority_hash_value": attachment_manifest_sha256,
            "synthetic_only": True,
            "legal_submission_executed": False,
        }
        wrong_material = {**material, "authority_hash_value": "f" * 64}
        captured_document["metadata"] = {
            "synthetic_only": True,
            "synthetic_submission_receipt": {
                **wrong_material,
                "material_sha256": canonical_sha256(wrong_material),
            },
        }
        self.now[0] = NOW + timedelta(hours=3)
        with self.assertRaises(PresenterConflict) as mismatch:
            self.service.verify_receipt_and_enable_tracking(
                self.conn,
                actor=_reviewer_actor(),
                case_id=CASE_ID,
                portal_session_id=session["portal_session_id"],
                receipt_document_version_id=captured_document_id,
                expected_receipt_sha256=RECEIPT_SHA256,
                idempotency_key="portal-receipt-command-bad-hash",
            )
        self.assertEqual(
            mismatch.exception.code,
            "presenter.receipt_authority_hash_mismatch",
        )
        self.assertEqual(self.repository.followup_signals, [])

        captured_document["metadata"] = {
            "synthetic_only": True,
            "synthetic_submission_receipt": {
                **material,
                "material_sha256": canonical_sha256(material),
            },
        }
        receipt = self.service.verify_receipt_and_enable_tracking(
            self.conn,
            actor=_reviewer_actor(),
            case_id=CASE_ID,
            portal_session_id=session["portal_session_id"],
            receipt_document_version_id=captured_document_id,
            expected_receipt_sha256=RECEIPT_SHA256,
            idempotency_key="portal-receipt-command-0001",
        )
        self.assertEqual(receipt["sent_at"], _stamp(submitted_at))
        self.assertTrue(receipt["receipt_verified"])
        self.assertEqual(
            receipt["deadline_tracking"]["status"],
            "followup_activation_ready",
        )
        self.assertIsNone(receipt["legal_due_at"])
        self.assertFalse(receipt["legal_deadline_calculated"])
        self.assertFalse(receipt["case_status_changed"])
        self.assertFalse(receipt["followups_created"])
        self.assertEqual(
            receipt["authority_hash_value"], attachment_manifest_sha256
        )
        self.assertEqual(len(self.repository.followup_signals), 1)
        self.assertEqual(
            self.repository.followup_signals[0]["source_event_type"],
            RTM_PRESENTER_DEADLINE_SOURCE_EVENT,
        )

    def test_receipt_body_cannot_supply_registration_or_timestamp(self):
        with self.assertRaises(ValidationError):
            VerifyPortalReceiptBody.model_validate(
                {
                    "receipt_document_version_id": RECEIPT_DOCUMENT_ID,
                    "expected_receipt_sha256": RECEIPT_SHA256,
                    "registration_number": "FORGED",
                    "submitted_at": _stamp(NOW),
                }
            )

        session = self._open()
        intent = self._intent(session["portal_session_id"])
        self._record(session["portal_session_id"], intent["attachment_intent_id"])
        with self.assertRaises(PresenterPolicyError):
            self.service.verify_receipt_and_enable_tracking(
                self.conn,
                actor=_ui_actor(receipt_verify=False),
                case_id=CASE_ID,
                portal_session_id=session["portal_session_id"],
                receipt_document_version_id=RECEIPT_DOCUMENT_ID,
                expected_receipt_sha256=RECEIPT_SHA256,
                idempotency_key="portal-receipt-command-0001",
            )
        self.assertEqual(self.repository.followup_signals, [])

    def test_presenter_cannot_verify_own_receipt_even_with_permission(self):
        session = self._open()

        with self.assertRaises(PresenterForbidden):
            self.service.verify_receipt_and_enable_tracking(
                self.conn,
                actor=_ui_actor(receipt_verify=True),
                case_id=CASE_ID,
                portal_session_id=session["portal_session_id"],
                receipt_document_version_id=RECEIPT_DOCUMENT_ID,
                expected_receipt_sha256=RECEIPT_SHA256,
                idempotency_key="portal-receipt-self-verification",
            )

        self.assertEqual(self.repository.followup_signals, [])


if __name__ == "__main__":
    unittest.main()
