#!/usr/bin/env python3
"""Smoke HTTP transaccional y sin red de RTM CONNECT C5.

Publica el router supervisor solo dentro de una aplicacion ASGI temporal,
crea identidad y datos sinteticos, prueba autorizacion, scope, redaccion y
auditoria, y demuestra que todas las tablas ``rtm_connect_*`` permanecen
byte-a-byte iguales durante las lecturas. La transaccion completa se revierte.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SMOKE_VERSION = "rtm_connect_c5_smoke_v1_0"
_FORBIDDEN_RESPONSE_KEYS = {
    "approved_by_operator_ids",
    "audit_event_id",
    "authority_code",
    "authority_version",
    "authorized_connector_modes",
    "claimed_action_id",
    "claimed_attempt_id",
    "configuration",
    "credential_ref",
    "document_hashes",
    "event_key",
    "error_code",
    "external_reference",
    "failure_class",
    "idempotency_key",
    "instructions",
    "normalized_payload",
    "package_manifest",
    "payload",
    "raw_headers",
    "raw_payload",
    "reason_code",
    "reason_detail",
    "receipt_sha256",
    "receipt_storage_ref",
    "request_metadata",
    "resolution_code",
    "result_metadata",
    "signature",
    "source_event_id",
    "target_ref",
    "verification_method",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    return parser


def _blockers() -> list[str]:
    if (os.getenv("RTM_ENV") or "").strip().lower() != "staging":
        return ["RTM_ENV_must_be_staging"]
    try:
        from rtm_connect.supervisor_policy import (
            assert_connect_supervisor_staging_boundary,
        )

        assert_connect_supervisor_staging_boundary()
        return []
    except Exception as exc:
        return [
            "connect_c5_staging_boundary_blocked:"
            f"{type(exc).__name__}:{exc}"
        ]


def _now(*, seconds_ago: int = 0) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    ).isoformat()


def _print(payload: dict[str, Any], compact: bool) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
            default=str,
        )
    )


def _grant(action, *, operator_id: str):
    from rtm_connect.contracts import (
        AuthorizationGrant,
        ConnectorMode,
        EvidenceLevel,
    )
    from rtm_connect.idempotency import (
        derive_idempotency_key,
        payload_sha256,
    )

    return AuthorizationGrant(
        authorization_id=str(uuid.uuid4()),
        action_id=action.action_id,
        authority_code="rtm.core.authorization",
        authority_version="rtm_core_authority_v1",
        decision="approved_frozen",
        payload_sha256=payload_sha256(action),
        idempotency_key=derive_idempotency_key(
            action,
            authority_scope="rtm.core.authorization",
        ),
        required_evidence_level=EvidenceLevel.E4_RECEIPT_VERIFIED,
        authorized_connector_modes=(ConnectorMode.API,),
        approved_by_operator_ids=(operator_id,),
        authorized_at=_now(),
        expires_at=(
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
        legal_effect_authorized=True,
    )


def _connect_snapshot(conn) -> dict[str, dict[str, Any]]:
    """Cuenta y digiere cada tabla CONNECT para detectar INSERT/UPDATE/DELETE."""

    from sqlalchemy import text

    names = [
        str(row[0])
        for row in conn.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema='public'
                  AND table_type='BASE TABLE'
                  AND table_name LIKE 'rtm_connect_%'
                ORDER BY table_name
                """
            )
        ).fetchall()
    ]
    preparer = conn.dialect.identifier_preparer
    snapshot: dict[str, dict[str, Any]] = {}
    for table_name in names:
        quoted = preparer.quote(table_name)
        row = conn.execute(
            text(
                f"""
                SELECT
                    COUNT(*) AS total,
                    COALESCE(
                        md5(
                            string_agg(
                                row_to_json(t)::text,
                                '' ORDER BY row_to_json(t)::text
                            )
                        ),
                        md5('')
                    ) AS digest
                FROM {quoted} t
                """
            )
        ).mappings().one()
        snapshot[table_name] = {
            "total": int(row["total"]),
            "digest": str(row["digest"]),
        }
    return snapshot


