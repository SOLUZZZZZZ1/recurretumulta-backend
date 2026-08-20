"""Servicio de login individual y sesiones de operadores RTM."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import text

from rtm_core.operator_access_runtime_repository import (
    link_session_to_access_event,
    record_operator_access_event,
    upsert_operator_device,
)
from rtm_core.operator_auth_crypto import (
    generate_device_secret,
    generate_session_token,
    hash_device_secret,
    hash_operator_password,
    verify_operator_password,
)
from rtm_core.operator_auth_repository import (
    DEFAULT_ABSOLUTE_SESSION_HOURS,
    DEFAULT_SESSION_HOURS,
    ActiveOperatorSession,
    clear_failed_logins,
    close_operator_session,
    create_operator_session,
    find_operator_for_login,
    load_active_operator_session,
    register_failed_login,
    touch_operator_session,
)
from rtm_core.operator_auth_request import (
    OperatorAuthRuntimeConfig,
    RequestFingerprint,
    hash_login_identifier,
    normalize_device_token,
)


OPERATOR_AUTH_SERVICE_VERSION = "rtm_operator_auth_service_v1_0"
_GENERIC_LOGIN_ERROR = "Credenciales no válidas"


@dataclass(frozen=True)
class LoginDecision:
    ok: bool
    status_code: int
    detail: str
    retry_after: int | None = None
    token: str | None = None
    session_id: str | None = None
    expires_at: datetime | None = None
    absolute_expires_at: datetime | None = None
    device_token: str | None = None
    device_id: str | None = None
    operator: dict[str, Any] | None = None


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    return hash_operator_password(
        "RTM timing equalizer password - no operator account"
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _permissions(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _failure(
    status_code: int = 401,
    detail: str = _GENERIC_LOGIN_ERROR,
    *,
    retry_after: int | None = None,
) -> LoginDecision:
    return LoginDecision(
        ok=False,
        status_code=status_code,
        detail=detail,
        retry_after=retry_after,
    )


def login_operator(
    conn,
    *,
    email: str,
    password: str,
    device_token: str | None,
    context: RequestFingerprint,
    config: OperatorAuthRuntimeConfig,
    now: datetime | None = None,
) -> LoginDecision:
    current = now or _now()
    raw_email = str(email or "").strip()
    login_hash = hash_login_identifier(raw_email, config.hmac_key)

    try:
        from rtm_core.operator_auth_crypto import normalize_operator_email
        normalized_email = normalize_operator_email(raw_email)
    except ValueError:
        verify_operator_password(_dummy_password_hash(), password)
        record_operator_access_event(
            conn,
            context=context,
            event_type="auth.login_failed",
            result="failure",
            auth_method="password",
            retention_days=config.evidence_retention_days,
            login_identifier_sha256=login_hash,
            reason_code="invalid_credentials",
            risk_flags=("invalid_email_format",),
            now=current,
        )
        return _failure()

    operator = find_operator_for_login(conn, normalized_email)
    if not operator:
        verify_operator_password(_dummy_password_hash(), password)
        record_operator_access_event(
            conn,
            context=context,
            event_type="auth.login_failed",
            result="failure",
            auth_method="password",
            retention_days=config.evidence_retention_days,
            login_identifier_sha256=login_hash,
            reason_code="invalid_credentials",
            now=current,
        )
        return _failure()

    operator_id = str(operator["id"])
    status = str(operator["status"] or "")
    password_hash = str(operator["password_hash"] or "")
    if status != "active" or not password_hash:
        verify_operator_password(_dummy_password_hash(), password)
        record_operator_access_event(
            conn,
            context=context,
            event_type="auth.login_denied",
            result="denied",
            auth_method="password",
            retention_days=config.evidence_retention_days,
            operator_id=operator_id,
            login_identifier_sha256=login_hash,
            reason_code="operator_unavailable",
            now=current,
        )
        return _failure()

    locked_until = operator["locked_until"]
    if locked_until is not None and locked_until > current:
        retry_after = max(1, int((locked_until - current).total_seconds()))
        record_operator_access_event(
            conn,
            context=context,
            event_type="auth.login_denied",
            result="denied",
            auth_method="password",
            retention_days=config.evidence_retention_days,
            operator_id=operator_id,
            login_identifier_sha256=login_hash,
            reason_code="operator_locked",
            risk_flags=("operator_locked",),
            now=current,
        )
        return _failure(
            status_code=429,
            detail="Acceso temporalmente bloqueado",
            retry_after=retry_after,
        )

    verification = verify_operator_password(password_hash, password)
    if not verification.valid:
        failed = register_failed_login(
            conn,
            operator_id,
            now=current,
        )
        flags: list[str] = []
        if failed and failed["locked_until"]:
            flags.append("lockout_threshold_reached")
        record_operator_access_event(
            conn,
            context=context,
            event_type="auth.login_failed",
            result="failure",
            auth_method="password",
            retention_days=config.evidence_retention_days,
            operator_id=operator_id,
            login_identifier_sha256=login_hash,
            reason_code="invalid_credentials",
            risk_flags=flags,
            now=current,
        )
        return _failure()

    if bool(operator["mfa_required"]):
        record_operator_access_event(
            conn,
            context=context,
            event_type="auth.login_denied",
            result="denied",
            auth_method="password",
            retention_days=config.evidence_retention_days,
            operator_id=operator_id,
            login_identifier_sha256=login_hash,
            reason_code="mfa_not_available",
            now=current,
        )
        return _failure(
            status_code=409,
            detail="La cuenta requiere una fase de seguridad todavía no activada",
        )

    supplied_device = normalize_device_token(device_token)
    issued_device: str | None = None
    device_flags: list[str] = []
    if supplied_device is None:
        issued_device = generate_device_secret()
        supplied_device = issued_device
        device_flags.append("new_device")
        if device_token:
            device_flags.append("invalid_device_token")

    device_hash = hash_device_secret(supplied_device)
    device = upsert_operator_device(
        conn,
        operator_id=operator_id,
        device_key_sha256=device_hash,
        context=context,
        now=current,
    )
    if device.status == "revoked":
        record_operator_access_event(
            conn,
            context=context,
            event_type="auth.login_denied",
            result="denied",
            auth_method="password",
            retention_days=config.evidence_retention_days,
            operator_id=operator_id,
            device_id=device.device_id,
            device_key_sha256=device_hash,
            login_identifier_sha256=login_hash,
            reason_code="device_revoked",
            risk_flags=("revoked_device",),
            now=current,
        )
        return _failure(
            status_code=403,
            detail="El dispositivo está revocado",
        )
    if device.created and "new_device" not in device_flags:
        device_flags.append("new_device")

    if verification.needs_rehash:
        conn.execute(
            text(
                """
                UPDATE rtm_operators
                SET password_hash=:password_hash,
                    password_algorithm='argon2id',
                    updated_at=NOW()
                WHERE id=CAST(:operator_id AS UUID)
                """
            ),
            {
                "operator_id": operator_id,
                "password_hash": hash_operator_password(password),
            },
        )

    clear_failed_logins(conn, operator_id)
    raw_token = generate_session_token()
    auth_epoch = int(operator["auth_epoch"])
    session_id = create_operator_session(
        conn,
        operator_id=operator_id,
        raw_token=raw_token,
        auth_epoch=auth_epoch,
        now=current,
        device_id=device.device_id,
        ip_address=context.ip_masked,
        user_agent=context.user_agent_summary,
        ip_source=context.ip_source,
        ip_trusted=context.ip_trusted,
        country_code=context.country_code,
        region=context.region,
        city=context.city,
        timezone_name=context.timezone,
        risk_flags_json=json.dumps(
            sorted(set(context.risk_flags) | set(device_flags)),
            ensure_ascii=False,
        ),
        metadata_json=json.dumps(
            {
                "auth_service_version": OPERATOR_AUTH_SERVICE_VERSION,
                "request_id": context.request_id,
            },
            ensure_ascii=False,
        ),
    )
    access_event_id = record_operator_access_event(
        conn,
        context=context,
        event_type="auth.login_succeeded",
        result="success",
        auth_method="password",
        retention_days=config.evidence_retention_days,
        operator_id=operator_id,
        session_id=session_id,
        device_id=device.device_id,
        device_key_sha256=device_hash,
        login_identifier_sha256=login_hash,
        risk_flags=device_flags,
        now=current,
    )
    link_session_to_access_event(
        conn,
        session_id=session_id,
        access_event_id=access_event_id,
        device_id=device.device_id,
    )

    expires_at = current + timedelta(hours=DEFAULT_SESSION_HOURS)
    absolute_expires_at = current + timedelta(
        hours=DEFAULT_ABSOLUTE_SESSION_HOURS
    )
    return LoginDecision(
        ok=True,
        status_code=200,
        detail="Acceso correcto",
        token=raw_token,
        session_id=session_id,
        expires_at=expires_at,
        absolute_expires_at=absolute_expires_at,
        device_token=issued_device,
        device_id=device.device_id,
        operator={
            "id": operator_id,
            "email": str(operator["email"]),
            "display_name": str(operator["display_name"]),
            "role_code": (
                str(operator["role_code"])
                if operator["role_code"]
                else None
            ),
            "permissions": _permissions(operator["permissions"]),
            "must_change_password": bool(
                operator["must_change_password"]
            ),
            "mfa_required": bool(operator["mfa_required"]),
        },
    )


def load_operator_session(
    conn,
    *,
    raw_token: str,
    touch: bool = True,
    now: datetime | None = None,
) -> ActiveOperatorSession | None:
    current = now or _now()
    session = load_active_operator_session(conn, raw_token, now=current)
    if session and touch:
        touch_operator_session(conn, session.session_id, now=current)
    return session


def logout_operator(
    conn,
    *,
    raw_token: str,
    context: RequestFingerprint,
    config: OperatorAuthRuntimeConfig,
    now: datetime | None = None,
) -> bool:
    current = now or _now()
    session = load_active_operator_session(conn, raw_token, now=current)
    if not session:
        return False
    closed = close_operator_session(
        conn,
        session.session_id,
        reason="operator_logout",
    )
    if closed:
        record_operator_access_event(
            conn,
            context=context,
            event_type="auth.logout",
            result="success",
            auth_method="bearer",
            retention_days=config.evidence_retention_days,
            operator_id=session.operator_id,
            session_id=session.session_id,
            reason_code="operator_logout",
            now=current,
        )
    return bool(closed)


__all__ = [
    "LoginDecision",
    "OPERATOR_AUTH_SERVICE_VERSION",
    "load_operator_session",
    "login_operator",
    "logout_operator",
]
