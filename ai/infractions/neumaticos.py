"""
RTM — TRANSPORTE — NEUMÁTICOS (TRP-NEU-1)
"""

from __future__ import annotations
from typing import Any, Dict, List


def is_neumaticos_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    if str(core.get("tipo_infraccion") or "").lower().strip() == "neumaticos":
        return True
    blob = (body or "").lower() + "\n" + str(core.get("hecho_imputado") or "").lower()
    signals = ["neumaticos", "neumáticos", "desgaste", "profundidad del dibujo", "cubierta"]
    return any(s in blob for s in signals)


def build_neumaticos_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "DEFICIENCIAS EN NEUMÁTICOS DEL VEHÍCULO PESADO."

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"
    cuerpo = (
        "A la atención del órgano competente.\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — NECESIDAD DE DESCRIPCIÓN TÉCNICA DEL DEFECTO\n\n"
        "La imputación por deficiencias en neumáticos exige indicar de forma concreta qué neumático presentaba el defecto, "
        "qué magnitud tenía y cómo se constató técnicamente.\n\n"
        "ALEGACIÓN SEGUNDA — PRUEBA OBJETIVA Y MOTIVACIÓN\n\n"
        "Sin medición, soporte gráfico o descripción individualizada, la imputación queda insuficientemente motivada.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y detalle técnico del defecto imputado.\n"
    ).strip()
    return {"asunto": asunto, "cuerpo": cuerpo}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing = []
    if "defecto" not in b and "tecnic" not in b:
        missing.append("descripcion_tecnica")
    if "archivo" not in b:
        missing.append("archivo")
    return list(dict.fromkeys(missing))
