from __future__ import annotations

import asyncio
import ast
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from cases import CaseContactIn, CaseDetailsIn
import app as backend_app
from rtm_core import http_security
from fastapi.testclient import TestClient

from rtm_core.http_security import (
    ABSOLUTE_MAX_REQUEST_BODY_BYTES,
    DEFAULT_MAX_REQUEST_BODY_BYTES,
    DEFAULT_SENSITIVE_RATE_RULES,
    LOCAL_ALLOWED_HOSTS,
    ExactHostMiddleware,
    RequestBodyLimitMiddleware,
    SecurityHeaderAmbiguityMiddleware,
    SecurityHeadersMiddleware,
    SensitiveRateLimitMiddleware,
    configured_allowed_hosts,
    configured_request_body_limit,
    parse_allowed_hosts,
    parse_allowed_origins,
    scope_path,
    trusted_client_ip,
)


def _scope(*, content_length: str | None = None, scheme: str = "http"):
    headers = []
    if content_length is not None:
        headers.append((b"content-length", content_length.encode("ascii")))
    return {
        "type": "http",
        "method": "POST",
        "scheme": scheme,
        "path": "/partner/login",
        "raw_path": b"/partner/login",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
        "http_version": "1.1",
    }


async def _run_asgi(app, scope, messages):
    sent = []
    queue = list(messages)

    async def receive():
        if queue:
            return queue.pop(0)
        await asyncio.sleep(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


async def _echo_body(scope, receive, send):
    body = b""
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        body += message.get("body", b"")
        if not message.get("more_body", False):
            break
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


class HttpSecurityTest(unittest.IsolatedAsyncioTestCase):
    def test_every_fastapi_header_parameter_is_singleton(self):
        """Impide que una nueva capacidad HTTP quede fuera del anti-smuggling."""

        root = Path(__file__).resolve().parents[1]
        aliases: set[str] = set()
        for path in root.rglob("*.py"):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            for function in (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ):
                positional = list(function.args.args)
                defaults = [None] * (len(positional) - len(function.args.defaults))
                defaults.extend(function.args.defaults)
                parameters = list(zip(positional, defaults))
                parameters.extend(
                    zip(function.args.kwonlyargs, function.args.kw_defaults)
                )
                for parameter, default in parameters:
                    if not isinstance(default, ast.Call):
                        continue
                    call_name = (
                        default.func.id
                        if isinstance(default.func, ast.Name)
                        else ""
                    )
                    if call_name != "Header":
                        continue
                    alias = parameter.arg.replace("_", "-")
                    for keyword in default.keywords:
                        if (
                            keyword.arg == "alias"
                            and isinstance(keyword.value, ast.Constant)
                            and isinstance(keyword.value.value, str)
                        ):
                            alias = keyword.value.value
                    aliases.add(alias.casefold())

        protected = {
            name.decode("ascii")
            for name in http_security._SINGLETON_SECURITY_HEADERS
        }
        self.assertFalse(
            aliases - protected,
            f"FastAPI Header sin defensa singleton: {sorted(aliases - protected)}",
        )

    async def test_duplicate_singleton_security_headers_are_rejected_early(self):
        middleware = SecurityHeaderAmbiguityMiddleware(_echo_body)
        for name in (
            b"authorization",
            b"content-length",
            b"content-type",
            b"host",
            b"idempotency-key",
            b"if-match",
            b"origin",
            b"stripe-signature",
            b"transfer-encoding",
            b"x-admin-token",
            b"x-csrf-token",
            b"x-lab-key",
            b"x-operator-actor",
            b"x-operator-token",
            b"x-request-id",
            b"x-reservas-pin",
            b"x-rtm-attachment-manifest-sha256",
            b"x-rtm-case-token",
            b"x-rtm-device",
            b"x-rtm-observed-portal-origin",
            b"x-rtm-presenter-extension",
            b"x-rtm-receipt-capture-source",
            b"x-rtm-receipt-filename",
            b"x-rtm-receipt-media-type",
            b"x-rtm-synthetic-confirmed",
        ):
            with self.subTest(name=name):
                scope = _scope()
                scope["headers"] = [
                    (name, b"first-value"),
                    (name.upper(), b"second-value"),
                ]
                sent = await _run_asgi(
                    middleware,
                    scope,
                    [{"type": "http.request", "body": b"", "more_body": False}],
                )
                self.assertEqual(sent[0]["status"], 400)
                self.assertEqual(
                    json.loads(sent[1]["body"]),
                    {"detail": "Cabeceras de seguridad ambiguas"},
                )

    async def test_duplicate_sensitive_cookie_names_are_rejected(self):
        middleware = SecurityHeaderAmbiguityMiddleware(_echo_body)
        cookie_sets = (
            [
                (
                    b"cookie",
                    b"__Host-rtm_partner_session=one; "
                    b"__Host-rtm_partner_session=two",
                )
            ],
            [
                (b"cookie", b"__Host-rtm_partner_csrf=one"),
                (b"cookie", b"__Host-rtm_partner_csrf=two"),
            ],
            [
                (b"cookie", b"__Host-rtm_presenter_device=one"),
                (b"cookie", b"ordinary=value; __Host-rtm_presenter_device=two"),
            ],
            [
                (
                    b"cookie",
                    b"__Host-rtm_partner_session =one; "
                    b"__Host-rtm_partner_session=two",
                )
            ],
        )
        for headers in cookie_sets:
            with self.subTest(headers=headers):
                scope = _scope()
                scope["headers"] = headers
                sent = await _run_asgi(
                    middleware,
                    scope,
                    [{"type": "http.request", "body": b"", "more_body": False}],
                )
                self.assertEqual(sent[0]["status"], 400)

    async def test_transfer_encoding_is_rejected_at_the_asgi_boundary(self):
        middleware = SecurityHeaderAmbiguityMiddleware(_echo_body)
        for headers in (
            [(b"transfer-encoding", b"chunked")],
            [
                (b"content-length", b"4"),
                (b"transfer-encoding", b"chunked"),
            ],
        ):
            with self.subTest(headers=headers):
                scope = _scope()
                scope["headers"] = headers
                sent = await _run_asgi(
                    middleware,
                    scope,
                    [{"type": "http.request", "body": b"test", "more_body": False}],
                )
                self.assertEqual(sent[0]["status"], 400)
                self.assertEqual(
                    json.loads(sent[1]["body"]),
                    {"detail": "Cabeceras de seguridad ambiguas"},
                )

    async def test_http2_cookie_crumbling_is_safely_normalized(self):
        observed: dict[str, list[tuple[bytes, bytes]]] = {}

        async def capture(scope, receive, send):
            observed["headers"] = list(scope["headers"])
            await _echo_body(scope, receive, send)

        middleware = SecurityHeaderAmbiguityMiddleware(capture)
        scope = _scope()
        scope["http_version"] = "2"
        scope["headers"] = [
            (
                b"cookie",
                b"ordinary=one; __Host-rtm_partner_session=session",
            ),
            (
                b"cookie",
                b"__Host-rtm_partner_csrf=csrf; preference=compact",
            ),
        ]
        sent = await _run_asgi(
            middleware,
            scope,
            [{"type": "http.request", "body": b"", "more_body": False}],
        )

        self.assertEqual(sent[0]["status"], 200)
        cookies = [
            value
            for name, value in observed["headers"]
            if name.lower() == b"cookie"
        ]
        self.assertEqual(
            cookies,
            [
                b"ordinary=one; __Host-rtm_partner_session=session; "
                b"__Host-rtm_partner_csrf=csrf; preference=compact"
            ],
        )

    def test_legacy_operator_login_is_rate_limited_during_migration(self):
        self.assertIn(("POST", "/ops/login"), DEFAULT_SENSITIVE_RATE_RULES)

    def test_individual_auth_and_admin_mutations_are_rate_limited(self):
        for key in (
            ("POST", "/ops/auth/login"),
            ("POST", "/ops/auth/reauthenticate"),
            ("POST", "/ops/auth/logout"),
            ("POST", "/ops/auth/password/change"),
            ("POST", "/ops/admin/operators"),
            ("POST", "/ops/admin/operators/*"),
            ("POST", "/ops/admin/sessions/*"),
            ("POST", "/ops/admin/devices/*"),
        ):
            with self.subTest(key=key):
                self.assertIn(key, DEFAULT_SENSITIVE_RATE_RULES)

    def test_public_case_mutations_have_dynamic_rate_limits(self):
        self.assertIn(("POST", "/cases/*/contact"), DEFAULT_SENSITIVE_RATE_RULES)
        self.assertIn(("POST", "/cases/*/details"), DEFAULT_SENSITIVE_RATE_RULES)
        self.assertIn(
            ("POST", "/cases/*/append-documents"),
            DEFAULT_SENSITIVE_RATE_RULES,
        )
        self.assertIn(("POST", "/cases/*/authorize"), DEFAULT_SENSITIVE_RATE_RULES)
        self.assertIn(("GET", "/files/presign"), DEFAULT_SENSITIVE_RATE_RULES)

    def test_paid_provider_entrypoints_have_cost_rate_limits(self):
        for key in (
            ("POST", "/billing/checkout"),
            ("POST", "/checkout"),
            ("POST", "/vehicle-removal/verify-registration"),
            ("POST", "/vehicle-removal/create-checkout-session"),
            ("POST", "/ops/automation/tick"),
            ("POST", "/ops/core/cases/*/document-extractions/run"),
        ):
            with self.subTest(key=key):
                self.assertIn(key, DEFAULT_SENSITIVE_RATE_RULES)

    def test_public_payment_reads_and_restaurant_admin_are_rate_limited(self):
        for key in (
            ("GET", "/billing/review-context/*"),
            ("GET", "/billing/status/*"),
            ("GET", "/status/*"),
            ("GET", "/vehicle-removal/quote"),
            ("POST", "/ops/admin/restaurants/create"),
        ):
            with self.subTest(key=key):
                self.assertIn(key, DEFAULT_SENSITIVE_RATE_RULES)

    def test_public_case_models_reject_oversize_and_unknown_fields(self):
        with self.assertRaises(ValidationError):
            CaseContactIn(name="x" * 161, email="valid@example.com")
        with self.assertRaises(ValidationError):
            CaseContactIn(name="Valid", email="valid@example.com", injected="x")
        with self.assertRaises(ValidationError):
            CaseDetailsIn(
                full_name="Valid",
                dni_nie="12345678Z",
                domicilio_notif="x" * 501,
                email="valid@example.com",
            )

    async def test_validation_error_never_reflects_attacker_input(self):
        marker = "SECRET-CANARY-" + ("x" * 10_000)
        request = type("Request", (), {"scope": {"path": "/cases/x/contact"}})()
        error = RequestValidationError(
            [
                {
                    "type": "string_too_long",
                    "loc": ("body", "name"),
                    "msg": "String should have at most 160 characters",
                    "input": marker,
                }
            ]
        )
        response = await backend_app.redact_request_validation_error(request, error)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(marker.encode(), response.body)
        self.assertLess(len(response.body), 1024)

    def test_deployed_profiles_hide_interactive_api_schema(self):
        source = Path(backend_app.__file__).read_text(encoding="utf-8")
        self.assertIn('docs_url=None if _DEPLOYED_PROFILE else "/docs"', source)
        self.assertIn('openapi_url=None if _DEPLOYED_PROFILE else "/openapi.json"', source)

    def test_trailing_slash_never_redirects_to_an_attacker_host(self):
        with TestClient(
            backend_app.app,
            follow_redirects=False,
        ) as client:
            response = client.get(
                "/health/live/",
                headers={"Host": "attacker.example"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("location", response.headers)

    def test_application_accepts_only_its_exact_local_host(self):
        with TestClient(backend_app.app) as client:
            accepted = client.get("/health/live", headers={"Host": "testserver"})
            rejected = client.get(
                "/health/live",
                headers={"Host": "testserver.attacker.invalid"},
            )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.json(), {"detail": "Host no autorizado"})

    def test_startup_preflight_always_runs_for_deployed_profiles(self):
        for environment in ("staging", "production"):
            with (
                self.subTest(environment=environment),
                patch.dict(os.environ, {"RTM_ENV": environment}, clear=True),
                patch.object(backend_app, "assert_environment_ready") as preflight,
                patch.object(backend_app, "extraction_limits") as limits,
                patch.object(
                    backend_app,
                    "assert_parser_isolation_ready",
                ) as parser_preflight,
            ):
                backend_app.validate_deployed_environment()
                preflight.assert_called_once_with()
                limits.assert_called_once_with()
                parser_preflight.assert_called_once_with()

    def test_startup_fails_closed_for_missing_or_misspelled_deployed_env(self):
        scenarios = (
            {"RENDER_SERVICE_ID": "srv-rtm"},
            {"RTM_ENV": "stagin", "DATABASE_URL": "postgresql://deployed/rtm"},
            {"RTM_ENV": "development", "RENDER_SERVICE_NAME": "rtm-api"},
        )
        for environment in scenarios:
            with (
                self.subTest(environment=environment),
                patch.dict(os.environ, environment, clear=True),
                patch.object(
                    backend_app,
                    "assert_environment_ready",
                    side_effect=RuntimeError("blocked"),
                ) as preflight,
                patch.object(backend_app, "extraction_limits") as limits,
                self.assertRaisesRegex(RuntimeError, "blocked"),
            ):
                backend_app.validate_deployed_environment()
            preflight.assert_called_once_with()
            limits.assert_not_called()

    def test_empty_local_runtime_does_not_run_deployment_preflight(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(backend_app, "assert_environment_ready") as preflight,
            patch.object(backend_app, "extraction_limits") as limits,
        ):
            backend_app.validate_deployed_environment()
        preflight.assert_not_called()
        limits.assert_not_called()

    def test_scope_path_does_not_use_reconstructed_url_or_host(self):
        request_like = type(
            "RequestLike",
            (),
            {
                "scope": {"path": "/ops/auth/status"},
                "url": type("URL", (), {"path": "/attacker-controlled"})(),
            },
        )()
        self.assertEqual(scope_path(request_like), "/ops/auth/status")

    def test_cors_is_explicit_https_and_fail_closed(self):
        self.assertEqual(parse_allowed_origins(None), [])
        self.assertEqual(
            parse_allowed_origins(
                "https://www.recurretumulta.eu, https://staging.recurretumulta.eu/"
            ),
            [
                "https://www.recurretumulta.eu",
                "https://staging.recurretumulta.eu",
            ],
        )
        for value in (
            "*",
            "https://good.example/hidden-path",
            "https://user:pass@good.example",
            "http://public.example",
            "//public.example",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_allowed_origins(value)

    def test_allowed_hosts_are_exact_and_environment_scoped(self):
        self.assertEqual(
            parse_allowed_hosts("API.Example.invalid, 127.0.0.1, [::1]"),
            ["api.example.invalid", "127.0.0.1", "::1"],
        )
        self.assertEqual(configured_allowed_hosts({}), LOCAL_ALLOWED_HOSTS)
        self.assertEqual(
            configured_allowed_hosts(
                {"RTM_ENV": "test", "RTM_ALLOWED_HOSTS": "testserver,::1"}
            ),
            ("testserver", "::1"),
        )
        self.assertEqual(
            configured_allowed_hosts({"RTM_ENV": "staging"}),
            (),
        )
        self.assertEqual(
            configured_allowed_hosts(
                {
                    "RTM_ENV": "production",
                    "RTM_ALLOWED_HOSTS": "api.example.invalid",
                }
            ),
            ("api.example.invalid",),
        )

        for value in (
            "*",
            "*.example.invalid",
            "https://api.example.invalid",
            "api.example.invalid:443",
            "api.example.invalid/path",
            "api.example.invalid,",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_allowed_hosts(value)
        with self.assertRaises(ValueError):
            configured_allowed_hosts(
                {
                    "RTM_ENV": "development",
                    "RTM_ALLOWED_HOSTS": "api.example.invalid",
                }
            )

    async def test_exact_host_gate_accepts_ports_but_rejects_ambiguity(self):
        app = ExactHostMiddleware(_echo_body, ["api.example.invalid", "::1"])
        for value in (b"api.example.invalid", b"API.EXAMPLE.INVALID:8443", b"[::1]:443"):
            with self.subTest(value=value):
                scope = _scope()
                scope["headers"] = [(b"host", value)]
                sent = await _run_asgi(
                    app,
                    scope,
                    [{"type": "http.request", "body": b"", "more_body": False}],
                )
                self.assertEqual(sent[0]["status"], 200)

        for headers in (
            [],
            [(b"host", b"attacker.invalid")],
            [(b"host", b"api.example.invalid.attacker.invalid")],
            [(b"host", b"api.example.invalid:0")],
            [(b"host", b"::1")],
            [(b"host", b"api.example.invalid/path")],
            [(b"host", b"api.example.invalid"), (b"host", b"attacker.invalid")],
        ):
            with self.subTest(headers=headers):
                scope = _scope()
                scope["headers"] = headers
                sent = await _run_asgi(
                    app,
                    scope,
                    [{"type": "http.request", "body": b"", "more_body": False}],
                )
                self.assertEqual(sent[0]["status"], 400)

    def test_request_limit_configuration_is_bounded(self):
        self.assertEqual(configured_request_body_limit({}), DEFAULT_MAX_REQUEST_BODY_BYTES)
        self.assertEqual(
            configured_request_body_limit({"RTM_MAX_REQUEST_BODY_BYTES": "2048"}),
            2048,
        )
        for value in ("no", "0", str(ABSOLUTE_MAX_REQUEST_BODY_BYTES + 1)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                configured_request_body_limit({"RTM_MAX_REQUEST_BODY_BYTES": value})

    def test_forwarded_ip_requires_an_explicit_trusted_proxy(self):
        scope = _scope()
        scope["client"] = ("10.0.0.7", 1234)
        scope["headers"].append((b"x-forwarded-for", b"203.0.113.25, 10.0.0.7"))
        self.assertEqual(trusted_client_ip(scope, {}), "10.0.0.7")
        self.assertEqual(
            trusted_client_ip(scope, {"RTM_TRUSTED_PROXY_CIDRS": "10.0.0.0/8"}),
            "10.0.0.7",
        )
        trusted_environment = {
            "RTM_TRUST_PROXY_HEADERS": "true",
            "RTM_TRUSTED_PROXY_CIDRS": "10.0.0.0/8",
        }
        self.assertEqual(
            trusted_client_ip(scope, trusted_environment),
            "203.0.113.25",
        )
        scope["headers"][-1] = (
            b"x-forwarded-for",
            b"198.51.100.66, 203.0.113.25, 10.0.0.7",
        )
        self.assertEqual(
            trusted_client_ip(scope, trusted_environment),
            "203.0.113.25",
        )

    def test_forwarded_ip_fail_closes_for_invalid_proxy_flag(self):
        scope = _scope()
        scope["client"] = ("10.0.0.7", 1234)
        scope["headers"].append((b"x-forwarded-for", b"203.0.113.25"))
        self.assertEqual(
            trusted_client_ip(
                scope,
                {
                    "RTM_TRUST_PROXY_HEADERS": "sometimes",
                    "RTM_TRUSTED_PROXY_CIDRS": "10.0.0.0/8",
                },
            ),
            "10.0.0.7",
        )

    def test_duplicate_forwarded_headers_fail_closed_to_direct_peer(self):
        scope = _scope()
        scope["client"] = ("10.0.0.7", 1234)
        scope["headers"].extend(
            (
                (b"x-forwarded-for", b"198.51.100.10"),
                (b"x-forwarded-for", b"203.0.113.25"),
            )
        )
        self.assertEqual(
            trusted_client_ip(
                scope,
                {
                    "RTM_TRUST_PROXY_HEADERS": "true",
                    "RTM_TRUSTED_PROXY_CIDRS": "10.0.0.0/8",
                },
            ),
            "10.0.0.7",
        )

    async def test_declared_oversize_body_is_rejected_before_router(self):
        called = False

        async def inner(scope, receive, send):
            nonlocal called
            called = True

        app = RequestBodyLimitMiddleware(inner, max_body_bytes=1024)
        sent = await _run_asgi(
            app,
            _scope(content_length="1025"),
            [{"type": "http.request", "body": b"", "more_body": False}],
        )
        self.assertFalse(called)
        self.assertEqual(sent[0]["status"], 413)

    async def test_content_length_uses_only_http_decimal_grammar(self):
        app = RequestBodyLimitMiddleware(_echo_body, max_body_bytes=1024)
        for value in ("+4", "-1", "4,4", "4x"):
            with self.subTest(value=value):
                sent = await _run_asgi(
                    app,
                    _scope(content_length=value),
                    [{"type": "http.request", "body": b"test", "more_body": False}],
                )
                self.assertEqual(sent[0]["status"], 400)

    async def test_chunked_oversize_body_is_rejected(self):
        app = RequestBodyLimitMiddleware(_echo_body, max_body_bytes=1024)
        sent = await _run_asgi(
            app,
            _scope(),
            [
                {"type": "http.request", "body": b"a" * 700, "more_body": True},
                {"type": "http.request", "body": b"b" * 400, "more_body": False},
            ],
        )
        self.assertEqual(sent[0]["status"], 413)
        self.assertIn("limite", json.loads(sent[1]["body"])["detail"])

    async def test_security_headers_cover_https_json_responses(self):
        app = SecurityHeadersMiddleware(_echo_body)
        sent = await _run_asgi(
            app,
            _scope(content_length="0", scheme="https"),
            [{"type": "http.request", "body": b"", "more_body": False}],
        )
        headers = dict(sent[0]["headers"])
        self.assertIn(b"no-store", headers[b"cache-control"])
        self.assertEqual(headers[b"x-content-type-options"], b"nosniff")
        self.assertEqual(headers[b"x-frame-options"], b"DENY")
        self.assertIn(b"default-src 'none'", headers[b"content-security-policy"])
        self.assertIn(b"max-age=31536000", headers[b"strict-transport-security"])

    async def test_sensitive_route_rate_limit_fails_with_retry_after(self):
        app = SensitiveRateLimitMiddleware(
            _echo_body,
            rules={("POST", "/partner/login"): (2, 60)},
        )
        scope = _scope(content_length="0")
        for _ in range(2):
            sent = await _run_asgi(
                app,
                scope,
                [{"type": "http.request", "body": b"", "more_body": False}],
            )
            self.assertEqual(sent[0]["status"], 200)
        blocked = await _run_asgi(
            app,
            scope,
            [{"type": "http.request", "body": b"", "more_body": False}],
        )
        self.assertEqual(blocked[0]["status"], 429)
        self.assertIn(b"retry-after", dict(blocked[0]["headers"]))

    async def test_read_quota_cannot_exhaust_write_quota_for_same_path(self):
        app = SensitiveRateLimitMiddleware(
            _echo_body,
            rules={
                ("GET", "/partner/cases"): (2, 60),
                ("POST", "/partner/cases"): (1, 60),
            },
        )
        scope = _scope(content_length="0")
        scope["path"] = "/partner/cases"
        scope["raw_path"] = b"/partner/cases"
        scope["method"] = "GET"

        for _ in range(2):
            sent = await _run_asgi(
                app,
                scope,
                [{"type": "http.request", "body": b"", "more_body": False}],
            )
            self.assertEqual(sent[0]["status"], 200)

        scope["method"] = "POST"
        write = await _run_asgi(
            app,
            scope,
            [{"type": "http.request", "body": b"", "more_body": False}],
        )
        self.assertEqual(write[0]["status"], 200)

    async def test_document_extraction_rate_limit_cannot_be_rotated_by_case_id(self):
        rule_path = "/ops/core/cases/*/document-extractions/run"
        limit, _window = DEFAULT_SENSITIVE_RATE_RULES[("POST", rule_path)]
        app = SensitiveRateLimitMiddleware(_echo_body)
        scope = _scope(content_length="0")
        scope["path"] = "/ops/core/cases/first/document-extractions/run"

        for _ in range(limit):
            sent = await _run_asgi(
                app,
                scope,
                [{"type": "http.request", "body": b"", "more_body": False}],
            )
            self.assertEqual(sent[0]["status"], 200)

        # El wildcard usa un único bucket por IP+ruta, no uno nuevo por case_id.
        scope["path"] = "/ops/core/cases/second/document-extractions/run"
        blocked = await _run_asgi(
            app,
            scope,
            [{"type": "http.request", "body": b"", "more_body": False}],
        )
        self.assertEqual(blocked[0]["status"], 429)
        self.assertIn(b"retry-after", dict(blocked[0]["headers"]))

    def test_dynamic_sensitive_route_uses_prefix_rule(self):
        app = SensitiveRateLimitMiddleware(
            _echo_body,
            rules={("POST", "/ops/restaurant-reservations/*"): (2, 60)},
        )
        scope = _scope()
        scope["path"] = "/ops/restaurant-reservations/abc/cancel"
        self.assertEqual(app._rule(scope), (2, 60))
        first_key = app._key(scope)
        scope["path"] = "/ops/restaurant-reservations/different/no-show"
        self.assertEqual(app._key(scope), first_key)

    def test_dynamic_case_route_matches_one_segment_and_shares_bucket(self):
        app = SensitiveRateLimitMiddleware(
            _echo_body,
            rules={("POST", "/cases/*/contact"): (2, 60)},
        )
        scope = _scope()
        scope["path"] = "/cases/first/contact"
        self.assertEqual(app._rule(scope), (2, 60))
        first_key = app._key(scope)
        scope["path"] = "/cases/second/contact"
        self.assertEqual(app._key(scope), first_key)
        scope["path"] = "/cases/second/contact/extra"
        self.assertIsNone(app._rule(scope))


if __name__ == "__main__":
    unittest.main()
