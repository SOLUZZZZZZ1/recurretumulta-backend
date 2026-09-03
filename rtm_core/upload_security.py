"""Validación por contenido y lectura acotada de documentos no confiables."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import re
import warnings
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable

try:
    from PIL import Image
except Exception:  # pragma: no cover - el runtime debe instalar Pillow
    Image = None

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - el runtime debe instalar pypdf
    PdfReader = None


PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
JPEG = "image/jpeg"
PNG = "image/png"
WEBP = "image/webp"
TIFF = "image/tiff"

SAFE_DOCUMENT_MIMES = frozenset({PDF, DOCX, JPEG, PNG, WEBP, TIFF})
SAFE_IMAGE_OR_PDF_MIMES = frozenset({PDF, JPEG, PNG, WEBP, TIFF})

_EXTENSIONS = {
    PDF: frozenset({".pdf"}),
    DOCX: frozenset({".docx"}),
    JPEG: frozenset({".jpg", ".jpeg"}),
    PNG: frozenset({".png"}),
    WEBP: frozenset({".webp"}),
    TIFF: frozenset({".tif", ".tiff"}),
}
_CANONICAL_EXTENSION = {
    PDF: ".pdf",
    DOCX: ".docx",
    JPEG: ".jpg",
    PNG: ".png",
    WEBP: ".webp",
    TIFF: ".tiff",
}
_MIME_ALIASES = {
    "image/jpg": JPEG,
    "application/x-pdf": PDF,
    "application/octet-stream": "application/octet-stream",
}
_MAX_DOCX_ENTRIES = 500
_MAX_DOCX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
_MAX_DOCX_COMPRESSION_RATIO = 200
_MAX_PDF_PAGES = 100
_MAX_PDF_OBJECTS = 5_000
_MAX_IMAGE_PIXELS = 20_000_000
_MAX_IMAGE_DIMENSION = 12_000
_MAX_IMAGE_FRAMES = 4
_PDF_ACTIVE_MARKERS = (
    b"/JavaScript",
    b"/EmbeddedFile",
    b"/OpenAction",
    b"/Launch",
    b"/RichMedia",
    b"/AA",
    b"/XFA",
    b"/SubmitForm",
    b"/ImportData",
)
_PDF_ACTIVE_NAMES = frozenset(
    {
        "/AA",
        "/OpenAction",
        "/JS",
        "/JavaScript",
        "/Launch",
        "/RichMedia",
        "/EmbeddedFile",
        "/EmbeddedFiles",
        "/XFA",
        "/SubmitForm",
        "/ImportData",
        "/GoToR",
    }
)
_DOCX_ACTIVE_PATH_PARTS = (
    "word/activex/",
    "word/embeddings/",
    "customui/",
)
_DOCX_ACTIVE_XML_PATTERNS = (
    br"<!DOCTYPE",
    br"<!ENTITY",
    br"<w:altChunk\b",
    br"\bDDEAUTO\b",
    br"\bDDE\s+",
    br"\bINCLUDETEXT\b",
    br"\bINCLUDEPICTURE\b",
)


class UploadSecurityError(ValueError):
    def __init__(self, message: str, *, status_code: int = 415) -> None:
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    mime: str
    extension: str
    size_bytes: int
    sha256: str


def safe_filename(value: str | None, *, fallback: str = "documento") -> str:
    filename = str(value or fallback).replace("/", "_").replace("\\", "_")
    filename = "".join(ch for ch in filename if ch.isprintable() and ch != "\x00")
    filename = re.sub(r"\s+", " ", filename).strip(" .")
    return (filename or fallback)[:140]


async def read_upload_limited(upload: Any, *, max_bytes: int) -> bytes:
    if not 1 <= int(max_bytes) <= 64 * 1024 * 1024:
        raise ValueError("Limite de archivo fuera del rango seguro")
    data = await upload.read(int(max_bytes) + 1)
    if len(data) > max_bytes:
        raise UploadSecurityError(
            f"El archivo supera el limite permitido de {max_bytes} bytes",
            status_code=413,
        )
    if not data:
        raise UploadSecurityError("El archivo esta vacio", status_code=400)
    return data


def _looks_like_docx(data: bytes) -> bool:
    if not data.startswith(b"PK"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return False
    return "[Content_Types].xml" in names and "word/document.xml" in names


def detect_document_mime(data: bytes) -> str | None:
    if data.startswith(b"%PDF-"):
        return PDF
    if data.startswith(b"\xff\xd8\xff"):
        return JPEG
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return WEBP
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return TIFF
    if _looks_like_docx(data):
        return DOCX
    return None


def validate_docx_archive(data: bytes) -> None:
    """Bloquea traversal, macros, objetos y relaciones externas en DOCX."""

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (OSError, zipfile.BadZipFile) as exc:
        raise UploadSecurityError("El DOCX no es un contenedor ZIP valido") from exc
    with archive:
        entries = archive.infolist()
        if len(entries) > _MAX_DOCX_ENTRIES:
            raise UploadSecurityError("El DOCX contiene demasiadas entradas", status_code=422)
        total = 0
        names: set[str] = set()
        for entry in entries:
            name = entry.filename
            path = PurePosixPath(name)
            if (
                not name
                or name.startswith(("/", "\\"))
                or "\\" in name
                or ".." in path.parts
                or entry.flag_bits & 0x1
            ):
                raise UploadSecurityError("El DOCX contiene una ruta o entrada insegura")
            lower = name.casefold()
            if (
                "vbaproject" in lower
                or any(part in lower for part in _DOCX_ACTIVE_PATH_PARTS)
                or "afchunk" in lower
                or lower.endswith((".bin", ".vml"))
            ):
                raise UploadSecurityError("El DOCX contiene contenido activo o incrustado")
            unix_mode = (entry.external_attr >> 16) & 0o170000
            if unix_mode == 0o120000:
                raise UploadSecurityError("El DOCX contiene enlaces simbolicos")
            total += entry.file_size
            if total > _MAX_DOCX_UNCOMPRESSED_BYTES:
                raise UploadSecurityError("El DOCX se expande por encima del limite", status_code=413)
            if entry.file_size and (
                entry.compress_size == 0
                or entry.file_size / entry.compress_size > _MAX_DOCX_COMPRESSION_RATIO
            ):
                raise UploadSecurityError("El DOCX tiene una compresion anomala", status_code=413)
            names.add(name)
        if len(names) != len(entries):
            raise UploadSecurityError("El DOCX contiene entradas duplicadas")
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise UploadSecurityError("El archivo no es un DOCX valido")
        for entry in entries:
            if not entry.filename.casefold().endswith(".rels"):
                continue
            if entry.file_size > 1024 * 1024:
                raise UploadSecurityError("Relaciones DOCX sobredimensionadas", status_code=413)
            relationship_xml = archive.read(entry)
            if re.search(
                br"TargetMode\s*=\s*['\"]External['\"]",
                relationship_xml,
                flags=re.I,
            ):
                raise UploadSecurityError("El DOCX contiene relaciones externas")
        for entry in entries:
            if not entry.filename.casefold().endswith((".xml", ".rels")):
                continue
            if entry.file_size > 4 * 1024 * 1024:
                raise UploadSecurityError("XML DOCX sobredimensionado", status_code=413)
            xml = archive.read(entry)
            if any(re.search(pattern, xml, flags=re.I) for pattern in _DOCX_ACTIVE_XML_PATTERNS):
                raise UploadSecurityError("El DOCX contiene XML activo o no permitido")


def extract_docx_text_bounded(
    data: bytes,
    *,
    max_chars: int = 120_000,
    max_nodes: int = 50_000,
) -> tuple[str, bool]:
    """Extract DOCX text incrementally with strict work/output budgets."""

    if not 1 <= int(max_chars) <= 500_000 or not 1 <= int(max_nodes) <= 100_000:
        raise ValueError("Presupuesto DOCX fuera del rango seguro")
    validate_docx_archive(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml")

    parts: list[str] = []
    char_count = 0
    node_count = 0
    truncated = False
    try:
        iterator = ET.iterparse(io.BytesIO(xml), events=("end",))
        for _event, element in iterator:
            node_count += 1
            if node_count > max_nodes:
                truncated = True
                break
            tag = str(element.tag)
            value = ""
            if tag.endswith("}t") or tag == "t":
                value = str(element.text or "")
            elif tag.endswith("}tab") or tag == "tab":
                value = "\t"
            elif tag.endswith("}br") or tag == "br":
                value = "\n"
            elif tag.endswith("}p") or tag == "p":
                value = "\n"
            if value:
                remaining = max_chars - char_count
                if remaining <= 0:
                    truncated = True
                    break
                if len(value) > remaining:
                    value = value[:remaining]
                    truncated = True
                parts.append(value)
                char_count += len(value)
            element.clear()
            if truncated:
                break
    except ET.ParseError as exc:
        raise UploadSecurityError("El XML principal del DOCX no es válido", status_code=422) from exc
    return "".join(parts).strip(), truncated


def _validate_pdf_object_graph(reader: Any) -> None:
    """Walk a bounded parsed graph so compressed active actions cannot hide."""

    xref = getattr(reader, "xref", {}) or {}
    object_count = sum(len(entries or {}) for entries in xref.values())
    if object_count > _MAX_PDF_OBJECTS:
        raise UploadSecurityError("El PDF contiene demasiados objetos", status_code=422)

    seen: set[tuple[int, int] | int] = set()
    stack: list[tuple[Any, int]] = [(getattr(reader, "trailer", {}), 0)]
    visited = 0
    while stack:
        value, depth = stack.pop()
        if depth > 40:
            raise UploadSecurityError("El PDF tiene una estructura demasiado profunda")

        indirect_id = getattr(value, "idnum", None)
        generation = getattr(value, "generation", None)
        if indirect_id is not None:
            marker = (int(indirect_id), int(generation or 0))
            if marker in seen:
                continue
            seen.add(marker)
            try:
                value = value.get_object()
            except Exception as exc:
                raise UploadSecurityError("El PDF contiene referencias inválidas") from exc
        elif isinstance(value, (dict, list, tuple)):
            marker = id(value)
            if marker in seen:
                continue
            seen.add(marker)

        visited += 1
        if visited > _MAX_PDF_OBJECTS:
            raise UploadSecurityError("El PDF excede el presupuesto de complejidad")
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key) in _PDF_ACTIVE_NAMES:
                    raise UploadSecurityError("El PDF contiene acciones activas no permitidas")
                stack.append((child, depth + 1))
        elif isinstance(value, (list, tuple)):
            stack.extend((child, depth + 1) for child in value)


def validate_pdf_document(data: bytes) -> None:
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-4096:]:
        raise UploadSecurityError("El PDF está truncado o no tiene estructura completa")
    if any(marker in data for marker in _PDF_ACTIVE_MARKERS):
        raise UploadSecurityError("El PDF contiene acciones o adjuntos no permitidos")
    if PdfReader is None:
        raise UploadSecurityError(
            "El validador PDF no está disponible",
            status_code=503,
        )
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise UploadSecurityError("Los PDF cifrados no están permitidos")
        page_count = len(reader.pages)
        _validate_pdf_object_graph(reader)
    except UploadSecurityError:
        raise
    except Exception as exc:
        raise UploadSecurityError("El PDF no puede validarse", status_code=422) from exc
    if page_count < 1 or page_count > _MAX_PDF_PAGES:
        raise UploadSecurityError(
            f"El PDF debe contener entre 1 y {_MAX_PDF_PAGES} páginas",
            status_code=422,
        )


def validate_image_document(data: bytes, expected_mime: str) -> None:
    if Image is None:
        raise UploadSecurityError(
            "El validador de imágenes no está disponible",
            status_code=503,
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                frames = int(getattr(image, "n_frames", 1) or 1)
                detected_format = str(image.format or "").upper()
                image.verify()
    except Exception as exc:
        raise UploadSecurityError("La imagen no puede validarse", status_code=422) from exc
    if (
        width < 1
        or height < 1
        or width > _MAX_IMAGE_DIMENSION
        or height > _MAX_IMAGE_DIMENSION
        or width * height > _MAX_IMAGE_PIXELS
        or frames > _MAX_IMAGE_FRAMES
    ):
        raise UploadSecurityError("La imagen supera los límites de complejidad", status_code=422)
    expected_formats = {
        JPEG: {"JPEG"},
        PNG: {"PNG"},
        WEBP: {"WEBP"},
        TIFF: {"TIFF"},
    }
    if detected_format not in expected_formats.get(expected_mime, set()):
        raise UploadSecurityError("El formato interno de la imagen no coincide")


def _validate_document_bytes_local(
    *,
    filename: str | None,
    declared_mime: str | None,
    data: bytes,
    max_bytes: int,
    allowed_mimes: Iterable[str] = SAFE_DOCUMENT_MIMES,
) -> ValidatedUpload:
    if not data:
        raise UploadSecurityError("El archivo esta vacio", status_code=400)
    if len(data) > max_bytes:
        raise UploadSecurityError(
            f"El archivo supera el limite permitido de {max_bytes} bytes",
            status_code=413,
        )
    detected = detect_document_mime(data)
    if detected is None:
        raise UploadSecurityError("El contenido del archivo no corresponde a un formato permitido")
    if detected not in set(allowed_mimes):
        raise UploadSecurityError(f"Formato no permitido: {detected}")

    clean_name = safe_filename(filename)
    suffix = PurePosixPath(clean_name).suffix.casefold()
    if suffix not in _EXTENSIONS[detected]:
        raise UploadSecurityError("La extension no coincide con el contenido del archivo")

    normalized_declared = str(declared_mime or "application/octet-stream").split(";", 1)[0]
    normalized_declared = _MIME_ALIASES.get(
        normalized_declared.strip().casefold(),
        normalized_declared.strip().casefold(),
    )
    if normalized_declared not in {detected, "application/octet-stream", ""}:
        raise UploadSecurityError("El tipo MIME declarado no coincide con el contenido")

    if detected == DOCX:
        validate_docx_archive(data)
    elif detected == PDF:
        validate_pdf_document(data)
    elif detected in {JPEG, PNG, WEBP, TIFF}:
        validate_image_document(data, detected)

    return ValidatedUpload(
        filename=clean_name,
        mime=detected,
        extension=_CANONICAL_EXTENSION[detected],
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _validated_upload_from_isolated(
    value: Any,
    *,
    data: bytes,
    filename: str | None,
    allowed_mimes: Iterable[str],
) -> ValidatedUpload:
    if not isinstance(value, dict):
        raise UploadSecurityError(
            "El validador documental no produjo un resultado verificable",
            status_code=503,
        )
    try:
        returned_filename = str(value["filename"])
        mime = str(value["mime"])
        extension = str(value["extension"])
        size_bytes = int(value["size_bytes"])
        sha256 = str(value["sha256"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UploadSecurityError(
            "El validador documental no produjo un resultado verificable",
            status_code=503,
        ) from exc
    if (
        mime not in SAFE_DOCUMENT_MIMES
        or mime not in {str(item) for item in allowed_mimes}
        or extension != _CANONICAL_EXTENSION.get(mime)
        or returned_filename != safe_filename(filename)
        or size_bytes != len(data)
        or not re.fullmatch(r"[0-9a-f]{64}", sha256)
        or not hmac.compare_digest(sha256, hashlib.sha256(data).hexdigest())
    ):
        raise UploadSecurityError(
            "El validador documental no produjo un resultado verificable",
            status_code=503,
        )
    return ValidatedUpload(
        filename=returned_filename,
        mime=mime,
        extension=extension,
        size_bytes=size_bytes,
        sha256=sha256,
    )


def _isolated_validation_payload(
    *,
    filename: str | None,
    declared_mime: str | None,
    data: bytes,
    max_bytes: int,
    allowed_mimes: Iterable[str],
) -> dict[str, Any]:
    return {
        "filename": filename,
        "declared_mime": declared_mime,
        "data": bytes(data),
        "max_bytes": int(max_bytes),
        "allowed_mimes": sorted({str(item) for item in allowed_mimes}),
    }


def validate_document_bytes(
    *,
    filename: str | None,
    declared_mime: str | None,
    data: bytes,
    max_bytes: int,
    allowed_mimes: Iterable[str] = SAFE_DOCUMENT_MIMES,
) -> ValidatedUpload:
    """Valida bytes no confiables en un proceso desechable y acotado."""

    from rtm_core.parser_isolation import (
        ParserIsolationError,
        ParserRejected,
        run_parser_isolated,
    )

    allowed_mime_values = tuple(str(item) for item in allowed_mimes)
    try:
        value = run_parser_isolated(
            "validate_document",
            _isolated_validation_payload(
                filename=filename,
                declared_mime=declared_mime,
                data=data,
                max_bytes=max_bytes,
                allowed_mimes=allowed_mime_values,
            ),
        )
    except ParserRejected as exc:
        raise UploadSecurityError(str(exc), status_code=exc.status_code) from exc
    except ParserIsolationError as exc:
        raise UploadSecurityError(
            "Validación documental temporalmente no disponible",
            status_code=503,
        ) from exc
    return _validated_upload_from_isolated(
        value,
        data=data,
        filename=filename,
        allowed_mimes=allowed_mime_values,
    )


async def validate_document_bytes_isolated_async(
    *,
    filename: str | None,
    declared_mime: str | None,
    data: bytes,
    max_bytes: int,
    allowed_mimes: Iterable[str] = SAFE_DOCUMENT_MIMES,
) -> ValidatedUpload:
    """Valida sin bloquear el event loop que atiende la petición ASGI."""

    return await asyncio.to_thread(
        validate_document_bytes,
        filename=filename,
        declared_mime=declared_mime,
        data=data,
        max_bytes=max_bytes,
        allowed_mimes=tuple(str(item) for item in allowed_mimes),
    )
