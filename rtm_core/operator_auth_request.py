"""Contexto seguro de petición para autenticación individual RTM.

Esta unidad interpreta únicamente datos técnicos de la petición. No recoge GPS,
MAC, IMEI, números de serie ni fingerprint del hardware. La IP completa se
entrega solo a la capa de evidencia temporal; el historial normalizado usa IP
enmascarada y correlación HMAC-SHA256.
"""

from __future__ import annotations

import ipaddress
import os
import re
import uuid
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import unquote

from rtm_core.operator_auth_crypto import hmac_identifier


OPERATOR_AUTH_REQUEST_VERSION = "rtm_operator_auth_request_v1_0"
OPERATOR_AUTH_MODE_INDIVIDUAL = "individual"
OPERATOR_AUTH_MODE_LEGACY = "legacy"
OPERATOR_AUTH_MODE_FAIL_CLOSED = "fail_closed"
_DEVICE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{24,200}$")
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled", ""}
_STAGING_IDENTITY_VARIABLES = (
    "RTM_INSTANCE_ID",
    "RTM_DATA_NAMESPACE",
    "RENDER_SERVICE_NAME",
)


class OperatorAuthRoutesDisabled(RuntimeError):
    """Las rutas individuales permanecen desactivadas por configuración."""


class OperatorAuthRuntimeMisconfigured(RuntimeError):
    """La activación de rutas no cumple el contrato mínimo de seguridad."""


@dataclass(frozen=True)
class OperatorAuthRuntimeConfig:
    environment: str
    enabled: bool
    trust_proxy_headers: bool
    hmac_key: str
    evidence_retention_days: int

    @property
    def available(self) -> bool:
        return self.environment == "staging" and self.enabled


@dataclass(frozen=True)
class RequestFingerprint:
    request_id: str
    ip_address: str | None
    ip_masked: str | None
    ip_hash_sha256: str | None
    ip_family: int | None
    ip_source: str
    ip_trusted: bool
    raw_user_agent: str | None
    user_agent_summary: str | None
    device_type: str
    os_family: str | None
    os_version: str | None
    browser_family: str | None
    browser_version: str | None
    country_code: str | None
    region: str | None
    city: str | None
    timezone: str | None
    location_source: str | None
    trusted_headers: dict[str, str]
    risk_flags: tuple[str, ...]


def _strict_flag(value: str | None, *, default: bool = False) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ValueError("Valor booleano no reconocido")


def operator_auth_environment_mode(
    environ: Mapping[str, str] | None = None,
) -> str:
    """Clasifica la frontera legacy sin confiar solo en ``RTM_ENV``.

    El despliegue inicial de sesiones individuales pertenece exclusivamente a
    staging. Si el flag las solicita fuera de staging, tiene un valor inválido
    o cualquier identidad técnica sigue marcando staging, la única respuesta
    segura es cerrar el acceso. El passthrough legacy queda reservado a un
    entorno no marcado en el que la función no se ha solicitado.
    """

    source = environ if environ is not None else os.environ
    environment = str(source.get("RTM_ENV") or "").strip().casefold()
    raw_feature = source.get("RTM_ENABLE_OPERATOR_AUTH_V1")
    try:
        feature_enabled = _strict_flag(raw_feature, default=False)
    except ValueError:
        return OPERATOR_AUTH_MODE_FAIL_CLOSED

    staging_identity_present = any(
        "staging" in str(source.get(variable) or "").strip().casefold()
        for variable in _STAGING_IDENTITY_VARIABLES
    )
    if environment == "staging":
        return OPERATOR_AUTH_MODE_INDIVIDUAL
    if feature_enabled or staging_identity_present:
        return OPERATOR_AUTH_MODE_FAIL_CLOSED
    return OPERATOR_AUTH_MODE_LEGACY


