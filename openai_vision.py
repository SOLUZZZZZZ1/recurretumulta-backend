import base64
import json
import os
from typing import Any, Dict, Optional

import requests

from rtm_core.ai_security import (
    consume_model_call_budget,
    protect_responses_payload,
    require_model_call_budget,
)
from rtm_core.runtime_capabilities import require_capability
from rtm_core.parser_isolation import run_image_parser_isolated
_MAIN_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "organismo": {"type": ["string", "null"]},
        "expediente_ref": {"type": ["string", "null"]},
        "importe": {"type": ["number", "null"]},
        "fecha_notificacion": {"type": ["string", "null"]},
        "fecha_documento": {"type": ["string", "null"]},
        "tipo_sancion": {"type": ["string", "null"]},
        "pone_fin_via_administrativa": {"type": ["boolean", "null"]},
        "plazo_recurso_sugerido": {"type": ["string", "null"]},
        "observaciones": {"type": "string", "maxLength": 1000},
        "vision_raw_text": {"type": "string", "maxLength": 4000},
    },
    "required": [
        "organismo",
        "expediente_ref",
        "importe",
        "fecha_notificacion",
        "fecha_documento",
        "tipo_sancion",
        "pone_fin_via_administrativa",
        "plazo_recurso_sugerido",
        "observaciones",
        "vision_raw_text",
    ],
}
_FOCUS_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "hecho_denunciado_focus": {"type": ["string", "null"], "maxLength": 1000},
        "hecho_denunciado_focus_es": {"type": ["string", "null"], "maxLength": 1000},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "ocr_quality": {"type": "string", "enum": ["good", "medium", "bad"]},
        "needs_operator_review": {"type": "boolean"},
        "notes": {"type": "string", "maxLength": 1000},
    },
    "required": [
        "hecho_denunciado_focus",
        "hecho_denunciado_focus_es",
        "confidence",
        "ocr_quality",
        "needs_operator_review",
        "notes",
    ],
}


def _env(name: str) -> str:
    v = (os.getenv(name) or "").strip()
    if not v:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return v


def _b64_data_url(mime: str, content: bytes) -> str:
    b64 = base64.b64encode(content).decode("utf-8")
    return f"data:{mime};base64,{b64}"



def _is_image_mime(mime: str) -> bool:
    return bool((mime or "").lower().startswith("image/"))


def _crop_transit_fet_denunciat_bytes(content: bytes, mime: str) -> tuple[bytes, str]:
    """
    Recorta visualmente la zona del campo 9. FET DENUNCIAT en boletines Trànsit.
    Si no puede recortar, devuelve la imagen original.
    """
    if not _is_image_mime(mime):
        return content, mime
    # Nunca se abre la estructura original en el proceso web. Si Pillow se
    # bloquea o excede CPU/RAM, el supervisor mata el worker y no se envía el
    # documento completo como degradación silenciosa.
    cropped, cropped_mime, _metadata = run_image_parser_isolated(
        "crop_fet_image",
        content,
        mime,
    )
    return cropped, cropped_mime



