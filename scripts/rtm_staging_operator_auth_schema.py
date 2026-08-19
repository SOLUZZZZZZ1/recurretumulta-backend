#!/usr/bin/env python3
"""Audita y aplica el núcleo de autenticación individual RTM en staging.

Requiere Management Core y el historial de accesos ya instalados. ``--apply``
exige ``STAGING_OPERATOR_AUTH_SCHEMA_ONLY``. No crea operadores reales, no
publica endpoints y no sustituye el login OPS actual.
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

SCHEMA_VERSION = "rtm_staging_operator_auth_schema_v1_0"
APPLY_CONFIRMATION = "STAGING_OPERATOR_AUTH_SCHEMA_ONLY"

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}

BASE_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "rtm_management_schema_migrations": {"name", "metadata", "applied_at"},
    "rtm_operator_roles": {"id", "code", "permissions"},
    "rtm_operators": {"id", "email", "password_hash", "status"},
    "rtm_operator_sessions": {
        "id", "operator_id", "token_sha256", "status", "expires_at",
        "device_id", "login_access_event_id",
    },
    "rtm_operator_access_events": {"id", "operator_id", "session_id"},
}

AUTH_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "rtm_operators": {
        "failed_login_count", "last_failed_login_at", "locked_until",
        "password_changed_at", "password_algorithm", "password_version",
        "auth_epoch",
    },
    "rtm_operator_sessions": {
        "auth_epoch", "last_verified_at", "absolute_expires_at",
    },
}

REQUIRED_INDEXES = {
    "idx_rtm_operator_auth_lockout",
    "idx_rtm_operator_sessions_epoch",
    "idx_rtm_operator_sessions_absolute_expiry",
}

REQUIRED_CONSTRAINTS = {
    "ck_rtm_operator_failed_login_count",
    "ck_rtm_operator_password_version",
    "ck_rtm_operator_auth_epoch",
    "ck_rtm_operator_password_algorithm",
    "ck_rtm_operator_session_auth_epoch",
    "ck_rtm_operator_session_absolute_expiry",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audita o aplica autenticación individual RTM en staging."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--compact", action="store_true")
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
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name=:table_name
            """
        ),
        {"table_name": table_name},
    ).fetchall()
    return {str(row[0]) for row in rows}


def _existing_indexes(conn) -> set[str]:
    from sqlalchemy import text
    return {
        str(row[0])
        for row in conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE schemaname='public'")
        ).fetchall()
    }


def _existing_constraints(conn) -> set[str]:
    from sqlalchemy import text
    return {
        str(row[0])
        for row in conn.execute(
            text(
                """
                SELECT conname FROM pg_constraint
                WHERE connamespace='public'::regnamespace
                """
            )
        ).fetchall()
    }


def _snapshot_group(conn, requirements: dict[str, set[str]]):
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
        missing_total.extend(f"{table_name}.{column}" for column in missing)
    return tables, sorted(missing_total)


def schema_snapshot(conn) -> dict[str, Any]:
    base_tables, base_missing = _snapshot_group(conn, BASE_REQUIRED_COLUMNS)
    auth_tables, auth_missing = _snapshot_group(conn, AUTH_REQUIRED_COLUMNS)
    indexes = _existing_indexes(conn)
    constraints = _existing_constraints(conn)
    missing_indexes = sorted(REQUIRED_INDEXES - indexes)
    missing_constraints = sorted(REQUIRED_CONSTRAINTS - constraints)
    ready = not (
        base_missing or auth_missing or missing_indexes or missing_constraints
    )
    return {
        "base": {
            "tables": base_tables,
            "missing_columns": base_missing,
            "ready": not base_missing,
        },
        "operator_auth": {
            "tables": auth_tables,
            "missing_columns": auth_missing,
            "missing_indexes": missing_indexes,
            "missing_constraints": missing_constraints,
            "ready": not auth_missing
            and not missing_indexes
            and not missing_constraints,
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


def _record_migration(conn) -> None:
    from sqlalchemy import text
    from rtm_core.operator_auth_schema import RTM_OPERATOR_AUTH_SCHEMA_VERSION
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
            "name": RTM_OPERATOR_AUTH_SCHEMA_VERSION,
            "metadata": json.dumps(
                {
                    "source": SCHEMA_VERSION,
                    "password_hash": "argon2id",
                    "failed_login_lockout": True,
                    "session_token_storage": "sha256_only",
                    "auth_epoch_invalidation": True,
                    "login_replaced": False,
                    "routes_published": False,
                    "operators_created": False,
                    "destructive": False,
                }
            ),
        },
    )


def apply_operator_auth_schema(conn) -> list[str]:
    from sqlalchemy import text
    from rtm_core.operator_auth_schema import operator_auth_v1_ddl
    applied: list[str] = []
    for name, statement in operator_auth_v1_ddl():
        conn.execute(text(statement))
        applied.append(name)
    _record_migration(conn)
    return applied


def _print(report: dict[str, Any], compact: bool) -> None:
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
        "authority": "rtm_staging_operator_auth_schema",
        "version": SCHEMA_VERSION,
        "environment": environment,
        "apply_requested": bool(args.apply),
        "confirmation_required": APPLY_CONFIRMATION,
        "destructive": False,
        "login_replaced": False,
        "routes_published": False,
        "operators_created": False,
        "password_hash": "argon2id",
        "session_token_storage": "sha256_only",
        "applied": [],
        "blockers": [],
    }
    safety = _safety_blockers(args)
    if safety:
        report["blockers"] = safety
        report["safe"] = False
        _print(report, args.compact)
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
                report["applied"] = apply_operator_auth_schema(conn)
            after = schema_snapshot(conn)
            report["after"] = after
            report["blockers"] = list(report["blockers"]) + [
                f"missing_after_run:{item}"
                for item in (
                    after["base"]["missing_columns"]
                    + after["operator_auth"]["missing_columns"]
                    + after["operator_auth"]["missing_indexes"]
                    + after["operator_auth"]["missing_constraints"]
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
    _print(report, args.compact)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
