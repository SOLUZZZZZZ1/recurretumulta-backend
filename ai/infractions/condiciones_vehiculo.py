"""
RTM — CONDICIONES DEL VEHÍCULO (SVL-CV-2)

Familia: Art. 12 / Art. 15 RGC
Subtipos:
- Art. 15 → Alumbrado / señalización óptica
- Art. 12 → Condiciones reglamentarias generales / modificaciones

Determinista. Sin OpenAI.
"""

from __future__ import annotations
from typing import Any, Dict


# ==========================================================
# DISPATCH INTERNO
# ==========================================================

def build_condiciones_vehiculo_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}

    art = core.get("articulo_infringido_num")
    try:
        art = int(art)
    except Exception:
        art = None

    if art == 15:
        return _build_art15_alumbrado(core)
    else:
        # Si es 12 o no viene claro → enfoque general
        return _build_art12_condiciones(core)


# ==========================================================
# ARTÍCULO 15 — ALUMBRADO / SEÑALIZACIÓN
# ==========================================================

def _build_art15_alumbrado(core: Dict[str, Any]) -> Dict[str, str]:
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "INCUMPLIMIENTO DE DISPOSITIVOS DE ALUMBRADO O SEÑALIZACIÓN ÓPTICA."

    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = (
        "A la atención del órgano competente,\n\n"

        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"

        "II. ALEGACIONES\n\n"

        "ALEGACIÓN PRIMERA — DISPOSITIVOS DE ALUMBRADO (ART. 15): NECESIDAD DE PRUEBA TÉCNICA OBJETIVA\n\n"

        "La imputación relativa a dispositivos de alumbrado o señalización óptica no puede "
        "fundamentarse en apreciaciones genéricas o fórmulas estereotipadas. "
        "Debe acreditarse de forma objetiva y verificable:\n"
        "1) Qué dispositivo concreto se considera irregular (posición exacta y naturaleza del elemento).\n"
        "2) En qué consistía exactamente el supuesto incumplimiento (color, intensidad, modo de emisión, "
        "intermitencia o configuración técnica).\n"
        "3) Si el elemento era obligatorio, permitido o accesorio conforme a la normativa aplicable.\n"
        "4) Soporte objetivo (fotografías o vídeo nítidos) que permitan constatar el defecto.\n"
        "5) Descripción circunstanciada por parte del agente (distancia, visibilidad, condiciones de luz).\n\n"

        "No consta acreditación técnica suficiente que permita verificar el supuesto incumplimiento, "
        "por lo que no puede tenerse por probado el hecho infractor.\n\n"

        "ALEGACIÓN SEGUNDA — APLICACIÓN NORMATIVA Y MOTIVACIÓN\n\n"
        "Se solicita identificación expresa del precepto aplicado, anexo técnico invocado "
        "y motivación individualizada que justifique la subsunción del hecho descrito "
        "en la norma correspondiente.\n\n"

        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y soporte técnico acreditativo.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}


# ==========================================================
# ARTÍCULO 12 — CONDICIONES REGLAMENTARIAS GENERALES
# ==========================================================

def _build_art12_condiciones(core: Dict[str, Any]) -> Dict[str, str]:
    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "INCUMPLIMIENTO DE CONDICIONES REGLAMENTARIAS DEL VEHÍCULO."

    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = (
        "A la atención del órgano competente,\n\n"

        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"

        "II. ALEGACIONES\n\n"

        "ALEGACIÓN PRIMERA — CONDICIONES REGLAMENTARIAS (ART. 12): NECESIDAD DE ACREDITACIÓN TÉCNICA\n\n"

        "La imputación por presunto incumplimiento de condiciones reglamentarias "
        "exige prueba objetiva y concreta del defecto atribuido. "
        "Debe constar:\n"
        "1) Defecto específico detectado.\n"
        "2) Norma técnica concreta vulnerada.\n"
        "3) Medio de constatación empleado.\n"
        "4) Soporte verificable que permita contradicción.\n\n"

        "En ausencia de acreditación técnica suficiente y descripción detallada del defecto, "
        "no puede tenerse por probado el hecho infractor.\n\n"

        "ALEGACIÓN SEGUNDA — MOTIVACIÓN INDIVIDUALIZADA\n\n"
        "Se solicita identificación expresa del precepto aplicado y motivación completa "
        "que justifique la subsunción del hecho descrito en la norma invocada.\n\n"

        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria.\n"
        "3) Subsidiariamente, que se aporte expediente íntegro y soporte técnico acreditativo.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}