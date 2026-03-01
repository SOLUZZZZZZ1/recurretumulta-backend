"""
RTM — TRÁFICO — ATENCIÓN / CONDUCCIÓN NEGLIGENTE (RGC)
ULTRA ADMIN v6 — Subtipos por ARTÍCULO + KEYWORDS (SIN IA)

Subtipos:
- ATN-MOV: Libertad de movimientos (Art. 18.1 típico) — acciones manuales (morder uñas, comer, beber…)
- ATN-ATT: Atención permanente (Art. 18.1 típico) — distracción / no percatarse / conversación / ciclistas / arcén / paralelo
- ATN-3.1: Conducción negligente (Art. 3.1) — exige riesgo concreto y coherencia interna

Salida: {"asunto","cuerpo"}
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
import re


# ==========================
# UTILIDADES
# ==========================

def _blob(core: Dict[str, Any], body: str = "") -> str:
    core = core or {}
    parts: List[str] = []
    for k in ("raw_text_pdf", "raw_text_vision", "raw_text_blob", "hecho_imputado"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    if isinstance(body, str) and body.strip():
        parts.append(body)
    return " ".join(parts).strip()


def _blob_lower(core: Dict[str, Any], body: str = "") -> str:
    return _blob(core, body).lower()


def _safe_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(str(v).strip())
    except Exception:
        return None


def _common_head(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    return {
        "expediente": str(core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."),
        "organo": str(core.get("organo") or core.get("organismo") or "No consta acreditado."),
        "hecho": str(core.get("hecho_imputado") or "NO MANTENER LA ATENCIÓN PERMANENTE A LA CONDUCCIÓN (RGC)."),
    }


# ==========================
# SUBTIPO (artículo + keywords)
# ==========================

def _detect_subtype(core: Dict[str, Any], body: str = "") -> str:
    """
    Devuelve: 'ATN-MOV' | 'ATN-ATT' | 'ATN-3.1' | 'ATN-GEN'
    """
    text = _blob_lower(core, body=body)
    art = _safe_int(core.get("articulo_infringido_num"))
    apt = _safe_int(core.get("apartado_infringido_num"))

    # Art. 3 => conducción negligente (3.1)
    if art == 3:
        return "ATN-3.1"

    mov_keywords = [
        "libertad de movimientos",
        "morder", "uñas", "unas",
        "comiendo", "comer",
        "bebiendo", "beber",
        "fumando", "fumar",
        "maquill", "afeit",
    ]

    att_keywords = [
        "no mantener la atención", "no mantener la atencion",
        "atención permanente", "atencion permanente",
        "no se percata", "no se percata que",
        "va conversando", "conversando",
        "distracc",  # distracción/distraccion
        "bicicleta", "ciclista", "ciclistas",
        "arcén", "arcen",
        "en paralelo", "paralelo",
        "carril", "ocupando",
        "atropello", "exponi",  # exponiéndose
        "de a tres",
    ]

    # Art. 18.* -> decidir por keywords
    if art == 18:
        if any(k in text for k in mov_keywords):
            return "ATN-MOV"
        if any(k in text for k in att_keywords):
            return "ATN-ATT"
        # si 18.2 (auriculares) idealmente lo maneja distracciones.py, pero aquí genérico
        if apt == 2:
            return "ATN-GEN"
        return "ATN-GEN"

    # Sin artículo: inferir
    if any(k in text for k in mov_keywords):
        return "ATN-MOV"
    if any(k in text for k in att_keywords):
        return "ATN-ATT"
    if "conducción negligente" in text or "conduccion negligente" in text:
        return "ATN-3.1"

    return "ATN-GEN"


# ==========================
# CONTEXTO
# ==========================

def is_atencion_context(core: Dict[str, Any], body: str = "") -> bool:
    text = _blob_lower(core, body=body)
    art = _safe_int(core.get("articulo_infringido_num"))
    if art in (3, 18):
        return True

    signals = [
        "conducción negligente", "conduccion negligente",
        "atención permanente", "atencion permanente",
        "no mantener la atención", "no mantener la atencion",
        "libertad de movimientos",
        "morder", "uñas", "unas",
        "ciclista", "ciclistas", "bicicleta",
    ]
    return any(s in text for s in signals)


# ==========================
# PLANTILLAS
# ==========================

def _tpl_mov(core: Dict[str, Any]) -> Dict[str, str]:
    h = _common_head(core)
    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = "\n".join([
        "A la atención del órgano competente,",
        "",
        "I. ANTECEDENTES",
        f"1) Órgano: {h['organo']}",
        f"2) Expediente: {h['expediente']}",
        f"3) Hecho imputado: {h['hecho']}",
        "",
        "II. ALEGACIONES",
        "",
        "ALEGACIÓN PRIMERA — TIPICIDAD: 'LIBERTAD DE MOVIMIENTOS' (ART. 18.1) NO PRESUMIBLE",
        "",
        "No toda acción manual puntual (p. ej., morderse las uñas, comer, beber, etc.) implica por sí misma una pérdida "
        "jurídicamente relevante del control del vehículo. Para apreciar infracción debe acreditarse afectación real y verificable "
        "a la conducción, y no una presunción automática.",
        "",
        "Debe precisarse, como mínimo:",
        "1) Duración y entidad de la acción.",
        "2) Cómo afectó al control del vehículo (trayectoria, maniobras, correcciones, etc.).",
        "3) Circunstancias de tráfico/visibilidad y posición del agente.",
        "4) Consecuencia objetiva o riesgo concreto derivado (si se alega).",
        "",
        "ALEGACIÓN SEGUNDA — PRUEBA COMPLETA Y MOTIVACIÓN INDIVIDUALIZADA",
        "",
        "Se solicita expediente íntegro (denuncia completa, informe ampliatorio si existe) y cualquier soporte objetivo "
        "(grabación, fotografías, anotaciones) que permita contradicción efectiva.",
        "",
        "III. SOLICITO",
        "1) Que se tengan por formuladas las presentes alegaciones.",
        "2) Que se acuerde el ARCHIVO por insuficiencia probatoria y falta de motivación concreta.",
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa.",
    ]).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}


def _tpl_att(core: Dict[str, Any]) -> Dict[str, str]:
    h = _common_head(core)
    text = _blob_lower(core)

    is_bici = any(w in text for w in ["bicicleta", "ciclista", "ciclistas"])
    has_arcen = ("arcén" in text) or ("arcen" in text)
    has_paralelo = ("paralelo" in text) or ("en paralelo" in text) or ("de a tres" in text)
    has_carril = ("carril" in text) or ("ocupando" in text)
    has_atropello = ("atropello" in text) or ("exponi" in text)
    has_no_percata = "no se percata" in text
    has_conversando = "convers" in text

    blocks: List[str] = []

    if is_bici or has_arcen or has_paralelo or has_carril or has_atropello:
        lines: List[str] = []
        lines.append("BLOQUE ESPECÍFICO — CICLISTAS / ARCÉN / PARALELO / CARRIL")
        lines.append("")
        lines.append("La imputación debe concretar el encaje normativo y el riesgo real. No basta una valoración abstracta.")
        if has_arcen:
            lines.append("- Si se invoca arcén, debe acreditarse que era practicable, continuo y seguro (estado, obstáculos, continuidad) y por qué se afirma obligación concreta de circular por él.")
        if has_paralelo:
            lines.append("- Si se alega circulación en paralelo o 'de a tres', debe precisarse posición exacta, anchura del carril y presencia real de tráfico.")
        if has_carril:
            lines.append("- Si se alega ocupación relevante del carril, debe precisarse anchura efectiva, distancia a vehículos y maniobra objetiva (frenada/adelantamiento evasivo/etc.).")
        if has_atropello:
            lines.append("- La referencia a 'atropello' es hipotética si no se identifica vehículo concreto, distancia, maniobra y consecuencia objetiva.")
        lines.append("La mera posibilidad teórica de riesgo no satisface el estándar exigible para la subsunción.")
        blocks.append("\n".join(lines))

    if has_no_percata or has_conversando:
        blocks.append("\n".join([
            "BLOQUE ESPECÍFICO — DISTRACCIÓN / CONVERSACIÓN",
            "",
            "Si se imputa que el conductor 'no se percata' o que iba conversando, debe concretarse qué hechos objetivos permiten esa conclusión "
            "(momento exacto, duración, señales de conducción anómala) y el riesgo real derivado, evitando presunciones.",
        ]))

    blocks_text = ("\n\n" + "\n\n".join(blocks) + "\n\n") if blocks else ""

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = "\n".join([
        "A la atención del órgano competente,",
        "",
        "I. ANTECEDENTES",
        f"1) Órgano: {h['organo']}",
        f"2) Expediente: {h['expediente']}",
        f"3) Hecho imputado: {h['hecho']}",
        "",
        "II. ALEGACIONES",
        "",
        "ALEGACIÓN PRIMERA — TIPICIDAD (ART. 18.1): CONDUCTA CONCRETA Y RIESGO OBJETIVABLE",
        "",
        "La falta de atención permanente exige una conducta concreta y un riesgo real, específico y objetivable. No basta una descripción genérica.",
        "",
        "Debe precisarse:",
        "1) Qué conducta exacta se observó y en qué momento.",
        "2) Circunstancias de tráfico/visibilidad y posición del agente.",
        "3) Qué riesgo concreto se produjo (consecuencia objetiva), no meramente hipotético.",
        "",
        blocks_text.strip(),
        "ALEGACIÓN SEGUNDA — EXPEDIENTE ÍNTEGRO Y PRUEBA COMPLETA",
        "",
        "Se solicita expediente íntegro y cualquier soporte objetivo (grabación, fotografías, anotaciones, testigos, croquis) para contradicción efectiva.",
        "",
        "III. SOLICITO",
        "1) Que se tengan por formuladas las presentes alegaciones.",
        "2) Que se acuerde el ARCHIVO por insuficiencia probatoria y falta de motivación concreta.",
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa.",
    ]).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}


def _tpl_31(core: Dict[str, Any]) -> Dict[str, str]:
    h = _common_head(core)
    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = "\n".join([
        "A la atención del órgano competente,",
        "",
        "I. ANTECEDENTES",
        f"1) Órgano: {h['organo']}",
        f"2) Expediente: {h['expediente']}",
        f"3) Hecho imputado: {h['hecho']}",
        "",
        "II. ALEGACIONES",
        "",
        "ALEGACIÓN PRIMERA — SUBSUNCIÓN EN ART. 3.1: PELIGRO CONCRETO Y COHERENCIA DEL RELATO",
        "",
        "El art. 3.1 RGC exige un peligro jurídicamente relevante y objetivable. Debe concretarse conducta, riesgo y consecuencia objetiva.",
        "",
        "ALEGACIÓN SEGUNDA — PRUEBA COMPLETA Y MOTIVACIÓN INDIVIDUALIZADA",
        "",
        "Se solicita expediente íntegro y prueba completa. Sin descripción circunstanciada, no puede enervarse la presunción de inocencia.",
        "",
        "III. SOLICITO",
        "1) Archivo por insuficiencia probatoria.",
        "2) Subsidiariamente, expediente íntegro y prueba completa.",
    ]).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}


# ==========================
# API PRINCIPAL
# ==========================

def build_atencion_strong_template(core: Dict[str, Any], body: str = "") -> Dict[str, str]:
    st = _detect_subtype(core, body=body)
    if st == "ATN-MOV":
        return _tpl_mov(core)
    if st == "ATN-ATT":
        return _tpl_att(core)
    if st == "ATN-3.1":
        return _tpl_31(core)
    return _tpl_att(core)