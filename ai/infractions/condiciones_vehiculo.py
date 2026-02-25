"""
RTM — CONDICIONES DEL VEHÍCULO (SVL-CV-3)

Subtipos:
- Art. 15 → Alumbrado / señalización óptica
- Art. 12 → Condiciones generales / modificaciones

Determinista. Sin OpenAI.
Robusto a OCR: si no llega articulo_infringido_num, infiere por raw_text_* y hecho_imputado.
"""

from __future__ import annotations
import re
from typing import Any, Dict


def _blob(core: Dict[str, Any]) -> str:
    core = core or {}
    parts = []
    for k in ("raw_text_pdf", "raw_text_vision", "raw_text_blob", "hecho_imputado"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    return " ".join(parts).lower()


def _infer_article(core: Dict[str, Any]) -> int | None:
    core = core or {}
    art = core.get("articulo_infringido_num")
    try:
        return int(art)
    except Exception:
        pass

    b = _blob(core)

    # Art. 15 por texto
    if re.search(r"\bart\.?\s*15\b", b) or re.search(r"\bart[ií]culo\s*15\b", b):
        return 15

    # Art. 12 por texto
    if re.search(r"\bart\.?\s*12\b", b) or re.search(r"\bart[ií]culo\s*12\b", b):
        return 12

    # Heurística alumbrado -> 15
    alumbrado_signals = [
        "alumbrado",
        "señalización óptica", "senalizacion optica",
        "dispositivos de alumbrado",
        "luz roja en la parte trasera",
        "destellos",
        "anexo ii",
    ]
    if any(s in b for s in alumbrado_signals):
        return 15

    return None


def build_condiciones_vehiculo_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    art = _infer_article(core)
    if art == 15:
        return _build_art15_alumbrado(core)
    return _build_art12_condiciones(core)


def _build_art15_alumbrado(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "INCUMPLIMIENTO DE DISPOSITIVOS DE ALUMBRADO O SEÑALIZACIÓN ÓPTICA (ART. 15)."

    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — ALUMBRADO/SEÑALIZACIÓN ÓPTICA (ART. 15): PRUEBA TÉCNICA OBJETIVA\n\n"
        "En infracciones relativas a alumbrado o señalización óptica, la imputación debe apoyarse en soporte objetivo y verificable, "
        "no en fórmulas genéricas. Debe acreditarse:\n"
        "1) Dispositivo concreto afectado (ubicación exacta y naturaleza).\n"
        "2) En qué consistía el incumplimiento (color, intensidad, modo de emisión, intermitencia/configuración).\n"
        "3) Norma técnica concreta aplicada (anexo/reglamentación invocada) y su motivación.\n"
        "4) Soporte objetivo (fotografías/vídeo) que permitan constatar el hecho.\n"
        "5) Descripción circunstanciada (distancia, visibilidad, condiciones de iluminación).\n\n"
        "No constando acreditación técnica suficiente, no puede tenerse por probado el hecho infractor.\n\n"
        "ALEGACIÓN SEGUNDA — EXPEDIENTE ÍNTEGRO Y MOTIVACIÓN\n\n"
        "Se solicita copia íntegra del expediente (boletín/denuncia completo, soportes y fundamentos), con identificación expresa del precepto aplicado "
        "y motivación individualizada.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación técnica objetiva.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y soporte técnico acreditativo.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}


def _build_art12_condiciones(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "INCUMPLIMIENTO DE CONDICIONES REGLAMENTARIAS DEL VEHÍCULO (ART. 12)."

    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — CONDICIONES REGLAMENTARIAS (ART. 12): NECESIDAD DE ACREDITACIÓN TÉCNICA\n\n"
        "La imputación por presunto incumplimiento de condiciones reglamentarias exige prueba objetiva y concreta del defecto atribuido. Debe constar:\n"
        "1) Defecto específico detectado.\n"
        "2) Norma técnica concreta vulnerada.\n"
        "3) Medio de constatación empleado.\n"
        "4) Soporte verificable que permita contradicción.\n\n"
        "En ausencia de acreditación técnica suficiente y descripción detallada del defecto, no puede tenerse por probado el hecho infractor.\n\n"
        "ALEGACIÓN SEGUNDA — MOTIVACIÓN INDIVIDUALIZADA\n\n"
        "Se solicita identificación expresa del precepto aplicado y motivación completa que justifique la subsunción del hecho descrito en la norma invocada.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y soporte técnico acreditativo.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}