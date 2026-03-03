"""RTM — ATENCIÓN / CONDUCCIÓN NEGLIGENTE (ART. 3.1 / 18.1 RGC) — DEMOLEDOR (Operativo)

Detector ampliado para casos tipo 'bailando/palmas/volante/tambor'.
Determinista.
"""

from __future__ import annotations
from typing import Any, Dict, List
import re


def is_atencion_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo in ("atencion", "negligente", "conduccion_negligente", "conducción negligente"):
        return True

    hecho = str(core.get("hecho_imputado") or "")
    raw = str(core.get("raw_text_blob") or "")
    blob = (body or "") + "\n" + hecho + "\n" + raw
    b = blob.lower()

    signals = [
        "no mantener la atención", "no mantener la atencion", "atención permanente", "atencion permanente",
        "conducción negligente", "conduccion negligente", "conducir de forma negligente",
        "situacion de riesgo", "situación de riesgo", "riesgo y peligro", "peligro para",
        "art. 3.1", "art 3.1", "artículo 3", "articulo 3",
        "art. 18.1", "art 18.1", "artículo 18", "articulo 18",
        "bailando", "palmas", "tocando las palmas", "dar palmas",
        "golpeando contra el volante", "golpeando el volante", "volante", "tambor", "como si fuera un tambor",
        "no se percata", "conversando", "distracción", "distraccion",
        "interceptado", "hasta ser interceptado",
    ]
    return any(s in b for s in signals)


def build_atencion_strong_template(core: Dict[str, Any], body: str = "") -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "NO MANTENER LA ATENCIÓN PERMANENTE A LA CONDUCCIÓN (RGC)."

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
        "ALEGACIÓN PRIMERA — ELEMENTO OBJETIVO: RIESGO REAL Y OBJETIVABLE (ART. 3.1 / 18.1)\n\n"
        "El tipo sancionador exige acreditar una conducta concreta que genere un RIESGO REAL, específico y objetivable. "
        "No basta una descripción llamativa ni valoraciones subjetivas.\n\n"
        "No consta acreditado en el expediente:\n"
        "1) Maniobra específica (trayectoria anómala, invasión de carril, aproximación peligrosa, frenada o maniobra evasiva de terceros, etc.).\n"
        "2) Distancia real respecto de otros vehículos/usuarios y circunstancias del tráfico en ese instante.\n"
        "3) Configuración exacta de la vía y condiciones de observación (posición del agente, distancia, ángulo, visibilidad).\n"
        "4) Consecuencia objetiva del riesgo (hecho verificable), no meramente hipotética.\n\n"
        "ALEGACIÓN SEGUNDA — DISTANCIA/DURACIÓN ALEGADA Y CONTINUIDAD DE OBSERVACIÓN\n\n"
        "Si se afirma que la conducta se prolongó durante un tramo relevante (p.ej. '1,5 km'), debe acreditarse punto inicial y final, "
        "medio de medición y continuidad real de observación, y por qué no hubo intervención inmediata si el peligro era real.\n\n"
        "ALEGACIÓN TERCERA — PRUEBA OBJETIVA Y EXPEDIENTE ÍNTEGRO\n\n"
        "Se solicita expediente íntegro y cualquier soporte objetivo (grabación, fotografías, anotaciones, croquis, testigos) para contradicción efectiva.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO por insuficiencia probatoria y falta de motivación concreta.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []
    if "riesgo" not in b:
        missing.append("riesgo")
    if "maniobra" not in b:
        missing.append("maniobra")
    if "distancia" not in b and "km" not in b:
        missing.append("distancia")
    if "archivo" not in b:
        missing.append("archivo")
    out=[]
    seen=set()
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
