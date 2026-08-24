#!/usr/bin/env python3
"""Audita y aplica el esquema aditivo RTM CONNECT C7 en staging.

``--apply`` exige la confirmacion exacta
``STAGING_CONNECT_C7_SCHEMA_ONLY``. El script no publica rutas, no registra
conectores y no ejecuta efectos externos.
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


SCHEMA_VERSION = "rtm_staging_connect_c7_schema_v1_0"
APPLY_CONFIRMATION = "STAGING_CONNECT_C7_SCHEMA_ONLY"


BASE_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "rtm_management_schema_migrations": {
        "name", "metadata", "applied_at",
    },
    "rtm_operators": {
        "id", "status", "primary_role_id",
    },
    "rtm_operator_roles": {
        "id", "permissions", "active",
    },
    "rtm_connect_connectors": {
        "id", "code", "version", "mode", "status", "environment",
        "synthetic_only", "credential_ref",
    },
    "rtm_connect_actions": {
        "id", "status", "status_version", "payload_sha256",
        "idempotency_key", "current_connector_id",
    },
    "rtm_connect_authorizations": {
        "id", "action_id", "authorization_version", "payload_sha256",
        "idempotency_key", "required_evidence_level",
        "authorized_connector_modes", "approved_by_operator_ids",
        "legal_effect_authorized", "frozen", "revoked_at",
    },
    "rtm_connect_attempts": {
        "id", "action_id", "connector_id", "status",
        "reconciliation_required",
    },
    "rtm_connect_evidence": {
        "id", "action_id", "attempt_id", "evidence_level",
        "request_sha256", "external_reference", "receipt_sha256",
        "receipt_storage_ref", "verified_at", "verification_method",
        "verified_by_operator_id",
    },
    "rtm_connect_manual_tasks": {
        "id", "action_id", "attempt_id",
    },
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--compact", action="store_true")
    return parser


def safety_blockers(
    args: argparse.Namespace | None = None,
) -> list[str]:
    """Devuelve bloqueos fail-closed antes de abrir la base de datos."""

    blockers: list[str] = []
    try:
        from rtm_connect.assisted_legal_policy import (
            AssistedLegalRuntimeDisabled,
            assert_c7_staging_boundary,
            load_c7_runtime_configuration,
        )

        assert_c7_staging_boundary(os.environ)
        try:
            load_c7_runtime_configuration(os.environ)
        except AssistedLegalRuntimeDisabled as exc:
            blockers.append(f"connect_c7_runtime_must_remain_disabled:{exc}")
    except Exception as exc:
        blockers.append(
            "connect_c7_staging_boundary_blocked:"
            f"{type(exc).__name__}:{exc}"
        )
    if args is not None and args.apply:
        if args.confirmation != APPLY_CONFIRMATION:
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
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname='public'"
            )
        ).fetchall()
    }


def _existing_triggers(conn) -> set[str]:
    from sqlalchemy import text

    return {
        str(row[0])
        for row in conn.execute(
            text(
                "SELECT tgname FROM pg_trigger "
                "WHERE NOT tgisinternal"
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
    """Audita dependencias C1-C6 y los objetos propios de C7."""

    from rtm_connect.assisted_schema import (
        CONNECT_C7_REQUIRED_COLUMNS,
        CONNECT_C7_REQUIRED_CONSTRAINTS,
        CONNECT_C7_REQUIRED_INDEXES,
        CONNECT_C7_REQUIRED_TRIGGERS,
    )
    from scripts.rtm_staging_connect_c6_schema import (
        schema_snapshot as c6_schema_snapshot,
    )

    dependencies = c6_schema_snapshot(conn)
    base_tables, base_missing = _snapshot_group(
        conn,
        BASE_REQUIRED_COLUMNS,
    )
    c7_tables, c7_missing = _snapshot_group(
        conn,
        CONNECT_C7_REQUIRED_COLUMNS,
    )
    missing_indexes = sorted(
        CONNECT_C7_REQUIRED_INDEXES - _existing_indexes(conn)
    )
    missing_triggers = sorted(
        CONNECT_C7_REQUIRED_TRIGGERS - _existing_triggers(conn)
    )
    missing_constraints = sorted(
        CONNECT_C7_REQUIRED_CONSTRAINTS
        - _existing_constraints(conn)
    )
    c7_ready = not (
        c7_missing
        or missing_indexes
        or missing_triggers
        or missing_constraints
    )
    return {
        "dependencies": dependencies,
        "base": {
            "tables": base_tables,
            "missing_columns": base_missing,
            "ready": not base_missing,
        },
        "connect_c7": {
            "tables": c7_tables,
            "missing_columns": c7_missing,
            "missing_indexes": missing_indexes,
            "missing_triggers": missing_triggers,
            "missing_constraints": missing_constraints,
            "ready": c7_ready,
        },
        "ready": bool(
            dependencies.get("ready")
            and not base_missing
            and c7_ready
        ),
    }


def _base_blockers(snapshot: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if not snapshot["dependencies"].get("ready"):
        blockers.append("connect_c7_dependencies_not_ready")
    for table_name, table in snapshot["base"]["tables"].items():
        if not table["exists"]:
            blockers.append(f"missing_base_table:{table_name}")
        blockers.extend(
            f"missing_base_column:{table_name}.{column}"
            for column in table["missing_columns"]
        )
    return blockers


def apply_schema(conn) -> list[str]:
    """Aplica exclusivamente DDL C7 y registra su migracion."""

    from sqlalchemy import text
    from rtm_connect.assisted_schema import (
        RTM_CONNECT_C7_ASSISTED_SCHEMA_VERSION,
        connect_c7_assisted_ddl,
    )

    applied: list[str] = []
    for name, statement in connect_c7_assisted_ddl():
        conn.execute(text(statement))
        applied.append(name)
    conn.execute(
        text(
            """
            INSERT INTO rtm_management_schema_migrations(
                name, metadata, applied_at
            ) VALUES (:name, CAST(:metadata AS JSONB), NOW())
            ON CONFLICT (name)
            DO UPDATE SET
                metadata=EXCLUDED.metadata,
                applied_at=NOW()
            """
        ),
        {
            "name": RTM_CONNECT_C7_ASSISTED_SCHEMA_VERSION,
            "metadata": json.dumps(
                {
                    "source": SCHEMA_VERSION,
                    "architecture": "rtm_connect_architecture_v1_0",
                    "tables": [
                        "rtm_connect_assisted_tasks",
                        "rtm_connect_assisted_events",
                    ],
                    "risk_class": "R4_critical_regulated",
                    "human_final_submit_required": True,
                    "append_only": ["rtm_connect_assisted_events"],
                    "package_frozen": True,
                    "routes_published": False,
                    "connectors_seeded": False,
                    "network_used": False,
                    "external_effects": False,
                    "destructive": False,
                },
                ensure_ascii=False,
                sort_keys=True,
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
        "authority": "rtm_staging_connect_c7_schema",
        "version": SCHEMA_VERSION,
        "environment": (
            os.getenv("RTM_ENV") or ""
        ).strip().lower() or "unset",
        "apply_requested": bool(args.apply),
        "confirmation_required": APPLY_CONFIRMATION,
        "destructive": False,
        "routes_published": False,
        "connectors_seeded": False,
        "network_used": False,
        "external_effects_executed": False,
        "applied": [],
        "blockers": [],
    }
    report["blockers"] = safety_blockers(args)
    if report["blockers"]:
        report["safe"] = False
        _print(report, args.compact)
        return 2

    try:
        from database import get_engine
        from rtm_connect.manifest import assert_manifest_frozen

        assert_manifest_frozen()
        engine = get_engine()
        with engine.begin() as conn:
            before = schema_snapshot(conn)
            report["before"] = before
            report["blockers"].extend(_base_blockers(before))
            if not report["blockers"] and args.apply:
                report["applied"] = apply_schema(conn)
            after = schema_snapshot(conn)
            report["after"] = after
            c7 = after["connect_c7"]
            report["blockers"].extend(
                f"missing_after_run:{item}"
                for item in (
                    after["base"]["missing_columns"]
                    + c7["missing_columns"]
                    + c7["missing_indexes"]
                    + c7["missing_triggers"]
                    + c7["missing_constraints"]
                )
            )
        report["safe"] = not report["blockers"]
        report["ok"] = bool(
            report["safe"] and report["after"]["ready"]
        )
        code = 0 if report["ok"] else 1
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["safe"] = False
        code = 1
    _print(report, args.compact)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
