# dgt_client.py — conector DGT (homologado) para presentación automática
# ---------------------------------------------------------------
# IMPORTANTE: este módulo es el único punto donde vive la integración real con DGT.
# La automatización (OPS) llamará a submit_pdf() y espera:
#   - registro: str (número de registro o identificador de presentación)
#   - csv: str|None (si DGT devuelve CSV)
#   - justificante_pdf: bytes (PDF justificante o acuse)
#
# Cuando tengáis el endpoint/certificado/cliente definitivo, implementad aquí
# y el resto del sistema quedará “sin humanos”.

from __future__ import annotations

import os
from typing import Dict, Any, Optional


class DGTNotConfigured(RuntimeError):
    pass


def is_configured() -> bool:
    # Marca rápida: si existe variable, consideramos que hay integración real.
    # Sustituye esto por las variables necesarias (cert, pfx, endpoint, etc.).
    return bool((os.getenv("DGT_ENABLED") or "").strip())


def submit_pdf(case_id: str, pdf_bytes: bytes, *, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Presenta el PDF en DGT y devuelve registro/csv/justificante_pdf.

    Debe ser idempotente a nivel de negocio (si DGT permite idempotency key, usad case_id).
    """
    if not is_configured():
        raise DGTNotConfigured("Integración DGT no configurada (DGT_ENABLED vacío).")

    # TODO: Implementación real (homologada):
    # - cargar certificado / client auth
    # - preparar request (multipart, SOAP/REST, etc.)
    # - enviar
    # - parsear respuesta
    # - descargar justificante
    #
    # Debe devolver:
    # return {"registro": "...", "csv": "...", "justificante_pdf": b"..."}
    raise NotImplementedError("submit_pdf() pendiente de implementar con la integración homologada.")
