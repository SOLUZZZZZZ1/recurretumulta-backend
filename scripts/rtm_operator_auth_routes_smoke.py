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
from contextlib import contextmanager
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


class _SharedTransactionEngine:
    """Expone al middleware la conexión incluida en el rollback del smoke."""

    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def begin(self):
        yield self.connection


async def _run_http_smoke(
    connection,
    report: dict[str, Any],
    password: str,
    *,
    assigned_case_id: str,
    unassigned_case_id: str,
    signer_email: str,
    signer_password: str,
):
    import app as backend_app
    import httpx
    import ops as ops_routes
    import ops_operator_router as operator_case_routes
    from rtm_core import legacy_ops_session_bridge as bridge
    from rtm_core import operator_auth_router as auth_routes
    from rtm_core import ops_case_scope as case_scope
    from rtm_core import workspace_router as workspace_routes

    # Se ejercita la aplicacion real y los handlers publicados. Solo se
    # sustituyen, de forma reversible, los puntos de entrada a base de datos
    # necesarios para que todas las rutas compartan la transaccion que el
    # smoke revierte al terminar.
    app = backend_app.app

    async def override_connection():
        yield connection

    shared_engine = _SharedTransactionEngine(connection)
    engine_modules = (
        bridge,
        case_scope,
        ops_routes,
        operator_case_routes,
        workspace_routes,
    )
    original_engines = {
        module: module.get_engine
        for module in engine_modules
    }
    dependency_sentinel = object()
    original_override = app.dependency_overrides.get(
        auth_routes.operator_auth_connection,
        dependency_sentinel,
    )
    app.dependency_overrides[
        auth_routes.operator_auth_connection
    ] = override_connection
    for module in engine_modules:
        module.get_engine = lambda: shared_engine

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
    try:
        report["checks"]["bridge_middleware_registered"] = any(
            getattr(middleware, "kwargs", {}).get("dispatch")
            is bridge.legacy_ops_individual_session_bridge
            for middleware in app.user_middleware
        )
        expected_endpoints = {
            ("/ops/queue", ops_routes.queue),
            (
                "/ops/cases/{case_id}",
                operator_case_routes.get_case_detail,
            ),
            ("/ops/cases/{case_id}/events", ops_routes.list_events),
            (
                "/ops/core/cases/{case_id}/payment-status",
                workspace_routes.get_case_payment_status,
            ),
        }
        wired_endpoints = {
            (getattr(route, "path", None), getattr(route, "endpoint", None))
            for route in app.routes
        }
        report["checks"]["real_app_handlers_wired"] = (
            expected_endpoints.issubset(wired_endpoints)
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://rtm-staging.test",
        ) as client:
            status = await client.get("/ops/auth/status")
            report["checks"]["status_route_enabled"] = (
                status.status_code == 200
                and status.json().get("individual_login_enabled") is True
                and status.json().get("shared_ops_login_accepted") is False
                and status.json().get("legacy_login_unchanged") is True
                and status.json().get("legacy_login_retired_in_staging") is True
                and status.json().get("non_staging_legacy_login_unchanged") is True
            )

            wrong = await client.post(
                "/ops/auth/login",
                json={
                    "email": report["synthetic_email"],
                    "password": "wrong-password-value",
                },
                headers=headers,
            )
            report["checks"]["wrong_password_rejected"] = (
                wrong.status_code == 401
            )

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
            response_device_cookie = login.cookies.get("rtm_presenter_device")
            client_device_cookie = client.cookies.get("rtm_presenter_device")
            device_token = str(
                response_device_cookie or client_device_cookie or ""
            )
            report["checks"]["login_succeeded"] = (
                login.status_code == 200
                and len(token) >= 32
                and body.get("token_type") == "bearer"
                and body.get("shared_ops_login_accepted") is False
                and body.get("legacy_login_retired_in_staging") is True
                and body.get("non_staging_legacy_login_unchanged") is True
            )
            report["checks"]["device_secret_cookie_only"] = (
                "device_token" not in body
                and len(device_token) >= 24
                and response_device_cookie == device_token
                and client_device_cookie == device_token
            )

            auth_headers = {
                **headers,
                "Authorization": f"Bearer {token}",
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

            retired_login = await client.post("/ops/login")
            report["checks"]["bridge_legacy_login_retired"] = (
                retired_login.status_code == 410
            )
            shared_token = await client.get(
                "/ops/queue",
                headers={
                    **auth_headers,
                    "X-Operator-Token": str(os.getenv("OPERATOR_TOKEN") or ""),
                },
            )
            report["checks"]["bridge_shared_token_rejected"] = (
                shared_token.status_code == 401
            )

            queue = await client.get(
                "/ops/queue?limit=50",
                headers=auth_headers,
            )
            queue_items = (
                queue.json().get("items", [])
                if queue.status_code == 200
                else []
            )
            queue_case_ids = {
                str(item.get("case_id") or "")
                for item in queue_items
                if isinstance(item, dict)
            }
            report["checks"]["real_queue_is_assignment_scoped"] = (
                queue.status_code == 200
                and assigned_case_id in queue_case_ids
                and unassigned_case_id not in queue_case_ids
            )

            assigned_case = await client.get(
                f"/ops/cases/{assigned_case_id}",
                headers=auth_headers,
            )
            report["checks"]["bridge_assigned_case_allowed"] = (
                assigned_case.status_code == 200
                and assigned_case.json().get("id") == assigned_case_id
            )

            case_events = await client.get(
                f"/ops/cases/{assigned_case_id}/events",
                headers=auth_headers,
            )
            event_types = (
                {
                    str(item.get("type") or "")
                    for item in case_events.json().get("events", [])
                    if isinstance(item, dict)
                }
                if case_events.status_code == 200
                else set()
            )
            report["checks"]["real_case_events_loaded"] = (
                case_events.status_code == 200
                and "rtm_operator_auth_routes_smoke_probe" in event_types
            )

            payment_status = await client.get(
                f"/ops/core/cases/{assigned_case_id}/payment-status",
                headers=auth_headers,
            )
            report["checks"]["real_payment_status_loaded"] = (
                payment_status.status_code == 200
                and payment_status.json().get("case_id")
                == assigned_case_id
            )
            unassigned_case = await client.get(
                f"/ops/core/cases/{unassigned_case_id}/payment-status",
                headers=auth_headers,
            )
            report["checks"]["bridge_unassigned_case_hidden"] = (
                unassigned_case.status_code == 404
            )

            signer_login = await client.post(
                "/ops/auth/login",
                json={
                    "email": signer_email,
                    "password": signer_password,
                },
                headers=headers,
            )
            signer_body = signer_login.json()
            signer_token = str(signer_body.get("token") or "")
            signer_denied = await client.get(
                "/ops/queue",
                headers={
                    **headers,
                    "Authorization": f"Bearer {signer_token}",
                },
            )
            report["checks"]["bridge_non_operational_role_rejected"] = (
                signer_login.status_code == 200
                and signer_body.get("operator", {}).get("role_code")
                == "rtm.signer"
                and signer_denied.status_code == 403
            )
            if signer_token:
                await client.post(
                    "/ops/auth/logout",
                    headers={
                        **headers,
                        "Authorization": f"Bearer {signer_token}",
                    },
                )

            logout = await client.post(
                "/ops/auth/logout",
                headers=auth_headers,
            )
            report["checks"]["logout_succeeded"] = (
                logout.status_code == 200
            )

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
                headers=headers,
            )
            second_body = second.json()
            report["checks"]["known_device_reused"] = (
                second.status_code == 200
                and "device_token" not in second_body
                and second.headers.get("set-cookie") is None
                and client.cookies.get("rtm_presenter_device")
                == device_token
            )
            second_token = str(second_body.get("token") or "")
            if second_token:
                await client.post(
                    "/ops/auth/logout",
                    headers={
                        **headers,
                        "Authorization": f"Bearer {second_token}",
                    },
                )
            return token
    finally:
        for module, original_get_engine in original_engines.items():
            module.get_engine = original_get_engine
        if original_override is dependency_sentinel:
            app.dependency_overrides.pop(
                auth_routes.operator_auth_connection,
                None,
            )
        else:
            app.dependency_overrides[
                auth_routes.operator_auth_connection
            ] = original_override


