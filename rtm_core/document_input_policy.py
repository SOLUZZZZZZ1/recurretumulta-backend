"""Bloqueo runtime de entradas documentales según el entorno RTM.

El contrato de entorno valida la configuración antes del arranque. Este módulo
aplica la misma decisión a la ruta que podría enviar documentos persistidos al
proveedor externo.

En staging con ``synthetic_only`` la única entrada autorizada es el smoke
interno ``scripts/rtm_staging_smoke.py``. La ruta OPS de expedientes queda
bloqueada aunque el proveedor esté temporalmente habilitado, evitando que un
archivo subido a PostgreSQL/B2 pueda salir del entorno durante la prueba.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping, Optional


DOCUMENT_INPUT_POLICY_VERSION = "rtm_document_input_policy_v1_0"

_DOCUMENT_EXTRACTION_RUN_PATH = re.compile(
    r"^/ops/core/cases/[^/]+/document-extractions/run/?$"
)


@dataclass(frozen=True)
class DocumentInputPolicyBlock:
    status_code: int
    detail: dict[str, object]


def _value(environ: Mapping[str, str], name: str) -> str:
    return str(environ.get(name) or "").strip()


def document_input_policy_block(
    *,
    method: str,
    path: str,
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[DocumentInputPolicyBlock]:
    """Devuelve el bloqueo aplicable a una petición o ``None`` si puede seguir.

    La función es deliberadamente pequeña y determinista para poder probar la
    política sin iniciar FastAPI ni contactar con servicios externos.
    """

    if str(method or "").upper() != "POST":
        return None
    if not _DOCUMENT_EXTRACTION_RUN_PATH.fullmatch(str(path or "")):
        return None

    source: Mapping[str, str] = environ if environ is not None else os.environ
    environment = _value(source, "RTM_ENV").lower()
    policy = _value(source, "RTM_DOCUMENT_INPUT_POLICY").lower()

    if environment == "staging":
        if policy != "synthetic_only":
            return DocumentInputPolicyBlock(
                status_code=503,
                detail={
                    "message": (
                        "La extracción documental está bloqueada porque staging "
                        "no declara la política synthetic_only."
                    ),
                    "environment": environment,
                    "required_policy": "synthetic_only",
                    "configured_policy": policy or "unset",
                    "policy_version": DOCUMENT_INPUT_POLICY_VERSION,
                },
            )

        return DocumentInputPolicyBlock(
            status_code=409,
            detail={
                "message": (
                    "Staging synthetic_only no puede procesar documentos "
                    "persistidos de expedientes. La prueba autorizada utiliza "
                    "exclusivamente fixtures ficticios internos."
                ),
                "environment": environment,
                "policy": policy,
                "allowed_entrypoint": "scripts/rtm_staging_smoke.py",
                "policy_version": DOCUMENT_INPUT_POLICY_VERSION,
            },
        )

    if environment == "production" and policy != "customer_documents":
        return DocumentInputPolicyBlock(
            status_code=503,
            detail={
                "message": (
                    "La extracción documental de producción exige la política "
                    "customer_documents."
                ),
                "environment": environment,
                "required_policy": "customer_documents",
                "configured_policy": policy or "unset",
                "policy_version": DOCUMENT_INPUT_POLICY_VERSION,
            },
        )

    return None
