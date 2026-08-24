from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from scripts import rtm_connect_post_c8_g0_preflight as preflight
from scripts import rtm_connect_post_c8_g0_smoke as smoke


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "rtm_connect_post_c8_g0.py"
PREFLIGHT = ROOT / "scripts" / "rtm_connect_post_c8_g0_preflight.py"
SMOKE = ROOT / "scripts" / "rtm_connect_post_c8_g0_smoke.py"
APP = ROOT / "app.py"
PACKAGE_INIT = ROOT / "rtm_connect" / "__init__.py"
CONTROL = ROOT / "rtm_connect" / "production_control.py"


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


def literal_all(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
        ):
            value = ast.literal_eval(node.value)
            return list(value)
    raise AssertionError(f"__all__ no encontrado en {path}")


class ConnectPostC8G0ScriptsContractTest(unittest.TestCase):
    def test_scripts_exist_compile_and_have_only_archive_compact_arguments(self):
        for path in (PREFLIGHT, SMOKE):
            self.assertTrue(path.is_file(), path.name)
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            source = path.read_text(encoding="utf-8")
            self.assertIn('add_argument("--archive", required=True)', source)
            self.assertIn('add_argument("--compact"', source)
            self.assertNotIn('add_argument("--apply"', source)
            self.assertNotIn('add_argument("--confirmation"', source)
            for forbidden_arg in (
                "--endpoint",
                "--provider",
                "--tenant",
                "--token",
                "--secret",
                "--credential",
                "--database",
                "--url",
            ):
                self.assertNotIn(forbidden_arg, source)

    def test_scripts_import_no_network_database_app_secret_or_process_modules(self):
        forbidden = {
            "fastapi",
            "sqlalchemy",
            "database",
            "requests",
            "httpx",
            "urllib",
            "socket",
            "ssl",
            "smtplib",
            "subprocess",
            "asyncio",
            "threading",
            "multiprocessing",
            "importlib",
            "app",
            "b2_storage",
        }
        for path in (PREFLIGHT, SMOKE):
            with self.subTest(path=path.name):
                self.assertTrue(imported_roots(path).isdisjoint(forbidden))

    def test_preflight_never_extracts_or_writes_archive_members(self):
        tree = ast.parse(PREFLIGHT.read_text(encoding="utf-8"), filename=str(PREFLIGHT))
        attributes = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertTrue(attributes.isdisjoint({"extract", "extractall", "write", "writestr"}))
        open_modes = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "open" or not node.args:
                continue
            if isinstance(node.args[0], ast.Constant):
                open_modes.append(node.args[0].value)
        self.assertEqual(open_modes, ["rb"])
        source = PREFLIGHT.read_text(encoding="utf-8")
        for required in (
            "archive.testzip()",
            "stat.S_IFMT",
            "casefold()",
            "PurePosixPath",
            "EXPECTED_ZIP_ENTRIES = 524",
            "EXPECTED_ZIP_FILES = 505",
            "EXPECTED_ZIP_UNCOMPRESSED_BYTES = 6_293_321",
            "with path.open(\"rb\") as handle",
            "zipfile.ZipFile(handle, \"r\")",
            "orig_filename",
        ):
            self.assertIn(required, source)

    def test_member_safety_rejects_traversal_backslash_absolute_and_symlink(self):
        safe = zipfile.ZipInfo("docs/rtm_connect/file.md")
        self.assertTrue(preflight._safe_member(safe))
        for name in (
            "../escape", "/absolute", "C:/drive", "a/../../escape", "a\\b",
            "CON.txt", "a/NUL", "name:stream", "trailing. ",
        ):
            with self.subTest(name=name):
                self.assertFalse(preflight._safe_member(zipfile.ZipInfo(name)))
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (0o120777 << 16)
        self.assertFalse(preflight._safe_member(link))

        windows_normalized = zipfile.ZipInfo("a\\b")
        windows_normalized.filename = "a/b"
        self.assertEqual(windows_normalized.orig_filename, "a\\b")
        self.assertFalse(preflight._safe_member(windows_normalized))

    def test_wrong_archive_fails_closed_without_changing_inert_report(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "wrong.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("readme.txt", "wrong")
            for entrypoint in (preflight.main, smoke.main):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = entrypoint(["--archive", str(path), "--compact"])
                report = json.loads(output.getvalue())
                self.assertEqual(code, 2)
                self.assertFalse(report["ok"])
                self.assertFalse(report["safe"])
                self.assertFalse(report["gate_cleared"])
                self.assertEqual(report["live_verdict"], "no_go")
                self.assertFalse(report["production_authorized"])
                self.assertFalse(report["network_used"])
                self.assertFalse(report["external_effects_executed"])
                self.assertTrue(report["blockers"])

    def test_preflight_freezes_critical_snapshot_and_legacy_effect_inventory(self):
        self.assertEqual(len(preflight.CRITICAL_C8_TEXT_SHA256), 19)
        self.assertEqual(len(preflight.LEGACY_EFFECT_TEXT_SHA256), 16)
        self.assertEqual(preflight._snapshot_sha256(preflight.CRITICAL_C8_TEXT_SHA256),
                         "cc819ed72839500946910b643b30a181018a9665bc1fb3c37b67228697a116a5")
        for required in (
            "app.py",
            "rtm_connect/production_policy.py",
            "ops_automation.py",
            "dgt_client.py",
            "submitter_dgt.py",
            "submitters/registro.py",
            "rtm_core/runtime_capabilities.py",
            "cron_tick.sh",
            ".github/workflows/rtm-staging-synthetic-live.yml",
            "cases.py",
            "vehicle_removal_router.py",
            "ops_operator_submit_router.py",
            "dgt_test.py",
            "README.md",
        ):
            self.assertIn(
                required,
                preflight.CRITICAL_C8_TEXT_SHA256 | preflight.LEGACY_EFFECT_TEXT_SHA256,
            )
        self.assertEqual(
            preflight.BINARY_SHA256,
            {"templates/firma.png": "87bbe5a651ebbf708ebaf16813f840bd6a7227e0c1926b56f019d5a0b0aef37d"},
        )

    def test_smoke_exercises_identity_mutation_all_effect_flags_and_guard(self):
        source = SMOKE.read_text(encoding="utf-8")
        for required in (
            '"source_commit_sha40": "0" * 40',
            '"base_archive_sha256": "0" * 64',
            '"baseline_snapshot_sha256": "0" * 64',
            '"production_authorized": True',
            '"authorization_created": True',
            '"routes_allowed": True',
            '"workers_allowed": True',
            '"provider_contact_allowed": True',
            '"network_allowed": True',
            '"secret_access_allowed": True',
            '"database_access_allowed": True',
            '"database_ddl_allowed": True',
            '"database_dml_allowed": True',
            '"real_data_allowed": True',
            '"external_effects_allowed": True',
            '"live_activation_allowed": True',
            '"production_effects_available": True',
            '"production_safe": True',
            '"approval_matrix_satisfied": True',
            '"authority_chain_satisfied": True',
            '"evidence_freshness_satisfied": True',
            '"revocation_status_verified": True',
            '"live_canary_percent": 1',
            '"c8_dry_run_is_authentic_e4": True',
            '"live_verdict": "go"',
            "assert_g0_live_activation_unavailable",
            "except PostC8LiveActivationUnavailable",
        ):
            self.assertIn(required, source)

    def test_valid_archive_path_uses_one_descriptor_and_verifies_content(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "valid.zip"
            raw = b"known\n"
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.comment = preflight.POST_C8_GATE_BASE_COMMIT_SHA40.encode("ascii")
                archive.writestr("a.txt", raw)
            info = zipfile.ZipFile(path).getinfo("a.txt")
            text_hash = hashlib.sha256(raw).hexdigest()
            mapping = {"a.txt": text_hash}
            snapshot = preflight._snapshot_sha256(mapping)
            with (
                mock.patch.object(preflight, "POST_C8_GATE_BASE_ARCHIVE_SHA256", hashlib.sha256(path.read_bytes()).hexdigest()),
                mock.patch.object(preflight, "POST_C8_GATE_BASELINE_SNAPSHOT_SHA256", snapshot),
                mock.patch.object(preflight, "EXPECTED_ZIP_ENTRIES", 1),
                mock.patch.object(preflight, "EXPECTED_ZIP_FILES", 1),
                mock.patch.object(preflight, "EXPECTED_ZIP_UNCOMPRESSED_BYTES", info.file_size),
                mock.patch.object(preflight, "EXPECTED_ZIP_COMPRESSED_BYTES", info.compress_size),
                mock.patch.object(preflight, "EXPECTED_ZIP_MAX_FILE_BYTES", info.file_size),
                mock.patch.object(preflight, "CRITICAL_C8_TEXT_SHA256", mapping),
                mock.patch.object(preflight, "LEGACY_EFFECT_TEXT_SHA256", {}),
                mock.patch.object(preflight, "BINARY_SHA256", {}),
                mock.patch.object(preflight, "G0_OVERLAY_PATHS", ("g0.py",)),
            ):
                report = preflight.audit_archive(path)
            self.assertTrue(report["crc_ok"])
            self.assertTrue(report["g0_overlay_absent_from_base_archive"])
            self.assertEqual(report["critical_files_verified"], 1)

    def test_successful_review_is_machine_readable_no_go_not_success(self):
        archive_report = {
            "crc_ok": True,
            "safe_members": True,
            "g0_overlay_absent_from_base_archive": True,
            "_base_file_canonical_sha256": {},
        }
        local_report = {"frozen_base_tree_exact": True, "evidence_manifest_exact": True}
        for module in (preflight, smoke):
            output = io.StringIO()
            with (
                mock.patch.object(module, "audit_archive", return_value=archive_report),
                mock.patch.object(module, "audit_local_gate", return_value=local_report),
                mock.patch.object(module, "_interpreter_isolated", return_value=True),
                contextlib.redirect_stdout(output),
            ):
                code = module.main(["--archive", "ignored.zip", "--compact"])
            report = json.loads(output.getvalue())
            self.assertEqual(code, 2)
            self.assertFalse(report["ok"])
            self.assertTrue(report["audit_ok"])
            self.assertTrue(report["offline_review_reproduced"])
            self.assertFalse(report["safe"])
            self.assertFalse(report["production_safe"])
            self.assertFalse(report["gate_cleared"])
            self.assertEqual(report["live_verdict"], "no_go")
            self.assertTrue(report["blockers"])

    def test_unrelated_runtime_error_does_not_count_as_live_guard(self):
        archive_report = {
            "crc_ok": True,
            "safe_members": True,
            "g0_overlay_absent_from_base_archive": True,
            "_base_file_canonical_sha256": {},
        }
        local_report = {"frozen_base_tree_exact": True, "evidence_manifest_exact": True}
        for module in (preflight, smoke):
            output = io.StringIO()
            with (
                mock.patch.object(module, "audit_archive", return_value=archive_report),
                mock.patch.object(module, "audit_local_gate", return_value=local_report),
                mock.patch.object(module, "_interpreter_isolated", return_value=True),
                mock.patch.object(module, "assert_g0_live_activation_unavailable", side_effect=RuntimeError("unrelated")),
                contextlib.redirect_stdout(output),
            ):
                code = module.main(["--archive", "ignored.zip", "--compact"])
            report = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertFalse(report["audit_ok"])
            self.assertIn("unexpected:RuntimeError:unrelated", report["blockers"])

    def test_g0_is_not_wired_and_does_not_modify_runtime_package_exports(self):
        for path in (APP, PACKAGE_INIT):
            self.assertNotIn("rtm_connect_post_c8_g0", path.read_text(encoding="utf-8").lower())
        self.assertNotIn("rtm_connect_post_c8_g0", APP.read_text(encoding="utf-8").lower())
        self.assertTrue(imported_roots(MODULE).isdisjoint({"fastapi", "sqlalchemy", "database"}))

    def test_c8_control_surface_exports_are_exactly_frozen(self):
        self.assertEqual(
            literal_all(CONTROL),
            [
                "RTM_CONNECT_C8_PRODUCTION_CONTROL_VERSION",
                "C8_HUMAN_GATE_PHRASE",
                "ProductionControlError",
                "ProductionReleaseConflict",
                "ProductionReleaseStateError",
                "ProductionDispatchReplayConflict",
                "ProductionDispatchStateError",
                "ProductionOptimisticLockError",
                "ProductionClaimFenceError",
                "release_snapshot",
                "propose_production_release",
                "approve_production_release",
                "mark_production_release_ready",
                "simulate_production_release_activation",
                "emergency_halt_production_release",
                "dispatch_snapshot",
                "prepare_dispatch_dry_run",
                "claim_dispatch_dry_run",
                "confirm_dispatch_dry_run",
                "mark_dispatch_unknown",
                "move_dispatch_manual_review",
            ],
        )


if __name__ == "__main__":
    unittest.main()
