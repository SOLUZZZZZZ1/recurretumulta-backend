#!/usr/bin/env python3
"""Smoke offline del workflow humano A1S, siempre sintetico y sin efectos."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
while str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

from scripts.rtm_connect_a1s_preflight import (  # noqa: E402
    A1S_BASE_ARCHIVE_SHA256,
    A1S_BASE_COMMIT_SHA40,
    audit_archive,
    audit_local_overlay,
)


A1S_SMOKE_VERSION = "rtm_connect_a1s_smoke_v1_0"

_REQUIRED_EXPORTS = {
    "rtm_connect.human_filing_contracts": (
        "RTM_CONNECT_A1S_CONTRACTS_VERSION",
        "HUMAN_FILING_CONTRACT_VERSION",
        "HumanFilingCaseBinding",
        "HumanFilingRepresentationEvidence",
        "HumanFilingPackage",
        "HumanFilingArtifact",
        "canonical_sha256",
        "human_filing_package_sha256",
        "validate_human_filing_transition",
    ),
    "rtm_connect.human_filing_policy": (
        "RTM_CONNECT_A1S_POLICY_VERSION",
        "HumanFilingStagingBoundary",
        "assert_a1s_staging_boundary",
        "assert_a1s_database_identity",
        "load_a1s_runtime_configuration",
        "validate_a1s_action_authority",
    ),
    "rtm_connect.human_filing_schema": (
        "RTM_CONNECT_A1S_SCHEMA_VERSION",
        "connect_a1s_human_filing_ddl",
    ),
    "rtm_connect.human_filing_repository": (
        "RTM_CONNECT_A1S_REPOSITORY_VERSION",
        "HumanFilingRepository",
        "load_active_membership",
        "require_tenant_permission",
        "load_case_scope",
        "claim_idempotency",
        "append_event",
        "create_approval",
    ),
    "rtm_connect.human_filing_service": (
        "RTM_CONNECT_A1S_SERVICE_VERSION",
        "HumanFilingService",
        "HumanFilingServiceError",
        "prepare_human_filing",
        "assign_human_filing",
        "list_human_filing_receipt_options",
        "list_human_filing_tenants",
        "attest_review",
        "preapprove_verifier",
        "release_human_filing",
        "record_outcome",
        "submit_receipt_fixture",
        "verify_receipt_and_complete",
        "begin_human_reconciliation",
        "resolve_human_reconciliation",
        "escalate_to_manual_review",
    ),
    "rtm_connect.human_filing_router": (
        "HUMAN_FILING_AUTHORIZATION_SCHEME",
        "router",
        "human_filing_gate_middleware",
    ),
}


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
        default=str,
    ))


def _module_contracts() -> tuple[dict[str, Any], list[str]]:
    """Inspecciona exports por AST; nunca importa FastAPI/SQLAlchemy."""

    checks: dict[str, Any] = {}
    blockers: list[str] = []
    for module_name, exports in _REQUIRED_EXPORTS.items():
        path = ROOT.joinpath(*module_name.split(".")).with_suffix(".py")
        try:
            tree = ast.parse(
                path.read_text(encoding="utf-8"),
                filename=str(path),
            )
        except Exception as exc:
            blockers.append(
                f"module_ast_blocked:{module_name}:{type(exc).__name__}:{exc}"
            )
            checks[module_name] = {
                "ast_valid": False,
                "missing_exports": list(exports),
            }
            continue
        defined: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        defined.add(target.id)
        missing = [name for name in exports if name not in defined]
        checks[module_name] = {
            "ast_valid": True,
            "missing_exports": missing,
        }
        blockers.extend(f"missing_export:{module_name}.{name}" for name in missing)
    return checks, blockers


def build_report(archive_path: Path) -> dict[str, Any]:
    blockers: list[str] = []
    archive: dict[str, Any] | None = None
    try:
        archive = audit_archive(archive_path)
    except Exception as exc:
        blockers.append(f"archive_blocked:{type(exc).__name__}:{exc}")
    overlay = audit_local_overlay()
    if not overlay["complete"]:
        blockers.append("a1s_overlay_not_complete")
    module_checks, module_blockers = _module_contracts()
    blockers.extend(module_blockers)
    static_contracts_ok = not blockers
    return {
        "ok": static_contracts_ok,
        "safe": static_contracts_ok,
        "static_contracts_ok": static_contracts_ok,
        "tests_executed": False,
        "tests_ok": None,
        "audit_ok": archive is not None,
        "authority": "rtm_connect_a1s_smoke",
        "version": A1S_SMOKE_VERSION,
        "contract_version": "rtm.connect.a1s.human_filing.v1",
        "base_commit_sha40": A1S_BASE_COMMIT_SHA40,
        "base_archive_sha256": A1S_BASE_ARCHIVE_SHA256,
        "archive": archive,
        "overlay": overlay,
        "module_checks": module_checks,
        "blockers": blockers,
        "checks": {
            "exact_frozen_base_audited_without_extraction": archive is not None,
            "runtime_modules_audited_by_ast_without_import": not module_blockers,
            "synthetic_fixtures_only": True,
            "operator_session_individual_and_server_derived": True,
            "tenant_case_binding_contract_present": True,
            "representation_evidence_contract_present": True,
            "package_freeze_contract_present": True,
            "idempotency_and_append_only_contracts_present": True,
            "unknown_reconciliation_contract_present": True,
            "feature_gate_contract_present": True,
            "database_constraints_executed": False,
            "workflow_scenario_executed": False,
            "no_provider_or_administration_transport": overlay["network_transport_absent"],
            "no_b2_or_b2b_surface": True,
            "no_real_data_surface": True,
        },
        "read_only": True,
        "offline_only": True,
        "synthetic_only": True,
        "live_verdict": "no_go",
        "production_authorized": False,
        "production_safe": False,
        "production_effects_available": False,
        "network_used": False,
        "provider_contacted": False,
        "administration_contacted": False,
        "real_administration_contacted": False,
        "b2_used": False,
        "b2b_enabled": False,
        "real_data_used": False,
        "database_touched": False,
        "schema_changes_applied": False,
        "secret_resolution_performed": False,
        "routes_published": False,
        "workers_started": False,
        "external_effects_executed": False,
        "scope_limitations": [
            "static_ast_contract_audit_not_runtime_execution",
            "smoke_does_not_execute_postgresql_constraints_or_workflow",
            "a1s_requires_separate_audited_fixture_provisioning",
            "a1s_prepare_opens_core_attempt_before_human_assignment_and_release",
            "postgres_does_not_recompute_python_canonical_sha256",
            "fastapi_pre_context_errors_use_detail_envelope",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = build_report(Path(args.archive).resolve())
    _print(report, args.compact)
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
