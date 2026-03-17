"""
RTM — ALCOHOL / ALCOHOLEMIA
Determinista, sin IA.
Salida: {"asunto","cuerpo"}

Objetivo:
- Activarse solo cuando la infracción sea realmente de alcohol/alcoholemia.
- Cubrir positivos en aire espirado, tasa superior a la permitida y conducción bajo influencia.
- Servir como módulo paralelo a casco.py / telefono.py / carril.py.
- Reforzar líneas de defensa típicas:
    * falta de concreción de resultados
    * ausencia de doble prueba o tiempos entre pruebas
    * falta de acreditación metrológica / calibración / verificación
    * falta de expediente íntegro y soportes documentales
"""

from __future__ import annotations
from typing import Any, Dict, List


def _safe_str(v: Any) -> str:
    return v if isinstance(v, str) else ""


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
        "subtipo_infraccion",
        "tipo_infraccion",
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

    blob = " ".join(parts).lower()
    blob = (
        blob.replace("alcoholemia", "alcoholemia")
            .replace("alcohólica", "alcoholica")
            .replace("alcohólicas", "alcoholicas")
            .replace("vehículo", "vehiculo")
            .replace("etilómetro", "etilometro")
            .replace("verificación", "verificacion")
            .replace("calibración", "calibracion")
            .replace("vía", "via")
    )
    return blob


def is_alcohol_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    b = _blob(core, body=body)

    tipo = _safe_str(core.get("tipo_infraccion")).lower().strip()
    if tipo in ("alcohol", "alcoholemia", "tasa_alcohol", "bajo_influencia"):
        return True

    strong_signals = [
        "alcohol",
        "alcoholemia",
        "resultado positivo",
        "positivo en prueba de alcoholemia",
        "positivo en control de alcohol",
        "test de alcohol",
        "prueba de alcohol",
        "prueba de alcoholemia",
        "tasa superior a la permitida",
        "tasa de alcohol",
        "bajo la influencia de bebidas alcoholicas",
        "bajo la influencia del alcohol",
        "etilometro",
        "aire espirado",
        "mg/l",
        "g/l",
    ]
    if any(tok in b for tok in strong_signals):
        return True

    combo_1 = ("tasa" in b and "permitida" in b)
    combo_2 = ("positivo" in b and ("alcohol" in b or "alcoholemia" in b))
    combo_3 = ("etilometro" in b and ("prueba" in b or "resultado" in b))
    combo_4 = ("aire espirado" in b and ("resultado" in b or "tasa" in b))

    return combo_1 or combo_2 or combo_3 or combo_4


def build_alcohol_strong_template(core: Dict[str, Any], body: str = "") -> Dict[str, str]:
    core = core or {}

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = (
        core.get("hecho_imputado")
        or core.get("hecho_denunciado_resumido")
        or "CONDUCIR CON TASA DE ALCOHOL SUPERIOR A LA PERMITIDA / RESULTADO POSITIVO EN PRUEBA DE ALCOHOLEMIA."
    )

    fecha = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha})" if isinstance(fecha, str) and fecha.strip() else ""

    tasa_1 = (
        core.get("tasa_1")
        or core.get("tasa_alcohol_1")
        or core.get("resultado_1")
        or core.get("resultado_prueba_1")
        or ""
    )
    tasa_2 = (
        core.get("tasa_2")
        or core.get("tasa_alcohol_2")
        or core.get("resultado_2")
        or core.get("resultado_prueba_2")
        or ""
    )

    tasa_txt = ""
    if isinstance(tasa_1, str) and tasa_1.strip():
        tasa_txt = f" Primer resultado consignado: {tasa_1.strip()}."
    if isinstance(tasa_2, str) and tasa_2.strip():
        tasa_txt += f" Segundo resultado consignado: {tasa_2.strip()}."

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n"
        f"4) Datos de tasa/resultados disponibles en el expediente aportado: {tasa_txt or 'No constan acreditados con claridad.'}\n\n"

        "II. ALEGACIONES\n\n"

        "ALEGACIÓN PRIMERA — NECESIDAD DE CONCRECIÓN SUFICIENTE DEL HECHO IMPUTADO\n\n"
        "La potestad sancionadora exige una descripción clara y precisa de la conducta atribuida, "
        "incluyendo, en su caso, el tipo de prueba practicada, los resultados obtenidos, la unidad empleada, "
        "las circunstancias temporales relevantes y la concreta subsunción en el tipo sancionador aplicado.\n\n"
        "Una redacción genérica o insuficientemente detallada impide el pleno ejercicio del derecho de defensa.\n\n"

        "ALEGACIÓN SEGUNDA — NECESIDAD DE APORTAR EL EXPEDIENTE ÍNTEGRO Y LA DOCUMENTACIÓN TÉCNICA DE LA PRUEBA\n\n"
        "Se solicita la aportación íntegra del expediente administrativo, incluyendo: "
        "boletín o denuncia completa, diligencias de práctica de la prueba, resultados consignados, "
        "identificación del etilómetro utilizado, documentación de verificación o control metrológico aplicable, "
        "y cualquier otro soporte documental necesario para comprobar la regularidad del procedimiento.\n\n"

        "ALEGACIÓN TERCERA — GARANTÍAS PROCEDIMENTALES DE LA PRUEBA\n\n"
        "Cuando la sanción descansa en una prueba de alcoholemia, la Administración debe poder acreditar de forma suficiente "
        "la regularidad de su práctica, incluyendo la identificación del dispositivo utilizado, "
        "la secuencia de pruebas realizadas y el cumplimiento de las exigencias procedimentales aplicables.\n\n"
        "Sin esa acreditación documental suficiente, la fuerza probatoria del expediente queda debilitada.\n\n"

        "ALEGACIÓN CUARTA — MOTIVACIÓN Y SUFICIENCIA PROBATORIA\n\n"
        "No basta la mera afirmación de un resultado positivo si el expediente no permite comprobar con precisión "
        "cómo se obtuvo, en qué condiciones se practicó la prueba y con qué soporte documental se respalda. "
        "La sanción requiere motivación individualizada y prueba bastante, no simples fórmulas estereotipadas.\n\n"

        "ALEGACIÓN QUINTA — SOLICITUD DE ARCHIVO O, SUBSIDIARIAMENTE, DE COMPLETAR EL EXPEDIENTE\n\n"
        "Si no se aporta soporte técnico y documental suficiente que permita verificar la regularidad, fiabilidad y motivación "
        "de la imputación, procede el archivo del expediente por insuficiencia probatoria. "
        "Subsidiariamente, debe completarse íntegramente el expediente antes de resolver.\n\n"

        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de motivación suficiente.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro con toda la documentación técnica y procedimental de la prueba para contradicción efectiva.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing: List[str] = []

    if "prueba" not in b and "etilometro" not in b and "tasa" not in b:
        missing.append("prueba_tecnica_alcohol")

    if "expediente" not in b and "documentacion" not in b and "documentación" not in b:
        missing.append("expediente_integro")

    if "motiv" not in b and "probator" not in b:
        missing.append("motivacion_prueba")

    if "archivo" not in b:
        missing.append("peticion_archivo")

    out: List[str] = []
    seen = set()
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
