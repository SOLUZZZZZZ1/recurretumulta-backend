"""Explicit local custody for synthetic documents; never an S3 client.

Objects are immutable, bounded envelopes containing size, SHA-256 and payload.
Directory descriptors (POSIX) or locked directory handles (Windows) keep path
resolution below the configured private directory without following links.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
import struct
from typing import Iterator, Mapping
import uuid

from rtm_core.local_operator_auth import assert_local_operator_auth_ready

LOCAL_DOCUMENT_STORAGE_ENV = "RTM_ENABLE_LOCAL_DOCUMENT_STORAGE"
LOCAL_DOCUMENT_BUCKET = "rtm-local-documents-v1"
MAX_LOCAL_DOCUMENT_BYTES = 64 * 1024 * 1024
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_HEADER = struct.Struct(">8sQ32s")
_MAGIC = b"RTMLDOC1"
_REPARSE_POINT = 0x400
_CODE_ROOT = Path(__file__).resolve().parent.parent
_WINDOWS_DEVICE_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class LocalDocumentStorageMisconfigured(RuntimeError):
    pass


class LocalDocumentTooLargeError(ValueError):
    pass


class LocalDocumentIntegrityError(ValueError):
    pass


def local_document_storage_requested(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    raw = str(source.get(LOCAL_DOCUMENT_STORAGE_ENV) or "").strip().casefold()
    return bool(raw) and raw not in _FALSE_VALUES


def _ordinary(info: os.stat_result, *, directory: bool) -> bool:
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    return expected(info.st_mode) and not (
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    )


def assert_local_document_storage_ready(
    environ: Mapping[str, str] | None = None,
) -> Path:
    source = os.environ if environ is None else environ
    assert_local_operator_auth_ready(source)
    if str(source.get(LOCAL_DOCUMENT_STORAGE_ENV) or "").strip().casefold() not in _TRUE_VALUES:
        raise LocalDocumentStorageMisconfigured("Custodia local no activada explícitamente")
    raw = str(source.get("RTM_LOCAL_DOCUMENT_ROOT") or "").strip()
    root = Path(raw)
    if (
        not raw or "\x00" in raw or not root.is_absolute()
        or raw.startswith(("\\\\", "//")) or ".." in root.parts
        or root == Path(root.anchor)
        or root == _CODE_ROOT or _CODE_ROOT in root.parents or root in _CODE_ROOT.parents
    ):
        raise LocalDocumentStorageMisconfigured("RTM_LOCAL_DOCUMENT_ROOT debe ser una carpeta local absoluta fuera del código")
    try:
        for directory in (root, *root.parents):
            if not _ordinary(directory.lstat(), directory=True):
                raise LocalDocumentStorageMisconfigured("La raíz local no admite enlaces ni puntos de reanálisis")
        resolved = root.resolve(strict=True)
        if resolved == _CODE_ROOT or _CODE_ROOT in resolved.parents or resolved in _CODE_ROOT.parents:
            raise LocalDocumentStorageMisconfigured("La carpeta local debe estar fuera del código")
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
            kernel.GetDriveTypeW.restype = wintypes.UINT
            if kernel.GetDriveTypeW(root.anchor) != 3:  # DRIVE_FIXED, never mapped network drives.
                raise LocalDocumentStorageMisconfigured("La raíz local requiere una unidad fija local")
    except (OSError, ValueError) as exc:
        raise LocalDocumentStorageMisconfigured("La carpeta de custodia local no está disponible") from exc
    return resolved


def local_document_storage_enabled(environ: Mapping[str, str] | None = None) -> bool:
    if not local_document_storage_requested(environ):
        return False
    assert_local_document_storage_ready(environ)
    return True


def validate_local_object_coordinate(
    bucket: str, key: str, *, case_id: str | None = None,
) -> tuple[str, str]:
    # Never strip or normalise a supplied coordinate into a different object.
    if bucket != LOCAL_DOCUMENT_BUCKET or not isinstance(key, str) or len(key) > 200:
        raise ValueError("Coordenada fuera de la custodia local")
    parts = key.split("/")
    if len(parts) != 4 or parts[0] != "cases":
        raise ValueError("Clave fuera del namespace local")
    try:
        canonical_case = str(uuid.UUID(parts[1]))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Expediente local inválido") from exc
    if (
        parts[1] != canonical_case
        or (case_id is not None and canonical_case != str(case_id))
        or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", parts[2])
        or parts[2] in _WINDOWS_DEVICE_NAMES
        or not re.fullmatch(r"[0-9a-f]{32}(?:\.[a-z0-9]{1,10})?", parts[3])
    ):
        raise ValueError("Clave fuera del expediente local autorizado")
    return bucket, key


@contextmanager
def _windows_directories(directories: list[tuple[Path, bool]]) -> Iterator[None]:
    """Hold every ancestor against write/delete opens, including junction edits."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handles = []
    try:
        for path, create in directories:
            # A fixed drive's root cannot be renamed like an ordinary directory.
            # Do not deny write/delete sharing globally on that volume anchor.
            if path == Path(path.anchor):
                if not _ordinary(path.lstat(), directory=True):
                    raise ValueError("Raíz de volumen local no válida")
                continue
            if create:
                path.mkdir(mode=0o700, exist_ok=True)
            handle = kernel.CreateFileW(
                str(path), 0x80, 1, None, 3, 0x02000000 | 0x00200000, None,
            )  # READ_ATTRIBUTES, SHARE_READ, OPEN_EXISTING, BACKUP_SEMANTICS|OPEN_REPARSE_POINT
            if handle == wintypes.HANDLE(-1).value:
                raise OSError(ctypes.get_last_error(), "No se pudo asegurar la carpeta local")
            handles.append(handle)
            if not _ordinary(path.lstat(), directory=True):
                raise ValueError("La custodia local no admite enlaces de directorio")
        yield
    finally:
        for handle in reversed(handles):
            kernel.CloseHandle(handle)


