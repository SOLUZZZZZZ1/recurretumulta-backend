"""Alcance de expedientes para la transición de OPS a sesión individual.

La autenticación sigue perteneciendo al puente de sesión individual. Este
módulo consume únicamente el contexto confiable que dicho puente deja en
``request.state`` y aplica la frontera de datos:

* un supervisor real, con ``ops.supervise``, puede ver el conjunto completo;
* un operador solo puede acceder a expedientes sintéticos que tenga asignados
  de forma directa, activa y aceptada dentro de un tenant A1-S, binding y
  membership sintéticos que continúen activos;
* cualquier otro rol queda fuera del espacio general de OPS.

Fuera de staging se conserva el contrato legacy mientras esta migración no se
publique en producción. En staging, la ausencia de contexto falla cerrada.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import text

from database import get_engine
from rtm_core.operator_auth_request import (
    OPERATOR_AUTH_MODE_FAIL_CLOSED,
    OPERATOR_AUTH_MODE_LEGACY,
    operator_auth_environment_mode,
)


OPS_CASE_SCOPE_VERSION = "rtm_ops_case_scope_v1_0"
OPS_VIEW_PERMISSION = "ops.view"
OPS_SUPERVISE_PERMISSION = "ops.supervise"
OPS_OPERATOR_ROLE = "rtm.operator"
OPS_SUPERVISOR_ROLE = "rtm.supervisor"

_LEGACY_OPERATOR_ID = "00000000-0000-4000-8000-000000000000"
_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}

# Se usa siempre con el alias fijo ``c``. EXISTS evita duplicar expedientes si
# un operador conserva más de un rol operativo sobre el mismo caso. La cadena
# A1-S completa forma parte de la autorización: revocar tenant, binding o
# membership retira también el expediente de todas las listas OPS.
OPS_CASE_SCOPE_SQL = """
(
    :rtm_ops_scope_all = TRUE
    OR (
        COALESCE(c.test_mode, FALSE) = TRUE
        AND EXISTS (
            SELECT 1
            FROM rtm_work_assignments rtm_ops_assignment
            JOIN rtm_connect_a1s_case_bindings rtm_ops_binding
              ON rtm_ops_binding.case_id = rtm_ops_assignment.case_id
             AND rtm_ops_binding.status = 'active'
             AND rtm_ops_binding.synthetic_only = TRUE
             AND rtm_ops_binding.revoked_at IS NULL
             AND rtm_ops_binding.metadata @>
                 '{"synthetic_marker":"RTM_A1S_SYNTHETIC_ONLY",\
                   "synthetic_only":true,"test_mode":true}'::jsonb
            JOIN rtm_connect_a1s_tenants rtm_ops_tenant
              ON rtm_ops_tenant.id = rtm_ops_binding.tenant_id
             AND rtm_ops_tenant.status = 'active'
             AND rtm_ops_tenant.synthetic_only = TRUE
             AND rtm_ops_tenant.metadata @>
                 '{"synthetic_marker":"RTM_A1S_SYNTHETIC_ONLY",\
                   "synthetic_only":true}'::jsonb
            JOIN rtm_connect_a1s_memberships rtm_ops_membership
              ON rtm_ops_membership.tenant_id = rtm_ops_binding.tenant_id
             AND rtm_ops_membership.operator_id =
                 CAST(:rtm_ops_operator_id AS UUID)
             AND rtm_ops_membership.status = 'active'
             AND rtm_ops_membership.synthetic_only = TRUE
             AND rtm_ops_membership.revoked_at IS NULL
             AND rtm_ops_membership.metadata @>
                 '{"synthetic_marker":"RTM_A1S_SYNTHETIC_ONLY",\
                   "synthetic_only":true}'::jsonb
            WHERE rtm_ops_assignment.case_id = c.id
              AND rtm_ops_assignment.attention_item_id IS NULL
              AND rtm_ops_assignment.operator_id =
                  CAST(:rtm_ops_operator_id AS UUID)
              AND rtm_ops_assignment.status = 'active'
              AND rtm_ops_assignment.accepted_at IS NOT NULL
              AND rtm_ops_assignment.released_at IS NULL
              AND rtm_ops_assignment.assignment_role IN (
                  'responsible', 'reviewer', 'supervisor'
              )
              AND rtm_ops_assignment.metadata @>
                  '{"synthetic_marker":"RTM_PRESENTER_SYNTHETIC_ONLY",\
                    "synthetic_only":true}'::jsonb
        )
    )
)
"""


@dataclass(frozen=True)
class OpsCaseScope:
    operator_id: str
    role_code: str
    permissions: tuple[str, ...]
    scope_all: bool
    individual_session: bool


def _permission_tuple(value: Any) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(permission).strip()
                for permission in (value or ())
                if str(permission).strip()
            }
        )
    )


def load_ops_case_scope(request: Request) -> OpsCaseScope:
    """Construye el alcance desde identidad de servidor, nunca desde headers."""

    environment_mode = operator_auth_environment_mode()
    if environment_mode == OPERATOR_AUTH_MODE_FAIL_CLOSED:
        raise HTTPException(
            status_code=503,
            detail="Autenticación individual no disponible",
            headers=_NO_STORE_HEADERS,
        )
    if environment_mode == OPERATOR_AUTH_MODE_LEGACY:
        return OpsCaseScope(
            operator_id=_LEGACY_OPERATOR_ID,
            role_code="legacy.operator",
            permissions=(OPS_VIEW_PERMISSION,),
            scope_all=True,
            individual_session=False,
        )

    context = getattr(request.state, "rtm_operator_context", None)
    if context is None:
        raise HTTPException(
            status_code=401,
            detail="Sesión individual requerida",
            headers=_NO_STORE_HEADERS,
        )

    try:
        operator_id = str(uuid.UUID(str(context.operator_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=401,
            detail="Sesión individual no válida",
            headers=_NO_STORE_HEADERS,
        ) from exc

    role_code = str(getattr(context, "role_code", None) or "").strip()
    permissions = _permission_tuple(getattr(context, "permissions", ()))
    permission_set = set(permissions)

    if OPS_VIEW_PERMISSION not in permission_set:
        raise HTTPException(
            status_code=403,
            detail="Permiso OPS requerido",
            headers=_NO_STORE_HEADERS,
        )

    if role_code == OPS_SUPERVISOR_ROLE:
        if OPS_SUPERVISE_PERMISSION not in permission_set:
            raise HTTPException(
                status_code=403,
                detail="Permiso de supervisor requerido",
                headers=_NO_STORE_HEADERS,
            )
        scope_all = True
    elif role_code == OPS_OPERATOR_ROLE:
        scope_all = False
    else:
        # En particular, rtm.signer conserva ops.view para su cola de firma,
        # pero no obtiene por ello acceso al espacio general de expedientes.
        raise HTTPException(
            status_code=403,
            detail="Rol de trabajo OPS requerido",
            headers=_NO_STORE_HEADERS,
        )

    return OpsCaseScope(
        operator_id=operator_id,
        role_code=role_code,
        permissions=permissions,
        scope_all=scope_all,
        individual_session=True,
    )


def ops_case_scope_params(scope: OpsCaseScope) -> dict[str, Any]:
    return {
        "rtm_ops_scope_all": bool(scope.scope_all),
        "rtm_ops_operator_id": scope.operator_id,
    }


def ops_case_scope_filter(
    scope: OpsCaseScope,
) -> tuple[str, dict[str, Any]]:
    """Selecciona SQL de scope sin acoplar el contrato legacy al esquema RTM."""

    if not scope.individual_session:
        # Producción y otros entornos legacy pueden no tener todavía las tablas
        # de gestión. Incluso una rama muerta de OR debe poder parsearse allí.
        return "TRUE", {}
    return OPS_CASE_SCOPE_SQL, ops_case_scope_params(scope)


def require_case_in_scope(
    conn: Any,
    *,
    scope: OpsCaseScope,
    case_id: str,
) -> str:
    """Exige existencia y alcance sin permitir enumerar UUID ajenos."""

    if not scope.individual_session:
        # Conserva literalmente el identificador y el orden de validación del
        # endpoint legacy. No consulta gestión antes de validar su token.
        return case_id

    try:
        normalized_case_id = str(uuid.UUID(str(case_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=404,
            detail="Expediente no encontrado",
            headers=_NO_STORE_HEADERS,
        ) from exc

    if scope.scope_all:
        statement = text(
            """
            SELECT c.id
            FROM cases c
            WHERE c.id = CAST(:rtm_ops_case_id AS UUID)
            FOR UPDATE OF c
            """
        )
        parameters = {"rtm_ops_case_id": normalized_case_id}
    else:
        statement = text(
            """
            SELECT c.id
            FROM cases c
            JOIN rtm_connect_a1s_case_bindings rtm_ops_binding
              ON rtm_ops_binding.case_id = c.id
             AND rtm_ops_binding.status = 'active'
             AND rtm_ops_binding.synthetic_only = TRUE
             AND rtm_ops_binding.revoked_at IS NULL
             AND rtm_ops_binding.metadata @>
                 '{"synthetic_marker":"RTM_A1S_SYNTHETIC_ONLY",\
                   "synthetic_only":true,"test_mode":true}'::jsonb
            JOIN rtm_connect_a1s_tenants rtm_ops_tenant
              ON rtm_ops_tenant.id = rtm_ops_binding.tenant_id
             AND rtm_ops_tenant.status = 'active'
             AND rtm_ops_tenant.synthetic_only = TRUE
             AND rtm_ops_tenant.metadata @>
                 '{"synthetic_marker":"RTM_A1S_SYNTHETIC_ONLY",\
                   "synthetic_only":true}'::jsonb
            JOIN rtm_connect_a1s_memberships rtm_ops_membership
              ON rtm_ops_membership.tenant_id = rtm_ops_binding.tenant_id
             AND rtm_ops_membership.operator_id =
                 CAST(:rtm_ops_operator_id AS UUID)
             AND rtm_ops_membership.status = 'active'
             AND rtm_ops_membership.synthetic_only = TRUE
             AND rtm_ops_membership.revoked_at IS NULL
             AND rtm_ops_membership.metadata @>
                 '{"synthetic_marker":"RTM_A1S_SYNTHETIC_ONLY",\
                   "synthetic_only":true}'::jsonb
            JOIN rtm_work_assignments rtm_ops_assignment
              ON rtm_ops_assignment.case_id = c.id
             AND rtm_ops_assignment.attention_item_id IS NULL
             AND rtm_ops_assignment.operator_id =
                 CAST(:rtm_ops_operator_id AS UUID)
             AND rtm_ops_assignment.status = 'active'
             AND rtm_ops_assignment.accepted_at IS NOT NULL
             AND rtm_ops_assignment.released_at IS NULL
             AND rtm_ops_assignment.assignment_role IN (
                 'responsible', 'reviewer', 'supervisor'
             )
             AND rtm_ops_assignment.metadata @>
                 '{"synthetic_marker":"RTM_PRESENTER_SYNTHETIC_ONLY",\
                   "synthetic_only":true}'::jsonb
            WHERE c.id = CAST(:rtm_ops_case_id AS UUID)
              AND COALESCE(c.test_mode, FALSE) = TRUE
            ORDER BY rtm_ops_assignment.assigned_at DESC,
                     rtm_ops_assignment.id DESC
            LIMIT 1
            FOR UPDATE OF c, rtm_ops_binding, rtm_ops_tenant,
                          rtm_ops_membership, rtm_ops_assignment
            """
        )
        parameters = {
            "rtm_ops_case_id": normalized_case_id,
            "rtm_ops_operator_id": scope.operator_id,
        }

    allowed = conn.execute(statement, parameters).fetchone()
    if allowed is None:
        raise HTTPException(
            status_code=404,
            detail="Expediente no encontrado",
            headers=_NO_STORE_HEADERS,
        )
    return normalized_case_id


def require_current_case_scope(
    request: Request,
    case_id: str,
) -> OpsCaseScope:
    """Dependencia FastAPI para routers cuyos paths contienen ``case_id``."""

    scope = load_ops_case_scope(request)
    if not scope.individual_session:
        return scope
    with get_engine().begin() as conn:
        require_case_in_scope(conn, scope=scope, case_id=case_id)
    return scope


__all__ = [
    "OPS_CASE_SCOPE_SQL",
    "OPS_CASE_SCOPE_VERSION",
    "OpsCaseScope",
    "load_ops_case_scope",
    "ops_case_scope_filter",
    "ops_case_scope_params",
    "require_case_in_scope",
    "require_current_case_scope",
]
