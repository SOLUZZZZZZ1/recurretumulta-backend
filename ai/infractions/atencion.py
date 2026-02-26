"""
RTM — TRÁFICO — CONDUCCIÓN NEGLIGENTE / ATENCIÓN PERMANENTE (SVL-ATN-2)

Objetivo:
- Plantilla determinista "con chicha" para Art. 3.1 RGC (conducción negligente) y casos de Art. 18 (atención/distracciones).
- Ataca: tipicidad + presunción de inocencia + concreción fáctica + riesgo real + fiabilidad perceptiva + proporcionalidad.
- No inventa hechos. Usa lenguaje prudente y solicita expediente íntegro.

Salida: {"asunto","cuerpo"} compatible con generate.py/dispatcher.
"""

from __future__ import annotations
from typing import Any, Dict, List
import re


def _blob(core: Dict[str, Any], body: str = "") -> str:
    core = core or {}
    parts: List[str] = []
    for k in ("raw_text_pdf", "raw_text_vision", "raw_text_blob", "hecho_imputado"):
        v = core.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    if isinstance(body, str) and body.strip():
        parts.append(body)
    return " ".join(parts).lower()


def is_atencion_context(core: Dict[str, Any], body: str = "") -> bool:
    core = core or {}
    b = _blob(core, body=body)

    # Señal estructural
    tipo = str(core.get("tipo_infraccion") or "").lower().strip()
    if tipo in ("atencion", "atención", "negligente", "conduccion_negligente", "conducción_negligente"):
        return True

    # Artículo explícito (3 o 18)
    art = core.get("articulo_infringido_num")
    try:
        art_i = int(art) if art is not None else None
    except Exception:
        art_i = None

    signals = [
        "conducción negligente", "conduccion negligente",
        "no mantener la atención permanente", "no mantener la atencion permanente",
        "atención permanente", "atencion permanente",
        "distracción", "distraccion",
        "conducción descuidada", "conduccion descuidada",
        "creando una situación de riesgo", "creando una situacion de riesgo",
    ]
    if art_i in (3, 18) and any(s in b for s in signals):
        return True
    return any(s in b for s in signals)


def build_atencion_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "CONDUCCIÓN NEGLIGENTE / FALTA DE ATENCIÓN PERMANENTE (RGC)."

    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""

    h_lower = str(hecho).lower()
    tramo_hint = ""
    if "km" in h_lower:
        tramo_hint = " Se indica un tramo/seguimiento en la denuncia; se exige su acreditación objetiva y método de determinación."

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        "ALEGACIÓN PRIMERA — PRESUNCIÓN DE INOCENCIA Y CARGA PROBATORIA\n\n"
        "En el procedimiento sancionador rige la presunción de inocencia y la carga de la prueba corresponde a la Administración. "
        "No es suficiente una descripción estereotipada o genérica: la imputación debe apoyarse en hechos concretos, verificables y motivación individualizada.\n\n"
        "ALEGACIÓN SEGUNDA — TIPICIDAD (ART. 3.1 RGC): EXIGENCIA DE RIESGO CONCRETO Y HECHO CIRCUNSTANCIADO\n\n"
        "La conducción negligente no se configura por meras valoraciones: exige una conducta concreta y una creación de riesgo real, "
        "no hipotético. Debe precisarse, como mínimo:\n"
        "1) Conducta exacta observada (qué se hacía exactamente) y por qué encaja en el tipo.\n"
        "2) En qué consistió el riesgo concreto (para quién, dónde y cómo), y su relación con la conducta descrita.\n"
        "3) Circunstancias del tráfico y visibilidad, y por qué impedían/agravaban la supuesta conducta.\n"
        "4) Motivación individualizada (no fórmulas genéricas) que permita contradicción efectiva.\n"
        f"{tramo_hint}\n\n"
        "En ausencia de concreción fáctica suficiente y riesgo objetivable, no puede tenerse por acreditada la infracción.\n\n"
        "ALEGACIÓN TERCERA — FIABILIDAD DE LA OBSERVACIÓN Y SEGUIMIENTO (SI SE INVOCA)\n\n"
        "Si la imputación se basa en observación presencial y/o seguimiento, debe detallarse con precisión:\n"
        "1) Posición del agente y condiciones de observación (distancia, ángulo, iluminación, tráfico, obstáculos).\n"
        "2) Continuidad de la observación (si se afirma un tramo, cómo se determinó y por qué no se intervino antes).\n"
        "3) Elementos objetivos de corroboración (anotaciones, testigos, grabación, medios técnicos), si existieran.\n\n"
        "La falta de estos extremos impide verificar la fiabilidad perceptiva y vulnera el derecho de defensa.\n\n"
        "ALEGACIÓN CUARTA — EXPEDIENTE ÍNTEGRO, PRECEPTO APLICADO Y MOTIVACIÓN\n\n"
        "Se solicita copia íntegra del expediente (denuncia/boletín completo, informe ampliatorio si existe, diligencias, propuesta y resolución si existieran), "
        "con identificación expresa del precepto aplicado (artículo/apartado) y motivación completa de la subsunción.\n\n"
        "ALEGACIÓN QUINTA — REFERENCIAS ACCESORIAS (p. ej. ocupantes/menor)\n\n"
        "La eventual mención a ocupantes o a un menor no suple la prueba de la conducta imputada ni convierte por sí misma el hecho en negligencia. "
        "Si se pretende agravar o fundamentar el tipo en esa circunstancia, debe explicarse la relación causal con el riesgo y el encaje normativo específico.\n\n"
        "ALEGACIÓN SEXTA — PROPORCIONALIDAD\n\n"
        "Aun en hipótesis de infracción, procede valorar proporcionalidad y circunstancias concretas. "
        "Sin acreditación técnica y motivación individualizada, no procede sanción.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria, falta de concreción y ausencia de motivación individualizada.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y cualquier soporte probatorio completo que permita contradicción efectiva.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}
