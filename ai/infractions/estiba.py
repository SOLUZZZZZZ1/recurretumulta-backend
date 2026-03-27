"""
RTM — TRANSPORTE — ESTIBA / SUJECIÓN DE CARGA (TRP-EST-1)
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


def is_estiba_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo == "estiba":
        return True
    blob = (body or "").lower() + "\n" + str(core.get("hecho_imputado") or "").lower()
    signals = [
        "estiba", "carga mal sujeta", "carga mal asegurada", "sujecion de carga",
        "sujeción de carga", "amarre", "trincaje", "carga desplazada", "mercancia mal estibada",
        "mercancía mal estibada"
    ]
    return any(s in blob for s in signals)


def build_estiba_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "ESTIBA O SUJECIÓN INCORRECTA DE LA CARGA."
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
        "ALEGACIÓN PRIMERA — NECESIDAD DE DESCRIPCIÓN TÉCNICA CONCRETA DE LA DEFICIENCIA DE ESTIBA\n\n"
        "No basta una referencia genérica a una supuesta mala sujeción de la carga. Debe constar de forma precisa:\n"
        "• Qué parte de la carga se consideró incorrectamente estibada.\n"
        "• Qué elementos de sujeción existían y por qué se reputan insuficientes.\n"
        "• Si hubo desplazamiento, riesgo objetivo o afectación real a la seguridad vial.\n"
        "• Medio de constatación y soporte verificable (fotografías, croquis o vídeo).\n\n"
        "ALEGACIÓN SEGUNDA — FALTA DE SOPORTE OBJETIVO Y DE MOTIVACIÓN INDIVIDUALIZADA\n\n"
        "Sin soporte gráfico o descripción técnica individualizada, la imputación queda en una afirmación genérica y no permite "
        "comprobar la existencia real de la infracción.\n\n"
        "ALEGACIÓN TERCERA — EXPEDIENTE ÍNTEGRO\n\n"
        "Se solicita expediente íntegro, con fotografías, acta completa, identificación del agente o inspector y motivación concreta del riesgo imputado.\n"
    )

    if modo_c:
        cuerpo += (
            "\nALEGACIÓN ADICIONAL (GRAVEDAD) — EXIGENCIA REFORZADA DE PRUEBA\n\n"
            "Cuando la sanción es especialmente grave, la exigencia de prueba objetiva y de motivación técnica individualizada debe ser máxima.\n"
        )

    cuerpo += (
        "\nIII. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y soporte objetivo completo de la supuesta deficiencia de estiba.\n"
    )

    return {"asunto": asunto, "cuerpo": cuerpo.strip()}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []
    if "soporte" not in b and "fotograf" not in b and "video" not in b:
        missing.append("soporte_objetivo")
    if "descripcion" not in b and "descripción" not in b:
        missing.append("descripcion_tecnica")
    if "archivo" not in b:
        missing.append("archivo")
    seen = set()
    out: List[str] = []
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
