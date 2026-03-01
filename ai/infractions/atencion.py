"""
RTM — TRÁFICO — ATENCIÓN / CONDUCCIÓN NEGLIGENTE (RGC)
ULTRA ADMIN v6.2 — Subtipos por ARTÍCULO + KEYWORDS (SIN IA) — PRODUCCIÓN ESTABLE

Subtipos:
- ATN-MOV: Libertad de movimientos (Art. 18.1 típico) — acciones manuales (morder uñas, comer, beber…)
- ATN-ATT: Atención permanente (Art. 18.1 típico) — distracción / no percatarse / conversación / ciclistas / arcén / paralelo
- ATN-3.1: Conducción negligente (Art. 3.1) — exige riesgo concreto + coherencia interna + estándar probatorio (REFORZADO)

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


def _km_in_text(text: str) -> Optional[str]:
    t = (text or "").lower()
    m = re.search(r"\b(\d+(?:[\.,]\d+)?)\s*km\b", t)
    if not m:
        return None
    return m.group(1).replace(",", ".")


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
# PLANTILLAS (FUERTES)
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
        "La infracción no puede deducirse automáticamente de una acción aislada (p. ej., morderse las uñas).",
        "Debe acreditarse una afectación REAL y OBJETIVA al control del vehículo. No basta una presunción.",
        "",
        "Para enervar la presunción de inocencia debe constar, al menos:",
        "1) Duración y entidad de la acción (puntual vs mantenida).",
        "2) Cómo afectó al control: trayectoria errática, correcciones bruscas, maniobras anómalas.",
        "3) Circunstancias de tráfico/visibilidad y posición del agente (distancia/ángulo/obstáculos).",
        "4) Consecuencia objetiva o riesgo concreto derivado de esa acción (si se alega).",
        "",
        "ALEGACIÓN SEGUNDA — MOTIVACIÓN INDIVIDUALIZADA Y PRUEBA COMPLETA",
        "",
        "Se solicita denuncia íntegra e informe ampliatorio (si existe) con descripción circunstanciada,",
        "así como cualquier soporte objetivo disponible (grabación, fotografías, anotaciones) para contradicción efectiva.",
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

    # Bloque fuerte ciclistas/arcén/paralelo/carril/atropello
    if is_bici or has_arcen or has_paralelo or has_carril or has_atropello:
        lines: List[str] = []
        lines.append("BLOQUE ESPECÍFICO — CICLISTAS / ARCÉN / PARALELO / CARRIL (ENCAJE NORMATIVO + RIESGO REAL)")
        lines.append("")
        lines.append(
            "La denuncia alude a circulación en bicicleta junto a otros ciclistas y a una supuesta situación de riesgo. "
            "Sin embargo, la subsunción en el art. 18.1 exige concreción fáctica y riesgo OBJETIVABLE, no inferencias genéricas."
        )
        lines.append("")
        lines.append("1) Arcén:")
        lines.append(
            "   La mera mención a un arcén (incluso con indicación de anchura) no permite presumir obligación automática de circular por él. "
            "   Debe acreditarse que era practicable, continuo y seguro en ese punto concreto (estado, obstáculos, continuidad, visibilidad) "
            "   y por qué su uso era viable en las circunstancias reales."
        )
        lines.append("")
        lines.append("2) Paralelo / 'de a tres' / ocupación del carril:")
        lines.append(
            "   Debe precisarse anchura efectiva del carril, intensidad del tráfico, posición exacta, distancia respecto a otros vehículos "
            "   y maniobra concreta que evidencie riesgo (frenada brusca, maniobra evasiva, alteración real de la circulación). "
            "   Sin esos datos, la imputación es estereotipada."
        )
        lines.append("")
        lines.append("3) 'Exposición a atropello':")
        lines.append(
            "   Constituye una valoración hipotética si no se identifica vehículo concreto, maniobra real, distancia y consecuencia objetiva. "
            "   El riesgo abstracto o potencial no satisface el estándar probatorio exigible."
        )
        lines.append("")
        lines.append(
            "Sin descripción circunstanciada del peligro real y su relación con una conducta concreta, no procede la subsunción típica."
        )
        blocks.append("\n".join(lines))

    # Bloque distracción/conversación/no percatarse (fuerte)
    if has_no_percata or has_conversando:
        blocks.append("\n".join([
            "BLOQUE ESPECÍFICO — DISTRACCIÓN / CONVERSACIÓN / 'NO SE PERCATA'",
            "",
            "Si se imputa que el conductor 'no se percata' o que iba conversando, debe concretarse el hecho objetivo que lo demuestra:",
            "- momento exacto y duración aproximada,",
            "- signos externos observables (trayectoria, maniobras),",
            "- condiciones de observación y distancia del agente,",
            "- consecuencia objetiva (riesgo real), no meramente hipotética.",
            "Sin esa concreción, la conclusión es una inferencia no verificable.",
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
        "ALEGACIÓN PRIMERA — TIPICIDAD (ART. 18.1): CONDUCTA CONCRETA + RIESGO OBJETIVABLE",
        "",
        "La falta de atención permanente exige una conducta concreta y un riesgo real, específico y objetivable.",
        "No basta una descripción genérica ni valoraciones hipotéticas.",
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
    text = _blob_lower(core)
    km = _km_in_text(text)

    has_bail = any(w in text for w in ["bail", "palmas", "golpeando", "volante", "tambor"])
    has_menor = any(w in text for w in ["menor", "dos años", "dos anos", "sri"])

    blocks: List[str] = []

    if has_bail:
        blocks.append(
            "La descripción de gestos como 'bailar', 'dar palmas' o 'golpear el volante' "
            "no equivale automáticamente a conducción peligrosa. "
            "Sin acreditación de pérdida objetiva de control, alteración de trayectoria o maniobra anómala, "
            "la imputación se basa en una valoración conductual subjetiva."
        )

    if km:
        blocks.append(
            f"La afirmación de riesgo continuado durante {km} km resulta difícilmente compatible "
            "con la ausencia de intervención inmediata. "
            "Si el peligro era real y persistente, debía producirse actuación preventiva sin dilación. "
            "Esta incoherencia interna debilita la consistencia del relato fáctico."
        )

    if has_menor:
        blocks.append(
            "La mención a la presencia de un menor no sustituye la exigencia de acreditar peligro concreto. "
            "Si se pretende reforzar la imputación con dicha circunstancia, debe identificarse infracción específica "
            "y relación causal directa con riesgo real y no meramente hipotético."
        )

    blocks_text = "\n\n".join(blocks)

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
        "ALEGACIÓN PRIMERA — SUBSUNCIÓN EN ART. 3.1 RGC",
        "",
        "El art. 3.1 exige un peligro jurídicamente relevante y objetivable. "
        "No basta una descripción llamativa si no se acredita riesgo efectivo y consecuencia objetiva.",
        "",
        blocks_text,
        "",
        "ALEGACIÓN SEGUNDA — PRUEBA COMPLETA Y MOTIVACIÓN",
        "",
        "Se solicita expediente íntegro y soporte probatorio suficiente. "
        "Sin descripción circunstanciada y acreditación técnica, no puede enervarse la presunción de inocencia.",
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