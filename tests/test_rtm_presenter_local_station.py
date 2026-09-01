from __future__ import annotations

import hashlib
import json
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
    SqlPresenterRepository,
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
OTHER_SESSION_ID = _id(20)
THIRD_SESSION_ID = _id(21)


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

    def list_signer_workspace_recovery_events(self, conn: Any, **kwargs: Any):
        del conn
        matching = [
            event
            for event in self.workspace_events
            if event["actor_operator_id"] == kwargs["operator_id"]
            and event["payload"]["signer_operator_id"] == kwargs["operator_id"]
            and event["payload"]["operator_device_id"]
            == kwargs["operator_device_id"]
            and event["payload"]["installation_id"] == kwargs["installation_id"]
        ]
        latest_by_delivery: dict[str, str] = {}
        for event in matching:
            latest_by_delivery[event["payload"]["delivery_id"]] = event[
                "payload"
            ]["workspace_id"]
        latest_workspace_ids = list(reversed(latest_by_delivery.values()))
        allowed = set(latest_workspace_ids[: kwargs["limit"]])
        return [
            event
            for event in matching
            if event["payload"]["workspace_id"] in allowed
        ]

    def list_latest_signer_delivery_workspace_events(
        self, conn: Any, **kwargs: Any
    ):
        del conn
        matching = [
            event
            for event in self.workspace_events
            if event["actor_operator_id"] == kwargs["operator_id"]
            and event["payload"]["operator_device_id"]
            == kwargs["operator_device_id"]
            and event["payload"]["installation_id"] == kwargs["installation_id"]
            and event["payload"]["delivery_id"] == kwargs["delivery_id"]
        ]
        if not matching:
            return []
        latest_workspace_id = matching[-1]["payload"]["workspace_id"]
        return [
            event
            for event in matching
            if event["payload"]["workspace_id"] == latest_workspace_id
        ]

    def append_audit(self, conn: Any, **kwargs: Any) -> None:
        del conn
        event = {
            **kwargs,
            "actor_operator_id": kwargs["actor"].operator_id,
            "sequence_number": len(self.claim_events)
            + len(self.workspace_events)
            + 1,
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

    def recover(
        self,
        station: Mapping[str, Any],
        source: Mapping[str, Any],
        *,
        session_id: str = OTHER_SESSION_ID,
        idempotency_key: str = "workspace-recovery-local-0001",
        expected_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        return self.service.recover_workspace(
            self.conn,
            actor=_actor(session_id=session_id),
            operator_device_id=DEVICE_ID,
            installation_id=station["installation_id"],
            delivery_id=DELIVERY_ID,
            source_workspace_id=source["workspace_id"],
            expected_task_fingerprint_sha256=(
                expected_fingerprint
                or source["task"]["task_fingerprint_sha256"]
            ),
            idempotency_key=idempotency_key,
        )

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

    def test_discovery_is_metadata_only_and_distinguishes_reload_from_relogin(self):
        station, ready = self.prepared()

        same_session = self.service.discover_workspaces(
            self.conn,
            actor=_actor(),
            operator_device_id=DEVICE_ID,
            installation_id=station["installation_id"],
        )
        new_session = self.service.discover_workspaces(
            self.conn,
            actor=_actor(session_id=OTHER_SESSION_ID),
            operator_device_id=DEVICE_ID,
            installation_id=station["installation_id"],
        )

        self.assertEqual(same_session["items"][0]["recovery_status"], "current_session")
        self.assertEqual(
            new_session["items"][0]["recovery_status"],
            "adoptable_supersession",
        )
        self.assertEqual(new_session["items"][0]["workspace_id"], ready["workspace_id"])
        self.assertTrue(new_session["metadata_only"])
        self.assertFalse(new_session["browser_storage_required"])
        self.assertFalse(new_session["document_bytes_available"])
        serialized = str(new_session).lower()
        for forbidden in (
            "portal_preparation",
            "document_version_id",
            "cookie_value",
            "certificate_bytes",
            "storage_key",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_same_session_recovery_reopens_without_new_ledger_event(self):
        station, ready = self.prepared()
        before = (len(self.repository.claim_events), len(self.repository.workspace_events))

        reopened = self.recover(station, ready, session_id=SESSION_ID)

        self.assertTrue(reopened["replayed"])
        self.assertEqual(reopened["workspace_id"], ready["workspace_id"])
        self.assertIsInstance(reopened["claim_expires_at"], str)
        json.dumps(reopened)
        self.assertEqual(
            before,
            (len(self.repository.claim_events), len(self.repository.workspace_events)),
        )

    def test_exact_station_relogin_supersedes_claim_and_adopts_durable_snapshot(self):
        station, ready = self.prepared()

        recovered = self.recover(station, ready)

        self.assertFalse(recovered["replayed"])
        self.assertTrue(recovered["recovery_adopted"])
        self.assertEqual(recovered["attempt_number"], 2)
        self.assertNotEqual(recovered["claim_id"], ready["claim_id"])
        self.assertNotEqual(recovered["workspace_id"], ready["workspace_id"])
        self.assertEqual(
            recovered["recovered_from"],
            {
                "workspace_id": ready["workspace_id"],
                "claim_id": ready["claim_id"],
                "attempt_number": 1,
            },
        )
        self.assertIsInstance(recovered["claim_expires_at"], str)
        self.assertEqual(
            [event["event_type"] for event in self.repository.claim_events],
            [
                "presenter.signer_station.claimed",
                "presenter.signer_station.superseded",
                "presenter.signer_station.claimed",
            ],
        )
        self.assertEqual(
            [event["event_type"] for event in self.repository.workspace_events],
            [
                "presenter.signer_workspace.prepared",
                "presenter.signer_workspace.recovered",
            ],
        )

    def test_recovery_retry_is_idempotent_but_stale_source_cannot_fork(self):
        station, ready = self.prepared()
        first = self.recover(station, ready)
        replay = self.recover(station, ready)
        counts = (len(self.repository.claim_events), len(self.repository.workspace_events))

        self.assertTrue(replay["replayed"])
        self.assertIsInstance(replay["claim_expires_at"], str)
        json.dumps(replay)
        self.assertEqual(replay["workspace_id"], first["workspace_id"])
        self.assertEqual(replay["claim_id"], first["claim_id"])
        self.assertEqual(
            counts,
            (len(self.repository.claim_events), len(self.repository.workspace_events)),
        )
        with self.assertRaises(PresenterConflict) as stale:
            self.recover(
                station,
                ready,
                idempotency_key="workspace-recovery-local-0002",
            )
        self.assertEqual(
            stale.exception.code,
            "presenter.signer_workspace_recovery_source_stale",
        )
        self.assertEqual(
            counts,
            (len(self.repository.claim_events), len(self.repository.workspace_events)),
        )

    def test_second_session_cannot_race_the_new_active_claim(self):
        station, ready = self.prepared()
        self.recover(station, ready)
        counts = (len(self.repository.claim_events), len(self.repository.workspace_events))

        with self.assertRaises(PresenterConflict) as blocked:
            self.recover(
                station,
                ready,
                session_id=THIRD_SESSION_ID,
                idempotency_key="workspace-recovery-local-0003",
            )

        self.assertEqual(
            blocked.exception.code,
            "presenter.signer_workspace_recovery_claim_active",
        )
        self.assertEqual(
            counts,
            (len(self.repository.claim_events), len(self.repository.workspace_events)),
        )

    def test_historical_session_cannot_take_back_a_descendant_claim(self):
        station, ready = self.prepared()
        descendant = self.recover(station, ready)
        counts = (len(self.repository.claim_events), len(self.repository.workspace_events))

        with self.assertRaises(PresenterConflict) as rollback:
            self.recover(
                station,
                descendant,
                session_id=SESSION_ID,
                idempotency_key="workspace-recovery-rollback-0001",
            )

        self.assertEqual(
            rollback.exception.code,
            "presenter.signer_workspace_recovery_session_rollback",
        )
        self.assertEqual(
            counts,
            (len(self.repository.claim_events), len(self.repository.workspace_events)),
        )

    def test_historical_session_remains_blocked_after_descendant_claim_expires(self):
        station, ready = self.prepared()
        descendant = self.recover(station, ready)
        later = NOW + timedelta(minutes=31)
        self.service.clock = lambda: later
        self.service.signer.clock = lambda: later
        counts = (len(self.repository.claim_events), len(self.repository.workspace_events))

        with self.assertRaises(PresenterConflict) as rollback:
            self.recover(
                station,
                descendant,
                session_id=SESSION_ID,
                idempotency_key="workspace-recovery-rollback-0002",
            )

        self.assertEqual(
            rollback.exception.code,
            "presenter.signer_workspace_recovery_session_rollback",
        )
        self.assertEqual(
            counts,
            (len(self.repository.claim_events), len(self.repository.workspace_events)),
        )

    def test_new_third_session_can_extend_the_monotonic_recovery_chain(self):
        station, ready = self.prepared()
        second = self.recover(station, ready)

        third = self.recover(
            station,
            second,
            session_id=THIRD_SESSION_ID,
            idempotency_key="workspace-recovery-local-0005",
        )

        self.assertEqual(third["attempt_number"], 3)
        self.assertEqual(third["recovered_from"]["workspace_id"], second["workspace_id"])
        discoveries = self.service.discover_workspaces(
            self.conn,
            actor=_actor(),
            operator_device_id=DEVICE_ID,
            installation_id=station["installation_id"],
        )
        self.assertEqual(
            discoveries["items"][0]["recovery_status"],
            "blocked_session_rollback",
        )

    def test_expired_claim_creates_new_attempt_without_supersession(self):
        station, ready = self.prepared()
        later = NOW + timedelta(minutes=31)
        self.service.clock = lambda: later
        self.service.signer.clock = lambda: later

        recovered = self.recover(station, ready)

        self.assertEqual(recovered["attempt_number"], 2)
        self.assertEqual(
            [event["event_type"] for event in self.repository.claim_events],
            [
                "presenter.signer_station.claimed",
                "presenter.signer_station.claimed",
            ],
        )

    def test_foreign_active_claim_is_never_superseded(self):
        station, ready = self.prepared()
        claim_event = self.repository.claim_events[0]
        claim_event["payload"]["signer_operator_id"] = PREPARER_ID
        claim_event["actor_operator_id"] = PREPARER_ID
        counts = (len(self.repository.claim_events), len(self.repository.workspace_events))

        with self.assertRaises(PresenterConflict) as blocked:
            self.recover(station, ready)

        self.assertEqual(
            blocked.exception.code,
            "presenter.signer_workspace_recovery_claim_active",
        )
        self.assertEqual(
            counts,
            (len(self.repository.claim_events), len(self.repository.workspace_events)),
        )

    def test_recovery_rejects_another_device_or_installation_before_audit(self):
        station, ready = self.prepared()
        counts = (len(self.repository.claim_events), len(self.repository.workspace_events))

        with self.assertRaises(PresenterNotFound):
            self.service.recover_workspace(
                self.conn,
                actor=_actor(session_id=OTHER_SESSION_ID),
                operator_device_id=_id(99),
                installation_id=station["installation_id"],
                delivery_id=DELIVERY_ID,
                source_workspace_id=ready["workspace_id"],
                expected_task_fingerprint_sha256=ready["task"][
                    "task_fingerprint_sha256"
                ],
                idempotency_key="workspace-recovery-local-0004",
            )
        other_station = self.register(
            client_instance_id=OTHER_INSTANCE_ID,
            client_binding_sha256=hashlib.sha256(b"other-client").hexdigest(),
        )["installation"]
        with self.assertRaises(PresenterConflict):
            self.recover(other_station, ready)
        self.assertEqual(
            counts,
            (len(self.repository.claim_events), len(self.repository.workspace_events)),
        )

    def test_superseded_claim_cannot_be_released_by_old_session(self):
        station, ready = self.prepared()
        self.recover(station, ready)
        count = len(self.repository.claim_events)

        with self.assertRaises(PresenterConflict) as denied:
            self.service.signer.release(
                self.conn,
                actor=_actor(),
                delivery_id=DELIVERY_ID,
                claim_id=ready["claim_id"],
                idempotency_key="signer-release-superseded-0001",
            )

        self.assertEqual(
            denied.exception.code,
            "presenter.signer_station_claim_not_active",
        )
        self.assertEqual(len(self.repository.claim_events), count)

    def test_recovery_lock_order_is_claim_then_source_then_target(self):
        station, ready = self.prepared()
        self.repository.locks.clear()

        recovered = self.recover(station, ready)

        self.assertEqual(self.repository.locks[0], ("claim", DELIVERY_ID))
        self.assertEqual(
            self.repository.locks[1],
            ("workspace", ready["workspace_id"]),
        )
        self.assertEqual(
            self.repository.locks[-1],
            ("workspace", recovered["workspace_id"]),
        )

    def test_recovery_provenance_rejects_a_cycle(self):
        station, ready = self.prepared()
        recovered = self.recover(station, ready)
        first = self.repository.workspace_events[0]
        second = self.repository.workspace_events[1]
        first["event_type"] = "presenter.signer_workspace.recovered"
        first["payload"].update(
            {
                "state": "ready",
                "attempt_number": 2,
                "recovery_contract_version": (
                    "rtm_presenter_workspace_recovery_v1_0"
                ),
                "source_workspace_id": recovered["workspace_id"],
                "source_claim_id": recovered["claim_id"],
                "source_attempt_number": 1,
                "expected_task_fingerprint_sha256": ready["task"][
                    "task_fingerprint_sha256"
                ],
                "browser_storage_required": False,
                "cookie_material_persisted": False,
                "certificate_material_persisted": False,
            }
        )
        second["payload"]["attempt_number"] = 3
        second["payload"]["source_attempt_number"] = 2

        with self.assertRaises(PresenterConflict) as invalid:
            self.service.discover_workspaces(
                self.conn,
                actor=_actor(session_id=OTHER_SESSION_ID),
                operator_device_id=DEVICE_ID,
                installation_id=station["installation_id"],
            )

        self.assertEqual(
            invalid.exception.code,
            "presenter.signer_workspace_recovery_provenance_invalid",
        )

    def test_recovery_requires_exact_fingerprint_before_superseding(self):
        station, ready = self.prepared()
        counts = (len(self.repository.claim_events), len(self.repository.workspace_events))

        with self.assertRaises(PresenterConflict) as mismatch:
            self.recover(
                station,
                ready,
                expected_fingerprint=hashlib.sha256(b"different").hexdigest(),
            )

        self.assertEqual(
            mismatch.exception.code,
            "presenter.signer_workspace_fingerprint_mismatch",
        )
        self.assertEqual(
            counts,
            (len(self.repository.claim_events), len(self.repository.workspace_events)),
        )

    def test_discovery_uses_ledger_sequence_when_timestamps_are_equal(self):
        station, ready = self.prepared()
        recovered = self.recover(station, ready)

        discoveries = self.service.discover_workspaces(
            self.conn,
            actor=_actor(session_id=OTHER_SESSION_ID),
            operator_device_id=DEVICE_ID,
            installation_id=station["installation_id"],
        )

        self.assertEqual(discoveries["item_count"], 1)
        self.assertEqual(discoveries["items"][0]["workspace_id"], recovered["workspace_id"])
        self.assertEqual(discoveries["items"][0]["attempt_number"], 2)
        self.assertEqual(discoveries["items"][0]["recovery_status"], "current_session")
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
            '"/signer/installations/{installation_id}/workspace-recoveries"',
            '"/signer/tasks/{delivery_id}/workspace-recovery"',
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
        self.assertIn("DISTINCT ON (delivery_id)", repository)
        self.assertIn("list_latest_signer_delivery_workspace_events", repository)
        self.assertIn("event.payload->>'operator_device_id'=:operator_device_id", repository)


class PresenterLocalStationSqlContractTest(unittest.TestCase):
    class Result:
        def mappings(self) -> "PresenterLocalStationSqlContractTest.Result":
            return self

        def all(self) -> list[Mapping[str, Any]]:
            return []

    class Connection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Mapping[str, Any]]] = []

        def execute(self, statement: Any, params: Mapping[str, Any]):
            self.calls.append((str(statement), params))
            return PresenterLocalStationSqlContractTest.Result()

    def test_exact_workspace_query_projects_ledger_identity_and_recovered_event(self):
        conn = self.Connection()
        SqlPresenterRepository().list_signer_workspace_events(
            conn,
            case_id=CASE_ID,
            package_id=PACKAGE_ID,
            delivery_id=DELIVERY_ID,
            workspace_id=_id(30),
        )

        sql, params = conn.calls[0]
        self.assertIn("actor_operator_id, case_id, package_id", sql)
        self.assertIn("'presenter.signer_workspace.recovered'", sql)
        self.assertEqual(params["delivery_id"], DELIVERY_ID)
        self.assertEqual(params["workspace_id"], _id(30))

    def test_discovery_and_leaf_queries_repeat_exact_station_scope(self):
        conn = self.Connection()
        repository = SqlPresenterRepository()
        repository.list_signer_workspace_recovery_events(
            conn,
            operator_id=SIGNER_ID,
            operator_device_id=DEVICE_ID,
            installation_id=_id(31),
            limit=20,
        )
        repository.list_latest_signer_delivery_workspace_events(
            conn,
            operator_id=SIGNER_ID,
            operator_device_id=DEVICE_ID,
            installation_id=_id(31),
            delivery_id=DELIVERY_ID,
        )

        discovery_sql, discovery_params = conn.calls[0]
        leaf_sql, leaf_params = conn.calls[1]
        self.assertIn("DISTINCT ON (delivery_id)", discovery_sql)
        for sql in (discovery_sql, leaf_sql):
            self.assertGreaterEqual(
                sql.count("event.payload->>'operator_device_id'=:operator_device_id"),
                2,
            )
            self.assertGreaterEqual(
                sql.count("event.payload->>'installation_id'=:installation_id"),
                2,
            )
            self.assertIn("event.case_id, event.package_id", sql)
        self.assertEqual(discovery_params["limit"], 20)
        self.assertEqual(leaf_params["delivery_id"], DELIVERY_ID)


if __name__ == "__main__":
    unittest.main()
