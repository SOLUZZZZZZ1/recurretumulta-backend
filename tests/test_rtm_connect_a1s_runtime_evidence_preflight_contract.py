from __future__ import annotations

import ast
import importlib.util
import io
import json
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT / "scripts" / "rtm_connect_a1s_runtime_evidence_preflight.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "rtm_connect_a1s_runtime_evidence_preflight_contract_subject",
        SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("No se pudo cargar el preflight de evidencia")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConnectA1SRuntimeEvidencePreflightContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SCRIPT.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(SCRIPT))
        cls.module = _load_module()

    def test_script_exists_and_exports_entry_points(self):
        self.assertTrue(SCRIPT.is_file())
        for name in (
            "audit_archive",
            "audit_local_tree",
            "audit_evidence",
            "audit_documents",
            "audit_closure_python",
            "build_report",
            "main",
        ):
            self.assertTrue(callable(getattr(self.module, name)), name)

    def test_final_subject_identity_is_exact(self):
        self.assertEqual(
            self.module.FINAL_BASE_COMMIT_SHA40,
            "9e0a26777f19efeb2c54b093e771570493a3de0e",
        )
        self.assertEqual(
            self.module.FINAL_BASE_ARCHIVE_SHA256,
            "038e28a14262d8029d95a86d71f06780f239d0aa144fb25207d9a4afc534684e",
        )
        self.assertEqual(self.module.EXPECTED_ZIP_ENTRIES, 571)
        self.assertEqual(self.module.EXPECTED_ZIP_FILES, 552)
        self.assertEqual(
            self.module.EXPECTED_ZIP_UNCOMPRESSED_BYTES, 7_189_195
        )
        self.assertEqual(
            self.module.EXPECTED_ZIP_COMPRESSED_BYTES, 1_789_936
        )
        self.assertEqual(self.module.EXPECTED_ZIP_MAX_FILE_BYTES, 174_062)

    def test_original_runtime_base_is_preserved_as_history(self):
        self.assertEqual(
            self.module.ORIGINAL_RUNTIME_BASE_COMMIT_SHA40,
            "a94dcd314c67880e40aa333dc679ef98b80a1956",
        )
        self.assertEqual(
            self.module.ORIGINAL_RUNTIME_BASE_ARCHIVE_SHA256,
            "4bed25e3fd30989a617ad3640a63e4a20d98f5347167cf097f91e9d183220c21",
        )

    def test_closure_allowlist_is_exactly_six_paths(self):
        expected_modified = {
            "docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME.md",
            "docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME_EVIDENCE.json",
            "docs/rtm_connect/adrs/0019-a1s-runtime-validation.md",
            "tests/test_rtm_connect_a1s_runtime_docs_contract.py",
        }
        expected_new = {
            "scripts/rtm_connect_a1s_runtime_evidence_preflight.py",
            "tests/test_rtm_connect_a1s_runtime_evidence_preflight_contract.py",
        }
        self.assertEqual(
            set(self.module.CLOSURE_MODIFIED_PATHS), expected_modified
        )
        self.assertEqual(set(self.module.CLOSURE_NEW_PATHS), expected_new)
        self.assertEqual(
            set(self.module.CLOSURE_PATHS), expected_modified | expected_new
        )

    def test_script_imports_only_standard_library(self):
        forbidden_roots = {
            "aiohttp",
            "app",
            "boto3",
            "database",
            "httpx",
            "psycopg",
            "psycopg2",
            "requests",
            "rtm_connect",
            "sqlalchemy",
            "subprocess",
        }
        imports: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        self.assertFalse(
            {name.split(".", 1)[0] for name in imports} & forbidden_roots
        )

    def test_archive_is_audited_without_extraction_or_shell(self):
        for forbidden in (
            "extractall(",
            "extract(",
            "os.system",
            "shell=True",
        ):
            self.assertNotIn(forbidden, self.source)
        for required in (
            "archive.testzip()",
            "archive.comment.decode",
            "_safe_member(info)",
            "archive.read(info.filename)",
        ):
            self.assertIn(required, self.source)

    def test_final_three_hotfix_signatures_are_mandatory(self):
        for required in (
            "final_jsonb_hotfix_signature_missing",
            "@> CAST(:test_mode_metadata AS JSONB)",
            "@> CAST(:synthetic_metadata AS JSONB)",
            "SELECT transaction_timestamp()",
            "transaction_clock_coherent",
            "final_release_event_hotfix_signature_missing",
            "test_release_event_ids_are_canonical_uuid_text",
        ):
            self.assertIn(required, self.source)

    def test_manifest_contract_separates_three_decisions(self):
        for required in (
            'evidence.get("status") == "completed_synthetic_staging"',
            'evidence.get("execution_status") == "completed_synthetic_staging"',
            'evidence.get("gate_status") == "passed_synthetic_staging"',
            'evidence.get("production_gate_status") == "blocked"',
            'evidence.get("live_verdict") == "no_go"',
        ):
            self.assertIn(required, self.source)

    def test_manifest_contract_forbids_overclaiming(self):
        for required in (
            "evidence_must_not_claim_git_signature",
            "evidence_must_not_claim_provenance",
            "evidence_must_not_claim_smoke_report_hash",
            "evidence_must_not_claim_smoke_signature",
            "evidence_must_not_claim_content_level_zero_delta",
            "evidence_must_not_claim_unknown_zero_baseline",
        ):
            self.assertIn(required, self.source)

    def test_local_tree_comparison_is_portable_and_fail_closed(self):
        self.assertEqual(
            self.module._canonical_member_sha256(b"a\r\nb\n", "x.txt"),
            self.module._canonical_member_sha256(b"a\nb\n", "x.txt"),
        )
        self.assertNotEqual(
            self.module._canonical_member_sha256(b"a\rb\n", "x.txt"),
            self.module._canonical_member_sha256(b"a\nb\n", "x.txt"),
        )
        for required in (
            "local_final_base_content_mismatch",
            "closure_modified_path_unchanged",
            "local_tree_allowlist_mismatch",
            "strict_utf8_crlf_to_lf_or_binary_raw_v1",
        ):
            self.assertIn(required, self.source)

    def test_manifest_and_documents_are_executed_against_current_tree(self):
        evidence = self.module.audit_evidence(ROOT)
        self.assertEqual(evidence["gate_status"], "passed_synthetic_staging")
        self.assertEqual(evidence["production_gate_status"], "blocked")
        documents = self.module.audit_documents(ROOT)
        self.assertTrue(
            documents["synthetic_runtime_and_production_decisions_separated"]
        )
        closure_python = self.module.audit_closure_python(ROOT)
        self.assertTrue(closure_python["network_or_process_surface_absent"])

    def test_manifest_audit_fails_closed_on_gate_mutation(self):
        evidence = json.loads(
            (ROOT / self.module.EVIDENCE_PATH).read_text(encoding="utf-8")
        )
        evidence["gate_status"] = "blocked"
        with mock.patch.object(
            self.module.Path,
            "read_text",
            return_value=json.dumps(evidence),
        ):
            with self.assertRaisesRegex(
                self.module.A1SRuntimeEvidencePreflightError,
                "evidence_gate_status_mismatch",
            ):
                self.module.audit_evidence(ROOT)

    def test_zip_member_safety_rejects_escape_and_windows_names(self):
        for name in (
            "../escape.txt",
            "/absolute.txt",
            "C:/drive.txt",
            "safe/CON.txt",
            "safe/trailing. ",
            "safe\\backslash.txt",
        ):
            with self.subTest(name=name):
                self.assertFalse(self.module._safe_member(zipfile.ZipInfo(name)))
        self.assertTrue(
            self.module._safe_member(zipfile.ZipInfo("safe/member.txt"))
        )

    def test_report_is_offline_read_only_and_production_no_go(self):
        source = self.source
        for required in (
            '"read_only": True',
            '"offline_only": True',
            '"synthetic_only": True',
            '"archive_extracted": False',
            '"network_used": False',
            '"database_touched": False',
            '"external_effects_executed": False',
            '"production_gate_status": "blocked"',
            '"live_verdict": "no_go"',
            '"production_authorized": False',
            '"production_safe": False',
        ):
            self.assertIn(required, source)

    def test_main_uses_exit_zero_only_for_ok_report(self):
        output = io.StringIO()
        with mock.patch.object(
            self.module,
            "build_report",
            return_value={"ok": True},
        ), redirect_stdout(output):
            self.assertEqual(
                self.module.main(["--archive", "unused", "--compact"]), 0
            )
        self.assertEqual(json.loads(output.getvalue()), {"ok": True})

        output = io.StringIO()
        with mock.patch.object(
            self.module,
            "build_report",
            return_value={"ok": False},
        ), redirect_stdout(output):
            self.assertEqual(
                self.module.main(["--archive", "unused", "--compact"]), 2
            )
        self.assertEqual(json.loads(output.getvalue()), {"ok": False})


if __name__ == "__main__":
    unittest.main()