def load_operator_auth_runtime_config(
    environ: Mapping[str, str] | None = None,
    *,
    require_enabled: bool = True,
) -> OperatorAuthRuntimeConfig:
    source = environ if environ is not None else os.environ
    environment = str(source.get("RTM_ENV") or "").strip().lower()
    try:
        enabled = _strict_flag(
            source.get("RTM_ENABLE_OPERATOR_AUTH_V1"),
            default=False,
        )
        trust_proxy = _strict_flag(
            source.get("RTM_TRUST_PROXY_HEADERS"),
            default=False,
        )
    except ValueError as exc:
        raise OperatorAuthRuntimeMisconfigured(str(exc)) from exc

    raw_days = str(
        source.get("RTM_OPERATOR_ACCESS_RETENTION_DAYS") or "180"
    ).strip()
    try:
        retention_days = int(raw_days)
    except ValueError as exc:
        raise OperatorAuthRuntimeMisconfigured(
            "RTM_OPERATOR_ACCESS_RETENTION_DAYS no es un entero"
        ) from exc
    if not 30 <= retention_days <= 365:
        raise OperatorAuthRuntimeMisconfigured(
            "RTM_OPERATOR_ACCESS_RETENTION_DAYS debe estar entre 30 y 365"
        )

    hmac_key = str(source.get("RTM_OPERATOR_ACCESS_HMAC_KEY") or "").strip()
    config = OperatorAuthRuntimeConfig(
        environment=environment,
        enabled=enabled,
        trust_proxy_headers=trust_proxy,
        hmac_key=hmac_key,
        evidence_retention_days=retention_days,
    )
    if require_enabled and not enabled:
        raise OperatorAuthRoutesDisabled("Autenticación individual desactivada")
    if enabled:
        if environment != "staging":
            raise OperatorAuthRuntimeMisconfigured(
                "La primera publicación de rutas solo está autorizada en staging"
            )
        if len(hmac_key) < 32:
            raise OperatorAuthRuntimeMisconfigured(
                "RTM_OPERATOR_ACCESS_HMAC_KEY no cumple la longitud mínima"
            )
    return config


def extract_bearer_token(authorization: str | None) -> str | None:
    raw = str(authorization or "").strip()
    if not raw:
        return None
    parts = raw.split(None, 1)
    if len(parts) != 2 or parts[0].casefold() != "bearer":
        return None
    token = parts[1].strip()
    return token if len(token) >= 32 else None


def normalize_device_token(value: str | None) -> str | None:
    token = str(value or "").strip()
    if not token or not _DEVICE_TOKEN_RE.fullmatch(token):
        return None
    return token


def hash_login_identifier(value: str, secret: str) -> str:
    candidate = str(value or "").strip().casefold() or "<empty>"
    return hmac_identifier(candidate, secret)


def _first_valid_ip(value: str | None) -> str | None:
    for item in str(value or "").split(","):
        candidate = item.strip().strip('"')
        if not candidate or candidate.lower() in {"unknown", "null", "none"}:
            continue
        try:
            return ipaddress.ip_address(candidate).compressed
        except ValueError:
            continue
    return None


def mask_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if address.version == 4:
        parts = address.compressed.split(".")
        return ".".join(parts[:3] + ["xxx"])
    network = ipaddress.ip_network(f"{address.compressed}/48", strict=False)
    return f"{network.network_address.compressed}/48"


def _header(headers: Mapping[str, str], name: str) -> str:
    if hasattr(headers, "get"):
        return str(headers.get(name) or headers.get(name.lower()) or "").strip()
    return ""


def _safe_text(value: str | None, *, max_length: int) -> str | None:
    raw = unquote(str(value or "")).strip()
    if not raw:
        return None
    clean = re.sub(r"[\x00-\x1f\x7f]+", " ", raw)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:max_length] or None


def parse_user_agent(
    value: str | None,
) -> tuple[str, str | None, str | None, str | None, str | None, str | None]:
    ua = str(value or "").strip()
    if not ua:
        return ("unknown", None, None, None, None, None)

    lower = ua.lower()
    device_type = "desktop"
    if any(token in lower for token in ("bot", "crawler", "spider", "headless")):
        device_type = "bot"
    elif "ipad" in lower or "tablet" in lower:
        device_type = "tablet"
    elif "mobile" in lower or "iphone" in lower or "android" in lower:
        device_type = "mobile"

    os_family: str | None = None
    os_version: str | None = None
    os_patterns = (
        ("Windows", r"Windows NT ([0-9.]+)"),
        ("Android", r"Android ([0-9.]+)"),
        ("iOS", r"(?:CPU (?:iPhone )?OS|iPhone OS) ([0-9_]+)"),
        ("macOS", r"Mac OS X ([0-9_\.]+)"),
        ("Linux", r"(Linux)"),
    )
    for family, pattern in os_patterns:
        match = re.search(pattern, ua, re.IGNORECASE)
        if match:
            os_family = family
            if match.lastindex and match.group(1).lower() != "linux":
                os_version = match.group(1).replace("_", ".")[:32]
            break

    browser_family: str | None = None
    browser_version: str | None = None
    browser_patterns = (
        ("Edge", r"EdgA?/([0-9.]+)"),
        ("Firefox", r"Firefox/([0-9.]+)"),
        ("Chrome", r"(?:Chrome|CriOS)/([0-9.]+)"),
        ("Safari", r"Version/([0-9.]+).*Safari/"),
        ("curl", r"curl/([0-9.]+)"),
    )
    for family, pattern in browser_patterns:
        match = re.search(pattern, ua, re.IGNORECASE)
        if match:
            browser_family = family
            browser_version = match.group(1)[:32]
            break

    summary_parts = [
        part
        for part in (
            device_type,
            os_family,
            os_version,
            browser_family,
            browser_version,
        )
        if part
    ]
    summary = " · ".join(summary_parts)[:240] or None
    return (
        device_type,
        os_family,
        os_version,
        browser_family,
        browser_version,
        summary,
    )


