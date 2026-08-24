from __future__ import annotations

import inspect
import unittest
from dataclasses import fields, replace

import rtm_connect.production_contracts as contracts_module
from rtm_connect.production_contracts import (
    ProductionAdmissionAssessment,
    ProductionAdmissionCandidate,
    ProductionApprovalRole,
    ProductionContractError,
    ProductionReleaseApproval,
    SimulatedOutboxIntent,
    SimulatedOutboxStatus,
    candidate_sha256,
    expected_c8_admission_payload,
)


CANDIDATE_ID = "11111111-1111-4111-8111-111111111111"
REQUESTER_ID = "22222222-2222-4222-8222-222222222222"
SECURITY_ID = "33333333-3333-4333-8333-333333333333"
OPERATIONS_ID = "44444444-4444-4444-8444-444444444444"
ACTION_ID = "55555555-5555-4555-8555-555555555555"
AUTHORIZATION_ID = "66666666-6666-4666-8666-666666666666"
INTENT_ID = "77777777-7777-4777-8777-777777777777"


def candidate(**overrides) -> ProductionAdmissionCandidate:
    values = {
        "candidate_id": CANDIDATE_ID,
        "requested_by_operator_id": REQUESTER_ID,
        "source_commit_sha40": "a" * 40,
        "build_artifact_sha256": "a" * 64,
        "connector_manifest_sha256": "b" * 64,
        "provider_contract_sha256": "c" * 64,
        "egress_policy_sha256": "d" * 64,
        "credential_reference_sha256": "e" * 64,
        "schema_snapshot_sha256": "f" * 64,
        "test_report_sha256": "0" * 64,
        "created_at": "2026-08-24T10:00:00Z",
        "expires_at": "2026-08-25T10:00:00Z",
        "canary_percent": 5,
        "concurrency": 1,
        "max_simulated_actions_total": 1,
        "max_simulated_actions_per_day": 1,
        "max_payload_bytes": 8192,
        "admission_ttl_seconds": 86400,
    }
    values.update(overrides)
    return ProductionAdmissionCandidate(**values)


def approval(role: ProductionApprovalRole, operator_id: str, **overrides):
    item = candidate()
    values = {
        "approval_id": (
            "88888888-8888-4888-8888-888888888888"
            if role is ProductionApprovalRole.SECURITY
            else "99999999-9999-4999-8999-999999999999"
        ),
        "candidate_id": item.candidate_id,
        "candidate_sha256": candidate_sha256(item),
        "requested_by_operator_id": item.requested_by_operator_id,
        "approver_operator_id": operator_id,
        "approval_role": role,
        "approved_at": "2026-08-24T11:00:00Z",
        "expires_at": "2026-08-25T09:00:00Z",
    }
    values.update(overrides)
    return ProductionReleaseApproval(**values)


def intent(**overrides) -> SimulatedOutboxIntent:
    item = candidate()
    values = {
        "intent_id": INTENT_ID,
        "candidate_id": item.candidate_id,
        "action_id": ACTION_ID,
        "authorization_id": AUTHORIZATION_ID,
        "candidate_sha256": candidate_sha256(item),
        "request_sha256": "1" * 64,
        "idempotency_key": "rtmc1:" + ("2" * 64),
        "status": SimulatedOutboxStatus.PREPARED,
        "created_at": "2026-08-24T12:00:00Z",
        "reconciliation_required": False,
    }
    values.update(overrides)
    return SimulatedOutboxIntent(**values)


