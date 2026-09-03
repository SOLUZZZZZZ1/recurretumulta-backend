#!/usr/bin/env python3
"""Smoke HTTP transaccional del ciclo de vida de operadores RTM."""

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
    if (os.getenv("RTM_ENV") or "").strip().lower() != "staging":
        blockers.append("RTM_ENV_must_be_staging")
    if "staging" not in (os.getenv("RTM_DATA_NAMESPACE") or "").lower():
        blockers.append("RTM_DATA_NAMESPACE_must_identify_staging")
    if (os.getenv("RTM_SIDE_EFFECT_POLICY") or "").strip().lower() != "isolated":
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
    target_email: str,
    temporary_password: str,
    first_password: str,
    rotated_password: str,
    final_password: str,
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
    from rtm_core.operator_lifecycle_router import (
        operator_lifecycle_connection,
        router as lifecycle_router,
    )

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(lifecycle_router)

    async def override_connection():
        yield connection

    app.dependency_overrides[
        operator_auth_connection
    ] = override_connection
    app.dependency_overrides[
        operator_admin_connection
    ] = override_connection
    app.dependency_overrides[
        operator_lifecycle_connection
    ] = override_connection

    transport = httpx.ASGITransport(app=app)
    common_headers = {
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36 "
            "Edg/151.0.0.0"
        ),
        "x-forwarded-for": "203.0.113.72",
    }

    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://rtm-staging.test",
    ) as client:
        lifecycle_status = await client.get(
            "/ops/admin/lifecycle/status"
        )
        report["checks"]["lifecycle_status_enabled"] = (
            lifecycle_status.status_code == 200
            and lifecycle_status.json().get(
                "operator_lifecycle_enabled"
            ) is True
            and lifecycle_status.json().get(
                "public_registration_available"
            ) is False
            and lifecycle_status.json().get("passwords_returned") is False
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
        supervisor_device = str(
            client.cookies.get("__Host-rtm_presenter_device") or ""
        )
        supervisor_session_id = str(
            supervisor_body.get("session_id") or ""
        )
        supervisor_id = str(
            supervisor_body.get("operator", {}).get("id") or ""
        )
        report["checks"]["supervisor_login_succeeded"] = (
            supervisor_login.status_code == 200
            and len(supervisor_token) >= 32
        )
        supervisor_headers = {
            **common_headers,
            "Authorization": f"Bearer {supervisor_token}",
            "X-RTM-Device": supervisor_device,
        }

        supervisor_reauth = await client.post(
            "/ops/auth/reauthenticate",
            json={"password": supervisor_password},
            headers=supervisor_headers,
        )
        report["checks"]["supervisor_reauthenticated"] = (
            supervisor_reauth.status_code == 200
            and supervisor_reauth.json().get("status") == "reauthenticated"
        )

        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://rtm-staging.test",
        ) as no_device_client:
            no_device = await no_device_client.post(
                "/ops/admin/operators",
                json={
                    "email": target_email,
                    "display_name": "RTM STAGING TARGET",
                    "temporary_password": temporary_password,
                },
                headers={
                    **common_headers,
                    "Authorization": f"Bearer {supervisor_token}",
                },
            )
        report["checks"]["supervisor_without_device_denied"] = (
            no_device.status_code == 401
        )

        unauthenticated = await client.post(
            "/ops/admin/operators",
            json={
                "email": target_email,
                "display_name": "RTM STAGING TARGET",
                "temporary_password": temporary_password,
            },
        )
        report["checks"]["public_creation_blocked"] = (
            unauthenticated.status_code == 401
        )

        created = await client.post(
            "/ops/admin/operators",
            json={
                "email": target_email,
                "display_name": "RTM STAGING TARGET",
                "temporary_password": temporary_password,
            },
            headers=supervisor_headers,
        )
        created_payload = created.json()
        target_id = str(
            created_payload.get("operator", {}).get("operator_id") or ""
        )
        report["synthetic_ids"]["target_operator_id"] = target_id
        report["checks"]["controlled_operator_created"] = (
            created.status_code == 201
            and created_payload.get("operator", {}).get("role_code")
            == "rtm.operator"
            and created_payload.get("operator", {}).get(
                "must_change_password"
            ) is True
            and created_payload.get(
                "temporary_password_returned"
            ) is False
        )
        rendered_create = json.dumps(
            created_payload,
            ensure_ascii=False,
        )
        report["checks"]["creation_response_has_no_password"] = all(
            secret not in rendered_create
            for secret in (
                temporary_password,
                "password_hash",
            )
        )

        duplicate = await client.post(
            "/ops/admin/operators",
            json={
                "email": target_email,
                "display_name": "RTM STAGING TARGET",
                "temporary_password": temporary_password,
            },
            headers=supervisor_headers,
        )
        report["checks"]["duplicate_operator_rejected"] = (
            duplicate.status_code == 409
        )

        target_login = await client.post(
            "/ops/auth/login",
            json={
                "email": target_email,
                "password": temporary_password,
            },
            headers=common_headers,
        )
        target_body = target_login.json()
        target_token = str(target_body.get("token") or "")
        target_device = str(
            client.cookies.get("__Host-rtm_presenter_device") or ""
        )
        report["checks"]["temporary_password_login_succeeded"] = (
            target_login.status_code == 200
            and target_body.get("operator", {}).get(
                "must_change_password"
            ) is True
        )
        target_headers = {
            **common_headers,
            "Authorization": f"Bearer {target_token}",
            "X-RTM-Device": target_device,
        }

        denied = await client.post(
            "/ops/admin/operators",
            json={
                "email": f"rtm-staging-denied-{report['run_id'][:8]}@example.com",
                "display_name": "RTM DENIED",
                "temporary_password": temporary_password,
            },
            headers=target_headers,
        )
        report["checks"]["non_supervisor_denied"] = (
            denied.status_code == 403
        )

        first_change = await client.post(
            "/ops/auth/password/change",
            json={
                "current_password": temporary_password,
                "new_password": first_password,
                "reason": "Synthetic first password change",
            },
            headers=target_headers,
        )
        report["checks"]["self_password_change_succeeded"] = (
            first_change.status_code == 200
            and first_change.json().get(
                "reauthentication_required"
            ) is True
            and first_change.json().get("password_returned") is False
            and first_change.json().get(
                "shared_ops_login_accepted"
            ) is False
            and first_change.json().get(
                "legacy_login_retired_in_staging"
            ) is True
            and first_change.json().get(
                "non_staging_legacy_login_unchanged"
            ) is True
        )
        old_session = await client.get(
            "/ops/auth/me",
            headers=target_headers,
        )
        report["checks"]["self_change_revoked_old_session"] = (
            old_session.status_code == 401
        )
        old_password = await client.post(
            "/ops/auth/login",
            json={
                "email": target_email,
                "password": temporary_password,
            },
            headers=common_headers,
        )
        report["checks"]["temporary_password_rejected_after_change"] = (
            old_password.status_code == 401
        )

        target_login_2 = await client.post(
            "/ops/auth/login",
            json={
                "email": target_email,
                "password": first_password,
            },
            headers={
                **common_headers,
                "X-RTM-Device": target_device,
            },
        )
        target_body_2 = target_login_2.json()
        target_token_2 = str(target_body_2.get("token") or "")
        target_headers_2 = {
            **common_headers,
            "Authorization": f"Bearer {target_token_2}",
            "X-RTM-Device": target_device,
        }
        report["checks"]["new_password_login_succeeded"] = (
            target_login_2.status_code == 200
            and target_body_2.get("operator", {}).get(
                "must_change_password"
            ) is False
        )

        role_change = await client.post(
            f"/ops/admin/operators/{target_id}/role",
            json={
                "role_code": "rtm.supervisor",
                "reason": "Synthetic supervisor promotion",
            },
            headers=supervisor_headers,
        )
        report["checks"]["role_assignment_succeeded"] = (
            role_change.status_code == 200
            and role_change.json().get("operator", {}).get("role_code")
            == "rtm.supervisor"
        )
        role_old_session = await client.get(
            "/ops/auth/me",
            headers=target_headers_2,
        )
        report["checks"]["role_change_revoked_sessions"] = (
            role_old_session.status_code == 401
        )

        target_login_3 = await client.post(
            "/ops/auth/login",
            json={
                "email": target_email,
                "password": first_password,
            },
            headers={
                **common_headers,
                "X-RTM-Device": target_device,
            },
        )
        target_body_3 = target_login_3.json()
        target_token_3 = str(target_body_3.get("token") or "")
        target_headers_3 = {
            **common_headers,
            "Authorization": f"Bearer {target_token_3}",
            "X-RTM-Device": target_device,
        }
        admin_access = await client.get(
            "/ops/admin/operators",
            headers=target_headers_3,
        )
        report["checks"]["promoted_supervisor_can_read_admin"] = (
            admin_access.status_code == 200
        )

        suspended = await client.post(
            f"/ops/admin/operators/{target_id}/suspend",
            json={"reason": "Synthetic suspension"},
            headers=supervisor_headers,
        )
        report["checks"]["suspension_succeeded"] = (
            suspended.status_code == 200
            and suspended.json().get("operator", {}).get("status")
            == "suspended"
        )
        suspended_session = await client.get(
            "/ops/auth/me",
            headers=target_headers_3,
        )
        suspended_login = await client.post(
            "/ops/auth/login",
            json={
                "email": target_email,
                "password": first_password,
            },
            headers=common_headers,
        )
        report["checks"]["suspended_operator_denied"] = (
            suspended_session.status_code == 401
            and suspended_login.status_code == 401
        )

        reactivated = await client.post(
            f"/ops/admin/operators/{target_id}/reactivate",
            json={"reason": "Synthetic reactivation"},
            headers=supervisor_headers,
        )
        report["checks"]["reactivation_succeeded"] = (
            reactivated.status_code == 200
            and reactivated.json().get("operator", {}).get("status")
            == "active"
        )
        target_login_4 = await client.post(
            "/ops/auth/login",
            json={
                "email": target_email,
                "password": first_password,
            },
            headers={
                **common_headers,
                "X-RTM-Device": target_device,
            },
        )
        target_body_4 = target_login_4.json()
        target_token_4 = str(target_body_4.get("token") or "")
        target_headers_4 = {
            **common_headers,
            "Authorization": f"Bearer {target_token_4}",
            "X-RTM-Device": target_device,
        }
        report["checks"]["reactivated_operator_can_login"] = (
            target_login_4.status_code == 200
        )

        rotated = await client.post(
            f"/ops/admin/operators/{target_id}/credentials/rotate",
            json={
                "new_password": rotated_password,
                "must_change_password": False,
                "reason": "Synthetic formal supervisor rotation",
            },
            headers=supervisor_headers,
        )
        rotated_payload = rotated.json()
        report["checks"]["formal_rotation_succeeded"] = (
            rotated.status_code == 200
            and rotated_payload.get("password_returned") is False
            and rotated_payload.get("operator", {}).get(
                "must_change_password"
            ) is False
        )
        rendered_rotation = json.dumps(
            rotated_payload,
            ensure_ascii=False,
        )
        report["checks"]["rotation_response_has_no_password"] = (
            rotated_password not in rendered_rotation
            and "password_hash" not in rendered_rotation
        )
        rotated_old_session = await client.get(
            "/ops/auth/me",
            headers=target_headers_4,
        )
        old_after_rotation = await client.post(
            "/ops/auth/login",
            json={
                "email": target_email,
                "password": first_password,
            },
            headers=common_headers,
        )
        report["checks"]["rotation_revoked_old_access"] = (
            rotated_old_session.status_code == 401
            and old_after_rotation.status_code == 401
        )

        target_login_5 = await client.post(
            "/ops/auth/login",
            json={
                "email": target_email,
                "password": rotated_password,
            },
            headers={
                **common_headers,
                "X-RTM-Device": target_device,
            },
        )
        target_body_5 = target_login_5.json()
        target_token_5 = str(target_body_5.get("token") or "")
        target_headers_5 = {
            **common_headers,
            "Authorization": f"Bearer {target_token_5}",
            "X-RTM-Device": target_device,
        }
        report["checks"]["rotated_password_login_succeeded"] = (
            target_login_5.status_code == 200
        )

        own_admin_suspend = await client.post(
            f"/ops/admin/operators/{supervisor_id}/suspend",
            json={"reason": "Self protection smoke"},
            headers=supervisor_headers,
        )
        own_admin_role = await client.post(
            f"/ops/admin/operators/{supervisor_id}/role",
            json={
                "role_code": "rtm.operator",
                "reason": "Self protection smoke",
            },
            headers=supervisor_headers,
        )
        own_admin_rotate = await client.post(
            f"/ops/admin/operators/{supervisor_id}/credentials/rotate",
            json={
                "new_password": final_password,
                "must_change_password": False,
                "reason": "Self protection smoke",
            },
            headers=supervisor_headers,
        )
        own_admin_revoke = await client.post(
            f"/ops/admin/operators/{supervisor_id}/sessions/revoke-all",
            json={"reason": "Self protection smoke"},
            headers=supervisor_headers,
        )
        report["checks"]["supervisor_self_protection_complete"] = all(
            response.status_code == 409
            for response in (
                own_admin_suspend,
                own_admin_role,
                own_admin_rotate,
                own_admin_revoke,
            )
        )

        revoke_all = await client.post(
            f"/ops/admin/operators/{target_id}/sessions/revoke-all",
            json={"reason": "Synthetic global session revocation"},
            headers=supervisor_headers,
        )
        report["checks"]["global_session_revocation_succeeded"] = (
            revoke_all.status_code == 200
            and revoke_all.json().get(
                "reauthentication_required"
            ) is True
        )
        target_after_revoke = await client.get(
            "/ops/auth/me",
            headers=target_headers_5,
        )
        report["checks"]["globally_revoked_session_rejected"] = (
            target_after_revoke.status_code == 401
        )

        event_rows = connection.execute(
            __import__("sqlalchemy").text(
                """
                SELECT event_type
                FROM rtm_operator_access_events
                WHERE operator_id=CAST(:operator_id AS UUID)
                """
            ),
            {"operator_id": target_id},
        ).fetchall()
        event_types = {str(row[0]) for row in event_rows}
        report["checks"]["lifecycle_actions_audited"] = {
            "admin.operator_created",
            "auth.password_changed",
            "admin.operator_role_changed",
            "admin.operator_suspended",
            "admin.operator_reactivated",
            "admin.operator_password_rotated",
            "admin.operator_sessions_revoked",
        }.issubset(event_types)
        created_audit_session_id = connection.execute(
            __import__("sqlalchemy").text(
                """
                SELECT session_id
                FROM rtm_operator_access_events
                WHERE operator_id=CAST(:operator_id AS UUID)
                  AND event_type='admin.operator_created'
                ORDER BY occurred_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"operator_id": target_id},
        ).scalar_one()
        report["checks"]["lifecycle_audit_links_supervisor_session"] = (
            str(created_audit_session_id) == supervisor_session_id
        )

        database_state = connection.execute(
            __import__("sqlalchemy").text(
                """
                SELECT
                    password_hash,
                    password_version,
                    auth_epoch,
                    (
                        SELECT COUNT(*)
                        FROM rtm_operator_sessions
                        WHERE operator_id=o.id
                          AND status='active'
                    ) AS active_sessions
                FROM rtm_operators o
                WHERE id=CAST(:operator_id AS UUID)
                """
            ),
            {"operator_id": target_id},
        ).mappings().one()
        report["checks"]["database_credentials_are_hashed"] = (
            str(database_state["password_hash"]).startswith("$argon2id$")
            and temporary_password not in str(database_state["password_hash"])
            and rotated_password not in str(database_state["password_hash"])
            and int(database_state["password_version"]) >= 3
            and int(database_state["auth_epoch"]) >= 7
        )
        report["checks"]["database_has_no_active_target_sessions"] = (
            int(database_state["active_sessions"]) == 0
        )

        await client.post(
            "/ops/auth/logout",
            headers=supervisor_headers,
        )


def main() -> int:
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_operator_lifecycle_smoke",
        "version": "rtm_operator_lifecycle_smoke_v1_1",
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "synthetic_only": True,
        "transactional": True,
        "public_registration_available": False,
        "passwords_returned": False,
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
        "RTM_ENABLE_OPERATOR_LIFECYCLE_V1",
        "RTM_OPERATOR_ACCESS_HMAC_KEY",
        "RTM_TRUST_PROXY_HEADERS",
        "RTM_TRUSTED_PROXY_CIDRS",
        "RTM_OPERATOR_ACCESS_RETENTION_DAYS",
    )
    old_env = {name: os.environ.get(name) for name in env_names}
    os.environ["RTM_ENABLE_OPERATOR_AUTH_V1"] = "1"
    os.environ["RTM_ENABLE_OPERATOR_ADMIN_V1"] = "1"
    os.environ["RTM_ENABLE_OPERATOR_LIFECYCLE_V1"] = "1"
    os.environ["RTM_OPERATOR_ACCESS_HMAC_KEY"] = "L" * 64
    os.environ["RTM_TRUST_PROXY_HEADERS"] = "1"
    os.environ["RTM_TRUSTED_PROXY_CIDRS"] = "127.0.0.1/32"
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
            supervisor_id = str(uuid.uuid4())
            supervisor_email = (
                f"rtm-staging-lifecycle-supervisor-{suffix}@example.com"
            )
            target_email = (
                f"rtm-staging-lifecycle-target-{suffix}@example.com"
            )
            supervisor_password = (
                "RTM lifecycle supervisor passphrase 2026!"
            )
            temporary_password = (
                "RTM lifecycle temporary passphrase 2026!"
            )
            first_password = (
                "RTM lifecycle first personal passphrase 2026!"
            )
            rotated_password = (
                "RTM lifecycle rotated supervisor passphrase 2026!"
            )
            final_password = (
                "RTM lifecycle self protection passphrase 2026!"
            )
            report["synthetic_ids"] = {
                "supervisor_role_id": supervisor_role_id,
                "supervisor_id": supervisor_id,
            }

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
                    "id": supervisor_role_id,
                    "code": f"synthetic.lifecycle.supervisor.{suffix}",
                    "name": "RTM LIFECYCLE SUPERVISOR SMOKE",
                    "permissions": json.dumps(
                        ["ops.view", "ops.supervise"]
                    ),
                },
            )
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
                    "id": supervisor_id,
                    "email": supervisor_email,
                    "display_name": "RTM LIFECYCLE SUPERVISOR SMOKE",
                    "password_hash": hash_operator_password(
                        supervisor_password
                    ),
                    "role_id": supervisor_role_id,
                },
            )
            report["checks"]["synthetic_supervisor_inserted"] = True

            asyncio.run(
                _run_http_smoke(
                    connection,
                    report,
                    supervisor_email=supervisor_email,
                    supervisor_password=supervisor_password,
                    target_email=target_email,
                    temporary_password=temporary_password,
                    first_password=first_password,
                    rotated_password=rotated_password,
                    final_password=final_password,
                )
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
