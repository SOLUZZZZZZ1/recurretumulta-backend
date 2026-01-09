import json
import os
from typing import Any, Dict

import requests


def _env(name: str) -> str:
    v = (os.getenv(name) or "").strip()
    if not v:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return v


def extract_from_text(text: str) -> Dict[str, Any]:
    api_key = _env("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", "gpt-4o")

    system = (
        "Eres un asistente experto en sanciones administrativas en España. "
        "Analiza textos de multas para extraer datos clave y preparar recursos."
    )

    user_text = (
        "Analiza el siguiente texto de una sanción/multa y devuelve SOLO un JSON válido "
        "con estas claves EXACTAS:\n"
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
        "}\n\n"
        "Texto:\n"
        f"{text[:12000]}"
    )

    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
        "response_format": {"type": "json_object"},
    }

    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )

    if not r.ok:
        raise RuntimeError(f"OpenAI error {r.status_code}: {r.text[:400]}")

    data = r.json()

    text_out = ""
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") in ("output_text", "text"):
                    text_out += c.get("text", "")

    if not text_out.strip():
        raise RuntimeError("OpenAI no devolvió contenido.")

    return json.loads(text_out)
