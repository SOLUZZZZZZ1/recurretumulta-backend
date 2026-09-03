from __future__ import annotations

import asyncio
import hashlib
import json
import os
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

import billing


CASE_ID = "81b9edba-e9ad-4a9b-84b6-8d978b7028f8"
SESSION_ID = "cs_test_vehicle_authoritative_123"
EVENT_ID = "evt_vehicle_authoritative_123"
PAYMENT_INTENT = "pi_vehicle_authoritative_123"


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, *, case: dict | None = None, intent: dict | None = None):
        self.case = case or _pending_case()
        self.intent = intent or _intent()
        self.calls: list[tuple[str, dict]] = []
        self.events: list[tuple[str, dict]] = []
        self.updates: list[dict] = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        parameters = dict(parameters or {})
        self.calls.append((sql, parameters))
        if "SELECT payment_status, stripe_session_id" in sql:
            return _Result(self.case)
        if (
            "SELECT COALESCE(payment_status, '') AS payment_status" in sql
            and "COALESCE(stripe_session_id, '') AS stripe_session_id" in sql
        ):
            return _Result(self.case)
        if (
            "checkout_async_payment_pending" in sql
            and "payload->>'stripe_event_id'" in sql
        ):
            event_id = parameters["stripe_event_id"]
            return _Result(
                (1,)
                if any(
                    payload.get("stripe_event_id") == event_id
                    for _event_type, payload in self.events
                )
                else None
            )
        if (
            "vehicle_removal_checkout_session_expired" in sql
            and "payload->>'stripe_event_id'" in sql
        ):
            event_id = parameters["stripe_event_id"]
            return _Result(
                (1,)
                if any(
                    event_type == "vehicle_removal_checkout_session_expired"
                    and payload.get("stripe_event_id") == event_id
                    for event_type, payload in self.events
                )
                else None
            )
        if "SELECT payload" in sql and "vehicle_removal_checkout_session_created" in sql:
            return _Result((self.intent,))
        if "UPDATE cases" in sql and "status='vehicle_removal_paid'" in sql:
            self.updates.append(parameters)
            return _Result((parameters["id"],))
        if "UPDATE cases" in sql and "status='authorization_pending'" in sql:
            if (
                self.case.get("payment_status") == "pending"
                and self.case.get("stripe_session_id") == parameters["session_id"]
                and self.case.get("status") == "vehicle_removal_pending_payment"
            ):
                self.case.update(
                    payment_status="unpaid",
                    stripe_session_id="",
                    product_code="",
                    status="authorization_pending",
                )
                self.updates.append(parameters)
                return _Result((parameters["case_id"],))
            return _Result()
        if "UPDATE cases" in sql and "payment_status='failed'" in sql:
            if (
                self.case.get("payment_status") == "pending"
                and self.case.get("stripe_session_id") == parameters["session_id"]
            ):
                self.case.update(
                    payment_status="failed",
                    status=parameters["next_status"],
                )
                self.updates.append(parameters)
                return _Result((parameters["id"],))
            return _Result()
        if "INSERT INTO events" in sql:
            payload = json.loads(parameters["payload"])
            self.events.append((parameters["type"], payload))
            return _Result()
        raise AssertionError(f"SQL inesperado: {sql}")


