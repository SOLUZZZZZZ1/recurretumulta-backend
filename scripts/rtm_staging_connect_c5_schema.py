#!/usr/bin/env python3
"""Audita las dependencias persistentes de C5 sin aplicar DDL.

C5 reutiliza los ledgers C1/C3/C4 y la auditoria individual de operadores.
Este script es deliberadamente read-only y no ofrece ``--apply``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SCHEMA_AUDIT_VERSION = "rtm_staging_connect_c5_schema_v1_0"

REQUIRED_AUDIT_TRIGGERS = {
    "trg_rtm_operator_access_events_append_only": (
        "rtm_operator_access_events",
        "rtm_guard_operator_access_events_append_only",
    ),
    "trg_rtm_operator_access_evidence_retention": (
        "rtm_operator_access_evidence",
        "rtm_guard_operator_access_evidence_retention",
    ),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    return parser


def safety_blockers() -> list[str]:
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


def _table_columns(conn, table_name: str) -> set[str]:
    from sqlalchemy import text

    return {
        str(row[0])
        for row in conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=:table_name
                """
            ),
            {"table_name": table_name},
        ).fetchall()
    }


def _audit_trigger_contract(conn) -> dict[str, dict[str, Any]]:
    from sqlalchemy import text

    return {
        str(row["trigger_name"]): dict(row)
        for row in conn.execute(
            text(
                """
                SELECT
                    t.tgname AS trigger_name,
                    c.relname AS table_name,
                    p.proname AS function_name,
                    t.tgenabled AS enabled_mode,
                    pg_get_triggerdef(t.oid, TRUE) AS definition
                FROM pg_trigger t
                JOIN pg_class c ON c.oid=t.tgrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_proc p ON p.oid=t.tgfoid
                WHERE NOT t.tgisinternal
                  AND n.nspname='public'
                  AND c.relname IN (
                      'rtm_operator_access_events',
                      'rtm_operator_access_evidence'
                  )
                """
            )
        ).mappings().all()
    }


def schema_snapshot(conn) -> dict[str, Any]:
    from rtm_connect.supervisor_schema import (
        CONNECT_C5_REQUIRED_COLUMNS,
        CONNECT_C5_SCHEMA_CHANGES_REQUIRED,
        connect_c5_supervisor_ddl,
    )

    tables: dict[str, Any] = {}
    missing_total: list[str] = []
    for table_name, required in CONNECT_C5_REQUIRED_COLUMNS.items():
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
    trigger_rows = _audit_trigger_contract(conn)
    invalid_triggers: list[str] = []
    trigger_contract: dict[str, Any] = {}
    for name, (table_name, function_name) in REQUIRED_AUDIT_TRIGGERS.items():
        row = trigger_rows.get(name)
        definition = str((row or {}).get("definition") or "").upper()
        valid = bool(
            row
            and row.get("table_name") == table_name
            and row.get("function_name") == function_name
            and row.get("enabled_mode") in {"O", "A"}
            and "BEFORE" in definition
            and "UPDATE" in definition
            and "DELETE" in definition
            and "FOR EACH ROW" in definition
        )
        trigger_contract[name] = {
            "present": row is not None,
            "table_name": (row or {}).get("table_name"),
            "function_name": (row or {}).get("function_name"),
            "enabled": bool(row and row.get("enabled_mode") in {"O", "A"}),
            "definition_valid": valid,
        }
        if not valid:
            invalid_triggers.append(name)
    ddl = connect_c5_supervisor_ddl()
    ready = (
        not missing_total
        and not invalid_triggers
        and not ddl
        and not CONNECT_C5_SCHEMA_CHANGES_REQUIRED
    )
    return {
        "tables": tables,
        "missing_columns": sorted(missing_total),
        "audit_trigger_contract": trigger_contract,
        "invalid_audit_triggers": sorted(invalid_triggers),
        "schema_changes_required": CONNECT_C5_SCHEMA_CHANGES_REQUIRED,
        "ddl_statement_count": len(ddl),
        "ready": ready,
    }


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
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_staging_connect_c5_schema",
        "version": SCHEMA_AUDIT_VERSION,
        "environment": (
            os.getenv("RTM_ENV") or ""
        ).strip().lower() or "unset",
        "read_only": True,
        "apply_available": False,
        "schema_changes_required": False,
        "applied": [],
        "destructive": False,
        "routes_modified": False,
        "connectors_seeded": False,
        "external_effects_executed": False,
        "blockers": [],
    }
    report["blockers"].extend(safety_blockers())
    if report["blockers"]:
        report["safe"] = False
        _print(report, args.compact)
        return 2

    try:
        from sqlalchemy import text
        from database import get_engine
        from rtm_connect.supervisor_schema import (
            RTM_CONNECT_C5_SUPERVISOR_SCHEMA_VERSION,
        )
        from rtm_connect.supervisor_policy import (
            assert_connect_supervisor_database_identity,
            assert_connect_supervisor_staging_boundary,
        )

        boundary = assert_connect_supervisor_staging_boundary()
        engine = get_engine()
        with engine.connect() as conn:
            report["connected_database"] = (
                assert_connect_supervisor_database_identity(
                    conn,
                    expected_database_name=boundary.database_name,
                )
            )
            before = schema_snapshot(conn)
            after = schema_snapshot(conn)
            migration_count = int(
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM rtm_management_schema_migrations
                        WHERE name=:name
                        """
                    ),
                    {"name": RTM_CONNECT_C5_SUPERVISOR_SCHEMA_VERSION},
                ).scalar_one()
            )
        report["before"] = before
        report["after"] = after
        report["c5_migration_registered"] = migration_count != 0
        if not after["ready"]:
            report["blockers"].append("connect_c5_dependencies_not_ready")
        if migration_count:
            report["blockers"].append(
                "unexpected_connect_c5_migration_registered"
            )
        report["unchanged"] = before == after
        report["safe"] = not report["blockers"] and report["unchanged"]
        report["ok"] = bool(report["safe"])
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["safe"] = False
        report["ok"] = False
    _print(report, args.compact)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
