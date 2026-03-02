"""
RTM — SEGURO OBLIGATORIO (SVL-SEG-3) — ENFOQUE OPERATIVO (Maximiza archivo real)

Estrategia:
- Generar DUDA OPERATIVA razonable.
- Forzar revisión de FIVA / certificación negativa.
- Atacar fecha y hora exactas.
- Exigir trazabilidad de consulta.
- Introducir posibles causas habituales de error administrativo.

Modo B por defecto.
Modo C solo si sanción elevada (>=1000€) o gravedad marcada.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
import json
import re


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# Context detection
# ---------------------------------------------------------

def is_seguro_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo == "seguro":
        return True

    norma_hint = str(core.get("norma_hint") or "").upper()
    if "8/2004" in norma_hint or "RDL 8/2004" in norma_hint or "LSOA" in norma_hint:
        return True

    blob = ""
    try:
        blob = json.dumps(core, ensure_ascii=False).lower()
    except Exception:
        pass

    blob += "\n" + (body or "").lower()

    signals = [
        "seguro obligatorio",
        "sin seguro",
        "carecer de seguro",
        "vehículo no asegurado",
        "vehiculo no asegurado",
        "fiva",
        "fichero informativo",
        "responsabilidad civil",
        "8/2004",
        "lsoa"
    ]

    return any(s in blob for s in signals)


# ---------------------------------------------------------
# Template operativo (maximiza revisión)
# ---------------------------------------------------------

def build_seguro_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    modo_c = _is_grave(core)

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "CARENCIA DE SEGURO OBLIGATORIO."

    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    hora_hecho = core.get("hora_infraccion") or core.get("hora_hecho") or ""

    fecha_line = ""
    if fecha_hecho:
        fecha_line = f" (fecha indicada: {fecha_hecho}"
        if hora_hecho:
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

        "ALEGACIÓN PRIMERA — VERIFICACIÓN EFECTIVA DE INEXISTENCIA DE PÓLIZA EN FECHA Y HORA EXACTAS\n\n"

        "Para sancionar por carencia de seguro obligatorio no basta una referencia genérica a bases de datos. "
        "Es imprescindible acreditar de forma concreta y verificable que, en la FECHA Y HORA exactas del hecho, "
        "no existía póliza en vigor que cubriera el vehículo denunciado.\n\n"

        "No consta acreditado en el expediente:\n"
        "1) Que la consulta FIVA se realizara referida exactamente a la fecha y hora del hecho.\n"
        "2) Trazabilidad técnica de la consulta (fecha real de consulta, sistema utilizado, operador, registro).\n"
        "3) Certificación negativa emitida con referencia temporal concreta.\n"
        "4) Que se descartara error de matrícula, error de titularidad o discrepancia de datos identificativos.\n\n"

        "La mera ausencia de datos en una consulta administrativa no equivale automáticamente a inexistencia de cobertura, "
        "especialmente si no consta certificación negativa específica referida al momento exacto del hecho.\n\n"

        "ALEGACIÓN SEGUNDA — POSIBLES INCIDENCIAS EN SISTEMAS DE INFORMACIÓN\n\n"

        "Es notorio que los sistemas de información pueden verse afectados por:\n"
        "• Retrasos en actualización por parte de entidades aseguradoras.\n"
        "• Errores de transmisión de datos.\n"
        "• Modificaciones recientes de póliza no volcadas aún en el sistema.\n"
        "• Errores materiales en matrícula (0/O, 8/B, etc.).\n\n"

        "En ausencia de comprobación directa con la entidad aseguradora correspondiente o certificación negativa inequívoca, "
        "no puede afirmarse con certeza la inexistencia de seguro en el momento del hecho.\n\n"

        "ALEGACIÓN TERCERA — NECESIDAD DE EXPEDIENTE ÍNTEGRO Y VERIFICACIÓN COMPLETA\n\n"

        "Se interesa la aportación del expediente íntegro, incluyendo:\n"
        "• Documento o registro completo de la consulta realizada.\n"
        "• Identificación del sistema empleado.\n"
        "• Resultado íntegro de la consulta con referencia temporal.\n"
        "• Motivación individualizada del encaje típico.\n\n"
    )

    if modo_c:
        cuerpo += (
            "ALEGACIÓN ADICIONAL (GRAVEDAD) — EXIGENCIA REFORZADA DE PRUEBA Y MOTIVACIÓN\n\n"
            "Tratándose de sanción de elevada cuantía, la exigencia de prueba plena y motivación individualizada es máxima. "
            "En ausencia de acreditación técnica inequívoca de inexistencia de póliza en la fecha y hora exactas, "
            "no se enerva la presunción de inocencia ni se satisface la legalidad sancionadora.\n\n"
        )

    cuerpo += (
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación plena de inexistencia de póliza.\n"
        "3) Subsidiariamente, que se practique verificación expresa ante la entidad aseguradora correspondiente "
        "y se aporte certificación negativa referida a la fecha y hora exactas del hecho.\n"
    )

    return {"asunto": asunto, "cuerpo": cuerpo.strip()}


# ---------------------------------------------------------
# Strict check
# ---------------------------------------------------------

def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []

    if "fecha y hora" not in b:
        missing.append("fecha_hora_exactas")
    if "fiva" not in b:
        missing.append("fiva")
    if "archivo" not in b:
        missing.append("archivo")

    out = []
    seen = set()
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out