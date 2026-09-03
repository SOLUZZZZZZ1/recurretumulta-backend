#!/usr/bin/env python3
"""Preflight de solo lectura del ciclo de vida de operadores RTM."""

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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-enabled", action="store_true")
    parser.add_argument("--compact", action="store_true")
    return parser


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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_operator_lifecycle_preflight",
        "version": "rtm_operator_lifecycle_preflight_v1_0",
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "read_only": True,
        "require_enabled": bool(args.require_enabled),
        "synthetic_only": True,
        "public_registration_available": False,
        "passwords_returned": False,
        "schema_changes_required": False,
        "legacy_login_unchanged": True,
        "legacy_login_retired_in_staging": True,
        "non_staging_legacy_login_unchanged": True,
        "shared_ops_login_accepted": False,
        "checks": {},
        "blockers": [],
    }
    if report["environment"] != "staging":
        report["blockers"].append("RTM_ENV_must_be_staging")
        report["safe"] = False
        _print(report, compact=args.compact)
        return 2

    try:
        from sqlalchemy import text
        from database import get_engine
        from rtm_core.environment_contract import assert_environment_ready
        from rtm_core.operator_lifecycle_policy import (
            OperatorLifecycleRoutesDisabled,
            OperatorLifecycleRuntimeMisconfigured,
            load_operator_lifecycle_runtime_config,
        )

        assert_environment_ready()
        try:
            config = load_operator_lifecycle_runtime_config(
                require_enabled=args.require_enabled
            )
            report["checks"]["configuration_valid"] = True
            report["checks"]["feature_enabled"] = bool(config.enabled)
            report["checks"]["admin_feature_enabled"] = bool(
                config.admin.enabled
            )
            report["checks"]["auth_feature_enabled"] = bool(
                config.admin.auth.enabled
            )
        except OperatorLifecycleRoutesDisabled:
            report["checks"]["configuration_valid"] = True
            report["checks"]["feature_enabled"] = False
            report["checks"]["admin_feature_enabled"] = True
            report["checks"]["auth_feature_enabled"] = True
            report["blockers"].append(
                "operator_lifecycle_feature_not_enabled"
            )
            config = None
        except OperatorLifecycleRuntimeMisconfigured as exc:
            report["checks"]["configuration_valid"] = False
            report["blockers"].append(
                f"operator_lifecycle_configuration_invalid:{type(exc).__name__}"
            )
            config = None

        engine = get_engine()
        with engine.connect() as conn:
            required = {
                "rtm_operator_roles": {
                    "id", "code", "permissions", "system_role", "active",
                },
                "rtm_operators": {
                    "id", "email", "display_name", "password_hash",
                    "status", "primary_role_id", "must_change_password",
                    "failed_login_count", "password_algorithm",
                    "password_version", "auth_epoch", "created_by",
                },
                "rtm_operator_sessions": {
                    "id", "operator_id", "status", "auth_epoch",
                    "revoked_at", "revoked_by", "close_reason",
                },
                "rtm_operator_access_events": {
                    "id", "operator_id", "session_id",
                    "event_type", "result", "reason_code",
                },
                "rtm_operator_access_evidence": {
                    "id", "access_event_id", "retention_until",
                },
            }
            missing: list[str] = []
            for table_name, columns in required.items():
                rows = conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema='public'
                          AND table_name=:table_name
                        """
                    ),
                    {"table_name": table_name},
                ).fetchall()
                present = {str(row[0]) for row in rows}
                missing.extend(
                    f"{table_name}.{column}"
                    for column in sorted(columns - present)
                )
            report["checks"]["schema_ready"] = not missing
            report["missing_schema"] = missing
            if missing:
                report["blockers"].append(
                    "operator_lifecycle_schema_not_ready"
                )

            role_rows = conn.execute(
                text(
                    """
                    SELECT code, active, system_role, permissions
                    FROM rtm_operator_roles
                    WHERE code IN ('rtm.operator', 'rtm.supervisor')
                    """
                )
            ).mappings().all()
            roles = {str(row["code"]): row for row in role_rows}
            report["checks"]["minimum_roles_present"] = (
                set(roles) == {"rtm.operator", "rtm.supervisor"}
            )
            report["checks"]["minimum_roles_active"] = all(
                bool(row["active"]) and bool(row["system_role"])
                for row in roles.values()
            ) and len(roles) == 2
            supervisor_count = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM rtm_operators o
                    JOIN rtm_operator_roles r
                      ON r.id=o.primary_role_id
                    WHERE o.status='active'
                      AND r.active=TRUE
                      AND r.code='rtm.supervisor'
                    """
                )
            ).scalar_one()
            report["checks"]["active_supervisor_present"] = (
                int(supervisor_count) >= 1
            )

        from app import app
        paths = {route.path for route in app.routes}
        expected_paths = {
            "/ops/admin/lifecycle/status",
            "/ops/admin/operators",
            "/ops/admin/operators/{operator_id}/suspend",
            "/ops/admin/operators/{operator_id}/reactivate",
            "/ops/admin/operators/{operator_id}/role",
            "/ops/admin/operators/{operator_id}/credentials/rotate",
            "/ops/admin/operators/{operator_id}/sessions/revoke-all",
            "/ops/auth/password/change",
        }
        missing_routes = sorted(expected_paths - paths)
        report["checks"]["routes_wired"] = not missing_routes
        report["missing_routes"] = missing_routes
        report["checks"]["admin_routes_present"] = (
            "/ops/admin/status" in paths
            and "/ops/admin/operators/{operator_id}" in paths
        )
        report["checks"]["auth_routes_present"] = (
            "/ops/auth/login" in paths
            and "/ops/auth/me" in paths
        )
        report["checks"]["legacy_login_present"] = "/ops/login" in paths

        failed_checks = sorted(
            key
            for key, value in report["checks"].items()
            if not bool(value)
        )
        report["failed_checks"] = failed_checks
        if missing_routes:
            report["blockers"].append(
                "operator_lifecycle_routes_not_wired"
            )
        if not report["checks"]["admin_routes_present"]:
            report["blockers"].append("operator_admin_routes_missing")
        if not report["checks"]["auth_routes_present"]:
            report["blockers"].append("operator_auth_routes_missing")
        if not report["checks"]["legacy_login_present"]:
            report["blockers"].append("legacy_login_missing")
        for check in (
            "schema_ready",
            "minimum_roles_present",
            "minimum_roles_active",
            "active_supervisor_present",
        ):
            if not report["checks"].get(check):
                report["blockers"].append(f"failed_check:{check}")

        if config is not None and config.enabled:
            report["checks"]["staging_only"] = (
                config.environment == "staging"
            )
            report["checks"]["supervisor_permission_declared"] = True

        report["safe"] = not report["blockers"]
        report["ok"] = bool(report["safe"])
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
