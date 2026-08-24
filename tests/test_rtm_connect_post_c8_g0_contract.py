from __future__ import annotations

import ast
import inspect
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import rtm_connect_post_c8_g0 as gate
from rtm_connect_post_c8_g0 import (
    POST_C8_GATE_BASE_ARCHIVE_SHA256,
    POST_C8_GATE_BASE_COMMIT_SHA40,
    POST_C8_GATE_BASELINE_SNAPSHOT_SHA256,
    POST_C8_GATE_FROZEN_EVALUATED_AT,
    POST_C8_GATE_REQUIRED_APPROVAL_ROLES,
    POST_C8_GATE_REQUIRED_AUTHORITY_CHAIN,
    POST_C8_GATE_REQUIRED_EVIDENCE_CONTROLS,
    PostC8GateDomain,
    PostC8GateError,
    PostC8LiveActivationUnavailable,
    assess_post_c8_gate,
    assert_g0_live_activation_unavailable,
    post_c8_gate_fingerprint_material,
    post_c8_gate_sha256,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "rtm_connect_post_c8_g0.py"
EVALUATED_AT = POST_C8_GATE_FROZEN_EVALUATED_AT


def assessment(**overrides):
    values = {
        "source_commit_sha40": POST_C8_GATE_BASE_COMMIT_SHA40,
        "base_archive_sha256": POST_C8_GATE_BASE_ARCHIVE_SHA256,
        "baseline_snapshot_sha256": POST_C8_GATE_BASELINE_SNAPSHOT_SHA256,
        "evaluated_at": EVALUATED_AT,
    }
    values.update(overrides)
    return assess_post_c8_gate(**values)


class ConnectPostC8G0ContractTest(unittest.TestCase):
    def test_exact_base_identity_is_frozen(self):
        self.assertEqual(
            POST_C8_GATE_BASE_COMMIT_SHA40,
            "a0ecdebd4575d54f7e89c69b9871a29039370d22",
        )
        self.assertEqual(
            POST_C8_GATE_BASE_ARCHIVE_SHA256,
            "5832b0acd854e0dc5d864521a5a9350e44802facb74eea6d28cc15f44dbbd14f",
        )
        self.assertEqual(
            POST_C8_GATE_BASELINE_SNAPSHOT_SHA256,
            "cc819ed72839500946910b643b30a181018a9665bc1fb3c37b67228697a116a5",
        )

    def test_assessment_is_exactly_six_blocked_domains_and_no_go(self):
        item = assessment()
        self.assertEqual(
            tuple(finding.domain for finding in item.findings),
            tuple(PostC8GateDomain),
        )
        self.assertEqual(len(item.findings), 6)
        self.assertTrue(all(finding.status == "blocked" for finding in item.findings))
        self.assertTrue(all(finding.blocker_codes for finding in item.findings))
        self.assertEqual(item.gate_status, "blocked")
        self.assertEqual(item.live_verdict, "no_go")
        self.assertEqual(item.live_canary_percent, 0)

    def test_blocker_codes_are_unique_normalized_and_domain_bound(self):
        item = assessment()
        all_codes = []
        for finding in item.findings:
            for code in finding.blocker_codes:
                self.assertTrue(code.startswith(f"{finding.domain.value}."))
                self.assertRegex(code, r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
                all_codes.append(code)
        self.assertEqual(len(all_codes), len(set(all_codes)))
        for expected in (
            "security.legacy_submission_bypass_not_closed",
            "security.embedded_legal_signature_asset_custody_and_classification_not_resolved",
            "security.global_external_effect_inventory_and_guards_incomplete",
            "security.independent_security_approval_missing",
            "operations.runtime_environment_startup_guard_not_enforced",
            "operations.approval_expiry_and_revocation_controls_missing",
            "privacy.embedded_signature_custody_authorization_missing",
            "privacy.privacy_and_legal_compliance_approvals_missing",
            "provider.authentic_e4_verifier_missing",
            "provider.provider_owner_approval_missing",
            "canary.live_percentage_must_remain_zero_in_g0",
            "canary.core_requester_independent_activator_separation_missing",
            "rollback.remote_effect_restore_drill_missing",
        ):
            self.assertIn(expected, all_codes)

    def test_wrong_commit_archive_snapshot_or_time_fails_closed(self):
        mutations = (
            {"source_commit_sha40": "0" * 40},
            {"source_commit_sha40": "bad"},
            {"base_archive_sha256": "0" * 64},
            {"base_archive_sha256": "bad"},
            {"baseline_snapshot_sha256": "0" * 64},
            {"evaluated_at": "2026-08-24T17:15:00"},
            {"evaluated_at": "2026-08-24T19:15:00+02:00"},
            {"evaluated_at": "2026-08-24T17:16:00Z"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(PostC8GateError):
                    assessment(**mutation)

    def test_assessment_and_findings_are_immutable(self):
        item = assessment()
        with self.assertRaises(FrozenInstanceError):
            item.live_verdict = "go"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            item.findings[0].status = "cleared"  # type: ignore[misc]

    def test_caller_lists_are_normalized_to_immutable_tuples(self):
        item = assessment()
        activation = list(item.activation_blockers)
        roles = list(item.required_approval_roles)
        chain = list(item.required_authority_chain)
        controls = list(item.required_evidence_controls)
        normalized = replace(
            item,
            activation_blockers=activation,
            required_approval_roles=roles,
            required_authority_chain=chain,
            required_evidence_controls=controls,
        )
        activation.clear()
        roles.clear()
        chain.clear()
        controls.clear()
        self.assertIsInstance(normalized.activation_blockers, tuple)
        self.assertEqual(normalized.activation_blockers, item.activation_blockers)
        self.assertEqual(normalized.required_approval_roles, item.required_approval_roles)
        self.assertEqual(normalized.required_authority_chain, item.required_authority_chain)
        self.assertEqual(normalized.required_evidence_controls, item.required_evidence_controls)

    def test_every_authority_runtime_and_effect_flag_is_structurally_false(self):
        item = assessment()
        false_fields = {
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
        }
        self.assertTrue(false_fields.issubset({field.name for field in fields(item)}))
        for name in false_fields:
            self.assertIs(getattr(item, name), False)
            with self.subTest(name=name):
                with self.assertRaises(PostC8GateError):
                    replace(item, **{name: True})
        for name in ("review_only", "offline_only", "read_only"):
            self.assertIs(getattr(item, name), True)
            with self.assertRaises(PostC8GateError):
                replace(item, **{name: False})
        with self.assertRaises(PostC8GateError):
            replace(item, live_canary_percent=1)
        self.assertEqual(item.required_approval_roles, POST_C8_GATE_REQUIRED_APPROVAL_ROLES)
        self.assertEqual(item.required_authority_chain, POST_C8_GATE_REQUIRED_AUTHORITY_CHAIN)
        self.assertEqual(item.required_evidence_controls, POST_C8_GATE_REQUIRED_EVIDENCE_CONTROLS)
        for name in (
            "required_approval_roles",
            "required_authority_chain",
            "required_evidence_controls",
        ):
            with self.assertRaises(PostC8GateError):
                replace(item, **{name: ()})

    def test_findings_cannot_be_removed_reordered_or_marked_clear(self):
        item = assessment()
        mutations = (
            item.findings[:-1],
            tuple(reversed(item.findings)),
            (replace(item.findings[0], blocker_codes=("security.fake",)),)
            + item.findings[1:],
        )
        for findings in mutations:
            with self.subTest(findings=len(findings)):
                with self.assertRaises(PostC8GateError):
                    replace(item, findings=findings)
        with self.assertRaises(PostC8GateError):
            replace(item.findings[0], status="reviewed")

    def test_fingerprint_is_canonical_deterministic_and_complete(self):
        first = assessment()
        second = assessment()
        self.assertEqual(post_c8_gate_sha256(first), post_c8_gate_sha256(second))
        self.assertRegex(post_c8_gate_sha256(first), r"^[0-9a-f]{64}$")
        material = post_c8_gate_fingerprint_material(first)
        self.assertEqual(material["live_verdict"], "no_go")
        self.assertEqual(material["live_canary_percent"], 0)
        self.assertFalse(material["production_authorized"])
        self.assertEqual(len(material["findings"]), 6)
        self.assertEqual(material["evaluated_at"], POST_C8_GATE_FROZEN_EVALUATED_AT)
        self.assertEqual(
            post_c8_gate_sha256(first),
            "f3e50831c3f3aa4d06382a32636a3a635524b3000f2611beeb6d7ad0f835c2a0",
        )

    def test_live_activation_guard_is_unconditional(self):
        for supplied in (None, assessment()):
            with self.subTest(supplied=supplied is not None):
                with self.assertRaises(PostC8LiveActivationUnavailable):
                    assert_g0_live_activation_unavailable(assessment=supplied)

    def test_public_surface_is_exact_and_has_no_clear_or_activation_api(self):
        self.assertEqual(
            gate.__all__,
            [
                "RTM_CONNECT_POST_C8_G0_VERSION",
                "POST_C8_GATE_CONTRACT_VERSION",
                "POST_C8_GATE_BASE_COMMIT_SHA40",
                "POST_C8_GATE_BASE_ARCHIVE_SHA256",
                "POST_C8_GATE_BASELINE_SNAPSHOT_SHA256",
                "POST_C8_GATE_FROZEN_EVALUATED_AT",
                "POST_C8_GATE_NEXT_STEP",
                "POST_C8_GATE_REQUIRED_APPROVAL_ROLES",
                "POST_C8_GATE_REQUIRED_AUTHORITY_CHAIN",
                "POST_C8_GATE_REQUIRED_EVIDENCE_CONTROLS",
                "PostC8GateAssessment",
                "PostC8GateDomain",
                "PostC8GateError",
                "PostC8GateFinding",
                "PostC8LiveActivationUnavailable",
                "assess_post_c8_gate",
                "assert_g0_live_activation_unavailable",
                "post_c8_gate_fingerprint_material",
                "post_c8_gate_sha256",
            ],
        )
        exported_lower = {name.lower() for name in gate.__all__}
        for forbidden in (
            "go_live",
            "approve",
            "authorize",
            "activate",
            "send",
            "submit",
            "dispatch",
            "resolve_secret",
            "connect_database",
        ):
            self.assertFalse(any(forbidden in name for name in exported_lower))

    def test_module_imports_only_standard_library_and_has_no_io_surface(self):
        source = MODULE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(MODULE))
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots.update(
            str(node.module or "").split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        self.assertTrue(
            imported_roots.issubset(
                {"__future__", "hashlib", "json", "re", "dataclasses", "datetime", "enum", "types", "typing"}
            )
        )
        self.assertTrue(
            imported_roots.isdisjoint(
                {"fastapi", "sqlalchemy", "database", "requests", "httpx", "urllib", "socket", "ssl", "subprocess", "os", "asyncio", "threading"}
            )
        )
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(
            calls.isdisjoint(
                {"open", "get_engine", "include_router", "submit", "send", "dispatch", "resolve_secret"}
            )
        )
        self.assertNotIn("endpoint", {field.name for field in fields(gate.PostC8GateAssessment)})
        self.assertNotIn("tenant", {field.name for field in fields(gate.PostC8GateAssessment)})
        self.assertNotIn("credential", {field.name for field in fields(gate.PostC8GateAssessment)})

    def test_assessment_field_allowlist_and_blocker_catalog_are_immutable(self):
        self.assertEqual(
            tuple(field.name for field in fields(gate.PostC8GateAssessment)),
            (
                "source_commit_sha40", "base_archive_sha256", "baseline_snapshot_sha256",
                "evaluated_at", "findings", "activation_blockers", "contract_version",
                "gate_status", "live_verdict", "next_step", "review_only", "offline_only",
                "read_only", "production_authorized", "authorization_created", "routes_allowed",
                "workers_allowed", "provider_contact_allowed", "network_allowed",
                "secret_access_allowed", "database_access_allowed", "database_ddl_allowed",
                "database_dml_allowed", "real_data_allowed", "external_effects_allowed",
                "live_activation_allowed", "production_effects_available", "production_safe",
                "approval_matrix_satisfied", "authority_chain_satisfied",
                "evidence_freshness_satisfied", "revocation_status_verified",
                "live_canary_percent", "c8_dry_run_is_authentic_e4",
                "required_approval_roles", "required_authority_chain",
                "required_evidence_controls",
            ),
        )
        with self.assertRaises(TypeError):
            gate._FROZEN_BLOCKERS[PostC8GateDomain.SECURITY] = ()


if __name__ == "__main__":
    unittest.main()
