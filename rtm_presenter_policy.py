"""Politica fail-closed de RTM Presenter MVP.

El MVP solo puede operar en staging con expedientes sinteticos. Un rol admin no
concede exportacion por si solo: se exige permiso dedicado, motivo, evidencia
de reautenticacion reciente y una exportacion marcada y auditada.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from rtm_presenter_contracts import (
    PresenterClientKind,
    PresenterTicketBinding,
    canonical_sha256,
    normalize_origin,
)


RTM_PRESENTER_POLICY_VERSION = "rtm_presenter_policy_v1_2"
RTM_PRESENTER_FEATURE_FLAG = "RTM_ENABLE_PRESENTER_MVP"
RTM_PRESENTER_EXTENSION_CLIENT_ID = "rtm.presenter.browser_extension.v1"

PRESENTER_DOCUMENT_READ_PERMISSION = "presenter.documents.read"
PRESENTER_DOCUMENT_INGEST_PERMISSION = "presenter.documents.ingest"
PRESENTER_PACKAGE_FREEZE_PERMISSION = "presenter.package.freeze"
PRESENTER_DELIVERY_PREPARE_PERMISSION = "presenter.delivery.prepare"
PRESENTER_HANDOFF_ISSUE_PERMISSION = "presenter.handoff.issue"
PRESENTER_HANDOFF_EXCHANGE_PERMISSION = "presenter.handoff.exchange"
PRESENTER_ADMIN_EXPORT_PERMISSION = "ops.documents.export_exceptional"

PRESENTER_REAUTH_MAX_AGE_SECONDS = 300
PRESENTER_EXPORT_REASON_MIN_LENGTH = 12

_TRUE = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSE = frozenset({"0", "false", "no", "off", "disabled"})
PRESENTER_ADMIN_ROLE_CODE = "rtm.admin"


class PresenterPolicyError(PermissionError):
    """La operacion queda cerrada por la politica Presenter."""


class PresenterRuntimeDisabled(PresenterPolicyError):
    pass


@dataclass(frozen=True)
class PresenterRuntimeConfiguration:
    enabled: bool
    environment: str | None
    synthetic_only: bool = True
    real_data_allowed: bool = False
    external_effects_allowed: bool = False
    direct_storage_allowed: bool = False
    managed_extension_attestation_enabled: bool = False
    routes_default_off: bool = True


@dataclass(frozen=True)
class PresenterActorContext:
    operator_id: str
    operator_session_id: str
    permissions: tuple[str, ...]
    role_codes: tuple[str, ...]
    client_kind: PresenterClientKind
    authenticated_at: datetime
    reauthenticated_at: datetime | None = None
    reauthentication_event_id: str | None = None
    extension_client_id: str | None = None
    managed_extension_attested: bool = False
    extension_attestation_id: str | None = None
    exceptional_export_grant_id: str | None = None
    synthetic_only: bool = True

    def __post_init__(self) -> None:
        # Reutiliza la validacion UUID cerrada del binding sin exportar helpers
        # privados: un binding efimero no se crea; UUID valida aqui directamente.
        from uuid import UUID

        for name in ("operator_id", "operator_session_id"):
            try:
                normalized = str(UUID(str(getattr(self, name))))
            except (TypeError, ValueError, AttributeError) as exc:
                raise PresenterPolicyError(f"{name} debe ser UUID") from exc
            object.__setattr__(self, name, normalized)
        permissions = tuple(sorted({str(item).strip() for item in self.permissions if str(item).strip()}))
        roles = tuple(sorted({str(item).strip().lower() for item in self.role_codes if str(item).strip()}))
        object.__setattr__(self, "permissions", permissions)
        object.__setattr__(self, "role_codes", roles)
        try:
            kind = (
                self.client_kind
                if isinstance(self.client_kind, PresenterClientKind)
                else PresenterClientKind(self.client_kind)
            )
        except (TypeError, ValueError) as exc:
            raise PresenterPolicyError("client_kind no admitido") from exc
        object.__setattr__(self, "client_kind", kind)
        object.__setattr__(self, "authenticated_at", _aware(self.authenticated_at, "authenticated_at"))
        if self.reauthenticated_at is not None:
            object.__setattr__(
                self,
                "reauthenticated_at",
                _aware(self.reauthenticated_at, "reauthenticated_at"),
            )
        event_id = str(self.reauthentication_event_id or "").strip() or None
        if event_id is not None:
            from uuid import UUID

            try:
                event_id = str(UUID(event_id))
            except (TypeError, ValueError, AttributeError) as exc:
                raise PresenterPolicyError(
                    "reauthentication_event_id debe ser UUID"
                ) from exc
        object.__setattr__(self, "reauthentication_event_id", event_id)
        client_id = str(self.extension_client_id or "").strip() or None
        if kind is PresenterClientKind.TRUSTED_EXTENSION:
            if client_id != RTM_PRESENTER_EXTENSION_CLIENT_ID:
                raise PresenterPolicyError("Extension Presenter no confiable")
            if type(self.managed_extension_attested) is not bool:
                raise PresenterPolicyError("Estado de atestacion no valido")
            attestation_id = str(self.extension_attestation_id or "").strip().lower()
            if self.managed_extension_attested:
                if len(attestation_id) != 64 or any(
                    value not in "0123456789abcdef" for value in attestation_id
                ):
                    raise PresenterPolicyError("Atestacion gestionada no valida")
            elif attestation_id:
                raise PresenterPolicyError("Atestacion inconsistente")
        elif client_id is not None:
            raise PresenterPolicyError("Solo la extension puede declarar extension_client_id")
        elif self.managed_extension_attested or self.extension_attestation_id is not None:
            raise PresenterPolicyError("Solo la extension puede declarar atestacion")
        else:
            attestation_id = ""
        object.__setattr__(self, "extension_client_id", client_id)
        object.__setattr__(
            self, "extension_attestation_id", attestation_id or None
        )
        grant_id = str(self.exceptional_export_grant_id or "").strip() or None
        if grant_id is not None:
            from uuid import UUID

            try:
                grant_id = str(UUID(grant_id))
            except (TypeError, ValueError, AttributeError) as exc:
                raise PresenterPolicyError(
                    "exceptional_export_grant_id debe ser UUID"
                ) from exc
        if kind is not PresenterClientKind.ADMIN_EXPORT and grant_id is not None:
            raise PresenterPolicyError(
                "Solo el canal admin puede declarar grant excepcional"
            )
        object.__setattr__(self, "exceptional_export_grant_id", grant_id)
        if self.synthetic_only is not True:
            raise PresenterPolicyError("MVP Presenter solo admite actor sintetico")


@dataclass(frozen=True)
class PresenterExportAuthorization:
    operator_id: str
    operator_session_id: str
    reason: str
    reauthenticated_at: datetime
    reauthentication_event_id: str
    exceptional_export_grant_id: str
    reauthentication_evidence_sha256: str
    watermark: str
    authorized_at: datetime


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PresenterPolicyError(f"{name} exige datetime con zona horaria")
    return value.astimezone(timezone.utc)


def _flag(values: Mapping[str, str], name: str) -> bool | None:
    raw = str(values.get(name) or "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def load_presenter_runtime_configuration(
    values: Mapping[str, str] | None = None,
    *,
    require_enabled: bool = False,
) -> PresenterRuntimeConfiguration:
    env = values if values is not None else os.environ
    raw = str(env.get(RTM_PRESENTER_FEATURE_FLAG) or "").strip().lower()
    if raw and raw not in _TRUE and raw not in _FALSE:
        raise PresenterPolicyError(f"{RTM_PRESENTER_FEATURE_FLAG}_must_be_boolean")
    enabled = raw in _TRUE
    if require_enabled and not enabled:
        raise PresenterRuntimeDisabled(f"{RTM_PRESENTER_FEATURE_FLAG}_must_be_true")
    if not enabled:
        return PresenterRuntimeConfiguration(enabled=False, environment=None)

    expected = {
        "RTM_PRESENTER_SYNTHETIC_ONLY": True,
        "RTM_ALLOW_REAL_CUSTOMER_DATA": False,
        "RTM_PRESENTER_EXTERNAL_EFFECTS_ALLOWED": False,
        "RTM_PRESENTER_DIRECT_STORAGE_ALLOWED": False,
    }
    for name, required in expected.items():
        if _flag(env, name) is not required:
            raise PresenterPolicyError(f"{name}_must_be_{str(required).lower()}")
    environment = str(env.get("RTM_ENV") or "").strip().lower()
    if environment != "staging":
        raise PresenterPolicyError("RTM_ENV_must_be_staging")
    managed_extension_attestation_enabled = _flag(
        env, "RTM_PRESENTER_MANAGED_EXTENSION_ATTESTATION_ENABLED"
    )
    return PresenterRuntimeConfiguration(
        enabled=True,
        environment=environment,
        managed_extension_attestation_enabled=(
            managed_extension_attestation_enabled is True
        ),
    )


def require_presenter_runtime(config: PresenterRuntimeConfiguration) -> None:
    if not isinstance(config, PresenterRuntimeConfiguration) or not config.enabled:
        raise PresenterRuntimeDisabled("Presenter MVP esta cerrado")
    if (
        config.environment != "staging"
        or config.synthetic_only is not True
        or config.real_data_allowed is not False
        or config.external_effects_allowed is not False
        or config.direct_storage_allowed is not False
    ):
        raise PresenterPolicyError("Frontera Presenter no valida")


def _require_permission(actor: PresenterActorContext, permission: str) -> None:
    if permission not in set(actor.permissions):
        raise PresenterPolicyError(f"Permiso Presenter requerido: {permission}")


def authorize_document_list(actor: PresenterActorContext) -> None:
    if actor.client_kind is not PresenterClientKind.OPERATOR_UI:
        raise PresenterPolicyError("La lista documental pertenece a la UI de operador")
    _require_permission(actor, PRESENTER_DOCUMENT_READ_PERMISSION)


def authorize_document_ingest(actor: PresenterActorContext) -> None:
    if actor.client_kind is not PresenterClientKind.OPERATOR_UI:
        raise PresenterPolicyError("El ingreso documental pertenece a la UI de operador")
    _require_permission(actor, PRESENTER_DOCUMENT_INGEST_PERMISSION)


def authorize_package_freeze(actor: PresenterActorContext) -> None:
    if actor.client_kind is not PresenterClientKind.OPERATOR_UI:
        raise PresenterPolicyError("Solo la UI de operador congela paquetes")
    _require_permission(actor, PRESENTER_PACKAGE_FREEZE_PERMISSION)


def authorize_delivery_prepare(actor: PresenterActorContext) -> None:
    """Autoriza solo la preparación; nunca equivale a presentar o enviar."""

    if actor.client_kind is not PresenterClientKind.OPERATOR_UI:
        raise PresenterPolicyError(
            "Solo la UI de operador prepara una entrega controlada"
        )
    _require_permission(actor, PRESENTER_DELIVERY_PREPARE_PERMISSION)


def authorize_handoff_issue(actor: PresenterActorContext) -> None:
    if actor.client_kind is not PresenterClientKind.TRUSTED_EXTENSION:
        raise PresenterPolicyError("El ticket solo puede entregarse a la extension")
    if actor.extension_client_id != RTM_PRESENTER_EXTENSION_CLIENT_ID:
        raise PresenterPolicyError("Audience de extension no valida")
    if not actor.managed_extension_attested or not actor.extension_attestation_id:
        raise PresenterPolicyError("Canal de extension no disponible")
    _require_permission(actor, PRESENTER_HANDOFF_ISSUE_PERMISSION)


def authorize_handoff_exchange_client(actor: PresenterActorContext) -> None:
    """Valida el cliente antes del canje atomico, sin confiar aun en ticket."""

    if actor.client_kind is not PresenterClientKind.TRUSTED_EXTENSION:
        raise PresenterPolicyError("Los bytes solo se entregan a la extension")
    if actor.extension_client_id != RTM_PRESENTER_EXTENSION_CLIENT_ID:
        raise PresenterPolicyError("Audience de extension no valida")
    if not actor.managed_extension_attested or not actor.extension_attestation_id:
        raise PresenterPolicyError("Canal de extension no disponible")
    _require_permission(actor, PRESENTER_HANDOFF_EXCHANGE_PERMISSION)


def authorize_handoff_exchange(
    actor: PresenterActorContext,
    binding: PresenterTicketBinding,
    *,
    request_origin: str,
) -> None:
    authorize_handoff_exchange_client(actor)
    exact_origin = normalize_origin(request_origin)
    if (
        binding.operator_id != actor.operator_id
        or binding.operator_session_id != actor.operator_session_id
        or binding.extension_client_id != actor.extension_client_id
        or binding.portal_origin != exact_origin
    ):
        raise PresenterPolicyError("Ticket fuera de actor, sesion, audience u origen")


def authorize_admin_export(
    actor: PresenterActorContext,
    *,
    reason: str,
    now: datetime | None = None,
) -> PresenterExportAuthorization:
    current = _aware(now or datetime.now(timezone.utc), "now")
    if actor.client_kind is not PresenterClientKind.ADMIN_EXPORT:
        raise PresenterPolicyError("Canal de exportacion admin requerido")
    if set(actor.role_codes) != {PRESENTER_ADMIN_ROLE_CODE}:
        raise PresenterPolicyError("Rol rtm.admin requerido")
    # Ser admin no basta: este permiso debe concederse de forma independiente.
    _require_permission(actor, PRESENTER_ADMIN_EXPORT_PERMISSION)
    # El rol almacena permisos agregados; por tanto, el permiso por si solo no
    # prueba una concesion excepcional individual. Hasta que un repositorio de
    # grants verificados alimente este campo, la ruta remota permanece cerrada.
    if actor.exceptional_export_grant_id is None:
        raise PresenterPolicyError("Concesion excepcional individual requerida")
    clean_reason = " ".join(str(reason or "").split())
    if len(clean_reason) < PRESENTER_EXPORT_REASON_MIN_LENGTH:
        raise PresenterPolicyError("Motivo de exportacion obligatorio y concreto")
    if actor.reauthenticated_at is None:
        raise PresenterPolicyError("Reautenticacion reciente obligatoria")
    if actor.reauthentication_event_id is None:
        raise PresenterPolicyError("Evento de reautenticacion obligatorio")
    reauthenticated = _aware(actor.reauthenticated_at, "reauthenticated_at")
    if reauthenticated <= actor.authenticated_at:
        raise PresenterPolicyError("Login inicial no equivale a reautenticacion")
    age = (current - reauthenticated).total_seconds()
    if age < -30 or age > PRESENTER_REAUTH_MAX_AGE_SECONDS:
        raise PresenterPolicyError("Reautenticacion fuera de ventana")
    # La evidencia no viaja en el body Presenter. Se deriva exclusivamente de
    # la sesion ya reautenticada por /ops/auth/reauthenticate, que actualiza
    # rtm_operator_sessions.last_verified_at.
    raw_evidence = canonical_sha256(
        {
            "evidence_type": "rtm.presenter.session_reauthentication.v1",
            "operator_id": actor.operator_id,
            "operator_session_id": actor.operator_session_id,
            "reauthenticated_at": reauthenticated.isoformat(),
            "reauthentication_event_id": actor.reauthentication_event_id,
        }
    )
    watermark = (
        f"RTM EXPORT SYNTHETIC | operator={actor.operator_id} | "
        f"session={actor.operator_session_id} | at={current.isoformat()}"
    )
    return PresenterExportAuthorization(
        operator_id=actor.operator_id,
        operator_session_id=actor.operator_session_id,
        reason=clean_reason,
        reauthenticated_at=reauthenticated,
        reauthentication_event_id=actor.reauthentication_event_id,
        exceptional_export_grant_id=actor.exceptional_export_grant_id,
        reauthentication_evidence_sha256=raw_evidence,
        watermark=watermark,
        authorized_at=current,
    )


__all__ = [
    "PRESENTER_ADMIN_EXPORT_PERMISSION",
    "PRESENTER_ADMIN_ROLE_CODE",
    "PRESENTER_DOCUMENT_INGEST_PERMISSION",
    "PRESENTER_DOCUMENT_READ_PERMISSION",
    "PRESENTER_DELIVERY_PREPARE_PERMISSION",
    "PRESENTER_HANDOFF_EXCHANGE_PERMISSION",
    "PRESENTER_HANDOFF_ISSUE_PERMISSION",
    "PRESENTER_PACKAGE_FREEZE_PERMISSION",
    "PRESENTER_REAUTH_MAX_AGE_SECONDS",
    "RTM_PRESENTER_EXTENSION_CLIENT_ID",
    "RTM_PRESENTER_FEATURE_FLAG",
    "RTM_PRESENTER_POLICY_VERSION",
    "PresenterActorContext",
    "PresenterExportAuthorization",
    "PresenterPolicyError",
    "PresenterRuntimeConfiguration",
    "PresenterRuntimeDisabled",
    "authorize_admin_export",
    "authorize_document_ingest",
    "authorize_document_list",
    "authorize_delivery_prepare",
    "authorize_handoff_exchange",
    "authorize_handoff_exchange_client",
    "authorize_handoff_issue",
    "authorize_package_freeze",
    "load_presenter_runtime_configuration",
    "require_presenter_runtime",
]
