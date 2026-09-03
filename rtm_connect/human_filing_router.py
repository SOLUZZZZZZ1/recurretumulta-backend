"""API privada A1-S de operacion humana sintetica RTM CONNECT.

Las rutas estan apagadas por defecto, exigen sesion bearer individual y dejan
la autorizacion fina al membership del tenant. No aceptan binarios: el recibo
se liga por UUID y SHA-256 a un fixture documental ya persistido.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator, Literal
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.engine import Connection

from database import get_engine
from rtm_connect import human_filing_contracts as a1s_contracts
from rtm_connect import human_filing_policy as a1s_policy
from rtm_connect import human_filing_service as service
from rtm_core.operator_auth_request import extract_bearer_token
from rtm_core.operator_auth_router import (
    load_operator_session_with_device_possession,
)


RTM_CONNECT_A1S_ROUTES_VERSION = "rtm_connect_a1s_human_filing_routes_v1_0"
HUMAN_FILING_AUTHORIZATION_SCHEME = "Bearer"

router = APIRouter(
    prefix="/ops/connect/human-filings",
    tags=["ops-connect-human-filings"],
    include_in_schema=False,
)

_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SYNTHETIC_REFERENCE_PATTERN = r"^a1s-synthetic-[0-9a-f]{24}$"
_TASK_STATUS_PATTERN = (
    r"^(prepared|assigned|reviewing|ready_for_release|released|in_progress|"
    r"awaiting_receipt|outcome_unknown|reconciling|receipt_submitted|"
    r"verified|completed|manual_review|permanent_failed)$"
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PrepareHumanFilingBody(_StrictModel):
    tenant_id: UUID
    case_binding_id: UUID
    representation_evidence_id: UUID
    action_id: UUID
    authorization_id: UUID
    due_at: datetime


class AssignmentBody(_StrictModel):
    assignee_operator_id: UUID


class AttestationBody(_StrictModel):
    package_sha256: str = Field(pattern=_SHA256_PATTERN)
    attestation: str = Field(min_length=3, max_length=96)


class OutcomeBody(_StrictModel):
    outcome: Literal["submitted", "unknown"]
    external_reference: str | None = Field(
        default=None,
        pattern=_SYNTHETIC_REFERENCE_PATTERN,
    )
    witnessed_at: datetime


class ReceiptFixtureBody(_StrictModel):
    document_id: UUID
    document_sha256: str = Field(pattern=_SHA256_PATTERN)
    external_reference: str = Field(pattern=_SYNTHETIC_REFERENCE_PATTERN)
    witnessed_at: datetime


class VerificationBody(_StrictModel):
    observed_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    observed_external_reference: str = Field(
        pattern=_SYNTHETIC_REFERENCE_PATTERN
    )
    observed_package_sha256: str = Field(pattern=_SHA256_PATTERN)
    attestation: str = Field(min_length=3, max_length=96)


class ReconciliationResolutionBody(_StrictModel):
    resolution: Literal[
        "remains_unknown", "manual_review", "permanent_failed"
    ]


class ManualReviewBody(_StrictModel):
    reason_code: Literal[
        "synthetic_evidence_inadmissible",
        "authority_or_representation_changed",
        "assignment_or_separation_exception",
        "workflow_inconsistency",
    ]


@dataclass(frozen=True)
class HumanFilingGate:
    config: a1s_policy.HumanFilingRuntimeConfiguration = field(repr=False)
    authorization: str = field(repr=False)
    x_rtm_device: str | None = field(repr=False)
    rtm_presenter_device: str | None = field(repr=False)


@dataclass(frozen=True)
class HumanFilingContext:
    connection: Connection = field(repr=False)
    session: Any = field(repr=False)
    request_id: str


class HumanFilingRouterRequestError(ValueError):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _error_content(
    *,
    code: str,
    message: str,
    request_id: str | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    return {
        "ok": False,
        "request_id": request_id,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    }


def _http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "retryable": False},
        headers=_NO_STORE_HEADERS,
    )


def _runtime_configuration(
    *, require_enabled: bool = True
) -> a1s_policy.HumanFilingRuntimeConfiguration:
    try:
        return a1s_policy.load_a1s_runtime_configuration(
            require_enabled=require_enabled
        )
    except a1s_policy.HumanFilingRuntimeDisabled as exc:
        raise _http_error(
            404,
            "human_filing.routes_disabled",
            "Not found",
        ) from exc
    except a1s_policy.HumanFilingPolicyError as exc:
        raise _http_error(
            503,
            "human_filing.runtime_unavailable",
            "Operacion humana A1-S no disponible",
        ) from exc


def require_human_filing_gate(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_rtm_device: str | None = Header(
        default=None,
        alias="X-RTM-Device",
    ),
    rtm_presenter_device: str | None = Cookie(
        default=None,
        alias="__Host-rtm_presenter_device",
    ),
) -> HumanFilingGate:
    config = _runtime_configuration(require_enabled=True)
    raw_token = extract_bearer_token(authorization)
    if not raw_token or not (x_rtm_device or rtm_presenter_device):
        raise _http_error(
            401,
            "human_filing.session_required",
            "Sesion individual y dispositivo no validos",
        )
    return HumanFilingGate(
        config=config,
        authorization=str(authorization),
        x_rtm_device=x_rtm_device,
        rtm_presenter_device=rtm_presenter_device,
    )


def human_filing_connection(
    gate: HumanFilingGate = Depends(require_human_filing_gate),
) -> Iterator[Connection]:
    engine = get_engine()
    with engine.begin() as conn:
        boundary = gate.config.boundary
        if boundary is None:
            raise _http_error(
                503,
                "human_filing.boundary_unavailable",
                "Frontera A1-S no disponible",
            )
        try:
            a1s_policy.assert_a1s_database_identity(
                conn,
                expected_database_name=boundary.database_name,
                expected_database_role=boundary.database_role,
            )
        except a1s_policy.HumanFilingPolicyError as exc:
            raise _http_error(
                503,
                "human_filing.database_identity_mismatch",
                "Base de datos A1-S no disponible",
            ) from exc
        yield conn


def require_human_filing_context(
    gate: HumanFilingGate = Depends(require_human_filing_gate),
    conn: Connection = Depends(human_filing_connection),
) -> HumanFilingContext:
    session = load_operator_session_with_device_possession(
        conn,
        authorization=gate.authorization,
        x_rtm_device=gate.x_rtm_device,
        rtm_presenter_device=gate.rtm_presenter_device,
        touch=False,
    )
    if not session:
        raise _http_error(
            401,
            "human_filing.session_invalid",
            "Sesion individual no valida",
        )
    if bool(session.must_change_password) or bool(session.mfa_required):
        raise _http_error(
            403,
            "human_filing.session_not_operational",
            "La sesion individual no puede operar A1-S",
        )
    return HumanFilingContext(
        connection=conn,
        session=session,
        request_id=str(uuid.uuid4()),
    )


async def human_filing_gate_middleware(request: Request, call_next):
    """Oculta todo el prefijo A1-S antes del routing cuando el gate cierra."""

    path = request.url.path.rstrip("/")
    if path == router.prefix or path.startswith(f"{router.prefix}/"):
        try:
            _runtime_configuration(require_enabled=True)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            return JSONResponse(
                status_code=exc.status_code,
                content=_error_content(
                    code=str(detail.get("code") or "human_filing.unavailable"),
                    message=str(detail.get("message") or exc.detail),
                ),
                headers=_NO_STORE_HEADERS,
            )
        response = await call_next(request)
        response.headers.update(_NO_STORE_HEADERS)
        return response
    return await call_next(request)


def _idempotency_key(value: str | None) -> str:
    if value is None:
        raise HumanFilingRouterRequestError(
            "human_filing.idempotency_required",
            "Idempotency-Key es obligatoria",
            400,
        )
    try:
        return a1s_contracts.validate_human_filing_idempotency_key(value)
    except a1s_contracts.HumanFilingContractError as exc:
        raise HumanFilingRouterRequestError(
            "human_filing.idempotency_invalid",
            "Idempotency-Key A1-S no valida",
            422,
        ) from exc


def _expected_version(task_id: str, value: str | None) -> int:
    if value is None:
        raise HumanFilingRouterRequestError(
            "human_filing.precondition_required",
            "If-Match es obligatorio",
            428,
        )
    token = value.strip()
    if token.startswith("W/"):
        token = token[2:].strip()
    if len(token) >= 2 and token[0] == token[-1] == '"':
        token = token[1:-1]
    try:
        resource_id, raw_version = token.rsplit(":", 1)
        if str(UUID(resource_id)) != str(UUID(task_id)):
            raise ValueError("resource mismatch")
        version = int(raw_version)
        if version < 1:
            raise ValueError("invalid version")
        return version
    except (ValueError, TypeError, AttributeError) as exc:
        raise HumanFilingRouterRequestError(
            "human_filing.if_match_invalid",
            "If-Match no identifica esta version de tarea",
            412,
        ) from exc


def _etag(task: dict[str, Any]) -> str:
    version = int(task.get("status_version") or task["version"])
    return f'W/"{task["task_id"]}:{version}"'


def _success(
    context: HumanFilingContext,
    payload: dict[str, Any],
    *,
    status_code: int = 200,
) -> JSONResponse:
    content = {"ok": True, "request_id": context.request_id, **payload}
    headers = dict(_NO_STORE_HEADERS)
    task = payload.get("task")
    if isinstance(task, dict):
        headers["ETag"] = _etag(task)
    return JSONResponse(status_code=status_code, content=content, headers=headers)


def _failure(
    context: HumanFilingContext,
    exc: Exception,
) -> JSONResponse:
    context.connection.rollback()
    if isinstance(exc, service.HumanFilingServiceError):
        status_code = exc.status_code
        code = exc.code
        message = exc.message
        retryable = exc.retryable
    elif isinstance(exc, HumanFilingRouterRequestError):
        status_code = exc.status_code
        code = exc.code
        message = exc.message
        retryable = False
    else:
        status_code = 500
        code = "human_filing.internal_failure"
        message = "No se pudo completar la operacion A1-S"
        retryable = False
    return JSONResponse(
        status_code=status_code,
        content=_error_content(
            code=code,
            message=message,
            request_id=context.request_id,
            retryable=retryable,
        ),
        headers=_NO_STORE_HEADERS,
    )


def _command(
    context: HumanFilingContext,
    operation,
    *,
    created: bool = False,
) -> JSONResponse:
    try:
        task = operation()
    except Exception as exc:
        return _failure(context, exc)
    return _success(
        context,
        {"task": task},
        status_code=201 if created and not task.get("replayed") else 200,
    )


@router.post("")
def prepare_human_filing_route(
    body: PrepareHumanFilingBody,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    try:
        key = _idempotency_key(idempotency_key)
    except HumanFilingRouterRequestError as exc:
        return _failure(context, exc)
    return _command(
        context,
        lambda: service.prepare_human_filing(
            context.connection,
            tenant_id=str(body.tenant_id),
            case_binding_id=str(body.case_binding_id),
            representation_evidence_id=str(body.representation_evidence_id),
            action_id=str(body.action_id),
            authorization_id=str(body.authorization_id),
            due_at=body.due_at.isoformat(),
            operator_id=context.session.operator_id,
            idempotency_key=key,
        ),
        created=True,
    )


@router.get("")
def list_human_filings_route(
    tenant_id: UUID = Query(),
    status: str | None = Query(default=None, pattern=_TASK_STATUS_PATTERN),
    assignee_operator_id: UUID | None = Query(default=None),
    overdue_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    try:
        result = service.list_human_filings(
            context.connection,
            tenant_id=str(tenant_id),
            operator_id=context.session.operator_id,
            status=status,
            assignee_operator_id=(
                str(assignee_operator_id) if assignee_operator_id else None
            ),
            overdue_only=overdue_only,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        return _failure(context, exc)
    return _success(context, result)


@router.get("/context")
def get_human_filing_context_route(
    tenant_id: UUID = Query(),
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    try:
        result = service.get_human_filing_context(
            context.connection,
            tenant_id=str(tenant_id),
            operator_id=context.session.operator_id,
        )
    except Exception as exc:
        return _failure(context, exc)
    return _success(context, result)


@router.get("/tenants")
def list_human_filing_tenants_route(
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    try:
        result = service.list_human_filing_tenants(
            context.connection,
            operator_id=context.session.operator_id,
        )
    except Exception as exc:
        return _failure(context, exc)
    return _success(context, result)


@router.get("/preparation-options")
def list_human_filing_preparation_options_route(
    tenant_id: UUID = Query(),
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    try:
        result = service.list_human_filing_preparation_options(
            context.connection,
            tenant_id=str(tenant_id),
            operator_id=context.session.operator_id,
        )
    except Exception as exc:
        return _failure(context, exc)
    return _success(context, result)


@router.get("/{task_id}")
def get_human_filing_route(
    task_id: UUID,
    tenant_id: UUID = Query(),
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    try:
        task = service.get_human_filing(
            context.connection,
            tenant_id=str(tenant_id),
            task_id=str(task_id),
            operator_id=context.session.operator_id,
        )
    except Exception as exc:
        return _failure(context, exc)
    return _success(context, {"task": task})


@router.get("/{task_id}/receipt-options")
def list_human_filing_receipt_options_route(
    task_id: UUID,
    tenant_id: UUID = Query(),
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    try:
        result = service.list_human_filing_receipt_options(
            context.connection,
            tenant_id=str(tenant_id),
            task_id=str(task_id),
            operator_id=context.session.operator_id,
        )
    except Exception as exc:
        return _failure(context, exc)
    return _success(context, result)


def _mutation_headers(
    *, task_id: UUID, idempotency_key: str | None, if_match: str | None
) -> tuple[str, int]:
    return (
        _idempotency_key(idempotency_key),
        _expected_version(str(task_id), if_match),
    )


def _transition_route(
    *,
    context: HumanFilingContext,
    task_id: UUID,
    tenant_id: UUID,
    idempotency_key: str | None,
    if_match: str | None,
    invoke,
) -> JSONResponse:
    try:
        key, expected_version = _mutation_headers(
            task_id=task_id,
            idempotency_key=idempotency_key,
            if_match=if_match,
        )
    except HumanFilingRouterRequestError as exc:
        return _failure(context, exc)
    return _command(
        context,
        lambda: invoke(str(tenant_id), str(task_id), key, expected_version),
    )


@router.post("/{task_id}/assignments")
def assign_human_filing_route(
    task_id: UUID,
    body: AssignmentBody,
    tenant_id: UUID = Query(),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    if_match: str | None = Header(default=None, alias="If-Match"),
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    return _transition_route(
        context=context, task_id=task_id, tenant_id=tenant_id,
        idempotency_key=idempotency_key, if_match=if_match,
        invoke=lambda tenant, task, key, version: service.assign_human_filing(
            context.connection, tenant_id=tenant, task_id=task,
            assignee_operator_id=str(body.assignee_operator_id),
            operator_id=context.session.operator_id,
            idempotency_key=key, expected_version=version,
        ),
    )


@router.post("/{task_id}/reviews/start")
def begin_review_route(
    task_id: UUID,
    tenant_id: UUID = Query(),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    if_match: str | None = Header(default=None, alias="If-Match"),
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    return _transition_route(
        context=context, task_id=task_id, tenant_id=tenant_id,
        idempotency_key=idempotency_key, if_match=if_match,
        invoke=lambda tenant, task, key, version: service.begin_review(
            context.connection, tenant_id=tenant, task_id=task,
            operator_id=context.session.operator_id, idempotency_key=key,
            expected_version=version,
        ),
    )


@router.post("/{task_id}/reviews/attest")
def attest_review_route(
    task_id: UUID,
    body: AttestationBody,
    tenant_id: UUID = Query(),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    if_match: str | None = Header(default=None, alias="If-Match"),
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    return _transition_route(
        context=context, task_id=task_id, tenant_id=tenant_id,
        idempotency_key=idempotency_key, if_match=if_match,
        invoke=lambda tenant, task, key, version: service.attest_review(
            context.connection, tenant_id=tenant, task_id=task,
            operator_id=context.session.operator_id, idempotency_key=key,
            expected_version=version, package_sha256=body.package_sha256,
            attestation=body.attestation,
        ),
    )


@router.post("/{task_id}/verification-preapprovals")
def preapprove_verifier_route(
    task_id: UUID,
    body: AttestationBody,
    tenant_id: UUID = Query(),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    if_match: str | None = Header(default=None, alias="If-Match"),
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    return _transition_route(
        context=context, task_id=task_id, tenant_id=tenant_id,
        idempotency_key=idempotency_key, if_match=if_match,
        invoke=lambda tenant, task, key, version: service.preapprove_verifier(
            context.connection, tenant_id=tenant, task_id=task,
            operator_id=context.session.operator_id, idempotency_key=key,
            expected_version=version, package_sha256=body.package_sha256,
            attestation=body.attestation,
        ),
    )


@router.post("/{task_id}/releases")
def release_human_filing_route(
    task_id: UUID,
    body: AttestationBody,
    tenant_id: UUID = Query(),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    if_match: str | None = Header(default=None, alias="If-Match"),
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    return _transition_route(
        context=context, task_id=task_id, tenant_id=tenant_id,
        idempotency_key=idempotency_key, if_match=if_match,
        invoke=lambda tenant, task, key, version: service.release_human_filing(
            context.connection, tenant_id=tenant, task_id=task,
            operator_id=context.session.operator_id, idempotency_key=key,
            expected_version=version, package_sha256=body.package_sha256,
            attestation=body.attestation,
        ),
    )


@router.post("/{task_id}/executions/start")
def begin_execution_route(
    task_id: UUID,
    tenant_id: UUID = Query(),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    if_match: str | None = Header(default=None, alias="If-Match"),
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    return _transition_route(
        context=context, task_id=task_id, tenant_id=tenant_id,
        idempotency_key=idempotency_key, if_match=if_match,
        invoke=lambda tenant, task, key, version: service.begin_execution(
            context.connection, tenant_id=tenant, task_id=task,
            operator_id=context.session.operator_id, idempotency_key=key,
            expected_version=version,
        ),
    )


@router.post("/{task_id}/outcomes")
def record_outcome_route(
    task_id: UUID,
    body: OutcomeBody,
    tenant_id: UUID = Query(),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    if_match: str | None = Header(default=None, alias="If-Match"),
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    return _transition_route(
        context=context, task_id=task_id, tenant_id=tenant_id,
        idempotency_key=idempotency_key, if_match=if_match,
        invoke=lambda tenant, task, key, version: service.record_outcome(
            context.connection, tenant_id=tenant, task_id=task,
            operator_id=context.session.operator_id, idempotency_key=key,
            expected_version=version, outcome=body.outcome,
            external_reference=body.external_reference,
            witnessed_at=body.witnessed_at.isoformat(),
        ),
    )


@router.post("/{task_id}/receipts")
def submit_receipt_fixture_route(
    task_id: UUID,
    body: ReceiptFixtureBody,
    tenant_id: UUID = Query(),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    if_match: str | None = Header(default=None, alias="If-Match"),
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    return _transition_route(
        context=context, task_id=task_id, tenant_id=tenant_id,
        idempotency_key=idempotency_key, if_match=if_match,
        invoke=lambda tenant, task, key, version: service.submit_receipt_fixture(
            context.connection, tenant_id=tenant, task_id=task,
            operator_id=context.session.operator_id, idempotency_key=key,
            expected_version=version, document_id=str(body.document_id),
            document_sha256=body.document_sha256,
            external_reference=body.external_reference,
            witnessed_at=body.witnessed_at.isoformat(),
        ),
    )


@router.post("/{task_id}/verifications")
def verify_receipt_route(
    task_id: UUID,
    body: VerificationBody,
    tenant_id: UUID = Query(),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    if_match: str | None = Header(default=None, alias="If-Match"),
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    return _transition_route(
        context=context, task_id=task_id, tenant_id=tenant_id,
        idempotency_key=idempotency_key, if_match=if_match,
        invoke=lambda tenant, task, key, version: (
            service.verify_receipt_and_complete(
                context.connection, tenant_id=tenant, task_id=task,
                operator_id=context.session.operator_id, idempotency_key=key,
                expected_version=version,
                observed_receipt_sha256=body.observed_receipt_sha256,
                observed_external_reference=body.observed_external_reference,
                observed_package_sha256=body.observed_package_sha256,
                attestation=body.attestation,
            )
        ),
    )


@router.post("/{task_id}/reconciliations/start")
def begin_reconciliation_route(
    task_id: UUID,
    tenant_id: UUID = Query(),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    if_match: str | None = Header(default=None, alias="If-Match"),
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    return _transition_route(
        context=context, task_id=task_id, tenant_id=tenant_id,
        idempotency_key=idempotency_key, if_match=if_match,
        invoke=lambda tenant, task, key, version: (
            service.begin_human_reconciliation(
                context.connection, tenant_id=tenant, task_id=task,
                operator_id=context.session.operator_id, idempotency_key=key,
                expected_version=version,
            )
        ),
    )


@router.post("/{task_id}/reconciliations/resolve")
def resolve_reconciliation_route(
    task_id: UUID,
    body: ReconciliationResolutionBody,
    tenant_id: UUID = Query(),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    if_match: str | None = Header(default=None, alias="If-Match"),
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    return _transition_route(
        context=context, task_id=task_id, tenant_id=tenant_id,
        idempotency_key=idempotency_key, if_match=if_match,
        invoke=lambda tenant, task, key, version: (
            service.resolve_human_reconciliation(
                context.connection, tenant_id=tenant, task_id=task,
                operator_id=context.session.operator_id, idempotency_key=key,
                expected_version=version, resolution=body.resolution,
            )
        ),
    )


@router.post("/{task_id}/manual-reviews")
def escalate_to_manual_review_route(
    task_id: UUID,
    body: ManualReviewBody,
    tenant_id: UUID = Query(),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
    if_match: str | None = Header(default=None, alias="If-Match"),
    context: HumanFilingContext = Depends(require_human_filing_context),
) -> JSONResponse:
    return _transition_route(
        context=context,
        task_id=task_id,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        if_match=if_match,
        invoke=lambda tenant, task, key, version: (
            service.escalate_to_manual_review(
                context.connection,
                tenant_id=tenant,
                task_id=task,
                operator_id=context.session.operator_id,
                idempotency_key=key,
                expected_version=version,
                reason_code=body.reason_code,
            )
        ),
    )


__all__ = [
    "RTM_CONNECT_A1S_ROUTES_VERSION",
    "HUMAN_FILING_AUTHORIZATION_SCHEME",
    "HumanFilingContext",
    "HumanFilingGate",
    "human_filing_connection",
    "human_filing_gate_middleware",
    "require_human_filing_context",
    "require_human_filing_gate",
    "router",
]
