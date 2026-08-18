#!/usr/bin/env python3
"""Audita y aplica RTM Management Core V1 únicamente en staging.

La utilidad se niega a acceder a la base de datos fuera de un entorno staging
aislado. ``--apply`` requiere la confirmación literal
``STAGING_MANAGEMENT_SCHEMA_ONLY``. Solo ejecuta DDL aditivo e idempotente.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))


SCHEMA_VERSION = "rtm_staging_management_schema_v1_0"
APPLY_CONFIRMATION = "STAGING_MANAGEMENT_SCHEMA_ONLY"

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}

BASE_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "cases": {"id"},
    "documents": {"id", "case_id"},
    "events": {"id", "case_id", "type", "created_at"},
}

MANAGEMENT_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "rtm_management_schema_migrations": {"name", "metadata", "applied_at"},
    "rtm_operator_roles": {
        "id", "code", "name", "description", "permissions", "system_role",
        "active", "created_at", "updated_at",
    },
    "rtm_operators": {
        "id", "email", "display_name", "password_hash", "status",
        "primary_role_id", "must_change_password", "mfa_required", "profile",
        "last_login_at", "created_by", "created_at", "updated_at",
        "disabled_by", "disabled_at",
    },
    "rtm_operator_sessions": {
        "id", "operator_id", "token_sha256", "status", "login_at",
        "last_seen_at", "expires_at", "logout_at", "revoked_at",
        "revoked_by", "close_reason", "ip_address", "user_agent", "metadata",
        "created_at",
    },
    "rtm_attention_items": {
        "id", "case_id", "satellite", "attention_class", "code",
        "dedupe_key", "title", "summary", "severity", "status",
        "source_event_id", "source_document_id", "source_entity_type",
        "source_entity_id", "due_at", "assigned_operator_id", "assigned_at",
        "seen_by", "seen_at", "in_review_by", "in_review_at", "resolved_by",
        "resolved_at", "resolution_code", "resolution_note", "version",
        "first_detected_at", "last_detected_at", "metadata", "created_at",
        "updated_at",
    },
    "rtm_deadlines": {
        "id", "case_id", "attention_item_id", "deadline_class", "code",
        "title", "source_event_id", "source_document_id", "origin_at",
        "origin_status", "origin_timezone", "rule_code", "rule_version",
        "computation_basis", "calendar_code", "quantity", "due_at",
        "confidence", "validation_status", "validated_by", "validated_at",
        "validation_note", "supersedes_id", "metadata", "created_at",
        "updated_at",
    },
    "rtm_work_assignments": {
        "id", "case_id", "attention_item_id", "operator_id",
        "assignment_role", "status", "team_code", "assigned_by",
        "assigned_at", "accepted_at", "released_at", "release_reason",
        "metadata", "created_at", "updated_at",
    },
    "rtm_attention_events": {
        "id", "attention_item_id", "case_id", "operator_id", "session_id",
        "actor_type", "event_type", "result", "reason", "request_id",
        "previous_state", "new_state", "payload", "created_at",
    },
    "rtm_attention_engine_runs": {
        "id", "run_key", "engine_version", "environment", "status",
        "triggered_by", "started_at", "heartbeat_at", "finished_at",
        "scanned_count", "created_count", "updated_count", "resolved_count",
        "error_count", "error_summary", "metrics", "created_at",
    },
}

REQUIRED_INDEXES = {
    "uq_rtm_operator_email",
    "uq_rtm_operator_role_code",
    "idx_rtm_operator_status",
    "idx_rtm_operator_sessions_active",
    "uq_rtm_attention_active_dedupe",
    "idx_rtm_attention_priority",
    "idx_rtm_attention_case",
    "idx_rtm_attention_assignee",
    "idx_rtm_deadlines_due",
    "idx_rtm_deadlines_case",
    "uq_rtm_assignment_attention_role",
    "uq_rtm_assignment_case_role",
    "idx_rtm_assignments_operator",
    "idx_rtm_attention_events_item",
    "idx_rtm_attention_events_case",
    "idx_rtm_engine_runs_health",
}

REQUIRED_TRIGGERS = {"trg_rtm_attention_events_append_only"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audita o aplica RTM Management Core V1 en staging.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica exclusivamente DDL aditivo e idempotente.",
    )
    parser.add_argument(
        "--confirmation",
        default="",
        help=f"Confirmación obligatoria para --apply: {APPLY_CONFIRMATION}",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Imprime el informe JSON en una sola línea.",
    )
    return parser


def _flag(name: str) -> bool | None:
    raw = (os.getenv(name) or "").strip().lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return None


def _safety_blockers(args: argparse.Namespace) -> list[str]:
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
    if args.apply and args.confirmation != APPLY_CONFIRMATION:
        blockers.append("invalid_apply_confirmation")
    return blockers


def _table_columns(conn, table_name: str) -> set[str]:
    from sqlalchemy import text

    rows = conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=:table_name
            """
        ),
        {"table_name": table_name},
    ).fetchall()
    return {str(row[0]) for row in rows}


