"""Compatibilidad temporal con el antiguo nombre ``b2_storage_patched``.

Toda la implementación vive en :mod:`b2_storage`, que aplica el interruptor de
capacidad RTM antes de leer credenciales o crear un cliente de red. Mantener una
segunda copia aquí permitiría eludir accidentalmente ese control.
"""

from b2_storage import (
    download_bytes,
    get_b2_bucket,
    get_s3_client,
    guess_ext,
    presign_get_url,
    upload_bytes,
    upload_original,
)


__all__ = [
    "download_bytes",
    "get_b2_bucket",
    "get_s3_client",
    "guess_ext",
    "presign_get_url",
    "upload_bytes",
    "upload_original",
]
