#!/usr/bin/env python3
"""Auditor offline y fail-closed del overlay RTM CONNECT A1-S Runtime.

El auditor usa exclusivamente la biblioteca estandar. No importa el runtime,
no extrae el ZIP, no abre PostgreSQL y no resuelve secretos ni transportes.
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

A1S_RUNTIME_PREFLIGHT_VERSION = "rtm_connect_a1s_runtime_preflight_v1_1"
A1S_RUNTIME_CONTRACT_VERSION = "rtm.connect.a1s.runtime.v1"
A1S_RUNTIME_BASE_COMMIT_SHA40 = "a94dcd314c67880e40aa333dc679ef98b80a1956"
A1S_RUNTIME_BASE_ARCHIVE_SHA256 = (
    "4bed25e3fd30989a617ad3640a63e4a20d98f5347167cf097f91e9d183220c21"
)
EXPECTED_ZIP_ENTRIES = 559
EXPECTED_ZIP_FILES = 540
EXPECTED_ZIP_UNCOMPRESSED_BYTES = 6_991_260
EXPECTED_ZIP_COMPRESSED_BYTES = 1_740_549
EXPECTED_ZIP_MAX_FILE_BYTES = 174_062

A1S_RUNTIME_OVERLAY_PATHS = (
    "rtm_connect/human_filing_runtime.py",
    "scripts/rtm_staging_connect_a1s_runtime_fixture.py",
    "scripts/rtm_connect_a1s_runtime_preflight.py",
    "scripts/rtm_connect_a1s_runtime_smoke.py",
    "docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME.md",
    "docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME_EVIDENCE.json",
    "docs/rtm_connect/adrs/0019-a1s-runtime-validation.md",
    "tests/test_rtm_connect_a1s_runtime_contract.py",
    "tests/test_rtm_connect_a1s_runtime_fixture_script_contract.py",
    "tests/test_rtm_connect_a1s_runtime_preflight_contract.py",
    "tests/test_rtm_connect_a1s_runtime_smoke_contract.py",
    "tests/test_rtm_connect_a1s_runtime_docs_contract.py",
)

_NETWORK_OR_B2_IMPORT_ROOTS = frozenset({
    "aiohttp",
    "b2_storage",
    "boto3",
    "botocore",
    "dgt_client",
    "ftplib",
    "httpx",
    "paramiko",
    "requests",
    "smtplib",
    "socket",
    "ssl",
    "submitter_dgt",
    "submitters",
    "websockets",
})
_NETWORK_OR_B2_IMPORT_MODULES = frozenset({
    "http.client",
    "rtm_b2_storage",
    "rtm_connect.production_transport",
    "rtm_connect.provider_sandbox_transport",
    "rtm_connect.provider_transport",
    "submitters.registro",
    "urllib.request",
})
_NETWORK_OR_B2_CALL_NAMES = frozenset({
    "HTTPConnection",
    "HTTPSConnection",
    "create_connection",
    "download_file_by_name",
    "get_b2_bucket",
    "get_s3_client",
    "get_file_info_by_name",
    "upload_bytes",
    "upload_local_file",
    "urlopen",
    "urlretrieve",
})
_AUDITOR_FORBIDDEN_IMPORT_ROOTS = _NETWORK_OR_B2_IMPORT_ROOTS | frozenset({
    "app",
    "database",
    "psycopg",
    "psycopg2",
    "rtm_connect",
    "sqlalchemy",
    "subprocess",
})
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


class A1SRuntimePreflightError(ValueError):
    """La entrega no satisface el limite offline de A1-S Runtime."""


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
    """Canonicaliza CRLF solo para texto UTF-8 estricto sin controles."""

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise A1SRuntimePreflightError(f"utf8_required:{name}") from exc
    allowed_controls = {"\t", "\n", "\r"}
    if any(
        unicodedata.category(character) == "Cc"
        and character not in allowed_controls
        for character in text
    ):
        raise A1SRuntimePreflightError(f"text_control_forbidden:{name}")
    normalized = text.replace("\r\n", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _canonical_member_sha256(raw: bytes, name: str) -> str:
    """Normaliza texto; conserva comparacion byte a byte para binarios."""

    try:
        return _canonical_text_sha256(raw, name)
    except A1SRuntimePreflightError:
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


def audit_archive(path: Path) -> dict[str, Any]:
    """Valida el ZIP exacto en memoria y devuelve su inventario de ficheros."""

    if path.is_symlink() or not path.is_file():
        raise A1SRuntimePreflightError("archive_not_found_or_symlink")
    try:
        with path.open("rb") as handle:
            actual_sha256 = _stream_sha256(handle)
            if actual_sha256 != A1S_RUNTIME_BASE_ARCHIVE_SHA256:
                raise A1SRuntimePreflightError("archive_sha256_mismatch")
            handle.seek(0)
            with zipfile.ZipFile(handle, "r") as archive:
                try:
                    comment = archive.comment.decode("ascii", errors="strict")
                except UnicodeDecodeError as exc:
                    raise A1SRuntimePreflightError(
                        "archive_comment_not_ascii"
                    ) from exc
                if comment != A1S_RUNTIME_BASE_COMMIT_SHA40:
                    raise A1SRuntimePreflightError(
                        "archive_commit_comment_mismatch"
                    )
                infos = archive.infolist()
                files = [info for info in infos if not info.is_dir()]
                if len(infos) != EXPECTED_ZIP_ENTRIES:
                    raise A1SRuntimePreflightError("archive_entry_count_mismatch")
                if len(files) != EXPECTED_ZIP_FILES:
                    raise A1SRuntimePreflightError("archive_file_count_mismatch")
                if sum(info.file_size for info in infos) != EXPECTED_ZIP_UNCOMPRESSED_BYTES:
                    raise A1SRuntimePreflightError(
                        "archive_uncompressed_size_mismatch"
                    )
                if sum(info.compress_size for info in infos) != EXPECTED_ZIP_COMPRESSED_BYTES:
                    raise A1SRuntimePreflightError(
                        "archive_compressed_size_mismatch"
                    )
                if not files or max(info.file_size for info in files) != EXPECTED_ZIP_MAX_FILE_BYTES:
                    raise A1SRuntimePreflightError("archive_max_file_size_mismatch")
                if any(not _safe_member(info) for info in infos):
                    raise A1SRuntimePreflightError("archive_unsafe_member")
                folded = [
                    unicodedata.normalize("NFKC", info.filename).casefold()
                    for info in infos
                ]
                if len(folded) != len(set(folded)):
                    raise A1SRuntimePreflightError("archive_casefold_duplicate")
                bad_crc = archive.testzip()
                if bad_crc is not None:
                    raise A1SRuntimePreflightError(
                        f"archive_crc_failed:{bad_crc}"
                    )
                names = {info.filename for info in infos}
                contaminated = sorted(set(A1S_RUNTIME_OVERLAY_PATHS) & names)
                if contaminated:
                    raise A1SRuntimePreflightError(
                        "runtime_overlay_present_in_frozen_base:"
                        + ",".join(contaminated)
                    )
                base_file_sha256 = {
                    info.filename: hashlib.sha256(
                        archive.read(info.filename)
                    ).hexdigest()
                    for info in files
                }
                base_file_canonical_sha256 = {
                    info.filename: _canonical_member_sha256(
                        archive.read(info.filename),
                        info.filename,
                    )
                    for info in files
                }
    except zipfile.BadZipFile as exc:
        raise A1SRuntimePreflightError("archive_invalid_zip") from exc
    snapshot = _snapshot_sha256(base_file_sha256)
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
        "runtime_overlay_absent_from_base_archive": True,
        "full_base_snapshot_sha256": snapshot,
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
    """Compara contenido portable del arbol base y limita el overlay."""

    if len(base_file_sha256) != EXPECTED_ZIP_FILES:
        raise A1SRuntimePreflightError("base_inventory_file_count_mismatch")
    if set(base_file_sha256) != set(base_file_canonical_sha256):
        raise A1SRuntimePreflightError("base_inventory_canonical_mismatch")
    actual_raw: dict[str, str] = {}
    actual_canonical: dict[str, str] = {}
    for relative in base_file_sha256:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise A1SRuntimePreflightError(
                f"local_base_file_missing_or_special:{relative}"
            )
        raw = path.read_bytes()
        actual_raw[relative] = hashlib.sha256(raw).hexdigest()
        actual_canonical[relative] = _canonical_member_sha256(raw, relative)
    if actual_canonical != base_file_canonical_sha256:
        changed = sorted(
            name
            for name, digest in actual_canonical.items()
            if digest != base_file_canonical_sha256[name]
        )
        raise A1SRuntimePreflightError(
            "local_full_base_tree_content_mismatch:" + ",".join(changed)
        )

    raw_byte_exact_files = sum(
        digest == base_file_sha256[name]
        for name, digest in actual_raw.items()
    )

    local_files, special_entries = _walk_local_files(root)
    allowed = set(base_file_sha256) | set(A1S_RUNTIME_OVERLAY_PATHS)
    unexpected = sorted(local_files - allowed)
    missing = sorted(allowed - local_files)
    if unexpected or missing or special_entries:
        raise A1SRuntimePreflightError(
            "local_tree_allowlist_mismatch:"
            f"unexpected={','.join(unexpected)};"
            f"missing={','.join(missing)};"
            f"special={','.join(special_entries)}"
        )
    overlay_sha256 = {
        relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
        for relative in A1S_RUNTIME_OVERLAY_PATHS
    }
    return {
        "base_files_verified": len(actual_canonical),
        "frozen_base_tree_content_equivalent": True,
        "frozen_base_tree_byte_exact": (
            raw_byte_exact_files == len(actual_canonical)
        ),
        "comparison_mode": "strict_utf8_crlf_to_lf_or_binary_raw_v1",
        "raw_byte_exact_files": raw_byte_exact_files,
        "newline_canonical_equivalent_files": (
            len(actual_canonical) - raw_byte_exact_files
        ),
        "overlay_paths_required": len(A1S_RUNTIME_OVERLAY_PATHS),
        "overlay_paths_present": len(A1S_RUNTIME_OVERLAY_PATHS),
        "unexpected_paths": [],
        "special_entries": [],
        "overlay_snapshot_sha256": _snapshot_sha256(overlay_sha256),
    }


def _import_names(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
            imports.update(
                f"{node.module}.{alias.name}"
                for alias in node.names
                if alias.name != "*"
            )
    return imports


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _dynamic_import_target(node: ast.Call) -> str | None:
    name = _call_name(node)
    if name not in {"__import__", "import_module"} or not node.args:
        return None
    value = node.args[0]
    return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None


def _forbidden_module(module: str) -> bool:
    root = module.split(".", 1)[0]
    return root in _NETWORK_OR_B2_IMPORT_ROOTS or module in _NETWORK_OR_B2_IMPORT_MODULES


def audit_overlay_ast(root: Path = ROOT) -> dict[str, Any]:
    """Parsea el overlay sin ejecutarlo y rechaza red, transports y B2."""

    python_paths = tuple(
        relative for relative in A1S_RUNTIME_OVERLAY_PATHS
        if relative.endswith(".py")
    )
    missing: list[str] = []
    invalid: list[str] = []
    forbidden_imports: list[str] = []
    forbidden_calls: list[str] = []
    for relative in python_paths:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            missing.append(relative)
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, UnicodeError, SyntaxError) as exc:
            invalid.append(f"{relative}:{type(exc).__name__}")
            continue
        imports = _import_names(tree)
        for module in sorted(imports):
            if _forbidden_module(module):
                forbidden_imports.append(f"{relative}:{module}")
        if relative == "scripts/rtm_connect_a1s_runtime_preflight.py":
            for module in sorted(imports):
                if module.split(".", 1)[0] in _AUDITOR_FORBIDDEN_IMPORT_ROOTS:
                    forbidden_imports.append(f"{relative}:auditor:{module}")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = _call_name(node)
            if call_name in _NETWORK_OR_B2_CALL_NAMES:
                forbidden_calls.append(
                    f"{relative}:{getattr(node, 'lineno', 0)}:{call_name}"
                )
            target = _dynamic_import_target(node)
            if target and _forbidden_module(target):
                forbidden_calls.append(
                    f"{relative}:{getattr(node, 'lineno', 0)}:dynamic:{target}"
                )
    return {
        "python_paths_required": len(python_paths),
        "python_paths_parsed": len(python_paths) - len(missing) - len(invalid),
        "missing_python_paths": sorted(missing),
        "invalid_python": sorted(invalid),
        "forbidden_imports": sorted(set(forbidden_imports)),
        "forbidden_calls": sorted(set(forbidden_calls)),
        "network_transport_b2_ast_absent": not (
            missing or invalid or forbidden_imports or forbidden_calls
        ),
        "runtime_imported": False,
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
    ast_audit: dict[str, Any] | None = None
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
        ast_audit = audit_overlay_ast(root)
        blockers.extend(
            f"overlay_python_missing:{item}"
            for item in ast_audit["missing_python_paths"]
        )
        blockers.extend(
            f"overlay_python_invalid:{item}"
            for item in ast_audit["invalid_python"]
        )
        blockers.extend(
            f"forbidden_overlay_import:{item}"
            for item in ast_audit["forbidden_imports"]
        )
        blockers.extend(
            f"forbidden_overlay_call:{item}"
            for item in ast_audit["forbidden_calls"]
        )
    except Exception as exc:
        blockers.append(
            f"a1s_runtime_preflight_blocked:{type(exc).__name__}:{exc}"
        )
    audit_ok = not blockers
    return {
        "ok": audit_ok,
        "safe": audit_ok,
        "audit_ok": audit_ok,
        "authority": "rtm_connect_a1s_runtime_preflight",
        "version": A1S_RUNTIME_PREFLIGHT_VERSION,
        "contract_version": A1S_RUNTIME_CONTRACT_VERSION,
        "base_commit_sha40": A1S_RUNTIME_BASE_COMMIT_SHA40,
        "base_archive_sha256": A1S_RUNTIME_BASE_ARCHIVE_SHA256,
        "archive": archive,
        "local_tree": local_tree,
        "ast_audit": ast_audit,
        "blockers": blockers,
        "checks": {
            "isolated_no_site_no_bytecode_interpreter": isolated,
            "exact_archive_sha256": archive is not None,
            "exact_commit_comment": archive is not None,
            "archive_crc_valid": bool(archive and archive["crc_ok"]),
            "archive_members_safe": bool(archive and archive["safe_members"]),
            "runtime_overlay_absent_from_base_archive": bool(
                archive and archive["runtime_overlay_absent_from_base_archive"]
            ),
            "full_frozen_base_tree_matches_archive": bool(
                local_tree
                and local_tree["frozen_base_tree_content_equivalent"]
            ),
            "runtime_overlay_allowlist_exact": bool(
                local_tree
                and local_tree["overlay_paths_present"]
                == len(A1S_RUNTIME_OVERLAY_PATHS)
            ),
            "overlay_ast_has_no_network_transport_or_b2": bool(
                ast_audit and ast_audit["network_transport_b2_ast_absent"]
            ),
            "runtime_modules_not_imported": bool(
                ast_audit and not ast_audit["runtime_imported"]
            ),
        },
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
        "live_verdict": "no_go",
        "production_authorized": False,
        "production_safe": False,
        "scope_limitations": [
            "preflight_is_static_and_does_not_execute_runtime_or_postgresql",
            "ast_review_cannot_prove_absence_of_all_indirect_transport_behavior",
            "archive_hash_and_comment_do_not_prove_commit_authorship",
            "a1s_runtime_remains_synthetic_only_and_live_filing_remains_no_go",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = build_report(Path(args.archive).resolve())
    _print(report, args.compact)
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
