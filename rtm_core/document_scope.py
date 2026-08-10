"""Alcance común de documentos aptos para lectura factual RTM.

La decisión de si un documento puede alimentar la extracción pertenece al
núcleo y no a cada router. Identidad, autorizaciones, pagos, justificantes y
documentos ya generados nunca se usan como fuente factual del problema.
"""

from __future__ import annotations

import re
from typing import Optional


DOCUMENT_SCOPE_VERSION = "rtm_document_scope_v1_0"

_EXCLUDED_KIND_TOKENS = {
    "generated",
    "rtm_generated",
    "receipt",
    "justificante",
    "submission",
    "presentacion",
    "authorization",
    "autorizacion",
    "identity_front",
    "identity_back",
    "dni_front",
    "dni_back",
    "nie_front",
    "nie_back",
    "payment",
    "stripe",
    "firma",
    "signature",
}


def normalized_document_kind(value: Optional[str]) -> str:
    return re.sub(
        r"[^a-z0-9_]+",
        "_",
        str(value or "").strip().lower(),
    ).strip("_")


def is_extractable_document_kind(value: Optional[str]) -> bool:
    kind = normalized_document_kind(value)
    if not kind:
        return False
    return not any(token in kind for token in _EXCLUDED_KIND_TOKENS)


def excluded_document_kind_tokens() -> tuple[str, ...]:
    return tuple(sorted(_EXCLUDED_KIND_TOKENS))
