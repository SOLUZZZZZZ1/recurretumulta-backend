from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from rtm_presenter_contracts import PresenterClientKind
from rtm_presenter_delivery import RTM_PRESENTER_DELIVERY_VERSION
from rtm_presenter_local_station import (
    RTM_PRESENTER_LOCAL_STATION_VERSION,
    RTM_PRESENTER_SIGNER_WORKSPACE_VERSION,
    PresenterLocalStationService,
)
from rtm_presenter_policy import (
    PRESENTER_SIGNING_CLAIM_PERMISSION,
    PRESENTER_SIGNING_QUEUE_PERMISSION,
    PresenterActorContext,
    PresenterPolicyError,
    PresenterRuntimeConfiguration,
)
from rtm_presenter_service import (
    PresenterConflict,
    PresenterNotFound,
    PresenterSchemaNotReady,
)


NOW = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)


def _id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


CASE_ID = _id(1)
PACKAGE_ID = _id(2)
DELIVERY_ID = _id(3)
PREPARER_ID = _id(4)
SIGNER_ID = _id(5)
SESSION_ID = _id(6)
DEVICE_ID = _id(7)
INSTANCE_ID = _id(8)
OTHER_INSTANCE_ID = _id(9)


def _runtime() -> PresenterRuntimeConfiguration:
    return PresenterRuntimeConfiguration(
        enabled=True,
        environment="staging",
        synthetic_only=True,
        real_data_allowed=False,
        external_effects_allowed=False,
        direct_storage_allowed=False,
        managed_extension_attestation_enabled=False,
    )


def _actor(*, session_id: str = SESSION_ID) -> PresenterActorContext:
    return PresenterActorContext(
        operator_id=SIGNER_ID,
        operator_session_id=session_id,
        permissions=(
            "ops.view",
            PRESENTER_SIGNING_CLAIM_PERMISSION,
            PRESENTER_SIGNING_QUEUE_PERMISSION,
        ),
        role_codes=("rtm.signer",),
        client_kind=PresenterClientKind.SIGNER_STATION,
        authenticated_at=NOW - timedelta(minutes=3),
    )


def _delivery_event() -> dict[str, Any]:
    payload = {
        "delivery_contract_version": RTM_PRESENTER_DELIVERY_VERSION,
        "delivery_id": DELIVERY_ID,
        "case_id": CASE_ID,
        "package_id": PACKAGE_ID,
        "package_manifest_sha256": hashlib.sha256(b"manifest").hexdigest(),
        "destination_profile_id": _id(10),
        "destination_profile_code": "reg_general_synthetic",
        "destination_profile_version": 1,
        "destination_profile_sha256": hashlib.sha256(b"profile").hexdigest(),
        "destination_display_name": "Registro Electronico General sintetico",
        "channel": "portal",
        "state": "awaiting_signature",
        "representation_mode": "self",
        "destination": {
            "kind": "verified_portal_origin",
            "portal_origin": "https://reg.synthetic.example",
        },
        "portal_preparation": {
            "form_code": "reg_general_v1",
            "fields": [
                {
                    "field_code": "subject",
                    "label": "Asunto",
                    "required": True,
                    "multiline": False,
                    "max_length": 80,
                    "step_order": 1,
                },
                {
                    "field_code": "facts",
                    "label": "Expone",
                    "required": True,
                    "multiline": True,
                    "max_length": 4000,
                    "step_order": 2,
                },
            ],
            "values": {
                "subject": "Recurso sintetico",
                "facts": "Hechos completamente sinteticos.",
            },
        },
        "items": [
            {
                "package_item_id": _id(11),
                "document_version_id": _id(12),
                "document_sha256": hashlib.sha256(b"resource").hexdigest(),
                "item_order": 1,
                "field_code": "main_filing",
                "portal_filename": "recurso.pdf",
                "media_type": "application/pdf",
                "size_bytes": 120,
                "state": "pending",
            }
        ],
        "prepared_at": NOW.isoformat(),
        "prepared_by_operator_id": PREPARER_ID,
        "signature_queue_ready": True,
        "authoritative_submission": False,
        "external_effects_allowed": False,
        "synthetic_only": True,
        "signing_controls": {
            "certificate_stored_by_rtm": False,
            "certificate_secret_allowed": False,
            "browser_session_shared_with_operator": False,
            "local_signer_activation_required": True,
            "signature_automated": False,
            "final_submit_automated": False,
        },
    }
    return {
        "case_id": CASE_ID,
        "package_id": PACKAGE_ID,
        "actor_operator_id": PREPARER_ID,
        "event_type": "presenter.delivery.prepared",
        "payload": payload,
    }


