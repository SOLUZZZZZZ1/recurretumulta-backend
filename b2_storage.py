import os
import re
import uuid
from typing import Optional, Tuple
from urllib.parse import urlsplit

import boto3
from botocore.config import Config

from rtm_core.runtime_capabilities import require_capability


class B2ObjectTooLargeError(ValueError):
    pass


def _env(name: str) -> str:
    v = (os.getenv(name) or "").strip()
    if not v:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return v


def get_b2_bucket() -> str:
    return _env("B2_BUCKET")


def _validated_b2_endpoint(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    hostname = str(parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or not re.fullmatch(r"s3\.[a-z0-9-]+\.backblazeb2\.com", hostname)
        or parsed.port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("B2_ENDPOINT no pertenece al servicio Backblaze B2")
    return f"https://{hostname}"


def validate_b2_object_coordinate(
    bucket: str,
    key: str,
    *,
    case_id: str | None = None,
) -> tuple[str, str]:
    """Constrain every B2 read/write helper to RTM's private case namespace."""

    expected_bucket = get_b2_bucket()
    clean_bucket = str(bucket or "").strip()
    clean_key = str(key or "").strip()
    parts = clean_key.split("/")
    if clean_bucket != expected_bucket:
        raise ValueError("Bucket B2 fuera del namespace RTM")
    if (
        len(parts) < 4
        or parts[0] != "cases"
        or any(not part or part in {".", ".."} for part in parts)
        or "\\" in clean_key
        or "\x00" in clean_key
    ):
        raise ValueError("Clave B2 fuera del namespace RTM")
    if case_id is not None and parts[1] != str(case_id):
        raise ValueError("Clave B2 fuera del expediente autorizado")
    return clean_bucket, clean_key


def get_s3_client():
    # En staging/producción B2 es opt-in. La comprobación se hace antes de
    # leer credenciales o construir un cliente con capacidad de red.
    require_capability("b2")

    endpoint = _validated_b2_endpoint(_env("B2_ENDPOINT"))
    key_id = _env("B2_KEY_ID")
    app_key = _env("B2_APPLICATION_KEY")

    cfg = Config(signature_version="s3v4", s3={"addressing_style": "path"})

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=app_key,
        config=cfg,
    )


def guess_ext(filename: Optional[str], mime: Optional[str]) -> str:
    fn = (filename or "").lower()
    if fn.endswith(".pdf"):
        return ".pdf"
    if fn.endswith(".png"):
        return ".png"
    if fn.endswith(".jpg") or fn.endswith(".jpeg"):
        return ".jpg"
    if fn.endswith(".webp"):
        return ".webp"
    if fn.endswith(".docx"):
        return ".docx"
    if mime == "application/pdf":
        return ".pdf"
    if mime == "image/png":
        return ".png"
    if mime in ("image/jpg", "image/jpeg"):
        return ".jpg"
    if mime == "image/webp":
        return ".webp"
    if mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return ".docx"
    return ""


def upload_bytes(case_id: str, kind_folder: str, content: bytes, ext: str, mime: str) -> Tuple[str, str]:
    bucket = get_b2_bucket()
    s3 = get_s3_client()
    key = f"cases/{case_id}/{kind_folder}/{uuid.uuid4().hex}{ext}"

    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=content,
            ContentType=mime or "application/octet-stream",
        )
    except Exception:
        # Un timeout puede llegar después de que B2 haya aceptado el objeto. La
        # clave ya es conocida, por lo que intentamos retirar ese posible
        # huérfano sin ocultar el fallo original. Los flujos multiarchivo
        # compensan además los objetos anteriores que sí fueron confirmados.
        try:
            s3.delete_object(Bucket=bucket, Key=key)
        except Exception:
            pass
        raise
    return bucket, key


def delete_object(bucket: str, key: str) -> None:
    """Elimina únicamente un objeto bajo el namespace privado ``cases/``.

    Las coordenadas proceden siempre de una subida confirmada. La comprobación
    de bucket y path evita convertir este helper de compensación en una
    primitiva de borrado arbitrario si se reutiliza por error.
    """

    clean_bucket, clean_key = validate_b2_object_coordinate(bucket, key)
    get_s3_client().delete_object(Bucket=clean_bucket, Key=clean_key)


def upload_original(case_id: str, content: bytes, filename: Optional[str], mime: str) -> Tuple[str, str]:
    ext = guess_ext(filename, mime)
    return upload_bytes(case_id, "original", content, ext or "", mime)


def download_bytes(bucket: str, key: str, *, case_id: str | None = None) -> bytes:
    """
    Descarga el objeto completo como bytes desde B2 (S3 compatible).
    """
    clean_bucket, clean_key = validate_b2_object_coordinate(
        bucket, key, case_id=case_id
    )
    s3 = get_s3_client()
    obj = s3.get_object(Bucket=clean_bucket, Key=clean_key)
    body = obj.get("Body")
    return body.read() if body else b""


def download_bytes_limited(
    bucket: str,
    key: str,
    *,
    max_bytes: int,
    case_id: str | None = None,
) -> bytes:
    """Descarga como máximo ``max_bytes + 1`` y aborta el stream al excederlo."""

    if not 1 <= int(max_bytes) <= 64 * 1024 * 1024:
        raise ValueError("Límite B2 fuera del rango seguro")
    clean_bucket, clean_key = validate_b2_object_coordinate(
        bucket, key, case_id=case_id
    )
    s3 = get_s3_client()
    obj = s3.get_object(Bucket=clean_bucket, Key=clean_key)
    body = obj.get("Body")
    if body is None:
        return b""
    try:
        data = body.read(int(max_bytes) + 1)
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()
    if len(data) > max_bytes:
        raise B2ObjectTooLargeError(
            f"El objeto B2 supera el límite de {max_bytes} bytes"
        )
    return data


def presign_get_url(bucket: str, key: str, expires_seconds: int = 300, filename: Optional[str] = None) -> str:
    """
    Genera una URL temporal (presigned) para descargar desde B2.
    """
    clean_bucket, clean_key = validate_b2_object_coordinate(bucket, key)
    s3 = get_s3_client()
    requested_name = str(filename or clean_key.rsplit("/", 1)[-1] or "documento.bin")
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", requested_name).strip("._")
    safe_name = (safe_name or "documento.bin")[:120]
    params = {
        "Bucket": clean_bucket,
        "Key": clean_key,
        "ResponseContentDisposition": f'attachment; filename="{safe_name}"',
        # Stored client documents are untrusted.  Force download and prevent a
        # browser from rendering attacker-controlled HTML/SVG under B2 origin.
        "ResponseContentType": "application/octet-stream",
    }
    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params=params,
        ExpiresIn=int(expires_seconds),
    )
