"""Provisión controlada de operadores sintéticos RTM en staging.

Esta unidad no publica endpoints ni activa el login individual. Crea únicamente
roles mínimos y una cuenta interna inequívocamente sintética, con contraseña
Argon2id y sin conservar nunca la contraseña en claro.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import text

from rtm_core.operator_auth_crypto import (
    hash_operator_password,
    normalize_operator_email,
    validate_operator_password,
)


OPERATOR_PROVISIONING_VERSION = "rtm_operator_provisioning_v1_2"
DEFAULT_SYNTHETIC_EMAIL = "rtm-staging-supervisor@example.com"
DEFAULT_SYNTHETIC_DISPLAY_NAME = "RTM STAGING SUPERVISOR"

_ALLOWED_SYNTHETIC_DOMAINS = {
    "example.com",
    "example.net",
    "example.org",
}


@dataclass(frozen=True)
class OperatorRoleDefinition:
    key: str
    code: str
    name: str
    description: str
    permissions: tuple[str, ...]


ROLE_DEFINITIONS: dict[str, OperatorRoleDefinition] = {
    "operator": OperatorRoleDefinition(
        key="operator",
        code="rtm.operator",
        name="Operador RTM",
        description=(
            "Rol mínimo de trabajo operativo. La autorización fina se "
            "incorporará en una fase posterior."
        ),
        permissions=(
            "ops.view",
            "presenter.documents.ingest",
            "presenter.documents.read",
            "presenter.package.freeze",
        ),
    ),
    "supervisor": OperatorRoleDefinition(
        key="supervisor",
        code="rtm.supervisor",
        name="Supervisor RTM",
        description=(
            "Rol mínimo de supervisión en staging. La autorización fina se "
            "incorporará en una fase posterior."
        ),
        permissions=(
            "ops.view",
            "ops.supervise",
            "presenter.documents.ingest",
            "presenter.documents.read",
            "presenter.package.freeze",
        ),
    ),
}


@dataclass(frozen=True)
class ProvisionedOperator:
    operator_id: str
    email: str
    display_name: str
    role_code: str
    created: bool
    password_issued: bool


def normalize_synthetic_operator_email(value: str) -> str:
    normalized = normalize_operator_email(value)
    local, _, domain = normalized.partition("@")
    if (
        not local.startswith("rtm-staging-")
        or domain not in _ALLOWED_SYNTHETIC_DOMAINS
    ):
        raise ValueError(
            "La cuenta de esta fase debe usar rtm-staging-*@example.com/.net/.org"
        )
    return normalized


def role_definition(key: str) -> OperatorRoleDefinition:
    normalized = str(key or "").strip().lower()
    if normalized not in ROLE_DEFINITIONS:
        raise ValueError("Rol no reconocido")
    return ROLE_DEFINITIONS[normalized]


def generate_temporary_password() -> str:
    # Se añade variedad explícita sin imponer reglas de composición al usuario.
    value = f"RTM-{secrets.token_urlsafe(24)}-7a!"
    return validate_operator_password(value)


def _profile_is_synthetic(value: Any) -> bool:
    return isinstance(value, Mapping) and value.get("synthetic") is True


def count_non_synthetic_operators(conn) -> int:
    return int(
        conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM rtm_operators
                WHERE NOT COALESCE(
                    profile @> '{"synthetic": true}'::JSONB,
                    FALSE
                )
                """
            )
        ).scalar_one()
    )


def _lock_and_require_synthetic_only_operator_population(conn) -> None:
    # Los roles rtm.operator/rtm.supervisor son globales. El bloqueo de tabla
    # cierra la carrera entre comprobar la población y mutar esos roles: ningún
    # alta o cambio de operador puede aparecer hasta que termine la transacción.
    conn.execute(
        text(
            """
            LOCK TABLE rtm_operators, rtm_operator_roles
            IN SHARE ROW EXCLUSIVE MODE
            """
        )
    )
    if count_non_synthetic_operators(conn):
        raise RuntimeError(
            "La provisión sintética no puede mutar roles compartidos cuando "
            "existen operadores no sintéticos"
        )


