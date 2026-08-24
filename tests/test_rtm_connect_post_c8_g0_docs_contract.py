from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from rtm_connect_post_c8_g0 import (
    POST_C8_GATE_BASE_ARCHIVE_SHA256,
    POST_C8_GATE_BASE_COMMIT_SHA40,
    POST_C8_GATE_BASELINE_SNAPSHOT_SHA256,
    POST_C8_GATE_FROZEN_EVALUATED_AT,
    assess_post_c8_gate,
    post_c8_gate_fingerprint_material,
    post_c8_gate_sha256,
)
from scripts.rtm_connect_post_c8_g0_preflight import (
    BINARY_SHA256,
    CRITICAL_C8_TEXT_SHA256,
    G0_OVERLAY_PATHS,
    LEGACY_EFFECT_TEXT_SHA256,
)


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "rtm_connect" / "RTM_CONNECT_POST_C8_GATE_G0.md"
ADR = (
    ROOT
    / "docs"
    / "rtm_connect"
    / "adrs"
    / "0016-post-c8-g0-offline-decision-gate.md"
)
EVIDENCE = (
    ROOT
    / "docs"
    / "rtm_connect"
    / "RTM_CONNECT_POST_C8_G0_EVIDENCE.json"
)
C0_MANIFEST = ROOT / "docs" / "rtm_connect" / "RTM_CONNECT_C0_MANIFEST.json"
C8_DOC = ROOT / "docs" / "rtm_connect" / "RTM_CONNECT_C8_CONTROLLED_PRODUCTION.md"
C8_ADR = (
    ROOT
    / "docs"
    / "rtm_connect"
    / "adrs"
    / "0015-c8-controlled-production-admission.md"
)


