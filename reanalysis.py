from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

import requests
from fastapi import HTTPException
from sqlalchemy import text

from database import get_engine
from b2_storage import download_bytes
from analyze import (
    analyze_existing_case_document,
    _enrich_with_triage,
    _flatten_text,
    _merge_extracted,
)

try:
    from PIL import Image, ImageOps, ImageEnhance
except Exception:  # pragma: no cover
    Image = None
    ImageOps = None


_ENGINE_NAME = "rtm_intelligence_core_v1"
_EXTRACTOR_VERSION = "traffic_fine_reanalysis_v1_9"
_SECONDARY_FACTS_VERSION = "velocity_secondary_v1_0"
_TRAFFIC_FINE_TYPES = {"fine", "multa", "multas", "sanction", "sancion", "sanción"}


def _append_event(case_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO events(case_id, type, payload, created_at) "
                "VALUES (:case_id, :type, CAST(:payload AS JSONB), NOW())"
            ),
            {
                "case_id": case_id,
                "type": event_type,
                "payload": json.dumps(payload or {}, ensure_ascii=False),
            },
        )


def _case_meta(case_id: str) -> Dict[str, str]:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT COALESCE(department,''), COALESCE(case_type,''), "
                "COALESCE(status,''), COALESCE(payment_status,'') "
                "FROM cases WHERE id=:id"
            ),
            {"id": case_id},
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")

    return {
        "department": str(row[0] or "").strip().lower(),
        "case_type": str(row[1] or "").strip().lower(),
        "status": str(row[2] or "").strip(),
        "payment_status": str(row[3] or "").strip(),
    }


def _load_original_documents(case_id: str) -> List[Dict[str, Any]]:
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, b2_bucket, b2_key, COALESCE(mime,''), "
                "COALESCE(size_bytes,0), created_at "
                "FROM documents "
                "WHERE case_id=:id AND kind='original' "
                "AND b2_bucket IS NOT NULL AND b2_key IS NOT NULL "
                "ORDER BY created_at ASC, id ASC"
            ),
            {"id": case_id},
        ).fetchall()

    return [
        {
            "id": str(r[0]),
            "bucket": r[1],
            "key": r[2],
            "mime": str(r[3] or ""),
            "size_bytes": int(r[4] or 0),
            "created_at": str(r[5]),
        }
        for r in rows
    ]


def _sniff_mime(content: bytes, declared_mime: str = "", key: str = "") -> str:
    head = content[:32]
    declared = (declared_mime or "").strip().lower()
    key_l = (key or "").lower()

    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"

    if head.startswith(b"PK\x03\x04") and (
        key_l.endswith(".docx")
        or declared == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    if declared and declared != "application/octet-stream":
        return declared

    if Image is not None:
        try:
            with Image.open(io.BytesIO(content)) as img:
                fmt = str(img.format or "").upper()
            return {
                "JPEG": "image/jpeg",
                "JPG": "image/jpeg",
                "PNG": "image/png",
                "TIFF": "image/tiff",
                "TIF": "image/tiff",
                "WEBP": "image/webp",
            }.get(fmt, declared or "application/octet-stream")
        except Exception:
            pass

    return declared or "application/octet-stream"


def _filename_for_mime(index: int, key: str, mime: str) -> str:
    base = os.path.basename(key or "") or f"pagina_{index}"
    ext_map = {
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/tiff": ".tif",
        "image/webp": ".webp",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    }
    wanted = ext_map.get(mime, "")
    _, ext = os.path.splitext(base)
    if not ext or ext.lower() == ".bin":
        return f"pagina_{index}{wanted}"
    return base


def _jpeg_bytes(img) -> bytes:
    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=95, optimize=True)
    return out.getvalue()


