#!/usr/bin/env python3
"""Preflight offline y read-only de la puerta post-C8 G0.

El ZIP se inspecciona sin extraer al filesystem ni importar su codigo. Incluso
si la auditoria se reproduce, G0 devuelve exit 2 porque produccion sigue NO-GO.
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

from rtm_connect_post_c8_g0 import (  # noqa: E402
    POST_C8_GATE_BASE_ARCHIVE_SHA256,
    POST_C8_GATE_BASE_COMMIT_SHA40,
    POST_C8_GATE_BASELINE_SNAPSHOT_SHA256,
    POST_C8_GATE_CONTRACT_VERSION,
    POST_C8_GATE_FROZEN_EVALUATED_AT,
    PostC8LiveActivationUnavailable,
    assess_post_c8_gate,
    assert_g0_live_activation_unavailable,
    post_c8_gate_fingerprint_material,
    post_c8_gate_sha256,
)


PREFLIGHT_VERSION = "rtm_connect_post_c8_g0_preflight_v1_0"
EXPECTED_ZIP_ENTRIES = 524
EXPECTED_ZIP_FILES = 505
EXPECTED_ZIP_UNCOMPRESSED_BYTES = 6_293_321
EXPECTED_ZIP_COMPRESSED_BYTES = 1_581_589
EXPECTED_ZIP_MAX_FILE_BYTES = 174_062
G0_EVIDENCE_MANIFEST_SHA256 = (
    "42cfde74dc70291679fb141d13d3a1bcaa234dbd638395282b0b5a105f7fada0"
)

CRITICAL_C8_TEXT_SHA256 = {
    "app.py": "fd089d1cce4f65ebe6fb84b380dd13eba8a98ba4a16049515f4f513c27eeb7ea",
    "docs/rtm_connect/RTM_CONNECT_C0_MANIFEST.json": "6c897149924008f277436942849139c9c0f41d1ce87474260487c7f9a64b9460",
    "docs/rtm_connect/RTM_CONNECT_C8_CONTROLLED_PRODUCTION.md": "d8babc5656518484bfbf52276e30ebfdab52acedabe57489a1126b009cf5149f",
    "docs/rtm_connect/adrs/0015-c8-controlled-production-admission.md": "a6e7bfa697c4f6a07b2493b8edb44684123fd3c5ca96f2e3ffeee04058fe3821",
    "rtm_connect/manifest.py": "685c21d73620b670ac3108a84d3c73461fc34142cce717168122fd51e5cbad55",
    "rtm_connect/state_machine.py": "addadfc273f55e486f306f926be5ef48dfff24b4701e4fa40a56c016467572ae",
    "rtm_connect/production_contracts.py": "5b26fe972f53e2f5b60dcf73e485fb22dc78d7844d6f15a07c0d8da6b3df0e22",
    "rtm_connect/production_policy.py": "e6d009422f3959a1186cf72e23bb9594e882c09c0b396ac1a46673a3939eb1af",
    "rtm_connect/production_control.py": "c941a856343bf2771ca354f2527b9737eb5631f8a70e82314d53eb84c5b00a1b",
    "rtm_connect/production_schema.py": "d3d1759c416f21bdc75a95098d00540cfc683a6bd6cc8198f17103074de8d121",
    "scripts/rtm_connect_c8_preflight.py": "ed16c5c65a70ade8079118ad601490ddf060774971ef07c1cb7ba11e1b3390d9",
    "scripts/rtm_connect_c8_smoke.py": "a3c40bce76c80f5085fa5f7c7d2ea3f4b8c9e3a6dc470c2122989255f86f7195",
    "scripts/rtm_staging_connect_c8_schema.py": "c063372329789b8ddedb745e96328bb0f31913438b7219298d4906b52e269f24",
    "tests/test_rtm_connect_c8_docs_contract.py": "aa86040c4c498aba844f9cc932aa8eb4f516cf3ea63fc0fbac556be0deee66af",
    "tests/test_rtm_connect_c8_production_contracts.py": "1ae0a9b609d71a720093b26feea3843b740fcb1e4db04166f76da99b00cbadd9",
    "tests/test_rtm_connect_c8_production_control.py": "241058932245543ab25bdecb7692914d444fe5bedbc162fb43570c268f4d775a",
    "tests/test_rtm_connect_c8_production_policy.py": "b9b3b4c948fad992b26e6b9a30bcf895dccc138aca6d88f46b1dde64726ecef3",
    "tests/test_rtm_connect_c8_production_schema_contract.py": "b126a92e315580f977d221d9107e18427bc572d683ac6236c1d10bc6fd97e743",
    "tests/test_rtm_connect_c8_scripts_contract.py": "461a05d5ebd4523ec624b4aec7f368038438adb503e4eaf803ec537a879f2e26",
}

LEGACY_EFFECT_TEXT_SHA256 = {
    "rtm_connect/__init__.py": "fdb209b99b8d731466d467dea5b9711a792ddcb4a284343204ea2126568144ac",
    "ops_automation.py": "7aa0dc59e7d942c0b3c53efbae570dcecb1ae554a5333e7368236b590b59c083",
    "ops_automation_router.py": "e43ecbd6b71244fc378aa43c67253ffd3619874bc749719a65d53bd10fafa387",
    "dgt_client.py": "c911dc71978eaa4a077d54aea0b83fb2eaf9a7acd6a4df55fcdad36d360e6d3a",
    "submitter_dgt.py": "f41dd781d6390c8d4989cf00622ce2402995fc3f2e52a455d5eb86b069f331a3",
    "submitters/registro.py": "aca2b5baa63a85765d421639d646d59488627e2b06a0838130fae9041ddf0697",
    "rtm_core/runtime_capabilities.py": "efab84b8887a327e148159c2423fef92a0b867db963ed64fa186640ae84d2eb1",
    "cron_tick.sh": "972fca0277879258a87abfaa9d321bf5710721106f532a6de2eb550334381792",
    ".github/workflows/rtm-staging-synthetic-live.yml": "0f88688129d75ff46ce3477c164a50a4cbac3a1194444ff5ed934c4994f048f3",
    "partner.py": "9765475b4fd76eabfd1bcb8f81220ed5a47e208d0ef5535089d3b1469d0c03f5",
    "authorization_pdf.py": "4a40927ff42839b33864be519751c12fdd285417b3f8a72e1e9891f1951bd2b1",
    "cases.py": "66f7ba71ed73fda74a2b0bed042e521aa68502940593fa250f78ffb39f8b30cf",
    "vehicle_removal_router.py": "6cfa46759440c585a54c08e0300d7f0401f168d2029d32623c3e4c4e22ef138a",
    "ops_operator_submit_router.py": "26ef85b6b97b084c6f8d1c0bc5c08034ad7c4b4ca65f2e80742e4c3e374ffae2",
    "dgt_test.py": "4a49df7a1ff32eabe4f22f3688893e41ebca84665efa3d78b0478dd7d1c95ba5",
    "README.md": "c8c7858d9f4815a76828e46dfc599bb1b64ca44a71cde1cb327ea3a6700b0d64",
}

BINARY_SHA256 = {
    "templates/firma.png": "87bbe5a651ebbf708ebaf16813f840bd6a7227e0c1926b56f019d5a0b0aef37d",
}

G0_OVERLAY_PATHS = (
    "rtm_connect_post_c8_g0.py",
    "scripts/rtm_connect_post_c8_g0_preflight.py",
    "scripts/rtm_connect_post_c8_g0_smoke.py",
    "docs/rtm_connect/RTM_CONNECT_POST_C8_GATE_G0.md",
    "docs/rtm_connect/RTM_CONNECT_POST_C8_G0_EVIDENCE.json",
    "docs/rtm_connect/adrs/0016-post-c8-g0-offline-decision-gate.md",
    "tests/test_rtm_connect_post_c8_g0_contract.py",
    "tests/test_rtm_connect_post_c8_g0_docs_contract.py",
    "tests/test_rtm_connect_post_c8_g0_scripts_contract.py",
)

SCOPE_LIMITATIONS = (
    "hash_format_and_presence_do_not_prove_artifact_provenance_or_signature",
    "c8_static_no_network_claim_is_not_global_network_instrumentation",
    "fastapi_route_graph_does_not_cover_all_entrypoints_cron_or_render_config",
    "repository_contains_networked_workflow_and_legacy_effect_surfaces",
    "c8_dry_run_is_not_authentic_e4_provider_evidence",
    "c0_manifest_flags_are_historical_not_effective_runtime_state",
    "legacy_effect_inventory_is_a_frozen_minimum_not_exhaustive_discovery",
    "delivery_zip_sha256_is_external_and_must_be_verified_before_extraction",
)


class ArchiveGateError(ValueError):
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
        or any(part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES for part in pure.parts)
    ):
        return False
    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    return file_type in {0, stat.S_IFREG, stat.S_IFDIR}


def _canonical_text_sha256(raw: bytes, name: str) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArchiveGateError(f"utf8_required:{name}") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _canonical_member_sha256(raw: bytes, name: str) -> str:
    try:
        return _canonical_text_sha256(raw, name)
    except ArchiveGateError:
        return hashlib.sha256(raw).hexdigest()


def _snapshot_sha256(mapping: dict[str, str]) -> str:
    canonical = json.dumps(
        mapping,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def audit_archive(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ArchiveGateError("archive_not_found")
    with path.open("rb") as handle:
        actual_archive_sha256 = _stream_sha256(handle)
        if actual_archive_sha256 != POST_C8_GATE_BASE_ARCHIVE_SHA256:
            raise ArchiveGateError("archive_sha256_mismatch")
        handle.seek(0)
        with zipfile.ZipFile(handle, "r") as archive:
            infos = archive.infolist()
            if archive.comment.decode("ascii", errors="strict") != POST_C8_GATE_BASE_COMMIT_SHA40:
                raise ArchiveGateError("archive_commit_comment_mismatch")
            if len(infos) != EXPECTED_ZIP_ENTRIES:
                raise ArchiveGateError("archive_entry_count_mismatch")
            files = [info for info in infos if not info.is_dir()]
            if len(files) != EXPECTED_ZIP_FILES:
                raise ArchiveGateError("archive_file_count_mismatch")
            if sum(info.file_size for info in infos) != EXPECTED_ZIP_UNCOMPRESSED_BYTES:
                raise ArchiveGateError("archive_uncompressed_size_mismatch")
            if sum(info.compress_size for info in infos) != EXPECTED_ZIP_COMPRESSED_BYTES:
                raise ArchiveGateError("archive_compressed_size_mismatch")
            if max(info.file_size for info in files) != EXPECTED_ZIP_MAX_FILE_BYTES:
                raise ArchiveGateError("archive_max_file_size_mismatch")
            if any(not _safe_member(info) for info in infos):
                raise ArchiveGateError("archive_unsafe_member")
            folded = [
                unicodedata.normalize("NFKC", info.filename).casefold()
                for info in infos
            ]
            if len(folded) != len(set(folded)):
                raise ArchiveGateError("archive_casefold_duplicate")
            bad_crc = archive.testzip()
            if bad_crc is not None:
                raise ArchiveGateError(f"archive_crc_failed:{bad_crc}")
            names = {info.filename for info in infos}
            if any(name in names for name in G0_OVERLAY_PATHS):
                raise ArchiveGateError("g0_overlay_must_not_exist_in_c8_base_archive")
            required = set(CRITICAL_C8_TEXT_SHA256) | set(LEGACY_EFFECT_TEXT_SHA256) | set(BINARY_SHA256)
            missing = sorted(required - names)
            if missing:
                raise ArchiveGateError("archive_required_members_missing:" + ",".join(missing))
            critical_actual = {
                name: _canonical_text_sha256(archive.read(name), name)
                for name in CRITICAL_C8_TEXT_SHA256
            }
            legacy_actual = {
                name: _canonical_text_sha256(archive.read(name), name)
                for name in LEGACY_EFFECT_TEXT_SHA256
            }
            binary_actual = {
                name: hashlib.sha256(archive.read(name)).hexdigest()
                for name in BINARY_SHA256
            }
            base_file_canonical_sha256 = {
                info.filename: _canonical_member_sha256(
                    archive.read(info.filename),
                    info.filename,
                )
                for info in files
            }
    if critical_actual != CRITICAL_C8_TEXT_SHA256:
        raise ArchiveGateError("critical_c8_content_mismatch")
    if legacy_actual != LEGACY_EFFECT_TEXT_SHA256:
        raise ArchiveGateError("legacy_effect_inventory_mismatch")
    if binary_actual != BINARY_SHA256:
        raise ArchiveGateError("embedded_signature_asset_mismatch")
    snapshot = _snapshot_sha256(critical_actual)
    if snapshot != POST_C8_GATE_BASELINE_SNAPSHOT_SHA256:
        raise ArchiveGateError("baseline_snapshot_mismatch")
    return {
        "archive_sha256": actual_archive_sha256,
        "archive_commit_comment": POST_C8_GATE_BASE_COMMIT_SHA40,
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
        "legacy_effect_files_frozen_minimum": len(legacy_actual),
        "embedded_legal_signature_assets": len(binary_actual),
        "g0_overlay_absent_from_base_archive": True,
        "_base_file_canonical_sha256": base_file_canonical_sha256,
    }


def _local_text_hashes(mapping: dict[str, str]) -> dict[str, str]:
    actual: dict[str, str] = {}
    for name in mapping:
        path = ROOT / name
        if not path.is_file():
            raise ArchiveGateError(f"local_required_file_missing:{name}")
        actual[name] = _canonical_text_sha256(path.read_bytes(), name)
    return actual


def audit_local_gate(
    assessment: Any,
    base_file_canonical_sha256: dict[str, str],
) -> dict[str, Any]:
    """Liga el ZIP C8 al worktree y al manifest G0 que ejecuta el operador."""

    if len(base_file_canonical_sha256) != EXPECTED_ZIP_FILES:
        raise ArchiveGateError("base_inventory_file_count_mismatch")
    base_actual: dict[str, str] = {}
    for name in base_file_canonical_sha256:
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            raise ArchiveGateError(f"local_base_file_missing_or_special:{name}")
        base_actual[name] = _canonical_member_sha256(path.read_bytes(), name)
    if base_actual != base_file_canonical_sha256:
        raise ArchiveGateError("local_full_base_tree_content_mismatch")

    allowed_files = set(base_file_canonical_sha256) | set(G0_OVERLAY_PATHS)
    local_files: set[str] = set()
    ignored_directories = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
    for folder, directories, filenames in os.walk(ROOT):
        directories[:] = [name for name in directories if name not in ignored_directories]
        for filename in filenames:
            path = Path(folder) / filename
            if path.suffix == ".pyc":
                continue
            local_files.add(path.relative_to(ROOT).as_posix())
    unexpected = sorted(local_files - allowed_files)
    missing = sorted(allowed_files - local_files)
    if unexpected or missing:
        raise ArchiveGateError(
            "local_tree_allowlist_mismatch:"
            f"unexpected={','.join(unexpected)};missing={','.join(missing)}"
        )

    critical_actual = _local_text_hashes(CRITICAL_C8_TEXT_SHA256)
    legacy_actual = _local_text_hashes(LEGACY_EFFECT_TEXT_SHA256)
    binary_actual: dict[str, str] = {}
    for name in BINARY_SHA256:
        path = ROOT / name
        if not path.is_file():
            raise ArchiveGateError(f"local_required_file_missing:{name}")
        binary_actual[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if critical_actual != CRITICAL_C8_TEXT_SHA256:
        raise ArchiveGateError("local_critical_c8_content_mismatch")
    if legacy_actual != LEGACY_EFFECT_TEXT_SHA256:
        raise ArchiveGateError("local_legacy_effect_inventory_mismatch")
    if binary_actual != BINARY_SHA256:
        raise ArchiveGateError("local_embedded_signature_asset_mismatch")
    if _snapshot_sha256(critical_actual) != POST_C8_GATE_BASELINE_SNAPSHOT_SHA256:
        raise ArchiveGateError("local_baseline_snapshot_mismatch")
    missing_overlay = [name for name in G0_OVERLAY_PATHS if not (ROOT / name).is_file()]
    if missing_overlay:
        raise ArchiveGateError("local_g0_overlay_missing:" + ",".join(missing_overlay))

    evidence_path = ROOT / "docs/rtm_connect/RTM_CONNECT_POST_C8_G0_EVIDENCE.json"
    try:
        evidence_raw = evidence_path.read_bytes()
        if _canonical_text_sha256(
            evidence_raw,
            evidence_path.as_posix(),
        ) != G0_EVIDENCE_MANIFEST_SHA256:
            raise ArchiveGateError("g0_evidence_manifest_sha256_mismatch")
        evidence = json.loads(evidence_raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArchiveGateError("g0_evidence_manifest_invalid") from exc
    expected_top_level = {
        "authority",
        "version",
        "phase",
        "not_phase",
        "source",
        "assessment",
        "observed_closure_evidence",
        "legacy_effect_inventory",
        "embedded_asset_inventory",
        "scope_limitations",
        "critical_c8_text_sha256",
        "legacy_effect_text_sha256",
        "binary_sha256",
        "overlay_identity",
    }
    if set(evidence) != expected_top_level:
        raise ArchiveGateError("g0_evidence_top_level_allowlist_mismatch")
    if (
        evidence.get("authority") != "rtm_connect_post_c8_g0_evidence"
        or evidence.get("version") != "rtm_connect_post_c8_g0_evidence_v1_0"
        or evidence.get("phase") != "post_c8_g0"
        or evidence.get("not_phase") != "C9"
    ):
        raise ArchiveGateError("g0_evidence_identity_mismatch")
    if evidence.get("source") != {
        "commit_sha40": POST_C8_GATE_BASE_COMMIT_SHA40,
        "archive_sha256": POST_C8_GATE_BASE_ARCHIVE_SHA256,
        "archive_comment": POST_C8_GATE_BASE_COMMIT_SHA40,
        "archive_entries": EXPECTED_ZIP_ENTRIES,
        "archive_files": EXPECTED_ZIP_FILES,
        "critical_snapshot_sha256": POST_C8_GATE_BASELINE_SNAPSHOT_SHA256,
        "crc_valid": True,
        "safe_member_names": True,
    }:
        raise ArchiveGateError("g0_evidence_source_mismatch")
    expected_assessment = post_c8_gate_fingerprint_material(assessment)
    actual_assessment = evidence.get("assessment")
    if not isinstance(actual_assessment, dict):
        raise ArchiveGateError("g0_evidence_assessment_missing")
    expected_assessment_with_hash = dict(expected_assessment)
    expected_assessment_with_hash["assessment_sha256"] = post_c8_gate_sha256(assessment)
    if actual_assessment != expected_assessment_with_hash:
        raise ArchiveGateError("g0_evidence_assessment_mismatch")
    if evidence.get("critical_c8_text_sha256") != CRITICAL_C8_TEXT_SHA256:
        raise ArchiveGateError("g0_evidence_critical_hashes_mismatch")
    if evidence.get("legacy_effect_text_sha256") != LEGACY_EFFECT_TEXT_SHA256:
        raise ArchiveGateError("g0_evidence_legacy_hashes_mismatch")
    if evidence.get("binary_sha256") != BINARY_SHA256:
        raise ArchiveGateError("g0_evidence_binary_hashes_mismatch")
    if evidence.get("scope_limitations") != list(SCOPE_LIMITATIONS):
        raise ArchiveGateError("g0_evidence_scope_limitations_mismatch")
    legacy_inventory = evidence.get("legacy_effect_inventory")
    if (
        not isinstance(legacy_inventory, list)
        or not legacy_inventory
        or any(
            not isinstance(item, dict)
            or set(item) != {"surface", "paths", "risk", "status"}
            or item.get("status") != "blocked"
            for item in legacy_inventory
        )
    ):
        raise ArchiveGateError("g0_legacy_inventory_allowlist_mismatch")
    embedded_inventory = evidence.get("embedded_asset_inventory")
    if (
        not isinstance(embedded_inventory, list)
        or len(embedded_inventory) != 1
        or set(embedded_inventory[0]) != {
            "path", "sha256", "dimensions", "used_by", "classification",
            "required_action", "status",
        }
        or embedded_inventory[0].get("status") != "blocked"
    ):
        raise ArchiveGateError("g0_embedded_inventory_allowlist_mismatch")
    overlay_identity = evidence.get("overlay_identity")
    if not isinstance(overlay_identity, dict) or set(overlay_identity) != {
        "base_commit_sha40",
        "delivery_zip_sha256",
        "git_commit_sha40",
        "integrity_status",
        "paths",
    }:
        raise ArchiveGateError("g0_overlay_identity_allowlist_mismatch")
    if overlay_identity != {
        "base_commit_sha40": POST_C8_GATE_BASE_COMMIT_SHA40,
        "delivery_zip_sha256": None,
        "git_commit_sha40": None,
        "integrity_status": "external_delivery_hash_and_future_git_commit_required",
        "paths": list(G0_OVERLAY_PATHS),
    }:
        raise ArchiveGateError("g0_overlay_identity_not_frozen")
    observed = evidence.get("observed_closure_evidence")
    if (
        not isinstance(observed, dict)
        or set(observed) != {
            "attestation_class", "source", "observed_at",
            "unit_tests_claimed_total", "unit_tests_claimed_skipped",
            "c8_schema_claim", "c8_preflight_claim", "c8_smoke_claim",
            "render_deploy_claim", "render_deploy_id_claim", "health_claim",
            "health_url_claim", "limitation",
        }
        or observed.get("attestation_class") != "observed_unattested"
    ):
        raise ArchiveGateError("g0_observed_evidence_classification_mismatch")
    if any(type(value) is bool for value in observed.values()):
        raise ArchiveGateError("g0_observed_evidence_must_not_assert_boolean_truth")
    return {
        "frozen_base_tree_exact": True,
        "evidence_manifest_exact": True,
        "overlay_paths_present_uncommitted": len(G0_OVERLAY_PATHS),
        "overlay_snapshot_sha256": _snapshot_sha256({
            name: _canonical_text_sha256((ROOT / name).read_bytes(), name)
            for name in G0_OVERLAY_PATHS
        }),
        "base_files_verified": len(base_actual),
        "critical_files_verified": len(critical_actual),
        "legacy_effect_files_frozen_minimum": len(legacy_actual),
        "binary_assets_verified": len(binary_actual),
    }


def _interpreter_isolated() -> bool:
    return bool(
        sys.flags.isolated
        and sys.flags.no_site
        and sys.dont_write_bytecode
    )


def _base_report() -> dict[str, Any]:
    return {
        "ok": False,
        "audit_ok": False,
        "offline_review_reproduced": False,
        "authority": "rtm_connect_post_c8_g0_preflight",
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
        "findings": [],
        "gate_blockers": [],
        "scope_limitations": list(SCOPE_LIMITATIONS),
        "blockers": [],
        "safe": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _base_report()
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
        assessment = assess_post_c8_gate(
            source_commit_sha40=POST_C8_GATE_BASE_COMMIT_SHA40,
            base_archive_sha256=POST_C8_GATE_BASE_ARCHIVE_SHA256,
            baseline_snapshot_sha256=POST_C8_GATE_BASELINE_SNAPSHOT_SHA256,
            evaluated_at=POST_C8_GATE_FROZEN_EVALUATED_AT,
        )
        local_gate_report = audit_local_gate(
            assessment,
            base_file_canonical_sha256,
        )
        guard_blocked = False
        try:
            assert_g0_live_activation_unavailable(assessment=assessment)
        except PostC8LiveActivationUnavailable as exc:
            guard_blocked = exc.code == "g0_live_activation_unavailable"
        report["archive"] = archive_report
        report["local_gate"] = local_gate_report
        report["contract_version"] = POST_C8_GATE_CONTRACT_VERSION
        report["evaluated_at"] = assessment.evaluated_at
        report["fingerprint_material"] = post_c8_gate_fingerprint_material(assessment)
        report["findings"] = [
            {
                "domain": finding.domain.value,
                "status": finding.status,
                "blocker_codes": list(finding.blocker_codes),
            }
            for finding in assessment.findings
        ]
        report["gate_blockers"] = [
            code
            for finding in assessment.findings
            for code in finding.blocker_codes
        ] + list(assessment.activation_blockers)
        report["blockers"] = list(report["gate_blockers"])
        report["checks"] = {
            "exact_archive_sha256": True,
            "exact_commit_comment": True,
            "archive_crc_valid": True,
            "archive_members_safe": True,
            "critical_c8_snapshot_exact": True,
            "legacy_effect_minimum_frozen": True,
            "embedded_signature_asset_inventoried": True,
            "six_domains_blocked": len(assessment.findings) == 6,
            "gate_status_blocked": assessment.gate_status == "blocked",
            "live_verdict_no_go": assessment.live_verdict == "no_go",
            "live_canary_zero": assessment.live_canary_percent == 0,
            "live_activation_guard_unconditional": guard_blocked,
            "g0_overlay_absent_from_base_archive": archive_report[
                "g0_overlay_absent_from_base_archive"
            ],
            "full_frozen_base_tree_matches_archive": local_gate_report[
                "frozen_base_tree_exact"
            ],
            "g0_evidence_manifest_exact": local_gate_report[
                "evidence_manifest_exact"
            ],
            "isolated_no_site_no_bytecode_interpreter": _interpreter_isolated(),
        }
        report["assessment_sha256"] = post_c8_gate_sha256(assessment)
        report["audit_ok"] = all(report["checks"].values())
        report["offline_review_reproduced"] = report["audit_ok"]
        report["ok"] = False
        report["safe"] = False
        report["production_safe"] = False
        _print(report, args.compact)
        return 2
    except (ArchiveGateError, OSError, ValueError, zipfile.BadZipFile) as exc:
        report["blockers"] = [f"post_c8_g0_archive_blocked:{type(exc).__name__}:{exc}"]
        _print(report, args.compact)
        return 2
    except Exception as exc:
        report["blockers"] = [f"unexpected:{type(exc).__name__}:{exc}"]
        _print(report, args.compact)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