def extract_from_image_bytes(
    content: bytes,
    mime: str,
    filename: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Extracción desde IMAGEN usando OpenAI Responses API (visión).

    Devuelve un JSON estructurado + un OCR textual completo en 'vision_raw_text',
    para que el motor pueda extraer velocidades (123/90) incluso en PDFs escaneados.
    """
    require_capability("document_provider")
    require_model_call_budget()
    api_key = _env("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", "gpt-4o")

    data_url = _b64_data_url(mime, content)

    system_text = (
        "Eres un asistente experto en sanciones administrativas en España. "
        "Analizas imágenes de multas y extraes datos clave para preparar recursos administrativos. "
        "Devuelve siempre JSON válido."
    )

    user_text = (
        "Analiza la imagen de la sanción administrativa y devuelve EXCLUSIVAMENTE "
        "un objeto JSON válido con estas claves EXACTAS (incluye también 'vision_raw_text'):\n\n"
        "{\n"
        '  "organismo": string|null,\n'
        '  "expediente_ref": string|null,\n'
        '  "importe": number|null,\n'
        '  "fecha_notificacion": string|null,\n'
        '  "fecha_documento": string|null,\n'
        '  "tipo_sancion": string|null,\n'
        '  "pone_fin_via_administrativa": boolean|null,\n'
        '  "plazo_recurso_sugerido": string|null,\n'
        '  "observaciones": string,\n'
        '  "vision_raw_text": string\n'
        "}\n\n"
        "Reglas:\n"
        "- Si algún dato no se ve con claridad, usa null y explica el motivo en observaciones.\n"
        "- vision_raw_text debe ser una transcripción OCR lo más literal posible del documento (máx. ~4000 caracteres).\n"
        "- NO inventes texto que no se vea. Si hay zonas ilegibles, usa '[ILEGIBLE]'.\n"
    )

    payload = protect_responses_payload({
        "model": model,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_text}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": user_text},
                    {"type": "input_image", "image_url": data_url},
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "rtm_traffic_document_ocr",
                "strict": True,
                "schema": _MAIN_OUTPUT_SCHEMA,
            }
        },
        "max_output_tokens": 2048,
    })

    consume_model_call_budget()
    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=90,
        allow_redirects=False,
    )

    if not r.ok:
        raise RuntimeError(f"El proveedor OCR devolvió HTTP {r.status_code}")

    data = r.json()

    output_text = ""
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    output_text += c.get("text", "")

    if not output_text.strip():
        raise RuntimeError("OpenAI no devolvió contenido.")

    try:
        obj = json.loads(output_text)
    except Exception as e:
        raise RuntimeError(f"JSON inválido devuelto por el proveedor OCR: {type(e).__name__}")

    # Garantía: que siempre exista la clave
    if not isinstance(obj, dict):
        raise RuntimeError("El proveedor OCR no devolvió un objeto JSON")
    allowed = {
        "organismo",
        "expediente_ref",
        "importe",
        "fecha_notificacion",
        "fecha_documento",
        "tipo_sancion",
        "pone_fin_via_administrativa",
        "plazo_recurso_sugerido",
        "observaciones",
        "vision_raw_text",
    }
    result = {key: obj.get(key) for key in allowed}
    result["vision_raw_text"] = str(result.get("vision_raw_text") or "")[:4000]
    result["observaciones"] = str(result.get("observaciones") or "")[:1000]
    return result


def extract_fet_denunciat_focus(
    content: bytes,
    mime: str,
    filename: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Segunda pasada OCR focalizada para boletines del Servei Català de Trànsit.
    Lee únicamente el campo 'FET DENUNCIAT' y devuelve confianza.
    """
    require_capability("document_provider")
    require_model_call_budget()
    api_key = _env("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", "gpt-4o")
    focused_content, focused_mime = _crop_transit_fet_denunciat_bytes(content, mime)
    data_url = _b64_data_url(focused_mime, focused_content)

    system_text = (
        "Eres un OCR jurídico especializado en boletines de denuncia de tráfico de Cataluña. "
        "Lee solo el campo 'FET DENUNCIAT'. No inventes. Devuelve siempre JSON válido."
    )

    user_text = (
        "Devuelve EXCLUSIVAMENTE un JSON válido con estas claves EXACTAS:\n"
        "{\n"
        '  "hecho_denunciado_focus": string|null,\n'
        '  "hecho_denunciado_focus_es": string|null,\n'
        '  "confidence": number,\n'
        '  "ocr_quality": "good"|"medium"|"bad",\n'
        '  "needs_operator_review": boolean,\n'
        '  "notes": string\n'
        "}\n\n"
        "Instrucciones:\n"
        "1) La imagen que recibes debería ser un RECORTE del campo 9 / FET DENUNCIAT. Lee SOLO ese campo.\n"
        "2) Ignora cabeceras, RIN F5, emissora, dors, referencias, entidad, fechas, importes, puntos, DNI, permiso de conducir, población, denunciante, notificador y datos personales.\n"
        "3) Si el recorte NO contiene claramente el campo FET DENUNCIAT, devuelve hecho_denunciado_focus=null, confidence=0 y needs_operator_review=true.\n"
        "4) Transcribe de forma LITERAL. No completes frases por sentido jurídico.\n"
        "5) PROHIBIDO añadir consecuencias no leídas literalmente: colisión, accidente, choque, daños, lesionados, atropello, invasión de carril, riesgo concreto.\n"
        "6) Si una palabra no se ve clara, usa [ILEGIBLE]. No la sustituyas por una palabra probable.\n"
        "7) Si dudas entre dos lecturas, devuelve confidence menor de 0.70 y needs_operator_review=true.\n"
        "4) Mantén catalán si está en catalán.\n"
        "5) En hecho_denunciado_focus_es pon traducción/resumen jurídico prudente al castellano, sin inventar.\n"
        "6) confidence de 0 a 1. Si no puedes leer con seguridad: hecho_denunciado_focus=null, confidence<0.55 y needs_operator_review=true.\n"
        "7) No clasifiques como semáforo salvo que se lea claramente semàfor/llum vermella/fase roja.\n"
    )

    payload = protect_responses_payload({
        "model": model,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": user_text},
                    {"type": "input_image", "image_url": data_url},
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "rtm_fet_denunciat_focus",
                "strict": True,
                "schema": _FOCUS_OUTPUT_SCHEMA,
            }
        },
        "max_output_tokens": 1024,
    })

    consume_model_call_budget()
    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=90,
        allow_redirects=False,
    )

    if not r.ok:
        raise RuntimeError(f"El proveedor OCR focal devolvió HTTP {r.status_code}")

    data = r.json()
    output_text = ""
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    output_text += c.get("text", "")

    if not output_text.strip():
        raise RuntimeError("OpenAI focus OCR no devolvió contenido.")

    try:
        obj = json.loads(output_text)
    except Exception as e:
        raise RuntimeError(
            f"JSON inválido devuelto por el proveedor OCR focal: {type(e).__name__}"
        )

    if not isinstance(obj, dict):
        obj = {}

    try:
        conf = float(obj.get("confidence") or 0)
    except Exception:
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    obj["confidence"] = conf

    quality = str(obj.get("ocr_quality") or "").strip().lower()
    if quality not in ("good", "medium", "bad"):
        quality = "good" if conf >= 0.78 else "medium" if conf >= 0.55 else "bad"
    obj["ocr_quality"] = quality

    needs_review = obj.get("needs_operator_review") is not False
    if conf < 0.75 or quality == "bad":
        needs_review = True
    return {
        "hecho_denunciado_focus": (
            str(obj.get("hecho_denunciado_focus"))[:1000]
            if obj.get("hecho_denunciado_focus") is not None
            else None
        ),
        "hecho_denunciado_focus_es": (
            str(obj.get("hecho_denunciado_focus_es"))[:1000]
            if obj.get("hecho_denunciado_focus_es") is not None
            else None
        ),
        "confidence": conf,
        "ocr_quality": quality,
        "needs_operator_review": needs_review,
        "notes": str(obj.get("notes") or "")[:1000],
    }
