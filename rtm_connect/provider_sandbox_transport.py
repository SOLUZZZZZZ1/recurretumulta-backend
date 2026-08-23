"""Transporte HTTP cerrado del proveedor sandbox controlado C6.

No usa la configuración proxy del proceso, no sigue redirecciones y nunca
incluye respuestas, cabeceras o secretos crudos en sus excepciones.
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping
from uuid import UUID

from rtm_connect.idempotency import canonical_json
from rtm_connect.provider_sandbox_policy import (
    CONTROLLED_SANDBOX_CONTRACT_VERSION,
    CONTROLLED_SANDBOX_MARKER,
    ProviderSandboxEndpoint,
    ProviderSandboxPolicyError,
)
from rtm_connect.secret_resolver import (
    EnvironmentSecretResolver,
    ResolvedSecret,
)


RTM_CONNECT_C6_PROVIDER_TRANSPORT_VERSION = (
    "rtm_connect_c6_provider_transport_v1_0"
)
MAX_RESPONSE_BYTES = 65_536
MAX_REQUEST_BYTES = 8_192
DEFAULT_TIMEOUT_SECONDS = 3.0

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EXTERNAL_REFERENCE_RE = re.compile(r"^c6probe-[0-9a-f-]{36}$")
_IDEMPOTENCY_KEY_RE = re.compile(r"^rtmc1:[0-9a-f]{64}$")


class SandboxObservationStatus(str, Enum):
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"
    REJECTED = "rejected"


class ProviderSandboxTransportError(RuntimeError):
    """Error normalizado sin causa cruda ni material sensible."""

    def __init__(
        self,
        message: str,
        *,
        network_call_performed: bool = False,
    ) -> None:
        super().__init__(message)
        self.network_call_performed = bool(network_call_performed)


class ProviderSandboxAmbiguous(ProviderSandboxTransportError):
    """No puede determinarse si el sandbox registró el probe."""


class ProviderSandboxContractError(ProviderSandboxTransportError):
    pass


@dataclass(frozen=True)
class ControlledSandboxProbe:
    action_id: str
    request_sha256: str

    def __post_init__(self) -> None:
        try:
            normalized_action = str(UUID(str(self.action_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("action_id C6 debe ser UUID") from exc
        request_hash = str(self.request_sha256 or "").strip().lower()
        if not _SHA256_RE.fullmatch(request_hash):
            raise ValueError("request_sha256 C6 no válido")
        object.__setattr__(self, "action_id", normalized_action)
        object.__setattr__(self, "request_sha256", request_hash)

    @property
    def client_reference(self) -> str:
        return self.action_id

    @property
    def expected_external_reference(self) -> str:
        return f"c6probe-{self.action_id}"

    def body(self) -> dict[str, str]:
        # Deliberadamente estable: no incluye attempt_id ni timestamps.
        return {
            "contract_version": CONTROLLED_SANDBOX_CONTRACT_VERSION,
            "client_reference": self.client_reference,
            "request_sha256": self.request_sha256,
            "marker": CONTROLLED_SANDBOX_MARKER,
        }


@dataclass(frozen=True)
class ControlledSandboxObservation:
    status: SandboxObservationStatus
    external_reference: str
    client_reference: str
    request_sha256: str


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderSandboxContractError("JSON sandbox con clave duplicada")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ProviderSandboxContractError("JSON sandbox no finito")


def _assert_json_shape(value: Any, *, depth: int = 0) -> None:
    if depth > 6:
        raise ProviderSandboxContractError("JSON sandbox demasiado profundo")
    if isinstance(value, dict):
        if len(value) > 12:
            raise ProviderSandboxContractError("JSON sandbox demasiado ancho")
        for child in value.values():
            _assert_json_shape(child, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > 32:
            raise ProviderSandboxContractError("JSON sandbox demasiado largo")
        for child in value:
            _assert_json_shape(child, depth=depth + 1)


def _remaining_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Plazo HTTP C6 agotado")
    return remaining


def _set_response_socket_timeout(response: Any, timeout: float) -> None:
    """Reduce el timeout del socket al presupuesto total restante."""

    candidates = (
        getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None),
        getattr(getattr(response, "fp", None), "_sock", None),
        getattr(response, "_sock", None),
    )
    for candidate in candidates:
        if candidate is not None and hasattr(candidate, "settimeout"):
            candidate.settimeout(max(0.001, timeout))
            return


def _read_response_capped(response: Any, *, deadline: float) -> bytes:
    chunks: list[bytes] = []
    total = 0
    reader = getattr(response, "read1", None) or response.read
    while total <= MAX_RESPONSE_BYTES:
        remaining = _remaining_seconds(deadline)
        _set_response_socket_timeout(response, remaining)
        chunk = reader(min(8192, MAX_RESPONSE_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(bytes(chunk))
        total += len(chunk)
    return b"".join(chunks)


def _abort_connection(connection: http.client.HTTPConnection) -> None:
    """Interrumpe de forma cancelable cualquier fase HTTP al vencer el plazo."""

    sock = connection.sock
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    try:
        connection.close()
    except Exception:
        pass


def _header_values(headers: Any, name: str) -> list[str]:
    getter = getattr(headers, "get_all", None)
    if callable(getter):
        return [str(value) for value in (getter(name) or [])]
    value = headers.get(name) if hasattr(headers, "get") else None
    return [] if value is None else [str(value)]


def _decode_observation(
    raw: bytes,
    *,
    probe: ControlledSandboxProbe,
) -> ControlledSandboxObservation:
    try:
        decoded = raw.decode("utf-8", errors="strict")
        payload = json.loads(
            decoded,
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_constant,
        )
    except ProviderSandboxContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProviderSandboxContractError(
            "Respuesta sandbox no es JSON válido"
        ) from None
    _assert_json_shape(payload)
    if not isinstance(payload, Mapping):
        raise ProviderSandboxContractError("Respuesta sandbox debe ser objeto")
    expected_keys = {
        "contract_version",
        "environment",
        "status",
        "external_reference",
        "client_reference",
        "request_sha256",
    }
    if set(payload) != expected_keys:
        raise ProviderSandboxContractError("Campos de respuesta sandbox no admitidos")
    if payload["contract_version"] != CONTROLLED_SANDBOX_CONTRACT_VERSION:
        raise ProviderSandboxContractError("Versión de respuesta sandbox no válida")
    if payload["environment"] != "sandbox":
        raise ProviderSandboxContractError("Respuesta fuera de sandbox")
    try:
        status = SandboxObservationStatus(str(payload["status"]))
    except ValueError:
        raise ProviderSandboxContractError(
            "Estado sandbox no reconocido"
        ) from None
    external_reference = str(payload["external_reference"] or "")
    if (
        not _EXTERNAL_REFERENCE_RE.fullmatch(external_reference)
        or external_reference != probe.expected_external_reference
    ):
        raise ProviderSandboxContractError("Referencia externa no correlacionada")
    if str(payload["client_reference"]) != probe.client_reference:
        raise ProviderSandboxContractError("Client reference no correlacionada")
    if str(payload["request_sha256"]) != probe.request_sha256:
        raise ProviderSandboxContractError("Hash de respuesta no correlacionado")
    return ControlledSandboxObservation(
        status=status,
        external_reference=external_reference,
        client_reference=probe.client_reference,
        request_sha256=probe.request_sha256,
    )


class ControlledSandboxTransport:
    __slots__ = ("_endpoint", "_resolver", "_timeout", "__sealed")

    def __init__(
        self,
        *,
        endpoint: ProviderSandboxEndpoint,
        secret_resolver: EnvironmentSecretResolver,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if type(endpoint) is not ProviderSandboxEndpoint:
            raise TypeError("C6 exige ProviderSandboxEndpoint exacto")
        if type(secret_resolver) is not EnvironmentSecretResolver:
            raise TypeError("C6 exige EnvironmentSecretResolver exacto")
        timeout = float(timeout_seconds)
        if not 0.1 <= timeout <= 10.0:
            raise ValueError("Timeout C6 fuera de rango")
        object.__setattr__(self, "_endpoint", endpoint)
        object.__setattr__(self, "_resolver", secret_resolver)
        object.__setattr__(self, "_timeout", timeout)
        object.__setattr__(self, "_ControlledSandboxTransport__sealed", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_ControlledSandboxTransport__sealed", False):
            raise AttributeError("ControlledSandboxTransport es inmutable")
        object.__setattr__(self, name, value)

    @property
    def loopback_test_only(self) -> bool:
        return self._endpoint.loopback_test_only

    def assert_runtime_sealed(self) -> None:
        """Revalida una copia exacta para detectar mutación/inyección tardía."""

        if type(self) is not ControlledSandboxTransport:
            raise ProviderSandboxContractError(
                "Subclase de transporte C6 no admitida"
            )
        if type(self._resolver) is not EnvironmentSecretResolver:
            raise ProviderSandboxContractError(
                "Resolver C6 no sellado"
            )
        resolver_sealed = False
        try:
            EnvironmentSecretResolver.assert_runtime_sealed(
                self._resolver,
                expected_reference=self._endpoint.credential_ref,
            )
            resolver_sealed = True
        except Exception:
            pass
        if not resolver_sealed:
            raise ProviderSandboxContractError("Resolver C6 no sellado")
        revalidated = ProviderSandboxEndpoint(
            origin=self._endpoint.origin,
            credential_ref=self._endpoint.credential_ref,
            loopback_test_only=self._endpoint.loopback_test_only,
        )
        if revalidated != self._endpoint or not revalidated.loopback_test_only:
            raise ProviderSandboxContractError(
                "Configuración de transporte C6 no sellada"
            )

    def _call(
        self,
        *,
        method: str,
        path: str,
        probe: ControlledSandboxProbe,
        idempotency_key: str,
    ) -> ControlledSandboxObservation:
        deadline = time.monotonic() + self._timeout
        normalized_key = str(idempotency_key or "")
        if not _IDEMPOTENCY_KEY_RE.fullmatch(normalized_key):
            raise ProviderSandboxContractError(
                "Clave de idempotencia sandbox no válida"
            )
        target_error: ProviderSandboxTransportError | None = None
        try:
            self.assert_runtime_sealed()
            revalidated_endpoint = ProviderSandboxEndpoint(
                origin=self._endpoint.origin,
                credential_ref=self._endpoint.credential_ref,
                loopback_test_only=self._endpoint.loopback_test_only,
            )
            revalidated_endpoint.assert_network_target(
                timeout_seconds=_remaining_seconds(deadline)
            )
        except ProviderSandboxPolicyError:
            target_error = ProviderSandboxContractError(
                "Destino de red sandbox bloqueado"
            )
        except OSError:
            target_error = ProviderSandboxAmbiguous(
                "Resolución del sandbox no concluyente"
            )
        if target_error is not None:
            # Se eleva fuera del ``except`` para que __context__ tampoco
            # conserve el error DNS/policy crudo.
            raise target_error

        resolution_error: ProviderSandboxTransportError | None = None
        secret: ResolvedSecret | None = None
        try:
            resolved = EnvironmentSecretResolver.resolve(
                self._resolver,
                self._endpoint.credential_ref,
            )
            if (
                type(resolved) is not ResolvedSecret
                or resolved.reference != self._endpoint.credential_ref
            ):
                raise TypeError("resolver no opaco")
            secret = resolved
        except Exception:
            resolution_error = ProviderSandboxContractError(
                "Credencial sandbox no disponible"
            )
        if resolution_error is not None:
            raise resolution_error

        body = (
            canonical_json(probe.body()).encode("utf-8")
            if method == "POST" else None
        )
        if body is not None and len(body) > MAX_REQUEST_BYTES:
            raise ProviderSandboxContractError("Petición sandbox sobredimensionada")
        parsed = urllib.parse.urlsplit(revalidated_endpoint.origin)
        host = parsed.hostname
        port = parsed.port
        if parsed.scheme != "http" or host is None or port is None:
            raise ProviderSandboxContractError(
                "Endpoint de transporte C6 no admitido"
            )
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Content-Type": "application/json",
            "Idempotency-Key": normalized_key,
            "X-RTM-Request-SHA256": probe.request_sha256,
            "User-Agent": "RTM-CONNECT-C6/1.0",
        }
        raw = b""
        pending_error: ProviderSandboxTransportError | None = None
        response = None
        request_started = False
        connection: http.client.HTTPConnection | None = None
        watchdog: threading.Timer | None = None
        try:
            remaining = _remaining_seconds(deadline)
            connection = http.client.HTTPConnection(
                host,
                port,
                timeout=remaining,
            )
            watchdog = threading.Timer(
                remaining,
                _abort_connection,
                args=(connection,),
            )
            watchdog.daemon = True
            headers["Authorization"] = (
                "Bearer " + secret.reveal_for_transport()
            )
            secret = None
            watchdog.start()
            connection.connect()
            if connection.sock is not None:
                connection.sock.settimeout(_remaining_seconds(deadline))
            # Desde este punto no puede afirmarse con seguridad que el
            # proveedor no haya recibido parte de la petición.
            request_started = True
            connection.request(method, path, body=body, headers=headers)
            headers["Authorization"] = "<redacted>"
            response = connection.getresponse()
            if int(response.status) != 200:
                raise ProviderSandboxAmbiguous(
                    "Respuesta HTTP sandbox ambigua",
                    network_call_performed=True,
                )
            content_types = _header_values(response.headers, "Content-Type")
            if (
                len(content_types) != 1
                or content_types[0].strip().lower() != "application/json"
            ):
                raise ProviderSandboxContractError(
                    "Content-Type sandbox no admitido",
                    network_call_performed=True,
                )
            encodings = _header_values(response.headers, "Content-Encoding")
            if len(encodings) > 1 or (
                encodings and encodings[0].strip().lower() != "identity"
            ):
                raise ProviderSandboxContractError(
                    "Content-Encoding sandbox no admitido",
                    network_call_performed=True,
                )
            if _header_values(response.headers, "Transfer-Encoding"):
                raise ProviderSandboxContractError(
                    "Transfer-Encoding sandbox no admitido",
                    network_call_performed=True,
                )
            content_lengths = _header_values(response.headers, "Content-Length")
            if len(content_lengths) != 1:
                raise ProviderSandboxContractError(
                    "Content-Length sandbox ausente o duplicado",
                    network_call_performed=True,
                )
            try:
                content_length = int(content_lengths[0], 10)
            except (TypeError, ValueError):
                raise ProviderSandboxContractError(
                    "Content-Length sandbox no válido",
                    network_call_performed=True,
                ) from None
            if not 0 <= content_length <= MAX_RESPONSE_BYTES:
                raise ProviderSandboxContractError(
                    "Respuesta sandbox sobredimensionada",
                    network_call_performed=True,
                )
            raw = _read_response_capped(response, deadline=deadline)
            if len(raw) != content_length:
                raise ProviderSandboxContractError(
                    "Longitud de respuesta sandbox no válida",
                    network_call_performed=True,
                )
        except (ProviderSandboxContractError, ProviderSandboxAmbiguous) as exc:
            pending_error = type(exc)(
                str(exc),
                network_call_performed=(
                    request_started or exc.network_call_performed
                ),
            )
        except Exception:
            # Errores de socket/protocolo pueden incluir datos reflejados.
            # Nunca se encadenan ni se copian al error normalizado.
            if request_started:
                pending_error = ProviderSandboxAmbiguous(
                    "Resultado HTTP sandbox desconocido",
                    network_call_performed=True,
                )
            else:
                pending_error = ProviderSandboxContractError(
                    "Conexión loopback sandbox no disponible"
                )
        finally:
            secret = None
            headers["Authorization"] = "<redacted>"
            if watchdog is not None:
                watchdog.cancel()
                if watchdog.ident is not None:
                    watchdog.join()
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            if connection is not None:
                connection.close()
            response = None
        if pending_error is not None:
            raw = b""
            raise pending_error

        decode_error: ProviderSandboxTransportError | None = None
        observation: ControlledSandboxObservation | None = None
        try:
            observation = _decode_observation(raw, probe=probe)
        except ProviderSandboxTransportError as exc:
            decode_error = ProviderSandboxContractError(
                str(exc),
                network_call_performed=True,
            )
        except Exception:
            decode_error = ProviderSandboxContractError(
                "Respuesta sandbox no válida",
                network_call_performed=True,
            )
        finally:
            raw = b""
        if decode_error is not None:
            raise decode_error
        if observation is None:
            raise ProviderSandboxContractError(
                "Respuesta sandbox ausente",
                network_call_performed=True,
            )
        return observation

    def submit(
        self,
        probe: ControlledSandboxProbe,
        *,
        idempotency_key: str,
    ) -> ControlledSandboxObservation:
        return self._call(
            method="POST",
            path="/v1/probes",
            probe=probe,
            idempotency_key=idempotency_key,
        )

    def reconcile(
        self,
        probe: ControlledSandboxProbe,
        *,
        idempotency_key: str,
    ) -> ControlledSandboxObservation:
        reference = urllib.parse.quote(probe.client_reference, safe="")
        return self._call(
            method="GET",
            path=f"/v1/probes/by-client-reference/{reference}",
            probe=probe,
            idempotency_key=idempotency_key,
        )


__all__ = [
    "RTM_CONNECT_C6_PROVIDER_TRANSPORT_VERSION",
    "MAX_REQUEST_BYTES",
    "MAX_RESPONSE_BYTES",
    "ControlledSandboxObservation",
    "ControlledSandboxProbe",
    "ControlledSandboxTransport",
    "ProviderSandboxAmbiguous",
    "ProviderSandboxContractError",
    "ProviderSandboxTransportError",
    "SandboxObservationStatus",
]
