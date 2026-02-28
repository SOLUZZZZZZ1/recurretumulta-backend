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


def _detect_subtype(core: Dict[str, Any], body: str = "") -> str:
    """
    Devuelve: 'ATN-MOV' | 'ATN-ATT' | 'ATN-3.1' | 'ATN-GEN'
    Usa artículo/apartado si existen + keywords en texto.
    """
    text = _blob_lower(core, body=body)
    art = _safe_int(core.get("articulo_infringido_num"))
    apt = _safe_int(core.get("apartado_infringido_num"))

    # 3.1 = negligente (si viene detectado)
    if art == 3:
        return "ATN-3.1"

    # 18.1 suele ser atención/libertad movimientos
    # Si no hay artículo, inferimos por keywords.
    mov_keywords = [
        "libertad de movimientos",
        "morder", "uñas", "unas",
        "comiendo", "comer",
        "bebiendo", "beber",
        "fumando", "fumar",
        "maquill", "afeit",
        "buscando", "cogiendo",
    ]

    att_keywords = [
        "no mantener la atención", "no mantener la atencion",
        "atención permanente", "atencion permanente",
        "no se percata", "no se percata que",
        "va conversando", "conversando",
        "distra",  # distracción/distraccion
        "bicicleta", "ciclista", "ciclistas",
        "arcén", "arcen",
        "en paralelo", "paralelo",
        "carril", "ocupando",
        "atropello", "exponi",  # exponiéndose
    ]

    # Si es 18.1 explícito, decide por keywords dominantes
    if art == 18 and (apt == 1 or apt is None):
        if any(k in text for k in mov_keywords):
            return "ATN-MOV"
        if any(k in text for k in att_keywords):
            return "ATN-ATT"
        return "ATN-GEN"

    # Si viene 18.2 suele ser auriculares (idealmente lo maneja distracciones.py),
    # pero por si entra aquí, lo tratamos como atención genérica.
    if art == 18 and apt == 2:
        return "ATN-GEN"

    # Sin artículo: inferimos
    if any(k in text for k in mov_keywords):
        return "ATN-MOV"
    if any(k in text for k in att_keywords):
        return "ATN-ATT"

    # Si menciona "conducción negligente" sin art 3
    if "conducción negligente" in text or "conduccion negligente" in text:
        return "ATN-3.1"

    return "ATN-GEN"


# ==========================
# CONTEXTO
# ==========================

def is_atencion_context(core: Dict[str, Any], body: str = "") -> bool:
    """
    True si parece Art. 3.1 o Art. 18.* o keywords claras de atención/movimientos.
    """
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
# PLANTILLAS POR SUBTIPO
# ==========================

