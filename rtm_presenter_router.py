"""Router privado y no montado por defecto de RTM Presenter.

No existe endpoint de descarga para la UI normal. La unica respuesta binaria
normal se encuentra bajo ``/extension`` y exige sesion individual, permiso,
audience de extension, origen exacto y ticket de un solo uso. La exportacion
ZIP vive en un canal admin separado y conserva sus controles adicionales.
"""

from __future__ import annotations

import re
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
from starlette.concurrency import run_in_threadpool

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
    normalize_origin,
    safe_filename,
)
from rtm_presenter_delivery import PresenterDeliveryService
from rtm_presenter_directory import default_presenter_directory
from rtm_presenter_local_station import PresenterLocalStationService
from rtm_presenter_portal_session import PresenterPortalSessionService
from rtm_presenter_policy import (
    RTM_PRESENTER_EXTENSION_CLIENT_ID,
    PresenterActorContext,
    PresenterPolicyError,
    PresenterRuntimeDisabled,
    authorize_handoff_exchange_client,
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
from rtm_presenter_signer_station import PresenterSignerStationService


RTM_PRESENTER_ROUTER_VERSION = "rtm_presenter_router_v1_7"
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
_RAW_RECEIPT_CONTENT_TYPES = frozenset(
    {"application/pdf", "application/octet-stream"}
)
_RAW_RECEIPT_IDEMPOTENCY_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$"
)
_RAW_RECEIPT_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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


class CorrespondenceConfirmationsBody(_StrictModel):
    destination_reviewed: bool
    interested_confirmed: bool
    representation_confirmed: bool
    text_confirmed: bool
    attachments_confirmed: bool
    data_minimization_confirmed: bool


class CorrespondenceDraftBody(_StrictModel):
    subject: str = Field(min_length=1, max_length=240)
    body: str = Field(min_length=1, max_length=12000)
    confirmations: CorrespondenceConfirmationsBody


class PortalPreparationConfirmationsBody(_StrictModel):
    destination_reviewed: bool
    interested_confirmed: bool
    representation_confirmed: bool
    text_confirmed: bool
    attachments_confirmed: bool


class PortalPreparationBody(_StrictModel):
    form_code: str = Field(min_length=2, max_length=128)
    values: dict[str, str] = Field(min_length=1, max_length=32)
    confirmations: PortalPreparationConfirmationsBody


class PrepareDeliveryBody(_StrictModel):
    channel: Literal["portal", "email"]
    recipient_email: str | None = Field(default=None, min_length=3, max_length=254)
    recipient_confirmed: bool = False
    correspondence: CorrespondenceDraftBody | None = None
    portal_preparation: PortalPreparationBody | None = None


class DestinationLinkProposalBody(_StrictModel):
    label: str = Field(min_length=3, max_length=120)
    portal_url: str = Field(min_length=9, max_length=1024)


class OpenPortalSessionBody(_StrictModel):
    destination_profile_id: UUID
    portal_origin: str = Field(min_length=9, max_length=255)
    representation_mode: Literal["self", "representative"]


class PreparePortalAttachmentIntentBody(_StrictModel):
    field_code: str = Field(min_length=2, max_length=128)
    portal_field_fingerprint_sha256: str = Field(min_length=64, max_length=64)
    document_version_id: UUID
    portal_filename: str | None = Field(default=None, min_length=1, max_length=180)


class RecordSyntheticPortalAttachmentBody(_StrictModel):
    observed_portal_origin: str = Field(min_length=9, max_length=255)
    portal_field_fingerprint_sha256: str = Field(min_length=64, max_length=64)
    observed_document_sha256: str = Field(min_length=64, max_length=64)


class VerifyPortalReceiptBody(_StrictModel):
    receipt_document_version_id: UUID
    expected_receipt_sha256: str = Field(min_length=64, max_length=64)


