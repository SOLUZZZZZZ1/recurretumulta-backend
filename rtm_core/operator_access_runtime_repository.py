"""Persistencia del historial real de accesos y dispositivos RTM."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import text

from rtm_core.operator_auth_request import RequestFingerprint


OPERATOR_ACCESS_RUNTIME_REPOSITORY_VERSION = (
    "rtm_operator_access_runtime_repository_v1_0"
)


@dataclass(frozen=True)
class DeviceResolution:
    device_id: str
    status: str
    created: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def upsert_operator_device(
    conn,
    *,
    operator_id: str,
    device_key_sha256: str,
    context: RequestFingerprint,
    now: datetime | None = None,
) -> DeviceResolution:
    """Crea o actualiza un dispositivo sin carrera entre logins simultáneos."""

    current = now or _utcnow()
    metadata = json.dumps(
        {
            "runtime_version": OPERATOR_ACCESS_RUNTIME_REPOSITORY_VERSION,
            "last_request_id": context.request_id,
        },
        ensure_ascii=False,
    )
    inserted = conn.execute(
        text(
            """
            INSERT INTO rtm_operator_devices(
                id, operator_id, device_key_sha256, status, display_name,
                device_type, os_family, os_version, browser_family,
                browser_version, first_seen_at, last_seen_at,
                first_ip_hash_sha256, last_ip_hash_sha256, metadata,
                created_at, updated_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:operator_id AS UUID),
                :device_key_sha256, 'known', NULL, :device_type,
                :os_family, :os_version, :browser_family, :browser_version,
                :now, :now, :ip_hash, :ip_hash, CAST(:metadata AS JSONB),
                :now, :now
            )
            ON CONFLICT (operator_id, device_key_sha256) DO NOTHING
            RETURNING id, status
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "operator_id": operator_id,
            "device_key_sha256": device_key_sha256,
            "device_type": context.device_type,
            "os_family": context.os_family,
            "os_version": context.os_version,
            "browser_family": context.browser_family,
            "browser_version": context.browser_version,
            "now": current,
            "ip_hash": context.ip_hash_sha256,
            "metadata": metadata,
        },
    ).mappings().fetchone()
    if inserted:
        return DeviceResolution(
            device_id=str(inserted["id"]),
            status=str(inserted["status"]),
            created=True,
        )

    existing = conn.execute(
        text(
            """
            UPDATE rtm_operator_devices
            SET last_seen_at=:now,
                last_ip_hash_sha256=:ip_hash,
                device_type=:device_type,
                os_family=:os_family,
                os_version=:os_version,
                browser_family=:browser_family,
                browser_version=:browser_version,
                metadata=metadata || CAST(:metadata AS JSONB),
                updated_at=:now
            WHERE operator_id=CAST(:operator_id AS UUID)
              AND device_key_sha256=:device_key_sha256
            RETURNING id, status
            """
        ),
        {
            "operator_id": operator_id,
            "device_key_sha256": device_key_sha256,
            "now": current,
            "ip_hash": context.ip_hash_sha256,
            "device_type": context.device_type,
            "os_family": context.os_family,
            "os_version": context.os_version,
            "browser_family": context.browser_family,
            "browser_version": context.browser_version,
            "metadata": metadata,
        },
    ).mappings().one()
    return DeviceResolution(
        device_id=str(existing["id"]),
        status=str(existing["status"]),
        created=False,
    )


