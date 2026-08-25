from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path

from scripts import rtm_connect_a1s_preflight as preflight
from scripts import rtm_connect_a1s_smoke as smoke
from scripts import rtm_staging_connect_a1s_schema as schema_script


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "scripts" / "rtm_staging_connect_a1s_schema.py"
PREFLIGHT = ROOT / "scripts" / "rtm_connect_a1s_preflight.py"
SMOKE = ROOT / "scripts" / "rtm_connect_a1s_smoke.py"


class ConnectA1SScriptsContractTest(unittest.TestCase):
    def test_scripts_exist_compile_and_freeze_exact_base(self):
        for path in (SCHEMA, PREFLIGHT, SMOKE):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        self.assertEqual(
            preflight.A1S_BASE_COMMIT_SHA40,
            "b0bc7ddfad9278e601dce8dd69083472662874b5",
        )
        self.assertEqual(
            preflight.A1S_BASE_ARCHIVE_SHA256,
            "4b32167288e41be2c8b556bde49149390181f8f918c3a4a864020b269493825e",
        )
        self.assertEqual(preflight.EXPECTED_ZIP_ENTRIES, 542)
        self.assertEqual(preflight.EXPECTED_ZIP_FILES, 523)
        for path in (
            "tests/test_rtm_connect_c7_scripts_contract.py",
            "tests/test_rtm_connect_c8_scripts_contract.py",
            "tests/test_rtm_connect_post_c8_g1_docs_contract.py",
        ):
            self.assertIn(path, preflight.A1S_TEST_PATHS)
            self.assertIn(path, preflight.A1S_OVERLAY_PATHS)

    def test_preflight_rejects_any_other_archive_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrong.zip"
            path.write_bytes(b"not-the-frozen-archive")
            with self.assertRaisesRegex(preflight.A1SPreflightError, "sha256_mismatch"):
                preflight.audit_archive(path)

    def test_offline_reports_declare_all_prohibited_effects_false(self):
        for path in (PREFLIGHT, SMOKE):
            source = path.read_text(encoding="utf-8")
            for required in (
                '"network_used": False',
                '"provider_contacted": False',
                '"administration_contacted": False',
                '"b2_used": False',
                '"b2b_enabled": False',
                '"real_data_used": False',
                '"external_effects_executed": False',
            ):
                self.assertIn(required, source, f"{path.name}: {required}")

    def test_schema_report_distinguishes_database_from_external_network(self):
        source = SCHEMA.read_text(encoding="utf-8")
        for required in (
            '"provider_network_used": False',
            '"administration_network_used": False',
            '"database_connection_used": False',
            '"database_configuration_loaded": False',
            'report["database_configuration_loaded"] = True',
            'report["database_connection_used"] = True',
            '"external_secret_resolution_performed": False',
        ):
            self.assertIn(required, source)
        self.assertNotIn('"network_used": False', source)
        self.assertNotIn('"secret_resolution_performed": False', source)

    def test_preflight_is_offline_read_only_and_no_extract(self):
        source = PREFLIGHT.read_text(encoding="utf-8")
        for required in (
            '"read_only": True',
            '"offline_only": True',
            '"synthetic_only": True',
            '"live_verdict": "no_go"',
            '"production_authorized": False',
            '"schema_changes_required": True',
            "archive.testzip()",
            "_safe_member",
            "a1s_overlay_absent_from_base_archive",
            "successor_assertions_absent_from_base_archive",
            "app_runtime_wiring_present",
            "missing_contract_checks",
            '"contract_checks": contract_checks',
        ):
            self.assertIn(required, source)
        self.assertNotIn("extractall", source)
        self.assertNotIn("extract(", source)

    def test_schema_apply_requires_exact_confirmation_and_is_non_destructive(self):
        wrong = schema_script._parser().parse_args([
            "--apply", "--confirmation", "WRONG",
        ])
        blockers = schema_script.safety_blockers(wrong, values={})
        self.assertIn("invalid_apply_confirmation", blockers)
        source = SCHEMA.read_text(encoding="utf-8")
        self.assertIn("STAGING_CONNECT_A1S_SCHEMA_ONLY", source)
        self.assertIn("ON CONFLICT (name) DO NOTHING", source)
        upper = source.upper()
        for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM"):
            self.assertNotIn(forbidden, upper)

    def test_smoke_uses_preflight_and_never_enables_live(self):
        source = SMOKE.read_text(encoding="utf-8")
        self.assertIn("audit_archive", source)
        self.assertIn("audit_local_overlay", source)
        self.assertIn("ast.parse", source)
        self.assertNotIn("importlib", source)
        self.assertIn('"static_contracts_ok": static_contracts_ok', source)
        self.assertIn('"tests_executed": False', source)
        self.assertIn('"database_constraints_executed": False', source)
        self.assertIn('"workflow_scenario_executed": False', source)
        self.assertIn('"live_verdict": "no_go"', source)
        self.assertIn('"production_authorized": False', source)
        self.assertIn(
            "smoke_does_not_execute_postgresql_constraints_or_workflow",
            source,
        )
        self.assertIn(
            "fastapi_pre_context_errors_use_detail_envelope",
            source,
        )

    def test_schema_audits_every_fk_and_fixture_dependency(self):
        source = SCHEMA.read_text(encoding="utf-8")
        for required in (
            '"id", "status", "display_name", "primary_role_id"',
            '"cases": {"id", "test_mode"}',
            '"documents": {',
            '"b2_bucket", "b2_key"',
            '"rtm_connect_connectors": {',
            '"rtm_connect_actions": {',
            '"rtm_connect_authorizations": {',
            '"rtm_connect_attempts": {',
            '"rtm_connect_evidence": {',
            '"receipt_storage_ref"',
        ):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main()
