#!/usr/bin/env python3
"""Preflight offline y read-only de la admision de proveedor G1.

El ZIP G0 se inspecciona sin extraer miembros al filesystem ni importar su
codigo. Una reproduccion correcta conserva BLOCKED/NO-GO y devuelve exit 2.
"""

from __future__ import annotations

import argparse
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
while str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

from rtm_connect_post_c8_g1 import (  # noqa: E402
    POST_C8_G1_BASE_ARCHIVE_SHA256,
    POST_C8_G1_BASE_COMMIT_SHA40,
    POST_C8_G1_BASELINE_SNAPSHOT_SHA256,
    POST_C8_G1_CONTRACT_VERSION,
    POST_C8_G1_FROZEN_EVALUATED_AT,
    POST_C8_G1_REQUIRED_DOSSIER_SECTIONS,
    PostC8G1LiveActivationUnavailable,
    assess_provider_admission,
    assert_g1_live_activation_unavailable,
    provider_admission_fingerprint_material,
    provider_admission_sha256,
)


PREFLIGHT_VERSION = "rtm_connect_post_c8_g1_preflight_v1_0"
EXPECTED_ZIP_ENTRIES = 533
EXPECTED_ZIP_FILES = 514
EXPECTED_ZIP_UNCOMPRESSED_BYTES = 6_426_113
EXPECTED_ZIP_COMPRESSED_BYTES = 1_622_311
EXPECTED_ZIP_MAX_FILE_BYTES = 174_062
G0_EVIDENCE_MANIFEST_SHA256 = (
    "42cfde74dc70291679fb141d13d3a1bcaa234dbd638395282b0b5a105f7fada0"
)
G1_EVIDENCE_MANIFEST_SHA256 = (
    "87e7c97a84af36fe93e6f8f7030dec872af8740ef9266eeb9a3f422bfad4b8be"
)

BASE_CRITICAL_TEXT_SHA256 = {
    "app.py": "fd089d1cce4f65ebe6fb84b380dd13eba8a98ba4a16049515f4f513c27eeb7ea",
    "dgt_client.py": "c911dc71978eaa4a077d54aea0b83fb2eaf9a7acd6a4df55fcdad36d360e6d3a",
    "docs/rtm_connect/RTM_CONNECT_C0_MANIFEST.json": "6c897149924008f277436942849139c9c0f41d1ce87474260487c7f9a64b9460",
    "docs/rtm_connect/RTM_CONNECT_C8_CONTROLLED_PRODUCTION.md": "d8babc5656518484bfbf52276e30ebfdab52acedabe57489a1126b009cf5149f",
    "docs/rtm_connect/RTM_CONNECT_POST_C8_G0_EVIDENCE.json": "42cfde74dc70291679fb141d13d3a1bcaa234dbd638395282b0b5a105f7fada0",
    "docs/rtm_connect/RTM_CONNECT_POST_C8_GATE_G0.md": "f4713c3e3a38f9efc4e223f1f88669c7f44ee00b0dd1ca193f439c69113fa6b0",
    "docs/rtm_connect/adrs/0015-c8-controlled-production-admission.md": "a6e7bfa697c4f6a07b2493b8edb44684123fd3c5ca96f2e3ffeee04058fe3821",
    "docs/rtm_connect/adrs/0016-post-c8-g0-offline-decision-gate.md": "3a5ab809d94f1014c95d0724735cdec49bb0fe39330bfd574fd101e2bd3fcb11",
    "rtm_connect/__init__.py": "fdb209b99b8d731466d467dea5b9711a792ddcb4a284343204ea2126568144ac",
    "rtm_connect/production_contracts.py": "5b26fe972f53e2f5b60dcf73e485fb22dc78d7844d6f15a07c0d8da6b3df0e22",
    "rtm_connect/production_control.py": "c941a856343bf2771ca354f2527b9737eb5631f8a70e82314d53eb84c5b00a1b",
    "rtm_connect/production_policy.py": "e6d009422f3959a1186cf72e23bb9594e882c09c0b396ac1a46673a3939eb1af",
    "rtm_connect/production_schema.py": "d3d1759c416f21bdc75a95098d00540cfc683a6bd6cc8198f17103074de8d121",
    "rtm_connect_post_c8_g0.py": "fc50f45e21854f9e6977e554b455c3f9fff78093a0a316468c4f69b03a147fd5",
    "scripts/rtm_connect_post_c8_g0_preflight.py": "0b713f72896f582d14a4d3f9f5eebbec93ecdddb878baf5d895eddbfc3e10ffc",
    "scripts/rtm_connect_post_c8_g0_smoke.py": "4bcc79b6996c163d38bac51036fdf48fa78b0289794b190e9e3c047be2a740e5",
    "submitter_dgt.py": "f41dd781d6390c8d4989cf00622ce2402995fc3f2e52a455d5eb86b069f331a3",
    "submitters/base.py": "3f20d69fc6b0679ea4462996f1c5a01d4a98060b25962e01d613d2e9a7ec5859",
    "submitters/registro.py": "aca2b5baa63a85765d421639d646d59488627e2b06a0838130fae9041ddf0697",
    "tests/test_rtm_connect_post_c8_g0_contract.py": "74da1794aadd9c77e770551b3a6341b2fed877256d030e7e96a751442320d3b9",
    "tests/test_rtm_connect_post_c8_g0_docs_contract.py": "2a8b8189a8bf8cfc4f54f7297dfc48e929dc4e231a054cdc95e5dfd8c5c10935",
    "tests/test_rtm_connect_post_c8_g0_scripts_contract.py": "22cb92b872c37555f5b867fb1eb77725166e909b30ab3715f4af5491883347eb",
}

