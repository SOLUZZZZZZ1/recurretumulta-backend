# ai/text_loader.py
# Descarga archivos desde B2 y extrae texto (PDF / imagen) sin romper el flujo.
#
# Versión BLINDADA:
# - Intenta extraer texto desde el propio archivo (PDF nativo / OCR local).
# - Si el texto es insuficiente (casos PDF escaneado / imagen) y el key contiene un case_id,
#   hace fallback a la última extracción guardada en BD (raw_text_blob/raw_text_pdf/raw_text_vision/vision_raw_text).
#
# Objetivo: que el clasificador y el motor SIEMPRE dispongan de texto legible y coherente del documento,
# evitando malas clasificaciones por falta de OCR.

from __future__ import annotations

import json
import re
from io import BytesIO
from typing import Optional


def _download_bytes(bucket: str, key: str) -> bytes:
    """Descarga binarios desde B2 usando distintas funciones posibles para
    ser compatible con tu b2_storage.py.
    """
    import b2_storage

    for fn_name in (
        "download_bytes",
        "get_bytes",
        "b2_download_bytes",
        "download_file_bytes",
    ):
        fn = getattr(b2_storage, fn_name, None)
        if callable(fn):
            return fn(bucket, key)

    raise RuntimeError(
        "No se encontró función de descarga en b2_storage "
        "(download_bytes/get_bytes/b2_download_bytes/download_file_bytes)."
    )


_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)


def _extract_case_id_from_key(key: str) -> Optional[str]:
    """Intenta inferir case_id (UUID) desde el B2 key."""
    if not key:
        return None
    m = _UUID_RE.search(key)
    return m.group(0) if m else None


def _load_latest_extraction_text(case_id: str) -> str:
    """Carga texto OCR/merged de la última extracción guardada en BD para el case_id.
    Nunca lanza excepción: devuelve '' si falla.
    """
    try:
        from database import get_engine
        from sqlalchemy import text

        engine = get_engine()
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT extracted_json FROM extractions WHERE case_id=:case_id ORDER BY created_at DESC LIMIT 1"),
                {"case_id": case_id},
            ).fetchone()

        if not row or not row[0]:
            return ""

        extracted_json = row[0]
        wrapper = extracted_json if isinstance(extracted_json, dict) else json.loads(extracted_json)

        core = (wrapper or {}).get("extracted") or {}
        if not isinstance(core, dict):
            return ""

        candidates = [
            core.get("raw_text_blob"),
            core.get("raw_text_pdf"),
            core.get("raw_text_vision"),
            core.get("vision_raw_text"),
        ]
        for c in candidates:
            if isinstance(c, str) and len(c.strip()) >= 200:
                return c.strip()
        for c in candidates:
            if isinstance(c, str) and c.strip():
                return c.strip()

        return ""
    except Exception:
        return ""


def load_text_from_b2(bucket: str, key: str, mime: Optional[str]) -> str:
    """Descarga el archivo desde B2 y extrae texto.
    - PDF nativo: extracción directa
    - Imagen/PDF escaneado: OCR local si existe
    - Fallback robusto: usar OCR/merge guardado en la última extracción del case_id (si se puede inferir)
    Nunca lanza excepción (devuelve '' si falla).
    """
    try:
        data = _download_bytes(bucket, key)
        if not data:
            return ""

        text_out = ""

        try:
            import text_extractors

            if hasattr(text_extractors, "extract_text_bytes"):
                text_out = (text_extractors.extract_text_bytes(data, mime=mime) or "").strip()
            else:
                text_out = (text_extractors.extract_text(BytesIO(data), mime=mime) or "").strip()
        except Exception:
            text_out = ""

        if text_out and len(text_out.strip()) >= 250:
            return text_out

        case_id = _extract_case_id_from_key(key)
        if case_id:
            fallback = _load_latest_extraction_text(case_id)
            if fallback:
                return fallback

        return text_out or ""

    except Exception:
        return ""
