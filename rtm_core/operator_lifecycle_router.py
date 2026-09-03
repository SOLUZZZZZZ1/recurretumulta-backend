"""Rutas de ciclo de vida y credenciales de operadores RTM.

Solo staging. Requieren una feature flag independiente, autenticación individual,
panel supervisor y permiso ``ops.supervise``. No existe registro público y nunca
se devuelve una contraseña en una respuesta.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, AsyncIterator, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    Header,
    HTTPException,
    Request,
    status,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.engine import Connection

from database import get_engine
from rtm_core.operator_access_runtime_repository import (
    record_operator_access_event,
)
from rtm_core.operator_auth_request import (
    build_request_fingerprint,
)
from rtm_core.operator_auth_router import (
    load_operator_session_with_device_possession,
)
from rtm_core.operator_auth_service import has_recent_reauthentication
from rtm_core.operator_lifecycle_policy import (
    OperatorLifecycleRoutesDisabled,
    OperatorLifecycleRuntimeConfig,
    OperatorLifecycleRuntimeMisconfigured,
    load_operator_lifecycle_runtime_config,
    session_has_lifecycle_permission,
)
from rtm_core.operator_lifecycle_repository import (
    OperatorCurrentPasswordInvalid,
    OperatorLifecycleConflict,
    OperatorLifecycleSelfProtectionError,
    OperatorLifecycleStateError,
    OperatorPasswordReuseError,
    assign_operator_role,
    change_own_password,
    create_controlled_synthetic_operator,
    reactivate_operator,
    revoke_all_operator_sessions,
    rotate_operator_password,
    suspend_operator,
)


OPERATOR_LIFECYCLE_ROUTES_VERSION = "rtm_operator_lifecycle_routes_v1_1"
router = APIRouter(tags=["ops-operator-lifecycle"])


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateOperatorRequest(_StrictModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=3, max_length=160)
    temporary_password: str = Field(
        min_length=12,
        max_length=256,
        repr=False,
    )


class ReasonRequest(_StrictModel):
    reason: str = Field(min_length=3, max_length=240)


class AssignRoleRequest(ReasonRequest):
    role_code: Literal["rtm.operator", "rtm.supervisor"]


class RotatePasswordRequest(ReasonRequest):
    new_password: str = Field(
        min_length=12,
        max_length=256,
        repr=False,
    )
    must_change_password: bool = True


class ChangeOwnPasswordRequest(_StrictModel):
    current_password: str = Field(
        min_length=1,
        max_length=256,
        repr=False,
    )
    new_password: str = Field(
        min_length=12,
        max_length=256,
        repr=False,
    )
    reason: str = Field(
        default="Cambio personal de contraseña",
        min_length=3,
        max_length=240,
    )


@dataclass(frozen=True)
class LifecycleSupervisorContext:
    session: Any = field(repr=False)
    config: OperatorLifecycleRuntimeConfig = field(repr=False)


@dataclass(frozen=True)
class LifecycleOperatorContext:
    session: Any = field(repr=False)
    config: OperatorLifecycleRuntimeConfig = field(repr=False)


async def operator_lifecycle_connection() -> AsyncIterator[Connection]:
    engine = get_engine()
    with engine.begin() as conn:
        yield conn


def _runtime_config(
    *,
    require_enabled: bool = True,
) -> OperatorLifecycleRuntimeConfig:
    try:
        return load_operator_lifecycle_runtime_config(
            require_enabled=require_enabled
        )
    except OperatorLifecycleRoutesDisabled as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc
    except OperatorLifecycleRuntimeMisconfigured as exc:
        raise HTTPException(
            status_code=503,
            detail="Ciclo de vida de operadores no disponible",
        ) from exc


async def require_lifecycle_operator_context(
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
    conn: Connection = Depends(operator_lifecycle_connection),
) -> LifecycleOperatorContext:
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
    return LifecycleOperatorContext(session=session, config=config)


async def require_lifecycle_supervisor_context(
    operator_context: LifecycleOperatorContext = Depends(
        require_lifecycle_operator_context
    ),
) -> LifecycleSupervisorContext:
    if not session_has_lifecycle_permission(operator_context.session):
        raise HTTPException(
            status_code=403,
            detail="Permiso de supervisor requerido",
        )
    if bool(operator_context.session.must_change_password):
        raise HTTPException(
            status_code=409,
            detail=(
                "Debe cambiar la contraseña temporal antes de administrar "
                "operadores"
            ),
        )
    if bool(operator_context.session.mfa_required):
        raise HTTPException(
            status_code=409,
            detail="La cuenta requiere completar una fase de seguridad adicional",
        )
    return LifecycleSupervisorContext(
        session=operator_context.session,
        config=operator_context.config,
    )


async def require_recent_lifecycle_supervisor_context(
    context: LifecycleSupervisorContext = Depends(
        require_lifecycle_supervisor_context
    ),
) -> LifecycleSupervisorContext:
    """Exige un step-up persistido y vigente para cada mutación crítica."""

    if not has_recent_reauthentication(
        context.session,
        max_age_seconds=(
            context.config.admin.auth.reauthentication_max_age_seconds
        ),
    ):
        raise HTTPException(
            status_code=403,
            detail="Reautenticación reciente requerida",
        )
    return context


def _fingerprint(request: Request, config):
    client_host = request.client.host if request.client else None
    return build_request_fingerprint(
        request.headers,
        client_host=client_host,
        hmac_key=config.admin.auth.hmac_key,
        trust_proxy_headers=config.admin.auth.trust_proxy_headers,
        trusted_proxy_cidrs=config.admin.auth.trusted_proxy_cidrs,
    )


def _clean_reason(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def _raise_repository_error(exc: Exception) -> None:
    if isinstance(exc, OperatorCurrentPasswordInvalid):
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if isinstance(
        exc,
        (
            OperatorLifecycleConflict,
            OperatorLifecycleSelfProtectionError,
            OperatorLifecycleStateError,
            OperatorPasswordReuseError,
        ),
    ):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _audit(
    conn,
    *,
    request: Request,
    config: OperatorLifecycleRuntimeConfig,
    event_type: str,
    result: str,
    target_operator_id: str,
    actor_operator_id: str,
    reason_code: str,
    reason: str,
    actor_session_id: str | None = None,
) -> str:
    context = _fingerprint(request, config)
    return record_operator_access_event(
        conn,
        context=context,
        event_type=event_type,
        result=result,
        auth_method="bearer",
        retention_days=config.admin.auth.evidence_retention_days,
        operator_id=target_operator_id,
        session_id=actor_session_id,
        reason_code=reason_code,
        reason_detail=(
            f"actor={actor_operator_id}; reason={_clean_reason(reason)}"
        ),
        risk_flags=("supervisor_action",),
    )


@router.get("/ops/admin/lifecycle/status")
async def operator_lifecycle_status() -> dict[str, Any]:
    try:
        config = load_operator_lifecycle_runtime_config(
            require_enabled=False
        )
        available = config.available
        configuration_valid = True
        admin_enabled = bool(config.admin.enabled)
        auth_enabled = bool(config.admin.auth.enabled)
    except OperatorLifecycleRuntimeMisconfigured:
        available = False
        configuration_valid = False
        admin_enabled = False
        auth_enabled = False
    return {
        "ok": True,
        "version": OPERATOR_LIFECYCLE_ROUTES_VERSION,
        "operator_lifecycle_enabled": available,
        "configuration_valid": configuration_valid,
        "operator_admin_enabled": admin_enabled,
        "auth_enabled": auth_enabled,
        "staging_only": True,
        "synthetic_only": True,
        "public_registration_available": False,
        "direct_supervisor_creation_available": False,
        "passwords_returned": False,
        "legacy_login_unchanged": True,
        "legacy_login_retired_in_staging": True,
        "non_staging_legacy_login_unchanged": True,
        "shared_ops_login_accepted": False,
    }


@router.post(
    "/ops/admin/operators",
    status_code=status.HTTP_201_CREATED,
)
async def lifecycle_create_operator(
    payload: CreateOperatorRequest,
    request: Request,
    context: LifecycleSupervisorContext = Depends(
        require_recent_lifecycle_supervisor_context
    ),
    conn: Connection = Depends(operator_lifecycle_connection),
):
    try:
        result = create_controlled_synthetic_operator(
            conn,
            actor_operator_id=context.session.operator_id,
            email=payload.email,
            display_name=payload.display_name,
            temporary_password=payload.temporary_password,
        )
    except Exception as exc:
        _raise_repository_error(exc)
    event_id = _audit(
        conn,
        request=request,
        config=context.config,
        event_type="admin.operator_created",
        result="success",
        target_operator_id=result.operator_id,
        actor_operator_id=context.session.operator_id,
        actor_session_id=context.session.session_id,
        reason_code="controlled_synthetic_operator_created",
        reason="Alta controlada de operador sintético",
    )
    return {
        "ok": True,
        "operator": asdict(result),
        "audit_event_id": event_id,
        "temporary_password_returned": False,
        "public_registration_available": False,
    }


@router.post("/ops/admin/operators/{operator_id}/suspend")
async def lifecycle_suspend_operator(
    operator_id: UUID,
    payload: ReasonRequest,
    request: Request,
    context: LifecycleSupervisorContext = Depends(
        require_recent_lifecycle_supervisor_context
    ),
    conn: Connection = Depends(operator_lifecycle_connection),
):
    try:
        result = suspend_operator(
            conn,
            operator_id=str(operator_id),
            actor_operator_id=context.session.operator_id,
            reason=_clean_reason(payload.reason),
        )
    except Exception as exc:
        _raise_repository_error(exc)
    event_id = _audit(
        conn,
        request=request,
        config=context.config,
        event_type="admin.operator_suspended",
        result="success" if result.changed else "noop",
        target_operator_id=result.operator_id,
        actor_operator_id=context.session.operator_id,
        actor_session_id=context.session.session_id,
        reason_code="operator_suspended",
        reason=payload.reason,
    )
    return {
        "ok": True,
        "operator": asdict(result),
        "audit_event_id": event_id,
    }


@router.post("/ops/admin/operators/{operator_id}/reactivate")
async def lifecycle_reactivate_operator(
    operator_id: UUID,
    payload: ReasonRequest,
    request: Request,
    context: LifecycleSupervisorContext = Depends(
        require_recent_lifecycle_supervisor_context
    ),
    conn: Connection = Depends(operator_lifecycle_connection),
):
    try:
        result = reactivate_operator(
            conn,
            operator_id=str(operator_id),
            actor_operator_id=context.session.operator_id,
            reason=_clean_reason(payload.reason),
        )
    except Exception as exc:
        _raise_repository_error(exc)
    event_id = _audit(
        conn,
        request=request,
        config=context.config,
        event_type="admin.operator_reactivated",
        result="success" if result.changed else "noop",
        target_operator_id=result.operator_id,
        actor_operator_id=context.session.operator_id,
        actor_session_id=context.session.session_id,
        reason_code="operator_reactivated",
        reason=payload.reason,
    )
    return {
        "ok": True,
        "operator": asdict(result),
        "audit_event_id": event_id,
    }


@router.post("/ops/admin/operators/{operator_id}/role")
async def lifecycle_assign_role(
    operator_id: UUID,
    payload: AssignRoleRequest,
    request: Request,
    context: LifecycleSupervisorContext = Depends(
        require_recent_lifecycle_supervisor_context
    ),
    conn: Connection = Depends(operator_lifecycle_connection),
):
    try:
        result = assign_operator_role(
            conn,
            operator_id=str(operator_id),
            actor_operator_id=context.session.operator_id,
            role_code=payload.role_code,
            reason=_clean_reason(payload.reason),
        )
    except Exception as exc:
        _raise_repository_error(exc)
    event_id = _audit(
        conn,
        request=request,
        config=context.config,
        event_type="admin.operator_role_changed",
        result="success" if result.changed else "noop",
        target_operator_id=result.operator_id,
        actor_operator_id=context.session.operator_id,
        actor_session_id=context.session.session_id,
        reason_code="operator_role_changed",
        reason=payload.reason,
    )
    return {
        "ok": True,
        "operator": asdict(result),
        "audit_event_id": event_id,
    }


@router.post("/ops/admin/operators/{operator_id}/credentials/rotate")
async def lifecycle_rotate_password(
    operator_id: UUID,
    payload: RotatePasswordRequest,
    request: Request,
    context: LifecycleSupervisorContext = Depends(
        require_recent_lifecycle_supervisor_context
    ),
    conn: Connection = Depends(operator_lifecycle_connection),
):
    try:
        result = rotate_operator_password(
            conn,
            operator_id=str(operator_id),
            actor_operator_id=context.session.operator_id,
            new_password=payload.new_password,
            must_change_password=payload.must_change_password,
            reason=_clean_reason(payload.reason),
        )
    except Exception as exc:
        _raise_repository_error(exc)
    event_id = _audit(
        conn,
        request=request,
        config=context.config,
        event_type="admin.operator_password_rotated",
        result="success",
        target_operator_id=result.operator_id,
        actor_operator_id=context.session.operator_id,
        actor_session_id=context.session.session_id,
        reason_code="operator_password_rotated",
        reason=payload.reason,
    )
    return {
        "ok": True,
        "operator": asdict(result),
        "audit_event_id": event_id,
        "password_returned": False,
        "reauthentication_required": True,
    }


@router.post("/ops/admin/operators/{operator_id}/sessions/revoke-all")
async def lifecycle_revoke_all_sessions(
    operator_id: UUID,
    payload: ReasonRequest,
    request: Request,
    context: LifecycleSupervisorContext = Depends(
        require_recent_lifecycle_supervisor_context
    ),
    conn: Connection = Depends(operator_lifecycle_connection),
):
    try:
        result = revoke_all_operator_sessions(
            conn,
            operator_id=str(operator_id),
            actor_operator_id=context.session.operator_id,
            reason=_clean_reason(payload.reason),
        )
    except Exception as exc:
        _raise_repository_error(exc)
    event_id = _audit(
        conn,
        request=request,
        config=context.config,
        event_type="admin.operator_sessions_revoked",
        result="success",
        target_operator_id=result.operator_id,
        actor_operator_id=context.session.operator_id,
        actor_session_id=context.session.session_id,
        reason_code="operator_sessions_revoked_all",
        reason=payload.reason,
    )
    return {
        "ok": True,
        "operator": asdict(result),
        "audit_event_id": event_id,
        "reauthentication_required": True,
    }


@router.post("/ops/auth/password/change")
async def lifecycle_change_own_password(
    payload: ChangeOwnPasswordRequest,
    request: Request,
    context: LifecycleOperatorContext = Depends(
        require_lifecycle_operator_context
    ),
    conn: Connection = Depends(operator_lifecycle_connection),
):
    try:
        result = change_own_password(
            conn,
            operator_id=context.session.operator_id,
            actor_session_id=context.session.session_id,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except Exception as exc:
        _raise_repository_error(exc)
    request_context = _fingerprint(request, context.config)
    event_id = record_operator_access_event(
        conn,
        context=request_context,
        event_type="auth.password_changed",
        result="success",
        auth_method="bearer+password",
        retention_days=context.config.admin.auth.evidence_retention_days,
        operator_id=result.operator_id,
        session_id=context.session.session_id,
        reason_code="operator_self_password_changed",
        reason_detail=_clean_reason(payload.reason),
        risk_flags=("credential_change",),
    )
    return {
        "ok": True,
        "operator": asdict(result),
        "audit_event_id": event_id,
        "password_returned": False,
        "reauthentication_required": True,
        "shared_ops_login_accepted": False,
        "legacy_login_unchanged": True,
        "legacy_login_retired_in_staging": True,
        "non_staging_legacy_login_unchanged": True,
    }


__all__ = [
    "OPERATOR_LIFECYCLE_ROUTES_VERSION",
    "LifecycleOperatorContext",
    "LifecycleSupervisorContext",
    "operator_lifecycle_connection",
    "require_lifecycle_operator_context",
    "require_lifecycle_supervisor_context",
    "require_recent_lifecycle_supervisor_context",
    "router",
]
