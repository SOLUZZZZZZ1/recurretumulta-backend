"""
RTM — TRÁFICO — ATENCIÓN / CONDUCCIÓN NEGLIGENTE
VERSIÓN PRODUCCIÓN ESTABLE (ULTRA ADMIN V5)

- Sin IA
- Sin llamadas externas
- Subtipos automáticos:
    * Bicicleta / ciclistas
    * Arcén
    * Ocupación de carril / paralelo
    * Exposición a atropello
    * Menor / SRI
    * Conductas internas (bailar, palmas, morder uñas)
- Técnico-administrativo fuerte
"""

from __future__ import annotations
from typing import Any, Dict, List
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
    if body:
        parts.append(body)
    return " ".join(parts)


def _blob_lower(core: Dict[str, Any], body: str = "") -> str:
    return _blob(core, body).lower()


def _detect_subtypes(text: str) -> Dict[str, Any]:
    t = text.lower()

    km_val = None
    m_km = re.search(r"\b(\d+(?:[\.,]\d+)?)\s*km\b", t)
    if m_km:
        km_val = m_km.group(1).replace(",", ".")

    return {
        "is_bici": any(w in t for w in ["bicicleta", "ciclista", "ciclistas"]),
        "has_arcen": any(w in t for w in ["arcén", "arcen"]),
        "has_carril": any(w in t for w in ["carril", "paralelo", "ocupando"]),
        "has_atropello": "atropello" in t or "exponi" in t,
        "has_menor": any(w in t for w in ["menor", "dos años", "dos anos", "sri"]),
        "has_bail": any(w in t for w in ["bail", "palmas", "golpeando", "volante"]),
        "has_morder_unas": any(w in t for w in ["mordía", "mordia", "morder", "uñas", "unas"]),
        "km_val": km_val,
    }


# ==========================
# CONTEXTO
# ==========================

def is_atencion_context(core: Dict[str, Any], body: str = "") -> bool:
    text = _blob_lower(core, body)

    signals = [
        "conducción negligente",
        "conduccion negligente",
        "atención permanente",
        "atencion permanente",
        "no mantener la atención",
        "libertad de movimientos",
        "creando una situación de riesgo",
        "creando una situacion de riesgo",
    ]

    return any(s in text for s in signals)


# ==========================
# GENERADOR PRINCIPAL
# ==========================

def build_atencion_strong_template(core: Dict[str, Any], body: str = "") -> Dict[str, str]:

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "CONDUCCIÓN NEGLIGENTE / FALTA DE ATENCIÓN PERMANENTE."

    texto_completo = _blob(core, body)
    subs = _detect_subtypes(texto_completo)

    bloques: List[str] = []

    # -------- TRAMO --------
    if subs["km_val"]:
        bloques.append(
            f"BLOQUE ESPECÍFICO — TRAMO/SEGUIMIENTO\n\n"
            f"Se afirma un seguimiento de aproximadamente {subs['km_val']} km. "
            "Debe indicarse el método de determinación del tramo, continuidad de observación "
            "y motivo de no intervención inmediata si el riesgo era real.\n"
        )

    # -------- CICLISTAS --------
    if subs["is_bici"] or subs["has_arcen"] or subs["has_carril"] or subs["has_atropello"]:
        bloque_bici = (
            "BLOQUE ESPECÍFICO — CIRCULACIÓN DE CICLISTAS / ARCÉN / OCUPACIÓN DE CARRIL\n\n"
            "La mera referencia a bicicleta, ocupación de carril o uso del arcén exige encaje normativo concreto.\n"
            "Debe acreditarse:\n"
            "- Practicabilidad real del arcén.\n"
            "- Anchura efectiva del carril.\n"
            "- Intensidad del tráfico en ese momento.\n"
            "- Maniobra objetiva que evidencie riesgo real.\n\n"
            "La simple posibilidad teórica de atropello no satisface el estándar exigible para subsumir el hecho en el art. 3.1 RGC.\n"
        )
        bloques.append(bloque_bici)

    # -------- CONDUCTAS INTERNAS --------
    if subs["has_bail"]:
        bloques.append(
            "BLOQUE ESPECÍFICO — CONDUCTAS INTERNAS (BAILAR / PALMAS / VOLANTE)\n\n"
            "Debe describirse con precisión qué se observó, durante cuánto tiempo "
            "y cómo afectó objetivamente al control del vehículo.\n"
        )

    if subs["has_morder_unas"]:
        bloques.append(
            "BLOQUE ESPECÍFICO — LIBERTAD DE MOVIMIENTOS\n\n"
            "No toda acción manual puntual implica pérdida jurídicamente relevante del control del vehículo. "
            "Debe acreditarse afectación real y no presunción automática.\n"
        )

    # -------- MENOR --------
    if subs["has_menor"]:
        bloques.append(
            "BLOQUE ESPECÍFICO — MENOR / SRI\n\n"
            "La presencia de menor no suple la prueba del riesgo concreto. "
            "Debe identificarse el encaje normativo específico y relación causal directa.\n"
        )

    bloques_texto = "\n\n".join(bloques)

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — PRESUNCIÓN DE INOCENCIA Y CARGA PROBATORIA\n\n"
        "Corresponde a la Administración acreditar con precisión los hechos constitutivos de infracción.\n\n"
        "ALEGACIÓN SEGUNDA — SUBSUNCIÓN EN EL ART. 3.1 RGC\n\n"
        "La conducción negligente exige riesgo jurídicamente relevante y objetivable. "
        "La referencia abstracta a 'riesgo' no es suficiente sin concreción fáctica.\n\n"
        "ALEGACIÓN TERCERA — CONCRECIÓN TEMPORAL Y COHERENCIA\n\n"
        "Debe precisarse duración, circunstancias de tráfico y posición del agente.\n\n"
        f"{bloques_texto}\n\n"
        "III. SOLICITO\n"
        "1) Archivo del expediente por insuficiencia probatoria.\n"
        "2) Subsidiariamente, aportación íntegra del expediente y prueba completa.\n"
    )

    return {"asunto": asunto, "cuerpo": cuerpo}