"""Contratos inmutables del puente documental RTM Presenter.

Presenter no automatiza la firma ni el envio juridico. Su unica capacidad
operativa es entregar, a una extension confiable y mediante un ticket de un
solo uso, los bytes de una version documental ya congelada en un paquete.

Los contratos publicos de este modulo nunca contienen referencias de
almacenamiento, URLs firmadas, credenciales ni tokens de sesion.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit
from uuid import UUID


RTM_PRESENTER_CONTRACT_VERSION = "rtm.presenter.v1"
RTM_PRESENTER_SYNTHETIC_MARKER = "RTM_PRESENTER_SYNTHETIC_ONLY"
RTM_PRESENTER_MAX_ITEMS = 32
RTM_PRESENTER_MAX_FILE_BYTES = 25 * 1024 * 1024
RTM_PRESENTER_MAX_TICKET_TTL_SECONDS = 300

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,95}$")
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
_MEDIA_TYPE_RE = re.compile(
    r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]{0,126}$"
)
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._() -]+")


class PresenterContractError(ValueError):
    """El material no cumple el contrato cerrado de Presenter."""


class PresenterDocumentState(str, Enum):
    DRAFT = "draft"
    REVIEW = "review"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"


class PresenterPackageStatus(str, Enum):
    DRAFT = "draft"
    FROZEN = "frozen"
    CANCELLED = "cancelled"


class PresenterClientKind(str, Enum):
    OPERATOR_UI = "operator_ui"
    SIGNER_STATION = "signer_station"
    TRUSTED_EXTENSION = "trusted_extension"
    ADMIN_EXPORT = "admin_export"


def _uuid(value: Any, name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PresenterContractError(f"{name} debe ser UUID") from exc


def _sha256(value: Any, name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise PresenterContractError(f"{name} debe ser SHA-256 hexadecimal")
    return normalized


def _positive_int(value: Any, name: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PresenterContractError(f"{name} debe ser entero positivo")
    if maximum is not None and value > maximum:
        raise PresenterContractError(f"{name} supera el maximo permitido")
    return value


def _timestamp(value: Any, name: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PresenterContractError(f"{name} no es timestamp ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PresenterContractError(f"{name} exige zona horaria")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_origin(value: Any) -> str:
    """Devuelve un origen HTTPS exacto; nunca acepta paths ni comodines."""

    raw = str(value or "").strip()
    if not raw or "*" in raw:
        raise PresenterContractError("portal_origin debe ser exacto")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise PresenterContractError("portal_origin debe usar HTTPS")
    if parsed.username or parsed.password:
        raise PresenterContractError("portal_origin no admite userinfo")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise PresenterContractError("portal_origin no admite path, query o fragment")
    host = parsed.hostname.lower().rstrip(".")
    if not host or ".." in host:
        raise PresenterContractError("portal_origin no valido")
    try:
        port = parsed.port
    except ValueError as exc:
        raise PresenterContractError("portal_origin contiene puerto no valido") from exc
    if port is None or port == 443:
        return f"https://{host}"
    return f"https://{host}:{port}"


def safe_filename(value: Any) -> str:
    raw = str(value or "").replace("\\", "_").replace("/", "_").strip()
    cleaned = _SAFE_FILENAME_RE.sub("_", raw).strip(" .")[:180]
    if not cleaned or cleaned in {".", ".."}:
        raise PresenterContractError("portal_filename no valido")
    return cleaned


def canonical_json(value: Mapping[str, Any]) -> bytes:
    if not isinstance(value, Mapping):
        raise PresenterContractError("El material canonico debe ser un objeto")
    try:
        return json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PresenterContractError("Material no canonicalizable") from exc


def canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _immutable_mapping(value: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PresenterContractError(f"{name} debe ser objeto")
    # Canonicalizar valida que no haya objetos arbitrarios ni NaN. Se vuelve a
    # cargar para romper referencias mutables aportadas por el llamador.
    clean = json.loads(canonical_json(value).decode("utf-8"))
    return MappingProxyType(clean)


@dataclass(frozen=True)
class PresenterDocumentVersion:
    """Proyeccion sanitizada; deliberadamente no incluye bucket ni key."""

    document_version_id: str
    case_id: str
    logical_document_id: str
    version_number: int
    sha256: str
    purpose: str
    state: PresenterDocumentState
    scan_status: str
    original_filename: str
    media_type: str
    size_bytes: int
    source_kind: str
    synthetic_only: bool = True

    def __post_init__(self) -> None:
        for name in ("document_version_id", "case_id", "logical_document_id"):
            object.__setattr__(self, name, _uuid(getattr(self, name), name))
        object.__setattr__(
            self, "version_number", _positive_int(self.version_number, "version_number")
        )
        object.__setattr__(self, "sha256", _sha256(self.sha256, "sha256"))
        for name in ("purpose", "source_kind"):
            value = str(getattr(self, name) or "").strip().lower()
            if not _CODE_RE.fullmatch(value):
                raise PresenterContractError(f"{name} no valido")
            object.__setattr__(self, name, value)
        try:
            state = (
                self.state
                if isinstance(self.state, PresenterDocumentState)
                else PresenterDocumentState(self.state)
            )
        except (TypeError, ValueError) as exc:
            raise PresenterContractError("state documental no valido") from exc
        object.__setattr__(self, "state", state)
        scan_status = str(self.scan_status or "").strip().lower()
        if scan_status not in {"pending", "clean", "blocked", "error"}:
            raise PresenterContractError("scan_status no valido")
        if state is PresenterDocumentState.ACTIVE and scan_status != "clean":
            raise PresenterContractError("Un documento activo debe estar limpio")
        object.__setattr__(self, "scan_status", scan_status)
        object.__setattr__(
            self, "original_filename", safe_filename(self.original_filename)
        )
        media_type = str(self.media_type or "").strip().lower()
        if not _MEDIA_TYPE_RE.fullmatch(media_type):
            raise PresenterContractError("media_type no valido")
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(
            self,
            "size_bytes",
            _positive_int(
                self.size_bytes,
                "size_bytes",
                maximum=RTM_PRESENTER_MAX_FILE_BYTES,
            ),
        )
        if self.synthetic_only is not True:
            raise PresenterContractError("MVP Presenter solo admite datos sinteticos")

    def sanitized(self) -> dict[str, Any]:
        return {
            "document_version_id": self.document_version_id,
            "case_id": self.case_id,
            "logical_document_id": self.logical_document_id,
            "version_number": self.version_number,
            "sha256": self.sha256,
            "purpose": self.purpose,
            "state": self.state.value,
            "scan_status": self.scan_status,
            "original_filename": self.original_filename,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "source_kind": self.source_kind,
            "synthetic_only": True,
        }


@dataclass(frozen=True)
class PresenterPackageItem:
    item_id: str
    document_version_id: str
    logical_document_id: str
    document_version: int
    document_sha256: str
    item_order: int
    field_code: str
    purpose: str
    portal_filename: str
    media_type: str
    size_bytes: int
    required: bool = True

    def __post_init__(self) -> None:
        for name in ("item_id", "document_version_id", "logical_document_id"):
            object.__setattr__(self, name, _uuid(getattr(self, name), name))
        object.__setattr__(
            self,
            "document_version",
            _positive_int(self.document_version, "document_version"),
        )
        object.__setattr__(
            self,
            "document_sha256",
            _sha256(self.document_sha256, "document_sha256"),
        )
        object.__setattr__(
            self, "item_order", _positive_int(self.item_order, "item_order")
        )
        field_code = str(self.field_code or "").strip().lower()
        if not _FIELD_RE.fullmatch(field_code):
            raise PresenterContractError("field_code no valido")
        object.__setattr__(self, "field_code", field_code)
        purpose = str(self.purpose or "").strip().lower()
        if not _CODE_RE.fullmatch(purpose):
            raise PresenterContractError("purpose no valido")
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(self, "portal_filename", safe_filename(self.portal_filename))
        media_type = str(self.media_type or "").strip().lower()
        if not _MEDIA_TYPE_RE.fullmatch(media_type):
            raise PresenterContractError("media_type no valido")
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(
            self,
            "size_bytes",
            _positive_int(
                self.size_bytes,
                "size_bytes",
                maximum=RTM_PRESENTER_MAX_FILE_BYTES,
            ),
        )
        if self.required is not True and self.required is not False:
            raise PresenterContractError("required debe ser booleano")

    def material(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "document_version_id": self.document_version_id,
            "logical_document_id": self.logical_document_id,
            "document_version": self.document_version,
            "document_sha256": self.document_sha256,
            "item_order": self.item_order,
            "field_code": self.field_code,
            "purpose": self.purpose,
            "portal_filename": self.portal_filename,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "required": self.required,
        }


@dataclass(frozen=True)
class FrozenPresenterPackage:
    package_id: str
    logical_package_id: str
    package_version: int
    case_id: str
    destination_profile_id: str
    destination_profile_code: str
    destination_profile_version: int
    destination_profile_sha256: str
    portal_origin: str
    representation_mode: str
    authorization_document_version_id: str | None
    created_by_operator_id: str
    frozen_by_operator_id: str
    frozen_at: str
    expires_at: str
    items: tuple[PresenterPackageItem, ...]
    manifest_sha256: str
    status: PresenterPackageStatus = PresenterPackageStatus.FROZEN
    synthetic_only: bool = True
    contract_version: str = RTM_PRESENTER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "package_id",
            "logical_package_id",
            "case_id",
            "destination_profile_id",
            "created_by_operator_id",
            "frozen_by_operator_id",
        ):
            object.__setattr__(self, name, _uuid(getattr(self, name), name))
        if self.authorization_document_version_id is not None:
            object.__setattr__(
                self,
                "authorization_document_version_id",
                _uuid(
                    self.authorization_document_version_id,
                    "authorization_document_version_id",
                ),
            )
        object.__setattr__(
            self, "package_version", _positive_int(self.package_version, "package_version")
        )
        code = str(self.destination_profile_code or "").strip().lower()
        if not _CODE_RE.fullmatch(code):
            raise PresenterContractError("destination_profile_code no valido")
        object.__setattr__(self, "destination_profile_code", code)
        object.__setattr__(
            self,
            "destination_profile_version",
            _positive_int(
                self.destination_profile_version, "destination_profile_version"
            ),
        )
        object.__setattr__(
            self,
            "destination_profile_sha256",
            _sha256(self.destination_profile_sha256, "destination_profile_sha256"),
        )
        object.__setattr__(self, "portal_origin", normalize_origin(self.portal_origin))
        representation = str(self.representation_mode or "").strip().lower()
        if representation not in {"self", "representative"}:
            raise PresenterContractError("representation_mode no valido")
        if representation == "representative" and not self.authorization_document_version_id:
            raise PresenterContractError(
                "La representacion exige una version de autorizacion"
            )
        object.__setattr__(self, "representation_mode", representation)
        frozen_at = _timestamp(self.frozen_at, "frozen_at")
        expires_at = _timestamp(self.expires_at, "expires_at")
        if datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= datetime.fromisoformat(
            frozen_at.replace("Z", "+00:00")
        ):
            raise PresenterContractError("expires_at debe ser posterior a frozen_at")
        object.__setattr__(self, "frozen_at", frozen_at)
        object.__setattr__(self, "expires_at", expires_at)
        items = tuple(self.items)
        if not 1 <= len(items) <= RTM_PRESENTER_MAX_ITEMS:
            raise PresenterContractError("Numero de items Presenter no admitido")
        if any(type(item) is not PresenterPackageItem for item in items):
            raise PresenterContractError("items exige PresenterPackageItem exactos")
        ordered = tuple(sorted(items, key=lambda item: item.item_order))
        if tuple(item.item_order for item in ordered) != tuple(range(1, len(ordered) + 1)):
            raise PresenterContractError("item_order debe ser contiguo desde 1")
        if len({item.item_id for item in ordered}) != len(ordered):
            raise PresenterContractError("item_id duplicado")
        if len({item.document_version_id for item in ordered}) != len(ordered):
            raise PresenterContractError("Una version documental no puede repetirse")
        object.__setattr__(self, "items", ordered)
        try:
            status = (
                self.status
                if isinstance(self.status, PresenterPackageStatus)
                else PresenterPackageStatus(self.status)
            )
        except (TypeError, ValueError) as exc:
            raise PresenterContractError("status de paquete no valido") from exc
        if status is not PresenterPackageStatus.FROZEN:
            raise PresenterContractError("Este contrato solo representa paquetes congelados")
        object.__setattr__(self, "status", status)
        if self.synthetic_only is not True:
            raise PresenterContractError("MVP Presenter solo admite paquetes sinteticos")
        if self.contract_version != RTM_PRESENTER_CONTRACT_VERSION:
            raise PresenterContractError("Version de contrato Presenter no admitida")
        digest = _sha256(self.manifest_sha256, "manifest_sha256")
        object.__setattr__(self, "manifest_sha256", digest)
        if canonical_sha256(self.manifest_material()) != digest:
            raise PresenterContractError("manifest_sha256 no coincide con el paquete")

    def manifest_material(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "package_id": self.package_id,
            "logical_package_id": self.logical_package_id,
            "package_version": self.package_version,
            "case_id": self.case_id,
            "destination_profile_id": self.destination_profile_id,
            "destination_profile_code": self.destination_profile_code,
            "destination_profile_version": self.destination_profile_version,
            "destination_profile_sha256": self.destination_profile_sha256,
            "portal_origin": self.portal_origin,
            "representation_mode": self.representation_mode,
            "authorization_document_version_id": self.authorization_document_version_id,
            "created_by_operator_id": self.created_by_operator_id,
            "frozen_by_operator_id": self.frozen_by_operator_id,
            "frozen_at": self.frozen_at,
            "expires_at": self.expires_at,
            "items": [item.material() for item in self.items],
            "synthetic_marker": RTM_PRESENTER_SYNTHETIC_MARKER,
            "synthetic_only": True,
        }


@dataclass(frozen=True)
class PresenterTicketBinding:
    ticket_id: str
    ticket_sha256: str
    operator_id: str
    operator_session_id: str
    extension_client_id: str
    case_id: str
    package_id: str
    package_item_id: str
    portal_origin: str
    field_code: str
    issued_at: str
    expires_at: str
    used_at: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "ticket_id",
            "operator_id",
            "operator_session_id",
            "case_id",
            "package_id",
            "package_item_id",
        ):
            object.__setattr__(self, name, _uuid(getattr(self, name), name))
        object.__setattr__(
            self, "ticket_sha256", _sha256(self.ticket_sha256, "ticket_sha256")
        )
        client_id = str(self.extension_client_id or "").strip()
        if not _CODE_RE.fullmatch(client_id):
            raise PresenterContractError("extension_client_id no valido")
        object.__setattr__(self, "extension_client_id", client_id)
        object.__setattr__(self, "portal_origin", normalize_origin(self.portal_origin))
        field_code = str(self.field_code or "").strip().lower()
        if not _FIELD_RE.fullmatch(field_code):
            raise PresenterContractError("field_code no valido")
        object.__setattr__(self, "field_code", field_code)
        issued = _timestamp(self.issued_at, "issued_at")
        expires = _timestamp(self.expires_at, "expires_at")
        issued_dt = datetime.fromisoformat(issued.replace("Z", "+00:00"))
        expires_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        ttl = (expires_dt - issued_dt).total_seconds()
        if ttl <= 0 or ttl > RTM_PRESENTER_MAX_TICKET_TTL_SECONDS:
            raise PresenterContractError("TTL de ticket no permitido")
        object.__setattr__(self, "issued_at", issued)
        object.__setattr__(self, "expires_at", expires)
        if self.used_at is not None:
            object.__setattr__(self, "used_at", _timestamp(self.used_at, "used_at"))


@dataclass(frozen=True)
class IssuedPresenterTicket:
    """El token bruto solo vive en esta respuesta para la extension."""

    ticket_id: str
    token: str = field(repr=False)
    expires_at: str = ""
    package_item_id: str = ""
    field_code: str = ""
    portal_origin: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "ticket_id", _uuid(self.ticket_id, "ticket_id"))
        token = str(self.token or "")
        if len(token) < 43 or any(char.isspace() for char in token):
            raise PresenterContractError("Token Presenter no valido")
        object.__setattr__(self, "expires_at", _timestamp(self.expires_at, "expires_at"))
        object.__setattr__(
            self,
            "package_item_id",
            _uuid(self.package_item_id, "package_item_id"),
        )
        field_code = str(self.field_code or "").strip().lower()
        if not _FIELD_RE.fullmatch(field_code):
            raise PresenterContractError("field_code no valido")
        object.__setattr__(self, "field_code", field_code)
        object.__setattr__(self, "portal_origin", normalize_origin(self.portal_origin))


@dataclass(frozen=True)
class PresenterFilePayload:
    content: bytes = field(repr=False)
    filename: str = ""
    media_type: str = "application/octet-stream"
    sha256: str = ""
    package_id: str = ""
    package_item_id: str = ""
    field_code: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.content, bytes) or not self.content:
            raise PresenterContractError("La entrega exige bytes no vacios")
        object.__setattr__(self, "filename", safe_filename(self.filename))
        media_type = str(self.media_type or "").strip().lower()
        if not _MEDIA_TYPE_RE.fullmatch(media_type):
            raise PresenterContractError("media_type no valido")
        object.__setattr__(self, "media_type", media_type)
        digest = _sha256(self.sha256, "sha256")
        if hashlib.sha256(self.content).hexdigest() != digest:
            raise PresenterContractError("Los bytes no coinciden con su SHA-256")
        object.__setattr__(self, "sha256", digest)
        for name in ("package_id", "package_item_id"):
            object.__setattr__(self, name, _uuid(getattr(self, name), name))
        field_code = str(self.field_code or "").strip().lower()
        if not _FIELD_RE.fullmatch(field_code):
            raise PresenterContractError("field_code no valido")
        object.__setattr__(self, "field_code", field_code)

    @property
    def headers(self) -> Mapping[str, str]:
        return MappingProxyType(
            {
                "Content-Disposition": f'attachment; filename="{self.filename}"',
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "X-RTM-Document-SHA256": self.sha256,
                "X-RTM-Presenter-Field": self.field_code,
            }
        )


@dataclass(frozen=True)
class PresenterAdminExportPayload:
    content: bytes = field(repr=False)
    filename: str = ""
    manifest_sha256: str = ""
    export_sha256: str = ""
    watermark: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.content, bytes) or not self.content:
            raise PresenterContractError("La exportacion exige bytes")
        object.__setattr__(self, "filename", safe_filename(self.filename))
        manifest_digest = _sha256(self.manifest_sha256, "manifest_sha256")
        export_digest = _sha256(self.export_sha256, "export_sha256")
        if hashlib.sha256(self.content).hexdigest() != export_digest:
            raise PresenterContractError("export_sha256 no coincide con el bundle")
        object.__setattr__(self, "manifest_sha256", manifest_digest)
        object.__setattr__(self, "export_sha256", export_digest)
        watermark = str(self.watermark or "").strip()
        if not watermark.startswith("RTM EXPORT SYNTHETIC |"):
            raise PresenterContractError("Marca de agua Presenter no valida")
        object.__setattr__(self, "watermark", watermark)

    @property
    def headers(self) -> Mapping[str, str]:
        return MappingProxyType(
            {
                "Content-Disposition": f'attachment; filename="{self.filename}"',
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "X-RTM-Export-SHA256": self.export_sha256,
                "X-RTM-Manifest-SHA256": self.manifest_sha256,
            }
        )


def build_frozen_package(
    *,
    package_id: str,
    logical_package_id: str,
    package_version: int,
    case_id: str,
    destination_profile_id: str,
    destination_profile_code: str,
    destination_profile_version: int,
    destination_profile_sha256: str,
    portal_origin: str,
    representation_mode: str,
    authorization_document_version_id: str | None,
    created_by_operator_id: str,
    frozen_by_operator_id: str,
    frozen_at: str,
    expires_at: str,
    items: Sequence[PresenterPackageItem],
) -> FrozenPresenterPackage:
    common = dict(
        package_id=package_id,
        logical_package_id=logical_package_id,
        package_version=package_version,
        case_id=case_id,
        destination_profile_id=destination_profile_id,
        destination_profile_code=destination_profile_code,
        destination_profile_version=destination_profile_version,
        destination_profile_sha256=destination_profile_sha256,
        portal_origin=portal_origin,
        representation_mode=representation_mode,
        authorization_document_version_id=authorization_document_version_id,
        created_by_operator_id=created_by_operator_id,
        frozen_by_operator_id=frozen_by_operator_id,
        frozen_at=frozen_at,
        expires_at=expires_at,
        items=tuple(items),
    )
    # Normaliza el material antes de calcular la huella congelada.
    normalized_origin = normalize_origin(portal_origin)
    frozen_stamp = _timestamp(frozen_at, "frozen_at")
    expiry_stamp = _timestamp(expires_at, "expires_at")
    material = {
        "contract_version": RTM_PRESENTER_CONTRACT_VERSION,
        "package_id": _uuid(package_id, "package_id"),
        "logical_package_id": _uuid(logical_package_id, "logical_package_id"),
        "package_version": _positive_int(package_version, "package_version"),
        "case_id": _uuid(case_id, "case_id"),
        "destination_profile_id": _uuid(
            destination_profile_id, "destination_profile_id"
        ),
        "destination_profile_code": str(destination_profile_code or "").strip().lower(),
        "destination_profile_version": _positive_int(
            destination_profile_version, "destination_profile_version"
        ),
        "destination_profile_sha256": _sha256(
            destination_profile_sha256, "destination_profile_sha256"
        ),
        "portal_origin": normalized_origin,
        "representation_mode": str(representation_mode or "").strip().lower(),
        "authorization_document_version_id": (
            _uuid(
                authorization_document_version_id,
                "authorization_document_version_id",
            )
            if authorization_document_version_id is not None
            else None
        ),
        "created_by_operator_id": _uuid(
            created_by_operator_id, "created_by_operator_id"
        ),
        "frozen_by_operator_id": _uuid(
            frozen_by_operator_id, "frozen_by_operator_id"
        ),
        "frozen_at": frozen_stamp,
        "expires_at": expiry_stamp,
        "items": [item.material() for item in sorted(items, key=lambda item: item.item_order)],
        "synthetic_marker": RTM_PRESENTER_SYNTHETIC_MARKER,
        "synthetic_only": True,
    }
    digest = canonical_sha256(material)
    return FrozenPresenterPackage(**common, manifest_sha256=digest)


__all__ = [
    "RTM_PRESENTER_CONTRACT_VERSION",
    "RTM_PRESENTER_MAX_FILE_BYTES",
    "RTM_PRESENTER_MAX_ITEMS",
    "RTM_PRESENTER_MAX_TICKET_TTL_SECONDS",
    "RTM_PRESENTER_SYNTHETIC_MARKER",
    "FrozenPresenterPackage",
    "IssuedPresenterTicket",
    "PresenterAdminExportPayload",
    "PresenterClientKind",
    "PresenterContractError",
    "PresenterDocumentState",
    "PresenterDocumentVersion",
    "PresenterFilePayload",
    "PresenterPackageItem",
    "PresenterPackageStatus",
    "PresenterTicketBinding",
    "build_frozen_package",
    "canonical_json",
    "canonical_sha256",
    "normalize_origin",
    "safe_filename",
]
