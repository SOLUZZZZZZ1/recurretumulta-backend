"""Defensas HTTP comunes y conservadoras para el backend RTM.

Este modulo no decide autorizacion de negocio. Reduce la superficie previa a
los routers: evita que una URL reconstruida desde ``Host`` gobierne controles,
limita cuerpos antes de que los parsers los materialicen y añade cabeceras
seguras incluso a respuestas de error.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import threading
import time
from collections import OrderedDict, deque
from typing import Any, Awaitable, Callable, Mapping, MutableMapping
from urllib.parse import urlsplit


DEFAULT_MAX_REQUEST_BODY_BYTES = 25 * 1024 * 1024
ABSOLUTE_MAX_REQUEST_BODY_BYTES = 64 * 1024 * 1024

_SINGLETON_SECURITY_HEADERS = frozenset(
    {
        b"authorization",
        b"content-length",
        b"content-type",
        b"host",
        b"idempotency-key",
        b"if-match",
        b"origin",
        b"stripe-signature",
        b"transfer-encoding",
        b"x-admin-token",
        b"x-csrf-token",
        b"x-lab-key",
        b"x-operator-actor",
        b"x-operator-token",
        b"x-request-id",
        b"x-reservas-pin",
        b"x-rtm-attachment-manifest-sha256",
        b"x-rtm-case-token",
        b"x-rtm-device",
        b"x-rtm-observed-portal-origin",
        b"x-rtm-presenter-extension",
        b"x-rtm-receipt-capture-source",
        b"x-rtm-receipt-filename",
        b"x-rtm-receipt-media-type",
        b"x-rtm-synthetic-confirmed",
    }
)
_SENSITIVE_COOKIE_NAMES = frozenset(
    {
        b"__Host-rtm_partner_csrf",
        b"__Host-rtm_partner_session",
        b"__Host-rtm_presenter_device",
        # La cookie anterior ya no autentica, pero también se rechaza ambigua
        # mientras las respuestas de login/logout la purgan del navegador.
        b"rtm_presenter_device",
    }
)

LOCAL_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "::1", "testserver")
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _normalize_host_name(raw: str) -> str:
    """Normaliza solo nombres de host exactos, nunca autoridades o URLs."""

    candidate = str(raw or "")
    if not candidate or candidate != candidate.strip():
        raise ValueError("Host vacío o con espacios")
    if any(ord(character) < 33 or ord(character) > 126 for character in candidate):
        raise ValueError("Host contiene caracteres no ASCII o de control")
    if any(character in candidate for character in "*/\\/@?#,%"):
        raise ValueError("Host contiene sintaxis no permitida")

    if candidate.startswith("[") or candidate.endswith("]"):
        if not (candidate.startswith("[") and candidate.endswith("]")):
            raise ValueError("Literal IPv6 no válido")
        candidate = candidate[1:-1]

    lowered = candidate.casefold()
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        if ":" in lowered:
            raise ValueError("RTM_ALLOWED_HOSTS no admite puertos")
        if len(lowered) > 253 or lowered.endswith("."):
            raise ValueError("Nombre DNS no válido")
        labels = lowered.split(".")
        if not labels or any(not _DNS_LABEL.fullmatch(label) for label in labels):
            raise ValueError("Nombre DNS no válido")
        return lowered
    return address.compressed


def parse_allowed_hosts(raw: str | None) -> list[str]:
    """Valida una lista exacta de hosts sin comodines, puertos ni esquemas."""

    text = str(raw or "").strip()
    if not text:
        return []
    parts = text.split(",")
    if any(not item.strip() for item in parts):
        raise ValueError("RTM_ALLOWED_HOSTS contiene una entrada vacía")

    hosts: list[str] = []
    for item in parts:
        candidate = item.strip()
        if candidate == "*" or "*" in candidate:
            raise ValueError("RTM_ALLOWED_HOSTS no admite comodines")
        host = _normalize_host_name(candidate)
        if host not in hosts:
            hosts.append(host)
    return hosts


def configured_allowed_hosts(
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Resuelve la allowlist de Host; un perfil ambiguo queda sin hosts."""

    source = environ if environ is not None else os.environ
    environment = str(source.get("RTM_ENV") or "").strip().casefold()
    raw = str(source.get("RTM_ALLOWED_HOSTS") or "")

    if environment in {"", "development", "test"}:
        if not raw.strip():
            return LOCAL_ALLOWED_HOSTS
        configured = tuple(parse_allowed_hosts(raw))
        if not configured or any(host not in LOCAL_ALLOWED_HOSTS for host in configured):
            raise ValueError(
                "development/test solo admite localhost, loopback y testserver"
            )
        return configured

    if environment in {"staging", "production"}:
        # La ausencia se conserva como allowlist vacía: el middleware niega
        # todo y el contrato de entorno impide completar el startup.
        return tuple(parse_allowed_hosts(raw))

    # Un RTM_ENV desconocido no puede degradar a la allowlist local.
    return ()


