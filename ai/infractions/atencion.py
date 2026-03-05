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

    signals = [
        "no mantener la atención",
        "conducción negligente",
        "conducir de forma negligente",
        "distracción",
        "bail",
        "palm",
        "golpe",
        "volante",
        "tambor",
        "intercept",
        "km",
        "menor",
        "niñ",
        "bebe",
        "bebé",
    ]

    return any(s in b for s in signals)


def _has_distance(b: str) -> bool:
    if re.search(r"\b\d+(?:[.,]\d+)?\s*km\b", b):
        return True
    if "intercept" in b:
        return True
    if "tramo" in b:
        return True
    return False


def _has_conducta_interior(b: str) -> bool:
    return any(k in b for k in ["bail", "palm", "golpe", "volante", "tambor"])


def _has_menor(b: str) -> bool:
    return any(k in b for k in ["menor", "niñ", "bebe", "bebé", "dos años", "2 años"])


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
        "ALEGACIÓN PRIMERA — ELEMENTO OBJETIVO DEL TIPO\n\n"
        "El tipo sancionador exige acreditar una conducta concreta que genere un riesgo real "
        "para la seguridad vial. No basta una descripción genérica o valoraciones subjetivas.\n\n"
        "No consta acreditado en el expediente:\n"
        "1) Maniobra concreta que suponga peligro real.\n"
        "2) Distancia respecto de otros usuarios de la vía.\n"
        "3) Condiciones exactas de observación del agente.\n"
        "4) Consecuencia objetiva derivada de la supuesta conducta.\n"
    )

    if _has_distance(b):
        parts.append(
            "\nALEGACIÓN SEGUNDA — INTERVENCIÓN TARDÍA DEL AGENTE\n\n"
            "La denuncia afirma que la conducta fue observada durante un tramo prolongado "
            "hasta la interceptación del vehículo.\n\n"
            "Si la conducta descrita generaba realmente una situación de peligro para la "
            "seguridad vial, resulta lógico preguntarse por qué no se produjo una intervención "
            "inmediata desde el primer momento en que fue observada.\n\n"
            "La continuidad de la marcha durante una distancia considerable cuestiona la "
            "existencia de un riesgo real e inminente.\n"
        )

    if _has_conducta_interior(b):
        parts.append(
            "\nALEGACIÓN TERCERA — CONDICIONES DE OBSERVACIÓN DEL INTERIOR DEL VEHÍCULO\n\n"
            "La denuncia describe conductas realizadas dentro del habitáculo del vehículo "
            "(por ejemplo tocar las palmas o golpear el volante).\n\n"
            "Sin embargo, el boletín no indica desde qué posición se realizó la observación, "
            "ni la distancia aproximada, ni el tiempo durante el cual se habría producido.\n\n"
            "La ausencia de estos datos impide valorar la fiabilidad de la observación.\n"
        )

    if _has_menor(b):
        parts.append(
            "\nALEGACIÓN CUARTA — PRESENCIA DEL MENOR EN EL VEHÍCULO\n\n"
            "La denuncia menciona la presencia de un menor en el asiento trasero, pero no "
            "indica en qué momento se realizó dicha observación ni si el menor se encontraba "
            "utilizando un sistema de retención infantil homologado.\n\n"
            "Sin estos extremos no puede afirmarse que dicha circunstancia implique por sí "
            "misma un riesgo real para la seguridad vial.\n"
        )

    parts.append(
        "\nALEGACIÓN FINAL — PRUEBA OBJETIVA\n\n"
        "Se solicita la aportación de cualquier prueba objetiva que permita verificar "
        "los hechos descritos (grabaciones, fotografías o anotaciones completas del agente).\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el archivo del expediente por insuficiencia probatoria.\n"
    )

    return {"asunto": asunto, "cuerpo": "".join(parts).strip()}