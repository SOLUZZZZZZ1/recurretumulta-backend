import io
import re
from typing import Optional

from pypdf import PdfReader
from docx import Document


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


def extract_text_from_pdf_bytes(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    parts = []
    for page in reader.pages:
        t = page.extract_text() or ""
        parts.append(t)

    raw = "\n".join(parts)
    raw = normalize_ocr_text(raw)
    raw = strip_admin_noise(raw)
    return _normalize_text(raw)


def extract_text_from_docx_bytes(content: bytes) -> str:
    doc = Document(io.BytesIO(content))
    raw = "\n".join(p.text for p in doc.paragraphs)
    raw = normalize_ocr_text(raw)
    raw = strip_admin_noise(raw)
    return _normalize_text(raw)


def has_enough_text(text: Optional[str], min_chars: int = 500) -> bool:
    return bool(text and len(text.strip()) >= min_chars)