class ConnectC8ProductionContractsTest(unittest.TestCase):
    def test_candidate_is_strict_hash_bound_and_deterministic(self):
        first = candidate()
        second = candidate()
        self.assertEqual(candidate_sha256(first), candidate_sha256(second))
        self.assertRegex(candidate_sha256(first), r"^[0-9a-f]{64}$")
        changed = replace(first, test_report_sha256="1" * 64)
        self.assertNotEqual(candidate_sha256(first), candidate_sha256(changed))

    def test_candidate_rejects_bad_uuid_commit_hash_and_non_utc_time(self):
        mutations = (
            {"candidate_id": "not-a-uuid"},
            {"source_commit_sha40": "a" * 39},
            {"provider_contract_sha256": "z" * 64},
            {"created_at": "2026-08-24T10:00:00"},
            {"created_at": "2026-08-24T12:00:00+02:00"},
            {"expires_at": "2026-08-24T09:00:00Z"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(ProductionContractError):
                    candidate(**mutation)

    def test_candidate_freezes_canary_concurrency_and_positive_limits(self):
        mutations = (
            {"canary_percent": 0},
            {"canary_percent": 6},
            {"canary_percent": True},
            {"concurrency": 2},
            {"max_simulated_actions_total": 0},
            {"max_simulated_actions_total": 2},
            {"max_simulated_actions_total": 2_147_483_648},
            {"max_simulated_actions_per_day": 2},
            {"max_payload_bytes": -1},
            {"max_payload_bytes": 2_147_483_648},
            {"admission_ttl_seconds": 3600},
            {"admission_ttl_seconds": 2_147_483_648},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(ProductionContractError):
                    candidate(**mutation)

    def test_candidate_flags_can_only_describe_an_inert_human_gate(self):
        mutations = (
            {"simulation_only": False},
            {"external_effects_allowed": True},
            {"live_activation_allowed": True},
            {"human_activation_required": False},
            {"simulation_only": 1},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(ProductionContractError):
                    candidate(**mutation)

    def test_candidate_contains_only_digests_for_provider_egress_and_credential(self):
        names = {field.name for field in fields(ProductionAdmissionCandidate)}
        self.assertIn("provider_contract_sha256", names)
        self.assertIn("egress_policy_sha256", names)
        self.assertIn("credential_reference_sha256", names)
        self.assertTrue(
            names.isdisjoint(
                {
                    "provider_endpoint",
                    "provider_origin",
                    "provider_url",
                    "credential_ref",
                    "secret",
                    "token",
                    "private_key",
                }
            )
        )

    def test_payload_is_an_exact_synthetic_allowlist(self):
        digest = candidate_sha256(candidate())
        self.assertEqual(
            set(expected_c8_admission_payload(digest)),
            {
                "contract_version",
                "candidate_sha256",
                "synthetic_marker",
                "simulation_only",
                "external_effects_allowed",
                "live_activation_allowed",
                "human_activation_required",
            },
        )
        with self.assertRaises(ProductionContractError):
            expected_c8_admission_payload("bad")

    def test_release_approval_is_role_bound_inert_and_not_self_approved(self):
        security = approval(ProductionApprovalRole.SECURITY, SECURITY_ID)
        self.assertEqual(security.approval_role, ProductionApprovalRole.SECURITY)
        mutations = (
            {"approver_operator_id": REQUESTER_ID},
            {"approval_role": "owner"},
            {"decision": "activate_live"},
            {"external_effects_allowed": True},
            {"expires_at": "2026-08-24T10:30:00Z"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(ProductionContractError):
                    approval(
                        ProductionApprovalRole.SECURITY,
                        SECURITY_ID,
                        **mutation,
                    )

    def test_outbox_statuses_match_workflow_and_never_enable_effects(self):
        self.assertEqual(
            {status.value for status in SimulatedOutboxStatus},
            {
                "prepared",
                "claimed",
                "dry_run_confirmed",
                "unknown",
                "manual_review",
                "cancelled",
            },
        )
        for status in SimulatedOutboxStatus:
            requires_reconciliation = status in {
                SimulatedOutboxStatus.UNKNOWN,
                SimulatedOutboxStatus.MANUAL_REVIEW,
            }
            item = intent(
                status=status,
                reconciliation_required=requires_reconciliation,
            )
            self.assertFalse(item.external_effects_allowed)
            self.assertFalse(item.network_call_performed)
            self.assertFalse(item.secret_resolution_performed)
            self.assertFalse(item.blind_retry_allowed)

    def test_outbox_rejects_reconciliation_mismatch_or_effect_flags(self):
        mutations = (
            {
                "status": SimulatedOutboxStatus.UNKNOWN,
                "reconciliation_required": False,
            },
            {
                "status": SimulatedOutboxStatus.PREPARED,
                "reconciliation_required": True,
            },
            {"network_call_performed": True},
            {"secret_resolution_performed": True},
            {"blind_retry_allowed": True},
            {"idempotency_key": "invalid"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(ProductionContractError):
                    intent(**mutation)

    def test_assessment_is_structurally_no_go(self):
        assessment = ProductionAdmissionAssessment(
            candidate_sha256=candidate_sha256(candidate()),
            evaluated_at="2026-08-24T12:00:00Z",
            blocker_codes=("provider_specific_pack_missing",),
        )
        self.assertEqual(assessment.verdict, "no_go")
        self.assertTrue(assessment.simulation_admitted)
        self.assertFalse(assessment.live_production_admitted)
        self.assertFalse(assessment.production_effects_available)
        with self.assertRaises(ProductionContractError):
            replace(assessment, verdict="go")

    def test_contract_module_has_no_network_web_or_secret_resolver_import(self):
        source = inspect.getsource(contracts_module)
        for forbidden in (
            "import socket",
            "import requests",
            "import httpx",
            "import urllib",
            "from fastapi",
            "secret_resolver",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
