"""Política del ciclo de vida y credenciales de operadores RTM.

La primera versión es exclusiva de staging, tiene una barrera independiente y
requiere que autenticación individual y panel supervisor estén ya activos.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from rtm_core.operator_admin_policy import (
    OperatorAdminRuntimeConfig,
    OperatorAdminRuntimeMisconfigured,
    load_operator_admin_runtime_config,
)


OPERATOR_LIFECYCLE_POLICY_VERSION = "rtm_operator_lifecycle_policy_v1_0"
LIFECYCLE_SUPERVISOR_PERMISSION = "ops.supervise"
ALLOWED_ROLE_CODES = ("rtm.operator", "rtm.supervisor")

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"", "0", "false", "no", "off", "disabled"}


class OperatorLifecycleRoutesDisabled(RuntimeError):
    """Las rutas de ciclo de vida permanecen desactivadas."""


class OperatorLifecycleRuntimeMisconfigured(RuntimeError):
    """La configuración no cumple las barreras mínimas de seguridad."""


@dataclass(frozen=True)
class OperatorLifecycleRuntimeConfig:
    environment: str
    enabled: bool
    admin: OperatorAdminRuntimeConfig

    @property
    def available(self) -> bool:
        return (
            self.environment == "staging"
            and self.enabled
            and self.admin.available
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


def load_operator_lifecycle_runtime_config(
    environ: Mapping[str, str] | None = None,
    *,
    require_enabled: bool = True,
) -> OperatorLifecycleRuntimeConfig:
    source = environ if environ is not None else os.environ
    environment = str(source.get("RTM_ENV") or "").strip().lower()
    try:
        enabled = _strict_flag(
            source.get("RTM_ENABLE_OPERATOR_LIFECYCLE_V1"),
            default=False,
        )
        admin = load_operator_admin_runtime_config(
            source,
            require_enabled=False,
        )
    except (ValueError, OperatorAdminRuntimeMisconfigured) as exc:
        raise OperatorLifecycleRuntimeMisconfigured(str(exc)) from exc

    config = OperatorLifecycleRuntimeConfig(
        environment=environment,
        enabled=enabled,
        admin=admin,
    )
    if require_enabled and not enabled:
        raise OperatorLifecycleRoutesDisabled(
            "Ciclo de vida de operadores desactivado"
        )
    if enabled:
        if environment != "staging":
            raise OperatorLifecycleRuntimeMisconfigured(
                "La primera publicación del ciclo de vida solo se autoriza "
                "en staging"
            )
        if not admin.enabled or not admin.available:
            raise OperatorLifecycleRuntimeMisconfigured(
                "El ciclo de vida requiere el panel supervisor activo"
            )
    return config


def session_has_lifecycle_permission(session) -> bool:
    permissions = {
        str(value)
        for value in getattr(session, "permissions", ())
    }
    return LIFECYCLE_SUPERVISOR_PERMISSION in permissions


__all__ = [
    "ALLOWED_ROLE_CODES",
    "LIFECYCLE_SUPERVISOR_PERMISSION",
    "OPERATOR_LIFECYCLE_POLICY_VERSION",
    "OperatorLifecycleRoutesDisabled",
    "OperatorLifecycleRuntimeConfig",
    "OperatorLifecycleRuntimeMisconfigured",
    "load_operator_lifecycle_runtime_config",
    "session_has_lifecycle_permission",
]
