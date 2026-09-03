from __future__ import annotations

import asyncio
from contextlib import contextmanager, ExitStack
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from fastapi import HTTPException

import billing


CASE_ID = "11111111-1111-4111-8111-111111111111"
SESSION_ID = "cs_review_checkout_1"
PAYMENT_INTENT = "pi_review_checkout_1"


class _Result:
    def __init__(self, row=None) -> None:
        self.row = row

    def fetchone(self):
        return self.row


class _CheckoutConnection:
    def __init__(self) -> None:
        self.payment_status = "unpaid"
        self.stripe_session_id = ""
        self.product_code = ""
        self.status = "ready_for_review_payment"
        self.events: list[tuple[str, dict]] = []
        self.statements: list[str] = []

    def execute(self, statement, parameters=None):
        parameters = dict(parameters or {})
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        if (
            "SELECT COALESCE(payment_status, '') AS payment_status" in sql
            and "FROM cases" in sql
        ):
            return _Result(
                (
                    self.payment_status,
                    self.stripe_session_id,
                    self.product_code,
                    self.status,
                )
            )
        if "SET payment_status='creating'" in sql:
            matches = (
                self.payment_status == parameters["expected_payment_status"]
                and self.stripe_session_id == parameters["expected_reference"]
                and self.status == "ready_for_review_payment"
            )
            if matches:
                self.payment_status = "creating"
                self.stripe_session_id = parameters["claim_reference"]
                self.product_code = parameters["product"]
                return _Result((CASE_ID,))
            return _Result()
        if "SET payment_status='pending'" in sql:
            matches = (
                self.payment_status == "creating"
                and self.stripe_session_id == parameters["claim_reference"]
                and self.product_code == parameters["product"]
                and self.status == "ready_for_review_payment"
            )
            if matches:
                self.payment_status = "pending"
                self.stripe_session_id = parameters["session_id"]
                return _Result((CASE_ID,))
            return _Result()
        if "SET payment_status='unpaid'" in sql:
            matches = (
                self.payment_status in {"creating", "pending"}
                and self.stripe_session_id == parameters["expected_reference"]
            )
            if matches:
                self.payment_status = "unpaid"
                self.stripe_session_id = ""
                self.product_code = ""
                return _Result((CASE_ID,))
            return _Result()
        if "INSERT INTO events" in sql:
            self.events.append(
                (parameters["type"], json.loads(parameters["payload"]))
            )
            return _Result(("event-id",))
        raise AssertionError(f"SQL inesperado: {sql}")


class _Engine:
    def __init__(self, connection) -> None:
        self.connection = connection
        self.begin_count = 0

    @contextmanager
    def begin(self):
        self.begin_count += 1
        yield self.connection


def _snapshot():
    return SimpleNamespace(
        authorized=True,
        authorized_at="2026-09-03T00:00:00+00:00",
        contact_email="person@example.test",
        interested_data={"email": "person@example.test"},
        payment_status="unpaid",
    )


def _readiness():
    return SimpleNamespace(
        ready=True,
        version="review-readiness-v1",
        model_dump=lambda mode: {"ready": True, "mode": mode},
    )


def _product():
    return {
        "price_id": "price_review_1",
        "billing_code": "TRAFFIC_REVIEW",
        "service_code": "traffic",
        "payment_stage": "review",
        "amount_cents": 1000,
        "currency": "EUR",
        "authority_version": "review-quote-v1",
    }


def _authority():
    return {
        "material_sha256": "a" * 64,
        "signed_document_attestation": {"material_sha256": "b" * 64},
    }


