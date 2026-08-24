"""Política fail-closed del plano inerte RTM CONNECT C8.

La política solo admite evaluar candidatos sintéticos en staging.  No existe
una configuración capaz de habilitar producción real en C8 v1.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, NoReturn

from rtm_connect.assisted_legal_policy import (
    assert_c7_database_identity,
    assert_c7_staging_boundary,
)
from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    RiskClass,
)
from rtm_connect.idempotency import derive_idempotency_key, payload_sha256
from rtm_connect.production_contracts import (
    ProductionApprovalRole,
    ProductionAdmissionAssessment,
    ProductionAdmissionCandidate,
    ProductionReleaseApproval,
    candidate_sha256,
    expected_c8_admission_payload,
)


RTM_CONNECT_C8_PRODUCTION_POLICY_VERSION = (
    "rtm_connect_c8_production_policy_v1_0"
)
C8_ADMISSION_CAPABILITY = "connect.production.admission.simulate"
C8_ADMISSION_SATELLITE = "rtm.connect.production.admission"
C8_ADMISSION_TARGET_TYPE = "production.admission.candidate"
C8_ADMISSION_TARGET_REF = "synthetic-c8-admission"
C8_ADMISSION_AUTHORITY_CODE = "rtm.core.authorization"
C8_ADMISSION_AUTHORITY_VERSION = "rtm_core_authority_v1"
C8_ADMISSION_MODE = ConnectorMode.ASSISTED

_TRUE = {"1", "true", "yes", "on", "enabled"}
_FALSE = {"0", "false", "no", "off", "disabled", ""}

_C8_INERT_FLAGS = (
    "RTM_ENABLE_CONNECT_C8_CONTROLLED_PRODUCTION",
    "RTM_ENABLE_CONNECT_C8_LIVE",
    "RTM_ALLOW_CONNECT_C8_LIVE_ACTIVATION",
    "RTM_ALLOW_CONNECT_C8_EXTERNAL_EFFECTS",
    "RTM_CONNECT_C8_DISPATCH_ENABLED",
    "RTM_ENABLE_CONNECT_C6_SANDBOX",
    "RTM_ENABLE_CONNECT_C7_ASSISTED",
)

_C8_DORMANT_LIVE_VARIABLES = (
    "RTM_CONNECT_C8_PROVIDER_ORIGIN",
    "RTM_CONNECT_C8_PROVIDER_ENDPOINT",
    "RTM_CONNECT_C8_PROVIDER_URL",
    "RTM_CONNECT_C8_PROVIDER_TENANT",
    "RTM_CONNECT_C8_CREDENTIAL_REF",
    "RTM_CONNECT_C8_PROVIDER_TOKEN",
    "RTM_CONNECT_C8_CLIENT_SECRET",
    "RTM_CONNECT_C8_PRIVATE_KEY",
    "RTM_CONNECT_C8_EGRESS_PROXY",
    "RTM_CONNECT_C8_RELEASE_TOKEN",
    "RTM_CONNECT_C8_LIVE_ACTIVATION",
)

_NO_GO_BLOCKERS = (
    "provider_specific_pack_missing",
    "production_transport_absent",
    "live_activation_unavailable",
    "external_effects_forbidden",
)


class ProductionPolicyError(RuntimeError):
    pass


class ProductionRuntimeDisabled(ProductionPolicyError):
    pass


class ProductionLiveActivationUnavailable(ProductionPolicyError):
    pass


@dataclass(frozen=True)
class ProductionAdmissionStagingBoundary:
    environment: str
    instance_id: str
    data_namespace: str
    database_name: str
    database_role: str
    side_effect_policy: str
    expected_branch: str
    simulation_only: bool = True
    external_effects_allowed: bool = False
    live_activation_allowed: bool = False


def _source(values: Mapping[str, str] | None) -> Mapping[str, str]:
    return values if values is not None else os.environ


def _assert_flag_false(values: Mapping[str, str], name: str) -> None:
    raw = str(values.get(name) or "").strip().lower()
    if raw in _TRUE:
        raise ProductionRuntimeDisabled(f"{name}_must_remain_false")
    if raw not in _FALSE:
        raise ProductionPolicyError(f"{name}_must_be_explicit_boolean")


def _as_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProductionPolicyError("Timestamp C8 no válido") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset().total_seconds() != 0
    ):
        raise ProductionPolicyError("Timestamp C8 debe estar en UTC")
    return parsed


def assert_c8_staging_boundary(
    values: Mapping[str, str] | None = None,
) -> ProductionAdmissionStagingBoundary:
    """Reutiliza C7/C6 y añade ausencia total de configuración live C8."""

    source = _source(values)
    try:
        boundary = assert_c7_staging_boundary(source)
    except Exception as exc:
        raise ProductionPolicyError(str(exc)) from exc
    for name in _C8_INERT_FLAGS:
        _assert_flag_false(source, name)
    present = tuple(
        name
        for name in _C8_DORMANT_LIVE_VARIABLES
        if str(source.get(name) or "").strip()
    )
    if present:
        raise ProductionRuntimeDisabled(
            "C8_dormant_live_configuration_forbidden:" + ",".join(present)
        )
    return ProductionAdmissionStagingBoundary(
        environment=boundary.environment,
        instance_id=boundary.instance_id,
        data_namespace=boundary.data_namespace,
        database_name=boundary.database_name,
        database_role=boundary.database_role,
        side_effect_policy=boundary.side_effect_policy,
        expected_branch=boundary.expected_branch,
    )


def assert_c8_database_identity(
    connection,
    *,
    expected_database_name: str,
    expected_database_role: str,
):
    """Mantiene el guard de identidad/search_path ya congelado en C6/C7."""

    try:
        return assert_c7_database_identity(
            connection,
            expected_database_name=expected_database_name,
            expected_database_role=expected_database_role,
        )
    except Exception as exc:
        raise ProductionPolicyError(str(exc)) from exc


def load_c8_runtime_configuration(
    values: Mapping[str, str] | None = None,
    *,
    require_enabled: bool = False,
) -> None:
    """C8 v1 no devuelve endpoint, secreto, transporte ni runtime live."""

    assert_c8_staging_boundary(values)
    if require_enabled:
        raise ProductionRuntimeDisabled(
            "RTM_ENABLE_CONNECT_C8_CONTROLLED_PRODUCTION_must_remain_false"
        )
    return None


def validate_c8_admission_authority(
    action: ConnectActionRequest,
    grant: AuthorizationGrant,
    *,
    candidate: ProductionAdmissionCandidate | None = None,
    now: datetime | None = None,
) -> None:
    """Valida autoridad para simular admisión, nunca para ejecutar."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ProductionPolicyError("now debe incluir zona horaria")
    current = current.astimezone(timezone.utc)

    if action.capability != C8_ADMISSION_CAPABILITY:
        raise ProductionPolicyError("Capacidad C8 no permitida")
    if action.satellite != C8_ADMISSION_SATELLITE:
        raise ProductionPolicyError("Satélite C8 no permitido")
    if action.target_type != C8_ADMISSION_TARGET_TYPE:
        raise ProductionPolicyError("Target C8 no permitido")
    if action.target_ref != C8_ADMISSION_TARGET_REF:
        raise ProductionPolicyError("Target ref C8 debe ser sintético")
    if action.risk_class is not RiskClass.R4_CRITICAL_REGULATED:
        raise ProductionPolicyError("La admisión C8 exige riesgo exactamente R4")
    if not action.requires_dual_control:
        raise ProductionPolicyError("La admisión C8 exige doble control")
    if (
        action.case_id is not None
        or action.correlation_id is not None
        or action.document_hashes
    ):
        raise ProductionPolicyError(
            "C8 no admite expediente, correlación ni documentos reales"
        )

    supplied_payload = dict(action.payload)
    supplied_digest = supplied_payload.get("candidate_sha256")
    try:
        expected_payload = expected_c8_admission_payload(str(supplied_digest or ""))
    except ValueError as exc:
        raise ProductionPolicyError(str(exc)) from exc
    if supplied_payload != expected_payload:
        raise ProductionPolicyError("Payload C8 fuera de la allowlist sintética")
    if candidate is not None:
        if type(candidate) is not ProductionAdmissionCandidate:
            raise ProductionPolicyError("Candidato C8 no sellado")
        if supplied_digest != candidate_sha256(candidate):
            raise ProductionPolicyError("Payload C8 no pertenece al candidato")
        if candidate.requested_by_operator_id != action.requested_by_operator_id:
            raise ProductionPolicyError("Solicitante de candidato y acción no coincide")
        if not (
            _as_datetime(candidate.created_at)
            <= current
            < _as_datetime(candidate.expires_at)
        ):
            raise ProductionPolicyError("Candidato C8 fuera de vigencia")

    if grant.action_id != action.action_id:
        raise ProductionPolicyError("Grant C8 no pertenece a la acción")
    if (
        grant.authority_code != C8_ADMISSION_AUTHORITY_CODE
        or grant.authority_version != C8_ADMISSION_AUTHORITY_VERSION
    ):
        raise ProductionPolicyError("C8 exige emisor y versión CORE congelados")
    if grant.required_evidence_level is not EvidenceLevel.E4_RECEIPT_VERIFIED:
        raise ProductionPolicyError("C8 exige evidencia E4 exacta")
    if grant.authorized_connector_modes != (C8_ADMISSION_MODE,):
        raise ProductionPolicyError("C8 autoriza solo modo assisted inerte")
    if grant.legal_effect_authorized:
        raise ProductionPolicyError("C8 v1 prohíbe autorización de efecto legal")
    if grant.revoked_at is not None:
        raise ProductionPolicyError("Grant C8 revocado")
    if len(grant.approved_by_operator_ids) != 2:
        raise ProductionPolicyError("C8 exige exactamente dos aprobadores")
    if action.requested_by_operator_id in grant.approved_by_operator_ids:
        raise ProductionPolicyError("El solicitante C8 no puede aprobar")
    if grant.payload_sha256 != payload_sha256(action):
        raise ProductionPolicyError("Hash de grant C8 no coincide")
    expected_key = derive_idempotency_key(
        action,
        authority_scope=C8_ADMISSION_AUTHORITY_CODE,
    )
    if grant.idempotency_key != expected_key:
        raise ProductionPolicyError("Idempotencia C8 no coincide")

    requested_at = _as_datetime(action.requested_at)
    authorized_at = _as_datetime(grant.authorized_at)
    if requested_at > current or authorized_at > current:
        raise ProductionPolicyError("C8 no admite timestamps futuros")
    if authorized_at < requested_at:
        raise ProductionPolicyError("La aprobación C8 precede a la solicitud")
    if grant.expires_at is None or _as_datetime(grant.expires_at) <= current:
        raise ProductionPolicyError("Grant C8 debe estar vigente y expirar")
    if candidate is not None and _as_datetime(grant.expires_at) > _as_datetime(
        candidate.expires_at
    ):
        raise ProductionPolicyError("Grant C8 excede vigencia del candidato")


