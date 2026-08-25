#!/usr/bin/env python3
"""Audita y aplica exclusivamente el esquema aditivo A1S en staging.

La aplicacion exige ``STAGING_CONNECT_A1S_SCHEMA_ONLY``. No publica rutas,
no crea operadores o tenants, no siembra expedientes y no usa red de
proveedor/Administracion, B2 ni B2B. El modo audit/apply si abre la conexion
PostgreSQL staging autorizada y carga su configuracion de base de datos.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

A1S_SCHEMA_SCRIPT_VERSION = "rtm_staging_connect_a1s_schema_v1_0_1"
APPLY_CONFIRMATION = "STAGING_CONNECT_A1S_SCHEMA_ONLY"

BASE_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "rtm_management_schema_migrations": {"name", "metadata", "applied_at"},
    "rtm_operators": {
        "id", "status", "display_name", "primary_role_id", "must_change_password",
        "mfa_required", "locked_until",
    },
    "rtm_operator_roles": {"id", "permissions", "active"},
    "cases": {"id", "test_mode"},
    "documents": {
        "id", "case_id", "kind", "sha256", "mime", "size_bytes",
        "b2_bucket", "b2_key",
    },
    "rtm_connect_connectors": {
        "id", "code", "version", "mode", "status", "environment",
        "synthetic_only", "credential_ref", "capabilities", "configuration",
        "risk_ceiling", "supports_idempotency", "supports_reconciliation",
        "created_at", "updated_at",
    },
    "rtm_connect_actions": {
        "id", "case_id", "status", "status_version", "payload",
        "payload_sha256", "capability", "satellite", "target_type",
        "target_ref", "document_hashes", "requested_by_operator_id",
        "requested_at", "risk_class", "correlation_id",
        "requires_dual_control", "contract_version", "current_connector_id",
        "idempotency_key", "external_reference", "next_attempt_at",
        "unknown_since", "confirmed_at", "cancelled_at", "metadata",
        "created_at", "updated_at",
    },
    "rtm_connect_authorizations": {
        "id", "action_id", "authorization_version", "authority_code",
        "authority_version", "decision", "payload_sha256", "idempotency_key",
        "required_evidence_level", "authorized_connector_modes",
        "approved_by_operator_ids", "authorized_at", "expires_at",
        "revoked_at", "legal_effect_authorized", "frozen", "supersedes_id",
        "metadata", "created_at",
    },
    "rtm_connect_attempts": {
        "id", "action_id", "connector_id", "attempt_number", "status",
        "started_at", "finished_at", "request_sha256", "external_reference",
        "failure_class", "error_code", "retryable", "reconciliation_required",
        "request_metadata", "result_metadata", "created_at", "updated_at",
    },
    "rtm_connect_evidence": {
        "id", "action_id", "attempt_id", "sequence_number",
        "evidence_level", "request_sha256", "external_reference",
        "receipt_sha256", "receipt_storage_ref", "verified_at",
        "verification_method", "verified_by_operator_id", "metadata",
        "created_at",
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
    values: Mapping[str, str] | None = None,
) -> list[str]:
    """Valida frontera staging antes de importar motor o abrir conexion."""

    blockers: list[str] = []
    try:
        from rtm_connect.human_filing_policy import (
            assert_a1s_staging_boundary,
            load_a1s_runtime_configuration,
        )

        assert_a1s_staging_boundary(values if values is not None else os.environ)
        load_a1s_runtime_configuration(
            values if values is not None else os.environ,
            require_enabled=False,
        )
    except Exception as exc:
        blockers.append(f"connect_a1s_staging_boundary_blocked:{type(exc).__name__}:{exc}")
    if args is not None and args.apply and args.confirmation != APPLY_CONFIRMATION:
        blockers.append("invalid_apply_confirmation")
    return blockers


def _table_columns(conn: Any, table_name: str) -> set[str]:
    from sqlalchemy import text

    return {
        str(row[0])
        for row in conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=:table_name"
        ), {"table_name": table_name}).fetchall()
    }


def _catalog_names(conn: Any, query: str) -> set[str]:
    from sqlalchemy import text

    return {str(row[0]) for row in conn.execute(text(query)).fetchall()}


def _requirements() -> tuple[dict[str, set[str]], set[str], set[str], set[str]]:
    from rtm_connect import human_filing_schema as schema

    columns = getattr(schema, "CONNECT_A1S_REQUIRED_COLUMNS")
    indexes = set(getattr(schema, "CONNECT_A1S_REQUIRED_INDEXES", set()))
    triggers = set(getattr(schema, "CONNECT_A1S_REQUIRED_TRIGGERS", set()))
    constraints = set(getattr(schema, "CONNECT_A1S_REQUIRED_CONSTRAINTS", set()))
    return columns, indexes, triggers, constraints


def _table_snapshot(conn: Any, requirements: dict[str, set[str]]) -> tuple[dict[str, Any], list[str]]:
    tables: dict[str, Any] = {}
    missing_total: list[str] = []
    for table_name, required in requirements.items():
        present = _table_columns(conn, table_name)
        missing = sorted(set(required) - present)
        tables[table_name] = {
            "exists": bool(present),
            "missing_columns": missing,
            "required_count": len(required),
            "present_required_count": len(set(required) & present),
        }
        missing_total.extend(f"{table_name}.{column}" for column in missing)
    return tables, sorted(missing_total)


def schema_snapshot(conn: Any) -> dict[str, Any]:
    columns, indexes, triggers, constraints = _requirements()
    base_tables, base_missing = _table_snapshot(conn, BASE_REQUIRED_COLUMNS)
    a1s_tables, a1s_missing = _table_snapshot(conn, columns)
    present_indexes = _catalog_names(
        conn, "SELECT indexname FROM pg_indexes WHERE schemaname='public'",
    )
    present_triggers = _catalog_names(
        conn, "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal",
    )
    present_constraints = _catalog_names(
        conn,
        "SELECT conname FROM pg_constraint WHERE connamespace='public'::regnamespace",
    )
    missing_indexes = sorted(indexes - present_indexes)
    missing_triggers = sorted(triggers - present_triggers)
    missing_constraints = sorted(constraints - present_constraints)
    return {
        "base": {
            "tables": base_tables,
            "missing_columns": base_missing,
            "ready": not base_missing,
        },
        "connect_a1s": {
            "tables": a1s_tables,
            "missing_columns": a1s_missing,
            "missing_indexes": missing_indexes,
            "missing_triggers": missing_triggers,
            "missing_constraints": missing_constraints,
            "ready": not (
                a1s_missing or missing_indexes or missing_triggers or missing_constraints
            ),
        },
        "ready": not (
            base_missing or a1s_missing or missing_indexes or missing_triggers or missing_constraints
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


def apply_schema(conn: Any) -> list[str]:
    """Aplica DDL A1S idempotente y ledger inmutable; nunca hace seed."""

    from sqlalchemy import text
    from rtm_connect.human_filing_schema import (
        RTM_CONNECT_A1S_SCHEMA_VERSION,
        connect_a1s_human_filing_ddl,
    )

    # The DDL is trusted static PostgreSQL, not a parameterized application
    # query.  Execute it through the raw driver so JSON literals and PL/pgSQL
    # colons/percent signs are never reinterpreted as SQLAlchemy bind markers.
    applied: list[str] = []
    for name, statement in connect_a1s_human_filing_ddl():
        conn.exec_driver_sql(
            statement,
            execution_options={"no_parameters": True},
        )
        applied.append(str(name))
    metadata = {
        "source": A1S_SCHEMA_SCRIPT_VERSION,
        "architecture": "rtm_connect_architecture_v1_0",
        "scope": "synthetic_human_filing_staging_only",
        "tables": [
            "rtm_connect_a1s_tenants",
            "rtm_connect_a1s_memberships",
            "rtm_connect_a1s_case_bindings",
            "rtm_connect_a1s_representation_evidence",
            "rtm_connect_a1s_human_tasks",
            "rtm_connect_a1s_approvals",
            "rtm_connect_a1s_artifacts",
            "rtm_connect_a1s_events",
            "rtm_connect_a1s_idempotency",
        ],
        "synthetic_only": True,
        "real_data_allowed": False,
        "routes_published": False,
        "operators_seeded": False,
        "tenants_seeded": False,
        "cases_seeded": False,
        "provider_contacted": False,
        "administration_contacted": False,
        "provider_network_used": False,
        "administration_network_used": False,
        "database_connection_used": True,
        "b2_used": False,
        "b2b_enabled": False,
        "external_effects": False,
        "destructive": False,
    }
    conn.execute(text(
        "INSERT INTO rtm_management_schema_migrations(name, metadata, applied_at) "
        "VALUES (:name, CAST(:metadata AS JSONB), NOW()) "
        "ON CONFLICT (name) DO NOTHING"
    ), {
        "name": RTM_CONNECT_A1S_SCHEMA_VERSION,
        "metadata": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
    })
    stored = conn.execute(text(
        "SELECT metadata FROM rtm_management_schema_migrations WHERE name=:name"
    ), {"name": RTM_CONNECT_A1S_SCHEMA_VERSION}).scalar_one()
    stored_source = (stored if isinstance(stored, dict) else json.loads(stored)).get("source")
    if stored_source != A1S_SCHEMA_SCRIPT_VERSION:
        raise RuntimeError("a1s_schema_migration_ledger_metadata_mismatch")
    return applied


def _print(report: dict[str, Any], compact: bool) -> None:
    print(json.dumps(
        report,
        ensure_ascii=False,
        indent=None if compact else 2,
        separators=(",", ":") if compact else None,
        sort_keys=True,
        default=str,
    ))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "ok": False,
        "safe": False,
        "authority": "rtm_staging_connect_a1s_schema",
        "version": A1S_SCHEMA_SCRIPT_VERSION,
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "apply_requested": bool(args.apply),
        "confirmation_required": APPLY_CONFIRMATION,
        "destructive": False,
        "schema_only": True,
        "read_only": not bool(args.apply),
        "synthetic_only": True,
        "real_data_used": False,
        "live_verdict": "no_go",
        "production_authorized": False,
        "production_effects_available": False,
        "routes_published": False,
        "operators_seeded": False,
        "tenants_seeded": False,
        "cases_seeded": False,
        "provider_contacted": False,
        "administration_contacted": False,
        "provider_network_used": False,
        "administration_network_used": False,
        "database_connection_used": False,
        "database_configuration_loaded": False,
        "b2_used": False,
        "b2b_enabled": False,
        "workers_started": False,
        "external_effects_executed": False,
        "database_touched": False,
        "schema_changes_applied": False,
        "external_secret_resolution_performed": False,
        "applied": [],
        "blockers": [],
    }
    report["blockers"] = safety_blockers(args)
    if report["blockers"]:
        _print(report, args.compact)
        return 2
    try:
        from database import get_engine
        from rtm_connect.human_filing_policy import (
            assert_a1s_database_identity,
            assert_a1s_staging_boundary,
        )

        boundary = assert_a1s_staging_boundary(os.environ)
        report["database_configuration_loaded"] = True
        engine = get_engine()
        with engine.begin() as conn:
            report["database_connection_used"] = True
            report["database_touched"] = True
            assert_a1s_database_identity(
                conn,
                expected_database_name=boundary.database_name,
                expected_database_role=boundary.database_role,
            )
            before = schema_snapshot(conn)
            report["before"] = before
            report["blockers"].extend(_base_blockers(before))
            if not report["blockers"] and args.apply:
                report["applied"] = apply_schema(conn)
                report["schema_changes_applied"] = bool(report["applied"])
            after = schema_snapshot(conn)
            report["after"] = after
            if args.apply and not after["ready"]:
                report["blockers"].append("a1s_schema_not_ready_after_apply")
        report["ok"] = not report["blockers"] and bool(report["after"]["ready"])
        report["safe"] = not report["blockers"]
        code = 0 if report["ok"] else 1
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        code = 1
    _print(report, args.compact)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