G1_OVERLAY_PATHS = (
    "rtm_connect_post_c8_g1.py",
    "scripts/rtm_connect_post_c8_g1_preflight.py",
    "scripts/rtm_connect_post_c8_g1_smoke.py",
    "docs/rtm_connect/RTM_CONNECT_POST_C8_GATE_G1.md",
    "docs/rtm_connect/RTM_CONNECT_POST_C8_G1_EVIDENCE.json",
    "docs/rtm_connect/adrs/0017-post-c8-g1-provider-admission.md",
    "tests/test_rtm_connect_post_c8_g1_contract.py",
    "tests/test_rtm_connect_post_c8_g1_docs_contract.py",
    "tests/test_rtm_connect_post_c8_g1_scripts_contract.py",
)

SCOPE_LIMITATIONS = (
    "archive_hash_and_comment_do_not_prove_git_commit_object_or_authorship",
    "delivery_integrity_does_not_prove_supply_chain_signature_or_provenance",
    "legacy_candidate_inventory_is_exact_for_g1_but_not_global_discovery",
    "static_review_does_not_contact_or_identify_an_actual_provider",
    "no_provider_dossier_is_present_or_admissible_in_g1",
    "no_receipt_in_g1_is_authentic_e4_provider_evidence",
    "g0_blocked_no_go_decision_remains_authoritative",
    "g1_overlay_commit_and_delivery_hash_require_external_freezing",
)


class ArchiveAdmissionError(ValueError):
    pass


_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
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
    ))


