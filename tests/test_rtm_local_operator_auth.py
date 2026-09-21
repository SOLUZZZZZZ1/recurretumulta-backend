from __future__ import annotations

import asyncio
import os
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy.engine import URL
from starlette.responses import JSONResponse

from rtm_core.local_operator_auth import (
    LOCAL_OPERATOR_ORIGIN,
    LocalOperatorAuthMiddleware,
    LocalOperatorAuthMisconfigured,
    assert_local_operator_auth_ready,
    local_operator_auth_requested,
)
from rtm_core.operator_auth_request import (
    OPERATOR_AUTH_MODE_FAIL_CLOSED,
    OPERATOR_AUTH_MODE_INDIVIDUAL,
    OPERATOR_AUTH_MODE_LEGACY,
    OperatorAuthRuntimeMisconfigured,
    load_operator_auth_runtime_config,
    operator_auth_environment_mode,
)
from rtm_core.operator_auth_router import operator_auth_status


def local_environment() -> dict[str, str]:
    return {
        "RTM_ENV": "development",
        "RTM_ENABLE_LOCAL_OPERATOR_AUTH": "1",
        "RTM_ENABLE_OPERATOR_AUTH_V1": "1",
        "RTM_LOCAL_BIND_HOST": "127.0.0.1",
        "RTM_ALLOWED_HOSTS": "127.0.0.1",
        "ALLOWED_ORIGINS": LOCAL_OPERATOR_ORIGIN,
        "RTM_ALLOW_REAL_CUSTOMER_DATA": "0",
        "RTM_TRUST_PROXY_HEADERS": "0",
        "RTM_OPERATOR_ACCESS_HMAC_KEY": "H" * 64,
        "OPERATOR_TOKEN": "L" * 64,
        "RTM_ENABLE_B2": "0",
        "RTM_ENABLE_STRIPE": "0",
        "RTM_ENABLE_FINAL_PAYMENTS": "0",
        "RTM_ENABLE_DOCUMENT_PROVIDER": "0",
        "RTM_ENABLE_OUTBOUND_EMAIL": "0",
        "RTM_ENABLE_EXTERNAL_SUBMISSION": "0",
        "DATABASE_URL": URL.create(
            "postgresql+psycopg", username="rtm_local_app",
            password="test password @/%", host="127.0.0.1",
            port=5432, database="rtm_local",
        ).render_as_string(hide_password=False),
    }


