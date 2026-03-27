"""
RTM — TRANSPORTE — DOCUMENTACIÓN (TRP-DOC-1)
"""

from __future__ import annotations
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
    if fine is not None and fine >= 1000:
        return True
    g = str(core.get("gravedad") or "").lower().strip()
    return g in ("grave", "muy grave", "critico", "crítico")


def is_documentacion_transporte_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo == "documentacion_transporte":
        return True
    blob = (body or "").lower() + "\n" + str(core.get("hecho_imputado") or "").lower()
    signals = [
        "documentacion de transporte", "documentación de transporte",
        "carece de documentacion", "carece de documentación",
        "sin documentacion", "sin documentación",
        "carta de porte", "documento de control",
        "permiso comunitario", "licencia comunitaria"
    ]
    return any(s in blob for s in signals)


def build_documentacion_transporte_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "INCUMPLIMIENTO DOCUMENTAL EN TRANSPORTE PROFESIONAL."
    fecha = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha})" if isinstance(fecha, str) and fecha.strip() else ""
    modo_c = _is_grave(core)

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = (
        "A la atención del órgano competente.\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — FALTA DE CONCRECIÓN DEL DOCUMENTO PRESUNTAMENTE AUSENTE O IRREGULAR\n\n"
        "La imputación debe concretar con exactitud qué documento era exigible en ese transporte concreto, por qué razón y cuál era la obligación normativa aplicable. "
        "No basta una fórmula genérica sobre ausencia de documentación si no se identifica de modo expreso el documento exigido.\n\n"
        "ALEGACIÓN SEGUNDA — NECESIDAD DE ACREDITAR EL REQUERIMIENTO Y LA IMPOSIBILIDAD REAL DE EXHIBICIÓN\n\n"
        "Debe constar:\n"
        "• Qué documento se solicitó exactamente.\n"
        "• En qué momento y en qué condiciones se requirió su exhibición.\n"
        "• Si se concedió posibilidad real de aportación o comprobación.\n"
        "• Si el documento existía pero no pudo mostrarse en ese instante por causa material o formal.\n\n"
        "ALEGACIÓN TERCERA — EXPEDIENTE ÍNTEGRO Y MOTIVACIÓN\n\n"
        "Se solicita expediente íntegro, con acta completa, identificación precisa del documento exigido y motivación individualizada del encaje sancionador.\n"
    )

    if modo_c:
        cuerpo += (
            "\nALEGACIÓN ADICIONAL (GRAVEDAD) — EXIGENCIA REFORZADA DE TIPICIDAD\n\n"
            "Cuando la sanción reviste especial entidad, la tipicidad debe venir descrita con máxima precisión, identificando norma, documento y obligación concreta.\n"
        )

    cuerpo += (
        "\nIII. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de concreción del documento exigible.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y motivación completa del requerimiento documental realizado.\n"
    )

    return {"asunto": asunto, "cuerpo": cuerpo.strip()}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []
    if "documento" not in b:
        missing.append("documento_concreto")
    if "requer" not in b:
        missing.append("requerimiento")
    if "archivo" not in b:
        missing.append("archivo")
    seen = set()
    out: List[str] = []
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
