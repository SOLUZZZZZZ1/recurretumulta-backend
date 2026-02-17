import json
import os
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from database import get_engine
from openai import OpenAI

from ai.text_loader import load_text_from_b2
from ai.prompts.classify_documents import PROMPT as PROMPT_CLASSIFY
from ai.prompts.timeline_builder import PROMPT as PROMPT_TIMELINE
from ai.prompts.procedure_phase import PROMPT as PROMPT_PHASE
from ai.prompts.admissibility_guard import PROMPT as PROMPT_GUARD
from ai.prompts.draft_recurso_v2 import PROMPT as PROMPT_DRAFT
from ai.prompts.module_semaforo import module_semaforo

MAX_EXCERPT_CHARS = 12000

PROMPT_DRAFT_REPAIR_VELOCIDAD = """
Eres abogado especialista en sancionador (España). Debes REPARAR un borrador de recurso por EXCESO DE VELOCIDAD.

OBJETIVO: reescribir el borrador COMPLETO para que pase una validación estricta.

REGLAS OBLIGATORIAS:
1) La PRIMERA ALEGACIÓN NO puede ser 'Presunción de inocencia'.
2) La PRIMERA ALEGACIÓN debe titularse exactamente:
   'ALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y CADENA DE CUSTODIA (CINEMÓMETRO)'
3) El cuerpo debe contener literalmente la expresión: 'cadena de custodia'.
4) Debe incluir 'margen' y 'velocidad corregida'.
5) Debe exigir 'certificado' y 'verificación' (metrológica) del cinemómetro.
6) Debe exigir 'captura' o 'fotograma' completo.
7) El SOLICITO en velocidad debe pedir ARCHIVO (no "revisión").
8) No inventes hechos. Mantén prudencia: 'no consta acreditado', 'no se aporta'.

ENTRADA: JSON con borrador anterior y contexto.
SALIDA: SOLO JSON con la misma forma {asunto,cuerpo,variables_usadas,checks,notes_for_operator}.
"""


# ==========================
# VALIDACIÓN / FIXES
# ==========================

def _velocity_strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []

    if "cadena de custodia" not in b:
        missing.append("cadena_custodia")

    first = ""
    for line in (body or "").splitlines():
        l = (line or "").strip()
        if l.lower().startswith("alegación") or l.lower().startswith("alegacion"):
            first = l.lower()
            break

    if first and ("presunción" in first or "presuncion" in first or "inocencia" in first):
        missing.append("orden_alegaciones")

    required = {
        "margen": ["margen"],
        "velocidad_corregida": ["velocidad corregida", "corregida"],
        "metrologia": ["certificado", "verificación", "verificacion", "metrológ", "metrolog"],
        "cinemometro": ["cinemómetro", "cinemometro", "radar"],
        "captura": ["captura", "fotograma", "imagen"],
    }
    for key, needles in required.items():
        if not any(n in b for n in needles):
            missing.append(key)

    seen = set()
    out: List[str] = []
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _force_velocity_asunto(draft: Dict[str, Any]) -> None:
    """En VELOCIDAD el asunto no puede ser 'revisión'. Debe ser ARCHIVO."""
    try:
        draft["asunto"] = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    except Exception:
        pass


def _fix_solicito_format(body: str) -> str:
    """Arregla saltos de línea en SOLICITO para que no se pegue 2) y 3)."""
    if not body:
        return body
    body = re.sub(r"(III\.\s*SOLICITO)\s*(?=1[\)\.])", r"\1\n", body, flags=re.IGNORECASE)
    body = re.sub(r"(\n1[\)\.][^\n]*?)\s*2[\)\.]", r"\1\n2)", body)
    body = re.sub(r"(\n2\)[^\n]*?)\s*3[\)\.]", r"\1\n3)", body)
    body = re.sub(r"(\.\s*)3[\)\.]", r"\1\n3)", body)
    return body


def _force_archivo_in_speed_body(body: str) -> str:
    if not body:
        return body
    reps = [
        ("ALEGACIONES — SOLICITA REVISIÓN DEL EXPEDIENTE", "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"),
        ("ALEGACIONES — SOLICITA REVISIÓN", "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"),
        ("Que se acuerde la revisión del expediente", "Que se acuerde el ARCHIVO del expediente"),
        ("Que se acuerde la REVISIÓN del expediente", "Que se acuerde el ARCHIVO del expediente"),
        ("2) Que se acuerde la revisión del expediente", "2) Que se acuerde el ARCHIVO del expediente"),
        ("2) Que se acuerde la REVISIÓN del expediente", "2) Que se acuerde el ARCHIVO del expediente"),
    ]
    for a, b in reps:
        body = body.replace(a, b)
    return body


def _force_velocity_first_title(body: str) -> str:
    """Fuerza título fuerte en ALEGACIÓN PRIMERA para VELOCIDAD."""
    if not body:
        return body
    target = "ALEGACIÓN PRIMERA — PRUEBA TÉCNICA, METROLOGÍA Y CADENA DE CUSTODIA (CINEMÓMETRO)"
    body = re.sub(
        r"ALEGACIÓN\s+PRIMERA\s+—\s+INSUFICIENCIA\s+PROBATORIA\s+ESPECÍFICA\s+DEL\s+TIPO",
        target,
        body,
        flags=re.IGNORECASE,
    )
    body = re.sub(
        r"ALEGACIÓN\s+PRIMERA\s+—\s+INSUFICIENCIA\s+PROBATORIA\s+ESPECIFICA\s+DEL\s+TIPO",
        target,
        body,
        flags=re.IGNORECASE,
    )
    return body


def _remove_tipicity_intruder_in_speed(body: str) -> str:
    """
    Elimina el bloque intruso de tipicidad/subsunción que a veces mete el LLM al final en velocidad normal.
    """
    if not body:
        return body
    # borra desde "Se pone de manifiesto..." hasta antes de "III. SOLICITO" si aparece
    body = re.sub(
        r"Se pone de manifiesto[\s\S]*?(?=\nIII\.\s*SOLICITO)",
        "",
        body,
        flags=re.IGNORECASE,
    )
    # borra colas típicas
    body = re.sub(r"[^\n]*subsunción típica[\s\S]*$", "", body, flags=re.IGNORECASE)
    body = re.sub(r"[^\n]*tipicidad[\s\S]*$", "", body, flags=re.IGNORECASE)
    body = re.sub(r"[^\n]*artículo\s+300[\s\S]*$", "", body, flags=re.IGNORECASE)
    return body


def _ensure_speed_antecedentes(body: str, velocity_calc: Dict[str, Any]) -> str:
    try:
        if not body or not (velocity_calc or {}).get("ok"):
            return body
        measured = velocity_calc.get("measured")
        if not isinstance(measured, int):
            return body
        body = re.sub(
            r"(Hecho imputado:\s*EXCESO DE VELOCIDAD)\s*(?:\([^)]+\))?\s*\.",
            rf"\1 ({measured} km/h).",
            body,
            flags=re.IGNORECASE,
        )
        return body
    except Exception:
        return body


def _ensure_velocity_calc_paragraph(body: str, velocity_calc: Dict[str, Any]) -> str:
    """Inserta el párrafo de cálculo si velocity_calc.ok y aún no existe."""
    try
