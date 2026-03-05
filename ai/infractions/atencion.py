from __future__ import annotations
from typing import Any, Dict, List
import re


def _blob(core: Dict[str, Any], body: str) -> str:
    hecho = str(core.get("hecho_imputado") or "")
    raw = str(core.get("raw_text_blob") or "")
    return ((body or "") + "\n" + hecho + "\n" + raw).lower()


def is_atencion_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}

    # 🔒 Blindaje: si analyze ya decidió un tipo distinto, NO secuestrar.
    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo and tipo not in ("atencion", "negligente", "conduccion_negligente", "conducción negligente"):
        return False

    b = _blob(core, body)

    # Señales fuertes de atención/negligente (NO usamos "km" para detectar, para evitar secuestro de velocidad)
    signals = [
        "no mantener la atención",
        "no mantener la atencion",
        "atención permanente",
        "atencion permanente",
        "conducción negligente",
        "conduccion negligente",
        "conducir de forma negligente",
        "distracción",
        "distraccion",
        "libertad de movimientos",
        # conducta concreta
        "bail",
        "palm",
        "golpe",
        "volante",
        "tambor",
    ]

    return any(s in b for s in signals)


def _has_distance(b: str) -> bool:
    # aquí sí usamos km/interceptado, pero solo dentro de un caso ya clasificado como atención
    if re.search(r"\b\d+(?:[\.,]\d+)?\s*km\b", b):
        return True
    if "intercept" in b:
        return True
    if "tramo" in b:
        return True
    return False


def _has_conducta_interior(b: str) -> bool:
    return any(k in b for k in ["bail", "palm", "golpe", "volante", "tambor"])


def _has_menor(b: str) -> bool:
    return any(k in b for k in ["menor", "niñ", "bebe", "bebé", "dos años", "2 años", "asiento trasero"])


def build_atencion_strong_template(core: Dict[str, Any], body: str = "") -> Dict[str, str]:
    core = core or {}
    b = _blob(core, body)

    expediente = core.get("expediente_ref") or "No consta acreditado."
    organo = core.get("organo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "NO MANTENER LA ATENCIÓN PERMANENTE A LA CONDUCCIÓN"

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    parts: List[str] = []

    parts.append(
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — AUSENCIA DE CONDUCTA DE CONDUCCIÓN CONCRETA\n\n"
        "La denuncia puede describir gestos o comportamientos dentro del vehículo, pero debe acreditar una maniobra concreta "
        "de conducción que suponga peligro real para la seguridad vial (trayectoria anómala, invasión, frenada, maniobra evasiva, etc.).\n\n"
        "Sin conducta de conducción concreta y sin acreditación objetiva del riesgo, no procede la subsunción típica del art. 3.1/18.1.\n"
    )

    if _has_distance(b):
        parts.append(
            "\nALEGACIÓN SEGUNDA — COHERENCIA DE LA INTERVENCIÓN\n\n"
            "Si se afirma observación durante un tramo (km) hasta la interceptación, debe acreditarse cómo se midió la distancia y "
            "la continuidad real de la observación.\n"
            "Si el peligro era real e inminente, debe explicarse por qué no se intervino de forma inmediata desde el primer momento.\n"
        )

    if _has_conducta_interior(b):
        parts.append(
            "\nALEGACIÓN TERCERA — CONDICIONES DE OBSERVACIÓN DEL INTERIOR\n\n"
            "Debe precisarse desde qué posición se observó el interior del vehículo (detrás/lateral), a qué distancia, durante cuánto tiempo "
            "y con qué visibilidad real. Sin estos datos no puede valorarse la fiabilidad de la observación.\n"
        )

    if _has_menor(b):
        parts.append(
            "\nALEGACIÓN CUARTA — MENOR EN EL VEHÍCULO\n\n"
            "La mención al menor exige concretar cuándo y cómo se observó (en marcha o tras la detención) y si el menor estaba en "
            "sistema de retención infantil homologado. Sin ello, no puede usarse para afirmar riesgo real.\n"
        )

    parts.append(
        "\nALEGACIÓN FINAL — PRUEBA OBJETIVA\n\n"
        "Se solicita expediente íntegro y cualquier soporte objetivo (grabación, fotografías, anotaciones) que permita verificar los hechos.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el archivo del expediente por insuficiencia probatoria.\n"
    )

    return {"asunto": asunto, "cuerpo": "".join(parts).strip()}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []
    if "archivo" not in b:
        missing.append("archivo")
    return missing
