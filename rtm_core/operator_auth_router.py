"""Rutas staging de login individual y sesión de operadores RTM.

El router convive con ``POST /ops/login``. La activación requiere
``RTM_ENABLE_OPERATOR_AUTH_V1=1`` y queda cerrada fuera de staging.
No contiene rutas de creación de operadores.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.engine import Connection

from database import get_engine
from rtm_core.operator_auth_crypto import hash_device_secret
from rtm_core.operator_auth_repository import (
    ActiveOperatorSession,
    load_active_operator_session_for_device,
    touch_operator_session,
)
from rtm_core.operator_auth_request import (
    OperatorAuthRoutesDisabled,
    OperatorAuthRuntimeMisconfigured,
    build_request_fingerprint,
    extract_bearer_token,
    load_operator_auth_runtime_config,
    normalize_device_token,
)
from rtm_core.operator_auth_service import (
    load_operator_session,
    login_operator,
    logout_operator,
    reauthenticate_operator,
    record_reauthentication_denial,
)


OPERATOR_AUTH_ROUTES_VERSION = "rtm_operator_auth_routes_v1_2"
router = APIRouter(prefix="/ops/auth", tags=["ops-operator-auth"])
_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}
_DEVICE_COOKIE = "rtm_presenter_device"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OperatorLoginRequest(_StrictModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256, repr=False)


class OperatorReauthenticationRequest(_StrictModel):
    password: str = Field(min_length=1, max_length=256, repr=False)


async def operator_auth_connection() -> AsyncIterator[Connection]:
    engine = get_engine()
    with engine.begin() as conn:
        yield conn


def _runtime_config():
    try:
        return load_operator_auth_runtime_config(require_enabled=True)
    except OperatorAuthRoutesDisabled as exc:
        raise HTTPException(
            status_code=404,
            detail="Not found",
            headers=_NO_STORE_HEADERS,
        ) from exc
    except OperatorAuthRuntimeMisconfigured as exc:
        raise HTTPException(
            status_code=503,
            detail="Autenticación individual no disponible",
            headers=_NO_STORE_HEADERS,
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


def _device_secret_digests(
    x_rtm_device: str | None,
    rtm_presenter_device: str | None,
) -> tuple[str, ...]:
    """Normaliza secretos candidatos y entrega unicamente sus digests."""

    digests: list[str] = []
    for candidate in (x_rtm_device, rtm_presenter_device):
        normalized = normalize_device_token(candidate)
        if normalized is None:
            continue
        digest = hash_device_secret(normalized)
        if digest not in digests:
            digests.append(digest)
    return tuple(digests)


def load_operator_session_with_device_possession(
    conn,
    *,
    authorization: str | None,
    x_rtm_device: str | None,
    rtm_presenter_device: str | None,
    touch: bool,
) -> ActiveOperatorSession | None:
    """Carga una sesion solo si la peticion prueba su dispositivo asociado."""

    raw_token = extract_bearer_token(authorization)
    device_digests = _device_secret_digests(
        x_rtm_device,
        rtm_presenter_device,
    )
    if not raw_token or not device_digests:
        return None
    session = None
    for digest in device_digests:
        session = load_active_operator_session_for_device(
            conn,
            raw_token,
            device_key_sha256=digest,
        )
        if session:
            break
    if not session:
        return None
    if touch:
        touch_operator_session(conn, session.session_id)
    return session


def require_operator_device_possession(
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
    ),
    x_rtm_device: str | None = Header(
        default=None,
        alias="X-RTM-Device",
    ),
    rtm_presenter_device: str | None = Cookie(
        default=None,
        alias=_DEVICE_COOKIE,
    ),
    conn: Connection = Depends(operator_auth_connection),
) -> ActiveOperatorSession:
    """Dependencia reutilizable para superficies ligadas al dispositivo."""

    _runtime_config()
    session = load_operator_session_with_device_possession(
        conn,
        authorization=authorization,
        x_rtm_device=x_rtm_device,
        rtm_presenter_device=rtm_presenter_device,
        touch=False,
    )
    if not session:
        raise HTTPException(
            status_code=401,
            detail="Sesión no válida",
            headers=_NO_STORE_HEADERS,
        )
    return session


@router.get("/status")
async def operator_auth_status(response: Response) -> dict[str, Any]:
    response.headers.update(_NO_STORE_HEADERS)
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
    response: Response,
    x_rtm_device: str | None = Header(
        default=None,
        alias="X-RTM-Device",
    ),
    rtm_presenter_device: str | None = Cookie(
        default=None,
        alias=_DEVICE_COOKIE,
    ),
    conn: Connection = Depends(operator_auth_connection),
):
    response.headers.update(_NO_STORE_HEADERS)
    config = _runtime_config()
    context = _fingerprint(request, config)
    decision = login_operator(
        conn,
        email=payload.email,
        password=payload.password,
        device_token=x_rtm_device or rtm_presenter_device,
        context=context,
        config=config,
    )
    if not decision.ok:
        headers = dict(_NO_STORE_HEADERS)
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
    if decision.device_token:
        response.set_cookie(
            key=_DEVICE_COOKIE,
            value=decision.device_token,
            max_age=60 * 60 * 24 * 180,
            httponly=True,
            secure=True,
            samesite="strict",
            path="/",
        )
    return {
        "ok": True,
        "token_type": "bearer",
        "token": decision.token,
        "session_id": decision.session_id,
        "expires_at": decision.expires_at,
        "absolute_expires_at": decision.absolute_expires_at,
        "device_id": decision.device_id,
        "operator": decision.operator,
        "request_id": context.request_id,
        "legacy_login_unchanged": True,
    }


@router.get("/me")
async def operator_me(
    response: Response,
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
    ),
    x_rtm_device: str | None = Header(
        default=None,
        alias="X-RTM-Device",
    ),
    rtm_presenter_device: str | None = Cookie(
        default=None,
        alias=_DEVICE_COOKIE,
    ),
    conn: Connection = Depends(operator_auth_connection),
):
    response.headers.update(_NO_STORE_HEADERS)
    _runtime_config()
    session = load_operator_session_with_device_possession(
        conn,
        authorization=authorization,
        x_rtm_device=x_rtm_device,
        rtm_presenter_device=rtm_presenter_device,
        touch=True,
    )
    if not session:
        raise HTTPException(
            status_code=401,
            detail="Sesión no válida",
            headers=_NO_STORE_HEADERS,
        )
    return {"ok": True, **_session_payload(session)}


@router.post("/heartbeat")
async def operator_heartbeat(
    response: Response,
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
    ),
    x_rtm_device: str | None = Header(
        default=None,
        alias="X-RTM-Device",
    ),
    rtm_presenter_device: str | None = Cookie(
        default=None,
        alias=_DEVICE_COOKIE,
    ),
    conn: Connection = Depends(operator_auth_connection),
):
    response.headers.update(_NO_STORE_HEADERS)
    _runtime_config()
    session = load_operator_session_with_device_possession(
        conn,
        authorization=authorization,
        x_rtm_device=x_rtm_device,
        rtm_presenter_device=rtm_presenter_device,
        touch=True,
    )
    if not session:
        raise HTTPException(
            status_code=401,
            detail="Sesión no válida",
            headers=_NO_STORE_HEADERS,
        )
    return {
        "ok": True,
        "session_id": session.session_id,
        "server_time": datetime.now(timezone.utc),
        "expires_at": session.expires_at,
        "absolute_expires_at": session.absolute_expires_at,
    }


@router.post("/reauthenticate")
async def operator_reauthenticate(
    payload: OperatorReauthenticationRequest,
    request: Request,
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
    ),
    x_rtm_device: str | None = Header(
        default=None,
        alias="X-RTM-Device",
    ),
    rtm_presenter_device: str | None = Cookie(
        default=None,
        alias=_DEVICE_COOKIE,
    ),
    conn: Connection = Depends(operator_auth_connection),
):
    config = _runtime_config()
    context = _fingerprint(request, config)
    raw_token = extract_bearer_token(authorization)
    if not raw_token:
        record_reauthentication_denial(
            conn,
            context=context,
            config=config,
            reason_code="missing_or_invalid_bearer",
            risk_flags=("invalid_session_state",),
        )
        return JSONResponse(
            status_code=401,
            content={
                "ok": False,
                "detail": "Sesión no válida",
                "request_id": context.request_id,
            },
            headers=_NO_STORE_HEADERS,
        )
    session = load_operator_session_with_device_possession(
        conn,
        authorization=authorization,
        x_rtm_device=x_rtm_device,
        rtm_presenter_device=rtm_presenter_device,
        touch=False,
    )
    if not session:
        record_reauthentication_denial(
            conn,
            context=context,
            config=config,
            reason_code="invalid_session",
            risk_flags=("invalid_session_state",),
        )
        return JSONResponse(
            status_code=401,
            content={
                "ok": False,
                "detail": "Sesión no válida",
                "request_id": context.request_id,
            },
            headers=_NO_STORE_HEADERS,
        )

    decision = reauthenticate_operator(
        conn,
        session=session,
        password=payload.password,
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
            headers={**_NO_STORE_HEADERS, **headers},
        )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "status": "reauthenticated",
            "session_id": session.session_id,
            "reauthenticated_at": decision.reauthenticated_at.isoformat(),
            "request_id": context.request_id,
        },
        headers=_NO_STORE_HEADERS,
    )


@router.post("/logout")
async def operator_logout(
    request: Request,
    response: Response,
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
    ),
    conn: Connection = Depends(operator_auth_connection),
):
    response.headers.update(_NO_STORE_HEADERS)
    config = _runtime_config()
    raw_token = extract_bearer_token(authorization)
    if not raw_token:
        raise HTTPException(
            status_code=401,
            detail="Sesión no válida",
            headers=_NO_STORE_HEADERS,
        )
    context = _fingerprint(request, config)
    closed = logout_operator(
        conn,
        raw_token=raw_token,
        context=context,
        config=config,
    )
    if not closed:
        raise HTTPException(
            status_code=401,
            detail="Sesión no válida",
            headers=_NO_STORE_HEADERS,
        )
    return {
        "ok": True,
        "status": "closed",
        "request_id": context.request_id,
    }


__all__ = [
    "OPERATOR_AUTH_ROUTES_VERSION",
    "load_operator_session_with_device_possession",
    "operator_auth_connection",
    "require_operator_device_possession",
    "router",
]
