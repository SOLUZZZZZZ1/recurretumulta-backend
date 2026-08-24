"""Política fail-closed del conector jurídico asistido C7.

C7 no selecciona una Administración ni abre una sede. Modela el límite entre
la asistencia de RTM y el acto final que debe realizar una persona. Todo el
alcance ejecutable en staging es sintético, sin red y sin datos de clientes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    RiskClass,
)
from rtm_connect.provider_sandbox_policy import assert_c6_staging_boundary


RTM_CONNECT_C7_ASSISTED_POLICY_VERSION = (
    "rtm_connect_c7_assisted_legal_policy_v1_0"
)
ASSISTED_LEGAL_CODE = "assisted.legal"
ASSISTED_LEGAL_CONNECTOR_VERSION = "v1.0"
ASSISTED_LEGAL_CAPABILITY = "administration.submit.legal.assisted"
ASSISTED_LEGAL_SATELLITE = "rtm.legal.assisted"
ASSISTED_LEGAL_TARGET_TYPE = "administration.synthetic.filing"
ASSISTED_LEGAL_TARGET_REF = "synthetic-c7-administration"
ASSISTED_LEGAL_MARKER = "RTM_C7_SYNTHETIC_ONLY"
ASSISTED_LEGAL_CONTRACT_VERSION = "rtm.c7.assisted_legal.v1"
ASSISTED_LEGAL_AUTHORITY_CODE = "rtm.core.authorization"
ASSISTED_LEGAL_AUTHORITY_VERSION = "rtm_core_authority_v1"
ASSISTED_LEGAL_HUMAN_GATE_PHRASE = "HUMAN_FINAL_SUBMIT_REQUIRED"

_EXPECTED_PAYLOAD = {
    "human_final_submit_required": True,
    "procedure": "synthetic_administrative_filing",
    "submission_channel": "synthetic_portal",
    "synthetic_marker": ASSISTED_LEGAL_MARKER,
}
_TRUE = {"1", "true", "yes", "on", "enabled"}
_FALSE = {"0", "false", "no", "off", "disabled"}


class AssistedLegalPolicyError(RuntimeError):
    pass


class AssistedLegalRuntimeDisabled(AssistedLegalPolicyError):
    pass


@dataclass(frozen=True)
class AssistedLegalStagingBoundary:
    environment: str
    instance_id: str
    data_namespace: str
    database_name: str
    database_role: str
    side_effect_policy: str
    expected_branch: str


def expected_c7_payload() -> dict[str, object]:
    """Devuelve una copia del único payload permitido por C7 v1."""

    return dict(_EXPECTED_PAYLOAD)


def assert_c7_staging_boundary(
    values: Mapping[str, str] | None = None,
) -> AssistedLegalStagingBoundary:
    """Reutiliza y vuelve a nombrar la frontera exhaustiva de C6."""

    try:
        boundary = assert_c6_staging_boundary(values)
    except Exception as exc:
        raise AssistedLegalPolicyError(str(exc)) from exc
    return AssistedLegalStagingBoundary(
        environment=boundary.environment,
        instance_id=boundary.instance_id,
        data_namespace=boundary.data_namespace,
        database_name=boundary.database_name,
        database_role=boundary.database_role,
        side_effect_policy=boundary.side_effect_policy,
        expected_branch=boundary.expected_branch,
    )


def assert_c7_database_identity(
    connection,
    *,
    expected_database_name: str,
    expected_database_role: str,
) -> dict[str, str]:
    """Delega en el guard C6, que ya congela identidad y ``search_path``."""

    from rtm_connect.provider_sandbox_policy import assert_c6_database_identity

    return assert_c6_database_identity(
        connection,
        expected_database_name=expected_database_name,
        expected_database_role=expected_database_role,
    )


def load_c7_runtime_configuration(
    values: Mapping[str, str] | None = None,
    *,
    require_enabled: bool = False,
) -> None:
    """C7 v1 no tiene runtime público ni una sede real configurable."""

    env = values if values is not None else os.environ
    assert_c7_staging_boundary(env)
    raw_enabled = str(
        env.get("RTM_ENABLE_CONNECT_C7_ASSISTED") or ""
    ).strip().lower()
    if raw_enabled in _TRUE:
        raise AssistedLegalRuntimeDisabled(
            "C7 v1 no publica ejecución jurídica asistida"
        )
    if raw_enabled and raw_enabled not in _FALSE:
        raise AssistedLegalPolicyError(
            "RTM_ENABLE_CONNECT_C7_ASSISTED_must_be_explicit_boolean"
        )
    if require_enabled:
        raise AssistedLegalRuntimeDisabled(
            "RTM_ENABLE_CONNECT_C7_ASSISTED_must_remain_false"
        )
    return None


def validate_c7_action_authority(
    action: ConnectActionRequest,
    grant: AuthorizationGrant,
) -> None:
    """Valida la tupla C7 completa antes de cualquier DML."""

    if action.capability != ASSISTED_LEGAL_CAPABILITY:
        raise AssistedLegalPolicyError("Capacidad C7 no permitida")
    if action.satellite != ASSISTED_LEGAL_SATELLITE:
        raise AssistedLegalPolicyError("Satélite C7 no permitido")
    if action.target_type != ASSISTED_LEGAL_TARGET_TYPE:
        raise AssistedLegalPolicyError("Target C7 no permitido")
    if action.target_ref != ASSISTED_LEGAL_TARGET_REF:
        raise AssistedLegalPolicyError("Target ref C7 debe ser sintético")
    if action.risk_class is not RiskClass.R4_CRITICAL_REGULATED:
        raise AssistedLegalPolicyError("C7 exige riesgo exactamente R4")
    if not action.requires_dual_control:
        raise AssistedLegalPolicyError("C7 exige doble control")
    if action.case_id is not None or action.correlation_id is not None:
        raise AssistedLegalPolicyError(
            "C7 staging no admite expediente ni correlación real"
        )
    if not 1 <= len(action.document_hashes) <= 8:
        raise AssistedLegalPolicyError(
            "C7 exige entre uno y ocho documentos sintéticos"
        )
    if dict(action.payload) != _EXPECTED_PAYLOAD:
        raise AssistedLegalPolicyError("Payload C7 fuera de allowlist")
    if grant.action_id != action.action_id:
        raise AssistedLegalPolicyError("Grant C7 no pertenece a la acción")
    if (
        grant.authority_code != ASSISTED_LEGAL_AUTHORITY_CODE
        or grant.authority_version != ASSISTED_LEGAL_AUTHORITY_VERSION
    ):
        raise AssistedLegalPolicyError(
            "C7 exige emisor y versión CORE congelados"
        )
    if grant.required_evidence_level is not EvidenceLevel.E4_RECEIPT_VERIFIED:
        raise AssistedLegalPolicyError("C7 exige evidencia E4 exacta")
    if grant.authorized_connector_modes != (ConnectorMode.ASSISTED,):
        raise AssistedLegalPolicyError("C7 autoriza solo modo assisted")
    if not grant.legal_effect_authorized:
        raise AssistedLegalPolicyError(
            "C7 exige autorización expresa de efecto legal"
        )
    if len(set(grant.approved_by_operator_ids)) < 2:
        raise AssistedLegalPolicyError(
            "C7 exige dos aprobadores distintos"
        )
    if action.requested_by_operator_id in grant.approved_by_operator_ids:
        raise AssistedLegalPolicyError(
            "El solicitante C7 debe ser distinto de los aprobadores"
        )
    authorized_at = datetime.fromisoformat(
        grant.authorized_at.replace("Z", "+00:00")
    )
    if authorized_at > datetime.now(timezone.utc):
        raise AssistedLegalPolicyError(
            "C7 no admite autorizaciones fechadas en el futuro"
        )


__all__ = [
    "RTM_CONNECT_C7_ASSISTED_POLICY_VERSION",
    "ASSISTED_LEGAL_AUTHORITY_CODE",
    "ASSISTED_LEGAL_AUTHORITY_VERSION",
    "ASSISTED_LEGAL_CAPABILITY",
    "ASSISTED_LEGAL_CODE",
    "ASSISTED_LEGAL_CONNECTOR_VERSION",
    "ASSISTED_LEGAL_CONTRACT_VERSION",
    "ASSISTED_LEGAL_HUMAN_GATE_PHRASE",
    "ASSISTED_LEGAL_MARKER",
    "ASSISTED_LEGAL_SATELLITE",
    "ASSISTED_LEGAL_TARGET_REF",
    "ASSISTED_LEGAL_TARGET_TYPE",
    "AssistedLegalPolicyError",
    "AssistedLegalRuntimeDisabled",
    "AssistedLegalStagingBoundary",
    "assert_c7_database_identity",
    "assert_c7_staging_boundary",
    "expected_c7_payload",
    "load_c7_runtime_configuration",
    "validate_c7_action_authority",
]
