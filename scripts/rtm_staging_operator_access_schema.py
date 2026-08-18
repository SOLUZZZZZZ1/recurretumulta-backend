#!/usr/bin/env python3
"""Audita y aplica historial de accesos RTM únicamente en staging.

La utilidad exige Management Core V1 instalado. ``--apply`` requiere la
confirmación literal ``STAGING_OPERATOR_ACCESS_SCHEMA_ONLY``. Solo ejecuta DDL
aditivo e idempotente y no sustituye el login OPS actual.
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


SCHEMA_VERSION = "rtm_staging_operator_access_schema_v1_0"
APPLY_CONFIRMATION = "STAGING_OPERATOR_ACCESS_SCHEMA_ONLY"

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}

BASE_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "rtm_management_schema_migrations": {"name", "metadata", "applied_at"},
    "rtm_operator_roles": {"id"},
    "rtm_operators": {"id", "email", "status"},
    "rtm_operator_sessions": {
        "id", "operator_id", "ip_address", "user_agent", "login_at",
        "last_seen_at", "expires_at", "status",
    },
}

ACCESS_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "rtm_operator_devices": {
        "id", "operator_id", "device_key_sha256", "status", "display_name",
        "device_type", "os_family", "os_version", "browser_family",
        "browser_version", "first_seen_at", "last_seen_at",
        "first_ip_hash_sha256", "last_ip_hash_sha256", "trusted_at",
        "trusted_by", "revoked_at", "revoked_by", "revocation_reason",
        "metadata", "created_at", "updated_at",
    },
    "rtm_operator_access_events": {
        "id", "operator_id", "session_id", "device_id", "event_type", "result",
        "auth_method", "occurred_at", "login_identifier_sha256", "ip_masked",
        "ip_hash_sha256", "ip_family", "ip_source", "ip_trusted",
        "device_key_sha256", "device_type", "os_family", "os_version",
        "browser_family", "browser_version", "country_code", "region", "city",
        "timezone", "location_source", "request_id", "reason_code",
        "reason_detail", "risk_flags", "metadata", "created_at",
    },
    "rtm_operator_access_evidence": {
        "id", "access_event_id", "ip_address", "raw_user_agent",
        "trusted_headers", "retention_until", "created_at",
    },
    "rtm_operator_sessions": {
        "device_id", "login_access_event_id", "ip_source", "ip_trusted",
        "country_code", "region", "city", "timezone", "risk_flags",
    },
}

REQUIRED_INDEXES = {
    "uq_rtm_operator_device_key",
    "idx_rtm_operator_devices_status",
    "idx_rtm_operator_access_operator_time",
    "idx_rtm_operator_access_session_time",
    "idx_rtm_operator_access_device_time",
    "idx_rtm_operator_access_ip_hash_time",
    "idx_rtm_operator_access_result_time",
    "idx_rtm_operator_access_login_identifier",
    "idx_rtm_operator_access_evidence_retention",
    "idx_rtm_operator_sessions_device_active",
}

REQUIRED_TRIGGERS = {
    "trg_rtm_operator_access_events_append_only",
    "trg_rtm_operator_access_evidence_retention",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audita o aplica historial de accesos RTM en staging.",
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
    access_tables, access_missing = _snapshot_group(
        conn,
        ACCESS_REQUIRED_COLUMNS,
    )
    indexes = _existing_indexes(conn)
    triggers = _existing_triggers(conn)
    missing_indexes = sorted(REQUIRED_INDEXES - indexes)
    missing_triggers = sorted(REQUIRED_TRIGGERS - triggers)
    ready = not (
        base_missing
        or access_missing
        or missing_indexes
        or missing_triggers
    )
    return {
        "base": {
            "tables": base_tables,
            "missing_columns": base_missing,
            "ready": not base_missing,
        },
        "operator_access": {
            "tables": access_tables,
            "missing_columns": access_missing,
            "missing_indexes": missing_indexes,
            "missing_triggers": missing_triggers,
            "ready": not access_missing
            and not missing_indexes
            and not missing_triggers,
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

    from rtm_core.operator_access_schema import (
        RTM_OPERATOR_ACCESS_SCHEMA_VERSION,
    )

    conn.execute(
        text(
            """
            INSERT INTO rtm_management_schema_migrations(
                name,
                metadata,
                applied_at
            )
            VALUES (:name, CAST(:metadata AS JSONB), NOW())
            ON CONFLICT (name)
            DO UPDATE SET metadata=EXCLUDED.metadata, applied_at=NOW()
            """
        ),
        {
            "name": RTM_OPERATOR_ACCESS_SCHEMA_VERSION,
            "metadata": json.dumps(metadata, ensure_ascii=False),
        },
    )


def apply_operator_access_schema(conn) -> list[str]:
    from sqlalchemy import text

    from rtm_core.operator_access_schema import operator_access_v1_ddl

    applied: list[str] = []
    for name, statement in operator_access_v1_ddl():
        conn.execute(text(statement))
        applied.append(name)

    _record_migration(
        conn,
        metadata={
            "source": SCHEMA_VERSION,
            "tables": [
                "rtm_operator_devices",
                "rtm_operator_access_events",
                "rtm_operator_access_evidence",
            ],
            "session_extension": True,
            "event_append_only": True,
            "raw_ip_separated": True,
            "opaque_device_id_only": True,
            "gps_collected": False,
            "hardware_fingerprint_collected": False,
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
        "authority": "rtm_staging_operator_access_schema",
        "version": SCHEMA_VERSION,
        "environment": environment,
        "apply_requested": bool(args.apply),
        "confirmation_required": APPLY_CONFIRMATION,
        "destructive": False,
        "login_replaced": False,
        "raw_ip_separated": True,
        "opaque_device_id_only": True,
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
                report["applied"] = apply_operator_access_schema(conn)

            after = schema_snapshot(conn)
            report["after"] = after
            report["blockers"] = list(report["blockers"]) + [
                f"missing_after_run:{item}"
                for item in (
                    after["base"]["missing_columns"]
                    + after["operator_access"]["missing_columns"]
                    + after["operator_access"]["missing_indexes"]
                    + after["operator_access"]["missing_triggers"]
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