def _common_head(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    return {
        "expediente": str(core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."),
        "organo": str(core.get("organo") or core.get("organismo") or "No consta acreditado."),
        "hecho": str(core.get("hecho_imputado") or "NO MANTENER LA ATENCIÓN PERMANENTE A LA CONDUCCIÓN (RGC)."),
    }


def _tpl_mov(core: Dict[str, Any]) -> Dict[str, str]:
    h = _common_head(core)
    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {h['organo']}\n"
        f"2) Expediente: {h['expediente']}\n"
        f"3) Hecho imputado: {h['hecho']}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — TIPICIDAD: 'LIBERTAD DE MOVIMIENTOS' (ART. 18.1) NO PRESUMIBLE\n\n"
        "No toda acción manual puntual (p. ej., morderse las uñas, comer, beber, etc.) implica por sí misma una pérdida "
        "jurídicamente relevante del control del vehículo. Para apreciar infracción debe acreditarse afectación real y verificable "
        "a la conducción, y no una presunción automática.\n\n"
        "Debe precisarse, como mínimo:\n"
        "1) Duración y entidad de la acción.\n"
        "2) Cómo afectó al control del vehículo (trayectoria, maniobras, correcciones, etc.).\n"
        "3) Circunstancias de tráfico/visibilidad y posición del agente.\n"
        "4) Consecuencia objetiva o riesgo concreto derivado (si se alega).\n\n"
        "ALEGACIÓN SEGUNDA — PRUEBA COMPLETA Y MOTIVACIÓN INDIVIDUALIZADA\n\n"
        "Se solicita expediente íntegro (denuncia completa, informe ampliatorio si existe) y cualquier soporte objetivo "
        "(grabación, fotografías, anotaciones) que permita contradicción efectiva.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO por insuficiencia probatoria y falta de motivación concreta.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa.\n"
    )
    return {"asunto": asunto, "cuerpo": cuerpo}


def _tpl_att(core: Dict[str, Any]) -> Dict[str, str]:
    h = _common_head(core)
    text = _blob_lower(core)

    # activar micro-bloques
    is_bici = any(w in text for w in ["bicicleta", "ciclista", "ciclistas"])
    has_arcen = ("arcén" in text) or ("arcen" in text)
    has_paralelo = ("paralelo" in text) or ("en paralelo" in text)
    has_carril = "carril" in text or "ocupando" in text
    has_atropello = "atropello" in text or "exponi" in text
    has_no_percata = "no se percata" in text
    has_conversando = "convers" in text

    blocks: List[str] = []
    if is_bici or has_arcen or has_paralelo or has_carril or has_atropello:
        lines: List[str] = []
        lines.append(
            "BLOQUE ESPECÍFICO — CICLISTAS / ARCÉN / PARALELO / CARRIL\n\n"
            "La imputación debe concretar el encaje normativo y el riesgo real. No basta una valoración abstracta.\n"
        )
        if has_arcen:
            lines.append(
                "- Si se invoca arcén, debe acreditarse que era practicable, continuo y seguro en ese punto (estado, obstáculos, continuidad), "
                "y por qué se afirma obligación concreta de circular por él.\n"
            )
        if has_paralelo or "de a tres" in text:
            lines.append(
                "- Si se alega circulación en paralelo o 'de a tres', debe precisarse posición exacta, anchura del carril y presencia real de tráfico.\n"
            )
        if has_carril:
            lines.append(
                "- Si se alega ocupación relevante del carril, debe precisarse anchura efectiva, distancia a vehículos y maniobra objetiva (frenada/adelantamiento evasivo/etc.).\n"
            )
        if has_atropello:
            lines.append(
                "- La referencia a 'atropello' es hipotética si no se identifica vehículo concreto, distancia, maniobra y consecuencia objetiva.\n"
            )
        blocks.append("".join(lines))

    if has_no_percata or has_conversando:
        blocks.append(
            "BLOQUE ESPECÍFICO — DISTRACCIÓN / CONVERSACIÓN\n\n"
            "Si se imputa que el conductor 'no se percata' o que iba conversando, debe concretarse qué hechos objetivos permiten esa conclusión "
            "(momento exacto, duración, señales de conducción anómala) y el riesgo real derivado.\n"
        )

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = (
        "A la atención del órgano competente,\n\n",
        "I. ANTECEDENTES\n"
        f"1) Órgano: {h['organo']}\n"
        f"2) Expediente: {h['expediente']}\n"
        f"3) Hecho imputado: {h['hecho']}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — TIPICIDAD (ART. 18.1): CONDUCTA CONCRETA Y RIESGO OBJETIVABLE\n\n"
        "La falta de atención permanente exige una conducta concreta y un riesgo real, específico y objetivable. "
        "No basta la mera descripción genérica.\n\n"
        "Debe precisarse:\n"
        "1) Qué conducta exacta se observó y en qué momento.\n"
        "2) Circunstancias de tráfico/visibilidad y posición del agente.\n"
        "3) Qué riesgo concreto se produjo (consecuencia objetiva), no meramente hipotético.\n\n"
        + ("\n\n".join(blocks) + "\n\n" if blocks else "")
        "ALEGACIÓN SEGUNDA — EXPEDIENTE ÍNTEGRO Y PRUEBA COMPLETA\n\n"
        "Se solicita expediente íntegro y cualquier soporte objetivo (grabación, fotografías, anotaciones, testigos, croquis) "
        "para contradicción efectiva.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO por insuficiencia probatoria y falta de motivación concreta.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa.\n"
    )
    return {"asunto": asunto, "cuerpo": cuerpo}


def _tpl_31(core: Dict[str, Any]) -> Dict[str, str]:
    h = _common_head(core)
    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {h['organo']}\n"
        f"2) Expediente: {h['expediente']}\n"
        f"3) Hecho imputado: {h['hecho']}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — SUBSUNCIÓN EN ART. 3.1: PELIGRO CONCRETO Y COHERENCIA DEL RELATO\n\n"
        "El art. 3.1 RGC exige un peligro jurídicamente relevante y objetivable. Debe concretarse conducta, riesgo y consecuencia objetiva.\n\n"
        "ALEGACIÓN SEGUNDA — PRUEBA COMPLETA Y MOTIVACIÓN INDIVIDUALIZADA\n\n"
        "Se solicita expediente íntegro y prueba completa. Sin descripción circunstanciada, no puede enervarse la presunción de inocencia.\n\n"
        "III. SOLICITO\n"
        "1) Archivo por insuficiencia probatoria.\n"
        "2) Subsidiariamente, expediente íntegro y prueba completa.\n"
    )
    return {"asunto": asunto, "cuerpo": cuerpo}


# ==========================
# API
# ==========================

def build_atencion_strong_template(core: Dict[str, Any], body: str = "") -> Dict[str, str]:
    st = _detect_subtype(core, body=body)
    if st == "ATN-MOV":
        return _tpl_mov(core)
    if st == "ATN-ATT":
        return _tpl_att(core)
    if st == "ATN-3.1":
        return _tpl_31(core)
    # genérico
    return _tpl_att(core)