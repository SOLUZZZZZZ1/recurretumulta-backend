from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "docs" / "rtm_connect" / "RTM_CONNECT_A1S_RUNTIME.md"
EVIDENCE = (
    ROOT / "docs" / "rtm_connect" / "RTM_CONNECT_A1S_RUNTIME_EVIDENCE.json"
)
ADR = (
    ROOT / "docs" / "rtm_connect" / "adrs"
    / "0019-a1s-runtime-validation.md"
)

FINAL_COMMIT = "9e0a26777f19efeb2c54b093e771570493a3de0e"
FINAL_ARCHIVE_SHA256 = (
    "038e28a14262d8029d95a86d71f06780f239d0aa144fb25207d9a4afc534684e"
)


class ConnectA1SRuntimeDocsContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = GATE.read_text(encoding="utf-8")
        self.adr = ADR.read_text(encoding="utf-8")
        self.combined = self.gate + "\n" + self.adr
        self.evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def test_documents_exist_and_freeze_design_and_final_subjects(self):
        for path in (GATE, EVIDENCE, ADR):
            self.assertTrue(path.is_file(), path)
        source = self.evidence["source"]
        self.assertEqual(
            source["base_commit_sha40"],
            "a94dcd314c67880e40aa333dc679ef98b80a1956",
        )
        self.assertEqual(
            source["base_archive_sha256"],
            "4bed25e3fd30989a617ad3640a63e4a20d98f5347167cf097f91e9d183220c21",
        )
        self.assertEqual(source["final_commit_sha40"], FINAL_COMMIT)
        self.assertEqual(
            source["final_base_archive_name"],
            "RTM_CONNECT_A1S_RUNTIME_EVIDENCE_BASE_9e0a267.zip",
        )
        self.assertEqual(
            source["final_base_archive_sha256"], FINAL_ARCHIVE_SHA256
        )
        self.assertEqual(
            source["final_base_archive_comment_sha40"], FINAL_COMMIT
        )
        self.assertIn(FINAL_COMMIT, self.combined)
        self.assertIn(FINAL_ARCHIVE_SHA256, self.combined)

    def test_runtime_and_production_decisions_are_separate(self):
        self.assertEqual(
            self.evidence["status"], "completed_synthetic_staging"
        )
        self.assertEqual(
            self.evidence["execution_status"],
            "completed_synthetic_staging",
        )
        self.assertEqual(
            self.evidence["gate_status"], "passed_synthetic_staging"
        )
        self.assertEqual(
            self.evidence["production_gate_status"], "blocked"
        )
        self.assertEqual(self.evidence["live_verdict"], "no_go")
        for required in (
            "completed_synthetic_staging",
            "passed_synthetic_staging",
            "production_gate_status=blocked",
            "live_verdict=no_go",
        ):
            self.assertIn(required, self.gate)
            self.assertIn(required, self.adr)

    def test_commit_is_conditioned_on_exact_preflight_success(self):
        self.assertEqual(self.evidence["closure_blockers"], [])
        self.assertTrue(
            self.evidence["next_step"].startswith(
                "commit_only_after_exact_final_delivery_preflight_passes"
            )
        )
        for required in (
            "scripts/rtm_connect_a1s_runtime_evidence_preflight.py",
            "RTM_CONNECT_A1S_RUNTIME_EVIDENCE_BASE_9e0a267.zip",
            "solo puede entregarse o commitirse después",
        ):
            self.assertIn(required, self.combined)

    def test_original_and_closure_path_allowlists_are_exact(self):
        planned = {
            "rtm_connect/human_filing_runtime.py",
            "scripts/rtm_connect_a1s_runtime_preflight.py",
            "scripts/rtm_staging_connect_a1s_runtime_fixture.py",
            "scripts/rtm_connect_a1s_runtime_smoke.py",
            "docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME.md",
            "docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME_EVIDENCE.json",
            "docs/rtm_connect/adrs/0019-a1s-runtime-validation.md",
            "tests/test_rtm_connect_a1s_runtime_contract.py",
            "tests/test_rtm_connect_a1s_runtime_fixture_script_contract.py",
            "tests/test_rtm_connect_a1s_runtime_preflight_contract.py",
            "tests/test_rtm_connect_a1s_runtime_smoke_contract.py",
            "tests/test_rtm_connect_a1s_runtime_docs_contract.py",
        }
        closure = {
            "docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME.md",
            "docs/rtm_connect/RTM_CONNECT_A1S_RUNTIME_EVIDENCE.json",
            "docs/rtm_connect/adrs/0019-a1s-runtime-validation.md",
            "tests/test_rtm_connect_a1s_runtime_docs_contract.py",
            "scripts/rtm_connect_a1s_runtime_evidence_preflight.py",
            "tests/test_rtm_connect_a1s_runtime_evidence_preflight_contract.py",
        }
        self.assertEqual(set(self.evidence["planned_paths"].values()), planned)
        self.assertEqual(set(self.evidence["closure_paths"].values()), closure)
        for path in planned | closure:
            self.assertIn(path, self.combined)

    def test_commit_and_delivery_chain_is_complete(self):
        expected_commits = [
            "b0bc7ddfad9278e601dce8dd69083472662874b5",
            "37a4479022519d34d1a220cb1ac6380ea7b9f238",
            "a94dcd314c67880e40aa333dc679ef98b80a1956",
            "aaf040b1c8d35ee61aa3720a4dc4b8cf1822b827",
            "d546b1368eeacf34bb50dea5820ab1ed27f93053",
            "407ced9acbeffdd6c727264e8c9ac26e3cd110fa",
            FINAL_COMMIT,
        ]
        self.assertEqual(
            [item["commit_sha40"] for item in self.evidence["commit_chain"]],
            expected_commits,
        )
        delivery = {
            item["archive_name"]: item["sha256"]
            for item in self.evidence["delivery_chain"]
        }
        self.assertEqual(
            delivery["RTM_CONNECT_A1S_RUNTIME_OVERLAY_a94dcd3.zip"],
            "0f44d10543c777fd1ef36b20357934cafd4605c8a296d469af3b1af6b56c0e24",
        )
        self.assertEqual(
            delivery["RTM_CONNECT_A1S_RUNTIME_SQL_HOTFIX_aaf040b.zip"],
            "e1c497a3e65aa8462f8f50050f11275ab30337b6d7203d6d76c16c4c3cbd3ebb",
        )
        self.assertEqual(
            delivery["RTM_CONNECT_A1S_RUNTIME_CLOCK_HOTFIX_d546b13.zip"],
            "4756378882a385e409ec3b8c8617c252039e2131e10ca7afdda97d535afb13fb",
        )
        self.assertEqual(
            delivery["RTM_CONNECT_A1S_RUNTIME_EVENT_HOTFIX_407ced9.zip"],
            "6bad4f4e5f7fcd5a39b30c14e934aa67a001c97a2d43fe68a6a969020170ddea",
        )
        self.assertEqual(
            delivery["RTM_CONNECT_A1S_RUNTIME_EVIDENCE_BASE_9e0a267.zip"],
            FINAL_ARCHIVE_SHA256,
        )

    def test_persistent_fixture_and_three_operators_are_frozen(self):
        fixture = self.evidence["runtime_fixture"]
        self.assertEqual(fixture["fixture_key"], "runtime-a94dcd3-v1")
        for key in (
            "persistent",
            "synthetic_only",
            "creation_only",
            "a1s_rows_insert_only",
            "new_core_action_transitions_to_authorized",
            "three_existing_synthetic_operators_required",
            "smoke_derives_operator_ids_from_exact_memberships",
        ):
            self.assertIs(fixture[key], True, key)
        self.assertIs(fixture["preexisting_rows_mutated"], False)
        self.assertIs(fixture["smoke_accepts_operator_ids_on_cli"], False)
        self.assertEqual(
            fixture["requester_operator_id"],
            "0a558d35-01b0-4c74-8640-c690ec21d52c",
        )
        self.assertEqual(
            fixture["releaser_operator_id"],
            "cd2d8df3-9e67-4c86-8f4e-1df55fb67b44",
        )
        self.assertEqual(
            fixture["verifier_operator_id"],
            "9cae8979-f25f-4350-b3e7-1c8c7bd9c62b",
        )

    def test_final_smoke_inventory_is_exact_and_all_true(self):
        smoke = self.evidence["final_smoke"]
        self.assertEqual(smoke["subject_commit_sha40"], FINAL_COMMIT)
        self.assertIs(smoke["ok"], True)
        self.assertIs(smoke["safe"], True)
        self.assertEqual(smoke["blockers"], [])
        self.assertEqual(len(smoke["checks"]), 28)
        self.assertTrue(all(smoke["checks"].values()))
        self.assertEqual(
            smoke["cleanup"],
            {
                "database_rolled_back": True,
                "ephemeral_sessions_remaining": 0,
                "fixture_snapshots_equal_to_baselines": True,
            },
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
            self.assertIs(smoke[key], False, key)

    def test_execution_records_success_without_overclaiming_rollback(self):
        execution = self.evidence["execution"]
        for key in (
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
        ):
            self.assertIs(execution[key], True, key)
        self.assertEqual(execution["network_attempts"], 0)
        self.assertEqual(
            execution["render_deployment_id"], "dep-da6qp1p5efls73d4q0kg"
        )
        self.assertEqual(execution["health_response"], {"ok": True})
        self.assertIs(execution["content_level_zero_delta_verified"], False)
        self.assertIs(
            execution["unknown_fixture_baseline_was_zero_verified"], False
        )
        self.assertIs(execution["final_smoke"]["raw_report_sha256"], None)
        self.assertIs(execution["final_smoke"]["signature_verified"], False)

    def test_tests_and_deploy_identity_are_exact(self):
        tests = self.evidence["tests"]
        self.assertEqual(tests["subject_commit_sha40"], FINAL_COMMIT)
        self.assertIs(tests["executed"], True)
        self.assertIs(tests["ok"], True)
        self.assertEqual(
            tests["suites"],
            [
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
        )
        deployments = {
            item["commit_sha40"]: item for item in self.evidence["deployments"]
        }
        self.assertEqual(
            deployments[FINAL_COMMIT]["deployment_id"],
            "dep-da6qp1p5efls73d4q0kg",
        )
        self.assertIs(deployments[FINAL_COMMIT]["live_observed"], True)
        self.assertIs(deployments[FINAL_COMMIT]["health_ok_observed"], True)

    def test_legacy_preflight_is_not_reused_for_final_subject(self):
        legacy = self.evidence["execution"]["legacy_preflight"]
        self.assertEqual(
            legacy["subject_commit_sha40"],
            "a94dcd314c67880e40aa333dc679ef98b80a1956",
        )
        self.assertIs(legacy["ok"], True)
        self.assertIs(legacy["applies_to_final_subject"], False)
        self.assertIn("no aplica al sujeto final", self.gate)

    def test_scope_remains_synthetic_staging_and_no_go(self):
        scope = self.evidence["scope"]
        self.assertIs(scope["staging_only"], True)
        self.assertIs(scope["synthetic_only"], True)
        self.assertIs(scope["read_only_evidence"], True)
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
            self.assertIs(scope[key], False, key)

    def test_limitations_and_claims_not_made_are_explicit(self):
        limitations = set(self.evidence["limitations"])
        self.assertTrue(
            {
                "operator_console_reports_are_unattested_and_not_hash_frozen",
                "content_level_zero_delta_was_not_verified",
                "unknown_fixture_zero_baseline_was_not_verified",
                "git_archive_is_not_a_supply_chain_signature",
                "http_smoke_used_in_process_asgi",
            } <= limitations
        )
        claims = set(self.evidence["claims_not_made"])
        self.assertTrue(
            {
                "frontend_ready",
                "real_filing_available",
                "authentic_provider_e4_available",
                "production_safe",
                "production_authorized",
            } <= claims
        )
        for required in (
            "operator_console_observed_unattested",
            "content-level zero delta",
            "UNKNOWN",
            "frontend",
            "E4 autentica",
        ):
            self.assertIn(required, self.gate)
            self.assertIn(required, self.adr)

    def test_production_blockers_remain_and_runtime_blockers_are_empty(self):
        self.assertEqual(self.evidence["runtime_validation_blockers"], [])
        self.assertEqual(self.evidence["closure_blockers"], [])
        self.assertTrue(self.evidence["production_blockers"])
        combined = self.combined
        for required in (
            "synthetic_only=true",
            "staging_only=true",
            "real_data_used=false",
            "provider_network_used=false",
            "administration_network_used=false",
            "provider_contacted=false",
            "administration_contacted=false",
            "b2_used=false",
            "b2b_enabled=false",
            "external_effects_executed=false",
            "production_authorized=false",
            "live_verdict=no_go",
        ):
            self.assertIn(required, combined)


if __name__ == "__main__":
    unittest.main()
