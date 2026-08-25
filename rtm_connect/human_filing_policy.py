"""Politica fail-closed de RTM CONNECT A1-S.

La fase A1-S solo habilita un ensayo humano con fixtures sinteticos dentro de
un staging aislado. Aunque el flujo modele una presentacion regulada, el
backend no abre sedes, no usa red ni B2, no resuelve secretos, no selecciona
proveedores y no ejecuta ningun efecto juridico externo.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

from rtm_connect.authority import validate_execution_authority
from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    RiskClass,
)
from rtm_connect.human_filing_contracts import (
    HUMAN_FILING_AUTHORITY_CODE,
    HUMAN_FILING_AUTHORITY_VERSION,
    HUMAN_FILING_CAPABILITY,
    HUMAN_FILING_CONTRACT_VERSION,
    HUMAN_FILING_MARKER,
    HUMAN_FILING_SATELLITE,
    HUMAN_FILING_TARGET_REF,
    HUMAN_FILING_TARGET_TYPE,
)
from rtm_connect.assisted_legal_policy import (
    assert_c7_database_identity,
    assert_c7_staging_boundary,
)


RTM_CONNECT_A1S_POLICY_VERSION = "rtm_connect_a1s_human_filing_policy_v1_0"
HUMAN_FILING_FEATURE_FLAG = "RTM_ENABLE_CONNECT_A1S_HUMAN_FILING"
HUMAN_FILING_READ_PERMISSION = "connect.human_filing.read"
HUMAN_FILING_PREPARE_PERMISSION = "connect.human_filing.prepare"
HUMAN_FILING_ASSIGN_PERMISSION = "connect.human_filing.assign"
HUMAN_FILING_EXECUTE_PERMISSION = "connect.human_filing.execute"
HUMAN_FILING_RELEASE_PERMISSION = "connect.human_filing.release"
HUMAN_FILING_VERIFY_PERMISSION = "connect.human_filing.verify"
HUMAN_FILING_RECONCILE_PERMISSION = "connect.human_filing.reconcile"
HUMAN_FILING_SUPERVISE_PERMISSION = "connect.human_filing.supervise"
HUMAN_FILING_PERMISSIONS = (
    HUMAN_FILING_READ_PERMISSION,
    HUMAN_FILING_PREPARE_PERMISSION,
    HUMAN_FILING_ASSIGN_PERMISSION,
    HUMAN_FILING_EXECUTE_PERMISSION,
    HUMAN_FILING_RELEASE_PERMISSION,
    HUMAN_FILING_VERIFY_PERMISSION,
    HUMAN_FILING_RECONCILE_PERMISSION,
    HUMAN_FILING_SUPERVISE_PERMISSION,
)

_TRUE = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSE = frozenset({"0", "false", "no", "off", "disabled"})
_LOCAL_DISABLED_FLAGS = (
    "RTM_CONNECT_A1S_NETWORK_ALLOWED",
    "RTM_CONNECT_A1S_B2_ALLOWED",
    "RTM_CONNECT_A1S_PROVIDER_ALLOWED",
    "RTM_CONNECT_A1S_REAL_DATA_ALLOWED",
    "RTM_CONNECT_A1S_EXTERNAL_EFFECTS_ALLOWED",
    "RTM_ALLOW_REAL_CUSTOMER_DATA",
    "RTM_ENABLE_CONNECT_SUPERVISOR_V1",
    "RTM_ENABLE_CONNECT_C6_SANDBOX",
    "RTM_ENABLE_CONNECT_C7_ASSISTED",
    "RTM_ENABLE_CONNECT_C8_CONTROLLED_PRODUCTION",
    "RTM_ENABLE_CONNECT_C8_LIVE",
)
_FORBIDDEN_CONFIGURATION = (
    "RTM_CONNECT_A1S_ENDPOINT",
    "RTM_CONNECT_A1S_ORIGIN",
    "RTM_CONNECT_A1S_CREDENTIAL_REF",
    "RTM_CONNECT_A1S_PROVIDER",
    "RTM_CONNECT_A1S_PROVIDER_ID",
    "RTM_CONNECT_A1S_B2_BUCKET",
    "RTM_CONNECT_A1S_B2_KEY",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class HumanFilingPolicyError(RuntimeError):
    """La configuracion o autoridad A1-S incumple la frontera congelada."""


class HumanFilingRuntimeDisabled(HumanFilingPolicyError):
    """La fase A1-S permanece apagada por defecto."""


@dataclass(frozen=True)
class HumanFilingStagingBoundary:
    environment: str
    instance_id: str
    data_namespace: str
    database_name: str
    database_role: str
    side_effect_policy: str
    expected_branch: str
    synthetic_only: bool = True
    network_allowed: bool = False
    b2_allowed: bool = False
    provider_allowed: bool = False
    real_data_allowed: bool = False
    external_effects_allowed: bool = False


@dataclass(frozen=True)
class HumanFilingRuntimeConfiguration:
    enabled: bool
    boundary: HumanFilingStagingBoundary | None
    connector_environment: str = "staging"
    synthetic_only: bool = True
    network_allowed: bool = False
    b2_allowed: bool = False
    provider_allowed: bool = False
    credential_ref: None = None
    routes_default_off: bool = True


def _flag(values: Mapping[str, str], name: str) -> bool | None:
    raw = str(values.get(name) or "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def _uuid(value: str, field_name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HumanFilingPolicyError(f"{field_name} debe ser UUID") from exc


def _sha256(value: str, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise HumanFilingPolicyError(
            f"{field_name} debe ser SHA-256 hexadecimal"
        )
    return normalized


def assert_a1s_staging_boundary(
    values: Mapping[str, str] | None = None,
) -> HumanFilingStagingBoundary:
    """Exige la frontera C6 y desactiva explicitamente todo efecto A1-S."""

    env = values if values is not None else os.environ
    try:
        base = assert_c7_staging_boundary(env)
    except Exception as exc:
        raise HumanFilingPolicyError(str(exc)) from exc

    for name in _LOCAL_DISABLED_FLAGS:
        if _flag(env, name) is not False:
            raise HumanFilingPolicyError(f"{name}_must_be_false")
    for name in _FORBIDDEN_CONFIGURATION:
        if str(env.get(name) or "").strip():
            raise HumanFilingPolicyError(f"{name}_must_be_empty")

    return HumanFilingStagingBoundary(
        environment=base.environment,
        instance_id=base.instance_id,
        data_namespace=base.data_namespace,
        database_name=base.database_name,
        database_role=base.database_role,
        side_effect_policy=base.side_effect_policy,
        expected_branch=base.expected_branch,
    )


def assert_a1s_database_identity(
    connection,
    *,
    expected_database_name: str,
    expected_database_role: str,
) -> str:
    """Reutiliza la comprobacion exhaustiva de DB staging de C6."""

    try:
        return assert_c7_database_identity(
            connection,
            expected_database_name=expected_database_name,
            expected_database_role=expected_database_role,
        )
    except Exception as exc:
        raise HumanFilingPolicyError(str(exc)) from exc


def load_a1s_runtime_configuration(
    values: Mapping[str, str] | None = None,
    *,
    require_enabled: bool = False,
) -> HumanFilingRuntimeConfiguration:
    """Carga el flag default-off sin aceptar configuracion operativa externa."""

    env = values if values is not None else os.environ
    raw = str(env.get(HUMAN_FILING_FEATURE_FLAG) or "").strip().lower()
    if raw and raw not in _TRUE and raw not in _FALSE:
        raise HumanFilingPolicyError(
            f"{HUMAN_FILING_FEATURE_FLAG}_must_be_explicit_boolean"
        )
    enabled = raw in _TRUE
    if require_enabled and not enabled:
        raise HumanFilingRuntimeDisabled(
            f"{HUMAN_FILING_FEATURE_FLAG}_must_be_true"
        )
    boundary = assert_a1s_staging_boundary(env) if enabled else None
    if enabled:
        try:
            from rtm_core.operator_auth_request import (
                load_operator_auth_runtime_config,
            )

            load_operator_auth_runtime_config(env, require_enabled=True)
        except Exception as exc:
            raise HumanFilingPolicyError(
                "A1-S exige autenticacion individual V1 habilitada: "
                f"{exc}"
            ) from exc
    return HumanFilingRuntimeConfiguration(
        enabled=enabled,
        boundary=boundary,
    )


def expected_a1s_action_payload(
    *,
    case_binding_id: str,
    representation_evidence_id: str,
    case_snapshot_sha256: str,
) -> dict[str, object]:
    """Construye el unico payload CORE admisible en A1-S."""

    return {
        "contract_version": HUMAN_FILING_CONTRACT_VERSION,
        "case_binding_id": _uuid(case_binding_id, "case_binding_id"),
        "representation_evidence_id": _uuid(
            representation_evidence_id,
            "representation_evidence_id",
        ),
        "case_snapshot_sha256": _sha256(
            case_snapshot_sha256,
            "case_snapshot_sha256",
        ),
        "human_final_submit_required": True,
        "submission_channel": "synthetic_human_filing",
        "synthetic_marker": HUMAN_FILING_MARKER,
        "synthetic_only": True,
        "network_used": False,
        "b2_used": False,
        "provider_contacted": False,
        "external_effects_allowed": False,
    }


def validate_a1s_action_authority(
    action: ConnectActionRequest,
    grant: AuthorizationGrant,
) -> None:
    """Valida tupla, payload y autoridad congelada antes de cualquier DML."""

    if type(action) is not ConnectActionRequest:
        raise HumanFilingPolicyError("A1-S exige ConnectActionRequest exacto")
    if type(grant) is not AuthorizationGrant:
        raise HumanFilingPolicyError("A1-S exige AuthorizationGrant exacto")
    if action.capability != HUMAN_FILING_CAPABILITY:
        raise HumanFilingPolicyError("Capacidad A1-S no permitida")
    if action.satellite != HUMAN_FILING_SATELLITE:
        raise HumanFilingPolicyError("Satelite A1-S no permitido")
    if action.target_type != HUMAN_FILING_TARGET_TYPE:
        raise HumanFilingPolicyError("Target type A1-S no permitido")
    if action.target_ref != HUMAN_FILING_TARGET_REF:
        raise HumanFilingPolicyError("Target ref A1-S debe ser sintetico")
    if action.risk_class is not RiskClass.R4_CRITICAL_REGULATED:
        raise HumanFilingPolicyError("A1-S exige riesgo exactamente R4")
    if not action.requires_dual_control:
        raise HumanFilingPolicyError("A1-S exige doble control CORE")
    if action.case_id is None or action.correlation_id is None:
        raise HumanFilingPolicyError(
            "A1-S exige expediente y correlacion sinteticos"
        )
    if not 1 <= len(action.document_hashes) <= 8:
        raise HumanFilingPolicyError("A1-S exige entre 1 y 8 documentos")

    payload = dict(action.payload)
    expected = expected_a1s_action_payload(
        case_binding_id=str(payload.get("case_binding_id") or ""),
        representation_evidence_id=str(
            payload.get("representation_evidence_id") or ""
        ),
        case_snapshot_sha256=str(payload.get("case_snapshot_sha256") or ""),
    )
    if payload != expected:
        raise HumanFilingPolicyError("Payload A1-S fuera de allowlist")

    if grant.action_id != action.action_id:
        raise HumanFilingPolicyError("Grant A1-S no pertenece a la accion")
    if (
        grant.authority_code != HUMAN_FILING_AUTHORITY_CODE
        or grant.authority_version != HUMAN_FILING_AUTHORITY_VERSION
    ):
        raise HumanFilingPolicyError(
            "A1-S exige emisor y version CORE congelados"
        )
    if grant.required_evidence_level is not EvidenceLevel.E4_RECEIPT_VERIFIED:
        raise HumanFilingPolicyError("A1-S exige evidencia E4 exacta")
    if grant.authorized_connector_modes != (ConnectorMode.ASSISTED,):
        raise HumanFilingPolicyError("A1-S autoriza solo modo assisted")
    if not grant.legal_effect_authorized:
        raise HumanFilingPolicyError(
            "CORE debe autorizar el alcance regulado aunque A1-S no lo ejecute"
        )
    if len(set(grant.approved_by_operator_ids)) < 2:
        raise HumanFilingPolicyError("A1-S exige dos aprobadores CORE distintos")
    if action.requested_by_operator_id in grant.approved_by_operator_ids:
        raise HumanFilingPolicyError(
            "Solicitante y aprobadores CORE deben estar separados"
        )
    authorized_at = datetime.fromisoformat(
        grant.authorized_at.replace("Z", "+00:00")
    )
    if authorized_at > datetime.now(timezone.utc):
        raise HumanFilingPolicyError(
            "A1-S no admite autorizaciones fechadas en el futuro"
        )
    try:
        validate_execution_authority(
            action,
            grant,
            connector_mode=ConnectorMode.ASSISTED,
        )
    except Exception as exc:
        raise HumanFilingPolicyError(str(exc)) from exc


__all__ = [
    "RTM_CONNECT_A1S_POLICY_VERSION",
    "HUMAN_FILING_ASSIGN_PERMISSION",
    "HUMAN_FILING_EXECUTE_PERMISSION",
    "HUMAN_FILING_FEATURE_FLAG",
    "HUMAN_FILING_PERMISSIONS",
    "HUMAN_FILING_PREPARE_PERMISSION",
    "HUMAN_FILING_READ_PERMISSION",
    "HUMAN_FILING_RECONCILE_PERMISSION",
    "HUMAN_FILING_RELEASE_PERMISSION",
    "HUMAN_FILING_SUPERVISE_PERMISSION",
    "HUMAN_FILING_VERIFY_PERMISSION",
    "HumanFilingPolicyError",
    "HumanFilingRuntimeConfiguration",
    "HumanFilingRuntimeDisabled",
    "HumanFilingStagingBoundary",
    "assert_a1s_database_identity",
    "assert_a1s_staging_boundary",
    "expected_a1s_action_payload",
    "load_a1s_runtime_configuration",
    "validate_a1s_action_authority",
]
