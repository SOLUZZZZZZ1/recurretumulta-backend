"""Interruptores de seguridad para efectos externos del runtime RTM.

El contrato de entorno valida la configuración antes de desplegar. Este módulo
aplica la segunda barrera dentro del proceso: una capacidad sensible no puede
usarse en staging o producción si su interruptor explícito no está activo.

En instalaciones legacy donde ``RTM_ENV`` todavía no está configurado, una
capacidad sin flag conserva temporalmente el comportamiento anterior. En cuanto
se arranca mediante el preflight de staging/producción, todas las capacidades
pasan a ser opt-in y fallan cerradas.
"""

from __future__ import annotations

import os
import re
from typing import Literal, Mapping, Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict


RUNTIME_CAPABILITIES_VERSION = "rtm_runtime_capabilities_v1_0"

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_ENFORCED_ENVIRONMENTS = {"staging", "production"}

_CAPABILITY_FLAGS = {
    "b2": "RTM_ENABLE_B2",
    "stripe": "RTM_ENABLE_STRIPE",
    "final_payments": "RTM_ENABLE_FINAL_PAYMENTS",
    "document_provider": "RTM_ENABLE_DOCUMENT_PROVIDER",
    "outbound_email": "RTM_ENABLE_OUTBOUND_EMAIL",
    "external_submission": "RTM_ENABLE_EXTERNAL_SUBMISSION",
}

_CAPABILITY_ALIASES = {
    "storage": "b2",
    "object_storage": "b2",
    "payments": "stripe",
    "payment": "stripe",
    "openai_document": "document_provider",
    "document_extraction": "document_provider",
    "email": "outbound_email",
    "mail": "outbound_email",
    "submission": "external_submission",
    "submit": "external_submission",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuntimeCapabilityState(_StrictModel):
    authority: Literal["rtm_runtime_capabilities"] = "rtm_runtime_capabilities"
    version: str = RUNTIME_CAPABILITIES_VERSION
    capability: str
    env_var: str
    environment: str
    enforced: bool
    configured: bool
    valid: bool
    enabled: bool
    reason: str


class CapabilityDisabledError(RuntimeError):
    def __init__(self, state: RuntimeCapabilityState) -> None:
        self.state = state
        super().__init__(
            f"Capacidad RTM desactivada: {state.capability} "
            f"({state.env_var}, entorno={state.environment or 'legacy'})"
        )


def _value(environ: Mapping[str, str], name: str) -> str:
    return str(environ.get(name) or "").strip()


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def canonical_capability(value: str) -> str:
    candidate = _normalise(value)
    candidate = _CAPABILITY_ALIASES.get(candidate, candidate)
    if candidate not in _CAPABILITY_FLAGS:
        raise ValueError(f"Capacidad RTM no registrada: {value}")
    return candidate


def _parse_bool(raw: str) -> tuple[bool, bool]:
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True, True
    if value in _FALSE_VALUES:
        return False, True
    return False, False


def capability_state(
    capability: str,
    environ: Optional[Mapping[str, str]] = None,
) -> RuntimeCapabilityState:
    source: Mapping[str, str] = environ if environ is not None else os.environ
    canonical = canonical_capability(capability)
    env_var = _CAPABILITY_FLAGS[canonical]
    environment = _value(source, "RTM_ENV").lower()
    raw = _value(source, env_var)
    configured = bool(raw)
    parsed, valid = _parse_bool(raw) if configured else (False, True)

    force_raw = _value(source, "RTM_ENFORCE_CAPABILITY_FLAGS")
    forced, force_valid = _parse_bool(force_raw) if force_raw else (False, True)
    enforced = environment in _ENFORCED_ENVIRONMENTS or (force_valid and forced)

    if not configured and not enforced:
        enabled = True
        reason = "legacy_unconfigured_compatibility"
    elif not configured and enforced:
        enabled = False
        reason = "required_flag_missing"
    elif not valid:
        enabled = False
        reason = "invalid_boolean_flag"
    elif parsed:
        enabled = True
        reason = "explicitly_enabled"
    else:
        enabled = False
        reason = "explicitly_disabled"

    return RuntimeCapabilityState(
        capability=canonical,
        env_var=env_var,
        environment=environment or "legacy",
        enforced=enforced,
        configured=configured,
        valid=valid,
        enabled=enabled,
        reason=reason,
    )


def capability_snapshot(
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, RuntimeCapabilityState]:
    return {
        name: capability_state(name, environ)
        for name in sorted(_CAPABILITY_FLAGS)
    }


def require_capability(
    capability: str,
    environ: Optional[Mapping[str, str]] = None,
) -> RuntimeCapabilityState:
    state = capability_state(capability, environ)
    if not state.enabled:
        raise CapabilityDisabledError(state)
    return state


def require_http_capability(
    capability: str,
    environ: Optional[Mapping[str, str]] = None,
) -> RuntimeCapabilityState:
    try:
        return require_capability(capability, environ)
    except CapabilityDisabledError as exc:
        state = exc.state
        raise HTTPException(
            status_code=503,
            detail={
                "message": "La capacidad externa solicitada no está habilitada en este entorno.",
                "capability": state.capability,
                "environment": state.environment,
                "required_flag": state.env_var,
                "reason": state.reason,
            },
        ) from exc
