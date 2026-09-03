#!/usr/bin/env python3
"""Smoke HTTP transaccional del panel supervisor RTM.

Crea supervisor y operador sintéticos, valida consultas sanitizadas y prueba
revocación de sesión y dispositivo. Toda la transacción se revierte.
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


async def _run_http_smoke(
    connection,
    report: dict[str, Any],
    *,
    supervisor_email: str,
    supervisor_password: str,
    operator_email: str,
    operator_password: str,
):
    import httpx
    from fastapi import FastAPI
    from rtm_core.operator_auth_router import (
        operator_auth_connection,
        router as auth_router,
    )
    from rtm_core.operator_admin_router import (
        operator_admin_connection,
        router as admin_router,
    )

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(admin_router)

    async def override_connection():
        yield connection

    app.dependency_overrides[
        operator_auth_connection
    ] = override_connection
    app.dependency_overrides[
        operator_admin_connection
    ] = override_connection

    transport = httpx.ASGITransport(app=app)
    common_headers = {
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36 "
            "Edg/151.0.0.0"
        ),
        "x-forwarded-for": "203.0.113.61",
    }

    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://rtm-staging.test",
    ) as client:
        admin_status = await client.get("/ops/admin/status")
        report["checks"]["admin_status_enabled"] = (
            admin_status.status_code == 200
            and admin_status.json().get("operator_admin_enabled") is True
            and admin_status.json().get("operator_creation_available") is False
            and admin_status.json().get("raw_evidence_available") is False
        )

        supervisor_login = await client.post(
            "/ops/auth/login",
            json={
                "email": supervisor_email,
                "password": supervisor_password,
            },
            headers=common_headers,
        )
        supervisor_body = supervisor_login.json()
        supervisor_token = str(supervisor_body.get("token") or "")
        supervisor_device_token = str(
            client.cookies.get("rtm_presenter_device") or ""
        )
        supervisor_session_id = str(
            supervisor_body.get("session_id") or ""
        )
        supervisor_device_id = str(
            supervisor_body.get("device_id") or ""
        )
        report["checks"]["supervisor_login_succeeded"] = (
            supervisor_login.status_code == 200
            and len(supervisor_token) >= 32
            and len(supervisor_device_token) >= 24
        )

        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://rtm-staging.test",
        ) as no_device_client:
            no_device = await no_device_client.get(
                "/ops/admin/operators",
                headers={
                    **common_headers,
                    "Authorization": f"Bearer {supervisor_token}",
                },
            )
        report["checks"]["supervisor_without_device_denied"] = (
            no_device.status_code == 401
        )

        operator_login = await client.post(
            "/ops/auth/login",
            json={
                "email": operator_email,
                "password": operator_password,
            },
            headers=common_headers,
        )
        operator_body = operator_login.json()
        operator_token = str(operator_body.get("token") or "")
        operator_device_token = str(
            client.cookies.get("rtm_presenter_device") or ""
        )
        operator_session_id = str(
            operator_body.get("session_id") or ""
        )
        operator_device_id = str(
            operator_body.get("device_id") or ""
        )
        report["checks"]["operator_login_succeeded"] = (
            operator_login.status_code == 200
            and len(operator_token) >= 32
            and len(operator_device_token) >= 24
        )

        supervisor_headers = {
            **common_headers,
            "Authorization": f"Bearer {supervisor_token}",
            "X-RTM-Device": supervisor_device_token,
        }
        operator_headers = {
            **common_headers,
            "Authorization": f"Bearer {operator_token}",
            "X-RTM-Device": operator_device_token,
        }

        denied = await client.get(
            "/ops/admin/operators",
            headers=operator_headers,
        )
        report["checks"]["non_supervisor_denied"] = (
            denied.status_code == 403
        )

        operators = await client.get(
            "/ops/admin/operators",
            headers=supervisor_headers,
        )
        operators_payload = operators.json()
        rendered_operators = json.dumps(
            operators_payload,
            ensure_ascii=False,
        )
        listed_emails = {
            str(item.get("email") or "")
            for item in operators_payload.get("items", [])
        }
        report["checks"]["operators_listed"] = (
            operators.status_code == 200
            and supervisor_email in listed_emails
            and operator_email in listed_emails
        )
        report["checks"]["operator_list_has_no_secrets"] = all(
            forbidden not in rendered_operators
            for forbidden in (
                "password_hash",
                "token_sha256",
                "device_key_sha256",
            )
        )

        operator_id = str(report["synthetic_ids"]["operator_id"])
        detail = await client.get(
            f"/ops/admin/operators/{operator_id}",
            headers=supervisor_headers,
        )
        report["checks"]["operator_detail_loaded"] = (
            detail.status_code == 200
            and detail.json().get("operator", {}).get("email")
            == operator_email
        )

        sessions = await client.get(
            f"/ops/admin/operators/{operator_id}/sessions",
            headers=supervisor_headers,
        )
        sessions_payload = sessions.json()
        rendered_sessions = json.dumps(
            sessions_payload,
            ensure_ascii=False,
        )
        report["checks"]["sessions_listed_without_token_hash"] = (
            sessions.status_code == 200
            and any(
                str(item.get("id")) == operator_session_id
                for item in sessions_payload.get("items", [])
            )
            and "token_sha256" not in rendered_sessions
        )

        devices = await client.get(
            f"/ops/admin/operators/{operator_id}/devices",
            headers=supervisor_headers,
        )
        devices_payload = devices.json()
        rendered_devices = json.dumps(
            devices_payload,
            ensure_ascii=False,
        )
        report["checks"]["devices_listed_without_opaque_hash"] = (
            devices.status_code == 200
            and any(
                str(item.get("id")) == operator_device_id
                for item in devices_payload.get("items", [])
            )
            and "device_key_sha256" not in rendered_devices
        )

        events = await client.get(
            f"/ops/admin/operators/{operator_id}/access-events",
            headers=supervisor_headers,
        )
        events_payload = events.json()
        rendered_events = json.dumps(
            events_payload,
            ensure_ascii=False,
        )
        report["checks"]["access_history_sanitized"] = (
            events.status_code == 200
            and events_payload.get("raw_evidence_exposed") is False
            and "ip_address" not in rendered_events
            and "raw_user_agent" not in rendered_events
            and "login_identifier_sha256" not in rendered_events
        )

        self_revoke = await client.post(
            f"/ops/admin/sessions/{supervisor_session_id}/revoke",
            json={"reason": "self protection smoke"},
            headers=supervisor_headers,
        )
        report["checks"]["self_session_revocation_blocked"] = (
            self_revoke.status_code == 409
        )

        self_device_revoke = await client.post(
            f"/ops/admin/devices/{supervisor_device_id}/revoke",
            json={"reason": "self device protection smoke"},
            headers=supervisor_headers,
        )
        report["checks"]["self_device_revocation_blocked"] = (
            self_device_revoke.status_code == 409
        )

        revoked_session = await client.post(
            f"/ops/admin/sessions/{operator_session_id}/revoke",
            json={"reason": "synthetic session revocation"},
            headers=supervisor_headers,
        )
        report["checks"]["target_session_revoked"] = (
            revoked_session.status_code == 200
            and revoked_session.json().get("changed") is True
            and revoked_session.json().get("status") == "revoked"
        )

        target_after_session = await client.get(
            "/ops/auth/me",
            headers=operator_headers,
        )
        report["checks"]["revoked_session_rejected"] = (
            target_after_session.status_code == 401
        )

        second_login = await client.post(
            "/ops/auth/login",
            json={
                "email": operator_email,
                "password": operator_password,
            },
            headers={
                **common_headers,
                "X-RTM-Device": operator_device_token,
            },
        )
        second_body = second_login.json()
        second_token = str(second_body.get("token") or "")
        second_headers = {
            **common_headers,
            "Authorization": f"Bearer {second_token}",
            "X-RTM-Device": operator_device_token,
        }
        report["checks"]["known_device_session_recreated"] = (
            second_login.status_code == 200
            and second_body.get("device_token") is None
            and str(second_body.get("device_id") or "")
            == operator_device_id
        )

        revoked_device = await client.post(
            f"/ops/admin/devices/{operator_device_id}/revoke",
            json={"reason": "synthetic device revocation"},
            headers=supervisor_headers,
        )
        report["checks"]["target_device_revoked"] = (
            revoked_device.status_code == 200
            and revoked_device.json().get("status") == "revoked"
            and int(
                revoked_device.json().get("sessions_revoked") or 0
            ) >= 1
        )

        target_after_device = await client.get(
            "/ops/auth/me",
            headers=second_headers,
        )
        report["checks"]["device_sessions_rejected"] = (
            target_after_device.status_code == 401
        )

        final_events = await client.get(
            f"/ops/admin/operators/{operator_id}/access-events",
            headers=supervisor_headers,
        )
        event_types = {
            str(item.get("event_type") or "")
            for item in final_events.json().get("items", [])
        }
        report["checks"]["admin_actions_audited"] = {
            "admin.session_revoked",
            "admin.device_revoked",
        }.issubset(event_types)


def main() -> int:
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_operator_admin_smoke",
        "version": "rtm_operator_admin_smoke_v1_0",
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "synthetic_only": True,
        "transactional": True,
        "operator_creation_public": False,
        "credential_rotation_available": False,
        "legacy_login_unchanged": True,
        "legacy_login_retired_in_staging": True,
        "non_staging_legacy_login_unchanged": True,
        "shared_ops_login_accepted": False,
        "run_id": uuid.uuid4().hex,
        "checks": {},
        "cleanup": {"database_rolled_back": False, "error": None},
        "synthetic_ids": {},
    }
    blockers = _blockers()
    if blockers:
        report["blockers"] = blockers
        _print(report)
        return 2

    env_names = (
        "RTM_ENABLE_OPERATOR_AUTH_V1",
        "RTM_ENABLE_OPERATOR_ADMIN_V1",
        "RTM_OPERATOR_ACCESS_HMAC_KEY",
        "RTM_TRUST_PROXY_HEADERS",
        "RTM_OPERATOR_ACCESS_RETENTION_DAYS",
    )
    old_env = {name: os.environ.get(name) for name in env_names}
    os.environ["RTM_ENABLE_OPERATOR_AUTH_V1"] = "1"
    os.environ["RTM_ENABLE_OPERATOR_ADMIN_V1"] = "1"
    os.environ["RTM_OPERATOR_ACCESS_HMAC_KEY"] = "A" * 64
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
            suffix = report["run_id"][:12]
            supervisor_role_id = str(uuid.uuid4())
            operator_role_id = str(uuid.uuid4())
            supervisor_id = str(uuid.uuid4())
            operator_id = str(uuid.uuid4())
            supervisor_email = (
                f"rtm-staging-admin-smoke-{suffix}@example.com"
            )
            operator_email = (
                f"rtm-staging-operator-smoke-{suffix}@example.com"
            )
            supervisor_password = (
                "RTM synthetic supervisor passphrase 2026!"
            )
            operator_password = (
                "RTM synthetic operator passphrase 2026!"
            )
            report["synthetic_ids"] = {
                "supervisor_role_id": supervisor_role_id,
                "operator_role_id": operator_role_id,
                "supervisor_id": supervisor_id,
                "operator_id": operator_id,
            }

            for role_id, code, permissions in (
                (
                    supervisor_role_id,
                    f"synthetic.admin.supervisor.{suffix}",
                    ["ops.view", "ops.supervise"],
                ),
                (
                    operator_role_id,
                    f"synthetic.admin.operator.{suffix}",
                    ["ops.view"],
                ),
            ):
                connection.execute(
                    text(
                        """
                        INSERT INTO rtm_operator_roles(
                            id, code, name, permissions,
                            system_role, active, created_at, updated_at
                        ) VALUES (
                            CAST(:id AS UUID), :code, :name,
                            CAST(:permissions AS JSONB),
                            FALSE, TRUE, NOW(), NOW()
                        )
                        """
                    ),
                    {
                        "id": role_id,
                        "code": code,
                        "name": code,
                        "permissions": json.dumps(permissions),
                    },
                )

            for (
                operator_uuid,
                email,
                display_name,
                password,
                role_id,
            ) in (
                (
                    supervisor_id,
                    supervisor_email,
                    "RTM STAGING ADMIN SMOKE",
                    supervisor_password,
                    supervisor_role_id,
                ),
                (
                    operator_id,
                    operator_email,
                    "RTM STAGING OPERATOR SMOKE",
                    operator_password,
                    operator_role_id,
                ),
            ):
                connection.execute(
                    text(
                        """
                        INSERT INTO rtm_operators(
                            id, email, display_name, password_hash, status,
                            primary_role_id, must_change_password,
                            mfa_required, profile, failed_login_count,
                            password_algorithm, password_version,
                            auth_epoch, password_changed_at,
                            created_at, updated_at
                        ) VALUES (
                            CAST(:id AS UUID), :email, :display_name,
                            :password_hash, 'active',
                            CAST(:role_id AS UUID), FALSE, FALSE,
                            '{"synthetic": true}'::jsonb, 0,
                            'argon2id', 1, 1, NOW(), NOW(), NOW()
                        )
                        """
                    ),
                    {
                        "id": operator_uuid,
                        "email": email,
                        "display_name": display_name,
                        "password_hash": hash_operator_password(password),
                        "role_id": role_id,
                    },
                )
            report["checks"]["synthetic_operators_inserted"] = True

            asyncio.run(
                _run_http_smoke(
                    connection,
                    report,
                    supervisor_email=supervisor_email,
                    supervisor_password=supervisor_password,
                    operator_email=operator_email,
                    operator_password=operator_password,
                )
            )

            operator_state = connection.execute(
                text(
                    """
                    SELECT
                        (
                            SELECT COUNT(*)
                            FROM rtm_operator_sessions
                            WHERE operator_id=CAST(:operator_id AS UUID)
                              AND status='active'
                        ) AS active_sessions,
                        (
                            SELECT status
                            FROM rtm_operator_devices
                            WHERE operator_id=CAST(:operator_id AS UUID)
                            ORDER BY last_seen_at DESC
                            LIMIT 1
                        ) AS latest_device_status
                    """
                ),
                {"operator_id": operator_id},
            ).mappings().one()
            report["checks"]["database_revocation_state_correct"] = (
                int(operator_state["active_sessions"]) == 0
                and str(operator_state["latest_device_status"]) == "revoked"
            )

            report["tests_ok"] = all(
                bool(value) for value in report["checks"].values()
            )
            report["ok"] = bool(report["tests_ok"])
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
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    _print(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
