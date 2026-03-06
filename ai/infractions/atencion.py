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

    tipo = str(core.get("tipo_infraccion") or "").lower().strip()

    # 🔒 Blindaje real: velocidad nunca debe entrar aquí
    if tipo == "velocidad":
        return False

    # Si analyze ya dijo atención/negligente, dentro
    if tipo in (
        "atencion",
        "negligente",
        "conduccion_negligente",
        "conducción negligente",
    ):
        return True

    # Señales fuertes reales de atención/negligente
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
    ]

    return any(s in b for s in strong_signals)


def _has_distance(b: str) -> bool:
    # 1,5 km / 1.5 km
    if re.search(r"\b\d+(?:[.,]\d+)\s*km\b", b):
        return True

    # 1 5 km (OCR roto)
    if re.search(r"\b\d+\s+\d+\s*km\b", b):
        return True

    # mejor usar además contexto semántico
    if "intercept" in b:
        return True

    if "tramo" in b and "km" in b:
        return True

    return False


def _has_conducta_interior(b: str) -> bool:
    return any(k in b for k in ["bail", "palm", "golpe", "volante", "tambor"])


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

    # ==========================
    # RUTA A: CICLISTAS
    # ==========================
    if _has_ciclistas(b):
        parts.append(
            "ALEGACIÓN PRIMERA — FALTA DE CONCRECIÓN DEL RIESGO REAL\n\n"
            "La imputación debe describir con precisión la maniobra concreta que habría generado "
            "riesgo real para la circulación. No basta una referencia genérica a la posición de los "
            "ciclistas, a que fueran conversando o a que circularan en paralelo si no se concretan "
            "circunstancias objetivas del peligro.\n\n"
            "No se especifica con precisión:\n"
            "- anchura útil de la vía o del arcén\n"
            "- intensidad real del tráfico en ese momento\n"
            "- distancia respecto de otros usuarios\n"
            "- maniobra concreta de riesgo o reacción de terceros\n\n"
            "Sin esos datos no puede afirmarse una situación de peligro objetivable.\n"
        )

        if _has_distance(b):
            parts.append(
                "\nALEGACIÓN SEGUNDA — FALTA DE PRECISIÓN SOBRE TRAMO Y OBSERVACIÓN\n\n"
                "Si se menciona un tramo o seguimiento, debe concretarse su longitud real, "
                "cómo fue determinada y si la observación se mantuvo de forma continua.\n\n"
                "Sin esa precisión, la imputación carece de la concreción necesaria para "
                "fundamentar válidamente la sanción.\n"
            )

    # ==========================
    # RUTA B: PALMAS / VOLANTE / MENOR
    # ==========================
    else:
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
            parts.append(
                "\nALEGACIÓN TERCERA — CONDICIONES DE OBSERVACIÓN DEL INTERIOR DEL VEHÍCULO\n\n"
                "La denuncia describe conductas realizadas dentro del habitáculo del vehículo "
                "(por ejemplo tocar las palmas o golpear el volante).\n\n"
                "Sin embargo, el boletín no indica:\n"
                "- desde qué posición se realizó la observación\n"
                "- a qué distancia\n"
                "- durante cuánto tiempo\n\n"
                "La ausencia de estos datos impide valorar la fiabilidad de la observación realizada.\n"
            )

        if _has_menor(b):
            parts.append(
                "\nALEGACIÓN CUARTA — PRESENCIA DEL MENOR EN EL VEHÍCULO\n\n"
                "La denuncia menciona la presencia de un menor en el asiento trasero.\n\n"
                "No se especifica:\n"
                "- en qué momento se observó al menor\n"
                "- si la observación se realizó durante la marcha o tras la detención\n"
                "- si el menor utilizaba un sistema de retención infantil homologado.\n\n"
                "Sin estos extremos no puede afirmarse que dicha circunstancia implique por sí misma "
                "un riesgo real para la seguridad vial.\n"
            )

    parts.append(
        "\nALEGACIÓN FINAL — PRUEBA OBJETIVA\n\n"
        "Se solicita la aportación de cualquier prueba objetiva que permita verificar los hechos descritos "
        "(grabaciones, fotografías o anotaciones completas del agente).\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el archivo del expediente por insuficiencia probatoria.\n"
    )

    return {"asunto": asunto, "cuerpo": "".join(parts).strip()}