def _request_host(scope: Mapping[str, Any]) -> str:
    """Extrae el hostname de Host con gramática estricta y puerto opcional."""

    authority = _header_value(scope, b"host")
    if not authority:
        raise ValueError("Falta Host")
    if any(ord(character) < 33 or ord(character) > 126 for character in authority):
        raise ValueError("Host inválido")
    if any(character in authority for character in "/\\@?#,%"):
        raise ValueError("Host inválido")

    if authority.startswith("["):
        closing = authority.find("]")
        if closing <= 1:
            raise ValueError("Host IPv6 inválido")
        host_literal = authority[1:closing]
        suffix = authority[closing + 1 :]
        if suffix:
            if not suffix.startswith(":") or not suffix[1:].isdigit():
                raise ValueError("Puerto de Host inválido")
            port = int(suffix[1:])
            if not 1 <= port <= 65535:
                raise ValueError("Puerto de Host inválido")
        address = ipaddress.ip_address(host_literal)
        if address.version != 6:
            raise ValueError("Los corchetes se reservan para IPv6")
        return address.compressed

    if "[" in authority or "]" in authority or authority.count(":") > 1:
        raise ValueError("Host IPv6 debe usar corchetes")
    hostname = authority
    if ":" in authority:
        hostname, raw_port = authority.rsplit(":", 1)
        if not raw_port.isdigit() or not 1 <= int(raw_port) <= 65535:
            raise ValueError("Puerto de Host inválido")
    return _normalize_host_name(hostname)


class ExactHostMiddleware:
    """Rechaza autoridades ambiguas o ajenas antes de llegar a los routers."""

    def __init__(self, app: Any, allowed_hosts: tuple[str, ...] | list[str]) -> None:
        self.app = app
        self.allowed_hosts = frozenset(
            _normalize_host_name(host) for host in allowed_hosts
        )

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        try:
            host = _request_host(scope)
        except (TypeError, ValueError):
            host = ""
        if not host or host not in self.allowed_hosts:
            await _send_json(send, status=400, detail="Host no autorizado")
            return
        await self.app(scope, receive, send)

def scope_path(scope_or_request: Any) -> str:
    """Devuelve exclusivamente el path resuelto por ASGI, nunca ``request.url``.

    ``request.url`` incorpora datos controlables como ``Host``. Los gates deben
    mirar el mismo path que utiliza el router.
    """

    scope = getattr(scope_or_request, "scope", scope_or_request)
    if not isinstance(scope, Mapping):
        return "/"
    value = scope.get("path")
    if not isinstance(value, str) or not value.startswith("/"):
        return "/"
    return value


def parse_allowed_origins(raw: str | None) -> list[str]:
    """Valida una lista CORS explícita y falla cerrada cuando está vacía."""

    origins: list[str] = []
    for item in str(raw or "").split(","):
        candidate = item.strip()
        if not candidate:
            continue
        if candidate == "*":
            raise ValueError("ALLOWED_ORIGINS no admite comodines")
        parsed = urlsplit(candidate)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"Origen CORS no valido: {candidate!r}")
        if parsed.scheme == "http" and parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("Los origenes CORS remotos deben usar HTTPS")
        canonical = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port is not None:
            canonical += f":{parsed.port}"
        if canonical not in origins:
            origins.append(canonical)
    return origins


