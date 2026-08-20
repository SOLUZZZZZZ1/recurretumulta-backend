"""Rutas staging de login individual y sesión de operadores RTM.

El router convive con ``POST /ops/login``. La activación requiere
``RTM_ENABLE_OPERATOR_AUTH_V1=1`` y queda cerrada fuera de staging.
No contiene rutas de creación de operadores.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.engine import Connection

from database import get_engine
from rtm_core.operator_auth_request import (
    OperatorAuthRoutesDisabled,
    OperatorAuthRuntimeMisconfigured,
    build_request_fingerprint,
    extract_bearer_token,
    load_operator_auth_runtime_config,
)
from rtm_core.operator_auth_service import (
    load_operator_session,
    login_operator,
    logout_operator,
)


OPERATOR_AUTH_ROUTES_VERSION = "rtm_operator_auth_routes_v1_0"
router = APIRouter(prefix="/ops/auth", tags=["ops-operator-auth"])


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OperatorLoginRequest(_StrictModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256, repr=False)


async def operator_auth_connection() -> AsyncIterator[Connection]:
    engine = get_engine()
    with engine.begin() as conn:
        yield conn


def _runtime_config():
    try:
        return load_operator_auth_runtime_config(require_enabled=True)
    except OperatorAuthRoutesDisabled as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc
    except OperatorAuthRuntimeMisconfigured as exc:
        raise HTTPException(
            status_code=503,
            detail="Autenticación individual no disponible",
        ) from exc


def _fingerprint(request: Request, config):
    client_host = request.client.host if request.client else None
    return build_request_fingerprint(
        request.headers,
        client_host=client_host,
        hmac_key=config.hmac_key,
        trust_proxy_headers=config.trust_proxy_headers,
    )


def _session_payload(session) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "operator": {
            "id": session.operator_id,
            "email": session.email,
            "display_name": session.display_name,
            "role_code": session.role_code,
            "permissions": list(session.permissions),
            "must_change_password": session.must_change_password,
            "mfa_required": session.mfa_required,
        },
        "expires_at": session.expires_at,
        "absolute_expires_at": session.absolute_expires_at,
    }


@router.get("/status")
async def operator_auth_status() -> dict[str, Any]:
    try:
        config = load_operator_auth_runtime_config(require_enabled=False)
        available = config.available and len(config.hmac_key) >= 32
        configuration_valid = True
    except OperatorAuthRuntimeMisconfigured:
        available = False
        configuration_valid = False
    return {
        "ok": True,
        "version": OPERATOR_AUTH_ROUTES_VERSION,
        "individual_login_enabled": available,
        "configuration_valid": configuration_valid,
        "staging_only": True,
        "legacy_login_unchanged": True,
        "operator_creation_available": False,
    }


@router.post("/login")
async def operator_login(
    payload: OperatorLoginRequest,
    request: Request,
    x_rtm_device: str | None = Header(
        default=None,
        alias="X-RTM-Device",
    ),
    conn: Connection = Depends(operator_auth_connection),
):
    config = _runtime_config()
    context = _fingerprint(request, config)
    decision = login_operator(
        conn,
        email=payload.email,
        password=payload.password,
        device_token=x_rtm_device,
        context=context,
        config=config,
    )
    if not decision.ok:
        headers = {}
        if decision.retry_after:
            headers["Retry-After"] = str(decision.retry_after)
        return JSONResponse(
            status_code=decision.status_code,
            content={
                "ok": False,
                "detail": decision.detail,
                "request_id": context.request_id,
            },
            headers=headers,
        )
    return {
        "ok": True,
        "token_type": "bearer",
        "token": decision.token,
        "session_id": decision.session_id,
        "expires_at": decision.expires_at,
        "absolute_expires_at": decision.absolute_expires_at,
        "device_token": decision.device_token,
        "device_id": decision.device_id,
        "operator": decision.operator,
        "request_id": context.request_id,
        "legacy_login_unchanged": True,
    }


@router.get("/me")
async def operator_me(
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
    ),
    conn: Connection = Depends(operator_auth_connection),
):
    _runtime_config()
    raw_token = extract_bearer_token(authorization)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Sesión no válida")
    session = load_operator_session(conn, raw_token=raw_token, touch=True)
    if not session:
        raise HTTPException(status_code=401, detail="Sesión no válida")
    return {"ok": True, **_session_payload(session)}


@router.post("/heartbeat")
async def operator_heartbeat(
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
    ),
    conn: Connection = Depends(operator_auth_connection),
):
    _runtime_config()
    raw_token = extract_bearer_token(authorization)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Sesión no válida")
    session = load_operator_session(conn, raw_token=raw_token, touch=True)
    if not session:
        raise HTTPException(status_code=401, detail="Sesión no válida")
    return {
        "ok": True,
        "session_id": session.session_id,
        "server_time": datetime.now(timezone.utc),
        "expires_at": session.expires_at,
        "absolute_expires_at": session.absolute_expires_at,
    }


@router.post("/logout")
async def operator_logout(
    request: Request,
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
    ),
    conn: Connection = Depends(operator_auth_connection),
):
    config = _runtime_config()
    raw_token = extract_bearer_token(authorization)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Sesión no válida")
    context = _fingerprint(request, config)
    closed = logout_operator(
        conn,
        raw_token=raw_token,
        context=context,
        config=config,
    )
    if not closed:
        raise HTTPException(status_code=401, detail="Sesión no válida")
    return {
        "ok": True,
        "status": "closed",
        "request_id": context.request_id,
    }


__all__ = [
    "OPERATOR_AUTH_ROUTES_VERSION",
    "operator_auth_connection",
    "router",
]
