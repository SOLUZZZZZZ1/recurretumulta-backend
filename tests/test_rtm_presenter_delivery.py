from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from rtm_presenter_contracts import PresenterClientKind
from rtm_presenter_delivery import (
    RTM_PRESENTER_DELIVERY_VERSION,
    RTM_PRESENTER_SIGNATURE_QUEUE_VERSION,
    PresenterDeliveryService,
)
from rtm_presenter_policy import (
    PRESENTER_DELIVERY_PREPARE_PERMISSION,
    PresenterActorContext,
    PresenterPolicyError,
    PresenterRuntimeConfiguration,
)
from rtm_presenter_service import PresenterConflict, PresenterForbidden


NOW = datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc)


def _id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


CASE_ID = _id(1)
PACKAGE_ID = _id(2)
PROFILE_ID = _id(3)
OPERATOR_ID = _id(4)
SESSION_ID = _id(5)
OTHER_OPERATOR_ID = _id(6)
MANIFEST_SHA256 = hashlib.sha256(b"manifest").hexdigest()
PROFILE_SHA256 = hashlib.sha256(b"profile").hexdigest()
DOCUMENT_SHA256 = hashlib.sha256(b"document").hexdigest()
PORTAL_ORIGIN = "https://sede.synthetic.example"
IDEMPOTENCY_KEY = "rtm-delivery-command-0001"


def _email_requirements() -> dict[str, Any]:
    return {
        "delivery": {
            "email": {
                "verified": True,
                "recipient": "reclamaciones@synthetic.example",
                "legal_entity_name": "Empresa sintética, S.A.",
                "entity_role": "comercializadora",
                "channel_status": "accepted",
                "official_source_label": "Atención sintética oficial",
                "official_source_url": (
                    "https://synthetic.example/reclamaciones"
                ),
                "recommended_evidence_channel": "correo_certificado",
                "sensitive_attachment_policy": "cifrado_o_enlace_seguro",
                "template_code": "consumer_claim",
                "template_version": 2,
            }
        }
    }


def _correspondence() -> dict[str, Any]:
    return {
        "subject": "Reclamación sintética – Expediente RTM 0001",
        "body": "Texto sintético revisado por el operador.",
        "confirmations": {
            "destination_reviewed": True,
            "interested_confirmed": True,
            "representation_confirmed": True,
            "text_confirmed": True,
            "attachments_confirmed": True,
            "data_minimization_confirmed": True,
        },
    }


def _portal_requirements() -> dict[str, Any]:
    return {
        "portal_preparation": {
            "enabled": True,
            "form_code": "reg_general_v1",
            "fields": [
                {
                    "field_code": "subject",
                    "label": "Asunto",
                    "required": True,
                    "multiline": False,
                    "max_length": 80,
                },
                {
                    "field_code": "facts",
                    "label": "Expone",
                    "required": True,
                    "multiline": True,
                    "max_length": 4000,
                },
                {
                    "field_code": "request",
                    "label": "Solicita",
                    "required": True,
                    "multiline": True,
                    "max_length": 4000,
                },
            ],
        }
    }


def _portal_preparation() -> dict[str, Any]:
    return {
        "form_code": "reg_general_v1",
        "values": {
            "subject": "Recurso sintético",
            "facts": "Se exponen hechos completamente sintéticos.",
            "request": "Se solicita una respuesta sintética.",
        },
        "confirmations": {
            "destination_reviewed": True,
            "interested_confirmed": True,
            "representation_confirmed": True,
            "text_confirmed": True,
            "attachments_confirmed": True,
        },
    }


def _actor(
    *, operator_id: str = OPERATOR_ID, include_permission: bool = True
) -> PresenterActorContext:
    return PresenterActorContext(
        operator_id=operator_id,
        operator_session_id=SESSION_ID,
        permissions=(PRESENTER_DELIVERY_PREPARE_PERMISSION,)
        if include_permission
        else (),
        role_codes=("rtm.operator",),
        client_kind=PresenterClientKind.OPERATOR_UI,
        authenticated_at=NOW - timedelta(hours=1),
    )