def _existing_indexes(conn) -> set[str]:
    from sqlalchemy import text

    rows = conn.execute(
        text(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname='public'
            """
        )
    ).fetchall()
    return {str(row[0]) for row in rows}


def _existing_triggers(conn) -> set[str]:
    from sqlalchemy import text

    rows = conn.execute(
        text(
            """
            SELECT tgname
            FROM pg_trigger
            WHERE NOT tgisinternal
            """
        )
    ).fetchall()
    return {str(row[0]) for row in rows}


def _snapshot_group(
    conn,
    requirements: dict[str, set[str]],
) -> tuple[dict[str, Any], list[str]]:
    tables: dict[str, Any] = {}
    missing_total: list[str] = []
    for table_name, required in requirements.items():
        present = _table_columns(conn, table_name)
        missing = sorted(required - present)
        tables[table_name] = {
            "exists": bool(present),
            "required_count": len(required),
            "present_required_count": len(required & present),
            "missing_columns": missing,
        }
        missing_total.extend(
            f"{table_name}.{column}" for column in missing
        )
    return tables, sorted(missing_total)


def schema_snapshot(conn) -> dict[str, Any]:
    base_tables, base_missing = _snapshot_group(conn, BASE_REQUIRED_COLUMNS)
    management_tables, management_missing = _snapshot_group(
        conn,
        MANAGEMENT_REQUIRED_COLUMNS,
    )
    indexes = _existing_indexes(conn)
    triggers = _existing_triggers(conn)
    missing_indexes = sorted(REQUIRED_INDEXES - indexes)
    missing_triggers = sorted(REQUIRED_TRIGGERS - triggers)
    ready = not (
        base_missing
        or management_missing
        or missing_indexes
        or missing_triggers
    )
    return {
        "base": {
            "tables": base_tables,
            "missing_columns": base_missing,
            "ready": not base_missing,
        },
        "management": {
            "tables": management_tables,
            "missing_columns": management_missing,
            "missing_indexes": missing_indexes,
            "missing_triggers": missing_triggers,
            "ready": not management_missing and not missing_indexes and not missing_triggers,
        },
        "ready": ready,
    }


def _base_structure_blockers(snapshot: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for table_name, table in snapshot["base"]["tables"].items():
        if not table["exists"]:
            blockers.append(f"missing_base_table:{table_name}")
        for column in table["missing_columns"]:
            blockers.append(f"missing_base_column:{table_name}.{column}")
    return blockers


def _record_migration(conn, *, metadata: dict[str, Any]) -> None:
    from sqlalchemy import text

    from rtm_core.management_schema import RTM_MANAGEMENT_SCHEMA_VERSION

    conn.execute(
        text(
            """
            INSERT INTO rtm_management_schema_migrations(name, metadata, applied_at)
            VALUES (:name, CAST(:metadata AS JSONB), NOW())
            ON CONFLICT (name)
            DO UPDATE SET metadata=EXCLUDED.metadata, applied_at=NOW()
            """
        ),
        {
            "name": RTM_MANAGEMENT_SCHEMA_VERSION,
            "metadata": json.dumps(metadata, ensure_ascii=False),
        },
    )


def apply_management_schema(conn) -> list[str]:
    from sqlalchemy import text

    from rtm_core.management_schema import management_v1_ddl

    applied: list[str] = []
    for name, statement in management_v1_ddl():
        conn.execute(text(statement))
        applied.append(name)

    _record_migration(
        conn,
        metadata={
            "source": SCHEMA_VERSION,
            "tables": sorted(MANAGEMENT_REQUIRED_COLUMNS),
            "append_only": ["rtm_attention_events"],
            "deadline_origin_policy": "missing_origin_never_computes_due_at",
            "login_replaced": False,
            "destructive": False,
        },
    )
    return applied


def _print_report(report: dict[str, Any], *, compact: bool) -> None:
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
            default=str,
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    environment = (os.getenv("RTM_ENV") or "").strip().lower() or "unset"
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_staging_management_schema",
        "version": SCHEMA_VERSION,
        "environment": environment,
        "apply_requested": bool(args.apply),
        "confirmation_required": APPLY_CONFIRMATION,
        "destructive": False,
        "login_replaced": False,
        "applied": [],
        "blockers": [],
    }

    safety = _safety_blockers(args)
    if safety:
        report["blockers"] = safety
        report["safe"] = False
        _print_report(report, compact=args.compact)
        return 2

    try:
        from database import get_engine

        engine = get_engine()
        with engine.begin() as conn:
            before = schema_snapshot(conn)
            report["before"] = before
            base_blockers = _base_structure_blockers(before)
            if base_blockers:
                report["blockers"] = base_blockers
            elif args.apply:
                report["applied"] = apply_management_schema(conn)

            after = schema_snapshot(conn)
            report["after"] = after
            report["blockers"] = list(report["blockers"]) + [
                f"missing_after_apply:{item}"
                for item in (
                    after["base"]["missing_columns"]
                    + after["management"]["missing_columns"]
                    + after["management"]["missing_indexes"]
                    + after["management"]["missing_triggers"]
                )
            ]

        report["safe"] = not report["blockers"]
        report["ok"] = bool(report["safe"] and report["after"]["ready"])
        exit_code = 0 if report["ok"] else 1
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["safe"] = False
        report["ok"] = False
        exit_code = 1

    _print_report(report, compact=args.compact)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
