"""
RTM — TRANSPORTE — PESO / SOBRECARGA (TRP-PES-1)

Determinista, sin OpenAI.
Objetivo: alegaciones sólidas para exceso de peso, sobrepeso, sobrecarga,
masa máxima autorizada o pesaje de vehículo pesado.
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
    pts = _get_int(core.get("puntos_detraccion") or core.get("puntos") or 0) or 0
    if fine is not None and fine >= 1000:
        return True
    if pts and pts > 0:
        return True
    g = str(core.get("gravedad") or "").lower().strip()
    return g in ("grave", "muy grave", "critico", "crítico")


def is_peso_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo == "peso":
        return True
    blob = (body or "").lower() + "\n" + str(core.get("hecho_imputado") or "").lower()
    signals = [
        "exceso de peso", "sobrecarga", "sobrepeso", "masa maxima", "masa máxima",
        "mma", "peso por eje", "pesaje", "bascula", "báscula", "vehiculo pesado", "vehículo pesado"
    ]
    return any(s in blob for s in signals)


def build_peso_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "EXCESO DE PESO O SOBRECARGA EN TRANSPORTE PROFESIONAL."
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
        "ALEGACIÓN PRIMERA — NECESIDAD DE PESAJE VÁLIDO, TRAZABLE Y REFERIDO AL VEHÍCULO CONCRETO\n\n"
        "La imputación por exceso de peso o sobrecarga exige acreditación técnica completa del pesaje. "
        "No basta una referencia genérica al supuesto sobrepeso si no consta:\n"
        "• Sistema de pesaje utilizado y su identificación.\n"
        "• Fecha, hora y lugar exactos del control.\n"
        "• Identificación inequívoca del vehículo y, en su caso, del conjunto tractor/semirremolque.\n"
        "• Resultado bruto del pesaje y criterio seguido para el cálculo del exceso.\n"
        "• Referencia a la MMA o al límite legal aplicable en el caso concreto.\n\n"
        "ALEGACIÓN SEGUNDA — AUSENCIA DE MOTIVACIÓN SUFICIENTE DEL EXCESO IMPUTADO\n\n"
        "Debe explicarse con claridad cuál era el límite aplicable, qué resultado arrojó el pesaje y cuál es el exceso exacto imputado. "
        "Sin esa operación comparativa, la imputación carece de precisión bastante.\n\n"
        "ALEGACIÓN TERCERA — EXPEDIENTE ÍNTEGRO Y CONTRADICCIÓN EFECTIVA\n\n"
        "Se solicita la aportación íntegra del expediente, incluyendo ticket o acta de pesaje, identificación del equipo, "
        "documentación técnica y motivación individualizada del encaje típico.\n"
    )

    if modo_c:
        cuerpo += (
            "\nALEGACIÓN ADICIONAL (GRAVEDAD) — EXIGENCIA REFORZADA DE PRUEBA TÉCNICA\n\n"
            "Tratándose de sanción de entidad relevante, la exigencia de prueba técnica verificable y motivación reforzada es máxima. "
            "En ausencia de soporte completo del pesaje, procede el archivo.\n"
        )

    cuerpo += (
        "\nIII. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación técnica bastante.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro, ticket/acta de pesaje y motivación individualizada del exceso imputado.\n"
    )

    return {"asunto": asunto, "cuerpo": cuerpo.strip()}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []
    if "pesaje" not in b:
        missing.append("pesaje")
    if "mma" not in b and "masa maxima" not in b and "masa máxima" not in b and "limite aplicable" not in b:
        missing.append("limite_aplicable")
    if "archivo" not in b:
        missing.append("archivo")
    seen = set()
    out: List[str] = []
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
