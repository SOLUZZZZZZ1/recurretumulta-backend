from __future__ import annotations

from typing import Any, Dict

from .base import SubmitterNotReady


class DGTSubmitter:
    """
    Canal específico DGT (cuando tengáis el cliente homologado listo).

    Implementación pendiente: debe enviar el PDF por el canal homologado y devolver
    justificante oficial en PDF + metadatos (registro/csv/fecha...).
    """

    name: str = "dgt"

    def submit(self, *, case_id: str, pdf_bytes: bytes) -> Dict[str, Any]:
        raise SubmitterNotReady("DGTSubmitter no implementado aún (falta cliente técnico homologado).")
