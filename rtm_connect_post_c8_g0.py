"""Puerta offline de decision RTM CONNECT posterior a C8 (G0).

G0 congela el inventario de bloqueos observado sobre el cierre C8 exacto. No
acepta evidencia inyectable capaz de despejar la puerta y no contiene un
veredicto GO. Un futuro pack especifico de proveedor necesitara otro ADR,
otros contratos y una autorizacion independiente.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, NoReturn


RTM_CONNECT_POST_C8_G0_VERSION = "rtm_connect_post_c8_g0_v1_0"
POST_C8_GATE_CONTRACT_VERSION = "rtm.connect.post_c8.g0.assessment.v1"
POST_C8_GATE_BASE_COMMIT_SHA40 = "a0ecdebd4575d54f7e89c69b9871a29039370d22"
POST_C8_GATE_BASE_ARCHIVE_SHA256 = (
    "5832b0acd854e0dc5d864521a5a9350e44802facb74eea6d28cc15f44dbbd14f"
)
POST_C8_GATE_BASELINE_SNAPSHOT_SHA256 = (
    "cc819ed72839500946910b643b30a181018a9665bc1fb3c37b67228697a116a5"
)
POST_C8_GATE_FROZEN_EVALUATED_AT = "2026-08-24T17:15:00Z"
POST_C8_GATE_NEXT_STEP = "provider_specific_pack_and_new_adr_required"
POST_C8_GATE_REQUIRED_APPROVAL_ROLES = (
    "security_owner",
    "operations_owner",
    "privacy_owner",
    "legal_compliance_owner",
    "provider_owner",
    "service_owner",
)
POST_C8_GATE_REQUIRED_AUTHORITY_CHAIN = (
    "core_authorizer",
    "requester",
    "independent_release_activator",
    "independent_evidence_verifier",
)
POST_C8_GATE_REQUIRED_EVIDENCE_CONTROLS = (
    "commit_and_artifact_binding",
    "approval_timestamp",
    "expires_at",
    "revocation_status",
    "evidence_freshness",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_BLOCKER_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


class PostC8GateError(ValueError):
    """La identidad o estructura de la evaluacion G0 no es exacta."""


class PostC8LiveActivationUnavailable(RuntimeError):
    """G0 no ofrece activacion live bajo ninguna configuracion."""

    code = "g0_live_activation_unavailable"


class PostC8GateDomain(str, Enum):
    SECURITY = "security"
    OPERATIONS = "operations"
    PRIVACY = "privacy"
    PROVIDER = "provider"
    CANARY = "canary"
    ROLLBACK = "rollback"


_FROZEN_BLOCKERS = MappingProxyType({
    PostC8GateDomain.SECURITY: (
        "security.production_egress_default_deny_not_verified",
        "security.production_secret_isolation_not_verified",
        "security.runtime_db_role_separation_missing",
        "security.postgresql_canonical_hash_recalculation_missing",
        "security.legacy_submission_bypass_not_closed",
        "security.runtime_capability_legacy_fail_open_not_closed",
        "security.embedded_legal_signature_asset_custody_and_classification_not_resolved",
        "security.supply_chain_signature_and_sbom_missing",
        "security.global_external_effect_inventory_and_guards_incomplete",
        "security.independent_security_approval_missing",
    ),
    PostC8GateDomain.OPERATIONS: (
        "operations.production_kill_switch_claim_drain_not_exercised",
        "operations.observability_alerting_on_call_missing",
        "operations.restore_reconciliation_runbook_not_exercised",
        "operations.deployment_entrypoints_and_cron_not_frozen",
        "operations.legacy_shared_token_boundary_not_closed",
        "operations.runtime_environment_startup_guard_not_enforced",
        "operations.external_effect_failures_can_be_silenced",
        "operations.service_owner_and_operations_approvals_missing",
        "operations.approval_expiry_and_revocation_controls_missing",
    ),
    PostC8GateDomain.PRIVACY: (
        "privacy.production_data_inventory_minimization_missing",
        "privacy.legal_basis_processor_terms_missing",
        "privacy.retention_redaction_dsar_controls_missing",
        "privacy.embedded_signature_custody_authorization_missing",
        "privacy.real_data_and_fixture_classification_missing",
        "privacy.privacy_and_legal_compliance_approvals_missing",
    ),
    PostC8GateDomain.PROVIDER: (
        "provider.specific_pack_missing",
        "provider.tenant_protocol_request_receipt_schemas_missing",
        "provider.remote_idempotency_read_only_lookup_missing",
        "provider.authentic_e4_verifier_missing",
        "provider.legacy_submitters_not_quarantined_or_migrated",
        "provider.endpoint_credential_origin_allowlist_missing",
        "provider.provider_owner_approval_missing",
    ),
    PostC8GateDomain.CANARY: (
        "canary.real_provider_scope_tenant_selection_missing",
        "canary.abort_thresholds_observation_window_missing",
        "canary.manual_no_auto_expand_control_not_verified",
        "canary.live_percentage_must_remain_zero_in_g0",
        "canary.core_requester_independent_activator_separation_missing",
        "canary.evidence_freshness_and_expiry_policy_missing",
    ),
    PostC8GateDomain.ROLLBACK: (
        "rollback.stop_claims_revoke_egress_secrets_not_exercised",
        "rollback.ambiguous_to_unknown_reconciliation_not_exercised",
        "rollback.compensation_as_new_authorized_action_not_defined",
        "rollback.remote_effect_restore_drill_missing",
    ),
})

_FROZEN_DOMAIN_ORDER = tuple(PostC8GateDomain)
_FROZEN_ACTIVATION_BLOCKERS = (
    "g0.never_authorizes_production",
    "g0.new_provider_pack_and_adr_required",
    "g0.independent_human_decision_required",
    "g0.approval_matrix_incomplete",
    "g0.authority_chain_and_revocation_missing",
    "g0.overlay_commit_and_archive_identity_not_frozen",
)


def _normal_sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise PostC8GateError(f"{field_name} debe ser SHA-256 textual")
    normalized = value.strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise PostC8GateError(f"{field_name} debe ser SHA-256 hexadecimal")
    return normalized


def _normal_sha40(value: str) -> str:
    if not isinstance(value, str):
        raise PostC8GateError("source_commit_sha40 debe ser SHA-40 textual")
    normalized = value.strip().lower()
    if not _SHA40_RE.fullmatch(normalized):
        raise PostC8GateError("source_commit_sha40 debe ser SHA-40 hexadecimal")
    return normalized


def _utc_timestamp(value: str) -> str:
    if not isinstance(value, str):
        raise PostC8GateError("evaluated_at debe ser timestamp textual")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PostC8GateError("evaluated_at no es timestamp valido") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset().total_seconds() != 0
    ):
        raise PostC8GateError("evaluated_at debe estar en UTC")
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _exact_bool(value: bool, expected: bool, field_name: str) -> None:
    if type(value) is not bool or value is not expected:
        literal = "true" if expected else "false"
        raise PostC8GateError(f"{field_name} debe ser {literal}")


@dataclass(frozen=True)
class PostC8GateFinding:
    domain: PostC8GateDomain
    blocker_codes: tuple[str, ...]
    status: str = "blocked"

    def __post_init__(self) -> None:
        try:
            domain = (
                self.domain
                if isinstance(self.domain, PostC8GateDomain)
                else PostC8GateDomain(str(self.domain))
            )
        except ValueError as exc:
            raise PostC8GateError("Dominio G0 no admitido") from exc
        object.__setattr__(self, "domain", domain)
        blockers = tuple(str(code).strip() for code in self.blocker_codes)
        if (
            not blockers
            or len(blockers) != len(set(blockers))
            or any(not _BLOCKER_RE.fullmatch(code) for code in blockers)
            or any(not code.startswith(f"{domain.value}.") for code in blockers)
        ):
            raise PostC8GateError("Blockers G0 no normalizados para el dominio")
        object.__setattr__(self, "blocker_codes", blockers)
        if self.status != "blocked":
            raise PostC8GateError("G0 solo admite findings bloqueados")


@dataclass(frozen=True)
class PostC8GateAssessment:
    source_commit_sha40: str
    base_archive_sha256: str
    baseline_snapshot_sha256: str
    evaluated_at: str
    findings: tuple[PostC8GateFinding, ...]
    activation_blockers: tuple[str, ...] = _FROZEN_ACTIVATION_BLOCKERS
    contract_version: str = POST_C8_GATE_CONTRACT_VERSION
    gate_status: str = "blocked"
    live_verdict: str = "no_go"
    next_step: str = POST_C8_GATE_NEXT_STEP
    review_only: bool = True
    offline_only: bool = True
    read_only: bool = True
    production_authorized: bool = False
    authorization_created: bool = False
    routes_allowed: bool = False
    workers_allowed: bool = False
    provider_contact_allowed: bool = False
    network_allowed: bool = False
    secret_access_allowed: bool = False
    database_access_allowed: bool = False
    database_ddl_allowed: bool = False
    database_dml_allowed: bool = False
    real_data_allowed: bool = False
    external_effects_allowed: bool = False
    live_activation_allowed: bool = False
    production_effects_available: bool = False
    production_safe: bool = False
    approval_matrix_satisfied: bool = False
    authority_chain_satisfied: bool = False
    evidence_freshness_satisfied: bool = False
    revocation_status_verified: bool = False
    live_canary_percent: int = 0
    c8_dry_run_is_authentic_e4: bool = False
    required_approval_roles: tuple[str, ...] = POST_C8_GATE_REQUIRED_APPROVAL_ROLES
    required_authority_chain: tuple[str, ...] = POST_C8_GATE_REQUIRED_AUTHORITY_CHAIN
    required_evidence_controls: tuple[str, ...] = POST_C8_GATE_REQUIRED_EVIDENCE_CONTROLS

    def __post_init__(self) -> None:
        commit = _normal_sha40(self.source_commit_sha40)
        archive = _normal_sha256(self.base_archive_sha256, "base_archive_sha256")
        snapshot = _normal_sha256(
            self.baseline_snapshot_sha256,
            "baseline_snapshot_sha256",
        )
        object.__setattr__(self, "source_commit_sha40", commit)
        object.__setattr__(self, "base_archive_sha256", archive)
        object.__setattr__(self, "baseline_snapshot_sha256", snapshot)
        object.__setattr__(self, "evaluated_at", _utc_timestamp(self.evaluated_at))
        if commit != POST_C8_GATE_BASE_COMMIT_SHA40:
            raise PostC8GateError("G0 exige el commit C8 final congelado")
        if archive != POST_C8_GATE_BASE_ARCHIVE_SHA256:
            raise PostC8GateError("G0 exige el ZIP C8 final congelado")
        if snapshot != POST_C8_GATE_BASELINE_SNAPSHOT_SHA256:
            raise PostC8GateError("G0 exige el snapshot C8 critico congelado")
        if self.evaluated_at != POST_C8_GATE_FROZEN_EVALUATED_AT:
            raise PostC8GateError("G0 exige el timestamp de evaluacion congelado")
        findings = tuple(self.findings)
        if any(type(item) is not PostC8GateFinding for item in findings):
            raise PostC8GateError("Findings G0 no sellados")
        if tuple(item.domain for item in findings) != _FROZEN_DOMAIN_ORDER:
            raise PostC8GateError("G0 exige los seis dominios en orden congelado")
        expected_findings = tuple(
            PostC8GateFinding(domain, _FROZEN_BLOCKERS[domain])
            for domain in _FROZEN_DOMAIN_ORDER
        )
        if findings != expected_findings:
            raise PostC8GateError("El inventario G0 no puede despejarse ni mutarse")
        object.__setattr__(self, "findings", findings)
        activation_blockers = tuple(self.activation_blockers)
        if activation_blockers != _FROZEN_ACTIVATION_BLOCKERS:
            raise PostC8GateError("Blockers de activacion G0 alterados")
        object.__setattr__(self, "activation_blockers", activation_blockers)
        if self.contract_version != POST_C8_GATE_CONTRACT_VERSION:
            raise PostC8GateError("Version de contrato G0 no admitida")
        if self.gate_status != "blocked" or self.live_verdict != "no_go":
            raise PostC8GateError("G0 solo admite blocked/no_go")
        if self.next_step != POST_C8_GATE_NEXT_STEP:
            raise PostC8GateError("Siguiente paso G0 no admitido")
        for name in (
            "review_only",
            "offline_only",
            "read_only",
        ):
            _exact_bool(getattr(self, name), True, name)
        for name in (
            "production_authorized",
            "authorization_created",
            "routes_allowed",
            "workers_allowed",
            "provider_contact_allowed",
            "network_allowed",
            "secret_access_allowed",
            "database_access_allowed",
            "database_ddl_allowed",
            "database_dml_allowed",
            "real_data_allowed",
            "external_effects_allowed",
            "live_activation_allowed",
            "production_effects_available",
            "production_safe",
            "approval_matrix_satisfied",
            "authority_chain_satisfied",
            "evidence_freshness_satisfied",
            "revocation_status_verified",
            "c8_dry_run_is_authentic_e4",
        ):
            _exact_bool(getattr(self, name), False, name)
        required_approval_roles = tuple(self.required_approval_roles)
        required_authority_chain = tuple(self.required_authority_chain)
        required_evidence_controls = tuple(self.required_evidence_controls)
        if required_approval_roles != POST_C8_GATE_REQUIRED_APPROVAL_ROLES:
            raise PostC8GateError("Matriz de aprobaciones G0 alterada")
        if required_authority_chain != POST_C8_GATE_REQUIRED_AUTHORITY_CHAIN:
            raise PostC8GateError("Cadena de autoridad G0 alterada")
        if required_evidence_controls != POST_C8_GATE_REQUIRED_EVIDENCE_CONTROLS:
            raise PostC8GateError("Controles de frescura y revocacion G0 alterados")
        object.__setattr__(self, "required_approval_roles", required_approval_roles)
        object.__setattr__(self, "required_authority_chain", required_authority_chain)
        object.__setattr__(self, "required_evidence_controls", required_evidence_controls)
        if type(self.live_canary_percent) is not int or self.live_canary_percent != 0:
            raise PostC8GateError("G0 exige live_canary_percent=0")


def assess_post_c8_gate(
    *,
    source_commit_sha40: str,
    base_archive_sha256: str,
    baseline_snapshot_sha256: str,
    evaluated_at: str,
) -> PostC8GateAssessment:
    """Devuelve siempre el inventario fijo BLOCKED/NO-GO de G0."""

    return PostC8GateAssessment(
        source_commit_sha40=source_commit_sha40,
        base_archive_sha256=base_archive_sha256,
        baseline_snapshot_sha256=baseline_snapshot_sha256,
        evaluated_at=evaluated_at,
        findings=tuple(
            PostC8GateFinding(domain, _FROZEN_BLOCKERS[domain])
            for domain in _FROZEN_DOMAIN_ORDER
        ),
    )


def post_c8_gate_fingerprint_material(
    assessment: PostC8GateAssessment,
) -> dict[str, Any]:
    if type(assessment) is not PostC8GateAssessment:
        raise PostC8GateError("Assessment G0 no sellado")
    return {
        "contract_version": assessment.contract_version,
        "source_commit_sha40": assessment.source_commit_sha40,
        "base_archive_sha256": assessment.base_archive_sha256,
        "baseline_snapshot_sha256": assessment.baseline_snapshot_sha256,
        "evaluated_at": assessment.evaluated_at,
        "findings": [
            {
                "domain": item.domain.value,
                "status": item.status,
                "blocker_codes": list(item.blocker_codes),
            }
            for item in assessment.findings
        ],
        "activation_blockers": list(assessment.activation_blockers),
        "gate_status": assessment.gate_status,
        "live_verdict": assessment.live_verdict,
        "next_step": assessment.next_step,
        "review_only": assessment.review_only,
        "offline_only": assessment.offline_only,
        "read_only": assessment.read_only,
        "production_authorized": assessment.production_authorized,
        "authorization_created": assessment.authorization_created,
        "routes_allowed": assessment.routes_allowed,
        "workers_allowed": assessment.workers_allowed,
        "provider_contact_allowed": assessment.provider_contact_allowed,
        "network_allowed": assessment.network_allowed,
        "secret_access_allowed": assessment.secret_access_allowed,
        "database_access_allowed": assessment.database_access_allowed,
        "database_ddl_allowed": assessment.database_ddl_allowed,
        "database_dml_allowed": assessment.database_dml_allowed,
        "real_data_allowed": assessment.real_data_allowed,
        "external_effects_allowed": assessment.external_effects_allowed,
        "live_activation_allowed": assessment.live_activation_allowed,
        "production_effects_available": assessment.production_effects_available,
        "production_safe": assessment.production_safe,
        "approval_matrix_satisfied": assessment.approval_matrix_satisfied,
        "authority_chain_satisfied": assessment.authority_chain_satisfied,
        "evidence_freshness_satisfied": assessment.evidence_freshness_satisfied,
        "revocation_status_verified": assessment.revocation_status_verified,
        "live_canary_percent": assessment.live_canary_percent,
        "c8_dry_run_is_authentic_e4": assessment.c8_dry_run_is_authentic_e4,
        "required_approval_roles": list(assessment.required_approval_roles),
        "required_authority_chain": list(assessment.required_authority_chain),
        "required_evidence_controls": list(assessment.required_evidence_controls),
    }


def post_c8_gate_sha256(assessment: PostC8GateAssessment) -> str:
    material = post_c8_gate_fingerprint_material(assessment)
    canonical = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assert_g0_live_activation_unavailable(
    *,
    assessment: PostC8GateAssessment | None = None,
) -> NoReturn:
    del assessment
    raise PostC8LiveActivationUnavailable(
        "G0 es una puerta offline NO-GO; otro ADR debe definir cualquier pack real"
    )


__all__ = [
    "RTM_CONNECT_POST_C8_G0_VERSION",
    "POST_C8_GATE_CONTRACT_VERSION",
    "POST_C8_GATE_BASE_COMMIT_SHA40",
    "POST_C8_GATE_BASE_ARCHIVE_SHA256",
    "POST_C8_GATE_BASELINE_SNAPSHOT_SHA256",
    "POST_C8_GATE_FROZEN_EVALUATED_AT",
    "POST_C8_GATE_NEXT_STEP",
    "POST_C8_GATE_REQUIRED_APPROVAL_ROLES",
    "POST_C8_GATE_REQUIRED_AUTHORITY_CHAIN",
    "POST_C8_GATE_REQUIRED_EVIDENCE_CONTROLS",
    "PostC8GateAssessment",
    "PostC8GateDomain",
    "PostC8GateError",
    "PostC8GateFinding",
    "PostC8LiveActivationUnavailable",
    "assess_post_c8_gate",
    "assert_g0_live_activation_unavailable",
    "post_c8_gate_fingerprint_material",
    "post_c8_gate_sha256",
]
