"""
RTM — CONDUCCIÓN TEMERARIA / NEGLIGENTE GRAVE
Determinista, sin IA.
Salida: {"asunto","cuerpo"}

Objetivo:
- Activarse cuando la infracción gire sobre "conducción temeraria", "conducció temerària",
  "conducción negligente" o hechos próximos: adelantamiento peligroso, invasión de carril,
  línea continua, riesgo grave, maniobra antirreglamentaria, etc.
- Evitar que estos casos caigan en semáforo, velocidad o genérico.
- Reforzar líneas de defensa:
    * falta de concreción de la maniobra
    * falta de acreditación del riesgo concreto
    * ausencia de prueba objetiva o expediente completo
    * proporcionalidad y recalificación subsidiaria
"""

from __future__ import annotations
from typing import Any, Dict, List


def _safe_str(v: Any) -> str:
    return v if isinstance(v, str) else ""


def _norm(s: str) -> str:
    s = (s or "").lower()
    return (
        s.replace("á", "a")
         .replace("é", "e")
         .replace("í", "i")
         .replace("ó", "o")
         .replace("ú", "u")
         .replace("à", "a")
         .replace("è", "e")
         .replace("ò", "o")
         .replace("ï", "i")
         .replace("ü", "u")
         .replace("ç", "c")
         .replace("ñ", "n")
    )


def _blob(core: Dict[str, Any], body: str = "") -> str:
    parts: List[str] = []
    for k in (
        "raw_text_pdf",
        "raw_text_vision",
        "raw_text_blob",
        "vision_raw_text",
        "hecho_denunciado_literal",
        "hecho_denunciado_resumido",
        "hecho_imputado",
        "hecho_para_recurso",
        "subtipo_infraccion",
        "tipo_infraccion",
        "familia_resuelta",
        "preceptos_detectados",
        "norma_hint",
        "observaciones",
    ):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)
        elif isinstance(v, list) and v:
            parts.append(" ".join(str(x) for x in v if x is not None))
    if body:
        parts.append(body)
    return _norm(" ".join(parts))


def is_temeraria_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    b = _blob(core, body=body)
    tipo = _safe_str(core.get("tipo_infraccion")).lower().strip()
    familia = _safe_str(core.get("familia_resuelta")).lower().strip()

    if tipo in ("temeraria", "conduccion_temeraria", "conducción_temeraria", "conduccio_temeraria"):
        return True

    if familia in ("temeraria", "conduccion_temeraria", "conducción_temeraria", "conduccio_temeraria"):
        return True

    strong_signals = [
        "conduir de forma temeraria",
        "conduir de forma temeraria",
        "conduccio temeraria",
        "conduccion temeraria",
        "conduccion temeraria",
        "forma temeraria",
        "forma temeraria",
        "conduir de forma negligent",
        "conduccion negligente",
        "conduccio negligent",
        "posant en greu risc",
        "grave riesgo",
        "greu risc",
        "riesgo grave",
        "invasion del carril contrario",
        "invasio del carril contrari",
        "invaint el carril contrari",
        "adelantamiento peligroso",
        "avancament perillos",
        "avancant amb linia continua",
        "linea continua",
        "linia continua",
        "maniobra peligrosa",
        "maniobra perillosa",
    ]

    return any(s in b for s in strong_signals)


def _has_linea_continua(b: str) -> bool:
    return any(s in b for s in ["linea continua", "linia continua", "marca longitudinal continua"])


def _has_adelantamiento(b: str) -> bool:
    return any(s in b for s in ["adelant", "avanc", "avanç"])


def _has_carril_contrario(b: str) -> bool:
    return any(s in b for s in ["carril contrario", "carril contrari", "sentido contrario", "sentit contrari", "invaint"])


def _has_riesgo_generico(b: str) -> bool:
    return any(s in b for s in ["greu risc", "grave riesgo", "riesgo grave", "posant en greu risc"])


