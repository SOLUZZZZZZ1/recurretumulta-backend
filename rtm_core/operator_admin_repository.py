"""Consultas sanitizadas y revocaciones del panel supervisor RTM.

Esta unidad no devuelve hashes de contraseña, tokens de sesión, claves opacas
de dispositivo ni evidencia sensible. Las mutaciones son revocaciones suaves:
no borran operadores, sesiones, dispositivos ni historial append-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text


OPERATOR_ADMIN_REPOSITORY_VERSION = "rtm_operator_admin_repository_v1_0"


class OperatorAdminSelfProtectionError(RuntimeError):
    """Impide que el supervisor destruya su propia sesión o dispositivo."""


@dataclass(frozen=True)
class SessionRevocation:
    session_id: str
    operator_id: str
    device_id: str | None
    previous_status: str
    status: str
    changed: bool


@dataclass(frozen=True)
class DeviceRevocation:
    device_id: str
    operator_id: str
    previous_status: str
    status: str
    changed: bool
    sessions_revoked: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _mapping_list(result) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


def count_operators(conn) -> int:
    return int(
        conn.execute(
            text("SELECT COUNT(*) FROM rtm_operators")
        ).scalar_one()
    )


def list_operator_summaries(
    conn,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    return _mapping_list(
        conn.execute(
            text(
                """
                SELECT
                    o.id,
                    o.email,
                    o.display_name,
                    o.status,
                    o.must_change_password,
                    o.mfa_required,
                    o.failed_login_count,
                    o.locked_until,
                    o.last_login_at,
                    o.created_at,
                    o.updated_at,
                    r.code AS role_code,
                    COALESCE(r.permissions, '[]'::jsonb) AS permissions,
                    (
                        SELECT COUNT(*)
                        FROM rtm_operator_sessions s
                        WHERE s.operator_id=o.id
                          AND s.status='active'
                    ) AS active_session_count,
                    (
                        SELECT COUNT(*)
                        FROM rtm_operator_devices d
                        WHERE d.operator_id=o.id
                    ) AS device_count
                FROM rtm_operators o
                LEFT JOIN rtm_operator_roles r
                  ON r.id=o.primary_role_id
                ORDER BY o.created_at ASC, o.id ASC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": limit, "offset": offset},
        )
    )


def get_operator_summary(conn, operator_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        text(
            """
            SELECT
                o.id,
                o.email,
                o.display_name,
                o.status,
                o.must_change_password,
                o.mfa_required,
                o.failed_login_count,
                o.last_failed_login_at,
                o.locked_until,
                o.password_algorithm,
                o.password_version,
                o.password_changed_at,
                o.auth_epoch,
                o.last_login_at,
                o.profile,
                o.created_at,
                o.updated_at,
                r.code AS role_code,
                COALESCE(r.permissions, '[]'::jsonb) AS permissions,
                (
                    SELECT COUNT(*)
                    FROM rtm_operator_sessions s
                    WHERE s.operator_id=o.id
                ) AS session_count,
                (
                    SELECT COUNT(*)
                    FROM rtm_operator_sessions s
                    WHERE s.operator_id=o.id
                      AND s.status='active'
                ) AS active_session_count,
                (
                    SELECT COUNT(*)
                    FROM rtm_operator_devices d
                    WHERE d.operator_id=o.id
                ) AS device_count
            FROM rtm_operators o
            LEFT JOIN rtm_operator_roles r
              ON r.id=o.primary_role_id
            WHERE o.id=CAST(:operator_id AS UUID)
            LIMIT 1
            """
        ),
        {"operator_id": operator_id},
    ).mappings().first()
    return dict(row) if row else None


