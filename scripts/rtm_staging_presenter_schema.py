#!/usr/bin/env python3
"""Audita y aplica el esquema de RTM Presenter solo en staging sintetico.

La utilidad es deliberadamente fail-closed: valida la frontera aislada, la
identidad efectiva de PostgreSQL y la huella del contrato DDL antes de aplicar
la migracion dentro de una unica transaccion. No siembra perfiles, documentos,
expedientes u operadores; tampoco resuelve secretos, accede a B2 ni habilita
efectos externos.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


PRESENTER_SCHEMA_SCRIPT_VERSION = "rtm_staging_presenter_schema_v1_0"
APPLY_CONFIRMATION = "STAGING_PRESENTER_SCHEMA_ONLY"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", "disabled"})
_LIVE_MARKERS = ("production", "prod", "live")
_FORBIDDEN_DDL = re.compile(
    r"(?im)^\s*(?:DROP|TRUNCATE|DELETE|INSERT|UPDATE)\b"
)

BASE_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "rtm_management_schema_migrations": {"name", "metadata", "applied_at"},
    "cases": {"id", "test_mode"},
    "documents": {"id", "case_id", "sha256", "size_bytes"},
    "rtm_operator_roles": {"id", "code", "permissions", "active"},
    "rtm_operators": {"id", "status", "primary_role_id"},
    "rtm_operator_sessions": {
        "id",
        "operator_id",
        "status",
        "login_at",
        "last_verified_at",
        "expires_at",
        "absolute_expires_at",
    },
    "rtm_operator_access_events": {
        "id",
        "operator_id",
        "session_id",
        "event_type",
        "result",
        "reason_code",
        "occurred_at",
    },
}


class PresenterSchemaMigrationError(RuntimeError):
    """La migracion Presenter no cumple su frontera o contrato."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audita o aplica RTM Presenter solo en staging sintetico.",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--compact", action="store_true")
    return parser


def _flag(values: Mapping[str, str], name: str) -> bool | None:
    raw = str(values.get(name) or "").strip().lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return None


def _database_identity_from_url(values: Mapping[str, str]) -> tuple[str, str]:
    raw_url = str(values.get("DATABASE_URL") or "").strip()
    parsed = urlsplit(raw_url)
    database_name = unquote(parsed.path.lstrip("/")).split("/", 1)[0]
    database_name = database_name.strip().lower()
    database_role = unquote(parsed.username or "").strip()
    if not parsed.scheme.startswith("postgresql"):
        raise PresenterSchemaMigrationError("DATABASE_URL_must_be_postgresql")
    if (
        not database_name
        or "staging" not in database_name
        or any(marker in database_name for marker in _LIVE_MARKERS)
    ):
        raise PresenterSchemaMigrationError(
            "DATABASE_URL_must_identify_staging_database"
        )
    if not database_role:
        raise PresenterSchemaMigrationError(
            "DATABASE_URL_must_identify_database_role"
        )
    return database_name, database_role


def safety_blockers(
    args: argparse.Namespace | None = None,
    values: Mapping[str, str] | None = None,
) -> list[str]:
    """Valida toda la frontera antes de importar el motor o abrir PostgreSQL."""

    env = values if values is not None else os.environ
    blockers: list[str] = []
    environment = str(env.get("RTM_ENV") or "").strip().lower()
    namespace = str(env.get("RTM_DATA_NAMESPACE") or "").strip().lower()
    side_effect_policy = str(
        env.get("RTM_SIDE_EFFECT_POLICY") or ""
    ).strip().lower()

    if environment != "staging":
        blockers.append("RTM_ENV_must_be_staging")
    if (
        "staging" not in namespace
        or any(marker in namespace for marker in _LIVE_MARKERS)
    ):
        blockers.append("RTM_DATA_NAMESPACE_must_identify_staging")
    if side_effect_policy != "isolated":
        blockers.append("RTM_SIDE_EFFECT_POLICY_must_be_isolated")
    expected_flags = {
        "RTM_ALLOW_REAL_CUSTOMER_DATA": False,
        "RTM_PRESENTER_SYNTHETIC_ONLY": True,
        "RTM_PRESENTER_EXTERNAL_EFFECTS_ALLOWED": False,
        "RTM_PRESENTER_DIRECT_STORAGE_ALLOWED": False,
    }
    for name, expected in expected_flags.items():
        if _flag(env, name) is not expected:
            blockers.append(f"{name}_must_be_{str(expected).lower()}")
    try:
        _database_identity_from_url(env)
    except PresenterSchemaMigrationError as exc:
        blockers.append(str(exc))
    if args is not None and args.apply and args.confirmation != APPLY_CONFIRMATION:
        blockers.append("invalid_apply_confirmation")
    return blockers