def _runtime(*, bridge: bool = False) -> PresenterRuntimeConfiguration:
    return PresenterRuntimeConfiguration(
        enabled=True,
        environment="staging",
        synthetic_only=True,
        real_data_allowed=False,
        external_effects_allowed=False,
        direct_storage_allowed=False,
        managed_extension_attestation_enabled=bridge,
    )


class FakeDeliveryRepository:
    def __init__(self) -> None:
        self.schema_ready = True
        self.case_access = True
        self.events: list[dict[str, Any]] = []
        self.locks: list[tuple[str, str]] = []
        self.byte_loads = 0
        self.queue_calls: list[tuple[str, int]] = []
        self.package: dict[str, Any] = {
            "id": PACKAGE_ID,
            "case_id": CASE_ID,
            "status": "frozen",
            "expires_at": NOW + timedelta(hours=2),
            "manifest_sha256": MANIFEST_SHA256,
            "destination_profile_id": PROFILE_ID,
            "profile_code": "synthetic_town_hall",
            "profile_version": 4,
            "profile_sha256": PROFILE_SHA256,
            "profile_status": "active",
            "destination_display_name": "Ayuntamiento sintético",
            "representation_mode": "self",
            "destination_requirements": _portal_requirements(),
            "portal_origin": PORTAL_ORIGIN,
            "items": [
                {
                    "id": _id(10),
                    "item_order": 1,
                    "field_code": "main_filing",
                    "portal_filename": "recurso.pdf",
                    "document_version_id": _id(20),
                    "document_sha256": DOCUMENT_SHA256,
                    "current_document_sha256": DOCUMENT_SHA256,
                    "state": "active",
                    "scan_status": "clean",
                    "detected_mime": "application/pdf",
                    "size_bytes": 128,
                },
                {
                    "id": _id(11),
                    "item_order": 2,
                    "field_code": "representation_authorization",
                    "portal_filename": "autorizacion.pdf",
                    "document_version_id": _id(21),
                    "document_sha256": "a" * 64,
                    "current_document_sha256": "a" * 64,
                    "state": "active",
                    "scan_status": "clean",
                    "detected_mime": "application/pdf",
                    "size_bytes": 96,
                },
            ],
        }

    def presenter_schema_ready(self, conn: Any) -> bool:
        del conn
        return self.schema_ready

    def has_active_synthetic_case_access(
        self, conn: Any, *, case_id: str, operator_id: str
    ) -> bool:
        del conn
        return self.case_access and case_id == CASE_ID and operator_id in {
            OPERATOR_ID,
            OTHER_OPERATOR_ID,
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
        if case_id != CASE_ID or package_id != PACKAGE_ID:
            return None
        return dict(self.package)

    def lock_delivery_command(
        self, conn: Any, *, package_id: str, delivery_id: str
    ) -> None:
        del conn
        self.locks.append((package_id, delivery_id))

    def list_delivery_events(
        self,
        conn: Any,
        *,
        case_id: str,
        package_id: str,
        delivery_id: str,
    ) -> list[Mapping[str, Any]]:
        del conn
        return [
            event
            for event in self.events
            if event["case_id"] == case_id
            and event["package_id"] == package_id
            and event["payload"]["delivery_id"] == delivery_id
        ]

    def append_audit(self, conn: Any, **kwargs: Any) -> None:
        del conn
        self.events.append(dict(kwargs))

    def list_signature_queue_events(
        self, conn: Any, *, operator_id: str, limit: int
    ) -> list[Mapping[str, Any]]:
        del conn
        self.queue_calls.append((operator_id, limit))
        return [
            event
            for event in self.events
            if event["payload"].get("channel") == "portal"
            and event["payload"].get("state") == "awaiting_signature"
        ][:limit]


class PresenterDeliveryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeDeliveryRepository()
        self.service = PresenterDeliveryService(
            repository=self.repository,
            runtime=_runtime(),
            clock=lambda: NOW,
        )
        self.conn = object()

    def _prepare(
        self,
        *,
        channel: str = "portal",
        recipient_email: str | None = None,
        recipient_confirmed: bool = False,
        correspondence: Mapping[str, Any] | None = None,
        portal_preparation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if channel == "email" and correspondence is None:
            correspondence = _correspondence()
        if channel == "portal" and portal_preparation is None:
            portal_preparation = _portal_preparation()
        return self.service.prepare(
            self.conn,
            actor=_actor(),
            case_id=CASE_ID,
            package_id=PACKAGE_ID,
            channel=channel,
            idempotency_key=IDEMPOTENCY_KEY,
            recipient_email=recipient_email,
            recipient_confirmed=recipient_confirmed,
            correspondence=correspondence,
            portal_preparation=portal_preparation,
        )

    def test_portal_delivery_is_ordered_audited_and_has_no_external_effect(self):
        delivery = self._prepare()

        self.assertEqual(
            delivery["delivery_contract_version"],
            RTM_PRESENTER_DELIVERY_VERSION,
        )
        self.assertEqual(delivery["state"], "awaiting_signature")
        self.assertEqual(delivery["channel"], "portal")
        self.assertEqual(delivery["representation_mode"], "self")
        self.assertEqual(
            delivery["destination"],
            {
                "kind": "verified_portal_origin",
                "portal_origin": PORTAL_ORIGIN,
            },
        )
        self.assertEqual(
            [item["item_order"] for item in delivery["items"]], [1, 2]
        )
        self.assertEqual(
            [item["field_code"] for item in delivery["items"]],
            ["main_filing", "representation_authorization"],
        )
        self.assertFalse(delivery["external_effects_allowed"])
        self.assertFalse(delivery["authoritative_submission"])
        self.assertFalse(delivery["local_files_created"])
        self.assertFalse(delivery["operator_download_available"])
        self.assertFalse(delivery["automatic_retry_allowed"])
        self.assertTrue(delivery["human_final_submit_required"])
        self.assertTrue(delivery["receipt_required"])
        self.assertEqual(
            delivery["next_action"],
            "managed_signing_bridge_activation_required",
        )
        self.assertTrue(delivery["signature_queue_ready"])
        self.assertEqual(
            delivery["portal_preparation"]["values"]["subject"],
            "Recurso sintético",
        )
        self.assertFalse(
            delivery["signing_controls"]["certificate_stored_by_rtm"]
        )
        self.assertFalse(
            delivery["signing_controls"]["browser_session_shared_with_operator"]
        )
        self.assertFalse(delivery["signing_controls"]["signature_automated"])
        self.assertEqual(len(self.repository.events), 1)
        self.assertEqual(
            self.repository.events[0]["event_type"],
            "presenter.delivery.prepared",
        )
        self.assertEqual(self.repository.byte_loads, 0)

    def test_same_idempotency_key_replays_exact_snapshot(self):
        first = self._prepare()
        second = self._prepare()

        self.assertEqual(first, second)
        self.assertEqual(len(self.repository.events), 1)

    def test_ledger_markers_do_not_change_idempotent_response_contract(self):
        first = self._prepare()
        self.repository.events[0]["payload"] = {
            **first,
            "service_version": "rtm_presenter_service_v1_3",
            "synthetic_marker": "RTM_PRESENTER_SYNTHETIC_ONLY",
            "synthetic_only": True,
        }

        second = self._prepare()

        self.assertEqual(second, first)
        self.assertNotIn("service_version", second)
        self.assertNotIn("synthetic_marker", second)
        self.assertNotIn("synthetic_only", second)
        self.assertEqual(len(self.repository.events), 1)
        self.assertEqual(len(self.repository.locks), 2)

    def test_signature_queue_lists_assigned_portal_tasks_without_signing_authority(self):
        prepared = self._prepare()

        queue = self.service.signature_queue(
            self.conn,
            actor=_actor(),
            limit=25,
        )

        self.assertEqual(
            queue["queue_contract_version"],
            RTM_PRESENTER_SIGNATURE_QUEUE_VERSION,
        )
        self.assertEqual(queue["item_count"], 1)
        self.assertEqual(queue["items"][0]["delivery_id"], prepared["delivery_id"])
        self.assertEqual(queue["items"][0]["case_id"], CASE_ID)
        self.assertEqual(queue["items"][0]["document_count"], 2)
        self.assertFalse(queue["items"][0]["authoritative_submission"])
        self.assertTrue(queue["items"][0]["local_signer_activation_required"])
        self.assertFalse(queue["items"][0]["local_activation_available"])
        self.assertFalse(queue["certificate_stored_by_rtm"])
        self.assertFalse(queue["browser_session_shared"])
        self.assertEqual(self.repository.queue_calls, [(OPERATOR_ID, 25)])

    def test_signature_queue_requires_prepare_permission_and_valid_limit(self):
        with self.assertRaises(PresenterPolicyError):
            self.service.signature_queue(
                self.conn,
                actor=_actor(include_permission=False),
            )
        with self.assertRaises(PresenterConflict) as invalid_limit:
            self.service.signature_queue(
                self.conn,
                actor=_actor(),
                limit=0,
            )
        self.assertEqual(
            invalid_limit.exception.code,
            "presenter.signature_queue_limit_invalid",
        )

    def test_same_idempotency_key_cannot_change_channel(self):
        self._prepare()

        with self.assertRaises(PresenterConflict) as denied:
            self._prepare(channel="email")

        self.assertEqual(
            denied.exception.code,
            "presenter.delivery_idempotency_key_reused",
        )
        self.assertEqual(len(self.repository.events), 1)

    def test_email_requires_destination_verified_inside_profile(self):
        with self.assertRaises(PresenterConflict) as denied:
            self._prepare(channel="email")

        self.assertEqual(
            denied.exception.code,
            "presenter.delivery_email_destination_unverified",
        )
        self.assertEqual(self.repository.events, [])

        self.repository.package["destination_requirements"] = _email_requirements()
        delivery = self._prepare(channel="email")
        self.assertEqual(
            delivery["destination"]["recipient"],
            "reclamaciones@synthetic.example",
        )
        self.assertEqual(delivery["mode"], "server_side_email_from_custody")
        self.assertTrue(delivery["destination"]["verified"])
        self.assertEqual(
            delivery["next_action"], "step_up_and_send_blocked_in_staging"
        )
        self.assertEqual(
            delivery["correspondence"]["sender"], "info@recurretumulta.eu"
        )
        self.assertEqual(
            delivery["correspondence"]["subject"],
            "Reclamación sintética – Expediente RTM 0001",
        )
        self.assertEqual(len(delivery["correspondence"]["attachments"]), 2)
        self.assertFalse(
            delivery["correspondence"]["transport_evidence"]["server_accepted"]
        )
        self.assertFalse(
            delivery["correspondence"]["transport_evidence"][
                "delivery_receipt_proven"
            ]
        )

    def test_manual_synthetic_email_is_prepared_but_never_treated_as_verified(self):
        self.repository.package["destination_requirements"] = _email_requirements()
        with self.assertRaises(PresenterConflict) as unconfirmed:
            self._prepare(
                channel="email",
                recipient_email="manual@synthetic.example",
            )
        self.assertEqual(
            unconfirmed.exception.code,
            "presenter.delivery_manual_email_confirmation_required",
        )

        delivery = self._prepare(
            channel="email",
            recipient_email="manual@synthetic.example",
            recipient_confirmed=True,
        )
        self.assertEqual(
            delivery["destination"]["kind"],
            "operator_entered_email_pending_verification",
        )
        self.assertEqual(
            delivery["destination"]["recipient"],
            "manual@synthetic.example",
        )
        self.assertFalse(delivery["destination"]["verified"])
        self.assertEqual(
            delivery["destination"]["official_profile_recipient"],
            "reclamaciones@synthetic.example",
        )
        self.assertEqual(
            delivery["next_action"], "recipient_verification_required"
        )
        self.assertFalse(delivery["external_effects_allowed"])

    def test_manual_email_rejects_real_domains_and_portal_channel(self):
        self.repository.package["destination_requirements"] = _email_requirements()
        with self.assertRaises(PresenterConflict) as real_email:
            self._prepare(
                channel="email",
                recipient_email="persona@example.es",
                recipient_confirmed=True,
            )
        self.assertEqual(
            real_email.exception.code,
            "presenter.delivery_manual_email_not_synthetic",
        )

        with self.assertRaises(PresenterConflict) as portal_email:
            self._prepare(
                channel="portal",
                recipient_email="manual@synthetic.example",
                recipient_confirmed=True,
                correspondence=_correspondence(),
            )
        self.assertEqual(
            portal_email.exception.code,
            "presenter.delivery_email_not_allowed_for_portal",
        )

    def test_correspondence_requires_every_human_confirmation(self):
        self.repository.package["destination_requirements"] = _email_requirements()
        draft = _correspondence()
        draft["confirmations"]["data_minimization_confirmed"] = False

        with self.assertRaises(PresenterConflict) as denied:
            self._prepare(channel="email", correspondence=draft)

        self.assertEqual(
            denied.exception.code,
            "presenter.correspondence_confirmation_required",
        )
        self.assertEqual(self.repository.events, [])

    def test_portal_preparation_is_profile_bound_and_requires_confirmations(self):
        missing_confirmation = _portal_preparation()
        missing_confirmation["confirmations"]["attachments_confirmed"] = False
        with self.assertRaises(PresenterConflict) as denied:
            self._prepare(portal_preparation=missing_confirmation)
        self.assertEqual(
            denied.exception.code,
            "presenter.portal_preparation_confirmation_required",
        )

        wrong_form = _portal_preparation()
        wrong_form["form_code"] = "other_form"
        with self.assertRaises(PresenterConflict) as mismatch:
            self._prepare(portal_preparation=wrong_form)
        self.assertEqual(
            mismatch.exception.code,
            "presenter.portal_preparation_form_mismatch",
        )
        self.assertEqual(self.repository.events, [])

    def test_expired_or_stale_package_is_rejected_before_audit(self):
        self.repository.package["expires_at"] = NOW
        with self.assertRaises(PresenterConflict) as expired:
            self._prepare()
        self.assertEqual(expired.exception.code, "presenter.delivery_package_unavailable")

        self.repository.package["expires_at"] = NOW + timedelta(hours=1)
        self.repository.package["items"][0]["current_document_sha256"] = "f" * 64
        with self.assertRaises(PresenterConflict) as stale:
            self._prepare()
        self.assertEqual(stale.exception.code, "presenter.delivery_package_stale")
        self.assertEqual(self.repository.events, [])

    def test_permission_and_case_scope_fail_closed(self):
        with self.assertRaises(PresenterPolicyError):
            self.service.prepare(
                self.conn,
                actor=_actor(include_permission=False),
                case_id=CASE_ID,
                package_id=PACKAGE_ID,
                channel="portal",
                idempotency_key=IDEMPOTENCY_KEY,
            )
        self.repository.case_access = False
        with self.assertRaises(PresenterForbidden):
            self._prepare()
        self.assertEqual(self.repository.events, [])

    def test_status_is_bound_to_preparing_operator(self):
        prepared = self._prepare()
        loaded = self.service.status(
            self.conn,
            actor=_actor(),
            case_id=CASE_ID,
            package_id=PACKAGE_ID,
            delivery_id=prepared["delivery_id"],
        )
        self.assertEqual(loaded, prepared)

        with self.assertRaises(PresenterForbidden):
            self.service.status(
                self.conn,
                actor=_actor(operator_id=OTHER_OPERATOR_ID),
                case_id=CASE_ID,
                package_id=PACKAGE_ID,
                delivery_id=prepared["delivery_id"],
            )


if __name__ == "__main__":
    unittest.main()
