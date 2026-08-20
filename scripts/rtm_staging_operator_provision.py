#!/usr/bin/env python3
"""Crea una única cuenta sintética de operador en PostgreSQL staging.

La capacidad de login individual debe permanecer desactivada durante esta
operación. La contraseña generada se muestra una sola vez por stderr y nunca se
incluye en el JSON ni se guarda en PostgreSQL.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


CONFIRMATION = "STAGING_SYNTHETIC_OPERATOR_ONLY"
_TRUE = {"1", "true", "yes", "on", "enabled"}
_FALSE = {"", "0", "false", "no", "off", "disabled"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Provisión controlada de un operador sintético en staging."
    )
    parser.add_argument(
        "--email",
        default="rtm-staging-supervisor@example.com",
    )
    parser.add_argument(
        "--display-name",
        default="RTM STAGING SUPERVISOR",
    )
    parser.add_argument(
        "--role",
        choices=("operator", "supervisor"),
        default="supervisor",
    )
    parser.add_argument("--generate-password", action="store_true")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--compact", action="store_true")
    return parser


def _strict_flag(name: str) -> bool | None:
    raw = (os.getenv(name) or "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def _safety_blockers(args: argparse.Namespace) -> list[str]:
    blockers: list[str] = []
    if (os.getenv("RTM_ENV") or "").strip().lower() != "staging":
        blockers.append("RTM_ENV_must_be_staging")
    if "staging" not in (os.getenv("RTM_DATA_NAMESPACE") or "").lower():
        blockers.append("RTM_DATA_NAMESPACE_must_identify_staging")
    if (os.getenv("RTM_SIDE_EFFECT_POLICY") or "").strip().lower() != "isolated":
        blockers.append("RTM_SIDE_EFFECT_POLICY_must_be_isolated")
    if _strict_flag("RTM_ALLOW_REAL_CUSTOMER_DATA") is not False:
        blockers.append("RTM_ALLOW_REAL_CUSTOMER_DATA_must_be_false")
    feature = _strict_flag("RTM_ENABLE_OPERATOR_AUTH_V1")
    if feature is None:
        blockers.append("RTM_ENABLE_OPERATOR_AUTH_V1_invalid")
    elif feature:
        blockers.append("operator_auth_must_be_disabled_during_provisioning")
    if args.confirmation != CONFIRMATION:
        blockers.append("invalid_provision_confirmation")
    return blockers


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
        "authority": "rtm_staging_operator_provision",
        "version": "rtm_staging_operator_provision_v1_0",
        "environment": (os.getenv("RTM_ENV") or "").strip().lower() or "unset",
        "synthetic_only": True,
        "operator_auth_enabled": bool(
            _strict_flag("RTM_ENABLE_OPERATOR_AUTH_V1")
        ),
        "legacy_login_unchanged": True,
        "operator_creation_public": False,
        "confirmation_required": CONFIRMATION,
        "blockers": [],
    }

    blockers = _safety_blockers(args)
    if blockers:
        report["blockers"] = blockers
        report["safe"] = False
        _print(report, compact=args.compact)
        return 2

    try:
        from database import get_engine
        from rtm_core.environment_contract import assert_environment_ready
        from rtm_core.operator_provisioning import (
            find_operator_by_email,
            generate_temporary_password,
            normalize_synthetic_operator_email,
            provision_synthetic_operator,
        )

        assert_environment_ready()
        normalized_email = normalize_synthetic_operator_email(args.email)
        engine = get_engine()

        with engine.begin() as conn:
            existing = find_operator_by_email(conn, normalized_email)
            if existing:
                result = provision_synthetic_operator(
                    conn,
                    email=normalized_email,
                    display_name=args.display_name,
                    role_key=args.role,
                    password="RTM existing synthetic account placeholder 2026!",
                )
                report.update(
                    {
                        "ok": True,
                        "safe": True,
                        "operator_created": False,
                        "password_issued": False,
                        "operator_id": result.operator_id,
                        "email": result.email,
                        "role_code": result.role_code,
                        "status": "already_exists",
                    }
                )
                _print(report, compact=args.compact)
                return 0

            if args.generate_password:
                password = generate_temporary_password()
            else:
                password = getpass.getpass(
                    "Contraseña temporal del operador sintético: "
                )
                confirmation = getpass.getpass(
                    "Repite la contraseña temporal: "
                )
                if password != confirmation:
                    raise ValueError("Las contraseñas no coinciden")

            result = provision_synthetic_operator(
                conn,
                email=normalized_email,
                display_name=args.display_name,
                role_key=args.role,
                password=password,
            )

        if result.password_issued:
            print(
                "\nRTM_STAGING_TEMPORARY_PASSWORD_BEGIN\n"
                f"{password}\n"
                "RTM_STAGING_TEMPORARY_PASSWORD_END\n"
                "GUÁRDALA EN PRIVADO Y NO LA PEGUES EN EL CHAT.\n",
                file=sys.stderr,
            )

        report.update(
            {
                "ok": True,
                "safe": True,
                "operator_created": result.created,
                "password_issued": result.password_issued,
                "operator_id": result.operator_id,
                "email": result.email,
                "display_name": result.display_name,
                "role_code": result.role_code,
                "status": "created" if result.created else "already_exists",
            }
        )
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
