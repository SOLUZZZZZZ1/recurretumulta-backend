import base64
import json
import os
from typing import Any, Dict, Optional

import requests


def _env(name: str) -> str:
    v = (os.getenv(name) or "").strip()
    if not v:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return v


def _optional(name: str, default: str) -> str:
    v = (os.getenv(name) or "").strip()
    return v or default


def _b64_data_url(mime: str, content: bytes) -> str:
    b64 = base64.b64encode(content).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def extract_from_image_bytes(
    content: bytes,
    mime: str,
    filename: Optional[str] = None,
) -> Dict[str, Any]:
    api_key = _env("OPENAI_API_KEY")
    model = _optional("OPENAI_MODEL", "gpt-4o")

    data_url = _b64_data_url(mime, content)

    system = (
        "Eres un asistente especializado en analizar sanciones administrativas en España. "
        "Tu objetivo es extraer datos clave del documento para preparar recursos administrativos."
    )

    user_text = (
        "Analiza el documento (multa/sanción). Devuelve SOLO un objeto JSON válido con estas claves EXACTAS:\n"
        "{\n"
        '  "organismo": string|null,\n'
        '  "expediente_ref": string|null,\n'
        '  "importe": number|null,\n'
        '  "fecha_notificacion": string|null,\n'
        '  "fecha_documento": string|null,\n'
        '  "tipo_sancion": string|null,\n'
        '  "pone_fin_via_administrativa": boolean|null,\n'
        '  "plazo_recurso_sugerido": string|null,\n'
        '  "observaciones": string\n'
        "}\n"
        "Si algún dato no se ve con claridad, pon null y explica en observaciones."
    )

    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": [{"type": "text", "text": system}]},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "input_image", "image_url": data_url},
                ],
            },
        ],
        "response_format": {"type": "json_object"},
    }

    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"OpenAI error {r.status_code}: {r.text[:500]}")

    data = r.json()

    text_out = ""
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") in ("output_text", "text"):
                    text_out += c.get("text", "")

    if not text_out.strip():
        raise RuntimeError("OpenAI no devolvió contenido.")

    try:
        return json.loads(text_out)
    except Exception as e:
        raise RuntimeError(f"OpenAI devolvió JSON inválido: {e}. Texto: {text_out[:400]}")
