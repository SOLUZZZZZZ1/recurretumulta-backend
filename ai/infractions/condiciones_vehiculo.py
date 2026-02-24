"""
RTM — CONDICIONES DEL VEHÍCULO (SVL-CV-1)
Incluye alumbrado/señalización óptica (art. 15) y condiciones reglamentarias (art. 12 / RD 2822/98).

Determinista, sin OpenAI.
Objetivo: exigir prueba técnica objetiva (informe, medición, fotografías), evitar apreciación subjetiva,
y pedir ARCHIVO si no hay soporte verificable.
"""

from __future__ import annotations
from typing import Any, Dict


def build_condiciones_vehiculo_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    core = core or {}

    expediente = core.get("expediente_ref") or core.get("numero_expediente") or "No consta acreditado."
    organo = core.get("organo") or core.get("organismo") or "No consta acreditado."
    hecho = core.get("hecho_imputado") or "INCUMPLIMIENTO DE CONDICIONES REGLAMENTARIAS DEL VEHÍCULO."

    fecha_hecho = core.get("fecha_infraccion") or core.get("fecha_hecho") or core.get("fecha_documento") or ""
    fecha_line = f" (fecha indicada: {fecha_hecho})" if isinstance(fecha_hecho, str) and fecha_hecho.strip() else ""

    # Si viene artículo 15, lo hacemos específico
    art = core.get("articulo_infringido_num")
    apt = core.get("apartado_infringido_num")
    is_art15 = str(art).strip() == "15" or art == 15

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    if is_art15:
        titulo = "ALEGACIÓN PRIMERA — ALUMBRADO/SEÑALIZACIÓN ÓPTICA (ART. 15): PRUEBA TÉCNICA OBJETIVA"
        enfoque = (
            "En infracciones relativas a dispositivos de alumbrado o señalización óptica, "
            "la imputación no puede basarse en fórmulas estereotipadas o apreciaciones genéricas: "
            "debe existir soporte técnico y objetivo (fotografías claras, acta detallada, informe técnico si procede) "
            "que permita verificar el supuesto defecto y su entidad.\n\n"
            "No consta acreditado en el expediente, de forma verificable:\n"
            "1) Qué dispositivo concreto se considera irregular (ubicación exacta, tipo de luz, configuración).\n"
            "2) En qué consistía exactamente el incumplimiento (p. ej., color, intensidad, modo de emisión, intermitencia), "
            "y si se trataba de un elemento permitido/obligatorio según el caso.\n"
            "3) Soporte objetivo (fotografías o vídeo) que permita constatar el hecho en condiciones de visibilidad adecuadas.\n"
            "4) Acta/denuncia íntegra con descripción circunstanciada (distancia, ángulo de observación, condiciones de iluminación).\n"
            "5) Base normativa concreta aplicada (anexo/reglamentación invocada) y motivación individualizada.\n\n"
            "En ausencia de prueba técnica objetiva y descripción circunstanciada suficiente, "
            "no puede tenerse por probado el hecho infractor, procediendo el ARCHIVO del expediente por insuficiencia probatoria.\n"
        )
    else:
        titulo = "ALEGACIÓN PRIMERA — CONDICIONES REGLAMENTARIAS: NECESIDAD DE PRUEBA TÉCNICA OBJETIVA"
        enfoque = (
            "En infracciones por presunto incumplimiento de condiciones reglamentarias del vehículo, "
            "la Administración debe aportar soporte objetivo y verificable que permita constatar el defecto concreto "
            "y su entidad, evitando fórmulas genéricas.\n\n"
            "No consta acreditado en el expediente:\n"
            "1) Defecto concreto imputado y norma técnica aplicable.\n"
            "2) Medio de constatación y descripción circunstanciada.\n"
            "3) Soporte objetivo (fotografías, medición, informe) y posibilidad de contradicción.\n\n"
            "En ausencia de acreditación suficiente, procede el ARCHIVO.\n"
        )

    cuerpo = (
        "A la atención del órgano competente,\n\n"
        "I. ANTECEDENTES\n"
        f"1) Órgano: {organo}\n"
        f"2) Identificación expediente: {expediente}\n"
        f"3) Hecho imputado: {hecho}{fecha_line}\n\n"
        "II. ALEGACIONES\n\n"
        f"{titulo}\n\n"
        f"{enfoque}\n"
        "ALEGACIÓN SEGUNDA — EXPEDIENTE ÍNTEGRO Y MOTIVACIÓN\n\n"
        "Se solicita la aportación del expediente íntegro (boletín/denuncia completo, soportes fotográficos o de vídeo, "
        "y fundamentos), con identificación expresa del precepto aplicado (artículo/apartado) y motivación completa.\n\n"
        "III. SOLICITO\n"
        "1) Que se tengan por formuladas las presentes alegaciones.\n"
        "2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación técnica objetiva.\n"
        "3) Subsidiariamente, que se aporte el expediente íntegro y se practique prueba.\n"
    ).strip()

    return {"asunto": asunto, "cuerpo": cuerpo}