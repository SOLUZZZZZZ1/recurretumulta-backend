"""Persistencia del ciclo de vida y credenciales de operadores RTM.

No borra operadores ni historial. Solo admite cuentas sintéticas de staging,
roles mínimos controlados y mutaciones suaves con invalidación de sesiones.
Las contraseñas se reciben en memoria y PostgreSQL conserva únicamente Argon2id.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from rtm_core.operator_auth_crypto import (
    hash_operator_password,
    validate_operator_password,
    verify_operator_password,
)
from rtm_core.operator_lifecycle_policy import ALLOWED_ROLE_CODES
from rtm_core.operator_provisioning import normalize_synthetic_operator_email


OPERATOR_LIFECYCLE_REPOSITORY_VERSION = (
    "rtm_operator_lifecycle_repository_v1_0"
)


class OperatorLifecycleConflict(RuntimeError):
    """La mutación contradice una protección del ciclo de vida."""


class OperatorLifecycleSelfProtectionError(RuntimeError):
    """Impide que un supervisor se quite su propio acceso."""


class OperatorLifecycleStateError(RuntimeError):
    """El estado actual no admite la transición solicitada."""


class OperatorCurrentPasswordInvalid(RuntimeError):
    """La contraseña actual de autoservicio no es válida."""


class OperatorPasswordReuseError(RuntimeError):
    """La contraseña nueva coincide con la vigente."""


@dataclass(frozen=True)
class CreatedOperator:
    operator_id: str
    email: str
    display_name: str
    status: str
    role_code: str
    must_change_password: bool
    password_version: int
    auth_epoch: int


@dataclass(frozen=True)
class OperatorMutation:
    operator_id: str
    email: str
    status: str
    role_code: str
    must_change_password: bool
    password_version: int
    auth_epoch: int
    sessions_revoked: int
    changed: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clean_name(value: str) -> str:
    clean = " ".join(str(value or "").split()).strip()
    if len(clean) < 3 or len(clean) > 160:
        raise ValueError("Nombre visible no válido")
    return clean


def _allowed_role(value: str) -> str:
    code = str(value or "").strip().lower()
    if code not in ALLOWED_ROLE_CODES:
        raise ValueError("Rol no permitido")
    return code


def _role_row(conn, role_code: str):
    code = _allowed_role(role_code)
    row = conn.execute(
        text(
            """
            SELECT id, code, permissions
            FROM rtm_operator_roles
            WHERE code=:code
              AND active=TRUE
              AND system_role=TRUE
            LIMIT 1
            """
        ),
        {"code": code},
    ).mappings().first()
    if not row:
        raise LookupError("Rol operativo no encontrado")
    return row


def _operator_for_update(conn, operator_id: str):
    row = conn.execute(
        text(
            """
            SELECT
                o.id,
                o.email,
                o.display_name,
                o.password_hash,
                o.status,
                o.primary_role_id,
                o.must_change_password,
                o.mfa_required,
                o.password_version,
                o.auth_epoch,
                o.profile,
                r.code AS role_code
            FROM rtm_operators o
            LEFT JOIN rtm_operator_roles r
              ON r.id=o.primary_role_id
            WHERE o.id=CAST(:operator_id AS UUID)
            FOR UPDATE OF o
            """
        ),
        {"operator_id": operator_id},
    ).mappings().first()
    if not row:
        raise LookupError("Operador no encontrado")
    return row


def _count_active_supervisors(conn) -> int:
    return int(
        conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM rtm_operators o
                JOIN rtm_operator_roles r
                  ON r.id=o.primary_role_id
                WHERE o.status='active'
                  AND r.active=TRUE
                  AND r.code='rtm.supervisor'
                """
            )
        ).scalar_one()
    )


def _revoke_active_sessions(
    conn,
    *,
    operator_id: str,
    actor_operator_id: str,
    reason: str,
    now: datetime,
) -> int:
    count = conn.execute(
        text(
            """
            UPDATE rtm_operator_sessions
            SET status='revoked',
                revoked_at=:now,
                revoked_by=CAST(:actor_operator_id AS UUID),
                close_reason=:reason
            WHERE operator_id=CAST(:operator_id AS UUID)
              AND status='active'
            """
        ),
        {
            "operator_id": operator_id,
            "actor_operator_id": actor_operator_id,
            "reason": reason,
            "now": now,
        },
    ).rowcount
    return int(count or 0)