def configured_request_body_limit(environ: Mapping[str, str] | None = None) -> int:
    source = environ if environ is not None else os.environ
    raw = str(source.get("RTM_MAX_REQUEST_BODY_BYTES") or "").strip()
    if not raw:
        return DEFAULT_MAX_REQUEST_BODY_BYTES
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("RTM_MAX_REQUEST_BODY_BYTES debe ser un entero") from exc
    if value < 1024 or value > ABSOLUTE_MAX_REQUEST_BODY_BYTES:
        raise ValueError(
            "RTM_MAX_REQUEST_BODY_BYTES debe estar entre 1024 y "
            f"{ABSOLUTE_MAX_REQUEST_BODY_BYTES}"
        )
    return value


def trusted_client_ip(
    scope_or_request: Any,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Usa proxy headers solo si el peer pertenece a una red configurada."""

    source = environ if environ is not None else os.environ
    scope = getattr(scope_or_request, "scope", scope_or_request)
    if not isinstance(scope, Mapping):
        return ""
    client = scope.get("client")
    direct = str(client[0] if isinstance(client, (tuple, list)) and client else "")
    try:
        direct_ip = ipaddress.ip_address(direct)
    except ValueError:
        return ""

    trust_proxy_headers = str(
        source.get("RTM_TRUST_PROXY_HEADERS") or ""
    ).strip().casefold()
    if trust_proxy_headers not in {"1", "true", "yes", "on"}:
        return str(direct_ip)

    networks = []
    for raw in str(source.get("RTM_TRUSTED_PROXY_CIDRS") or "").split(","):
        candidate = raw.strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            continue
    if not networks or not any(direct_ip in network for network in networks):
        return str(direct_ip)

    # Un proxy correcto debe entregar una única cadena X-Forwarded-For. Si
    # llegan varias cabeceras separadas, elegir la primera o la última crea una
    # discrepancia explotable entre ingress, servidor ASGI y aplicación. La
    # extracción estricta devuelve vacío en ese caso y conserva el peer directo
    # como identidad para la cuota.
    forwarded = _header_value(scope, b"x-forwarded-for")
    return resolve_forwarded_client_ip(
        direct=str(direct_ip),
        forwarded=forwarded,
        trusted_proxy_cidrs=tuple(str(network) for network in networks),
    )


def resolve_forwarded_client_ip(
    *,
    direct: str,
    forwarded: str,
    trusted_proxy_cidrs: tuple[str, ...],
) -> str:
    """Walk X-Forwarded-For from the trusted ingress towards the client."""

    try:
        current = ipaddress.ip_address(str(direct or "").strip())
        networks = tuple(
            ipaddress.ip_network(cidr, strict=False)
            for cidr in trusted_proxy_cidrs
        )
    except ValueError:
        return ""
    if not networks or not any(current in network for network in networks):
        return str(current)
    raw_hops = [
        item.strip().strip('"')
        for item in str(forwarded or "").split(",")
    ]
    if not raw_hops or any(not item for item in raw_hops):
        return str(current)
    try:
        hops = [ipaddress.ip_address(item) for item in raw_hops]
    except ValueError:
        return str(current)
    for hop in reversed(hops):
        if not any(current in network for network in networks):
            break
        current = hop
    return str(current)


def _header_value(scope: Mapping[str, Any], name: bytes) -> str:
    values = [
        value.decode("latin-1").strip()
        for key, value in scope.get("headers", [])
        if key.lower() == name
    ]
    if len(values) != 1:
        return ""
    return values[0]


async def _send_json(
    send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    *,
    status: int,
    detail: str,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    response_headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    response_headers.extend(headers or [])
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": response_headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


class SecurityHeaderAmbiguityMiddleware:
    """Rechaza identidades HTTP ambiguas antes de autenticación y webhooks.

    Authorization, Origin y las capacidades RTM son cabeceras singleton. Para
    Cookie se conserva el *cookie crumbling* legítimo de HTTP/2: varias líneas
    se concatenan con ``; `` tras comprobar que ningún nombre sensible se
    repite dentro de una línea ni entre líneas.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    @staticmethod
    def _normalized_headers(
        scope: Mapping[str, Any],
    ) -> list[tuple[bytes, bytes]] | None:
        raw_headers = list(scope.get("headers", []))
        singleton_counts: dict[bytes, int] = {}
        cookie_values: list[bytes] = []
        sensitive_cookie_counts: dict[bytes, int] = {}
        has_transfer_encoding = False

        for raw_name, raw_value in raw_headers:
            name = bytes(raw_name).lower()
            value = bytes(raw_value)
            if name in _SINGLETON_SECURITY_HEADERS:
                count = singleton_counts.get(name, 0) + 1
                singleton_counts[name] = count
                if count > 1:
                    return None
            if name == b"transfer-encoding":
                has_transfer_encoding = True
            if name != b"cookie":
                continue
            cookie_values.append(value)
            for item in value.split(b";"):
                cookie_name, separator, _cookie_value = item.strip().partition(b"=")
                cookie_name = cookie_name.strip()
                if not separator or cookie_name not in _SENSITIVE_COOKIE_NAMES:
                    continue
                count = sensitive_cookie_counts.get(cookie_name, 0) + 1
                sensitive_cookie_counts[cookie_name] = count
                if count > 1:
                    return None

        # El ingress/servidor ASGI debe entregar el cuerpo ya decodificado.
        # Aceptar Transfer-Encoding aquí —solo o junto a Content-Length— abre
        # interpretaciones distintas de framing entre proxy y aplicación.
        if has_transfer_encoding:
            return None

        if len(cookie_values) <= 1:
            return raw_headers

        # ASGI no obliga a todos los servidores a presentar Cookie ya
        # concatenada. Normalizar evita que Starlette y el proxy elijan líneas
        # distintas sin prohibir el formato legítimo de HTTP/2.
        combined_cookie = b"; ".join(cookie_values)
        normalized: list[tuple[bytes, bytes]] = []
        cookie_added = False
        for raw_name, raw_value in raw_headers:
            if bytes(raw_name).lower() == b"cookie":
                if not cookie_added:
                    normalized.append((b"cookie", combined_cookie))
                    cookie_added = True
                continue
            normalized.append((bytes(raw_name), bytes(raw_value)))
        return normalized

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        normalized = self._normalized_headers(scope)
        if normalized is None:
            await _send_json(
                send,
                status=400,
                detail="Cabeceras de seguridad ambiguas",
            )
            return
        if normalized != scope.get("headers", []):
            scope["headers"] = normalized
        await self.app(scope, receive, send)


class _RequestTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Rechaza cuerpos sobredimensionados declarados o enviados por chunks."""

    def __init__(self, app: Any, max_body_bytes: int | None = None) -> None:
        self.app = app
        self.max_body_bytes = (
            configured_request_body_limit()
            if max_body_bytes is None
            else int(max_body_bytes)
        )
        if not 1024 <= self.max_body_bytes <= ABSOLUTE_MAX_REQUEST_BODY_BYTES:
            raise ValueError("Limite de cuerpo HTTP fuera del rango seguro")

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        raw_length = _header_value(scope, b"content-length")
        if raw_length:
            if not raw_length.isascii() or not raw_length.isdigit():
                await _send_json(send, status=400, detail="Content-Length no valido")
                return
            try:
                declared = int(raw_length, 10)
            except ValueError:
                await _send_json(send, status=400, detail="Content-Length no valido")
                return
            if declared < 0:
                await _send_json(send, status=400, detail="Content-Length no valido")
                return
            if declared > self.max_body_bytes:
                await _send_json(
                    send,
                    status=413,
                    detail="El cuerpo de la solicitud supera el limite permitido",
                )
                return

        received = 0
        response_started = False

        async def limited_receive() -> MutableMapping[str, Any]:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise _RequestTooLarge
            return message

        async def tracked_send(message: MutableMapping[str, Any]) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestTooLarge:
            if not response_started:
                await _send_json(
                    send,
                    status=413,
                    detail="El cuerpo de la solicitud supera el limite permitido",
                )


DEFAULT_SENSITIVE_RATE_RULES = {
    ("POST", "/partner/admin-create"): (5, 600),
    ("POST", "/partner/change-password"): (10, 600),
    ("POST", "/partner/login"): (20, 300),
    ("POST", "/partner/logout"): (30, 300),
    ("POST", "/partner/cases"): (20, 300),
    ("GET", "/partner/cases"): (120, 300),
    ("GET", "/partner/session"): (120, 300),
    ("GET", "/partner/authorization-template-pdf"): (30, 300),
    ("POST", "/partner/signup"): (5, 600),
    ("POST", "/contact"): (5, 600),
    ("POST", "/cases/*/contact"): (5, 900),
    ("POST", "/cases/*/details"): (10, 900),
    ("POST", "/cases/*/append-documents"): (10, 600),
    ("POST", "/cases/*/review"): (10, 600),
    ("POST", "/cases/*/authorize"): (10, 600),
    ("POST", "/cases/*/authorization-signed"): (10, 600),
    ("POST", "/cases/*/upload-authorization-signed"): (10, 600),
    ("POST", "/cases/*/upload-receipt"): (10, 600),
    ("GET", "/cases/*/public-status"): (60, 300),
    ("GET", "/cases/*/authorization-pdf"): (20, 300),
    ("GET", "/cases/*/rtm-authorization-pdf"): (20, 300),
    ("GET", "/files/presign"): (60, 300),
    ("POST", "/billing/checkout"): (10, 600),
    ("POST", "/checkout"): (10, 600),
    ("GET", "/billing/review-context/*"): (60, 300),
    ("GET", "/billing/status/*"): (60, 300),
    ("GET", "/status/*"): (60, 300),
    ("POST", "/billing/webhook"): (300, 300),
    ("POST", "/webhook"): (300, 300),
    ("POST", "/ops/login"): (10, 300),
    ("POST", "/ops/auth/login"): (20, 300),
    ("GET", "/ops/auth/me"): (120, 300),
    ("POST", "/ops/auth/heartbeat"): (180, 300),
    ("POST", "/ops/auth/reauthenticate"): (20, 300),
    ("POST", "/ops/auth/logout"): (30, 300),
    ("POST", "/ops/auth/password/change"): (10, 600),
    ("POST", "/ops/admin/operators"): (10, 600),
    ("POST", "/ops/admin/operators/*"): (20, 600),
    ("POST", "/ops/admin/sessions/*"): (30, 600),
    ("POST", "/ops/admin/devices/*"): (30, 600),
    ("POST", "/ops/admin/restaurants/create"): (5, 600),
    ("POST", "/ops/automation/tick"): (10, 300),
    ("POST", "/ops/core/cases/*/document-extractions/run"): (5, 600),
    ("POST", "/ops/cases/*/authorization-signature-review"): (12, 600),
    ("POST", "/vehicle-removal/verify-registration"): (10, 600),
    ("POST", "/vehicle-removal/create-checkout-session"): (10, 600),
    ("GET", "/vehicle-removal/quote"): (60, 300),
    ("POST", "/analyze"): (8, 600),
    ("POST", "/analyze/expediente"): (8, 600),
    ("POST", "/cases/intake-draft"): (15, 600),
    ("POST", "/ops/restaurants/change-pin"): (10, 600),
    ("GET", "/ops/restaurant-reservations"): (30, 300),
    ("POST", "/ops/restaurant-reservations"): (30, 300),
    ("POST", "/ops/restaurant-reservations/*"): (30, 300),
    ("PUT", "/ops/restaurant-reservations/*"): (30, 300),
}


class SensitiveRateLimitMiddleware:
    """Cuota local acotada para login, parsers y proveedores de coste.

    Es una segunda barrera por proceso; la cuota distribuida debe mantenerse
    también en el WAF/API gateway. Las claves se hashean y nunca se registran.
    """

    def __init__(
        self,
        app: Any,
        rules: Mapping[tuple[str, str], tuple[int, int]] | None = None,
        *,
        max_buckets: int = 50_000,
    ) -> None:
        self.app = app
        self.rules = dict(rules or DEFAULT_SENSITIVE_RATE_RULES)
        self.max_buckets = max(100, min(int(max_buckets), 100_000))
        self._buckets: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _rule(self, scope: Mapping[str, Any]) -> tuple[int, int] | None:
        method = str(scope.get("method") or "").upper()
        path = scope_path(scope)
        exact = self.rules.get((method, path))
        if exact is not None:
            return exact
        for (rule_method, rule_path), rule in self.rules.items():
            if rule_method == method and self._path_matches(rule_path, path):
                return rule
        return None

    @staticmethod
    def _path_matches(rule_path: str, path: str) -> bool:
        if "*" not in rule_path:
            return rule_path == path
        # Un asterisco final conserva la semántica de prefijo para acciones
        # anidadas (p. ej. /{id}/cancel). En otra posición sustituye un único
        # segmento y nunca cruza una barra.
        if rule_path.endswith("*") and rule_path.count("*") == 1:
            return path.startswith(rule_path[:-1])
        rule_segments = rule_path.strip("/").split("/")
        path_segments = path.strip("/").split("/")
        return len(rule_segments) == len(path_segments) and all(
            expected == "*" or expected == actual
            for expected, actual in zip(rule_segments, path_segments)
        )

    def _key(self, scope: Mapping[str, Any]) -> str:
        method = str(scope.get("method") or "").upper()
        path = scope_path(scope)
        bucket_path = path
        for rule_method, rule_path in self.rules:
            if rule_method == method and self._path_matches(rule_path, path):
                bucket_path = rule_path
                break
        # Keep read and write quotas independent.  A comparatively generous
        # GET allowance must never be able to exhaust the stricter POST bucket
        # for users sharing the same NAT/proxy address.
        identity = f"{trusted_client_ip(scope)}\x00{method}\x00{bucket_path}"
        return hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()

    def _consume(self, key: str, limit: int, window: int) -> int | None:
        now = time.monotonic()
        cutoff = now - window
        with self._lock:
            bucket = self._buckets.pop(key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                self._buckets[key] = bucket
                return max(1, int(window - (now - bucket[0])))
            bucket.append(now)
            self._buckets[key] = bucket
            while len(self._buckets) > self.max_buckets:
                self._buckets.popitem(last=False)
        return None

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        rule = self._rule(scope)
        if rule is not None:
            limit, window = rule
            retry_after = self._consume(self._key(scope), limit, window)
            if retry_after is not None:
                await _send_json(
                    send,
                    status=429,
                    detail="Demasiadas solicitudes; inténtelo de nuevo más tarde",
                    headers=[(b"retry-after", str(retry_after).encode("ascii"))],
                )
                return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """Aplica cabeceras de aislamiento sin confiar en cada router."""

    _BASE_HEADERS = {
        b"cache-control": b"no-store, private, max-age=0",
        b"pragma": b"no-cache",
        b"permissions-policy": b"camera=(), microphone=(), geolocation=()",
        b"referrer-policy": b"no-referrer",
        b"x-content-type-options": b"nosniff",
        b"x-frame-options": b"DENY",
        b"x-permitted-cross-domain-policies": b"none",
    }

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def secure_send(message: MutableMapping[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {key.lower() for key, _ in headers}
                for key, value in self._BASE_HEADERS.items():
                    if key not in existing:
                        headers.append((key, value))
                content_type = next(
                    (
                        value.decode("latin-1").lower()
                        for key, value in headers
                        if key.lower() == b"content-type"
                    ),
                    "",
                )
                if (
                    "text/html" not in content_type
                    and b"content-security-policy" not in existing
                ):
                    headers.append(
                        (
                            b"content-security-policy",
                            b"default-src 'none'; frame-ancestors 'none'; "
                            b"base-uri 'none'; form-action 'none'",
                        )
                    )
                if scope.get("scheme") == "https" and b"strict-transport-security" not in existing:
                    headers.append(
                        (
                            b"strict-transport-security",
                            b"max-age=31536000; includeSubDomains",
                        )
                    )
                message = dict(message)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, secure_send)
