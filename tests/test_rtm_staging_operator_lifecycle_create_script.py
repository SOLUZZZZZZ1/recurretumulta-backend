from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

from scripts import rtm_staging_operator_lifecycle_create as create_script


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rtm_staging_operator_lifecycle_create.py"
TARGET_ID = "11111111-1111-4111-8111-111111111111"
AUDIT_ID = "22222222-2222-4222-8222-222222222222"
TARGET_EMAIL = create_script.DEFAULT_OPERATOR_EMAIL
TARGET_NAME = create_script.DEFAULT_OPERATOR_DISPLAY_NAME
SUPERVISOR_PASSWORD = "supervisor secret that must never be rendered"
TEMPORARY_PASSWORD = "RTM-generated-target-secret-7a!"


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class _FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeCookies:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str):
        return self.values.get(name)

    def clear(self) -> None:
        self.values.clear()


class _FakeTransport:
    def __init__(self, *, app, raise_app_exceptions: bool) -> None:
        self.app = app
        self.raise_app_exceptions = raise_app_exceptions


class _FakeAsyncClient:
    def __init__(self, *, transport, **kwargs) -> None:
        self.transport = transport
        self.kwargs = kwargs
        self.cookies = _FakeCookies()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get(self, path: str, **kwargs):
        return await self.transport.app.handle(
            self,
            "GET",
            path,
            **kwargs,
        )

    async def post(self, path: str, **kwargs):
        return await self.transport.app.handle(
            self,
            "POST",
            path,
            **kwargs,
        )


def _fake_httpx_module() -> ModuleType:
    module = ModuleType("httpx")
    module.ASGITransport = _FakeTransport
    module.AsyncClient = _FakeAsyncClient
    return module


def _fake_runtime_modules(
    *,
    generated_password: str = TEMPORARY_PASSWORD,
) -> dict[str, ModuleType]:
    environment_contract = ModuleType("rtm_core.environment_contract")
    environment_contract.assert_environment_ready = mock.Mock()

    lifecycle_policy = ModuleType("rtm_core.operator_lifecycle_policy")
    lifecycle_policy.load_operator_lifecycle_runtime_config = mock.Mock(
        return_value=SimpleNamespace(available=True)
    )

    provisioning = ModuleType("rtm_core.operator_provisioning")
    provisioning.generate_temporary_password = mock.Mock(
        return_value=generated_password
    )
    provisioning.normalize_synthetic_operator_email = (
        lambda value: str(value).strip().casefold()
    )

    auth_crypto = ModuleType("rtm_core.operator_auth_crypto")
    auth_crypto.normalize_operator_email = (
        lambda value: str(value).strip().casefold()
    )
    auth_crypto.validate_operator_password = lambda value: str(value)
    return {
        "rtm_core.environment_contract": environment_contract,
        "rtm_core.operator_lifecycle_policy": lifecycle_policy,
        "rtm_core.operator_provisioning": provisioning,
        "rtm_core.operator_auth_crypto": auth_crypto,
    }


