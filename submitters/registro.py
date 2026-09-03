from __future__ import annotations

from typing import Any, Dict

from rtm_core.runtime_capabilities import require_capability

from .base import SubmitterNotReady


class RegistroSubmitter:
    """
    Canal multi-administración.

    IMPORTANTE (sin humanos):
    - No automatiza navegación web.
    - Se integra con un proveedor/servicio oficial de registro (SIR/GEISER o equivalente)
      o con una API intermediaria que devuelva justificante oficial.
    """

    name: str = "registro_general"
    _MAX_PDF_BYTES = 10 * 1024 * 1024
    _MAX_RESPONSE_BYTES = 15 * 1024 * 1024

    def submit(self, *, case_id: str, pdf_bytes: bytes) -> Dict[str, Any]:
        # Se comprueba antes de leer URL/token, serializar el documento o abrir
        # una conexión. En staging esta capacidad permanece bloqueada.
        require_capability("external_submission")
        del case_id, pdf_bytes
        raise SubmitterNotReady(
            "El conector de registro configurable está retirado hasta disponer "
            "de proveedor homologado y destino inmutable."
        )