def _request(**overrides):
    values = {
        "case_id": CASE_ID,
        "payment_stage": "review",
        "product": "attacker-controlled-alias",
        "email": "ignored@example.test",
        "locale": "fr",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _session(session_id: str, metadata: dict, *, status: str = "open"):
    return SimpleNamespace(
        id=session_id,
        url=f"https://checkout.stripe.com/c/pay/{session_id}",
        status=status,
        amount_total=1000,
        currency="eur",
        metadata=metadata,
    )


class CheckoutClaimConcurrencyTest(unittest.TestCase):
    def _patches(self, engine: _Engine):
        return (
            mock.patch.object(billing, "require_case_access_token", return_value=CASE_ID),
            mock.patch.object(billing, "require_http_capability"),
            mock.patch.object(billing, "_env", return_value="sk_test_synthetic"),
            mock.patch.object(
                billing,
                "trusted_frontend_origin",
                return_value="https://www.recurretumulta.eu",
            ),
            mock.patch.object(billing, "get_engine", return_value=engine),
            mock.patch.object(
                billing, "load_case_review_snapshot", return_value=_snapshot()
            ),
            mock.patch.object(
                billing, "build_case_review_readiness", return_value=_readiness()
            ),
            mock.patch.object(billing, "_review_product", return_value=_product()),
            mock.patch.object(
                billing, "verify_signed_case_authority", return_value=_authority()
            ),
        )

    def test_two_overlapping_requests_share_one_idempotent_session(self):
        connection = _CheckoutConnection()
        engine = _Engine(connection)
        calls: list[dict] = []
        nested_result: list[dict] = []

        def create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                nested_result.append(
                    billing.create_checkout(
                        _request(product="different", locale="de", email="other@test"),
                        "case-token",
                    )
                )
            return _session(SESSION_ID, kwargs["metadata"])

        with ExitStack() as stack:
            for patcher in self._patches(engine):
                stack.enter_context(patcher)
            stack.enter_context(
                mock.patch.object(
                    billing.stripe.checkout.Session, "create", side_effect=create
                )
            )
            retrieve = stack.enter_context(
                mock.patch.object(billing.stripe.checkout.Session, "retrieve")
            )
            expire = stack.enter_context(
                mock.patch.object(billing.stripe.checkout.Session, "expire")
            )
            outer_result = billing.create_checkout(_request(), "case-token")

        self.assertEqual(outer_result["url"], nested_result[0]["url"])
        self.assertEqual(connection.payment_status, "pending")
        self.assertEqual(connection.stripe_session_id, SESSION_ID)
        self.assertEqual(calls[0]["idempotency_key"], calls[1]["idempotency_key"])
        self.assertEqual(calls[0]["metadata"], calls[1]["metadata"])
        self.assertNotIn("requested_product", calls[0]["metadata"])
        self.assertEqual(calls[0]["locale"], "es")
        self.assertEqual(calls[0]["payment_method_types"], ["card"])
        self.assertEqual(
            [event for event, _ in connection.events],
            [
                "checkout_creation_claimed",
                "checkout_started",
                "checkout_session_created",
            ],
        )
        retrieve.assert_not_called()
        expire.assert_not_called()

    def test_non_idempotent_provider_loser_is_expired_not_published(self):
        connection = _CheckoutConnection()
        engine = _Engine(connection)
        calls: list[dict] = []
        nested_result: list[dict] = []

        def create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                nested_result.append(
                    billing.create_checkout(_request(), "case-token")
                )
                return _session("cs_losing_remote", kwargs["metadata"])
            return _session("cs_winning_remote", kwargs["metadata"])

        with ExitStack() as stack:
            for patcher in self._patches(engine):
                stack.enter_context(patcher)
            stack.enter_context(
                mock.patch.object(
                    billing.stripe.checkout.Session, "create", side_effect=create
                )
            )
            expire = stack.enter_context(
                mock.patch.object(billing.stripe.checkout.Session, "expire")
            )
            with self.assertRaises(HTTPException) as raised:
                billing.create_checkout(_request(), "case-token")

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(
            connection.stripe_session_id,
            "cs_winning_remote",
        )
        self.assertEqual(nested_result[0]["url"].split("/")[-1], "cs_winning_remote")
        self.assertEqual(calls[0]["idempotency_key"], calls[1]["idempotency_key"])
        expire.assert_called_once_with("cs_losing_remote")

    def test_retry_after_provider_timeout_recovers_same_claim_and_key(self):
        connection = _CheckoutConnection()
        engine = _Engine(connection)
        calls: list[dict] = []

        def create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise TimeoutError("private provider timeout")
            return _session(SESSION_ID, kwargs["metadata"])

        with ExitStack() as stack:
            for patcher in self._patches(engine):
                stack.enter_context(patcher)
            stack.enter_context(
                mock.patch.object(
                    billing.stripe.checkout.Session, "create", side_effect=create
                )
            )
            with self.assertRaises(HTTPException) as first:
                billing.create_checkout(_request(), "case-token")
            recovered = billing.create_checkout(_request(), "case-token")

        self.assertEqual(first.exception.status_code, 502)
        self.assertNotIn("private provider", str(first.exception.detail))
        self.assertEqual(calls[0]["idempotency_key"], calls[1]["idempotency_key"])
        self.assertEqual(recovered["url"].split("/")[-1], SESSION_ID)
        self.assertEqual(connection.payment_status, "pending")

    def test_terminal_case_cannot_claim_or_open_a_checkout(self):
        connection = _CheckoutConnection()
        connection.status = "submitted"
        engine = _Engine(connection)

        with ExitStack() as stack:
            for patcher in self._patches(engine):
                stack.enter_context(patcher)
            create = stack.enter_context(
                mock.patch.object(billing.stripe.checkout.Session, "create")
            )
            with self.assertRaises(HTTPException) as raised:
                billing.create_checkout(_request(), "case-token")

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(connection.payment_status, "unpaid")
        self.assertEqual(connection.stripe_session_id, "")
        self.assertEqual(connection.events, [])
        create.assert_not_called()


class _WebhookConnection:
    def __init__(self) -> None:
        self.payment_status = "pending"
        self.stripe_session_id = SESSION_ID
        self.product_code = "TRAFFIC_REVIEW"
        self.status = "ready_for_review_payment"
        self.payment_intent = ""
        self.intent = {
            "session": SESSION_ID,
            "payment_stage": "review",
            "billing_code": "TRAFFIC_REVIEW",
            "authoritative_service_code": "traffic",
            "authority_version": "review-quote-v1",
            "amount_cents": 1000,
            "stripe_amount_total": 1000,
            "currency": "EUR",
            "authority_material_sha256": "a" * 64,
            "signed_document_attestation_sha256": "b" * 64,
        }
        self.events: list[str] = []
        self.event_payloads: list[dict] = []

    def execute(self, statement, parameters=None):
        parameters = dict(parameters or {})
        sql = " ".join(str(statement).split())
        if "SELECT payment_status, stripe_session_id, product_code" in sql:
            return _Result(
                (
                    self.payment_status,
                    self.stripe_session_id,
                    self.product_code,
                    self.payment_intent,
                    self.status,
                )
            )
        if "type='checkout_session_created'" in sql:
            return _Result((self.intent,))
        if (
            "checkout_async_payment_pending" in sql
            and "payload->>'stripe_event_id'" in sql
        ):
            event_id = parameters["stripe_event_id"]
            return _Result(
                (1,)
                if any(
                    payload.get("stripe_event_id") == event_id
                    for payload in self.event_payloads
                )
                else None
            )
        if "SET payment_status='paid'" in sql:
            if (
                self.stripe_session_id == parameters["sid"]
                and self.payment_status == "pending"
                and self.status == "ready_for_review_payment"
            ):
                self.payment_status = "paid"
                self.payment_intent = parameters["pi"]
                self.status = "manual_review"
                return _Result((CASE_ID,))
            return _Result()
        if (
            "SELECT COALESCE(payment_status, '') AS payment_status" in sql
            and "WHERE id=:id" in sql
        ):
            return _Result(
                (self.payment_status, self.stripe_session_id, self.status)
            )
        if "SET payment_status='failed'" in sql:
            if self.payment_status == "pending":
                self.payment_status = "failed"
                self.status = parameters["next_status"]
                return _Result((CASE_ID,))
            return _Result()
        if "SET payment_status='unpaid'" in sql:
            if (
                self.payment_status in {"creating", "pending"}
                and self.stripe_session_id == parameters["expected_reference"]
            ):
                self.payment_status = "unpaid"
                self.stripe_session_id = ""
                self.product_code = ""
                return _Result((CASE_ID,))
            return _Result()
        if "INSERT INTO events" in sql:
            self.events.append(parameters["type"])
            self.event_payloads.append(json.loads(parameters["payload"]))
            return _Result(("event-id",))
        raise AssertionError(f"SQL inesperado: {sql}")


class _WebhookRequest:
    def __init__(self, chunks=(b"{}",)) -> None:
        self.headers = {"stripe-signature": "synthetic-signature"}
        self.chunks = chunks

    async def stream(self):
        for chunk in self.chunks:
            yield chunk


def _checkout_event(event_type: str, *, payment_status: str):
    return {
        "id": "evt_review_checkout_1",
        "type": event_type,
        "data": {
            "object": {
                "id": SESSION_ID,
                "mode": "payment",
                "status": "complete",
                "payment_status": payment_status,
                "payment_intent": PAYMENT_INTENT,
                "amount_total": 1000,
                "currency": "eur",
                "metadata": {
                    "case_id": CASE_ID,
                    "service_code": "traffic",
                    "billing_code": "TRAFFIC_REVIEW",
                    "payment_stage": "review",
                    "authority_version": "review-quote-v1",
                    "amount_cents": "1000",
                    "currency": "EUR",
                    "authority_material_sha256": "a" * 64,
                    "signed_document_attestation_sha256": "b" * 64,
                },
            }
        },
    }


class StripeWebhookStateMachineTest(unittest.TestCase):
    def _run_event(self, event, connection: _WebhookConnection):
        with (
            mock.patch.object(billing, "require_http_capability"),
            mock.patch.object(billing, "_env", return_value="secret"),
            mock.patch.object(
                billing.stripe.Webhook, "construct_event", return_value=event
            ),
            mock.patch.object(billing, "get_engine", return_value=_Engine(connection)),
            mock.patch.object(
                billing, "verify_signed_case_authority", return_value=_authority()
            ),
        ):
            return asyncio.run(billing.stripe_webhook(_WebhookRequest()))

    def test_oversize_body_is_rejected_before_signature_parser(self):
        request = _WebhookRequest(
            chunks=(b"a", b"b" * billing._MAX_STRIPE_WEBHOOK_BYTES)
        )
        with (
            mock.patch.object(billing, "require_http_capability"),
            mock.patch.object(billing.stripe.Webhook, "construct_event") as construct,
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(billing.stripe_webhook(request))

        self.assertEqual(raised.exception.status_code, 413)
        construct.assert_not_called()

    def test_async_success_uses_same_bound_settlement_path(self):
        connection = _WebhookConnection()
        result = self._run_event(
            _checkout_event(
                "checkout.session.async_payment_succeeded",
                payment_status="paid",
            ),
            connection,
        )

        self.assertTrue(result["processed"])
        self.assertEqual(connection.payment_status, "paid")
        self.assertEqual(connection.payment_intent, PAYMENT_INTENT)
        self.assertEqual(connection.status, "manual_review")

    def test_completed_processing_does_not_grant_service(self):
        connection = _WebhookConnection()
        result = self._run_event(
            _checkout_event("checkout.session.completed", payment_status="unpaid"),
            connection,
        )

        self.assertTrue(result["processed"])
        self.assertEqual(connection.payment_status, "pending")
        self.assertEqual(connection.status, "ready_for_review_payment")
        self.assertIn("checkout_async_payment_pending", connection.events)

    def test_exact_unsettled_replay_is_acknowledged_without_duplicate_event(self):
        connection = _WebhookConnection()
        event = _checkout_event(
            "checkout.session.completed", payment_status="unpaid"
        )

        first = self._run_event(event, connection)
        replay = self._run_event(event, connection)

        self.assertTrue(first["processed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(
            connection.events.count("checkout_async_payment_pending"), 1
        )

    def test_async_failure_freezes_case_without_granting_service(self):
        connection = _WebhookConnection()
        result = self._run_event(
            _checkout_event(
                "checkout.session.async_payment_failed",
                payment_status="unpaid",
            ),
            connection,
        )

        self.assertTrue(result["processed"])
        self.assertEqual(connection.payment_status, "failed")
        self.assertEqual(connection.status, "payment_reconciliation_required")
        self.assertNotEqual(connection.payment_status, "paid")

        replay = self._run_event(
            _checkout_event(
                "checkout.session.async_payment_failed",
                payment_status="unpaid",
            ),
            connection,
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(connection.events.count("checkout_async_payment_failed"), 1)

    def test_late_success_cannot_reopen_refunded_entitlement(self):
        connection = _WebhookConnection()
        connection.payment_status = "refunded"
        connection.payment_intent = PAYMENT_INTENT
        connection.status = "payment_reconciliation_required"

        result = self._run_event(
            _checkout_event(
                "checkout.session.async_payment_succeeded",
                payment_status="paid",
            ),
            connection,
        )

        self.assertTrue(result["reconciled"])
        self.assertEqual(connection.payment_status, "refunded")
        self.assertEqual(connection.status, "payment_reconciliation_required")

    def test_paid_webhook_cannot_resurrect_a_case_changed_after_checkout(self):
        connection = _WebhookConnection()
        connection.status = "submitted"

        with self.assertRaises(HTTPException) as raised:
            self._run_event(
                _checkout_event(
                    "checkout.session.async_payment_succeeded",
                    payment_status="paid",
                ),
                connection,
            )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(connection.payment_status, "pending")
        self.assertEqual(connection.status, "submitted")
        self.assertNotIn("paid_ok", connection.events)

    def test_expired_event_clears_only_the_exact_pending_session(self):
        connection = _WebhookConnection()
        result = self._run_event(
            _checkout_event("checkout.session.expired", payment_status="unpaid"),
            connection,
        )

        self.assertTrue(result["processed"])
        self.assertEqual(connection.payment_status, "unpaid")
        self.assertEqual(connection.stripe_session_id, "")

        replay = self._run_event(
            _checkout_event("checkout.session.expired", payment_status="unpaid"),
            connection,
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(connection.events.count("checkout_session_expired"), 1)


class _ReversalConnection:
    def __init__(self) -> None:
        self.payment_status = "paid"
        self.status = "manual_review"
        self.events: list[str] = []
        self.event_payloads: list[dict] = []

    def execute(self, statement, parameters=None):
        parameters = dict(parameters or {})
        sql = " ".join(str(statement).split())
        if sql.startswith("SELECT id, COALESCE(payment_status"):
            return _Result((CASE_ID, self.payment_status, self.status))
        if "type='payment_entitlement_suspended'" in sql:
            event_id = parameters["stripe_event_id"]
            return _Result(
                (1,)
                if any(
                    payload.get("stripe_event_id") == event_id
                    for payload in self.event_payloads
                )
                else None
            )
        if "SET payment_status=:target_payment_status" in sql:
            if (
                self.payment_status == parameters["expected_payment_status"]
                and self.status == parameters["expected_status"]
            ):
                self.payment_status = parameters["target_payment_status"]
                self.status = parameters["next_status"]
                return _Result((CASE_ID,))
            return _Result()
        if "INSERT INTO events" in sql:
            self.events.append(parameters["type"])
            self.event_payloads.append(json.loads(parameters["payload"]))
            return _Result(("event-id",))
        raise AssertionError(f"SQL inesperado: {sql}")


class PaymentReversalWebhookTest(unittest.TestCase):
    def test_vehicle_refund_and_exact_replay_suspend_entitlement_once(self):
        connection = _ReversalConnection()
        connection.status = "vehicle_removal_paid"
        event = {
            "id": "evt_vehicle_refund_1",
            "type": "charge.refunded",
            "data": {"object": {"payment_intent": PAYMENT_INTENT}},
        }
        checkout_session = SimpleNamespace(
            id=SESSION_ID,
            metadata={
                "case_id": CASE_ID,
                "service_code": "vehicle_removal",
                "product_code": "ELIMINAR_COCHE",
                "checkout_contract": "rtm_vehicle_removal_v3",
                "amount_cents": "3900",
                "currency": "EUR",
                "quote_version": "rtm_vehicle_removal_quote_v1",
            },
        )

        for expected_replay in (False, True):
            with (
                mock.patch.object(billing, "require_http_capability"),
                mock.patch.object(billing, "_env", return_value="secret"),
                mock.patch.object(
                    billing.stripe.Webhook, "construct_event", return_value=event
                ),
                mock.patch.object(
                    billing.stripe.checkout.Session,
                    "list",
                    return_value=SimpleNamespace(data=[checkout_session]),
                ),
                mock.patch.object(
                    billing, "get_engine", return_value=_Engine(connection)
                ),
            ):
                result = asyncio.run(billing.stripe_webhook(_WebhookRequest()))

            self.assertEqual(bool(result.get("replayed")), expected_replay)

        self.assertEqual(connection.payment_status, "refunded")
        self.assertEqual(connection.status, "payment_reconciliation_required")
        self.assertEqual(connection.events, ["payment_entitlement_suspended"])

    def test_refund_suspends_paid_entitlement_and_workflow(self):
        connection = _ReversalConnection()
        event = {
            "id": "evt_refund_1",
            "type": "charge.refunded",
            "data": {"object": {"payment_intent": PAYMENT_INTENT}},
        }
        with (
            mock.patch.object(billing, "require_http_capability"),
            mock.patch.object(billing, "_env", return_value="secret"),
            mock.patch.object(
                billing.stripe.Webhook, "construct_event", return_value=event
            ),
            mock.patch.object(
                billing.stripe.checkout.Session,
                "list",
                return_value=SimpleNamespace(
                    data=[
                        SimpleNamespace(
                            id=SESSION_ID,
                            metadata={"case_id": CASE_ID},
                        )
                    ]
                ),
            ),
            mock.patch.object(billing, "get_engine", return_value=_Engine(connection)),
        ):
            result = asyncio.run(billing.stripe_webhook(_WebhookRequest()))

        self.assertTrue(result["processed"])
        self.assertEqual(connection.payment_status, "refunded")
        self.assertEqual(connection.status, "payment_reconciliation_required")
        self.assertEqual(connection.events, ["payment_entitlement_suspended"])

        with (
            mock.patch.object(billing, "require_http_capability"),
            mock.patch.object(billing, "_env", return_value="secret"),
            mock.patch.object(
                billing.stripe.Webhook, "construct_event", return_value=event
            ),
            mock.patch.object(
                billing.stripe.checkout.Session,
                "list",
                return_value=SimpleNamespace(
                    data=[
                        SimpleNamespace(
                            id=SESSION_ID,
                            metadata={"case_id": CASE_ID},
                        )
                    ]
                ),
            ),
            mock.patch.object(billing, "get_engine", return_value=_Engine(connection)),
        ):
            replay = asyncio.run(billing.stripe_webhook(_WebhookRequest()))

        self.assertTrue(replay["replayed"])
        self.assertEqual(connection.events, ["payment_entitlement_suspended"])

    def test_refund_arriving_before_settlement_is_persisted_fail_closed(self):
        connection = _ReversalConnection()
        connection.payment_status = "pending"
        event = {
            "id": "evt_refund_before_settlement",
            "type": "charge.refunded",
            "data": {"object": {"payment_intent": PAYMENT_INTENT}},
        }
        checkout_session = SimpleNamespace(
            id=SESSION_ID,
            metadata={"case_id": CASE_ID},
        )
        with (
            mock.patch.object(billing, "require_http_capability"),
            mock.patch.object(billing, "_env", return_value="secret"),
            mock.patch.object(
                billing.stripe.Webhook, "construct_event", return_value=event
            ),
            mock.patch.object(
                billing.stripe.checkout.Session,
                "list",
                return_value=SimpleNamespace(data=[checkout_session]),
            ),
            mock.patch.object(billing, "get_engine", return_value=_Engine(connection)),
        ):
            result = asyncio.run(billing.stripe_webhook(_WebhookRequest()))

        self.assertTrue(result["processed"])
        self.assertEqual(connection.payment_status, "refunded")
        self.assertEqual(connection.status, "payment_reconciliation_required")


if __name__ == "__main__":
    unittest.main()