def _data_url_jpeg(content: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(content).decode("ascii")


def _rotate_document_image(img, rotation: int):
    if rotation == 90:
        return img.rotate(270, expand=True)
    if rotation == 180:
        return img.rotate(180, expand=True)
    if rotation == -90:
        return img.rotate(90, expand=True)
    return img


def _orientation_preview(img, max_dim: int = 900):
    preview = img.copy()
    try:
        preview.thumbnail((max_dim, max_dim))
    except Exception:
        pass
    return preview


def _choose_upright_document_image(img) -> Tuple[Any, Dict[str, Any]]:
    """Selecciona orientación sin mantener varias copias grandes en RAM.

    V1.7:
    - crea UNA miniatura de orientación;
    - rota únicamente esa miniatura para preguntar al selector;
    - aplica UNA sola rotación final a la imagen completa.
    """
    if img.width > img.height:
        specs = [("A", 0), ("B", 90), ("C", 180), ("D", -90)]
        fallback_rotation = 90
    else:
        specs = [("A", 0), ("B", 180)]
        fallback_rotation = 0

    meta: Dict[str, Any] = {
        "rotation_applied": fallback_rotation,
        "orientation_selector": "fallback",
        "orientation_confidence": 0.0,
        "orientation_preview_max_dim": 900,
    }

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return _rotate_document_image(img, fallback_rotation), meta

    preview = _orientation_preview(img, 900)
    try:
        model = (os.getenv("OPENAI_MODEL") or "gpt-4o").strip()
        content_parts: List[Dict[str, Any]] = [{
            "type": "input_text",
            "text": (
                "Las imágenes siguientes son rotaciones de la MISMA página. "
                "Elige la que esté completamente derecha: cabecera arriba, texto horizontal "
                "y lectura natural de izquierda a derecha y de arriba abajo. "
                "Devuelve solo JSON con upright y confidence."
            ),
        }]

        for label, rotation in specs:
            candidate_preview = _rotate_document_image(preview, rotation)
            content_parts.append({"type": "input_text", "text": f"Versión {label}:"})
            content_parts.append({
                "type": "input_image",
                "image_url": _data_url_jpeg(_jpeg_bytes(candidate_preview)),
            })
            del candidate_preview

        payload = {
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": [{
                        "type": "input_text",
                        "text": (
                            "Eres un selector de orientación documental. No extraigas datos ni interpretes "
                            "el contenido; solo decide qué rotación deja la página derecha."
                        ),
                    }],
                },
                {"role": "user", "content": content_parts},
            ],
            "text": {"format": {"type": "json_object"}},
        }

        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )

        if r.ok:
            obj = json.loads(_response_output_text(r.json()) or "{}")
            choice = str(obj.get("upright") or "").strip().upper()
            try:
                confidence = float(obj.get("confidence") or 0)
            except Exception:
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))

            for label, rotation in specs:
                if choice == label:
                    return _rotate_document_image(img, rotation), {
                        "rotation_applied": rotation,
                        "orientation_selector": "openai_preview",
                        "orientation_confidence": confidence,
                        "orientation_preview_max_dim": 900,
                    }
    except Exception as exc:
        meta["orientation_selector_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            del preview
        except Exception:
            pass

    return _rotate_document_image(img, fallback_rotation), meta

def _normalize_image_for_analysis(content: bytes, mime: str) -> Tuple[bytes, str, Dict[str, Any]]:
    """Crea una copia JPEG para análisis, sin modificar el original de B2.

    - corrige EXIF;
    - convierte TIFF/PNG/WebP a JPEG;
    - corrige páginas giradas 90° y páginas verticales invertidas 180° antes del OCR.
    """
    meta: Dict[str, Any] = {"rotation_applied": 0, "orientation_selector": "none"}
    if not (mime or "").startswith("image/"):
        return content, mime, meta
    if Image is None:
        return content, mime, meta

    try:
        with Image.open(io.BytesIO(content)) as source:
            img = source.copy()

        if ImageOps is not None:
            img = ImageOps.exif_transpose(img)

        if getattr(img, "n_frames", 1) > 1:
            try:
                img.seek(0)
            except Exception:
                pass

        img = img.convert("RGB")
        img, orientation_meta = _choose_upright_document_image(img)
        meta.update(orientation_meta)

        meta["source_oriented_width"] = int(img.width)
        meta["source_oriented_height"] = int(img.height)

        # 2.600 px es suficiente para OCR/visión documental y evita conservar
        # fotografías de móvil de 10-20 MP durante todas las pasadas.
        max_analysis_dim = 2600
        if max(img.width, img.height) > max_analysis_dim:
            img.thumbnail((max_analysis_dim, max_analysis_dim))
            meta["analysis_resized"] = True
        else:
            meta["analysis_resized"] = False

        meta["normalized_width"] = int(img.width)
        meta["normalized_height"] = int(img.height)
        meta["analysis_max_dim"] = max_analysis_dim
        return _jpeg_bytes(img), "image/jpeg", meta
    except Exception as exc:
        meta["normalization_error"] = f"{type(exc).__name__}: {exc}"
        return content, mime, meta


def _page_text(core: Dict[str, Any]) -> str:
    for key in ("raw_text_blob", "vision_raw_text", "raw_text_vision", "raw_text_pdf"):
        value = core.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return _flatten_text(core or {}, text_content="")


def _fold(text_value: str) -> str:
    s = unicodedata.normalize("NFKD", str(text_value or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("’", "'").replace("`", "'")
    s = re.sub(r"[\t\r]+", " ", s)
    s = re.sub(r"[ ]{2,}", " ", s)
    return s


def _first_match(patterns: List[str], text_value: str, flags: int = re.I | re.S) -> Optional[str]:
    for pattern in patterns:
        m = re.search(pattern, text_value, flags)
        if m:
            value = str(m.group(1) or "").strip(" \t\r\n:;,.—–-")
            if value:
                return value
    return None


def _response_output_text(data: Dict[str, Any]) -> str:
    output_text = ""
    for item in (data or {}).get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "output_text":
                output_text += str(part.get("text") or "")
    return output_text.strip()


def _normalise_plate(value: Any) -> Optional[str]:
    raw = re.sub(r"[^A-Z0-9]", "", _fold(str(value or "")).upper())
    m = re.fullmatch(r"(\d{4})([A-Z]{3})", raw)
    if not m:
        return None
    return f"{m.group(1)} {m.group(2)}"


def _normalise_date(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    m = re.search(r"\b(\d{2})[-/](\d{2})[-/](\d{4})\b", raw)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _normalise_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return float(value)
        raw = str(value).replace("€", "").replace("EUR", "").replace(" ", "").replace(",", ".")
        m = re.search(r"\d+(?:\.\d+)?", raw)
        return float(m.group(0)) if m else None
    except Exception:
        return None


def _normalise_int(value: Any, min_value: int = 0, max_value: int = 999999) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        m = re.search(r"\d+", str(value))
        if not m:
            return None
        n = int(m.group(0))
        return n if min_value <= n <= max_value else None
    except Exception:
        return None


def _critical_fields_from_images(analyzed_pages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Segunda lectura visual, focalizada SOLO en campos críticos.

    No interpreta jurídicamente la multa y no infiere datos: transcribe etiquetas y
    valores visibles. Se usa para corregir errores OCR típicos (p. ej. letras de la
    matrícula, fechas próximas entre sí o identificadores de antena).
    """
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return {"values": {}, "confidence": {}, "evidence": {}, "error": "OPENAI_API_KEY_missing"}

    image_parts: List[Dict[str, Any]] = []
    page_labels: List[str] = []
    for page in analyzed_pages:
        content = page.get("analysis_content")
        mime = str(page.get("analysis_mime") or "")
        if not isinstance(content, (bytes, bytearray)) or not mime.startswith("image/"):
            continue
        # La normalización previa convierte imágenes a JPEG vertical, así evitamos
        # depender de EXIF o del nombre/extensión original.
        if mime != "image/jpeg" and Image is not None:
            try:
                with Image.open(io.BytesIO(bytes(content))) as img:
                    content = _jpeg_bytes(img.convert("RGB"))
                mime = "image/jpeg"
            except Exception:
                pass
        page_index = int(page.get("page_index") or 0)
        page_labels.append(f"Página lógica {page_index}")
        image_parts.append({"type": "input_text", "text": f"Página lógica {page_index}:"})
        image_parts.append({
            "type": "input_image",
            "image_url": f"data:{mime};base64," + base64.b64encode(bytes(content)).decode("ascii"),
        })

    if not image_parts:
        return {"values": {}, "confidence": {}, "evidence": {}, "error": "no_image_pages"}

    fields = [
        "organismo", "expediente_ref", "matricula", "velocidad_medida_kmh",
        "velocidad_limite_kmh", "radar_modelo_hint", "radar_antena",
        "sancion_importe_eur", "puntos_detraccion", "lugar_infraccion",
        "fecha_infraccion", "hora_infraccion",
    ]
    schema_hint = ", ".join(fields)
    system_text = (
        "Eres un lector documental de alta precisión para sanciones de tráfico en España. "
        "Tu única tarea es TRANSCRIBIR campos críticos visibles en las imágenes. "
        "No interpretes el Derecho, no calcules, no completes por contexto y no confundas fechas. "
        "Si un valor no es claramente legible, devuelve null. "
        "Distingue especialmente: fecha de la infracción frente a fecha de notificación o verificación; "
        "número de expediente frente a número de envío; importe total de sanción frente a importe reducido; "
        "matrícula frente a códigos/barcodes."
    )
    user_text = f"""
Lee conjuntamente todas las páginas y devuelve EXCLUSIVAMENTE un JSON válido con esta forma:
{{
  "values": {{
    "organismo": string|null,
    "expediente_ref": string|null,
    "matricula": string|null,
    "velocidad_medida_kmh": integer|null,
    "velocidad_limite_kmh": integer|null,
    "radar_modelo_hint": string|null,
    "radar_antena": string|null,
    "sancion_importe_eur": number|null,
    "puntos_detraccion": integer|null,
    "lugar_infraccion": string|null,
    "fecha_infraccion": string|null,
    "hora_infraccion": string|null
  }},
  "confidence": {{"{fields[0]}": 0.0}},
  "evidence": {{"{fields[0]}": "texto literal breve"}}
}}

Incluye en confidence y evidence una entrada para cada campo no nulo de values.
Campos: {schema_hint}.
Reglas críticas:
- matrícula: transcribe exactamente los 4 dígitos y 3 letras del vehículo; verifica visualmente cada letra.
- fecha_infraccion: SOLO la fecha del hecho dentro del bloque de datos básicos de la infracción; no uses fecha de notificación, emisión, pago, certificado o verificación.
- lugar_infraccion: incluye carretera/vía y punto kilométrico si ambos son legibles.
- radar_antena: número de antena del cinemómetro, no número de serie, código de barras o expediente.
- sancion_importe_eur: importe total/propuesto de la sanción, no pago reducido.
- velocidad_medida_kmh y velocidad_limite_kmh: solo valores expresamente escritos en el hecho o tabla.
- expediente_ref: número del expediente sancionador, no número de envío.
- confidence: 0..1 según legibilidad visual real.
- evidence: máximo 140 caracteres, copiando la etiqueta/fragmento donde se ve el dato.
- No inventes. Si dudas entre dos caracteres, usa null para ese campo.
"""

    payload = {
        "model": (os.getenv("OPENAI_MODEL") or "gpt-4o").strip(),
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_text}] + image_parts},
        ],
        "text": {"format": {"type": "json_object"}},
    }

    try:
        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=90,
        )
        if not r.ok:
            return {"values": {}, "confidence": {}, "evidence": {}, "error": f"OpenAI {r.status_code}: {r.text[:300]}"}
        obj = json.loads(_response_output_text(r.json()) or "{}")
        values = obj.get("values") if isinstance(obj, dict) else {}
        confidence = obj.get("confidence") if isinstance(obj, dict) else {}
        evidence = obj.get("evidence") if isinstance(obj, dict) else {}
        if not isinstance(values, dict):
            values = {}
        if not isinstance(confidence, dict):
            confidence = {}
        if not isinstance(evidence, dict):
            evidence = {}

        cleaned: Dict[str, Any] = {}
        for key in fields:
            value = values.get(key)
            if value in (None, "", "null"):
                continue
            if key == "matricula":
                value = _normalise_plate(value)
            elif key == "fecha_infraccion":
                value = _normalise_date(value)
            elif key in ("velocidad_medida_kmh", "velocidad_limite_kmh"):
                value = _normalise_int(value, 10, 250)
            elif key == "puntos_detraccion":
                value = _normalise_int(value, 0, 15)
            elif key == "sancion_importe_eur":
                value = _normalise_float(value)
            elif key == "radar_antena":
                m = re.search(r"\d{3,10}", str(value))
                value = m.group(0) if m else None
            else:
                value = str(value).strip()
            if value not in (None, ""):
                cleaned[key] = value

        conf_clean: Dict[str, float] = {}
        for key in cleaned:
            try:
                c = float(confidence.get(key) or 0)
            except Exception:
                c = 0.0
            conf_clean[key] = max(0.0, min(1.0, c))

        ev_clean = {k: str(evidence.get(k) or "")[:180] for k in cleaned if evidence.get(k)}
        return {
            "values": cleaned,
            "confidence": conf_clean,
            "evidence": ev_clean,
            "pages": page_labels,
        }
    except Exception as exc:
        return {
            "values": {}, "confidence": {}, "evidence": {},
            "error": f"{type(exc).__name__}: {exc}",
        }


def _enhanced_crop_bytes(img, box: Tuple[float, float, float, float], min_width: int = 1800) -> bytes:
    """Recorta por proporciones y amplía la zona para lectura carácter a carácter."""
    w, h = img.size
    left = max(0, min(w - 1, int(w * box[0])))
    top = max(0, min(h - 1, int(h * box[1])))
    right = max(left + 10, min(w, int(w * box[2])))
    bottom = max(top + 10, min(h, int(h * box[3])))
    crop = img.crop((left, top, right, bottom)).convert("RGB")

    if crop.width < min_width:
        scale = min(3.0, max(1.0, float(min_width) / max(1, crop.width)))
        crop = crop.resize((int(crop.width * scale), int(crop.height * scale)))

    try:
        if ImageOps is not None:
            crop = ImageOps.autocontrast(crop)
        if ImageEnhance is not None:
            crop = ImageEnhance.Contrast(crop).enhance(1.12)
            crop = ImageEnhance.Sharpness(crop).enhance(1.65)
    except Exception:
        pass
    return _jpeg_bytes(crop)


def _valid_sct_expediente(value: Any) -> bool:
    raw = str(value or "").strip().replace(" ", "")
    return bool(re.fullmatch(r"\d{2}/\d{8}-\d", raw))


def _critical_fields_from_zoomed_crops(analyzed_pages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Tercera lectura visual de precisión, orientada a plantillas SCT.

    V1.5 no confía en la orientación previa para los campos de identidad.
    Para cada página crea variantes 0º/180º (y 90º/270º si hiciera falta) y
    recortes mucho más estrechos de:
      - cabecera / NÚMERO D'EXPEDIENT;
      - Dades bàsiques de la infracció;
      - fila Matrícula;
      - fila final Número d'expedient / Import / Punts.

    El objetivo no es interpretar: solo transcribir caracteres inequívocos.
    Si la lectura de matrícula o expediente sigue siendo dudosa, devuelve null
    y el expediente queda bloqueado para Generate.
    """
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key or Image is None:
        return {"values": {}, "confidence": {}, "evidence": {}, "error": "zoom_unavailable"}

    model = (os.getenv("OPENAI_MODEL") or "gpt-4o").strip()
    page_candidates: List[Dict[str, Any]] = []
    errors: List[str] = []
    crop_count = 0

    fields = [
        "expediente_ref", "matricula", "velocidad_medida_kmh",
        "velocidad_limite_kmh", "radar_modelo_hint", "radar_antena",
        "sancion_importe_eur", "puntos_detraccion", "lugar_infraccion",
        "fecha_infraccion", "hora_infraccion",
    ]

    def _clean_values(obj: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, float], Dict[str, str]]:
        values = obj.get("values") if isinstance(obj, dict) else {}
        confidence = obj.get("confidence") if isinstance(obj, dict) else {}
        evidence = obj.get("evidence") if isinstance(obj, dict) else {}
        if not isinstance(values, dict):
            values = {}
        if not isinstance(confidence, dict):
            confidence = {}
        if not isinstance(evidence, dict):
            evidence = {}

        cleaned: Dict[str, Any] = {}
        for key in fields:
            value = values.get(key)
            if value in (None, "", "null"):
                continue
            if key == "matricula":
                value = _normalise_plate(value)
            elif key == "expediente_ref":
                value = str(value).strip().replace(" ", "")
                if not _valid_sct_expediente(value):
                    value = None
            elif key == "fecha_infraccion":
                value = _normalise_date(value)
            elif key in ("velocidad_medida_kmh", "velocidad_limite_kmh"):
                value = _normalise_int(value, 10, 250)
            elif key == "puntos_detraccion":
                value = _normalise_int(value, 0, 15)
            elif key == "sancion_importe_eur":
                value = _normalise_float(value)
            elif key == "radar_antena":
                m = re.search(r"\d{3,10}", str(value))
                value = m.group(0) if m else None
            else:
                value = re.sub(r"\s+", " ", str(value)).strip()
            if value not in (None, ""):
                cleaned[key] = value

        conf_clean: Dict[str, float] = {}
        ev_clean: Dict[str, str] = {}
        for key in cleaned:
            try:
                c = float(confidence.get(key) or 0)
            except Exception:
                c = 0.0
            conf_clean[key] = max(0.0, min(1.0, c))
            if evidence.get(key):
                ev_clean[key] = str(evidence.get(key) or "")[:220]
        return cleaned, conf_clean, ev_clean

    def _variants(img):
        # La normalización suele dejar la página vertical, pero en fotografías
        # sin EXIF una página puede seguir invertida. La lectura fina prueba
        # ambas orientaciones sin modificar el original.
        variants = [("0", img)]
        variants.append(("180", img.rotate(180, expand=True)))
        if img.width > img.height:
            variants.append(("90", img.rotate(90, expand=True)))
            variants.append(("270", img.rotate(270, expand=True)))
        return variants

    for page in analyzed_pages:
        content = page.get("analysis_content")
        mime = str(page.get("analysis_mime") or "")
        if not isinstance(content, (bytes, bytearray)) or not mime.startswith("image/"):
            continue
        try:
            with Image.open(io.BytesIO(bytes(content))) as source:
                base_img = source.convert("RGB")
        except Exception as exc:
            errors.append(f"page_{page.get('page_index')}:open:{type(exc).__name__}")
            continue

        for rotation_label, img in _variants(base_img):
            # Plantilla de la notificación SCT fotografiada a página completa.
            # Los recortes son deliberadamente solapados para tolerar márgenes.
            header_crop = _enhanced_crop_bytes(img, (0.48, 0.115, 0.88, 0.265), min_width=2400)
            basic_crop = _enhanced_crop_bytes(img, (0.145, 0.285, 0.855, 0.535), min_width=3000)
            plate_crop = _enhanced_crop_bytes(img, (0.145, 0.335, 0.545, 0.445), min_width=2600)
            sanction_crop = _enhanced_crop_bytes(img, (0.145, 0.465, 0.820, 0.545), min_width=2600)
            crop_count += 4

            system_text = (
                "Eres un transcriptor visual forense de documentos administrativos. "
                "Recibes recortes ampliados de UNA MISMA página. Tu tarea es copiar "
                "solo caracteres inequívocos. No completes por contexto ni por conocimiento. "
                "Si una letra o dígito no se distingue, devuelve null para ese campo."
            )
            user_text = """
Devuelve EXCLUSIVAMENTE JSON válido:
{
  "values": {
    "expediente_ref": string|null,
    "matricula": string|null,
    "velocidad_medida_kmh": integer|null,
    "velocidad_limite_kmh": integer|null,
    "radar_modelo_hint": string|null,
    "radar_antena": string|null,
    "sancion_importe_eur": number|null,
    "puntos_detraccion": integer|null,
    "lugar_infraccion": string|null,
    "fecha_infraccion": string|null,
    "hora_infraccion": string|null
  },
  "confidence": {},
  "evidence": {}
}

REGLAS:
1) expediente_ref: SOLO la línea NÚMERO D'EXPEDIENT / Número d'expedient.
   Ignora NÚMERO D'ENVIAMENT. Debe conservar formato 00/00000000-0.
2) matrícula: SOLO la fila MATRÍCULA. Debe ser 4 dígitos + 3 letras.
   Lee las tres letras carácter a carácter. En evidence escribe por ejemplo:
   "Matrícula 1579 M X V". No sustituyas M/H/N, X/V/Y/K ni V/Y/G por contexto.
3) lugar_infraccion, fecha_infraccion y hora_infraccion:
   SOLO Dades bàsiques de la infracció. Para lugar combina Via/Carrer y Km/Núm.
   como "AP-7, p.k. 204,6" si ambos son visibles.
4) velocidad_medida_kmh, velocidad_limite_kmh, radar_modelo_hint, radar_antena:
   SOLO Fet denunciat. Transcribe el modelo literalmente.
5) sancion_importe_eur y puntos_detraccion:
   SOLO Import de la sanció y Punts a detreure.
6) No uses fechas de notificación, envío, verificación del radar o certificado.
7) confidence 0..1 por cada campo no nulo; evidence copia el fragmento visible.
8) Si el recorte está boca abajo, no contiene la etiqueta solicitada o existe duda:
   devuelve null. No adivines.
"""

            payload = {
                "model": model,
                "input": [
                    {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": user_text},
                            {"type": "input_text", "text": "CABECERA / EXPEDIENTE:"},
                            {"type": "input_image", "image_url": _data_url_jpeg(header_crop)},
                            {"type": "input_text", "text": "DATOS BÁSICOS / HECHO:"},
                            {"type": "input_image", "image_url": _data_url_jpeg(basic_crop)},
                            {"type": "input_text", "text": "FILA MATRÍCULA (zoom máximo):"},
                            {"type": "input_image", "image_url": _data_url_jpeg(plate_crop)},
                            {"type": "input_text", "text": "EXPEDIENTE / IMPORTE / PUNTOS (zoom inferior):"},
                            {"type": "input_image", "image_url": _data_url_jpeg(sanction_crop)},
                        ],
                    },
                ],
                "text": {"format": {"type": "json_object"}},
            }

            try:
                r = requests.post(
                    "https://api.openai.com/v1/responses",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=90,
                )
                if not r.ok:
                    errors.append(
                        f"page_{page.get('page_index')}_rot_{rotation_label}:openai_{r.status_code}"
                    )
                    continue
                obj = json.loads(_response_output_text(r.json()) or "{}")
                values, conf, evidence = _clean_values(obj)

                score = 0.0
                if _valid_sct_expediente(values.get("expediente_ref")):
                    score += 7.0
                if _normalise_plate(values.get("matricula")):
                    score += 7.0
                for k in (
                    "velocidad_medida_kmh", "velocidad_limite_kmh", "radar_modelo_hint",
                    "radar_antena", "sancion_importe_eur", "puntos_detraccion",
                    "lugar_infraccion", "fecha_infraccion", "hora_infraccion",
                ):
                    if values.get(k) not in (None, ""):
                        score += 1.0
                score += sum(float(conf.get(k) or 0) for k in values) * 0.05

                page_candidates.append({
                    "page_index": int(page.get("page_index") or 0),
                    "rotation": rotation_label,
                    "values": values,
                    "confidence": conf,
                    "evidence": evidence,
                    "score": round(score, 3),
                })
            except Exception as exc:
                errors.append(
                    f"page_{page.get('page_index')}_rot_{rotation_label}:{type(exc).__name__}:{exc}"
                )

    if not page_candidates:
        return {
            "values": {}, "confidence": {}, "evidence": {},
            "crop_count": crop_count,
            "error": "; ".join(errors)[:800] if errors else "no_zoom_candidates",
        }

    page_candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
    primary = page_candidates[0]

    merged_values = dict(primary.get("values") or {})
    merged_conf = dict(primary.get("confidence") or {})
    merged_evidence = dict(primary.get("evidence") or {})

    # Solo suplementa un campo ausente desde otra variante/página con lectura
    # de confianza muy alta. Nunca usa una lectura secundaria para pisar la mejor.
    for candidate in page_candidates[1:]:
        vals = candidate.get("values") or {}
        confs = candidate.get("confidence") or {}
        evid = candidate.get("evidence") or {}
        for key, value in vals.items():
            if key in merged_values or value in (None, ""):
                continue
            try:
                c = float(confs.get(key) or 0)
            except Exception:
                c = 0.0
            if c >= 0.94:
                merged_values[key] = value
                merged_conf[key] = c
                if evid.get(key):
                    merged_evidence[key] = evid[key]

    return {
        "values": merged_values,
        "confidence": merged_conf,
        "evidence": merged_evidence,
        "crop_count": crop_count,
        "selected_page": primary.get("page_index"),
        "selected_rotation": primary.get("rotation"),
        "selected_page_score": primary.get("score"),
        "candidate_pages": [
            {
                "page_index": c.get("page_index"),
                "rotation": c.get("rotation"),
                "score": c.get("score"),
                "fields": sorted((c.get("values") or {}).keys()),
            }
            for c in page_candidates
        ],
        "error": "; ".join(errors)[:800] if errors else None,
    }

def _critical_fields_from_blob(text_blob: str) -> Dict[str, Any]:
    """Capa determinista de respaldo para campos críticos.

    Se apoya en etiquetas/frases explícitas del documento. La V1.2 refuerza el
    bloque tabular catalán de datos básicos y evita elegir la primera fecha cercana
    a la carretera, que podía ser la fecha de notificación.
    """
    raw = str(text_blob or "")
    folded = _fold(raw)
    upper = folded.upper()
    out: Dict[str, Any] = {}

    if "SERVEI CATALA DE TRANSIT" in upper:
        out["organismo"] = "Servei Català de Trànsit"

    expediente = _first_match(
        [
            r"NUMERO\s+D['’ ]EXPEDIENT\s*[:#-]?\s*([A-Z0-9][A-Z0-9./-]{5,30})",
            r"NUMERO\s+DE\s+EXPEDIENTE\s*[:#-]?\s*([A-Z0-9][A-Z0-9./-]{5,30})",
            r"N[ºO]\.?\s*EXPEDIENTE\s*[:#-]?\s*([A-Z0-9][A-Z0-9./-]{5,30})",
            r"EXPEDIENTE\s+SANCIONADOR\s*[:#-]?\s*([A-Z0-9][A-Z0-9./-]{5,30})",
        ], upper,
    )
    if expediente:
        out["expediente_ref"] = expediente

    matricula = _first_match(
        [
            r"\bMATRICULA\b\s*[:#-]?\s*(\d{4}\s*[A-Z]{3})\b",
            r"\bMATRICULA\b.{0,90}?\b(\d{4}\s*[A-Z]{3})\b",
        ], upper,
    )
    plate = _normalise_plate(matricula)
    if plate:
        out["matricula"] = plate

    speed_pair = re.search(
        r"(?:VELOCITAT|VELOCIDAD)\s+(?:MESURADA\s+)?(?:DE|A)?\s*(\d{2,3})\s*KM\s*/?\s*H"
        r".{0,240}?(?:LIMITAD[AOA]*|LIMIT\w*)"
        r".{0,120}?(?:A|DE|UN\s+SENYAL\s+A)\s*(\d{2,3})\s*KM\s*/?\s*H",
        upper, flags=re.S,
    )
    if not speed_pair:
        speed_pair = re.search(
            r"(?:CIRCULAR|CIRCULABA|CIRCULANT|CIRCULANDO).{0,160}?"
            r"(\d{2,3})\s*KM\s*/?\s*H.{0,260}?"
            r"(?:LIMITAD[AOA]*|LIMIT\w*).{0,130}?(\d{2,3})\s*KM\s*/?\s*H",
            upper, flags=re.S,
        )
    if speed_pair:
        measured, limit = int(speed_pair.group(1)), int(speed_pair.group(2))
        if 10 <= measured <= 250 and 10 <= limit <= 200 and measured > limit:
            out["velocidad_medida_kmh"] = measured
            out["velocidad_limite_kmh"] = limit
            out["speed_pair_source"] = "explicit_fact_sentence"

    radar = _first_match(
        [
            r"CINEMOMETR[EO].{0,40}?((?:MULTI?RADAR|MULTARADAR|MULTANOVA)[ -]?[A-Z0-9-]*)",
            r"\b((?:MULTI?RADAR|MULTARADAR|MULTANOVA)[ -]?[A-Z0-9-]*)\b",
        ], upper,
    )
    if radar:
        radar_clean = re.sub(r"\s+", " ", radar).strip(" .,-")
        radar_clean = re.sub(r"^MULTARADAR\s*[- ]?\s*C$", "MULTIRADAR C", radar_clean, flags=re.I)
        radar_clean = re.sub(r"^MULTIRADAR\s*[- ]?\s*C$", "MULTIRADAR C", radar_clean, flags=re.I)
        out["radar_modelo_hint"] = radar_clean

    antena = _first_match(
        [
            r"N(?:UM|ÚM)\.?\s*(?:D\s*['’]?\s*)?ANTENA\s*[:#.-]?\s*(\d{3,10})",
            r"D\s*['’]\s*ANTENA\s*[:#.-]?\s*(\d{3,10})",
            r"ANTENA\s*(?:NUMERO|N(?:UM|ÚM)\.?)?\s*[:#.-]?\s*(\d{3,10})",
        ], upper,
    )
    if antena:
        out["radar_antena"] = antena

    # Tabla de datos básicos: Via/Carrer | Km/Núm. | ... | Data | Hora
    facts_table = re.search(
        r"VIA\s*/\s*CARRER.{0,180}?KM\s*/\s*N(?:UM|ÚM)\.?"
        r".{0,450}?\b([A-Z]{1,5}-?\d{1,4})\b"
        r".{0,100}?\b(\d{1,4}(?:[.,]\d{1,2}))\b"
        r".{0,240}?\b(\d{2}[-/]\d{2}[-/]\d{4})\b"
        r".{0,80}?\b([0-2]\d:[0-5]\d)\b",
        upper, flags=re.S,
    )
    if facts_table:
        via, km, date_value, time_value = facts_table.groups()
        out["lugar_infraccion"] = f"{via}, p.k. {km}"
        out["fecha_infraccion"] = _normalise_date(date_value)
        out["hora_infraccion"] = time_value
    else:
        via = _first_match([r"\bVIA\s*/\s*CARRER\b.{0,180}?\b([A-Z]{1,5}-?\d{1,4})\b"], upper)
        km = _first_match([r"\bKM\s*/\s*N(?:UM|ÚM)\.?\b.{0,180}?\b(\d{1,4}(?:[.,]\d{1,2}))\b"], upper)
        if via and km:
            out["lugar_infraccion"] = f"{via}, p.k. {km}"
        elif via:
            out["lugar_infraccion"] = via

    # Bloque sanción/puntos. Primero busca la fila completa, luego etiquetas aisladas.
    sanction_row = re.search(
        r"IMPORT\s+DE\s+LA\s+SANCIO\w*.{0,160}?PUNTS\s+A\s+DETREURE"
        r".{0,260}?(\d{2,4}[.,]\d{2})\s*(?:EUR|€)?"
        r".{0,80}?\b([0-9]|1[0-5])\b",
        upper, flags=re.S,
    )
    if sanction_row:
        amount = _normalise_float(sanction_row.group(1))
        points = _normalise_int(sanction_row.group(2), 0, 15)
        if amount is not None:
            out["sancion_importe_eur"] = amount
        if points is not None:
            out["puntos_detraccion"] = points

    if "sancion_importe_eur" not in out:
        importe = _first_match(
            [
                r"IMPORT(?:E)?\s+DE\s+LA\s+SANCIO\w*\s*[:#-]?\s*(\d{2,4}(?:[.,]\d{1,2})?)\s*(?:EUR|€)?",
                r"IMPORT\s+DE\s+LA\s+SANCIO\s*[:#-]?\s*(\d{2,4}(?:[.,]\d{1,2})?)",
            ], upper,
        )
        amount = _normalise_float(importe)
        if amount is not None:
            out["sancion_importe_eur"] = amount

    if "puntos_detraccion" not in out:
        points = _first_match(
            [
                r"PUNTS\s+A\s+DETREURE\s*[:#-]?\s*(\d{1,2})",
                r"PUNTOS\s+A\s+DETRAER\s*[:#-]?\s*(\d{1,2})",
                r"DETRACCION\s+DE\s*(\d{1,2})\s*PUNTOS",
            ], upper,
        )
        pnum = _normalise_int(points, 0, 15)
        if pnum is not None:
            out["puntos_detraccion"] = pnum

    return {k: v for k, v in out.items() if v not in (None, "")}

def _same_value(a: Any, b: Any) -> bool:
    if a in (None, "") or b in (None, ""):
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    na = re.sub(r"\s+", " ", _fold(str(a))).strip().upper()
    nb = re.sub(r"\s+", " ", _fold(str(b))).strip().upper()
    return na == nb


def _apply_critical_fields(
    core: Dict[str, Any],
    text_blob: str,
    vision_meta: Optional[Dict[str, Any]] = None,
    zoom_meta: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    out = dict(core or {})
    deterministic = _critical_fields_from_blob(text_blob)
    vision_meta = vision_meta or {"values": {}, "confidence": {}, "evidence": {}}
    zoom_meta = zoom_meta or {"values": {}, "confidence": {}, "evidence": {}}

    vision_values = vision_meta.get("values") if isinstance(vision_meta.get("values"), dict) else {}
    vision_conf = vision_meta.get("confidence") if isinstance(vision_meta.get("confidence"), dict) else {}
    vision_evidence = vision_meta.get("evidence") if isinstance(vision_meta.get("evidence"), dict) else {}

    zoom_values = zoom_meta.get("values") if isinstance(zoom_meta.get("values"), dict) else {}
    zoom_conf = zoom_meta.get("confidence") if isinstance(zoom_meta.get("confidence"), dict) else {}
    zoom_evidence = zoom_meta.get("evidence") if isinstance(zoom_meta.get("evidence"), dict) else {}

    conflicts: List[Dict[str, Any]] = []
    unresolved_fields: set[str] = set()
    sources: Dict[str, str] = {}
    override_fields = {
        "organismo", "expediente_ref", "matricula", "velocidad_medida_kmh",
        "velocidad_limite_kmh", "radar_modelo_hint", "radar_antena",
        "sancion_importe_eur", "puntos_detraccion", "lugar_infraccion",
        "fecha_infraccion", "hora_infraccion",
    }

    for key, value in deterministic.items():
        if key not in override_fields:
            continue
        old = out.get(key)
        if old not in (None, "", [], {}) and not _same_value(old, value):
            conflicts.append({
                "field": key,
                "current_value": old,
                "deterministic_value": value,
                "chosen": value,
                "source": "explicit_text",
                "resolved": True,
            })
        out[key] = value
        sources[key] = "explicit_text"

    full_priority = {
        "matricula", "radar_antena", "sancion_importe_eur", "puntos_detraccion",
        "lugar_infraccion", "fecha_infraccion", "hora_infraccion",
    }
    for key, value in vision_values.items():
        if key not in override_fields or value in (None, ""):
            continue
        try:
            conf = float(vision_conf.get(key) or 0)
        except Exception:
            conf = 0.0
        evidence = str(vision_evidence.get(key) or "").strip()
        current = out.get(key)

        if current in (None, "", [], {}):
            if conf >= 0.80:
                out[key] = value
                sources[key] = "targeted_vision"
            continue

        if _same_value(current, value):
            if sources.get(key) == "explicit_text":
                sources[key] = "explicit_text+targeted_vision"
            continue

        trusted_current = sources.get(key) == "explicit_text"
        strict_identity_field = key in {"matricula", "expediente_ref"}
        prefer_vision = key in full_priority and conf >= 0.92 and bool(evidence) and not trusted_current

        if prefer_vision:
            chosen = value
            out[key] = value
            sources[key] = "targeted_vision"
            resolved = True
        else:
            chosen = current
            # En matrícula/expediente, dos lectores que discrepan NO quedan resueltos
            # solo porque uno proceda del OCR textual. Exigimos confirmación por zoom.
            if strict_identity_field:
                unresolved_fields.add(key)
                resolved = False
            else:
                resolved = trusted_current
                if not resolved and conf >= 0.88:
                    unresolved_fields.add(key)

        conflicts.append({
            "field": key,
            "current_value": current,
            "vision_value": value,
            "vision_confidence": round(conf, 3),
            "vision_evidence": evidence[:180],
            "chosen": chosen,
            "source": sources.get(key, "existing_extraction"),
            "resolved": resolved,
        })

    zoom_priority = {
        "expediente_ref", "matricula", "radar_modelo_hint", "radar_antena",
        "sancion_importe_eur", "puntos_detraccion", "lugar_infraccion",
        "fecha_infraccion", "hora_infraccion",
    }
    for key, value in zoom_values.items():
        if key not in override_fields or value in (None, ""):
            continue
        try:
            conf = float(zoom_conf.get(key) or 0)
        except Exception:
            conf = 0.0
        evidence = str(zoom_evidence.get(key) or "").strip()
        current = out.get(key)

        if key == "expediente_ref" and not _valid_sct_expediente(value):
            continue

        if current in (None, "", [], {}):
            if conf >= 0.86:
                out[key] = value
                sources[key] = "zoomed_crop"
                unresolved_fields.discard(key)
            continue

        if _same_value(current, value):
            sources[key] = (
                f"{sources.get(key)}+zoomed_crop" if sources.get(key) else "zoomed_crop_confirmed"
            )
            unresolved_fields.discard(key)
            continue

        explicit_speed = (
            key in {"velocidad_medida_kmh", "velocidad_limite_kmh"}
            and deterministic.get("speed_pair_source") == "explicit_fact_sentence"
            and key in deterministic
        )
        if explicit_speed:
            conflicts.append({
                "field": key,
                "current_value": current,
                "zoom_value": value,
                "zoom_confidence": round(conf, 3),
                "zoom_evidence": evidence[:180],
                "chosen": current,
                "source": sources.get(key, "explicit_text"),
                "resolved": True,
            })
            unresolved_fields.discard(key)
            continue

        threshold = 0.90 if key == "expediente_ref" else 0.92 if key == "matricula" else 0.88
        prefer_zoom = key in zoom_priority and conf >= threshold and bool(evidence)
        if prefer_zoom:
            out[key] = value
            sources[key] = "zoomed_crop"
            unresolved_fields.discard(key)
            resolved = True
            chosen = value
        else:
            chosen = current
            resolved = sources.get(key, "").startswith("explicit_text")
            if not resolved:
                unresolved_fields.add(key)

        conflicts.append({
            "field": key,
            "current_value": current,
            "zoom_value": value,
            "zoom_confidence": round(conf, 3),
            "zoom_evidence": evidence[:180],
            "chosen": chosen,
            "source": sources.get(key, "existing_extraction"),
            "resolved": resolved,
        })

    measured = out.get("velocidad_medida_kmh")
    limit = out.get("velocidad_limite_kmh")
    if isinstance(measured, (int, float)) and isinstance(limit, (int, float)) and measured > limit:
        out["tipo_infraccion"] = "velocidad"
        out["familia_resuelta"] = "velocidad"
        radar = str(out.get("radar_modelo_hint") or "").strip()
        hecho = f"Circular a {int(measured)} km/h en un tramo limitado a {int(limit)} km/h"
        if radar:
            hecho += f", medición efectuada mediante {radar}"
        out["hecho_imputado"] = hecho
        out["hecho_para_recurso"] = hecho
        out["tipo_infraccion_confidence"] = max(float(out.get("tipo_infraccion_confidence") or 0), 0.99)

    required = ["expediente_ref", "matricula", "organismo"]
    if (out.get("tipo_infraccion") or "").lower() == "velocidad":
        required += [
            "velocidad_medida_kmh", "velocidad_limite_kmh",
            "fecha_infraccion", "lugar_infraccion",
        ]
    missing_required = [k for k in required if out.get(k) in (None, "", [], {})]

    organismo_blob = _fold(str(out.get("organismo") or "")).upper()
    if "TRANSIT" in organismo_blob and out.get("expediente_ref") and not _valid_sct_expediente(out.get("expediente_ref")):
        unresolved_fields.add("expediente_ref")

    out["critical_fields_deterministic"] = deterministic
    out["critical_fields_vision"] = vision_values
    out["critical_fields_zoomed"] = zoom_values
    out["critical_field_sources"] = sources
    out["critical_field_conflicts"] = conflicts
    out["unresolved_critical_fields"] = sorted(unresolved_fields)
    out["critical_fields_validation"] = {
        "extractor_version": _EXTRACTOR_VERSION,
        "conflicts_detected": len(conflicts),
        "unresolved_fields": sorted(unresolved_fields),
        "missing_required": missing_required,
        "targeted_vision_error": vision_meta.get("error"),
        "zoomed_vision_error": zoom_meta.get("error"),
    }

    if conflicts or missing_required or unresolved_fields:
        out["requires_operator_review"] = True
        reasons = list(out.get("operator_review_reasons") or [])
        reason = "critical_fields_need_operator_validation"
        if reason not in reasons:
            reasons.append(reason)
        out["operator_review_reasons"] = reasons

    ready_for_generate = not missing_required and not unresolved_fields
    out["ready_for_generate"] = ready_for_generate

    return out, {
        "deterministic": deterministic,
        "vision": vision_meta,
        "zoom": zoom_meta,
        "sources": sources,
        "conflicts": conflicts,
        "unresolved_fields": sorted(unresolved_fields),
        "missing_required": missing_required,
        "ready_for_generate": ready_for_generate,
    }



def _fold_for_match(value: Any) -> str:
    txt = unicodedata.normalize("NFKD", str(value or ""))
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", txt).lower().strip()



def _semaforo_critical_fields_from_images(analyzed_pages: List[Dict[str, Any]]) -> Dict[str, Any]:
    '''Lectura visual ligera y específica para multas de semáforo.

    Solo transcribe datos documentales. No hace razonamiento jurídico.
    Una única llamada visual sobre las páginas normalizadas para evitar el coste
    de targeted vision + zooms + secondary facts propios de VELOCIDAD.
    '''
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return {"values": {}, "confidence": {}, "evidence": {}, "error": "OPENAI_API_KEY_missing"}

    image_parts: List[Dict[str, Any]] = []
    for page in analyzed_pages:
        content = page.get("analysis_content")
        mime = str(page.get("analysis_mime") or "")
        if not isinstance(content, (bytes, bytearray)) or not mime.startswith("image/"):
            continue
        page_index = int(page.get("page_index") or 0)
        image_parts.append({"type": "input_text", "text": f"Página {page_index}:"})
        image_parts.append({
            "type": "input_image",
            "image_url": f"data:{mime};base64," + base64.b64encode(bytes(content)).decode("ascii"),
        })

    if not image_parts:
        return {"values": {}, "confidence": {}, "evidence": {}, "error": "no_image_pages"}

    system_text = (
        "Eres un lector documental de alta precisión para multas municipales de tráfico en España. "
        "Tu única tarea es TRANSCRIBIR datos visibles. No interpretes el Derecho, no corrijas el documento "
        "y no inventes. Si un dato no se ve con claridad, devuelve null."
    )

    user_text = r'''
Lee todas las páginas y devuelve EXCLUSIVAMENTE JSON con esta estructura:

{
  "values": {
    "organismo": string|null,
    "expediente_ref": string|null,
    "matricula": string|null,
    "fecha_infraccion": string|null,
    "hora_infraccion": string|null,
    "lugar_infraccion": string|null,
    "sancion_importe_eur": number|null,
    "puntos_detraccion": integer|null,
    "capture_method": string|null,
    "capture_automatic": boolean|null,
    "norma": string|null,
    "articulo": string|null,
    "document_subject_name": string|null,
    "document_subject_id": string|null,
    "fecha_emision": string|null,
    "fecha_limite_pago": string|null,
    "vehicle_photo_present": boolean|null
  },
  "confidence": {},
  "evidence": {}
}

Reglas:
- expediente_ref: el EXPEDIENTE sancionador, no referencia bancaria ni identificación de pago.
- matrícula: exactamente 4 dígitos y 3 letras.
- fecha_infraccion y hora_infraccion: fecha/hora del HECHO, no fecha de emisión ni fecha límite de pago.
- lugar_infraccion: vía/calle y número o punto kilométrico si aparece.
- sancion_importe_eur: importe de la sanción correspondiente al hecho, no una cifra de código o referencia.
- puntos_detraccion: puntos asociados a la infracción.
- capture_method: copia la expresión del documento; ejemplos: "CONTROL PER CÀMERA DE VÍDEO", "agente", "radar".
- capture_automatic: true solo si el documento indica captación/control automático o por cámara sin observación directa como soporte principal; false si consta observación presencial; null si no se puede saber.
- norma / articulo: copia literalmente la norma y el artículo/apartado indicados. NO sustituyas por el artículo que creas correcto.
- document_subject_name / document_subject_id: persona identificada como infractor/interesado en la notificación.
- vehicle_photo_present: true si la página contiene una fotografía del vehículo asociada a la denuncia.
- confidence: 0..1 para cada valor no nulo.
- evidence: fragmento literal breve donde se ve cada dato.
'''

    payload = {
        "model": (os.getenv("OPENAI_MODEL") or "gpt-4o").strip(),
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_text}] + image_parts},
        ],
        "text": {"format": {"type": "json_object"}},
    }

    try:
        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=90,
        )
        if not r.ok:
            return {"values": {}, "confidence": {}, "evidence": {}, "error": f"OpenAI {r.status_code}: {r.text[:300]}"}

        obj = json.loads(_response_output_text(r.json()) or "{}")
        values = obj.get("values") if isinstance(obj, dict) else {}
        confidence = obj.get("confidence") if isinstance(obj, dict) else {}
        evidence = obj.get("evidence") if isinstance(obj, dict) else {}
        if not isinstance(values, dict):
            values = {}
        if not isinstance(confidence, dict):
            confidence = {}
        if not isinstance(evidence, dict):
            evidence = {}

        out: Dict[str, Any] = {}

        plate = _normalise_plate(values.get("matricula"))
        if plate:
            out["matricula"] = plate

        for key in (
            "organismo", "expediente_ref", "hora_infraccion", "lugar_infraccion",
            "capture_method", "norma", "articulo", "document_subject_name",
            "document_subject_id",
        ):
            value = values.get(key)
            if value not in (None, "", "null"):
                out[key] = str(value).strip()

        for key in ("fecha_infraccion", "fecha_emision", "fecha_limite_pago"):
            value = _normalise_date(values.get(key))
            if value:
                out[key] = value

        amount = _normalise_float(values.get("sancion_importe_eur"))
        if amount is not None:
            out["sancion_importe_eur"] = amount

        points = _normalise_int(values.get("puntos_detraccion"), 0, 15)
        if points is not None:
            out["puntos_detraccion"] = points

        if isinstance(values.get("capture_automatic"), bool):
            out["capture_automatic"] = values["capture_automatic"]
        if isinstance(values.get("vehicle_photo_present"), bool):
            out["vehicle_photo_present"] = values["vehicle_photo_present"]

        conf_out: Dict[str, float] = {}
        ev_out: Dict[str, str] = {}
        for key in out:
            try:
                conf_out[key] = max(0.0, min(1.0, float(confidence.get(key) or 0)))
            except Exception:
                conf_out[key] = 0.0
            if evidence.get(key):
                ev_out[key] = str(evidence.get(key))[:220]

        return {
            "values": out,
            "confidence": conf_out,
            "evidence": ev_out,
            "version": "semaforo_secondary_v1_0",
        }
    except Exception as exc:
        return {
            "values": {}, "confidence": {}, "evidence": {},
            "version": "semaforo_secondary_v1_0",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _apply_semaforo_fields(
    core: Dict[str, Any],
    sema_meta: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    out = dict(core or {})
    values = (sema_meta or {}).get("values") or {}
    confidence = (sema_meta or {}).get("confidence") or {}
    evidence = (sema_meta or {}).get("evidence") or {}
    sources: Dict[str, str] = {}
    conflicts: List[Dict[str, Any]] = []

    direct_map = {
        "organismo": "organismo",
        "expediente_ref": "expediente_ref",
        "matricula": "matricula",
        "fecha_infraccion": "fecha_infraccion",
        "hora_infraccion": "hora_infraccion",
        "lugar_infraccion": "lugar_infraccion",
        "sancion_importe_eur": "sancion_importe_eur",
        "puntos_detraccion": "puntos_detraccion",
    }

    for src, dst in direct_map.items():
        value = values.get(src)
        if value in (None, "", [], {}):
            continue
        current = out.get(dst)
        conf = float(confidence.get(src) or 0)
        if current not in (None, "", [], {}) and str(current).strip() != str(value).strip():
            conflicts.append({
                "field": dst,
                "current_value": current,
                "vision_value": value,
                "vision_confidence": conf,
                "resolved": conf >= 0.85,
                "chosen": value if conf >= 0.85 else current,
                "source": "semaforo_targeted_vision" if conf >= 0.85 else "existing_extraction",
            })
        if current in (None, "", [], {}) or conf >= 0.85:
            out[dst] = value
            sources[dst] = "semaforo_targeted_vision"

    sema_facts = {
        "capture_method": values.get("capture_method"),
        "capture_automatic": values.get("capture_automatic"),
        "normative_reference": {
            "norm": values.get("norma"),
            "article": values.get("articulo"),
        },
        "document_subject": {
            "full_name": values.get("document_subject_name"),
            "id_number": values.get("document_subject_id"),
        },
        "fecha_emision": values.get("fecha_emision"),
        "fecha_limite_pago": values.get("fecha_limite_pago"),
        "vehicle_photo_present": values.get("vehicle_photo_present"),
    }
    sema_facts = {
        k: v for k, v in sema_facts.items()
        if v not in (None, "", [], {}) and not (
            isinstance(v, dict) and not any(x not in (None, "", [], {}) for x in v.values())
        )
    }

    if sema_facts:
        out["semaforo_secondary_facts"] = sema_facts
        out["semaforo_secondary_facts_version"] = (sema_meta or {}).get("version") or "semaforo_secondary_v1_0"
        out["semaforo_secondary_facts_confidence"] = confidence
        out["semaforo_secondary_facts_evidence"] = evidence

    required = [
        "expediente_ref", "organismo", "matricula", "fecha_infraccion",
        "lugar_infraccion", "sancion_importe_eur", "puntos_detraccion",
    ]
    missing = [k for k in required if out.get(k) in (None, "", [], {})]

    out["ready_for_generate"] = False
    out["requires_operator_review"] = True

    reasons = list(out.get("operator_review_reasons") or [])
    if missing and "semaforo_basic_fields_missing" not in reasons:
        reasons.append("semaforo_basic_fields_missing")
    if "semaforo_legal_specialist_pending" not in reasons:
        reasons.append("semaforo_legal_specialist_pending")
    out["operator_review_reasons"] = reasons

    return out, {
        "deterministic": {},
        "vision": sema_meta or {},
        "zoom": {"values": {}, "confidence": {}, "evidence": {}, "skipped": True},
        "secondary": sema_meta or {},
        "sources": sources,
        "conflicts": conflicts,
        "unresolved_fields": [],
        "missing_required": missing,
        "ready_for_generate": False,
        "specialist_dispatch": "semaforo",
        "deep_analysis": "semaforo",
    }


def _resolved_traffic_family(core: Dict[str, Any], text_blob: str = "") -> str:
    """Dispatcher barato: decide especialista antes de ejecutar visión profunda."""
    raw = (
        (core or {}).get("familia_resuelta")
        or (core or {}).get("tipo_infraccion")
        or (core or {}).get("familia")
        or ""
    )
    value = _fold_for_match(raw).replace(" ", "_")
    aliases = {
        "speed": "velocidad",
        "velocitat": "velocidad",
        "red_light": "semaforo",
        "semáforo": "semaforo",
        "semaforo": "semaforo",
        "movil": "movil",
        "móvil": "movil",
        "cinturon": "cinturon",
        "cinturón": "cinturon",
        "parking": "estacionamiento",
    }
    value = aliases.get(value, value)

    # "otro", "generic", "unknown", etc. NO son una familia especializada.
    # Son resultados provisionales de la extracción base y deben permitir
    # que el dispatcher inspeccione el texto completo del documento.
    unresolved_family_values = {
        "",
        "otro",
        "otros",
        "otra",
        "otras",
        "unknown",
        "generic",
        "generico",
        "genérico",
        "traffic_generic",
        "municipal_generico",
        "municipal_genérico",
        "sin_clasificar",
        "unclassified",
    }

    if value not in unresolved_family_values:
        return value

    blob = _fold_for_match(
        " ".join([
            str((core or {}).get("hecho_imputado") or ""),
            str((core or {}).get("hecho_denunciado_literal") or ""),
            str(text_blob or ""),
        ])
    )

    if any(x in blob for x in (
        "llum vermella",
        "luz roja",
        "fase roja",
        "semàfor",
        "semafor",
        "semaforo",
        "semáforo",
        "no respetar la luz roja",
        "no respectar el llum vermell",
        "no respectar la llum vermella",
    )):
        return "semaforo"

    if any(x in blob for x in (
        "km/h", "cinemometro", "cinemómetro", "radar",
        "exceso de velocidad", "velocitat",
    )):
        return "velocidad"

    if any(x in blob for x in ("telefono movil", "teléfono móvil", "movil", "móvil")):
        return "movil"

    if any(x in blob for x in ("cinturon", "cinturón")):
        return "cinturon"

    return "traffic_generic"


def _velocity_secondary_facts_from_images(analyzed_pages: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Segunda lectura visual de hechos secundarios. No pisa campos primarios V8.
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return {
            "version": _SECONDARY_FACTS_VERSION,
            "facts": {},
            "confidence": {},
            "evidence": {},
            "error": "OPENAI_API_KEY_not_configured",
        }

    image_parts: List[Dict[str, Any]] = []
    for page in analyzed_pages:
        content = page.get("analysis_content")
        mime = str(page.get("analysis_mime") or "")
        if not content or not mime.startswith("image/"):
            continue
        image_parts.append({
            "type": "input_text",
            "text": f"PÁGINA {int(page.get('page_index') or 0)}:",
        })
        image_parts.append({
            "type": "input_image",
            "image_url": _data_url_jpeg(content),
        })

    if not image_parts:
        return {
            "version": _SECONDARY_FACTS_VERSION,
            "facts": {},
            "confidence": {},
            "evidence": {},
            "error": "no_image_pages",
        }

    system_text = (
        "Eres un transcriptor jurídico-documental de alta precisión para sanciones de tráfico en España. "
        "Lees TODAS las páginas como un único documento, pero tu tarea es extraer exclusivamente HECHOS SECUNDARIOS. "
        "No corrijas, no completes por contexto, no hagas cálculos y no extraigas matrícula, número de expediente, "
        "velocidad, límite, radar, antena, importe o puntos: esos campos ya han sido validados por otra capa. "
        "Cada fecha debe atribuirse únicamente a la etiqueta o frase visible que la acompaña. "
        "Si una fecha pertenece a datos del conductor, nunca la conviertas en fecha de verificación metrológica. "
        "Si un dato no se ve inequívocamente, devuelve null."
    )

    user_text = r'''
Devuelve EXCLUSIVAMENTE JSON válido:

{
  "facts": {
    "capture_automatic": true|false|null,
    "initiation_document_date": "DD-MM-YYYY"|null,
    "driver_data_date": "DD-MM-YYYY"|null,
    "verification_date": "DD-MM-YYYY"|null,
    "normative_reference": {
      "norm": string|null,
      "article": string|null
    },
    "document_subject": {
      "full_name": string|null,
      "id_number": string|null
    },
    "vehicle_photo_present": true|false|null,
    "certificate_reproduction_present": true|false|null
  },
  "confidence": {
    "capture_automatic": 0.0,
    "initiation_document_date": 0.0,
    "driver_data_date": 0.0,
    "verification_date": 0.0,
    "normative_reference": 0.0,
    "document_subject": 0.0,
    "vehicle_photo_present": 0.0,
    "certificate_reproduction_present": 0.0
  },
  "evidence": {
    "capture_automatic": "fragmento literal",
    "initiation_document_date": "fragmento literal",
    "driver_data_date": "fragmento literal",
    "verification_date": "fragmento literal",
    "normative_reference": "fragmento literal",
    "document_subject": "fragmento literal",
    "vehicle_photo_present": "descripción visual breve",
    "certificate_reproduction_present": "descripción visual breve"
  }
}

REGLAS OBLIGATORIAS:

1) capture_automatic:
   true SOLO si se lee expresamente algo equivalente a
   "IMATGE CAPTADA AUTOMÀTICAMENT", "imagen captada automáticamente",
   "captació automàtica" o equivalente inequívoco.
   false SOLO si el documento dice expresamente que NO fue automática.
   Si no consta, null.

2) initiation_document_date:
   SOLO la fecha del acuerdo/documento de incoación cuando se vea una frase
   equivalente a "acord d'incoació ... de data", "acuerdo de incoación de fecha",
   "dictat en data" o similar.
   NO la llames fecha de notificación y NO uses fecha del hecho.

3) driver_data_date:
   SOLO la fecha que aparezca asociada a frases como
   "Dades del conductor facilitades pel titular del vehicle en data..."
   o "datos del conductor facilitados por el titular...".
   Lee día, mes y año carácter a carácter. No confundas 05 con 06.

4) verification_date:
   SOLO si la MISMA zona visible contiene términos inequívocos como
   "verificació periòdica", "verificación periódica",
   "certificat/certificado de verificación", "darrera/última data de verificació".
   Una fecha junto a "dades/datos del conductor" está PROHIBIDO usarla aquí.
   Si no existe una etiqueta de verificación inequívoca, null.

5) normative_reference:
   Extrae la norma y artículo/apartado que el documento indique como infringido,
   por ejemplo "Reglament General de Circulació 52.1.A".
   No completes artículos no visibles.

6) document_subject:
   Nombre completo y DNI/NIE SOLO si aparecen como interesado/infractor/conductor
   en la notificación sancionadora. No uses datos de autorización RTM ni de otros anexos.

7) vehicle_photo_present:
   true si en alguna página se ve claramente una fotografía/captura del vehículo;
   false solo si puede afirmarse visualmente que ninguna página contiene fotografía;
   si no puedes comprobarlo, null.

8) certificate_reproduction_present:
   true si se reproduce visualmente un certificado metrológico/verificación;
   false solo si puede afirmarse con seguridad que no aparece;
   si hay duda, null.

9) confidence debe reflejar LEGIBILIDAD, no plausibilidad.
   Evidence máximo 220 caracteres y debe copiar la etiqueta/frase o describir
   exactamente el elemento visual que sustenta el dato.

10) No uses conocimiento jurídico para completar información no visible.
'''

    payload = {
        "model": (os.getenv("OPENAI_MODEL") or "gpt-4o").strip(),
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_text}] + image_parts},
        ],
        "text": {"format": {"type": "json_object"}},
    }

    try:
        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=90,
        )
        if not r.ok:
            return {
                "version": _SECONDARY_FACTS_VERSION,
                "facts": {},
                "confidence": {},
                "evidence": {},
                "error": f"OpenAI {r.status_code}: {r.text[:300]}",
            }

        obj = json.loads(_response_output_text(r.json()) or "{}")
        facts = obj.get("facts") if isinstance(obj, dict) else {}
        confidence = obj.get("confidence") if isinstance(obj, dict) else {}
        evidence = obj.get("evidence") if isinstance(obj, dict) else {}
        if not isinstance(facts, dict):
            facts = {}
        if not isinstance(confidence, dict):
            confidence = {}
        if not isinstance(evidence, dict):
            evidence = {}

        cleaned: Dict[str, Any] = {}

        for key in ("capture_automatic", "vehicle_photo_present", "certificate_reproduction_present"):
            val = facts.get(key)
            if isinstance(val, bool):
                cleaned[key] = val

        for key in ("initiation_document_date", "driver_data_date", "verification_date"):
            val = _normalise_date(facts.get(key))
            if val:
                cleaned[key] = val

        nr = facts.get("normative_reference")
        if isinstance(nr, dict):
            norm = str(nr.get("norm") or "").strip()
            article = str(nr.get("article") or "").strip().upper()
            if norm or article:
                cleaned["normative_reference"] = {
                    "norm": norm or None,
                    "article": article or None,
                }

        subject = facts.get("document_subject")
        if isinstance(subject, dict):
            full_name = re.sub(r"\s+", " ", str(subject.get("full_name") or "")).strip()
            id_number = re.sub(r"[^0-9A-Za-z]", "", str(subject.get("id_number") or "")).upper()
            if full_name or id_number:
                cleaned["document_subject"] = {
                    "full_name": full_name or None,
                    "id_number": id_number or None,
                }

        conf_clean: Dict[str, float] = {}
        for key in cleaned:
            try:
                val = float(confidence.get(key) or 0)
            except Exception:
                val = 0.0
            conf_clean[key] = max(0.0, min(1.0, val))

        evidence_clean: Dict[str, str] = {}
        for key in cleaned:
            txt = re.sub(r"\s+", " ", str(evidence.get(key) or "")).strip()
            if txt:
                evidence_clean[key] = txt[:260]

        # Salvaguarda: fecha de datos del conductor != verificación.
        driver_date = cleaned.get("driver_data_date")
        verification_date = cleaned.get("verification_date")
        verification_ev = _fold_for_match(evidence_clean.get("verification_date") or "")
        if verification_date and (
            any(x in verification_ev for x in (
                "dades del conductor", "datos del conductor",
                "facilitades pel titular", "facilitados por el titular"
            ))
            or (
                driver_date
                and verification_date == driver_date
                and not any(x in verification_ev for x in (
                    "verificacio", "verificacion", "certificat", "certificado"
                ))
            )
        ):
            cleaned.pop("verification_date", None)
            conf_clean.pop("verification_date", None)
            evidence_clean.pop("verification_date", None)

        return {
            "version": _SECONDARY_FACTS_VERSION,
            "facts": cleaned,
            "confidence": conf_clean,
            "evidence": evidence_clean,
            "error": None,
        }
    except Exception as exc:
        return {
            "version": _SECONDARY_FACTS_VERSION,
            "facts": {},
            "confidence": {},
            "evidence": {},
            "error": f"{type(exc).__name__}: {exc}",
        }


