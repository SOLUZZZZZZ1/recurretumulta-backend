#!/usr/bin/env python3
"""Smoke offline de la admision de proveedor G1.

No importa la aplicacion, no abre red, no toca base de datos y no extrae el
ZIP. La unica salida satisfactoria conserva BLOCKED/NO-GO con exit 2.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
while str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

from rtm_connect_post_c8_g1 import (  # noqa: E402
    POST_C8_G1_BASE_ARCHIVE_SHA256,
    POST_C8_G1_BASE_COMMIT_SHA40,
    POST_C8_G1_BASELINE_SNAPSHOT_SHA256,
    POST_C8_G1_CONTRACT_VERSION,
    POST_C8_G1_FROZEN_EVALUATED_AT,
    PostC8G1Error,
    PostC8G1LiveActivationUnavailable,
    assess_provider_admission,
    assert_g1_live_activation_unavailable,
    provider_admission_fingerprint_material,
    provider_admission_sha256,
)
from scripts.rtm_connect_post_c8_g1_preflight import (  # noqa: E402
    _interpreter_isolated,
    audit_archive,
    audit_local_gate,
)


SMOKE_VERSION = "rtm_connect_post_c8_g1_smoke_v1_0"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--compact", action="store_true")
    return parser


def _print(report: dict[str, Any], compact: bool) -> None:
    print(json.dumps(
        report,
        ensure_ascii=False,
        indent=None if compact else 2,
        separators=(",", ":") if compact else None,
        sort_keys=True,
    ))


def _report() -> dict[str, Any]:
    return {
        "ok": False,
        "audit_ok": False,
        "offline_review_reproduced": False,
        "authority": "rtm_connect_post_c8_g1_smoke",
        "version": SMOKE_VERSION,
        "read_only": True,
        "offline_only": True,
        "transactional": False,
        "database_touched": False,
        "schema_changes_applied": False,
        "routes_published": False,
        "workers_started": False,
        "network_used": False,
        "provider_contacted": False,
        "secret_resolution_performed": False,
        "real_data_used": False,
        "external_effects_executed": False,
        "production_authorized": False,
        "live_activation_available": False,
        "production_effects_available": False,
        "production_safe": False,
        "gate_cleared": False,
        "live_verdict": "no_go",
        "checks": {},
        "blockers": [],
        "tests_ok": False,
        "safe": False,
    }


def _assessment():
    return assess_provider_admission(
        source_commit_sha40=POST_C8_G1_BASE_COMMIT_SHA40,
        base_archive_sha256=POST_C8_G1_BASE_ARCHIVE_SHA256,
        baseline_snapshot_sha256=POST_C8_G1_BASELINE_SNAPSHOT_SHA256,
        evaluated_at=POST_C8_G1_FROZEN_EVALUATED_AT,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _report()
    try:
        archive_audit = audit_archive(Path(args.archive).resolve())
        base_hashes = archive_audit["_base_file_canonical_sha256"]
        archive_report = {
            key: value
            for key, value in archive_audit.items()
            if key != "_base_file_canonical_sha256"
        }
        first = _assessment()
        second = _assessment()
        local_report = audit_local_gate(first, base_hashes)

        wrong_identity_blocked = True
        for mutation in (
            {"source_commit_sha40": "0" * 40},
            {"base_archive_sha256": "0" * 64},
            {"baseline_snapshot_sha256": "0" * 64},
            {"evaluated_at": "2026-08-25T05:35:22Z"},
        ):
            values = {
                "source_commit_sha40": POST_C8_G1_BASE_COMMIT_SHA40,
                "base_archive_sha256": POST_C8_G1_BASE_ARCHIVE_SHA256,
                "baseline_snapshot_sha256": POST_C8_G1_BASELINE_SNAPSHOT_SHA256,
                "evaluated_at": POST_C8_G1_FROZEN_EVALUATED_AT,
            }
            values.update(mutation)
            try:
                assess_provider_admission(**values)
            except PostC8G1Error:
                continue
            wrong_identity_blocked = False

        true_flags_blocked = True
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
            try:
                replace(first, **{name: True})
            except PostC8G1Error:
                continue
            true_flags_blocked = False

        structural_mutations_blocked = True
        for mutation in (
            {"gate_status": "cleared"},
            {"live_verdict": "go"},
            {"live_canary_percent": 1},
            {"candidates": first.candidates[:-1]},
            {"required_dossier_sections": first.required_dossier_sections[:-1]},
        ):
            try:
                replace(first, **mutation)
            except PostC8G1Error:
                continue
            structural_mutations_blocked = False

        frozen = False
        try:
            first.live_verdict = "go"  # type: ignore[misc]
        except FrozenInstanceError:
            frozen = True

        guard_blocked = False
        try:
            assert_g1_live_activation_unavailable(assessment=first)
        except PostC8G1LiveActivationUnavailable as exc:
            guard_blocked = exc.code == "g1_live_activation_unavailable"

        report["archive"] = archive_report
        report["local_gate"] = local_report
        report["contract_version"] = POST_C8_G1_CONTRACT_VERSION
        report["evaluated_at"] = first.evaluated_at
        report["fingerprint_material"] = provider_admission_fingerprint_material(
            first
        )
        report["assessment_sha256"] = provider_admission_sha256(first)
        report["gate_status"] = first.gate_status
        report["legacy_candidates_total"] = len(first.candidates)
        report["candidate_blockers_total"] = sum(
            len(item.blocker_codes) for item in first.candidates
        )
        report["checks"] = {
            "exact_archive_audited_without_filesystem_extraction": bool(
                archive_report["crc_ok"] and archive_report["safe_members"]
            ),
            "base_worktree_and_evidence_manifest_exact": bool(
                local_report["frozen_base_tree_exact"]
                and local_report["evidence_manifest_exact"]
            ),
            "isolated_no_site_no_bytecode_interpreter": _interpreter_isolated(),
            "assessment_is_deterministic": (
                provider_admission_sha256(first)
                == provider_admission_sha256(second)
            ),
            "wrong_commit_archive_snapshot_or_time_blocked": (
                wrong_identity_blocked
            ),
            "assessment_is_immutable": frozen,
            "all_authority_runtime_and_effect_mutations_blocked": (
                true_flags_blocked
            ),
            "structural_go_candidate_and_dossier_mutations_blocked": (
                structural_mutations_blocked
            ),
            "three_legacy_candidates_fixed_and_rejected": (
                len(first.candidates) == 3
                and all(item.status == "rejected" for item in first.candidates)
            ),
            "legacy_candidates_never_become_provider_pack": (
                not first.legacy_candidates_are_provider_pack
            ),
            "g0_no_go_is_preserved": (
                first.g0_decision_preserved and not first.g0_no_go_overridden
            ),
            "provider_remains_unselected_and_uncontacted": not any((
                first.provider_selected,
                first.provider_identity_verified,
                first.provider_contact_allowed,
            )),
            "provider_pack_remains_absent_and_inadmissible": not any((
                first.provider_pack_present,
                first.provider_pack_admissible,
            )),
            "live_activation_guard_unconditional": guard_blocked,
            "live_verdict_remains_no_go": first.live_verdict == "no_go",
            "gate_never_cleared": first.gate_status == "blocked",
            "live_canary_exactly_zero": first.live_canary_percent == 0,
            "no_runtime_surface_allowed": not any((
                first.routes_allowed,
                first.workers_allowed,
                first.network_allowed,
                first.secret_access_allowed,
                first.database_access_allowed,
            )),
            "no_production_authority_or_effects": not any((
                first.production_authorized,
                first.authorization_created,
                first.external_effects_allowed,
                first.live_activation_allowed,
                first.production_effects_available,
            )),
        }
        report["tests_ok"] = all(report["checks"].values())
        report["audit_ok"] = report["tests_ok"]
        report["offline_review_reproduced"] = report["tests_ok"]
        report["blockers"] = [
            code
            for candidate in first.candidates
            for code in candidate.blocker_codes
        ]
        report["ok"] = False
        report["safe"] = False
        report["production_safe"] = False
        _print(report, args.compact)
        return 2
    except (OSError, ValueError) as exc:
        report["blockers"] = [
            f"post_c8_g1_smoke_blocked:{type(exc).__name__}:{exc}"
        ]
        _print(report, args.compact)
        return 2
    except Exception as exc:
        report["blockers"] = [f"unexpected:{type(exc).__name__}:{exc}"]
        _print(report, args.compact)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
