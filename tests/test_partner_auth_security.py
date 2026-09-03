from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException, Response

import partner


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, row):
        self._row = row

    def execute(self, statement, parameters=None):
        return _Result(self._row)


class _Engine:
    def __init__(self, row):
        self._connection = _Connection(row)

    @contextmanager
    def begin(self):
        yield self._connection


class PartnerAuthenticationSecurityTest(unittest.TestCase):
    def test_api_tokens_are_expiring_and_digest_only_at_rest(self):
        raw = partner._make_token()
        stored = partner._stored_token(raw)
        self.assertTrue(stored.startswith("sha256$"))
        self.assertNotIn(raw, stored)

        conn = Mock()
        conn.execute.return_value = _Result(
            ("partner-id", "Partner", "p@example.com", True)
        )
        result = partner._get_partner_by_token(conn, raw)
        self.assertEqual(result["id"], "partner-id")
        self.assertEqual(conn.execute.call_count, 1)
        lookup_params = conn.execute.call_args.args[1]
        self.assertEqual(lookup_params, {"digest": stored})

    def test_legacy_and_expired_tokens_are_rejected_before_database_lookup(self):
        conn = Mock()
        with self.assertRaises(HTTPException) as legacy:
            partner._get_partner_by_token(conn, "partner-secret-token")
        self.assertEqual(legacy.exception.status_code, 401)
        conn.execute.assert_not_called()

        issued_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        expired = partner._make_token(now=issued_at)
        with self.assertRaises(HTTPException) as raised:
            partner._partner_token_expiration(
                expired,
                now=issued_at + timedelta(hours=8),
                environ={},
            )
        self.assertEqual(raised.exception.status_code, 401)

    def test_cookie_mutations_require_session_bound_double_submit_csrf(self):
        token = partner._make_token()
        credential = partner.PartnerCredential(token=token, via_cookie=True)
        csrf = partner._csrf_token_for_session(token)
        partner._require_partner_csrf(
            credential,
            csrf_header=csrf,
            csrf_cookie=csrf,
        )
        with self.assertRaises(HTTPException) as raised:
            partner._require_partner_csrf(
                credential,
                csrf_header="0" * 64,
                csrf_cookie="0" * 64,
            )
        self.assertEqual(raised.exception.status_code, 403)

        # Authorization headers are not ambient browser credentials and retain
        # compatibility while the frontend migrates to cookies.
        partner._require_partner_csrf(
            partner.PartnerCredential(token=token, via_cookie=False),
            csrf_header=None,
            csrf_cookie=None,
        )

    def test_session_cookie_is_host_only_secure_httponly_and_bounded(self):
        token = partner._make_token()
        expires_at = partner._partner_token_expiration(token)
        response = Response()
        csrf = partner._set_partner_session_cookies(
            response,
            token=token,
            expires_at=expires_at,
        )
        cookies = response.headers.getlist("set-cookie")
        session_cookie = next(
            item for item in cookies if partner._PARTNER_SESSION_COOKIE in item
        )
        csrf_cookie = next(
            item for item in cookies if partner._PARTNER_CSRF_COOKIE in item
        )
        self.assertIn("HttpOnly", session_cookie)
        self.assertIn("Secure", session_cookie)
        self.assertIn("SameSite=lax", session_cookie)
        self.assertIn("Path=/", session_cookie)
        self.assertNotIn("HttpOnly", csrf_cookie)
        self.assertIn(csrf, csrf_cookie)
        self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")

    def test_partner_admin_requires_nominal_supervisor_role(self):
        context = SimpleNamespace(
            session=SimpleNamespace(role_code="rtm.operator")
        )
        with self.assertRaises(HTTPException) as raised:
            import asyncio

            asyncio.run(partner.require_partner_admin_supervisor(context))
        self.assertEqual(raised.exception.status_code, 403)

    def _denied_login(self, row):
        verifier = Mock(return_value=(False, False))
        with (
            patch.object(partner, "get_engine", return_value=_Engine(row)),
            patch.object(partner, "_verify_password", verifier),
            self.assertRaises(HTTPException) as raised,
        ):
            partner.partner_login(
                partner.PartnerLoginIn(
                    email="partner@example.com",
                    password="intento-no-valido",
                ),
                Response(),
            )
        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.detail, "Credenciales incorrectas")
        return verifier

    def test_missing_partner_runs_one_dummy_argon_verification(self):
        self.assertTrue(
            partner._DUMMY_PARTNER_PASSWORD_HASH.startswith("$argon2id$")
        )
        self.assertFalse(
            partner._PASSWORD_HASHER.check_needs_rehash(
                partner._DUMMY_PARTNER_PASSWORD_HASH
            )
        )
        verifier = self._denied_login(None)

        verifier.assert_called_once_with(
            "intento-no-valido",
            "",
            partner._DUMMY_PARTNER_PASSWORD_HASH,
        )

    def test_inactive_partner_runs_the_same_dummy_argon_verification(self):
        inactive = (
            "11111111-1111-4111-8111-111111111111",
            "Partner inactivo",
            "partner@example.com",
            "legacy-salt",
            "hash-que-no-debe-probarse",
            False,
        )
        verifier = self._denied_login(inactive)

        verifier.assert_called_once_with(
            "intento-no-valido",
            "",
            partner._DUMMY_PARTNER_PASSWORD_HASH,
        )

    def test_active_partner_uses_exactly_one_real_verification(self):
        active = (
            "11111111-1111-4111-8111-111111111111",
            "Partner activo",
            "partner@example.com",
            "legacy-salt",
            "hash-real",
            True,
            False,
        )
        verifier = self._denied_login(active)

        verifier.assert_called_once_with(
            "intento-no-valido",
            "legacy-salt",
            "hash-real",
        )

    def test_legacy_verification_always_consumes_dummy_argon_work(self):
        password = "legacy-password"
        salt = "legacy-salt"
        expected = partner._hash_password(password, salt)
        with patch.object(partner, "_consume_dummy_argon_work") as dummy_argon:
            valid, needs_upgrade = partner._verify_password(password, salt, expected)

        self.assertTrue(valid)
        self.assertTrue(needs_upgrade)
        dummy_argon.assert_called_once_with(password)

    def test_argon_verification_always_consumes_dummy_legacy_work(self):
        password = "modern-password"
        with patch.object(partner, "_consume_dummy_legacy_work") as dummy_legacy:
            valid, _ = partner._verify_password(
                password,
                "",
                partner._DUMMY_PARTNER_PASSWORD_HASH,
            )

        self.assertFalse(valid)
        dummy_legacy.assert_called_once_with(password)


if __name__ == "__main__":
    unittest.main()
