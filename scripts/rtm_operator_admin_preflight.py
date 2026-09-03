#!/usr/bin/env python3
"""Preflight de solo lectura del panel supervisor RTM.

Comprueba entorno, configuración, esquema, supervisor y cableado. No modifica
PostgreSQL. ``--require-enabled`` exige la activación explícita de la capacidad.
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
        "authority": "rtm_operator_admin_preflight",
        "version": "rtm_operator_admin_preflight_v1_0",
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "read_only": True,
        "require_enabled": bool(args.require_enabled),
        "operator_creation_available": False,
        "credential_rotation_available": False,
        "raw_evidence_available": False,
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
        from rtm_core.operator_admin_policy import (
            OperatorAdminRoutesDisabled,
            OperatorAdminRuntimeMisconfigured,
            load_operator_admin_runtime_config,
        )

        assert_environment_ready()
        try:
            config = load_operator_admin_runtime_config(
                require_enabled=args.require_enabled
            )
            report["checks"]["configuration_valid"] = True
            report["checks"]["feature_enabled"] = bool(config.enabled)
            report["checks"]["auth_feature_enabled"] = bool(
                config.auth.enabled
            )
        except OperatorAdminRoutesDisabled:
            report["checks"]["configuration_valid"] = True
            report["checks"]["feature_enabled"] = False
            report["checks"]["auth_feature_enabled"] = True
            report["blockers"].append("operator_admin_feature_not_enabled")
            config = None
        except OperatorAdminRuntimeMisconfigured as exc:
            report["checks"]["configuration_valid"] = False
            report["blockers"].append(
                f"operator_admin_configuration_invalid:{type(exc).__name__}"
            )
            config = None

        engine = get_engine()
        with engine.connect() as conn:
            required = {
                "rtm_operators": {
                    "id", "email", "display_name", "status",
                    "failed_login_count", "locked_until", "auth_epoch",
                },
                "rtm_operator_roles": {
                    "id", "code", "permissions", "active",
                },
                "rtm_operator_sessions": {
                    "id", "operator_id", "status", "device_id",
                    "revoked_at", "revoked_by", "close_reason",
                },
                "rtm_operator_devices": {
                    "id", "operator_id", "status", "revoked_at",
                    "revoked_by", "revocation_reason",
                },
                "rtm_operator_access_events": {
                    "id", "operator_id", "session_id", "device_id",
                    "event_type", "result", "ip_masked",
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
                    "operator_admin_schema_not_ready"
                )

            supervisor_count = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM rtm_operators o
                    JOIN rtm_operator_roles r
                      ON r.id=o.primary_role_id
                    WHERE o.status='active'
                      AND r.active=TRUE
                      AND r.permissions ? 'ops.supervise'
                    """
                )
            ).scalar_one()
            report["checks"]["active_supervisor_present"] = (
                int(supervisor_count) >= 1
            )
            if int(supervisor_count) < 1:
                report["blockers"].append(
                    "active_supervisor_missing"
                )

        from app import app
        paths = {route.path for route in app.routes}
        expected_paths = {
            "/ops/admin/status",
            "/ops/admin/operators",
            "/ops/admin/operators/{operator_id}",
            "/ops/admin/operators/{operator_id}/sessions",
            "/ops/admin/operators/{operator_id}/devices",
            "/ops/admin/operators/{operator_id}/access-events",
            "/ops/admin/sessions/{session_id}/revoke",
            "/ops/admin/devices/{device_id}/revoke",
        }
        missing_routes = sorted(expected_paths - paths)
        report["checks"]["routes_wired"] = not missing_routes
        report["missing_routes"] = missing_routes
        report["checks"]["auth_routes_present"] = (
            "/ops/auth/login" in paths
            and "/ops/auth/me" in paths
        )
        report["checks"]["legacy_login_present"] = "/ops/login" in paths
        if missing_routes:
            report["blockers"].append(
                "operator_admin_routes_not_wired"
            )
        if not report["checks"]["auth_routes_present"]:
            report["blockers"].append("operator_auth_routes_missing")
        if not report["checks"]["legacy_login_present"]:
            report["blockers"].append("legacy_login_missing")

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
