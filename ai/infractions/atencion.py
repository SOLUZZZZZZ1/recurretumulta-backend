from __future__ import annotations
from typing import Any, Dict
import re


def _blob(core: Dict[str, Any], body: str = "") -> str:
    parts = []
    for k in ("raw_text_pdf", "raw_text_vision", "raw_text_blob", "hecho_imputado"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    if body:
        parts.append(body)
    return " ".join(parts)


def _blob_lower(core: Dict[str, Any], body: str = "") -> str:
    return _blob(core, body).lower()


def is_atencion_context(core: Dict[str, Any], body: str = "") -> bool:
    text = _blob_lower(core, body)

    signals = [
        "atención permanente",
        "atencion permanente",
        "libertad de movimientos",
        "conducción negligente",
        "conduccion negligente",
        "morder",
        "ciclista",
        "bicicleta",
    ]

    return any(s in text for s in signals)


def build_atencion_strong_template(core: Dict[str, Any], body: str = "") -> Dict[str, str]:

    expediente = core.get("expediente_ref") or "No consta acreditado."
    organo = core.get("organo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "NO MANTENER LA ATENCIÓN PERMANENTE."

    asunto = "ESCRITO DE ALEGACIONES"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"Órgano: {organo}\n"
        f"Expediente: {expediente}\n"
        f"Hecho imputado: {hecho}\n\n"
        "II. ALEGACIONES\n\n"
        "La imputación exige concreción fáctica suficiente y acreditación de riesgo real.\n"
        "No basta una valoración genérica.\n\n"
        "III. SOLICITO\n"
        "Archivo del expediente por insuficiencia probatoria."
    )

    return {"asunto": asunto, "cuerpo": cuerpo}