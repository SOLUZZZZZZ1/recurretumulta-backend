"""
RTM — CONDUCCIÓN NEGLIGENTE / ATENCIÓN (ART. 3.1 / 18 RGC)
Módulo reforzado — versión avanzada
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
        "distracción",
        "distraccion",
    ]

    if art in (3, 18) and any(s in b for s in signals):
        return True

    if any(s in b for s in signals):
        return True

    return False


def build_atencion_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "CONDUCCIÓN NEGLIGENTE / FALTA DE ATENCIÓN PERMANENTE."

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}\n\n"

        "II. ALEGACIONES\n\n"

        "ALEGACIÓN PRIMERA — FALTA DE DESCRIPCIÓN CIRCUNSTANCIADA\n\n"
        "La imputación por conducción negligente exige concreción fáctica suficiente. "
        "No basta una fórmula genérica. Debe precisarse:\n"
        "1) Conducta exacta observada.\n"
        "2) Momento concreto y duración real.\n"
        "3) Circunstancias del tráfico y visibilidad.\n"
        "4) Identificación del supuesto riesgo generado.\n\n"

        "ALEGACIÓN SEGUNDA — INEXISTENCIA DE RIESGO REAL\n\n"
        "Para que exista tipicidad es necesario que la conducta genere un riesgo concreto y objetivable. "
        "No consta:\n"
        "- Maniobra evasiva de terceros.\n"
        "- Invasión de carril.\n"
        "- Frenada brusca.\n"
        "- Daño o incidente.\n\n"
        "Sin riesgo real, no puede subsumirse el hecho en el art. 3.1 RGC.\n\n"

        "ALEGACIÓN TERCERA — VALORACIÓN SUBJETIVA NO SUFICIENTE\n\n"
        "La mera apreciación subjetiva del agente, sin soporte objetivo o descripción técnica detallada, "
        "no resulta suficiente para enervar la presunción de inocencia.\n\n"

        "ALEGACIÓN CUARTA — PROPORCIONALIDAD\n\n"
        "Aun en hipótesis de conducta irregular, debe valorarse proporcionalidad y circunstancias concretas.\n\n"

        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa.\n"
    )

    return {"asunto": asunto, "cuerpo": cuerpo}