class RegisterSignerInstallationBody(_StrictModel):
    client_instance_id: UUID
    client_binding_sha256: str = Field(min_length=64, max_length=64)
    station_label: str = Field(min_length=3, max_length=80)
    platform: Literal["windows"]
    client_version: str = Field(min_length=5, max_length=48)


class SignerWorkspaceBody(_StrictModel):
    installation_id: UUID


class RecoverSignerWorkspaceBody(_StrictModel):
    installation_id: UUID
    source_workspace_id: UUID
    expected_task_fingerprint_sha256: str = Field(min_length=64, max_length=64)


@dataclass(frozen=True)
class PresenterRequestContext:
    connection: Connection
    actor: PresenterActorContext
    request_id: str
    operator_device_id: str | None = None
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
        alias="__Host-rtm_presenter_device",
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
                operator_device_id=getattr(session, "device_id", None),
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


def _signer_actor(context: PresenterRequestContext) -> PresenterActorContext:
    """Cambia solo el canal; rol y permisos proceden de la sesión verificada."""

    return replace(
        context.actor,
        client_kind=PresenterClientKind.SIGNER_STATION,
        extension_client_id=None,
        managed_extension_attested=False,
        extension_attestation_id=None,
    )


def _service() -> PresenterService:
    return PresenterService(
        repository=SqlPresenterRepository(),
        runtime=load_presenter_runtime_configuration(require_enabled=True),
        # La exportacion permanece cerrada hasta inyectar un motor que aplique
        # una marca real al tipo documental correspondiente.
        watermarker=None,
        directory=default_presenter_directory(),
    )


def _delivery_service() -> PresenterDeliveryService:
    return PresenterDeliveryService(
        repository=SqlPresenterRepository(),
        runtime=load_presenter_runtime_configuration(require_enabled=True),
    )


def _portal_session_service() -> PresenterPortalSessionService:
    return PresenterPortalSessionService(
        repository=SqlPresenterRepository(),
        runtime=load_presenter_runtime_configuration(require_enabled=True),
    )


def _signer_station_service() -> PresenterSignerStationService:
    return PresenterSignerStationService(
        repository=SqlPresenterRepository(),
        runtime=load_presenter_runtime_configuration(require_enabled=True),
    )


