"""Real transactions on a uniquely owned schema in the disposable CI database."""
from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
import re
import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from starlette.datastructures import Headers, UploadFile
from starlette.requests import Request

import cases
from rtm_core import staging_rehearsal as policy


@unittest.skipUnless(os.getenv("RTM_CORE_INTEGRATION_DB") == "1" and os.getenv("DATABASE_URL"), "Requires disposable CI PostgreSQL")
class StagingRehearsalPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["DATABASE_URL"])
        if url.host not in {"localhost", "127.0.0.1", "postgres"} or not str(url.database).endswith("_test"):
            raise unittest.SkipTest("Only a disposable local test database is permitted")
        cls.schema = "rtm_rehearsal_test_" + uuid.uuid4().hex
        cls.admin = create_engine(url)
        with cls.admin.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {cls.schema}"))
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema})
        with cls.engine.begin() as conn:
            conn.execute(text("""CREATE TABLE cases (
                id UUID PRIMARY KEY, contact_email TEXT, contact_name TEXT,
                status TEXT, payment_status TEXT, authorized BOOLEAN, interested_data JSONB,
                department TEXT, case_type TEXT, customer_comment TEXT, source_module TEXT,
                category TEXT, test_mode BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
            )"""))
            conn.execute(text("""CREATE TABLE documents (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(), case_id UUID REFERENCES cases(id),
                kind TEXT, b2_bucket TEXT, b2_key TEXT, mime TEXT, size_bytes BIGINT, sha256 TEXT,
                created_at TIMESTAMPTZ
            )"""))
            conn.execute(text("""CREATE TABLE events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(), case_id UUID REFERENCES cases(id),
                type TEXT, payload JSONB, created_at TIMESTAMPTZ
            )"""))

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        assert re.fullmatch("rtm_rehearsal_test_[0-9a-f]{32}", cls.schema)
        with cls.admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {cls.schema} CASCADE"))
        cls.admin.dispose()

    def setUp(self):
        self.grant = policy.RehearsalGrant(str(uuid.uuid4()))
        self.enterContext(patch.object(cases, "get_engine", return_value=self.engine))
        self.enterContext(patch.object(policy, "get_engine", return_value=self.engine))
        self.enterContext(patch.object(policy, "require_supervisor", return_value=self.grant))
        self.enterContext(patch.object(cases, "require_public_case_access_configured"))
        self.enterContext(patch.object(cases, "require_http_capability"))
        self.enterContext(patch.object(cases, "local_operator_auth_requested", return_value=False))
        self.enterContext(patch.object(cases, "issue_case_access_token", return_value="test-access"))
        self.enterContext(patch("public_case_access.issue_case_access_token", return_value="test-access"))
        self.storage = self.enterContext(patch.object(cases, "upload_bytes", side_effect=lambda *args: ("fixture-only", uuid.uuid4().hex)))
        self.cleanup = self.enterContext(patch.object(cases, "_cleanup_b2_objects"))

    def run_intake(self):
        request = Request({"type": "http"})
        request.state.rtm_rehearsal_grant = self.grant
        kwargs = dict(policy.FORM_FIELDS)
        kwargs.update(representation_confirmed=True, privacy_accepted=True, prejudicial_counsel_requested=False)
        for field, kind in (("dni_front", "identity_front"), ("dni_back", "identity_back")):
            filename, content = policy.fixture(kind)
            kwargs[field] = UploadFile(io.BytesIO(content), filename=filename, headers=Headers({"content-type": "application/pdf"}))
        return asyncio.run(cases.create_rtm_intake_draft(request=request, **kwargs))

    def test_concurrent_and_lost_response_retries_have_one_case_and_two_identity_documents(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.run_intake(), range(2)))
        self.assertEqual({result["case_id"] for result in results}, {self.grant.case_id})
        count = self.storage.call_count
        self.assertTrue(self.run_intake()["recovered"])
        self.assertEqual(self.storage.call_count, count)
        with self.engine.begin() as conn:
            self.assertTrue(policy.existing_case(self.grant, conn))
            row = conn.execute(text("SELECT test_mode, authorized, payment_status FROM cases WHERE id=:id"), {"id": self.grant.case_id}).one()
            self.assertEqual(tuple(row), (True, False, None))
            self.assertEqual(conn.execute(text("SELECT count(*) FROM documents WHERE case_id=:id"), {"id": self.grant.case_id}).scalar_one(), 2)
            self.assertEqual(conn.execute(text("SELECT count(*) FROM events WHERE case_id=:id"), {"id": self.grant.case_id}).scalar_one(), 3)
        if count == 4:
            self.assertEqual(len(self.cleanup.call_args.args[0]), 2)

    def test_final_event_failure_rolls_back_case_documents_and_events_and_compensates_storage(self):
        original = cases._event_on_conn
        def fail_final(conn, case_id, kind, payload):
            if kind == "rtm_identity_documents_saved":
                raise RuntimeError("synthetic final write failure")
            return original(conn, case_id, kind, payload)
        with patch.object(cases, "_event_on_conn", side_effect=fail_final):
            with self.assertRaises(Exception): self.run_intake()
        with self.engine.begin() as conn:
            for table in ("cases", "documents", "events"):
                key = "id" if table == "cases" else "case_id"
                self.assertEqual(conn.execute(text(f"SELECT count(*) FROM {table} WHERE {key}=:id"), {"id": self.grant.case_id}).scalar_one(), 0)
        self.assertEqual(len(self.cleanup.call_args.args[0]), 2)

    def test_colliding_unmarked_case_is_not_reclassified_or_written(self):
        self.run_intake()
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE cases SET test_mode=FALSE WHERE id=:id"), {"id": self.grant.case_id})
        count = self.storage.call_count
        with self.assertRaises(Exception): self.run_intake()
        self.assertEqual(self.storage.call_count, count)
        with self.engine.begin() as conn:
            self.assertFalse(conn.execute(text("SELECT test_mode FROM cases WHERE id=:id"), {"id": self.grant.case_id}).scalar_one())
