import json
import os
from typing import Any, Dict

from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


SYSTEM_PROMPT = """
Eres un asistente que extrae información estructurada de denuncias administrativas de tráfico en España.

Devuelve SIEMPRE un JSON válido con los siguientes campos.

Si un dato no aparece claramente en el documento, devuelve null.

Campos:

organismo: organismo que emite la denuncia
expediente_ref: número o referencia del expediente
importe: importe de la sanción en euros
fecha_notificacion: fecha de notificación si aparece
fecha_documento: fecha del documento
tipo_sancion: tipo de sanción si se menciona
pone_fin_via_administrativa: true/false si aparece
plazo_recurso_sugerido: plazo de recurso si aparece

hecho_denunciado_literal:
EXTRACTO literal del relato del agente describiendo la conducta.
Debe contener la descripción concreta (por ejemplo: "bailando en el interior del vehículo tocando las palmas...").
Si no existe relato claro, devolver null.

observaciones: cualquier otro dato relevante.
"""


def extract_from_text(text: str) -> Dict[str, Any]:

    prompt = f"""
Analiza el siguiente documento de denuncia administrativa y extrae los campos solicitados.

Documento:

{text}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )

    try:
        data = json.loads(response.choices[0].message.content)
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}

    return data