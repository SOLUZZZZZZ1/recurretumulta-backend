"""Puente temporal entre las sesiones individuales y los routers OPS legacy.

Los routers históricos todavía esperan ``X-Operator-Token``. En staging este
middleware hace que ese secreto deje de ser una credencial de cliente:

* valida ``Bearer`` + posesión del dispositivo contra la sesión individual;
* exige un rol operativo, el permiso ``ops.view`` y una contraseña temporal
  ya sustituida;
* reserva ``/ops/automation`` a ``ops.supervise`` y retira el endpoint IA
  legacy sin control de expediente;
* elimina cualquier identidad declarada por el cliente e inyecta, solo en la
  petición interna, el secreto legacy y el actor derivado de la sesión.

El contexto sin secretos también queda en ``request.state`` para que la
posterior migración de auditoría y asignaciones no tenga que confiar en
cabeceras aportadas por el navegador.

El puente es deliberadamente exclusivo de staging. El contrato legacy solo se
conserva fuera de staging cuando la función individual no está solicitada y la
identidad técnica tampoco sigue marcada como staging. Una deriva entre esas
señales falla cerrada en vez de reabrir el secreto compartido.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from database import get_engine
from rtm_core.operator_auth_request import (
    OPERATOR_AUTH_MODE_FAIL_CLOSED,
    OPERATOR_AUTH_MODE_LEGACY,
    OperatorAuthRoutesDisabled,
    OperatorAuthRuntimeMisconfigured,
    load_operator_auth_runtime_config,
    operator_auth_environment_mode,
)
from rtm_core.operator_auth_router import (
    load_operator_session_with_device_possession,
)


LEGACY_OPS_SESSION_BRIDGE_VERSION = "rtm_legacy_ops_session_bridge_v1_2"
OPS_VIEW_PERMISSION = "ops.view"
OPS_SUPERVISE_PERMISSION = "ops.supervise"
OPS_GENERAL_ROLE_CODES = frozenset({"rtm.operator", "rtm.supervisor"})
OPS_SUPERVISOR_ROLE = "rtm.supervisor"

_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "Vary": "Authorization, Cookie, X-Operator-Token, X-RTM-Device",
}
_INDIVIDUAL_AUTH_HEADERS = {
    "WWW-Authenticate": "Bearer",
    **_NO_STORE_HEADERS,
}

# Estas superficies completas ya tienen autenticación y autorización propias.
# El orden y la comprobación por frontera evitan que, por ejemplo,
# /ops/authentic-* quede excluida accidentalmente. ``/ops/admin`` no pertenece
# aquí: mezcla los routers de operadores con endpoints legacy y se permite más
# abajo únicamente mediante patrones de ruta explícitos.
_OWN_CONTROL_PREFIXES = (
    "/ops/auth",
    "/ops/presenter",
    "/ops/connect",
    "/ops/restaurant-reservations",
    "/ops/restaurants",
)
_AUTOMATION_PREFIX = "/ops/automation"
_LEGACY_LOGIN_PATH = "/ops/login"
_LEGACY_AI_PATH = "/ai/expediente/run"
_VEHICLE_REMOVAL_PREFIX = "/ops/vehicle-removal"
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class LegacyOpsOperatorContext:
    """Identidad mínima confiable, sin token bearer ni secreto de dispositivo."""

    operator_id: str
    session_id: str
    role_code: str | None
    permissions: tuple[str, ...]
    actor: str


def _is_path_or_child(path: str, prefix: str) -> bool:
    normalized = path.rstrip("/") or "/"
    return normalized == prefix or normalized.startswith(prefix + "/")


def _is_canonical_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.casefold()
    except (TypeError, ValueError, AttributeError):
        return False


def _is_operator_admin_own_control_path(path: str) -> bool:
    """Allowlist cerrada de rutas admin con gate individual propio."""

    normalized = path.rstrip("/") or "/"
    if normalized in {
        "/ops/admin/status",
        "/ops/admin/lifecycle/status",
        "/ops/admin/operators",
    }:
        return True

    segments = normalized.split("/")
    if segments[1:3] != ["ops", "admin"]:
        return False

    # Rutas de consulta y ciclo de vida de un operador concreto.
    if (
        len(segments) >= 5
        and segments[3] == "operators"
        and _is_canonical_uuid(segments[4])
    ):
        suffix = segments[5:]
        return suffix in (
            [],
            ["sessions"],
            ["devices"],
            ["access-events"],
            ["suspend"],
            ["reactivate"],
            ["role"],
            ["credentials", "rotate"],
            ["sessions", "revoke-all"],
        )

    # Revocaciones supervisoras por identificador de sesión o dispositivo.
    return (
        len(segments) == 6
        and segments[3] in {"sessions", "devices"}
        and _is_canonical_uuid(segments[4])
        and segments[5] == "revoke"
    )


def is_legacy_ops_path(path: str) -> bool:
    """Identifica OPS legacy sin capturar módulos con controles propios."""

    if _is_path_or_child(path, _LEGACY_AI_PATH):
        return True
    if not _is_path_or_child(path, "/ops"):
        return False
    if any(
        _is_path_or_child(path, prefix)
        for prefix in _OWN_CONTROL_PREFIXES
    ):
        return False
    return not _is_operator_admin_own_control_path(path)


def legacy_ops_requires_supervisor(path: str) -> bool:
    return _is_path_or_child(path, _AUTOMATION_PREFIX)


def is_retired_vehicle_mark_paid(path: str, method: str) -> bool:
    """Detecta el mutador retirado para cualquier segmento de expediente.

    El router heredado declara ``case_id`` como texto y PostgreSQL puede aceptar
    representaciones UUID no canónicas. El cierre debe ocurrir antes de que el
    valor llegue al router, incluso si no cumple nuestro formato canónico.
    """

    if str(method or "").strip().upper() != "POST":
        return False
    segments = (path.rstrip("/") or "/").split("/")
    if len(segments) != 5 or segments[1:3] != ["ops", "vehicle-removal"]:
        return False
    return bool(segments[3]) and segments[4] == "mark-paid"


def is_scoped_operator_read_path(path: str) -> bool:
    """Allowlist exacta de lecturas que ya aplican scope en su transacción."""

    normalized = path.rstrip("/") or "/"
    if normalized in {
        "/ops/queue",
        "/ops/queue-smart",
        "/ops/followups",
        "/ops/followups/due",
        _VEHICLE_REMOVAL_PREFIX,
    }:
        return True

    segments = normalized.split("/")
    if (
        len(segments) == 4
        and segments[1:3] == ["ops", "vehicle-removal"]
    ):
        return _is_canonical_uuid(segments[3])

    if (
        len(segments) == 6
        and segments[1:4] == ["ops", "core", "cases"]
        and _is_canonical_uuid(segments[4])
    ):
        return segments[5] in {"workspace", "payment-status"}

    if (
        len(segments) not in {4, 5}
        or segments[1:3] != ["ops", "cases"]
        or not _is_canonical_uuid(segments[3])
    ):
        return False
    if len(segments) == 4:
        return True
    return segments[4] in {
        "documents",
        "events",
        "followups",
        "ai-overrides",
    }


def _json_error(
    status_code: int,
    detail: str,
    *,
    bearer_challenge: bool = False,
) -> JSONResponse:
    headers = (
        _INDIVIDUAL_AUTH_HEADERS
        if bearer_challenge
        else _NO_STORE_HEADERS
    )
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
        headers=headers,
    )


def _replace_internal_legacy_headers(
    request: Request,
    *,
    legacy_token: str,
    actor: str,
) -> None:
    """Sustituye cabeceras sensibles sin conservar valores del cliente."""

    blocked = {b"x-operator-token", b"x-operator-actor"}
    headers = [
        (name, value)
        for name, value in request.scope.get("headers", [])
        if name.lower() not in blocked
    ]
    headers.extend(
        (
            (b"x-operator-token", legacy_token.encode("utf-8")),
            (b"x-operator-actor", actor.encode("ascii")),
        )
    )
    request.scope["headers"] = headers
    # Starlette puede haber materializado Headers antes de la sustitución.
    if hasattr(request, "_headers"):
        del request._headers


async def legacy_ops_individual_session_bridge(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Autoriza OPS legacy mediante la sesión individual existente."""

    path = request.url.path
    if not is_legacy_ops_path(path) or request.method == "OPTIONS":
        return await call_next(request)

    environment_mode = operator_auth_environment_mode()
    if environment_mode == OPERATOR_AUTH_MODE_LEGACY:
        return await call_next(request)
    if environment_mode == OPERATOR_AUTH_MODE_FAIL_CLOSED:
        return _json_error(
            503,
            "Autenticación individual no disponible",
        )

    if _is_path_or_child(path, _LEGACY_LOGIN_PATH):
        return _json_error(410, "Acceso individual requerido")
    if _is_path_or_child(path, _LEGACY_AI_PATH):
        return _json_error(
            410,
            "Análisis legacy retirado; utilice el flujo RTM CORE",
        )
    if is_retired_vehicle_mark_paid(path, request.method):
        return _json_error(
            410,
            "Marcado manual de pago retirado",
        )

    # Ningún cliente puede seguir presentando el secreto compartido, aunque
    # también aporte una sesión válida.
    if request.headers.get("X-Operator-Token") is not None:
        return _json_error(
            401,
            "Autenticación individual requerida",
            bearer_challenge=True,
        )

    try:
        load_operator_auth_runtime_config(require_enabled=True)
    except OperatorAuthRoutesDisabled:
        return _json_error(
            503,
            "Autenticación individual no disponible",
        )
    except OperatorAuthRuntimeMisconfigured:
        return _json_error(
            503,
            "Autenticación individual no disponible",
        )

    authorization = request.headers.get("Authorization")
    device_header = request.headers.get("X-RTM-Device")
    device_cookie = request.cookies.get("rtm_presenter_device")
    try:
        engine = get_engine()
        with engine.begin() as conn:
            session = load_operator_session_with_device_possession(
                conn,
                authorization=authorization,
                x_rtm_device=device_header,
                rtm_presenter_device=device_cookie,
                touch=True,
            )
    except Exception:
        # Una avería de almacenamiento nunca reabre el acceso por token legacy.
        return _json_error(
            503,
            "Autenticación individual no disponible",
        )

    if not session:
        return _json_error(
            401,
            "Sesión no válida",
            bearer_challenge=True,
        )

    permissions = tuple(
        sorted(
            {
                str(permission).strip()
                for permission in session.permissions
                if str(permission).strip()
            }
        )
    )
    if OPS_VIEW_PERMISSION not in permissions:
        return _json_error(403, "Permiso OPS requerido")
    role_code = str(session.role_code or "")
    if role_code not in OPS_GENERAL_ROLE_CODES:
        return _json_error(403, "Rol de operador OPS requerido")
    if bool(session.must_change_password):
        return _json_error(
            409,
            "Debe cambiar la contraseña temporal antes de acceder a OPS",
        )
    if bool(session.mfa_required):
        return _json_error(
            409,
            "La cuenta requiere una fase de seguridad no disponible",
        )
    actor = f"operator:{session.operator_id}"
    context = LegacyOpsOperatorContext(
        operator_id=str(session.operator_id),
        session_id=str(session.session_id),
        role_code=role_code,
        permissions=permissions,
        actor=actor,
    )
    request.state.rtm_operator_context = context
    request.state.rtm_operator_id = context.operator_id
    request.state.rtm_operator_session_id = context.session_id
    request.state.rtm_operator_permissions = context.permissions

    is_supervisor = (
        role_code == OPS_SUPERVISOR_ROLE
        and OPS_SUPERVISE_PERMISSION in permissions
    )
    if legacy_ops_requires_supervisor(path) and not is_supervisor:
        return _json_error(403, "Permiso de supervisor requerido")
    if request.method.upper() in _MUTATING_METHODS and not is_supervisor:
        return _json_error(
            403,
            "Operación reservada a supervisión durante la migración",
        )
    if not is_supervisor and not is_scoped_operator_read_path(path):
        return _json_error(403, "Superficie OPS aún no migrada")

    legacy_token = str(os.getenv("OPERATOR_TOKEN") or "").strip()
    if not legacy_token:
        return _json_error(503, "OPS no disponible")

    _replace_internal_legacy_headers(
        request,
        legacy_token=legacy_token,
        actor=actor,
    )
    return await call_next(request)


__all__ = [
    "LEGACY_OPS_SESSION_BRIDGE_VERSION",
    "LegacyOpsOperatorContext",
    "OPS_GENERAL_ROLE_CODES",
    "OPS_SUPERVISE_PERMISSION",
    "OPS_SUPERVISOR_ROLE",
    "OPS_VIEW_PERMISSION",
    "is_legacy_ops_path",
    "is_retired_vehicle_mark_paid",
    "is_scoped_operator_read_path",
    "legacy_ops_individual_session_bridge",
    "legacy_ops_requires_supervisor",
]
