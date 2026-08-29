from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from rtm_presenter_contracts import PresenterClientKind
from rtm_presenter_delivery import (
    RTM_PRESENTER_DELIVERY_VERSION,
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
            "destination_requirements": {},
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


class PresenterDeliveryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeDeliveryRepository()
        self.service = PresenterDeliveryService(
            repository=self.repository,
            runtime=_runtime(),
            clock=lambda: NOW,
        )
        self.conn = object()

    def _prepare(self, *, channel: str = "portal") -> dict[str, Any]:
        return self.service.prepare(
            self.conn,
            actor=_actor(),
            case_id=CASE_ID,
            package_id=PACKAGE_ID,
            channel=channel,
            idempotency_key=IDEMPOTENCY_KEY,
        )

    def test_portal_delivery_is_ordered_audited_and_has_no_external_effect(self):
        delivery = self._prepare()

        self.assertEqual(
            delivery["delivery_contract_version"],
            RTM_PRESENTER_DELIVERY_VERSION,
        )
        self.assertEqual(delivery["state"], "prepared")
        self.assertEqual(delivery["channel"], "portal")
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
            delivery["next_action"], "managed_bridge_activation_required"
        )
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
        self.assertEqual(len(self.repository.locks), 2)

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

        self.repository.package["destination_requirements"] = {
            "delivery": {
                "email": {
                    "verified": True,
                    "recipient": "reclamaciones@synthetic.example",
                    "template_code": "consumer_claim",
                    "template_version": 2,
                }
            }
        }
        delivery = self._prepare(channel="email")
        self.assertEqual(
            delivery["destination"]["recipient"],
            "reclamaciones@synthetic.example",
        )
        self.assertEqual(delivery["mode"], "server_side_email_from_custody")
        self.assertEqual(
            delivery["next_action"], "compose_review_and_step_up_required"
        )

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
