"""Rutas supervisoras de operadores, sesiones, dispositivos y accesos RTM.

La primera versión es exclusiva de staging, tiene su propia feature flag y
requiere una sesión individual con permiso ``ops.supervise``. No publica alta
de operadores, rotación de credenciales, roles ni evidencia sensible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator
from uuid import UUID

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.engine import Connection

from database import get_engine
from rtm_core.operator_access_runtime_repository import (
    record_operator_access_event,
)
from rtm_core.operator_admin_policy import (
    OperatorAdminRoutesDisabled,
    OperatorAdminRuntimeConfig,
    OperatorAdminRuntimeMisconfigured,
    load_operator_admin_runtime_config,
    session_has_supervisor_permission,
)
from rtm_core.operator_admin_repository import (
    OperatorAdminSelfProtectionError,
    count_operators,
    get_operator_summary,
    list_operator_access_events,
    list_operator_devices,
    list_operator_sessions,
    list_operator_summaries,
    revoke_operator_device,
    revoke_operator_session,
)
from rtm_core.operator_auth_request import (
    build_request_fingerprint,
)
from rtm_core.operator_auth_service import has_recent_reauthentication
from rtm_core.operator_auth_router import (
    load_operator_session_with_device_possession,
)


OPERATOR_ADMIN_ROUTES_VERSION = "rtm_operator_admin_routes_v1_0"
router = APIRouter(prefix="/ops/admin", tags=["ops-operator-admin"])


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RevokeRequest(_StrictModel):
    reason: str = Field(min_length=3, max_length=240)


@dataclass(frozen=True)
class SupervisorContext:
    session: Any = field(repr=False)
    config: OperatorAdminRuntimeConfig = field(repr=False)


async def operator_admin_connection() -> AsyncIterator[Connection]:
    engine = get_engine()
    with engine.begin() as conn:
        yield conn


def _runtime_config(
    *,
    require_enabled: bool = True,
) -> OperatorAdminRuntimeConfig:
    try:
        return load_operator_admin_runtime_config(
            require_enabled=require_enabled
        )
    except OperatorAdminRoutesDisabled as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc
    except OperatorAdminRuntimeMisconfigured as exc:
        raise HTTPException(
            status_code=503,
            detail="Administración de operadores no disponible",
        ) from exc


async def require_supervisor_context(
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
        alias="__Host-rtm_presenter_device",
    ),
    conn: Connection = Depends(operator_admin_connection),
) -> SupervisorContext:
    config = _runtime_config(require_enabled=True)
    session = load_operator_session_with_device_possession(
        conn,
        authorization=authorization,
        x_rtm_device=x_rtm_device,
        rtm_presenter_device=rtm_presenter_device,
        touch=True,
    )
    if not session:
        raise HTTPException(status_code=401, detail="Sesión no válida")
    if bool(session.must_change_password) or bool(session.mfa_required):
        raise HTTPException(
            status_code=403,
            detail="Completa los controles de identidad antes de administrar",
        )
    if not session_has_supervisor_permission(session):
        raise HTTPException(
            status_code=403,
            detail="Permiso de supervisor requerido",
        )
    return SupervisorContext(session=session, config=config)


async def require_recent_supervisor_context(
    context: SupervisorContext = Depends(require_supervisor_context),
) -> SupervisorContext:
    """Step-up persistido obligatorio para mutaciones supervisoras."""

    if not has_recent_reauthentication(
        context.session,
        max_age_seconds=context.config.auth.reauthentication_max_age_seconds,
    ):
        raise HTTPException(
            status_code=403,
            detail="Reautenticación reciente requerida",
        )
    return context


def _fingerprint(request: Request, context: SupervisorContext):
    client_host = request.client.host if request.client else None
    return build_request_fingerprint(
        request.headers,
        client_host=client_host,
        hmac_key=context.config.auth.hmac_key,
        trust_proxy_headers=context.config.auth.trust_proxy_headers,
        trusted_proxy_cidrs=context.config.auth.trusted_proxy_cidrs,
    )


def _clean_reason(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


@router.get("/status")
async def operator_admin_status() -> dict[str, Any]:
    try:
        config = load_operator_admin_runtime_config(
            require_enabled=False
        )
        available = config.available
        configuration_valid = True
        auth_enabled = bool(config.auth.enabled)
    except OperatorAdminRuntimeMisconfigured:
        available = False
        configuration_valid = False
        auth_enabled = False
    return {
        "ok": True,
        "version": OPERATOR_ADMIN_ROUTES_VERSION,
        "operator_admin_enabled": available,
        "configuration_valid": configuration_valid,
        "auth_enabled": auth_enabled,
        "staging_only": True,
        "supervisor_permission": "ops.supervise",
        "operator_creation_available": False,
        "credential_rotation_available": False,
        "raw_evidence_available": False,
        "legacy_login_unchanged": True,
        "legacy_login_retired_in_staging": True,
        "non_staging_legacy_login_unchanged": True,
        "shared_ops_login_accepted": False,
    }


@router.get("/operators")
async def admin_list_operators(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
    context: SupervisorContext = Depends(require_supervisor_context),
    conn: Connection = Depends(operator_admin_connection),
):
    items = list_operator_summaries(
        conn,
        limit=limit,
        offset=offset,
    )
    return {
        "ok": True,
        "items": items,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": count_operators(conn),
        },
        "supervisor_operator_id": context.session.operator_id,
    }


@router.get("/operators/{operator_id}")
async def admin_get_operator(
    operator_id: UUID,
    context: SupervisorContext = Depends(require_supervisor_context),
    conn: Connection = Depends(operator_admin_connection),
):
    item = get_operator_summary(conn, str(operator_id))
    if not item:
        raise HTTPException(status_code=404, detail="Operador no encontrado")
    return {"ok": True, "operator": item}


@router.get("/operators/{operator_id}/sessions")
async def admin_list_sessions(
    operator_id: UUID,
    status: str | None = Query(
        default=None,
        pattern="^(active|closed|revoked|expired)$",
    ),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10000),
    context: SupervisorContext = Depends(require_supervisor_context),
    conn: Connection = Depends(operator_admin_connection),
):
    return {
        "ok": True,
        "operator_id": str(operator_id),
        "items": list_operator_sessions(
            conn,
            operator_id=str(operator_id),
            status=status,
            limit=limit,
            offset=offset,
        ),
    }


@router.get("/operators/{operator_id}/devices")
async def admin_list_devices(
    operator_id: UUID,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10000),
    context: SupervisorContext = Depends(require_supervisor_context),
    conn: Connection = Depends(operator_admin_connection),
):
    return {
        "ok": True,
        "operator_id": str(operator_id),
        "items": list_operator_devices(
            conn,
            operator_id=str(operator_id),
            limit=limit,
            offset=offset,
        ),
    }


@router.get("/operators/{operator_id}/access-events")
async def admin_list_access_events(
    operator_id: UUID,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10000),
    context: SupervisorContext = Depends(require_supervisor_context),
    conn: Connection = Depends(operator_admin_connection),
):
    return {
        "ok": True,
        "operator_id": str(operator_id),
        "items": list_operator_access_events(
            conn,
            operator_id=str(operator_id),
            limit=limit,
            offset=offset,
        ),
        "raw_evidence_exposed": False,
    }


@router.post("/sessions/{session_id}/revoke")
async def admin_revoke_session(
    session_id: UUID,
    payload: RevokeRequest,
    request: Request,
    context: SupervisorContext = Depends(require_recent_supervisor_context),
    conn: Connection = Depends(operator_admin_connection),
):
    reason = _clean_reason(payload.reason)
    try:
        result = revoke_operator_session(
            conn,
            session_id=str(session_id),
            actor_operator_id=context.session.operator_id,
            actor_session_id=context.session.session_id,
            reason=reason,
        )
    except OperatorAdminSelfProtectionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    request_context = _fingerprint(request, context)
    event_id = record_operator_access_event(
        conn,
        context=request_context,
        event_type="admin.session_revoked",
        result="success" if result.changed else "noop",
        auth_method="bearer",
        retention_days=context.config.auth.evidence_retention_days,
        operator_id=result.operator_id,
        session_id=result.session_id,
        device_id=result.device_id,
        reason_code="supervisor_session_revocation",
        reason_detail=(
            f"actor={context.session.operator_id}; reason={reason}"
        ),
        risk_flags=("supervisor_action",),
    )
    return {
        "ok": True,
        "changed": result.changed,
        "session_id": result.session_id,
        "operator_id": result.operator_id,
        "previous_status": result.previous_status,
        "status": result.status,
        "audit_event_id": event_id,
        "legacy_login_unchanged": True,
        "legacy_login_retired_in_staging": True,
        "non_staging_legacy_login_unchanged": True,
        "shared_ops_login_accepted": False,
    }


@router.post("/devices/{device_id}/revoke")
async def admin_revoke_device(
    device_id: UUID,
    payload: RevokeRequest,
    request: Request,
    context: SupervisorContext = Depends(require_recent_supervisor_context),
    conn: Connection = Depends(operator_admin_connection),
):
    reason = _clean_reason(payload.reason)
    try:
        result = revoke_operator_device(
            conn,
            device_id=str(device_id),
            actor_operator_id=context.session.operator_id,
            actor_session_id=context.session.session_id,
            reason=reason,
        )
    except OperatorAdminSelfProtectionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    request_context = _fingerprint(request, context)
    event_id = record_operator_access_event(
        conn,
        context=request_context,
        event_type="admin.device_revoked",
        result="success" if result.changed else "noop",
        auth_method="bearer",
        retention_days=context.config.auth.evidence_retention_days,
        operator_id=result.operator_id,
        device_id=result.device_id,
        reason_code="supervisor_device_revocation",
        reason_detail=(
            f"actor={context.session.operator_id}; reason={reason}"
        ),
        risk_flags=("supervisor_action",),
    )
    return {
        "ok": True,
        "changed": result.changed,
        "device_id": result.device_id,
        "operator_id": result.operator_id,
        "previous_status": result.previous_status,
        "status": result.status,
        "sessions_revoked": result.sessions_revoked,
        "audit_event_id": event_id,
        "legacy_login_unchanged": True,
        "legacy_login_retired_in_staging": True,
        "non_staging_legacy_login_unchanged": True,
        "shared_ops_login_accepted": False,
    }


__all__ = [
    "OPERATOR_ADMIN_ROUTES_VERSION",
    "SupervisorContext",
    "operator_admin_connection",
    "require_recent_supervisor_context",
    "require_supervisor_context",
    "router",
]
