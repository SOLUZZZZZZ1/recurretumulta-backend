# ai/text_loader.py
# Descarga archivos desde B2 y extrae texto (PDF / imagen) sin romper el flujo.

from typing import Optional
from io import BytesIO


def _download_bytes(bucket: str, key: str) -> bytes:
    """
    Descarga binarios desde B2 usando distintas funciones posibles para
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


def load_text_from_b2(bucket: str, key: str, mime: Optional[str]) -> str:
    """
    Descarga el archivo desde B2 y extrae texto.
    - PDF nativo: extracción directa
    - Imagen: OCR (si text_extractors lo soporta)
    Nunca lanza excepción (devuelve '' si falla).
    """
    try:
        data = _download_bytes(bucket, key)
        if not data:
            return ""

        import text_extractors

        # Preferimos extractor por bytes si existe
        if hasattr(text_extractors, "extract_text_bytes"):
            return (text_extractors.extract_text_bytes(data, mime=mime) or "").strip()

        # Fallback: extractor con file-like
        return (text_extractors.extract_text(BytesIO(data), mime=mime) or "").strip()

    except Exception:
        return ""