def record_operator_access_event(
    conn,
    *,
    context: RequestFingerprint,
    event_type: str,
    result: str,
    auth_method: str | None,
    retention_days: int,
    operator_id: str | None = None,
    session_id: str | None = None,
    device_id: str | None = None,
    device_key_sha256: str | None = None,
    login_identifier_sha256: str | None = None,
    reason_code: str | None = None,
    reason_detail: str | None = None,
    risk_flags: Iterable[str] = (),
    now: datetime | None = None,
) -> str:
    current = now or _utcnow()
    event_id = str(uuid.uuid4())
    combined_flags = sorted(set(context.risk_flags) | {str(v) for v in risk_flags})
    metadata = json.dumps(
        {"runtime_version": OPERATOR_ACCESS_RUNTIME_REPOSITORY_VERSION},
        ensure_ascii=False,
    )
    conn.execute(
        text(
            """
            INSERT INTO rtm_operator_access_events(
                id, operator_id, session_id, device_id, event_type, result,
                auth_method, occurred_at, login_identifier_sha256,
                ip_masked, ip_hash_sha256, ip_family, ip_source, ip_trusted,
                device_key_sha256, device_type, os_family, os_version,
                browser_family, browser_version, country_code, region, city,
                timezone, location_source, request_id, reason_code,
                reason_detail, risk_flags, metadata, created_at
            ) VALUES (
                CAST(:id AS UUID), CAST(:operator_id AS UUID),
                CAST(:session_id AS UUID), CAST(:device_id AS UUID),
                :event_type, :result, :auth_method, :occurred_at,
                :login_identifier_sha256, :ip_masked, :ip_hash_sha256,
                :ip_family, :ip_source, :ip_trusted, :device_key_sha256,
                :device_type, :os_family, :os_version, :browser_family,
                :browser_version, :country_code, :region, :city, :timezone,
                :location_source, :request_id, :reason_code, :reason_detail,
                CAST(:risk_flags AS JSONB), CAST(:metadata AS JSONB), :created_at
            )
            """
        ),
        {
            "id": event_id,
            "operator_id": operator_id,
            "session_id": session_id,
            "device_id": device_id,
            "event_type": event_type,
            "result": result,
            "auth_method": auth_method,
            "occurred_at": current,
            "login_identifier_sha256": login_identifier_sha256,
            "ip_masked": context.ip_masked,
            "ip_hash_sha256": context.ip_hash_sha256,
            "ip_family": context.ip_family,
            "ip_source": context.ip_source,
            "ip_trusted": context.ip_trusted,
            "device_key_sha256": device_key_sha256,
            "device_type": context.device_type,
            "os_family": context.os_family,
            "os_version": context.os_version,
            "browser_family": context.browser_family,
            "browser_version": context.browser_version,
            "country_code": context.country_code,
            "region": context.region,
            "city": context.city,
            "timezone": context.timezone,
            "location_source": context.location_source,
            "request_id": context.request_id,
            "reason_code": reason_code,
            "reason_detail": reason_detail,
            "risk_flags": json.dumps(combined_flags, ensure_ascii=False),
            "metadata": metadata,
            "created_at": current,
        },
    )

    if context.ip_address or context.raw_user_agent:
        conn.execute(
            text(
                """
                INSERT INTO rtm_operator_access_evidence(
                    id, access_event_id, ip_address, raw_user_agent,
                    trusted_headers, retention_until, created_at
                ) VALUES (
                    CAST(:id AS UUID), CAST(:event_id AS UUID),
                    CAST(:ip_address AS INET), :raw_user_agent,
                    CAST(:trusted_headers AS JSONB), :retention_until, :created_at
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "event_id": event_id,
                "ip_address": context.ip_address,
                "raw_user_agent": context.raw_user_agent,
                "trusted_headers": json.dumps(
                    context.trusted_headers,
                    ensure_ascii=False,
                ),
                "retention_until": current + timedelta(days=retention_days),
                "created_at": current,
            },
        )
    return event_id


def link_session_to_access_event(
    conn,
    *,
    session_id: str,
    access_event_id: str,
    device_id: str | None,
) -> None:
    conn.execute(
        text(
            """
            UPDATE rtm_operator_sessions
            SET login_access_event_id=CAST(:access_event_id AS UUID),
                device_id=CAST(:device_id AS UUID)
            WHERE id=CAST(:session_id AS UUID)
            """
        ),
        {
            "session_id": session_id,
            "access_event_id": access_event_id,
            "device_id": device_id,
        },
    )


__all__ = [
    "DeviceResolution",
    "OPERATOR_ACCESS_RUNTIME_REPOSITORY_VERSION",
    "link_session_to_access_event",
    "record_operator_access_event",
    "upsert_operator_device",
]