@contextmanager
def _object_directory(root: Path, key: str, *, create: bool = False) -> Iterator[tuple[Path, int | None]]:
    directory = root.joinpath(*key.split("/")[:-1])
    chain = list(reversed(root.parents)) + [root]
    existing = len(chain)
    current = root
    for part in key.split("/")[:-1]:
        current /= part
        chain.append(current)
    if os.name == "nt":
        with _windows_directories([(path, create and i >= existing) for i, path in enumerate(chain)]):
            yield directory, None
        return
    descriptors = []
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        for i, path in enumerate(chain):
            previous = descriptors[-1] if descriptors else None
            name = path.name if previous is not None else str(path)
            if create and i >= existing:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=previous)
                except FileExistsError:
                    pass
            descriptor = os.open(name, flags, dir_fd=previous)
            descriptors.append(descriptor)
        yield directory, descriptors[-1]
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _path_arg(directory: Path, fd: int | None, name: str):
    return directory / name if fd is None else name


def _read_object(directory: Path, fd: int | None, name: str, maximum: int) -> bytes:
    path = _path_arg(directory, fd, name)
    before = os.stat(path, dir_fd=fd, follow_symlinks=False)
    if not _ordinary(before, directory=False) or before.st_nlink != 1:
        raise ValueError("Objeto local no regular o enlazado")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), dir_fd=fd)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not _ordinary(info, directory=False) or info.st_nlink != 1 or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("La identidad del objeto local ha cambiado")
        if info.st_size > maximum + _HEADER.size:
            raise LocalDocumentTooLargeError("El documento local supera el límite permitido")
        header = stream.read(_HEADER.size)
        if len(header) != _HEADER.size:
            raise LocalDocumentIntegrityError("Cabecera de custodia local incompleta")
        magic, size, digest = _HEADER.unpack(header)
        if magic != _MAGIC or size < 1:
            raise LocalDocumentIntegrityError("Cabecera de custodia local inválida")
        if size > maximum:
            raise LocalDocumentTooLargeError("El documento local supera el límite permitido")
        data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise LocalDocumentTooLargeError("El documento local supera el límite permitido")
        if info.st_size != size + _HEADER.size or len(data) != size or not hmac.compare_digest(hashlib.sha256(data).digest(), digest):
            raise LocalDocumentIntegrityError("Integridad del documento local inválida")
        return data


def upload_bytes(case_id: str, kind_folder: str, content: bytes, ext: str, mime: str) -> tuple[str, str]:
    del mime  # Content type is kept by the application; local files are never served directly.
    root = assert_local_document_storage_ready()
    if not isinstance(content, bytes) or not content:
        raise ValueError("El documento local debe contener bytes")
    if len(content) > MAX_LOCAL_DOCUMENT_BYTES:
        raise LocalDocumentTooLargeError("El documento local supera el límite permitido")
    key = f"cases/{case_id}/{kind_folder}/{uuid.uuid4().hex}{ext}"
    validate_local_object_coordinate(LOCAL_DOCUMENT_BUCKET, key, case_id=case_id)
    name = key.rsplit("/", 1)[1]
    pending = f".rtm-pending-{uuid.uuid4().hex}"
    with _object_directory(root, key, create=True) as (directory, fd):
        temporary = _path_arg(directory, fd, pending)
        target = _path_arg(directory, fd, name)
        descriptor = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600, dir_fd=fd)
        published = False
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(_HEADER.pack(_MAGIC, len(content), hashlib.sha256(content).digest()))
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            # Both publish variants fail if the generated coordinate already exists.
            if fd is None:
                os.rename(temporary, target)  # Windows rename never replaces an existing file.
                published = True
            else:
                os.link(temporary, target, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                published = True
                os.unlink(temporary, dir_fd=fd)
        except Exception:
            if published:
                try:
                    os.unlink(target, dir_fd=fd)
                except OSError:
                    pass
            raise
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass
    return LOCAL_DOCUMENT_BUCKET, key


def download_bytes_limited(bucket: str, key: str, *, max_bytes: int, case_id: str | None = None) -> bytes:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_LOCAL_DOCUMENT_BYTES:
        raise ValueError("Límite de custodia local fuera del rango seguro")
    root = assert_local_document_storage_ready()
    validate_local_object_coordinate(bucket, key, case_id=case_id)
    with _object_directory(root, key) as (directory, fd):
        return _read_object(directory, fd, key.rsplit("/", 1)[1], max_bytes)


def download_bytes(bucket: str, key: str, *, case_id: str | None = None) -> bytes:
    return download_bytes_limited(bucket, key, max_bytes=MAX_LOCAL_DOCUMENT_BYTES, case_id=case_id)


def delete_object(bucket: str, key: str) -> None:
    root = assert_local_document_storage_ready()
    validate_local_object_coordinate(bucket, key)
    try:
        with _object_directory(root, key) as (directory, fd):
            target = _path_arg(directory, fd, key.rsplit("/", 1)[1])
            info = os.stat(target, dir_fd=fd, follow_symlinks=False)
            if not _ordinary(info, directory=False) or info.st_nlink != 1:
                raise ValueError("No se puede retirar un objeto local enlazado o no regular")
            os.unlink(target, dir_fd=fd)
    except FileNotFoundError:
        return  # Compensation is idempotent and only ever removes this coordinate.
