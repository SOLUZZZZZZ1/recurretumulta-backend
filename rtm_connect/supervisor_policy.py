"""Politica de activacion del panel supervisor de RTM CONNECT C5.

El panel queda cerrado por defecto, solo puede activarse en staging y exige
autenticacion individual. La autorizacion funcional se comprueba ademas con
el permiso explicito ``ops.supervise`` de la sesion cargada.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import unquote, urlparse

from rtm_core.environment_contract import assert_environment_ready
from rtm_core.operator_auth_request import (
    OperatorAuthRuntimeConfig,
    OperatorAuthRuntimeMisconfigured,
    load_operator_auth_runtime_config,
)


RTM_CONNECT_C5_SUPERVISOR_POLICY_VERSION = (
    "rtm_connect_c5_supervisor_policy_v1_0"
)
CONNECT_SUPERVISOR_PERMISSION = "ops.supervise"

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"", "0", "false", "no", "off", "disabled"}


class ConnectSupervisorRoutesDisabled(RuntimeError):
    """Las rutas CONNECT supervisoras permanecen cerradas."""


class ConnectSupervisorRuntimeMisconfigured(RuntimeError):
    """La configuracion no cumple las barreras de C5."""


@dataclass(frozen=True)
class ConnectSupervisorRuntimeConfig:
    environment: str
    instance_id: str
    data_namespace: str
    database_name: str
    side_effect_policy: str
    allowed_origins: tuple[str, ...]
    document_input_policy: str
    enabled: bool
    auth: OperatorAuthRuntimeConfig

    @property
    def available(self) -> bool:
        return (
            self.environment == "staging"
            and "staging" in self.instance_id
            and "staging" in self.data_namespace
            and "staging" in self.database_name
            and self.side_effect_policy == "isolated"
            and bool(self.allowed_origins)
            and self.document_input_policy == "synthetic_only"
            and self.enabled
            and self.auth.available
        )


@dataclass(frozen=True)
class ConnectSupervisorStagingBoundary:
    environment: str
    instance_id: str
    data_namespace: str
    database_name: str
    side_effect_policy: str
    allowed_origins: tuple[str, ...]
    document_input_policy: str


def _strict_flag(value: str | None, *, default: bool = False) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ValueError("Valor booleano no reconocido")


def _contains_forbidden_live_marker(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return any(marker in normalized for marker in ("production", "prod", "live"))


def assert_connect_supervisor_staging_boundary(
    environ: Mapping[str, str] | None = None,
) -> ConnectSupervisorStagingBoundary:
    """Valida el entorno completo antes de abrir una conexion C5."""

    source = environ if environ is not None else os.environ
    try:
        central = assert_environment_ready(source)
    except RuntimeError as exc:
        raise ConnectSupervisorRuntimeMisconfigured(str(exc)) from exc

    environment = str(source.get("RTM_ENV") or "").strip().lower()
    instance_id = str(source.get("RTM_INSTANCE_ID") or "").strip().lower()
    data_namespace = str(
        source.get("RTM_DATA_NAMESPACE") or ""
    ).strip().lower()
    side_effect_policy = str(
        source.get("RTM_SIDE_EFFECT_POLICY") or ""
    ).strip().lower()
    confirmation = str(
        source.get("RTM_ENVIRONMENT_CONFIRMATION") or ""
    ).strip()
    parsed_database = urlparse(str(source.get("DATABASE_URL") or "").strip())
    database_name = unquote(parsed_database.path.lstrip("/")).split("/", 1)[0]
    database_name = database_name.strip().lower()
    raw_origins = str(source.get("ALLOWED_ORIGINS") or "")
    allowed_origins = tuple(
        item.strip() for item in raw_origins.split(",") if item.strip()
    )
    document_input_policy = str(
        source.get("RTM_DOCUMENT_INPUT_POLICY") or ""
    ).strip().lower()
    expected_branch = str(
        source.get("RTM_EXPECTED_BRANCH") or ""
    ).strip()

    if central.environment != "staging" or environment != "staging":
        raise ConnectSupervisorRuntimeMisconfigured(
            "RTM CONNECT C5 solo puede ejecutarse en staging"
        )
    if (
        "staging" not in instance_id
        or _contains_forbidden_live_marker(instance_id)
    ):
        raise ConnectSupervisorRuntimeMisconfigured(
            "RTM_INSTANCE_ID debe identificar staging"
        )
    if (
        "staging" not in data_namespace
        or _contains_forbidden_live_marker(data_namespace)
    ):
        raise ConnectSupervisorRuntimeMisconfigured(
            "RTM_DATA_NAMESPACE debe identificar staging"
        )
    if confirmation != "RTM_STAGING_ISOLATED":
        raise ConnectSupervisorRuntimeMisconfigured(
            "Falta confirmacion de staging aislado"
        )
    if not parsed_database.scheme.startswith("postgresql"):
        raise ConnectSupervisorRuntimeMisconfigured(
            "DATABASE_URL debe ser PostgreSQL"
        )
    if (
        "staging" not in database_name
        or _contains_forbidden_live_marker(database_name)
    ):
        raise ConnectSupervisorRuntimeMisconfigured(
            "La base C5 debe estar identificada como staging"
        )
    if side_effect_policy != "isolated":
        raise ConnectSupervisorRuntimeMisconfigured(
            "RTM CONNECT C5 requiere efectos aislados"
        )
    if not allowed_origins or "*" in allowed_origins:
        raise ConnectSupervisorRuntimeMisconfigured(
            "C5 requiere una allowlist CORS explicita"
        )
    if any(
        urlparse(origin).scheme not in {"http", "https"}
        or not urlparse(origin).netloc
        for origin in allowed_origins
    ):
        raise ConnectSupervisorRuntimeMisconfigured(
            "ALLOWED_ORIGINS contiene un origen no valido"
        )
    if document_input_policy != "synthetic_only":
        raise ConnectSupervisorRuntimeMisconfigured(
            "C5 requiere RTM_DOCUMENT_INPUT_POLICY=synthetic_only"
        )
    if not expected_branch or expected_branch == "main":
        raise ConnectSupervisorRuntimeMisconfigured(
            "C5 requiere una rama staging explicita distinta de main"
        )
    if any(bool(value) for value in central.capabilities.values()):
        raise ConnectSupervisorRuntimeMisconfigured(
            "C5 exige todas las capacidades externas globales desactivadas"
        )
    return ConnectSupervisorStagingBoundary(
        environment=environment,
        instance_id=instance_id,
        data_namespace=data_namespace,
        database_name=database_name,
        side_effect_policy=side_effect_policy,
        allowed_origins=allowed_origins,
        document_input_policy=document_input_policy,
    )


def assert_connect_supervisor_database_identity(
    connection,
    *,
    expected_database_name: str,
) -> str:
    """Vincula la configuracion validada con la base realmente conectada."""

    actual = str(
        connection.exec_driver_sql("SELECT current_database()").scalar_one()
        or ""
    ).strip().lower()
    expected = str(expected_database_name or "").strip().lower()
    if not expected or actual != expected or "staging" not in actual:
        raise ConnectSupervisorRuntimeMisconfigured(
            "La conexion C5 no corresponde a la base staging declarada"
        )
    return actual


def load_connect_supervisor_runtime_config(
    environ: Mapping[str, str] | None = None,
    *,
    require_enabled: bool = True,
) -> ConnectSupervisorRuntimeConfig:
    source = environ if environ is not None else os.environ
    environment = str(source.get("RTM_ENV") or "").strip().lower()
    data_namespace = str(
        source.get("RTM_DATA_NAMESPACE") or ""
    ).strip().lower()
    side_effect_policy = str(
        source.get("RTM_SIDE_EFFECT_POLICY") or ""
    ).strip().lower()
    instance_id = str(source.get("RTM_INSTANCE_ID") or "").strip().lower()
    database_name = ""
    allowed_origins: tuple[str, ...] = ()
    document_input_policy = str(
        source.get("RTM_DOCUMENT_INPUT_POLICY") or ""
    ).strip().lower()
    try:
        enabled = _strict_flag(
            source.get("RTM_ENABLE_CONNECT_SUPERVISOR_V1"),
            default=False,
        )
        auth = load_operator_auth_runtime_config(
            source,
            require_enabled=False,
        )
    except (ValueError, OperatorAuthRuntimeMisconfigured) as exc:
        raise ConnectSupervisorRuntimeMisconfigured(str(exc)) from exc

    config = ConnectSupervisorRuntimeConfig(
        environment=environment,
        instance_id=instance_id,
        data_namespace=data_namespace,
        database_name=database_name,
        side_effect_policy=side_effect_policy,
        allowed_origins=allowed_origins,
        document_input_policy=document_input_policy,
        enabled=enabled,
        auth=auth,
    )
    if require_enabled and not enabled:
        raise ConnectSupervisorRoutesDisabled(
            "Panel supervisor RTM CONNECT desactivado"
        )
    if enabled:
        boundary = assert_connect_supervisor_staging_boundary(source)
        config = ConnectSupervisorRuntimeConfig(
            environment=boundary.environment,
            instance_id=boundary.instance_id,
            data_namespace=boundary.data_namespace,
            database_name=boundary.database_name,
            side_effect_policy=boundary.side_effect_policy,
            allowed_origins=boundary.allowed_origins,
            document_input_policy=boundary.document_input_policy,
            enabled=enabled,
            auth=auth,
        )
        try:
            real_data_enabled = _strict_flag(
                source.get("RTM_ALLOW_REAL_CUSTOMER_DATA"),
                default=True,
            )
            outbound_enabled = any(
                _strict_flag(source.get(name), default=True)
                for name in (
                    "RTM_ENABLE_EXTERNAL_SUBMISSION",
                    "RTM_ENABLE_OUTBOUND_EMAIL",
                    "RTM_ENABLE_STRIPE",
                    "RTM_ENABLE_FINAL_PAYMENTS",
                )
            )
        except ValueError as exc:
            raise ConnectSupervisorRuntimeMisconfigured(str(exc)) from exc
        if real_data_enabled:
            raise ConnectSupervisorRuntimeMisconfigured(
                "RTM CONNECT C5 prohibe datos reales en esta fase"
            )
        if outbound_enabled:
            raise ConnectSupervisorRuntimeMisconfigured(
                "RTM CONNECT C5 requiere efectos externos desactivados"
            )
        if not auth.enabled or not auth.available:
            raise ConnectSupervisorRuntimeMisconfigured(
                "RTM CONNECT C5 requiere autenticacion individual activa"
            )
    return config


def session_has_connect_supervisor_permission(session) -> bool:
    permissions = {
        str(value)
        for value in getattr(session, "permissions", ())
    }
    return CONNECT_SUPERVISOR_PERMISSION in permissions


__all__ = [
    "RTM_CONNECT_C5_SUPERVISOR_POLICY_VERSION",
    "CONNECT_SUPERVISOR_PERMISSION",
    "ConnectSupervisorStagingBoundary",
    "ConnectSupervisorRoutesDisabled",
    "ConnectSupervisorRuntimeConfig",
    "ConnectSupervisorRuntimeMisconfigured",
    "assert_connect_supervisor_database_identity",
    "assert_connect_supervisor_staging_boundary",
    "load_connect_supervisor_runtime_config",
    "session_has_connect_supervisor_permission",
]
