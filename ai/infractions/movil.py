"""
RTM — Módulo MÓVIL (uso del teléfono) — determinista y Render-safe

Objetivo:
- Proveer utilidades deterministas para:
  - Identificar contexto 'móvil' desde core/texto ya extraído.
  - Construir un cuerpo robusto (si se necesita plantilla determinista).
  - Validación estricta mínima (SVL-MOV-1) para evitar borradores genéricos.

Este módulo NO llama a OpenAI. Solo lógica de apoyo.

Principios:
- No inventar hechos.
- Exigir descripción circunstanciada / prueba objetiva.
- Diferenciar captación: AGENT vs AUTO vs UNKNOWN (si se dispone).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


MOVIL_TERMS = [
    "teléfono", "telefono", "teléfono móvil", "telefono movil", "móvil", "movil",
    "uso del teléfono", "uso del telefono", "utilizando manualmente", "sujetar", "sujetando",
]


def is_movil_context(core: Dict[str, Any], body: str = "") -> bool:
    try:
        t = str((core or {}).get("tipo_infraccion") or "").lower().strip()
        if t == "movil":
            return True
    except Exception:
        pass
    blob = (body or "").lower()
    # también miramos hecho_imputado / facts_phrases
    try:
        hecho = str((core or {}).get("hecho_imputado") or "").lower()
        blob = blob + "\n" + hecho
    except Exception:
        pass
    try:
        fp = (core or {}).get("facts_phrases") or []
        blob = blob + "\n" + "\n".join([str(x) for x in fp if x])
    except Exception:
        pass

    return any(k in blob for k in [x.lower() for x in MOVIL_TERMS])


def build_movil_template(core: Dict[str, Any], capture_mode: str = "UNKNOWN") -> Dict[str, str]:
    """Plantilla determinista (fallback) para móvil.
    No inventa prueba: exige descripción concreta o evidencia objetiva.
    """
    expediente = (core or {}).get("expediente_ref") or (core or {}).get("numero_expediente") or "No consta acreditado."
    organo = (core or {}).get("organo") or (core or {}).get("organismo") or "No consta acreditado."
    hecho = (core or {}).get("hecho_imputado") or "USO MANUAL DEL TELÉFONO MÓVIL."

    cm = (capture_mode or "UNKNOWN").upper()
    if cm == "AGENT":
        capt = (
            "Al tratarse, en su caso, de denuncia presencial, es imprescindible una descripción circunstanciada y verificable: "
            "posición del agente, distancia aproximada, visibilidad, maniobra observada, mano utilizada, duración de la supuesta conducta "
            "y circunstancias del tráfico. La fórmula estereotipada no permite contradicción efectiva."
        )
    elif cm == "AUTO":
        capt = (
            "Si se pretende sostener la imputación en medios técnicos, debe aportarse prueba objetiva completa (fotografía/vídeo/capturas) "
            "que permita identificar inequívocamente al conductor y la acción concreta (uso manual efectivo), sin recortes y con trazabilidad."
        )
    else:
        capt = (
            "Debe concretarse el medio de constatación (denuncia presencial o captación técnica) y aportarse prueba suficiente. "
            "En ausencia de prueba objetiva o descripción detallada, procede el archivo por insuficiencia probatoria."
        )

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}\n\n"
        "II. ALEGACIONES\n"
        "ALEGACIÓN PRIMERA — USO DEL MÓVIL: PRUEBA OBJETIVA O DESCRIPCIÓN CIRCUNSTANCIADA\n\n"
        "La imputación de uso del teléfono móvil exige acreditar de forma concreta el uso MANUAL efectivo y la conducta observada, "
        "no siendo suficiente una referencia genérica.\n\n"
        f"{capt}\n\n"
        "No consta aportada prueba objetiva ni una descripción suficientemente detallada de los hechos que permita valorar la conducta y "
        "ejercer contradicción efectiva.\n\n"
        "ALEGACIÓN SEGUNDA — MOTIVACIÓN INDIVIDUALIZADA Y PRESUNCIÓN DE INOCENCIA\n\n"
        "En Derecho sancionador, la carga de la prueba corresponde a la Administración. En ausencia de acreditación concreta del hecho y "
        "motivación individualizada, prevalece la presunción de inocencia y procede el ARCHIVO.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación suficiente del hecho.\n"
        "3) Subsidiariamente, que se practique prueba y se aporte expediente íntegro.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}


def strict_missing(body: str) -> List[str]:
    """SVL-MOV-1: mínimos para móvil."""
    b = (body or "").lower()
    missing: List[str] = []
    # Debe existir estructura de alegaciones
    if not re.search(r"^II\.\s*ALEGACIONES\b", body or "", re.IGNORECASE | re.MULTILINE):
        # aceptar si al menos hay ALEGACIÓN PRIMERA
        if not re.search(r"^ALEGACI[ÓO]N\s+PRIMERA\b", body or "", re.IGNORECASE | re.MULTILINE):
            missing.append("estructura_alegaciones")

    # debe mencionar móvil/teléfono
    if not any(k in b for k in ["móvil", "movil", "teléfono", "telefono"]):
        missing.append("referencia_movil")

    # debe exigir prueba o descripción
    if not any(k in b for k in ["prueba", "fotografía", "fotografia", "vídeo", "video", "descripción", "descripcion", "circunstanc"]):
        missing.append("exigir_prueba_o_descripcion")

    # debe pedir archivo en punto 2
    if "archivo" not in b or not re.search(r"^2\)\s*que\s+se\s+acuerde\s+el\s+archivo", body or "", re.IGNORECASE | re.MULTILINE):
        # tolerancia por variantes
        if "archivo del expediente" not in b and "acuerde el archivo" not in b:
            missing.append("solicito_archivo_punto2")

    return list(dict.fromkeys(missing))