def _response_is_redacted(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower()
            if normalized in _FORBIDDEN_RESPONSE_KEYS:
                return False
            if normalized.endswith("_sha256") or normalized.endswith("_hash"):
                return False
            if not _response_is_redacted(nested):
                return False
        return True
    if isinstance(value, list):
        return all(_response_is_redacted(item) for item in value)
    return True


async def _run_http_smoke(
    connection,
    report: dict[str, Any],
    *,
    supervisor_email: str,
    supervisor_password: str,
    operator_email: str,
    operator_password: str,
    supervisor_role_id: str,
    action_id: str,
    webhook_id: str,
    webhook_connector_id: str,
    secret_marker: str,
) -> None:
    import httpx
    from fastapi import FastAPI
    from sqlalchemy import text
    from rtm_connect.supervisor_router import (
        connect_supervisor_connection,
        connect_supervisor_gate_middleware,
        router as supervisor_router,
    )
    from rtm_core.operator_auth_router import (
        operator_auth_connection,
        router as auth_router,
    )

    app = FastAPI()
    app.middleware("http")(connect_supervisor_gate_middleware)
    app.include_router(auth_router)
    app.include_router(supervisor_router)

    async def override_connection():
        yield connection

    app.dependency_overrides[operator_auth_connection] = override_connection
    app.dependency_overrides[
        connect_supervisor_connection
    ] = override_connection

    c5_methods = [
        set(route.methods or set())
        for route in supervisor_router.routes
    ]
    disclosed_paths = {
        path
        for path in app.openapi()["paths"]
        if path.startswith("/ops/connect/supervisor")
    }
    report["checks"]["c5_surface_get_only_and_openapi_hidden"] = (
        len(c5_methods) == 7
        and all(methods == {"GET"} for methods in c5_methods)
        and not disclosed_paths
    )

    transport = httpx.ASGITransport(app=app)
    common_headers = {
        "user-agent": "RTM CONNECT C5 synthetic supervisor smoke/1.0",
        "x-forwarded-for": "203.0.113.65",
    }
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://rtm-staging.test",
    ) as client:
        disabled_before = os.environ["RTM_ENABLE_CONNECT_SUPERVISOR_V1"]
        os.environ["RTM_ENABLE_CONNECT_SUPERVISOR_V1"] = "0"
        disabled = await client.get("/ops/connect/supervisor/status")
        report["checks"]["feature_gate_closed_by_default"] = (
            disabled.status_code == 404
        )
        hidden_invalid_method = await client.post(
            "/ops/connect/supervisor/actions/not-a-uuid"
        )
        hidden_options = await client.options(
            "/ops/connect/supervisor/overview"
        )
        report["checks"]["closed_gate_precedes_routing_and_validation"] = (
            hidden_invalid_method.status_code == 404
            and hidden_options.status_code == 404
        )
        os.environ["RTM_ENABLE_CONNECT_SUPERVISOR_V1"] = disabled_before

        outbound_before = os.environ["RTM_ENABLE_EXTERNAL_SUBMISSION"]
        os.environ["RTM_ENABLE_EXTERNAL_SUBMISSION"] = "1"
        unsafe = await client.get("/ops/connect/supervisor/status")
        os.environ["RTM_ENABLE_EXTERNAL_SUBMISSION"] = outbound_before
        report["checks"]["unsafe_runtime_fails_closed"] = (
            unsafe.status_code == 503
        )

        unauthenticated = await client.get(
            "/ops/connect/supervisor/status"
        )
        report["checks"]["individual_auth_required"] = (
            unauthenticated.status_code == 401
        )

        async def login(email: str, password: str) -> dict[str, Any]:
            response = await client.post(
                "/ops/auth/login",
                json={"email": email, "password": password},
                headers=common_headers,
            )
            body = response.json()
            return {
                "status": response.status_code,
                "token": str(body.get("token") or ""),
                "device_token": str(body.get("device_token") or ""),
                "session_id": str(body.get("session_id") or ""),
                "device_id": str(body.get("device_id") or ""),
            }

        supervisor_login = await login(
            supervisor_email,
            supervisor_password,
        )
        operator_login = await login(operator_email, operator_password)
        report["synthetic_ids"]["supervisor_session_id"] = (
            supervisor_login["session_id"]
        )
        report["synthetic_ids"]["supervisor_device_id"] = (
            supervisor_login["device_id"]
        )
        report["synthetic_ids"]["operator_session_id"] = (
            operator_login["session_id"]
        )
        report["synthetic_ids"]["operator_device_id"] = (
            operator_login["device_id"]
        )
        report["checks"]["synthetic_logins_succeeded"] = (
            supervisor_login["status"] == 200
            and len(supervisor_login["token"]) >= 32
            and operator_login["status"] == 200
            and len(operator_login["token"]) >= 32
        )

        supervisor_headers = {
            **common_headers,
            "Authorization": f"Bearer {supervisor_login['token']}",
            "X-RTM-Device": supervisor_login["device_token"],
        }
        operator_headers = {
            **common_headers,
            "Authorization": f"Bearer {operator_login['token']}",
            "X-RTM-Device": operator_login["device_token"],
        }

        denied = await client.get(
            "/ops/connect/supervisor/overview",
            headers=operator_headers,
        )
        report["checks"]["non_supervisor_denied"] = (
            denied.status_code == 403
        )

        contamination_probe = connection.begin_nested()
        connection.execute(
            text(
                """
                UPDATE rtm_operator_roles SET active=FALSE, updated_at=NOW()
                WHERE id=CAST(:role_id AS UUID)
                """
            ),
            {"role_id": supervisor_role_id},
        )
        stale_role = await client.get(
            "/ops/connect/supervisor/overview",
            headers=supervisor_headers,
        )
        connection.execute(
            text(
                """
                UPDATE rtm_operator_roles SET active=TRUE, updated_at=NOW()
                WHERE id=CAST(:role_id AS UUID)
                """
            ),
            {"role_id": supervisor_role_id},
        )
        report["checks"]["inactive_live_role_denied"] = (
            stale_role.status_code == 403
        )

        connection.execute(
            text(
                """
                UPDATE rtm_connect_connectors
                SET synthetic_only=FALSE, updated_at=NOW()
                WHERE id=CAST(:connector_id AS UUID)
                """
            ),
            {"connector_id": webhook_connector_id},
        )
        contaminated = await client.get(
            "/ops/connect/supervisor/overview",
            headers=supervisor_headers,
        )
        contamination_probe.rollback()
        report["checks"]["non_synthetic_scope_blocked"] = (
            contaminated.status_code == 503
        )
        report["checks"]["explicit_denials_are_not_cacheable"] = all(
            response.headers.get("cache-control") == "no-store, max-age=0"
            and response.headers.get("pragma") == "no-cache"
            for response in (
                disabled,
                unsafe,
                unauthenticated,
                denied,
                stale_role,
                contaminated,
            )
        )

        paths = (
            "/ops/connect/supervisor/status",
            "/ops/connect/supervisor/overview",
            "/ops/connect/supervisor/attention?limit=20&offset=0",
            "/ops/connect/supervisor/actions?status=unknown&limit=20",
            f"/ops/connect/supervisor/actions/{action_id}?history_limit=50",
            "/ops/connect/supervisor/manual-tasks?limit=20",
            "/ops/connect/supervisor/webhook-dlq?limit=20",
        )
        responses = [
            await client.get(path, headers=supervisor_headers)
            for path in paths
        ]
        report["checks"]["all_supervisor_reads_succeeded"] = all(
            response.status_code == 200 for response in responses
        )
        report["checks"]["all_supervisor_reads_no_store"] = all(
            response.headers.get("cache-control") == "no-store, max-age=0"
            and response.headers.get("pragma") == "no-cache"
            for response in responses
        )
        payloads = [response.json() for response in responses]
        status_body, overview_body, attention_body, actions_body = payloads[:4]
        detail_body, manual_body, dlq_body = payloads[4:]
        report["checks"]["status_declares_observer_boundary"] = (
            status_body.get("business_operations_read_only") is True
            and status_body.get("execution_controls_available") is False
            and status_body.get("synthetic_only") is True
        )
        report["checks"]["overview_projects_synthetic_ledgers"] = (
            int(
                overview_body.get("overview", {})
                .get("actions", {})
                .get("total", 0)
            )
            >= 1
            and int(
                overview_body.get("overview", {})
                .get("connectors", {})
                .get("total", 0)
            )
            >= 2
            and int(
                overview_body.get("overview", {})
                .get("webhooks", {})
                .get("dead_lettered", 0)
            )
            >= 1
        )
        report["checks"]["unknown_action_filtered_and_detailed"] = (
            any(
                str(item.get("id")) == action_id
                for item in actions_body.get("items", [])
            )
            and str(detail_body.get("action", {}).get("id")) == action_id
            and detail_body.get("action", {}).get("status") == "unknown"
        )
        attention_types = {
            str(item.get("resource_type"))
            for item in attention_body.get("items", [])
        }
        report["checks"]["attention_is_technical_projection"] = {
            "action",
            "webhook_dead_letter",
        }.issubset(attention_types)
        report["checks"]["manual_projection_bounded"] = (
            manual_body.get("pagination", {}).get("limit") == 20
            and isinstance(manual_body.get("items"), list)
        )
        report["checks"]["dead_letter_projected_without_raw_body"] = (
            any(
                str(item.get("id")) == webhook_id
                for item in dlq_body.get("items", [])
            )
        )
        rendered = json.dumps(payloads, ensure_ascii=False, default=str)
        report["checks"]["all_responses_recursively_redacted"] = (
            all(_response_is_redacted(payload) for payload in payloads)
            and secret_marker not in rendered
        )

        audit = connection.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE device_id IS NOT NULL)
                        AS with_device,
                    COUNT(DISTINCT event_type) AS distinct_types
                FROM rtm_operator_access_events
                WHERE operator_id=CAST(:operator_id AS UUID)
                  AND event_type LIKE 'connect.supervisor.%'
                """
            ),
            {"operator_id": report["synthetic_ids"]["supervisor_id"]},
        ).mappings().one()
        report["checks"]["every_successful_read_audited_individually"] = (
            int(audit["total"]) == len(paths)
            and int(audit["with_device"]) == len(paths)
            and int(audit["distinct_types"]) == len(paths)
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_connect_c5_smoke",
        "version": SMOKE_VERSION,
        "environment": (
            os.getenv("RTM_ENV") or ""
        ).strip().lower() or "unset",
        "synthetic_only": True,
        "transactional": True,
        "network_used": False,
        "supervisor_projection_exercised": True,
        "execution_runtime_published": False,
        "business_operations_read_only": True,
        "schema_changes_applied": False,
        "external_effects_executed": False,
        "checks": {},
        "cleanup": {
            "database_rolled_back": False,
            "error": None,
        },
        "synthetic_ids": {},
    }
    blockers = _blockers()
    if blockers:
        report["blockers"] = blockers
        _print(report, args.compact)
        return 2

    env_names = (
        "RTM_ENABLE_OPERATOR_AUTH_V1",
        "RTM_ENABLE_CONNECT_SUPERVISOR_V1",
        "RTM_OPERATOR_ACCESS_HMAC_KEY",
        "RTM_TRUST_PROXY_HEADERS",
        "RTM_OPERATOR_ACCESS_RETENTION_DAYS",
    )
    old_env = {name: os.environ.get(name) for name in env_names}
    os.environ["RTM_ENABLE_OPERATOR_AUTH_V1"] = "1"
    os.environ["RTM_ENABLE_CONNECT_SUPERVISOR_V1"] = "1"
    os.environ["RTM_OPERATOR_ACCESS_HMAC_KEY"] = "C" * 64
    os.environ["RTM_TRUST_PROXY_HEADERS"] = "1"
    os.environ["RTM_OPERATOR_ACCESS_RETENTION_DAYS"] = "180"

    engine = None
    ids: dict[str, str] = {}
    try:
        from sqlalchemy import text
        from database import get_engine
        from rtm_connect.connectors.synthetic_echo import (
            SyntheticEchoScenario,
        )
        from rtm_connect.connectors.synthetic_webhook import (
            SyntheticWebhookConnector,
            SyntheticWebhookOutcome,
        )
        from rtm_connect.contracts import ConnectActionRequest, RiskClass
        from rtm_connect.execution import execute_synthetic_echo
        from rtm_connect.supervisor_policy import (
            assert_connect_supervisor_database_identity,
            assert_connect_supervisor_staging_boundary,
        )
        from rtm_connect.webhooks import (
            WebhookMatchError,
            dead_letter_webhook,
            match_webhook,
            receive_synthetic_webhook,
            register_synthetic_webhook_connector,
            verify_webhook,
        )
        from rtm_core.operator_auth_crypto import hash_operator_password
        from unittest.mock import patch

        boundary = assert_connect_supervisor_staging_boundary()
        engine = get_engine()
        connection = engine.connect()
        transaction = connection.begin()
        report["connected_database"] = (
            assert_connect_supervisor_database_identity(
                connection,
                expected_database_name=boundary.database_name,
            )
        )
        report["checks"]["connected_database_identity_valid"] = True
        network_attempts: list[str] = []

        def deny_network(*args, **kwargs):
            network_attempts.append(repr(args[:2]))
            raise AssertionError(
                "RTM CONNECT C5 smoke bloquea toda red saliente"
            )

        create_connection_patch = patch(
            "socket.create_connection",
            side_effect=deny_network,
        )
        socket_connect_patch = patch(
            "socket.socket.connect",
            side_effect=deny_network,
        )
        create_connection_patch.start()
        socket_connect_patch.start()
        try:
            suffix = uuid.uuid4().hex[:12]
            supervisor_role_id = str(uuid.uuid4())
            operator_role_id = str(uuid.uuid4())
            supervisor_id = str(uuid.uuid4())
            operator_id = str(uuid.uuid4())
            supervisor_email = f"c5-supervisor-{suffix}@example.com"
            operator_email = f"c5-operator-{suffix}@example.com"
            supervisor_password = "RTM C5 synthetic supervisor 2026!"
            operator_password = "RTM C5 synthetic operator 2026!"
            ids.update(
                {
                    "supervisor_role_id": supervisor_role_id,
                    "operator_role_id": operator_role_id,
                    "supervisor_id": supervisor_id,
                    "operator_id": operator_id,
                }
            )
            report["synthetic_ids"] = ids

            for role_id, code, permissions in (
                (
                    supervisor_role_id,
                    f"synthetic.connect.c5.supervisor.{suffix}",
                    ["ops.view", "ops.supervise"],
                ),
                (
                    operator_role_id,
                    f"synthetic.connect.c5.operator.{suffix}",
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
                            CAST(:id AS UUID), :code, :code,
                            CAST(:permissions AS JSONB),
                            FALSE, TRUE, NOW(), NOW()
                        )
                        """
                    ),
                    {
                        "id": role_id,
                        "code": code,
                        "permissions": json.dumps(permissions),
                    },
                )

            for identity in (
                (
                    supervisor_id,
                    supervisor_email,
                    "RTM CONNECT C5 SUPERVISOR",
                    supervisor_password,
                    supervisor_role_id,
                ),
                (
                    operator_id,
                    operator_email,
                    "RTM CONNECT C5 OPERATOR",
                    operator_password,
                    operator_role_id,
                ),
            ):
                identity_id, email, display_name, password, role_id = identity
                connection.execute(
                    text(
                        """
                        INSERT INTO rtm_operators(
                            id, email, display_name, password_hash, status,
                            primary_role_id, must_change_password,
                            mfa_required, profile, failed_login_count,
                            password_algorithm, password_version, auth_epoch,
                            password_changed_at, created_at, updated_at
                        ) VALUES (
                            CAST(:id AS UUID), :email, :display_name,
                            :password_hash, 'active', CAST(:role_id AS UUID),
                            FALSE, FALSE,
                            '{"synthetic":true,"environment":"staging",
                              "purpose":"connect_c5_smoke"}'::jsonb,
                            0, 'argon2id', 1, 1, NOW(), NOW(), NOW()
                        )
                        """
                    ),
                    {
                        "id": identity_id,
                        "email": email,
                        "display_name": display_name,
                        "password_hash": hash_operator_password(password),
                        "role_id": role_id,
                    },
                )
            report["checks"]["synthetic_operators_inserted"] = True

            case_id = str(uuid.uuid4())
            connection.execute(
                text(
                    """
                    INSERT INTO cases(
                        id, status, test_mode, created_at, updated_at
                    ) VALUES (
                        CAST(:case_id AS UUID), 'core_review_pending',
                        TRUE, NOW(), NOW()
                    )
                    """
                ),
                {"case_id": case_id},
            )
            ids["case_id"] = case_id
            report["checks"]["synthetic_case_inserted"] = True

            action_id = str(uuid.uuid4())
            secret_marker = f"C5-RAW-MATERIAL-{suffix}"
            action = ConnectActionRequest(
                action_id=action_id,
                capability="synthetic.echo",
                satellite="synthetic",
                target_type="synthetic.endpoint",
                target_ref=f"synthetic://private-target/{secret_marker}",
                payload={
                    "message": secret_marker,
                    "sequence": 5,
                },
                requested_by_operator_id=supervisor_id,
                requested_at=_now(),
                risk_class=RiskClass.R3_LEGAL_OR_FINANCIAL,
                case_id=case_id,
            )
            unknown = execute_synthetic_echo(
                connection,
                action=action,
                grant=_grant(action, operator_id=supervisor_id),
                scenario=SyntheticEchoScenario.UNKNOWN,
                operator_id=supervisor_id,
            )
            ids.update(
                {
                    "action_id": action_id,
                    "attempt_id": str(unknown.attempt_id),
                    "echo_connector_id": unknown.connector_id,
                }
            )
            report["checks"]["synthetic_unknown_action_created"] = (
                unknown.status == "unknown"
                and unknown.attempt_id is not None
                and not unknown.confirmed
            )

            webhook_connector = register_synthetic_webhook_connector(
                connection
            )
            ids["webhook_connector_id"] = webhook_connector.connector_id
            adapter = SyntheticWebhookConnector()
            fake_action_id = str(uuid.uuid4())
            fake_attempt_id = str(uuid.uuid4())
            delivery = adapter.build_delivery(
                event_key=f"c5-unmatched-{suffix}",
                observed_at=_now(seconds_ago=1),
                origin_connector_code="synthetic.echo",
                origin_connector_version="v1.0",
                action_id=fake_action_id,
                attempt_id=fake_attempt_id,
                request_sha256="d" * 64,
                external_reference=f"SYN-C5-{secret_marker}",
                outcome=SyntheticWebhookOutcome.UNKNOWN,
                normalized_payload={"private_marker": secret_marker},
            )
            intake = receive_synthetic_webhook(
                connection,
                ingress_connector_id=webhook_connector.connector_id,
                delivery=delivery,
            )
            ids["webhook_id"] = intake.webhook_id
            verify_webhook(connection, webhook_id=intake.webhook_id)
            match_blocked = False
            try:
                match_webhook(connection, webhook_id=intake.webhook_id)
            except WebhookMatchError:
                match_blocked = True
            dead_lettered = dead_letter_webhook(
                connection,
                webhook_id=intake.webhook_id,
                reason_code="exact_match_not_found",
                reason_detail=f"synthetic only {secret_marker}",
            )
            report["checks"]["synthetic_dead_letter_created"] = (
                match_blocked and dead_lettered
            )

            before_reads = _connect_snapshot(connection)
            asyncio.run(
                _run_http_smoke(
                    connection,
                    report,
                    supervisor_email=supervisor_email,
                    supervisor_password=supervisor_password,
                    operator_email=operator_email,
                    operator_password=operator_password,
                    supervisor_role_id=supervisor_role_id,
                    action_id=action_id,
                    webhook_id=intake.webhook_id,
                    webhook_connector_id=webhook_connector.connector_id,
                    secret_marker=secret_marker,
                )
            )
            after_reads = _connect_snapshot(connection)
            report["checks"]["all_connect_ledgers_unchanged_by_http_gets"] = (
                before_reads == after_reads
            )
            report["checks"]["outbound_network_guard_held"] = (
                not network_attempts
            )
            report["checks"]["no_external_effects"] = (
                report["network_used"] is False
                and report["execution_runtime_published"] is False
                and report["schema_changes_applied"] is False
                and report["external_effects_executed"] is False
            )
            report["tests_ok"] = all(
                bool(value) for value in report["checks"].values()
            )
            report["ok"] = bool(report["tests_ok"])
        finally:
            socket_connect_patch.stop()
            create_connection_patch.stop()
            transaction.rollback()
            connection.close()
            report["cleanup"]["database_rolled_back"] = True

        with engine.connect() as verification:
            remaining = verification.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM rtm_connect_actions
                         WHERE id=CAST(:action_id AS UUID)) AS actions,
                        (SELECT COUNT(*) FROM rtm_connect_connectors
                         WHERE id IN (
                            CAST(:echo_connector_id AS UUID),
                            CAST(:webhook_connector_id AS UUID)
                         )) AS connectors,
                        (SELECT COUNT(*) FROM rtm_connect_webhook_inbox
                         WHERE id=CAST(:webhook_id AS UUID)) AS webhook_inbox,
                        (SELECT COUNT(*) FROM rtm_operators
                         WHERE id IN (
                            CAST(:supervisor_id AS UUID),
                            CAST(:operator_id AS UUID)
                         )) AS operators,
                        (SELECT COUNT(*) FROM rtm_operator_roles
                         WHERE id IN (
                            CAST(:supervisor_role_id AS UUID),
                            CAST(:operator_role_id AS UUID)
                         )) AS roles,
                        (SELECT COUNT(*) FROM rtm_operator_access_events
                         WHERE operator_id IN (
                            CAST(:supervisor_id AS UUID),
                            CAST(:operator_id AS UUID)
                         )) AS access_events,
                        (SELECT COUNT(*) FROM cases
                         WHERE id=CAST(:case_id AS UUID)) AS cases
                    """
                ),
                ids,
            ).mappings().one()
        for key, value in remaining.items():
            report["cleanup"][f"synthetic_{key}_remaining"] = int(value)
        report["checks"]["rollback_removed_synthetic_records"] = all(
            int(value) == 0 for value in remaining.values()
        )
        report["tests_ok"] = all(
            bool(value) for value in report["checks"].values()
        )
        report["ok"] = bool(
            report["tests_ok"]
            and report["cleanup"]["database_rolled_back"]
        )
        code = 0 if report["ok"] else 1
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["tests_ok"] = False
        report["ok"] = False
        report["cleanup"]["error"] = str(exc)
        code = 1
    finally:
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    _print(report, args.compact)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
