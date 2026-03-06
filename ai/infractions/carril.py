"""
RTM — TRÁFICO — POSICIÓN EN LA VÍA / CARRIL (ART. 31 RGC)
Determinista, sin IA.
Salida: {"asunto","cuerpo"}
"""

from __future__ import annotations
from typing import Any, Dict, List
import re


def _blob(core: Dict[str, Any], body: str = "") -> str:
    parts: List[str] = []
    for k in ("raw_text_pdf", "raw_text_vision", "raw_text_blob", "hecho_imputado"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    if body:
        parts.append(body)
    return " ".join(parts).lower()


def is_carril_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    b = _blob(core, body=body)

    # Si el extractor ya detectó el artículo 31, es carril.
    art = core.get("articulo_infringido_num")
    try:
        art_i = int(art) if art is not None else None
    except Exception:
        art_i = None
    if art_i == 31:
        return True

    # Señales típicas de Art. 31 (posición en la vía)
    signals = [
        "artículo 31", "articulo 31", "art. 31",
        "circular fuera de poblado",
        "carril distinto del situado más a la derecha",
        "carril distinto del situado mas a la derecha",
        "calzada con más de un carril", "calzada con mas de un carril",
        "sentido de la marcha",
    ]
    return any(s in b for s in signals)


def build_carril_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "POSICIÓN EN LA VÍA / USO INDEBIDO DEL CARRIL (ART. 31 RGC)."

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = "\n".join([
        "A la atención del órgano competente,",
        "",
        "I. ANTECEDENTES",
        f"1) Órgano: {organo}",
        f"2) Identificación expediente: {expediente}",
        f"3) Hecho imputado: {hecho}",
        "",
        "II. ALEGACIONES",
        "",
        "ALEGACIÓN PRIMERA — TIPICIDAD Y DESCRIPCIÓN CIRCUNSTANCIADA (ART. 31 RGC)",
        "",
        "La imputación por circular fuera de poblado por un carril distinto del situado más a la derecha exige una descripción concreta y verificable.",
        "No basta una fórmula genérica. Debe precisarse, como mínimo:",
        "1) Número de carriles existentes y configuración real del tramo.",
        "2) Carril exacto por el que circulaba el vehículo y durante cuánto tiempo.",
        "3) Circunstancias del tráfico (densidad, adelantamientos, incorporaciones, seguridad).",
        "4) Existencia de circunstancias de la vía o del tráfico que aconsejaran o justificaran la posición adoptada.",
        "",
        "ALEGACIÓN SEGUNDA — CARGA PROBATORIA Y MOTIVACIÓN",
        "",
        "Corresponde a la Administración acreditar los hechos y motivar la subsunción en la norma aplicada.",
        "Sin concreción fáctica suficiente, no puede enervarse la presunción de inocencia.",
        "",
        "III. SOLICITO",
        "1) Que se tengan por formuladas las presentes alegaciones.",
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de motivación concreta.",
        "3) Subsidiariamente, que se aporte expediente íntegro (denuncia completa, informe, soportes) para contradicción efectiva.",
    ]).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}