class ConnectPostC8G0DocsContractTest(unittest.TestCase):
    def test_docs_and_machine_readable_evidence_exist(self):
        for path in (DOC, ADR, EVIDENCE):
            self.assertTrue(path.is_file(), path.name)
        manifest = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        self.assertEqual(manifest["phase"], "post_c8_g0")
        self.assertEqual(manifest["not_phase"], "C9")

    def test_docs_freeze_no_go_and_distinguish_g0_from_c9(self):
        combined = DOC.read_text(encoding="utf-8") + ADR.read_text(encoding="utf-8")
        for literal in (
            "G0",
            "no es C9",
            "NO-GO",
            "gate_status = blocked",
            "live_verdict = no_go",
            "live_canary_percent = 0",
            "provider_specific_pack_and_new_adr_required",
            "No significa que la puerta se haya despejado",
        ):
            self.assertIn(literal, combined)

    def test_exact_source_identity_is_consistent_everywhere(self):
        combined = (
            DOC.read_text(encoding="utf-8")
            + ADR.read_text(encoding="utf-8")
            + EVIDENCE.read_text(encoding="utf-8")
        )
        for literal in (
            POST_C8_GATE_BASE_COMMIT_SHA40,
            POST_C8_GATE_BASE_ARCHIVE_SHA256,
            POST_C8_GATE_BASELINE_SNAPSHOT_SHA256,
        ):
            self.assertIn(literal, combined)
        manifest = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        self.assertEqual(manifest["source"]["archive_entries"], 524)
        self.assertEqual(manifest["source"]["archive_files"], 505)

    def test_six_domains_and_frozen_blockers_match_code(self):
        manifest = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        item = assess_post_c8_gate(
            source_commit_sha40=POST_C8_GATE_BASE_COMMIT_SHA40,
            base_archive_sha256=POST_C8_GATE_BASE_ARCHIVE_SHA256,
            baseline_snapshot_sha256=POST_C8_GATE_BASELINE_SNAPSHOT_SHA256,
            evaluated_at="2026-08-24T17:15:00Z",
        )
        expected = post_c8_gate_fingerprint_material(item)
        expected["assessment_sha256"] = post_c8_gate_sha256(item)
        self.assertEqual(manifest["assessment"], expected)
        self.assertEqual(
            [entry["domain"] for entry in manifest["assessment"]["findings"]],
            ["security", "operations", "privacy", "provider", "canary", "rollback"],
        )

    def test_evidence_decision_has_no_authority_runtime_or_effect(self):
        decision = json.loads(EVIDENCE.read_text(encoding="utf-8"))["assessment"]
        self.assertEqual(decision["gate_status"], "blocked")
        self.assertEqual(decision["live_verdict"], "no_go")
        self.assertEqual(decision["live_canary_percent"], 0)
        for name in (
            "production_authorized",
            "authorization_created",
            "routes_allowed",
            "workers_allowed",
            "provider_contact_allowed",
            "network_allowed",
            "secret_access_allowed",
            "database_access_allowed",
            "database_ddl_allowed",
            "database_dml_allowed",
            "real_data_allowed",
            "external_effects_allowed",
            "live_activation_allowed",
            "production_effects_available",
            "production_safe",
            "approval_matrix_satisfied",
            "authority_chain_satisfied",
            "evidence_freshness_satisfied",
            "revocation_status_verified",
            "c8_dry_run_is_authentic_e4",
        ):
            self.assertIs(decision[name], False, name)

    def test_docs_explicitly_inventory_legacy_bypasses_and_signature_asset(self):
        source = DOC.read_text(encoding="utf-8")
        for literal in (
            "/ops/automation/tick",
            "DGT_ENABLED",
            "submitter_dgt.py",
            "submitters/registro.py",
            "REG_PROVIDER_URL",
            "runtime_capabilities",
            "SMTP",
            "cron_tick.sh",
            "templates/firma.png",
            "posible activo jurídico sensible",
            "no afirma “cero red global”",
            "vehicle_removal_router.py",
            "ops_operator_submit_router.py",
            "dgt_test.py",
            "README.md",
        ):
            self.assertIn(literal, source)
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        self.assertEqual(
            evidence["embedded_asset_inventory"][0]["sha256"],
            "87bbe5a651ebbf708ebaf16813f840bd6a7227e0c1926b56f019d5a0b0aef37d",
        )
        self.assertEqual(evidence["embedded_asset_inventory"][0]["status"], "blocked")

    def test_observed_c8_closure_is_labeled_unattested_not_e4(self):
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        observed = evidence["observed_closure_evidence"]
        self.assertEqual(observed["attestation_class"], "observed_unattested")
        self.assertEqual(observed["unit_tests_claimed_total"], 1040)
        self.assertEqual(observed["unit_tests_claimed_skipped"], 8)
        self.assertEqual(observed["c8_preflight_claim"], "reported_safe_unattested")
        self.assertIn("unattested", observed["c8_smoke_claim"])
        self.assertFalse(evidence["assessment"]["c8_dry_run_is_authentic_e4"])

    def test_evidence_schema_hash_inventories_and_overlay_identity_are_exact(self):
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        self.assertEqual(
            set(evidence),
            {
                "authority", "version", "phase", "not_phase", "source", "assessment",
                "observed_closure_evidence", "legacy_effect_inventory",
                "embedded_asset_inventory", "scope_limitations", "critical_c8_text_sha256",
                "legacy_effect_text_sha256", "binary_sha256", "overlay_identity",
            },
        )
        self.assertEqual(evidence["critical_c8_text_sha256"], CRITICAL_C8_TEXT_SHA256)
        self.assertEqual(evidence["legacy_effect_text_sha256"], LEGACY_EFFECT_TEXT_SHA256)
        self.assertEqual(evidence["binary_sha256"], BINARY_SHA256)
        self.assertEqual(evidence["assessment"]["evaluated_at"], POST_C8_GATE_FROZEN_EVALUATED_AT)
        self.assertEqual(evidence["overlay_identity"]["paths"], list(G0_OVERLAY_PATHS))
        self.assertIsNone(evidence["overlay_identity"]["delivery_zip_sha256"])
        self.assertIsNone(evidence["overlay_identity"]["git_commit_sha40"])
        self.assertNotIn("go", evidence["assessment"])
        self.assertNotIn("production_approved", evidence["assessment"])

    def test_docs_require_isolated_execution_and_governance(self):
        combined = DOC.read_text(encoding="utf-8") + ADR.read_text(encoding="utf-8")
        for literal in (
            "python -I -S -B",
            "production_safe=false",
            "audit_ok=true",
            "exit code `2`",
            "legal_compliance_owner",
            "independent_release_activator",
            "expires_at",
            "revocation_status",
        ):
            self.assertIn(literal, combined)

    def test_c0_and_c8_frozen_documents_are_unchanged(self):
        def normalized_sha256(path: Path) -> str:
            source = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
            return hashlib.sha256(source.encode("utf-8")).hexdigest()

        self.assertEqual(
            normalized_sha256(C0_MANIFEST),
            "6c897149924008f277436942849139c9c0f41d1ce87474260487c7f9a64b9460",
        )
        self.assertEqual(
            normalized_sha256(C8_DOC),
            "d8babc5656518484bfbf52276e30ebfdab52acedabe57489a1126b009cf5149f",
        )
        self.assertEqual(
            normalized_sha256(C8_ADR),
            "a6e7bfa697c4f6a07b2493b8edb44684123fd3c5ca96f2e3ffeee04058fe3821",
        )
        frozen = json.loads(C0_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(frozen["first_implementation_order"][-1], "C8_controlled_production")
        self.assertNotIn("G0", frozen["first_implementation_order"])
        self.assertFalse(frozen["runtime_published"])
        self.assertFalse(frozen["external_effects_enabled"])


if __name__ == "__main__":
    unittest.main()