def validate_c8_release_approvals(
    candidate: ProductionAdmissionCandidate,
    security: ProductionReleaseApproval,
    operations: ProductionReleaseApproval,
    *,
    now: datetime | None = None,
) -> None:
    """Exige dos atestaciones role-bound sin convertirlas en activación."""

    if type(candidate) is not ProductionAdmissionCandidate:
        raise ProductionPolicyError("Candidato C8 no sellado")
    if type(security) is not ProductionReleaseApproval or type(
        operations
    ) is not ProductionReleaseApproval:
        raise ProductionPolicyError("Aprobaciones C8 no selladas")
    if security.approval_role is not ProductionApprovalRole.SECURITY:
        raise ProductionPolicyError("Falta aprobación C8 de seguridad")
    if operations.approval_role is not ProductionApprovalRole.OPERATIONS:
        raise ProductionPolicyError("Falta aprobación C8 de operaciones")
    expected_digest = candidate_sha256(candidate)
    for approval in (security, operations):
        if (
            approval.candidate_id != candidate.candidate_id
            or approval.candidate_sha256 != expected_digest
            or approval.requested_by_operator_id
            != candidate.requested_by_operator_id
        ):
            raise ProductionPolicyError("Aprobación C8 fuera del candidato")
    if security.approver_operator_id == operations.approver_operator_id:
        raise ProductionPolicyError("Seguridad y operaciones deben ser distintas")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ProductionPolicyError("now debe incluir zona horaria")
    current = current.astimezone(timezone.utc)
    if not (
        _as_datetime(candidate.created_at)
        <= current
        < _as_datetime(candidate.expires_at)
    ):
        raise ProductionPolicyError("Candidato C8 fuera de vigencia")
    for approval in (security, operations):
        if not (
            _as_datetime(candidate.created_at)
            <= _as_datetime(approval.approved_at)
            <= current
            < _as_datetime(approval.expires_at)
        ):
            raise ProductionPolicyError("Aprobación C8 fuera de vigencia")
        if _as_datetime(approval.expires_at) > _as_datetime(candidate.expires_at):
            raise ProductionPolicyError("Aprobación C8 excede al candidato")
    if _as_datetime(operations.approved_at) < _as_datetime(
        security.approved_at
    ):
        raise ProductionPolicyError(
            "Operaciones C8 no puede aprobar antes que seguridad"
        )