class _OfficialRouteScenario:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls: list[str] = []
        self.create_payload: dict[str, object] = {}
        self.seen_device_header: str | None = None

    async def handle(self, client, method: str, path: str, **kwargs):
        if method == "GET" and path == "/ops/auth/status":
            self.calls.append("auth_status")
            return _FakeResponse(200, {"individual_login_enabled": True})
        if method == "GET" and path == "/ops/admin/lifecycle/status":
            self.calls.append("lifecycle_status")
            return _FakeResponse(
                200,
                {
                    "operator_lifecycle_enabled": True,
                    "synthetic_only": True,
                    "passwords_returned": False,
                },
            )
        if method == "POST" and path == "/ops/auth/login":
            self.calls.append("login")
            body = kwargs.get("json") or {}
            if self.mode == "login_error":
                return _FakeResponse(
                    422,
                    {"detail": [{"input": body.get("password")}]} ,
                )
            if body != {
                "email": create_script.DEFAULT_SUPERVISOR_EMAIL,
                "password": SUPERVISOR_PASSWORD,
            }:
                return _FakeResponse(401, {"ok": False})
            client.cookies.values[create_script._DEVICE_COOKIE] = "D" * 48
            return _FakeResponse(
                200,
                {
                    "ok": True,
                    "token": "T" * 64,
                    "operator": {
                        "permissions": ["ops.view", "ops.supervise"],
                        "must_change_password": False,
                    },
                },
            )
        if method == "GET" and path == "/ops/admin/operators":
            self.calls.append("list")
            self.seen_device_header = (kwargs.get("headers") or {}).get(
                "X-RTM-Device"
            )
            if self.mode == "create":
                return _FakeResponse(
                    200,
                    {
                        "items": [],
                        "pagination": {
                            "limit": 100,
                            "offset": 0,
                            "total": 0,
                        },
                    },
                )
            return _FakeResponse(
                200,
                {
                    "items": [{"id": TARGET_ID, "email": TARGET_EMAIL}],
                    "pagination": {
                        "limit": 100,
                        "offset": 0,
                        "total": 1,
                    },
                },
            )
        if method == "GET" and path == f"/ops/admin/operators/{TARGET_ID}":
            self.calls.append("detail")
            profile = (
                {"synthetic": False}
                if self.mode == "collision"
                else {
                    "synthetic": True,
                    "environment": "staging",
                    "purpose": "controlled_operator_lifecycle",
                }
            )
            return _FakeResponse(
                200,
                {
                    "operator": {
                        "id": TARGET_ID,
                        "email": TARGET_EMAIL,
                        "display_name": TARGET_NAME,
                        "status": "active",
                        "role_code": "rtm.operator",
                        "must_change_password": False,
                        "profile": profile,
                    }
                },
            )
        if method == "POST" and path == "/ops/admin/operators":
            self.calls.append("create")
            self.create_payload.update(kwargs.get("json") or {})
            self.seen_device_header = (kwargs.get("headers") or {}).get(
                "X-RTM-Device"
            )
            return _FakeResponse(
                201,
                {
                    "ok": True,
                    "operator": {
                        "operator_id": TARGET_ID,
                        "email": TARGET_EMAIL,
                        "display_name": TARGET_NAME,
                        "status": "active",
                        "role_code": "rtm.operator",
                        "must_change_password": True,
                    },
                    "audit_event_id": AUDIT_ID,
                    "temporary_password_returned": False,
                },
            )
        if method == "POST" and path == "/ops/auth/logout":
            self.calls.append("logout")
            authorization = (kwargs.get("headers") or {}).get(
                "Authorization"
            )
            if authorization != f"Bearer {'T' * 64}":
                return _FakeResponse(401, {"ok": False})
            return _FakeResponse(200, {"ok": True, "status": "closed"})
        return _FakeResponse(404, {"ok": False})


def _safe_environment() -> dict[str, str]:
    return {
        "RTM_ENV": "staging",
        "RTM_DATA_NAMESPACE": "rtm_staging",
        "RTM_ENVIRONMENT_CONFIRMATION": "RTM_STAGING_ISOLATED",
        "RTM_SIDE_EFFECT_POLICY": "isolated",
        "RTM_ALLOW_REAL_CUSTOMER_DATA": "false",
        "RTM_ENABLE_OPERATOR_AUTH_V1": "true",
        "RTM_ENABLE_OPERATOR_ADMIN_V1": "true",
        "RTM_ENABLE_OPERATOR_LIFECYCLE_V1": "true",
    }


