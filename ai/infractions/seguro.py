# ai/infractions/traffic/seguro.py
"""
RTM — TRÁFICO — SEGURO (SVL-SEG-1)

Determinista, sin OpenAI.
Objetivo: alegaciones quirúrgicas para presunta carencia de seguro obligatorio (RDL 8/2004 / LSOA / FIVA).

- No inventa hechos: usa lenguaje prudente ("no consta acreditado", "no se aporta").
- Exige prueba plena de inexistencia de póliza en la fecha exacta.
- Solicita expediente íntegro y soportes (consulta FIVA / certificación negativa).
- Solicita ARCHIVO.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List


def is_seguro_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    blob_body = (body or "").lower()

    # Señal directa por tipo
    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo == "seguro":
        return True

    # Norma / preceptos
    norma_hint = str(core.get("norma_hint") or "").upper()
    precepts = core.get("preceptos_detectados") or []
    pre_blob = " ".join([str(p) for p in precepts]).upper()

    if "8/2004" in norma_hint or "RDL 8/2004" in norma_hint:
        return True
    if "8/2004" in pre_blob or "RDL 8/2004" in pre_blob or "LSOA" in pre_blob:
        return True

    # Señales semánticas (core + body)
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
    ]
    return any(s in blob for s in signals)


def build_seguro_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "CARENCIA DE SEGURO OBLIGATORIO / VEHÍCULO PRESUNTAMENTE NO ASEGURADO."

    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — SEGURO OBLIGATORIO: INEXISTENCIA DE PÓLIZA NO ACREDITADA (CARGA PROBATORIA)\n\n"
        "En materia sancionadora, corresponde a la Administración la carga probatoria de los hechos constitutivos de la infracción. "
        "Para sancionar por carencia de seguro obligatorio no basta una mención genérica o una referencia indeterminada a bases de datos: "
        "debe acreditarse de forma plena y verificable la inexistencia de póliza en la fecha exacta del hecho imputado, "
        "con identificación inequívoca del vehículo y soporte documental suficiente.\n\n"
        "No consta acreditado en el expediente, de forma verificable:\n"
        "1) La fecha y hora exactas del hecho imputado y el medio de constatación (acta/denuncia/sistema), con identificación completa.\n"
        "2) La consulta al FIVA (Fichero Informativo de Vehículos Asegurados) referida a esa fecha concreta, con resultado y trazabilidad.\n"
        "3) Certificación negativa o documento equivalente que acredite, sin margen de duda, la inexistencia de cobertura a la fecha del hecho.\n"
        "4) La motivación individualizada del encaje típico (artículo/apartado aplicado) y la graduación de la sanción.\n\n"
        "En ausencia de dicha acreditación técnica y documental, no puede tenerse por probada la infracción, "
        "procediendo el ARCHIVO del expediente por insuficiencia probatoria.\n\n"
        "ALEGACIÓN SEGUNDA — EXPEDIENTE ÍNTEGRO, MOTIVACIÓN Y CONTRADICCIÓN EFECTIVA\n\n"
        "Se interesa la aportación del expediente íntegro (denuncia/boletín, diligencias, consultas y soportes documentales), "
        "así como la identificación expresa del precepto aplicado (artículo y apartado) y la motivación completa que permita ejercer "
        "contradicción efectiva.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación plena de la inexistencia de póliza.\n"
        "3) Subsidiariamente, que se practique prueba y se aporte el expediente íntegro con los soportes documentales solicitados.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []

    if not any(x in b for x in ["inexistencia de póliza", "inexistencia de poliza", "vehículo no asegurado", "vehiculo no asegurado", "carencia de seguro"]):
        missing.append("inexistencia_poliza")
    if not any(x in b for x in ["carga probatoria", "carga de la prueba", "corresponde a la administración"]):
        missing.append("carga_probatoria")
    if "fiva" not in b and "fichero informativo" not in b:
        missing.append("fiva")
    if not any(x in b for x in ["fecha y hora exact", "fecha exacta", "fecha indicada"]):
        missing.append("fecha_exacta_hecho")

    ok_archivo = bool(
        re.search(r"^2\)\s*que\s+se\s+acuerde\s+el\s+archivo\b", body or "", flags=re.IGNORECASE | re.MULTILINE)
    )
    if not ok_archivo:
        if "archivo del expediente" not in b and "acuerde el archivo" not in b:
            missing.append("solicito_archivo_punto2")

    # unique
    seen = set()
    out: List[str] = []
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out