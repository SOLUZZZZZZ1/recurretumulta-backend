from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from rtm_connect_post_c8_g1 import (
    POST_C8_G1_BASE_ARCHIVE_SHA256,
    POST_C8_G1_BASE_COMMIT_SHA40,
    POST_C8_G1_BASELINE_SNAPSHOT_SHA256,
    POST_C8_G1_FROZEN_EVALUATED_AT,
    POST_C8_G1_REQUIRED_DOSSIER_SECTIONS,
    assess_provider_admission,
    provider_admission_fingerprint_material,
    provider_admission_sha256,
)
from scripts.rtm_connect_post_c8_g1_preflight import (
    BASE_CRITICAL_TEXT_SHA256,
    G0_EVIDENCE_MANIFEST_SHA256,
    G1_EVIDENCE_MANIFEST_SHA256,
    G1_OVERLAY_PATHS,
    SCOPE_LIMITATIONS,
)


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/rtm_connect/RTM_CONNECT_POST_C8_GATE_G1.md"
ADR = ROOT / "docs/rtm_connect/adrs/0017-post-c8-g1-provider-admission.md"
EVIDENCE = ROOT / "docs/rtm_connect/RTM_CONNECT_POST_C8_G1_EVIDENCE.json"


def canonical_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def assessment():
    return assess_provider_admission(
        source_commit_sha40=POST_C8_G1_BASE_COMMIT_SHA40,
        base_archive_sha256=POST_C8_G1_BASE_ARCHIVE_SHA256,
        baseline_snapshot_sha256=POST_C8_G1_BASELINE_SNAPSHOT_SHA256,
        evaluated_at=POST_C8_G1_FROZEN_EVALUATED_AT,
    )


class ConnectPostC8G1DocsContractTest(unittest.TestCase):
    def test_docs_and_machine_evidence_exist(self):
        for path in (DOC, ADR, EVIDENCE):
            self.assertTrue(path.is_file(), path)

    def test_docs_freeze_no_go_and_distinguish_g1_from_c9(self):
        combined = DOC.read_text(encoding="utf-8") + ADR.read_text(encoding="utf-8")
        for literal in (
            "G1 no es C9",
            "gate_status = blocked",
            "live_verdict = no_go",
            "provider_selected = false",
            "provider_pack_admissible = false",
            "live_canary_percent = 0",
            "expected_exit_code = 2",
            "No se inicia C9",
        ):
            self.assertIn(literal, combined)

    def test_docs_reject_all_three_legacy_candidates(self):
        combined = DOC.read_text(encoding="utf-8") + ADR.read_text(encoding="utf-8")
        for literal in (
            "legacy.dgt_client_placeholder",
            "legacy.dgt_dev_xml_submitter",
            "legacy.registro_general_generic",
            "status = rejected",
            "provider_specific = false",
            "production_eligible = false",
        ):
            self.assertIn(literal, combined)

    def test_docs_define_full_provider_dossier_without_selecting_provider(self):
        combined = DOC.read_text(encoding="utf-8") + ADR.read_text(encoding="utf-8")
        for literal in (
            "identidad jurídica del proveedor",
            "lookup read-only",
            "reconciliación UNKNOWN",
            "verificador E4 auténtico",
            "workload identity",
            "kill switch",
            "prohibición de autoexpansión",
            "separación de funciones",
            "SBOM",
        ):
            self.assertIn(literal, combined)

    def test_docs_require_isolated_offline_execution_and_exit_two(self):
        combined = DOC.read_text(encoding="utf-8") + ADR.read_text(encoding="utf-8")
        combined_lower = combined.lower()
        for literal in (
            "python -I -S -B",
            "--archive",
            "--compact",
            "exit `2`",
            "audit_ok=true",
            "offline_review_reproduced=true",
        ):
            self.assertIn(literal, combined)
        self.assertIn("no extraen", combined_lower)

    def test_evidence_assessment_matches_code_exactly(self):
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        expected = provider_admission_fingerprint_material(assessment())
        expected["assessment_sha256"] = provider_admission_sha256(assessment())
        self.assertEqual(evidence["assessment"], expected)
        self.assertEqual(
            evidence["legacy_candidate_inventory"],
            expected["candidates"],
        )

    def test_evidence_source_and_g0_identity_closure_are_exact(self):
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        self.assertEqual(evidence["source"]["commit_sha40"], POST_C8_G1_BASE_COMMIT_SHA40)
        self.assertEqual(evidence["source"]["archive_sha256"], POST_C8_G1_BASE_ARCHIVE_SHA256)
        self.assertEqual(evidence["source"]["critical_snapshot_sha256"], POST_C8_G1_BASELINE_SNAPSHOT_SHA256)
        closure = evidence["g0_identity_closure"]
        self.assertEqual(closure["g0_evidence_manifest_sha256"], G0_EVIDENCE_MANIFEST_SHA256)
        self.assertTrue(closure["delivery_sha256_verified"])
        self.assertTrue(closure["archive_comment_matches_claimed_commit"])
        self.assertTrue(closure["g0_decision_remains_blocked_no_go"])
        self.assertFalse(closure["git_commit_object_and_authorship_attested"])
        self.assertFalse(closure["supply_chain_signature_and_sbom_verified"])

    def test_evidence_hashes_scope_and_dossier_are_frozen(self):
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        self.assertEqual(evidence["critical_base_text_sha256"], BASE_CRITICAL_TEXT_SHA256)
        self.assertEqual(evidence["scope_limitations"], list(SCOPE_LIMITATIONS))
        self.assertEqual(
            evidence["required_provider_dossier_sections"],
            list(POST_C8_G1_REQUIRED_DOSSIER_SECTIONS),
        )
        self.assertEqual(canonical_text_sha256(EVIDENCE), G1_EVIDENCE_MANIFEST_SHA256)

    def test_overlay_identity_is_external_and_uncommitted(self):
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        overlay = evidence["overlay_identity"]
        self.assertEqual(overlay["base_commit_sha40"], POST_C8_G1_BASE_COMMIT_SHA40)
        self.assertIsNone(overlay["delivery_zip_sha256"])
        self.assertIsNone(overlay["git_commit_sha40"])
        self.assertEqual(overlay["paths"], list(G1_OVERLAY_PATHS))

    def test_frozen_base_documents_and_runtime_boundaries_are_unchanged(self):
        for name, expected_hash in BASE_CRITICAL_TEXT_SHA256.items():
            if name == "app.py":
                # G1 freezes the base delivery.  A1-S is a later, independently
                # gated runtime overlay, so current app.py must no longer equal
                # the C5/G1 file while the recorded base hash stays immutable.
                self.assertEqual(
                    expected_hash,
                    "fd089d1cce4f65ebe6fb84b380dd13eba8a98ba4a16049515f4f513c27eeb7ea",
                )
                continue
            self.assertEqual(canonical_text_sha256(ROOT / name), expected_hash, name)
        app_text = (ROOT / "app.py").read_text(encoding="utf-8")
        init_text = (ROOT / "rtm_connect/__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("post_c8_g1", app_text.lower())
        self.assertNotIn("post_c8_g1", init_text.lower())
        self.assertIn("human_filing_router", app_text)
        self.assertIn("human_filing_gate_middleware", app_text)


if __name__ == "__main__":
    unittest.main()
