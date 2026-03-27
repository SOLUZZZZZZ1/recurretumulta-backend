"""
RTM — TRANSPORTE — LIMITADOR DE VELOCIDAD (TRP-LIM-1)
"""

from __future__ import annotations
from typing import Any, Dict, List


def is_limitador_velocidad_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    if str(core.get("tipo_infraccion") or "").lower().strip() == "limitador_velocidad":
        return True
    blob = (body or "").lower() + "\n" + str(core.get("hecho_imputado") or "").lower()
    return "limitador de velocidad" in blob


def build_limitador_velocidad_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "INCUMPLIMIENTO RELATIVO AL LIMITADOR DE VELOCIDAD."

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = (
        "A la atención del órgano competente.\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — NECESIDAD DE PRUEBA TÉCNICA COMPLETA\n\n"
        "La imputación relativa al limitador de velocidad exige acreditación técnica específica del sistema, del defecto observado y del método de comprobación utilizado.\n\n"
        "ALEGACIÓN SEGUNDA — FALTA DE MOTIVACIÓN INDIVIDUALIZADA\n\n"
        "No basta una referencia genérica al mal funcionamiento del limitador si no consta informe, lectura técnica o verificación bastante.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro e informe técnico del limitador.\n"
    ).strip()
    return {"asunto": asunto, "cuerpo": cuerpo}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing = []
    if "tecnic" not in b:
        missing.append("prueba_tecnica")
    if "archivo" not in b:
        missing.append("archivo")
    return list(dict.fromkeys(missing))
