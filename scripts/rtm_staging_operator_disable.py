#!/usr/bin/env python3
"""Desactiva sin borrar una cuenta sintética de staging y revoca sus sesiones."""

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


CONFIRMATION = "STAGING_SYNTHETIC_OPERATOR_DISABLE_ONLY"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--email",
        default="rtm-staging-supervisor@example.com",
    )
    parser.add_argument("--confirmation", default="")
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
        "authority": "rtm_staging_operator_disable",
        "version": "rtm_staging_operator_disable_v1_0",
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "synthetic_only": True,
        "destructive": False,
        "history_preserved": True,
        "confirmation_required": CONFIRMATION,
        "blockers": [],
    }
    if report["environment"] != "staging":
        report["blockers"].append("RTM_ENV_must_be_staging")
    if args.confirmation != CONFIRMATION:
        report["blockers"].append("invalid_disable_confirmation")
    if report["blockers"]:
        report["safe"] = False
        _print(report, compact=args.compact)
        return 2

    try:
        from database import get_engine
        from rtm_core.environment_contract import assert_environment_ready
        from rtm_core.operator_provisioning import disable_synthetic_operator

        assert_environment_ready()
        engine = get_engine()
        with engine.begin() as conn:
            result = disable_synthetic_operator(conn, email=args.email)
        report.update(result)
        report["safe"] = True
        report["ok"] = True
        exit_code = 0
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["safe"] = False
        report["ok"] = False
        exit_code = 1

    _print(report, compact=args.compact)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
