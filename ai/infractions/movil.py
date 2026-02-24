"""
RTM — MÓVIL STRONG MODULE (SVL-MOV-3)

Objetivo:
- 100% determinista (sin OpenAI)
- No inventa hechos: redacción prudente ("si", "no consta", "no se aporta")
- Tipicidad quirúrgica: exige USO MANUAL EFECTIVO con descripción circunstanciada
- Diferencia modo de captación: AGENT / AUTO / UNKNOWN (si se pasa capture_mode)
- Solicita ARCHIVO (Tráfico determinista)
"""

from __future__ import annotations
import json
import re
from typing import Dict, Any, List


# --------------------------
# Detección de contexto
# --------------------------
def is_movil_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    blob = (body or "").lower()
    hecho = str(core.get("hecho_imputado") or "").lower()

    # Señales estructurales (tipo_infraccion si viene)
    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo == "movil":
        return True

    signals = [
        "teléfono", "telefono", "móvil", "movil",
        "uso manual", "utilizando manualmente", "en la mano",
        "manipulando", "pantalla", "whatsapp", "llamada",
    ]
    return any(s in (blob + "\n" + hecho) for s in signals)


# --------------------------
# Plantilla determinista fuerte
# --------------------------
def build_movil_strong_template(core: Dict[str, Any], capture_mode: str = "UNKNOWN") -> Dict[str, str]:
    core = core or {}
    cm = (capture_mode or "UNKNOWN").upper()

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "USO MANUAL DEL TELÉFONO MÓVIL."

    # Fecha/hora si consta (sin inventar)
    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    # Bloque específico según modo de captación
    if cm == "AGENT":
        captacion_block = (
            "ALEGACIÓN SEGUNDA — OBSERVACIÓN PRESENCIAL: FIABILIDAD PERCEPTIVA Y DESCRIPCIÓN CIRCUNSTANCIADA\n\n"
            "Si la imputación se basa en observación presencial, debe detallarse con precisión:\n"
            "• Posición del agente y punto exacto de observación.\n"
            "• Distancia aproximada y ángulo de visión respecto del habitáculo.\n"
            "• Condiciones de visibilidad (tráfico, iluminación, obstáculos, lunas/tintes).\n"
            "• Tiempo durante el cual se observó la conducta.\n"
            "• Identificación clara de la mano utilizada y de la acción concreta realizada.\n\n"
            "La ausencia de estos extremos impide verificar la fiabilidad perceptiva de la observación y ejercer contradicción efectiva.\n\n"
        )
    elif cm == "AUTO":
        captacion_block = (
            "ALEGACIÓN SEGUNDA — CAPTACIÓN TÉCNICA/AUTOMÁTICA: SOPORTE ÍNTEGRO Y LEGIBLE\n\n"
            "Si se invoca captación técnica o automática, debe aportarse soporte íntegro, legible y sin recortes "
            "(fotografías/secuencias/capturas), que permita constatar inequívocamente:\n"
            "• La identidad del vehículo.\n"
            "• El uso manual efectivo (no mera sujeción).\n"
            "• La correspondencia temporal del registro con el hecho imputado.\n\n"
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
        "ALEGACIÓN PRIMERA — TIPICIDAD: EXIGENCIA DE USO MANUAL EFECTIVO Y DESCRIPCIÓN CONCRETA\n\n"
        "La infracción exige acreditar un USO MANUAL EFECTIVO del teléfono móvil incompatible con la conducción. "
        "No basta una mención genérica, ni la mera presencia o sujeción del dispositivo, "
        "si no se describe una manipulación activa (p. ej., marcar, escribir, interactuar con pantalla) "
        "y su incidencia real en la conducción.\n\n"
        "No consta acreditado en el expediente, de forma concreta y verificable:\n"
        "1) Qué acción exacta se realizaba con el dispositivo (manipulación activa vs mera sujeción).\n"
        "2) Qué mano se utilizaba y cómo se constató dicho extremo.\n"
        "3) La duración aproximada de la conducta y el momento exacto de la observación/captación.\n"
        "4) Las circunstancias relevantes del tráfico y visibilidad en el instante del hecho.\n"
        "5) La motivación individualizada que permita subsunción típica y contradicción efectiva.\n\n"
        "En ausencia de descripción circunstanciada y prueba suficiente, no puede tenerse por acreditada la infracción, "
        "procediendo el ARCHIVO por insuficiencia probatoria.\n\n"
        f"{captacion_block}"
        "ALEGACIÓN TERCERA — NO NOTIFICACIÓN EN EL ACTO (SI PROCEDIERE)\n\n"
        "La no notificación inmediata debe motivarse de manera suficiente y específica. "
        "La mera fórmula estereotipada no suple el deber de motivación cuando se afirma constatación directa del hecho.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación suficiente del uso manual efectivo.\n"
        "3) Subsidiariamente, que se practique prueba y se aporte expediente íntegro (acta/denuncia completa y soportes, si existieran).\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}


# --------------------------
# Strict (SVL-MOV-3)
# --------------------------
def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []

    # Tipicidad fuerte
    if "uso manual efectivo" not in b and "uso manual" not in b:
        missing.append("tipicidad_uso_manual")
    if not any(k in b for k in ["acción exacta", "accion exacta", "manipulación activa", "manipulacion activa"]):
        missing.append("accion_concreta")
    if "mano" not in b:
        missing.append("mano_utilizada")
    if not any(k in b for k in ["duración", "duracion", "tiempo durante"]):
        missing.append("duracion_aproximada")

    # Descripción circunstanciada / fiabilidad
    if not any(k in b for k in ["distancia", "ángulo", "angulo", "posición del agente", "posicion del agente", "captación", "captacion", "fotograf", "secuencia"]):
        missing.append("modo_constatacion_y_soporte")

    # Archivo en solicito
    if not re.search(r"^2\)\s*que\s+se\s+acuerde\s+el\s+archivo", body or "", flags=re.IGNORECASE | re.MULTILINE):
        if "archivo del expediente" not in b and "acuerde el archivo" not in b:
            missing.append("solicito_archivo_punto2")

    # unique
    return list(dict.fromkeys(missing))


# Alias compatibilidad (por si algún punto del sistema busca este nombre)
def movil_strict_missing(body: str) -> List[str]:
    return strict_missing(body)