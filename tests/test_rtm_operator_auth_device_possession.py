from __future__ import annotations

import asyncio
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.requests import Request

import app as backend_app
from rtm_core.operator_auth_crypto import hash_device_secret
from rtm_core.operator_auth_repository import ActiveOperatorSession
import rtm_core.operator_auth_repository as auth_repository
import rtm_core.operator_auth_router as auth_router
import rtm_presenter_router as presenter_router


class _Result:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def fetchone(self):
        return self.row


class _CaptureConnection:
    def __init__(self, row=None):
        self.row = row
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), dict(parameters or {})))
        return _Result(self.row)


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
            "scheme": "http",
            "http_version": "1.1",
        }
    )


class OperatorDevicePossessionTest(unittest.TestCase):
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

    def _session_row(self) -> dict:
        return {
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

    def test_repository_matches_token_and_device_digest_atomically(self):
        device_secret = "A" * 32
        device_digest = hash_device_secret(device_secret)
        conn = _CaptureConnection(self._session_row())

        loaded = auth_repository.load_active_operator_session_for_device(
            conn,
            "t" * 48,
            device_key_sha256=device_digest,
            now=self.now,
        )

        self.assertEqual(loaded, self.session)
        self.assertEqual(len(conn.calls), 1)
        sql = " ".join(conn.calls[0][0].split())
        parameters = conn.calls[0][1]
        self.assertIn("d.device_key_sha256 = :device_key_sha256", sql)
        self.assertIn("s.token_sha256 = :token_sha256", sql)
        self.assertIn("r.active = TRUE", sql)
        self.assertEqual(parameters["device_key_sha256"], device_digest)
        self.assertNotIn(device_secret, parameters.values())

    def test_repository_rejects_a_non_digest_without_querying(self):
        conn = _CaptureConnection(self._session_row())

        loaded = auth_repository.load_active_operator_session_for_device(
            conn,
            "t" * 48,
            device_key_sha256="raw-device-secret-must-not-reach-sql",
            now=self.now,
        )

        self.assertIsNone(loaded)
        self.assertEqual(conn.calls, [])

    def test_router_accepts_header_or_http_only_cookie_and_touches_after_match(self):
        for channel in ("header", "cookie"):
            with self.subTest(channel=channel):
                device_secret = ("H" if channel == "header" else "C") * 32
                header = device_secret if channel == "header" else None
                cookie = device_secret if channel == "cookie" else None
                connection = Mock()
                with (
                    patch.object(
                        auth_router,
                        "load_active_operator_session_for_device",
                        return_value=self.session,
                    ) as load_bound_session,
                    patch.object(
                        auth_router,
                        "touch_operator_session",
                    ) as touch_session,
                ):
                    loaded = auth_router.load_operator_session_with_device_possession(
                        connection,
                        authorization="Bearer " + ("t" * 48),
                        x_rtm_device=header,
                        rtm_presenter_device=cookie,
                        touch=True,
                    )

                self.assertEqual(loaded, self.session)
                load_bound_session.assert_called_once_with(
                    connection,
                    "t" * 48,
                    device_key_sha256=hash_device_secret(device_secret),
                )
                self.assertNotIn(
                    device_secret,
                    load_bound_session.call_args.kwargs.values(),
                )
                touch_session.assert_called_once_with(
                    connection,
                    self.session_id,
                )

    def test_missing_or_wrong_device_never_touches_session(self):
        connection = Mock()
        with (
            patch.object(
                auth_router,
                "load_active_operator_session_for_device",
                return_value=None,
            ) as load_bound_session,
            patch.object(auth_router, "touch_operator_session") as touch_session,
        ):
            missing = auth_router.load_operator_session_with_device_possession(
                connection,
                authorization="Bearer " + ("t" * 48),
                x_rtm_device=None,
                rtm_presenter_device=None,
                touch=True,
            )
            wrong = auth_router.load_operator_session_with_device_possession(
                connection,
                authorization="Bearer " + ("t" * 48),
                x_rtm_device="W" * 32,
                rtm_presenter_device=None,
                touch=True,
            )

        self.assertIsNone(missing)
        self.assertIsNone(wrong)
        load_bound_session.assert_called_once_with(
            connection,
            "t" * 48,
            device_key_sha256=hash_device_secret("W" * 32),
        )
        touch_session.assert_not_called()


class OperatorAuthAppHardeningTest(unittest.TestCase):
    def test_auth_status_declares_the_authoritative_rollout_boundary(self):
        response = Response()
        config = SimpleNamespace(available=True, hmac_key="H" * 32)
        with (
            patch.object(
                auth_router,
                "operator_auth_environment_mode",
                return_value="individual",
            ),
            patch.object(
                auth_router,
                "load_operator_auth_runtime_config",
                return_value=config,
            ),
        ):
            payload = asyncio.run(auth_router.operator_auth_status(response))

        self.assertIs(payload["shared_ops_login_accepted"], False)
        self.assertIs(payload["legacy_login_retired_in_staging"], True)
        self.assertIs(
            payload["non_staging_legacy_login_unchanged"], True
        )
        self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")

    def test_successful_login_repeats_the_authoritative_rollout_boundary(self):
        decision = SimpleNamespace(
            ok=True,
            device_token=None,
            token="T" * 48,
            session_id="44444444-4444-4444-8444-444444444444",
            expires_at=self._future_time(),
            absolute_expires_at=self._future_time(),
            device_id="55555555-5555-4555-8555-555555555555",
            operator={"email": "operator@example.test"},
        )
        response = Response()
        with (
            patch.object(auth_router, "_runtime_config", return_value=object()),
            patch.object(
                auth_router,
                "_fingerprint",
                return_value=SimpleNamespace(request_id="request-rollout"),
            ),
            patch.object(auth_router, "login_operator", return_value=decision),
        ):
            payload = asyncio.run(
                auth_router.operator_login(
                    auth_router.OperatorLoginRequest(
                        email="operator@example.test",
                        password="synthetic-password",
                    ),
                    _request("/ops/auth/login"),
                    response,
                    None,
                    None,
                    Mock(),
                )
            )

        self.assertIs(payload["shared_ops_login_accepted"], False)
        self.assertIs(payload["legacy_login_retired_in_staging"], True)
        self.assertIs(
            payload["non_staging_legacy_login_unchanged"], True
        )

    @staticmethod
    def _future_time():
        return datetime(2026, 9, 3, 23, 0, tzinfo=timezone.utc)

    def test_auth_validation_error_never_reflects_password_or_input(self):
        secret = "validation-password-canary"
        error = RequestValidationError(
            [
                {
                    "type": "string_too_long",
                    "loc": ("body", "password"),
                    "msg": "String too long",
                    "input": secret,
                    "ctx": {"submitted": secret},
                }
            ]
        )

        response = asyncio.run(
            backend_app.redact_operator_auth_validation_error(
                _request("/ops/auth/login"),
                error,
            )
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")
        self.assertEqual(response.headers["pragma"], "no-cache")
        self.assertNotIn(secret, response.body.decode("utf-8"))
        self.assertEqual(
            json.loads(response.body),
            {"detail": "Solicitud no válida"},
        )

    def test_validation_errors_outside_auth_use_fastapi_default(self):
        canary = "ordinary-input-canary"
        error = RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("body", "field"),
                    "msg": "Invalid value",
                    "input": canary,
                }
            ]
        )

        response = asyncio.run(
            backend_app.redact_operator_auth_validation_error(
                _request("/public/other"),
                error,
            )
        )

        self.assertIn(canary, response.body.decode("utf-8"))
        self.assertNotIn("cache-control", response.headers)

    def test_private_ops_no_store_preserves_existing_vary(self):
        async def call_next(_request):
            return JSONResponse(
                {"ok": True},
                headers={"Vary": "Origin"},
            )

        response = asyncio.run(
            backend_app.no_store_private_ops(
                _request("/ops/auth/me"),
                call_next,
            )
        )

        vary = {
            value.strip().casefold()
            for value in response.headers["vary"].split(",")
        }
        self.assertEqual(
            vary,
            {
                "origin",
                "authorization",
                "cookie",
                "x-operator-token",
                "x-rtm-device",
            },
        )

    def test_every_presenter_route_uses_atomic_presenter_context(self):
        presenter_routes = [
            route
            for route in backend_app.app.routes
            if getattr(route, "path", "").startswith("/ops/presenter/")
        ]
        self.assertGreater(len(presenter_routes), 0)
        for route in presenter_routes:
            dependencies = {
                dependency.call
                for dependency in route.dependant.dependencies
            }
            self.assertIn(
                presenter_router.require_presenter_context,
                dependencies,
                route.path,
            )

    def test_presenter_context_honours_operator_auth_kill_switch(self):
        environment = {
            "RTM_ENV": "staging",
            "RTM_ENABLE_PRESENTER_MVP": "true",
            "RTM_PRESENTER_SYNTHETIC_ONLY": "true",
            "RTM_ALLOW_REAL_CUSTOMER_DATA": "false",
            "RTM_PRESENTER_EXTERNAL_EFFECTS_ALLOWED": "false",
            "RTM_PRESENTER_DIRECT_STORAGE_ALLOWED": "false",
            "RTM_ENABLE_OPERATOR_AUTH_V1": "false",
        }
        with patch.dict(os.environ, environment, clear=True):
            dependency = presenter_router.require_presenter_context()
            with self.assertRaises(HTTPException) as caught:
                next(dependency)
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(caught.exception.detail, "Not found")

    def test_presenter_context_fails_closed_on_invalid_auth_config(self):
        environment = {
            "RTM_ENV": "staging",
            "RTM_ENABLE_PRESENTER_MVP": "true",
            "RTM_PRESENTER_SYNTHETIC_ONLY": "true",
            "RTM_ALLOW_REAL_CUSTOMER_DATA": "false",
            "RTM_PRESENTER_EXTERNAL_EFFECTS_ALLOWED": "false",
            "RTM_PRESENTER_DIRECT_STORAGE_ALLOWED": "false",
            "RTM_ENABLE_OPERATOR_AUTH_V1": "true",
            "RTM_OPERATOR_ACCESS_HMAC_KEY": "short",
        }
        with patch.dict(os.environ, environment, clear=True):
            dependency = presenter_router.require_presenter_context()
            with self.assertRaises(HTTPException) as caught:
                next(dependency)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(
            caught.exception.detail,
            "Autenticacion individual no disponible",
        )


if __name__ == "__main__":
    unittest.main()
