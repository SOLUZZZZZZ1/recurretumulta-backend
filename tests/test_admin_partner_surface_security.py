from __future__ import annotations

import inspect
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, Response

import app as backend_app
import partner
from rtm_core.http_security import DEFAULT_SENSITIVE_RATE_RULES


ROOT = Path(__file__).resolve().parents[1]


class _Result:
    def __init__(self, row=None, *, rowcount: int = 0):
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row


class _SequenceConnection:
    def __init__(self, *results: _Result):
        self.results = list(results)
        self.calls = []

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters or {}))
        if not self.results:
            raise AssertionError("Ejecución SQL inesperada")
        return self.results.pop(0)


class _Engine:
    def __init__(self, connection: _SequenceConnection):
        self.connection = connection

    @contextmanager
    def begin(self):
        yield self.connection


class AdminAndPartnerSurfaceSecurityTest(unittest.TestCase):
    def test_all_http_migration_routers_are_unmounted(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        for router_name in (
            "admin_migrate_router",
            "admin_payments_router",
            "rtm_core_migration_router",
            "rtm_core_document_extraction_migration_router",
        ):
            with self.subTest(router=router_name):
                self.assertNotIn(router_name, source)

        mounted_paths = {
            str(getattr(route, "path", "")) for route in backend_app.app.routes
        }
        self.assertFalse(
            {path for path in mounted_paths if path.startswith("/admin/migrate")}
        )

    def test_partner_admin_no_longer_accepts_shared_admin_token(self):
        source = inspect.getsource(partner.admin_create_partner)
        signature = inspect.signature(partner.admin_create_partner)
        self.assertNotIn("x_admin_token", signature.parameters)
        self.assertNotIn("ADMIN_TOKEN", source)
        self.assertIn("require_partner_admin_supervisor", repr(signature))
        self.assertIn(
            "require_recent_supervisor_context",
            inspect.getsource(partner.require_partner_admin_supervisor),
        )

    def test_sensitive_partner_mutations_are_rate_limited(self):
        for path in (
            "/partner/admin-create",
            "/partner/change-password",
            "/partner/login",
            "/partner/logout",
            "/partner/cases",
        ):
            with self.subTest(path=path):
                self.assertIn(("POST", path), DEFAULT_SENSITIVE_RATE_RULES)
        self.assertIn(("GET", "/partner/session"), DEFAULT_SENSITIVE_RATE_RULES)

    def test_public_generic_authorization_template_is_not_mounted(self):
        mounted_paths = {
            str(getattr(route, "path", "")) for route in backend_app.app.routes
        }
        self.assertNotIn(
            "/partner/authorization-template-pdf",
            mounted_paths,
        )
        source = inspect.getsource(partner)
        self.assertNotIn("authorization-template-pdf", source)
        self.assertNotIn("templates/firma.png", source)

    def test_temporary_password_login_never_issues_a_session(self):
        row = (
            "11111111-1111-4111-8111-111111111111",
            "Partner",
            "partner@example.com",
            "",
            "temporary-hash",
            True,
            True,
        )
        connection = _SequenceConnection(_Result(row), _Result())
        response = Response()
        with (
            patch.object(partner, "get_engine", return_value=_Engine(connection)),
            patch.object(partner, "_verify_password", return_value=(True, False)),
        ):
            result = partner.partner_login(
                partner.PartnerLoginIn(
                    email="partner@example.com",
                    password="temporary-password",
                ),
                response,
            )

        self.assertTrue(result["must_change_password"])
        self.assertFalse(result["token_returned"])
        self.assertNotIn("token", result)
        self.assertIn("api_token=NULL", connection.calls[1][0])

    def test_initial_password_change_is_atomic_and_revokes_sessions(self):
        row = (
            "11111111-1111-4111-8111-111111111111",
            "",
            "temporary-hash",
            True,
            True,
        )
        connection = _SequenceConnection(
            _Result(row),
            _Result(rowcount=1),
        )
        with (
            patch.object(partner, "get_engine", return_value=_Engine(connection)),
            patch.object(partner, "_verify_password", return_value=(True, False)),
            patch.object(partner, "_modern_password_hash", return_value="new-hash"),
        ):
            result = partner.partner_change_initial_password(
                partner.PartnerChangePasswordIn(
                    email="partner@example.com",
                    old_password="temporary-password",
                    new_password="a-new-strong-password",
                ),
                Response(),
            )

        update_sql, update_params = connection.calls[1]
        self.assertIn("must_change_password=FALSE", update_sql)
        self.assertIn("api_token=NULL", update_sql)
        self.assertIn("AND must_change_password=TRUE", update_sql)
        self.assertEqual(update_params["expected_hash"], "temporary-hash")
        self.assertTrue(result["login_required"])

    def test_initial_password_change_cannot_be_consumed_twice(self):
        row = (
            "11111111-1111-4111-8111-111111111111",
            "",
            "temporary-hash",
            True,
            True,
        )
        connection = _SequenceConnection(
            _Result(row),
            _Result(rowcount=0),
        )
        with (
            patch.object(partner, "get_engine", return_value=_Engine(connection)),
            patch.object(partner, "_verify_password", return_value=(True, False)),
            patch.object(partner, "_modern_password_hash", return_value="new-hash"),
            self.assertRaises(HTTPException) as raised,
        ):
            partner.partner_change_initial_password(
                partner.PartnerChangePasswordIn(
                    email="partner@example.com",
                    old_password="temporary-password",
                    new_password="a-new-strong-password",
                ),
                Response(),
            )

        self.assertEqual(getattr(raised.exception, "status_code", None), 409)

    def test_normal_login_rotates_digest_and_sets_secure_cookie(self):
        row = (
            "11111111-1111-4111-8111-111111111111",
            "Partner",
            "partner@example.com",
            "",
            "current-hash",
            True,
            False,
        )
        connection = _SequenceConnection(_Result(row), _Result(rowcount=1))
        response = Response()
        with (
            patch.object(partner, "get_engine", return_value=_Engine(connection)),
            patch.object(partner, "_verify_password", return_value=(True, False)),
        ):
            result = partner.partner_login(
                partner.PartnerLoginIn(
                    email="partner@example.com",
                    password="current-strong-password",
                ),
                response,
            )

        self.assertNotIn("token", result)
        self.assertNotIn("csrf_token", result)
        self.assertFalse(result["token_returned"])
        session_cookie = next(
            value
            for value in response.headers.getlist("set-cookie")
            if partner._PARTNER_SESSION_COOKIE in value
        )
        raw_session_token = session_cookie.split(";", 1)[0].split("=", 1)[1]
        self.assertTrue(raw_session_token.startswith("ps1."))
        self.assertEqual(
            connection.calls[1][1]["t"],
            partner._stored_token(raw_session_token),
        )
        self.assertIn("HttpOnly", session_cookie)
        self.assertIn("Secure", session_cookie)

    def test_logout_revokes_the_exact_session_digest(self):
        token = partner._make_token()
        row = (
            "11111111-1111-4111-8111-111111111111",
            "Partner",
            "partner@example.com",
            True,
        )
        connection = _SequenceConnection(_Result(row), _Result(rowcount=1))
        with patch.object(partner, "get_engine", return_value=_Engine(connection)):
            result = partner.partner_logout(
                Response(),
                authorization=f"Bearer {token}",
                rtm_partner_session=None,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(
            connection.calls[1][1]["digest"],
            partner._stored_token(token),
        )
        self.assertIn("api_token=NULL", connection.calls[1][0])

    def test_partner_admin_creates_only_a_restricted_temporary_credential(self):
        connection = _SequenceConnection(
            _Result(None),
            _Result(("11111111-1111-4111-8111-111111111111",)),
        )
        context = SimpleNamespace(
            session=SimpleNamespace(
                role_code="rtm.supervisor",
                operator_id="22222222-2222-4222-8222-222222222222",
                session_id="33333333-3333-4333-8333-333333333333",
            ),
            config=SimpleNamespace(
                auth=SimpleNamespace(
                    hmac_key="h" * 32,
                    trust_proxy_headers=False,
                    trusted_proxy_cidrs=(),
                    evidence_retention_days=180,
                )
            ),
        )
        request = SimpleNamespace(headers={}, client=None)
        with (
            patch.object(partner, "get_engine", return_value=_Engine(connection)),
            patch.object(partner, "_modern_password_hash", return_value="argon-hash"),
            patch.object(partner, "build_request_fingerprint", return_value=object()),
            patch.object(
                partner,
                "record_operator_access_event",
                return_value="audit-event-id",
            ),
        ):
            result = partner.admin_create_partner(
                partner.PartnerCreateIn(
                    name="Asesoría",
                    email="partner@example.com",
                    password="temporary-password",
                ),
                request,
                Response(),
                context,
            )

        insert_sql, insert_params = connection.calls[1]
        self.assertIn("NULL,TRUE,TRUE", insert_sql)
        self.assertNotIn("t", insert_params)
        self.assertTrue(result["must_change_password"])
        self.assertFalse(result["token_returned"])
        self.assertNotIn("token", result)
        self.assertEqual(result["audit_event_id"], "audit-event-id")


if __name__ == "__main__":
    unittest.main()