def assess_c8_candidate(
    candidate: ProductionAdmissionCandidate,
    *,
    values: Mapping[str, str] | None = None,
    evaluated_at: str,
) -> ProductionAdmissionAssessment:
    """Admite la simulación y conserva producción real en NO-GO."""

    if type(candidate) is not ProductionAdmissionCandidate:
        raise ProductionPolicyError("Candidato C8 no sellado")
    assert_c8_staging_boundary(values)
    evaluated = _as_datetime(evaluated_at)
    if (
        evaluated.tzinfo is None
        or evaluated.utcoffset() is None
        or evaluated.utcoffset().total_seconds() != 0
    ):
        raise ProductionPolicyError("evaluated_at debe incluir UTC")
    if not (
        _as_datetime(candidate.created_at)
        <= evaluated
        < _as_datetime(candidate.expires_at)
    ):
        raise ProductionPolicyError("Candidato C8 fuera de vigencia")
    return ProductionAdmissionAssessment(
        candidate_sha256=candidate_sha256(candidate),
        evaluated_at=evaluated_at,
        blocker_codes=_NO_GO_BLOCKERS,
    )


def assert_live_activation_unavailable(
    *,
    candidate: ProductionAdmissionCandidate | None = None,
    values: Mapping[str, str] | None = None,
) -> NoReturn:
    """Barrera no configurable: C8 v1 jamás activa producción real."""

    del candidate, values
    raise ProductionLiveActivationUnavailable(
        "C8 v1 es un plano inerte; falta un pack específico de proveedor"
    )


__all__ = [
    "RTM_CONNECT_C8_PRODUCTION_POLICY_VERSION",
    "C8_ADMISSION_AUTHORITY_CODE",
    "C8_ADMISSION_AUTHORITY_VERSION",
    "C8_ADMISSION_CAPABILITY",
    "C8_ADMISSION_MODE",
    "C8_ADMISSION_SATELLITE",
    "C8_ADMISSION_TARGET_REF",
    "C8_ADMISSION_TARGET_TYPE",
    "ProductionAdmissionStagingBoundary",
    "ProductionLiveActivationUnavailable",
    "ProductionPolicyError",
    "ProductionRuntimeDisabled",
    "assert_c8_database_identity",
    "assert_c8_staging_boundary",
    "assert_live_activation_unavailable",
    "assess_c8_candidate",
    "load_c8_runtime_configuration",
    "validate_c8_admission_authority",
    "validate_c8_release_approvals",
]
