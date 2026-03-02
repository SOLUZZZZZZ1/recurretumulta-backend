"""RTM — SEGURO OBLIGATORIO (SVL-SEG-2) — DEMOLEDOR 9.5/10 (Modo B; C automático en graves)

Determinista, sin OpenAI.
Compatibilidad: is_seguro_context(), build_seguro_strong_template(), strict_missing()
"""

from __future__ import annotations

import json
import re
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
    if fine is not None and fine >= 1000:
        return True
    if pts and pts > 0:
        return True
    g = str(core.get("gravedad") or "").lower().strip()
    return g in ("grave", "muy grave", "critico", "crítico")


def is_seguro_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    blob_body = (body or "").lower()
    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo == "seguro":
        return True

    norma_hint = str(core.get("norma_hint") or "").upper()
    precepts = core.get("preceptos_detectados") or []
    pre_blob = " ".join([str(p) for p in precepts]).upper()

    if "8/2004" in norma_hint or "RDL 8/2004" in norma_hint:
        return True
    if "8/2004" in pre_blob or "RDL 8/2004" in pre_blob or "LSOA" in pre_blob:
        return True

    try:
        core_blob = json.dumps(core, ensure_ascii=False).lower()
    except Exception:
        core_blob = ""

    blob = core_blob + "\n" + blob_body
    signals = [
        "seguro obligatorio", "sin seguro", "carecer de seguro",
        "vehículo no asegurado", "vehiculo no asegurado",
        "fiva", "fichero informativo de vehículos asegurados", "fichero informativo de vehiculos asegurados",
        "póliza", "poliza", "aseguradora", "responsabilidad civil",
        "certificación negativa", "certificacion negativa",
        "lsoa", "8/2004",
    ]
    return any(s in blob for s in signals)


def build_seguro_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    modo_c = _is_grave(core)

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "CARENCIA DE SEGURO OBLIGATORIO / VEHÍCULO PRESUNTAMENTE NO ASEGURADO."

    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    hora_hecho = core.get("hora_infraccion") or core.get("hora_hecho") or ""
    fecha_line = ""
    if isinstance(fecha_hecho, str) and fecha_hecho.strip():
        fecha_line = f" (fecha indicada: {fecha_hecho}"
        if isinstance(hora_hecho, str) and hora_hecho.strip():
            fecha_line += f" — hora: {hora_hecho}"
        fecha_line += ")"

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — SEGURO OBLIGATORIO: PRUEBA PLENA EN FECHA/HORA EXACTAS (FIVA/CERTIFICACIÓN NEGATIVA)\n\n"
        "Para sancionar por carencia de seguro obligatorio no basta una mención genérica a bases de datos. "
        "Debe acreditarse de forma plena y verificable la inexistencia de póliza en la fecha y hora exactas del hecho imputado, "
        "con identificación inequívoca del vehículo y soporte documental suficiente.\n\n"
        "No consta acreditado en el expediente:\n"
        "1) Fecha y hora exactas del hecho imputado y medio de constatación (acta/denuncia/sistema).\n"
        "2) Consulta FIVA referida a esa fecha/hora concreta, con resultado y trazabilidad.\n"
        "3) Certificación negativa o documento equivalente que acredite inexistencia de cobertura a la fecha/hora del hecho.\n"
        "4) Identificación completa del vehículo (matrícula y, en su caso, bastidor) y correspondencia inequívoca con la consulta.\n"
        "5) Motivación individualizada del encaje típico y de la graduación de la sanción.\n\n"
        "En ausencia de acreditación técnica y documental, procede el ARCHIVO por insuficiencia probatoria.\n\n"
        "ALEGACIÓN SEGUNDA — PROCEDIMIENTO, NOTIFICACIÓN Y CONTRADICCIÓN EFECTIVA\n\n"
        "Se solicita expediente íntegro (denuncia/boletín, diligencias, consultas y soportes) e identificación expresa del precepto aplicado "
        "con motivación completa. La falta de documentación verificable impide contradicción efectiva y genera indefensión.\n"
    )

    if modo_c:
        cuerpo += (
            "\nALEGACIÓN ADICIONAL (MODO C — GRAVEDAD): LEGALIDAD, TIPICIDAD Y PRESUNCIÓN DE INOCENCIA\n\n"
            "En sanciones de elevada cuantía o especial gravedad, la exigencia de prueba plena y motivación es máxima. "
            "Si no se acredita de forma verificable la inexistencia de póliza en la fecha y hora exactas, "
            "no se enerva la presunción de inocencia (art. 24 CE) ni se satisface la legalidad/tipicidad estricta (art. 25 CE). "
            "La ausencia de motivación suficiente determina la anulabilidad/nulidad del acto sancionador.\n"
        )

    cuerpo += (
        "\nIII. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación plena de la inexistencia de póliza.\n"
        "3) Subsidiariamente, que se practique prueba y se aporte el expediente íntegro con los soportes solicitados (FIVA/certificación negativa).\n"
    )

    return {"asunto": asunto, "cuerpo": cuerpo.strip()}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []
    if "fiva" not in b:
        missing.append("fiva")
    if "certific" not in b:
        missing.append("certificacion")
    if "fecha" not in b:
        missing.append("fecha")
    if "archivo" not in b:
        missing.append("archivo")
    out=[]
    seen=set()
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
