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


class ConnectA1SRuntimeDocsContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = GATE.read_text(encoding="utf-8")
        self.adr = ADR.read_text(encoding="utf-8")
        self.evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def test_documents_exist_and_freeze_exact_base(self):
        for path in (GATE, EVIDENCE, ADR):
            self.assertTrue(path.is_file(), path)
        combined = self.gate + self.adr
        self.assertIn(
            "a94dcd314c67880e40aa333dc679ef98b80a1956",
            combined,
        )
        self.assertEqual(
            self.evidence["source"]["base_commit_sha40"],
            "a94dcd314c67880e40aa333dc679ef98b80a1956",
        )
        self.assertEqual(
            self.evidence["source"]["base_archive_sha256"],
            "4bed25e3fd30989a617ad3640a63e4a20d98f5347167cf097f91e9d183220c21",
        )
        self.assertIs(
            self.evidence["source"]["base_delivery_identity_frozen"], True
        )

    def test_planned_runtime_paths_are_literal_and_consistent(self):
        paths = {
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
        combined = self.gate + self.adr
        for path in paths:
            self.assertIn(path, combined)
        self.assertEqual(set(self.evidence["planned_paths"].values()), paths)

    def test_persistent_fixture_and_smoke_operator_source_are_frozen(self):
        fixture = self.evidence["runtime_fixture"]
        self.assertEqual(fixture["fixture_key"], "runtime-a94dcd3-v1")
        self.assertIs(fixture["persistent"], True)
        self.assertIs(fixture["creation_only"], True)
        self.assertIs(fixture["a1s_rows_insert_only"], True)
        self.assertIs(fixture["preexisting_rows_mutated"], False)
        self.assertIs(
            fixture["new_core_action_transitions_to_authorized"], True
        )
        self.assertIs(
            fixture["three_existing_synthetic_operators_required"], True
        )
        self.assertIs(
            fixture["smoke_derives_operator_ids_from_exact_memberships"],
            True,
        )
        self.assertIs(fixture["smoke_accepts_operator_ids_on_cli"], False)
        self.assertIn("runtime-a94dcd3-v1", self.gate)

    def test_evidence_is_pending_and_does_not_claim_render_or_postgresql(self):
        self.assertEqual(
            self.evidence["status"],
            "pending_external_execution",
        )
        self.assertEqual(self.evidence["gate_status"], "blocked")
        self.assertEqual(self.evidence["live_verdict"], "no_go")
        source = self.evidence["source"]
        for key in (
            "runtime_overlay_commit_sha40",
            "runtime_overlay_archive_name",
            "runtime_overlay_archive_sha256",
        ):
            self.assertIsNone(source[key], key)
        for key in (
            "runtime_delivery_identity_frozen",
            "git_commit_signature_verified",
            "supply_chain_provenance_verified",
        ):
            self.assertIs(source[key], False, key)

        execution = self.evidence["execution"]
        self.assertIsNone(execution["render_deployment_id"])
        self.assertIsNone(execution["health_response"])
        self.assertIsNone(execution["network_attempts"])
        self.assertIsNone(execution["tests_ok"])
        self.assertIsNone(execution["evaluated_at"])
        for key, value in execution.items():
            if isinstance(value, bool):
                self.assertIs(value, False, key)

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
        ):
            self.assertIs(scope[key], False, key)

    def test_gate_documents_order_confirmation_and_rollback(self):
        for required in (
            "## Secuencia de admisión",
            "Preflight offline",
            "Audit de schema y fixtures",
            "Provisioning confirmado",
            "Smoke E2E transaccional",
            "Rollback y verificación independiente",
            "STAGING_CONNECT_A1S_RUNTIME_FIXTURES_ONLY",
            "transacción se revierte siempre",
            "conexión nueva",
            "cero delta frente a los dos baselines",
            "fixture UNKNOWN",
        ):
            self.assertIn(required, self.gate)
        self.assertNotIn("login individual", self.gate)
        self.assertNotIn("cero filas de la ejecución", self.gate)

    def test_runtime_environment_and_post_audit_are_executable(self):
        required = self.evidence["required_environment"]
        for key in (
            "RTM_INSTANCE_ID",
            "RTM_DATA_NAMESPACE",
            "DATABASE_URL",
            "RTM_EXPECTED_BRANCH",
            "RENDER_GIT_BRANCH_OR_GIT_BRANCH",
            "RTM_ENABLE_CONNECT_A1S_HUMAN_FILING",
            "RTM_ENABLE_OPERATOR_AUTH_V1",
            "RTM_OPERATOR_ACCESS_HMAC_KEY",
        ):
            self.assertIn(key, required)
        post_audit = self.gate.rsplit(
            "rtm_staging_connect_a1s_runtime_fixture.py", 1
        )[1]
        for argument in (
            "--requester-operator-id",
            "--releaser-operator-id",
            "--verifier-operator-id",
        ):
            self.assertIn(argument, post_audit)

    def test_docs_forbid_external_real_b2_and_production_claims(self):
        combined = self.gate + self.adr
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

    def test_evidence_names_every_current_blocker(self):
        blockers = set(self.evidence["blockers"])
        self.assertEqual(
            blockers,
            {
                "runtime_overlay_identity_not_frozen",
                "render_deployment_not_observed",
                "postgresql_runtime_audit_not_executed",
                "runtime_fixture_provisioning_not_executed",
                "transactional_e2e_not_executed",
                "rollback_and_zero_delta_from_baseline_not_verified",
                "runtime_test_report_not_frozen",
            },
        )
        claims = set(self.evidence["claims_not_made"])
        self.assertIn("runtime_ready", claims)
        self.assertIn("frontend_ready", claims)
        self.assertIn("production_authorized", claims)


if __name__ == "__main__":
    unittest.main()