def _mutation_from_row(
    row,
    *,
    sessions_revoked: int,
    changed: bool,
) -> OperatorMutation:
    return OperatorMutation(
        operator_id=str(row["id"]),
        email=str(row["email"]),
        status=str(row["status"]),
        role_code=str(row["role_code"] or ""),
        must_change_password=bool(row["must_change_password"]),
        password_version=int(row["password_version"]),
        auth_epoch=int(row["auth_epoch"]),
        sessions_revoked=int(sessions_revoked),
        changed=bool(changed),
    )


def create_controlled_synthetic_operator(
    conn,
    *,
    actor_operator_id: str,
    email: str,
    display_name: str,
    temporary_password: str,
) -> CreatedOperator:
    normalized_email = normalize_synthetic_operator_email(email)
    clean_name = _clean_name(display_name)
    password = validate_operator_password(temporary_password)
    role = _role_row(conn, "rtm.operator")

    existing = conn.execute(
        text(
            """
            SELECT id
            FROM rtm_operators
            WHERE lower(btrim(email))=:email
            LIMIT 1
            """
        ),
        {"email": normalized_email},
    ).first()
    if existing:
        raise OperatorLifecycleConflict(
            "Ya existe un operador con ese correo"
        )

    operator_id = str(uuid.uuid4())
    profile = {
        "synthetic": True,
        "environment": "staging",
        "purpose": "controlled_operator_lifecycle",
        "lifecycle_version": OPERATOR_LIFECYCLE_REPOSITORY_VERSION,
    }
    row = conn.execute(
        text(
            """
            INSERT INTO rtm_operators(
                id, email, display_name, password_hash, status,
                primary_role_id, must_change_password, mfa_required,
                profile, failed_login_count, last_failed_login_at,
                locked_until, password_changed_at, password_algorithm,
                password_version, auth_epoch, created_by,
                created_at, updated_at, disabled_at, disabled_by
            ) VALUES (
                CAST(:id AS UUID), :email, :display_name, :password_hash,
                'active', CAST(:role_id AS UUID), TRUE, FALSE,
                CAST(:profile AS JSONB), 0, NULL, NULL, NOW(),
                'argon2id', 1, 1, CAST(:actor_operator_id AS UUID),
                NOW(), NOW(), NULL, NULL
            )
            RETURNING id, email, display_name, status,
                      must_change_password, password_version, auth_epoch
            """
        ),
        {
            "id": operator_id,
            "email": normalized_email,
            "display_name": clean_name,
            "password_hash": hash_operator_password(password),
            "role_id": str(role["id"]),
            "profile": json.dumps(profile, ensure_ascii=False),
            "actor_operator_id": actor_operator_id,
        },
    ).mappings().one()
    return CreatedOperator(
        operator_id=str(row["id"]),
        email=str(row["email"]),
        display_name=str(row["display_name"]),
        status=str(row["status"]),
        role_code=str(role["code"]),
        must_change_password=bool(row["must_change_password"]),
        password_version=int(row["password_version"]),
        auth_epoch=int(row["auth_epoch"]),
    )


def suspend_operator(
    conn,
    *,
    operator_id: str,
    actor_operator_id: str,
    reason: str,
    now: datetime | None = None,
) -> OperatorMutation:
    if operator_id == actor_operator_id:
        raise OperatorLifecycleSelfProtectionError(
            "No se puede suspender la cuenta supervisora actual"
        )
    current = now or _utcnow()
    operator = _operator_for_update(conn, operator_id)
    if str(operator["status"]) == "suspended":
        return _mutation_from_row(
            operator,
            sessions_revoked=0,
            changed=False,
        )
    if str(operator["status"]) != "active":
        raise OperatorLifecycleStateError(
            "Solo puede suspenderse un operador activo"
        )
    if (
        str(operator["role_code"]) == "rtm.supervisor"
        and _count_active_supervisors(conn) <= 1
    ):
        raise OperatorLifecycleConflict(
            "No puede suspenderse el último supervisor activo"
        )

    sessions = _revoke_active_sessions(
        conn,
        operator_id=operator_id,
        actor_operator_id=actor_operator_id,
        reason="operator_suspended",
        now=current,
    )
    row = conn.execute(
        text(
            """
            UPDATE rtm_operators
            SET status='suspended',
                auth_epoch=auth_epoch+1,
                failed_login_count=0,
                last_failed_login_at=NULL,
                locked_until=NULL,
                updated_at=:now
            WHERE id=CAST(:operator_id AS UUID)
            RETURNING id, email, status, must_change_password,
                      password_version, auth_epoch
            """
        ),
        {"operator_id": operator_id, "now": current},
    ).mappings().one()
    result = dict(row)
    result["role_code"] = operator["role_code"]
    return _mutation_from_row(
        result,
        sessions_revoked=sessions,
        changed=True,
    )


