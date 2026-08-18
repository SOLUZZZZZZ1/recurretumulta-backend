#!/usr/bin/env python3
"""Smoke transaccional de RTM Management Core V1 en staging.

Crea exclusivamente registros sintéticos dentro de una transacción que siempre
se revierte. Comprueba integridad de fecha de origen, deduplicación activa,
asignación exclusiva y auditoría append-only. No toca casos ni documentos.
"""

from __future__ import annotations

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


SMOKE_VERSION = "rtm_management_core_smoke_v1_0"
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
        "authority": "rtm_management_core_smoke",
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
        from sqlalchemy.exc import IntegrityError, InternalError

        from database import get_engine

        engine = get_engine()
        connection = engine.connect()
        transaction = connection.begin()
        try:
            now = datetime.now(timezone.utc)
            role_id = uuid.uuid4()
            operator_id = uuid.uuid4()
            session_id = uuid.uuid4()
            attention_id = uuid.uuid4()
            deadline_id = uuid.uuid4()
            assignment_id = uuid.uuid4()
            event_id = uuid.uuid4()
            engine_run_id = uuid.uuid4()

            connection.execute(
                text(
                    """
                    INSERT INTO rtm_operator_roles(
                        id, code, name, description, permissions,
                        system_role, active, created_at, updated_at
                    ) VALUES (
                        :id, :code, 'Operador sintético',
                        'Solo smoke transaccional de staging',
                        CAST(:permissions AS JSONB), FALSE, TRUE, NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": role_id,
                    "code": f"synthetic.smoke.{run_id[:12]}",
                    "permissions": json.dumps(["attention.view"]),
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
                        :id, :email, 'RTM STAGING SYNTHETIC OPERATOR', 'active',
                        :role_id, FALSE, FALSE, CAST(:profile AS JSONB), NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": operator_id,
                    "email": f"rtm-management-smoke-{run_id[:12]}@recurretumulta.eu",
                    "role_id": role_id,
                    "profile": json.dumps({"synthetic": True}),
                },
            )
            report["checks"]["operator_inserted"] = True

            connection.execute(
                text(
                    """
                    INSERT INTO rtm_operator_sessions(
                        id, operator_id, token_sha256, status, login_at,
                        last_seen_at, expires_at, metadata, created_at
                    ) VALUES (
                        :id, :operator_id, :token_sha256, 'active', :now,
                        :now, :expires_at, CAST(:metadata AS JSONB), :now
                    )
                    """
                ),
                {
                    "id": session_id,
                    "operator_id": operator_id,
                    "token_sha256": (run_id * 2)[:64],
                    "now": now,
                    "expires_at": now + timedelta(hours=1),
                    "metadata": json.dumps({"synthetic": True}),
                },
            )
            report["checks"]["session_inserted"] = True

            dedupe_key = f"synthetic:system-health:{run_id}"
            connection.execute(
                text(
                    """
                    INSERT INTO rtm_attention_items(
                        id, satellite, attention_class, code, dedupe_key,
                        title, summary, severity, status, due_at,
                        assigned_operator_id, assigned_at, metadata,
                        first_detected_at, last_detected_at, created_at, updated_at
                    ) VALUES (
                        :id, 'system', 'system_health', 'synthetic_engine_health',
                        :dedupe_key, 'Atención sintética',
                        'Smoke transaccional de staging', 'attention', 'assigned',
                        :due_at, :operator_id, :now, CAST(:metadata AS JSONB),
                        :now, :now, :now, :now
                    )
                    """
                ),
                {
                    "id": attention_id,
                    "dedupe_key": dedupe_key,
                    "due_at": now + timedelta(days=7),
                    "operator_id": operator_id,
                    "now": now,
                    "metadata": json.dumps({"synthetic": True}),
                },
            )
            report["checks"]["attention_inserted"] = True

            duplicate_blocked = False
            try:
                with connection.begin_nested():
                    connection.execute(
                        text(
                            """
                            INSERT INTO rtm_attention_items(
                                satellite, attention_class, code, dedupe_key,
                                title, severity, status
                            ) VALUES (
                                'system', 'system_health', 'duplicate',
                                :dedupe_key, 'Duplicado', 'attention', 'new'
                            )
                            """
                        ),
                        {"dedupe_key": dedupe_key},
                    )
            except IntegrityError:
                duplicate_blocked = True
            report["checks"]["active_dedupe_enforced"] = duplicate_blocked

            connection.execute(
                text(
                    """
                    INSERT INTO rtm_deadlines(
                        id, attention_item_id, deadline_class, code, title,
                        origin_at, origin_status, rule_code, rule_version,
                        computation_basis, quantity, due_at, confidence,
                        validation_status, validated_by, validated_at,
                        validation_note, metadata, created_at, updated_at
                    ) VALUES (
                        :id, :attention_id, 'operational', 'synthetic_plus_7',
                        'Seguimiento sintético +7', :origin_at, 'verified',
                        'synthetic.plus_days', 'v1', 'natural_days', 7, :due_at,
                        1.0, 'validated', :operator_id, :validated_at,
                        'Validación sintética', CAST(:metadata AS JSONB), NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": deadline_id,
                    "attention_id": attention_id,
                    "origin_at": now,
                    "due_at": now + timedelta(days=7),
                    "operator_id": operator_id,
                    "validated_at": now,
                    "metadata": json.dumps({"synthetic": True}),
                },
            )
            report["checks"]["validated_deadline_inserted"] = True

            invalid_due_blocked = False
            try:
                with connection.begin_nested():
                    connection.execute(
                        text(
                            """
                            INSERT INTO rtm_deadlines(
                                attention_item_id, deadline_class, code, title,
                                origin_status, computation_basis, due_at,
                                validation_status
                            ) VALUES (
                                :attention_id, 'operational', 'invalid_missing_origin',
                                'No debe calcular', 'missing', 'natural_days',
                                :due_at, 'pending'
                            )
                            """
                        ),
                        {
                            "attention_id": attention_id,
                            "due_at": now + timedelta(days=30),
                        },
                    )
            except IntegrityError:
                invalid_due_blocked = True
            report["checks"]["missing_origin_cannot_compute_due_at"] = invalid_due_blocked

            connection.execute(
                text(
                    """
                    INSERT INTO rtm_work_assignments(
                        id, attention_item_id, operator_id, assignment_role,
                        status, assigned_by, assigned_at, accepted_at,
                        metadata, created_at, updated_at
                    ) VALUES (
                        :id, :attention_id, :operator_id, 'responsible', 'active',
                        :operator_id, :now, :now, CAST(:metadata AS JSONB),
                        :now, :now
                    )
                    """
                ),
                {
                    "id": assignment_id,
                    "attention_id": attention_id,
                    "operator_id": operator_id,
                    "now": now,
                    "metadata": json.dumps({"synthetic": True}),
                },
            )
            report["checks"]["assignment_inserted"] = True

            duplicate_assignment_blocked = False
            try:
                with connection.begin_nested():
                    connection.execute(
                        text(
                            """
                            INSERT INTO rtm_work_assignments(
                                attention_item_id, operator_id, assignment_role,
                                status, assigned_at
                            ) VALUES (
                                :attention_id, :operator_id, 'responsible',
                                'active', :now
                            )
                            """
                        ),
                        {
                            "attention_id": attention_id,
                            "operator_id": operator_id,
                            "now": now,
                        },
                    )
            except IntegrityError:
                duplicate_assignment_blocked = True
            report["checks"]["single_active_responsible_enforced"] = duplicate_assignment_blocked

            connection.execute(
                text(
                    """
                    INSERT INTO rtm_attention_events(
                        id, attention_item_id, operator_id, session_id,
                        actor_type, event_type, result, reason, request_id,
                        previous_state, new_state, payload, created_at
                    ) VALUES (
                        :id, :attention_id, :operator_id, :session_id,
                        'operator', 'attention.assigned', 'success',
                        'Smoke sintético', :request_id,
                        CAST(:previous_state AS JSONB), CAST(:new_state AS JSONB),
                        CAST(:payload AS JSONB), :now
                    )
                    """
                ),
                {
                    "id": event_id,
                    "attention_id": attention_id,
                    "operator_id": operator_id,
                    "session_id": session_id,
                    "request_id": f"smoke-{run_id}",
                    "previous_state": json.dumps({"status": "new"}),
                    "new_state": json.dumps({"status": "assigned"}),
                    "payload": json.dumps({"synthetic": True}),
                    "now": now,
                },
            )
            report["checks"]["audit_event_inserted"] = True

            mutation_blocked = False
            try:
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE rtm_attention_events SET reason='mutated' WHERE id=:id"
                        ),
                        {"id": event_id},
                    )
            except (InternalError, Exception) as exc:
                # PostgreSQL surfaces the trigger exception as DBAPI/SQLAlchemy
                # error; the nested transaction prevents aborting the smoke.
                mutation_blocked = "append-only" in str(exc).lower()
            report["checks"]["audit_append_only_enforced"] = mutation_blocked

            connection.execute(
                text(
                    """
                    INSERT INTO rtm_attention_engine_runs(
                        id, run_key, engine_version, environment, status,
                        triggered_by, started_at, heartbeat_at, finished_at,
                        scanned_count, created_count, metrics, created_at
                    ) VALUES (
                        :id, :run_key, :engine_version, 'staging', 'succeeded',
                        'smoke', :now, :now, :now, 1, 1,
                        CAST(:metrics AS JSONB), :now
                    )
                    """
                ),
                {
                    "id": engine_run_id,
                    "run_key": f"smoke-{run_id}",
                    "engine_version": SMOKE_VERSION,
                    "now": now,
                    "metrics": json.dumps({"synthetic": True}),
                },
            )
            report["checks"]["engine_run_inserted"] = True

            all_checks = all(bool(value) for value in report["checks"].values())
            report["tests_ok"] = all_checks
            report["ok"] = all_checks
            report["synthetic_ids"] = {
                "role_id": str(role_id),
                "operator_id": str(operator_id),
                "session_id": str(session_id),
                "attention_id": str(attention_id),
                "deadline_id": str(deadline_id),
                "assignment_id": str(assignment_id),
                "event_id": str(event_id),
                "engine_run_id": str(engine_run_id),
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