def build_request_fingerprint(
    headers: Mapping[str, str],
    *,
    client_host: str | None,
    hmac_key: str,
    trust_proxy_headers: bool,
) -> RequestFingerprint:
    request_id = _safe_text(_header(headers, "x-request-id"), max_length=120)
    request_id = request_id or uuid.uuid4().hex
    risk_flags: list[str] = []

    ip_value: str | None = None
    ip_source = "unknown"
    ip_trusted = False
    source_header: str | None = None
    if trust_proxy_headers:
        for header_name, source_name in (
            ("x-vercel-forwarded-for", "x_vercel_forwarded_for"),
            ("x-forwarded-for", "x_forwarded_for"),
            ("x-real-ip", "x_real_ip"),
        ):
            candidate = _first_valid_ip(_header(headers, header_name))
            if candidate:
                ip_value = candidate
                ip_source = source_name
                ip_trusted = True
                source_header = header_name
                break
    else:
        if any(
            _header(headers, name)
            for name in (
                "x-vercel-forwarded-for",
                "x-forwarded-for",
                "x-real-ip",
            )
        ):
            risk_flags.append("proxy_headers_ignored")

    if not ip_value:
        ip_value = _first_valid_ip(client_host)
        if ip_value:
            ip_source = "direct"
            ip_trusted = True

    ip_family: int | None = None
    ip_hash: str | None = None
    if ip_value:
        address = ipaddress.ip_address(ip_value)
        ip_family = address.version
        ip_hash = hmac_identifier(address.compressed, hmac_key)
    else:
        risk_flags.append("ip_unavailable")

    raw_ua = _safe_text(_header(headers, "user-agent"), max_length=2048)
    (
        device_type,
        os_family,
        os_version,
        browser_family,
        browser_version,
        ua_summary,
    ) = parse_user_agent(raw_ua)
    if device_type == "unknown":
        risk_flags.append("user_agent_unavailable")

    trusted_headers: dict[str, str] = {}
    if source_header:
        trusted_headers[source_header] = _header(headers, source_header)[:512]

    country = region = city = timezone = location_source = None
    if trust_proxy_headers:
        geo_headers = {
            "x-vercel-ip-country": 2,
            "x-vercel-ip-country-region": 120,
            "x-vercel-ip-city": 120,
            "x-vercel-ip-timezone": 120,
        }
        for name, limit in geo_headers.items():
            value = _safe_text(_header(headers, name), max_length=limit)
            if value:
                trusted_headers[name] = value
        raw_country = trusted_headers.get("x-vercel-ip-country")
        if raw_country and re.fullmatch(r"[A-Za-z]{2}", raw_country):
            country = raw_country.upper()
        region = trusted_headers.get("x-vercel-ip-country-region")
        city = trusted_headers.get("x-vercel-ip-city")
        timezone = trusted_headers.get("x-vercel-ip-timezone")
        if any((country, region, city, timezone)):
            location_source = "vercel_headers"

    return RequestFingerprint(
        request_id=request_id,
        ip_address=ip_value,
        ip_masked=mask_ip(ip_value),
        ip_hash_sha256=ip_hash,
        ip_family=ip_family,
        ip_source=ip_source,
        ip_trusted=ip_trusted,
        raw_user_agent=raw_ua,
        user_agent_summary=ua_summary,
        device_type=device_type,
        os_family=os_family,
        os_version=os_version,
        browser_family=browser_family,
        browser_version=browser_version,
        country_code=country,
        region=region,
        city=city,
        timezone=timezone,
        location_source=location_source,
        trusted_headers=trusted_headers,
        risk_flags=tuple(sorted(set(risk_flags))),
    )


__all__ = [
    "OPERATOR_AUTH_REQUEST_VERSION",
    "OperatorAuthRoutesDisabled",
    "OperatorAuthRuntimeConfig",
    "OperatorAuthRuntimeMisconfigured",
    "RequestFingerprint",
    "build_request_fingerprint",
    "extract_bearer_token",
    "hash_login_identifier",
    "load_operator_auth_runtime_config",
    "mask_ip",
    "normalize_device_token",
    "parse_user_agent",
]