def _invalidate_synthetic_sessions_for_roles(
    conn,
    role_ids: list[str],
) -> None:
    if not role_ids:
        return
    parameters = {
        "role_ids": json.dumps(role_ids, ensure_ascii=True),
    }
    conn.execute(
        text(
            """
            UPDATE rtm_operator_sessions AS s
            SET status='revoked',
                revoked_at=NOW(),
                close_reason='synthetic_role_permissions_changed'
            FROM rtm_operators AS o
            WHERE o.id=s.operator_id
              AND o.profile @> '{"synthetic": true}'::JSONB
              AND o.primary_role_id IN (
                  SELECT CAST(value AS UUID)
                  FROM jsonb_array_elements_text(
                      CAST(:role_ids AS JSONB)
                  ) AS changed_role(value)
              )
              AND s.status='active'
            """
        ),
        parameters,
    )
    conn.execute(
        text(
            """
            UPDATE rtm_operators
            SET auth_epoch=auth_epoch+1,
                updated_at=NOW()
            WHERE profile @> '{"synthetic": true}'::JSONB
              AND primary_role_id IN (
                  SELECT CAST(value AS UUID)
                  FROM jsonb_array_elements_text(
                      CAST(:role_ids AS JSONB)
                  ) AS changed_role(value)
              )
            """
        ),
        parameters,
    )


