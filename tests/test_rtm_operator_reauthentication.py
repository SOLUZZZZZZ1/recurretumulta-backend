from __future__ import annotations

import asyncio
import inspect
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from starlette.requests import Request

from rtm_core.operator_auth_crypto import (
    PasswordVerification,
    hash_device_secret,
)
from rtm_core.operator_auth_repository import ActiveOperatorSession
from rtm_core.operator_auth_request import (
    OperatorAuthRuntimeConfig,
    RequestFingerprint,
)
import rtm_core.operator_auth_router as auth_router
import rtm_core.operator_auth_repository as auth_repository
import rtm_core.operator_auth_service as auth_service


class _Result:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def fetchone(self):
        return self.row


class _CaptureConnection:
    def __init__(self, first_row=None):
        self.first_row = first_row
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), dict(parameters or {})))
        if len(self.calls) == 1:
            return _Result(self.first_row)
        return _Result()


class OperatorReauthenticationTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 28, 18, 8, tzinfo=timezone.utc)
        self.operator_id = "11111111-1111-4111-8111-111111111111"
        self.session_id = "22222222-2222-4222-8222-222222222222"
        self.device_id = "33333333-3333-4333-8333-333333333333"
        self.session = ActiveOperatorSession(
            session_id=self.session_id,
            operator_id=self.operator_id,
            email="operator@example.test",
            display_name="Operator",
            role_code="operator",
            permissions=("cases.read",),
            must_change_password=False,
            mfa_required=False,
            login_at=self.now - timedelta(hours=1),
            last_verified_at=self.now - timedelta(hours=1),
            device_id=self.device_id,
            expires_at=self.now + timedelta(hours=7),
            absolute_expires_at=self.now + timedelta(hours=23),
        )
        self.operator_row = {
            "id": self.operator_id,
            "password_hash": "argon2id-hash",
            "status": "active",
            "must_change_password": False,
            "mfa_required": False,
            "failed_login_count": 0,
            "locked_until": None,
            "auth_epoch": 3,
            "session_id": self.session_id,
            "session_status": "active",
            "expires_at": self.now + timedelta(hours=7),
            "absolute_expires_at": self.now + timedelta(hours=23),
            "session_auth_epoch": 3,
            "device_id": self.device_id,
            "device_status": "known",
        }
        self.context = RequestFingerprint(
            request_id="request-reauth-1",
            ip_address=None,
            ip_masked=None,
            ip_hash_sha256=None,
            ip_family=None,
            ip_source="direct",
            ip_trusted=False,
            raw_user_agent=None,
            user_agent_summary=None,
            device_type="unknown",
            os_family=None,
            os_version=None,
            browser_family=None,
            browser_version=None,
            country_code=None,
            region=None,
            city=None,
            timezone=None,
            location_source=None,
            trusted_headers={},
            risk_flags=(),
        )
        self.config = OperatorAuthRuntimeConfig(
            environment="staging",
            enabled=True,
            trust_proxy_headers=False,
            hmac_key="K" * 64,
            evidence_retention_days=180,
        )

    def test_heartbeat_touch_never_refreshes_last_verified_at(self):
        row = {
            "id": self.session_id,
            "operator_id": self.operator_id,
            "email": self.session.email,
            "display_name": self.session.display_name,
            "role_code": self.session.role_code,
            "permissions": list(self.session.permissions),
            "must_change_password": False,
            "mfa_required": False,
            "login_at": self.session.login_at,
            "last_verified_at": self.session.last_verified_at,
            "device_id": self.device_id,
            "expires_at": self.session.expires_at,
            "absolute_expires_at": self.session.absolute_expires_at,
        }
        conn = _CaptureConnection(row)

        loaded = auth_service.load_operator_session(
            conn,
            raw_token="t" * 48,
            touch=True,
            now=self.now,
        )

        self.assertEqual(loaded.last_verified_at, self.session.last_verified_at)
        self.assertEqual(loaded.login_at, self.session.login_at)
        self.assertEqual(loaded.device_id, self.device_id)
        self.assertEqual(len(conn.calls), 2)
        touch_sql = " ".join(conn.calls[1][0].split())
        self.assertIn("SET last_seen_at=:now", touch_sql)
        self.assertNotIn("last_verified_at", touch_sql)

    def test_login_and_live_session_require_an_active_role(self):
        login_sql = " ".join(
            inspect.getsource(auth_repository.find_operator_for_login).split()
        )
        session_sql = " ".join(
            inspect.getsource(auth_repository.load_active_operator_session).split()
        )

        for query in (login_sql, session_sql):
            self.assertIn("JOIN rtm_operator_roles r", query)
            self.assertIn("r.active = TRUE", query)

    def test_reauthentication_locks_and_rechecks_the_active_role(self):
        lock_sql = " ".join(
            inspect.getsource(
                auth_repository.find_operator_for_reauthentication
            ).split()
        )
        update_sql = " ".join(
            inspect.getsource(
                auth_repository.mark_operator_session_verified
            ).split()
        )

        self.assertIn("JOIN rtm_operator_roles r", lock_sql)
        self.assertIn("r.active = TRUE", lock_sql)
        self.assertIn("FOR UPDATE OF o, r, s, d", lock_sql)
        self.assertIn("rtm_operator_roles r", update_sql)
        self.assertIn("r.id = o.primary_role_id", update_sql)
        self.assertIn("r.active = TRUE", update_sql)

    def test_fresh_login_does_not_count_as_explicit_reauthentication(self):
        fresh_login = replace(
            self.session,
            last_verified_at=self.session.login_at,
        )
        explicitly_reauthenticated = replace(
            fresh_login,
            last_verified_at=fresh_login.login_at + timedelta(microseconds=1),
        )

        self.assertFalse(
            auth_service.has_explicit_reauthentication(fresh_login)
        )
        self.assertTrue(
            auth_service.has_explicit_reauthentication(
                explicitly_reauthenticated
            )
        )

    def test_wrong_password_does_not_refresh_verification(self):
        mark_verified = Mock()
        clear_failures = Mock()
        audit = Mock(return_value="event-id")
        with (
            patch.object(
                auth_service,
                "find_operator_for_reauthentication",
                return_value=self.operator_row,
            ),
            patch.object(
                auth_service,
                "verify_operator_password",
                return_value=PasswordVerification(valid=False),
            ),
            patch.object(
                auth_service,
                "register_failed_login",
                return_value={"failed_login_count": 1, "locked_until": None},
            ) as failed_login,
            patch.object(
                auth_service,
                "mark_operator_session_verified",
                mark_verified,
            ),
            patch.object(
                auth_service,
                "clear_failed_reauthentication_attempts",
                clear_failures,
            ),
            patch.object(auth_service, "record_operator_access_event", audit),
        ):
            decision = auth_service.reauthenticate_operator(
                Mock(),
                session=self.session,
                password="incorrect-password",
                context=self.context,
                config=self.config,
                now=self.now,
            )

        self.assertFalse(decision.ok)
        self.assertEqual(decision.status_code, 401)
        failed_login.assert_called_once_with(
            unittest.mock.ANY,
            self.operator_id,
            now=self.now,
        )
        mark_verified.assert_not_called()
        clear_failures.assert_not_called()
        self.assertEqual(
            audit.call_args.kwargs["event_type"],
            "auth.reauthentication_denied",
        )
        self.assertEqual(
            audit.call_args.kwargs["reason_code"],
            "invalid_credentials",
        )
        self.assertNotIn("password", audit.call_args.kwargs)
        self.assertNotIn("token", audit.call_args.kwargs)

    def test_correct_password_refreshes_verification_and_audits(self):
        mark_verified = Mock(return_value=True)
        clear_failures = Mock()
        audit = Mock(return_value="event-id")
        with (
            patch.object(
                auth_service,
                "find_operator_for_reauthentication",
                return_value=self.operator_row,
            ),
            patch.object(
                auth_service,
                "verify_operator_password",
                return_value=PasswordVerification(valid=True),
            ),
            patch.object(
                auth_service,
                "mark_operator_session_verified",
                mark_verified,
            ),
            patch.object(
                auth_service,
                "clear_failed_reauthentication_attempts",
                clear_failures,
            ),
            patch.object(auth_service, "record_operator_access_event", audit),
        ):
            decision = auth_service.reauthenticate_operator(
                Mock(),
                session=self.session,
                password="correct-password",
                context=self.context,
                config=self.config,
                now=self.now,
            )

        self.assertTrue(decision.ok)
        self.assertEqual(decision.reauthenticated_at, self.now)
        mark_verified.assert_called_once_with(
            unittest.mock.ANY,
            session_id=self.session_id,
            operator_id=self.operator_id,
            now=self.now,
        )
        clear_failures.assert_called_once_with(
            unittest.mock.ANY,
            self.operator_id,
        )
        self.assertEqual(
            audit.call_args.kwargs["event_type"],
            "auth.reauthenticated",
        )
        self.assertEqual(audit.call_args.kwargs["result"], "success")
        self.assertNotIn("password", audit.call_args.kwargs)
        self.assertNotIn("token", audit.call_args.kwargs)

    def test_mfa_or_password_change_requirement_fails_closed(self):
        for field, reason in (
            ("mfa_required", "mfa_required"),
            ("must_change_password", "password_change_required"),
        ):
            with self.subTest(field=field):
                row = dict(self.operator_row, **{field: True})
                verify = Mock()
                mark_verified = Mock()
                audit = Mock(return_value="event-id")
                with (
                    patch.object(
                        auth_service,
                        "find_operator_for_reauthentication",
                        return_value=row,
                    ),
                    patch.object(auth_service, "verify_operator_password", verify),
                    patch.object(
                        auth_service,
                        "mark_operator_session_verified",
                        mark_verified,
                    ),
                    patch.object(
                        auth_service,
                        "record_operator_access_event",
                        audit,
                    ),
                ):
                    decision = auth_service.reauthenticate_operator(
                        Mock(),
                        session=self.session,
                        password="not-used",
                        context=self.context,
                        config=self.config,
                        now=self.now,
                    )

                self.assertFalse(decision.ok)
                self.assertEqual(decision.status_code, 409)
                verify.assert_not_called()
                mark_verified.assert_not_called()
                self.assertEqual(audit.call_args.kwargs["reason_code"], reason)

    def test_invalid_session_is_blocked_before_password_verification(self):
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/ops/auth/reauthenticate",
                "headers": [],
                "query_string": b"",
                "client": ("127.0.0.1", 50000),
                "server": ("testserver", 80),
                "scheme": "http",
                "http_version": "1.1",
            }
        )
        payload = auth_router.OperatorReauthenticationRequest(
            password="never-verified"
        )
        reauthenticate = Mock()
        audit_denial = Mock(return_value="event-id")
        device_secret = "D" * 32
        connection = Mock()
        with (
            patch.object(auth_router, "_runtime_config", return_value=self.config),
            patch.object(
                auth_router,
                "load_active_operator_session_for_device",
                return_value=None,
            ) as load_session,
            patch.object(
                auth_router,
                "reauthenticate_operator",
                reauthenticate,
            ),
            patch.object(
                auth_router,
                "record_reauthentication_denial",
                audit_denial,
            ),
        ):
            response = asyncio.run(
                auth_router.operator_reauthenticate(
                    payload,
                    request,
                    authorization="Bearer " + ("t" * 48),
                    x_rtm_device=device_secret,
                    rtm_presenter_device=None,
                    conn=connection,
                )
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")
        load_session.assert_called_once_with(
            connection,
            "t" * 48,
            device_key_sha256=hash_device_secret(device_secret),
        )
        reauthenticate.assert_not_called()
        self.assertEqual(
            audit_denial.call_args.kwargs["reason_code"],
            "invalid_session",
        )
        self.assertNotIn("password", audit_denial.call_args.kwargs)
        self.assertNotIn("token", audit_denial.call_args.kwargs)

    def test_reauthentication_password_is_hidden_from_model_repr(self):
        payload = auth_router.OperatorReauthenticationRequest(
            password="model-secret-password"
        )
        self.assertNotIn("model-secret-password", repr(payload))


if __name__ == "__main__":
    unittest.main()