def reactivate_operator(
    conn,
    *,
    operator_id: str,
    actor_operator_id: str,
    reason: str,
    now: datetime | None = None,
) -> OperatorMutation:
    if operator_id == actor_operator_id:
        raise OperatorLifecycleSelfProtectionError(
            "La sesión supervisora actual ya debe estar activa"
        )
    current = now or _utcnow()
    operator = _operator_for_update(conn, operator_id)
    if str(operator["status"]) == "active":
        return _mutation_from_row(
            operator,
            sessions_revoked=0,
            changed=False,
        )
    if str(operator["status"]) != "suspended":
        raise OperatorLifecycleStateError(
            "Solo puede reactivarse un operador suspendido"
        )

    row = conn.execute(
        text(
            """
            UPDATE rtm_operators
            SET status='active',
                auth_epoch=auth_epoch+1,
                failed_login_count=0,
                last_failed_login_at=NULL,
                locked_until=NULL,
                updated_at=:now
            WHERE id=CAST(:operator_id AS UUID)
            RETURNING id, email, status, must_change_password,
                      password_version, auth_epoch
            """
        ),
        {"operator_id": operator_id, "now": current},
    ).mappings().one()
    result = dict(row)
    result["role_code"] = operator["role_code"]
    return _mutation_from_row(
        result,
        sessions_revoked=0,
        changed=True,
    )


def assign_operator_role(
    conn,
    *,
    operator_id: str,
    actor_operator_id: str,
    role_code: str,
    reason: str,
    now: datetime | None = None,
) -> OperatorMutation:
    if operator_id == actor_operator_id:
        raise OperatorLifecycleSelfProtectionError(
            "No se puede cambiar el rol de la sesión supervisora actual"
        )
    current = now or _utcnow()
    target_role = _role_row(conn, role_code)
    operator = _operator_for_update(conn, operator_id)
    current_role = str(operator["role_code"] or "")
    new_role = str(target_role["code"])

    if current_role == new_role:
        return _mutation_from_row(
            operator,
            sessions_revoked=0,
            changed=False,
        )
    if (
        str(operator["status"]) == "active"
        and current_role == "rtm.supervisor"
        and new_role != "rtm.supervisor"
        and _count_active_supervisors(conn) <= 1
    ):
        raise OperatorLifecycleConflict(
            "No puede retirarse el rol al último supervisor activo"
        )
    if (
        new_role == "rtm.supervisor"
        and bool(operator["must_change_password"])
    ):
        raise OperatorLifecycleConflict(
            "El operador debe cambiar primero su contraseña temporal"
        )

    sessions = _revoke_active_sessions(
        conn,
        operator_id=operator_id,
        actor_operator_id=actor_operator_id,
        reason="operator_role_changed",
        now=current,
    )
    row = conn.execute(
        text(
            """
            UPDATE rtm_operators
            SET primary_role_id=CAST(:role_id AS UUID),
                auth_epoch=auth_epoch+1,
                updated_at=:now
            WHERE id=CAST(:operator_id AS UUID)
            RETURNING id, email, status, must_change_password,
                      password_version, auth_epoch
            """
        ),
        {
            "operator_id": operator_id,
            "role_id": str(target_role["id"]),
            "now": current,
        },
    ).mappings().one()
    result = dict(row)
    result["role_code"] = new_role
    return _mutation_from_row(
        result,
        sessions_revoked=sessions,
        changed=True,
    )


def rotate_operator_password(
    conn,
    *,
    operator_id: str,
    actor_operator_id: str,
    new_password: str,
    must_change_password: bool,
    reason: str,
    now: datetime | None = None,
) -> OperatorMutation:
    if operator_id == actor_operator_id:
        raise OperatorLifecycleSelfProtectionError(
            "Use el cambio de contraseña personal para la cuenta actual"
        )
    current = now or _utcnow()
    password = validate_operator_password(new_password)
    operator = _operator_for_update(conn, operator_id)
    if verify_operator_password(
        str(operator["password_hash"] or ""),
        password,
    ).valid:
        raise OperatorPasswordReuseError(
            "La contraseña nueva no puede coincidir con la vigente"
        )
    if (
        str(operator["role_code"]) == "rtm.supervisor"
        and bool(must_change_password)
    ):
        raise OperatorLifecycleConflict(
            "La rotación de un supervisor debe fijar una contraseña final"
        )

    sessions = _revoke_active_sessions(
        conn,
        operator_id=operator_id,
        actor_operator_id=actor_operator_id,
        reason="operator_password_rotated",
        now=current,
    )
    row = conn.execute(
        text(
            """
            UPDATE rtm_operators
            SET password_hash=:password_hash,
                password_algorithm='argon2id',
                password_version=password_version+1,
                password_changed_at=:now,
                must_change_password=:must_change_password,
                failed_login_count=0,
                last_failed_login_at=NULL,
                locked_until=NULL,
                auth_epoch=auth_epoch+1,
                updated_at=:now
            WHERE id=CAST(:operator_id AS UUID)
            RETURNING id, email, status, must_change_password,
                      password_version, auth_epoch
            """
        ),
        {
            "operator_id": operator_id,
            "password_hash": hash_operator_password(password),
            "must_change_password": bool(must_change_password),
            "now": current,
        },
    ).mappings().one()
    result = dict(row)
    result["role_code"] = operator["role_code"]
    return _mutation_from_row(
        result,
        sessions_revoked=sessions,
        changed=True,
    )


