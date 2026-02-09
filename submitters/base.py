from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


class SubmitterNotReady(RuntimeError):
    """El canal existe conceptualmente, pero aún no está integrado (sin humanos)."""


@dataclass
class SubmitResult:
    # PDF del acuse/justificante oficial (regla dura para marcar submitted)
    justificante_pdf: bytes
    # Metadatos relevantes (registro, csv, fecha, organismo, etc.)
    meta: Dict[str, Any]


class BaseSubmitter:
    name: str = "base"

    def submit(self, *, case_id: str, pdf_bytes: bytes) -> Dict[str, Any]:
        """
        Debe devolver dict con:
          - justificante_pdf: bytes
          - meta: dict
        """
        raise NotImplementedError
