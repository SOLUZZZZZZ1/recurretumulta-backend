"""Política fail-closed del proveedor sandbox controlado de C6.

C6 no selecciona ni suplanta un proveedor público. El único origen externo
compilado usa el TLD reservado ``.invalid``; por tanto el runtime real sigue
deliberadamente no disponible hasta que otra versión y otro ADR congelen un
proveedor concreto. El smoke usa una fábrica loopback que no se carga desde el
entorno ni desde datos de una acción.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping
from urllib.parse import unquote, urlparse, urlsplit

from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    RiskClass,
)
from rtm_core.environment_contract import assert_environment_ready


RTM_CONNECT_C6_PROVIDER_POLICY_VERSION = (
    "rtm_connect_c6_provider_policy_v1_0"
)
CONTROLLED_SANDBOX_CODE = "controlled.sandbox"
CONTROLLED_SANDBOX_CONNECTOR_VERSION = "v1.0"
CONTROLLED_SANDBOX_CAPABILITY = "sandbox.http.probe"
CONTROLLED_SANDBOX_SATELLITE = "rtm.connect.sandbox"
CONTROLLED_SANDBOX_TARGET_TYPE = "sandbox.probe"
CONTROLLED_SANDBOX_MARKER = "RTM_C6_SYNTHETIC_ONLY"
CONTROLLED_SANDBOX_CONTRACT_VERSION = "rtm.c6.controlled_sandbox.probe.v1"
CONTROLLED_SANDBOX_ORIGIN = "https://c6-reference-provider.invalid"
CONTROLLED_SANDBOX_CREDENTIAL_REF = (
    "env://RTM_CONNECT_C6_SANDBOX_TOKEN"
)
CONTROLLED_SANDBOX_AUTHORITY_CODE = "rtm.core.authorization"
CONTROLLED_SANDBOX_AUTHORITY_VERSION = "rtm_core_authority_v1"

_TRUE = {"1", "true", "yes", "on", "enabled"}
_FALSE = {"0", "false", "no", "off", "disabled"}
_GLOBAL_CAPABILITIES = (
    "RTM_ENABLE_B2",
    "RTM_ENABLE_DOCUMENT_PROVIDER",
    "RTM_ENABLE_EXTERNAL_SUBMISSION",
    "RTM_ENABLE_OUTBOUND_EMAIL",
    "RTM_ENABLE_STRIPE",
    "RTM_ENABLE_FINAL_PAYMENTS",
)


class ProviderSandboxPolicyError(RuntimeError):
    pass


class ProviderSandboxRuntimeDisabled(ProviderSandboxPolicyError):
    pass


@dataclass(frozen=True)
class ProviderSandboxStagingBoundary:
    environment: str
    instance_id: str
    data_namespace: str
    database_name: str
    database_role: str
    side_effect_policy: str
    expected_branch: str


def _flag(values: Mapping[str, str], name: str) -> bool | None:
    raw = str(values.get(name) or "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def assert_c6_staging_boundary(
    values: Mapping[str, str] | None = None,
) -> ProviderSandboxStagingBoundary:
    env = values if values is not None else os.environ
    try:
        central = assert_environment_ready(env)
    except RuntimeError as exc:
        raise ProviderSandboxPolicyError(str(exc)) from exc
    environment = str(env.get("RTM_ENV") or "").strip().lower()
    instance_id = str(env.get("RTM_INSTANCE_ID") or "").strip().lower()
    namespace = str(env.get("RTM_DATA_NAMESPACE") or "").strip().lower()
    side_effect_policy = str(
        env.get("RTM_SIDE_EFFECT_POLICY") or ""
    ).strip().lower()
    confirmation = str(
        env.get("RTM_ENVIRONMENT_CONFIRMATION") or ""
    ).strip()
    parsed_database = urlparse(str(env.get("DATABASE_URL") or "").strip())
    database_name = unquote(parsed_database.path.lstrip("/")).split("/", 1)[0]
    database_name = database_name.strip().lower()
    database_role = unquote(parsed_database.username or "").strip()
    document_policy = str(
        env.get("RTM_DOCUMENT_INPUT_POLICY") or ""
    ).strip().lower()
    expected_branch = str(env.get("RTM_EXPECTED_BRANCH") or "").strip()
    runtime_branch = str(
        env.get("RENDER_GIT_BRANCH") or env.get("GIT_BRANCH") or ""
    ).strip()
    forbidden = ("production", "prod", "live")

    if central.environment != "staging" or environment != "staging":
        raise ProviderSandboxPolicyError("RTM_ENV_must_be_staging")
    if (
        "staging" not in instance_id
        or any(marker in instance_id for marker in forbidden)
    ):
        raise ProviderSandboxPolicyError("RTM_INSTANCE_ID_must_identify_staging")
    if (
        "staging" not in namespace
        or any(marker in namespace for marker in forbidden)
    ):
        raise ProviderSandboxPolicyError(
            "RTM_DATA_NAMESPACE_must_identify_staging"
        )
    if confirmation != "RTM_STAGING_ISOLATED":
        raise ProviderSandboxPolicyError(
            "RTM_ENVIRONMENT_CONFIRMATION_must_confirm_isolated_staging"
        )
    if not parsed_database.scheme.startswith("postgresql"):
        raise ProviderSandboxPolicyError("DATABASE_URL_must_be_postgresql")
    if (
        "staging" not in database_name
        or any(marker in database_name for marker in forbidden)
    ):
        raise ProviderSandboxPolicyError(
            "DATABASE_URL_must_identify_staging_database"
        )
    if not database_role:
        raise ProviderSandboxPolicyError("DATABASE_URL_must_identify_role")
    if side_effect_policy != "isolated":
        raise ProviderSandboxPolicyError(
            "RTM_SIDE_EFFECT_POLICY_must_be_isolated"
        )
    if _flag(env, "RTM_ALLOW_REAL_CUSTOMER_DATA") is not False:
        raise ProviderSandboxPolicyError(
            "RTM_ALLOW_REAL_CUSTOMER_DATA_must_be_false"
        )
    for name in _GLOBAL_CAPABILITIES:
        if _flag(env, name) is not False:
            raise ProviderSandboxPolicyError(f"{name}_must_be_false")
    if any(bool(value) for value in central.capabilities.values()):
        raise ProviderSandboxPolicyError(
            "all_global_external_capabilities_must_be_disabled"
        )
    if document_policy != "synthetic_only":
        raise ProviderSandboxPolicyError(
            "RTM_DOCUMENT_INPUT_POLICY_must_be_synthetic_only"
        )
    if (
        not expected_branch
        or expected_branch.lower() in {"main", "master"}
    ):
        raise ProviderSandboxPolicyError(
            "RTM_EXPECTED_BRANCH_must_be_explicit_staging_branch"
        )
    if not runtime_branch or runtime_branch != expected_branch:
        raise ProviderSandboxPolicyError(
            "runtime_branch_must_match_RTM_EXPECTED_BRANCH"
        )
    return ProviderSandboxStagingBoundary(
        environment=environment,
        instance_id=instance_id,
        data_namespace=namespace,
        database_name=database_name,
        database_role=database_role,
        side_effect_policy=side_effect_policy,
        expected_branch=expected_branch,
    )


def assert_c6_database_identity(
    connection,
    *,
    expected_database_name: str,
    expected_database_role: str,
) -> str:
    row = connection.exec_driver_sql(
        """
        SELECT current_database() AS database_name,
               current_user AS current_role,
               session_user AS session_role,
               current_schemas(FALSE) AS explicit_schemas,
               current_schemas(TRUE) AS effective_schemas,
               pg_my_temp_schema() AS temp_schema_oid
        """
    ).mappings().one()
    actual = str(row["database_name"] or "").strip().lower()
    expected = str(expected_database_name or "").strip().lower()
    expected_role = str(expected_database_role or "").strip()
    current_role = str(row["current_role"] or "").strip()
    session_role = str(row["session_role"] or "").strip()
    explicit_schemas = tuple(str(value) for value in row["explicit_schemas"])
    effective_schemas = tuple(str(value) for value in row["effective_schemas"])
    if (
        not expected
        or actual != expected
        or "staging" not in actual
        or any(marker in actual for marker in ("production", "prod", "live"))
        or not expected_role
        or current_role != expected_role
        or session_role != expected_role
        or explicit_schemas != ("public",)
        or effective_schemas != ("pg_catalog", "public")
        or int(row["temp_schema_oid"] or 0) != 0
    ):
        raise ProviderSandboxPolicyError(
            "La conexión C6 no corresponde a la base staging declarada"
        )
    return actual


@dataclass(frozen=True)
class ProviderSandboxEndpoint:
    origin: str
    credential_ref: str
    loopback_test_only: bool = False

    def __post_init__(self) -> None:
        parsed = urlsplit(str(self.origin or ""))
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ProviderSandboxPolicyError("Origen sandbox no válido")
        if parsed.path not in {"", "/"}:
            raise ProviderSandboxPolicyError("El origen no admite path")
        host = (parsed.hostname or "").lower()
        if self.loopback_test_only:
            if parsed.scheme != "http" or host not in {
                "127.0.0.1", "::1"
            }:
                raise ProviderSandboxPolicyError(
                    "El endpoint de test debe usar un IP loopback literal"
                )
            if parsed.port is None:
                raise ProviderSandboxPolicyError(
                    "El endpoint loopback exige puerto efímero"
                )
            if not 32768 <= parsed.port <= 65535:
                raise ProviderSandboxPolicyError(
                    "El endpoint loopback exige puerto dinámico alto"
                )
        else:
            if self.origin.rstrip("/") != CONTROLLED_SANDBOX_ORIGIN:
                raise ProviderSandboxPolicyError(
                    "Origen externo no congelado en C6"
                )
            if parsed.scheme != "https" or parsed.port not in {None, 443}:
                raise ProviderSandboxPolicyError("C6 externo exige HTTPS:443")
        if self.credential_ref != CONTROLLED_SANDBOX_CREDENTIAL_REF:
            raise ProviderSandboxPolicyError(
                "Referencia de credencial no congelada en C6"
            )

    @classmethod
    def loopback_for_smoke(cls, origin: str) -> "ProviderSandboxEndpoint":
        return cls(
            origin=str(origin).rstrip("/"),
            credential_ref=CONTROLLED_SANDBOX_CREDENTIAL_REF,
            loopback_test_only=True,
        )

    def assert_network_target(self, *, timeout_seconds: float = 3.0) -> None:
        """Revalida host e IP inmediatamente antes de cada llamada."""

        if not self.loopback_test_only:
            # C6 v1 no congela un proveedor real. Ni DNS dividido ni una
            # entrada hosts pueden convertir el dominio .invalid en salida.
            raise ProviderSandboxRuntimeDisabled(
                "C6 v1 solo permite el transporte loopback del smoke"
            )
        parsed = urlsplit(self.origin)
        host = str(parsed.hostname)
        timeout = float(timeout_seconds)
        if not 0 < timeout <= 10.0:
            raise ProviderSandboxPolicyError("Timeout DNS C6 no válido")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise ProviderSandboxPolicyError(
                "C6 v1 no permite resolución DNS"
            ) from None
        if not address.is_loopback:
            raise ProviderSandboxPolicyError(
                "El smoke intentó salir de loopback"
            )


def load_c6_runtime_endpoint(
    values: Mapping[str, str] | None = None,
    *,
    require_enabled: bool = False,
) -> ProviderSandboxEndpoint | None:
    env = values if values is not None else os.environ
    assert_c6_staging_boundary(env)
    enabled = _flag(env, "RTM_ENABLE_CONNECT_C6_SANDBOX") is True
    if not enabled:
        if require_enabled:
            raise ProviderSandboxRuntimeDisabled(
                "RTM_ENABLE_CONNECT_C6_SANDBOX_must_be_true"
            )
        return None
    origin = str(env.get("RTM_CONNECT_C6_SANDBOX_ORIGIN") or "").rstrip("/")
    credential_ref = str(
        env.get("RTM_CONNECT_C6_SANDBOX_CREDENTIAL_REF") or ""
    ).strip()
    return ProviderSandboxEndpoint(
        origin=origin,
        credential_ref=credential_ref,
        loopback_test_only=False,
    )


def validate_c6_probe_authority(
    action: ConnectActionRequest,
    grant: AuthorizationGrant,
) -> None:
    if action.capability != CONTROLLED_SANDBOX_CAPABILITY:
        raise ProviderSandboxPolicyError("Capacidad C6 no permitida")
    if action.satellite != CONTROLLED_SANDBOX_SATELLITE:
        raise ProviderSandboxPolicyError("Satélite C6 no permitido")
    if action.target_type != CONTROLLED_SANDBOX_TARGET_TYPE:
        raise ProviderSandboxPolicyError("Target C6 no permitido")
    if action.target_ref != "synthetic-probe":
        raise ProviderSandboxPolicyError("Target ref C6 debe ser sintético")
    if action.risk_class is not RiskClass.R1_LOW_REVERSIBLE:
        raise ProviderSandboxPolicyError("C6 exige riesgo exactamente R1")
    if (
        action.case_id is not None
        or action.document_hashes
        or action.correlation_id is not None
    ):
        raise ProviderSandboxPolicyError(
            "C6 no admite expediente, documentos ni correlación libre"
        )
    if action.requires_dual_control:
        raise ProviderSandboxPolicyError("C6 no usa doble control")
    if dict(action.payload) != {"synthetic_marker": CONTROLLED_SANDBOX_MARKER}:
        raise ProviderSandboxPolicyError("Payload C6 fuera de allowlist")
    if grant.action_id != action.action_id:
        raise ProviderSandboxPolicyError("Grant C6 no pertenece a la acción")
    if (
        grant.authority_code != CONTROLLED_SANDBOX_AUTHORITY_CODE
        or grant.authority_version != CONTROLLED_SANDBOX_AUTHORITY_VERSION
    ):
        raise ProviderSandboxPolicyError(
            "C6 exige emisor y versión CORE congelados"
        )
    authorized_at = datetime.fromisoformat(
        grant.authorized_at.replace("Z", "+00:00")
    )
    if authorized_at > datetime.now(timezone.utc):
        raise ProviderSandboxPolicyError(
            "C6 no admite autorizaciones fechadas en el futuro"
        )
    if grant.legal_effect_authorized:
        raise ProviderSandboxPolicyError("C6 no admite efecto legal")
    if grant.required_evidence_level is not EvidenceLevel.E2_EXTERNAL_REFERENCE:
        raise ProviderSandboxPolicyError("C6 exige evidencia E2 exacta")
    if grant.authorized_connector_modes != (ConnectorMode.API,):
        raise ProviderSandboxPolicyError("C6 autoriza solo modo API")


__all__ = [
    "RTM_CONNECT_C6_PROVIDER_POLICY_VERSION",
    "CONTROLLED_SANDBOX_CAPABILITY",
    "CONTROLLED_SANDBOX_AUTHORITY_CODE",
    "CONTROLLED_SANDBOX_AUTHORITY_VERSION",
    "CONTROLLED_SANDBOX_CODE",
    "CONTROLLED_SANDBOX_CONNECTOR_VERSION",
    "CONTROLLED_SANDBOX_CONTRACT_VERSION",
    "CONTROLLED_SANDBOX_CREDENTIAL_REF",
    "CONTROLLED_SANDBOX_MARKER",
    "CONTROLLED_SANDBOX_ORIGIN",
    "CONTROLLED_SANDBOX_SATELLITE",
    "CONTROLLED_SANDBOX_TARGET_TYPE",
    "ProviderSandboxEndpoint",
    "ProviderSandboxPolicyError",
    "ProviderSandboxRuntimeDisabled",
    "ProviderSandboxStagingBoundary",
    "assert_c6_staging_boundary",
    "assert_c6_database_identity",
    "load_c6_runtime_endpoint",
    "validate_c6_probe_authority",
]
