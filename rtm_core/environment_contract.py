"""Contrato de aislamiento y preflight para los entornos RTM.

El módulo no arranca servicios ni muestra secretos. Inspecciona únicamente la
configuración recibida y devuelve un informe seguro para decidir si una
instancia puede considerarse development, test, staging o production.

La primera finalidad es impedir que un servicio de staging utilice por error la
base de datos, el bucket, Stripe, los orígenes web o los canales de salida de
producción. Las capacidades externas son opt-in y cada una conserva sus propias
condiciones de seguridad.
"""

from __future__ import annotations

import os
import re
import ipaddress
from email.utils import parseaddr
from typing import Literal, Mapping, Optional
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, ConfigDict, Field

from rtm_core.http_security import parse_allowed_hosts


ENVIRONMENT_CONTRACT_VERSION = "rtm_environment_contract_v1_2"

EnvironmentName = Literal["development", "test", "staging", "production"]
CheckStatus = Literal["pass", "warning", "blocking"]

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled", ""}
_KNOWN_ENVIRONMENTS = {"development", "test", "staging", "production"}
_LOCAL_ENVIRONMENTS = {"development", "test"}
_DEPLOYED_ENVIRONMENTS = {"staging", "production"}
_STAGING_CONFIRMATION = "RTM_STAGING_ISOLATED"
_PRODUCTION_CONFIRMATION = "RTM_PRODUCTION_LIVE"
_DEFAULT_PRODUCTION_FRONTEND_HOSTS = {
    "recurretumulta.eu",
    "www.recurretumulta.eu",
    "recurretumulta.vercel.app",
}
_TRUSTED_FRONTEND_HOSTS = frozenset(
    _DEFAULT_PRODUCTION_FRONTEND_HOSTS | {"staging.recurretumulta.eu"}
)
_ALLOWED_SMTP_HOSTS = frozenset({"authsmtp.securemail.pro"})
_FORBIDDEN_DEPLOYED_OVERRIDES = (
    "FRONTEND_BASE_URL",
    "OPENAI_BASE_URL",
)
_PLACEHOLDER_TOKENS = {
    "change_me",
    "changeme",
    "example",
    "placeholder",
    "replace_me",
    "secret",
    "todo",
    "your_key_here",
}
_FEATURE_FLAGS = (
    "RTM_ENABLE_B2",
    "RTM_ENABLE_STRIPE",
    "RTM_ENABLE_FINAL_PAYMENTS",
    "RTM_ENABLE_DOCUMENT_PROVIDER",
    "RTM_ENABLE_OUTBOUND_EMAIL",
    "RTM_ENABLE_EXTERNAL_SUBMISSION",
)

