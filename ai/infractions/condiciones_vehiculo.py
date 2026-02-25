"""
RTM — CONDICIONES DEL VEHÍCULO (SVL-CV-4 PRO)

Objetivo:
- Determinista (sin OpenAI).
- Robusto a OCR: si no llega articulo_infringido_num, infiere por raw_text_* y hecho_imputado.
- Subtipos internos (especialmente Art. 12) para respuestas más precisas y medibles.

Subtipos:
- Art. 15 → ART15_ALUMBRADO
- Art. 12 → ART12_ITV | ART12_NEUMATICOS | ART12_REFLECTANTE | ART12_REFORMAS | ART12_OTROS

Salida enriquecida (para auditoría/integración):
- family, subtype, strict_id, confidence, asunto, cuerpo

Nota:
- Este módulo genera plantillas "fuertes" orientadas a carga probatoria / motivación.
"""

from __future__ import annotations
import re
from typing import Any, Dict, Optional, Tuple


FAMILY_ID = "SVL-CV-4"


# -----------------------------
# Utils: normalización / blob
# -----------------------------
def _blob(core: Dict[str, Any]) -> str:
    core = core or {}
    parts = []
    for k in ("raw_text_pdf", "raw_text_vision", "raw_text_blob", "hecho_imputado"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    return " ".join(parts).lower()


def _safe_str(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


# -----------------------------
# Inferencia de artículo
# -----------------------------
def _infer_article(core: Dict[str, Any]) -> Optional[int]:
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
        "luz trasera", "luces traseras",
        "destellos",
        "anexo ii",
    ]
    if any(s in b for s in alumbrado_signals):
        return 15

    return None


# -----------------------------
# Subtipificación Art. 12
# -----------------------------
def _score_signals(b: str, signals: list[str]) -> int:
    score = 0
    for s in signals:
        if s in b:
            score += 1
    return score


def _infer_art12_subtype(core: Dict[str, Any]) -> Tuple[str, str]:
    """
    Devuelve (subtype, confidence) solo para Art. 12.
    confidence: high/medium/low
    """
    b = _blob(core)

    # 1) ITV
    itv_signals = [
        "itv", "inspección técnica", "inspeccion tecnica",
        "caducad", "sin itv", "favorable", "desfavorable",
        "tarjeta itv", "inspeccion periodica", "inspección periódica",
    ]

    # 2) Neumáticos
    neum_signals = [
        "neumático", "neumatico", "neumáticos", "neumaticos",
        "dibujo", "profundidad", "mm", "desgastad", "liso",
        "presión", "presion", "revent", "banda de rodadura",
    ]

    # 3) Reflectante / deslumbramiento (tu caso real)
    refl_signals = [
        "pulid", "reflej", "reflect", "como un espejo",
        "deslumbr", "emitir deslumbr", "encandil",
        "cisterna pulida", "parte trasera pulida", "parte trasera reflect",
    ]

    # 4) Reformas / modificaciones
    reform_signals = [
        "reforma", "modificación", "modificacion", "homolog",
        "proyecto", "certificado", "taller", "inspección de reforma",
        "variación", "variacion", "no autorizada", "sin autorización",
    ]

    scores = {
        "ART12_ITV": _score_signals(b, itv_signals),
        "ART12_NEUMATICOS": _score_signals(b, neum_signals),
        "ART12_REFLECTANTE": _score_signals(b, refl_signals),
        "ART12_REFORMAS": _score_signals(b, reform_signals),
    }

    # Elige el mejor
    best = max(scores.items(), key=lambda kv: kv[1])
    best_subtype, best_score = best

    if best_score >= 3:
        return best_subtype, "high"
    if best_score == 2:
        return best_subtype, "medium"
    if best_score == 1:
        return best_subtype, "low"
    return "ART12_OTROS", "low"


# -----------------------------
# Stricts (deterministas)
# -----------------------------
STRICTS: Dict[str, Dict[str, Any]] = {
    "STRICT_CV_BASE": {
        "requires_objective_proof": True,
        "requires_norm_reference": True,
        "requires_support_media_or_report": True,
        "requires_individual_motivation": True,
    },
    "STRICT_CV_ALUMBRADO": {
        "requires_device_specificity": True,
        "requires_visibility_context": True,
        "requires_media": True,
    },
    "STRICT_CV_ITV": {
        "requires_itv_status": True,
        "requires_expiry_or_result": True,
        "requires_document_reference": True,
    },
    "STRICT_CV_NEUMATICOS": {
        "requires_measurement_or_detail": True,
        "requires_tire_position_detail": True,
        "requires_media_or_report": True,
    },
    "STRICT_CV_REFLECT": {
        "requires_technical_basis": True,
        "requires_method_of_verification": True,
        "requires_media_or_report": True,
    },
    "STRICT_CV_REFORMAS": {
        "requires_mod_detail": True,
        "requires_homologation_basis": True,
        "requires_doc_reference": True,
    },
}


def build_condiciones_vehiculo_strong_template(core: Dict[str, Any]) -> Dict[str, Any]:
    """
    Router principal del módulo.
    """
    art = _infer_article(core)
    if art == 15:
        return _build_art15_alumbrado(core)

    # Por defecto Art. 12 (si no podemos inferir: Art.12 es la familia más probable en este módulo)
    return _build_art12_condiciones(core)


# -----------------------------
# Builders
# -----------------------------
def _common_header(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if _safe_str(fecha_hecho) else ""
    return {"expediente": str(expediente), "organo": str(organo), "fecha_line": fecha_line}


def _build_art15_alumbrado(core: Dict[str, Any]) -> Dict[str, Any]:
    core = core or {}
    h = _common_header(core)
    hecho = core.get("hecho_imputado") or "INCUMPLIMIENTO DE DISPOSITIVOS DE ALUMBRADO O SEÑALIZACIÓN ÓPTICA (ART. 15)."

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {h['organo']}\n"
        f"2) Identificación expediente: {h['expediente']}\n"
        f"3) Hecho imputado: {hecho}{h['fecha_line']}\n\n"
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

    return {
        "family": FAMILY_ID,
        "subtype": "ART15_ALUMBRADO",
        "strict_id": "STRICT_CV_ALUMBRADO",
        "confidence": "high",
        "asunto": asunto,
        "cuerpo": cuerpo,
        "strict": STRICTS.get("STRICT_CV_ALUMBRADO", {}),
    }


def _build_art12_condiciones(core: Dict[str, Any]) -> Dict[str, Any]:
    core = core or {}
    h = _common_header(core)
    hecho = core.get("hecho_imputado") or "INCUMPLIMIENTO DE CONDICIONES REGLAMENTARIAS DEL VEHÍCULO (ART. 12)."

    subtype, conf = _infer_art12_subtype(core)

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    # Bloque base (común)
    base_block = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {h['organo']}\n"
        f"2) Identificación expediente: {h['expediente']}\n"
        f"3) Hecho imputado: {hecho}{h['fecha_line']}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — CONDICIONES REGLAMENTARIAS (ART. 12): NECESIDAD DE ACREDITACIÓN TÉCNICA\n\n"
        "La imputación por presunto incumplimiento de condiciones reglamentarias exige prueba objetiva y concreta del defecto atribuido. Debe constar:\n"
        "1) Defecto específico detectado.\n"
        "2) Norma técnica concreta vulnerada.\n"
        "3) Medio de constatación empleado.\n"
        "4) Soporte verificable que permita contradicción.\n\n"
        "En ausencia de acreditación técnica suficiente y descripción detallada del defecto, no puede tenerse por probado el hecho infractor.\n\n"
    )

    # Bloques específicos por subtipo
    extra = ""
    strict_id = "STRICT_CV_BASE"

    if subtype == "ART12_ITV":
        strict_id = "STRICT_CV_ITV"
        extra = (
            "ALEGACIÓN ESPECÍFICA — ITV: CONCRECIÓN DEL ESTADO Y SOPORTE DOCUMENTAL\n\n"
            "En supuestos relacionados con ITV, debe concretarse el estado atribuido (caducada, desfavorable, negativa, etc.), "
            "la fecha exacta de vencimiento/resultado y la referencia documental utilizada para la constatación. "
            "Sin esa concreción y soporte, la imputación resulta insuficiente.\n\n"
        )
    elif subtype == "ART12_NEUMATICOS":
        strict_id = "STRICT_CV_NEUMATICOS"
        extra = (
            "ALEGACIÓN ESPECÍFICA — NEUMÁTICOS: MEDICIÓN/DETALLE Y POSICIÓN\n\n"
            "En defectos de neumáticos debe indicarse el elemento concreto (rueda/posición), el defecto observado "
            "(profundidad de dibujo, deformación, lonas, etc.) y, en su caso, la medición o soporte verificable. "
            "La mera referencia genérica no permite contradicción efectiva.\n\n"
        )
    elif subtype == "ART12_REFLECTANTE":
        strict_id = "STRICT_CV_REFLECT"
        extra = (
            "ALEGACIÓN ESPECÍFICA — DESLUMBRAMIENTO POR SUPERFICIE REFLECTANTE: BASE TÉCNICA\n\n"
            "Si se imputa deslumbramiento por superficies pulidas/reflectantes, debe constar la norma técnica concreta "
            "aplicable (reglamentación/anexo específico), el método de verificación empleado y soporte objetivo (fotografías/vídeo o informe). "
            "En ausencia de esa base técnica y soporte verificable, no puede tenerse por probado el hecho.\n\n"
        )
    elif subtype == "ART12_REFORMAS":
        strict_id = "STRICT_CV_REFORMAS"
        extra = (
            "ALEGACIÓN ESPECÍFICA — REFORMAS/MODIFICACIONES: IDENTIFICACIÓN Y HOMOLOGACIÓN\n\n"
            "Si se imputa una reforma/modificación, debe identificarse con precisión el elemento modificado, el motivo por el que "
            "se considera no conforme y la base normativa/homologación exigible, con referencia documental. "
            "Sin esa individualización, la imputación es genérica e insuficiente.\n\n"
        )

    tail = (
        "ALEGACIÓN SEGUNDA — MOTIVACIÓN INDIVIDUALIZADA\n\n"
        "Se solicita identificación expresa del precepto aplicado y motivación completa que justifique la subsunción del hecho descrito en la norma invocada.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y soporte técnico acreditativo.\n"
    )

    cuerpo = (base_block + extra + tail).strip()

    return {
        "family": FAMILY_ID,
        "subtype": subtype,
        "strict_id": strict_id,
        "confidence": conf,
        "asunto": asunto,
        "cuerpo": cuerpo,
        "strict": STRICTS.get(strict_id, {}),
    }
