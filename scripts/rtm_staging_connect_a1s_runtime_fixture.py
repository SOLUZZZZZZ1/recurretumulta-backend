#!/usr/bin/env python3
"""Audita o provisiona la fixture persistente e inerte de A1-S Runtime.

Solo opera contra PostgreSQL staging ya validado. No crea operadores,
credenciales o sesiones: ``--apply`` exige tres UUID de operadores sinteticos
ya existentes y la confirmacion exacta congelada. El modo por defecto es de
solo lectura y ``--list-eligible`` descubre unicamente identidades marcadas
como sinteticas, sin exponer correo, tokens ni hashes de contrasena.
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

SCRIPT_VERSION = "rtm_staging_connect_a1s_runtime_fixture_v1_0"
APPLY_CONFIRMATION = "STAGING_CONNECT_A1S_RUNTIME_FIXTURES_ONLY"
DEFAULT_FIXTURE_KEY = "runtime-a94dcd3-v1"
_FALSE = frozenset({"0", "false", "no", "off", "disabled"})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--fixture-key", default=DEFAULT_FIXTURE_KEY)
    parser.add_argument("--requester-operator-id", default="")
    parser.add_argument("--releaser-operator-id", default="")
    parser.add_argument("--verifier-operator-id", default="")
    parser.add_argument("--list-eligible", action="store_true")
    parser.add_argument("--compact", action="store_true")
    return parser


def _explicit_false(values: Mapping[str, str], name: str) -> bool:
    return str(values.get(name) or "").strip().lower() in _FALSE


def safety_blockers(
    args: argparse.Namespace,
    values: Mapping[str, str] | None = None,
) -> list[str]:
    """Fail closed before importing the engine or opening PostgreSQL."""

    env = values if values is not None else os.environ
    blockers: list[str] = []
    try:
        from rtm_connect.human_filing_policy import assert_a1s_staging_boundary

        assert_a1s_staging_boundary(env)
    except Exception as exc:
        blockers.append(
            "connect_a1s_staging_boundary_blocked:"
            f"{type(exc).__name__}:{exc}"
        )
    for name in (
        "RTM_ENABLE_CONNECT_A1S_HUMAN_FILING",
        "RTM_ENABLE_OPERATOR_AUTH_V1",
    ):
        if not _explicit_false(env, name):
            blockers.append(f"{name}_must_be_explicitly_false")
    if args.apply and args.list_eligible:
        blockers.append("apply_and_list_eligible_are_mutually_exclusive")
    if args.apply and args.confirmation != APPLY_CONFIRMATION:
        blockers.append("invalid_apply_confirmation")
    operator_ids = (
        args.requester_operator_id,
        args.releaser_operator_id,
        args.verifier_operator_id,
    )
    if args.apply and any(not str(value or "").strip() for value in operator_ids):
        blockers.append("three_operator_ids_required_for_apply")
    return blockers


def _operator_arguments(args: argparse.Namespace):
    from rtm_connect.human_filing_runtime import RuntimeOperators

    values = (
        str(args.requester_operator_id or "").strip(),
        str(args.releaser_operator_id or "").strip(),
        str(args.verifier_operator_id or "").strip(),
    )
    if not any(values):
        return None
    if not all(values):
        raise ValueError("three_operator_ids_required_together")
    return RuntimeOperators(
        requester_executor_id=values[0],
        releaser_id=values[1],
        verifier_id=values[2],
    )


def eligible_synthetic_operators(conn: Any) -> list[dict[str, Any]]:
    """Returns non-secret identifiers for explicit operator selection."""

    from sqlalchemy import text

    rows = conn.execute(text(
        """
        SELECT o.id, COALESCE(r.code, '') AS role_code
        FROM rtm_operators o
        LEFT JOIN rtm_operator_roles r ON r.id=o.primary_role_id
        WHERE o.status='active'
          AND o.must_change_password=FALSE
          AND o.mfa_required=FALSE
          AND (o.locked_until IS NULL OR o.locked_until <= NOW())
          AND COALESCE((o.profile->>'synthetic')::boolean,FALSE)=TRUE
          AND COALESCE(o.profile->>'environment','')='staging'
        ORDER BY o.id
        LIMIT 50
        """
    )).mappings().all()
    return [
        {
            "operator_id": str(row["id"]),
            "role_code": str(row["role_code"]),
            "synthetic": True,
        }
        for row in rows
    ]


def _print(report: dict[str, Any], compact: bool) -> None:
    print(json.dumps(
        report,
        ensure_ascii=False,
        indent=None if compact else 2,
        separators=(",", ":") if compact else None,
        sort_keys=True,
        default=str,
    ))


def _base_report(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "ok": False,
        "safe": False,
        "authority": "rtm_staging_connect_a1s_runtime_fixture",
        "version": SCRIPT_VERSION,
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "fixture_key": args.fixture_key,
        "apply_requested": bool(args.apply),
        "list_eligible_requested": bool(args.list_eligible),
        "confirmation_required": APPLY_CONFIRMATION,
        "read_only": not bool(args.apply),
        "creation_only": True,
        "a1s_rows_insert_only": True,
        "preexisting_rows_mutated": False,
        "new_core_action_transitions_to_authorized": True,
        "destructive": False,
        "synthetic_only": True,
        "database_configuration_loaded": False,
        "database_connection_used": False,
        "database_touched": False,
        "database_mutated": False,
        "operators_created": False,
        "credentials_created": False,
        "sessions_created": False,
        "routes_published": False,
        "workers_started": False,
        "provider_network_used": False,
        "administration_network_used": False,
        "provider_contacted": False,
        "administration_contacted": False,
        "b2_used": False,
        "b2b_enabled": False,
        "real_data_used": False,
        "external_effects_executed": False,
        "production_authorized": False,
        "production_effects_available": False,
        "live_verdict": "no_go",
        "blockers": [],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _base_report(args)
    report["blockers"] = safety_blockers(args)
    if report["blockers"]:
        _print(report, args.compact)
        return 2

    try:
        from sqlalchemy import text
        from database import get_engine
        from rtm_connect.human_filing_policy import (
            assert_a1s_database_identity,
            assert_a1s_staging_boundary,
        )
        from rtm_connect.human_filing_runtime import (
            audit_runtime_fixture,
            provision_runtime_fixture,
        )
        from scripts.rtm_staging_connect_a1s_schema import schema_snapshot

        boundary = assert_a1s_staging_boundary(os.environ)
        operators = _operator_arguments(args)
        report["database_configuration_loaded"] = True
        engine = get_engine()

        if args.apply:
            with engine.begin() as conn:
                report["database_connection_used"] = True
                report["database_touched"] = True
                assert_a1s_database_identity(
                    conn,
                    expected_database_name=boundary.database_name,
                    expected_database_role=boundary.database_role,
                )
                schema = schema_snapshot(conn)
                report["schema_ready"] = bool(schema["ready"])
                if not schema["ready"]:
                    raise RuntimeError("connect_a1s_schema_not_ready")
                assert operators is not None
                before_audit = audit_runtime_fixture(
                    conn,
                    fixture_key=args.fixture_key,
                    operators=operators,
                )
                report["fixture_ready_before_apply"] = bool(
                    before_audit["ready"]
                )
                result = provision_runtime_fixture(
                    conn,
                    operators=operators,
                    fixture_key=args.fixture_key,
                )
                report["database_mutated"] = bool(
                    not before_audit["ready"] and result["ready"]
                )
                report["fixture_created"] = bool(result["created"])
                report["fixture"] = result["fixture"]
                report["operators"] = result["operators"]
            # Commit must be followed by a new read-only transaction so a
            # successful report cannot describe only uncommitted rows.
            with engine.connect() as conn:
                transaction = conn.begin()
                try:
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                    assert_a1s_database_identity(
                        conn,
                        expected_database_name=boundary.database_name,
                        expected_database_role=boundary.database_role,
                    )
                    audit = audit_runtime_fixture(
                        conn,
                        fixture_key=args.fixture_key,
                        operators=operators,
                    )
                finally:
                    transaction.rollback()
        else:
            with engine.connect() as conn:
                transaction = conn.begin()
                try:
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                    report["database_connection_used"] = True
                    report["database_touched"] = True
                    assert_a1s_database_identity(
                        conn,
                        expected_database_name=boundary.database_name,
                        expected_database_role=boundary.database_role,
                    )
                    schema = schema_snapshot(conn)
                    report["schema_ready"] = bool(schema["ready"])
                    if not schema["ready"]:
                        raise RuntimeError("connect_a1s_schema_not_ready")
                    if args.list_eligible:
                        eligible = eligible_synthetic_operators(conn)
                        report["eligible_synthetic_operators"] = eligible
                        report["eligible_synthetic_operator_count"] = len(eligible)
                        audit = None
                    else:
                        audit = audit_runtime_fixture(
                            conn,
                            fixture_key=args.fixture_key,
                            operators=operators,
                        )
                finally:
                    transaction.rollback()

        if audit is not None:
            report["audit"] = audit
            report["fixture_ready"] = bool(audit["ready"])
            if not audit["ready"]:
                report["blockers"].append("a1s_runtime_fixture_not_ready")
        report["ok"] = not report["blockers"]
        report["safe"] = True
        code = 0 if report["ok"] else 1
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["blockers"].append("a1s_runtime_fixture_operation_failed")
        code = 1
    _print(report, args.compact)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