def revoke_all_operator_sessions(
    conn,
    *,
    operator_id: str,
    actor_operator_id: str,
    reason: str,
    now: datetime | None = None,
) -> OperatorMutation:
    if operator_id == actor_operator_id:
        raise OperatorLifecycleSelfProtectionError(
            "Use logout para cerrar la sesión supervisora actual"
        )
    current = now or _utcnow()
    operator = _operator_for_update(conn, operator_id)
    sessions = _revoke_active_sessions(
        conn,
        operator_id=operator_id,
        actor_operator_id=actor_operator_id,
        reason="operator_sessions_revoked_all",
        now=current,
    )
    row = conn.execute(
        text(
            """
            UPDATE rtm_operators
            SET auth_epoch=auth_epoch+1,
                updated_at=:now
            WHERE id=CAST(:operator_id AS UUID)
            RETURNING id, email, status, must_change_password,
                      password_version, auth_epoch
            """
        ),
        {"operator_id": operator_id, "now": current},
    ).mappings().one()
    result = dict(row)
    result["role_code"] = operator["role_code"]
    return _mutation_from_row(
        result,
        sessions_revoked=sessions,
        changed=True,
    )


def change_own_password(
    conn,
    *,
    operator_id: str,
    actor_session_id: str,
    current_password: str,
    new_password: str,
    now: datetime | None = None,
) -> OperatorMutation:
    current = now or _utcnow()
    password = validate_operator_password(new_password)
    operator = _operator_for_update(conn, operator_id)
    verification = verify_operator_password(
        str(operator["password_hash"] or ""),
        current_password,
    )
    if not verification.valid:
        raise OperatorCurrentPasswordInvalid(
            "La contraseña actual no es válida"
        )
    if verify_operator_password(
        str(operator["password_hash"] or ""),
        password,
    ).valid:
        raise OperatorPasswordReuseError(
            "La contraseña nueva no puede coincidir con la vigente"
        )

    sessions = _revoke_active_sessions(
        conn,
        operator_id=operator_id,
        actor_operator_id=operator_id,
        reason="operator_self_password_changed",
        now=current,
    )
    row = conn.execute(
        text(
            """
            UPDATE rtm_operators
            SET password_hash=:password_hash,
                password_algorithm='argon2id',
                password_version=password_version+1,
                password_changed_at=:now,
                must_change_password=FALSE,
                failed_login_count=0,
                last_failed_login_at=NULL,
                locked_until=NULL,
                auth_epoch=auth_epoch+1,
                updated_at=:now
            WHERE id=CAST(:operator_id AS UUID)
            RETURNING id, email, status, must_change_password,
                      password_version, auth_epoch
            """
        ),
        {
            "operator_id": operator_id,
            "password_hash": hash_operator_password(password),
            "now": current,
        },
    ).mappings().one()
    result = dict(row)
    result["role_code"] = operator["role_code"]
    return _mutation_from_row(
        result,
        sessions_revoked=sessions,
        changed=True,
    )


__all__ = [
    "OPERATOR_LIFECYCLE_REPOSITORY_VERSION",
    "CreatedOperator",
    "OperatorCurrentPasswordInvalid",
    "OperatorLifecycleConflict",
    "OperatorLifecycleSelfProtectionError",
    "OperatorLifecycleStateError",
    "OperatorMutation",
    "OperatorPasswordReuseError",
    "assign_operator_role",
    "change_own_password",
    "create_controlled_synthetic_operator",
    "reactivate_operator",
    "revoke_all_operator_sessions",
    "rotate_operator_password",
    "suspend_operator",
]
