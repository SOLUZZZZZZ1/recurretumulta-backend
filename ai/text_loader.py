# ai/text_loader.py
# Descarga archivos desde B2 y extrae texto (PDF / imagen)

from typing import Optional
from io import BytesIO


def _download_bytes(bucket: str, key: str) -> bytes:
    """
    Descarga
