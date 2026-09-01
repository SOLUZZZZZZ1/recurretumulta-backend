#!/usr/bin/env python3
"""Auditoría de solo lectura previa a activar el login individual en staging."""

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


_TRUE = {"1", "true", "yes", "on", "enabled"}
_FALSE = {"", "0", "false", "no", "off", "disabled"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--email",
        default="rtm-staging-supervisor@example.com",
    )
    parser.add_argument("--compact", action="store_true")
    return parser


def _strict_flag(name: str) -> bool | None:
    raw = (os.getenv(name) or "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def _print(report: dict[str, Any], *, compact: bool) -> None:
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
            default=str,
        )
    )


def _load_defined_role_rows(
    conn: Any,
    role_definitions: dict[str, Any],
) -> tuple[list[Any], set[str]]:
    from sqlalchemy import bindparam, text

    expected_codes = {
        str(definition.code) for definition in role_definitions.values()
    }
    statement = text(
        """
        SELECT code, active, system_role, permissions
        FROM rtm_operator_roles
        WHERE code IN :role_codes
        """
    ).bindparams(bindparam("role_codes", expanding=True))
    role_rows = conn.execute(
        statement,
        {"role_codes": sorted(expected_codes)},
    ).mappings().fetchall()
    return role_rows, expected_codes


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_staging_operator_activation_preflight",
        "version": "rtm_staging_operator_activation_preflight_v1_0",
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "read_only": True,
        "legacy_login_unchanged": True,
        "operator_creation_public": False,
        "checks": {},
        "blockers": [],
    }

    if report["environment"] != "staging":
        report["blockers"].append("RTM_ENV_must_be_staging")
        report["safe"] = False
        _print(report, compact=args.compact)
        return 2

    try:
        from database import get_engine
        from rtm_core.environment_contract import assert_environment_ready
        from rtm_core.operator_provisioning import (
            ROLE_DEFINITIONS,
            count_non_synthetic_operators,
            find_operator_by_email,
            normalize_synthetic_operator_email,
        )

        assert_environment_ready()
        feature = _strict_flag("RTM_ENABLE_OPERATOR_AUTH_V1")
        report["checks"]["feature_flag_valid"] = feature is not None
        report["checks"]["feature_still_disabled"] = feature is False
        if feature is None:
            report["blockers"].append("RTM_ENABLE_OPERATOR_AUTH_V1_invalid")
        elif feature:
            report["blockers"].append(
                "operator_auth_should_remain_disabled_before_activation"
            )

        email = normalize_synthetic_operator_email(args.email)
        engine = get_engine()
        with engine.connect() as conn:
            role_rows, expected_codes = _load_defined_role_rows(
                conn,
                ROLE_DEFINITIONS,
            )
            roles = {str(row["code"]): row for row in role_rows}
            report["checks"]["minimum_roles_present"] = (
                set(roles) == expected_codes
            )
            role_contract_ok = True
            for definition in ROLE_DEFINITIONS.values():
                row = roles.get(definition.code)
                if not row:
                    role_contract_ok = False
                    continue
                actual_permissions = (
                    row["permissions"]
                    if isinstance(row["permissions"], list)
                    else []
                )
                role_contract_ok = role_contract_ok and (
                    bool(row["active"])
                    and bool(row["system_role"])
                    and actual_permissions == list(definition.permissions)
                )
            report["checks"]["minimum_roles_match_contract"] = role_contract_ok

            operator = find_operator_by_email(conn, email)
            report["checks"]["synthetic_operator_present"] = bool(operator)
            if operator:
                profile = operator["profile"]
                report["checks"]["synthetic_profile"] = (
                    isinstance(profile, dict)
                    and profile.get("synthetic") is True
                    and profile.get("environment") == "staging"
                )
                report["checks"]["operator_active"] = (
                    str(operator["status"]) == "active"
                )
                report["checks"]["argon2id_password_ready"] = str(
                    operator["password_hash"] or ""
                ).startswith("$argon2id$")
                report["checks"]["supervisor_role_assigned"] = (
                    str(operator["role_code"] or "") == "rtm.supervisor"
                )
                report["checks"]["mfa_not_prematurely_required"] = not bool(
                    operator["mfa_required"]
                )
                report["operator_id"] = str(operator["id"])
                report["email"] = str(operator["email"])
                active_sessions = conn.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM rtm_operator_sessions
                        WHERE operator_id=CAST(:operator_id AS UUID)
                          AND status='active'
                        """
                    ),
                    {"operator_id": str(operator["id"])},
                ).scalar_one()
                report["checks"]["no_active_sessions_before_activation"] = (
                    int(active_sessions) == 0
                )

            report["checks"]["no_non_synthetic_operators"] = (
                count_non_synthetic_operators(conn) == 0
            )

        from app import app
        paths = {route.path for route in app.routes}
        expected_routes = {
            "/ops/auth/status",
            "/ops/auth/login",
            "/ops/auth/me",
            "/ops/auth/heartbeat",
            "/ops/auth/logout",
        }
        report["checks"]["auth_routes_wired"] = expected_routes.issubset(paths)
        report["checks"]["legacy_login_present"] = "/ops/login" in paths

        failed_checks = sorted(
            key for key, value in report["checks"].items() if not bool(value)
        )
        report["failed_checks"] = failed_checks
        if failed_checks:
            report["blockers"].append("activation_preflight_checks_failed")

        report["safe"] = not report["blockers"]
        report["ready_for_activation"] = bool(report["safe"])
        report["ok"] = bool(report["safe"])
        exit_code = 0 if report["ok"] else 1
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["safe"] = False
        report["ready_for_activation"] = False
        report["ok"] = False
        exit_code = 1

    _print(report, compact=args.compact)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
