import base64
import json
import os
import io
from typing import Any, Dict, Optional

import requests

try:
    from PIL import Image, ImageEnhance
except Exception:
    Image = None
    ImageEnhance = None


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
    if not _is_image_mime(mime) or Image is None:
        return content, mime

    try:
        img = Image.open(io.BytesIO(content)).convert("RGB")
        w, h = img.size
        if w <= 0 or h <= 0:
            return content, mime

        if w >= h:
            left = int(w * 0.28)
            top = int(h * 0.34)
            right = int(w * 0.73)
            bottom = int(h * 0.57)
        else:
            left = int(w * 0.10)
            top = int(h * 0.26)
            right = int(w * 0.78)
            bottom = int(h * 0.58)

        left = max(0, min(left, w - 1))
        top = max(0, min(top, h - 1))
        right = max(left + 10, min(right, w))
        bottom = max(top + 10, min(bottom, h))

        crop = img.crop((left, top, right, bottom))

        try:
            crop = ImageEnhance.Contrast(crop).enhance(1.45)
            crop = ImageEnhance.Sharpness(crop).enhance(1.25)
        except Exception:
            pass

        buf = io.BytesIO()
        crop.save(buf, format="JPEG", quality=92)
        return buf.getvalue(), "image/jpeg"

    except Exception:
        return content, mime



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

    payload = {
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
        "text": {"format": {"type": "json_object"}},
    }

    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=90,
    )

    if not r.ok:
        raise RuntimeError(f"OpenAI error {r.status_code}: {r.text[:500]}")

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
        raise RuntimeError(f"JSON inválido devuelto por OpenAI: {e}. Texto: {output_text[:400]}")

    # Garantía: que siempre exista la clave
    if isinstance(obj, dict) and "vision_raw_text" not in obj:
        obj["vision_raw_text"] = ""

    return obj


def extract_fet_denunciat_focus(
    content: bytes,
    mime: str,
    filename: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Segunda pasada OCR focalizada para boletines del Servei Català de Trànsit.
    Lee únicamente el campo 'FET DENUNCIAT' y devuelve confianza.
    """
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

    payload = {
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
        "text": {"format": {"type": "json_object"}},
    }

    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=90,
    )

    if not r.ok:
        raise RuntimeError(f"OpenAI focus OCR error {r.status_code}: {r.text[:500]}")

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
        raise RuntimeError(f"JSON inválido devuelto por OpenAI focus OCR: {e}. Texto: {output_text[:400]}")

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

    obj.setdefault("needs_operator_review", bool(conf < 0.75 or quality == "bad"))
    obj.setdefault("hecho_denunciado_focus", None)
    obj.setdefault("hecho_denunciado_focus_es", None)
    obj.setdefault("notes", "")
    return obj
