from __future__ import annotations
from typing import Any, Dict, List
import re


def _blob(core: Dict[str, Any], body: str) -> str:
    hecho = str(core.get("hecho_imputado") or "")
    raw = str(core.get("raw_text_blob") or "")
    literal = str(core.get("hecho_denunciado_literal") or "")
    return ((body or "") + "\n" + hecho + "\n" + literal + "\n" + raw).lower()


def is_atencion_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    b = _blob(core, body)

    tipo = str(core.get("tipo_infraccion") or "").lower().strip()

    if tipo == "velocidad":
        return False

    if tipo in (
        "atencion",
        "negligente",
        "conduccion_negligente",
        "conducción negligente",
    ):
        return True

    strong_signals = [
        "no mantener la atención",
        "no mantener la atencion",
        "atención permanente",
        "atencion permanente",
        "conducción negligente",
        "conduccion negligente",
        "conducir de forma negligente",
        "distracción",
        "distraccion",
        "bail",
        "palm",
        "golpe",
        "volante",
        "tambor",
        "menor",
        "niñ",
        "bebe",
        "bebé",
        "intercept",
        "tramo",
        "convers",
        "mirando en repetidas ocasiones",
        "libertad de movimientos",
        "mordia las uñas",
        "mordia las unas",
    ]

    return any(s in b for s in strong_signals)


def _has_distance(b: str) -> bool:
    if re.search(r"\b\d+(?:[.,]\d+)\s*km\b", b):
        return True

    if re.search(r"\b\d+\s+\d+\s*km\b", b):
        return True

    if "intercept" in b:
        return True

    if "tramo" in b and "km" in b:
        return True

    return False


def _has_conducta_interior(b: str) -> bool:
    return any(
        k in b
        for k in [
            "bail",
            "palm",
            "golpe",
            "volante",
            "tambor",
            "convers",
            "mirando",
            "acompañante",
            "acompanante",
            "mordia las uñas",
            "mordia las unas",
            "libertad de movimientos",
        ]
    )


def _has_menor(b: str) -> bool:
    return any(
        k in b
        for k in [
            "menor",
            "niñ",
            "bebe",
            "bebé",
            "dos años",
            "2 años",
            "asiento trasero",
        ]
    )


def _has_ciclistas(b: str) -> bool:
    return any(
        k in b
        for k in [
            "ciclist",
            "biciclet",
            "arcén",
            "arcen",
            "paralelo",
            "de a tres",
            "ocupando",
            "conversando",
        ]
    )


def _extraer_ejemplos_habitaculo(b: str) -> str:
    ejemplos = []

    if "palm" in b:
        ejemplos.append("tocar las palmas")
    if "volante" in b:
        ejemplos.append("golpear el volante")
    if "bail" in b:
        ejemplos.append("bailar dentro del vehículo")
    if "convers" in b:
        ejemplos.append("mantener conversación con ocupantes")
    if "mirando" in b and ("acompañante" in b or "acompanante" in b):
        ejemplos.append("mirar repetidamente al acompañante")
    if "mordia las uñas" in b or "mordia las unas" in b:
        ejemplos.append("morderse las uñas mientras conduce")
    if "libertad de movimientos" in b:
        ejemplos.append("conducir sin mantener la propia libertad de movimientos")

    if not ejemplos:
        return ""

    return " (por ejemplo " + ", ".join(ejemplos) + ")"


def build_atencion_strong_template(core: Dict[str, Any], body: str = "") -> Dict[str, str]:
    core = core or {}
    b = _blob(core, body)

    expediente = core.get("expediente_ref") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
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
    )

    parts.append(
        "ALEGACIÓN PRIMERA — AUSENCIA DE CONDUCTA DE CONDUCCIÓN CONCRETA\n\n"
        "La denuncia describe conductas realizadas dentro del vehículo, pero no describe "
        "ninguna maniobra concreta de conducción que suponga peligro real para la seguridad vial.\n\n"
        "No se menciona:\n"
        "- desviación de trayectoria\n"
        "- invasión de carril\n"
        "- frenada brusca\n"
        "- reacción de otros conductores\n\n"
        "Sin una conducta de conducción concreta no puede afirmarse la existencia de conducción negligente.\n"
    )

    if _has_distance(b):
        parts.append(
            "\nALEGACIÓN SEGUNDA — INTERVENCIÓN TARDÍA DEL AGENTE\n\n"
            "La denuncia afirma que la conducta fue observada durante un tramo antes de proceder "
            "a la interceptación del vehículo.\n\n"
            "Si la conducta generaba realmente un peligro inmediato para la seguridad vial, "
            "resultaría lógico que la intervención se produjera de forma inmediata.\n\n"
            "La continuación de la marcha durante una distancia apreciable resulta difícilmente "
            "compatible con la existencia de un riesgo real e inminente.\n"
        )

    if _has_conducta_interior(b):
        ejemplo_texto = _extraer_ejemplos_habitaculo(b)

        parts.append(
            "\nALEGACIÓN TERCERA — CONDICIONES DE OBSERVACIÓN DEL INTERIOR DEL VEHÍCULO\n\n"
            "La denuncia describe conductas realizadas dentro del habitáculo del vehículo"
            + ejemplo_texto +
            ".\n\n"
            "Sin embargo, el boletín no indica:\n"
            "- desde qué posición se realizó la observación\n"
            "- a qué distancia\n"
            "- durante cuánto tiempo\n\n"
            "La ausencia de estos datos impide valorar la fiabilidad de la observación realizada.\n"
        )

    parts.append(
        "\nIII. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el archivo del expediente por insuficiencia probatoria.\n"
    )

    return {"asunto": asunto, "cuerpo": "".join(parts).strip()}