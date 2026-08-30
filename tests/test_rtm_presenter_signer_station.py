from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from rtm_presenter_contracts import PresenterClientKind
from rtm_presenter_delivery import RTM_PRESENTER_DELIVERY_VERSION
from rtm_presenter_policy import (
    PRESENTER_SIGNING_CLAIM_PERMISSION,
    PRESENTER_SIGNING_QUEUE_PERMISSION,
    PresenterActorContext,
    PresenterPolicyError,
    PresenterRuntimeConfiguration,
)
from rtm_presenter_service import PresenterConflict, SqlPresenterRepository
from rtm_presenter_signer_station import (
    RTM_PRESENTER_SIGNER_CLAIM_TTL_SECONDS,
    RTM_PRESENTER_SIGNER_STATION_VERSION,
    PresenterSignerStationService,
)


NOW = datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc)


def _id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


CASE_ID = _id(1)
PACKAGE_ID = _id(2)
DELIVERY_ID = _id(3)
PREPARER_ID = _id(4)
SIGNER_ID = _id(5)
SESSION_ID = _id(6)
OTHER_SESSION_ID = _id(7)


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


def _signer_actor(
    *,
    session_id: str = SESSION_ID,
    role: str = "rtm.signer",
    client_kind: PresenterClientKind = PresenterClientKind.SIGNER_STATION,
    permissions: tuple[str, ...] = (
        "ops.view",
        PRESENTER_SIGNING_CLAIM_PERMISSION,
        PRESENTER_SIGNING_QUEUE_PERMISSION,
    ),
) -> PresenterActorContext:
    return PresenterActorContext(
        operator_id=SIGNER_ID,
        operator_session_id=session_id,
        permissions=permissions,
        role_codes=(role,),
        client_kind=client_kind,
        authenticated_at=NOW - timedelta(minutes=5),
    )


