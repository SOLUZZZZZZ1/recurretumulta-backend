"""Repositorio SQL del núcleo de autenticación individual RTM.

El token bruto solo existe en memoria y se devuelve una única vez al login.
PostgreSQL conserva exclusivamente SHA-256. Esta unidad no expone endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from rtm_core.operator_auth_crypto import hash_session_token


OPERATOR_AUTH_REPOSITORY_VERSION = "rtm_operator_auth_repository_v1_3"
DEFAULT_LOCK_THRESHOLD = 5
DEFAULT_LOCK_MINUTES = 15
DEFAULT_SESSION_HOURS = 8
DEFAULT_ABSOLUTE_SESSION_HOURS = 24


@dataclass(frozen=True)
class ActiveOperatorSession:
    session_id: str
    operator_id: str
    email: str
    display_name: str
    role_code: str | None
    permissions: tuple[str, ...]
    must_change_password: bool
    mfa_required: bool
    login_at: datetime
    last_verified_at: datetime | None
    device_id: str | None
    expires_at: datetime
    absolute_expires_at: datetime | None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def find_operator_for_login(conn, normalized_email: str):
    return conn.execute(
        text(
            """
            SELECT o.id, o.email, o.display_name, o.password_hash, o.status,
                   o.must_change_password, o.mfa_required,
                   o.failed_login_count, o.locked_until, o.auth_epoch,
                   r.code AS role_code,
                   COALESCE(r.permissions, '[]'::jsonb) AS permissions
            FROM rtm_operators o
            JOIN rtm_operator_roles r
              ON r.id = o.primary_role_id
             AND r.active = TRUE
            WHERE lower(btrim(o.email)) = :email
            LIMIT 1
            """
        ),
        {"email": normalized_email},
    ).mappings().fetchone()


def register_failed_login(
    conn,
    operator_id: str,
    *,
    now: datetime | None = None,
    threshold: int = DEFAULT_LOCK_THRESHOLD,
    lock_minutes: int = DEFAULT_LOCK_MINUTES,
):
    current = now or utcnow()
    return conn.execute(
        text(
            """
            UPDATE rtm_operators
            SET failed_login_count = failed_login_count + 1,
                last_failed_login_at = :now,
                locked_until = CASE
                    WHEN failed_login_count + 1 >= :threshold
                    THEN :now + (:lock_minutes || ' minutes')::interval
                    ELSE locked_until
                END,
                updated_at = NOW()
            WHERE id = CAST(:operator_id AS UUID)
            RETURNING failed_login_count, locked_until
            """
        ),
        {
            "operator_id": operator_id,
            "now": current,
            "threshold": threshold,
            "lock_minutes": lock_minutes,
        },
    ).mappings().fetchone()


def clear_failed_logins(conn, operator_id: str) -> None:
    conn.execute(
        text(
            """
            UPDATE rtm_operators
            SET failed_login_count=0,
                last_failed_login_at=NULL,
                locked_until=NULL,
                last_login_at=NOW(),
                updated_at=NOW()
            WHERE id=CAST(:operator_id AS UUID)
            """
        ),
        {"operator_id": operator_id},
    )


def clear_failed_reauthentication_attempts(conn, operator_id: str) -> None:
    """Limpia el bloqueo sin convertir el step-up en un nuevo login."""

    conn.execute(
        text(
            """
            UPDATE rtm_operators
            SET failed_login_count=0,
                last_failed_login_at=NULL,
                locked_until=NULL,
                updated_at=NOW()
            WHERE id=CAST(:operator_id AS UUID)
            """
        ),
        {"operator_id": operator_id},
    )


def create_operator_session(
    conn,
    *,
    operator_id: str,
    raw_token: str,
    auth_epoch: int,
    now: datetime | None = None,
    session_hours: int = DEFAULT_SESSION_HOURS,
    absolute_hours: int = DEFAULT_ABSOLUTE_SESSION_HOURS,
    device_id: str | None = None,
    login_access_event_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    ip_source: str | None = None,
    ip_trusted: bool = False,
    country_code: str | None = None,
    region: str | None = None,
    city: str | None = None,
    timezone_name: str | None = None,
    risk_flags_json: str = "[]",
    metadata_json: str = "{}",
) -> str:
    current = now or utcnow()
    expires_at = current + timedelta(hours=session_hours)
    absolute_expires_at = current + timedelta(hours=absolute_hours)
    row = conn.execute(
        text(
            """
            INSERT INTO rtm_operator_sessions(
                operator_id, token_sha256, status, login_at, last_seen_at,
                expires_at, absolute_expires_at, auth_epoch,
                ip_address, user_agent, metadata, device_id,
                login_access_event_id, ip_source, ip_trusted,
                country_code, region, city, timezone, risk_flags,
                created_at, last_verified_at
            ) VALUES (
                CAST(:operator_id AS UUID), :token_sha256, 'active', :now, :now,
                :expires_at, :absolute_expires_at, :auth_epoch,
                :ip_address, :user_agent, CAST(:metadata AS JSONB),
                CAST(:device_id AS UUID),
                CAST(:login_access_event_id AS UUID),
                :ip_source, :ip_trusted, :country_code, :region, :city,
                :timezone_name, CAST(:risk_flags AS JSONB), :now, :now
            )
            RETURNING id
            """
        ),
        {
            "operator_id": operator_id,
            "token_sha256": hash_session_token(raw_token),
            "now": current,
            "expires_at": expires_at,
            "absolute_expires_at": absolute_expires_at,
            "auth_epoch": auth_epoch,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "metadata": metadata_json,
            "device_id": device_id,
            "login_access_event_id": login_access_event_id,
            "ip_source": ip_source,
            "ip_trusted": ip_trusted,
            "country_code": country_code,
            "region": region,
            "city": city,
            "timezone_name": timezone_name,
            "risk_flags": risk_flags_json,
        },
    ).fetchone()
    return str(row[0])


def load_active_operator_session(
    conn,
    raw_token: str,
    *,
    now: datetime | None = None,
) -> ActiveOperatorSession | None:
    current = now or utcnow()
    row = conn.execute(
        text(
            """
            SELECT s.id, s.operator_id, o.email, o.display_name,
                   r.code AS role_code,
                   COALESCE(r.permissions, '[]'::jsonb) AS permissions,
                   o.must_change_password, o.mfa_required,
                   s.login_at, s.last_verified_at, s.device_id,
                   s.expires_at, s.absolute_expires_at
            FROM rtm_operator_sessions s
            JOIN rtm_operators o ON o.id = s.operator_id
            JOIN rtm_operator_devices d
              ON d.id = s.device_id
             AND d.operator_id = o.id
             AND d.status IN ('known', 'trusted')
            JOIN rtm_operator_roles r
              ON r.id = o.primary_role_id
             AND r.active = TRUE
            WHERE s.token_sha256 = :token_sha256
              AND s.status = 'active'
              AND s.expires_at > :now
              AND (s.absolute_expires_at IS NULL OR s.absolute_expires_at > :now)
              AND o.status = 'active'
              AND (o.locked_until IS NULL OR o.locked_until <= :now)
              AND s.auth_epoch = o.auth_epoch
            LIMIT 1
            """
        ),
        {"token_sha256": hash_session_token(raw_token), "now": current},
    ).mappings().fetchone()
    return _active_operator_session_from_row(row)


def _active_operator_session_from_row(row) -> ActiveOperatorSession | None:
    if not row:
        return None
    permissions = row["permissions"] if isinstance(row["permissions"], list) else []
    return ActiveOperatorSession(
        session_id=str(row["id"]),
        operator_id=str(row["operator_id"]),
        email=str(row["email"]),
        display_name=str(row["display_name"]),
        role_code=str(row["role_code"]) if row["role_code"] else None,
        permissions=tuple(str(value) for value in permissions),
        must_change_password=bool(row["must_change_password"]),
        mfa_required=bool(row["mfa_required"]),
        login_at=row["login_at"],
        last_verified_at=row["last_verified_at"],
        device_id=(
            str(row["device_id"])
            if row["device_id"] is not None
            else None
        ),
        expires_at=row["expires_at"],
        absolute_expires_at=row["absolute_expires_at"],
    )


def load_active_operator_session_for_device(
    conn,
    raw_token: str,
    *,
    device_key_sha256: str,
    now: datetime | None = None,
) -> ActiveOperatorSession | None:
    """Carga la sesion y prueba su dispositivo usando solo un digest."""

    digest = str(device_key_sha256 or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        return None
    current = now or utcnow()
    row = conn.execute(
        text(
            """
            SELECT s.id, s.operator_id, o.email, o.display_name,
                   r.code AS role_code,
                   COALESCE(r.permissions, '[]'::jsonb) AS permissions,
                   o.must_change_password, o.mfa_required,
                   s.login_at, s.last_verified_at, s.device_id,
                   s.expires_at, s.absolute_expires_at
            FROM rtm_operator_sessions s
            JOIN rtm_operators o ON o.id = s.operator_id
            JOIN rtm_operator_devices d
              ON d.id = s.device_id
             AND d.operator_id = o.id
             AND d.status IN ('known', 'trusted')
             AND d.device_key_sha256 = :device_key_sha256
            JOIN rtm_operator_roles r
              ON r.id = o.primary_role_id
             AND r.active = TRUE
            WHERE s.token_sha256 = :token_sha256
              AND s.status = 'active'
              AND s.expires_at > :now
              AND (s.absolute_expires_at IS NULL OR s.absolute_expires_at > :now)
              AND o.status = 'active'
              AND (o.locked_until IS NULL OR o.locked_until <= :now)
              AND s.auth_epoch = o.auth_epoch
            LIMIT 1
            """
        ),
        {
            "token_sha256": hash_session_token(raw_token),
            "device_key_sha256": digest,
            "now": current,
        },
    ).mappings().fetchone()
    return _active_operator_session_from_row(row)


def find_operator_for_reauthentication(
    conn,
    *,
    operator_id: str,
    session_id: str,
):
    """Bloquea identidad, rol activo, sesion y dispositivo del step-up."""

    return conn.execute(
        text(
            """
            SELECT o.id, o.password_hash, o.status,
                   o.must_change_password, o.mfa_required,
                   o.failed_login_count, o.locked_until, o.auth_epoch,
                   s.id AS session_id, s.status AS session_status,
                   s.expires_at, s.absolute_expires_at,
                   s.auth_epoch AS session_auth_epoch, s.device_id,
                   d.status AS device_status, r.id AS role_id
            FROM rtm_operators o
            JOIN rtm_operator_roles r
              ON r.id = o.primary_role_id
             AND r.active = TRUE
            JOIN rtm_operator_sessions s
              ON s.operator_id=o.id
             AND s.id=CAST(:session_id AS UUID)
            JOIN rtm_operator_devices d
              ON d.id=s.device_id
             AND d.operator_id=o.id
            WHERE o.id=CAST(:operator_id AS UUID)
            FOR UPDATE OF o, r, s, d
            """
        ),
        {
            "operator_id": operator_id,
            "session_id": session_id,
        },
    ).mappings().fetchone()


def mark_operator_session_verified(
    conn,
    *,
    session_id: str,
    operator_id: str,
    now: datetime | None = None,
) -> bool:
    """Actualiza el reloj de verificación solo si toda la cadena sigue activa."""

    current = now or utcnow()
    row = conn.execute(
        text(
            """
            UPDATE rtm_operator_sessions s
            SET last_verified_at=:now
            FROM rtm_operators o, rtm_operator_devices d,
                 rtm_operator_roles r
            WHERE s.id=CAST(:session_id AS UUID)
              AND s.operator_id=CAST(:operator_id AS UUID)
              AND o.id=s.operator_id
              AND d.id=s.device_id
              AND d.operator_id=o.id
              AND d.status IN ('known', 'trusted')
              AND r.id = o.primary_role_id
              AND r.active = TRUE
              AND s.status='active'
              AND s.expires_at > :now
              AND (
                    s.absolute_expires_at IS NULL
                    OR s.absolute_expires_at > :now
                  )
              AND o.status='active'
              AND (o.locked_until IS NULL OR o.locked_until <= :now)
              AND s.auth_epoch=o.auth_epoch
            RETURNING s.id
            """
        ),
        {
            "session_id": session_id,
            "operator_id": operator_id,
            "now": current,
        },
    ).fetchone()
    return bool(row)


def touch_operator_session(
    conn,
    session_id: str,
    *,
    now: datetime | None = None,
) -> None:
    conn.execute(
        text(
            """
            UPDATE rtm_operator_sessions
            SET last_seen_at=:now
            WHERE id=CAST(:session_id AS UUID) AND status='active'
            """
        ),
        {"session_id": session_id, "now": now or utcnow()},
    )


def close_operator_session(
    conn,
    session_id: str,
    *,
    reason: str = "logout",
) -> bool:
    row = conn.execute(
        text(
            """
            UPDATE rtm_operator_sessions
            SET status='closed', logout_at=NOW(), close_reason=:reason
            WHERE id=CAST(:session_id AS UUID) AND status='active'
            RETURNING id
            """
        ),
        {"session_id": session_id, "reason": reason},
    ).fetchone()
    return bool(row)


def increment_operator_auth_epoch(conn, operator_id: str) -> int:
    row = conn.execute(
        text(
            """
            UPDATE rtm_operators
            SET auth_epoch=auth_epoch+1, updated_at=NOW()
            WHERE id=CAST(:operator_id AS UUID)
            RETURNING auth_epoch
            """
        ),
        {"operator_id": operator_id},
    ).fetchone()
    if not row:
        raise LookupError("Operador no encontrado")
    return int(row[0])


__all__ = [
    "ActiveOperatorSession",
    "DEFAULT_ABSOLUTE_SESSION_HOURS",
    "DEFAULT_LOCK_MINUTES",
    "DEFAULT_LOCK_THRESHOLD",
    "DEFAULT_SESSION_HOURS",
    "OPERATOR_AUTH_REPOSITORY_VERSION",
    "clear_failed_logins",
    "clear_failed_reauthentication_attempts",
    "close_operator_session",
    "create_operator_session",
    "find_operator_for_login",
    "find_operator_for_reauthentication",
    "increment_operator_auth_epoch",
    "load_active_operator_session",
    "load_active_operator_session_for_device",
    "mark_operator_session_verified",
    "register_failed_login",
    "touch_operator_session",
    "utcnow",
]
