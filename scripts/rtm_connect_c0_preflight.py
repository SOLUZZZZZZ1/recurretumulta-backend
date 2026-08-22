#!/usr/bin/env python3
"""Preflight de solo lectura de RTM CONNECT C0.

C0 debe permanecer sin rutas, tablas, conectores reales ni efectos externos.
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


_TRUE = {"1", "true", "yes", "on", "enabled"}
_FALSE = {"", "0", "false", "no", "off", "disabled"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    return parser


def _flag(name: str) -> bool | None:
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "ok": False,
        "authority": "rtm_connect_c0_preflight",
        "version": "rtm_connect_c0_preflight_v1_0",
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "read_only": True,
        "architecture_only": True,
        "routes_published": False,
        "database_schema_created": False,
        "connectors_registered": False,
        "external_effects_executed": False,
        "checks": {},
        "blockers": [],
    }

    if report["environment"] != "staging":
        report["blockers"].append("RTM_ENV_must_be_staging")
        report["safe"] = False
        _print(report, compact=args.compact)
        return 2

    try:
        from rtm_connect.manifest import (
            EXPECTED_MANIFEST_SHA256,
            architecture_manifest,
            assert_manifest_frozen,
            manifest_sha256,
        )
        from rtm_connect.state_machine import ActionStatus, next_states

        assert_manifest_frozen()
        manifest = architecture_manifest()
        report["checks"]["manifest_frozen"] = (
            manifest_sha256() == EXPECTED_MANIFEST_SHA256
        )
        report["checks"]["authority_rule_frozen"] = (
            manifest["authority_rule"]
            == "CORE authorizes; CONNECT executes; evidence confirms; "
            "only then CORE may change legal state"
        )
        report["checks"]["unknown_state_present"] = (
            "unknown" in manifest["states"]
            and "reconciling" in manifest["states"]
        )
        report["checks"]["e4_evidence_present"] = (
            "E4_receipt_verified" in manifest["evidence_levels"]
        )
        report["checks"]["manual_handoff_planned"] = (
            "manual_handoff" in manifest["components"]
        )
        report["checks"]["unknown_has_no_direct_retry"] = (
            "queued"
            not in {
                state.value
                for state in next_states(ActionStatus.UNKNOWN)
            }
        )

        app_path = ROOT / "app.py"
        app_source = (
            app_path.read_text(encoding="utf-8")
            if app_path.exists()
            else ""
        )
        report["checks"]["runtime_not_wired"] = (
            "rtm_connect_router" not in app_source
            and "include_router(rtm_connect" not in app_source
        )
        report["checks"]["no_connect_schema_module"] = not (
            ROOT / "rtm_connect" / "schema.py"
        ).exists()

        for name in (
            "RTM_ENABLE_EXTERNAL_SUBMISSION",
            "RTM_ENABLE_OUTBOUND_EMAIL",
            "RTM_ENABLE_STRIPE",
            "RTM_ENABLE_FINAL_PAYMENTS",
        ):
            value = _flag(name)
            report["checks"][f"{name.lower()}_disabled"] = value is False
            if value is not False:
                report["blockers"].append(f"{name}_must_be_false")

        real_data = _flag("RTM_ALLOW_REAL_CUSTOMER_DATA")
        report["checks"]["real_customer_data_disabled"] = (
            real_data is False
        )
        if real_data is not False:
            report["blockers"].append(
                "RTM_ALLOW_REAL_CUSTOMER_DATA_must_be_false"
            )

        report["checks"]["side_effect_policy_isolated"] = (
            (os.getenv("RTM_SIDE_EFFECT_POLICY") or "").strip().lower()
            == "isolated"
        )
        if not report["checks"]["side_effect_policy_isolated"]:
            report["blockers"].append(
                "RTM_SIDE_EFFECT_POLICY_must_be_isolated"
            )

        failed = sorted(
            key
            for key, value in report["checks"].items()
            if not bool(value)
        )
        report["failed_checks"] = failed
        if failed:
            report["blockers"].append("rtm_connect_c0_checks_failed")

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
