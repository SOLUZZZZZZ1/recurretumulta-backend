from __future__ import annotations

import ast
import inspect
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import rtm_connect_post_c8_g1 as gate
from rtm_connect_post_c8_g1 import (
    POST_C8_G1_BASE_ARCHIVE_SHA256,
    POST_C8_G1_BASE_COMMIT_SHA40,
    POST_C8_G1_BASELINE_SNAPSHOT_SHA256,
    POST_C8_G1_FROZEN_EVALUATED_AT,
    POST_C8_G1_REQUIRED_DOSSIER_SECTIONS,
    PostC8G1Error,
    PostC8G1LiveActivationUnavailable,
    ProviderCandidateCode,
    assess_provider_admission,
    assert_g1_live_activation_unavailable,
    provider_admission_fingerprint_material,
    provider_admission_sha256,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "rtm_connect_post_c8_g1.py"


def assessment(**overrides):
    values = {
        "source_commit_sha40": POST_C8_G1_BASE_COMMIT_SHA40,
        "base_archive_sha256": POST_C8_G1_BASE_ARCHIVE_SHA256,
        "baseline_snapshot_sha256": POST_C8_G1_BASELINE_SNAPSHOT_SHA256,
        "evaluated_at": POST_C8_G1_FROZEN_EVALUATED_AT,
    }
    values.update(overrides)
    return assess_provider_admission(**values)


class ConnectPostC8G1ContractTest(unittest.TestCase):
    def test_exact_g0_base_identity_is_frozen(self):
        self.assertEqual(
            POST_C8_G1_BASE_COMMIT_SHA40,
            "eedd521ecf1703c9b5e20196651da04557900e74",
        )
        self.assertEqual(
            POST_C8_G1_BASE_ARCHIVE_SHA256,
            "8d69d66573d92b675be26d391c1d03a74ff62a514bdf369dfce817db396ba3f3",
        )
        self.assertEqual(
            POST_C8_G1_BASELINE_SNAPSHOT_SHA256,
            "04bbab064c06e58da288e43a2918f57e37ff3eca0f00ece5b81cfdd5f0bc903d",
        )

    def test_exactly_three_legacy_candidates_are_rejected(self):
        item = assessment()
        self.assertEqual(
            tuple(candidate.code for candidate in item.candidates),
            tuple(ProviderCandidateCode),
        )
        self.assertEqual(len(item.candidates), 3)
        for candidate in item.candidates:
            self.assertEqual(candidate.status, "rejected")
            self.assertFalse(candidate.provider_specific)
            self.assertFalse(candidate.production_eligible)
            self.assertTrue(candidate.source_paths)
            self.assertTrue(candidate.blocker_codes)

    def test_candidate_blockers_are_unique_and_normalized(self):
        all_codes: list[str] = []
        for candidate in assessment().candidates:
            self.assertEqual(
                len(candidate.blocker_codes),
                len(set(candidate.blocker_codes)),
            )
            for code in candidate.blocker_codes:
                self.assertRegex(code, r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
                all_codes.append(code)
        self.assertEqual(len(all_codes), len(set(all_codes)))

    def test_required_dossier_is_exact_and_immutable(self):
        item = assessment()
        self.assertEqual(
            item.required_dossier_sections,
            POST_C8_G1_REQUIRED_DOSSIER_SECTIONS,
        )
        self.assertEqual(len(item.required_dossier_sections), 14)
        with self.assertRaises(PostC8G1Error):
            replace(
                item,
                required_dossier_sections=item.required_dossier_sections[:-1],
            )

    def test_positive_review_facts_are_exactly_true(self):
        item = assessment()
        for name in (
            "review_only",
            "offline_only",
            "read_only",
            "base_delivery_identity_verified",
            "g0_decision_preserved",
            "g0_overlay_identity_frozen",
            "legacy_candidates_reviewed",
            "provider_dossier_required",
        ):
            self.assertIs(getattr(item, name), True, name)

    def test_every_authority_runtime_effect_and_provider_flag_is_false(self):
        item = assessment()
        false_names = (
            "provider_selected",
            "provider_identity_verified",
            "provider_pack_present",
            "provider_pack_admissible",
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
            "authentic_e4_verifier_available",
            "remote_idempotency_verified",
            "read_only_lookup_verified",
            "unknown_reconciliation_verified",
            "remote_rollback_verified",
            "legacy_candidates_are_provider_pack",
            "g0_no_go_overridden",
        )
        for name in false_names:
            self.assertIs(getattr(item, name), False, name)
            with self.assertRaises(PostC8G1Error, msg=name):
                replace(item, **{name: True})

    def test_gate_is_structurally_blocked_no_go_and_canary_zero(self):
        item = assessment()
        self.assertEqual(item.gate_status, "blocked")
        self.assertEqual(item.live_verdict, "no_go")
        self.assertEqual(item.live_canary_percent, 0)
        for mutation in (
            {"gate_status": "cleared"},
            {"live_verdict": "go"},
            {"live_canary_percent": 1},
        ):
            with self.assertRaises(PostC8G1Error):
                replace(item, **mutation)

    def test_candidates_cannot_be_removed_reordered_or_promoted(self):
        item = assessment()
        mutations = (
            item.candidates[:-1],
            tuple(reversed(item.candidates)),
        )
        for candidates in mutations:
            with self.assertRaises(PostC8G1Error):
                replace(item, candidates=candidates)
        with self.assertRaises(PostC8G1Error):
            replace(item.candidates[0], status="admitted")

    def test_assessment_and_candidates_are_immutable(self):
        item = assessment()
        with self.assertRaises(FrozenInstanceError):
            item.live_verdict = "go"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            item.candidates[0].status = "admitted"  # type: ignore[misc]

    def test_wrong_commit_archive_snapshot_or_time_fails_closed(self):
        for mutation in (
            {"source_commit_sha40": "0" * 40},
            {"base_archive_sha256": "0" * 64},
            {"baseline_snapshot_sha256": "0" * 64},
            {"evaluated_at": "2026-08-25T05:35:22Z"},
            {"evaluated_at": "2026-08-25T07:35:21+02:00"},
        ):
            with self.assertRaises(PostC8G1Error):
                assessment(**mutation)

    def test_fingerprint_is_canonical_deterministic_and_complete(self):
        first = assessment()
        second = assessment()
        self.assertEqual(
            provider_admission_fingerprint_material(first),
            provider_admission_fingerprint_material(second),
        )
        self.assertEqual(provider_admission_sha256(first), provider_admission_sha256(second))
        self.assertEqual(
            provider_admission_sha256(first),
            "16e3a23fed9e9771fae4a3ce75079a8e7e3d0764aa6ee9fd03268acf3158d253",
        )
        material = provider_admission_fingerprint_material(first)
        self.assertEqual(len(material["candidates"]), 3)
        self.assertEqual(len(material["required_dossier_sections"]), 14)
        self.assertFalse(material["production_safe"])

    def test_live_activation_guard_is_unconditional(self):
        with self.assertRaises(PostC8G1LiveActivationUnavailable) as raised:
            assert_g1_live_activation_unavailable(assessment=assessment())
        self.assertEqual(raised.exception.code, "g1_live_activation_unavailable")
        with self.assertRaises(PostC8G1LiveActivationUnavailable):
            assert_g1_live_activation_unavailable()

    def test_module_imports_only_standard_library_and_has_no_io_surface(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            str(node.module or "").split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        self.assertLessEqual(
            imported,
            {
                "__future__",
                "dataclasses",
                "datetime",
                "enum",
                "hashlib",
                "json",
                "re",
                "types",
                "typing",
            },
        )
        forbidden = {
            "open", "Path", "socket", "requests", "urllib", "sqlalchemy",
            "subprocess", "os", "getenv", "connect", "urlopen",
        }
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        self.assertTrue(forbidden.isdisjoint(names))

    def test_public_surface_is_exact_and_has_no_activation_api(self):
        expected = {
            "RTM_CONNECT_POST_C8_G1_VERSION",
            "POST_C8_G1_CONTRACT_VERSION",
            "POST_C8_G1_BASE_COMMIT_SHA40",
            "POST_C8_G1_BASE_ARCHIVE_SHA256",
            "POST_C8_G1_BASELINE_SNAPSHOT_SHA256",
            "POST_C8_G1_FROZEN_EVALUATED_AT",
            "POST_C8_G1_NEXT_STEP",
            "POST_C8_G1_REQUIRED_DOSSIER_SECTIONS",
            "PostC8G1Error",
            "PostC8G1LiveActivationUnavailable",
            "ProviderAdmissionAssessment",
            "ProviderCandidateCode",
            "ProviderCandidateFinding",
            "assess_provider_admission",
            "assert_g1_live_activation_unavailable",
            "provider_admission_fingerprint_material",
            "provider_admission_sha256",
        }
        self.assertEqual(set(gate.__all__), expected)
        public_functions = {
            name
            for name, value in inspect.getmembers(gate, inspect.isfunction)
            if not name.startswith("_") and name in gate.__all__
        }
        self.assertEqual(
            public_functions,
            {
                "assess_provider_admission",
                "assert_g1_live_activation_unavailable",
                "provider_admission_fingerprint_material",
                "provider_admission_sha256",
            },
        )
        self.assertFalse(any(
            token in name.lower()
            for name in public_functions
            for token in ("activate", "approve", "clear", "execute", "submit")
            if name != "assert_g1_live_activation_unavailable"
        ))

    def test_assessment_field_allowlist_is_frozen(self):
        names = tuple(field.name for field in fields(type(assessment())))
        self.assertEqual(len(names), 49)
        self.assertEqual(names[0:5], (
            "source_commit_sha40",
            "base_archive_sha256",
            "baseline_snapshot_sha256",
            "evaluated_at",
            "candidates",
        ))
        self.assertEqual(names[-1], "live_canary_percent")


if __name__ == "__main__":
    unittest.main()
