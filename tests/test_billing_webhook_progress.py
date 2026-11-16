"""Synthetic payment receipts only: no Stripe, AI, mail or production DB calls."""
import asyncio
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

import billing

SECRET = "whsec_synthetic_billing_progress"
ENV = {"STRIPE_SECRET_KEY": "sk_test_synthetic_billing_progress", "STRIPE_WEBHOOK_SECRET": SECRET}


def signed(event):
    body = json.dumps(event).encode()
    timestamp = str(int(time.time()))
    digest = hmac.new(SECRET.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return body, {"stripe-signature": f"t={timestamp},v1={digest}"}


class SqliteAdapter:
    """Real transactions, with the small PostgreSQL syntax differences adapted."""
    def __init__(self, engine):
        self.engine = engine
        self.lock_timeouts = 0

    @contextmanager
    def begin(self):
        with self.engine.begin() as conn:
            adapter = self

            class Connection:
                def execute(self, statement, params=None):
                    sql = str(statement)
                    if sql == "SET LOCAL lock_timeout = '5s'":
                        adapter.lock_timeouts += 1
                        return None
                    sql = sql.replace(" FOR UPDATE", "").replace("NOW()", "CURRENT_TIMESTAMP")
                    sql = sql.replace("CAST(:payload AS JSONB)", ":payload")
                    return conn.execute(text(sql), params or {})

            yield Connection()


class BillingWebhookProgressTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.case = str(uuid4())
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE cases(id TEXT PRIMARY KEY,payment_status TEXT,paid_at TEXT,"
                              "stripe_session_id TEXT,stripe_payment_intent TEXT,status TEXT,updated_at TEXT)"))
            conn.execute(text("CREATE TABLE events(case_id TEXT,type TEXT,payload TEXT,created_at TEXT)"))
            conn.execute(text("INSERT INTO cases(id,payment_status,status) VALUES (:id,'pending','uploaded')"), {"id": self.case})
        self.adapter = SqliteAdapter(self.engine)
        self.event = {"id": "evt_synthetic", "type": "checkout.session.completed", "data": {"object": {
            "id": "cs_synthetic", "object": "checkout.session", "payment_status": "paid", "payment_intent": "pi_synthetic",
            "amount_total": 1000, "metadata": {"case_id": self.case}}}}
        self.patches = [patch.dict(os.environ, ENV), patch.object(billing, "get_engine", return_value=self.adapter),
                        patch.object(billing, "mark_invoice_paid"), patch.object(billing, "run_expediente_ai",
                        side_effect=AssertionError("AI must never run inside the payment webhook"))]
        self.mocks = [p.start() for p in self.patches]
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])
        self.addCleanup(self.engine.dispose)
        app = FastAPI()
        app.include_router(billing.router)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def post(self):
        body, headers = signed(self.event)
        return self.client.post("/billing/webhook", content=body, headers=headers)

    def state(self):
        with self.engine.connect() as conn:
            return dict(conn.execute(text("SELECT * FROM cases WHERE id=:id"), {"id": self.case}).mappings().one())

    def event_types(self):
        with self.engine.connect() as conn:
            return list(conn.execute(text("SELECT type FROM events")).scalars())

    def test_receipt_commits_and_activates_ops_without_ai(self):
        self.assertEqual(self.post().status_code, 200)
        state = self.state()
        self.assertEqual((state["payment_status"], state["status"]), ("paid", "manual_review"))
        self.assertEqual((state["stripe_session_id"], state["stripe_payment_intent"]), ("cs_synthetic", "pi_synthetic"))
        self.assertTrue(state["paid_at"])
        self.assertEqual(self.event_types(), ["paid_ok", "case_activated_after_payment", "post_payment_analysis_deferred"])
        self.mocks[3].assert_not_called()
        self.assertEqual(self.adapter.lock_timeouts, 1)

    def test_replay_does_not_duplicate_receipt_or_reset_work(self):
        self.assertEqual(self.post().status_code, 200)
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE cases SET status='in_progress'"))
        self.assertEqual(self.post().json(), {"ok": True, "already_recorded": True})
        self.assertEqual(self.state()["status"], "in_progress")
        self.assertEqual(len(self.event_types()), 3)
        self.assertEqual(self.mocks[2].call_count, 1)

    def test_different_receipt_does_not_replace_prior_payment(self):
        self.assertEqual(self.post().status_code, 200)
        self.event["data"]["object"]["id"] = "cs_different_synthetic"
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(self.state()["stripe_session_id"], "cs_synthetic")

    def test_failed_database_work_rolls_back_for_stripe_retry(self):
        self.mocks[2].side_effect = RuntimeError("synthetic invoice storage failure")
        self.assertEqual(self.post().status_code, 500)
        self.assertEqual(self.state()["payment_status"], "pending")
        self.assertIsNone(self.state()["paid_at"])
        self.assertEqual(self.event_types(), [])

    def test_unpaid_session_does_not_confirm_payment(self):
        self.event["data"]["object"]["payment_status"] = "unpaid"
        self.assertEqual(self.post().json(), {"ok": True, "payment_confirmation_pending": True})
        self.assertEqual(self.state()["payment_status"], "pending")
        self.assertEqual(self.event_types(), [])

    def test_balance_event_remains_owned_by_balance_handler(self):
        self.event["data"]["object"]["metadata"]["contract"] = "rtm_management_balance_v1"
        self.assertEqual(self.post().json()["handled_by"], "management_balance_webhook")
        self.assertEqual(self.state()["payment_status"], "pending")

    def test_invalid_signature_never_reaches_payment_storage(self):
        body, _ = signed(self.event)
        response = self.client.post("/billing/webhook", content=body, headers={"stripe-signature": "invalid"})
        self.assertEqual(response.status_code, 400)
        self.mocks[1].assert_not_called()

    def test_async_payment_success_uses_same_confirmation(self):
        self.event["type"] = "checkout.session.async_payment_succeeded"
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.state()["payment_status"], "paid")


class WebhookResponsivenessTest(unittest.IsolatedAsyncioTestCase):
    async def test_waiting_payment_storage_does_not_block_other_requests(self):
        started, release = threading.Event(), threading.Event()

        def waiting_handler(event):
            started.set()
            if not release.wait(3):
                raise RuntimeError("synthetic test timeout")
            return {"ok": True}

        app = FastAPI()
        app.include_router(billing.router)

        @app.get("/probe")
        async def probe():
            return {"ok": True}

        body, headers = signed({"id": "evt_synthetic", "type": "probe", "data": {"object": {}}})
        with patch.dict(os.environ, ENV), patch.object(billing, "_handle_verified_stripe_event", side_effect=waiting_handler):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
                payment = asyncio.create_task(client.post("/billing/webhook", content=body, headers=headers))
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 1))
                    response = await asyncio.wait_for(client.get("/probe"), timeout=0.5)
                    self.assertEqual(response.json(), {"ok": True})
                    self.assertFalse(payment.done())
                finally:
                    release.set()
                    response = await payment
                self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
