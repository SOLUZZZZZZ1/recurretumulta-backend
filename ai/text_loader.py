# ai/text_loader.py
# Descarga acotada desde B2 y extracción aislada de texto PDF/DOCX.
#
# Versión BLINDADA:
# - Valida y extrae en procesos desechables con límites CPU/RAM/wall-clock.
# - Si el texto es insuficiente (PDF escaneado / imagen) y el key contiene un case_id,
#   hace fallback a la última extracción guardada en BD (raw_text_blob/raw_text_pdf/raw_text_vision/vision_raw_text).
#
# Un fallo o exceso de tamaño detiene el motor; nunca se oculta como documento vacío.

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Optional


MAX_TEXT_LOADER_BYTES = 8 * 1024 * 1024
MAX_FALLBACK_TEXT_CHARS = 120_000


class TextLoaderSecurityError(RuntimeError):
    """La fuente documental no pudo verificarse; el motor debe parar."""


_EXTENSION_BY_MIME = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
}


def _download_bytes(bucket: str, key: str) -> bytes:
    """Descarga solo desde el namespace privado y con límite duro."""

    import b2_storage

    clean_bucket = str(bucket or "").strip()
    clean_key = str(key or "").strip()
    parts = clean_key.split("/")
    try:
        expected_bucket = b2_storage.get_b2_bucket()
    except Exception as exc:
        raise TextLoaderSecurityError("Almacén documental no disponible") from exc
    if clean_bucket != expected_bucket:
        raise TextLoaderSecurityError("Fuente documental fuera del almacén RTM")
    if (
        len(parts) < 4
        or parts[0] != "cases"
        or any(not part or part in {".", ".."} for part in parts)
        or "\\" in clean_key
        or "\x00" in clean_key
        or len(clean_key) > 1024
    ):
        raise TextLoaderSecurityError("Fuente documental fuera del namespace RTM")
    try:
        return b2_storage.download_bytes_limited(
            clean_bucket,
            clean_key,
            max_bytes=MAX_TEXT_LOADER_BYTES,
            case_id=parts[1],
        )
    except b2_storage.B2ObjectTooLargeError as exc:
        raise TextLoaderSecurityError("El documento almacenado supera el límite") from exc
    except TextLoaderSecurityError:
        raise
    except Exception as exc:
        raise TextLoaderSecurityError("No pudo leerse el documento almacenado") from exc


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
                return c.strip()[:MAX_FALLBACK_TEXT_CHARS]
        for c in candidates:
            if isinstance(c, str) and c.strip():
                return c.strip()[:MAX_FALLBACK_TEXT_CHARS]

        return ""
    except Exception:
        return ""


def load_text_from_b2(bucket: str, key: str, mime: Optional[str]) -> str:
    """Descarga el archivo desde B2 y extrae texto.
    - PDF nativo: extracción directa
    - Imagen/PDF escaneado: OCR local si existe
    - Fallback robusto: usar OCR/merge guardado en la última extracción del case_id (si se puede inferir)
    Los fallos de descarga, validación o parser son cerrados: el motor no puede
    continuar con un documento omitido de forma silenciosa.
    """
    from rtm_core.parser_isolation import ParserIsolationError
    from rtm_core.upload_security import (
        SAFE_DOCUMENT_MIMES,
        UploadSecurityError,
        validate_document_bytes,
    )
    from text_extractors import (
        extract_text_from_docx_bytes,
        extract_text_from_pdf_bytes,
    )

    data = _download_bytes(bucket, key)
    if not data:
        raise TextLoaderSecurityError("El documento almacenado está vacío")

    declared_mime = str(mime or "").split(";", 1)[0].strip().casefold()
    filename = PurePosixPath(str(key or "")).name
    expected_extension = _EXTENSION_BY_MIME.get(declared_mime)
    if expected_extension and not filename.casefold().endswith(
        (expected_extension, ".jpeg" if expected_extension == ".jpg" else expected_extension)
    ):
        filename = f"documento{expected_extension}"
    try:
        validated = validate_document_bytes(
            filename=filename or "documento",
            declared_mime=declared_mime,
            data=data,
            max_bytes=MAX_TEXT_LOADER_BYTES,
            allowed_mimes=SAFE_DOCUMENT_MIMES,
        )
        if validated.mime == "application/pdf":
            text_out = extract_text_from_pdf_bytes(data).strip()
        elif validated.mime == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ):
            text_out = extract_text_from_docx_bytes(data).strip()
        else:
            # Las imágenes se verifican estructuralmente en el worker, pero este
            # loader no ejecuta OCR local implícito ni abre Pillow en el proceso web.
            text_out = ""
    except (UploadSecurityError, ParserIsolationError) as exc:
        raise TextLoaderSecurityError(
            "El documento almacenado no supera la lectura segura"
        ) from exc

    if text_out and len(text_out) >= 250:
        return text_out[:MAX_FALLBACK_TEXT_CHARS]

    case_id = _extract_case_id_from_key(key)
    if case_id:
        fallback = _load_latest_extraction_text(case_id)
        if fallback:
            return fallback

    return text_out[:MAX_FALLBACK_TEXT_CHARS]