def schema_contract() -> dict[str, Any]:
    """Devuelve la identidad canonica del DDL local sin acceder a la base."""

    from rtm_presenter_schema import (
        PRESENTER_REQUIRED_COLUMNS,
        PRESENTER_REQUIRED_CONSTRAINTS,
        PRESENTER_REQUIRED_INDEXES,
        PRESENTER_REQUIRED_TRIGGERS,
        RTM_PRESENTER_SCHEMA_VERSION,
        rtm_presenter_schema_ddl,
    )

    ddl = list(rtm_presenter_schema_ddl())
    names = [str(name) for name, _ in ddl]
    if not RTM_PRESENTER_SCHEMA_VERSION or not ddl or len(names) != len(set(names)):
        raise PresenterSchemaMigrationError("presenter_schema_contract_invalid")
    for name, statement in ddl:
        if not str(name).strip() or not str(statement).strip():
            raise PresenterSchemaMigrationError("presenter_schema_contract_invalid")
        if _FORBIDDEN_DDL.search(str(statement)):
            raise PresenterSchemaMigrationError(
                "presenter_schema_contract_contains_dml_or_destructive_ddl"
            )

    canonical = {
        "schema_version": RTM_PRESENTER_SCHEMA_VERSION,
        "ddl": [
            {
                "name": str(name),
                "sha256": hashlib.sha256(
                    str(statement).encode("utf-8")
                ).hexdigest(),
            }
            for name, statement in ddl
        ],
        "required_columns": {
            table: sorted(columns)
            for table, columns in sorted(PRESENTER_REQUIRED_COLUMNS.items())
        },
        "required_indexes": sorted(PRESENTER_REQUIRED_INDEXES),
        "required_triggers": sorted(PRESENTER_REQUIRED_TRIGGERS),
        "required_constraints": sorted(PRESENTER_REQUIRED_CONSTRAINTS),
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "schema_version": RTM_PRESENTER_SCHEMA_VERSION,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "ddl_count": len(ddl),
        "ddl_names": names,
    }


def assert_database_identity(
    conn: Any,
    *,
    expected_database_name: str,
    expected_database_role: str,
) -> str:
    """Comprueba DB, rol y search path efectivos antes de cualquier DDL."""

    row = conn.exec_driver_sql(
        """
        SELECT current_database() AS database_name,
               current_user AS current_role,
               session_user AS session_role,
               current_schemas(FALSE) AS explicit_schemas,
               current_schemas(TRUE) AS effective_schemas,
               pg_my_temp_schema() AS temp_schema_oid
        """
    ).mappings().one()
    actual_database = str(row["database_name"] or "").strip().lower()
    actual_role = str(row["current_role"] or "").strip()
    session_role = str(row["session_role"] or "").strip()
    explicit_schemas = tuple(str(value) for value in row["explicit_schemas"])
    effective_schemas = tuple(str(value) for value in row["effective_schemas"])
    if (
        actual_database != str(expected_database_name).strip().lower()
        or "staging" not in actual_database
        or any(marker in actual_database for marker in _LIVE_MARKERS)
        or not expected_database_role
        or actual_role != expected_database_role
        or session_role != expected_database_role
        or explicit_schemas != ("public",)
        or effective_schemas != ("pg_catalog", "public")
        or int(row["temp_schema_oid"] or 0) != 0
    ):
        raise PresenterSchemaMigrationError(
            "presenter_database_identity_mismatch"
        )
    return actual_database


def _table_columns(conn: Any, table_name: str) -> set[str]:
    from sqlalchemy import text

    rows = conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=:table_name"
        ),
        {"table_name": table_name},
    ).fetchall()
    return {str(row[0]) for row in rows}


def _catalog_names(conn: Any, query: str) -> set[str]:
    from sqlalchemy import text

    return {str(row[0]) for row in conn.execute(text(query)).fetchall()}


def _table_snapshot(
    conn: Any,
    requirements: dict[str, set[str]],
) -> tuple[dict[str, Any], list[str]]:
    tables: dict[str, Any] = {}
    missing_total: list[str] = []
    for table_name, required in requirements.items():
        present = _table_columns(conn, table_name)
        missing = sorted(set(required) - present)
        tables[table_name] = {
            "exists": bool(present),
            "required_count": len(required),
            "present_required_count": len(set(required) & present),
            "missing_columns": missing,
        }
        missing_total.extend(
            f"{table_name}.{column}" for column in missing
        )
    return tables, sorted(missing_total)