def list_operator_sessions(
    conn,
    *,
    operator_id: str,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    status_clause = ""
    parameters: dict[str, Any] = {
        "operator_id": operator_id,
        "limit": limit,
        "offset": offset,
    }
    if status:
        status_clause = "AND s.status=:status"
        parameters["status"] = status

    return _mapping_list(
        conn.execute(
            text(
                f"""
                SELECT
                    s.id,
                    s.operator_id,
                    s.status,
                    s.login_at,
                    s.last_seen_at,
                    s.last_verified_at,
                    s.expires_at,
                    s.absolute_expires_at,
                    s.logout_at,
                    s.revoked_at,
                    s.revoked_by,
                    s.close_reason,
                    s.ip_address AS ip_masked,
                    s.user_agent AS user_agent_summary,
                    s.device_id,
                    s.login_access_event_id,
                    s.ip_source,
                    s.ip_trusted,
                    s.country_code,
                    s.region,
                    s.city,
                    s.timezone,
                    s.risk_flags,
                    s.created_at
                FROM rtm_operator_sessions s
                WHERE s.operator_id=CAST(:operator_id AS UUID)
                  {status_clause}
                ORDER BY s.login_at DESC, s.id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            parameters,
        )
    )


def list_operator_devices(
    conn,
    *,
    operator_id: str,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    return _mapping_list(
        conn.execute(
            text(
                """
                SELECT
                    d.id,
                    d.operator_id,
                    d.status,
                    d.display_name,
                    d.device_type,
                    d.os_family,
                    d.os_version,
                    d.browser_family,
                    d.browser_version,
                    d.first_seen_at,
                    d.last_seen_at,
                    d.trusted_at,
                    d.trusted_by,
                    d.revoked_at,
                    d.revoked_by,
                    d.revocation_reason,
                    d.created_at,
                    d.updated_at
                FROM rtm_operator_devices d
                WHERE d.operator_id=CAST(:operator_id AS UUID)
                ORDER BY d.last_seen_at DESC, d.id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {
                "operator_id": operator_id,
                "limit": limit,
                "offset": offset,
            },
        )
    )


def list_operator_access_events(
    conn,
    *,
    operator_id: str,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    return _mapping_list(
        conn.execute(
            text(
                """
                SELECT
                    a.id,
                    a.operator_id,
                    a.session_id,
                    a.device_id,
                    a.event_type,
                    a.result,
                    a.auth_method,
                    a.occurred_at,
                    a.ip_masked,
                    a.ip_family,
                    a.ip_source,
                    a.ip_trusted,
                    a.device_type,
                    a.os_family,
                    a.os_version,
                    a.browser_family,
                    a.browser_version,
                    a.country_code,
                    a.region,
                    a.city,
                    a.timezone,
                    a.location_source,
                    a.request_id,
                    a.reason_code,
                    a.reason_detail,
                    a.risk_flags,
                    a.created_at
                FROM rtm_operator_access_events a
                WHERE a.operator_id=CAST(:operator_id AS UUID)
                ORDER BY a.occurred_at DESC, a.id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {
                "operator_id": operator_id,
                "limit": limit,
                "offset": offset,
            },
        )
    )


def revoke_operator_session(
    conn,
    *,
    session_id: str,
    actor_operator_id: str,
    actor_session_id: str,
    reason: str,
    now: datetime | None = None,
) -> SessionRevocation:
    if session_id == actor_session_id:
        raise OperatorAdminSelfProtectionError(
            "Use logout para cerrar la sesión supervisora actual"
        )

    row = conn.execute(
        text(
            """
            SELECT id, operator_id, device_id, status
            FROM rtm_operator_sessions
            WHERE id=CAST(:session_id AS UUID)
            FOR UPDATE
            """
        ),
        {"session_id": session_id},
    ).mappings().first()
    if not row:
        raise LookupError("Sesión no encontrada")

    previous = str(row["status"])
    changed = False
    if previous == "active":
        conn.execute(
            text(
                """
                UPDATE rtm_operator_sessions
                SET status='revoked',
                    revoked_at=:now,
                    revoked_by=CAST(:actor_operator_id AS UUID),
                    close_reason=:reason
                WHERE id=CAST(:session_id AS UUID)
                  AND status='active'
                """
            ),
            {
                "session_id": session_id,
                "actor_operator_id": actor_operator_id,
                "reason": reason,
                "now": now or _utcnow(),
            },
        )
        changed = True

    return SessionRevocation(
        session_id=str(row["id"]),
        operator_id=str(row["operator_id"]),
        device_id=(
            str(row["device_id"])
            if row["device_id"] is not None
            else None
        ),
        previous_status=previous,
        status="revoked" if changed else previous,
        changed=changed,
    )


def revoke_operator_device(
    conn,
    *,
    device_id: str,
    actor_operator_id: str,
    actor_session_id: str,
    reason: str,
    now: datetime | None = None,
) -> DeviceRevocation:
    current = now or _utcnow()
    actor_device = conn.execute(
        text(
            """
            SELECT device_id
            FROM rtm_operator_sessions
            WHERE id=CAST(:session_id AS UUID)
            """
        ),
        {"session_id": actor_session_id},
    ).scalar_one_or_none()
    if actor_device is not None and str(actor_device) == device_id:
        raise OperatorAdminSelfProtectionError(
            "No se puede revocar el dispositivo de la sesión supervisora actual"
        )

    row = conn.execute(
        text(
            """
            SELECT id, operator_id, status
            FROM rtm_operator_devices
            WHERE id=CAST(:device_id AS UUID)
            FOR UPDATE
            """
        ),
        {"device_id": device_id},
    ).mappings().first()
    if not row:
        raise LookupError("Dispositivo no encontrado")

    previous = str(row["status"])
    changed = False
    if previous != "revoked":
        conn.execute(
            text(
                """
                UPDATE rtm_operator_devices
                SET status='revoked',
                    revoked_at=:now,
                    revoked_by=CAST(:actor_operator_id AS UUID),
                    revocation_reason=:reason,
                    trusted_at=NULL,
                    trusted_by=NULL,
                    updated_at=:now
                WHERE id=CAST(:device_id AS UUID)
                """
            ),
            {
                "device_id": device_id,
                "actor_operator_id": actor_operator_id,
                "reason": reason,
                "now": current,
            },
        )
        changed = True

    revoked_sessions = conn.execute(
        text(
            """
            UPDATE rtm_operator_sessions
            SET status='revoked',
                revoked_at=:now,
                revoked_by=CAST(:actor_operator_id AS UUID),
                close_reason='device_revoked'
            WHERE device_id=CAST(:device_id AS UUID)
              AND status='active'
            """
        ),
        {
            "device_id": device_id,
            "actor_operator_id": actor_operator_id,
            "now": current,
        },
    ).rowcount

    return DeviceRevocation(
        device_id=str(row["id"]),
        operator_id=str(row["operator_id"]),
        previous_status=previous,
        status="revoked",
        changed=bool(changed or revoked_sessions),
        sessions_revoked=int(revoked_sessions or 0),
    )


__all__ = [
    "OPERATOR_ADMIN_REPOSITORY_VERSION",
    "DeviceRevocation",
    "OperatorAdminSelfProtectionError",
    "SessionRevocation",
    "count_operators",
    "get_operator_summary",
    "list_operator_access_events",
    "list_operator_devices",
    "list_operator_sessions",
    "list_operator_summaries",
    "revoke_operator_device",
    "revoke_operator_session",
]
