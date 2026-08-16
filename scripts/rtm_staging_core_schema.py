#!/usr/bin/env python3
"""Audita y completa el esquema autoritativo de RTM CORE en staging.

La utilidad aplica únicamente las migraciones aditivas e idempotentes ya
definidas por RTM CORE para:

- hechos validados y congelación;
- resolución y bloqueo de familia;
- Previa Jurídica y su ciclo de aprobación;
- recursos generados;
- extracción documental persistida y su enlace a hechos.

No borra, renombra ni transforma datos legacy. Se niega a funcionar fuera de
staging y exige una confirmación literal para aplicar DDL.
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


SCHEMA_VERSION = "rtm_staging_core_schema_v1_0"
APPLY_CONFIRMATION = "STAGING_CORE_SCHEMA_ONLY"

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}

BASE_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "cases": {
        "id",
        "status",
        "payment_status",
        "authorized",
        "interested_data",
        "department",
        "case_type",
        "category",
        "contact_email",
        "test_mode",
        "created_at",
        "updated_at",
    },
    "documents": {
        "id",
        "case_id",
        "kind",
        "b2_bucket",
        "b2_key",
        "mime",
        "size_bytes",
        "created_at",
    },
    "events": {"id", "case_id", "type", "payload", "created_at"},
}

CORE_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "rtm_core_schema_migrations": {"name", "metadata", "applied_at"},
    "rtm_validated_facts": {
        "id",
        "case_id",
        "sequence",
        "version",
        "service",
        "extractor_version",
        "payload",
        "payload_sha256",
        "frozen",
        "created_by",
        "created_at",
        "updated_at",
        "frozen_by",
        "frozen_at",
        "invalidated_by",
        "invalidated_at",
        "invalidation_reason",
        "supersedes_id",
        "source_extraction_id",
    },
    "rtm_family_resolutions": {
        "id",
        "case_id",
        "validated_facts_id",
        "sequence",
        "version",
        "service",
        "status",
        "family",
        "specialist",
        "confidence",
        "payload",
        "payload_sha256",
        "locked",
        "created_by",
        "created_at",
        "updated_at",
        "locked_by",
        "locked_at",
        "invalidated_by",
        "invalidated_at",
        "invalidation_reason",
        "supersedes_id",
    },
    "rtm_legal_previews": {
        "id",
        "case_id",
        "validated_facts_id",
        "family_resolution_id",
        "sequence",
        "status",
        "service",
        "family",
        "specialist",
        "facts_version",
        "family_resolution_version",
        "payload",
        "payload_sha256",
        "created_by",
        "created_at",
        "updated_at",
        "approved_by",
        "approved_at",
        "frozen_by",
        "frozen_at",
        "invalidated_by",
        "invalidated_at",
        "invalidation_reason",
        "supersedes_id",
        "state_reason",
    },
    "rtm_generated_resources": {
        "id",
        "case_id",
        "legal_preview_id",
        "sequence",
        "status",
        "family",
        "generator_version",
        "preview_payload_sha256",
        "content_sha256",
        "docx_document_id",
        "pdf_document_id",
        "generated_by",
        "created_at",
        "updated_at",
        "approved_by",
        "approved_at",
        "invalidated_at",
        "invalidation_reason",
    },
    "rtm_document_extractions": {
        "id",
        "case_id",
        "sequence",
        "service",
        "status",
        "extractor_version",
        "provider_version",
        "model",
        "packet",
        "packet_sha256",
        "diagnostics",
        "created_by",
        "created_at",
        "invalidated_by",
        "invalidated_at",
        "invalidation_reason",
    },
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audita o aplica el esquema autoritativo de RTM CORE en staging.",
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
    core_tables, core_missing = _snapshot_group(conn, CORE_REQUIRED_COLUMNS)
    return {
        "base": {
            "tables": base_tables,
            "missing_columns": base_missing,
            "ready": not base_missing,
        },
        "core": {
            "tables": core_tables,
            "missing_columns": core_missing,
            "ready": not core_missing,
        },
        "ready": not base_missing and not core_missing,
    }


def _base_structure_blockers(snapshot: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for table_name, table in snapshot["base"]["tables"].items():
        if not table["exists"]:
            blockers.append(f"missing_base_table:{table_name}")
        for column in table["missing_columns"]:
            blockers.append(
                f"missing_base_column:{table_name}.{column}"
            )
    return blockers


def _record_migration(
    conn,
    *,
    name: str,
    metadata: dict[str, Any],
) -> None:
    from sqlalchemy import text

    conn.execute(
        text(
            """
            INSERT INTO rtm_core_schema_migrations(name, metadata, applied_at)
            VALUES (:name, CAST(:metadata AS JSONB), NOW())
            ON CONFLICT (name)
            DO UPDATE SET metadata=EXCLUDED.metadata, applied_at=NOW()
            """
        ),
        {
            "name": name,
            "metadata": json.dumps(metadata, ensure_ascii=False),
        },
    )


def apply_core_schema(conn) -> list[str]:
    from sqlalchemy import text

    from rtm_core.document_extraction_migration import (
        DOCUMENT_EXTRACTION_SCHEMA_VERSION,
        document_extraction_ddl,
    )
    from rtm_core.migration_router import (
        RTM_CORE_AUTHORITY_SCHEMA_VERSION,
        authority_v1_ddl,
    )

    applied: list[str] = []
    for name, statement in authority_v1_ddl():
        conn.execute(text(statement))
        applied.append(f"authority:{name}")

    _record_migration(
        conn,
        name=RTM_CORE_AUTHORITY_SCHEMA_VERSION,
        metadata={
            "source": SCHEMA_VERSION,
            "tables": [
                "rtm_validated_facts",
                "rtm_family_resolutions",
                "rtm_legal_previews",
                "rtm_generated_resources",
            ],
            "destructive": False,
        },
    )

    for name, statement in document_extraction_ddl():
        conn.execute(text(statement))
        applied.append(f"document_extraction:{name}")

    _record_migration(
        conn,
        name=DOCUMENT_EXTRACTION_SCHEMA_VERSION,
        metadata={
            "source": SCHEMA_VERSION,
            "tables": ["rtm_document_extractions"],
            "authority_links": [
                "rtm_validated_facts.source_extraction_id",
            ],
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
        "authority": "rtm_staging_core_schema",
        "version": SCHEMA_VERSION,
        "environment": environment,
        "apply_requested": bool(args.apply),
        "confirmation_required": APPLY_CONFIRMATION,
        "destructive": False,
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
                report["applied"] = apply_core_schema(conn)

            after = schema_snapshot(conn)
            report["after"] = after
            report["blockers"] = list(report["blockers"]) + [
                f"missing_after_apply:{item}"
                for item in after["base"]["missing_columns"]
                + after["core"]["missing_columns"]
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