def _consolidate_extraction(case_id: str, analyzed_pages: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not analyzed_pages:
        raise HTTPException(status_code=422, detail="No se pudo analizar ningún documento original")

    combined_core: Dict[str, Any] = {}
    raw_parts: List[str] = []
    source_ids: List[str] = []
    source_keys: List[str] = []

    for page in analyzed_pages:
        wrapper = page.get("wrapper") or {}
        core = wrapper.get("extracted") or {}
        if not isinstance(core, dict):
            core = {}

        combined_core = _merge_extracted(combined_core, core)

        text_page = _page_text(core)
        if text_page:
            raw_parts.append(f"===== PÁGINA {page['page_index']} =====\n{text_page}")

        if page.get("document_id"):
            source_ids.append(str(page["document_id"]))
        if page.get("key"):
            source_keys.append(str(page["key"]))

    combined_blob = "\n\n".join(raw_parts).strip()
    if combined_blob:
        combined_core["raw_text_blob"] = combined_blob
        combined_core["vision_raw_text"] = combined_blob[:16000]

    combined_core = _enrich_with_triage(combined_core, combined_blob or _flatten_text(combined_core))
    dispatched_family = _resolved_traffic_family(combined_core, combined_blob)
    combined_core["specialist_dispatch"] = dispatched_family

    if dispatched_family == "velocidad":
        # Solo VELOCIDAD paga el coste de targeted vision + zooms SCT +
        # Secondary Facts de velocidad.
        vision_meta = _critical_fields_from_images(analyzed_pages)
        zoom_meta = _critical_fields_from_zoomed_crops(analyzed_pages)
        combined_core, critical_meta = _apply_critical_fields(
            combined_core, combined_blob, vision_meta, zoom_meta
        )

        secondary_meta = _velocity_secondary_facts_from_images(analyzed_pages)
        secondary_facts = secondary_meta.get("facts") if isinstance(secondary_meta, dict) else {}
        if isinstance(secondary_facts, dict) and secondary_facts:
            combined_core["velocity_secondary_facts"] = secondary_facts
            combined_core["velocity_secondary_facts_version"] = secondary_meta.get("version")
            combined_core["velocity_secondary_facts_confidence"] = secondary_meta.get("confidence") or {}
            combined_core["velocity_secondary_facts_evidence"] = secondary_meta.get("evidence") or {}
        critical_meta["secondary"] = secondary_meta
        critical_meta["specialist_dispatch"] = dispatched_family
        critical_meta["deep_analysis"] = "velocity"
    elif dispatched_family == "semaforo":
        sema_meta = _semaforo_critical_fields_from_images(analyzed_pages)
        combined_core, critical_meta = _apply_semaforo_fields(combined_core, sema_meta)
    else:
        basic_required = ["expediente_ref", "organismo", "matricula", "fecha_infraccion", "lugar_infraccion"]
        missing_required = [
            key for key in basic_required
            if combined_core.get(key) in (None, "", [], {})
        ]

        combined_core["requires_operator_review"] = True
        reasons = list(combined_core.get("operator_review_reasons") or [])
        reason = f"{dispatched_family}_specialist_pending"
        if reason not in reasons:
            reasons.append(reason)
        combined_core["operator_review_reasons"] = reasons
        combined_core["ready_for_generate"] = False

        critical_meta = {
            "deterministic": {},
            "vision": {"values": {}, "confidence": {}, "evidence": {}, "skipped": True},
            "zoom": {"values": {}, "confidence": {}, "evidence": {}, "skipped": True},
            "secondary": {"facts": {}, "confidence": {}, "evidence": {}, "skipped": True},
            "sources": {},
            "conflicts": [],
            "unresolved_fields": [],
            "missing_required": missing_required,
            "ready_for_generate": False,
            "specialist_dispatch": dispatched_family,
            "deep_analysis": "skipped_specialist_pending",
        }

    combined_core["document_role"] = "primary_case_document"
    combined_core["document_group_type"] = "traffic_fine"
    combined_core["document_page_count"] = len(analyzed_pages)
    combined_core["source_document_ids"] = source_ids
    combined_core["extractor_version"] = _EXTRACTOR_VERSION

    wrapper = {
        "filename": "multa_consolidada",
        "mime": "application/x-rtm-logical-document",
        "size_bytes": sum(int(p.get("size_bytes") or 0) for p in analyzed_pages),
        "sha256": hashlib.sha256(
            "|".join(str(p.get("sha256") or "") for p in analyzed_pages).encode("utf-8")
        ).hexdigest(),
        "storage": {
            "type": "document_group",
            "source_document_ids": source_ids,
            "source_keys": source_keys,
        },
        "document_group": {
            "role": "primary_case_document",
            "type": "traffic_fine",
            "page_count": len(analyzed_pages),
        },
        "pages": [
            {
                "page_index": p["page_index"],
                "document_id": p.get("document_id"),
                "mime_detected": p.get("mime_detected"),
                "analysis_mime": p.get("analysis_mime"),
                "key": p.get("key"),
                "orientation": p.get("orientation"),
            }
            for p in analyzed_pages
        ],
        "extracted": combined_core,
    }

    confidence_values: List[float] = []
    for p in analyzed_pages:
        try:
            conf = float(p.get("confidence") or 0)
            if conf > 0:
                confidence_values.append(conf)
        except Exception:
            pass
    confidence = max(confidence_values) if confidence_values else 0.75
    if critical_meta.get("deterministic", {}).get("speed_pair_source") == "explicit_fact_sentence":
        confidence = max(confidence, 0.90)

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO extractions(case_id, extracted_json, confidence, model, created_at) "
                "VALUES (:id, CAST(:payload AS JSONB), :confidence, :model, NOW())"
            ),
            {
                "id": case_id,
                "payload": json.dumps(wrapper, ensure_ascii=False),
                "confidence": confidence,
                "model": f"{_ENGINE_NAME}+traffic_fine+v1_9",
            },
        )

    return wrapper, critical_meta


