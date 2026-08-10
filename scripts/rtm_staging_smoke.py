#!/usr/bin/env python3
"""Ejecuta el smoke test live de RTM únicamente con documentos sintéticos.

No usa base de datos, B2, expedientes reales ni Generate. Para realizar llamadas
al proveedor exige simultáneamente:

    RTM_ENV=staging
    RTM_STAGING_CONFIRM=SYNTHETIC_ONLY
    RTM_ALLOW_SYNTHETIC_LIVE_EXTRACTION=1
    OPENAI_API_KEY=<clave del entorno de staging>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


# Al ejecutar ``python scripts/rtm_staging_smoke.py``, Python coloca ``scripts``
# como primer elemento de sys.path. Se añade exclusivamente la raíz del propio
# repositorio para importar rtm_core sin depender de una instalación global.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from fastapi import HTTPException

from rtm_core.staging_validation import (
    LIVE_CONFIRMATION,
    run_synthetic_staging_suite,
    staging_scenarios,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Valida en live los documentos sintéticos de staging de RTM.",
    )
    parser.add_argument(
        "--services",
        default="",
        help=(
            "Servicios separados por comas. Vacío ejecuta debt, administration, "
            "travel y claims."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Muestra los escenarios disponibles sin contactar con el proveedor.",
    )
    return parser


def _services(raw: str) -> Optional[list[str]]:
    values = [item.strip().lower() for item in str(raw or "").split(",")]
    result = [item for item in values if item]
    return result or None


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    selected = _services(args.services)

    if args.list:
        payload = {
            "confirmation_required": LIVE_CONFIRMATION,
            "scenarios": [
                {
                    "code": item.code,
                    "service": item.service,
                    "fixture": item.fixture_filename,
                    "expected_family": item.expected_family,
                }
                for item in staging_scenarios(selected)
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    try:
        report = run_synthetic_staging_suite(
            selected_services=selected,
            require_live_guard=True,
        )
    except HTTPException as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "status_code": exc.status_code,
                    "detail": exc.detail,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 3

    print(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
