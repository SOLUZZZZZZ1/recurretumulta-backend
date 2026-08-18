#!/usr/bin/env python3
"""Smoke transaccional del historial de accesos RTM en staging.

Crea solo rol, operador, dispositivo, sesión y eventos sintéticos dentro de una
transacción que siempre se revierte. No toca casos, documentos ni operadores
reales. Comprueba deduplicación de dispositivo, auditoría append-only y
retención controlada de la IP completa.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))


SMOKE_VERSION = "rtm_operator_access_smoke_v1_0"

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


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    environment = (os.getenv("RTM_ENV") or "").strip().lower() or "unset"
    run_id = uuid.uuid4().hex
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_operator_access_smoke",
        "version": SMOKE_VERSION,
        "environment": environment,
        "synthetic_only": True,
        "transactional": True,
        "run_id": run_id,
        "checks": {},
        "cleanup": {
            "database_rolled_back": False,
            "error": None,
        },
    }

    blockers = _blockers()
    if blockers:
        report["blockers"] = blockers
        _print(report)
        return 2

    try:
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError, IntegrityError

        from database import get_engine

        engine = get_engine()
        connection = engine.connect()
        transaction = connection.begin()
        try:
            now = datetime.now(timezone.utc)
            role_id = uuid.uuid4()
            operator_id = uuid.uuid4()
            device_id = uuid.uuid4()
            access_event_id = uuid.uuid4()
            evidence_id = uuid.uuid4()
            session_id = uuid.uuid4()
            expired_event_id = uuid.uuid4()
            expired_evidence_id = uuid.uuid4()

            email = (
                f"rtm-access-smoke-{run_id[:12]}@recurretumulta.eu"
            )
            device_key = _sha256(f"device:{run_id}")
            login_hash = _sha256(email.lower())
            ip_hash = _sha256(f"staging-ip:203.0.113.10")
            token_hash = _sha256(f"session-token:{run_id}")

            connection.execute(
                text(
                    """
                    INSERT INTO rtm_operator_roles(
                        id, code, name, permissions, system_role, active,
                        created_at, updated_at
                    ) VALUES (
                        :id, :code, 'Rol sintético de acceso',
                        CAST(:permissions AS JSONB), FALSE, TRUE, NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": role_id,
                    "code": f"synthetic.access.{run_id[:12]}",
                    "permissions": json.dumps(["access.history.view"]),
                },
            )
            report["checks"]["role_inserted"] = True

            connection.execute(
                text(
                    """
                    INSERT INTO rtm_operators(
                        id, email, display_name, status, primary_role_id,
                        must_change_password, mfa_required, profile,
                        created_at, updated_at
                    ) VALUES (
                        :id, :email, 'RTM STAGING ACCESS OPERATOR', 'active',
                        :role_id, FALSE, FALSE, CAST(:profile AS JSONB),
                        NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": operator_id,
                    "email": email,
                    "role_id": role_id,
                    "profile": json.dumps({"synthetic": True}),
                },
            )
            report["checks"]["operator_inserted"] = True

            connection.execute(
                text(
                    """
                    INSERT INTO rtm_operator_devices(
                        id, operator_id, device_key_sha256, status,
                        display_name, device_type, os_family, os_version,
                        browser_family, browser_version, first_seen_at,
                        last_seen_at, first_ip_hash_sha256,
                        last_ip_hash_sha256, metadata, created_at, updated_at
                    ) VALUES (
                        :id, :operator_id, :device_key, 'known',
                        'Dispositivo sintético', 'desktop', 'Windows', '11',
                        'Edge', '151', :now, :now, :ip_hash, :ip_hash,
                        CAST(:metadata AS JSONB), :now, :now
                    )
                    """
                ),
                {
                    "id": device_id,
                    "operator_id": operator_id,
                    "device_key": device_key,
                    "now": now,
                    "ip_hash": ip_hash,
                    "metadata": json.dumps({"synthetic": True}),
                },
            )
            report["checks"]["device_inserted"] = True

            duplicate_device_blocked = False
            try:
                with connection.begin_nested():
                    connection.execute(
                        text(
                            """
                            INSERT INTO rtm_operator_devices(
                                operator_id, device_key_sha256, status,
                                device_type
                            ) VALUES (
                                :operator_id, :device_key, 'known', 'desktop'
                            )
                            """
                        ),
                        {
                            "operator_id": operator_id,
                            "device_key": device_key,
                        },
                    )
            except IntegrityError:
                duplicate_device_blocked = True
            report["checks"][
                "operator_device_dedupe_enforced"
            ] = duplicate_device_blocked

            connection.execute(
                text(
                    """
                    INSERT INTO rtm_operator_access_events(
                        id, operator_id, session_id, device_id, event_type,
                        result, auth_method, occurred_at,
                        login_identifier_sha256, ip_masked, ip_hash_sha256,
                        ip_family, ip_source, ip_trusted, device_key_sha256,
                        device_type, os_family, os_version, browser_family,
                        browser_version, country_code, region, city, timezone,
                        location_source, request_id, risk_flags, metadata,
                        created_at
                    ) VALUES (
                        :id, :operator_id, :session_id, :device_id,
                        'auth.login_succeeded', 'success', 'password', :now,
                        :login_hash, '203.0.113.xxx', :ip_hash, 4,
                        'x_forwarded_for', TRUE, :device_key, 'desktop',
                        'Windows', '11', 'Edge', '151', 'ES',
                        'Catalunya', 'Barcelona', 'Europe/Madrid',
                        'trusted_proxy_header', :request_id,
                        CAST(:risk_flags AS JSONB), CAST(:metadata AS JSONB),
                        :now
                    )
                    """
                ),
                {
                    "id": access_event_id,
                    "operator_id": operator_id,
                    "session_id": session_id,
                    "device_id": device_id,
                    "now": now,
                    "login_hash": login_hash,
                    "ip_hash": ip_hash,
                    "device_key": device_key,
                    "request_id": f"access-smoke-{run_id}",
                    "risk_flags": json.dumps(["new_device"]),
                    "metadata": json.dumps({"synthetic": True}),
                },
            )
            report["checks"]["access_event_inserted"] = True

            connection.execute(
                text(
                    """
                    INSERT INTO rtm_operator_access_evidence(
                        id, access_event_id, ip_address, raw_user_agent,
                        trusted_headers, retention_until, created_at
                    ) VALUES (
                        :id, :access_event_id, CAST(:ip_address AS INET),
                        :user_agent, CAST(:headers AS JSONB),
                        :retention_until, :now
                    )
                    """
                ),
                {
                    "id": evidence_id,
                    "access_event_id": access_event_id,
                    "ip_address": "203.0.113.10",
                    "user_agent": (
                        "RTM-STAGING-SYNTHETIC/1.0 "
                        "(Windows 11; Edge 151)"
                    ),
                    "headers": json.dumps(
                        {
                            "x-forwarded-for": "203.0.113.10",
                            "x-vercel-ip-country": "ES",
                        }
                    ),
                    "retention_until": now + timedelta(days=180),
                    "now": now,
                },
            )
            report["checks"]["raw_evidence_inserted"] = True

            connection.execute(
                text(
                    """
                    INSERT INTO rtm_operator_sessions(
                        id, operator_id, token_sha256, status, login_at,
                        last_seen_at, expires_at, ip_address, user_agent,
                        metadata, created_at, device_id,
                        login_access_event_id, ip_source, ip_trusted,
                        country_code, region, city, timezone, risk_flags
                    ) VALUES (
                        :id, :operator_id, :token_hash, 'active', :now,
                        :now, :expires_at, '203.0.113.10',
                        'RTM-STAGING-SYNTHETIC/1.0',
                        CAST(:metadata AS JSONB), :now, :device_id,
                        :access_event_id, 'x_forwarded_for', TRUE, 'ES',
                        'Catalunya', 'Barcelona', 'Europe/Madrid',
                        CAST(:risk_flags AS JSONB)
                    )
                    """
                ),
                {
                    "id": session_id,
                    "operator_id": operator_id,
                    "token_hash": token_hash,
                    "now": now,
                    "expires_at": now + timedelta(hours=1),
                    "metadata": json.dumps({"synthetic": True}),
                    "device_id": device_id,
                    "access_event_id": access_event_id,
                    "risk_flags": json.dumps(["new_device"]),
                },
            )
            report["checks"]["session_linked_to_access"] = True

            event_update_blocked = False
            try:
                with connection.begin_nested():
                    connection.execute(
                        text(
                            """
                            UPDATE rtm_operator_access_events
                            SET reason_detail='mutated'
                            WHERE id=:id
                            """
                        ),
                        {"id": access_event_id},
                    )
            except DBAPIError as exc:
                event_update_blocked = "append-only" in str(exc).lower()
            report["checks"][
                "access_event_update_blocked"
            ] = event_update_blocked

            event_delete_blocked = False
            try:
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "DELETE FROM rtm_operator_access_events WHERE id=:id"
                        ),
                        {"id": access_event_id},
                    )
            except DBAPIError as exc:
                event_delete_blocked = "append-only" in str(exc).lower()
            report["checks"][
                "access_event_delete_blocked"
            ] = event_delete_blocked

            evidence_update_blocked = False
            try:
                with connection.begin_nested():
                    connection.execute(
                        text(
                            """
                            UPDATE rtm_operator_access_evidence
                            SET raw_user_agent='mutated'
                            WHERE id=:id
                            """
                        ),
                        {"id": evidence_id},
                    )
            except DBAPIError as exc:
                evidence_update_blocked = "immutable" in str(exc).lower()
            report["checks"][
                "raw_evidence_update_blocked"
            ] = evidence_update_blocked

            premature_delete_blocked = False
            try:
                with connection.begin_nested():
                    connection.execute(
                        text(
                            """
                            DELETE FROM rtm_operator_access_evidence
                            WHERE id=:id
                            """
                        ),
                        {"id": evidence_id},
                    )
            except DBAPIError as exc:
                premature_delete_blocked = (
                    "retention-protected" in str(exc).lower()
                )
            report["checks"][
                "raw_evidence_premature_delete_blocked"
            ] = premature_delete_blocked

            connection.execute(
                text(
                    """
                    INSERT INTO rtm_operator_access_events(
                        id, operator_id, event_type, result, occurred_at,
                        ip_masked, ip_hash_sha256, ip_family, ip_source,
                        ip_trusted, device_type, risk_flags, metadata,
                        created_at
                    ) VALUES (
                        :id, :operator_id, 'security.retention_fixture',
                        'success', :created_at, '198.51.100.xxx',
                        :ip_hash, 4, 'direct', TRUE, 'other',
                        '[]'::jsonb, '{"synthetic": true}'::jsonb, :created_at
                    )
                    """
                ),
                {
                    "id": expired_event_id,
                    "operator_id": operator_id,
                    "created_at": now - timedelta(days=2),
                    "ip_hash": _sha256(
                        "staging-ip:198.51.100.20"
                    ),
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO rtm_operator_access_evidence(
                        id, access_event_id, ip_address, raw_user_agent,
                        trusted_headers, retention_until, created_at
                    ) VALUES (
                        :id, :access_event_id,
                        CAST('198.51.100.20' AS INET),
                        'RTM-EXPIRED-SYNTHETIC/1.0', '{}'::jsonb,
                        :retention_until, :created_at
                    )
                    """
                ),
                {
                    "id": expired_evidence_id,
                    "access_event_id": expired_event_id,
                    "created_at": now - timedelta(days=2),
                    "retention_until": now - timedelta(days=1),
                },
            )
            connection.execute(
                text(
                    """
                    SELECT set_config(
                        'rtm.operator_access_evidence_purge',
                        'enabled',
                        TRUE
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    DELETE FROM rtm_operator_access_evidence
                    WHERE id=:id
                    """
                ),
                {"id": expired_evidence_id},
            )
            remaining = connection.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM rtm_operator_access_evidence
                    WHERE id=:id
                    """
                ),
                {"id": expired_evidence_id},
            ).scalar_one()
            report["checks"][
                "expired_raw_evidence_purge_allowed"
            ] = int(remaining) == 0

            all_checks = all(
                bool(value) for value in report["checks"].values()
            )
            report["tests_ok"] = all_checks
            report["ok"] = all_checks
            report["synthetic_ids"] = {
                "role_id": str(role_id),
                "operator_id": str(operator_id),
                "device_id": str(device_id),
                "access_event_id": str(access_event_id),
                "evidence_id": str(evidence_id),
                "session_id": str(session_id),
                "expired_event_id": str(expired_event_id),
                "expired_evidence_id": str(expired_evidence_id),
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