# Solo se devuelven nombres, nunca valores. Estas señales permiten distinguir
# una ejecución local realmente vacía de un servicio desplegado cuyo RTM_ENV se
# haya omitido o escrito mal. ``DATABASE_URL`` se admite en desarrollo local
# únicamente cuando RTM_ENV declara explícitamente development/test.
_DEPLOYMENT_SIGNAL_NAMES = (
    "DATABASE_URL",
    "FRONTEND_URL",
    "FRONTEND_BASE_URL",
    "ALLOWED_ORIGINS",
    "RTM_ALLOWED_HOSTS",
    "OPENAI_BASE_URL",
    "RTM_INSTANCE_ID",
    "RTM_DATA_NAMESPACE",
    "RTM_ENVIRONMENT_CONFIRMATION",
    "RTM_SIDE_EFFECT_POLICY",
    "RTM_EXPECTED_BRANCH",
    "RENDER",
    "RENDER_SERVICE_ID",
    "RENDER_SERVICE_NAME",
    "RENDER_EXTERNAL_HOSTNAME",
    "RENDER_INSTANCE_ID",
    "DYNO",
    "K_SERVICE",
    "FLY_APP_NAME",
    "RAILWAY_ENVIRONMENT_ID",
)
_MANAGED_DEPLOYMENT_SIGNAL_NAMES = (
    "RTM_ENVIRONMENT_CONFIRMATION",
    "RENDER",
    "RENDER_SERVICE_ID",
    "RENDER_SERVICE_NAME",
    "RENDER_EXTERNAL_HOSTNAME",
    "RENDER_INSTANCE_ID",
    "DYNO",
    "K_SERVICE",
    "FLY_APP_NAME",
    "RAILWAY_ENVIRONMENT_ID",
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnvironmentCheck(_StrictModel):
    code: str = Field(min_length=1)
    status: CheckStatus
    message: str = Field(min_length=1)
    variables: list[str] = Field(default_factory=list)


class EnvironmentPreflightReport(_StrictModel):
    authority: Literal["rtm_environment_contract"] = "rtm_environment_contract"
    version: str = ENVIRONMENT_CONTRACT_VERSION
    environment: str
    instance_id: Optional[str] = None
    data_namespace: Optional[str] = None
    capabilities: dict[str, bool] = Field(default_factory=dict)
    checks: list[EnvironmentCheck] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    safe: bool = False


class _CheckCollector:
    def __init__(self) -> None:
        self.checks: list[EnvironmentCheck] = []

    def add(
        self,
        *,
        code: str,
        status: CheckStatus,
        message: str,
        variables: tuple[str, ...] = (),
    ) -> None:
        self.checks.append(
            EnvironmentCheck(
                code=code,
                status=status,
                message=message,
                variables=list(variables),
            )
        )

    def pass_(
        self,
        code: str,
        message: str,
        *variables: str,
    ) -> None:
        self.add(
            code=code,
            status="pass",
            message=message,
            variables=tuple(variables),
        )

    def warning(
        self,
        code: str,
        message: str,
        *variables: str,
    ) -> None:
        self.add(
            code=code,
            status="warning",
            message=message,
            variables=tuple(variables),
        )

    def blocking(
        self,
        code: str,
        message: str,
        *variables: str,
    ) -> None:
        self.add(
            code=code,
            status="blocking",
            message=message,
            variables=tuple(variables),
        )


def _value(environ: Mapping[str, str], name: str) -> str:
    return str(environ.get(name) or "").strip()


def deployment_runtime_signals(
    environ: Optional[Mapping[str, str]] = None,
) -> tuple[str, ...]:
    """Enumera solo los nombres de señales que delatan un runtime desplegado."""

    source: Mapping[str, str] = environ if environ is not None else os.environ
    names = {
        name
        for name in (*_DEPLOYMENT_SIGNAL_NAMES, *_FEATURE_FLAGS)
        if _value(source, name)
    }
    # Render puede añadir nuevas variables de plataforma. El prefijo es una
    # señal inequívoca y el valor no se copia ni se registra.
    names.update(
        str(name)
        for name, value in source.items()
        if str(name).startswith("RENDER_") and str(value or "").strip()
    )
    return tuple(sorted(names))


def runtime_requires_environment_preflight(
    environ: Optional[Mapping[str, str]] = None,
) -> bool:
    """Cierra entornos desplegados o ambiguos antes de servir una petición."""

    source: Mapping[str, str] = environ if environ is not None else os.environ
    environment = _value(source, "RTM_ENV").lower()
    if environment in _DEPLOYED_ENVIRONMENTS:
        return True
    if environment and environment not in _LOCAL_ENVIRONMENTS:
        return True

    signals = set(deployment_runtime_signals(source))
    if environment in _LOCAL_ENVIRONMENTS:
        return bool(signals.intersection(_MANAGED_DEPLOYMENT_SIGNAL_NAMES))
    return bool(signals)


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _looks_placeholder(value: str) -> bool:
    normalised = _normalise(value)
    if not normalised:
        return True
    if "<" in value or ">" in value:
        return True
    return any(token.replace("_", "") in normalised for token in _PLACEHOLDER_TOKENS)


def _looks_trivial_secret(value: str) -> bool:
    """Reject obvious repetitions/sequences without guessing provider entropy."""

    compact = "".join(character for character in value.casefold() if character.isalnum())
    if not compact:
        return True
    if len(set(compact)) < 6:
        return True
    if max(compact.count(character) for character in set(compact)) * 2 >= len(compact):
        return True
    for period in range(1, min(8, len(compact) // 2) + 1):
        if all(character == compact[index % period] for index, character in enumerate(compact)):
            return True
    return any(
        sequence in compact
        for sequence in (
            "0123456789",
            "1234567890",
            "abcdefghijklmnopqrstuvwxyz",
            "qwertyuiop",
        )
    )


def _flag(environ: Mapping[str, str], name: str) -> tuple[bool, bool]:
    raw = _value(environ, name).lower()
    if raw in _TRUE_VALUES:
        return True, True
    if raw in _FALSE_VALUES:
        return False, True
    return False, False


def _url_parts(value: str):
    try:
        return urlparse(value)
    except Exception:
        return urlparse("")


def _origin(value: str) -> Optional[str]:
    parsed = _url_parts(value)
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return None
    return f"https://{parsed.hostname.lower()}"


def _database_name(value: str) -> Optional[str]:
    parsed = _url_parts(value)
    if not parsed.scheme.startswith("postgresql") or not parsed.hostname:
        return None
    database_name = unquote(parsed.path.lstrip("/").split("/", 1)[0]).strip()
    return database_name or None


def _isolation_markers(instance_id: str, namespace: str) -> set[str]:
    markers = {"staging"}
    for candidate in (instance_id, namespace):
        normalised = _normalise(candidate)
        if len(normalised) >= 5:
            markers.add(normalised)
        for part in re.split(r"[^a-z0-9]+", candidate.lower()):
            part_normalised = _normalise(part)
            if len(part_normalised) >= 5:
                markers.add(part_normalised)
    return markers


def _contains_any_marker(value: str, markers: set[str]) -> bool:
    normalised = _normalise(value)
    return bool(normalised and any(marker in normalised for marker in markers))


def _production_frontend_hosts(environ: Mapping[str, str]) -> set[str]:
    configured = {
        item.strip().lower()
        for item in _value(environ, "RTM_PRODUCTION_FRONTEND_HOSTS").split(",")
        if item.strip()
    }
    return _DEFAULT_PRODUCTION_FRONTEND_HOSTS | configured


def _require_non_secret(
    collector: _CheckCollector,
    environ: Mapping[str, str],
    name: str,
    *,
    code: str,
) -> str:
    value = _value(environ, name)
    if value:
        collector.pass_(code, f"{name} está configurada.", name)
    else:
        collector.blocking(code, f"Falta la variable obligatoria {name}.", name)
    return value


def _require_secret(
    collector: _CheckCollector,
    environ: Mapping[str, str],
    name: str,
    *,
    code: str,
    minimum_length: int = 20,
    reject_trivial: bool = True,
) -> str:
    value = _value(environ, name)
    if not value:
        collector.blocking(code, f"Falta el secreto obligatorio {name}.", name)
    elif (
        len(value) < minimum_length
        or _looks_placeholder(value)
        or (reject_trivial and _looks_trivial_secret(value))
    ):
        collector.blocking(
            code,
            f"{name} no supera las reglas mínimas de secreto configurado.",
            name,
        )
    else:
        collector.pass_(code, f"{name} está presente y no se expone en el informe.", name)
    return value


def _check_forbidden_deployed_overrides(
    collector: _CheckCollector,
    environ: Mapping[str, str],
) -> None:
    """Prevent legacy/provider overrides from silently changing trust boundaries."""

    for name in _FORBIDDEN_DEPLOYED_OVERRIDES:
        code = f"{name.lower()}_forbidden"
        if _value(environ, name):
            collector.blocking(
                code,
                f"{name} no puede configurarse en staging o producción.",
                name,
            )
        else:
            collector.pass_(
                code,
                f"{name} no altera el destino canónico del servicio.",
                name,
            )


def _valid_mailbox(value: str) -> bool:
    if not value or len(value) > 320 or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        return False
    display_name, address = parseaddr(value)
    if value != address:
        # ``parseaddr`` es deliberadamente tolerante. Solo se acepta además
        # del buzón plano el formato completo ``Nombre <buzón>``; así texto
        # sobrante no puede convertirse silenciosamente en otra dirección.
        suffix = f"<{address}>"
        if (
            not display_name
            or not value.endswith(suffix)
            or not value[: -len(suffix)].strip()
        ):
            return False
    return bool(
        address
        and len(address) <= 254
        and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address)
    )


def _check_smtp_configuration(
    collector: _CheckCollector,
    environ: Mapping[str, str],
) -> None:
    host = _require_non_secret(
        collector,
        environ,
        "SMTP_HOST",
        code="smtp_host_present",
    ).casefold().rstrip(".")
    if host and host not in _ALLOWED_SMTP_HOSTS:
        collector.blocking(
            "smtp_host_allowed",
            "SMTP_HOST no pertenece al proveedor SMTP aprobado.",
            "SMTP_HOST",
        )
    elif host:
        collector.pass_(
            "smtp_host_allowed",
            "SMTP_HOST pertenece al proveedor SMTP aprobado.",
            "SMTP_HOST",
        )

    raw_port = _require_non_secret(
        collector,
        environ,
        "SMTP_PORT",
        code="smtp_port_present",
    )
    try:
        port = int(raw_port, 10)
    except ValueError:
        port = 0

    security = _require_non_secret(
        collector,
        environ,
        "SMTP_SECURITY",
        code="smtp_security_present",
    ).casefold()
    expected_port = {"ssl": 465, "starttls": 587}.get(security)
    if expected_port is None:
        collector.blocking(
            "smtp_transport_secure",
            "SMTP_SECURITY debe ser ssl o starttls; plain está prohibido.",
            "SMTP_SECURITY",
        )
    elif port != expected_port:
        collector.blocking(
            "smtp_transport_secure",
            f"SMTP_SECURITY={security} exige el puerto {expected_port}.",
            "SMTP_SECURITY",
            "SMTP_PORT",
        )
    else:
        collector.pass_(
            "smtp_transport_secure",
            "El transporte y puerto SMTP coinciden con el perfil TLS aprobado.",
            "SMTP_SECURITY",
            "SMTP_PORT",
        )

    user = _require_non_secret(
        collector,
        environ,
        "SMTP_USER",
        code="smtp_user_present",
    )
    sender = _require_non_secret(
        collector,
        environ,
        "SMTP_FROM",
        code="smtp_from_present",
    )
    for name, value in (("SMTP_USER", user), ("SMTP_FROM", sender)):
        if value and not _valid_mailbox(value):
            collector.blocking(
                f"{name.lower()}_valid",
                f"{name} debe identificar un buzón de correo válido.",
                name,
            )
        elif value:
            collector.pass_(
                f"{name.lower()}_valid",
                f"{name} identifica un buzón válido.",
                name,
            )

    _require_secret(
        collector,
        environ,
        "SMTP_PASSWORD",
        code="smtp_password_ready",
        minimum_length=20,
    )


def _check_base_identity(
    collector: _CheckCollector,
    environ: Mapping[str, str],
    environment: str,
) -> tuple[str, str, set[str]]:
    instance_id = _require_non_secret(
        collector,
        environ,
        "RTM_INSTANCE_ID",
        code="instance_id_present",
    )
    namespace = _require_non_secret(
        collector,
        environ,
        "RTM_DATA_NAMESPACE",
        code="data_namespace_present",
    )
    markers = _isolation_markers(instance_id, namespace)

    if environment == "staging":
        confirmation = _value(environ, "RTM_ENVIRONMENT_CONFIRMATION")
        if confirmation == _STAGING_CONFIRMATION:
            collector.pass_(
                "staging_confirmation",
                "La instancia conserva la confirmación explícita de staging aislado.",
                "RTM_ENVIRONMENT_CONFIRMATION",
            )
        else:
            collector.blocking(
                "staging_confirmation",
                f"RTM_ENVIRONMENT_CONFIRMATION debe ser {_STAGING_CONFIRMATION}.",
                "RTM_ENVIRONMENT_CONFIRMATION",
            )

        if _contains_any_marker(instance_id, {"staging"}) and _contains_any_marker(
            namespace,
            {"staging"},
        ):
            collector.pass_(
                "staging_identity_marked",
                "La identidad y el espacio de datos están marcados como staging.",
                "RTM_INSTANCE_ID",
                "RTM_DATA_NAMESPACE",
            )
        else:
            collector.blocking(
                "staging_identity_marked",
                "RTM_INSTANCE_ID y RTM_DATA_NAMESPACE deben identificar staging de forma inequívoca.",
                "RTM_INSTANCE_ID",
                "RTM_DATA_NAMESPACE",
            )

        if _value(environ, "RTM_SIDE_EFFECT_POLICY").lower() == "isolated":
            collector.pass_(
                "staging_side_effect_policy",
                "La política de efectos externos está fijada como isolated.",
                "RTM_SIDE_EFFECT_POLICY",
            )
        else:
            collector.blocking(
                "staging_side_effect_policy",
                "RTM_SIDE_EFFECT_POLICY debe ser isolated en staging.",
                "RTM_SIDE_EFFECT_POLICY",
            )

        real_data, real_data_valid = _flag(environ, "RTM_ALLOW_REAL_CUSTOMER_DATA")
        if real_data_valid and not real_data:
            collector.pass_(
                "staging_real_data_disabled",
                "Staging prohíbe datos reales de clientes.",
                "RTM_ALLOW_REAL_CUSTOMER_DATA",
            )
        else:
            collector.blocking(
                "staging_real_data_disabled",
                "RTM_ALLOW_REAL_CUSTOMER_DATA debe ser 0 en staging.",
                "RTM_ALLOW_REAL_CUSTOMER_DATA",
            )

    elif environment == "production":
        confirmation = _value(environ, "RTM_ENVIRONMENT_CONFIRMATION")
        if confirmation == _PRODUCTION_CONFIRMATION:
            collector.pass_(
                "production_confirmation",
                "La instancia conserva la confirmación explícita de producción.",
                "RTM_ENVIRONMENT_CONFIRMATION",
            )
        else:
            collector.blocking(
                "production_confirmation",
                f"RTM_ENVIRONMENT_CONFIRMATION debe ser {_PRODUCTION_CONFIRMATION}.",
                "RTM_ENVIRONMENT_CONFIRMATION",
            )

        identity_text = f"{instance_id} {namespace}".lower()
        if not any(token in identity_text for token in ("staging", "test", "demo")):
            collector.pass_(
                "production_identity_clean",
                "La identidad de producción no contiene marcadores de staging o test.",
                "RTM_INSTANCE_ID",
                "RTM_DATA_NAMESPACE",
            )
        else:
            collector.blocking(
                "production_identity_clean",
                "La identidad de producción contiene un marcador reservado a staging o test.",
                "RTM_INSTANCE_ID",
                "RTM_DATA_NAMESPACE",
            )

        if _value(environ, "RTM_SIDE_EFFECT_POLICY").lower() == "live":
            collector.pass_(
                "production_side_effect_policy",
                "La política de efectos externos está fijada como live.",
                "RTM_SIDE_EFFECT_POLICY",
            )
        else:
            collector.blocking(
                "production_side_effect_policy",
                "RTM_SIDE_EFFECT_POLICY debe ser live en producción.",
                "RTM_SIDE_EFFECT_POLICY",
            )

    return instance_id, namespace, markers


def _check_database(
    collector: _CheckCollector,
    environ: Mapping[str, str],
    environment: str,
    markers: set[str],
) -> None:
    database_url = _value(environ, "DATABASE_URL")
    database_name = _database_name(database_url)
    if not database_name:
        collector.blocking(
            "database_url_valid",
            "DATABASE_URL debe identificar una base PostgreSQL completa y separada.",
            "DATABASE_URL",
        )
        return

    collector.pass_(
        "database_url_valid",
        "DATABASE_URL identifica PostgreSQL; su valor no se incluye en el informe.",
        "DATABASE_URL",
    )
    database_normalised = _normalise(database_name)
    if environment == "staging":
        if _contains_any_marker(database_name, markers):
            collector.pass_(
                "staging_database_isolated",
                "El nombre de la base contiene el marcador de aislamiento de staging.",
                "DATABASE_URL",
            )
        else:
            collector.blocking(
                "staging_database_isolated",
                "La base de staging debe tener un nombre exclusivo que incluya su marcador de aislamiento.",
                "DATABASE_URL",
            )
    elif environment == "production":
        if any(token in database_normalised for token in ("staging", "test", "demo")):
            collector.blocking(
                "production_database_identity",
                "La base declarada para producción parece pertenecer a staging o test.",
                "DATABASE_URL",
            )
        else:
            collector.pass_(
                "production_database_identity",
                "La base de producción no contiene marcadores reservados a staging o test.",
                "DATABASE_URL",
            )


def _check_frontend_and_cors(
    collector: _CheckCollector,
    environ: Mapping[str, str],
    environment: str,
    markers: set[str],
) -> None:
    frontend_url = _value(environ, "FRONTEND_URL")
    frontend_origin = _origin(frontend_url)
    frontend_host = (_url_parts(frontend_url).hostname or "").lower()
    production_hosts = _production_frontend_hosts(environ)

    if not frontend_origin:
        collector.blocking(
            "frontend_url_valid",
            "FRONTEND_URL debe ser una URL http(s) completa.",
            "FRONTEND_URL",
        )
    else:
        collector.pass_(
            "frontend_url_valid",
            "FRONTEND_URL es válida; no se publica en el informe.",
            "FRONTEND_URL",
        )

    if frontend_host not in _TRUSTED_FRONTEND_HOSTS:
        collector.blocking(
            "frontend_host_trusted",
            "FRONTEND_URL debe pertenecer a un host RTM autorizado.",
            "FRONTEND_URL",
        )
    else:
        collector.pass_(
            "frontend_host_trusted",
            "FRONTEND_URL pertenece a un host RTM autorizado.",
            "FRONTEND_URL",
        )

    if environment == "staging" and frontend_origin:
        if frontend_host in production_hosts:
            collector.blocking(
                "staging_frontend_isolated",
                "FRONTEND_URL apunta al host exacto de producción.",
                "FRONTEND_URL",
                "RTM_PRODUCTION_FRONTEND_HOSTS",
            )
        elif not _contains_any_marker(frontend_host, markers):
            collector.blocking(
                "staging_frontend_isolated",
                "El host de staging debe incluir su marcador de aislamiento.",
                "FRONTEND_URL",
            )
        else:
            collector.pass_(
                "staging_frontend_isolated",
                "El frontend de staging está diferenciado del host exacto de producción.",
                "FRONTEND_URL",
            )

    if environment == "production" and frontend_origin:
        if any(token in frontend_host for token in ("staging", "test", "localhost")):
            collector.blocking(
                "production_frontend_identity",
                "FRONTEND_URL de producción parece un host de staging, test o local.",
                "FRONTEND_URL",
            )
        else:
            collector.pass_(
                "production_frontend_identity",
                "El frontend de producción no contiene marcadores de entorno de pruebas.",
                "FRONTEND_URL",
            )

    raw_origins = _value(environ, "ALLOWED_ORIGINS")
    origins = [item.strip() for item in raw_origins.split(",") if item.strip()]
    if not origins:
        collector.blocking(
            "cors_origins_present",
            "ALLOWED_ORIGINS debe declarar al menos un origen explícito.",
            "ALLOWED_ORIGINS",
        )
        return
    if "*" in origins:
        collector.blocking(
            "cors_no_wildcard",
            "ALLOWED_ORIGINS no puede contener * en staging o producción.",
            "ALLOWED_ORIGINS",
        )
    else:
        collector.pass_(
            "cors_no_wildcard",
            "CORS no utiliza un origen comodín.",
            "ALLOWED_ORIGINS",
        )

    parsed_origins = {_origin(item) for item in origins}
    if None in parsed_origins:
        collector.blocking(
            "cors_origins_valid",
            "Todos los valores de ALLOWED_ORIGINS deben ser orígenes http(s) completos.",
            "ALLOWED_ORIGINS",
        )
    else:
        collector.pass_(
            "cors_origins_valid",
            "Todos los orígenes CORS tienen formato válido.",
            "ALLOWED_ORIGINS",
        )

    cors_hosts = {
        str(_url_parts(origin).hostname or "").lower()
        for origin in origins
        if _origin(origin)
    }
    if any(host not in _TRUSTED_FRONTEND_HOSTS for host in cors_hosts):
        collector.blocking(
            "cors_hosts_trusted",
            "CORS contiene un host que no pertenece a RTM.",
            "ALLOWED_ORIGINS",
        )
    else:
        collector.pass_(
            "cors_hosts_trusted",
            "CORS solo contiene hosts RTM autorizados.",
            "ALLOWED_ORIGINS",
        )

    if frontend_origin and frontend_origin not in parsed_origins:
        collector.blocking(
            "cors_includes_frontend",
            "ALLOWED_ORIGINS debe incluir el origen exacto de FRONTEND_URL.",
            "ALLOWED_ORIGINS",
            "FRONTEND_URL",
        )
    elif frontend_origin:
        collector.pass_(
            "cors_includes_frontend",
            "CORS incluye el origen exacto del frontend del entorno.",
            "ALLOWED_ORIGINS",
            "FRONTEND_URL",
        )

    if environment == "staging":
        exact_hosts = {
            (_url_parts(item).hostname or "").lower()
            for item in origins
            if _origin(item)
        }
        if exact_hosts & production_hosts:
            collector.blocking(
                "staging_cors_excludes_production",
                "ALLOWED_ORIGINS de staging contiene un host exacto de producción.",
                "ALLOWED_ORIGINS",
                "RTM_PRODUCTION_FRONTEND_HOSTS",
            )
        else:
            collector.pass_(
                "staging_cors_excludes_production",
                "CORS de staging no autoriza los hosts exactos de producción.",
                "ALLOWED_ORIGINS",
            )


def _check_allowed_hosts(
    collector: _CheckCollector,
    environ: Mapping[str, str],
) -> None:
    """Exige una allowlist de autoridades exactas en perfiles desplegados."""

    raw_hosts = _value(environ, "RTM_ALLOWED_HOSTS")
    if not raw_hosts:
        collector.blocking(
            "allowed_hosts_present",
            "RTM_ALLOWED_HOSTS debe declarar al menos un hostname exacto.",
            "RTM_ALLOWED_HOSTS",
        )
        return
    try:
        hosts = parse_allowed_hosts(raw_hosts)
    except ValueError:
        collector.blocking(
            "allowed_hosts_exact",
            "RTM_ALLOWED_HOSTS solo admite hostnames exactos sin esquemas, puertos ni comodines.",
            "RTM_ALLOWED_HOSTS",
        )
        return
    if not hosts:
        collector.blocking(
            "allowed_hosts_present",
            "RTM_ALLOWED_HOSTS debe declarar al menos un hostname exacto.",
            "RTM_ALLOWED_HOSTS",
        )
        return
    collector.pass_(
        "allowed_hosts_exact",
        "La allowlist HTTP contiene exclusivamente hostnames exactos.",
        "RTM_ALLOWED_HOSTS",
    )


def _check_operator_token(
    collector: _CheckCollector,
    environ: Mapping[str, str],
) -> None:
    _require_secret(
        collector,
        environ,
        "OPERATOR_TOKEN",
        code="operator_token_ready",
        minimum_length=32,
    )


def _check_trusted_proxy_configuration(
    collector: _CheckCollector,
    environ: Mapping[str, str],
) -> None:
    enabled, valid = _flag(environ, "RTM_TRUST_PROXY_HEADERS")
    if not valid:
        collector.blocking(
            "trusted_proxy_flag_valid",
            "RTM_TRUST_PROXY_HEADERS contiene un valor no reconocido.",
            "RTM_TRUST_PROXY_HEADERS",
        )
        return
    raw_cidrs = _value(environ, "RTM_TRUSTED_PROXY_CIDRS")
    if not enabled:
        collector.pass_(
            "trusted_proxy_headers_disabled",
            "Las cabeceras de proxy no se consideran autoritativas.",
            "RTM_TRUST_PROXY_HEADERS",
        )
        return
    networks = []
    try:
        networks = [
            ipaddress.ip_network(item.strip(), strict=False)
            for item in raw_cidrs.split(",")
            if item.strip()
        ]
    except ValueError:
        networks = []
    if not networks or any(network.prefixlen == 0 for network in networks):
        collector.blocking(
            "trusted_proxy_cidrs_restricted",
            "El uso de cabeceras proxy exige CIDR concretos y válidos.",
            "RTM_TRUST_PROXY_HEADERS",
            "RTM_TRUSTED_PROXY_CIDRS",
        )
    else:
        collector.pass_(
            "trusted_proxy_cidrs_restricted",
            "Las cabeceras proxy solo se aceptan desde redes concretas.",
            "RTM_TRUST_PROXY_HEADERS",
            "RTM_TRUSTED_PROXY_CIDRS",
        )


def _check_case_authority_secrets(
    collector: _CheckCollector,
    environ: Mapping[str, str],
) -> None:
    """Require the independent secrets used by public and signed authority chains."""

    public_access_secret = _require_secret(
        collector,
        environ,
        "RTM_PUBLIC_CASE_ACCESS_SECRET",
        code="public_case_access_secret_ready",
        minimum_length=32,
    )
    authority_secret = _require_secret(
        collector,
        environ,
        "RTM_AUTHORITY_SIGNING_SECRET",
        code="authority_signing_secret_ready",
        minimum_length=32,
    )
    if public_access_secret and authority_secret:
        if public_access_secret == authority_secret:
            collector.blocking(
                "authority_secrets_independent",
                "Los secretos de acceso público y firma de autoridad deben ser distintos.",
                "RTM_PUBLIC_CASE_ACCESS_SECRET",
                "RTM_AUTHORITY_SIGNING_SECRET",
            )
        else:
            collector.pass_(
                "authority_secrets_independent",
                "Los secretos de acceso público y firma de autoridad son independientes.",
                "RTM_PUBLIC_CASE_ACCESS_SECRET",
                "RTM_AUTHORITY_SIGNING_SECRET",
            )


def _check_deployment_identity(
    collector: _CheckCollector,
    environ: Mapping[str, str],
    environment: str,
    markers: set[str],
) -> None:
    expected_branch = _require_non_secret(
        collector,
        environ,
        "RTM_EXPECTED_BRANCH",
        code="expected_branch_present",
    )
    runtime_branch = next(
        (
            _value(environ, name)
            for name in ("RENDER_GIT_BRANCH", "GIT_BRANCH", "BRANCH_NAME")
            if _value(environ, name)
        ),
        "",
    )

    if environment == "staging" and expected_branch == "main":
        collector.blocking(
            "staging_branch_not_main",
            "Un servicio de staging no puede declarar main como rama esperada.",
            "RTM_EXPECTED_BRANCH",
        )
    elif environment == "staging" and expected_branch:
        collector.pass_(
            "staging_branch_not_main",
            "La rama esperada de staging está separada de main.",
            "RTM_EXPECTED_BRANCH",
        )

    if environment == "production" and expected_branch and expected_branch != "main":
        collector.blocking(
            "production_branch_main",
            "Producción debe declarar main como rama esperada.",
            "RTM_EXPECTED_BRANCH",
        )
    elif environment == "production" and expected_branch == "main":
        collector.pass_(
            "production_branch_main",
            "Producción declara main como rama esperada.",
            "RTM_EXPECTED_BRANCH",
        )

    if runtime_branch and expected_branch:
        if runtime_branch == expected_branch:
            collector.pass_(
                "runtime_branch_matches",
                "La rama desplegada coincide con RTM_EXPECTED_BRANCH.",
                "RTM_EXPECTED_BRANCH",
                "RENDER_GIT_BRANCH",
                "GIT_BRANCH",
                "BRANCH_NAME",
            )
        else:
            collector.blocking(
                "runtime_branch_matches",
                "La rama desplegada no coincide con RTM_EXPECTED_BRANCH.",
                "RTM_EXPECTED_BRANCH",
                "RENDER_GIT_BRANCH",
                "GIT_BRANCH",
                "BRANCH_NAME",
            )
    elif expected_branch:
        collector.warning(
            "runtime_branch_unavailable",
            "No hay metadato de rama en runtime; la igualdad se comprobará al desplegar.",
            "RENDER_GIT_BRANCH",
            "GIT_BRANCH",
            "BRANCH_NAME",
        )

    expected_commit = _value(environ, "RTM_EXPECTED_COMMIT")
    runtime_commit = next(
        (
            _value(environ, name)
            for name in (
                "RENDER_GIT_COMMIT",
                "GIT_COMMIT",
                "COMMIT_SHA",
                "SOURCE_COMMIT",
            )
            if _value(environ, name)
        ),
        "",
    )
    if expected_commit and runtime_commit:
        if runtime_commit == expected_commit:
            collector.pass_(
                "runtime_commit_matches",
                "El commit desplegado coincide con RTM_EXPECTED_COMMIT.",
                "RTM_EXPECTED_COMMIT",
                "RENDER_GIT_COMMIT",
            )
        else:
            collector.blocking(
                "runtime_commit_matches",
                "El commit desplegado no coincide con RTM_EXPECTED_COMMIT.",
                "RTM_EXPECTED_COMMIT",
                "RENDER_GIT_COMMIT",
            )

    render_service = _value(environ, "RENDER_SERVICE_NAME")
    if environment == "staging" and render_service:
        if _contains_any_marker(render_service, markers):
            collector.pass_(
                "staging_service_name_isolated",
                "El nombre del servicio Render conserva el marcador de staging.",
                "RENDER_SERVICE_NAME",
            )
        else:
            collector.blocking(
                "staging_service_name_isolated",
                "RENDER_SERVICE_NAME debe identificar staging de forma inequívoca.",
                "RENDER_SERVICE_NAME",
            )


def _check_b2(
    collector: _CheckCollector,
    environ: Mapping[str, str],
    environment: str,
    markers: set[str],
    enabled: bool,
) -> None:
    if not enabled:
        if any(_value(environ, name) for name in ("B2_KEY_ID", "B2_APPLICATION_KEY")):
            collector.warning(
                "b2_secrets_present_while_disabled",
                "Hay credenciales B2 configuradas aunque RTM_ENABLE_B2 está desactivado.",
                "RTM_ENABLE_B2",
                "B2_KEY_ID",
                "B2_APPLICATION_KEY",
            )
        else:
            collector.pass_(
                "b2_disabled",
                "El almacenamiento externo B2 está desactivado.",
                "RTM_ENABLE_B2",
            )
        return

    endpoint = _require_non_secret(
        collector,
        environ,
        "B2_ENDPOINT",
        code="b2_endpoint_present",
    )
    bucket = _require_non_secret(
        collector,
        environ,
        "B2_BUCKET",
        code="b2_bucket_present",
    )
    _require_secret(
        collector,
        environ,
        "B2_KEY_ID",
        code="b2_key_id_ready",
        minimum_length=10,
        # Es un identificador de cuenta, no material secreto por sí solo. La
        # clave de aplicación sí conserva la comprobación anti-valores triviales.
        reject_trivial=False,
    )
    _require_secret(
        collector,
        environ,
        "B2_APPLICATION_KEY",
        code="b2_application_key_ready",
        minimum_length=20,
    )

    parsed_endpoint = _url_parts(endpoint)
    endpoint_host = str(parsed_endpoint.hostname or "").lower()
    if (
        parsed_endpoint.scheme == "https"
        and parsed_endpoint.username is None
        and parsed_endpoint.password is None
        and parsed_endpoint.port in (None, 443)
        and parsed_endpoint.path in ("", "/")
        and not parsed_endpoint.query
        and not parsed_endpoint.fragment
        and re.fullmatch(r"s3\.[a-z0-9-]+\.backblazeb2\.com", endpoint_host)
    ):
        collector.pass_(
            "b2_endpoint_official",
            "B2_ENDPOINT utiliza un origen HTTPS oficial de Backblaze.",
            "B2_ENDPOINT",
        )
    else:
        collector.blocking(
            "b2_endpoint_official",
            "B2_ENDPOINT debe ser un origen HTTPS oficial de Backblaze.",
            "B2_ENDPOINT",
        )

    if _value(environ, "RTM_B2_ISOLATION_MODE").lower() == "dedicated_bucket":
        collector.pass_(
            "b2_dedicated_bucket",
            "El entorno declara un bucket B2 dedicado.",
            "RTM_B2_ISOLATION_MODE",
        )
    else:
        collector.blocking(
            "b2_dedicated_bucket",
            "RTM_B2_ISOLATION_MODE debe ser dedicated_bucket.",
            "RTM_B2_ISOLATION_MODE",
        )

    if environment == "staging" and bucket:
        production_bucket = _value(environ, "RTM_PRODUCTION_B2_BUCKET")
        if production_bucket and bucket == production_bucket:
            collector.blocking(
                "staging_b2_bucket_isolated",
                "B2_BUCKET de staging coincide con el bucket declarado para producción.",
                "B2_BUCKET",
                "RTM_PRODUCTION_B2_BUCKET",
            )
        elif not _contains_any_marker(bucket, markers):
            collector.blocking(
                "staging_b2_bucket_isolated",
                "B2_BUCKET de staging debe incluir su marcador de aislamiento.",
                "B2_BUCKET",
            )
        else:
            collector.pass_(
                "staging_b2_bucket_isolated",
                "B2_BUCKET está identificado como recurso exclusivo de staging.",
                "B2_BUCKET",
            )

    if environment == "production" and bucket:
        if any(token in bucket.lower() for token in ("staging", "test", "demo")):
            collector.blocking(
                "production_b2_bucket_identity",
                "B2_BUCKET de producción parece pertenecer a staging o test.",
                "B2_BUCKET",
            )
        else:
            collector.pass_(
                "production_b2_bucket_identity",
                "B2_BUCKET de producción no contiene marcadores de pruebas.",
                "B2_BUCKET",
            )


def _check_stripe(
    collector: _CheckCollector,
    environ: Mapping[str, str],
    environment: str,
    enabled: bool,
    final_payments: bool,
) -> None:
    if final_payments and not enabled:
        collector.blocking(
            "final_payments_require_stripe",
            "RTM_ENABLE_FINAL_PAYMENTS requiere RTM_ENABLE_STRIPE.",
            "RTM_ENABLE_FINAL_PAYMENTS",
            "RTM_ENABLE_STRIPE",
        )

    if not enabled:
        if _value(environ, "STRIPE_SECRET_KEY"):
            collector.warning(
                "stripe_secret_present_while_disabled",
                "STRIPE_SECRET_KEY está configurada aunque Stripe está desactivado.",
                "RTM_ENABLE_STRIPE",
                "STRIPE_SECRET_KEY",
            )
        else:
            collector.pass_(
                "stripe_disabled",
                "Stripe está desactivado para este entorno.",
                "RTM_ENABLE_STRIPE",
            )
        return

    secret_key = _require_secret(
        collector,
        environ,
        "STRIPE_SECRET_KEY",
        code="stripe_secret_ready",
        minimum_length=20,
    )
    webhook_secret = _require_secret(
        collector,
        environ,
        "STRIPE_WEBHOOK_SECRET",
        code="stripe_webhook_secret_ready",
        minimum_length=20,
    )
    basic_price = _require_non_secret(
        collector,
        environ,
        "STRIPE_PRICE_ID_REVIEW_BASIC",
        code="stripe_basic_price_present",
    )
    admin_price = _require_non_secret(
        collector,
        environ,
        "STRIPE_PRICE_ID_ADMIN",
        code="stripe_admin_price_present",
    )

    if webhook_secret and not webhook_secret.startswith("whsec_"):
        collector.blocking(
            "stripe_webhook_secret_format",
            "STRIPE_WEBHOOK_SECRET no tiene el formato esperado de webhook.",
            "STRIPE_WEBHOOK_SECRET",
        )
    elif webhook_secret:
        collector.pass_(
            "stripe_webhook_secret_format",
            "El secreto de webhook tiene formato compatible.",
            "STRIPE_WEBHOOK_SECRET",
        )

    for name, value in (
        ("STRIPE_PRICE_ID_REVIEW_BASIC", basic_price),
        ("STRIPE_PRICE_ID_ADMIN", admin_price),
    ):
        if value and not value.startswith("price_"):
            collector.blocking(
                f"{name.lower()}_format",
                f"{name} no tiene formato de Price ID de Stripe.",
                name,
            )
        elif value:
            collector.pass_(
                f"{name.lower()}_format",
                f"{name} tiene formato compatible.",
                name,
            )

    stripe_mode = _value(environ, "RTM_STRIPE_MODE").lower()
    real_payments, real_payments_valid = _flag(environ, "RTM_ALLOW_REAL_PAYMENTS")
    if environment == "staging":
        if stripe_mode == "test" and secret_key.startswith("sk_test_"):
            collector.pass_(
                "staging_stripe_test_mode",
                "Stripe utiliza una clave de test en staging.",
                "RTM_STRIPE_MODE",
                "STRIPE_SECRET_KEY",
            )
        else:
            collector.blocking(
                "staging_stripe_test_mode",
                "Staging exige RTM_STRIPE_MODE=test y una STRIPE_SECRET_KEY sk_test_.",
                "RTM_STRIPE_MODE",
                "STRIPE_SECRET_KEY",
            )
        if real_payments_valid and not real_payments:
            collector.pass_(
                "staging_real_payments_disabled",
                "Staging prohíbe pagos reales.",
                "RTM_ALLOW_REAL_PAYMENTS",
            )
        else:
            collector.blocking(
                "staging_real_payments_disabled",
                "RTM_ALLOW_REAL_PAYMENTS debe ser 0 en staging.",
                "RTM_ALLOW_REAL_PAYMENTS",
            )
    elif environment == "production":
        if stripe_mode == "live" and secret_key.startswith("sk_live_"):
            collector.pass_(
                "production_stripe_live_mode",
                "Stripe utiliza una clave live en producción.",
                "RTM_STRIPE_MODE",
                "STRIPE_SECRET_KEY",
            )
        else:
            collector.blocking(
                "production_stripe_live_mode",
                "Producción exige RTM_STRIPE_MODE=live y una STRIPE_SECRET_KEY sk_live_.",
                "RTM_STRIPE_MODE",
                "STRIPE_SECRET_KEY",
            )
        if real_payments_valid and real_payments:
            collector.pass_(
                "production_real_payments_enabled",
                "Producción autoriza expresamente pagos reales.",
                "RTM_ALLOW_REAL_PAYMENTS",
            )
        else:
            collector.blocking(
                "production_real_payments_enabled",
                "RTM_ALLOW_REAL_PAYMENTS debe ser 1 cuando Stripe está activo en producción.",
                "RTM_ALLOW_REAL_PAYMENTS",
            )

    if final_payments:
        name = "STRIPE_PRICE_ID_ELIMINAR_COCHE"
        value = _require_non_secret(
            collector,
            environ,
            name,
            code=f"{name.lower()}_present",
        )
        if value and not value.startswith("price_"):
            collector.blocking(
                f"{name.lower()}_format",
                f"{name} no tiene formato de Price ID de Stripe.",
                name,
            )


def _check_document_provider(
    collector: _CheckCollector,
    environ: Mapping[str, str],
    environment: str,
    enabled: bool,
) -> None:
    if not enabled:
        if _value(environ, "OPENAI_API_KEY"):
            collector.warning(
                "document_provider_secret_present_while_disabled",
                "OPENAI_API_KEY está configurada aunque el proveedor documental está desactivado.",
                "RTM_ENABLE_DOCUMENT_PROVIDER",
                "OPENAI_API_KEY",
            )
        else:
            collector.pass_(
                "document_provider_disabled",
                "El proveedor documental externo está desactivado.",
                "RTM_ENABLE_DOCUMENT_PROVIDER",
            )
        return

    _require_secret(
        collector,
        environ,
        "OPENAI_API_KEY",
        code="document_provider_key_ready",
        minimum_length=20,
    )
    _require_non_secret(
        collector,
        environ,
        "OPENAI_DOCUMENT_MODEL",
        code="document_model_present",
    )
    input_policy = _value(environ, "RTM_DOCUMENT_INPUT_POLICY").lower()
    if environment == "staging":
        if input_policy == "synthetic_only":
            collector.pass_(
                "staging_document_input_policy",
                "El proveedor documental de staging solo admite fixtures sintéticos.",
                "RTM_DOCUMENT_INPUT_POLICY",
            )
        else:
            collector.blocking(
                "staging_document_input_policy",
                "RTM_DOCUMENT_INPUT_POLICY debe ser synthetic_only en staging.",
                "RTM_DOCUMENT_INPUT_POLICY",
            )
    elif environment == "production":
        if input_policy == "customer_documents":
            collector.pass_(
                "production_document_input_policy",
                "Producción declara explícitamente la política de documentos de cliente.",
                "RTM_DOCUMENT_INPUT_POLICY",
            )
        else:
            collector.blocking(
                "production_document_input_policy",
                "RTM_DOCUMENT_INPUT_POLICY debe ser customer_documents en producción.",
                "RTM_DOCUMENT_INPUT_POLICY",
            )


def _check_outbound_channels(
    collector: _CheckCollector,
    environ: Mapping[str, str],
    environment: str,
    outbound_email: bool,
    external_submission: bool,
) -> None:
    if environment == "staging":
        if outbound_email:
            collector.blocking(
                "staging_outbound_email_disabled",
                "RTM_ENABLE_OUTBOUND_EMAIL debe estar desactivado en staging.",
                "RTM_ENABLE_OUTBOUND_EMAIL",
            )
        else:
            collector.pass_(
                "staging_outbound_email_disabled",
                "Staging no puede enviar correos reales.",
                "RTM_ENABLE_OUTBOUND_EMAIL",
            )

        if external_submission:
            collector.blocking(
                "staging_external_submission_disabled",
                "RTM_ENABLE_EXTERNAL_SUBMISSION debe estar desactivado en staging.",
                "RTM_ENABLE_EXTERNAL_SUBMISSION",
            )
        else:
            collector.pass_(
                "staging_external_submission_disabled",
                "Staging no puede presentar actuaciones en sistemas externos.",
                "RTM_ENABLE_EXTERNAL_SUBMISSION",
            )
        return

    if environment == "production":
        notifications_allowed, notifications_valid = _flag(
            environ,
            "RTM_ALLOW_REAL_NOTIFICATIONS",
        )
        submissions_allowed, submissions_valid = _flag(
            environ,
            "RTM_ALLOW_EXTERNAL_SUBMISSIONS",
        )
        if outbound_email:
            _check_smtp_configuration(collector, environ)
            if notifications_valid and notifications_allowed:
                collector.pass_(
                    "production_notifications_confirmed",
                    "Las notificaciones reales están autorizadas expresamente.",
                    "RTM_ALLOW_REAL_NOTIFICATIONS",
                )
            else:
                collector.blocking(
                    "production_notifications_confirmed",
                    "RTM_ALLOW_REAL_NOTIFICATIONS debe ser 1 si el correo saliente está activo.",
                    "RTM_ENABLE_OUTBOUND_EMAIL",
                    "RTM_ALLOW_REAL_NOTIFICATIONS",
                )
        if external_submission:
            if submissions_valid and submissions_allowed:
                collector.pass_(
                    "production_submissions_confirmed",
                    "Las presentaciones externas están autorizadas expresamente.",
                    "RTM_ALLOW_EXTERNAL_SUBMISSIONS",
                )
            else:
                collector.blocking(
                    "production_submissions_confirmed",
                    "RTM_ALLOW_EXTERNAL_SUBMISSIONS debe ser 1 si la presentación externa está activa.",
                    "RTM_ENABLE_EXTERNAL_SUBMISSION",
                    "RTM_ALLOW_EXTERNAL_SUBMISSIONS",
                )


def build_environment_preflight(
    environ: Optional[Mapping[str, str]] = None,
) -> EnvironmentPreflightReport:
    source: Mapping[str, str] = environ if environ is not None else os.environ
    collector = _CheckCollector()
    environment = _value(source, "RTM_ENV").lower()

    if environment not in _KNOWN_ENVIRONMENTS:
        collector.blocking(
            "environment_known",
            "RTM_ENV debe ser development, test, staging o production.",
            "RTM_ENV",
        )
        return EnvironmentPreflightReport(
            environment=environment or "unconfigured",
            checks=collector.checks,
            blockers=[item.code for item in collector.checks if item.status == "blocking"],
            warnings=[item.code for item in collector.checks if item.status == "warning"],
            safe=False,
        )

    collector.pass_(
        "environment_known",
        f"RTM_ENV identifica el perfil {environment}.",
        "RTM_ENV",
    )

    capabilities: dict[str, bool] = {}
    for flag_name in _FEATURE_FLAGS:
        enabled, valid = _flag(source, flag_name)
        capability_name = flag_name.removeprefix("RTM_ENABLE_").lower()
        capabilities[capability_name] = enabled
        if valid:
            collector.pass_(
                f"{capability_name}_flag_valid",
                f"{flag_name} utiliza un valor booleano reconocido.",
                flag_name,
            )
        else:
            collector.blocking(
                f"{capability_name}_flag_valid",
                f"{flag_name} contiene un valor booleano no reconocido.",
                flag_name,
            )

    if environment in {"development", "test"}:
        instance_id = _value(source, "RTM_INSTANCE_ID") or None
        namespace = _value(source, "RTM_DATA_NAMESPACE") or None
        collector.warning(
            "non_deployable_profile",
            "El perfil development/test no acredita un servicio desplegable de staging o producción.",
            "RTM_ENV",
        )
    else:
        _check_forbidden_deployed_overrides(collector, source)
        instance_id, namespace, markers = _check_base_identity(
            collector,
            source,
            environment,
        )
        _check_database(collector, source, environment, markers)
        _check_frontend_and_cors(collector, source, environment, markers)
        _check_allowed_hosts(collector, source)
        _check_operator_token(collector, source)
        _check_trusted_proxy_configuration(collector, source)
        _check_case_authority_secrets(collector, source)
        _check_deployment_identity(collector, source, environment, markers)
        _check_b2(
            collector,
            source,
            environment,
            markers,
            capabilities["b2"],
        )
        _check_stripe(
            collector,
            source,
            environment,
            capabilities["stripe"],
            capabilities["final_payments"],
        )
        _check_document_provider(
            collector,
            source,
            environment,
            capabilities["document_provider"],
        )
        _check_outbound_channels(
            collector,
            source,
            environment,
            capabilities["outbound_email"],
            capabilities["external_submission"],
        )

    blockers = [item.code for item in collector.checks if item.status == "blocking"]
    warnings = [item.code for item in collector.checks if item.status == "warning"]
    return EnvironmentPreflightReport(
        environment=environment,
        instance_id=instance_id,
        data_namespace=namespace,
        capabilities=capabilities,
        checks=collector.checks,
        blockers=blockers,
        warnings=warnings,
        safe=not blockers and environment in {"staging", "production"},
    )


def assert_environment_ready(
    environ: Optional[Mapping[str, str]] = None,
) -> EnvironmentPreflightReport:
    report = build_environment_preflight(environ)
    if not report.safe:
        blocker_codes = ", ".join(report.blockers) or "environment_not_deployable"
        raise RuntimeError(f"RTM environment preflight bloqueado: {blocker_codes}")
    return report
