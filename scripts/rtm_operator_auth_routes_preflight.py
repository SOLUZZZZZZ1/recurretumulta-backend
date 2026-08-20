#!/usr/bin/env python3
"""Preflight de las rutas individuales de operadores RTM.

No modifica PostgreSQL. Comprueba configuración, esquema y cableado de rutas.
La opción ``--require-enabled`` exige que la capacidad esté activada.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-enabled", action="store_true")
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = {
        "ok": False,
        "authority": "rtm_operator_auth_routes_preflight",
        "version": "rtm_operator_auth_routes_preflight_v1_0",
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "require_enabled": bool(args.require_enabled),
        "routes_published": True,
        "legacy_login_unchanged": True,
        "operator_creation_available": False,
        "checks": {},
        "blockers": [],
    }
    if report["environment"] != "staging":
        report["blockers"].append("RTM_ENV_must_be_staging")

    try:
        from sqlalchemy import text
        from database import get_engine
        from rtm_core.operator_auth_request import (
            OperatorAuthRoutesDisabled,
            OperatorAuthRuntimeMisconfigured,
            load_operator_auth_runtime_config,
        )

        try:
            config = load_operator_auth_runtime_config(
                require_enabled=args.require_enabled
            )
            report["checks"]["configuration_valid"] = True
            report["checks"]["feature_enabled"] = bool(config.enabled)
        except OperatorAuthRoutesDisabled:
            report["checks"]["configuration_valid"] = True
            report["checks"]["feature_enabled"] = False
            report["blockers"].append("operator_auth_feature_not_enabled")
            config = None
        except OperatorAuthRuntimeMisconfigured as exc:
            report["checks"]["configuration_valid"] = False
            report["blockers"].append(
                f"operator_auth_configuration_invalid:{type(exc).__name__}"
            )
            config = None

        engine = get_engine()
        with engine.connect() as conn:
            required = {
                "rtm_operators": {
                    "id", "email", "password_hash", "status",
                    "failed_login_count", "locked_until", "auth_epoch",
                },
                "rtm_operator_sessions": {
                    "id", "operator_id", "token_sha256", "status",
                    "absolute_expires_at", "auth_epoch", "device_id",
                    "login_access_event_id",
                },
                "rtm_operator_devices": {
                    "id", "operator_id", "device_key_sha256", "status",
                },
                "rtm_operator_access_events": {
                    "id", "operator_id", "session_id", "event_type",
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
                        WHERE table_schema='public' AND table_name=:table_name
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
                report["blockers"].append("operator_auth_schema_not_ready")

        from app import app
        paths = {route.path for route in app.routes}
        expected_paths = {
            "/ops/auth/status",
            "/ops/auth/login",
            "/ops/auth/me",
            "/ops/auth/heartbeat",
            "/ops/auth/logout",
        }
        missing_routes = sorted(expected_paths - paths)
        report["checks"]["routes_wired"] = not missing_routes
        report["missing_routes"] = missing_routes
        report["checks"]["legacy_login_present"] = "/ops/login" in paths
        if missing_routes:
            report["blockers"].append("operator_auth_routes_not_wired")
        if "/ops/login" not in paths:
            report["blockers"].append("legacy_login_missing")

        if config is not None and config.enabled:
            report["checks"]["staging_only"] = config.environment == "staging"
            report["checks"]["hmac_key_ready"] = len(config.hmac_key) >= 32
            report["checks"]["retention_valid"] = (
                30 <= config.evidence_retention_days <= 365
            )

        report["safe"] = not report["blockers"]
        report["ok"] = bool(report["safe"])
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
