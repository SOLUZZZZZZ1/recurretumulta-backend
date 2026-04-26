"""RTM — MÓVIL (SVL-MOV-4) — DEMOLEDOR 9.5/10 (Modo B por defecto; Modo C solo graves)

Determinista, sin OpenAI.
Compatibilidad: is_movil_context(), build_movil_strong_template(), strict_missing(), movil_strict_missing()
"""

from __future__ import annotations

import re
import json
from typing import Any, Dict, List, Optional


def _get_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(round(v))
        s = str(v).strip()
        if not s:
            return None
        s = s.replace("€", "").replace(".", "").replace(",", "").strip()
        return int(s) if s.isdigit() else None
    except Exception:
        return None


def _is_grave(core: Dict[str, Any]) -> bool:
    core = core or {}
    fine = _get_int(core.get("sancion_importe_eur") or core.get("importe") or core.get("importe_total_multa"))
    pts = _get_int(core.get("puntos_detraccion") or core.get("puntos") or 0) or 0
    if pts >= 3:
        return True
    if fine is not None and fine >= 500:
        return True
    g = str(core.get("gravedad") or "").lower().strip()
    return g in ("grave", "muy grave", "critico", "crítico")


def is_movil_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    blob = (body or "").lower()
    hecho = str(core.get("hecho_imputado") or "").lower()

    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo == "movil":
        return True

    signals = [
        "teléfono", "telefono", "móvil", "movil",
        "uso manual", "utilizando manualmente", "en la mano",
        "manipulando", "manipulación", "manipulacion",
        "pantalla", "whatsapp", "llamada",
    ]
    return any(s in (blob + "\n" + hecho) for s in signals)


def build_movil_strong_template(core: Dict[str, Any], capture_mode: str = "UNKNOWN") -> Dict[str, str]:
    core = core or {}
    cm = (capture_mode or "UNKNOWN").upper()
    modo_c = _is_grave(core)

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "USO MANUAL DEL TELÉFONO MÓVIL."

    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    if cm == "AGENT":
        captacion_block = (
            "ALEGACIÓN SEGUNDA — MODO DE CONSTATACIÓN NO CONCLUYENTE Y AUSENCIA DE PRUEBA OBJETIVA\n\n"
            "Si la imputación se basa en observación presencial, debe detallarse con precisión:\n"
            "• Posición del agente y punto exacto de observación.\n"
            "• Distancia aproximada y ángulo de visión respecto del habitáculo.\n"
            "• Condiciones de visibilidad (tráfico, iluminación, obstáculos, lunas/tintes).\n"
            "• Tiempo durante el cual se observó la conducta.\n"
            "• Identificación clara de la mano utilizada y de la acción concreta realizada.\n\n"
            "La ausencia de estos extremos impide verificar la fiabilidad perceptiva y ejercer contradicción efectiva.\n\n"
        )
    elif cm == "AUTO":
        captacion_block = (
            "ALEGACIÓN SEGUNDA — CAPTACIÓN TÉCNICA/AUTOMÁTICA: SOPORTE ÍNTEGRO Y LEGIBLE\n\n"
            "Si se invoca captación técnica o automática, debe aportarse soporte íntegro, legible y sin recortes "
            "(fotografías/secuencias/capturas), que permita constatar inequívocamente:\n"
            "• La identidad del vehículo.\n"
            "• El uso manual efectivo (no mera tenencia).\n"
            "• La correspondencia temporal exacta del registro con el hecho imputado.\n\n"
            "En ausencia de soporte verificable, procede el archivo por insuficiencia probatoria.\n\n"
        )
    else:
        captacion_block = (
            "ALEGACIÓN SEGUNDA — MODO DE CONSTATACIÓN NO CONCLUYENTE: APORTACIÓN DE PRUEBA COMPLETA\n\n"
            "No constando con claridad el modo de constatación (observación presencial vs captación técnica), "
            "se solicita la aportación de la prueba completa y del acta/denuncia íntegra con motivación individualizada. "
            "En caso de no constar, procede el archivo por insuficiencia probatoria.\n\n"
        )

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — TIPICIDAD (ART. 18.2): USO MANUAL EFECTIVO, ACCIÓN CONCRETA Y PRUEBA INEQUÍVOCA\n\n"
        "La infracción exige acreditar un USO MANUAL EFECTIVO del teléfono móvil incompatible con la conducción. "
        "No basta una mención genérica, ni la mera presencia o sujeción del dispositivo, si no se describe una manipulación activa "
        "(p. ej., marcar, escribir, interactuar con pantalla) y su constatación inequívoca.\n\n"
        "No consta acreditado en el expediente, de forma concreta y verificable:\n"
        "1) Qué acción exacta se realizaba con el dispositivo (manipulación activa vs mera sujeción).\n"
        "2) Qué mano se utilizaba y cómo se constató dicho extremo.\n"
        "3) Duración aproximada y momento exacto de la conducta.\n"
        "4) Condiciones relevantes de tráfico/visibilidad y posibilidad real de percepción (distancia, ángulo, obstáculos).\n"
        "5) Motivación individualizada que permita subsunción típica y contradicción efectiva.\n\n"
        "Sin descripción circunstanciada y prueba suficiente, no puede tenerse por acreditada la infracción, procediendo el ARCHIVO.\n\n"
        f"{captacion_block}"
        "ALEGACIÓN TERCERA — NO NOTIFICACIÓN EN EL ACTO (SI PROCEDIERE)\n\n"
        "La no notificación inmediata debe motivarse de manera suficiente y específica. "
        "La mera fórmula estereotipada no suple el deber de motivación cuando se afirma constatación directa del hecho.\n\n"
    )

    if modo_c:
        cuerpo += (
            "ALEGACIÓN ADICIONAL (MODO C — GRAVEDAD): LEGALIDAD, TIPICIDAD Y PRESUNCIÓN DE INOCENCIA\n\n"
            "Cuando la sanción incorpora pérdida de puntos o especial gravedad, la exigencia de prueba inequívoca y motivación "
            "individualizada es máxima. En ausencia de descripción concreta (mano/acción/duración) y soporte verificable, "
            "no se enerva la presunción de inocencia (art. 24 CE) ni se satisface la tipicidad estricta (art. 25 CE).\n\n"
        )

    cuerpo += (
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación suficiente del uso manual efectivo.\n"
        "3) Subsidiariamente, que se practique prueba y se aporte expediente íntegro (acta/denuncia completa y soportes, si existieran).\n"
    )

    return {"asunto": asunto, "cuerpo": cuerpo.strip()}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []
    if "uso manual" not in b:
        missing.append("tipicidad_uso_manual")
    if "mano" not in b:
        missing.append("mano_utilizada")
    if "duración" not in b and "duracion" not in b:
        missing.append("duracion")
    if "archivo" not in b:
        missing.append("archivo")
    out=[]
    seen=set()
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def movil_strict_missing(body: str) -> List[str]:
    return strict_missing(body)