def schema_snapshot(conn: Any) -> dict[str, Any]:
    from rtm_presenter_schema import (
        PRESENTER_REQUIRED_COLUMNS,
        PRESENTER_REQUIRED_CONSTRAINTS,
        PRESENTER_REQUIRED_INDEXES,
        PRESENTER_REQUIRED_TRIGGERS,
    )

    base_tables, base_missing = _table_snapshot(conn, BASE_REQUIRED_COLUMNS)
    presenter_tables, presenter_missing = _table_snapshot(
        conn, PRESENTER_REQUIRED_COLUMNS
    )
    indexes = _catalog_names(
        conn,
        "SELECT indexname FROM pg_indexes WHERE schemaname='public'",
    )
    triggers = _catalog_names(
        conn,
        "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal",
    )
    constraints = _catalog_names(
        conn,
        "SELECT conname FROM pg_constraint "
        "WHERE connamespace='public'::regnamespace",
    )
    missing_indexes = sorted(PRESENTER_REQUIRED_INDEXES - indexes)
    missing_triggers = sorted(PRESENTER_REQUIRED_TRIGGERS - triggers)
    missing_constraints = sorted(PRESENTER_REQUIRED_CONSTRAINTS - constraints)
    presenter_ready = not (
        presenter_missing
        or missing_indexes
        or missing_triggers
        or missing_constraints
    )
    return {
        "base": {
            "tables": base_tables,
            "missing_columns": base_missing,
            "ready": not base_missing,
        },
        "presenter": {
            "tables": presenter_tables,
            "missing_columns": presenter_missing,
            "missing_indexes": missing_indexes,
            "missing_triggers": missing_triggers,
            "missing_constraints": missing_constraints,
            "ready": presenter_ready,
        },
        "ready": not base_missing and presenter_ready,
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


def _non_synthetic_case_count(conn: Any) -> int:
    from sqlalchemy import text

    return int(
        conn.execute(
            text(
                "SELECT COUNT(*) FROM cases "
                "WHERE test_mode IS DISTINCT FROM TRUE"
            )
        ).scalar_one()
    )


def apply_schema(conn: Any, *, contract: Mapping[str, Any]) -> list[str]:
    """Aplica el DDL y su ledger inmutable en la transaccion del llamador."""

    from sqlalchemy import text
    from rtm_presenter_schema import (
        RTM_PRESENTER_SCHEMA_VERSION,
        rtm_presenter_schema_ddl,
    )

    authoritative_contract = schema_contract()
    contract_identity = {
        "schema_version": contract.get("schema_version"),
        "sha256": contract.get("sha256"),
        "ddl_count": contract.get("ddl_count"),
        "ddl_names": list(contract.get("ddl_names") or ()),
    }
    if (
        contract_identity != authoritative_contract
        or contract.get("schema_version") != RTM_PRESENTER_SCHEMA_VERSION
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(contract.get("sha256") or "")
        )
    ):
        raise PresenterSchemaMigrationError(
            "presenter_schema_contract_identity_mismatch"
        )

    applied: list[str] = []
    for name, statement in rtm_presenter_schema_ddl():
        conn.exec_driver_sql(
            statement,
            execution_options={"no_parameters": True},
        )
        applied.append(str(name))

    metadata = {
        "source": PRESENTER_SCHEMA_SCRIPT_VERSION,
        "schema_version": RTM_PRESENTER_SCHEMA_VERSION,
        "schema_contract_sha256": contract["sha256"],
        "scope": "staging_isolated_synthetic_schema_only",
        "synthetic_only": True,
        "real_data_allowed": False,
        "profiles_seeded": False,
        "documents_seeded": False,
        "cases_seeded": False,
        "operators_seeded": False,
        "b2_used": False,
        "external_effects": False,
        "destructive": False,
    }
    conn.execute(
        text(
            "INSERT INTO rtm_management_schema_migrations"
            "(name, metadata, applied_at) "
            "VALUES (:name, CAST(:metadata AS JSONB), NOW()) "
            "ON CONFLICT (name) DO NOTHING"
        ),
        {
            "name": RTM_PRESENTER_SCHEMA_VERSION,
            "metadata": json.dumps(
                metadata,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
        },
    )
    stored = conn.execute(
        text(
            "SELECT metadata FROM rtm_management_schema_migrations "
            "WHERE name=:name"
        ),
        {"name": RTM_PRESENTER_SCHEMA_VERSION},
    ).scalar_one()
    stored_metadata = stored if isinstance(stored, dict) else json.loads(stored)
    expected_identity = {
        "source": PRESENTER_SCHEMA_SCRIPT_VERSION,
        "schema_version": RTM_PRESENTER_SCHEMA_VERSION,
        "schema_contract_sha256": contract["sha256"],
        "scope": "staging_isolated_synthetic_schema_only",
        "synthetic_only": True,
        "real_data_allowed": False,
        "profiles_seeded": False,
        "documents_seeded": False,
        "b2_used": False,
    }
    if any(
        stored_metadata.get(key) != value
        for key, value in expected_identity.items()
    ):
        raise PresenterSchemaMigrationError(
            "presenter_schema_migration_ledger_contract_mismatch"
        )
    return applied


def _missing_after_apply(snapshot: dict[str, Any]) -> list[str]:
    return [
        f"missing_after_apply:{item}"
        for item in (
            snapshot["base"]["missing_columns"]
            + snapshot["presenter"]["missing_columns"]
            + snapshot["presenter"]["missing_indexes"]
            + snapshot["presenter"]["missing_triggers"]
            + snapshot["presenter"]["missing_constraints"]
        )
    ]


def _print(report: dict[str, Any], *, compact: bool) -> None:
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
            sort_keys=True,
            default=str,
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "ok": False,
        "safe": False,
        "authority": "rtm_staging_presenter_schema",
        "version": PRESENTER_SCHEMA_SCRIPT_VERSION,
        "environment": str(os.getenv("RTM_ENV") or "").strip().lower()
        or "unset",
        "apply_requested": bool(args.apply),
        "confirmation_required": APPLY_CONFIRMATION,
        "schema_only": True,
        "read_only": not bool(args.apply),
        "synthetic_only": True,
        "real_data_used": False,
        "production_authorized": False,
        "external_effects_available": False,
        "profiles_seeded": False,
        "documents_seeded": False,
        "cases_seeded": False,
        "operators_seeded": False,
        "b2_used": False,
        "database_connection_used": False,
        "database_identity_verified": False,
        "schema_contract_verified": False,
        "schema_changes_applied": False,
        "transaction_committed": False,
        "applied": [],
        "blockers": [],
    }

    report["blockers"] = safety_blockers(args)
    if report["blockers"]:
        _print(report, compact=args.compact)
        return 2

    try:
        contract = schema_contract()
        report["schema_contract_sha256"] = contract["sha256"]
        report["schema_contract_version"] = contract["schema_version"]
        report["schema_contract_verified"] = True
        expected_database, expected_role = _database_identity_from_url(os.environ)

        from database import get_engine

        engine = get_engine()
        with engine.begin() as conn:
            report["database_connection_used"] = True
            report["connected_database"] = assert_database_identity(
                conn,
                expected_database_name=expected_database,
                expected_database_role=expected_role,
            )
            report["database_identity_verified"] = True
            before = schema_snapshot(conn)
            report["before"] = before
            report["blockers"].extend(_base_blockers(before))
            if not report["blockers"]:
                non_synthetic = _non_synthetic_case_count(conn)
                report["non_synthetic_case_count"] = non_synthetic
                if non_synthetic:
                    report["blockers"].append(
                        "non_synthetic_cases_present"
                    )
            if not report["blockers"] and args.apply:
                report["applied"] = apply_schema(conn, contract=contract)
                report["schema_changes_applied"] = bool(report["applied"])
            after = schema_snapshot(conn)
            report["after"] = after
            if args.apply:
                post_apply_blockers = _missing_after_apply(after)
                report["blockers"].extend(post_apply_blockers)
                if post_apply_blockers:
                    raise PresenterSchemaMigrationError(
                        "presenter_schema_contract_not_ready_after_apply"
                    )
            elif not after["ready"]:
                report["blockers"].append("presenter_schema_not_ready")
        report["transaction_committed"] = not report["blockers"]
        report["safe"] = not report["blockers"]
        report["ok"] = bool(report["safe"] and report["after"]["ready"])
        exit_code = 0 if report["ok"] else 1
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["safe"] = False
        report["ok"] = False
        exit_code = 1

    _print(report, compact=args.compact)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
