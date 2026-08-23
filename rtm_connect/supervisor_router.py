"""API GET-only del panel supervisor RTM CONNECT C5.

Las rutas protegidas exigen sesion individual, permiso ``ops.supervise`` y
scope de datos exclusivamente sintetico. Cada lectura protegida deja un evento
de acceso append-only, pero nunca modifica el dominio RTM CONNECT.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from sqlalchemy.engine import Connection
from starlette.responses import JSONResponse

from database import get_engine
from rtm_connect.supervisor_contracts import (
    ConnectSupervisorProjectionError,
    assert_sanitized_supervisor_projection,
)
from rtm_connect.supervisor_policy import (
    ConnectSupervisorRoutesDisabled,
    ConnectSupervisorRuntimeConfig,
    ConnectSupervisorRuntimeMisconfigured,
    assert_connect_supervisor_database_identity,
    load_connect_supervisor_runtime_config,
    session_has_connect_supervisor_permission,
)
from rtm_connect.supervisor_repository import (
    ConnectSupervisorScopeError,
    assert_synthetic_supervisor_scope,
    count_actions,
    count_dead_letters,
    count_manual_tasks,
    current_operator_can_supervise,
    current_supervisor_device_id,
    get_action_supervisor_detail,
    list_action_summaries,
    list_attention_items,
    list_dead_letter_summaries,
    list_manual_task_summaries,
    overview_snapshot,
)
from rtm_core.operator_access_runtime_repository import (
    record_operator_access_event,
)
from rtm_core.operator_auth_request import (
    build_request_fingerprint,
    extract_bearer_token,
)
from rtm_core.operator_auth_service import load_operator_session


RTM_CONNECT_C5_SUPERVISOR_ROUTES_VERSION = (
    "rtm_connect_c5_supervisor_routes_v1_0"
)

router = APIRouter(
    prefix="/ops/connect/supervisor",
    tags=["ops-connect-supervisor"],
    include_in_schema=False,
)

_ACTION_STATUS_PATTERN = (
    "^(draft|authorized|queued|executing|external_accepted|"
    "evidence_pending|confirmed|retryable_failed|unknown|reconciling|"
    "manual_review|permanent_failed|cancelled)$"
)
_RISK_PATTERN = (
    "^(R0_observation|R1_low_reversible|R2_business_effect|"
    "R3_legal_or_financial|R4_critical_regulated)$"
)
_MANUAL_STATUS_PATTERN = (
    "^(prepared|assigned|in_progress|awaiting_receipt|"
    "receipt_submitted|verified|completed)$"
)
_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}


@dataclass(frozen=True)
class ConnectSupervisorContext:
    session: Any
    device_id: str | None
    config: ConnectSupervisorRuntimeConfig
    connection: Connection


@dataclass(frozen=True)
class ConnectSupervisorGate:
    config: ConnectSupervisorRuntimeConfig
    raw_token: str


def require_connect_supervisor_gate(
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
    ),
) -> ConnectSupervisorGate:
    config = _runtime_config(require_enabled=True)
    raw_token = extract_bearer_token(authorization)
    if not raw_token:
        raise HTTPException(
            status_code=401,
            detail="Sesion no valida",
            headers=_NO_STORE_HEADERS,
        )
    return ConnectSupervisorGate(config=config, raw_token=raw_token)


def connect_supervisor_connection(
    gate: ConnectSupervisorGate = Depends(require_connect_supervisor_gate),
) -> Iterator[Connection]:
    engine = get_engine()
    with engine.begin() as conn:
        try:
            assert_connect_supervisor_database_identity(
                conn,
                expected_database_name=gate.config.database_name,
            )
        except ConnectSupervisorRuntimeMisconfigured as exc:
            raise HTTPException(
                status_code=503,
                detail="Panel supervisor RTM CONNECT no disponible",
                headers=_NO_STORE_HEADERS,
            ) from exc
        yield conn


def _runtime_config(
    *,
    require_enabled: bool = True,
) -> ConnectSupervisorRuntimeConfig:
    try:
        return load_connect_supervisor_runtime_config(
            require_enabled=require_enabled
        )
    except ConnectSupervisorRoutesDisabled as exc:
        raise HTTPException(
            status_code=404,
            detail="Not found",
            headers=_NO_STORE_HEADERS,
        ) from exc
    except ConnectSupervisorRuntimeMisconfigured as exc:
        raise HTTPException(
            status_code=503,
            detail="Panel supervisor RTM CONNECT no disponible",
            headers=_NO_STORE_HEADERS,
        ) from exc


def require_connect_supervisor_context(
    gate: ConnectSupervisorGate = Depends(require_connect_supervisor_gate),
    conn: Connection = Depends(connect_supervisor_connection),
) -> ConnectSupervisorContext:
    session = load_operator_session(
        conn,
        raw_token=gate.raw_token,
        touch=False,
    )
    if not session:
        raise HTTPException(
            status_code=401,
            detail="Sesion no valida",
            headers=_NO_STORE_HEADERS,
        )
    if not session_has_connect_supervisor_permission(session):
        raise HTTPException(
            status_code=403,
            detail="Permiso de supervisor requerido",
            headers=_NO_STORE_HEADERS,
        )
    if not current_operator_can_supervise(conn, session.operator_id):
        raise HTTPException(
            status_code=403,
            detail="Permiso supervisor vigente requerido",
            headers=_NO_STORE_HEADERS,
        )
    try:
        assert_synthetic_supervisor_scope(conn)
    except ConnectSupervisorScopeError as exc:
        raise HTTPException(
            status_code=503,
            detail="Scope supervisor RTM CONNECT no disponible",
            headers=_NO_STORE_HEADERS,
        ) from exc
    device_id = current_supervisor_device_id(
        conn,
        session_id=session.session_id,
        operator_id=session.operator_id,
    )
    return ConnectSupervisorContext(
        session=session,
        device_id=device_id,
        config=gate.config,
        connection=conn,
    )


async def connect_supervisor_gate_middleware(request: Request, call_next):
    """Oculta el prefijo C5 antes del routing cuando el gate esta cerrado."""

    path = request.url.path.rstrip("/")
    prefix = router.prefix
    if path == prefix or path.startswith(f"{prefix}/"):
        try:
            _runtime_config(require_enabled=True)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=_NO_STORE_HEADERS,
            )
        response = await call_next(request)
        response.headers.update(_NO_STORE_HEADERS)
        return response
    return await call_next(request)


def _audit_read(
    conn: Connection,
    *,
    request: Request,
    context: ConnectSupervisorContext,
    event_type: str,
    reason_code: str,
) -> None:
    client_host = request.client.host if request.client else None
    fingerprint = build_request_fingerprint(
        request.headers,
        client_host=client_host,
        hmac_key=context.config.auth.hmac_key,
        # C5 no confia en cabeceras de proxy aportadas por el cliente. El
        # ingress conserva la responsabilidad de registrar la IP de origen.
        trust_proxy_headers=False,
    )
    record_operator_access_event(
        conn,
        context=fingerprint,
        event_type=event_type,
        result="success",
        auth_method="bearer",
        retention_days=context.config.auth.evidence_retention_days,
        operator_id=context.session.operator_id,
        session_id=context.session.session_id,
        device_id=context.device_id,
        reason_code=reason_code,
        risk_flags=("supervisor_read", "connect_c5"),
    )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"


def _safe_projection(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        assert_sanitized_supervisor_projection(payload)
    except ConnectSupervisorProjectionError as exc:
        raise HTTPException(
            status_code=503,
            detail="Proyeccion supervisora RTM CONNECT no disponible",
            headers=_NO_STORE_HEADERS,
        ) from exc
    return payload


@router.get("/status")
def connect_supervisor_status(
    request: Request,
    response: Response,
    context: ConnectSupervisorContext = Depends(
        require_connect_supervisor_context
    ),
) -> dict[str, Any]:
    conn = context.connection
    _no_store(response)
    _audit_read(
        conn,
        request=request,
        context=context,
        event_type="connect.supervisor.status_viewed",
        reason_code="connect_supervisor_status",
    )
    return _safe_projection({
        "ok": True,
        "version": RTM_CONNECT_C5_SUPERVISOR_ROUTES_VERSION,
        "available": True,
        "configuration_valid": True,
        "staging_only": True,
        "synthetic_only": True,
        "business_operations_read_only": True,
        "supervisor_permission": "ops.supervise",
        "raw_operational_material_available": False,
        "execution_controls_available": False,
    })


@router.get("/overview")
def connect_supervisor_overview(
    request: Request,
    response: Response,
    context: ConnectSupervisorContext = Depends(
        require_connect_supervisor_context
    ),
):
    conn = context.connection
    _no_store(response)
    snapshot = overview_snapshot(conn)
    _audit_read(
        conn,
        request=request,
        context=context,
        event_type="connect.supervisor.overview_viewed",
        reason_code="connect_supervisor_overview",
    )
    return _safe_projection({
        "ok": True,
        "overview": snapshot,
    })


@router.get("/attention")
def connect_supervisor_attention(
    request: Request,
    response: Response,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
    context: ConnectSupervisorContext = Depends(
        require_connect_supervisor_context
    ),
):
    conn = context.connection
    _no_store(response)
    items, total = list_attention_items(
        conn,
        limit=limit,
        offset=offset,
    )
    _audit_read(
        conn,
        request=request,
        context=context,
        event_type="connect.supervisor.attention_viewed",
        reason_code="connect_supervisor_attention",
    )
    return _safe_projection({
        "ok": True,
        "items": items,
        "pagination": {"limit": limit, "offset": offset, "total": total},
    })


@router.get("/actions")
def connect_supervisor_actions(
    request: Request,
    response: Response,
    status: str | None = Query(default=None, pattern=_ACTION_STATUS_PATTERN),
    risk_class: str | None = Query(default=None, pattern=_RISK_PATTERN),
    capability: str | None = Query(
        default=None,
        min_length=3,
        max_length=96,
        pattern="^[a-z][a-z0-9_.-]{2,95}$",
    ),
    case_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
    context: ConnectSupervisorContext = Depends(
        require_connect_supervisor_context
    ),
):
    conn = context.connection
    _no_store(response)
    normalized_case_id = str(case_id) if case_id else None
    items = list_action_summaries(
        conn,
        status=status,
        risk_class=risk_class,
        capability=capability,
        case_id=normalized_case_id,
        limit=limit,
        offset=offset,
    )
    total = count_actions(
        conn,
        status=status,
        risk_class=risk_class,
        capability=capability,
        case_id=normalized_case_id,
    )
    _audit_read(
        conn,
        request=request,
        context=context,
        event_type="connect.supervisor.actions_viewed",
        reason_code="connect_supervisor_actions",
    )
    return _safe_projection({
        "ok": True,
        "items": items,
        "pagination": {"limit": limit, "offset": offset, "total": total},
    })


@router.get("/actions/{action_id}")
def connect_supervisor_action_detail(
    action_id: UUID,
    request: Request,
    response: Response,
    history_limit: int = Query(default=100, ge=1, le=200),
    context: ConnectSupervisorContext = Depends(
        require_connect_supervisor_context
    ),
):
    conn = context.connection
    _no_store(response)
    detail = get_action_supervisor_detail(
        conn,
        str(action_id),
        history_limit=history_limit,
    )
    if not detail:
        raise HTTPException(
            status_code=404,
            detail="Accion RTM CONNECT no encontrada",
            headers=_NO_STORE_HEADERS,
        )
    _audit_read(
        conn,
        request=request,
        context=context,
        event_type="connect.supervisor.action_viewed",
        reason_code="connect_supervisor_action",
    )
    return _safe_projection({
        "ok": True,
        **detail,
    })


@router.get("/manual-tasks")
def connect_supervisor_manual_tasks(
    request: Request,
    response: Response,
    status: str | None = Query(default=None, pattern=_MANUAL_STATUS_PATTERN),
    assignee_operator_id: UUID | None = Query(default=None),
    overdue_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
    context: ConnectSupervisorContext = Depends(
        require_connect_supervisor_context
    ),
):
    conn = context.connection
    _no_store(response)
    assignee = str(assignee_operator_id) if assignee_operator_id else None
    items = list_manual_task_summaries(
        conn,
        status=status,
        assignee_operator_id=assignee,
        overdue_only=overdue_only,
        limit=limit,
        offset=offset,
    )
    total = count_manual_tasks(
        conn,
        status=status,
        assignee_operator_id=assignee,
        overdue_only=overdue_only,
    )
    _audit_read(
        conn,
        request=request,
        context=context,
        event_type="connect.supervisor.manual_tasks_viewed",
        reason_code="connect_supervisor_manual_tasks",
    )
    return _safe_projection({
        "ok": True,
        "items": items,
        "pagination": {"limit": limit, "offset": offset, "total": total},
    })


@router.get("/webhook-dlq")
def connect_supervisor_webhook_dlq(
    request: Request,
    response: Response,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
    context: ConnectSupervisorContext = Depends(
        require_connect_supervisor_context
    ),
):
    conn = context.connection
    _no_store(response)
    items = list_dead_letter_summaries(
        conn,
        limit=limit,
        offset=offset,
    )
    total = count_dead_letters(conn)
    _audit_read(
        conn,
        request=request,
        context=context,
        event_type="connect.supervisor.dlq_viewed",
        reason_code="connect_supervisor_webhook_dlq",
    )
    return _safe_projection({
        "ok": True,
        "items": items,
        "pagination": {"limit": limit, "offset": offset, "total": total},
    })


__all__ = [
    "RTM_CONNECT_C5_SUPERVISOR_ROUTES_VERSION",
    "ConnectSupervisorContext",
    "ConnectSupervisorGate",
    "connect_supervisor_connection",
    "connect_supervisor_gate_middleware",
    "require_connect_supervisor_gate",
    "require_connect_supervisor_context",
    "router",
]
