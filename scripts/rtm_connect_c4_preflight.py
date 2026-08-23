#!/usr/bin/env python3
"""Preflight de solo lectura de RTM CONNECT C4 webhook/reconciliation."""

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
        "authority": "rtm_connect_c4_preflight",
        "version": "rtm_connect_c4_preflight_v1_0",
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
        from rtm_connect.connectors.synthetic_webhook import (
            SYNTHETIC_WEBHOOK_CAPABILITY,
            SYNTHETIC_WEBHOOK_CODE,
            SYNTHETIC_WEBHOOK_CONNECTOR_VERSION,
            SyntheticWebhookConnector,
            assert_synthetic_webhook_manifest_frozen,
        )
        from rtm_connect.manifest import assert_manifest_frozen
        from rtm_connect.webhook_schema import (
            RTM_CONNECT_C4_WEBHOOK_SCHEMA_VERSION,
        )
        from scripts.rtm_staging_connect_c1_schema import (
            schema_snapshot as c1_schema_snapshot,
        )
        from scripts.rtm_staging_connect_c3_schema import (
            schema_snapshot as c3_schema_snapshot,
        )
        from scripts.rtm_staging_connect_c4_schema import (
            schema_snapshot as c4_schema_snapshot,
        )

        assert_manifest_frozen()
        assert_synthetic_webhook_manifest_frozen()
        descriptor = SyntheticWebhookConnector.descriptor
        report["checks"]["c0_manifest_frozen"] = True
        report["checks"]["c4_manifest_frozen"] = True
        report["checks"]["connector_code_exact"] = (
            descriptor.code == SYNTHETIC_WEBHOOK_CODE
        )
        report["checks"]["connector_version_exact"] = (
            descriptor.version == SYNTHETIC_WEBHOOK_CONNECTOR_VERSION
        )
        report["checks"]["connector_capability_exact"] = (
            descriptor.capabilities == (SYNTHETIC_WEBHOOK_CAPABILITY,)
        )
        report["checks"]["connector_mode_webhook"] = (
            descriptor.mode.value == "webhook"
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
        report["checks"]["connector_supports_reconciliation"] = (
            descriptor.supports_reconciliation is True
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
        namespace = (os.getenv("RTM_DATA_NAMESPACE") or "").lower()
        namespace_ok = "staging" in namespace
        report["checks"]["data_namespace_is_staging"] = namespace_ok
        if not namespace_ok:
            report["blockers"].append(
                "RTM_DATA_NAMESPACE_must_identify_staging"
            )
        isolated = (
            (os.getenv("RTM_SIDE_EFFECT_POLICY") or "").strip().lower()
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
            c4 = c4_schema_snapshot(conn)
            report["c1_schema"] = c1
            report["c3_schema"] = c3
            report["c4_schema"] = c4
            c1_ready = bool(c1["ready"])
            c3_ready = bool(c3["ready"])
            c4_ready = bool(c4["ready"])
            report["checks"]["c1_schema_ready"] = c1_ready
            report["checks"]["c3_schema_ready"] = c3_ready
            report["checks"]["c4_schema_ready"] = c4_ready
            if not c1_ready:
                report["blockers"].append(
                    "rtm_connect_c1_schema_not_ready"
                )
            if not c3_ready:
                report["blockers"].append(
                    "rtm_connect_c3_schema_not_ready"
                )
            if not c4_ready:
                report["blockers"].append(
                    "rtm_connect_c4_schema_not_ready"
                )

            migration_names = {
                "c1": "rtm_connect_c1_schema_v1_0",
                "c3": "rtm_connect_c3_manual_schema_v1_0",
                "c4": RTM_CONNECT_C4_WEBHOOK_SCHEMA_VERSION,
            }
            migration_checks: dict[str, bool] = {}
            for phase, migration_name in migration_names.items():
                count = conn.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM rtm_management_schema_migrations
                        WHERE name=:name
                        """
                    ),
                    {"name": migration_name},
                ).scalar_one()
                registered = int(count) == 1
                migration_checks[phase] = registered
                if not registered:
                    report["blockers"].append(
                        f"rtm_connect_{phase}_migration_missing"
                    )
            report["checks"]["c1_migration_registered"] = (
                migration_checks["c1"]
            )
            report["checks"]["c3_migration_registered"] = (
                migration_checks["c3"]
            )
            report["checks"]["c4_migration_registered"] = (
                migration_checks["c4"]
            )

            counts = conn.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM rtm_connect_connectors)
                            AS connectors_total,
                        (SELECT COUNT(*) FROM rtm_connect_connectors
                         WHERE synthetic_only=FALSE)
                            AS real_connectors,
                        (SELECT COUNT(*) FROM rtm_connect_connectors
                         WHERE code='synthetic.webhook' AND version='v1.0')
                            AS persistent_webhook,
                        (SELECT COUNT(*) FROM rtm_connect_webhook_inbox)
                            AS webhook_inbox,
                        (SELECT COUNT(*) FROM rtm_connect_webhook_events)
                            AS webhook_events,
                        (SELECT COUNT(*) FROM rtm_connect_reconciliations)
                            AS reconciliations,
                        (SELECT COUNT(*)
                         FROM rtm_connect_reconciliation_events)
                            AS reconciliation_events
                    """
                )
            ).mappings().one()
            for key, value in counts.items():
                report[key] = int(value)
            report["webhook_inbox_total"] = int(counts["webhook_inbox"])
            report["webhook_events_total"] = int(counts["webhook_events"])
            report["reconciliations_total"] = int(counts["reconciliations"])
            report["reconciliation_events_total"] = int(
                counts["reconciliation_events"]
            )
            report["checks"]["no_real_connectors"] = (
                int(counts["real_connectors"]) == 0
            )
            report["checks"]["no_persistent_connectors"] = (
                int(counts["connectors_total"]) == 0
            )
            report["checks"]["webhook_connector_not_persistently_seeded"] = (
                int(counts["persistent_webhook"]) == 0
            )
            residue_keys = (
                "webhook_inbox",
                "webhook_events",
                "reconciliations",
                "reconciliation_events",
            )
            no_residue = all(int(counts[key]) == 0 for key in residue_keys)
            report["checks"]["no_persistent_c4_residue"] = no_residue
            if int(counts["real_connectors"]) != 0:
                report["blockers"].append(
                    "real_connector_present_in_staging"
                )
            if int(counts["connectors_total"]) != 0:
                report["blockers"].append(
                    "persistent_connector_present_in_staging"
                )
            if int(counts["persistent_webhook"]) != 0:
                report["blockers"].append(
                    "persistent_synthetic_webhook_connector_present"
                )
            if not no_residue:
                report["blockers"].append(
                    "persistent_connect_c4_test_data_present"
                )

        app_path = ROOT / "app.py"
        app_present = app_path.exists()
        report["checks"]["app_runtime_file_present"] = app_present
        if not app_present:
            report["blockers"].append("app_runtime_file_missing")
        app_source = (
            app_path.read_text(encoding="utf-8")
            if app_present else ""
        )
        runtime_markers = (
            "rtm_connect_router",
            "include_router(rtm_connect",
            "receive_synthetic_webhook",
            "reconcile_webhook",
            "synthetic.webhook",
        )
        runtime_not_wired = not any(
            marker in app_source for marker in runtime_markers
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
            report["blockers"].append("rtm_connect_c4_checks_failed")
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
