#!/usr/bin/env python3
"""Auditor final, offline y fail-closed de la evidencia A1-S Runtime.

Este preflight toma como sujeto la entrega final exacta ``9e0a267``. No
importa el runtime, no extrae el archivo, no abre PostgreSQL, no resuelve
secretos y no ejecuta red. Su cometido es separar tres decisiones:

* la ejecucion sintetica de staging ya observada;
* la admision de esta entrega documental, que solo pasa si este auditor pasa;
* la produccion y cualquier presentacion real, que permanecen bloqueadas.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import stat
import sys
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]

A1S_RUNTIME_EVIDENCE_PREFLIGHT_VERSION = (
    "rtm_connect_a1s_runtime_evidence_preflight_v1_0"
)
A1S_RUNTIME_EVIDENCE_CONTRACT_VERSION = (
    "rtm.connect.a1s.runtime.evidence.v1"
)
FINAL_BASE_COMMIT_SHA40 = "9e0a26777f19efeb2c54b093e771570493a3de0e"
FINAL_BASE_ARCHIVE_SHA256 = (
    "038e28a14262d8029d95a86d71f06780f239d0aa144fb25207d9a4afc534684e"
)
FINAL_BASE_ARCHIVE_NAME = "RTM_CONNECT_A1S_RUNTIME_EVIDENCE_BASE_9e0a267.zip"
EXPECTED_ZIP_ENTRIES = 571
EXPECTED_ZIP_FILES = 552
EXPECTED_ZIP_UNCOMPRESSED_BYTES = 7_189_195
EXPECTED_ZIP_COMPRESSED_BYTES = 1_789_936
EXPECTED_ZIP_MAX_FILE_BYTES = 174_062

ORIGINAL_RUNTIME_BASE_COMMIT_SHA40 = (
    "a94dcd314c67880e40aa333dc679ef98b80a1956"
)
ORIGINAL_RUNTIME_BASE_ARCHIVE_SHA256 = (
    "4bed25e3fd30989a617ad3640a63e4a20d98f5347167cf097f91e9d183220c21"
)

CLOSURE_MODIFIED_PATHS = (
    "docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME.md",
    "docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME_EVIDENCE.json",
    "docs/rtm_connect/adrs/0019-a1s-runtime-validation.md",
    "tests/test_rtm_connect_a1s_runtime_docs_contract.py",
)
CLOSURE_NEW_PATHS = (
    "scripts/rtm_connect_a1s_runtime_evidence_preflight.py",
    "tests/test_rtm_connect_a1s_runtime_evidence_preflight_contract.py",
)
CLOSURE_PATHS = CLOSURE_MODIFIED_PATHS + CLOSURE_NEW_PATHS

FINAL_SMOKE_CHECKS = frozenset({
    "completed_visible_through_read_api",
    "e4_exactly_bound_to_preapproved_verifier",
    "feature_closes_again_without_restart",
    "feature_default_off_returns_404",
    "fresh_connection_observes_baseline_restored_and_ephemeral_zero_residue",
    "full_http_state_machine_completed",
    "full_http_unknown_reconciliation_branch",
    "individual_bearer_session_required",
    "package_and_receipt_hashes_disjoint",
    "persistent_fixture_read_only_audited",
    "postgresql_final_state_completed",
    "postgresql_schema_ready",
    "prepare_idempotency_replayed",
    "sessions_store_only_sha256",
    "single_hash_only_receipt_fixture",
    "single_preparation_candidate",
    "temporary_runtime_flags_restored",
    "tenant_bootstrap_scoped",
    "three_distinct_tenant_participants",
    "transaction_clock_coherent",
    "transaction_contains_complete_fixture_graph",
    "two_preoperation_principals_distinct",
    "unknown_branch_closes_manual_review",
    "unknown_branch_never_blind_retries",
    "unknown_fixture_transactionally_provisioned",
    "unknown_manual_review_visible_through_read_api",
    "unknown_preoperation_principals_distinct",
    "zero_external_socket_attempts",
})

EVIDENCE_PATH = (
    "docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME_EVIDENCE.json"
)
GATE_PATH = "docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME.md"
ADR_PATH = "docs/rtm_connect/adrs/0019-a1s-runtime-validation.md"

_IGNORED_DIRECTORIES = frozenset({
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
})
_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_FORBIDDEN_IMPORT_ROOTS = frozenset({
    "aiohttp",
    "app",
    "b2_storage",
    "boto3",
    "botocore",
    "database",
    "ftplib",
    "httpx",
    "paramiko",
    "psycopg",
    "psycopg2",
    "requests",
    "rtm_connect",
    "smtplib",
    "socket",
    "sqlalchemy",
    "ssl",
    "subprocess",
    "urllib",
    "websockets",
})
_FORBIDDEN_CALL_NAMES = frozenset({
    "HTTPConnection",
    "HTTPSConnection",
    "Popen",
    "check_call",
    "check_output",
    "connect",
    "create_connection",
    "run",
    "urlopen",
    "urlretrieve",
})


class A1SRuntimeEvidencePreflightError(ValueError):
    """La entrega final de evidencia no satisface el contrato cerrado."""


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


def _canonical_text_sha256(raw: bytes, name: str) -> str:
    """Normaliza CRLF/CR solo para texto UTF-8 estricto sin controles."""

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise A1SRuntimeEvidencePreflightError(
            f"utf8_required:{name}"
        ) from exc
    allowed_controls = {"\t", "\n", "\r"}
    if any(
        unicodedata.category(character) == "Cc"
        and character not in allowed_controls
        for character in text
    ):
        raise A1SRuntimeEvidencePreflightError(
            f"text_control_forbidden:{name}"
        )
    normalized = text.replace("\r\n", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _canonical_member_sha256(raw: bytes, name: str) -> str:
    """Normaliza texto portable; conserva binarios byte a byte."""

    try:
        return _canonical_text_sha256(raw, name)
    except A1SRuntimeEvidencePreflightError:
        return hashlib.sha256(raw).hexdigest()


def _snapshot_sha256(mapping: dict[str, str]) -> str:
    canonical = json.dumps(mapping, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    parts = tuple(unicodedata.normalize("NFKC", part) for part in pure.parts)
    if (
        pure.is_absolute()
        or (parts and parts[0].endswith(":"))
        or any(part in {"", ".", ".."} for part in parts)
        or any(":" in part or part.endswith((" ", ".")) for part in parts)
        or any(
            part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
            for part in parts
        )
    ):
        return False
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_IFMT(mode) in {0, stat.S_IFREG, stat.S_IFDIR}


def _decode_member(archive: zipfile.ZipFile, name: str) -> str:
    try:
        return archive.read(name).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise A1SRuntimeEvidencePreflightError(
            f"archive_utf8_required:{name}"
        ) from exc


def _audit_final_hotfixes(archive: zipfile.ZipFile) -> dict[str, Any]:
    repository = _decode_member(
        archive, "rtm_connect/human_filing_repository.py"
    )
    service = _decode_member(
        archive, "rtm_connect/human_filing_service.py"
    )
    smoke = _decode_member(
        archive, "scripts/rtm_connect_a1s_runtime_smoke.py"
    )
    routes_test = _decode_member(
        archive, "tests/test_rtm_connect_a1s_routes_contract.py"
    )

    jsonb_parameterized = (
        "@> CAST(:test_mode_metadata AS JSONB)" in repository
        and "@> CAST(:synthetic_metadata AS JSONB)" in repository
        and "b.metadata @> '{\"test_mode\":true}'::jsonb" not in repository
    )
    transactional_clock = (
        'text("SELECT transaction_timestamp()")' in smoke
        and 'report["checks"]["transaction_clock_coherent"]' in smoke
    )
    release_ids_canonical = all(
        marker in service
        for marker in (
            '"release_approval_id": str(',
            'approvals["release"]["id"]',
            '"verification_preapproval_id": str(',
            'approvals["verification_preapproval"]["id"]',
        )
    ) and "test_release_event_ids_are_canonical_uuid_text" in routes_test

    if not jsonb_parameterized:
        raise A1SRuntimeEvidencePreflightError(
            "final_jsonb_hotfix_signature_missing"
        )
    if not transactional_clock:
        raise A1SRuntimeEvidencePreflightError(
            "final_transaction_clock_hotfix_signature_missing"
        )
    if not release_ids_canonical:
        raise A1SRuntimeEvidencePreflightError(
            "final_release_event_hotfix_signature_missing"
        )
    return {
        "jsonb_predicates_parameterized": True,
        "transaction_clock_uses_postgresql_transaction_timestamp": True,
        "release_event_ids_canonicalized_to_text": True,
        "three_required_hotfix_code_signatures_present": True,
    }


def audit_archive(path: Path) -> dict[str, Any]:
    """Audita en memoria el ZIP final exacto y sus tres hotfixes."""

    if path.is_symlink() or not path.is_file():
        raise A1SRuntimeEvidencePreflightError(
            "archive_not_found_or_symlink"
        )
    try:
        with path.open("rb") as handle:
            actual_sha256 = _stream_sha256(handle)
            if actual_sha256 != FINAL_BASE_ARCHIVE_SHA256:
                raise A1SRuntimeEvidencePreflightError(
                    "archive_sha256_mismatch"
                )
            handle.seek(0)
            with zipfile.ZipFile(handle, "r") as archive:
                try:
                    comment = archive.comment.decode("ascii", errors="strict")
                except UnicodeDecodeError as exc:
                    raise A1SRuntimeEvidencePreflightError(
                        "archive_comment_not_ascii"
                    ) from exc
                if comment != FINAL_BASE_COMMIT_SHA40:
                    raise A1SRuntimeEvidencePreflightError(
                        "archive_commit_comment_mismatch"
                    )
                infos = archive.infolist()
                files = [info for info in infos if not info.is_dir()]
                if len(infos) != EXPECTED_ZIP_ENTRIES:
                    raise A1SRuntimeEvidencePreflightError(
                        "archive_entry_count_mismatch"
                    )
                if len(files) != EXPECTED_ZIP_FILES:
                    raise A1SRuntimeEvidencePreflightError(
                        "archive_file_count_mismatch"
                    )
                if (
                    sum(info.file_size for info in infos)
                    != EXPECTED_ZIP_UNCOMPRESSED_BYTES
                ):
                    raise A1SRuntimeEvidencePreflightError(
                        "archive_uncompressed_size_mismatch"
                    )
                if (
                    sum(info.compress_size for info in infos)
                    != EXPECTED_ZIP_COMPRESSED_BYTES
                ):
                    raise A1SRuntimeEvidencePreflightError(
                        "archive_compressed_size_mismatch"
                    )
                if (
                    not files
                    or max(info.file_size for info in files)
                    != EXPECTED_ZIP_MAX_FILE_BYTES
                ):
                    raise A1SRuntimeEvidencePreflightError(
                        "archive_max_file_size_mismatch"
                    )
                if any(not _safe_member(info) for info in infos):
                    raise A1SRuntimeEvidencePreflightError(
                        "archive_unsafe_member"
                    )
                folded = [
                    unicodedata.normalize("NFKC", info.filename).casefold()
                    for info in infos
                ]
                if len(folded) != len(set(folded)):
                    raise A1SRuntimeEvidencePreflightError(
                        "archive_casefold_duplicate"
                    )
                bad_crc = archive.testzip()
                if bad_crc is not None:
                    raise A1SRuntimeEvidencePreflightError(
                        f"archive_crc_failed:{bad_crc}"
                    )
                names = {info.filename for info in infos}
                if set(CLOSURE_NEW_PATHS) & names:
                    raise A1SRuntimeEvidencePreflightError(
                        "closure_new_path_present_in_frozen_base"
                    )
                if not set(CLOSURE_MODIFIED_PATHS) <= names:
                    raise A1SRuntimeEvidencePreflightError(
                        "closure_modified_path_missing_from_frozen_base"
                    )

                stale_evidence = json.loads(
                    _decode_member(archive, EVIDENCE_PATH)
                )
                if (
                    stale_evidence.get("status")
                    != "pending_external_execution"
                    or stale_evidence.get("source", {}).get(
                        "base_commit_sha40"
                    ) != ORIGINAL_RUNTIME_BASE_COMMIT_SHA40
                ):
                    raise A1SRuntimeEvidencePreflightError(
                        "frozen_base_evidence_state_unexpected"
                    )

                hotfix_audit = _audit_final_hotfixes(archive)
                base_file_sha256 = {
                    info.filename: hashlib.sha256(
                        archive.read(info.filename)
                    ).hexdigest()
                    for info in files
                }
                base_file_canonical_sha256 = {
                    info.filename: _canonical_member_sha256(
                        archive.read(info.filename), info.filename
                    )
                    for info in files
                }
    except (json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise A1SRuntimeEvidencePreflightError(
            f"archive_invalid:{type(exc).__name__}"
        ) from exc

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
        "closure_new_paths_absent_from_base_archive": True,
        "closure_modified_paths_present_in_base_archive": True,
        "frozen_base_evidence_was_pending": True,
        "full_base_snapshot_sha256": _snapshot_sha256(base_file_sha256),
        "hotfix_audit": hotfix_audit,
        "_base_file_sha256": base_file_sha256,
        "_base_file_canonical_sha256": base_file_canonical_sha256,
    }


def _walk_local_files(root: Path) -> tuple[set[str], list[str]]:
    files: set[str] = set()
    special_entries: list[str] = []
    for folder, directories, filenames in os.walk(root, followlinks=False):
        kept: list[str] = []
        for directory in directories:
            path = Path(folder) / directory
            relative = path.relative_to(root).as_posix()
            if directory in _IGNORED_DIRECTORIES:
                continue
            if path.is_symlink():
                special_entries.append(relative)
                continue
            kept.append(directory)
        directories[:] = kept
        for filename in filenames:
            path = Path(folder) / filename
            if path.suffix == ".pyc" or filename == ".coverage":
                continue
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file():
                special_entries.append(relative)
                continue
            files.add(relative)
    return files, sorted(special_entries)


def audit_local_tree(
    base_file_sha256: dict[str, str],
    base_file_canonical_sha256: dict[str, str],
    root: Path = ROOT,
) -> dict[str, Any]:
    """Compara todo el sujeto final salvo los cuatro documentos de cierre."""

    if len(base_file_sha256) != EXPECTED_ZIP_FILES:
        raise A1SRuntimeEvidencePreflightError(
            "base_inventory_file_count_mismatch"
        )
    if set(base_file_sha256) != set(base_file_canonical_sha256):
        raise A1SRuntimeEvidencePreflightError(
            "base_inventory_canonical_mismatch"
        )

    modified = set(CLOSURE_MODIFIED_PATHS)
    unchanged = sorted(set(base_file_sha256) - modified)
    raw_exact = 0
    canonical_equivalent = 0
    mismatches: list[str] = []
    for relative in unchanged:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise A1SRuntimeEvidencePreflightError(
                f"local_base_file_missing_or_special:{relative}"
            )
        raw = path.read_bytes()
        raw_digest = hashlib.sha256(raw).hexdigest()
        canonical_digest = _canonical_member_sha256(raw, relative)
        if canonical_digest != base_file_canonical_sha256[relative]:
            mismatches.append(relative)
        if raw_digest == base_file_sha256[relative]:
            raw_exact += 1
        else:
            canonical_equivalent += 1
    if mismatches:
        raise A1SRuntimeEvidencePreflightError(
            "local_final_base_content_mismatch:" + ",".join(mismatches)
        )

    closure_digests: dict[str, str] = {}
    changed_existing: list[str] = []
    for relative in CLOSURE_PATHS:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise A1SRuntimeEvidencePreflightError(
                f"closure_path_missing_or_special:{relative}"
            )
        raw = path.read_bytes()
        closure_digests[relative] = hashlib.sha256(raw).hexdigest()
        if relative in modified:
            if (
                _canonical_member_sha256(raw, relative)
                == base_file_canonical_sha256[relative]
            ):
                raise A1SRuntimeEvidencePreflightError(
                    f"closure_modified_path_unchanged:{relative}"
                )
            changed_existing.append(relative)

    local_files, special_entries = _walk_local_files(root)
    allowed = set(base_file_sha256) | set(CLOSURE_NEW_PATHS)
    unexpected = sorted(local_files - allowed)
    missing = sorted(allowed - local_files)
    if unexpected or missing or special_entries:
        raise A1SRuntimeEvidencePreflightError(
            "local_tree_allowlist_mismatch:"
            f"unexpected={','.join(unexpected)};"
            f"missing={','.join(missing)};"
            f"special={','.join(special_entries)}"
        )

    return {
        "base_files_verified": len(unchanged),
        "final_base_content_equivalent_except_closure": True,
        "comparison_mode": "strict_utf8_crlf_to_lf_or_binary_raw_v1",
        "raw_byte_exact_files": raw_exact,
        "newline_canonical_equivalent_files": canonical_equivalent,
        "closure_paths_required": len(CLOSURE_PATHS),
        "closure_paths_present": len(CLOSURE_PATHS),
        "closure_modified_paths_changed": sorted(changed_existing),
        "closure_new_paths_present": sorted(CLOSURE_NEW_PATHS),
        "unexpected_paths": [],
        "special_entries": [],
        "closure_snapshot_sha256": _snapshot_sha256(closure_digests),
    }


def _require(value: bool, code: str) -> None:
    if not value:
        raise A1SRuntimeEvidencePreflightError(code)


def audit_evidence(root: Path = ROOT) -> dict[str, Any]:
    """Valida la separacion entre cierre sintetico y NO-GO real."""

    try:
        evidence = json.loads((root / EVIDENCE_PATH).read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise A1SRuntimeEvidencePreflightError(
            f"evidence_invalid:{type(exc).__name__}"
        ) from exc

    _require(
        evidence.get("authority") == "rtm_connect_a1s_runtime_evidence",
        "evidence_authority_mismatch",
    )
    _require(
        evidence.get("version") == "rtm_connect_a1s_runtime_evidence_v1_1",
        "evidence_version_mismatch",
    )
    _require(
        evidence.get("status") == "completed_synthetic_staging",
        "evidence_status_mismatch",
    )
    _require(
        evidence.get("execution_status") == "completed_synthetic_staging",
        "evidence_execution_status_mismatch",
    )
    _require(
        evidence.get("gate_status") == "passed_synthetic_staging",
        "evidence_gate_status_mismatch",
    )
    _require(
        evidence.get("production_gate_status") == "blocked",
        "evidence_production_gate_status_mismatch",
    )
    _require(
        evidence.get("live_verdict") == "no_go",
        "evidence_live_verdict_mismatch",
    )

    source = evidence.get("source", {})
    _require(
        source.get("final_commit_sha40") == FINAL_BASE_COMMIT_SHA40,
        "evidence_final_commit_mismatch",
    )
    _require(
        source.get("final_base_archive_name") == FINAL_BASE_ARCHIVE_NAME,
        "evidence_final_archive_name_mismatch",
    )
    _require(
        source.get("final_base_archive_sha256")
        == FINAL_BASE_ARCHIVE_SHA256,
        "evidence_final_archive_sha256_mismatch",
    )
    _require(
        source.get("final_base_archive_comment_sha40")
        == FINAL_BASE_COMMIT_SHA40,
        "evidence_final_archive_comment_mismatch",
    )
    _require(
        source.get("base_commit_sha40")
        == ORIGINAL_RUNTIME_BASE_COMMIT_SHA40,
        "evidence_original_base_commit_mismatch",
    )
    _require(
        source.get("base_archive_sha256")
        == ORIGINAL_RUNTIME_BASE_ARCHIVE_SHA256,
        "evidence_original_base_archive_mismatch",
    )
    _require(
        source.get("git_commit_signature_verified") is False,
        "evidence_must_not_claim_git_signature",
    )
    _require(
        source.get("supply_chain_provenance_verified") is False,
        "evidence_must_not_claim_provenance",
    )

    closure_paths = evidence.get("closure_paths", {})
    _require(
        set(closure_paths.values()) == set(CLOSURE_PATHS),
        "evidence_closure_paths_mismatch",
    )

    execution = evidence.get("execution", {})
    true_keys = (
        "render_deployment_live_observed",
        "health_check_observed",
        "postgresql_runtime_audit_executed",
        "runtime_fixture_provisioning_executed",
        "transactional_e2e_executed",
        "three_individual_bearer_sessions_exercised",
        "happy_path_completed",
        "unknown_reconciliation_exercised",
        "database_transaction_rolled_back",
        "rollback_verified",
        "zero_delta_from_baseline_verified",
        "persistent_fixture_baseline_restored",
        "ephemeral_sessions_residue_zero_verified",
        "environment_restored",
        "network_guard_exercised",
    )
    for key in true_keys:
        _require(execution.get(key) is True, f"evidence_execution_false:{key}")
    false_keys = (
        "login_endpoint_exercised",
        "legal_submission_executed",
        "raw_session_tokens_persisted",
        "raw_session_tokens_reported",
    )
    for key in false_keys:
        _require(
            execution.get(key) is False,
            f"evidence_execution_forbidden_true:{key}",
        )
    _require(
        execution.get("network_attempts") == 0,
        "evidence_network_attempts_not_zero",
    )
    _require(
        execution.get("render_deployment_id")
        == "dep-da6qp1p5efls73d4q0kg",
        "evidence_render_deployment_mismatch",
    )
    _require(
        execution.get("health_response") == {"ok": True},
        "evidence_health_response_mismatch",
    )
    _require(
        execution.get("final_smoke", {}).get("raw_report_sha256") is None,
        "evidence_must_not_claim_smoke_report_hash",
    )
    _require(
        execution.get("final_smoke", {}).get("signature_verified") is False,
        "evidence_must_not_claim_smoke_signature",
    )
    _require(
        execution.get("content_level_zero_delta_verified") is False,
        "evidence_must_not_claim_content_level_zero_delta",
    )
    _require(
        execution.get("unknown_fixture_baseline_was_zero_verified") is False,
        "evidence_must_not_claim_unknown_zero_baseline",
    )

    final_smoke = evidence.get("final_smoke", {})
    _require(
        final_smoke.get("authority") == "rtm_connect_a1s_runtime_smoke",
        "final_smoke_authority_mismatch",
    )
    _require(
        final_smoke.get("version") == "rtm_connect_a1s_runtime_smoke_v1_0",
        "final_smoke_version_mismatch",
    )
    _require(
        final_smoke.get("subject_commit_sha40") == FINAL_BASE_COMMIT_SHA40,
        "final_smoke_subject_mismatch",
    )
    for key in (
        "ok",
        "safe",
        "http_in_process_asgi",
        "database_configuration_loaded",
        "database_connection_used",
        "database_touched",
        "database_rolled_back",
        "fixture_baseline_restored",
        "synthetic_only",
    ):
        _require(
            final_smoke.get(key) is True,
            f"final_smoke_required_true:{key}",
        )
    for key in (
        "legal_submission_executed",
        "production_authorized",
        "production_safe",
        "raw_session_tokens_persisted",
        "raw_session_tokens_reported",
        "routes_published",
        "workers_started",
    ):
        _require(
            final_smoke.get(key) is False,
            f"final_smoke_required_false:{key}",
        )
    _require(final_smoke.get("blockers") == [], "final_smoke_blockers_present")
    _require(
        final_smoke.get("live_verdict") == "no_go",
        "final_smoke_live_verdict_mismatch",
    )
    smoke_checks = final_smoke.get("checks", {})
    _require(
        set(smoke_checks) == FINAL_SMOKE_CHECKS,
        "final_smoke_check_inventory_mismatch",
    )
    _require(
        all(value is True for value in smoke_checks.values()),
        "final_smoke_check_not_true",
    )
    _require(
        final_smoke.get("cleanup")
        == {
            "database_rolled_back": True,
            "ephemeral_sessions_remaining": 0,
            "fixture_snapshots_equal_to_baselines": True,
        },
        "final_smoke_cleanup_mismatch",
    )

    tests = evidence.get("tests", {})
    _require(
        tests.get("subject_commit_sha40") == FINAL_BASE_COMMIT_SHA40,
        "test_evidence_subject_mismatch",
    )
    _require(
        tests.get("executed") is True and tests.get("ok") is True,
        "test_evidence_not_green",
    )
    _require(
        tests.get("suites")
        == [
            {
                "pattern": "test_rtm_connect_a1s_*.py",
                "ran": 114,
                "status": "ok",
            },
            {
                "pattern": "test_rtm_connect_*.py",
                "ran": 643,
                "status": "ok",
            },
            {
                "pattern": "test_*.py",
                "ran": 1227,
                "skipped": 8,
                "status": "ok",
            },
        ],
        "test_evidence_suite_mismatch",
    )
    _require(
        evidence.get("closure_blockers") == [],
        "closure_blockers_not_empty",
    )
    _require(
        str(evidence.get("next_step", "")).startswith(
            "commit_only_after_exact_final_delivery_preflight_passes"
        ),
        "evidence_next_step_mismatch",
    )

    scope = evidence.get("scope", {})
    _require(scope.get("staging_only") is True, "scope_staging_required")
    _require(scope.get("synthetic_only") is True, "scope_synthetic_required")
    for key in (
        "real_data_allowed",
        "real_data_used",
        "provider_network_allowed",
        "provider_network_used",
        "administration_network_allowed",
        "administration_network_used",
        "provider_contacted",
        "administration_contacted",
        "b2_allowed",
        "b2_used",
        "b2b_enabled",
        "workers_allowed",
        "workers_started",
        "external_effects_allowed",
        "external_effects_executed",
        "production_authorized",
        "production_safe",
        "live_activation_allowed",
        "routes_published",
    ):
        _require(scope.get(key) is False, f"scope_forbidden_true:{key}")

    _require(
        evidence.get("runtime_validation_blockers") == [],
        "runtime_validation_blockers_not_empty",
    )
    _require(
        bool(evidence.get("production_blockers")),
        "production_blockers_required",
    )
    claims = set(evidence.get("claims_not_made", []))
    _require(
        {
            "frontend_ready",
            "real_filing_available",
            "authentic_provider_e4_available",
            "production_safe",
            "production_authorized",
        } <= claims,
        "claims_not_made_incomplete",
    )
    limitations = set(evidence.get("limitations", []))
    _require(
        {
            "operator_console_reports_are_unattested_and_not_hash_frozen",
            "content_level_zero_delta_was_not_verified",
            "unknown_fixture_zero_baseline_was_not_verified",
            "git_archive_is_not_a_supply_chain_signature",
            "http_smoke_used_in_process_asgi",
        } <= limitations,
        "evidence_limitations_incomplete",
    )
    return {
        "version": evidence["version"],
        "status": evidence["status"],
        "execution_status": evidence["execution_status"],
        "gate_status": evidence["gate_status"],
        "production_gate_status": evidence["production_gate_status"],
        "live_verdict": evidence["live_verdict"],
        "final_delivery_identity_declared": True,
        "runtime_validation_blockers": 0,
        "production_blockers": len(evidence["production_blockers"]),
        "console_reports_unattested": True,
        "content_level_zero_delta_not_claimed": True,
        "unknown_zero_baseline_not_claimed": True,
    }


def audit_documents(root: Path = ROOT) -> dict[str, Any]:
    texts: dict[str, str] = {}
    for relative in (GATE_PATH, ADR_PATH):
        path = root / relative
        try:
            texts[relative] = path.read_text("utf-8")
        except (OSError, UnicodeError) as exc:
            raise A1SRuntimeEvidencePreflightError(
                f"document_invalid:{relative}:{type(exc).__name__}"
            ) from exc
    combined = "\n".join(texts.values())
    required = (
        FINAL_BASE_COMMIT_SHA40,
        FINAL_BASE_ARCHIVE_SHA256,
        "completed_synthetic_staging",
        "passed_synthetic_staging",
        "production_gate_status=blocked",
        "live_verdict=no_go",
        "operator_console_observed_unattested",
        "content-level zero delta",
        "UNKNOWN",
        "frontend",
        "E4 autentica",
    )
    missing = [marker for marker in required if marker not in combined]
    if missing:
        raise A1SRuntimeEvidencePreflightError(
            "closure_document_marker_missing:" + ",".join(missing)
        )
    forbidden = (
        "production_gate_status=passed",
        "live_verdict=go",
        "frontend_ready=true",
        "real_filing_available=true",
        "authentic_provider_e4_available=true",
    )
    present_forbidden = [marker for marker in forbidden if marker in combined]
    if present_forbidden:
        raise A1SRuntimeEvidencePreflightError(
            "closure_document_forbidden_claim:" + ",".join(
                present_forbidden
            )
        )
    return {
        "documents_verified": 2,
        "required_markers_present": len(required),
        "forbidden_claims_absent": True,
        "synthetic_runtime_and_production_decisions_separated": True,
    }


def _import_names(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def audit_closure_python(root: Path = ROOT) -> dict[str, Any]:
    """Parsea el cierre sin importarlo y rechaza runtime, red y procesos."""

    python_paths = (
        "scripts/rtm_connect_a1s_runtime_evidence_preflight.py",
        "tests/test_rtm_connect_a1s_runtime_evidence_preflight_contract.py",
        "tests/test_rtm_connect_a1s_runtime_docs_contract.py",
    )
    forbidden_imports: list[str] = []
    forbidden_calls: list[str] = []
    invalid: list[str] = []
    for relative in python_paths:
        try:
            tree = ast.parse(
                (root / relative).read_text("utf-8"), filename=relative
            )
        except (OSError, UnicodeError, SyntaxError) as exc:
            invalid.append(f"{relative}:{type(exc).__name__}")
            continue
        for module in sorted(_import_names(tree)):
            if module.split(".", 1)[0] in _FORBIDDEN_IMPORT_ROOTS:
                forbidden_imports.append(f"{relative}:{module}")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name in _FORBIDDEN_CALL_NAMES:
                forbidden_calls.append(
                    f"{relative}:{getattr(node, 'lineno', 0)}:{name}"
                )
    if invalid or forbidden_imports or forbidden_calls:
        raise A1SRuntimeEvidencePreflightError(
            "closure_python_audit_failed:"
            f"invalid={','.join(invalid)};"
            f"imports={','.join(forbidden_imports)};"
            f"calls={','.join(forbidden_calls)}"
        )
    return {
        "python_paths_required": len(python_paths),
        "python_paths_parsed": len(python_paths),
        "invalid_python": [],
        "forbidden_imports": [],
        "forbidden_calls": [],
        "runtime_imported": False,
        "network_or_process_surface_absent": True,
    }


def _interpreter_isolated() -> bool:
    return bool(
        sys.flags.isolated
        and sys.flags.no_site
        and sys.flags.dont_write_bytecode
        and sys.dont_write_bytecode
    )


def build_report(archive_path: Path, root: Path = ROOT) -> dict[str, Any]:
    blockers: list[str] = []
    archive: dict[str, Any] | None = None
    local_tree: dict[str, Any] | None = None
    evidence_audit: dict[str, Any] | None = None
    documents_audit: dict[str, Any] | None = None
    closure_ast_audit: dict[str, Any] | None = None
    isolated = _interpreter_isolated()
    if not isolated:
        blockers.append("isolated_no_site_no_bytecode_interpreter_required")
    try:
        archive_result = audit_archive(archive_path)
        base_file_sha256 = archive_result.pop("_base_file_sha256")
        base_file_canonical_sha256 = archive_result.pop(
            "_base_file_canonical_sha256"
        )
        archive = archive_result
        local_tree = audit_local_tree(
            base_file_sha256,
            base_file_canonical_sha256,
            root,
        )
        evidence_audit = audit_evidence(root)
        documents_audit = audit_documents(root)
        closure_ast_audit = audit_closure_python(root)
    except Exception as exc:
        blockers.append(
            "a1s_runtime_evidence_preflight_blocked:"
            f"{type(exc).__name__}:{exc}"
        )

    audit_ok = not blockers
    return {
        "ok": audit_ok,
        "safe": audit_ok,
        "audit_ok": audit_ok,
        "authority": "rtm_connect_a1s_runtime_evidence_preflight",
        "version": A1S_RUNTIME_EVIDENCE_PREFLIGHT_VERSION,
        "contract_version": A1S_RUNTIME_EVIDENCE_CONTRACT_VERSION,
        "final_base_commit_sha40": FINAL_BASE_COMMIT_SHA40,
        "final_base_archive_sha256": FINAL_BASE_ARCHIVE_SHA256,
        "archive": archive,
        "local_tree": local_tree,
        "evidence_audit": evidence_audit,
        "documents_audit": documents_audit,
        "closure_ast_audit": closure_ast_audit,
        "blockers": blockers,
        "checks": {
            "isolated_no_site_no_bytecode_interpreter": isolated,
            "exact_final_archive_sha256": archive is not None,
            "exact_final_commit_comment": archive is not None,
            "archive_crc_valid": bool(archive and archive["crc_ok"]),
            "archive_members_safe": bool(
                archive and archive["safe_members"]
            ),
            "required_hotfix_code_signatures_present": bool(
                archive
                and archive["hotfix_audit"][
                    "three_required_hotfix_code_signatures_present"
                ]
            ),
            "final_base_tree_matches_except_closure": bool(
                local_tree
                and local_tree[
                    "final_base_content_equivalent_except_closure"
                ]
            ),
            "closure_allowlist_exact": bool(
                local_tree
                and local_tree["closure_paths_present"]
                == len(CLOSURE_PATHS)
            ),
            "evidence_manifest_exact": evidence_audit is not None,
            "runtime_execution_recorded_synthetic_only": bool(
                evidence_audit
                and evidence_audit["execution_status"]
                == "completed_synthetic_staging"
            ),
            "production_gate_remains_blocked": bool(
                evidence_audit
                and evidence_audit["production_gate_status"] == "blocked"
            ),
            "unattested_console_reports_not_overclaimed": bool(
                evidence_audit
                and evidence_audit["console_reports_unattested"]
            ),
            "content_level_zero_delta_not_overclaimed": bool(
                evidence_audit
                and evidence_audit["content_level_zero_delta_not_claimed"]
            ),
            "closure_docs_markers_consistent": documents_audit is not None,
            "closure_python_static_only": bool(
                closure_ast_audit
                and closure_ast_audit[
                    "network_or_process_surface_absent"
                ]
            ),
            "runtime_modules_not_imported": bool(
                closure_ast_audit
                and not closure_ast_audit["runtime_imported"]
            ),
        },
        "runtime_execution_status": "completed_synthetic_staging",
        "gate_status": (
            "passed_synthetic_staging" if audit_ok else "blocked"
        ),
        "production_gate_status": "blocked",
        "live_verdict": "no_go",
        "read_only": True,
        "offline_only": True,
        "synthetic_only": True,
        "runtime_imported": False,
        "archive_extracted": False,
        "network_used": False,
        "provider_contacted": False,
        "administration_contacted": False,
        "b2_used": False,
        "database_touched": False,
        "secret_resolution_performed": False,
        "external_effects_executed": False,
        "routes_published": False,
        "workers_started": False,
        "real_data_used": False,
        "production_authorized": False,
        "production_safe": False,
        "console_reports_cryptographically_verified": False,
        "content_level_zero_delta_verified": False,
        "scope_limitations": [
            "preflight_is_static_and_does_not_reexecute_runtime_or_postgresql",
            "operator_console_reports_are_unattested_and_not_hash_frozen",
            "archive_hash_and_comment_do_not_prove_commit_authorship",
            "git_archive_does_not_prove_commit_ancestry_or_parentage",
            "git_archive_is_not_a_supply_chain_signature",
            "rollback_snapshot_equality_is_count_level_not_content_level",
            "unknown_fixture_zero_baseline_was_not_independently_verified",
            "http_smoke_used_in_process_asgi",
            "a1s_runtime_remains_synthetic_only",
            "real_filing_frontend_and_production_remain_no_go",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = build_report(Path(args.archive).resolve())
    _print(report, args.compact)
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
