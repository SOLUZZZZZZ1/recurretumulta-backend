"""RTM — ATENCIÓN / CONDUCCIÓN NEGLIGENTE (ART. 3.1 / 18.1 RGC) — DEMOLEDOR 'CASE-AWARE'

Objetivo:
- NO genérico: desmonta el relato concreto del boletín.
- Se activa por palabras del hecho: km/interceptado, palmas/volante/tambor, menor/niño.
- Mantiene compatibilidad: is_atencion_context(core, body=""), build_atencion_strong_template(core, body="")

Determinista. Sin OpenAI.
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

    # Guard anti-condiciones_vehiculo (evita secuestro de alumbrado/ITV/etc.)
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
        # patrones conductuales
        "bail", "palm", "golpe", "volante", "tambor",
        # ciclistas
        "ciclist", "biciclet", "arcén", "arcen", "paralelo", "de a tres", "ocupando",
        # distancia/interceptación
        "intercept", "tramo", "km", "kilómetro", "kilometro",
        # menor
        "menor", "niñ", "bebe", "bebé", "dos años", "2 años", "asiento trasero",
    ]
    return any(s in b for s in signals)


def _has_distance(b: str) -> bool:
    if re.search(r"\b\d+(?:[\.,]\d+)?\s*km\b", b):
        return True
    if "kilómetro" in b or "kilometro" in b:
        return True
    if "intercept" in b:
        return True
    if "tramo" in b and re.search(r"\b\d+(?:[\.,]\d+)?\b", b):
        return True
    return False


def _has_conducta_interior(b: str) -> bool:
    return any(k in b for k in ["bail", "palm", "golpe", "volante", "tambor"]) or ("interior del veh" in b)


def _has_menor(b: str) -> bool:
    return any(k in b for k in ["menor", "niñ", "bebe", "bebé", "dos años", "2 años", "asiento trasero"]) 


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
        "El tipo sancionador exige acreditar una conducta concreta que genere un RIESGO REAL, específico y objetivable. "
        "No basta una descripción llamativa ni valoraciones subjetivas.\n\n"
        "No consta acreditado en el expediente:\n"
        "1) Maniobra específica (trayectoria anómala, invasión de carril, aproximación peligrosa, frenada o maniobra evasiva de terceros, etc.).\n"
        "2) Distancia real respecto de otros vehículos/usuarios y circunstancias del tráfico en ese instante.\n"
        "3) Configuración exacta de la vía y condiciones de observación (posición del agente, distancia, ángulo, visibilidad).\n"
        "4) Consecuencia objetiva del riesgo (hecho verificable), no meramente hipotética.\n"
    )

    # 🔥 Bloques específicos (desmontan el relato)
    if _has_distance(b):
        parts.append(
            "\nBLOQUE DEMOLEDOR — CONTRADICCIÓN DE INTERVENCIÓN TARDÍA (KM / INTERCEPTACIÓN)\n\n"
            "Si la denuncia afirma seguimiento durante un tramo (p.ej. kilómetros) hasta la interceptación, debe acreditarse cómo se midió la distancia, "
            "desde qué punto a qué punto y que la observación fue continua.\n"
            "Además, si el peligro era real e inminente, debe explicarse por qué no se intervino de forma inmediata desde el primer momento, "
            "pues permitir la continuidad de la marcha durante un tramo prolongado es difícilmente compatible con un riesgo grave y actual.\n"
        )

    if _has_conducta_interior(b):
        parts.append(
            "\nBLOQUE DEMOLEDOR — OBSERVACIÓN DEL INTERIOR DEL VEHÍCULO (PALMAS / VOLANTE / CONDUCTA)\n\n"
            "La denuncia describe conductas realizadas dentro del habitáculo (p. ej. tocar las palmas, golpear el volante, etc.). "
            "Debe precisarse desde qué posición se realizó la observación (detrás/lateral/en paralelo), a qué distancia, durante cuánto tiempo y con qué visibilidad real.\n"
            "Sin estos datos, la afirmación constituye una inferencia no verificable y no permite valorar fiabilidad perceptiva ni contradicción efectiva.\n"
        )

    if _has_menor(b):
        parts.append(
            "\nBLOQUE DEMOLEDOR — MENOR EN ASIENTO TRASERO (OBSERVACIÓN + SRI)\n\n"
            "La mención a un menor en el asiento trasero exige concretar cuándo y cómo se observó (durante la marcha o tras la detención). "
            "Asimismo, debe aclararse si el menor se encontraba en un sistema de retención infantil homologado (SRI/ISOFIX) correctamente instalado.\n"
            "Sin esos extremos, la referencia al menor no acredita por sí misma riesgo real ni refuerza la tipicidad del art. 3.1.\n"
        )

    if _has_ciclistas(b):
        parts.append(
            "\nBLOQUE ESPECÍFICO — CICLISTAS / ARCÉN / PARALELO (SOLO SI CONSTA EN BOLETÍN)\n\n"
            "Si se alude a ciclistas/bicicletas, circulación en paralelo o uso de arcén, la imputación debe concretar: anchura efectiva del carril, "
            "intensidad de tráfico, posición exacta, distancias y maniobra concreta que evidencie riesgo real. Sin esos datos, la imputación es estereotipada.\n"
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