class LocalOperatorAuthConfigurationTest(unittest.TestCase):
    def test_valid_local_profile_uses_individual_mode(self):
        env = local_environment()
        assert_local_operator_auth_ready(env)
        self.assertEqual(operator_auth_environment_mode(env), OPERATOR_AUTH_MODE_INDIVIDUAL)
        config = load_operator_auth_runtime_config(env)
        self.assertTrue(config.available)
        self.assertTrue(config.local_development)
        self.assertFalse(config.trust_proxy_headers)

    def test_default_and_staging_contracts_are_preserved(self):
        self.assertFalse(local_operator_auth_requested({}))
        self.assertEqual(operator_auth_environment_mode({"RTM_ENV": "development"}), OPERATOR_AUTH_MODE_LEGACY)
        self.assertEqual(operator_auth_environment_mode({"RTM_ENV": "production"}), OPERATOR_AUTH_MODE_FAIL_CLOSED)
        staging = {
            "RTM_ENV": "staging", "RTM_ENABLE_OPERATOR_AUTH_V1": "1",
            "RTM_OPERATOR_ACCESS_HMAC_KEY": "H" * 64,
        }
        config = load_operator_auth_runtime_config(staging)
        self.assertTrue(config.available)
        self.assertFalse(config.local_development)
        with patch.dict(os.environ, staging, clear=True):
            status = asyncio.run(operator_auth_status(Response()))
        self.assertTrue(status["staging_only"])
        self.assertNotIn("auth_profile", status)

    def test_original_feature_flag_alone_never_enables_local_auth(self):
        env = local_environment()
        env.pop("RTM_ENABLE_LOCAL_OPERATOR_AUTH")
        self.assertEqual(operator_auth_environment_mode(env), OPERATOR_AUTH_MODE_FAIL_CLOSED)
        with self.assertRaises(OperatorAuthRuntimeMisconfigured):
            load_operator_auth_runtime_config(env)

    def test_invalid_local_configuration_fails_closed(self):
        mutations = [
            ("RTM_ENV", value) for value in ("staging", "production", "test", "", "developmnt")
        ] + [
            ("RTM_ENABLE_LOCAL_OPERATOR_AUTH", "invalid"),
            ("RTM_ENABLE_OPERATOR_AUTH_V1", "0"),
            ("RTM_LOCAL_BIND_HOST", "0.0.0.0"),
            ("RTM_ALLOWED_HOSTS", "127.0.0.1,localhost"),
            ("ALLOWED_ORIGINS", "http://127.0.0.1:5174"),
            ("ALLOWED_ORIGINS", LOCAL_OPERATOR_ORIGIN + ",https://example.com"),
            ("RTM_ALLOW_REAL_CUSTOMER_DATA", "1"),
            ("RTM_TRUST_PROXY_HEADERS", "1"),
            ("RTM_TRUSTED_PROXY_CIDRS", "127.0.0.1/32"),
            ("RTM_BLOCK_LEGACY_OPERATOR_AUTH", "invalid"),
            ("RTM_INSTANCE_ID", "rtm-staging"),
            ("RTM_DATA_NAMESPACE", "rtm-production"),
            ("RTM_ENVIRONMENT_CONFIRMATION", "RTM_STAGING_ISOLATED"),
            ("RENDER_SERVICE_ID", "srv-other"),
            ("RENDER_FUTURE_VARIABLE", "present"),
            ("VERCEL", "1"),
            ("DYNO", "web.1"),
            ("PGHOSTADDR", "203.0.113.10"),
            ("PGSERVICE", "remote-service"),
            ("PGSERVICEFILE", "/tmp/other-service.conf"),
            ("FRONTEND_URL", "https://staging.recurretumulta.eu"),
        ]
        mutations.extend((name, "1") for name in local_environment() if name.startswith("RTM_ENABLE_") and name not in {"RTM_ENABLE_LOCAL_OPERATOR_AUTH", "RTM_ENABLE_OPERATOR_AUTH_V1"})
        for name, value in mutations:
            with self.subTest(name=name, value=value):
                env = dict(local_environment(), **{name: value})
                with self.assertRaises(LocalOperatorAuthMisconfigured):
                    assert_local_operator_auth_ready(env)
                self.assertEqual(operator_auth_environment_mode(env), OPERATOR_AUTH_MODE_FAIL_CLOSED)
                with self.assertRaises(OperatorAuthRuntimeMisconfigured):
                    load_operator_auth_runtime_config(env)

    def test_database_cannot_redirect_through_dialect_query_or_other_target(self):
        base = dict(drivername="postgresql+psycopg", username="rtm_local_app", password="local-test", host="127.0.0.1", port=5432, database="rtm_local")
        mutations = [
            {"drivername": "postgresql"}, {"host": "localhost"},
            {"host": "db.example.com"}, {"port": 5433}, {"port": None},
            {"database": "rtm_staging"}, {"username": "postgres"},
            {"password": None}, {"query": {"host": "remote.example.com"}},
            {"query": {"options": "-c search_path=another"}},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                env = local_environment()
                env["DATABASE_URL"] = URL.create(**(base | mutation)).render_as_string(hide_password=False)
                with self.assertRaises(LocalOperatorAuthMisconfigured) as raised:
                    assert_local_operator_auth_ready(env)
                self.assertNotIn("local-test", str(raised.exception))
                self.assertNotIn(env["DATABASE_URL"], str(raised.exception))

    def test_status_truthfully_identifies_local_profile(self):
        with patch.dict(os.environ, local_environment(), clear=True):
            status = asyncio.run(operator_auth_status(Response()))
        self.assertTrue(status["individual_login_enabled"])
        self.assertTrue(status["configuration_valid"])
        self.assertEqual(status["auth_environment"], "development")
        self.assertEqual(status["auth_profile"], "local_development")
        self.assertTrue(status["local_only"])
        self.assertFalse(status["staging_only"])
        self.assertFalse(status["shared_ops_login_accepted"])
        self.assertFalse(status["operator_creation_available"])

    def test_admin_lifecycle_and_presenter_are_not_enabled_by_local_auth(self):
        from rtm_core.operator_admin_policy import load_operator_admin_runtime_config, OperatorAdminRuntimeMisconfigured
        from rtm_core.operator_lifecycle_policy import load_operator_lifecycle_runtime_config, OperatorLifecycleRuntimeMisconfigured
        from rtm_presenter_policy import load_presenter_runtime_configuration, PresenterPolicyError
        env = dict(local_environment(), RTM_ENABLE_OPERATOR_ADMIN_V1="1", RTM_ENABLE_OPERATOR_LIFECYCLE_V1="1")
        with self.assertRaises(OperatorAdminRuntimeMisconfigured):
            load_operator_admin_runtime_config(env)
        with self.assertRaises(OperatorLifecycleRuntimeMisconfigured):
            load_operator_lifecycle_runtime_config(env)
        with self.assertRaises(PresenterPolicyError):
            load_presenter_runtime_configuration(env, require_enabled=True)

    def test_startup_rejects_local_drift_and_weak_auth_key(self):
        import app as backend_app
        with patch.dict(os.environ, local_environment(), clear=True):
            backend_app.validate_deployed_environment()
        for mutation in ({"RTM_LOCAL_BIND_HOST": "0.0.0.0"}, {"RTM_OPERATOR_ACCESS_HMAC_KEY": "short"}):
            with self.subTest(mutation=mutation), patch.dict(os.environ, local_environment() | mutation, clear=True):
                with self.assertRaises(RuntimeError):
                    backend_app.validate_deployed_environment()


class LocalOperatorAuthBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, local_environment(), clear=True))
        self.app = FastAPI()
        self.app.add_middleware(LocalOperatorAuthMiddleware)

        @self.app.api_route("/probe", methods=["GET", "POST"])
        def probe():
            return {"ok": True}

    def client(self, peer="127.0.0.1", host="127.0.0.1"):
        return TestClient(self.app, base_url=f"http://{host}:8000", client=(peer, 40000))

    def test_browser_reads_without_origin_and_exact_origin_writes(self):
        with self.client() as client:
            self.assertEqual(client.get("/probe", headers={"Sec-Fetch-Site": "same-origin"}).status_code, 200)
            self.assertEqual(client.post("/probe", headers={"Origin": LOCAL_OPERATOR_ORIGIN, "Sec-Fetch-Site": "same-origin"}).status_code, 200)
            self.assertEqual(client.post("/probe").status_code, 403)

    def test_remote_peers_and_wrong_server_are_rejected(self):
        for peer, host in (("203.0.113.10", "127.0.0.1"), ("127.0.0.1", "example.com")):
            with self.subTest(peer=peer, host=host), self.client(peer, host) as client:
                self.assertEqual(client.get("/probe", headers={"X-Forwarded-For": "127.0.0.1"}).status_code, 403)

    def test_cross_origin_fetch_metadata_and_duplicate_origins_are_rejected(self):
        headers = [
            {"Origin": "https://example.com"}, {"Origin": "null"},
            {"Origin": LOCAL_OPERATOR_ORIGIN + "/"},
            {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
            [("Origin", LOCAL_OPERATOR_ORIGIN), ("Origin", "https://example.com")],
        ]
        with self.client() as client:
            for value in headers:
                with self.subTest(headers=value):
                    self.assertEqual(client.get("/probe", headers=value).status_code, 403)

    def test_runtime_drift_and_invalid_flag_fail_before_handler(self):
        with self.client() as client:
            for mutation in ({"RTM_ENABLE_B2": "1"}, {"RTM_ENABLE_LOCAL_OPERATOR_AUTH": "invalid"}):
                with self.subTest(mutation=mutation), patch.dict(os.environ, mutation):
                    self.assertEqual(client.get("/probe").status_code, 503)

    def test_disabled_local_profile_does_not_change_existing_requests(self):
        with patch.dict(os.environ, {"RTM_ENABLE_LOCAL_OPERATOR_AUTH": "0"}), self.client("203.0.113.10") as client:
            self.assertEqual(client.get("/probe").status_code, 200)

    def test_local_requests_use_real_bridge_device_possession_and_roles(self):
        from rtm_core import legacy_ops_session_bridge as bridge
        from rtm_core import operator_auth_router as auth_router
        from rtm_core.operator_auth_crypto import hash_device_secret
        from rtm_core.ops_case_scope import load_ops_case_scope

        bearer, device = "b" * 48, "d" * 32
        session = SimpleNamespace(
            operator_id="11111111-1111-4111-8111-111111111111",
            session_id="22222222-2222-4222-8222-222222222222",
            role_code="rtm.operator", permissions=("ops.view",),
            must_change_password=False, mfa_required=False,
            login_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            last_verified_at=None,
        )

        class Engine:
            @contextmanager
            def begin(self):
                yield object()

        def lookup(conn, raw_token, *, device_key_sha256):
            if raw_token == bearer and device_key_sha256 == hash_device_secret(device):
                return session
            return None

        secured = FastAPI()
        secured.middleware("http")(bridge.legacy_ops_individual_session_bridge)
        secured.add_middleware(LocalOperatorAuthMiddleware)

        @secured.get("/ops/queue")
        def queue(request: Request):
            scope = load_ops_case_scope(request)
            return JSONResponse({"individual": scope.individual_session, "scope_all": scope.scope_all})

        with (
            patch.object(bridge, "get_engine", return_value=Engine()),
            patch.object(auth_router, "load_active_operator_session_for_device", side_effect=lookup),
            patch.object(auth_router, "touch_operator_session"),
            TestClient(secured, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 40000)) as client,
        ):
            token_headers = {"Authorization": f"Bearer {bearer}"}
            self.assertEqual(client.get("/ops/queue", headers=token_headers).status_code, 401)
            wrong_device = token_headers | {"X-RTM-Device": "x" * 32}
            self.assertEqual(client.get("/ops/queue", headers=wrong_device).status_code, 401)
            valid = token_headers | {"X-RTM-Device": device}
            response = client.get("/ops/queue", headers=valid)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"individual": True, "scope_all": False})
            session.role_code = "rtm.signer"
            self.assertEqual(client.get("/ops/queue", headers=valid).status_code, 403)
            self.assertEqual(client.get("/ops/queue", headers={"X-Operator-Token": os.environ["OPERATOR_TOKEN"]}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