class StagingOperatorLifecycleCreateScriptTest(unittest.TestCase):
    def test_parser_is_dry_run_by_default_and_has_exact_safe_defaults(self):
        args = create_script._parser().parse_args([])
        self.assertFalse(args.apply)
        self.assertFalse(args.generate_password)
        self.assertEqual(
            args.supervisor_email,
            "rtm-staging-supervisor@example.com",
        )
        self.assertEqual(
            args.email,
            "rtm-staging-operador-02@example.com",
        )
        self.assertEqual(args.display_name, "RTM STAGING OPERADOR 02")

        for forbidden in ("--password", "--temporary-password", "--role", "--base-url"):
            with self.subTest(forbidden=forbidden):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        create_script._parser().parse_args(
                            [forbidden, "not-allowed"]
                        )

    def test_safety_barriers_are_complete_and_fail_closed(self):
        safe = _safe_environment()
        self.assertEqual(create_script._safety_blockers(safe), [])
        cases = {
            "RTM_ENV": "production",
            "RTM_DATA_NAMESPACE": "rtm_production",
            "RTM_ENVIRONMENT_CONFIRMATION": "WRONG",
            "RTM_SIDE_EFFECT_POLICY": "live",
            "RTM_ALLOW_REAL_CUSTOMER_DATA": "true",
            "RTM_ENABLE_OPERATOR_AUTH_V1": "false",
            "RTM_ENABLE_OPERATOR_ADMIN_V1": "maybe",
            "RTM_ENABLE_OPERATOR_LIFECYCLE_V1": "false",
        }
        for name, value in cases.items():
            with self.subTest(name=name):
                candidate = dict(safe)
                candidate[name] = value
                self.assertTrue(
                    create_script._safety_blockers(candidate),
                    name,
                )

    def test_production_is_rejected_before_tty_or_runtime_import(self):
        env = dict(os.environ)
        env.update(_safe_environment())
        env.update(
            {
                "RTM_ENV": "production",
                "RTM_DATA_NAMESPACE": "rtm_production",
                "RTM_SIDE_EFFECT_POLICY": "live",
            }
        )
        process = subprocess.run(
            [sys.executable, str(SCRIPT), "--apply", "--compact"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(process.returncode, 2)
        payload = json.loads(process.stdout)
        self.assertIn("RTM_ENV_must_be_staging", payload["blockers"])
        self.assertNotIn("interactive_tty_required", payload["blockers"])
        self.assertNotIn("ModuleNotFoundError", process.stderr)

    def test_generated_secret_is_written_once_only_to_a_tty(self):
        tty = _TTY()
        create_script._reveal_generated_secret_once(
            TEMPORARY_PASSWORD,
            stream=tty,
        )
        self.assertEqual(tty.getvalue().count(TEMPORARY_PASSWORD), 1)
        self.assertIn("NO COPIES ESTE BLOQUE", tty.getvalue())

        with self.assertRaises(create_script.ControlledOperationError) as caught:
            create_script._reveal_generated_secret_once(
                TEMPORARY_PASSWORD,
                stream=io.StringIO(),
            )
        self.assertEqual(caught.exception.code, "temporary_password_requires_tty")

    def test_password_factory_generates_once_or_rejects_manual_mismatch(self):
        runtime_modules = _fake_runtime_modules()
        generate = runtime_modules[
            "rtm_core.operator_provisioning"
        ].generate_temporary_password
        with mock.patch.dict(sys.modules, runtime_modules):
            issued = create_script._password_factory(generate=True)()
        self.assertEqual(issued.value, TEMPORARY_PASSWORD)
        self.assertTrue(issued.generated)
        generate.assert_called_once_with()

        with (
            mock.patch.dict(sys.modules, _fake_runtime_modules()),
            mock.patch.object(
                create_script.getpass,
                "getpass",
                side_effect=["first secret value", "different secret value"],
            ),
        ):
            with self.assertRaises(
                create_script.ControlledOperationError
            ) as caught:
                create_script._password_factory(generate=False)()
        self.assertEqual(
            caught.exception.code,
            "temporary_password_confirmation_mismatch",
        )

    def test_official_route_chain_creates_audits_and_logs_out(self):
        app = _OfficialRouteScenario("create")
        issued_calls = 0

        def issue() -> create_script.IssuedSecret:
            nonlocal issued_calls
            issued_calls += 1
            return create_script.IssuedSecret(
                TEMPORARY_PASSWORD,
                generated=True,
            )

        with mock.patch.dict(
            sys.modules,
            {"httpx": _fake_httpx_module()},
        ):
            outcome = asyncio.run(
                create_script._run_official_routes(
                    app,
                    supervisor_email=create_script.DEFAULT_SUPERVISOR_EMAIL,
                    supervisor_password=SUPERVISOR_PASSWORD,
                    target_email=TARGET_EMAIL,
                    target_display_name=TARGET_NAME,
                    issue_secret=issue,
                )
            )
        self.assertEqual(
            app.calls,
            ["auth_status", "lifecycle_status", "login", "list", "create", "logout"],
        )
        self.assertEqual(app.seen_device_header, "D" * 48)
        self.assertEqual(issued_calls, 1)
        self.assertTrue(outcome.created)
        self.assertTrue(outcome.supervisor_session_closed)
        self.assertEqual(outcome.audit_event_id, AUDIT_ID)
        self.assertEqual(
            app.create_payload,
            {
                "email": TARGET_EMAIL,
                "display_name": TARGET_NAME,
                "temporary_password": TEMPORARY_PASSWORD,
            },
        )
        self.assertNotIn(TEMPORARY_PASSWORD, repr(outcome))
        self.assertNotIn(TEMPORARY_PASSWORD, json.dumps(outcome.operator))

    def test_login_error_cannot_echo_supervisor_secret_or_create(self):
        app = _OfficialRouteScenario("login_error")
        issued = mock.Mock()
        with mock.patch.dict(sys.modules, {"httpx": _fake_httpx_module()}):
            with self.assertRaises(
                create_script.ControlledOperationError
            ) as caught:
                asyncio.run(
                    create_script._run_official_routes(
                        app,
                        supervisor_email=create_script.DEFAULT_SUPERVISOR_EMAIL,
                        supervisor_password=SUPERVISOR_PASSWORD,
                        target_email=TARGET_EMAIL,
                        target_display_name=TARGET_NAME,
                        issue_secret=issued,
                    )
                )
        self.assertEqual(caught.exception.code, "supervisor_login_rejected")
        self.assertNotIn(SUPERVISOR_PASSWORD, str(caught.exception))
        issued.assert_not_called()
        self.assertNotIn("create", app.calls)

    def test_exact_existing_operator_is_idempotent_without_password_rotation(self):
        app = _OfficialRouteScenario("existing")
        issue = mock.Mock()
        with mock.patch.dict(sys.modules, {"httpx": _fake_httpx_module()}):
            outcome = asyncio.run(
                create_script._run_official_routes(
                    app,
                    supervisor_email=create_script.DEFAULT_SUPERVISOR_EMAIL,
                    supervisor_password=SUPERVISOR_PASSWORD,
                    target_email=TARGET_EMAIL,
                    target_display_name=TARGET_NAME,
                    issue_secret=issue,
                )
            )
        self.assertEqual(outcome.status, "already_exists")
        self.assertFalse(outcome.created)
        self.assertIsNone(outcome.issued_secret)
        self.assertTrue(outcome.supervisor_session_closed)
        issue.assert_not_called()
        self.assertEqual(
            app.calls,
            ["auth_status", "lifecycle_status", "login", "list", "detail", "logout"],
        )

    def test_existing_identity_mismatch_fails_closed_and_logs_out(self):
        app = _OfficialRouteScenario("collision")
        issue = mock.Mock()
        with mock.patch.dict(sys.modules, {"httpx": _fake_httpx_module()}):
            with self.assertRaises(
                create_script.ControlledOperationError
            ) as caught:
                asyncio.run(
                    create_script._run_official_routes(
                        app,
                        supervisor_email=create_script.DEFAULT_SUPERVISOR_EMAIL,
                        supervisor_password=SUPERVISOR_PASSWORD,
                        target_email=TARGET_EMAIL,
                        target_display_name=TARGET_NAME,
                        issue_secret=issue,
                    )
                )
        self.assertEqual(caught.exception.code, "operator_identity_collision")
        self.assertTrue(caught.exception.supervisor_session_closed)
        issue.assert_not_called()
        self.assertEqual(app.calls[-1], "logout")

    def test_main_success_keeps_json_clean_and_reveals_generated_secret_once(self):
        fake_app_module = ModuleType("app")
        fake_app_module.app = object()

        async def fake_run(_app, **kwargs):
            self.assertEqual(kwargs["supervisor_password"], SUPERVISOR_PASSWORD)
            issued = kwargs["issue_secret"]()
            return create_script.OperationOutcome(
                status="created",
                operator={
                    "operator_id": TARGET_ID,
                    "email": TARGET_EMAIL,
                    "display_name": TARGET_NAME,
                    "status": "active",
                    "role_code": "rtm.operator",
                    "must_change_password": True,
                },
                audit_event_id=AUDIT_ID,
                issued_secret=issued,
                supervisor_session_closed=True,
            )

        stdout = io.StringIO()
        stderr = _TTY()
        runtime_modules = _fake_runtime_modules()
        runtime_modules["app"] = fake_app_module
        with (
            mock.patch.object(create_script, "_safety_blockers", return_value=[]),
            mock.patch.object(create_script, "_interactive_tty_ready", return_value=True),
            mock.patch.object(create_script, "_prompt_literal", return_value=create_script.CONFIRMATION),
            mock.patch.object(create_script, "_prompt_supervisor_password", return_value=SUPERVISOR_PASSWORD),
            mock.patch.object(create_script, "_run_official_routes", side_effect=fake_run),
            mock.patch.dict(sys.modules, runtime_modules),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = create_script.main(
                ["--apply", "--generate-password", "--compact"]
            )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["operator_created"])
        self.assertFalse(payload["temporary_password_returned"])
        self.assertNotIn(TEMPORARY_PASSWORD, stdout.getvalue())
        self.assertNotIn(SUPERVISOR_PASSWORD, stdout.getvalue())
        self.assertEqual(stderr.getvalue().count(TEMPORARY_PASSWORD), 1)

    def test_main_requires_literal_confirmation_before_password_or_app(self):
        stdout = io.StringIO()
        with (
            mock.patch.object(create_script, "_safety_blockers", return_value=[]),
            mock.patch.object(create_script, "_interactive_tty_ready", return_value=True),
            mock.patch.object(create_script, "_prompt_literal", return_value="NO"),
            mock.patch.object(create_script, "_prompt_supervisor_password") as password,
            mock.patch.dict(sys.modules, _fake_runtime_modules()),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            exit_code = create_script.main(["--apply", "--compact"])
        self.assertEqual(exit_code, 2)
        password.assert_not_called()
        payload = json.loads(stdout.getvalue())
        self.assertIn(
            "invalid_lifecycle_create_confirmation",
            payload["blockers"],
        )

    def test_source_has_no_database_bypass_or_remote_transport(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("httpx.ASGITransport(", source)
        self.assertIn("from app import app as rtm_app", source)
        self.assertIn("assert_environment_ready()", source)
        self.assertIn(create_script.CONFIRMATION, source)
        self.assertIn("getpass.getpass(", source)
        self.assertNotIn("--password", source)
        self.assertNotIn("--base-url", source)
        self.assertNotIn("get_engine", source)
        self.assertNotIn("DATABASE_URL", source)
        self.assertNotIn("requests.", source)
        for sql_verb in ("INSERT INTO", "UPDATE rtm_", "DELETE FROM", "DROP TABLE", "TRUNCATE"):
            self.assertNotIn(sql_verb, source)


if __name__ == "__main__":
    unittest.main()
