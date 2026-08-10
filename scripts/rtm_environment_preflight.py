#!/usr/bin/env python3
"""Ejecuta el contrato de aislamiento del entorno RTM.

La salida es deliberadamente segura: identifica variables, capacidades y
bloqueos, pero nunca imprime valores de credenciales, URLs completas ni cadenas
de conexión.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


# Permite ejecutar ``python scripts/rtm_environment_preflight.py`` desde la raíz
# del repositorio sin depender de PYTHONPATH externo.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from rtm_core.environment_contract import build_environment_preflight


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Valida que un entorno RTM esté aislado antes de desplegar.",
    )
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        help="Devuelve código 2 cuando no hay bloqueos pero sí advertencias.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Imprime JSON compacto en lugar de indentado.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    report = build_environment_preflight()
    payload = report.model_dump(mode="json")
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            separators=(",", ":") if args.compact else None,
        )
    )
    if not report.safe:
        return 1
    if args.strict_warnings and report.warnings:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
