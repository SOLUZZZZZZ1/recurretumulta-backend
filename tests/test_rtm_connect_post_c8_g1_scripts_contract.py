from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from scripts import rtm_connect_post_c8_g1_preflight as preflight
from scripts import rtm_connect_post_c8_g1_smoke as smoke


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "rtm_connect_post_c8_g1.py"
PREFLIGHT = ROOT / "scripts/rtm_connect_post_c8_g1_preflight.py"
SMOKE = ROOT / "scripts/rtm_connect_post_c8_g1_smoke.py"
APP = ROOT / "app.py"
PACKAGE_INIT = ROOT / "rtm_connect/__init__.py"


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    roots.update(
        str(node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    return roots


def zip_bytes(name: str, content: bytes, *, comment: bytes = b"c" * 40) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(name, content)
        archive.comment = comment
    return target.getvalue()


class ConnectPostC8G1ScriptsContractTest(unittest.TestCase):
    def test_scripts_exist_compile_and_have_only_archive_compact_arguments(self):
        for path in (MODULE, PREFLIGHT, SMOKE):
            self.assertTrue(path.is_file(), path)
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        for path in (PREFLIGHT, SMOKE):
            text = path.read_text(encoding="utf-8")
            self.assertIn('add_argument("--archive", required=True)', text)
            self.assertIn('add_argument("--compact", action="store_true")', text)
            self.assertNotIn('add_argument("--apply"', text)
            self.assertNotIn('add_argument("--provider"', text)
            self.assertNotIn('add_argument("--endpoint"', text)
            self.assertNotIn('add_argument("--token"', text)

    def test_scripts_import_no_network_database_secret_or_process_modules(self):
        forbidden = {
            "requests", "urllib", "http", "socket", "ssl", "sqlalchemy",
            "psycopg", "psycopg2", "subprocess", "boto3", "stripe",
            "smtplib", "app", "database",
        }
        for path in (PREFLIGHT, SMOKE):
            self.assertTrue(forbidden.isdisjoint(imported_roots(path)), path)

    def test_preflight_never_extracts_or_writes_archive_members(self):
        tree = ast.parse(PREFLIGHT.read_text(encoding="utf-8"))
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

    def test_g1_is_not_wired_into_runtime(self):
        for path in (APP, PACKAGE_INIT):
            text = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("post_c8_g1", text)
            self.assertNotIn("provider_admission", text)

    def test_expected_base_archive_identity_and_counts_are_frozen(self):
        self.assertEqual(preflight.EXPECTED_ZIP_ENTRIES, 533)
        self.assertEqual(preflight.EXPECTED_ZIP_FILES, 514)
        self.assertEqual(preflight.EXPECTED_ZIP_UNCOMPRESSED_BYTES, 6_426_113)
        self.assertEqual(preflight.EXPECTED_ZIP_COMPRESSED_BYTES, 1_622_311)
        self.assertEqual(preflight.EXPECTED_ZIP_MAX_FILE_BYTES, 174_062)
        self.assertEqual(len(preflight.BASE_CRITICAL_TEXT_SHA256), 22)
        self.assertEqual(len(preflight.G1_OVERLAY_PATHS), 9)

    def test_member_safety_rejects_traversal_backslash_absolute_and_symlink(self):
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
        link = zipfile.ZipInfo("link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.assertFalse(preflight._safe_member(link))

    def test_valid_archive_path_uses_one_descriptor_and_verifies_content(self):
        raw = zip_bytes("base.txt", b"base\r\n")
        archive_hash = hashlib.sha256(raw).hexdigest()
        canonical_hash = hashlib.sha256(b"base\n").hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "base.zip"
            path.write_bytes(raw)
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                info = archive.infolist()[0]
            with (
                mock.patch.object(
                    preflight,
                    "POST_C8_G1_BASE_ARCHIVE_SHA256",
                    archive_hash,
                ),
                mock.patch.object(
                    preflight,
                    "POST_C8_G1_BASE_COMMIT_SHA40",
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
                    "BASE_CRITICAL_TEXT_SHA256",
                    {"base.txt": canonical_hash},
                ),
                mock.patch.object(
                    preflight,
                    "POST_C8_G1_BASELINE_SNAPSHOT_SHA256",
                    preflight._snapshot_sha256({"base.txt": canonical_hash}),
                ),
                mock.patch.object(preflight, "G1_OVERLAY_PATHS", ("g1.py",)),
            ):
                report = preflight.audit_archive(path)
        self.assertTrue(report["crc_ok"])
        self.assertTrue(report["safe_members"])
        self.assertEqual(report["critical_files_verified"], 1)
        self.assertEqual(
            report["_base_file_canonical_sha256"],
            {"base.txt": canonical_hash},
        )

    def test_wrong_archive_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "wrong.zip"
            path.write_bytes(zip_bytes("x.txt", b"x"))
            with self.assertRaises(preflight.ArchiveAdmissionError) as raised:
                preflight.audit_archive(path)
        self.assertIn("archive_sha256_mismatch", str(raised.exception))

    def test_preflight_success_is_machine_readable_no_go_exit_two(self):
        fake_archive = {
            "crc_ok": True,
            "safe_members": True,
            "g1_overlay_absent_from_base_archive": True,
            "_base_file_canonical_sha256": {"base.txt": "0" * 64},
        }
        fake_local = {
            "g0_delivery_identity_frozen": True,
            "frozen_base_tree_exact": True,
            "evidence_manifest_exact": True,
        }
        with (
            mock.patch.object(preflight, "audit_archive", return_value=fake_archive),
            mock.patch.object(preflight, "audit_local_gate", return_value=fake_local),
            mock.patch.object(preflight, "_interpreter_isolated", return_value=True),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = preflight.main(["--archive", "base.zip", "--compact"])
        payload = json.loads(output.getvalue())
        self.assertEqual(result, 2)
        self.assertTrue(payload["audit_ok"])
        self.assertTrue(payload["offline_review_reproduced"])
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["safe"])
        self.assertFalse(payload["production_safe"])
        self.assertEqual(payload["live_verdict"], "no_go")
        self.assertFalse(payload["production_authorized"])

    def test_preflight_error_remains_no_go_exit_two(self):
        with (
            mock.patch.object(
                preflight,
                "audit_archive",
                side_effect=preflight.ArchiveAdmissionError("blocked"),
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = preflight.main(["--archive", "wrong.zip", "--compact"])
        payload = json.loads(output.getvalue())
        self.assertEqual(result, 2)
        self.assertFalse(payload["audit_ok"])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["live_verdict"], "no_go")
        self.assertTrue(payload["blockers"])

    def test_smoke_exercises_mutations_guard_and_no_runtime_surface(self):
        source = SMOKE.read_text(encoding="utf-8")
        for literal in (
            '"provider_selected"',
            '"provider_pack_admissible"',
            '"production_authorized"',
            '"network_allowed"',
            '"external_effects_allowed"',
            '"g0_no_go_overridden"',
            '{"live_verdict": "go"}',
            '{"live_canary_percent": 1}',
            "assert_g1_live_activation_unavailable",
        ):
            self.assertIn(literal, source)

    def test_smoke_success_is_machine_readable_no_go_exit_two(self):
        fake_archive = {
            "crc_ok": True,
            "safe_members": True,
            "_base_file_canonical_sha256": {"base.txt": "0" * 64},
        }
        fake_local = {
            "frozen_base_tree_exact": True,
            "evidence_manifest_exact": True,
        }
        with (
            mock.patch.object(smoke, "audit_archive", return_value=fake_archive),
            mock.patch.object(smoke, "audit_local_gate", return_value=fake_local),
            mock.patch.object(smoke, "_interpreter_isolated", return_value=True),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = smoke.main(["--archive", "base.zip", "--compact"])
        payload = json.loads(output.getvalue())
        self.assertEqual(result, 2)
        self.assertTrue(payload["tests_ok"])
        self.assertTrue(payload["audit_ok"])
        self.assertTrue(payload["offline_review_reproduced"])
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["safe"])
        self.assertFalse(payload["production_safe"])
        self.assertEqual(payload["gate_status"], "blocked")
        self.assertEqual(payload["live_verdict"], "no_go")
        self.assertEqual(payload["legacy_candidates_total"], 3)
        self.assertTrue(payload["blockers"])

    def test_smoke_unrelated_runtime_error_does_not_count_as_guard(self):
        fake_archive = {
            "crc_ok": True,
            "safe_members": True,
            "_base_file_canonical_sha256": {"base.txt": "0" * 64},
        }
        fake_local = {
            "frozen_base_tree_exact": True,
            "evidence_manifest_exact": True,
        }
        with (
            mock.patch.object(smoke, "audit_archive", return_value=fake_archive),
            mock.patch.object(smoke, "audit_local_gate", return_value=fake_local),
            mock.patch.object(
                smoke,
                "assert_g1_live_activation_unavailable",
                side_effect=RuntimeError("wrong"),
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = smoke.main(["--archive", "base.zip", "--compact"])
        payload = json.loads(output.getvalue())
        self.assertEqual(result, 1)
        self.assertFalse(payload["audit_ok"])
        self.assertFalse(payload["ok"])
        self.assertIn("unexpected:RuntimeError:wrong", payload["blockers"][0])


if __name__ == "__main__":
    unittest.main()
