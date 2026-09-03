import io
import re
import time
from typing import Optional

from pypdf import PdfReader

from rtm_core.upload_security import (
    extract_docx_text_bounded,
    validate_pdf_document,
)


_MAX_PDF_TEXT_PAGES = 50
_MAX_EXTRACTED_TEXT_CHARS = 250_000
_PDF_EXTRACTION_DEADLINE_SECONDS = 15.0


_ADMIN_LINE_STARTS = [
    "tipificacion",
    "tipificación",
    "clasificacion",
    "clasificación",
    "valor de la prueba",
    "aparato",
    "importe",
    "reduccion",
    "reducción",
    "bonificacion",
    "bonificación",
    "puntos",
    "agente",
    "total principal",
    "para ingresar",
    "datos vehiculo",
    "datos vehículo",
    "fecha limite",
    "fecha límite",
    "boletin",
    "boletín",
]


def normalize_ocr_text(text: str) -> str:
    """Normaliza errores típicos de OCR sin sobrecorregir."""
    if not text:
        return ""

    t = text

    # Correcciones muy específicas observadas
    t = re.sub(r"\btrombo\b", "tambor", t, flags=re.IGNORECASE)
    t = re.sub(r"\bsemaforo\b", "semaforo", t, flags=re.IGNORECASE)
    t = re.sub(r"\blinea\s+de\s+detencion\b", "linea de detencion", t, flags=re.IGNORECASE)
    t = re.sub(r"\bluz\s+roja\s+no\s+intermitente\b", "luz roja no intermitente", t, flags=re.IGNORECASE)
    t = re.sub(r"\btelefono\s+movil\b", "telefono movil", t, flags=re.IGNORECASE)

    # Quitar espacios raros / NBSP
    t = re.sub(r"[\u00A0\t]+", " ", t)
    t = re.sub(r"[ ]{2,}", " ", t)

    # Unificar saltos
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"\n{3,}", "\n\n", t)

    # OCR raro frecuente en boletines
    t = re.sub(r"\bS\.\s*NO\b", "NO", t, flags=re.IGNORECASE)
    t = re.sub(r"\bCLASIFICACION\b", "CLASIFICACION", t, flags=re.IGNORECASE)
    t = re.sub(r"\bTIPIFICACION\b", "TIPIFICACION", t, flags=re.IGNORECASE)

    return t.strip()


def strip_admin_noise(text: str) -> str:
    """Recorta ruido administrativo que suele contaminar el hecho."""
    if not text:
        return ""

    lines = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue

        low = line.lower()
        if any(low.startswith(prefix) for prefix in _ADMIN_LINE_STARTS):
            continue

        # Si dentro de una línea aparece un bloque administrativo, recortarlo
        for token in _ADMIN_LINE_STARTS:
            idx = low.find(token)
            if idx > 0:
                line = line[:idx].strip(" ,;:-")
                low = line.lower()

        if line:
            lines.append(line)

    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _normalize_text(t: str) -> str:
    t = (t or "").replace("\x00", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _extract_text_from_pdf_bytes_local(content: bytes) -> str:
    validate_pdf_document(content)
    reader = PdfReader(io.BytesIO(content))
    parts = []
    char_count = 0
    deadline = time.monotonic() + _PDF_EXTRACTION_DEADLINE_SECONDS
    for page_index, page in enumerate(reader.pages):
        if page_index >= _MAX_PDF_TEXT_PAGES:
            break
        if time.monotonic() > deadline:
            break
        t = page.extract_text() or ""
        remaining = _MAX_EXTRACTED_TEXT_CHARS - char_count
        if remaining <= 0:
            break
        value = t[:remaining]
        parts.append(value)
        char_count += len(value)

    raw = "\n".join(parts)
    raw = normalize_ocr_text(raw)
    raw = strip_admin_noise(raw)
    return _normalize_text(raw)


def _extract_text_from_docx_bytes_local(content: bytes) -> str:
    raw, _truncated = extract_docx_text_bounded(
        content,
        max_chars=_MAX_EXTRACTED_TEXT_CHARS,
    )
    raw = normalize_ocr_text(raw)
    raw = strip_admin_noise(raw)
    return _normalize_text(raw)


def _isolated_text(operation: str, content: bytes) -> str:
    from rtm_core.parser_isolation import (
        ParserRejected,
        run_parser_isolated,
    )
    from rtm_core.upload_security import UploadSecurityError

    try:
        value = run_parser_isolated(operation, {"data": bytes(content)})
    except ParserRejected as exc:
        raise UploadSecurityError(str(exc), status_code=exc.status_code) from exc
    if not isinstance(value, str) or len(value) > _MAX_EXTRACTED_TEXT_CHARS:
        from rtm_core.parser_isolation import ParserIsolationError

        raise ParserIsolationError("El extractor aislado devolvió una salida inválida")
    return value


async def _isolated_text_async(operation: str, content: bytes) -> str:
    from rtm_core.parser_isolation import (
        ParserIsolationError,
        ParserRejected,
        run_parser_isolated_async,
    )
    from rtm_core.upload_security import UploadSecurityError

    try:
        value = await run_parser_isolated_async(operation, {"data": bytes(content)})
    except ParserRejected as exc:
        raise UploadSecurityError(str(exc), status_code=exc.status_code) from exc
    if not isinstance(value, str) or len(value) > _MAX_EXTRACTED_TEXT_CHARS:
        raise ParserIsolationError("El extractor aislado devolvió una salida inválida")
    return value


def extract_text_from_pdf_bytes(content: bytes) -> str:
    """Extrae PDF con terminación real si una página deja de responder."""

    return _isolated_text("extract_pdf_text", content)


async def extract_text_from_pdf_bytes_isolated_async(content: bytes) -> str:
    return await _isolated_text_async("extract_pdf_text", content)


def extract_text_from_docx_bytes(content: bytes) -> str:
    return _isolated_text("extract_docx_text", content)


async def extract_text_from_docx_bytes_isolated_async(content: bytes) -> str:
    return await _isolated_text_async("extract_docx_text", content)


def has_enough_text(text: Optional[str], min_chars: int = 500) -> bool:
    return bool(text and len(text.strip()) >= min_chars)
