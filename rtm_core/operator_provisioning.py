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


OPERATOR_PROVISIONING_VERSION = "rtm_operator_provisioning_v1_0"
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
        permissions=("ops.view",),
    ),
    "supervisor": OperatorRoleDefinition(
        key="supervisor",
        code="rtm.supervisor",
        name="Supervisor RTM",
        description=(
            "Rol mínimo de supervisión en staging. La autorización fina se "
            "incorporará en una fase posterior."
        ),
        permissions=("ops.view", "ops.supervise"),
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
                WHERE lower(COALESCE(profile->>'synthetic', 'false')) <> 'true'
                """
            )
        ).scalar_one()
    )


def ensure_minimum_roles(conn) -> dict[str, str]:
    role_ids: dict[str, str] = {}
    for definition in ROLE_DEFINITIONS.values():
        row = conn.execute(
            text(
                """
                INSERT INTO rtm_operator_roles(
                    id, code, name, description, permissions,
                    system_role, active, created_at, updated_at
                ) VALUES (
                    CAST(:id AS UUID), :code, :name, :description,
                    CAST(:permissions AS JSONB), TRUE, TRUE, NOW(), NOW()
                )
                ON CONFLICT (code) DO UPDATE SET
                    name=EXCLUDED.name,
                    description=EXCLUDED.description,
                    permissions=EXCLUDED.permissions,
                    system_role=TRUE,
                    active=TRUE,
                    updated_at=NOW()
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
        role_ids[definition.key] = str(row[0])
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
        return ProvisionedOperator(
            operator_id=str(existing["id"]),
            email=str(existing["email"]),
            display_name=str(existing["display_name"]),
            role_code=str(existing["role_code"] or ""),
            created=False,
            password_issued=False,
        )

    if count_non_synthetic_operators(conn):
        raise RuntimeError(
            "La provisión inicial se bloquea porque existen operadores no sintéticos"
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
