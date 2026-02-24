# ai/infractions/traffic/itv.py
"""
RTM — TRÁFICO — ITV (SVL-ITV-1)

Determinista, sin OpenAI.
Objetivo: alegaciones quirúrgicas para infracción ITV (inspección técnica).

- No inventa hechos: prudente ("no consta acreditado", "se solicita").
- Exige determinación de fechas: caducidad ITV vs fecha/hora del hecho.
- Exige prueba de circulación efectiva (no mero estacionamiento) cuando proceda.
- Exige identificación del medio de constatación (agente/sistema) y expediente íntegro.
- Solicita ARCHIVO.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List


def is_itv_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    b = (body or "").lower()

    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo == "itv":
        return True

    hecho = str(core.get("hecho_imputado") or "").lower()

    try:
        core_blob = json.dumps(core, ensure_ascii=False).lower()
    except Exception:
        core_blob = ""

    blob = core_blob + "\n" + hecho + "\n" + b
    signals = [
        "itv", "inspección técnica", "inspeccion tecnica", "inspección técnica de vehículos", "inspeccion tecnica de vehiculos",
        "caducad", "inspección caducada", "inspeccion caducada",
        "no tener itv", "carecer de itv",
    ]
    return any(s in blob for s in signals)


def build_itv_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "PRESUNTA ITV NO VIGENTE / INSPECCIÓN TÉCNICA CADUCADA."

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
        "ALEGACIÓN PRIMERA — ITV: HECHO OBJETIVO, PRUEBA Y DETERMINACIÓN DE FECHAS\n\n"
        "La imputación por ITV no vigente exige determinación objetiva y verificable de fechas y circunstancias. "
        "No basta una referencia genérica: debe acreditarse (i) la fecha exacta de caducidad de la ITV y su fuente documental, "
        "(ii) la fecha y hora exactas del hecho imputado, y (iii) el medio de constatación.\n\n"
        "No consta acreditado en el expediente, de forma completa:\n"
        "1) Fecha exacta de caducidad de la ITV y fuente documental (registro/consulta oficial) que lo respalde.\n"
        "2) Fecha y hora exactas del hecho imputado, con identificación inequívoca del vehículo.\n"
        "3) Medio de constatación (observación presencial vs sistema), con acta/denuncia íntegra y motivación individualizada.\n"
        "4) En su caso, prueba suficiente de circulación efectiva en ese instante, evitando sanciones basadas en meras presunciones.\n"
        "5) Identificación expresa del precepto aplicado (artículo/apartado) y motivación de la graduación.\n\n"
        "En ausencia de determinación clara de fechas y prueba suficiente del hecho imputado, procede el ARCHIVO por insuficiencia probatoria.\n\n"
        "ALEGACIÓN SEGUNDA — EXPEDIENTE ÍNTEGRO Y CONTRADICCIÓN EFECTIVA\n\n"
        "Se solicita la aportación del expediente íntegro (boletín/denuncia, consultas/soportes, diligencias, propuesta y resolución si existieran), "
        "con indicación expresa del precepto aplicado y motivación completa.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de determinación objetiva de fechas y hechos.\n"
        "3) Subsidiariamente, que se practique prueba y se aporte expediente íntegro con los soportes documentales solicitados.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []

    if "caducidad" not in b and "fecha exacta" not in b:
        missing.append("caducidad_y_fechas")
    if not any(k in b for k in ["medio de constatación", "medio de constatacion", "acta/denuncia", "boletín", "boletin"]):
        missing.append("medio_constatacion")
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