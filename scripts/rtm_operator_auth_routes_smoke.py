#!/usr/bin/env python3
"""Smoke HTTP transaccional de las rutas individuales de operadores RTM.

Crea un operador sintético dentro de una transacción que siempre se revierte.
Comprueba login fallido, login correcto, dispositivo opaco, /me, heartbeat,
logout, rechazo posterior y persistencia exclusiva del hash del token.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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


async def _run_http_smoke(connection, report: dict[str, Any], password: str):
    import httpx
    from fastapi import FastAPI
    from rtm_core.operator_auth_router import (
        operator_auth_connection,
        router,
    )

    app = FastAPI()
    app.include_router(router)

    async def override_connection():
        yield connection

    app.dependency_overrides[operator_auth_connection] = override_connection
    transport = httpx.ASGITransport(app=app)
    headers = {
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36 "
            "Edg/151.0.0.0"
        ),
        "x-forwarded-for": "203.0.113.44",
        "x-vercel-ip-country": "ES",
        "x-vercel-ip-country-region": "CT",
        "x-vercel-ip-city": "Barcelona",
        "x-vercel-ip-timezone": "Europe/Madrid",
    }
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://rtm-staging.test",
    ) as client:
        status = await client.get("/ops/auth/status")
        report["checks"]["status_route_enabled"] = (
            status.status_code == 200
            and status.json().get("individual_login_enabled") is True
            and status.json().get("legacy_login_unchanged") is True
        )

        wrong = await client.post(
            "/ops/auth/login",
            json={
                "email": report["synthetic_email"],
                "password": "wrong-password-value",
            },
            headers=headers,
        )
        report["checks"]["wrong_password_rejected"] = wrong.status_code == 401

        login = await client.post(
            "/ops/auth/login",
            json={
                "email": report["synthetic_email"],
                "password": password,
            },
            headers=headers,
        )
        body = login.json()
        token = str(body.get("token") or "")
        device_token = str(body.get("device_token") or "")
        report["checks"]["login_succeeded"] = (
            login.status_code == 200
            and len(token) >= 32
            and len(device_token) >= 24
        )

        auth_headers = {
            **headers,
            "Authorization": f"Bearer {token}",
            "X-RTM-Device": device_token,
        }
        me = await client.get("/ops/auth/me", headers=auth_headers)
        report["checks"]["me_loaded"] = (
            me.status_code == 200
            and me.json().get("operator", {}).get("email")
            == report["synthetic_email"]
        )

        heartbeat = await client.post(
            "/ops/auth/heartbeat",
            headers=auth_headers,
        )
        report["checks"]["heartbeat_succeeded"] = (
            heartbeat.status_code == 200
        )

        logout = await client.post(
            "/ops/auth/logout",
            headers=auth_headers,
        )
        report["checks"]["logout_succeeded"] = logout.status_code == 200

        after_logout = await client.get(
            "/ops/auth/me",
            headers=auth_headers,
        )
        report["checks"]["closed_session_rejected"] = (
            after_logout.status_code == 401
        )

        second = await client.post(
            "/ops/auth/login",
            json={
                "email": report["synthetic_email"],
                "password": password,
            },
            headers={**headers, "X-RTM-Device": device_token},
        )
        second_body = second.json()
        report["checks"]["known_device_reused"] = (
            second.status_code == 200
            and second_body.get("device_token") is None
        )
        second_token = str(second_body.get("token") or "")
        if second_token:
            await client.post(
                "/ops/auth/logout",
                headers={
                    **headers,
                    "Authorization": f"Bearer {second_token}",
                    "X-RTM-Device": device_token,
                },
            )
        return token


def main() -> int:
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_operator_auth_routes_smoke",
        "version": "rtm_operator_auth_routes_smoke_v1_0",
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "synthetic_only": True,
        "transactional": True,
        "legacy_login_unchanged": True,
        "operator_creation_available": False,
        "run_id": uuid.uuid4().hex,
        "checks": {},
        "cleanup": {"database_rolled_back": False, "error": None},
    }
    blockers = _blockers()
    if blockers:
        report["blockers"] = blockers
        _print(report)
        return 2

    old_env = {
        key: os.environ.get(key)
        for key in (
            "RTM_ENABLE_OPERATOR_AUTH_V1",
            "RTM_OPERATOR_ACCESS_HMAC_KEY",
            "RTM_TRUST_PROXY_HEADERS",
            "RTM_OPERATOR_ACCESS_RETENTION_DAYS",
        )
    }
    os.environ["RTM_ENABLE_OPERATOR_AUTH_V1"] = "1"
    os.environ["RTM_OPERATOR_ACCESS_HMAC_KEY"] = "S" * 64
    os.environ["RTM_TRUST_PROXY_HEADERS"] = "1"
    os.environ["RTM_OPERATOR_ACCESS_RETENTION_DAYS"] = "180"

    try:
        from sqlalchemy import text
        from database import get_engine
        from rtm_core.operator_auth_crypto import hash_operator_password

        engine = get_engine()
        connection = engine.connect()
        transaction = connection.begin()
        try:
            role_id = str(uuid.uuid4())
            operator_id = str(uuid.uuid4())
            email = (
                f"rtm-routes-smoke-{report['run_id'][:12]}"
                "@recurretumulta.eu"
            )
            password = "RTM synthetic routes passphrase 2026!"
            report["synthetic_email"] = email

            connection.execute(
                text(
                    """
                    INSERT INTO rtm_operator_roles(
                        id, code, name, permissions, system_role, active,
                        created_at, updated_at
                    ) VALUES (
                        CAST(:id AS UUID), :code, 'Rol sintético rutas',
                        CAST(:permissions AS JSONB), FALSE, TRUE, NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": role_id,
                    "code": f"synthetic.routes.{report['run_id'][:12]}",
                    "permissions": json.dumps(["ops.view"]),
                },
            )
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
                        CAST(:id AS UUID), :email,
                        'RTM STAGING ROUTES OPERATOR',
                        :password_hash, 'active', CAST(:role_id AS UUID),
                        FALSE, FALSE, '{"synthetic": true}'::jsonb,
                        0, 'argon2id', 1, 1, NOW(), NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": operator_id,
                    "email": email,
                    "password_hash": hash_operator_password(password),
                    "role_id": role_id,
                },
            )
            report["checks"]["synthetic_operator_inserted"] = True

            raw_token = asyncio.run(
                _run_http_smoke(connection, report, password)
            )
            stored_tokens = [
                str(row[0])
                for row in connection.execute(
                    text(
                        """
                        SELECT token_sha256
                        FROM rtm_operator_sessions
                        WHERE operator_id=CAST(:operator_id AS UUID)
                        """
                    ),
                    {"operator_id": operator_id},
                ).fetchall()
            ]
            report["checks"]["raw_token_never_persisted"] = bool(
                stored_tokens
                and raw_token not in stored_tokens
                and all(len(item) == 64 for item in stored_tokens)
            )
            event_types = {
                str(row[0])
                for row in connection.execute(
                    text(
                        """
                        SELECT event_type
                        FROM rtm_operator_access_events
                        WHERE operator_id=CAST(:operator_id AS UUID)
                        """
                    ),
                    {"operator_id": operator_id},
                ).fetchall()
            }
            report["checks"]["access_history_written"] = {
                "auth.login_failed",
                "auth.login_succeeded",
                "auth.logout",
            }.issubset(event_types)
            device_count = connection.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM rtm_operator_devices
                    WHERE operator_id=CAST(:operator_id AS UUID)
                    """
                ),
                {"operator_id": operator_id},
            ).scalar_one()
            report["checks"]["single_opaque_device_recorded"] = (
                int(device_count) == 1
            )
            evidence_count = connection.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM rtm_operator_access_evidence e
                    JOIN rtm_operator_access_events a
                      ON a.id=e.access_event_id
                    WHERE a.operator_id=CAST(:operator_id AS UUID)
                    """
                ),
                {"operator_id": operator_id},
            ).scalar_one()
            report["checks"]["sensitive_evidence_separated"] = (
                int(evidence_count) >= 3
            )

            report["tests_ok"] = all(
                bool(value) for value in report["checks"].values()
            )
            report["ok"] = bool(report["tests_ok"])
            report["synthetic_ids"] = {
                "role_id": role_id,
                "operator_id": operator_id,
            }
        finally:
            transaction.rollback()
            connection.close()
            report["cleanup"]["database_rolled_back"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["tests_ok"] = False
        report["ok"] = False
        report["cleanup"]["error"] = str(exc)
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    _print(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
