import io
from typing import Optional

from pypdf import PdfReader
from docx import Document


def extract_text_from_pdf_bytes(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    parts = []
    for page in reader.pages:
        t = page.extract_text() or ""
        parts.append(t)
    return "\n".join(parts).strip()


def extract_text_from_docx_bytes(content: bytes) -> str:
    doc = Document(io.BytesIO(content))
    return "\n".join(p.text for p in doc.paragraphs).strip()


def has_enough_text(text: Optional[str], min_chars: int = 500) -> bool:
    return bool(text and len(text.strip()) >= min_chars)
