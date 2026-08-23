#!/usr/bin/env python3
"""Audita y aplica el esquema C4 webhook/reconciliation solo en staging.

``--apply`` exige ``STAGING_CONNECT_C4_SCHEMA_ONLY``. El DDL es aditivo,
idempotente y no destructivo. No publica rutas, no siembra conectores ni
ejecuta efectos externos.
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

SCHEMA_VERSION = "rtm_staging_connect_c4_schema_v1_0"
APPLY_CONFIRMATION = "STAGING_CONNECT_C4_SCHEMA_ONLY"
_TRUE = {"1", "true", "yes", "on", "enabled"}
_FALSE = {"0", "false", "no", "off", "disabled"}

BASE_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "rtm_management_schema_migrations": {
        "name", "metadata", "applied_at",
    },
    "rtm_operators": {"id", "email", "status"},
    "rtm_connect_connectors": {
        "id", "code", "version", "mode", "synthetic_only",
        "supports_reconciliation",
    },
    "rtm_connect_actions": {
        "id", "status", "payload_sha256", "idempotency_key",
        "external_reference", "unknown_since",
    },
    "rtm_connect_attempts": {
        "id", "action_id", "connector_id", "status",
        "request_sha256", "external_reference", "reconciliation_required",
    },
    "rtm_connect_evidence": {
        "id", "action_id", "attempt_id", "evidence_level",
        "request_sha256", "receipt_sha256",
    },
    "rtm_connect_transitions": {
        "id", "action_id", "attempt_id", "sequence_number",
        "from_status", "to_status",
    },
    "rtm_connect_manual_tasks": {"id", "action_id", "status"},
    "rtm_connect_manual_events": {
        "id", "task_id", "sequence_number", "event_type",
    },
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--compact", action="store_true")
    return parser


def _flag(name: str) -> bool | None:
    raw = (os.getenv(name) or "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def _safety_blockers(args: argparse.Namespace) -> list[str]:
    blockers: list[str] = []
    if (os.getenv("RTM_ENV") or "").strip().lower() != "staging":
        blockers.append("RTM_ENV_must_be_staging")
    if "staging" not in (os.getenv("RTM_DATA_NAMESPACE") or "").lower():
        blockers.append("RTM_DATA_NAMESPACE_must_identify_staging")
    if (os.getenv("RTM_SIDE_EFFECT_POLICY") or "").strip().lower() != "isolated":
        blockers.append("RTM_SIDE_EFFECT_POLICY_must_be_isolated")
    if _flag("RTM_ALLOW_REAL_CUSTOMER_DATA") is not False:
        blockers.append("RTM_ALLOW_REAL_CUSTOMER_DATA_must_be_false")
    for name in (
        "RTM_ENABLE_EXTERNAL_SUBMISSION",
        "RTM_ENABLE_OUTBOUND_EMAIL",
        "RTM_ENABLE_STRIPE",
        "RTM_ENABLE_FINAL_PAYMENTS",
    ):
        if _flag(name) is not False:
            blockers.append(f"{name}_must_be_false")
    if args.apply and args.confirmation != APPLY_CONFIRMATION:
        blockers.append("invalid_apply_confirmation")
    return blockers


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


def _existing_indexes(conn) -> set[str]:
    from sqlalchemy import text

    return {
        str(row[0])
        for row in conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE schemaname='public'")
        ).fetchall()
    }


def _existing_triggers(conn) -> set[str]:
    from sqlalchemy import text

    return {
        str(row[0])
        for row in conn.execute(
            text(
                """
                SELECT t.tgname
                FROM pg_trigger t
                JOIN pg_class c ON c.oid=t.tgrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE NOT t.tgisinternal
                  AND n.nspname='public'
                  AND c.relname IN (
                      'rtm_connect_webhook_inbox',
                      'rtm_connect_webhook_events',
                      'rtm_connect_reconciliations',
                      'rtm_connect_reconciliation_events'
                  )
                """
            )
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
        missing_total.extend(
            f"{table_name}.{column}" for column in missing
        )
    return tables, sorted(missing_total)


def schema_snapshot(conn) -> dict[str, Any]:
    from rtm_connect.webhook_schema import (
        CONNECT_C4_REQUIRED_COLUMNS,
        CONNECT_C4_REQUIRED_CONSTRAINTS,
        CONNECT_C4_REQUIRED_INDEXES,
        CONNECT_C4_REQUIRED_TRIGGERS,
    )

    base_tables, base_missing = _snapshot_group(conn, BASE_REQUIRED_COLUMNS)
    c4_tables, c4_missing = _snapshot_group(
        conn, CONNECT_C4_REQUIRED_COLUMNS
    )
    missing_indexes = sorted(
        CONNECT_C4_REQUIRED_INDEXES - _existing_indexes(conn)
    )
    missing_triggers = sorted(
        CONNECT_C4_REQUIRED_TRIGGERS - _existing_triggers(conn)
    )
    missing_constraints = sorted(
        CONNECT_C4_REQUIRED_CONSTRAINTS - _existing_constraints(conn)
    )
    return {
        "base": {
            "tables": base_tables,
            "missing_columns": base_missing,
            "ready": not base_missing,
        },
        "connect_c4": {
            "tables": c4_tables,
            "missing_columns": c4_missing,
            "missing_indexes": missing_indexes,
            "missing_triggers": missing_triggers,
            "missing_constraints": missing_constraints,
            "ready": not (
                c4_missing
                or missing_indexes
                or missing_triggers
                or missing_constraints
            ),
        },
        "ready": not (
            base_missing
            or c4_missing
            or missing_indexes
            or missing_triggers
            or missing_constraints
        ),
    }


def _base_blockers(snapshot: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for table_name, table in snapshot["base"]["tables"].items():
        if not table["exists"]:
            blockers.append(f"missing_base_table:{table_name}")
        blockers.extend(
            f"missing_base_column:{table_name}.{column}"
            for column in table["missing_columns"]
        )
    return blockers


def apply_schema(conn) -> list[str]:
    from sqlalchemy import text
    from rtm_connect.webhook_schema import (
        RTM_CONNECT_C4_WEBHOOK_SCHEMA_VERSION,
        connect_c4_webhook_ddl,
    )

    applied: list[str] = []
    for name, statement in connect_c4_webhook_ddl():
        conn.execute(text(statement))
        applied.append(name)
    conn.execute(
        text(
            """
            INSERT INTO rtm_management_schema_migrations(
                name, metadata, applied_at
            )
            VALUES (:name, CAST(:metadata AS JSONB), NOW())
            ON CONFLICT (name)
            DO UPDATE SET metadata=EXCLUDED.metadata, applied_at=NOW()
            """
        ),
        {
            "name": RTM_CONNECT_C4_WEBHOOK_SCHEMA_VERSION,
            "metadata": json.dumps(
                {
                    "source": SCHEMA_VERSION,
                    "architecture": "rtm_connect_architecture_v1_0",
                    "c1_schema_required": True,
                    "c3_schema_required": True,
                    "tables": [
                        "rtm_connect_webhook_inbox",
                        "rtm_connect_webhook_events",
                        "rtm_connect_reconciliations",
                        "rtm_connect_reconciliation_events",
                    ],
                    "append_only": [
                        "rtm_connect_webhook_events",
                        "rtm_connect_reconciliation_events",
                    ],
                    "dead_letter_model": "webhook_terminal_state",
                    "routes_published": False,
                    "connectors_seeded": False,
                    "external_effects": False,
                    "destructive": False,
                },
                ensure_ascii=False,
            ),
        },
    )
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
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_staging_connect_c4_schema",
        "version": SCHEMA_VERSION,
        "environment": (
            os.getenv("RTM_ENV") or ""
        ).strip().lower() or "unset",
        "apply_requested": bool(args.apply),
        "confirmation_required": APPLY_CONFIRMATION,
        "destructive": False,
        "routes_published": False,
        "connectors_seeded": False,
        "external_effects_executed": False,
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
        from rtm_connect.connectors.synthetic_webhook import (
            assert_synthetic_webhook_manifest_frozen,
        )
        from rtm_connect.manifest import assert_manifest_frozen

        assert_manifest_frozen()
        assert_synthetic_webhook_manifest_frozen()
        engine = get_engine()
        with engine.begin() as conn:
            before = schema_snapshot(conn)
            report["before"] = before
            blockers = _base_blockers(before)
            if blockers:
                report["blockers"] = blockers
            elif args.apply:
                report["applied"] = apply_schema(conn)
            after = schema_snapshot(conn)
            report["after"] = after
            report["blockers"] = list(report["blockers"]) + [
                f"missing_after_run:{item}"
                for item in (
                    after["base"]["missing_columns"]
                    + after["connect_c4"]["missing_columns"]
                    + after["connect_c4"]["missing_indexes"]
                    + after["connect_c4"]["missing_triggers"]
                    + after["connect_c4"]["missing_constraints"]
                )
            ]
        report["safe"] = not report["blockers"]
        report["ok"] = bool(report["safe"] and report["after"]["ready"])
        code = 0 if report["ok"] else 1
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["safe"] = False
        report["ok"] = False
        code = 1
    _print(report, args.compact)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
