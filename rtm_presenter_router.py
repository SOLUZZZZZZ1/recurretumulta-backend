"""Router privado y no montado por defecto de RTM Presenter.

No existe endpoint de descarga para la UI normal. La unica respuesta binaria
normal se encuentra bajo ``/extension`` y exige sesion individual, permiso,
audience de extension, origen exacto y ticket de un solo uso. La exportacion
ZIP vive en un canal admin separado y conserva sus controles adicionales.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Literal
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from b2_storage import get_b2_bucket, get_s3_client
from database import get_engine
from rtm_core.operator_auth_router import (
    load_operator_session_with_device_possession,
)
from rtm_core.operator_auth_request import (
    OperatorAuthRoutesDisabled,
    OperatorAuthRuntimeMisconfigured,
    load_operator_auth_runtime_config,
)
from rtm_core.operator_auth_service import (
    has_explicit_reauthentication,
)
from rtm_presenter_contracts import (
    RTM_PRESENTER_MAX_FILE_BYTES,
    PresenterClientKind,
    PresenterContractError,
)
from rtm_presenter_delivery import PresenterDeliveryService
from rtm_presenter_policy import (
    RTM_PRESENTER_EXTENSION_CLIENT_ID,
    PresenterActorContext,
    PresenterPolicyError,
    PresenterRuntimeDisabled,
    load_presenter_runtime_configuration,
)
from rtm_presenter_service import (
    PresenterConflict,
    PresenterExternalDocumentUpload,
    PresenterItemSelection,
    PresenterService,
    PresenterServiceError,
    SqlPresenterRepository,
)


RTM_PRESENTER_ROUTER_VERSION = "rtm_presenter_router_v1_1"
router = APIRouter(
    prefix="/ops/presenter",
    tags=["ops-presenter"],
    include_in_schema=False,
)

_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}
_MAX_MULTIPART_OVERHEAD_BYTES = 2 * 1024 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PackageItemBody(_StrictModel):
    document_version_id: UUID
    item_order: int = Field(ge=1, le=32)
    field_code: str = Field(min_length=2, max_length=96)
    portal_filename: str = Field(min_length=1, max_length=160)


class FreezePackageBody(_StrictModel):
    destination_profile_id: UUID
    portal_origin: str = Field(min_length=9, max_length=255)
    representation_mode: Literal["self", "representative"]
    authorization_document_version_id: UUID | None = None
    expires_at: datetime
    supersedes_package_id: UUID | None = None
    items: list[PackageItemBody] = Field(min_length=1, max_length=32)


class IssueTicketBody(_StrictModel):
    portal_origin: str = Field(min_length=9, max_length=255)
    ttl_seconds: int = Field(default=90, ge=1, le=300)


class ExchangeTicketBody(_StrictModel):
    ticket: str = Field(min_length=43, max_length=256)


class AdminExportBody(_StrictModel):
    reason: str = Field(min_length=12, max_length=500)


class PrepareDeliveryBody(_StrictModel):
    channel: Literal["portal", "email"]


@dataclass(frozen=True)
class PresenterRequestContext:
    connection: Connection
    actor: PresenterActorContext
    request_id: str
    rollback_cleanups: list[Callable[[], None]] = field(default_factory=list)

    def register_storage_rollback(self, bucket: str, key: str) -> None:
        self.rollback_cleanups.append(
            lambda bucket=bucket, key=key: _delete_presenter_object(bucket, key)
        )


def _delete_presenter_object(bucket: str, key: str) -> None:
    """Best-effort; nunca oculta el fallo transaccional original."""

    try:
        get_s3_client().delete_object(Bucket=bucket, Key=key)
    except Exception:
        pass


def _run_rollback_cleanups(callbacks: list[Callable[[], None]]) -> None:
    while callbacks:
        cleanup = callbacks.pop()
        try:
            cleanup()
        except Exception:
            pass


def _parse_database_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _request_id(value: str | None) -> str:
    import uuid

    raw = str(value or "").strip()
    try:
        return str(uuid.UUID(raw)) if raw else str(uuid.uuid4())
    except ValueError:
        return str(uuid.uuid4())


def require_presenter_context(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_rtm_device: str | None = Header(default=None, alias="X-RTM-Device"),
    rtm_presenter_device: str | None = Cookie(
        default=None,
        alias="rtm_presenter_device",
    ),
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> Iterator[PresenterRequestContext]:
    try:
        config = load_presenter_runtime_configuration(require_enabled=True)
    except PresenterRuntimeDisabled as exc:
        raise HTTPException(
            status_code=404,
            detail="Not found",
            headers=_NO_STORE_HEADERS,
        ) from exc
    except PresenterPolicyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Presenter no disponible",
            headers=_NO_STORE_HEADERS,
        ) from exc
    try:
        load_operator_auth_runtime_config(require_enabled=True)
    except OperatorAuthRoutesDisabled as exc:
        raise HTTPException(
            status_code=404,
            detail="Not found",
            headers=_NO_STORE_HEADERS,
        ) from exc
    except OperatorAuthRuntimeMisconfigured as exc:
        raise HTTPException(
            status_code=503,
            detail="Autenticacion individual no disponible",
            headers=_NO_STORE_HEADERS,
        ) from exc
    rollback_cleanups: list[Callable[[], None]] = []
    try:
        with get_engine().begin() as conn:
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
                    detail="Sesion individual y dispositivo validos requeridos",
                    headers=_NO_STORE_HEADERS,
                )
            if session.must_change_password or session.mfa_required:
                raise HTTPException(
                    status_code=403,
                    detail="Completa los controles de identidad antes de usar Presenter",
                    headers=_NO_STORE_HEADERS,
                )
            explicit_reauthentication = has_explicit_reauthentication(session)
            session_row = conn.execute(
                text(
                    """
                    SELECT s.login_at, verified.occurred_at, verified.id
                    FROM rtm_operator_sessions s
                    LEFT JOIN LATERAL (
                        SELECT e.id, e.occurred_at
                        FROM rtm_operator_access_events e
                        WHERE e.session_id=s.id
                          AND e.operator_id=s.operator_id
                          AND e.event_type='auth.reauthenticated'
                          AND e.result='success'
                          AND e.reason_code='password_reverified'
                          AND e.occurred_at=s.last_verified_at
                        ORDER BY e.occurred_at DESC, e.id DESC
                        LIMIT 1
                    ) verified ON TRUE
                    WHERE s.id=CAST(:session_id AS UUID)
                      AND s.operator_id=CAST(:operator_id AS UUID)
                      AND s.status='active' AND s.expires_at > NOW()
                    """
                ),
                {
                    "session_id": session.session_id,
                    "operator_id": session.operator_id,
                },
            ).first()
            if not session_row:
                raise HTTPException(
                    status_code=401,
                    detail="Sesion no activa",
                    headers=_NO_STORE_HEADERS,
                )
            authenticated_at = _parse_database_timestamp(session_row[0])
            if authenticated_at is None:
                raise HTTPException(
                    status_code=401,
                    detail="Sesion no verificable",
                    headers=_NO_STORE_HEADERS,
                )
            actor = PresenterActorContext(
                operator_id=session.operator_id,
                operator_session_id=session.session_id,
                permissions=tuple(session.permissions),
                role_codes=((session.role_code,) if session.role_code else ()),
                client_kind=PresenterClientKind.OPERATOR_UI,
                authenticated_at=authenticated_at,
                reauthenticated_at=(
                    _parse_database_timestamp(session_row[1])
                    if explicit_reauthentication
                    else None
                ),
                reauthentication_event_id=(
                    str(session_row[2])
                    if explicit_reauthentication and session_row[2] is not None
                    else None
                ),
                synthetic_only=True,
            )
            # Config se valida antes de abrir la transaccion. El service vuelve a
            # validarla en cada operacion y no confia solo en el router.
            del config
            yield PresenterRequestContext(
                connection=conn,
                actor=actor,
                request_id=_request_id(x_request_id),
                rollback_cleanups=rollback_cleanups,
            )
    except BaseException:
        # Incluye excepciones del endpoint y fallos al salir de engine.begin(),
        # especialmente un COMMIT fallido tras haber subido el objeto a B2.
        _run_rollback_cleanups(rollback_cleanups)
        raise


def _extension_actor(
    context: PresenterRequestContext,
    extension_client_id: str | None,
) -> PresenterActorContext:
    if str(extension_client_id or "").strip() != RTM_PRESENTER_EXTENSION_CLIENT_ID:
        raise PresenterPolicyError("Audience de extension requerida")
    return replace(
        context.actor,
        client_kind=PresenterClientKind.TRUSTED_EXTENSION,
        extension_client_id=RTM_PRESENTER_EXTENSION_CLIENT_ID,
        # Un header es solo audience declarada y nunca una atestacion. Este
        # router remoto permanece cerrado hasta integrar un verificador
        # gestionado que construya el actor con prueba criptografica valida.
        managed_extension_attested=False,
        extension_attestation_id=None,
    )


def _admin_actor(context: PresenterRequestContext) -> PresenterActorContext:
    return replace(
        context.actor,
        client_kind=PresenterClientKind.ADMIN_EXPORT,
        extension_client_id=None,
    )


def _service() -> PresenterService:
    return PresenterService(
        repository=SqlPresenterRepository(),
        runtime=load_presenter_runtime_configuration(require_enabled=True),
        # La exportacion permanece cerrada hasta inyectar un motor que aplique
        # una marca real al tipo documental correspondiente.
        watermarker=None,
    )


def _delivery_service() -> PresenterDeliveryService:
    return PresenterDeliveryService(
        repository=SqlPresenterRepository(),
        runtime=load_presenter_runtime_configuration(require_enabled=True),
    )


def _as_http_exception(
    context: PresenterRequestContext, exc: Exception
) -> HTTPException:
    if isinstance(exc, PresenterServiceError):
        status = exc.status_code
        code = exc.code
        message = exc.message
    elif isinstance(exc, PresenterRuntimeDisabled):
        status, code, message = 404, "presenter.not_found", "Not found"
    elif isinstance(exc, PresenterPolicyError):
        status, code, message = 403, "presenter.forbidden", "Operacion no autorizada"
    elif isinstance(exc, PresenterContractError):
        status, code, message = 422, "presenter.contract_invalid", "Contrato Presenter no valido"
    else:
        status, code, message = 500, "presenter.internal_failure", "No se pudo completar la operacion"
    return HTTPException(
        status_code=status,
        headers=_NO_STORE_HEADERS,
        detail={
            "ok": False,
            "request_id": context.request_id,
            "error": {"code": code, "message": message, "retryable": False},
        },
    )


def _success(
    context: PresenterRequestContext,
    payload: dict[str, Any],
    *,
    status_code: int = 200,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        headers=_NO_STORE_HEADERS,
        content={"ok": True, "request_id": context.request_id, **payload},
    )


@router.post("/cases/{case_id}/documents/external")
async def ingest_external_document_route(
    case_id: UUID,
    request: Request,
    file: UploadFile = File(...),
    purpose: str = Form(..., min_length=3, max_length=64),
    synthetic_confirmed: Literal[True] = Form(...),
    supersedes_document_version_id: UUID | None = Form(default=None),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        # Esta comprobacion precede incluso a file.read(); el formulario no es
        # un detector de datos, solo una barrera explicita adicional al scope
        # staging/test_mode/A1-S que aplica el service.
        if synthetic_confirmed is not True:
            raise PresenterConflict(
                "presenter.synthetic_confirmation_required",
                "Confirmacion sintetica obligatoria",
            )
        raw_content_length = str(request.headers.get("content-length") or "").strip()
        if raw_content_length:
            try:
                content_length = int(raw_content_length)
            except ValueError as exc:
                raise PresenterConflict(
                    "presenter.external_document_length_invalid",
                    "Longitud multipart no valida",
                ) from exc
            if (
                content_length < 0
                or content_length
                > RTM_PRESENTER_MAX_FILE_BYTES + _MAX_MULTIPART_OVERHEAD_BYTES
            ):
                raise PresenterConflict(
                    "presenter.external_document_too_large",
                    "El documento supera el limite Presenter",
                )
        # Este limite acota la lectura de aplicacion. El reverse proxy/servidor
        # ASGI debe conservar su body limit para chunked y parse/spool previo.
        uploaded_size = getattr(file, "size", None)
        if (
            isinstance(uploaded_size, int)
            and not isinstance(uploaded_size, bool)
            and uploaded_size > RTM_PRESENTER_MAX_FILE_BYTES
        ):
            raise PresenterConflict(
                "presenter.external_document_too_large",
                "El documento supera el limite Presenter",
            )
        content = await file.read(RTM_PRESENTER_MAX_FILE_BYTES + 1)
        if len(content) > RTM_PRESENTER_MAX_FILE_BYTES:
            raise PresenterConflict(
                "presenter.external_document_too_large",
                "El documento supera el limite Presenter",
            )

        def storage_writer(
            upload: PresenterExternalDocumentUpload,
            register_rollback_cleanup: Callable[[str, str], None],
        ) -> tuple[str, str]:
            bucket = get_b2_bucket()
            key = (
                f"cases/{case_id}/presenter_external/"
                f"{uuid4().hex}{upload.extension}"
            )
            # La key se conoce y queda programada para borrado antes del PUT.
            # Asi tambien cubrimos respuesta B2 perdida tras persistir bytes.
            register_rollback_cleanup(bucket, key)
            get_s3_client().put_object(
                Bucket=bucket,
                Key=key,
                Body=upload.content,
                ContentType=upload.media_type,
            )
            return bucket, key

        document = _service().ingest_external_document(
            context.connection,
            actor=context.actor,
            case_id=str(case_id),
            content=content,
            original_filename=str(file.filename or ""),
            declared_mime=str(file.content_type or ""),
            purpose=purpose,
            synthetic_confirmed=synthetic_confirmed,
            supersedes_document_version_id=(
                str(supersedes_document_version_id)
                if supersedes_document_version_id is not None
                else None
            ),
            storage_writer=storage_writer,
            register_rollback_cleanup=context.register_storage_rollback,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    finally:
        try:
            await file.close()
        except Exception:
            pass
    return _success(
        context,
        {
            "document": {
                **document.sanitized(),
                "security_disposition": "pending_security_scan",
                "eligible_for_package": False,
            },
            "storage_references_exposed": False,
            "synthetic_only": True,
        },
        status_code=201,
    )


@router.get("/cases/{case_id}/documents")
def list_presenter_documents_route(
    case_id: UUID,
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        result = _service().list_documents(
            context.connection, actor=context.actor, case_id=str(case_id)
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(context, result)


@router.get("/cases/{case_id}/workspace")
def presenter_workspace_route(
    case_id: UUID,
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        result = _service().workspace(
            context.connection, actor=context.actor, case_id=str(case_id)
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(context, result)


@router.get("/cases/{case_id}/destinations/search")
def search_presenter_destinations_route(
    case_id: UUID,
    q: str = Query(min_length=2, max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        result = _service().search_destinations(
            context.connection,
            actor=context.actor,
            case_id=str(case_id),
            query=q,
            limit=limit,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(context, result)


@router.post("/cases/{case_id}/packages/freeze")
def freeze_presenter_package_route(
    case_id: UUID,
    body: FreezePackageBody,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        package = _service().freeze_package(
            context.connection,
            actor=context.actor,
            case_id=str(case_id),
            destination_profile_id=str(body.destination_profile_id),
            portal_origin=body.portal_origin,
            representation_mode=body.representation_mode,
            authorization_document_version_id=(
                str(body.authorization_document_version_id)
                if body.authorization_document_version_id
                else None
            ),
            selections=[
                PresenterItemSelection(
                    document_version_id=str(item.document_version_id),
                    item_order=item.item_order,
                    field_code=item.field_code,
                    portal_filename=item.portal_filename,
                )
                for item in body.items
            ],
            expires_at=body.expires_at,
            idempotency_key=idempotency_key,
            supersedes_package_id=(
                str(body.supersedes_package_id)
                if body.supersedes_package_id
                else None
            ),
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "package": {
                "package_id": package.package_id,
                "logical_package_id": package.logical_package_id,
                "package_version": package.package_version,
                "case_id": package.case_id,
                "status": package.status.value,
                "portal_origin": package.portal_origin,
                "destination_profile_id": package.destination_profile_id,
                "destination_profile_code": package.destination_profile_code,
                "destination_profile_version": package.destination_profile_version,
                "destination_profile_sha256": package.destination_profile_sha256,
                "representation_mode": package.representation_mode,
                "authorization_document_version_id": (
                    package.authorization_document_version_id
                ),
                "manifest_sha256": package.manifest_sha256,
                "items": [item.material() for item in package.items],
                "expires_at": package.expires_at,
                "download_available": False,
                "zip_available": False,
            }
        },
        status_code=201,
    )


@router.post(
    "/cases/{case_id}/packages/{package_id}/deliveries/prepare"
)
def prepare_presenter_delivery_route(
    case_id: UUID,
    package_id: UUID,
    body: PrepareDeliveryBody,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        delivery = _delivery_service().prepare(
            context.connection,
            actor=context.actor,
            case_id=str(case_id),
            package_id=str(package_id),
            channel=body.channel,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "delivery": delivery,
            "storage_references_exposed": False,
            "synthetic_only": True,
        },
        status_code=201,
    )


@router.get(
    "/cases/{case_id}/packages/{package_id}/deliveries/{delivery_id}"
)
def presenter_delivery_status_route(
    case_id: UUID,
    package_id: UUID,
    delivery_id: UUID,
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        delivery = _delivery_service().status(
            context.connection,
            actor=context.actor,
            case_id=str(case_id),
            package_id=str(package_id),
            delivery_id=str(delivery_id),
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "delivery": delivery,
            "storage_references_exposed": False,
            "synthetic_only": True,
        },
    )


@router.post(
    "/extension/cases/{case_id}/packages/{package_id}/items/{package_item_id}/tickets"
)
def issue_presenter_ticket_route(
    case_id: UUID,
    package_id: UUID,
    package_item_id: UUID,
    body: IssueTicketBody,
    x_extension_client: str | None = Header(
        default=None, alias="X-RTM-Presenter-Extension"
    ),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        actor = _extension_actor(context, x_extension_client)
        ticket = _service().issue_ticket(
            context.connection,
            actor=actor,
            case_id=str(case_id),
            package_id=str(package_id),
            package_item_id=str(package_item_id),
            portal_origin=body.portal_origin,
            ttl_seconds=body.ttl_seconds,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    # Esta es la unica respuesta que contiene el ticket bruto y solo se alcanza
    # desde el canal de extension. No contiene URL ni storage reference.
    return _success(
        context,
        {
            "ticket": {
                "ticket_id": ticket.ticket_id,
                "token": ticket.token,
                "expires_at": ticket.expires_at,
                "package_item_id": ticket.package_item_id,
                "field_code": ticket.field_code,
                "portal_origin": ticket.portal_origin,
                "single_use": True,
                "audience": RTM_PRESENTER_EXTENSION_CLIENT_ID,
            }
        },
        status_code=201,
    )


@router.post("/extension/tickets/exchange")
def exchange_presenter_ticket_route(
    body: ExchangeTicketBody,
    origin: str | None = Header(default=None, alias="Origin"),
    x_extension_client: str | None = Header(
        default=None, alias="X-RTM-Presenter-Extension"
    ),
    context: PresenterRequestContext = Depends(require_presenter_context),
):
    try:
        actor = _extension_actor(context, x_extension_client)
        payload = _service().exchange_ticket(
            context.connection,
            actor=actor,
            raw_ticket=body.ticket,
            request_origin=str(origin or ""),
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return Response(
        content=payload.content,
        media_type=payload.media_type,
        headers=dict(payload.headers),
    )


@router.post("/admin/cases/{case_id}/packages/{package_id}/exports")
def export_presenter_package_admin_route(
    case_id: UUID,
    package_id: UUID,
    body: AdminExportBody,
    context: PresenterRequestContext = Depends(require_presenter_context),
):
    try:
        payload = _service().export_package_admin(
            context.connection,
            actor=_admin_actor(context),
            case_id=str(case_id),
            package_id=str(package_id),
            reason=body.reason,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return Response(
        content=payload.content,
        media_type="application/zip",
        headers=dict(payload.headers),
    )


__all__ = [
    "RTM_PRESENTER_ROUTER_VERSION",
    "PresenterRequestContext",
    "require_presenter_context",
    "router",
]
