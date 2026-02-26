"""
RTM — TRÁFICO — ATENCIÓN / CONDUCCIÓN NEGLIGENTE (SVL-ATN-1)

Cubre:
- Art. 3.1 RGC: conducción negligente
- Art. 18.1 RGC: no mantener la atención permanente

Determinista.
"""

from __future__ import annotations
from typing import Any, Dict, List


def _blob(core: Dict[str, Any], body: str = "") -> str:
    core = core or {}
    parts: List[str] = []

    for k in ("raw_text_pdf", "raw_text_vision", "raw_text_blob", "hecho_imputado"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)

    if isinstance(body, str) and body.strip():
        parts.append(body)

    return " ".join(parts).lower()


def is_atencion_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    b = _blob(core, body)

    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo in (
        "atencion",
        "atención",
        "negligente",
        "conduccion_negligente",
        "conducción_negligente",
    ):
        return True

    try:
        art = int(core.get("articulo_infringido_num"))
    except Exception:
        art = None

    signals = [
        "conducción negligente",
        "conduccion negligente",
        "no mantener la atención permanente",
        "no mantener la atencion permanente",
        "atención permanente",
        "atencion permanente",
        "no mantiene la atención",
        "no mantiene la atencion",
        "distracción",
        "distraccion",
        "no adoptar las precauciones necesarias",
    ]

    if art in (3, 18) and any(s in b for s in signals):
        return True

    if any(
        s in b
        for s in [
            "conducción negligente",
            "conduccion negligente",
            "no mantener la atención permanente",
            "no mantener la atencion permanente",
        ]
    ):
        return True

    return False


def build_atencion_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "CONDUCCIÓN NEGLIGENTE / NO MANTENER LA ATENCIÓN PERMANENTE A LA CONDUCCIÓN."

    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if fecha_hecho else ""

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — TIPICIDAD Y DESCRIPCIÓN CONCRETA\n\n"
        "La imputación por conducción negligente o falta de atención permanente exige una descripción "
        "circunstanciada y motivación individualizada. No basta una fórmula genérica.\n\n"
        "Debe precisarse:\n"
        "1) Conducta concreta observada.\n"
        "2) Circunstancias del tráfico y visibilidad.\n"
        "3) Momento exacto y duración aproximada.\n"
        "4) Fundamento jurídico específico aplicado.\n\n"
        "En ausencia de prueba suficiente y motivación concreta, no puede tenerse por acreditada la infracción.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro con soporte probatorio.\n"
    )

    return {"asunto": asunto, "cuerpo": cuerpo}