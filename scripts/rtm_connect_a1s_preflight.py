#!/usr/bin/env python3
"""Preflight offline, read-only y fail-closed de RTM CONNECT A1S.

A1S modela el trabajo humano de preparacion de una presentacion solo con
fixtures sinteticas. Este script no extrae el ZIP, no abre la base de datos,
no resuelve secretos y no permite red, B2, B2B, proveedor o Administracion.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import stat
import sys
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]

A1S_PREFLIGHT_VERSION = "rtm_connect_a1s_preflight_v1_0"
A1S_BASE_COMMIT_SHA40 = "b0bc7ddfad9278e601dce8dd69083472662874b5"
A1S_BASE_ARCHIVE_SHA256 = (
    "4b32167288e41be2c8b556bde49149390181f8f918c3a4a864020b269493825e"
)
EXPECTED_ZIP_ENTRIES = 542
EXPECTED_ZIP_FILES = 523
EXPECTED_ZIP_UNCOMPRESSED_BYTES = 6_536_141
EXPECTED_ZIP_COMPRESSED_BYTES = 1_653_730
EXPECTED_ZIP_MAX_FILE_BYTES = 174_062

A1S_RUNTIME_PATHS = (
    "rtm_connect/human_filing_contracts.py",
    "rtm_connect/human_filing_policy.py",
    "rtm_connect/human_filing_schema.py",
    "rtm_connect/human_filing_repository.py",
    "rtm_connect/human_filing_service.py",
    "rtm_connect/human_filing_router.py",
)
A1S_CONTROL_PATHS = (
    "scripts/rtm_staging_connect_a1s_schema.py",
    "scripts/rtm_connect_a1s_preflight.py",
    "scripts/rtm_connect_a1s_smoke.py",
    "docs/rtm_connect/RTM_CONNECT_A1S_HUMAN_FILING.md",
    "docs/rtm_connect/adrs/0018-a1s-human-filing.md",
)
A1S_NEW_TEST_PATHS = (
    "tests/test_rtm_connect_a1s_contracts_contract.py",
    "tests/test_rtm_connect_a1s_policy_contract.py",
    "tests/test_rtm_connect_a1s_schema_contract.py",
    "tests/test_rtm_connect_a1s_routes_contract.py",
    "tests/test_rtm_connect_a1s_scripts_contract.py",
    "tests/test_rtm_connect_a1s_docs_contract.py",
)
A1S_SUCCESSOR_TEST_PATHS = (
    "tests/test_rtm_connect_c7_scripts_contract.py",
    "tests/test_rtm_connect_c8_scripts_contract.py",
    "tests/test_rtm_connect_post_c8_g1_docs_contract.py",
)
A1S_TEST_PATHS = A1S_NEW_TEST_PATHS + A1S_SUCCESSOR_TEST_PATHS
A1S_SUCCESSOR_PATHS = ("app.py",) + A1S_SUCCESSOR_TEST_PATHS
A1S_NEW_PATHS = A1S_RUNTIME_PATHS + A1S_CONTROL_PATHS + A1S_NEW_TEST_PATHS
A1S_OVERLAY_PATHS = A1S_NEW_PATHS + A1S_SUCCESSOR_PATHS
A1S_LOCAL_PATHS = A1S_OVERLAY_PATHS

_FORBIDDEN_IMPORT_ROOTS = frozenset({
    "boto3",
    "botocore",
    "b2_storage",
    "dgt_client",
    "ftplib",
    "httpx",
    "paramiko",
    "requests",
    "smtplib",
    "socket",
    "subprocess",
    "submitter_dgt",
})
_FORBIDDEN_IMPORT_MODULES = frozenset({
    "rtm_b2_storage",
    "http.client",
    "rtm_connect.provider_transport",
    "rtm_connect.production_transport",
    "submitters.registro",
    "urllib.request",
})
_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


class A1SPreflightError(ValueError):
    """El artefacto o el overlay no pertenece a la frontera A1S."""


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


def _stream_sha256(handle: Any) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _safe_member(info: zipfile.ZipInfo) -> bool:
    original = getattr(info, "orig_filename", info.filename)
    name = info.filename
    if (
        not name
        or not original
        or "\\" in name
        or "\\" in original
        or "\x00" in name
        or "\x00" in original
    ):
        return False
    pure = PurePosixPath(name)
    if (
        pure.is_absolute()
        or (pure.parts and pure.parts[0].endswith(":"))
        or any(part in {"", ".", ".."} for part in pure.parts)
        or any(":" in part or part.endswith((" ", ".")) for part in pure.parts)
        or any(
            part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
            for part in pure.parts
        )
    ):
        return False
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_IFMT(mode) in {0, stat.S_IFREG, stat.S_IFDIR}


def audit_archive(path: Path) -> dict[str, Any]:
    """Verifica el ZIP exacto sin extraer ni importar ninguno de sus miembros."""

    if not path.is_file():
        raise A1SPreflightError("archive_not_found")
    with path.open("rb") as handle:
        actual_sha256 = _stream_sha256(handle)
        if actual_sha256 != A1S_BASE_ARCHIVE_SHA256:
            raise A1SPreflightError("archive_sha256_mismatch")
        handle.seek(0)
        try:
            archive = zipfile.ZipFile(handle, "r")
        except zipfile.BadZipFile as exc:
            raise A1SPreflightError("archive_invalid_zip") from exc
        with archive:
            try:
                comment = archive.comment.decode("ascii", errors="strict")
            except UnicodeDecodeError as exc:
                raise A1SPreflightError("archive_comment_not_ascii") from exc
            if comment != A1S_BASE_COMMIT_SHA40:
                raise A1SPreflightError("archive_commit_comment_mismatch")
            infos = archive.infolist()
            files = [info for info in infos if not info.is_dir()]
            if len(infos) != EXPECTED_ZIP_ENTRIES:
                raise A1SPreflightError("archive_entry_count_mismatch")
            if len(files) != EXPECTED_ZIP_FILES:
                raise A1SPreflightError("archive_file_count_mismatch")
            if sum(info.file_size for info in infos) != EXPECTED_ZIP_UNCOMPRESSED_BYTES:
                raise A1SPreflightError("archive_uncompressed_size_mismatch")
            if sum(info.compress_size for info in infos) != EXPECTED_ZIP_COMPRESSED_BYTES:
                raise A1SPreflightError("archive_compressed_size_mismatch")
            if max(info.file_size for info in files) != EXPECTED_ZIP_MAX_FILE_BYTES:
                raise A1SPreflightError("archive_max_file_size_mismatch")
            if any(not _safe_member(info) for info in infos):
                raise A1SPreflightError("archive_unsafe_member")
            folded = [
                unicodedata.normalize("NFKC", info.filename).casefold()
                for info in infos
            ]
            if len(folded) != len(set(folded)):
                raise A1SPreflightError("archive_casefold_duplicate")
            bad_crc = archive.testzip()
            if bad_crc is not None:
                raise A1SPreflightError(f"archive_crc_failed:{bad_crc}")
            names = {info.filename for info in infos}
            contaminated = sorted(set(A1S_NEW_PATHS) & names)
            if contaminated:
                raise A1SPreflightError(
                    "a1s_overlay_present_in_frozen_base:" + ",".join(contaminated)
                )
            missing_successors = sorted(set(A1S_SUCCESSOR_PATHS) - names)
            if missing_successors:
                raise A1SPreflightError(
                    "base_successor_paths_missing:" + ",".join(missing_successors)
                )
            unchanged_successors: list[str] = []
            for relative in A1S_SUCCESSOR_PATHS:
                try:
                    base_text = archive.read(relative).decode("utf-8")
                    local_text = (ROOT / relative).read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    raise A1SPreflightError(
                        f"successor_assertion_unreadable:{relative}"
                    ) from exc
                canonical_base = base_text.replace("\r\n", "\n").replace("\r", "\n")
                canonical_local = local_text.replace("\r\n", "\n").replace("\r", "\n")
                if canonical_base == canonical_local:
                    unchanged_successors.append(relative)
            if unchanged_successors:
                raise A1SPreflightError(
                    "successor_assertion_not_overlaid:" + ",".join(unchanged_successors)
                )
    return {
        "archive_commit_comment": comment,
        "archive_sha256": actual_sha256,
        "entries": len(infos),
        "files": len(files),
        "uncompressed_bytes": sum(info.file_size for info in infos),
        "compressed_bytes": sum(info.compress_size for info in infos),
        "max_file_bytes": max(info.file_size for info in files),
        "safe_members": True,
        "casefold_duplicates": 0,
        "crc_ok": True,
        "a1s_overlay_absent_from_base_archive": True,
        "successor_assertions_absent_from_base_archive": True,
        "successor_paths_verified": len(A1S_SUCCESSOR_PATHS),
    }


def _import_names(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def audit_local_overlay(root: Path = ROOT) -> dict[str, Any]:
    """Compila el overlay y rechaza transports/red/B2 en el runtime A1S."""

    missing = sorted(path for path in A1S_LOCAL_PATHS if not (root / path).is_file())
    invalid_python: list[str] = []
    forbidden_imports: list[str] = []
    for relative in A1S_RUNTIME_PATHS:
        path = root / relative
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, UnicodeError, SyntaxError) as exc:
            invalid_python.append(f"{relative}:{type(exc).__name__}")
            continue
        for module in sorted(_import_names(tree)):
            root_name = module.split(".", 1)[0]
            if root_name in _FORBIDDEN_IMPORT_ROOTS or module in _FORBIDDEN_IMPORT_MODULES:
                forbidden_imports.append(f"{relative}:{module}")
    app_source = ""
    try:
        app_source = (root / "app.py").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        pass
    app_runtime_wiring_present = all(
        marker in app_source
        for marker in ("human_filing_router", "human_filing_gate_middleware")
    )
    if not app_runtime_wiring_present and "app.py" not in missing:
        invalid_python.append("app.py:a1s_runtime_wiring_missing")
    sources: dict[str, str] = {}
    for relative in A1S_RUNTIME_PATHS:
        try:
            sources[relative] = (root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            sources[relative] = ""
    contracts = sources.get("rtm_connect/human_filing_contracts.py", "")
    policy = sources.get("rtm_connect/human_filing_policy.py", "")
    repository = sources.get("rtm_connect/human_filing_repository.py", "")
    router_source = sources.get("rtm_connect/human_filing_router.py", "")
    schema = sources.get("rtm_connect/human_filing_schema.py", "")
    service = sources.get("rtm_connect/human_filing_service.py", "")
    contract_checks = {
        "synthetic_human_filing_contract_present": all(
            marker in contracts + policy
            for marker in ("RTM_A1S_SYNTHETIC_ONLY", "synthetic_only")
        ),
        "feature_gate_contract_present": (
            "RTM_ENABLE_CONNECT_A1S_HUMAN_FILING" in policy
            and "human_filing_gate_middleware" in router_source
        ),
        "individual_operator_session_contract_present": all(
            marker in router_source
            for marker in ("extract_bearer_token", "load_operator_session")
        ),
        "tenant_case_scope_contract_present": all(
            marker in repository
            for marker in (
                "load_active_membership", "load_case_scope",
                "assert_frozen_case_document_hashes",
            )
        ),
        "representation_evidence_contract_present": (
            "HumanFilingRepresentationEvidence" in contracts
            and "representation_evidence_id" in service
        ),
        "canonical_package_and_artifact_contract_present": all(
            marker in repository
            for marker in (
                "a1s_package_sha256_must_match_canonical_manifest",
                "a1s_artifact_sha256_must_match_canonical_payload",
            )
        ),
        "append_only_ledger_contract_present": all(
            marker in schema
            for marker in (
                "approval_append_only", "event_append_only",
                "artifact_append_only",
            )
        ),
        "unknown_reconciliation_contract_present": all(
            marker in contracts + service
            for marker in (
                "outcome_unknown", "reconciling", "blind_retry_allowed",
            )
        ),
    }
    missing_contract_checks = sorted(
        name for name, present in contract_checks.items() if not present
    )
    return {
        "required_paths": len(A1S_LOCAL_PATHS),
        "present_paths": len(A1S_LOCAL_PATHS) - len(missing),
        "missing_paths": missing,
        "invalid_python": invalid_python,
        "forbidden_imports": forbidden_imports,
        "network_transport_absent": not forbidden_imports,
        "app_runtime_wiring_present": app_runtime_wiring_present,
        "contract_checks": contract_checks,
        "missing_contract_checks": missing_contract_checks,
        "complete": not (
            missing or invalid_python or forbidden_imports
            or missing_contract_checks
        ),
    }


def build_report(archive_path: Path) -> dict[str, Any]:
    """Construye el veredicto A1S sin producir efectos externos."""

    blockers: list[str] = []
    archive: dict[str, Any] | None = None
    try:
        archive = audit_archive(archive_path)
    except Exception as exc:
        blockers.append(f"a1s_archive_blocked:{type(exc).__name__}:{exc}")
    overlay = audit_local_overlay()
    blockers.extend(f"missing_overlay_path:{path}" for path in overlay["missing_paths"])
    blockers.extend(f"invalid_overlay_python:{item}" for item in overlay["invalid_python"])
    blockers.extend(f"forbidden_runtime_import:{item}" for item in overlay["forbidden_imports"])
    blockers.extend(
        f"missing_contract_check:{item}"
        for item in overlay["missing_contract_checks"]
    )
    audit_ok = archive is not None and not blockers
    return {
        "ok": audit_ok,
        "safe": audit_ok,
        "audit_ok": audit_ok,
        "authority": "rtm_connect_a1s_preflight",
        "version": A1S_PREFLIGHT_VERSION,
        "contract_version": "rtm.connect.a1s.human_filing.v1",
        "base_commit_sha40": A1S_BASE_COMMIT_SHA40,
        "base_archive_sha256": A1S_BASE_ARCHIVE_SHA256,
        "archive": archive,
        "overlay": overlay,
        "blockers": blockers,
        "checks": {
            "exact_archive_sha256": archive is not None,
            "exact_commit_comment": archive is not None,
            "archive_crc_valid": bool(archive and archive["crc_ok"]),
            "archive_members_safe": bool(archive and archive["safe_members"]),
            "a1s_overlay_absent_from_base_archive": bool(
                archive and archive["a1s_overlay_absent_from_base_archive"]
            ),
            "successor_assertions_absent_from_base_archive": bool(
                archive and archive["successor_assertions_absent_from_base_archive"]
            ),
            "a1s_overlay_complete": overlay["complete"],
            "app_runtime_wiring_present": overlay["app_runtime_wiring_present"],
            **overlay["contract_checks"],
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
        "schema_changes_required": True,
        "secret_resolution_performed": False,
        "routes_published": False,
        "workers_started": False,
        "external_effects_executed": False,
        "scope_limitations": [
            "a1s_accepts_synthetic_fixtures_only",
            "a1s_does_not_contact_a_provider_or_administration",
            "a1s_does_not_upload_to_b2_or_enable_b2b",
            "a1s_does_not_authorize_real_customer_or_case_data",
            "a1s_schema_does_not_provision_operators_tenants_cases_or_fixtures",
            "a1s_requires_separate_audited_fixture_provisioning",
            "a1s_prepare_opens_core_attempt_before_human_assignment_and_release",
            "postgres_does_not_recompute_python_canonical_sha256",
            "fastapi_pre_context_errors_use_detail_envelope",
            "preflight_does_not_execute_postgresql_constraints_or_workflow",
            "synthetic_witnessed_timestamps_are_not_provider_trusted_time",
            "archive_hash_and_comment_do_not_prove_commit_authorship",
            "g0_and_g1_no_go_decisions_remain_authoritative_for_live_filing",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = build_report(Path(args.archive).resolve())
    _print(report, args.compact)
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
