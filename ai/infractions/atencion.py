
"""
RTM — ATENCIÓN / CONDUCCIÓN NEGLIGENTE (ART. 3.1 / 18.1 RGC)
VERSIÓN DEMOLEDORA 9.5/10 — ENFOQUE OPERATIVO (MAXIMIZA ARCHIVO REAL)
"""

from __future__ import annotations
from typing import Any, Dict, List


def is_atencion_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    blob = (body or "").lower()
    hecho = str(core.get("hecho_imputado") or "").lower()
    tipo = str(core.get("tipo_infraccion") or "").lower().strip()

    if tipo in ("atencion", "negligente", "conduccion_negligente"):
        return True

    signals = [
        "falta de atención",
        "falta de atencion",
        "no mantener la atención",
        "no mantener la atencion",
        "conducción negligente",
        "conduccion negligente",
        "art. 3.1",
        "art 3.1",
        "art. 18.1",
        "art 18.1",
    ]

    return any(s in (blob + "\n" + hecho) for s in signals)


def build_atencion_strong_template(core: Dict[str, Any], body: str = "") -> Dict[str, str]:
    core = core or {}

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "FALTA DE ATENCIÓN PERMANENTE A LA CONDUCCIÓN."

    fecha = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha})" if isinstance(fecha, str) and fecha.strip() else ""

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — RIESGO REAL Y OBJETIVABLE\n\n"
        "El tipo sancionador exige acreditar una conducta concreta que genere un riesgo real y objetivable. "
        "No basta una valoración genérica sobre la atención del conductor.\n\n"
        "No consta acreditado en el expediente:\n"
        "1) Maniobra específica realizada.\n"
        "2) Distancia real respecto de otros usuarios.\n"
        "3) Intensidad de tráfico y configuración de la vía.\n"
        "4) Existencia de peligro concreto y no meramente hipotético.\n\n"
        "ALEGACIÓN SEGUNDA — DURACIÓN Y MEDICIÓN\n\n"
        "Si se afirma una distancia prolongada (p. ej. 1,5 km), debe acreditarse punto inicial y final, "
        "medio de medición y continuidad real de observación.\n\n"
        "ALEGACIÓN TERCERA — MOTIVACIÓN INDIVIDUALIZADA\n\n"
        "La resolución debe describir hechos y prueba concreta, evitando fórmulas estereotipadas.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro con soporte verificable.\n"
    )

    return {"asunto": asunto, "cuerpo": cuerpo.strip()}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []
    if "riesgo" not in b:
        missing.append("riesgo_real")
    if "maniobra" not in b:
        missing.append("maniobra_concreta")
    if "distancia" not in b:
        missing.append("distancia")
    if "archivo" not in b:
        missing.append("archivo")
    out = []
    seen = set()
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