def _stream_sha256(handle: Any) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _safe_member(info: zipfile.ZipInfo) -> bool:
    original_name = getattr(info, "orig_filename", info.filename)
    name = info.filename
    if (
        not name
        or not original_name
        or "\\" in name
        or "\\" in original_name
        or "\x00" in name
        or "\x00" in original_name
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


def _canonical_text_sha256(raw: bytes, name: str) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArchiveAdmissionError(f"utf8_required:{name}") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _canonical_member_sha256(raw: bytes, name: str) -> str:
    try:
        return _canonical_text_sha256(raw, name)
    except ArchiveAdmissionError:
        return hashlib.sha256(raw).hexdigest()


def _snapshot_sha256(mapping: dict[str, str]) -> str:
    canonical = json.dumps(mapping, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def audit_archive(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ArchiveAdmissionError("archive_not_found")
    with path.open("rb") as handle:
        actual_archive_sha256 = _stream_sha256(handle)
        if actual_archive_sha256 != POST_C8_G1_BASE_ARCHIVE_SHA256:
            raise ArchiveAdmissionError("archive_sha256_mismatch")
        handle.seek(0)
        with zipfile.ZipFile(handle, "r") as archive:
            infos = archive.infolist()
            try:
                comment = archive.comment.decode("ascii", errors="strict")
            except UnicodeDecodeError as exc:
                raise ArchiveAdmissionError("archive_comment_not_ascii") from exc
            if comment != POST_C8_G1_BASE_COMMIT_SHA40:
                raise ArchiveAdmissionError("archive_commit_comment_mismatch")
            if len(infos) != EXPECTED_ZIP_ENTRIES:
                raise ArchiveAdmissionError("archive_entry_count_mismatch")
            files = [info for info in infos if not info.is_dir()]
            if len(files) != EXPECTED_ZIP_FILES:
                raise ArchiveAdmissionError("archive_file_count_mismatch")
            if sum(info.file_size for info in infos) != EXPECTED_ZIP_UNCOMPRESSED_BYTES:
                raise ArchiveAdmissionError("archive_uncompressed_size_mismatch")
            if sum(info.compress_size for info in infos) != EXPECTED_ZIP_COMPRESSED_BYTES:
                raise ArchiveAdmissionError("archive_compressed_size_mismatch")
            if max(info.file_size for info in files) != EXPECTED_ZIP_MAX_FILE_BYTES:
                raise ArchiveAdmissionError("archive_max_file_size_mismatch")
            if any(not _safe_member(info) for info in infos):
                raise ArchiveAdmissionError("archive_unsafe_member")
            folded = [
                unicodedata.normalize("NFKC", info.filename).casefold()
                for info in infos
            ]
            if len(folded) != len(set(folded)):
                raise ArchiveAdmissionError("archive_casefold_duplicate")
            bad_crc = archive.testzip()
            if bad_crc is not None:
                raise ArchiveAdmissionError(f"archive_crc_failed:{bad_crc}")
            names = {info.filename for info in infos}
            if any(name in names for name in G1_OVERLAY_PATHS):
                raise ArchiveAdmissionError("g1_overlay_must_not_exist_in_g0_base")
            missing = sorted(set(BASE_CRITICAL_TEXT_SHA256) - names)
            if missing:
                raise ArchiveAdmissionError(
                    "archive_required_members_missing:" + ",".join(missing)
                )
            critical_actual = {
                name: _canonical_text_sha256(archive.read(name), name)
                for name in BASE_CRITICAL_TEXT_SHA256
            }
            base_file_canonical_sha256 = {
                info.filename: _canonical_member_sha256(
                    archive.read(info.filename),
                    info.filename,
                )
                for info in files
            }
    if critical_actual != BASE_CRITICAL_TEXT_SHA256:
        raise ArchiveAdmissionError("critical_base_content_mismatch")
    snapshot = _snapshot_sha256(critical_actual)
    if snapshot != POST_C8_G1_BASELINE_SNAPSHOT_SHA256:
        raise ArchiveAdmissionError("baseline_snapshot_mismatch")
    return {
        "archive_sha256": actual_archive_sha256,
        "archive_commit_comment": POST_C8_G1_BASE_COMMIT_SHA40,
        "entries": len(infos),
        "files": len(files),
        "uncompressed_bytes": EXPECTED_ZIP_UNCOMPRESSED_BYTES,
        "compressed_bytes": EXPECTED_ZIP_COMPRESSED_BYTES,
        "max_file_bytes": EXPECTED_ZIP_MAX_FILE_BYTES,
        "crc_ok": True,
        "safe_members": True,
        "casefold_duplicates": 0,
        "critical_snapshot_sha256": snapshot,
        "critical_files_verified": len(critical_actual),
        "g1_overlay_absent_from_base_archive": True,
        "_base_file_canonical_sha256": base_file_canonical_sha256,
    }


def _expected_candidate_inventory(assessment: Any) -> list[dict[str, Any]]:
    material = provider_admission_fingerprint_material(assessment)
    return list(material["candidates"])


def audit_local_gate(
    assessment: Any,
    base_file_canonical_sha256: dict[str, str],
) -> dict[str, Any]:
    if len(base_file_canonical_sha256) != EXPECTED_ZIP_FILES:
        raise ArchiveAdmissionError("base_inventory_file_count_mismatch")
    base_actual: dict[str, str] = {}
    for name in base_file_canonical_sha256:
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            raise ArchiveAdmissionError(f"local_base_file_missing_or_special:{name}")
        base_actual[name] = _canonical_member_sha256(path.read_bytes(), name)
    if base_actual != base_file_canonical_sha256:
        raise ArchiveAdmissionError("local_full_base_tree_content_mismatch")

    allowed_files = set(base_file_canonical_sha256) | set(G1_OVERLAY_PATHS)
    local_files: set[str] = set()
    ignored_directories = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
    for folder, directories, filenames in os.walk(ROOT):
        directories[:] = [
            name for name in directories if name not in ignored_directories
        ]
        for filename in filenames:
            path = Path(folder) / filename
            if path.suffix == ".pyc":
                continue
            local_files.add(path.relative_to(ROOT).as_posix())
    unexpected = sorted(local_files - allowed_files)
    missing = sorted(allowed_files - local_files)
    if unexpected or missing:
        raise ArchiveAdmissionError(
            "local_tree_allowlist_mismatch:"
            f"unexpected={','.join(unexpected)};missing={','.join(missing)}"
        )

    evidence_path = ROOT / "docs/rtm_connect/RTM_CONNECT_POST_C8_G1_EVIDENCE.json"
    try:
        evidence_raw = evidence_path.read_bytes()
        evidence_hash = _canonical_text_sha256(evidence_raw, evidence_path.as_posix())
        if evidence_hash != G1_EVIDENCE_MANIFEST_SHA256:
            raise ArchiveAdmissionError("g1_evidence_manifest_sha256_mismatch")
        evidence = json.loads(evidence_raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArchiveAdmissionError("g1_evidence_manifest_invalid") from exc

    expected_top_level = {
        "authority",
        "version",
        "phase",
        "not_phase",
        "source",
        "assessment",
        "g0_identity_closure",
        "legacy_candidate_inventory",
        "required_provider_dossier_sections",
        "critical_base_text_sha256",
        "scope_limitations",
        "overlay_identity",
    }
    if set(evidence) != expected_top_level:
        raise ArchiveAdmissionError("g1_evidence_top_level_allowlist_mismatch")
    if (
        evidence.get("authority") != "rtm_connect_post_c8_g1_evidence"
        or evidence.get("version") != "rtm_connect_post_c8_g1_evidence_v1_0"
        or evidence.get("phase") != "post_c8_g1"
        or evidence.get("not_phase") != "C9"
    ):
        raise ArchiveAdmissionError("g1_evidence_identity_mismatch")
    if evidence.get("source") != {
        "commit_sha40": POST_C8_G1_BASE_COMMIT_SHA40,
        "archive_sha256": POST_C8_G1_BASE_ARCHIVE_SHA256,
        "archive_comment": POST_C8_G1_BASE_COMMIT_SHA40,
        "archive_entries": EXPECTED_ZIP_ENTRIES,
        "archive_files": EXPECTED_ZIP_FILES,
        "critical_snapshot_sha256": POST_C8_G1_BASELINE_SNAPSHOT_SHA256,
        "crc_valid": True,
        "safe_member_names": True,
    }:
        raise ArchiveAdmissionError("g1_evidence_source_mismatch")
    expected_assessment = provider_admission_fingerprint_material(assessment)
    expected_assessment["assessment_sha256"] = provider_admission_sha256(assessment)
    if evidence.get("assessment") != expected_assessment:
        raise ArchiveAdmissionError("g1_evidence_assessment_mismatch")
    if evidence.get("legacy_candidate_inventory") != _expected_candidate_inventory(
        assessment
    ):
        raise ArchiveAdmissionError("g1_candidate_inventory_mismatch")
    if evidence.get("required_provider_dossier_sections") != list(
        POST_C8_G1_REQUIRED_DOSSIER_SECTIONS
    ):
        raise ArchiveAdmissionError("g1_dossier_sections_mismatch")
    if evidence.get("critical_base_text_sha256") != BASE_CRITICAL_TEXT_SHA256:
        raise ArchiveAdmissionError("g1_critical_hashes_mismatch")
    if evidence.get("scope_limitations") != list(SCOPE_LIMITATIONS):
        raise ArchiveAdmissionError("g1_scope_limitations_mismatch")
    if evidence.get("g0_identity_closure") != {
        "g0_commit_sha40": POST_C8_G1_BASE_COMMIT_SHA40,
        "delivery_zip_sha256": POST_C8_G1_BASE_ARCHIVE_SHA256,
        "archive_comment_sha40": POST_C8_G1_BASE_COMMIT_SHA40,
        "g0_evidence_manifest_sha256": G0_EVIDENCE_MANIFEST_SHA256,
        "delivery_sha256_verified": True,
        "archive_comment_matches_claimed_commit": True,
        "g0_decision_remains_blocked_no_go": True,
        "git_commit_object_and_authorship_attested": False,
        "supply_chain_signature_and_sbom_verified": False,
        "status": "delivery_identity_frozen_provenance_unattested",
    }:
        raise ArchiveAdmissionError("g1_g0_identity_closure_mismatch")
    if evidence.get("overlay_identity") != {
        "base_commit_sha40": POST_C8_G1_BASE_COMMIT_SHA40,
        "delivery_zip_sha256": None,
        "git_commit_sha40": None,
        "integrity_status": "external_delivery_hash_and_future_git_commit_required",
        "paths": list(G1_OVERLAY_PATHS),
    }:
        raise ArchiveAdmissionError("g1_overlay_identity_not_frozen")
    return {
        "frozen_base_tree_exact": True,
        "evidence_manifest_exact": True,
        "base_files_verified": len(base_actual),
        "critical_files_verified": len(BASE_CRITICAL_TEXT_SHA256),
        "legacy_candidates_rejected": len(assessment.candidates),
        "g0_delivery_identity_frozen": True,
        "g1_overlay_paths_present_uncommitted": len(G1_OVERLAY_PATHS),
        "g1_overlay_snapshot_sha256": _snapshot_sha256({
            name: _canonical_text_sha256((ROOT / name).read_bytes(), name)
            for name in G1_OVERLAY_PATHS
        }),
    }


def _interpreter_isolated() -> bool:
    return bool(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode)


def _base_report() -> dict[str, Any]:
    return {
        "ok": False,
        "audit_ok": False,
        "offline_review_reproduced": False,
        "authority": "rtm_connect_post_c8_g1_preflight",
        "version": PREFLIGHT_VERSION,
        "read_only": True,
        "offline_only": True,
        "database_touched": False,
        "schema_changes_required": False,
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
        "archive": None,
        "local_gate": None,
        "checks": {},
        "candidate_findings": [],
        "blockers": [],
        "scope_limitations": list(SCOPE_LIMITATIONS),
        "safe": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _base_report()
    try:
        archive_audit = audit_archive(Path(args.archive).resolve())
        base_hashes = archive_audit["_base_file_canonical_sha256"]
        archive_report = {
            key: value
            for key, value in archive_audit.items()
            if key != "_base_file_canonical_sha256"
        }
        assessment = assess_provider_admission(
            source_commit_sha40=POST_C8_G1_BASE_COMMIT_SHA40,
            base_archive_sha256=POST_C8_G1_BASE_ARCHIVE_SHA256,
            baseline_snapshot_sha256=POST_C8_G1_BASELINE_SNAPSHOT_SHA256,
            evaluated_at=POST_C8_G1_FROZEN_EVALUATED_AT,
        )
        local_report = audit_local_gate(assessment, base_hashes)
        guard_blocked = False
        try:
            assert_g1_live_activation_unavailable(assessment=assessment)
        except PostC8G1LiveActivationUnavailable as exc:
            guard_blocked = exc.code == "g1_live_activation_unavailable"
        report["archive"] = archive_report
        report["local_gate"] = local_report
        report["contract_version"] = POST_C8_G1_CONTRACT_VERSION
        report["evaluated_at"] = assessment.evaluated_at
        report["assessment_sha256"] = provider_admission_sha256(assessment)
        report["candidate_findings"] = _expected_candidate_inventory(assessment)
        report["blockers"] = [
            code
            for candidate in assessment.candidates
            for code in candidate.blocker_codes
        ]
        report["checks"] = {
            "exact_archive_sha256": True,
            "exact_commit_comment": True,
            "archive_crc_valid": True,
            "archive_members_safe": True,
            "critical_g0_snapshot_exact": True,
            "g0_delivery_identity_frozen": local_report[
                "g0_delivery_identity_frozen"
            ],
            "g0_decision_preserved": assessment.g0_decision_preserved,
            "three_legacy_candidates_rejected": (
                len(assessment.candidates) == 3
                and all(item.status == "rejected" for item in assessment.candidates)
            ),
            "no_provider_selected_or_admissible": not any((
                assessment.provider_selected,
                assessment.provider_identity_verified,
                assessment.provider_pack_present,
                assessment.provider_pack_admissible,
            )),
            "live_canary_zero": assessment.live_canary_percent == 0,
            "live_activation_guard_unconditional": guard_blocked,
            "full_frozen_base_tree_matches_archive": local_report[
                "frozen_base_tree_exact"
            ],
            "g1_evidence_manifest_exact": local_report["evidence_manifest_exact"],
            "g1_overlay_absent_from_base_archive": archive_report[
                "g1_overlay_absent_from_base_archive"
            ],
            "isolated_no_site_no_bytecode_interpreter": _interpreter_isolated(),
        }
        report["audit_ok"] = all(report["checks"].values())
        report["offline_review_reproduced"] = report["audit_ok"]
        report["ok"] = False
        report["safe"] = False
        report["production_safe"] = False
        _print(report, args.compact)
        return 2
    except (
        ArchiveAdmissionError,
        OSError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        report["blockers"] = [
            f"post_c8_g1_archive_blocked:{type(exc).__name__}:{exc}"
        ]
        _print(report, args.compact)
        return 2
    except Exception as exc:
        report["blockers"] = [f"unexpected:{type(exc).__name__}:{exc}"]
        _print(report, args.compact)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
