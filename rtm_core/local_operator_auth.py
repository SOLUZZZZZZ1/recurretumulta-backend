"""Explicit, fail-closed boundary for individual OPS access on local Windows.

This profile reuses the individual authentication services. It does not enable
administration, lifecycle, Presenter, Connect, or any external capability.
"""

from __future__ import annotations

import os
from typing import Mapping

from sqlalchemy.engine import make_url
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


LOCAL_OPERATOR_AUTH_ENV = "RTM_ENABLE_LOCAL_OPERATOR_AUTH"
LOCAL_OPERATOR_ORIGIN = "http://127.0.0.1:5173"
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_EXTERNAL_FLAGS = (
    "RTM_ENABLE_B2",
    "RTM_ENABLE_STRIPE",
    "RTM_ENABLE_FINAL_PAYMENTS",
    "RTM_ENABLE_DOCUMENT_PROVIDER",
    "RTM_ENABLE_OUTBOUND_EMAIL",
    "RTM_ENABLE_EXTERNAL_SUBMISSION",
)
_DEPLOYMENT_VARIABLES = (
    "RTM_ENVIRONMENT_CONFIRMATION", "RENDER", "DYNO", "K_SERVICE",
    "FLY_APP_NAME", "RAILWAY_ENVIRONMENT_ID", "VERCEL",
)
_IDENTITY_VARIABLES = ("RTM_INSTANCE_ID", "RTM_DATA_NAMESPACE")
_CONNECTION_OVERRIDE_VARIABLES = ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")


class LocalOperatorAuthMisconfigured(RuntimeError):
    """The requested local profile does not satisfy its isolation contract."""


def _value(source: Mapping[str, str], name: str) -> str:
    return str(source.get(name) or "").strip()


def local_operator_auth_requested(environ: Mapping[str, str] | None = None) -> bool:
    """Invalid nonempty flags count as requested so they cannot reopen legacy."""

    source = environ if environ is not None else os.environ
    raw = _value(source, LOCAL_OPERATOR_AUTH_ENV).casefold()
    return bool(raw) and raw not in _FALSE_VALUES


def assert_local_operator_auth_ready(
    environ: Mapping[str, str] | None = None,
) -> None:
    """Validate configuration without connecting or disclosing secret values."""

    source = environ if environ is not None else os.environ
    blockers: list[str] = []
    exact = {
        "RTM_ENV": "development",
        "RTM_LOCAL_BIND_HOST": "127.0.0.1",
        "RTM_ALLOWED_HOSTS": "127.0.0.1",
        "ALLOWED_ORIGINS": LOCAL_OPERATOR_ORIGIN,
    }
    blockers.extend(
        name for name, expected in exact.items()
        if _value(source, name) != expected
    )
    for name in (LOCAL_OPERATOR_AUTH_ENV, "RTM_ENABLE_OPERATOR_AUTH_V1"):
        if _value(source, name).casefold() not in _TRUE_VALUES:
            blockers.append(name)
    for name in (
        *_EXTERNAL_FLAGS, "RTM_ALLOW_REAL_CUSTOMER_DATA", "RTM_TRUST_PROXY_HEADERS",
    ):
        if _value(source, name).casefold() not in _FALSE_VALUES:
            blockers.append(name)
    if _value(source, "RTM_TRUSTED_PROXY_CIDRS"):
        blockers.append("RTM_TRUSTED_PROXY_CIDRS")
    legacy_block = _value(source, "RTM_BLOCK_LEGACY_OPERATOR_AUTH").casefold()
    if legacy_block and legacy_block not in _TRUE_VALUES | _FALSE_VALUES:
        blockers.append("RTM_BLOCK_LEGACY_OPERATOR_AUTH")
    for name in (*_DEPLOYMENT_VARIABLES, *_CONNECTION_OVERRIDE_VARIABLES):
        if _value(source, name):
            blockers.append(name)
    for name, value in source.items():
        if str(name).startswith(("RENDER_", "VERCEL_")) and str(value or "").strip():
            blockers.append(str(name))
    for name in _IDENTITY_VARIABLES:
        identity = _value(source, name).casefold()
        if "staging" in identity or "production" in identity:
            blockers.append(name)
    for name in ("FRONTEND_URL", "FRONTEND_BASE_URL"):
        if _value(source, name) and _value(source, name) != LOCAL_OPERATOR_ORIGIN:
            blockers.append(name)
    try:
        url = make_url(_value(source, "DATABASE_URL"))
        database_valid = (
            url.drivername == "postgresql+psycopg"
            and url.host == "127.0.0.1"
            and url.port == 5432
            and url.database == "rtm_local"
            and url.username == "rtm_local_app"
            and bool(url.password)
            and not url.query
        )
    except Exception:
        database_valid = False
    if not database_valid:
        blockers.append("DATABASE_URL")
    if blockers:
        raise LocalOperatorAuthMisconfigured(
            "Configuración de OPS local inválida: " + ", ".join(sorted(set(blockers)))
        )


class LocalOperatorAuthMiddleware:
    """Check real ASGI peers and browser origins before local requests proceed."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not local_operator_auth_requested():
            await self.app(scope, receive, send)
            return
        try:
            assert_local_operator_auth_ready()
        except LocalOperatorAuthMisconfigured:
            response = JSONResponse(
                status_code=503,
                content={"detail": "Autenticación local no disponible"},
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return

        request = Request(scope)
        origins = request.headers.getlist("origin")
        fetch_sites = request.headers.getlist("sec-fetch-site")
        peer = scope.get("client")
        server = scope.get("server")
        denied = (
            not peer or peer[0] != "127.0.0.1"
            or not server or server[0] != "127.0.0.1"
            or len(origins) > 1
            or (bool(origins) and origins[0] != LOCAL_OPERATOR_ORIGIN)
            or len(fetch_sites) > 1
            or (bool(fetch_sites) and fetch_sites[0] not in {"same-origin", "none"})
            or (request.method not in {"GET", "HEAD", "OPTIONS"} and not origins)
        )
        if denied:
            response = JSONResponse(
                status_code=403,
                content={"detail": "Acceso local no autorizado"},
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
