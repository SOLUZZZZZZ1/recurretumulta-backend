#!/usr/bin/env python3
"""Smoke transaccional del núcleo de autenticación individual RTM.

Crea únicamente rol, operador, dispositivo y sesiones sintéticas en staging y
revierte toda la transacción. No publica rutas, no crea operadores reales y no
toca casos.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

SMOKE_VERSION = "rtm_operator_auth_smoke_v1_1"
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}


def _flag(name: str) -> bool | None:
    raw = (os.getenv(name) or "").strip().lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return None


def _blockers() -> list[str]:
    blockers: list[str] = []
    environment = (os.getenv("RTM_ENV") or "").strip().lower()
    namespace = (os.getenv("RTM_DATA_NAMESPACE") or "").strip().lower()
    policy = (os.getenv("RTM_SIDE_EFFECT_POLICY") or "").strip().lower()
    if environment != "staging":
        blockers.append("RTM_ENV_must_be_staging")
    if "staging" not in namespace:
        blockers.append("RTM_DATA_NAMESPACE_must_identify_staging")
    if policy != "isolated":
        blockers.append("RTM_SIDE_EFFECT_POLICY_must_be_isolated")
    if _flag("RTM_ALLOW_REAL_CUSTOMER_DATA") is not False:
        blockers.append("RTM_ALLOW_REAL_CUSTOMER_DATA_must_be_false")
    return blockers


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    environment = (os.getenv("RTM_ENV") or "").strip().lower() or "unset"
    run_id = uuid.uuid4().hex
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_operator_auth_smoke",
        "version": SMOKE_VERSION,
        "environment": environment,
        "synthetic_only": True,
        "transactional": True,
        "routes_published": False,
        "login_replaced": False,
        "run_id": run_id,
        "checks": {},
        "cleanup": {"database_rolled_back": False, "error": None},
    }
    blockers = _blockers()
    if blockers:
        report["blockers"] = blockers
        _print(report)
        return 2
    try:
        from sqlalchemy import text
        from database import get_engine
        from rtm_core.operator_auth_crypto import (
            generate_device_secret,
            generate_session_token,
            hash_device_secret,
            hash_operator_password,
            hash_session_token,
            verify_operator_password,
        )
        from rtm_core.operator_auth_repository import (
            clear_failed_logins,
            close_operator_session,
            create_operator_session,
            increment_operator_auth_epoch,
            load_active_operator_session,
            register_failed_login,
        )

        engine = get_engine()
        connection = engine.connect()
        transaction = connection.begin()
        try:
            now = datetime.now(timezone.utc)
            role_id = uuid.uuid4()
            operator_id = uuid.uuid4()
            device_id = str(uuid.uuid4())
            email = f"rtm-auth-smoke-{run_id[:12]}@recurretumulta.eu"
            password = "RTM synthetic passphrase 2026!"
            password_hash = hash_operator_password(password)

            connection.execute(
                text(
                    """
                    INSERT INTO rtm_operator_roles(
                        id, code, name, permissions, system_role, active,
                        created_at, updated_at
                    ) VALUES (
                        :id, :code, 'Rol sintético auth',
                        CAST(:permissions AS JSONB), FALSE, TRUE, NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": role_id,
                    "code": f"synthetic.auth.{run_id[:12]}",
                    "permissions": json.dumps(["ops.view"]),
                },
            )
            report["checks"]["role_inserted"] = True

            connection.execute(
                text(
                    """
                    INSERT INTO rtm_operators(
                        id, email, display_name, password_hash, status,
                        primary_role_id, must_change_password, mfa_required,
                        profile, failed_login_count, password_algorithm,
                        password_version, auth_epoch, password_changed_at,
                        created_at, updated_at
                    ) VALUES (
                        :id, :email, 'RTM STAGING AUTH OPERATOR',
                        :password_hash, 'active', :role_id, FALSE, FALSE,
                        '{"synthetic": true}'::jsonb, 0, 'argon2id', 1, 1,
                        :now, :now, :now
                    )
                    """
                ),
                {
                    "id": operator_id,
                    "email": email,
                    "password_hash": password_hash,
                    "role_id": role_id,
                    "now": now,
                },
            )
            report["checks"]["operator_inserted"] = True
            report["checks"]["password_hash_is_argon2id"] = password_hash.startswith(
                "$argon2id$"
            )
            report["checks"]["correct_password_verified"] = (
                verify_operator_password(password_hash, password).valid
            )
            report["checks"]["wrong_password_rejected"] = not (
                verify_operator_password(password_hash, "wrong-password-value").valid
            )

            device_secret = generate_device_secret()
            device_digest = hash_device_secret(device_secret)
            connection.execute(
                text(
                    """
                    INSERT INTO rtm_operator_devices(
                        id, operator_id, device_key_sha256, status,
                        display_name, device_type, metadata,
                        created_at, updated_at
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:operator_id AS UUID),
                        :device_key_sha256, 'known',
                        'RTM STAGING AUTH DEVICE', 'bot',
                        CAST(:metadata AS JSONB), :now, :now
                    )
                    """
                ),
                {
                    "id": device_id,
                    "operator_id": operator_id,
                    "device_key_sha256": device_digest,
                    "metadata": json.dumps({"synthetic": True}),
                    "now": now,
                },
            )
            report["checks"]["device_inserted"] = True
            stored_device = connection.execute(
                text(
                    """
                    SELECT device_key_sha256, status
                    FROM rtm_operator_devices
                    WHERE id=CAST(:id AS UUID)
                    """
                ),
                {"id": device_id},
            ).fetchone()
            report["checks"]["device_stores_sha256_only"] = bool(
                stored_device
                and stored_device[0] == device_digest
                and stored_device[0] != device_secret
                and len(stored_device[0]) == 64
                and stored_device[1] == "known"
            )

            locked = None
            for _ in range(5):
                locked = register_failed_login(connection, str(operator_id), now=now)
            report["checks"]["lockout_after_threshold"] = bool(
                locked and locked["failed_login_count"] == 5 and locked["locked_until"]
            )
            clear_failed_logins(connection, str(operator_id))
            cleared = connection.execute(
                text(
                    """
                    SELECT failed_login_count, locked_until
                    FROM rtm_operators WHERE id=:id
                    """
                ),
                {"id": operator_id},
            ).fetchone()
            report["checks"]["successful_login_clears_lockout"] = (
                int(cleared[0]) == 0 and cleared[1] is None
            )

            raw_token = generate_session_token()
            digest = hash_session_token(raw_token)
            session_id = create_operator_session(
                connection,
                operator_id=str(operator_id),
                raw_token=raw_token,
                auth_epoch=1,
                now=now,
                device_id=device_id,
                metadata_json='{"synthetic": true}',
            )
            stored = connection.execute(
                text(
                    """
                    SELECT token_sha256 FROM rtm_operator_sessions
                    WHERE id=CAST(:id AS UUID)
                    """
                ),
                {"id": session_id},
            ).scalar_one()
            report["checks"]["session_stores_sha256_only"] = (
                stored == digest and stored != raw_token and len(stored) == 64
            )
            session = load_active_operator_session(connection, raw_token, now=now)
            report["checks"]["active_session_loaded"] = bool(
                session
                and session.operator_id == str(operator_id)
                and session.device_id == device_id
            )

            closed = close_operator_session(connection, session_id)
            report["checks"]["session_logout_closes"] = bool(closed)
            report["checks"]["closed_session_rejected"] = (
                load_active_operator_session(connection, raw_token, now=now) is None
            )

            second_token = generate_session_token()
            second_session_id = create_operator_session(
                connection,
                operator_id=str(operator_id),
                raw_token=second_token,
                auth_epoch=1,
                now=now,
                device_id=device_id,
            )
            report["checks"]["second_session_created"] = bool(second_session_id)
            second_session = load_active_operator_session(
                connection,
                second_token,
                now=now,
            )
            report["checks"]["second_session_active_before_epoch_change"] = bool(
                second_session
                and second_session.operator_id == str(operator_id)
                and second_session.device_id == device_id
            )
            increment_operator_auth_epoch(connection, str(operator_id))
            report["checks"]["auth_epoch_invalidates_sessions"] = (
                load_active_operator_session(connection, second_token, now=now) is None
            )

            all_checks = all(bool(value) for value in report["checks"].values())
            report["tests_ok"] = all_checks
            report["ok"] = all_checks
            report["synthetic_ids"] = {
                "role_id": str(role_id),
                "operator_id": str(operator_id),
                "device_id": device_id,
                "session_id": str(session_id),
                "second_session_id": str(second_session_id),
            }
        finally:
            transaction.rollback()
            connection.close()
            report["cleanup"]["database_rolled_back"] = True
        exit_code = 0 if report["ok"] else 1
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["ok"] = False
        report["tests_ok"] = False
        report["cleanup"]["error"] = str(exc)
        exit_code = 1
    _print(report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
