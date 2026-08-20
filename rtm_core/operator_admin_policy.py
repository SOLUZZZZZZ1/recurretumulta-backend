"""Política de activación y autorización del panel supervisor RTM.

La primera versión queda restringida a staging, exige que la autenticación
individual ya esté activa y añade una barrera independiente para no publicar
administración de operadores de forma accidental.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from rtm_core.operator_auth_request import (
    OperatorAuthRuntimeConfig,
    OperatorAuthRuntimeMisconfigured,
    load_operator_auth_runtime_config,
)


OPERATOR_ADMIN_POLICY_VERSION = "rtm_operator_admin_policy_v1_0"
SUPERVISOR_PERMISSION = "ops.supervise"

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"", "0", "false", "no", "off", "disabled"}


class OperatorAdminRoutesDisabled(RuntimeError):
    """Las rutas de administración permanecen desactivadas."""


class OperatorAdminRuntimeMisconfigured(RuntimeError):
    """La configuración no cumple las barreras mínimas de seguridad."""


@dataclass(frozen=True)
class OperatorAdminRuntimeConfig:
    environment: str
    enabled: bool
    auth: OperatorAuthRuntimeConfig

    @property
    def available(self) -> bool:
        return (
            self.environment == "staging"
            and self.enabled
            and self.auth.available
        )


def _strict_flag(value: str | None, *, default: bool = False) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ValueError("Valor booleano no reconocido")


def load_operator_admin_runtime_config(
    environ: Mapping[str, str] | None = None,
    *,
    require_enabled: bool = True,
) -> OperatorAdminRuntimeConfig:
    source = environ if environ is not None else os.environ
    environment = str(source.get("RTM_ENV") or "").strip().lower()
    try:
        enabled = _strict_flag(
            source.get("RTM_ENABLE_OPERATOR_ADMIN_V1"),
            default=False,
        )
        auth = load_operator_auth_runtime_config(
            source,
            require_enabled=False,
        )
    except (ValueError, OperatorAuthRuntimeMisconfigured) as exc:
        raise OperatorAdminRuntimeMisconfigured(str(exc)) from exc

    config = OperatorAdminRuntimeConfig(
        environment=environment,
        enabled=enabled,
        auth=auth,
    )
    if require_enabled and not enabled:
        raise OperatorAdminRoutesDisabled(
            "Administración individual desactivada"
        )
    if enabled:
        if environment != "staging":
            raise OperatorAdminRuntimeMisconfigured(
                "La primera publicación del panel supervisor solo se autoriza "
                "en staging"
            )
        if not auth.enabled or not auth.available:
            raise OperatorAdminRuntimeMisconfigured(
                "La administración requiere autenticación individual activa"
            )
    return config


def session_has_supervisor_permission(session) -> bool:
    permissions = {
        str(value)
        for value in getattr(session, "permissions", ())
    }
    return SUPERVISOR_PERMISSION in permissions


__all__ = [
    "OPERATOR_ADMIN_POLICY_VERSION",
    "SUPERVISOR_PERMISSION",
    "OperatorAdminRoutesDisabled",
    "OperatorAdminRuntimeConfig",
    "OperatorAdminRuntimeMisconfigured",
    "load_operator_admin_runtime_config",
    "session_has_supervisor_permission",
]