class FakeLocalStationRepository:
    def __init__(self) -> None:
        self.schema_ready = True
        self.delivery_event = _delivery_event()
        self.claim_events: list[dict[str, Any]] = []
        self.workspace_events: list[dict[str, Any]] = []
        self.installations: dict[str, dict[str, Any]] = {}
        self.locks: list[tuple[str, str]] = []

    def presenter_schema_ready(self, conn: Any) -> bool:
        del conn
        return self.schema_ready

    def lock_signer_installation(self, conn: Any, **kwargs: Any) -> None:
        del conn
        self.locks.append(("installation", kwargs["client_instance_id"]))

    def load_signer_installation_by_instance(
        self, conn: Any, **kwargs: Any
    ) -> Mapping[str, Any] | None:
        del conn
        for row in self.installations.values():
            if all(row.get(key) == value for key, value in kwargs.items()):
                return row
        return None

    def load_signer_installation_by_binding(
        self, conn: Any, *, client_binding_sha256: str
    ) -> Mapping[str, Any] | None:
        del conn
        return next(
            (
                row
                for row in self.installations.values()
                if row["client_binding_sha256"] == client_binding_sha256
            ),
            None,
        )

    def load_signer_installation(
        self, conn: Any, **kwargs: Any
    ) -> Mapping[str, Any] | None:
        del conn
        row = self.installations.get(kwargs["installation_id"])
        if row is None:
            return None
        if row["operator_id"] != kwargs["operator_id"]:
            return None
        if row["operator_device_id"] != kwargs["operator_device_id"]:
            return None
        return row

    def insert_signer_installation(self, conn: Any, **kwargs: Any) -> Mapping[str, Any]:
        del conn
        row = {
            "id": kwargs["installation_id"],
            "operator_id": kwargs["operator_id"],
            "operator_device_id": kwargs["operator_device_id"],
            "client_instance_id": kwargs["client_instance_id"],
            "client_binding_sha256": kwargs["client_binding_sha256"],
            "station_label": kwargs["station_label"],
            "platform": kwargs["platform"],
            "client_version": kwargs["client_version"],
            "status": "candidate",
            "registered_at": kwargs["registered_at"],
        }
        self.installations[row["id"]] = row
        return row

    def list_signature_queue_events(self, conn: Any, **kwargs: Any):
        del conn, kwargs
        return [self.delivery_event]

    def lock_signature_claim(self, conn: Any, *, delivery_id: str) -> None:
        del conn
        self.locks.append(("claim", delivery_id))

    def load_signature_queue_event(self, conn: Any, **kwargs: Any):
        del conn
        return self.delivery_event if kwargs["delivery_id"] == DELIVERY_ID else None

    def list_signature_claim_events(self, conn: Any, **kwargs: Any):
        del conn, kwargs
        return list(self.claim_events)

    def lock_signer_workspace(self, conn: Any, **kwargs: Any) -> None:
        del conn
        self.locks.append(("workspace", kwargs["workspace_id"]))

    def list_signer_workspace_events(self, conn: Any, **kwargs: Any):
        del conn
        return [
            event
            for event in self.workspace_events
            if event["payload"]["workspace_id"] == kwargs["workspace_id"]
        ]

    def append_audit(self, conn: Any, **kwargs: Any) -> None:
        del conn
        event = {
            **kwargs,
            "actor_operator_id": kwargs["actor"].operator_id,
        }
        if kwargs["event_type"].startswith("presenter.signer_workspace."):
            self.workspace_events.append(event)
        else:
            self.claim_events.append(event)


class PresenterLocalStationServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeLocalStationRepository()
        self.service = PresenterLocalStationService(
            repository=self.repository,
            runtime=_runtime(),
            clock=lambda: NOW,
        )
        self.conn = object()

    def register(self, **overrides: Any) -> dict[str, Any]:
        values = {
            "actor": _actor(),
            "operator_device_id": DEVICE_ID,
            "client_instance_id": INSTANCE_ID,
            "client_binding_sha256": hashlib.sha256(b"local-client").hexdigest(),
            "station_label": "PC firma Ramon",
            "platform": "windows",
            "client_version": "1.0.0",
        }
        values.update(overrides)
        return self.service.register_candidate(self.conn, **values)

    def claimed(self) -> dict[str, Any]:
        return self.service.signer.claim(
            self.conn,
            actor=_actor(),
            delivery_id=DELIVERY_ID,
            idempotency_key="signer-claim-local-0001",
        )

    def prepared(self) -> tuple[dict[str, Any], dict[str, Any]]:
        station = self.register()["installation"]
        claim = self.claimed()
        workspace = self.service.prepare_workspace(
            self.conn,
            actor=_actor(),
            operator_device_id=DEVICE_ID,
            installation_id=station["installation_id"],
            delivery_id=DELIVERY_ID,
            claim_id=claim["claim_id"],
            idempotency_key="workspace-prepare-local-0001",
        )
        return station, workspace

    def test_candidate_registration_is_idempotent_and_not_attestation(self):
        first = self.register()
        second = self.register()

        self.assertEqual(
            first["station_contract_version"], RTM_PRESENTER_LOCAL_STATION_VERSION
        )
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertFalse(first["managed_attestation_verified"])
        self.assertFalse(first["local_activation_available"])
        self.assertFalse(first["document_bytes_available"])
        self.assertFalse(first["certificate_stored_by_rtm"])
        self.assertEqual(len(self.repository.installations), 1)

    def test_candidate_rejects_instance_or_binding_reuse(self):
        self.register()
        with self.assertRaises(PresenterConflict) as reused:
            self.register(client_version="1.0.1")
        self.assertEqual(
            reused.exception.code, "presenter.local_station_instance_reused"
        )
        with self.assertRaises(PresenterConflict) as collision:
            self.register(client_instance_id=OTHER_INSTANCE_ID)
        self.assertEqual(
            collision.exception.code, "presenter.local_station_binding_reused"
        )

    def test_candidate_requires_bound_device_and_windows_contract(self):
        with self.assertRaises(PresenterConflict):
            self.register(operator_device_id=None)
        with self.assertRaises(PresenterConflict):
            self.register(platform="linux")
        with self.assertRaises(PresenterConflict):
            self.register(client_binding_sha256="not-a-hash")

    def test_candidate_requires_schema_readiness_before_registration(self):
        self.repository.schema_ready = False

        with self.assertRaises(PresenterSchemaNotReady):
            self.register()

        self.assertEqual(self.repository.installations, {})

    def test_candidate_requires_exact_signer_role_permissions_and_channel(self):
        for actor in (
            replace(_actor(), client_kind=PresenterClientKind.OPERATOR_UI),
            replace(_actor(), role_codes=("rtm.operator",)),
            replace(_actor(), permissions=("ops.view",)),
        ):
            with self.subTest(actor=actor):
                with self.assertRaises(PresenterPolicyError):
                    self.register(actor=actor)

    def test_candidate_projection_contains_no_metadata_or_secret_material(self):
        station = self.register()

        self.assertEqual(
            set(station["installation"]),
            {
                "installation_id",
                "operator_id",
                "operator_device_id",
                "client_instance_id",
                "client_binding_sha256",
                "station_label",
                "platform",
                "client_version",
                "status",
                "registered_at",
            },
        )
        serialized = str(station).lower()
        for forbidden in (
            "password",
            "private_key",
            "certificate_bytes",
            "presigned_url",
            "b2_key",
            "portal_session",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_candidate_cannot_be_loaded_from_another_device(self):
        installation_id = self.register()["installation"]["installation_id"]

        with self.assertRaises(PresenterNotFound):
            self.service.installation(
                self.conn,
                actor=_actor(),
                operator_device_id=_id(99),
                installation_id=installation_id,
            )

    def test_workspace_keeps_rtm_draft_and_delivers_no_bytes(self):
        station, workspace = self.prepared()

        self.assertEqual(
            workspace["workspace_contract_version"],
            RTM_PRESENTER_SIGNER_WORKSPACE_VERSION,
        )
        self.assertEqual(workspace["state"], "ready")
        self.assertEqual(workspace["attempt_number"], 1)
        self.assertTrue(workspace["rtm_draft_persisted"])
        self.assertFalse(workspace["reg_draft_persisted"])
        self.assertTrue(workspace["reg_session_recovery_available"])
        self.assertFalse(workspace["browser_open_available"])
        self.assertFalse(workspace["document_bytes_available"])
        self.assertFalse(workspace["external_effects_executed"])
        self.assertEqual(
            workspace["installation"]["installation_id"],
            station["installation_id"],
        )
        self.assertEqual(workspace["task"]["portal_preparation"]["fields"][0]["value"], "Recurso sintetico")
        workspace_lock = self.repository.locks.index(
            ("workspace", workspace["workspace_id"])
        )
        claim_locks = [
            index
            for index, lock in enumerate(self.repository.locks)
            if lock == ("claim", DELIVERY_ID)
        ]
        self.assertTrue(claim_locks)
        self.assertLess(claim_locks[-1], workspace_lock)

    def test_workspace_prepare_replays_without_duplicate_audit_event(self):
        station, first = self.prepared()
        second = self.service.prepare_workspace(
            self.conn,
            actor=_actor(),
            operator_device_id=DEVICE_ID,
            installation_id=station["installation_id"],
            delivery_id=DELIVERY_ID,
            claim_id=first["claim_id"],
            idempotency_key="workspace-prepare-local-0002",
        )

        self.assertTrue(second["replayed"])
        self.assertEqual(second["workspace_id"], first["workspace_id"])
        self.assertEqual(len(self.repository.workspace_events), 1)

    def test_reg_expiry_and_resume_preserve_workspace_and_increment_attempt(self):
        station, ready = self.prepared()
        expired = self.service.transition_workspace(
            self.conn,
            actor=_actor(),
            operator_device_id=DEVICE_ID,
            installation_id=station["installation_id"],
            delivery_id=DELIVERY_ID,
            claim_id=ready["claim_id"],
            workspace_id=ready["workspace_id"],
            action="portal_session_expired",
            idempotency_key="workspace-expired-local-0001",
        )
        resumed = self.service.transition_workspace(
            self.conn,
            actor=_actor(),
            operator_device_id=DEVICE_ID,
            installation_id=station["installation_id"],
            delivery_id=DELIVERY_ID,
            claim_id=ready["claim_id"],
            workspace_id=ready["workspace_id"],
            action="resume",
            idempotency_key="workspace-resume-local-0001",
        )

        self.assertEqual(expired["state"], "reg_session_expired")
        self.assertTrue(expired["reg_session_expired"])
        self.assertEqual(expired["workspace_id"], ready["workspace_id"])
        self.assertEqual(resumed["state"], "ready")
        self.assertEqual(resumed["attempt_number"], 2)
        self.assertEqual(resumed["task"]["task_fingerprint_sha256"], ready["task"]["task_fingerprint_sha256"])
        self.assertEqual(len(self.repository.workspace_events), 3)

    def test_expiry_replays_only_the_same_idempotent_command(self):
        station, ready = self.prepared()
        arguments = {
            "actor": _actor(),
            "operator_device_id": DEVICE_ID,
            "installation_id": station["installation_id"],
            "delivery_id": DELIVERY_ID,
            "claim_id": ready["claim_id"],
            "workspace_id": ready["workspace_id"],
            "action": "portal_session_expired",
            "idempotency_key": "workspace-expired-local-0001",
        }
        first = self.service.transition_workspace(self.conn, **arguments)
        second = self.service.transition_workspace(self.conn, **arguments)

        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(len(self.repository.workspace_events), 2)

        with self.assertRaises(PresenterConflict):
            self.service.transition_workspace(
                self.conn,
                **{
                    **arguments,
                    "idempotency_key": "workspace-expired-local-0002",
                },
            )

    def test_resume_is_rejected_until_an_expiry_was_recorded(self):
        station, ready = self.prepared()

        with self.assertRaises(PresenterConflict) as denied:
            self.service.transition_workspace(
                self.conn,
                actor=_actor(),
                operator_device_id=DEVICE_ID,
                installation_id=station["installation_id"],
                delivery_id=DELIVERY_ID,
                claim_id=ready["claim_id"],
                workspace_id=ready["workspace_id"],
                action="resume",
                idempotency_key="workspace-resume-local-0001",
            )

        self.assertEqual(
            denied.exception.code,
            "presenter.signer_workspace_transition_invalid",
        )

    def test_current_workspace_recovers_expired_state_without_reg_session(self):
        station, ready = self.prepared()
        self.service.transition_workspace(
            self.conn,
            actor=_actor(),
            operator_device_id=DEVICE_ID,
            installation_id=station["installation_id"],
            delivery_id=DELIVERY_ID,
            claim_id=ready["claim_id"],
            workspace_id=ready["workspace_id"],
            action="portal_session_expired",
            idempotency_key="workspace-expired-local-0001",
        )
        recovered = self.service.current_workspace(
            self.conn,
            actor=_actor(),
            operator_device_id=DEVICE_ID,
            installation_id=station["installation_id"],
            delivery_id=DELIVERY_ID,
            claim_id=ready["claim_id"],
            workspace_id=ready["workspace_id"],
        )

        self.assertEqual(recovered["state"], "reg_session_expired")
        self.assertEqual(
            recovered["next_action"], "reauthenticate_reg_then_resume_from_rtm"
        )
        self.assertTrue(recovered["rtm_draft_persisted"])
        self.assertFalse(recovered["reg_draft_persisted"])

    def test_workspace_history_rejects_a_fabricated_reg_draft(self):
        station, ready = self.prepared()
        self.repository.workspace_events[0]["payload"]["reg_draft_persisted"] = True

        with self.assertRaises(PresenterConflict) as denied:
            self.service.current_workspace(
                self.conn,
                actor=_actor(),
                operator_device_id=DEVICE_ID,
                installation_id=station["installation_id"],
                delivery_id=DELIVERY_ID,
                claim_id=ready["claim_id"],
                workspace_id=ready["workspace_id"],
            )

        self.assertEqual(
            denied.exception.code,
            "presenter.signer_workspace_history_invalid",
        )

    def test_workspace_is_bound_to_exact_identifier_and_active_claim(self):
        station, ready = self.prepared()
        with self.assertRaises(PresenterNotFound):
            self.service.current_workspace(
                self.conn,
                actor=_actor(),
                operator_device_id=DEVICE_ID,
                installation_id=station["installation_id"],
                delivery_id=DELIVERY_ID,
                claim_id=ready["claim_id"],
                workspace_id=_id(999),
            )
        with self.assertRaises(PresenterNotFound):
            self.service.current_workspace(
                self.conn,
                actor=_actor(session_id=_id(1000)),
                operator_device_id=DEVICE_ID,
                installation_id=station["installation_id"],
                delivery_id=DELIVERY_ID,
                claim_id=ready["claim_id"],
                workspace_id=ready["workspace_id"],
            )

    def test_router_exposes_only_metadata_recovery_routes_bound_to_device(self):
        router = (
            Path(__file__).resolve().parents[1] / "rtm_presenter_router.py"
        ).read_text(encoding="utf-8")

        for route in (
            '@router.post("/signer/installations")',
            '@router.get("/signer/installations/{installation_id}")',
            '"/signer/tasks/{delivery_id}/claims/{claim_id}/workspaces"',
            '"{workspace_id}/portal-session-expired"',
            '"{workspace_id}/resume"',
        ):
            self.assertIn(route, router)
        self.assertIn('operator_device_id=getattr(session, "device_id", None)', router)
        self.assertIn("operator_device_id=context.operator_device_id", router)
        self.assertIn('platform: Literal["windows"]', router)
        self.assertNotIn("certificate: str = Field", router)
        self.assertNotIn("password: str = Field", router)
        repository = (
            Path(__file__).resolve().parents[1] / "rtm_presenter_service.py"
        ).read_text(encoding="utf-8")
        self.assertIn("rtm-presenter-signer-installation:", repository)
        self.assertIn("rtm-presenter-signer-binding:", repository)


if __name__ == "__main__":
    unittest.main()
