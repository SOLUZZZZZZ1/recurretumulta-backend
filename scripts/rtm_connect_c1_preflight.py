#!/usr/bin/env python3
"""Preflight de solo lectura del Kernel RTM CONNECT C1."""

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
_FALSE = {"0", "false", "no", "off", "disabled"}


def _flag(name: str) -> bool | None:
    raw = (os.getenv(name) or "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    return parser


def _print(report: dict[str, Any], compact: bool) -> None:
    print(json.dumps(
        report,
        ensure_ascii=False,
        indent=None if compact else 2,
        separators=(",", ":") if compact else None,
        default=str,
    ))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_connect_c1_preflight",
        "version": "rtm_connect_c1_preflight_v1_0",
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "read_only": True,
        "routes_published": False,
        "external_effects_executed": False,
        "real_connectors_allowed": False,
        "checks": {},
        "blockers": [],
    }
    if report["environment"] != "staging":
        report["blockers"].append("RTM_ENV_must_be_staging")
        report["safe"] = False
        _print(report, args.compact)
        return 2
    try:
        from sqlalchemy import text
        from database import get_engine
        from rtm_connect.manifest import assert_manifest_frozen
        from scripts.rtm_staging_connect_c1_schema import schema_snapshot

        assert_manifest_frozen()
        report["checks"]["c0_manifest_frozen"] = True
        for name in (
            "RTM_ENABLE_EXTERNAL_SUBMISSION",
            "RTM_ENABLE_OUTBOUND_EMAIL",
            "RTM_ENABLE_STRIPE",
            "RTM_ENABLE_FINAL_PAYMENTS",
        ):
            value = _flag(name)
            ok = value is False
            report["checks"][f"{name.lower()}_disabled"] = ok
            if not ok:
                report["blockers"].append(f"{name}_must_be_false")
        real_data = _flag("RTM_ALLOW_REAL_CUSTOMER_DATA")
        report["checks"]["real_customer_data_disabled"] = real_data is False
        if real_data is not False:
            report["blockers"].append("RTM_ALLOW_REAL_CUSTOMER_DATA_must_be_false")
        isolated = (os.getenv("RTM_SIDE_EFFECT_POLICY") or "").strip().lower() == "isolated"
        report["checks"]["side_effect_policy_isolated"] = isolated
        if not isolated:
            report["blockers"].append("RTM_SIDE_EFFECT_POLICY_must_be_isolated")

        with get_engine().connect() as conn:
            snapshot = schema_snapshot(conn)
            report["schema"] = snapshot
            report["checks"]["schema_ready"] = bool(snapshot["ready"])
            if not snapshot["ready"]:
                report["blockers"].append("rtm_connect_c1_schema_not_ready")
            migration = conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM rtm_management_schema_migrations
                    WHERE name='rtm_connect_c1_schema_v1_0'
                    """
                )
            ).scalar_one()
            report["checks"]["migration_registered"] = int(migration) == 1
            if int(migration) != 1:
                report["blockers"].append("rtm_connect_c1_migration_missing")
            connector_counts = conn.execute(
                text(
                    """
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE synthetic_only=FALSE) AS real_count
                    FROM rtm_connect_connectors
                    """
                )
            ).mappings().one()
            report["connectors_total"] = int(connector_counts["total"])
            report["non_synthetic_connectors"] = int(connector_counts["real_count"])
            report["checks"]["no_real_connectors"] = int(connector_counts["real_count"]) == 0
            if int(connector_counts["real_count"]) != 0:
                report["blockers"].append("real_connector_present_in_staging")

        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        runtime_not_wired = (
            "rtm_connect_router" not in app_source
            and "include_router(rtm_connect" not in app_source
        )
        report["checks"]["runtime_not_wired"] = runtime_not_wired
        if not runtime_not_wired:
            report["blockers"].append("rtm_connect_runtime_unexpectedly_wired")

        failed = sorted(k for k, v in report["checks"].items() if not bool(v))
        report["failed_checks"] = failed
        report["safe"] = not report["blockers"]
        report["ok"] = bool(report["safe"])
        code = 0 if report["ok"] else 1
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["safe"] = False
        report["ok"] = False
        code = 1
    _print(report, args.compact)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