def ensure_minimum_roles(conn) -> dict[str, str]:
    _lock_and_require_synthetic_only_operator_population(conn)
    role_ids: dict[str, str] = {}
    changed_role_ids: list[str] = []
    for definition in ROLE_DEFINITIONS.values():
        row = conn.execute(
            text(
                """
                INSERT INTO rtm_operator_roles AS role_row(
                    id, code, name, description, permissions,
                    system_role, active, created_at, updated_at
                ) VALUES (
                    CAST(:id AS UUID), :code, :name, :description,
                    CAST(:permissions AS JSONB), TRUE, TRUE, NOW(), NOW()
                )
                ON CONFLICT (code) DO UPDATE SET
                    permissions=(
                        SELECT COALESCE(
                            jsonb_agg(
                                merged.permission ORDER BY merged.permission
                            ),
                            '[]'::JSONB
                        )
                        FROM (
                            SELECT jsonb_array_elements_text(
                                CASE
                                    WHEN jsonb_typeof(
                                        role_row.permissions
                                    )='array'
                                    THEN role_row.permissions
                                    ELSE '[]'::JSONB
                                END
                            ) AS permission
                            UNION
                            SELECT jsonb_array_elements_text(
                                EXCLUDED.permissions
                            ) AS permission
                        ) AS merged
                    ),
                    updated_at=NOW()
                WHERE role_row.active=TRUE
                  AND role_row.system_role=TRUE
                  AND jsonb_typeof(role_row.permissions)='array'
                  AND NOT role_row.permissions @> EXCLUDED.permissions
                RETURNING id
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "code": definition.code,
                "name": definition.name,
                "description": definition.description,
                "permissions": json.dumps(
                    list(definition.permissions),
                    ensure_ascii=False,
                ),
            },
        ).fetchone()
        if row is not None:
            role_id = str(row[0])
            changed_role_ids.append(role_id)
        else:
            # DO UPDATE ... WHERE no devuelve fila cuando el rol ya satisface
            # el mínimo. La lectura cerrada también rechaza roles inactivos,
            # no-sistema o con un JSON de permisos malformado en vez de
            # "repararlos" pisando estado administrativo.
            existing_role = conn.execute(
                text(
                    """
                    SELECT id
                    FROM rtm_operator_roles
                    WHERE code=:code
                      AND active=TRUE
                      AND system_role=TRUE
                      AND jsonb_typeof(permissions)='array'
                      AND permissions @> CAST(:permissions AS JSONB)
                    LIMIT 1
                    """
                ),
                {
                    "code": definition.code,
                    "permissions": json.dumps(
                        list(definition.permissions),
                        ensure_ascii=False,
                    ),
                },
            ).fetchone()
            if existing_role is None:
                raise RuntimeError(
                    f"El rol compartido {definition.code} no cumple el mínimo "
                    "y no puede repararse de forma destructiva"
                )
            role_id = str(existing_role[0])
        role_ids[definition.key] = role_id

    # Los permisos se resuelven desde el rol en cada request. Si el mínimo se
    # amplió, un bearer vivo obtendría privilegios sin reautenticarse; se
    # revocan las sesiones afectadas y se avanza su epoch en la misma tx.
    _invalidate_synthetic_sessions_for_roles(conn, changed_role_ids)
    return role_ids


def find_operator_by_email(conn, email: str):
    return conn.execute(
        text(
            """
            SELECT o.id, o.email, o.display_name, o.status, o.password_hash,
                   o.must_change_password, o.mfa_required, o.profile,
                   o.auth_epoch, o.password_version,
                   r.code AS role_code
            FROM rtm_operators o
            LEFT JOIN rtm_operator_roles r ON r.id=o.primary_role_id
            WHERE lower(btrim(o.email))=:email
            LIMIT 1
            """
        ),
        {"email": email},
    ).mappings().fetchone()


def provision_synthetic_operator(
    conn,
    *,
    email: str,
    display_name: str,
    role_key: str,
    password: str,
) -> ProvisionedOperator:
    normalized_email = normalize_synthetic_operator_email(email)
    clean_name = " ".join(str(display_name or "").split()).strip()
    if len(clean_name) < 3 or len(clean_name) > 160:
        raise ValueError("Nombre visible no válido")
    definition = role_definition(role_key)

    existing = find_operator_by_email(conn, normalized_email)
    if existing:
        if not _profile_is_synthetic(existing["profile"]):
            raise RuntimeError(
                "La dirección ya pertenece a una cuenta no sintética"
            )
        # Una cuenta sintética puede preceder a una ampliación aditiva de los
        # permisos mínimos. No se debe devolver el registro antiguo antes de
        # refrescar ambos roles y enlazar la cuenta al rol solicitado.
        role_ids = ensure_minimum_roles(conn)
        refreshed = find_operator_by_email(conn, normalized_email)
        if (
            not refreshed
            or str(refreshed["id"]) != str(existing["id"])
            or not _profile_is_synthetic(refreshed["profile"])
        ):
            raise RuntimeError(
                "La cuenta cambió durante la provisión sintética"
            )
        current_role = str(refreshed["role_code"] or "")
        if current_role != definition.code:
            if (
                current_role == "rtm.supervisor"
                and definition.code != "rtm.supervisor"
            ):
                raise RuntimeError(
                    "La provisión no puede retirar el rol supervisor; use el "
                    "ciclo de vida administrativo"
                )
            if (
                definition.code == "rtm.supervisor"
                and bool(refreshed["must_change_password"])
            ):
                raise RuntimeError(
                    "La contraseña temporal debe cambiarse antes de asignar "
                    "el rol supervisor"
                )
            conn.execute(
                text(
                    """
                    UPDATE rtm_operator_sessions
                    SET status='revoked',
                        revoked_at=NOW(),
                        close_reason='synthetic_operator_role_changed'
                    WHERE operator_id=CAST(:operator_id AS UUID)
                      AND status='active'
                    """
                ),
                {"operator_id": str(refreshed["id"])},
            )
            assignment = conn.execute(
                text(
                    """
                    UPDATE rtm_operators
                    SET primary_role_id=CAST(:role_id AS UUID),
                        auth_epoch=auth_epoch+1,
                        updated_at=NOW()
                    WHERE id=CAST(:operator_id AS UUID)
                      AND profile @> '{"synthetic": true}'::JSONB
                      AND primary_role_id IS DISTINCT FROM
                          CAST(:role_id AS UUID)
                    """
                ),
                {
                    "operator_id": str(refreshed["id"]),
                    "role_id": role_ids[definition.key],
                },
            )
            if int(assignment.rowcount or 0) != 1:
                raise RuntimeError(
                    "No se pudo actualizar de forma segura la cuenta sintética"
                )
        return ProvisionedOperator(
            operator_id=str(refreshed["id"]),
            email=str(refreshed["email"]),
            display_name=str(refreshed["display_name"]),
            role_code=definition.code,
            created=False,
            password_issued=False,
        )

    role_ids = ensure_minimum_roles(conn)
    password_hash = hash_operator_password(password)
    operator_id = str(uuid.uuid4())
    profile = {
        "synthetic": True,
        "environment": "staging",
        "purpose": "operator_auth_activation",
        "provisioning_version": OPERATOR_PROVISIONING_VERSION,
    }
    conn.execute(
        text(
            """
            INSERT INTO rtm_operators(
                id, email, display_name, password_hash, status,
                primary_role_id, must_change_password, mfa_required,
                profile, failed_login_count, last_failed_login_at,
                locked_until, password_changed_at, password_algorithm,
                password_version, auth_epoch, created_at, updated_at,
                disabled_at, disabled_by
            ) VALUES (
                CAST(:id AS UUID), :email, :display_name, :password_hash,
                'active', CAST(:role_id AS UUID), FALSE, FALSE,
                CAST(:profile AS JSONB), 0, NULL, NULL, NOW(),
                'argon2id', 1, 1, NOW(), NOW(), NULL, NULL
            )
            """
        ),
        {
            "id": operator_id,
            "email": normalized_email,
            "display_name": clean_name,
            "password_hash": password_hash,
            "role_id": role_ids[definition.key],
            "profile": json.dumps(profile, ensure_ascii=False),
        },
    )
    return ProvisionedOperator(
        operator_id=operator_id,
        email=normalized_email,
        display_name=clean_name,
        role_code=definition.code,
        created=True,
        password_issued=True,
    )


def disable_synthetic_operator(conn, *, email: str) -> dict[str, Any]:
    normalized_email = normalize_synthetic_operator_email(email)
    operator = find_operator_by_email(conn, normalized_email)
    if not operator:
        raise LookupError("Operador sintético no encontrado")
    if not _profile_is_synthetic(operator["profile"]):
        raise RuntimeError("No se puede desactivar una cuenta no sintética")

    operator_id = str(operator["id"])
    revoked = conn.execute(
        text(
            """
            UPDATE rtm_operator_sessions
            SET status='revoked',
                revoked_at=NOW(),
                close_reason='synthetic_operator_disabled'
            WHERE operator_id=CAST(:operator_id AS UUID)
              AND status='active'
            """
        ),
        {"operator_id": operator_id},
    ).rowcount
    conn.execute(
        text(
            """
            UPDATE rtm_operators
            SET status='disabled',
                disabled_at=NOW(),
                auth_epoch=auth_epoch+1,
                updated_at=NOW()
            WHERE id=CAST(:operator_id AS UUID)
            """
        ),
        {"operator_id": operator_id},
    )
    return {
        "operator_id": operator_id,
        "email": normalized_email,
        "sessions_revoked": int(revoked or 0),
        "status": "disabled",
    }


__all__ = [
    "DEFAULT_SYNTHETIC_DISPLAY_NAME",
    "DEFAULT_SYNTHETIC_EMAIL",
    "OPERATOR_PROVISIONING_VERSION",
    "OperatorRoleDefinition",
    "ProvisionedOperator",
    "ROLE_DEFINITIONS",
    "count_non_synthetic_operators",
    "disable_synthetic_operator",
    "ensure_minimum_roles",
    "find_operator_by_email",
    "generate_temporary_password",
    "normalize_synthetic_operator_email",
    "provision_synthetic_operator",
    "role_definition",
]