class _Transaction:
    def __init__(self, connection: _Connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        return False


class _Engine:
    def __init__(self, connection: _Connection):
        self.connection = connection

    def begin(self):
        return _Transaction(self.connection)


class _Request:
    headers = {"stripe-signature": "synthetic-valid-signature"}

    def __init__(self, *, query_params=None):
        self.query_params = query_params or {}

    async def body(self):
        return b"synthetic-signed-stripe-event"


def _metadata() -> dict:
    return {
        "case_id": CASE_ID,
        "service_code": "vehicle_removal",
        "product_code": "ELIMINAR_COCHE",
        "checkout_contract": "rtm_vehicle_removal_v3",
        "amount_cents": "3900",
        "currency": "EUR",
        "quote_version": "rtm_vehicle_removal_quote_v1",
    }


def _event(**session_overrides) -> dict:
    session = {
        "id": SESSION_ID,
        "mode": "payment",
        "payment_status": "paid",
        "payment_intent": PAYMENT_INTENT,
        "amount_total": 3900,
        "currency": "eur",
        "metadata": _metadata(),
    }
    session.update(session_overrides)
    return {
        "id": EVENT_ID,
        "type": "checkout.session.completed",
        "data": {"object": session},
    }


def _pending_case(**overrides) -> dict:
    case = {
        "payment_status": "pending",
        "stripe_session_id": SESSION_ID,
        "product_code": "ELIMINAR_COCHE",
        "category": "vehicle_removal",
        "case_type": "vehicle_removal",
        "department": "traffic",
        "status": "vehicle_removal_pending_payment",
        "stripe_payment_intent": None,
    }
    case.update(overrides)
    return case


def _intent(**overrides) -> dict:
    intent = {
        "session_id": SESSION_ID,
        "amount_total": 3900,
        "currency": "EUR",
        "product_code": "ELIMINAR_COCHE",
        "service_code": "vehicle_removal",
        "checkout_contract": "rtm_vehicle_removal_v3",
        "quote_version": "rtm_vehicle_removal_quote_v1",
    }
    intent.update(overrides)
    return intent


def _run(event: dict, connection: _Connection, *, query_params=None):
    env = {
        "STRIPE_SECRET_KEY": "sk_test_synthetic_only",
        "STRIPE_WEBHOOK_SECRET": "whsec_synthetic_only",
    }
    with (
        patch.dict(os.environ, env, clear=False),
        patch.object(billing, "require_http_capability"),
        patch.object(billing, "get_engine", return_value=_Engine(connection)),
        patch.object(billing.stripe.Webhook, "construct_event", return_value=event),
    ):
        return asyncio.run(
            billing.stripe_webhook(_Request(query_params=query_params))
        )


class VehicleRemovalStripeSettlementTest(TestCase):
    def test_happy_path_uses_stored_intent_and_writes_no_pii(self):
        connection = _Connection()

        result = _run(_event(), connection)

        self.assertTrue(result["processed"])
        self.assertEqual(result["case_id"], CASE_ID)
        self.assertEqual(len(connection.updates), 1)
        self.assertEqual(
            connection.updates[0],
            {
                "id": CASE_ID,
                "session_id": SESSION_ID,
                "payment_intent": PAYMENT_INTENT,
            },
        )
        self.assertEqual(len(connection.events), 1)
        event_type, evidence = connection.events[0]
        self.assertEqual(event_type, "vehicle_removal_payment_confirmed")
        self.assertEqual(
            evidence,
            {
                "settlement_reference_sha256": hashlib.sha256(
                    "\x00".join(
                        (EVENT_ID, SESSION_ID, PAYMENT_INTENT)
                    ).encode("utf-8")
                ).hexdigest(),
                "amount_total": 3900,
                "currency": "EUR",
                "service_code": "vehicle_removal",
                "product_code": "ELIMINAR_COCHE",
                "checkout_contract": "rtm_vehicle_removal_v3",
                "quote_version": "rtm_vehicle_removal_quote_v1",
            },
        )
        serialized = json.dumps(evidence).lower()
        for forbidden in (
            "email",
            "phone",
            "dni",
            "plate",
            "full_name",
            EVENT_ID.lower(),
            SESSION_ID.lower(),
            PAYMENT_INTENT.lower(),
        ):
            self.assertNotIn(forbidden, serialized)
        executed_sql = "\n".join(sql for sql, _ in connection.calls)
        self.assertIn("FOR UPDATE", executed_sql)
        self.assertNotIn("classif", executed_sql.lower())
        self.assertNotIn("gestion", executed_sql.lower())

    def test_exact_replay_is_idempotent_and_never_regresses_status(self):
        connection = _Connection(
            case=_pending_case(
                payment_status="paid",
                status="vehicle_removal_assigned",
                stripe_payment_intent=PAYMENT_INTENT,
            )
        )

        result = _run(_event(), connection)

        self.assertTrue(result["replayed"])
        self.assertEqual(connection.updates, [])
        self.assertEqual(connection.events, [])

        mismatched_replay = _Connection(
            case=_pending_case(
                payment_status="paid",
                status="vehicle_removal_paid",
                stripe_payment_intent="pi_original_settlement",
            )
        )
        with self.assertRaises(HTTPException) as rejected:
            _run(_event(), mismatched_replay)
        self.assertEqual(rejected.exception.status_code, 409)
        self.assertEqual(mismatched_replay.updates, [])
        self.assertEqual(mismatched_replay.events, [])

    def test_amount_currency_session_and_case_scope_mismatches_fail_closed(self):
        scenarios = (
            (_event(amount_total=1), _Connection(), "one cent"),
            (_event(amount_total=4900), _Connection(), "wrong legacy amount"),
            (_event(currency="usd"), _Connection(), "currency"),
            (
                _event(),
                _Connection(case=_pending_case(stripe_session_id="cs_test_other_session")),
                "session",
            ),
            (
                _event(),
                _Connection(case=_pending_case(category="traffic")),
                "category",
            ),
            (
                _event(),
                _Connection(case=_pending_case(department="fines")),
                "department",
            ),
            (
                _event(),
                _Connection(intent=_intent(product_code="ELIMINAR_COCHE_FAKE")),
                "stored product",
            ),
        )
        for event, connection, label in scenarios:
            with self.subTest(label=label):
                with self.assertRaises(HTTPException) as rejected:
                    _run(event, connection)
                self.assertEqual(rejected.exception.status_code, 409)
                self.assertEqual(connection.updates, [])
                self.assertEqual(connection.events, [])

    def test_one_cent_and_wrong_legacy_amount_never_reach_settlement_storage(self):
        for amount in (1, 4900):
            with self.subTest(amount=amount):
                connection = _Connection()
                with self.assertRaises(HTTPException) as rejected:
                    _run(_event(amount_total=amount), connection)

                self.assertEqual(rejected.exception.status_code, 409)
                self.assertEqual(connection.calls, [])
                self.assertEqual(connection.updates, [])
                self.assertEqual(connection.events, [])

    def test_canary_extra_metadata_and_lookalike_contracts_never_fall_through(self):
        canary = f"canary-{uuid4()}@example.invalid"
        exact_with_extra = _metadata() | {"customer_email": canary}
        lookalike_without_contract = deepcopy(_metadata())
        lookalike_without_contract.pop("checkout_contract")
        unknown_contract = _metadata() | {"checkout_contract": "rtm_vehicle_removal_v4"}

        for metadata in (
            exact_with_extra,
            lookalike_without_contract,
            unknown_contract,
        ):
            with self.subTest(metadata=metadata):
                connection = _Connection()
                with self.assertRaises(HTTPException) as rejected:
                    _run(_event(metadata=metadata), connection)
                self.assertEqual(rejected.exception.status_code, 400)
                self.assertEqual(connection.calls, [])
                self.assertNotIn(canary, json.dumps(connection.events))

    def test_success_query_never_overrides_unpaid_stripe_session(self):
        connection = _Connection()

        result = _run(
            _event(payment_status="unpaid"),
            connection,
            query_params={
                "success": "1",
                "paid": "true",
                "session_id": SESSION_ID,
            },
        )

        self.assertTrue(result["processed"])
        self.assertEqual(result["payment_status"], "pending")
        self.assertEqual(connection.updates, [])
        self.assertEqual(
            [event_type for event_type, _payload in connection.events],
            ["checkout_async_payment_pending"],
        )

        replay = _run(
            _event(payment_status="unpaid"),
            connection,
            query_params={"success": "1", "paid": "true"},
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(
            [event_type for event_type, _payload in connection.events],
            ["checkout_async_payment_pending"],
        )

    def test_vehicle_async_failure_replay_is_exactly_once_and_never_grants_service(self):
        event = _event()
        event["type"] = "checkout.session.async_payment_failed"
        connection = _Connection()

        first = _run(event, connection)
        replay = _run(event, connection)

        self.assertTrue(first["processed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(connection.case["payment_status"], "failed")
        self.assertEqual(
            connection.case["status"], "payment_reconciliation_required"
        )
        self.assertEqual(
            [event_type for event_type, _payload in connection.events],
            ["checkout_async_payment_failed"],
        )

    def test_vehicle_expiry_releases_only_exact_session_and_replay_is_exactly_once(self):
        event = _event()
        event["type"] = "checkout.session.expired"
        connection = _Connection()

        first = _run(event, connection)
        replay = _run(event, connection)

        self.assertTrue(first["processed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(connection.case["payment_status"], "unpaid")
        self.assertEqual(connection.case["stripe_session_id"], "")
        self.assertEqual(connection.case["status"], "authorization_pending")
        self.assertEqual(
            [event_type for event_type, _payload in connection.events],
            ["vehicle_removal_checkout_session_expired"],
        )

    def test_malformed_uuid_and_stripe_ids_are_rejected_before_database(self):
        scenarios = (
            _event(metadata=_metadata() | {"case_id": "not-a-uuid"}),
            _event(id="not-a-session"),
            _event(payment_intent="not-an-intent"),
        )
        for event in scenarios:
            with self.subTest(event=event):
                connection = _Connection()
                with self.assertRaises(HTTPException) as rejected:
                    _run(event, connection)
                self.assertEqual(rejected.exception.status_code, 400)
                self.assertEqual(connection.calls, [])