def _local_station_service() -> PresenterLocalStationService:
    return PresenterLocalStationService(
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
    source_original_filename: str | None = Form(default=None, max_length=180),
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

        def ingest_blocking() -> Any:
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

            # Validadores (PDF/DOCX/imagen), B2 y SQL son síncronos y no deben
            # ejecutarse en el hilo del event loop del endpoint async.
            return _service().ingest_external_document(
                context.connection,
                actor=context.actor,
                case_id=str(case_id),
                content=content,
                original_filename=str(file.filename or ""),
                declared_mime=str(file.content_type or ""),
                purpose=purpose,
                source_original_filename=(
                    source_original_filename
                    if isinstance(source_original_filename, str)
                    else str(file.filename or "")
                ),
                synthetic_confirmed=synthetic_confirmed,
                supersedes_document_version_id=(
                    str(supersedes_document_version_id)
                    if supersedes_document_version_id is not None
                    else None
                ),
                storage_writer=storage_writer,
                register_rollback_cleanup=context.register_storage_rollback,
            )

        document = await run_in_threadpool(ingest_blocking)
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


@router.post("/cases/{case_id}/destinations/proposals")
def propose_presenter_destination_link_route(
    case_id: UUID,
    body: DestinationLinkProposalBody,
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        result = _service().propose_destination_link(
            context.connection,
            actor=context.actor,
            case_id=str(case_id),
            label=body.label,
            portal_url=body.portal_url,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(context, result, status_code=202)


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
            recipient_email=body.recipient_email,
            recipient_confirmed=body.recipient_confirmed,
            correspondence=(
                body.correspondence.model_dump()
                if body.correspondence is not None
                else None
            ),
            portal_preparation=(
                body.portal_preparation.model_dump()
                if body.portal_preparation is not None
                else None
            ),
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


@router.get("/signature-queue")
def presenter_signature_queue_route(
    limit: int = Query(default=50, ge=1, le=100),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        queue = _delivery_service().signature_queue(
            context.connection,
            actor=context.actor,
            limit=limit,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "queue": queue,
            "storage_references_exposed": False,
            "synthetic_only": True,
        },
    )


@router.get("/signer/queue")
def presenter_signer_station_queue_route(
    limit: int = Query(default=50, ge=1, le=100),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        queue = _signer_station_service().queue(
            context.connection,
            actor=_signer_actor(context),
            limit=limit,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "station_queue": queue,
            "storage_references_exposed": False,
            "document_bytes_exposed": False,
            "synthetic_only": True,
        },
    )


@router.post("/signer/tasks/{delivery_id}/claim")
def presenter_signer_station_claim_route(
    delivery_id: UUID,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        claim = _signer_station_service().claim(
            context.connection,
            actor=_signer_actor(context),
            delivery_id=str(delivery_id),
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "claim": claim,
            "storage_references_exposed": False,
            "document_bytes_exposed": False,
            "synthetic_only": True,
        },
        status_code=201,
    )


@router.get("/signer/tasks/{delivery_id}/claim")
def presenter_signer_station_current_claim_route(
    delivery_id: UUID,
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        claim = _signer_station_service().current_claim(
            context.connection,
            actor=_signer_actor(context),
            delivery_id=str(delivery_id),
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "claim": claim,
            "storage_references_exposed": False,
            "document_bytes_exposed": False,
            "synthetic_only": True,
        },
    )


@router.post("/signer/tasks/{delivery_id}/claims/{claim_id}/release")
def presenter_signer_station_release_route(
    delivery_id: UUID,
    claim_id: UUID,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        release = _signer_station_service().release(
            context.connection,
            actor=_signer_actor(context),
            delivery_id=str(delivery_id),
            claim_id=str(claim_id),
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "release": release,
            "storage_references_exposed": False,
            "document_bytes_exposed": False,
            "synthetic_only": True,
        },
    )


@router.post("/signer/installations")
def presenter_signer_installation_register_route(
    body: RegisterSignerInstallationBody,
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        station = _local_station_service().register_candidate(
            context.connection,
            actor=_signer_actor(context),
            operator_device_id=context.operator_device_id,
            client_instance_id=str(body.client_instance_id),
            client_binding_sha256=body.client_binding_sha256,
            station_label=body.station_label,
            platform=body.platform,
            client_version=body.client_version,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "station": station,
            "storage_references_exposed": False,
            "document_bytes_exposed": False,
            "synthetic_only": True,
        },
        status_code=201,
    )


@router.get("/signer/installations/{installation_id}")
def presenter_signer_installation_status_route(
    installation_id: UUID,
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        station = _local_station_service().installation(
            context.connection,
            actor=_signer_actor(context),
            operator_device_id=context.operator_device_id,
            installation_id=str(installation_id),
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "station": station,
            "storage_references_exposed": False,
            "document_bytes_exposed": False,
            "synthetic_only": True,
        },
    )


@router.get("/signer/installations/{installation_id}/workspace-recoveries")
def presenter_signer_workspace_recoveries_route(
    installation_id: UUID,
    limit: int = Query(default=20, ge=1, le=50),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        recoveries = _local_station_service().discover_workspaces(
            context.connection,
            actor=_signer_actor(context),
            operator_device_id=context.operator_device_id,
            installation_id=str(installation_id),
            limit=limit,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "workspace_recoveries": recoveries,
            "storage_references_exposed": False,
            "document_bytes_exposed": False,
            "cookie_material_exposed": False,
            "certificate_material_exposed": False,
            "synthetic_only": True,
        },
    )


@router.post("/signer/tasks/{delivery_id}/workspace-recovery")
def presenter_signer_workspace_recovery_route(
    delivery_id: UUID,
    body: RecoverSignerWorkspaceBody,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        workspace = _local_station_service().recover_workspace(
            context.connection,
            actor=_signer_actor(context),
            operator_device_id=context.operator_device_id,
            installation_id=str(body.installation_id),
            delivery_id=str(delivery_id),
            source_workspace_id=str(body.source_workspace_id),
            expected_task_fingerprint_sha256=(
                body.expected_task_fingerprint_sha256
            ),
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "workspace": workspace,
            "storage_references_exposed": False,
            "document_bytes_exposed": False,
            "cookie_material_exposed": False,
            "certificate_material_exposed": False,
            "synthetic_only": True,
        },
        status_code=201,
    )


@router.post(
    "/signer/tasks/{delivery_id}/claims/{claim_id}/workspaces"
)
def presenter_signer_workspace_prepare_route(
    delivery_id: UUID,
    claim_id: UUID,
    body: SignerWorkspaceBody,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        workspace = _local_station_service().prepare_workspace(
            context.connection,
            actor=_signer_actor(context),
            operator_device_id=context.operator_device_id,
            installation_id=str(body.installation_id),
            delivery_id=str(delivery_id),
            claim_id=str(claim_id),
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "workspace": workspace,
            "storage_references_exposed": False,
            "document_bytes_exposed": False,
            "synthetic_only": True,
        },
        status_code=201,
    )


@router.get(
    "/signer/tasks/{delivery_id}/claims/{claim_id}/workspaces/{workspace_id}"
)
def presenter_signer_workspace_status_route(
    delivery_id: UUID,
    claim_id: UUID,
    workspace_id: UUID,
    installation_id: UUID = Query(...),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        workspace = _local_station_service().current_workspace(
            context.connection,
            actor=_signer_actor(context),
            operator_device_id=context.operator_device_id,
            installation_id=str(installation_id),
            delivery_id=str(delivery_id),
            claim_id=str(claim_id),
            workspace_id=str(workspace_id),
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "workspace": workspace,
            "storage_references_exposed": False,
            "document_bytes_exposed": False,
            "synthetic_only": True,
        },
    )


def _transition_signer_workspace(
    *,
    context: PresenterRequestContext,
    delivery_id: UUID,
    claim_id: UUID,
    workspace_id: UUID,
    body: SignerWorkspaceBody,
    action: str,
    idempotency_key: str | None,
) -> JSONResponse:
    try:
        workspace = _local_station_service().transition_workspace(
            context.connection,
            actor=_signer_actor(context),
            operator_device_id=context.operator_device_id,
            installation_id=str(body.installation_id),
            delivery_id=str(delivery_id),
            claim_id=str(claim_id),
            workspace_id=str(workspace_id),
            action=action,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "workspace": workspace,
            "storage_references_exposed": False,
            "document_bytes_exposed": False,
            "synthetic_only": True,
        },
    )


@router.post(
    "/signer/tasks/{delivery_id}/claims/{claim_id}/workspaces/"
    "{workspace_id}/portal-session-expired"
)
def presenter_signer_workspace_expired_route(
    delivery_id: UUID,
    claim_id: UUID,
    workspace_id: UUID,
    body: SignerWorkspaceBody,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    return _transition_signer_workspace(
        context=context,
        delivery_id=delivery_id,
        claim_id=claim_id,
        workspace_id=workspace_id,
        body=body,
        action="portal_session_expired",
        idempotency_key=idempotency_key,
    )


@router.post(
    "/signer/tasks/{delivery_id}/claims/{claim_id}/workspaces/"
    "{workspace_id}/resume"
)
def presenter_signer_workspace_resume_route(
    delivery_id: UUID,
    claim_id: UUID,
    workspace_id: UUID,
    body: SignerWorkspaceBody,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    return _transition_signer_workspace(
        context=context,
        delivery_id=delivery_id,
        claim_id=claim_id,
        workspace_id=workspace_id,
        body=body,
        action="resume",
        idempotency_key=idempotency_key,
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


@router.post("/cases/{case_id}/portal-sessions")
def open_presenter_portal_session_route(
    case_id: UUID,
    body: OpenPortalSessionBody,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        session = _portal_session_service().open_session(
            context.connection,
            actor=context.actor,
            case_id=str(case_id),
            destination_profile_id=str(body.destination_profile_id),
            portal_origin=body.portal_origin,
            representation_mode=body.representation_mode,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "portal_session": session,
            "storage_references_exposed": False,
            "synthetic_only": True,
        },
        status_code=201,
    )


@router.get("/cases/{case_id}/portal-sessions/{portal_session_id}")
def presenter_portal_session_status_route(
    case_id: UUID,
    portal_session_id: UUID,
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        session = _portal_session_service().status(
            context.connection,
            actor=context.actor,
            case_id=str(case_id),
            portal_session_id=str(portal_session_id),
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "portal_session": session,
            "storage_references_exposed": False,
            "synthetic_only": True,
        },
    )


@router.post(
    "/cases/{case_id}/portal-sessions/{portal_session_id}/attachment-intents"
)
def prepare_presenter_portal_attachment_intent_route(
    case_id: UUID,
    portal_session_id: UUID,
    body: PreparePortalAttachmentIntentBody,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        intent = _portal_session_service().prepare_attachment_intent(
            context.connection,
            actor=context.actor,
            case_id=str(case_id),
            portal_session_id=str(portal_session_id),
            field_code=body.field_code,
            portal_field_fingerprint_sha256=(
                body.portal_field_fingerprint_sha256
            ),
            document_version_id=str(body.document_version_id),
            portal_filename=body.portal_filename,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "attachment_intent": intent,
            "document_count": 1,
            "storage_references_exposed": False,
            "synthetic_only": True,
        },
        status_code=201,
    )


@router.post(
    "/extension/cases/{case_id}/portal-sessions/{portal_session_id}"
    "/attachment-intents/{attachment_intent_id}/record-synthetic"
)
def record_presenter_synthetic_attachment_route(
    case_id: UUID,
    portal_session_id: UUID,
    attachment_intent_id: UUID,
    body: RecordSyntheticPortalAttachmentBody,
    x_extension_client: str | None = Header(
        default=None, alias="X-RTM-Presenter-Extension"
    ),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        actor = _extension_actor(context, x_extension_client)
        attachment = _portal_session_service().record_synthetic_attachment(
            context.connection,
            actor=actor,
            case_id=str(case_id),
            portal_session_id=str(portal_session_id),
            attachment_intent_id=str(attachment_intent_id),
            request_origin=body.observed_portal_origin,
            portal_field_fingerprint_sha256=(
                body.portal_field_fingerprint_sha256
            ),
            observed_document_sha256=body.observed_document_sha256,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "attachment": attachment,
            "document_bytes_read": False,
            "external_effects_executed": False,
            "synthetic_only": True,
        },
        status_code=201,
    )


@router.post(
    "/extension/cases/{case_id}/portal-sessions/{portal_session_id}"
    "/receipts/capture"
)
async def capture_presenter_receipt_pending_route(
    case_id: UUID,
    portal_session_id: UUID,
    request: Request,
    capture_source: str | None = Header(
        default=None, alias="X-RTM-Receipt-Capture-Source"
    ),
    observed_portal_origin: str | None = Header(
        default=None, alias="X-RTM-Observed-Portal-Origin"
    ),
    attachment_manifest_sha256: str | None = Header(
        default=None, alias="X-RTM-Attachment-Manifest-SHA256"
    ),
    receipt_filename: str | None = Header(
        default=None, alias="X-RTM-Receipt-Filename"
    ),
    receipt_media_type: str | None = Header(
        default=None, alias="X-RTM-Receipt-Media-Type"
    ),
    synthetic_confirmed: str | None = Header(
        default=None, alias="X-RTM-Synthetic-Confirmed"
    ),
    content_type: str | None = Header(default=None, alias="Content-Type"),
    content_length: str | None = Header(default=None, alias="Content-Length"),
    x_extension_client: str | None = Header(
        default=None, alias="X-RTM-Presenter-Extension"
    ),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    body_buffer = bytearray()
    content: bytes | None = None
    try:
        # Todas las barreras se evalúan antes de abrir el stream. El header de
        # extensión solo declara audience; no sustituye atestación gestionada.
        actor = _extension_actor(context, x_extension_client)
        runtime = load_presenter_runtime_configuration(require_enabled=True)
        if runtime.managed_extension_attestation_enabled is not True:
            raise PresenterPolicyError("Canal Presenter no disponible")
        authorize_handoff_exchange_client(actor)
        source = str(capture_source or "").strip().lower()
        if source == "email_attachment":
            raise PresenterConflict(
                "presenter.receipt_email_capture_not_ready",
                "La fuente email está reservada pero todavía no tiene actor confiable",
            )
        if source != "portal_download":
            raise PresenterConflict(
                "presenter.receipt_capture_source_invalid",
                "Fuente de justificante no admitida",
            )
        if synthetic_confirmed != "true":
            raise PresenterConflict(
                "presenter.synthetic_confirmation_required",
                "Confirmación sintética obligatoria",
            )
        exact_origin = normalize_origin(observed_portal_origin)
        if exact_origin != str(observed_portal_origin or "").strip():
            raise PresenterConflict(
                "presenter.receipt_origin_invalid",
                "El origen observado debe ser HTTPS exacto y canónico",
            )
        exact_manifest_sha = str(attachment_manifest_sha256 or "").strip()
        if not _RAW_RECEIPT_SHA256_RE.fullmatch(exact_manifest_sha):
            raise PresenterConflict(
                "presenter.receipt_attachment_manifest_invalid",
                "La huella del manifiesto no es válida",
            )
        exact_filename = safe_filename(receipt_filename)
        if (
            exact_filename != str(receipt_filename or "").strip()
            or not exact_filename.lower().endswith(".pdf")
        ):
            raise PresenterConflict(
                "presenter.receipt_filename_invalid",
                "El nombre del justificante PDF no es válido",
            )
        exact_media_type = str(receipt_media_type or "").strip().lower()
        exact_content_type = str(content_type or "").strip().lower()
        if (
            exact_media_type != "application/pdf"
            or exact_content_type not in _RAW_RECEIPT_CONTENT_TYPES
        ):
            raise PresenterConflict(
                "presenter.receipt_content_type_invalid",
                "La captura raw solo admite bytes de un justificante PDF",
            )
        raw_content_length = str(content_length or "").strip()
        if (
            not raw_content_length.isascii()
            or not raw_content_length.isdecimal()
        ):
            raise PresenterConflict(
                "presenter.receipt_length_required",
                "Content-Length exacto es obligatorio antes de capturar",
            )
        declared_length = int(raw_content_length)
        if (
            declared_length <= 0
            or declared_length > RTM_PRESENTER_MAX_FILE_BYTES
        ):
            raise PresenterConflict(
                "presenter.receipt_too_large",
                "El justificante está vacío o supera el límite Presenter",
            )
        if str(request.headers.get("transfer-encoding") or "").strip():
            raise PresenterConflict(
                "presenter.receipt_transfer_encoding_forbidden",
                "La captura exige longitud exacta y no admite transferencia ambigua",
            )
        if str(request.headers.get("content-encoding") or "").strip():
            raise PresenterConflict(
                "presenter.receipt_content_encoding_forbidden",
                "La captura exige bytes PDF sin codificación de contenido",
            )
        exact_idempotency_key = str(idempotency_key or "").strip()
        if not _RAW_RECEIPT_IDEMPOTENCY_RE.fullmatch(exact_idempotency_key):
            raise PresenterConflict(
                "presenter.portal_idempotency_key_required",
                "La captura exige una clave idempotente válida",
            )

        async for chunk in request.stream():
            if not isinstance(chunk, (bytes, bytearray)):
                raise PresenterConflict(
                    "presenter.receipt_stream_invalid",
                    "El stream del justificante no es binario",
                )
            if not chunk:
                continue
            if (
                len(body_buffer) + len(chunk) > declared_length
                or len(body_buffer) + len(chunk) > RTM_PRESENTER_MAX_FILE_BYTES
            ):
                raise PresenterConflict(
                    "presenter.receipt_length_mismatch",
                    "Los bytes recibidos superan la longitud declarada",
                )
            body_buffer.extend(chunk)
        if len(body_buffer) != declared_length:
            raise PresenterConflict(
                "presenter.receipt_length_mismatch",
                "Los bytes recibidos no coinciden con Content-Length",
            )
        content = bytes(body_buffer)

        def storage_writer(
            upload: PresenterExternalDocumentUpload,
            register_rollback_cleanup: Callable[[str, str], None],
        ) -> tuple[str, str]:
            bucket = get_b2_bucket()
            key = (
                f"cases/{case_id}/presenter_receipts/"
                f"{uuid4().hex}{upload.extension}"
            )
            register_rollback_cleanup(bucket, key)
            get_s3_client().put_object(
                Bucket=bucket,
                Key=key,
                Body=upload.content,
                ContentType=upload.media_type,
            )
            return bucket, key

        def capture_blocking() -> Any:
            # El validador aislado, B2 y SQL son síncronos. Mantener toda esta
            # unidad fuera del event loop evita que un parser no confiable
            # paralice las demás peticiones mientras el supervisor lo termina.
            return PresenterPortalSessionService(
                repository=SqlPresenterRepository(), runtime=runtime
            ).capture_receipt_pending(
                context.connection,
                actor=actor,
                case_id=str(case_id),
                portal_session_id=str(portal_session_id),
                request_origin=exact_origin,
                capture_source=source,
                attachment_manifest_sha256=exact_manifest_sha,
                content=content,
                original_filename=exact_filename,
                declared_mime=exact_media_type,
                synthetic_confirmed=True,
                idempotency_key=exact_idempotency_key,
                storage_writer=storage_writer,
                register_rollback_cleanup=context.register_storage_rollback,
            )

        capture = await run_in_threadpool(capture_blocking)
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    finally:
        if body_buffer:
            body_buffer[:] = b"\x00" * len(body_buffer)
            body_buffer.clear()
        if content is not None:
            del content
    return _success(
        context,
        {
            "receipt_capture": capture,
            "state": "receipt_pending",
            "capture_requires_explicit_human_action": True,
            "native_download_observed": False,
            "download_is_submission": False,
            "sent_at": None,
            "followup_activation_ready": False,
            "case_status_changed": False,
            "storage_references_exposed": False,
            "synthetic_only": True,
        },
        status_code=201,
    )


@router.post(
    "/cases/{case_id}/portal-sessions/{portal_session_id}/receipt/verify"
)
def verify_presenter_portal_receipt_route(
    case_id: UUID,
    portal_session_id: UUID,
    body: VerifyPortalReceiptBody,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: PresenterRequestContext = Depends(require_presenter_context),
) -> JSONResponse:
    try:
        receipt = _portal_session_service().verify_receipt_and_enable_tracking(
            context.connection,
            actor=context.actor,
            case_id=str(case_id),
            portal_session_id=str(portal_session_id),
            receipt_document_version_id=str(body.receipt_document_version_id),
            expected_receipt_sha256=body.expected_receipt_sha256,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        raise _as_http_exception(context, exc) from exc
    return _success(
        context,
        {
            "receipt": receipt,
            "followup_activation_ready": True,
            "legal_deadline_calculated": False,
            "case_status_changed": False,
            "synthetic_only": True,
        },
        status_code=201,
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
