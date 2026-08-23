#!/usr/bin/env python3
"""Preflight de solo lectura de RTM CONNECT C3 manual_handoff."""

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
        "authority": "rtm_connect_c3_preflight",
        "version": "rtm_connect_c3_preflight_v1_0",
        "environment": (
            os.getenv("RTM_ENV") or ""
        ).strip().lower() or "unset",
        "read_only": True,
        "synthetic_only": True,
        "schema_changes_required": False,
        "routes_published": False,
        "connector_seeded": False,
        "network_used": False,
        "external_effects_executed": False,
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
        from rtm_connect.connectors.manual_handoff import (
            MANUAL_HANDOFF_CAPABILITY,
            MANUAL_HANDOFF_CODE,
            MANUAL_HANDOFF_CONNECTOR_VERSION,
            ManualHandoffConnector,
            assert_manual_handoff_manifest_frozen,
        )
        from rtm_connect.manifest import assert_manifest_frozen
        from scripts.rtm_staging_connect_c1_schema import (
            schema_snapshot as c1_schema_snapshot,
        )
        from scripts.rtm_staging_connect_c3_schema import (
            schema_snapshot as c3_schema_snapshot,
        )

        assert_manifest_frozen()
        assert_manual_handoff_manifest_frozen()
        descriptor = ManualHandoffConnector.descriptor
        report["checks"]["c0_manifest_frozen"] = True
        report["checks"]["c3_manifest_frozen"] = True
        report["checks"]["connector_code_exact"] = (
            descriptor.code == MANUAL_HANDOFF_CODE
        )
        report["checks"]["connector_version_exact"] = (
            descriptor.version == MANUAL_HANDOFF_CONNECTOR_VERSION
        )
        report["checks"]["connector_capability_exact"] = (
            descriptor.capabilities
            == (MANUAL_HANDOFF_CAPABILITY,)
        )
        report["checks"]["connector_mode_manual"] = (
            descriptor.mode.value == "manual"
        )
        report["checks"]["connector_is_synthetic"] = (
            descriptor.synthetic_only is True
        )
        report["checks"]["connector_is_network_free"] = (
            descriptor.network_used is False
        )
        report["checks"]["connector_supports_idempotency"] = (
            descriptor.supports_idempotency is True
        )

        for name in (
            "RTM_ENABLE_EXTERNAL_SUBMISSION",
            "RTM_ENABLE_OUTBOUND_EMAIL",
            "RTM_ENABLE_STRIPE",
            "RTM_ENABLE_FINAL_PAYMENTS",
        ):
            ok = _flag(name) is False
            report["checks"][f"{name.lower()}_disabled"] = ok
            if not ok:
                report["blockers"].append(f"{name}_must_be_false")
        real_data = _flag("RTM_ALLOW_REAL_CUSTOMER_DATA")
        report["checks"]["real_customer_data_disabled"] = (
            real_data is False
        )
        if real_data is not False:
            report["blockers"].append(
                "RTM_ALLOW_REAL_CUSTOMER_DATA_must_be_false"
            )
        isolated = (
            (os.getenv("RTM_SIDE_EFFECT_POLICY") or "")
            .strip()
            .lower()
            == "isolated"
        )
        report["checks"]["side_effect_policy_isolated"] = isolated
        if not isolated:
            report["blockers"].append(
                "RTM_SIDE_EFFECT_POLICY_must_be_isolated"
            )

        with get_engine().connect() as conn:
            c1 = c1_schema_snapshot(conn)
            c3 = c3_schema_snapshot(conn)
            report["c1_schema"] = c1
            report["c3_schema"] = c3
            report["checks"]["c1_schema_ready"] = bool(c1["ready"])
            report["checks"]["c3_schema_ready"] = bool(c3["ready"])
            if not c1["ready"]:
                report["blockers"].append(
                    "rtm_connect_c1_schema_not_ready"
                )
            if not c3["ready"]:
                report["blockers"].append(
                    "rtm_connect_c3_schema_not_ready"
                )

            migration = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM rtm_management_schema_migrations
                    WHERE name='rtm_connect_c3_manual_schema_v1_0'
                    """
                )
            ).scalar_one()
            report["checks"]["c3_migration_registered"] = (
                int(migration) == 1
            )
            if int(migration) != 1:
                report["blockers"].append(
                    "rtm_connect_c3_migration_missing"
                )

            counts = conn.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*)
                         FROM rtm_connect_connectors)
                            AS connectors_total,
                        (SELECT COUNT(*)
                         FROM rtm_connect_connectors
                         WHERE synthetic_only=FALSE)
                            AS real_connectors,
                        (SELECT COUNT(*)
                         FROM rtm_connect_connectors
                         WHERE code='manual.handoff'
                           AND version='v1.0')
                            AS persistent_manual,
                        (SELECT COUNT(*)
                         FROM rtm_connect_manual_tasks)
                            AS manual_tasks,
                        (SELECT COUNT(*)
                         FROM rtm_connect_manual_events)
                            AS manual_events
                    """
                )
            ).mappings().one()
            report["connectors_total"] = int(
                counts["connectors_total"]
            )
            report["non_synthetic_connectors"] = int(
                counts["real_connectors"]
            )
            report["persistent_manual_handoff_connectors"] = int(
                counts["persistent_manual"]
            )
            report["manual_tasks_total"] = int(counts["manual_tasks"])
            report["manual_events_total"] = int(counts["manual_events"])
            report["checks"]["no_real_connectors"] = (
                int(counts["real_connectors"]) == 0
            )
            report["checks"][
                "manual_handoff_not_persistently_seeded"
            ] = int(counts["persistent_manual"]) == 0
            report["checks"]["no_persistent_manual_tasks"] = (
                int(counts["manual_tasks"]) == 0
                and int(counts["manual_events"]) == 0
            )
            if int(counts["real_connectors"]) != 0:
                report["blockers"].append(
                    "real_connector_present_in_staging"
                )
            if int(counts["persistent_manual"]) != 0:
                report["blockers"].append(
                    "persistent_manual_handoff_connector_present"
                )
            if (
                int(counts["manual_tasks"]) != 0
                or int(counts["manual_events"]) != 0
            ):
                report["blockers"].append(
                    "persistent_manual_handoff_test_data_present"
                )

        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        runtime_not_wired = (
            "rtm_connect_router" not in app_source
            and "include_router(rtm_connect" not in app_source
        )
        report["checks"]["runtime_not_wired"] = runtime_not_wired
        if not runtime_not_wired:
            report["blockers"].append(
                "rtm_connect_runtime_unexpectedly_wired"
            )

        failed = sorted(
            key
            for key, value in report["checks"].items()
            if not bool(value)
        )
        report["failed_checks"] = failed
        if failed:
            report["blockers"].append(
                "rtm_connect_c3_checks_failed"
            )
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
