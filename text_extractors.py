import io
import re
from typing import Optional

from pypdf import PdfReader
from docx import Document


# -----------------------------
# OCR/Text normalization (anti-typos)
# -----------------------------
def normalize_ocr_text(text: str) -> str:
    """Normaliza errores típicos de OCR para evitar interpretaciones absurdas.
    Mantener esta lista pequeña y muy específica.
    """
    if not text:
        return ""
    t = text

    # Ejemplos reales observados:
    # 'tambor' leído como 'trombo' (DGT)
    t = re.sub(r"\btrombo\b", "tambor", t, flags=re.IGNORECASE)

    # Comunes: espacios raros
    t = re.sub(r"[\u00A0\t]+", " ", t)
    t = re.sub(r" +", " ", t)

    return t


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
    raw = normalize_ocr_text(raw)  # ✅ aplica normalización OCR
    return _normalize_text(raw)


def extract_text_from_docx_bytes(content: bytes) -> str:
    doc = Document(io.BytesIO(content))
    raw = "\n".join(p.text for p in doc.paragraphs)
    raw = normalize_ocr_text(raw)  # ✅ también en docx (seguro, no rompe)
    return _normalize_text(raw)


def has_enough_text(text: Optional[str], min_chars: int = 500) -> bool:
    return bool(text and len(text.strip()) >= min_chars)
