"""Revision offline de admision de proveedor RTM CONNECT post-C8 (G1).

G1 congela la identidad externa del overlay G0 y rechaza expresamente las
tres superficies legacy que podrian confundirse con un pack productivo. No
selecciona proveedor, no acepta evidencia inyectable y no contiene un camino
GO. Un dossier real, especifico y verificado exige otra unidad y otro ADR.
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


RTM_CONNECT_POST_C8_G1_VERSION = "rtm_connect_post_c8_g1_v1_0"
POST_C8_G1_CONTRACT_VERSION = "rtm.connect.post_c8.g1.provider_admission.v1"
POST_C8_G1_BASE_COMMIT_SHA40 = "eedd521ecf1703c9b5e20196651da04557900e74"
POST_C8_G1_BASE_ARCHIVE_SHA256 = (
    "8d69d66573d92b675be26d391c1d03a74ff62a514bdf369dfce817db396ba3f3"
)
POST_C8_G1_BASELINE_SNAPSHOT_SHA256 = (
    "04bbab064c06e58da288e43a2918f57e37ff3eca0f00ece5b81cfdd5f0bc903d"
)
POST_C8_G1_FROZEN_EVALUATED_AT = "2026-08-25T05:35:21Z"
POST_C8_G1_NEXT_STEP = "verified_provider_dossier_and_provider_specific_g2_required"

POST_C8_G1_REQUIRED_DOSSIER_SECTIONS = (
    "provider_legal_identity_and_accountable_owner",
    "tenant_and_authorized_service_scope",
    "https_origin_protocol_and_version",
    "request_schema_and_canonical_hashing",
    "receipt_schema_and_authentic_e4_verifier",
    "remote_idempotency_fencing_and_read_only_lookup",
    "unknown_reconciliation_and_error_taxonomy",
    "workload_identity_secret_custody_and_egress_allowlist",
    "data_inventory_legal_basis_retention_redaction_and_dsar",
    "slo_alerting_on_call_kill_switch_and_claim_drain",
    "canary_cohort_abort_thresholds_and_no_auto_expand",
    "remote_rollback_restore_replay_and_authorized_compensation",
    "hash_bound_approvals_expiry_revocation_and_separation_of_duties",
    "signed_sbom_provenance_sast_sca_and_secret_scan",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_BLOCKER_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


class PostC8G1Error(ValueError):
    """La identidad o la estructura de G1 no coincide con el contrato."""


class PostC8G1LiveActivationUnavailable(RuntimeError):
    """G1 no ofrece activacion live bajo ninguna configuracion."""

    code = "g1_live_activation_unavailable"


class ProviderCandidateCode(str, Enum):
    DGT_CLIENT_PLACEHOLDER = "legacy.dgt_client_placeholder"
    DGT_DEV_XML_SUBMITTER = "legacy.dgt_dev_xml_submitter"
    REGISTRO_GENERAL_GENERIC = "legacy.registro_general_generic"


_FROZEN_CANDIDATE_PATHS = MappingProxyType({
    ProviderCandidateCode.DGT_CLIENT_PLACEHOLDER: (
        "dgt_client.py",
        "ops_automation.py",
        "ops_automation_router.py",
    ),
    ProviderCandidateCode.DGT_DEV_XML_SUBMITTER: (
        "submitter_dgt.py",
    ),
    ProviderCandidateCode.REGISTRO_GENERAL_GENERIC: (
        "submitters/registro.py",
        "submitters/base.py",
    ),
})

_FROZEN_CANDIDATE_BLOCKERS = MappingProxyType({
    ProviderCandidateCode.DGT_CLIENT_PLACEHOLDER: (
        "dgt_client.provider_identity_and_protocol_missing",
        "dgt_client.non_empty_dgt_enabled_is_fail_open",
        "dgt_client.implementation_is_placeholder",
        "dgt_client.connect_authority_chain_missing",
        "dgt_client.idempotency_lookup_reconciliation_and_e4_missing",
    ),
    ProviderCandidateCode.DGT_DEV_XML_SUBMITTER: (
        "dgt_xml.development_endpoint_is_not_verified_production_origin",
        "dgt_xml.hardcoded_requester_and_placeholder_subject_data",
        "dgt_xml.signer_supply_chain_and_secret_custody_missing",
        "dgt_xml.connect_authority_idempotency_and_fencing_missing",
        "dgt_xml.receipt_verification_reconciliation_and_rollback_missing",
    ),
    ProviderCandidateCode.REGISTRO_GENERAL_GENERIC: (
        "registro_general.provider_entity_tenant_and_origin_missing",
        "registro_general.arbitrary_url_and_optional_bearer_token_not_admissible",
        "registro_general.request_size_schema_and_idempotency_missing",
        "registro_general.receipt_is_unverified_base64_not_authentic_e4",
        "registro_general.lookup_reconciliation_fencing_and_rollback_missing",
    ),
})

_FROZEN_CANDIDATE_ORDER = tuple(ProviderCandidateCode)


def _normal_sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise PostC8G1Error(f"{field_name} debe ser SHA-256 textual")
    normalized = value.strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise PostC8G1Error(f"{field_name} debe ser SHA-256 hexadecimal")
    return normalized


def _normal_sha40(value: str) -> str:
    if not isinstance(value, str):
        raise PostC8G1Error("source_commit_sha40 debe ser SHA-40 textual")
    normalized = value.strip().lower()
    if not _SHA40_RE.fullmatch(normalized):
        raise PostC8G1Error("source_commit_sha40 debe ser SHA-40 hexadecimal")
    return normalized


def _utc_timestamp(value: str) -> str:
    if not isinstance(value, str):
        raise PostC8G1Error("evaluated_at debe ser timestamp textual")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PostC8G1Error("evaluated_at no es timestamp valido") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset().total_seconds() != 0
    ):
        raise PostC8G1Error("evaluated_at debe estar en UTC")
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _exact_bool(value: bool, expected: bool, field_name: str) -> None:
    if type(value) is not bool or value is not expected:
        literal = "true" if expected else "false"
        raise PostC8G1Error(f"{field_name} debe ser {literal}")


@dataclass(frozen=True)
class ProviderCandidateFinding:
    code: ProviderCandidateCode
    source_paths: tuple[str, ...]
    blocker_codes: tuple[str, ...]
    status: str = "rejected"
    provider_specific: bool = False
    production_eligible: bool = False

    def __post_init__(self) -> None:
        try:
            code = (
                self.code
                if isinstance(self.code, ProviderCandidateCode)
                else ProviderCandidateCode(str(self.code))
            )
        except ValueError as exc:
            raise PostC8G1Error("Candidato G1 no admitido") from exc
        object.__setattr__(self, "code", code)
        paths = tuple(str(path).strip() for path in self.source_paths)
        blockers = tuple(str(item).strip() for item in self.blocker_codes)
        if paths != _FROZEN_CANDIDATE_PATHS[code]:
            raise PostC8G1Error("Rutas del candidato G1 alteradas")
        if blockers != _FROZEN_CANDIDATE_BLOCKERS[code]:
            raise PostC8G1Error("Blockers del candidato G1 alterados")
        if (
            not blockers
            or len(blockers) != len(set(blockers))
            or any(not _BLOCKER_RE.fullmatch(item) for item in blockers)
        ):
            raise PostC8G1Error("Blockers del candidato G1 no normalizados")
        object.__setattr__(self, "source_paths", paths)
        object.__setattr__(self, "blocker_codes", blockers)
        if self.status != "rejected":
            raise PostC8G1Error("G1 solo admite candidatos rechazados")
        _exact_bool(self.provider_specific, False, "provider_specific")
        _exact_bool(self.production_eligible, False, "production_eligible")


@dataclass(frozen=True)
class ProviderAdmissionAssessment:
    source_commit_sha40: str
    base_archive_sha256: str
    baseline_snapshot_sha256: str
    evaluated_at: str
    candidates: tuple[ProviderCandidateFinding, ...]
    required_dossier_sections: tuple[str, ...] = (
        POST_C8_G1_REQUIRED_DOSSIER_SECTIONS
    )
    contract_version: str = POST_C8_G1_CONTRACT_VERSION
    gate_status: str = "blocked"
    live_verdict: str = "no_go"
    next_step: str = POST_C8_G1_NEXT_STEP
    review_only: bool = True
    offline_only: bool = True
    read_only: bool = True
    base_delivery_identity_verified: bool = True
    g0_decision_preserved: bool = True
    g0_overlay_identity_frozen: bool = True
    legacy_candidates_reviewed: bool = True
    provider_dossier_required: bool = True
    provider_selected: bool = False
    provider_identity_verified: bool = False
    provider_pack_present: bool = False
    provider_pack_admissible: bool = False
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
    authentic_e4_verifier_available: bool = False
    remote_idempotency_verified: bool = False
    read_only_lookup_verified: bool = False
    unknown_reconciliation_verified: bool = False
    remote_rollback_verified: bool = False
    legacy_candidates_are_provider_pack: bool = False
    g0_no_go_overridden: bool = False
    live_canary_percent: int = 0

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
        if commit != POST_C8_G1_BASE_COMMIT_SHA40:
            raise PostC8G1Error("G1 exige el commit G0 final congelado")
        if archive != POST_C8_G1_BASE_ARCHIVE_SHA256:
            raise PostC8G1Error("G1 exige el ZIP G0 final congelado")
        if snapshot != POST_C8_G1_BASELINE_SNAPSHOT_SHA256:
            raise PostC8G1Error("G1 exige el snapshot critico G0 congelado")
        if self.evaluated_at != POST_C8_G1_FROZEN_EVALUATED_AT:
            raise PostC8G1Error("G1 exige el timestamp de evaluacion congelado")
        candidates = tuple(self.candidates)
        expected = tuple(
            ProviderCandidateFinding(
                code=code,
                source_paths=_FROZEN_CANDIDATE_PATHS[code],
                blocker_codes=_FROZEN_CANDIDATE_BLOCKERS[code],
            )
            for code in _FROZEN_CANDIDATE_ORDER
        )
        if candidates != expected:
            raise PostC8G1Error("Inventario de candidatos G1 alterado")
        object.__setattr__(self, "candidates", candidates)
        sections = tuple(self.required_dossier_sections)
        if sections != POST_C8_G1_REQUIRED_DOSSIER_SECTIONS:
            raise PostC8G1Error("Secciones del dossier de proveedor alteradas")
        object.__setattr__(self, "required_dossier_sections", sections)
        if self.contract_version != POST_C8_G1_CONTRACT_VERSION:
            raise PostC8G1Error("Version de contrato G1 no admitida")
        if self.gate_status != "blocked" or self.live_verdict != "no_go":
            raise PostC8G1Error("G1 solo admite blocked/no_go")
        if self.next_step != POST_C8_G1_NEXT_STEP:
            raise PostC8G1Error("Siguiente paso G1 no admitido")
        for name in (
            "review_only",
            "offline_only",
            "read_only",
            "base_delivery_identity_verified",
            "g0_decision_preserved",
            "g0_overlay_identity_frozen",
            "legacy_candidates_reviewed",
            "provider_dossier_required",
        ):
            _exact_bool(getattr(self, name), True, name)
        for name in (
            "provider_selected",
            "provider_identity_verified",
            "provider_pack_present",
            "provider_pack_admissible",
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
            "authentic_e4_verifier_available",
            "remote_idempotency_verified",
            "read_only_lookup_verified",
            "unknown_reconciliation_verified",
            "remote_rollback_verified",
            "legacy_candidates_are_provider_pack",
            "g0_no_go_overridden",
        ):
            _exact_bool(getattr(self, name), False, name)
        if type(self.live_canary_percent) is not int or self.live_canary_percent != 0:
            raise PostC8G1Error("G1 exige live_canary_percent=0")


def assess_provider_admission(
    *,
    source_commit_sha40: str,
    base_archive_sha256: str,
    baseline_snapshot_sha256: str,
    evaluated_at: str,
) -> ProviderAdmissionAssessment:
    """Devuelve el inventario fijo BLOCKED/NO-GO de G1."""

    return ProviderAdmissionAssessment(
        source_commit_sha40=source_commit_sha40,
        base_archive_sha256=base_archive_sha256,
        baseline_snapshot_sha256=baseline_snapshot_sha256,
        evaluated_at=evaluated_at,
        candidates=tuple(
            ProviderCandidateFinding(
                code=code,
                source_paths=_FROZEN_CANDIDATE_PATHS[code],
                blocker_codes=_FROZEN_CANDIDATE_BLOCKERS[code],
            )
            for code in _FROZEN_CANDIDATE_ORDER
        ),
    )


def provider_admission_fingerprint_material(
    assessment: ProviderAdmissionAssessment,
) -> dict[str, Any]:
    if type(assessment) is not ProviderAdmissionAssessment:
        raise PostC8G1Error("Assessment G1 no sellado")
    return {
        "contract_version": assessment.contract_version,
        "source_commit_sha40": assessment.source_commit_sha40,
        "base_archive_sha256": assessment.base_archive_sha256,
        "baseline_snapshot_sha256": assessment.baseline_snapshot_sha256,
        "evaluated_at": assessment.evaluated_at,
        "candidates": [
            {
                "code": item.code.value,
                "source_paths": list(item.source_paths),
                "blocker_codes": list(item.blocker_codes),
                "status": item.status,
                "provider_specific": item.provider_specific,
                "production_eligible": item.production_eligible,
            }
            for item in assessment.candidates
        ],
        "required_dossier_sections": list(assessment.required_dossier_sections),
        **{
            name: getattr(assessment, name)
            for name in (
                "gate_status",
                "live_verdict",
                "next_step",
                "review_only",
                "offline_only",
                "read_only",
                "base_delivery_identity_verified",
                "g0_decision_preserved",
                "g0_overlay_identity_frozen",
                "legacy_candidates_reviewed",
                "provider_dossier_required",
                "provider_selected",
                "provider_identity_verified",
                "provider_pack_present",
                "provider_pack_admissible",
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
                "authentic_e4_verifier_available",
                "remote_idempotency_verified",
                "read_only_lookup_verified",
                "unknown_reconciliation_verified",
                "remote_rollback_verified",
                "legacy_candidates_are_provider_pack",
                "g0_no_go_overridden",
                "live_canary_percent",
            )
        },
    }


def provider_admission_sha256(assessment: ProviderAdmissionAssessment) -> str:
    canonical = json.dumps(
        provider_admission_fingerprint_material(assessment),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assert_g1_live_activation_unavailable(
    *,
    assessment: ProviderAdmissionAssessment | None = None,
) -> NoReturn:
    del assessment
    raise PostC8G1LiveActivationUnavailable(
        "G1 es una revision offline NO-GO; exige un dossier real y otra unidad"
    )


__all__ = [
    "RTM_CONNECT_POST_C8_G1_VERSION",
    "POST_C8_G1_CONTRACT_VERSION",
    "POST_C8_G1_BASE_COMMIT_SHA40",
    "POST_C8_G1_BASE_ARCHIVE_SHA256",
    "POST_C8_G1_BASELINE_SNAPSHOT_SHA256",
    "POST_C8_G1_FROZEN_EVALUATED_AT",
    "POST_C8_G1_NEXT_STEP",
    "POST_C8_G1_REQUIRED_DOSSIER_SECTIONS",
    "PostC8G1Error",
    "PostC8G1LiveActivationUnavailable",
    "ProviderAdmissionAssessment",
    "ProviderCandidateCode",
    "ProviderCandidateFinding",
    "assess_provider_admission",
    "assert_g1_live_activation_unavailable",
    "provider_admission_fingerprint_material",
    "provider_admission_sha256",
]
