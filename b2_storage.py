import os
import uuid
from typing import Optional, Tuple

import boto3
from botocore.config import Config


def _env(name: str) -> str:
    v = (os.getenv(name) or "").strip()
    if not v:
        raise RuntimeError(f"Falta variable de entorno: {name}")
    return v


def get_b2_bucket() -> str:
    return _env("B2_BUCKET")


def get_s3_client():
    endpoint = _env("B2_ENDPOINT")
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

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=content,
        ContentType=mime or "application/octet-stream",
    )
    return bucket, key


def upload_original(case_id: str, content: bytes, filename: Optional[str], mime: str) -> Tuple[str, str]:
    ext = guess_ext(filename, mime)
    return upload_bytes(case_id, "original", content, ext or "", mime)


def download_bytes(bucket: str, key: str) -> bytes:
    """
    Descarga el objeto completo como bytes desde B2 (S3 compatible).
    """
    s3 = get_s3_client()
    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj.get("Body")
    return body.read() if body else b""

def presign_get_url(bucket: str, key: str, expires_seconds: int = 300, filename: Optional[str] = None) -> str:
    """
    Genera una URL temporal (presigned) para descargar desde B2.
    """
    s3 = get_s3_client()
    params = {"Bucket": bucket, "Key": key}
    if filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params=params,
        ExpiresIn=int(expires_seconds),
    )