def main() -> int:
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_operator_auth_routes_smoke",
        "version": "rtm_operator_auth_routes_smoke_v1_4",
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "synthetic_only": True,
        "transactional": True,
        "legacy_login_unchanged": True,
        "legacy_login_retired_in_staging": True,
        "non_staging_legacy_login_unchanged": True,
        "shared_ops_login_accepted": False,
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
            "OPERATOR_TOKEN",
        )
    }
    os.environ["RTM_ENABLE_OPERATOR_AUTH_V1"] = "1"
    os.environ["RTM_OPERATOR_ACCESS_HMAC_KEY"] = "S" * 64
    os.environ["RTM_TRUST_PROXY_HEADERS"] = "1"
    os.environ["RTM_OPERATOR_ACCESS_RETENTION_DAYS"] = "180"
    os.environ["OPERATOR_TOKEN"] = (
        "rtm-routes-smoke-internal-legacy-token-" + report["run_id"]
    )

    try:
        from sqlalchemy import text
        from database import get_engine
        from rtm_core.operator_auth_crypto import hash_operator_password

        engine = get_engine()
        connection = engine.connect()
        transaction = connection.begin()
        try:
            operator_id = str(uuid.uuid4())
            signer_id = str(uuid.uuid4())
            tenant_id = str(uuid.uuid4())
            membership_id = str(uuid.uuid4())
            binding_id = str(uuid.uuid4())
            assignment_id = str(uuid.uuid4())
            assigned_case_id = str(uuid.uuid4())
            unassigned_case_id = str(uuid.uuid4())
            email = (
                f"rtm-routes-smoke-{report['run_id'][:12]}"
                "@recurretumulta.eu"
            )
            signer_email = (
                f"rtm-routes-signer-{report['run_id'][:12]}"
                "@recurretumulta.eu"
            )
            password = "RTM synthetic routes passphrase 2026!"
            signer_password = "RTM synthetic signer passphrase 2026!"
            report["synthetic_email"] = email

            role_rows = connection.execute(
                text(
                    """
                    SELECT id, code, permissions
                    FROM rtm_operator_roles
                    WHERE code IN ('rtm.operator', 'rtm.signer')
                      AND active=TRUE
                    """
                )
            ).mappings().all()
            roles = {str(row["code"]): row for row in role_rows}
            if set(roles) != {"rtm.operator", "rtm.signer"}:
                raise RuntimeError("required_bridge_roles_not_ready")
            report["checks"]["required_bridge_roles_ready"] = True

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
                        CAST(:id AS UUID), :email, :display_name,
                        :password_hash, 'active', CAST(:role_id AS UUID),
                        FALSE, FALSE, '{"synthetic": true}'::jsonb,
                        0, 'argon2id', 1, 1, NOW(), NOW(), NOW()
                    )
                    """
                ),
                [
                    {
                        "id": operator_id,
                        "email": email,
                        "display_name": "RTM STAGING ROUTES OPERATOR",
                        "password_hash": hash_operator_password(password),
                        "role_id": str(roles["rtm.operator"]["id"]),
                    },
                    {
                        "id": signer_id,
                        "email": signer_email,
                        "display_name": "RTM STAGING ROUTES SIGNER",
                        "password_hash": hash_operator_password(
                            signer_password
                        ),
                        "role_id": str(roles["rtm.signer"]["id"]),
                    },
                ],
            )
            report["checks"]["synthetic_operator_inserted"] = True

            connection.execute(
                text(
                    """
                    INSERT INTO cases(
                        id, status, test_mode, created_at, updated_at
                    ) VALUES (
                        CAST(:id AS UUID), 'uploaded', TRUE, NOW(), NOW()
                    )
                    """
                ),
                [
                    {"id": assigned_case_id},
                    {"id": unassigned_case_id},
                ],
            )
            connection.execute(
                text(
                    """
                    INSERT INTO events(case_id, type, payload, created_at)
                    VALUES (
                        CAST(:case_id AS UUID),
                        'rtm_operator_auth_routes_smoke_probe',
                        jsonb_build_object(
                            'synthetic', TRUE,
                            'external_effects', FALSE
                        ),
                        NOW()
                    )
                    """
                ),
                {"case_id": assigned_case_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO rtm_connect_a1s_tenants(
                        id, tenant_code, display_name, status,
                        synthetic_only, metadata, created_at, updated_at
                    ) VALUES (
                        CAST(:id AS UUID), :tenant_code,
                        'RTM STAGING ROUTES TENANT', 'active', TRUE,
                        jsonb_build_object(
                            'synthetic_marker', 'RTM_A1S_SYNTHETIC_ONLY',
                            'synthetic_only', TRUE
                        ),
                        NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": tenant_id,
                    "tenant_code": (
                        "a1s-synthetic-auth-" + report["run_id"][:12]
                    ),
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO rtm_connect_a1s_case_bindings(
                        id, tenant_id, case_id, binding_code, status,
                        synthetic_only, case_snapshot_sha256,
                        bound_by_operator_id, bound_at, version, metadata
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:tenant_id AS UUID),
                        CAST(:case_id AS UUID), :binding_code, 'active', TRUE,
                        :case_snapshot_sha256, CAST(:operator_id AS UUID),
                        NOW(), 1,
                        jsonb_build_object(
                            'synthetic_marker', 'RTM_A1S_SYNTHETIC_ONLY',
                            'synthetic_only', TRUE,
                            'test_mode', TRUE
                        )
                    )
                    """
                ),
                {
                    "id": binding_id,
                    "tenant_id": tenant_id,
                    "case_id": assigned_case_id,
                    "binding_code": (
                        "rtm-a1s-binding-" + report["run_id"][:24]
                    ),
                    "case_snapshot_sha256": "a" * 64,
                    "operator_id": operator_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO rtm_connect_a1s_memberships(
                        id, tenant_id, principal_id, operator_id, role,
                        status, synthetic_only, granted_by_operator_id,
                        granted_at, revoked_by_operator_id, revoked_at,
                        version, metadata
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:tenant_id AS UUID),
                        CAST(:operator_id AS UUID),
                        CAST(:operator_id AS UUID), 'executor', 'active', TRUE,
                        CAST(:operator_id AS UUID), NOW(), NULL, NULL, 1,
                        jsonb_build_object(
                            'synthetic_marker', 'RTM_A1S_SYNTHETIC_ONLY',
                            'synthetic_only', TRUE
                        )
                    )
                    """
                ),
                {
                    "id": membership_id,
                    "tenant_id": tenant_id,
                    "operator_id": operator_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO rtm_work_assignments(
                        id, case_id, attention_item_id, operator_id,
                        assignment_role, status, assigned_by, assigned_at,
                        accepted_at, released_at, metadata,
                        created_at, updated_at
                    ) VALUES (
                        CAST(:id AS UUID), CAST(:case_id AS UUID), NULL,
                        CAST(:operator_id AS UUID), 'responsible', 'active',
                        CAST(:operator_id AS UUID), NOW(), NOW(), NULL,
                        jsonb_build_object(
                            'synthetic_marker',
                            'RTM_PRESENTER_SYNTHETIC_ONLY',
                            'synthetic_only', TRUE
                        ), NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": assignment_id,
                    "case_id": assigned_case_id,
                    "operator_id": operator_id,
                },
            )
            report["checks"]["synthetic_bridge_scope_inserted"] = True

            raw_token = asyncio.run(
                _run_http_smoke(
                    connection,
                    report,
                    password,
                    assigned_case_id=assigned_case_id,
                    unassigned_case_id=unassigned_case_id,
                    signer_email=signer_email,
                    signer_password=signer_password,
                )
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
                "operator_id": operator_id,
                "signer_id": signer_id,
                "tenant_id": tenant_id,
                "membership_id": membership_id,
                "binding_id": binding_id,
                "assigned_case_id": assigned_case_id,
                "unassigned_case_id": unassigned_case_id,
                "assignment_id": assignment_id,
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
