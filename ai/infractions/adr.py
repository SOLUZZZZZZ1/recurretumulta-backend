"""
RTM — TRANSPORTE — ADR / MERCANCÍAS PELIGROSAS (TRP-ADR-1)
"""

from __future__ import annotations
from typing import Any, Dict, List


def is_adr_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    if str(core.get("tipo_infraccion") or "").lower().strip() == "adr":
        return True
    blob = (body or "").lower() + "\n" + str(core.get("hecho_imputado") or "").lower()
    signals = ["adr", "mercancias peligrosas", "mercancías peligrosas", "panel naranja"]
    return any(s in blob for s in signals)


def build_adr_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "INCUMPLIMIENTO ADR / MERCANCÍAS PELIGROSAS."

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = (
        "A la atención del órgano competente.\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — NECESIDAD DE IDENTIFICACIÓN PRECISA DE LA OBLIGACIÓN ADR INCUMPLIDA\n\n"
        "Debe identificarse con exactitud qué obligación ADR se considera incumplida, cuál era la materia transportada y qué requisito concreto resultaba exigible.\n\n"
        "ALEGACIÓN SEGUNDA — FALTA DE SOPORTE Y MOTIVACIÓN SUFICIENTE\n\n"
        "Sin descripción individualizada del incumplimiento y sin soporte objetivo bastante, no puede sostenerse válidamente la imputación.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro, identificación de la mercancía y concreción normativa ADR aplicada.\n"
    ).strip()
    return {"asunto": asunto, "cuerpo": cuerpo}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing = []
    if "adr" not in b:
        missing.append("adr")
    if "archivo" not in b:
        missing.append("archivo")
    return list(dict.fromkeys(missing))
