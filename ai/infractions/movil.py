
"""
RTM — MÓVIL STRONG MODULE (SVL‑MOV‑2)

Diseñado para denuncias presenciales (agente) y captación técnica.
Ataca tipicidad, descripción circunstanciada y motivación de no notificación.
No usa OpenAI. 100% determinista.
"""

from __future__ import annotations
import re
from typing import Dict, Any, List


def is_movil_context(core: Dict[str, Any], body: str = "") -> bool:
    blob = (body or "").lower()
    hecho = str((core or {}).get("hecho_imputado") or "").lower()
    return any(k in (blob + hecho) for k in [
        "teléfono", "telefono", "móvil", "movil",
        "utilizando manualmente", "en la mano"
    ])


def build_movil_strong_template(core: Dict[str, Any]) -> Dict[str, str]:
    expediente = (core or {}).get("expediente_ref") or "No consta acreditado."
    organo = (core or {}).get("organo") or (core or {}).get("organismo") or "No consta acreditado."
    hecho = (core or {}).get("hecho_imputado") or "USO MANUAL DEL TELÉFONO MÓVIL."

    asunto = "ESCRITO DE ALEGACIONES — SOLICITA ARCHIVO DEL EXPEDIENTE"

    cuerpo = f"""
A la atención del órgano competente,

I. ANTECEDENTES
1) Órgano: {organo}
2) Identificación expediente: {expediente}
3) Hecho imputado: {hecho}

II. ALEGACIONES

ALEGACIÓN PRIMERA — TIPICIDAD: USO MANUAL EFECTIVO

La infracción exige acreditar un USO MANUAL EFECTIVO del teléfono móvil incompatible con la conducción.
No basta la mera sujeción del dispositivo ni su presencia en la mano.
Debe acreditarse manipulación activa (marcar, escribir, interactuar con pantalla)
y su duración concreta.

No consta descripción específica de la acción realizada, duración, ni circunstancia concreta
que permita afirmar manipulación activa incompatible con la conducción.

ALEGACIÓN SEGUNDA — OBSERVACIÓN DESDE VEHÍCULO OFICIAL

La supuesta infracción habría sido observada desde vehículo camuflado en circulación.
Debe concretarse:

• Distancia aproximada de observación.
• Posición relativa de ambos vehículos.
• Condiciones de tráfico.
• Ángulo de visión y visibilidad real del interior del vehículo.
• Tiempo durante el cual se observó la conducta.

La ausencia de estos extremos impide verificar la fiabilidad perceptiva de la observación.

ALEGACIÓN TERCERA — NO NOTIFICACIÓN EN EL ACTO

La no notificación inmediata debe motivarse adecuadamente.
La mera referencia a falta de medios de seguimiento no suple el deber de motivación
cuando la infracción se habría observado directamente.

III. SOLICITO

1) Que se tengan por formuladas las presentes alegaciones.
2) Que se acuerde el ARCHIVO del expediente por insuficiencia probatoria y falta de acreditación suficiente del hecho.
3) Subsidiariamente, que se practique prueba y se aporte expediente íntegro.
""".strip()

    return {"asunto": asunto, "cuerpo": cuerpo}


def strict_missing(body: str) -> List[str]:
    b = (body or "").lower()
    missing = []
    if "uso manual" not in b:
        missing.append("tipicidad_uso_manual")
    if "distancia" not in b:
        missing.append("descripcion_circunstanciada")
    if "archivo" not in b:
        missing.append("solicito_archivo")
    return missing
