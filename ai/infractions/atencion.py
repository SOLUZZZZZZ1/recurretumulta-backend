"""RTM — ATENCIÓN (ART. 3.1 / 18.1) — GUARD anti-condiciones_vehiculo

Evita secuestrar multas técnicas del vehículo (alumbrado/ITV/neumáticos/reformas).
"""

from __future__ import annotations
from typing import Any, Dict, List
import re


def _blob(core: Dict[str, Any], body: str) -> str:
    hecho = str(core.get("hecho_imputado") or "")
    raw = str(core.get("raw_text_blob") or "")
    return ((body or "") + "\n" + hecho + "\n" + raw).lower()


def is_atencion_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    b = _blob(core, body)

    cond_signals = [
        "condiciones reglamentarias",
        "dispositivos de alumbrado",
        "señalización óptica", "senalizacion optica",
        "rd 2822/98", "2822/98",
        "anexo ii", "anexo i",
        "itv",
        "neumático", "neumatico", "banda de rodadura",
        "destello", "destellos", "luz roja",
        "reflect", "reflej", "pulid", "como un espejo", "deslumbr",
        "reforma", "homolog", "proyecto", "certificado",
    ]
    if any(s in b for s in cond_signals):
        return False

    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo in ("atencion", "negligente", "conduccion_negligente", "conducción negligente"):
        return True

    signals = [
        "no mantener la atención", "no mantener la atencion",
        "atención permanente", "atencion permanente",
        "conducción negligente", "conduccion negligente", "conducir de forma negligente",
        "distracción", "distraccion",
        "no se percata", "conversando",
        "art. 3.1", "art 3.1", "artículo 3", "articulo 3",
        "art. 18.1", "art 18.1", "artículo 18", "articulo 18",
        "bailando", "palmas", "golpeando", "volante", "tambor",
        "ciclist", "biciclet", "arcén", "arcen", "paralelo", "de a tres", "ocupando",
        "interceptad", "tramo",
    ]
    return any(s in b for s in signals)


def _has_distance(b: str) -> bool:
    if re.search(r"\b\d+(?:[\.,]\d+)?\s*km\b", b):
        return True
    if "kilómetro" in b or "kilometro" in b:
        return True
    if "hasta ser intercept" in b or "interceptad" in b:
        return True
    if "tramo" in b and re.search(r"\b\d+(?:[\.,]\d+)?\b", b):
        return True
    return False


def _has_ciclistas(b: str) -> bool:
    return any(k in b for k in ["ciclist", "biciclet", "arcén", "arcen", "paralelo", "de a tres", "ocupando"])


def build_atencion_strong_template(core: Dict[str, Any], body: str = "") -> Dict[str, str]:
    core = core or {}
    b = _blob(core, body)

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "NO MANTENER LA ATENCIÓN PERMANENTE A LA CONDUCCIÓN (RGC)."

    fecha = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha})" if isinstance(fecha, str) and fecha.strip() else ""

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    parts: List[str] = []
    parts.append(
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — ELEMENTO OBJETIVO: RIESGO REAL Y OBJETIVABLE (ART. 3.1 / 18.1)\n\n"
        "El tipo sancionador exige acreditar una conducta concreta que genere un RIESGO REAL, específico y objetivable. No basta una descripción llamativa ni valoraciones subjetivas.\n\n"
        "No consta acreditado en el expediente:\n"
        "1) Maniobra específica (trayectoria anómala, invasión de carril, aproximación peligrosa, frenada o maniobra evasiva de terceros, etc.).\n"
        "2) Distancia real respecto de otros vehículos/usuarios y circunstancias del tráfico en ese instante.\n"
        "3) Configuración exacta de la vía y condiciones de observación (posición del agente, distancia, ángulo, visibilidad).\n"
        "4) Consecuencia objetiva del riesgo (hecho verificable), no meramente hipotética.\n"
    )

    if _has_ciclistas(b):
        parts.append(
            "\nBLOQUE ESPECÍFICO — CICLISTAS / ARCÉN / PARALELO (SOLO SI CONSTA EN BOLETÍN)\n\n"
            "Si se alude a ciclistas/bicicletas, circulación en paralelo o uso de arcén, la imputación debe concretar: anchura efectiva del carril, intensidad de tráfico, posición exacta, distancias y maniobra concreta que evidencie riesgo real. Sin esos datos, la imputación es estereotipada.\n"
        )

    if _has_distance(b):
        parts.append(
            "\nALEGACIÓN SEGUNDA — DISTANCIA/DURACIÓN ALEGADA (SOLO SI CONSTA EN BOLETÍN)\n\n"
            "Cuando se afirma una distancia o duración concreta, debe acreditarse punto inicial y final exactos, medio de medición y continuidad real de observación, así como por qué no hubo actuación preventiva inmediata si el peligro era real.\n"
        )

    parts.append(
        "\nALEGACIÓN FINAL — PRUEBA OBJETIVA Y EXPEDIENTE ÍNTEGRO\n\n"
        "Se solicita expediente íntegro y cualquier soporte objetivo (grabación, fotografías, anotaciones, croquis, testigos) para contradicción efectiva.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO por insuficiencia probatoria y falta de motivación concreta.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa.\n"
    )

    return {"asunto": asunto, "cuerpo": "".join(parts).strip()}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []
    if "riesgo" not in b:
        missing.append("riesgo")
    if "maniobra" not in b:
        missing.append("maniobra")
    if "archivo" not in b:
        missing.append("archivo")
    out=[]
    seen=set()
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
