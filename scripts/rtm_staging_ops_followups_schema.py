#!/usr/bin/env python3
"""Audita o aplica el esquema de seguimientos OPS únicamente en staging.

La operación es aditiva: crea ``ops_followups`` y sus índices cuando faltan.
No borra ni modifica expedientes, documentos, eventos o seguimientos existentes.
``--apply`` exige una confirmación literal y las salvaguardas de staging.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))


SCHEMA_VERSION = "rtm_staging_ops_followups_schema_v1_0"
APPLY_CONFIRMATION = "STAGING_OPS_FOLLOWUPS_SCHEMA_ONLY"

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audita o aplica el esquema de seguimientos OPS en staging."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--env-file", default="")
    parser.add_argument("--compact", action="store_true")
    return parser


def _decode_env_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, str) else str(decoded)
        except json.JSONDecodeError:
            return value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    return value


def _load_env_file(path_value: str) -> int:
    """Carga solo variables ausentes sin imprimir nombres ni valores."""

    if not path_value:
        return 0
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"No existe el archivo de entorno indicado: {path}")

    loaded = 0
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not _ENV_KEY.fullmatch(key):
            continue
        if key not in os.environ:
            os.environ[key] = _decode_env_value(raw_value)
            loaded += 1
    return loaded


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
        text("SELECT indexname FROM pg_indexes WHERE schemaname='public'")
    ).fetchall()
    return {str(row[0]) for row in rows}


def schema_snapshot(conn) -> dict[str, Any]:
    from ops_followups_schema import REQUIRED_COLUMNS, REQUIRED_INDEXES

    cases_columns = _table_columns(conn, "cases")
    followup_columns = _table_columns(conn, "ops_followups")
    indexes = _existing_indexes(conn)
    missing_columns = sorted(REQUIRED_COLUMNS - followup_columns)
    missing_indexes = sorted(REQUIRED_INDEXES - indexes)
    return {
        "base": {
            "cases_exists": bool(cases_columns),
            "cases_id_exists": "id" in cases_columns,
            "ready": "id" in cases_columns,
        },
        "ops_followups": {
            "exists": bool(followup_columns),
            "missing_columns": missing_columns,
            "missing_indexes": missing_indexes,
            "ready": not missing_columns and not missing_indexes,
        },
        "ready": "id" in cases_columns and not missing_columns and not missing_indexes,
    }


def apply_ops_followups_schema(conn) -> list[str]:
    from sqlalchemy import text
    from ops_followups_schema import ops_followups_ddl

    applied: list[str] = []
    for name, statement in ops_followups_ddl():
        conn.execute(text(statement))
        applied.append(name)
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
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_staging_ops_followups_schema",
        "version": SCHEMA_VERSION,
        "environment": "unset",
        "apply_requested": bool(args.apply),
        "confirmation_required": APPLY_CONFIRMATION,
        "destructive": False,
        "applied": [],
        "blockers": [],
    }

    try:
        report["env_file_variables_loaded"] = _load_env_file(args.env_file)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["safe"] = False
        _print_report(report, compact=args.compact)
        return 2

    report["environment"] = (os.getenv("RTM_ENV") or "").strip().lower() or "unset"
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
            if not before["base"]["ready"]:
                report["blockers"].append("missing_base_table_or_column:cases.id")
            elif args.apply:
                report["applied"] = apply_ops_followups_schema(conn)

            after = schema_snapshot(conn)
            report["after"] = after
            if not after["ops_followups"]["ready"]:
                report["blockers"].extend(
                    f"missing_after_run:ops_followups.{column}"
                    for column in after["ops_followups"]["missing_columns"]
                )
                report["blockers"].extend(
                    f"missing_after_run:index.{index}"
                    for index in after["ops_followups"]["missing_indexes"]
                )

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