def build_temeraria_strong_template(core: Dict[str, Any], body: str = "") -> Dict[str, str]:
    core = core or {}
    b = _blob(core, body=body)

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = (
        core.get("hecho_imputado")
        or core.get("hecho_denunciado_literal")
        or core.get("hecho_denunciado_resumido")
        or "CONDUCCIÓN TEMERARIA / MANIOBRA SUPUESTAMENTE PELIGROSA."
    )

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
        "ALEGACIÓN PRIMERA — EXIGENCIA DE CONCRECIÓN REFORZADA EN UNA IMPUTACIÓN GRAVE\n\n"
        "La calificación de una conducta como conducción temeraria, negligente grave o generadora de riesgo relevante "
        "exige una descripción especialmente precisa de los hechos. No basta una fórmula genérica o valorativa, sino que "
        "deben concretarse la maniobra exacta, el punto de la vía, la dinámica observada, los vehículos o usuarios afectados "
        "y el riesgo objetivo que supuestamente se produjo.\n\n"
        "La utilización de expresiones como “forma temeraria”, “riesgo grave” o similares, sin una narración fáctica completa "
        "y verificable, no permite una subsunción típica suficientemente motivada ni garantiza el pleno ejercicio del derecho de defensa.\n"
    )

    parts.append(
        "\nALEGACIÓN SEGUNDA — AUSENCIA DE ACREDITACIÓN DEL RIESGO VIAL CONCRETO, INDIVIDUALIZADO Y OBJETIVABLE\n\n"
        "La gravedad de la imputación exige acreditar un riesgo vial real, concreto, individualizado y objetivable. "
        "No basta afirmar la existencia de un peligro de forma abstracta; debe describirse qué peligro se produjo, "
        "a qué usuario afectó, en qué momento, con qué intensidad y con qué soporte probatorio se acredita.\n\n"
        "No consta suficientemente acreditado:\n"
        "1) La existencia de un riesgo concreto para un tercero determinado.\n"
        "2) La maniobra exacta que habría creado dicho riesgo.\n"
        "3) La posición del vehículo denunciado y del resto de usuarios afectados.\n"
        "4) La necesidad de maniobras evasivas por parte de otros conductores o peatones.\n"
        "5) La existencia de frenadas, invasiones, pérdida de control o alteración efectiva de la circulación.\n\n"
        "Sin dicha concreción, la imputación queda reducida a una valoración genérica no suficiente para sostener una sanción grave.\n"
    )

    if _has_adelantamiento(b) or _has_linea_continua(b) or _has_carril_contrario(b):
        parts.append(
            "\nALEGACIÓN TERCERA — NECESIDAD DE DIFERENCIAR LA INFRACCIÓN FORMAL DE UNA VERDADERA CONDUCCIÓN TEMERARIA\n\n"
            "Si la Administración sostiene que existió un adelantamiento indebido, invasión de carril, rebase de línea continua "
            "o maniobra antirreglamentaria, debe diferenciar con claridad entre una eventual infracción formal de circulación "
            "y la calificación agravada de conducción temeraria o de riesgo grave.\n\n"
            "No toda infracción de señalización horizontal o posición en la vía permite automáticamente calificar la conducta "
            "como temeraria. Para ello debe acreditarse un plus de peligrosidad real y objetivable, distinto de la mera infracción "
            "formal, con prueba suficiente y motivación individualizada.\n"
        )

    if _has_riesgo_generico(b):
        parts.append(
            "\nALEGACIÓN CUARTA — INSUFICIENCIA DE LA MERA REFERENCIA A UN “RIESGO GRAVE” SIN DESARROLLO FÁCTICO\n\n"
            "La expresión de que la conducta habría puesto en grave riesgo a otros usuarios no puede operar como una cláusula "
            "vacía o automática. La Administración debe explicar qué usuarios resultaron concretamente afectados, cuál fue la "
            "situación de peligro creada y qué elementos probatorios permiten verificarla.\n\n"
            "En ausencia de esa precisión, la referencia al riesgo grave constituye una conclusión valorativa, no un hecho "
            "probado bastante para justificar la sanción en los términos pretendidos.\n"
        )

    parts.append(
        "\nALEGACIÓN QUINTA — CONDICIONES DE OBSERVACIÓN DEL AGENTE Y NECESIDAD DE SOPORTE OBJETIVO\n\n"
        "Cuando la imputación descansa en la observación directa de un agente, deben constar las condiciones de dicha observación: "
        "posición exacta, distancia, ángulo visual, duración de la observación, condiciones de tráfico, visibilidad y continuidad "
        "de la percepción. En una imputación de esta gravedad, la motivación no puede limitarse a una frase estandarizada.\n\n"
        "Asimismo, debe aportarse cualquier soporte objetivo disponible —fotografía, vídeo, croquis, informe complementario o "
        "diligencia explicativa— que permita comprobar la realidad de la maniobra y del riesgo afirmado.\n"
    )

    parts.append(
        "\nALEGACIÓN SEXTA — PROPORCIONALIDAD, TIPICIDAD Y RECALIFICACIÓN SUBSIDIARIA\n\n"
        "La eventual existencia de una maniobra antirreglamentaria no autoriza automáticamente la imposición de la calificación "
        "más gravosa si no se acredita el plus de peligrosidad exigible. En caso de que la Administración no estime el archivo, "
        "debe examinarse subsidiariamente si los hechos realmente acreditados admiten una calificación jurídica menos gravosa, "
        "sin detracción indebida de puntos o con la sanción mínima legalmente procedente.\n\n"
        "Esta petición subsidiaria se formula sin reconocer los hechos imputados y únicamente para el caso de que la Administración "
        "rechace el archivo del expediente.\n"
    )

    parts.append(
        "\nFUNDAMENTOS DE DERECHO\n\n"
        "PRIMERO.– Resultan de aplicación los artículos 24 y 25 de la Constitución Española, relativos a la presunción de inocencia, "
        "legalidad sancionadora y principio de tipicidad.\n\n"
        "SEGUNDO.– Conforme a los artículos 53, 63 y concordantes de la Ley 39/2015, el procedimiento sancionador exige motivación "
        "suficiente, prueba bastante y respeto efectivo del derecho de defensa.\n\n"
        "TERCERO.– Corresponde a la Administración acreditar los hechos constitutivos de la infracción mediante prueba suficiente, "
        "concreta e individualizada, no siendo admisibles presunciones genéricas o fórmulas estereotipadas.\n\n"
        "CUARTO.– La gravedad de una imputación por conducción temeraria o riesgo grave exige una motivación reforzada y una "
        "descripción fáctica completa de la maniobra y del riesgo generado.\n\n"
        "JURISPRUDENCIA APLICABLE\n\n"
        "La doctrina jurisprudencial consolidada exige actividad probatoria suficiente para enervar la presunción de inocencia, "
        "así como motivación individualizada y subsunción típica clara en todo procedimiento sancionador. La falta de prueba "
        "bastante, la indeterminación del hecho o la ausencia de motivación suficiente impiden sostener válidamente la sanción.\n\n"
    )

    parts.append(
        "S U P L I C A:\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que, en atención a las alegaciones presentadas y sus fundamentos, se acuerde el ARCHIVO DEL EXPEDIENTE por insuficiencia probatoria, falta de acreditación suficiente del hecho imputado o ausencia de motivación individualizada.\n"
        "3) Subsidiariamente, para el caso de no estimarse el archivo, que se proceda a una correcta recalificación jurídica de los hechos conforme a la prueba realmente acreditada en el expediente.\n"
        "4) Subsidiariamente, que se imponga en su caso la sanción mínima legalmente procedente dentro del tipo infractor que finalmente pudiera considerarse aplicable.\n"
        "5) Subsidiariamente, que se aporte expediente íntegro y prueba completa para contradicción efectiva.\n\n"
        "OTROSÍ DIGO\n"
        "Que esta parte se reserva expresamente el ejercicio de cuantos recursos administrativos y acciones legales pudieran corresponder en defensa de sus derechos e intereses legítimos.\n"
    )

    return {"asunto": asunto, "cuerpo": "".join(parts).strip()}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []

    checks = {
        "riesgo_concreto": ["riesgo vial real", "riesgo concreto", "individualizado"],
        "maniobra_exacta": ["maniobra exacta", "dinámica observada", "punto de la vía"],
        "observacion_agente": ["posición exacta", "distancia", "ángulo visual"],
        "proporcionalidad": ["proporcionalidad", "recalificación", "sanción mínima"],
        "archivo": ["archivo del expediente", "archivo"],
    }

    for key, tokens in checks.items():
        if not any(t in b for t in tokens):
            missing.append(key)

    out: List[str] = []
    seen = set()
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