def reanalyze_traffic_fine_case(case_id: str) -> Dict[str, Any]:
    """Reanaliza los originales YA almacenados de un expediente traffic/fine.

    No crea un caso nuevo, no cobra y no modifica los originales de B2.
    Inserta una extracción consolidada que será la última consumida por Generate.
    """
    meta = _case_meta(case_id)
    if meta["department"] != "traffic" or meta["case_type"] not in _TRAFFIC_FINE_TYPES:
        raise HTTPException(
            status_code=409,
            detail="El reanálisis especializado v1 solo está habilitado para expedientes de Tráfico / Multa.",
        )

    documents = _load_original_documents(case_id)
    if not documents:
        raise HTTPException(status_code=404, detail="El expediente no tiene documentos originales")

    _append_event(
        case_id,
        "case_reanalysis_started",
        {
            "engine": _ENGINE_NAME,
            "extractor_version": _EXTRACTOR_VERSION,
            "specialist": "traffic_fine",
            "original_documents": len(documents),
        },
    )

    analyzed_pages: List[Dict[str, Any]] = []
    seen_sha256: set[str] = set()

    try:
        for index, doc in enumerate(documents, start=1):
            content = download_bytes(doc["bucket"], doc["key"])
            if not content:
                _append_event(
                    case_id,
                    "case_reanalysis_document_skipped",
                    {"document_id": doc["id"], "reason": "empty_b2_object"},
                )
                continue

            sha256 = hashlib.sha256(content).hexdigest()
            if sha256 in seen_sha256:
                _append_event(
                    case_id,
                    "case_reanalysis_document_skipped",
                    {"document_id": doc["id"], "reason": "duplicate_sha256", "sha256": sha256},
                )
                continue
            seen_sha256.add(sha256)

            mime_detected = _sniff_mime(content, doc.get("mime") or "", doc.get("key") or "")
            analysis_content, analysis_mime, orientation_meta = _normalize_image_for_analysis(content, mime_detected)
            analysis_filename = _filename_for_mime(index, doc.get("key") or "", analysis_mime)

            _append_event(
                case_id,
                "case_reanalysis_document_detected",
                {
                    "document_id": doc["id"],
                    "page_index": index,
                    "declared_mime": doc.get("mime") or "",
                    "detected_mime": mime_detected,
                    "analysis_mime": analysis_mime,
                    "analysis_filename": analysis_filename,
                    "original_key": doc.get("key"),
                    "orientation": orientation_meta,
                },
            )

            result = analyze_existing_case_document(
                case_id=case_id,
                content=analysis_content,
                filename=analysis_filename,
                mime=analysis_mime,
                b2_bucket=doc["bucket"],
                b2_key=doc["key"],
            )
            wrapper = result.get("extracted") or {}

            analyzed_pages.append(
                {
                    "page_index": index,
                    "document_id": doc["id"],
                    "bucket": doc["bucket"],
                    "key": doc["key"],
                    "size_bytes": len(content),
                    "sha256": sha256,
                    "mime_detected": mime_detected,
                    "analysis_mime": analysis_mime,
                    "analysis_content": analysis_content,  # solo memoria; nunca se serializa en DB/eventos
                    "orientation": orientation_meta,
                    "wrapper": wrapper,
                    "confidence": 0.75,
                }
            )

        if not analyzed_pages:
            raise HTTPException(status_code=422, detail="Ningún original pudo ser reanalizado")

        consolidated, critical_meta = _consolidate_extraction(case_id, analyzed_pages)
        core = consolidated.get("extracted") or {}
        deterministic = critical_meta.get("deterministic") or {}
        conflicts = critical_meta.get("conflicts") or []

        event_payload = {
            "ok": True,
            "engine": _ENGINE_NAME,
            "extractor_version": _EXTRACTOR_VERSION,
            "specialist": "traffic_fine",
            "specialist_dispatch": critical_meta.get("specialist_dispatch") or core.get("specialist_dispatch"),
            "deep_analysis": critical_meta.get("deep_analysis"),
            "pages_analyzed": len(analyzed_pages),
            "tipo_infraccion": core.get("tipo_infraccion"),
            "familia_resuelta": core.get("familia_resuelta") or core.get("tipo_infraccion"),
            "hecho_imputado": core.get("hecho_imputado") or core.get("hecho_denunciado_literal"),
            "expediente_ref": core.get("expediente_ref"),
            "matricula": core.get("matricula"),
            "velocidad_medida_kmh": core.get("velocidad_medida_kmh"),
            "velocidad_limite_kmh": core.get("velocidad_limite_kmh"),
            "radar_modelo_hint": core.get("radar_modelo_hint"),
            "radar_antena": core.get("radar_antena"),
            "sancion_importe_eur": core.get("sancion_importe_eur"),
            "puntos_detraccion": core.get("puntos_detraccion"),
            "lugar_infraccion": core.get("lugar_infraccion"),
            "fecha_infraccion": core.get("fecha_infraccion"),
            "critical_fields_detected": deterministic,
            "critical_fields_vision": (critical_meta.get("vision") or {}).get("values") or {},
            "critical_fields_zoomed": (critical_meta.get("zoom") or {}).get("values") or {},
            "critical_fields_zoom_selected_page": (critical_meta.get("zoom") or {}).get("selected_page"),
            "critical_fields_zoom_selected_rotation": (critical_meta.get("zoom") or {}).get("selected_rotation"),
            "critical_fields_zoom_candidates": (critical_meta.get("zoom") or {}).get("candidate_pages") or [],
            "critical_fields_zoom_error": (critical_meta.get("zoom") or {}).get("error"),
            "critical_field_sources": critical_meta.get("sources") or {},
            "velocity_secondary_facts_version": ((critical_meta.get("secondary") or {}).get("version")),
            "velocity_secondary_facts": ((critical_meta.get("secondary") or {}).get("facts") or {}),
            "velocity_secondary_facts_confidence": ((critical_meta.get("secondary") or {}).get("confidence") or {}),
            "velocity_secondary_facts_evidence": ((critical_meta.get("secondary") or {}).get("evidence") or {}),
            "velocity_secondary_facts_error": ((critical_meta.get("secondary") or {}).get("error")),
            "semaforo_secondary_facts_version": core.get("semaforo_secondary_facts_version"),
            "semaforo_secondary_facts": core.get("semaforo_secondary_facts") or {},
            "semaforo_secondary_facts_confidence": core.get("semaforo_secondary_facts_confidence") or {},
            "semaforo_secondary_facts_evidence": core.get("semaforo_secondary_facts_evidence") or {},
            "critical_conflicts_resolved": conflicts,
            "missing_required_fields": critical_meta.get("missing_required") or [],
            "unresolved_critical_fields": critical_meta.get("unresolved_fields") or [],
            "requires_operator_review": bool(core.get("requires_operator_review")),
            "ready_for_generate": bool(critical_meta.get("ready_for_generate")),
        }
        _append_event(case_id, "case_reanalysis_completed", event_payload)

        return {
            "ok": True,
            "case_id": case_id,
            "specialist": "traffic_fine",
            "specialist_dispatch": critical_meta.get("specialist_dispatch") or core.get("specialist_dispatch"),
            "deep_analysis": critical_meta.get("deep_analysis"),
            "pages_analyzed": len(analyzed_pages),
            "tipo_infraccion": core.get("tipo_infraccion"),
            "familia_resuelta": core.get("familia_resuelta") or core.get("tipo_infraccion"),
            "hecho_imputado": core.get("hecho_imputado") or core.get("hecho_denunciado_literal"),
            "expediente_ref": core.get("expediente_ref"),
            "matricula": core.get("matricula"),
            "velocidad_medida_kmh": core.get("velocidad_medida_kmh"),
            "velocidad_limite_kmh": core.get("velocidad_limite_kmh"),
            "radar_modelo_hint": core.get("radar_modelo_hint"),
            "radar_antena": core.get("radar_antena"),
            "sancion_importe_eur": core.get("sancion_importe_eur"),
            "puntos_detraccion": core.get("puntos_detraccion"),
            "lugar_infraccion": core.get("lugar_infraccion"),
            "fecha_infraccion": core.get("fecha_infraccion"),
            "requires_operator_review": bool(core.get("requires_operator_review")),
            "critical_fields_zoomed": (critical_meta.get("zoom") or {}).get("values") or {},
            "critical_fields_zoom_selected_page": (critical_meta.get("zoom") or {}).get("selected_page"),
            "critical_fields_zoom_selected_rotation": (critical_meta.get("zoom") or {}).get("selected_rotation"),
            "critical_fields_zoom_candidates": (critical_meta.get("zoom") or {}).get("candidate_pages") or [],
            "critical_fields_zoom_error": (critical_meta.get("zoom") or {}).get("error"),
            "critical_field_sources": critical_meta.get("sources") or {},
            "velocity_secondary_facts_version": ((critical_meta.get("secondary") or {}).get("version")),
            "velocity_secondary_facts": ((critical_meta.get("secondary") or {}).get("facts") or {}),
            "velocity_secondary_facts_confidence": ((critical_meta.get("secondary") or {}).get("confidence") or {}),
            "velocity_secondary_facts_evidence": ((critical_meta.get("secondary") or {}).get("evidence") or {}),
            "velocity_secondary_facts_error": ((critical_meta.get("secondary") or {}).get("error")),
            "semaforo_secondary_facts_version": core.get("semaforo_secondary_facts_version"),
            "semaforo_secondary_facts": core.get("semaforo_secondary_facts") or {},
            "semaforo_secondary_facts_confidence": core.get("semaforo_secondary_facts_confidence") or {},
            "semaforo_secondary_facts_evidence": core.get("semaforo_secondary_facts_evidence") or {},
            "critical_conflicts_resolved": conflicts,
            "missing_required_fields": critical_meta.get("missing_required") or [],
            "unresolved_critical_fields": critical_meta.get("unresolved_fields") or [],
            "ready_for_generate": bool(critical_meta.get("ready_for_generate")),
            "message": "Reanálisis completado. Extracción consolidada con texto, visión de página y zoom de campos críticos.",
        }

    except HTTPException as exc:
        _append_event(
            case_id,
            "case_reanalysis_failed",
            {"error": str(exc.detail), "status_code": exc.status_code, "extractor_version": _EXTRACTOR_VERSION},
        )
        raise
    except Exception as exc:
        _append_event(
            case_id,
            "case_reanalysis_failed",
            {"error": f"{type(exc).__name__}: {exc}", "extractor_version": _EXTRACTOR_VERSION},
        )
        raise HTTPException(
            status_code=500,
            detail=f"Error reanalizando expediente: {type(exc).__name__}: {exc}",
        )
