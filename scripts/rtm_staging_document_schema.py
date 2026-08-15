#!/usr/bin/env python3
"""Audita y completa de forma idempotente el esquema documental de RTM staging.

La utilidad existe para bases de staging creadas a partir de migraciones legacy
parciales. Solo añade columnas compatibles mediante ``ADD COLUMN IF NOT EXISTS``
y crea índices no destructivos. No borra, renombra ni transforma datos.

Se niega a ejecutarse fuera de staging y exige una confirmación literal para
aplicar cambios. El informe nunca incluye credenciales ni valores de clientes.
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


SCHEMA_VERSION = "rtm_staging_document_schema_v1_0"
APPLY_CONFIRMATION = "STAGING_SCHEMA_ONLY"

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "cases": {
        "id",
        "contact_email",
        "contact_name",
        "status",
        "payment_status",
        "authorized",
        "authorized_at",
        "interested_data",
        "department",
        "case_type",
        "customer_comment",
        "source_module",
        "category",
        "organismo",
        "expediente_ref",
        "test_mode",
        "override_deadlines",
        "created_at",
        "updated_at",
    },
    "documents": {
        "id",
        "case_id",
        "kind",
        "b2_bucket",
        "b2_key",
        "sha256",
        "mime",
        "size_bytes",
        "created_at",
    },
    "events": {"id", "case_id", "type", "payload", "created_at"},
    "extractions": {
        "id",
        "case_id",
        "extracted_json",
        "confidence",
        "model",
        "created_at",
    },
}

# Solo columnas aditivas. Las columnas estructurales id/case_id/kind/type se
# validan, pero no se inventan sobre tablas incompatibles.
ADDITIVE_DDL: tuple[tuple[str, str], ...] = (
    ("cases.contact_email", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS contact_email TEXT"),
    ("cases.contact_name", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS contact_name TEXT"),
    (
        "cases.status",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'uploaded'",
    ),
    ("cases.payment_status", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS payment_status TEXT"),
    (
        "cases.authorized",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS authorized BOOLEAN NOT NULL DEFAULT FALSE",
    ),
    (
        "cases.authorized_at",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS authorized_at TIMESTAMPTZ",
    ),
    (
        "cases.interested_data",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS interested_data JSONB",
    ),
    ("cases.department", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS department TEXT"),
    ("cases.case_type", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS case_type TEXT"),
    (
        "cases.customer_comment",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS customer_comment TEXT",
    ),
    (
        "cases.source_module",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS source_module TEXT",
    ),
    ("cases.category", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS category TEXT"),
    ("cases.organismo", "ALTER TABLE cases ADD COLUMN IF NOT EXISTS organismo TEXT"),
    (
        "cases.expediente_ref",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS expediente_ref TEXT",
    ),
    (
        "cases.test_mode",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS test_mode BOOLEAN NOT NULL DEFAULT FALSE",
    ),
    (
        "cases.override_deadlines",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS override_deadlines BOOLEAN NOT NULL DEFAULT FALSE",
    ),
    (
        "cases.created_at",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    ),
    (
        "cases.updated_at",
        "ALTER TABLE cases ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    ),
    (
        "documents.b2_bucket",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS b2_bucket TEXT",
    ),
    ("documents.b2_key", "ALTER TABLE documents ADD COLUMN IF NOT EXISTS b2_key TEXT"),
    ("documents.sha256", "ALTER TABLE documents ADD COLUMN IF NOT EXISTS sha256 TEXT"),
    ("documents.mime", "ALTER TABLE documents ADD COLUMN IF NOT EXISTS mime TEXT"),
    (
        "documents.size_bytes",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS size_bytes BIGINT",
    ),
    (
        "documents.created_at",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    ),
    ("events.payload", "ALTER TABLE events ADD COLUMN IF NOT EXISTS payload JSONB"),
    (
        "events.created_at",
        "ALTER TABLE events ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    ),
    (
        "extractions.confidence",
        "ALTER TABLE extractions ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION",
    ),
    ("extractions.model", "ALTER TABLE extractions ADD COLUMN IF NOT EXISTS model TEXT"),
    (
        "extractions.created_at",
        "ALTER TABLE extractions ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    ),
    ("idx_cases_status", "CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status)"),
    (
        "idx_cases_payment_status",
        "CREATE INDEX IF NOT EXISTS idx_cases_payment_status ON cases(payment_status)",
    ),
    (
        "idx_cases_department_status",
        "CREATE INDEX IF NOT EXISTS idx_cases_department_status ON cases(department, status)",
    ),
    (
        "idx_documents_case",
        "CREATE INDEX IF NOT EXISTS idx_documents_case ON documents(case_id)",
    ),
    ("idx_events_case", "CREATE INDEX IF NOT EXISTS idx_events_case ON events(case_id)"),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audita o completa el esquema documental de RTM staging.",
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


def schema_snapshot(conn) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    missing_total: list[str] = []
    for table_name, required in REQUIRED_COLUMNS.items():
        present = _table_columns(conn, table_name)
        exists = bool(present)
        missing = sorted(required - present)
        tables[table_name] = {
            "exists": exists,
            "required_count": len(required),
            "present_required_count": len(required & present),
            "missing_columns": missing,
        }
        missing_total.extend(f"{table_name}.{column}" for column in missing)
    return {
        "tables": tables,
        "missing_columns": sorted(missing_total),
        "ready": not missing_total,
    }


def _critical_structure_blockers(snapshot: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    critical = {
        "cases": {"id"},
        "documents": {"id", "case_id", "kind"},
        "events": {"id", "case_id", "type"},
        "extractions": {"id", "case_id", "extracted_json"},
    }
    for table_name, columns in critical.items():
        table = snapshot["tables"][table_name]
        missing = set(table["missing_columns"])
        if not table["exists"]:
            blockers.append(f"missing_table:{table_name}")
        for column in sorted(columns & missing):
            blockers.append(f"missing_structural_column:{table_name}.{column}")
    return blockers


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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    environment = (os.getenv("RTM_ENV") or "").strip().lower() or "unset"
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_staging_document_schema",
        "version": SCHEMA_VERSION,
        "environment": environment,
        "apply_requested": bool(args.apply),
        "confirmation_required": APPLY_CONFIRMATION,
        "applied": [],
        "blockers": [],
    }

    safety = _safety_blockers(args)
    if safety:
        report["blockers"] = safety
        report["safe"] = False
        print(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=None if args.compact else 2,
                separators=(",", ":") if args.compact else None,
            ),
            file=sys.stderr,
        )
        return 2

    try:
        from sqlalchemy import text
        from database import get_engine

        engine = get_engine()
        with engine.begin() as conn:
            before = schema_snapshot(conn)
            report["before"] = before
            structure_blockers = _critical_structure_blockers(before)
            if structure_blockers:
                report["blockers"] = structure_blockers
                report["safe"] = False
            elif args.apply:
                applied: list[str] = []
                for name, statement in ADDITIVE_DDL:
                    conn.execute(text(statement))
                    applied.append(name)
                report["applied"] = applied

            after = schema_snapshot(conn)
            report["after"] = after
            report["blockers"] = list(report.get("blockers") or []) + [
                f"missing_after_apply:{item}" for item in after["missing_columns"]
            ]

        report["safe"] = not report["blockers"]
        report["ok"] = bool(report["safe"] and report["after"]["ready"])
        exit_code = 0 if report["ok"] else 1
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["safe"] = False
        report["ok"] = False
        exit_code = 1

    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            separators=(",", ":") if args.compact else None,
            default=str,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