def _delivery_event() -> dict[str, Any]:
    document_sha = hashlib.sha256(b"main").hexdigest()
    authorization_sha = hashlib.sha256(b"authorization").hexdigest()
    payload = {
        "delivery_contract_version": RTM_PRESENTER_DELIVERY_VERSION,
        "delivery_id": DELIVERY_ID,
        "case_id": CASE_ID,
        "package_id": PACKAGE_ID,
        "package_manifest_sha256": hashlib.sha256(b"manifest").hexdigest(),
        "destination_profile_id": _id(8),
        "destination_profile_code": "reg_general_synthetic",
        "destination_profile_version": 1,
        "destination_profile_sha256": hashlib.sha256(b"profile").hexdigest(),
        "destination_display_name": "Registro Electrónico General sintético",
        "channel": "portal",
        "state": "awaiting_signature",
        "representation_mode": "representative",
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
                "subject": "Recurso sintético",
                "facts": "Hechos completamente sintéticos.",
            },
        },
        "items": [
            {
                "package_item_id": _id(10),
                "document_version_id": _id(20),
                "document_sha256": document_sha,
                "item_order": 1,
                "field_code": "main_filing",
                "portal_filename": "recurso.pdf",
                "media_type": "application/pdf",
                "size_bytes": 120,
                "state": "pending",
            },
            {
                "package_item_id": _id(11),
                "document_version_id": _id(21),
                "document_sha256": authorization_sha,
                "item_order": 2,
                "field_code": "representation_authorization",
                "portal_filename": "autorizacion.pdf",
                "media_type": "application/pdf",
                "size_bytes": 90,
                "state": "pending",
            },
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


class FakeSignerRepository:
    def __init__(self) -> None:
        self.schema_ready = True
        self.delivery_event = _delivery_event()
        self.claim_events: list[dict[str, Any]] = []
        self.locks: list[str] = []
        self.queue_calls: list[tuple[str, int]] = []

    def presenter_schema_ready(self, conn: Any) -> bool:
        del conn
        return self.schema_ready

    def list_signature_queue_events(
        self, conn: Any, *, operator_id: str, limit: int
    ) -> list[Mapping[str, Any]]:
        del conn
        self.queue_calls.append((operator_id, limit))
        return [self.delivery_event][:limit]

    def lock_signature_claim(
        self, conn: Any, *, delivery_id: str
    ) -> None:
        del conn
        self.locks.append(delivery_id)

    def load_signature_queue_event(
        self, conn: Any, *, operator_id: str, delivery_id: str
    ) -> Mapping[str, Any] | None:
        del conn, operator_id
        if delivery_id == DELIVERY_ID:
            return self.delivery_event
        return None

    def list_signature_claim_events(
        self,
        conn: Any,
        *,
        case_id: str,
        package_id: str,
        delivery_id: str,
    ) -> list[Mapping[str, Any]]:
        del conn
        if (
            case_id == CASE_ID
            and package_id == PACKAGE_ID
            and delivery_id == DELIVERY_ID
        ):
            return list(self.claim_events)
        return []

    def append_audit(self, conn: Any, **kwargs: Any) -> None:
        del conn
        actor = kwargs["actor"]
        self.claim_events.append(
            {
                **dict(kwargs),
                "actor_operator_id": actor.operator_id,
            }
        )


class PresenterSignerStationServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = NOW
        self.repository = FakeSignerRepository()
        self.service = PresenterSignerStationService(
            repository=self.repository,
            runtime=_runtime(),
            clock=lambda: self.now,
        )
        self.conn = object()

    def test_queue_is_metadata_only_and_requires_exact_signer_authority(self):
        queue = self.service.queue(
            self.conn,
            actor=_signer_actor(),
            limit=25,
        )

        self.assertEqual(
            queue["station_contract_version"],
            RTM_PRESENTER_SIGNER_STATION_VERSION,
        )
        self.assertEqual(queue["item_count"], 1)
        self.assertEqual(queue["items"][0]["claim_status"], "available")
        self.assertEqual(queue["items"][0]["representation_mode"], "representative")
        self.assertFalse(queue["local_activation_available"])
        self.assertFalse(queue["certificate_stored_by_rtm"])
        self.assertFalse(queue["browser_session_shared"])
        self.assertNotIn("items", queue["items"][0])
        self.assertEqual(self.repository.queue_calls, [(SIGNER_ID, 25)])

        with self.assertRaises(PresenterPolicyError):
            self.service.queue(
                self.conn,
                actor=_signer_actor(client_kind=PresenterClientKind.OPERATOR_UI),
            )
        with self.assertRaises(PresenterPolicyError):
            self.service.queue(
                self.conn,
                actor=_signer_actor(role="rtm.operator"),
            )

    def test_claim_is_exclusive_idempotent_and_never_opens_the_browser(self):
        first = self.service.claim(
            self.conn,
            actor=_signer_actor(),
            delivery_id=DELIVERY_ID,
            idempotency_key="signer-claim-command-0001",
        )
        second = self.service.claim(
            self.conn,
            actor=_signer_actor(),
            delivery_id=DELIVERY_ID,
            idempotency_key="signer-claim-command-0001",
        )

        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(first["claim_id"], second["claim_id"])
        self.assertEqual(
            first["expires_at"],
            (NOW + timedelta(seconds=RTM_PRESENTER_SIGNER_CLAIM_TTL_SECONDS)).isoformat(),
        )
        self.assertEqual(first["task"]["document_count"], 2)
        self.assertEqual(
            first["task"]["items"][1]["field_code"],
            "representation_authorization",
        )
        self.assertFalse(first["browser_open_available"])
        self.assertFalse(first["certificate_stored_by_rtm"])
        self.assertFalse(first["signature_automated"])
        self.assertFalse(first["final_submit_automated"])
        self.assertFalse(first["external_effects_executed"])
        self.assertEqual(len(self.repository.claim_events), 1)

        with self.assertRaises(PresenterConflict) as busy:
            self.service.claim(
                self.conn,
                actor=_signer_actor(session_id=OTHER_SESSION_ID),
                delivery_id=DELIVERY_ID,
                idempotency_key="signer-claim-command-0002",
            )
        self.assertEqual(
            busy.exception.code,
            "presenter.signer_station_task_busy",
        )

    def test_expired_claim_does_not_block_a_new_session(self):
        self.service.claim(
            self.conn,
            actor=_signer_actor(),
            delivery_id=DELIVERY_ID,
            idempotency_key="signer-claim-command-0001",
        )
        self.now = NOW + timedelta(
            seconds=RTM_PRESENTER_SIGNER_CLAIM_TTL_SECONDS + 1
        )

        next_claim = self.service.claim(
            self.conn,
            actor=_signer_actor(session_id=OTHER_SESSION_ID),
            delivery_id=DELIVERY_ID,
            idempotency_key="signer-claim-command-0002",
        )

        self.assertFalse(next_claim["replayed"])
        self.assertEqual(len(self.repository.claim_events), 2)

    def test_release_is_owner_bound_and_idempotent(self):
        claim = self.service.claim(
            self.conn,
            actor=_signer_actor(),
            delivery_id=DELIVERY_ID,
            idempotency_key="signer-claim-command-0001",
        )
        released = self.service.release(
            self.conn,
            actor=_signer_actor(),
            delivery_id=DELIVERY_ID,
            claim_id=claim["claim_id"],
            idempotency_key="signer-release-command-0001",
        )
        replay = self.service.release(
            self.conn,
            actor=_signer_actor(),
            delivery_id=DELIVERY_ID,
            claim_id=claim["claim_id"],
            idempotency_key="signer-release-command-0001",
        )

        self.assertEqual(released["state"], "released")
        self.assertFalse(released["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertFalse(replay["external_effects_executed"])
        self.assertEqual(len(self.repository.claim_events), 2)

    def test_current_claim_recovers_only_this_sessions_active_task(self):
        created = self.service.claim(
            self.conn,
            actor=_signer_actor(),
            delivery_id=DELIVERY_ID,
            idempotency_key="signer-claim-command-0001",
        )

        recovered = self.service.current_claim(
            self.conn,
            actor=_signer_actor(),
            delivery_id=DELIVERY_ID,
        )

        self.assertEqual(recovered["claim_id"], created["claim_id"])
        self.assertTrue(recovered["recovered"])
        self.assertFalse(recovered["browser_open_available"])

        from rtm_presenter_service import PresenterNotFound

        with self.assertRaises(PresenterNotFound):
            self.service.current_claim(
                self.conn,
                actor=_signer_actor(session_id=OTHER_SESSION_ID),
                delivery_id=DELIVERY_ID,
            )

    def test_claim_permission_is_separate_from_queue_permission(self):
        with self.assertRaises(PresenterPolicyError):
            self.service.claim(
                self.conn,
                actor=_signer_actor(
                    permissions=("ops.view", PRESENTER_SIGNING_QUEUE_PERMISSION)
                ),
                delivery_id=DELIVERY_ID,
                idempotency_key="signer-claim-command-0001",
            )


class PresenterSignerStationSqlContractTest(unittest.TestCase):
    class MappingResult:
        def __init__(self, rows: list[Mapping[str, Any]] | None = None) -> None:
            self.rows = rows or []

        def mappings(self) -> "PresenterSignerStationSqlContractTest.MappingResult":
            return self

        def all(self) -> list[Mapping[str, Any]]:
            return list(self.rows)

        def first(self) -> Mapping[str, Any] | None:
            return self.rows[0] if self.rows else None

    class CapturingConnection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def execute(
            self,
            statement: Any,
            params: Mapping[str, Any] | None = None,
        ) -> "PresenterSignerStationSqlContractTest.MappingResult":
            self.calls.append(
                (" ".join(str(statement).split()).lower(), dict(params or {}))
            )
            return PresenterSignerStationSqlContractTest.MappingResult()

    def test_exact_task_lookup_keeps_assignment_scope_and_no_storage(self):
        conn = self.CapturingConnection()

        row = SqlPresenterRepository().load_signature_queue_event(
            conn,
            operator_id=SIGNER_ID,
            delivery_id=DELIVERY_ID,
        )

        self.assertIsNone(row)
        sql, params = conn.calls[0]
        self.assertEqual(params["operator_id"], SIGNER_ID)
        self.assertEqual(params["delivery_id"], DELIVERY_ID)
        self.assertIn("event.payload->>'delivery_id'=:delivery_id", sql)
        self.assertIn("delivery.payload->>'state'='awaiting_signature'", sql)
        self.assertIn("rtm_connect_a1s_memberships", sql)
        self.assertIn("rtm_work_assignments", sql)
        self.assertIn("w.operator_id=cast(:operator_id as uuid)", sql)
        self.assertIn("w.accepted_at is not null", sql)
        for forbidden in ("b2_bucket", "b2_key", "storage_key", "presigned_url"):
            self.assertNotIn(forbidden, sql)

    def test_claim_lock_and_history_are_delivery_bound(self):
        conn = self.CapturingConnection()
        repository = SqlPresenterRepository()

        repository.lock_signature_claim(conn, delivery_id=DELIVERY_ID)
        rows = repository.list_signature_claim_events(
            conn,
            case_id=CASE_ID,
            package_id=PACKAGE_ID,
            delivery_id=DELIVERY_ID,
        )

        self.assertEqual(rows, [])
        lock_sql, lock_params = conn.calls[0]
        history_sql, history_params = conn.calls[1]
        self.assertIn("pg_advisory_xact_lock", lock_sql)
        self.assertIn("rtm-presenter-signature-claim:", lock_sql)
        self.assertEqual(lock_params, {"delivery_id": DELIVERY_ID})
        self.assertIn("presenter.signer_station.claimed", history_sql)
        self.assertIn("presenter.signer_station.released", history_sql)
        self.assertIn("case_id=cast(:case_id as uuid)", history_sql)
        self.assertIn("package_id=cast(:package_id as uuid)", history_sql)
        self.assertEqual(
            history_params,
            {
                "case_id": CASE_ID,
                "package_id": PACKAGE_ID,
                "delivery_id": DELIVERY_ID,
            },
        )


if __name__ == "__main__":
    unittest.main()
