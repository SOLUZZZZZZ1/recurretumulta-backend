"""Aislamiento desechable para parsers de documentos no confiables.

Los límites lógicos dentro de un parser no pueden interrumpir una única página,
un objeto comprimido o una llamada nativa que deje de responder. Este módulo
ejecuta únicamente operaciones de parseo incluidas en una lista cerrada dentro
de un proceso independiente. El padre aplica un límite de pared y siempre
termina al worker antes de devolver el control.

En Linux se instalan además límites de CPU, espacio de direcciones, ficheros y
descriptores antes de importar Pillow/pypdf. Staging y producción fallan
cerrado si el sistema operativo no permite aplicar esos límites.

Esta frontera endurece un proceso desechable, pero no afirma aislamiento del
host: la denegación de red en Python no sustituye namespaces/seccomp ni un
filesystem separado. Esas garantías deben aplicarse en la infraestructura.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import multiprocessing
import os
import socket
import threading
from dataclasses import dataclass
from typing import Any, Mapping


_MAX_REQUEST_BYTES = 64 * 1024 * 1024
_MAX_RESULT_BYTES = 16 * 1024 * 1024
_MAX_JOIN_SECONDS = 0.25
_OPERATIONS = frozenset(
    {
        "validate_document",
        "extract_pdf_text",
        "extract_docx_text",
        "pdf_page_count",
        "canonicalize_image",
        "convert_tiff_png",
        "crop_fet_image",
        "require_docx_main_content_type",
        "_readiness_probe",
        # Ganchos no alcanzables desde HTTP; solo se habilitan explícitamente
        # para probar muerte, timeout y presión de memoria del supervisor.
        "_test_crash",
        "_test_sleep",
        "_test_memory_pressure",
    }
)


class ParserIsolationError(RuntimeError):
    """El parser no produjo un resultado confiable; nunca debe degradar abierto."""


class ParserIsolationTimeout(ParserIsolationError):
    pass


class ParserIsolationCapacityError(ParserIsolationError):
    pass


@dataclass(frozen=True)
class ParserRejected(ParserIsolationError):
    """Rechazo de contenido esperado comunicado por el proceso aislado."""

    message: str
    status_code: int

    def __str__(self) -> str:
        return self.message


def _bounded_env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name) or default).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} debe ser un entero") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} está fuera del rango seguro")
    return value


def _bounded_env_float(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = str(os.getenv(name) or default).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} debe ser numérico") from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise RuntimeError(f"{name} está fuera del rango seguro")
    return value


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _production_like() -> bool:
    from rtm_core.environment_contract import (
        runtime_requires_environment_preflight,
    )

    return runtime_requires_environment_preflight()


def _limits() -> dict[str, Any]:
    return {
        "wall_seconds": _bounded_env_float(
            "RTM_PARSER_WALL_SECONDS", 12.0, minimum=1.0, maximum=30.0
        ),
        "queue_seconds": _bounded_env_float(
            "RTM_PARSER_QUEUE_SECONDS", 1.0, minimum=0.05, maximum=5.0
        ),
        "cpu_seconds": _bounded_env_int(
            "RTM_PARSER_CPU_SECONDS", 6, minimum=1, maximum=20
        ),
        "memory_bytes": _bounded_env_int(
            "RTM_PARSER_MEMORY_MIB", 512, minimum=128, maximum=2048
        )
        * 1024
        * 1024,
        "require_os_limits": _production_like()
        or _truthy(os.getenv("RTM_PARSER_REQUIRE_OS_LIMITS")),
        "test_hooks": _truthy(os.getenv("RTM_PARSER_TEST_HOOKS")),
    }


_MAX_WORKERS = _bounded_env_int(
    "RTM_PARSER_MAX_WORKERS", 2, minimum=1, maximum=8
)
_CAPACITY = threading.BoundedSemaphore(_MAX_WORKERS)


def _context() -> multiprocessing.context.BaseContext:
    requested = str(os.getenv("RTM_PARSER_START_METHOD") or "").strip().casefold()
    safe_methods = set(multiprocessing.get_all_start_methods()) - {"fork"}
    if requested:
        if requested not in safe_methods:
            raise ParserIsolationError("Método seguro de aislamiento no disponible")
        return multiprocessing.get_context(requested)
    # ``spawn`` no hereda locks ni conexiones de los workers ASGI y tampoco
    # necesita el socket auxiliar que usa forkserver (bloqueado por algunos
    # sandboxes/contenedores). El coste de arranque es una concesión deliberada
    # a cambio de una frontera reproducible.
    if "spawn" in safe_methods:
        return multiprocessing.get_context("spawn")
    raise ParserIsolationError("Aislamiento de procesos no disponible")


def assert_parser_isolation_ready() -> dict[str, Any]:
    """Valida propiedades observables antes de aceptar tráfico desplegado.

    El probe acredita exclusivamente endurecimiento dentro del worker. No
    intenta representar aislamiento de red, procesos o filesystem del host.
    """

    try:
        limits = _limits()
        context = _context()
    except (RuntimeError, ValueError) as exc:
        raise RuntimeError("Configuración de aislamiento de parsers inválida") from exc
    if bool(limits["require_os_limits"]):
        try:
            import resource

            required = (
                "RLIMIT_AS",
                "RLIMIT_CORE",
                "RLIMIT_CPU",
                "RLIMIT_FSIZE",
                "RLIMIT_NOFILE",
            )
            if any(not hasattr(resource, item) for item in required):
                raise RuntimeError("Límites POSIX incompletos")
        except Exception as exc:
            raise RuntimeError(
                "El perfil desplegado exige límites POSIX para parsers"
            ) from exc
    try:
        probe = run_parser_isolated("_readiness_probe", {}, wall_seconds=5.0)
    except ParserIsolationError as exc:
        raise RuntimeError(
            "El endurecimiento observable del parser no supera el preflight"
        ) from exc
    observed = _validate_readiness_probe(
        probe,
        limits=limits,
        parent_pid=os.getpid(),
    )
    return {
        "guarantee_scope": "worker_process_only",
        "start_method": context.get_start_method(),
        "max_workers": _MAX_WORKERS,
        "wall_seconds": float(limits["wall_seconds"]),
        "cpu_seconds": int(limits["cpu_seconds"]),
        "memory_bytes": int(limits["memory_bytes"]),
        "os_limits_required": bool(limits["require_os_limits"]),
        "observed": observed,
    }


def _resource_limit_specs(
    resource_module: Any,
    limits: Mapping[str, Any],
) -> tuple[tuple[str, int, int], ...]:
    configured = (
        ("RLIMIT_CORE", resource_module.RLIMIT_CORE, 0),
        ("RLIMIT_FSIZE", resource_module.RLIMIT_FSIZE, 1024 * 1024),
        ("RLIMIT_NOFILE", resource_module.RLIMIT_NOFILE, 64),
        ("RLIMIT_CPU", resource_module.RLIMIT_CPU, int(limits["cpu_seconds"])),
        ("RLIMIT_AS", resource_module.RLIMIT_AS, int(limits["memory_bytes"])),
    )
    if hasattr(resource_module, "RLIMIT_NPROC"):
        configured = (
            *configured,
            ("RLIMIT_NPROC", resource_module.RLIMIT_NPROC, 1),
        )
    return configured


def _install_worker_limits(limits: Mapping[str, Any]) -> None:
    try:
        import resource
    except Exception as exc:
        if bool(limits.get("require_os_limits")):
            raise RuntimeError("Límites de recursos no disponibles") from exc
        return

    for _name, resource_kind, requested_limit in _resource_limit_specs(
        resource,
        limits,
    ):
        current_soft, current_hard = resource.getrlimit(resource_kind)
        effective_limit = int(requested_limit)
        if current_hard != resource.RLIM_INFINITY:
            effective_limit = min(effective_limit, int(current_hard))
        if current_soft != resource.RLIM_INFINITY:
            effective_limit = min(effective_limit, int(current_soft))
        if effective_limit < 0:
            raise RuntimeError("Límite de recursos inválido")
        # El hard-limit también se reduce. Si quedase heredado (a menudo
        # infinito), código explotado dentro del worker podría reelevar el soft
        # y anular la contención. Un límite heredado más estricto se conserva.
        resource.setrlimit(resource_kind, (effective_limit, effective_limit))
        if resource.getrlimit(resource_kind) != (
            effective_limit,
            effective_limit,
        ):
            raise RuntimeError("No pudo verificarse el límite de recursos")


def _resource_limit_snapshot(limits: Mapping[str, Any]) -> dict[str, list[int]]:
    try:
        import resource
    except Exception:
        return {}

    observed: dict[str, list[int]] = {}
    for name, resource_kind, _requested in _resource_limit_specs(resource, limits):
        soft, hard = resource.getrlimit(resource_kind)
        observed[name] = [int(soft), int(hard)]
    return observed


def _no_new_privileges_state() -> bool | None:
    if not os.name == "posix" or not hasattr(os, "uname"):
        return None
    if os.uname().sysname.casefold() != "linux":
        return None
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        value = int(libc.prctl(39, 0, 0, 0, 0))  # PR_GET_NO_NEW_PRIVS
    except Exception:
        return None
    return value == 1 if value >= 0 else None


def _network_denied(*_args: Any, **_kwargs: Any) -> Any:
    raise PermissionError("Red deshabilitada en parser aislado")


def _python_socket_api_replaced() -> dict[str, bool]:
    return {
        "socket": socket.socket is _network_denied,
        "socketpair": socket.socketpair is _network_denied,
        "create_connection": socket.create_connection is _network_denied,
    }


def _private_umask_is_active() -> bool:
    previous = os.umask(0o077)
    return previous == 0o077


def _worker_readiness_probe(limits: Mapping[str, Any]) -> dict[str, Any]:
    effective_uid = int(os.geteuid()) if hasattr(os, "geteuid") else None
    effective_gid = int(os.getegid()) if hasattr(os, "getegid") else None
    unprivileged_identity = (
        None
        if effective_uid is None and effective_gid is None
        else (
            (effective_uid is None or effective_uid != 0)
            and (effective_gid is None or effective_gid != 0)
        )
    )
    return {
        "contract": "rtm_parser_process_controls_v1",
        "worker_pid": int(os.getpid()),
        "environment_count": len(os.environ),
        "python_socket_api_replaced": _python_socket_api_replaced(),
        "private_umask": _private_umask_is_active(),
        "effective_uid": effective_uid,
        "effective_gid": effective_gid,
        "worker_unprivileged_identity": unprivileged_identity,
        "no_new_privileges": _no_new_privileges_state(),
        "resource_limits": _resource_limit_snapshot(limits),
    }


def _validate_readiness_probe(
    probe: Any,
    *,
    limits: Mapping[str, Any],
    parent_pid: int,
) -> dict[str, Any]:
    if not isinstance(probe, dict):
        raise RuntimeError("Respuesta inválida del preflight de parsers")
    if probe.get("contract") != "rtm_parser_process_controls_v1":
        raise RuntimeError("Respuesta inválida del preflight de parsers")

    worker_pid = probe.get("worker_pid")
    if (
        isinstance(worker_pid, bool)
        or not isinstance(worker_pid, int)
        or worker_pid <= 0
        or worker_pid == parent_pid
    ):
        raise RuntimeError("Respuesta inválida del preflight de parsers")

    if probe.get("private_umask") is not True:
        raise RuntimeError("Respuesta inválida del preflight de parsers")
    environment_count = probe.get("environment_count")
    if (
        isinstance(environment_count, bool)
        or not isinstance(environment_count, int)
        or environment_count != 0
    ):
        raise RuntimeError("Respuesta inválida del preflight de parsers")
    if probe.get("python_socket_api_replaced") != {
        "socket": True,
        "socketpair": True,
        "create_connection": True,
    }:
        raise RuntimeError("Respuesta inválida del preflight de parsers")

    no_new_privileges = probe.get("no_new_privileges")
    if bool(limits.get("require_os_limits")) and no_new_privileges is not True:
        raise RuntimeError("Respuesta inválida del preflight de parsers")
    if (
        no_new_privileges is not True
        and no_new_privileges is not False
        and no_new_privileges is not None
    ):
        raise RuntimeError("Respuesta inválida del preflight de parsers")

    observed_limits = probe.get("resource_limits")
    if not isinstance(observed_limits, dict):
        raise RuntimeError("Respuesta inválida del preflight de parsers")

    try:
        import resource
    except Exception:
        expected_limits: tuple[tuple[str, int, int], ...] = ()
    else:
        expected_limits = _resource_limit_specs(resource, limits)

    if bool(limits.get("require_os_limits")) and not expected_limits:
        raise RuntimeError("Respuesta inválida del preflight de parsers")
    if expected_limits:
        for name, _resource_kind, maximum in expected_limits:
            pair = observed_limits.get(name)
            if not isinstance(pair, list) or len(pair) != 2:
                raise RuntimeError("Respuesta inválida del preflight de parsers")
            soft, hard = pair
            if (
                isinstance(soft, bool)
                or isinstance(hard, bool)
                or not isinstance(soft, int)
                or not isinstance(hard, int)
                or soft < 0
                or soft != hard
                or soft > int(maximum)
            ):
                raise RuntimeError("Respuesta inválida del preflight de parsers")

    effective_uid = probe.get("effective_uid")
    effective_gid = probe.get("effective_gid")
    if effective_uid is not None and (
        isinstance(effective_uid, bool)
        or not isinstance(effective_uid, int)
        or effective_uid < 0
    ):
        raise RuntimeError("Respuesta inválida del preflight de parsers")
    if effective_gid is not None and (
        isinstance(effective_gid, bool)
        or not isinstance(effective_gid, int)
        or effective_gid < 0
    ):
        raise RuntimeError("Respuesta inválida del preflight de parsers")
    unprivileged_identity = (
        None
        if effective_uid is None and effective_gid is None
        else (
            (effective_uid is None or effective_uid != 0)
            and (effective_gid is None or effective_gid != 0)
        )
    )
    if probe.get("worker_unprivileged_identity") is not unprivileged_identity:
        raise RuntimeError("Respuesta inválida del preflight de parsers")

    return {
        "separate_process": True,
        "environment_cleared": True,
        "python_socket_api_replaced": {
            "socket": True,
            "socketpair": True,
            "create_connection": True,
        },
        "private_umask": True,
        "worker_unprivileged_identity": unprivileged_identity,
        "effective_uid": effective_uid,
        "effective_gid": effective_gid,
        "no_new_privileges": no_new_privileges,
        "resource_limits": {
            name: list(observed_limits[name])
            for name, _resource_kind, _maximum in expected_limits
        },
    }


def _deny_network() -> None:
    socket.socket = _network_denied  # type: ignore[assignment]
    socket.socketpair = _network_denied  # type: ignore[assignment]
    socket.create_connection = _network_denied  # type: ignore[assignment]


def _drop_worker_privileges(*, required: bool) -> None:
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    try:
        os.setgroups([])
        os.setgid(65534)
        os.setuid(65534)
    except Exception as exc:
        if required:
            raise RuntimeError("No pudieron reducirse privilegios del parser") from exc


def _set_no_new_privileges(*, required: bool) -> None:
    if not os.name == "posix" or not hasattr(os, "uname"):
        if required:
            raise RuntimeError("No-new-privileges no disponible")
        return
    if os.uname().sysname.casefold() != "linux":
        if required:
            raise RuntimeError("El perfil desplegado requiere aislamiento Linux")
        return
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        if int(libc.prctl(38, 1, 0, 0, 0)) != 0:  # PR_SET_NO_NEW_PRIVS
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_NO_NEW_PRIVS)")
    except Exception as exc:
        if required:
            raise RuntimeError("No pudo fijarse no-new-privileges") from exc


def _harden_worker_runtime(limits: Mapping[str, Any]) -> None:
    required = bool(limits.get("require_os_limits"))
    _install_worker_limits(limits)
    _set_no_new_privileges(required=required)
    # Algunos contenedores ejecutan uid 0 dentro de un user namespace sin
    # CAP_SETUID/CAP_SETGID. El descenso de uid es defensa adicional; los
    # límites CPU/RAM y no-new-privileges sí son obligatorios en despliegue.
    _drop_worker_privileges(required=False)
    os.umask(0o077)
    # Ninguna operación de parseo necesita secretos ni configuración de red.
    # Vaciar el entorno reduce el impacto si una librería nativa fuera explotada.
    os.environ.clear()
    _deny_network()


def _execute_operation(
    operation: str,
    payload: Mapping[str, Any],
    *,
    limits: Mapping[str, Any],
    test_hooks: bool,
) -> Any:
    if operation == "_readiness_probe":
        return _worker_readiness_probe(limits)
    if operation == "validate_document":
        from rtm_core.upload_security import _validate_document_bytes_local

        result = _validate_document_bytes_local(
            filename=payload.get("filename"),
            declared_mime=payload.get("declared_mime"),
            data=bytes(payload["data"]),
            max_bytes=int(payload["max_bytes"]),
            allowed_mimes=tuple(str(item) for item in payload["allowed_mimes"]),
        )
        return {
            "filename": result.filename,
            "mime": result.mime,
            "extension": result.extension,
            "size_bytes": result.size_bytes,
            "sha256": result.sha256,
        }
    if operation == "extract_pdf_text":
        from text_extractors import _extract_text_from_pdf_bytes_local

        return _extract_text_from_pdf_bytes_local(bytes(payload["data"]))
    if operation == "extract_docx_text":
        from text_extractors import _extract_text_from_docx_bytes_local

        return _extract_text_from_docx_bytes_local(bytes(payload["data"]))
    if operation == "pdf_page_count":
        import io

        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(bytes(payload["data"])), strict=False).pages)
    if operation == "require_docx_main_content_type":
        import io
        import xml.etree.ElementTree as ET
        import zipfile

        from rtm_core.upload_security import validate_docx_archive

        content = bytes(payload["data"])
        validate_docx_archive(content)
        with zipfile.ZipFile(io.BytesIO(content), mode="r") as archive:
            root = ET.fromstring(archive.read("[Content_Types].xml"))
        expected = (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document.main+xml"
        )
        for element in root.iter():
            if str(element.tag).rsplit("}", 1)[-1] != "Override":
                continue
            if (
                str(element.attrib.get("PartName") or "") == "/word/document.xml"
                and str(element.attrib.get("ContentType") or "").casefold()
                == expected
            ):
                return True
        from rtm_core.upload_security import UploadSecurityError

        raise UploadSecurityError(
            "El contenedor no declara un documento Word DOCX",
            status_code=422,
        )
    if operation in {"canonicalize_image", "convert_tiff_png", "crop_fet_image"}:
        import io
        import warnings

        from PIL import Image, ImageEnhance, ImageOps

        from rtm_core.upload_security import validate_image_document

        content = bytes(payload["data"])
        mime = str(payload.get("mime") or "")
        validate_image_document(content, mime)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as source:
                source.seek(0)
                image = ImageOps.exif_transpose(source).convert("RGB")

        if operation == "canonicalize_image":
            source_width, source_height = image.size
            maximum = int(payload.get("max_dimension") or 2600)
            if not 512 <= maximum <= 4000:
                raise RuntimeError("Dimensión canónica fuera del rango seguro")
            resized = max(image.size) > maximum
            if resized:
                image.thumbnail((maximum, maximum))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=92, optimize=False)
            return {
                "content_b64": base64.b64encode(output.getvalue()).decode("ascii"),
                "mime": "image/jpeg",
                "source_width": int(source_width),
                "source_height": int(source_height),
                "width": int(image.width),
                "height": int(image.height),
                "resized": bool(resized),
            }

        if operation == "convert_tiff_png":
            if max(image.size) > 1800:
                image.thumbnail((1800, 1800))
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=False)
            return {
                "content_b64": base64.b64encode(output.getvalue()).decode("ascii"),
                "mime": "image/png",
            }

        width, height = image.size
        if width >= height:
            box = (0.28, 0.34, 0.73, 0.57)
        else:
            box = (0.10, 0.26, 0.78, 0.58)
        left = max(0, min(int(width * box[0]), width - 1))
        top = max(0, min(int(height * box[1]), height - 1))
        right = max(left + 10, min(int(width * box[2]), width))
        bottom = max(top + 10, min(int(height * box[3]), height))
        crop = image.crop((left, top, right, bottom))
        crop = ImageEnhance.Contrast(crop).enhance(1.45)
        crop = ImageEnhance.Sharpness(crop).enhance(1.25)
        output = io.BytesIO()
        crop.save(output, format="JPEG", quality=92, optimize=False)
        return {
            "content_b64": base64.b64encode(output.getvalue()).decode("ascii"),
            "mime": "image/jpeg",
        }

    if not test_hooks:
        raise RuntimeError("Operación no permitida")
    if operation == "_test_crash":
        os._exit(73)
    if operation == "_test_sleep":
        import time

        time.sleep(float(payload.get("seconds") or 0))
        return "unexpected"
    if operation == "_test_memory_pressure":
        blocks: list[bytes] = []
        block_size = 8 * 1024 * 1024
        while True:
            blocks.append(b"x" * block_size)
    raise RuntimeError("Operación no permitida")


def _worker_main(
    sender: Any,
    operation: str,
    payload: Mapping[str, Any],
    limits: Mapping[str, Any],
) -> None:
    response: dict[str, Any]
    try:
        _harden_worker_runtime(limits)
        result = _execute_operation(
            operation,
            payload,
            limits=limits,
            test_hooks=bool(limits.get("test_hooks")),
        )
        response = {"ok": True, "result": result}
    except BaseException as exc:  # el padre solo recibe clases/mensajes acotados
        try:
            from rtm_core.upload_security import UploadSecurityError
        except Exception:
            UploadSecurityError = ()  # type: ignore[assignment,misc]
        if UploadSecurityError and isinstance(exc, UploadSecurityError):
            response = {
                "ok": False,
                "kind": "rejected",
                "status_code": int(getattr(exc, "status_code", 415)),
                "message": str(exc)[:500],
            }
        else:
            response = {"ok": False, "kind": "internal"}
    try:
        encoded = json.dumps(
            response,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > _MAX_RESULT_BYTES:
            encoded = b'{"ok":false,"kind":"oversized"}'
        sender.send_bytes(encoded)
    except BaseException:
        pass
    finally:
        try:
            sender.close()
        except Exception:
            pass


def _terminate(process: Any) -> None:
    if process is None:
        return
    try:
        if getattr(process, "_popen", None) is not None:
            process.join(_MAX_JOIN_SECONDS)
        if process.is_alive():
            process.terminate()
            process.join(_MAX_JOIN_SECONDS)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(_MAX_JOIN_SECONDS)
    finally:
        try:
            process.close()
        except (AttributeError, ValueError):
            pass


def run_parser_isolated(
    operation: str,
    payload: Mapping[str, Any],
    *,
    wall_seconds: float | None = None,
) -> Any:
    """Supervisa una operación permitida y devuelve solo su resultado verificado."""

    if operation not in _OPERATIONS:
        raise ParserIsolationError("Operación de parser no permitida")
    try:
        limits = _limits()
    except (RuntimeError, ValueError) as exc:
        raise ParserIsolationError("Configuración de parser inválida") from exc
    if wall_seconds is not None:
        requested_wall = float(wall_seconds)
        if not math.isfinite(requested_wall) or not 0.05 <= requested_wall <= 30.0:
            raise ValueError("Deadline de parser fuera del rango seguro")
        limits["wall_seconds"] = requested_wall
    data = payload.get("data")
    if data is not None and (
        not isinstance(data, (bytes, bytearray)) or len(data) > _MAX_REQUEST_BYTES
    ):
        raise ParserIsolationError("Entrada de parser fuera del límite")

    if not _CAPACITY.acquire(timeout=float(limits["queue_seconds"])):
        raise ParserIsolationCapacityError("Capacidad de parseo temporalmente agotada")

    process = None
    receiver = None
    sender = None
    try:
        context = _context()
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=_worker_main,
            args=(sender, operation, dict(payload), limits),
            name=f"rtm-parser-{operation}",
            daemon=True,
        )
        process.start()
        sender.close()
        sender = None

        if not receiver.poll(float(limits["wall_seconds"])):
            raise ParserIsolationTimeout("El parser excedió su tiempo máximo")
        try:
            encoded = receiver.recv_bytes(_MAX_RESULT_BYTES)
        except (EOFError, OSError) as exc:
            raise ParserIsolationError("El proceso de parseo terminó sin resultado") from exc
        try:
            response = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ParserIsolationError("Respuesta de parser inválida") from exc
        if not isinstance(response, dict) or response.get("ok") is not True:
            if isinstance(response, dict) and response.get("kind") == "rejected":
                status = int(response.get("status_code") or 415)
                if status not in {400, 413, 415, 422, 503}:
                    status = 415
                raise ParserRejected(str(response.get("message") or "Documento rechazado"), status)
            raise ParserIsolationError("El parser aislado no pudo completar la operación")
        return response.get("result")
    finally:
        if receiver is not None:
            try:
                receiver.close()
            except Exception:
                pass
        if sender is not None:
            try:
                sender.close()
            except Exception:
                pass
        _terminate(process)
        _CAPACITY.release()


def run_image_parser_isolated(
    operation: str,
    content: bytes,
    mime: str,
    **options: Any,
) -> tuple[bytes, str, dict[str, Any]]:
    """Decodifica una imagen generada por el worker con contrato estricto."""

    value = run_parser_isolated(
        operation,
        {"data": bytes(content), "mime": str(mime), **options},
    )
    if not isinstance(value, dict):
        raise ParserIsolationError("Salida de imagen aislada inválida")
    encoded = value.get("content_b64")
    result_mime = str(value.get("mime") or "")
    if not isinstance(encoded, str) or result_mime not in {"image/jpeg", "image/png"}:
        raise ParserIsolationError("Salida de imagen aislada inválida")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ParserIsolationError("Salida de imagen aislada inválida") from exc
    if not decoded or len(decoded) > 12 * 1024 * 1024:
        raise ParserIsolationError("Salida de imagen aislada fuera del límite")
    metadata = {
        str(key): item
        for key, item in value.items()
        if key not in {"content_b64", "mime"}
        and isinstance(item, (str, int, float, bool, type(None)))
    }
    return decoded, result_mime, metadata


async def run_parser_isolated_async(
    operation: str,
    payload: Mapping[str, Any],
    *,
    wall_seconds: float | None = None,
) -> Any:
    """Versión async: también la supervisión bloqueante sale del event loop."""

    return await asyncio.to_thread(
        run_parser_isolated,
        operation,
        payload,
        wall_seconds=wall_seconds,
    )
