#!/usr/bin/env python3
"""Smoke offline de la puerta post-C8 G0.

No importa la aplicacion, no abre red, no toca base de datos y no extrae el
ZIP. La unica salida satisfactoria conserva BLOCKED/NO-GO.
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

from rtm_connect_post_c8_g0 import (  # noqa: E402
    POST_C8_GATE_BASE_ARCHIVE_SHA256,
    POST_C8_GATE_BASE_COMMIT_SHA40,
    POST_C8_GATE_BASELINE_SNAPSHOT_SHA256,
    POST_C8_GATE_CONTRACT_VERSION,
    POST_C8_GATE_FROZEN_EVALUATED_AT,
    POST_C8_GATE_REQUIRED_APPROVAL_ROLES,
    PostC8GateError,
    PostC8LiveActivationUnavailable,
    assess_post_c8_gate,
    assert_g0_live_activation_unavailable,
    post_c8_gate_fingerprint_material,
    post_c8_gate_sha256,
)
from scripts.rtm_connect_post_c8_g0_preflight import (  # noqa: E402
    _interpreter_isolated,
    audit_archive,
    audit_local_gate,
)


SMOKE_VERSION = "rtm_connect_post_c8_g0_smoke_v1_0"


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
        "authority": "rtm_connect_post_c8_g0_smoke",
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _report()
    try:
        archive_audit = audit_archive(Path(args.archive).resolve())
        base_file_canonical_sha256 = archive_audit[
            "_base_file_canonical_sha256"
        ]
        archive_report = {
            key: value
            for key, value in archive_audit.items()
            if key != "_base_file_canonical_sha256"
        }
        evaluated_at = POST_C8_GATE_FROZEN_EVALUATED_AT
        first = assess_post_c8_gate(
            source_commit_sha40=POST_C8_GATE_BASE_COMMIT_SHA40,
            base_archive_sha256=POST_C8_GATE_BASE_ARCHIVE_SHA256,
            baseline_snapshot_sha256=POST_C8_GATE_BASELINE_SNAPSHOT_SHA256,
            evaluated_at=evaluated_at,
        )
        second = assess_post_c8_gate(
            source_commit_sha40=POST_C8_GATE_BASE_COMMIT_SHA40,
            base_archive_sha256=POST_C8_GATE_BASE_ARCHIVE_SHA256,
            baseline_snapshot_sha256=POST_C8_GATE_BASELINE_SNAPSHOT_SHA256,
            evaluated_at=evaluated_at,
        )
        local_gate_report = audit_local_gate(
            first,
            base_file_canonical_sha256,
        )

        bad_identity_blocked = True
        for mutation in (
            {"source_commit_sha40": "0" * 40},
            {"base_archive_sha256": "0" * 64},
            {"baseline_snapshot_sha256": "0" * 64},
        ):
            values = {
                "source_commit_sha40": POST_C8_GATE_BASE_COMMIT_SHA40,
                "base_archive_sha256": POST_C8_GATE_BASE_ARCHIVE_SHA256,
                "baseline_snapshot_sha256": POST_C8_GATE_BASELINE_SNAPSHOT_SHA256,
                "evaluated_at": evaluated_at,
            }
            values.update(mutation)
            try:
                assess_post_c8_gate(**values)
            except PostC8GateError:
                continue
            bad_identity_blocked = False

        live_mutations_blocked = True
        for mutation in (
            {"production_authorized": True},
            {"authorization_created": True},
            {"routes_allowed": True},
            {"workers_allowed": True},
            {"provider_contact_allowed": True},
            {"network_allowed": True},
            {"secret_access_allowed": True},
            {"database_access_allowed": True},
            {"database_ddl_allowed": True},
            {"database_dml_allowed": True},
            {"real_data_allowed": True},
            {"external_effects_allowed": True},
            {"live_activation_allowed": True},
            {"production_effects_available": True},
            {"production_safe": True},
            {"approval_matrix_satisfied": True},
            {"authority_chain_satisfied": True},
            {"evidence_freshness_satisfied": True},
            {"revocation_status_verified": True},
            {"required_approval_roles": POST_C8_GATE_REQUIRED_APPROVAL_ROLES[:-1]},
            {"live_canary_percent": 1},
            {"c8_dry_run_is_authentic_e4": True},
            {"gate_status": "cleared"},
            {"live_verdict": "go"},
        ):
            try:
                replace(first, **mutation)
            except PostC8GateError:
                continue
            live_mutations_blocked = False

        frozen = False
        try:
            first.live_verdict = "go"  # type: ignore[misc]
        except FrozenInstanceError:
            frozen = True

        guard_blocked = False
        try:
            assert_g0_live_activation_unavailable(assessment=first)
        except PostC8LiveActivationUnavailable as exc:
            guard_blocked = exc.code == "g0_live_activation_unavailable"

        report["archive"] = archive_report
        report["local_gate"] = local_gate_report
        report["contract_version"] = POST_C8_GATE_CONTRACT_VERSION
        report["evaluated_at"] = first.evaluated_at
        report["fingerprint_material"] = post_c8_gate_fingerprint_material(first)
        report["gate_status"] = first.gate_status
        report["findings_total"] = len(first.findings)
        report["gate_blockers_total"] = sum(
            len(item.blocker_codes) for item in first.findings
        ) + len(first.activation_blockers)
        report["assessment_sha256"] = post_c8_gate_sha256(first)
        report["checks"] = {
            "exact_archive_audited_without_filesystem_extraction": bool(
                archive_report["crc_ok"] and archive_report["safe_members"]
            ),
            "base_worktree_and_evidence_manifest_exact": bool(
                local_gate_report["frozen_base_tree_exact"]
                and local_gate_report["evidence_manifest_exact"]
            ),
            "isolated_no_site_no_bytecode_interpreter": _interpreter_isolated(),
            "six_domains_are_fixed_and_blocked": (
                len(first.findings) == 6
                and all(item.status == "blocked" for item in first.findings)
            ),
            "assessment_is_deterministic": (
                post_c8_gate_sha256(first) == post_c8_gate_sha256(second)
            ),
            "wrong_commit_archive_or_snapshot_blocked": bad_identity_blocked,
            "assessment_is_immutable": frozen,
            "all_live_and_effect_mutations_blocked": live_mutations_blocked,
            "live_activation_guard_unconditional": guard_blocked,
            "live_verdict_remains_no_go": first.live_verdict == "no_go",
            "gate_never_cleared": first.gate_status == "blocked",
            "live_canary_exactly_zero": first.live_canary_percent == 0,
            "c8_dry_run_not_authentic_e4": not first.c8_dry_run_is_authentic_e4,
            "no_runtime_surface_allowed": not any((
                first.routes_allowed,
                first.workers_allowed,
                first.provider_contact_allowed,
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
        report["gate_blockers"] = [
            code
            for finding in first.findings
            for code in finding.blocker_codes
        ] + list(first.activation_blockers)
        report["blockers"] = list(report["gate_blockers"])
        report["ok"] = False
        report["safe"] = False
        report["production_safe"] = False
        _print(report, args.compact)
        return 2
    except (OSError, ValueError) as exc:
        report["blockers"] = [f"post_c8_g0_smoke_blocked:{type(exc).__name__}:{exc}"]
        _print(report, args.compact)
        return 2
    except Exception as exc:
        report["blockers"] = [f"unexpected:{type(exc).__name__}:{exc}"]
        _print(report, args.compact)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
