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
        "conducir de forma temeraria",
        "conducción temeraria",
        "conduccion temeraria",
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
        "acompañante",
        "acompanante",
        "cabeza entre las piernas del conductor",
        "no se para en el lugar",
        "al ordenarle la detención",
        "al ordenarle la detencion",
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
            "cabeza entre las piernas del conductor",
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

    # solo si el boletín lo dice literalmente
    if "tocando las palmas" in b or "tocar las palmas" in b:
        ejemplos.append("tocar las palmas")

    if "golpeando el volante" in b or "golpear el volante" in b:
        ejemplos.append("golpear el volante")

    if "bail" in b:
        ejemplos.append("bailar dentro del vehículo")

    if "mantiene conversacion" in b or "mantener conversacion" in b or "mantiene conversación" in b:
        ejemplos.append("mantener conversación con ocupantes")

    if "mirando en repetidas ocasiones" in b:
        ejemplos.append("mirar repetidamente al acompañante")

    if "mordia las uñas" in b or "mordia las unas" in b:
        ejemplos.append("morderse las uñas mientras conduce")

    if "libertad de movimientos" in b:
        ejemplos.append("conducir sin mantener la propia libertad de movimientos")

    if "cabeza entre las piernas del conductor" in b:
        ejemplos.append("mantener a la acompañante con la cabeza entre las piernas del conductor")

    if not ejemplos:
        return ""

    ejemplos = list(dict.fromkeys(ejemplos))
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
        "ALEGACIÓN PRIMERA — FALTA DE TIPICIDAD Y DE SUBSUNCIÓN SUFICIENTE EN EL TIPO SANCIONADOR\n\n"
        "La infracción imputada exige acreditar una conducta de conducción que evidencie una falta real de atención "
        "incompatible con la seguridad vial. No basta una descripción llamativa, impropia o moralmente reprochable "
        "si no se explica de forma concreta y verificable cómo esa conducta afectó efectivamente al control del vehículo "
        "o generó un riesgo vial objetivable.\n\n"
        "La denuncia, tal como aparece redactada, no describe con precisión una subsunción suficiente entre el hecho observado "
        "y el tipo administrativo aplicado, por lo que se produce una insuficiente motivación típica contraria al principio de tipicidad "
        "propio del Derecho sancionador.\n"
    )

    parts.append(
        "\nALEGACIÓN SEGUNDA — AUSENCIA DE MANIOBRA PELIGROSA O DE RIESGO VIAL CONCRETO\n\n"
        "La denuncia no describe una maniobra concreta de conducción que permita afirmar un peligro real para la seguridad vial.\n\n"
        "No se menciona:\n"
        "- desviación de trayectoria\n"
        "- invasión de carril\n"
        "- frenada brusca\n"
        "- pérdida de control del vehículo\n"
        "- reacción evasiva de otros conductores\n"
        "- riesgo vial concreto, individualizado y objetivable\n\n"
        "Sin una maniobra peligrosa o un riesgo vial descrito de forma precisa, no puede afirmarse con el rigor exigible "
        "la concurrencia de conducción negligente o de falta de atención sancionable.\n"
    )

    if _has_distance(b):
        parts.append(
            "\nALEGACIÓN TERCERA — INTERVENCIÓN NO INMEDIATA DEL AGENTE Y FALTA DE CORRELACIÓN CON UN PELIGRO INMINENTE\n\n"
            "La denuncia sugiere que la conducta fue observada durante un cierto tramo antes de proceder a la interceptación del vehículo.\n\n"
            "Si la conducta generaba realmente un peligro inmediato y relevante para la circulación, resultaría lógico que la intervención "
            "se produjera de forma inmediata. La continuidad de la marcha durante una distancia apreciable es difícilmente compatible con "
            "la existencia de un riesgo real e inminente en los términos exigibles para sostener la imputación.\n"
        )

    if _has_conducta_interior(b):
        ejemplo_texto = _extraer_ejemplos_habitaculo(b)

        parts.append(
            "\nALEGACIÓN CUARTA — EXIGENCIA REFORZADA DE FIABILIDAD EN LA OBSERVACIÓN DEL INTERIOR DEL VEHÍCULO\n\n"
            "La denuncia describe conductas realizadas dentro del habitáculo del vehículo"
            + ejemplo_texto +
            ".\n\n"
            "Precisamente por tratarse de hechos supuestamente percibidos en el interior del vehículo, la Administración debe concretar con especial rigor:\n"
            "- desde qué posición se realizó la observación\n"
            "- a qué distancia\n"
            "- con qué ángulo visual\n"
            "- durante cuánto tiempo\n"
            "- con qué iluminación y continuidad visual\n\n"
            "La ausencia de estos datos impide valorar la fiabilidad real de la observación y debilita gravemente la fuerza incriminatoria del boletín.\n"
        )

    if _has_menor(b):
        parts.append(
            "\nALEGACIÓN ADICIONAL — IMPROCEDENCIA DE INTRODUCIR ELEMENTOS EMOCIONALES O ACCESORIOS SIN VALOR TÍPICO AUTÓNOMO\n\n"
            "La mera referencia a menores, acompañantes u otras circunstancias accesorias no suple la necesidad de describir con precisión "
            "la conducta de conducción relevante a efectos sancionadores. La valoración jurídica debe centrarse en el hecho típico realmente imputado "
            "y no en elementos de impacto narrativo que, por sí solos, no acreditan la infracción.\n"
        )

    parts.append(
        "\nIII. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el archivo del expediente por insuficiencia probatoria, falta de tipicidad suficiente y ausencia de motivación individualizada.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y prueba completa para contradicción efectiva.\n"
    )

    return {"asunto": asunto, "cuerpo": "".join(parts).strip()}
