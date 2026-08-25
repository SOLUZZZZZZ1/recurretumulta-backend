from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from scripts import rtm_connect_a1s_runtime_preflight as preflight


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "scripts/rtm_connect_a1s_runtime_preflight.py"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    result.update(
        str(node.module)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    return result


def zip_bytes(
    name: str,
    content: bytes,
    *,
    comment: bytes = b"c" * 40,
) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(name, content)
        archive.comment = comment
    return target.getvalue()


class ConnectA1SRuntimePreflightContractTest(unittest.TestCase):
    def test_exact_base_identity_counts_and_overlay_are_frozen(self):
        self.assertEqual(
            preflight.A1S_RUNTIME_PREFLIGHT_VERSION,
            "rtm_connect_a1s_runtime_preflight_v1_1",
        )
        self.assertEqual(
            preflight.A1S_RUNTIME_BASE_COMMIT_SHA40,
            "a94dcd314c67880e40aa333dc679ef98b80a1956",
        )
        self.assertEqual(
            preflight.A1S_RUNTIME_BASE_ARCHIVE_SHA256,
            "4bed25e3fd30989a617ad3640a63e4a20d98f5347167cf097f91e9d183220c21",
        )
        self.assertEqual(preflight.EXPECTED_ZIP_ENTRIES, 559)
        self.assertEqual(preflight.EXPECTED_ZIP_FILES, 540)
        self.assertEqual(preflight.EXPECTED_ZIP_UNCOMPRESSED_BYTES, 6_991_260)
        self.assertEqual(preflight.EXPECTED_ZIP_COMPRESSED_BYTES, 1_740_549)
        self.assertEqual(preflight.EXPECTED_ZIP_MAX_FILE_BYTES, 174_062)
        self.assertEqual(len(preflight.A1S_RUNTIME_OVERLAY_PATHS), 12)
        self.assertEqual(len(set(preflight.A1S_RUNTIME_OVERLAY_PATHS)), 12)
        self.assertIn(
            "tests/test_rtm_connect_a1s_runtime_preflight_contract.py",
            preflight.A1S_RUNTIME_OVERLAY_PATHS,
        )

    def test_auditor_is_standard_library_only_and_never_imports_runtime(self):
        modules = imported_modules(PREFLIGHT)
        roots = {module.split(".", 1)[0] for module in modules}
        self.assertTrue(
            {
                "app",
                "b2_storage",
                "database",
                "psycopg",
                "psycopg2",
                "requests",
                "rtm_connect",
                "sqlalchemy",
                "subprocess",
            }.isdisjoint(roots)
        )
        source = PREFLIGHT.read_text(encoding="utf-8")
        tree = ast.parse(source)
        attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        self.assertNotIn("extract", attributes)
        self.assertNotIn("extractall", attributes)
        self.assertNotIn("writestr", attributes)
        self.assertNotIn("write_bytes", attributes)
        self.assertNotIn("write_text", attributes)
        self.assertNotIn("open", names)
        self.assertIn("sys.dont_write_bytecode = True", source)

    def test_cli_has_only_archive_and_compact_arguments(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        self.assertIn('add_argument("--archive", required=True)', source)
        self.assertIn('add_argument("--compact", action="store_true")', source)
        for forbidden in (
            'add_argument("--apply"',
            'add_argument("--database"',
            'add_argument("--endpoint"',
            'add_argument("--token"',
        ):
            self.assertNotIn(forbidden, source)

    def test_member_safety_rejects_escape_alias_and_special_types(self):
        safe = zipfile.ZipInfo("folder/file.txt")
        safe.external_attr = (stat.S_IFREG | 0o644) << 16
        self.assertTrue(preflight._safe_member(safe))
        for name in (
            "../escape.txt",
            "folder/../escape.txt",
            "/absolute.txt",
            "C:/drive.txt",
            "folder\\file.txt",
            "folder/NUL.txt",
            "folder/file.txt.",
        ):
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            self.assertFalse(preflight._safe_member(info), name)
        symlink = zipfile.ZipInfo("link")
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.assertFalse(preflight._safe_member(symlink))

    def test_archive_audit_hashes_every_file_without_extraction(self):
        raw = zip_bytes("base.txt", b"base\r\n")
        archive_sha256 = hashlib.sha256(raw).hexdigest()
        member_sha256 = hashlib.sha256(b"base\r\n").hexdigest()
        canonical_sha256 = hashlib.sha256(b"base\n").hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "base.zip"
            path.write_bytes(raw)
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                info = archive.infolist()[0]
            with (
                mock.patch.object(
                    preflight,
                    "A1S_RUNTIME_BASE_ARCHIVE_SHA256",
                    archive_sha256,
                ),
                mock.patch.object(
                    preflight,
                    "A1S_RUNTIME_BASE_COMMIT_SHA40",
                    "c" * 40,
                ),
                mock.patch.object(preflight, "EXPECTED_ZIP_ENTRIES", 1),
                mock.patch.object(preflight, "EXPECTED_ZIP_FILES", 1),
                mock.patch.object(
                    preflight,
                    "EXPECTED_ZIP_UNCOMPRESSED_BYTES",
                    info.file_size,
                ),
                mock.patch.object(
                    preflight,
                    "EXPECTED_ZIP_COMPRESSED_BYTES",
                    info.compress_size,
                ),
                mock.patch.object(
                    preflight,
                    "EXPECTED_ZIP_MAX_FILE_BYTES",
                    info.file_size,
                ),
                mock.patch.object(
                    preflight,
                    "A1S_RUNTIME_OVERLAY_PATHS",
                    ("overlay.py",),
                ),
            ):
                report = preflight.audit_archive(path)
        self.assertTrue(report["safe_members"])
        self.assertTrue(report["crc_ok"])
        self.assertEqual(report["_base_file_sha256"], {"base.txt": member_sha256})
        self.assertEqual(
            report["_base_file_canonical_sha256"],
            {"base.txt": canonical_sha256},
        )
        self.assertEqual(
            report["full_base_snapshot_sha256"],
            preflight._snapshot_sha256({"base.txt": member_sha256}),
        )

    def test_archive_audit_fails_closed_on_wrong_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "wrong.zip"
            path.write_bytes(zip_bytes("x", b"x"))
            with self.assertRaises(preflight.A1SRuntimePreflightError) as raised:
                preflight.audit_archive(path)
        self.assertEqual(str(raised.exception), "archive_sha256_mismatch")

    def test_local_tree_allows_only_portable_newlines_and_exact_allowlist(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "base.txt").write_bytes(b"base\r\n")
            (root / "overlay.py").write_text("VALUE = 1\n", encoding="utf-8")
            base = {"base.txt": hashlib.sha256(b"base\r\n").hexdigest()}
            canonical = {"base.txt": hashlib.sha256(b"base\n").hexdigest()}
            with (
                mock.patch.object(preflight, "EXPECTED_ZIP_FILES", 1),
                mock.patch.object(
                    preflight,
                    "A1S_RUNTIME_OVERLAY_PATHS",
                    ("overlay.py",),
                ),
            ):
                report = preflight.audit_local_tree(base, canonical, root)
                self.assertTrue(report["frozen_base_tree_content_equivalent"])
                self.assertTrue(report["frozen_base_tree_byte_exact"])
                self.assertEqual(report["raw_byte_exact_files"], 1)
                self.assertEqual(report["newline_canonical_equivalent_files"], 0)
                (root / "unexpected.txt").write_text("x", encoding="utf-8")
                with self.assertRaises(preflight.A1SRuntimePreflightError):
                    preflight.audit_local_tree(base, canonical, root)
                (root / "unexpected.txt").unlink()
                (root / "base.txt").write_bytes(b"base\n")
                report = preflight.audit_local_tree(base, canonical, root)
                self.assertFalse(report["frozen_base_tree_byte_exact"])
                self.assertEqual(report["raw_byte_exact_files"], 0)
                self.assertEqual(report["newline_canonical_equivalent_files"], 1)
                (root / "base.txt").write_bytes(b"changed\n")
                with self.assertRaises(preflight.A1SRuntimePreflightError) as raised:
                    preflight.audit_local_tree(base, canonical, root)
        self.assertIn("local_full_base_tree_content_mismatch", str(raised.exception))

    def test_canonical_member_relaxes_only_strict_utf8_crlf(self):
        equivalent = (
            (b"base\r\n", b"base\n"),
            (b"base\n", b"base\r\n"),
            ("mañana\r\n".encode(), "mañana\n".encode()),
        )
        for left, right in equivalent:
            self.assertEqual(
                preflight._canonical_member_sha256(left, "text.txt"),
                preflight._canonical_member_sha256(right, "text.txt"),
            )

        different = (
            (b"base\r", b"base\n"),
            (b"\x00binary\r\n", b"\x00binary\n"),
            (b"\x01binary\r\n", b"\x01binary\n"),
            (b"\xffbinary\r\n", b"\xffbinary\n"),
            (b"base\r\n", b"changed\n"),
            (b"\xef\xbb\xbfbase\r\n", b"base\n"),
            (b"base\r\n", b"base"),
        )
        for left, right in different:
            self.assertNotEqual(
                preflight._canonical_member_sha256(left, "member.bin"),
                preflight._canonical_member_sha256(right, "member.bin"),
            )

    def test_local_tree_keeps_binary_comparison_byte_exact(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive_bytes = b"\xffbinary\r\n"
            (root / "base.bin").write_bytes(b"\xffbinary\n")
            (root / "overlay.py").write_text("VALUE = 1\n", encoding="utf-8")
            base = {"base.bin": hashlib.sha256(archive_bytes).hexdigest()}
            canonical = {
                "base.bin": preflight._canonical_member_sha256(
                    archive_bytes,
                    "base.bin",
                )
            }
            with (
                mock.patch.object(preflight, "EXPECTED_ZIP_FILES", 1),
                mock.patch.object(
                    preflight,
                    "A1S_RUNTIME_OVERLAY_PATHS",
                    ("overlay.py",),
                ),
            ):
                with self.assertRaises(preflight.A1SRuntimePreflightError) as raised:
                    preflight.audit_local_tree(base, canonical, root)
        self.assertIn("local_full_base_tree_content_mismatch", str(raised.exception))

    def test_overlay_ast_rejects_direct_and_dynamic_network_or_b2(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "runtime.py"
            path.write_text(
                "import requests\n"
                "import importlib\n"
                "importlib.import_module('boto3')\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                preflight,
                "A1S_RUNTIME_OVERLAY_PATHS",
                ("runtime.py",),
            ):
                report = preflight.audit_overlay_ast(root)
        self.assertFalse(report["network_transport_b2_ast_absent"])
        self.assertIn("runtime.py:requests", report["forbidden_imports"])
        self.assertTrue(
            any(item.endswith("dynamic:boto3") for item in report["forbidden_calls"])
        )

    def test_report_success_is_machine_readable_offline_no_go(self):
        fake_archive = {
            "crc_ok": True,
            "safe_members": True,
            "runtime_overlay_absent_from_base_archive": True,
            "_base_file_sha256": {"base.txt": "0" * 64},
            "_base_file_canonical_sha256": {"base.txt": "0" * 64},
        }
        fake_tree = {
            "frozen_base_tree_content_equivalent": True,
            "overlay_paths_present": len(preflight.A1S_RUNTIME_OVERLAY_PATHS),
        }
        fake_ast = {
            "missing_python_paths": [],
            "invalid_python": [],
            "forbidden_imports": [],
            "forbidden_calls": [],
            "network_transport_b2_ast_absent": True,
            "runtime_imported": False,
        }
        with (
            mock.patch.object(preflight, "audit_archive", return_value=fake_archive),
            mock.patch.object(preflight, "audit_local_tree", return_value=fake_tree),
            mock.patch.object(preflight, "audit_overlay_ast", return_value=fake_ast),
            mock.patch.object(preflight, "_interpreter_isolated", return_value=True),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = preflight.main(["--archive", "base.zip", "--compact"])
        payload = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertTrue(payload["audit_ok"])
        self.assertTrue(payload["safe"])
        self.assertTrue(payload["offline_only"])
        self.assertFalse(payload["runtime_imported"])
        self.assertFalse(payload["network_used"])
        self.assertFalse(payload["database_touched"])
        self.assertEqual(payload["live_verdict"], "no_go")
        self.assertFalse(payload["production_authorized"])

    def test_real_cli_requires_and_recognizes_isolated_no_site_no_bytecode(self):
        with tempfile.TemporaryDirectory() as folder:
            wrong = Path(folder) / "wrong.zip"
            wrong.write_bytes(zip_bytes("x", b"x"))
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    str(PREFLIGHT),
                    "--archive",
                    str(wrong),
                    "--compact",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stdout)
        self.assertTrue(
            payload["checks"]["isolated_no_site_no_bytecode_interpreter"]
        )
        self.assertTrue(payload["blockers"])
        self.assertFalse(payload["audit_ok"])

        without_dash_b = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                str(PREFLIGHT),
                "--archive",
                str(wrong),
                "--compact",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        without_dash_b_payload = json.loads(without_dash_b.stdout)
        self.assertFalse(
            without_dash_b_payload["checks"][
                "isolated_no_site_no_bytecode_interpreter"
            ]
        )


if __name__ == "__main__":
    unittest.main()
