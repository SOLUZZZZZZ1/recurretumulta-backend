from __future__ import annotations

import inspect
import unittest
from dataclasses import replace
from datetime import datetime, timezone

import rtm_connect.production_policy as policy_module
from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    RiskClass,
)
from rtm_connect.idempotency import derive_idempotency_key, payload_sha256
from rtm_connect.production_contracts import (
    ProductionAdmissionCandidate,
    ProductionApprovalRole,
    ProductionReleaseApproval,
    candidate_sha256,
    expected_c8_admission_payload,
)
from rtm_connect.production_policy import (
    C8_ADMISSION_AUTHORITY_CODE,
    C8_ADMISSION_AUTHORITY_VERSION,
    C8_ADMISSION_CAPABILITY,
    C8_ADMISSION_MODE,
    C8_ADMISSION_SATELLITE,
    C8_ADMISSION_TARGET_REF,
    C8_ADMISSION_TARGET_TYPE,
    ProductionLiveActivationUnavailable,
    ProductionPolicyError,
    ProductionRuntimeDisabled,
    assert_c8_staging_boundary,
    assert_live_activation_unavailable,
    assess_c8_candidate,
    load_c8_runtime_configuration,
    validate_c8_admission_authority,
    validate_c8_release_approvals,
)


CANDIDATE_ID = "11111111-1111-4111-8111-111111111111"
REQUESTER_ID = "22222222-2222-4222-8222-222222222222"
SECURITY_ID = "33333333-3333-4333-8333-333333333333"
OPERATIONS_ID = "44444444-4444-4444-8444-444444444444"
ACTION_ID = "55555555-5555-4555-8555-555555555555"
AUTHORIZATION_ID = "66666666-6666-4666-8666-666666666666"


def safe_env() -> dict[str, str]:
    return {
        "RTM_ENV": "staging",
        "RTM_ENVIRONMENT_CONFIRMATION": "RTM_STAGING_ISOLATED",
        "RTM_INSTANCE_ID": "rtm-staging",
        "RTM_DATA_NAMESPACE": "rtm-staging-c8",
        "RTM_SIDE_EFFECT_POLICY": "isolated",
        "RTM_ALLOW_REAL_CUSTOMER_DATA": "0",
        "DATABASE_URL": (
            "postgresql+psycopg://rtm:password@db.internal/rtm_staging"
        ),
        "FRONTEND_URL": "https://staging.recurretumulta.eu",
        "ALLOWED_ORIGINS": "https://staging.recurretumulta.eu",
        "OPERATOR_TOKEN": "op_" + ("x" * 48),
        "RTM_EXPECTED_BRANCH": "rtm-c8-staging-2026-08-24",
        "RENDER_GIT_BRANCH": "rtm-c8-staging-2026-08-24",
        "RENDER_SERVICE_NAME": "rtm-staging-backend",
        "RTM_DOCUMENT_INPUT_POLICY": "synthetic_only",
        "RTM_ENABLE_B2": "0",
        "RTM_ENABLE_DOCUMENT_PROVIDER": "0",
        "RTM_ENABLE_EXTERNAL_SUBMISSION": "0",
        "RTM_ENABLE_OUTBOUND_EMAIL": "0",
        "RTM_ENABLE_STRIPE": "0",
        "RTM_ENABLE_FINAL_PAYMENTS": "0",
        "RTM_ENABLE_CONNECT_C6_SANDBOX": "0",
        "RTM_ENABLE_CONNECT_C7_ASSISTED": "0",
        "RTM_ENABLE_CONNECT_C8_CONTROLLED_PRODUCTION": "0",
        "RTM_ENABLE_CONNECT_C8_LIVE": "0",
        "RTM_ALLOW_CONNECT_C8_LIVE_ACTIVATION": "0",
        "RTM_ALLOW_CONNECT_C8_EXTERNAL_EFFECTS": "0",
        "RTM_CONNECT_C8_DISPATCH_ENABLED": "0",
    }


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


def action(item: ProductionAdmissionCandidate | None = None, **overrides):
    selected = item or candidate()
    values = {
        "action_id": ACTION_ID,
        "capability": C8_ADMISSION_CAPABILITY,
        "satellite": C8_ADMISSION_SATELLITE,
        "target_type": C8_ADMISSION_TARGET_TYPE,
        "target_ref": C8_ADMISSION_TARGET_REF,
        "payload": expected_c8_admission_payload(candidate_sha256(selected)),
        "requested_by_operator_id": selected.requested_by_operator_id,
        "requested_at": "2026-08-24T10:15:00Z",
        "risk_class": RiskClass.R4_CRITICAL_REGULATED,
        "requires_dual_control": True,
    }
    values.update(overrides)
    return ConnectActionRequest(**values)


def grant(item: ConnectActionRequest, **overrides):
    values = {
        "authorization_id": AUTHORIZATION_ID,
        "action_id": item.action_id,
        "authority_code": C8_ADMISSION_AUTHORITY_CODE,
        "authority_version": C8_ADMISSION_AUTHORITY_VERSION,
        "decision": "approved_frozen",
        "payload_sha256": payload_sha256(item),
        "idempotency_key": derive_idempotency_key(
            item,
            authority_scope=C8_ADMISSION_AUTHORITY_CODE,
        ),
        "required_evidence_level": EvidenceLevel.E4_RECEIPT_VERIFIED,
        "authorized_connector_modes": (C8_ADMISSION_MODE,),
        "approved_by_operator_ids": (SECURITY_ID, OPERATIONS_ID),
        "authorized_at": "2026-08-24T10:30:00Z",
        "expires_at": "2026-08-25T09:00:00Z",
        "legal_effect_authorized": False,
    }
    values.update(overrides)
    return AuthorizationGrant(**values)


def approval(role, operator_id, **overrides):
    item = candidate()
    values = {
        "approval_id": (
            "77777777-7777-4777-8777-777777777777"
            if role is ProductionApprovalRole.SECURITY
            else "88888888-8888-4888-8888-888888888888"
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


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


class ConnectC8ProductionPolicyTest(unittest.TestCase):
    def test_safe_staging_boundary_is_default_off_and_assessment_is_no_go(self):
        boundary = assert_c8_staging_boundary(safe_env())
        self.assertEqual(boundary.environment, "staging")
        self.assertTrue(boundary.simulation_only)
        self.assertFalse(boundary.external_effects_allowed)
        self.assertFalse(boundary.live_activation_allowed)
        self.assertIsNone(load_c8_runtime_configuration(safe_env()))
        assessment = assess_c8_candidate(
            candidate(),
            values=safe_env(),
            evaluated_at="2026-08-24T12:00:00Z",
        )
        self.assertEqual(assessment.verdict, "no_go")
        self.assertFalse(assessment.production_effects_available)
        self.assertIn("provider_specific_pack_missing", assessment.blocker_codes)

    def test_all_c8_and_adjacent_runtime_flags_must_remain_false(self):
        names = (
            "RTM_ENABLE_CONNECT_C8_CONTROLLED_PRODUCTION",
            "RTM_ENABLE_CONNECT_C8_LIVE",
            "RTM_ALLOW_CONNECT_C8_LIVE_ACTIVATION",
            "RTM_ALLOW_CONNECT_C8_EXTERNAL_EFFECTS",
            "RTM_CONNECT_C8_DISPATCH_ENABLED",
            "RTM_ENABLE_CONNECT_C6_SANDBOX",
            "RTM_ENABLE_CONNECT_C7_ASSISTED",
        )
        for name in names:
            with self.subTest(name=name):
                env = safe_env()
                env[name] = "1"
                with self.assertRaises(ProductionRuntimeDisabled):
                    assert_c8_staging_boundary(env)
        env = safe_env()
        env["RTM_ENABLE_CONNECT_C8_LIVE"] = "perhaps"
        with self.assertRaises(ProductionPolicyError):
            assert_c8_staging_boundary(env)

    def test_any_dormant_live_configuration_is_blocked(self):
        names = (
            "RTM_CONNECT_C8_PROVIDER_ORIGIN",
            "RTM_CONNECT_C8_PROVIDER_ENDPOINT",
            "RTM_CONNECT_C8_PROVIDER_URL",
            "RTM_CONNECT_C8_PROVIDER_TENANT",
            "RTM_CONNECT_C8_CREDENTIAL_REF",
            "RTM_CONNECT_C8_PROVIDER_TOKEN",
            "RTM_CONNECT_C8_CLIENT_SECRET",
            "RTM_CONNECT_C8_PRIVATE_KEY",
            "RTM_CONNECT_C8_EGRESS_PROXY",
            "RTM_CONNECT_C8_RELEASE_TOKEN",
            "RTM_CONNECT_C8_LIVE_ACTIVATION",
        )
        for name in names:
            with self.subTest(name=name):
                env = safe_env()
                env[name] = "forbidden-live-material"
                with self.assertRaises(ProductionRuntimeDisabled):
                    assert_c8_staging_boundary(env)

    def test_c7_c6_boundary_still_blocks_production_data_and_capabilities(self):
        mutations = {
            "RTM_ENV": "production",
            "RTM_ENVIRONMENT_CONFIRMATION": "RTM_PRODUCTION_LIVE",
            "RTM_INSTANCE_ID": "rtm-production",
            "RTM_DATA_NAMESPACE": "rtm-production",
            "RTM_ALLOW_REAL_CUSTOMER_DATA": "1",
            "RTM_ENABLE_EXTERNAL_SUBMISSION": "1",
            "RTM_ENABLE_B2": "1",
            "RTM_EXPECTED_BRANCH": "main",
        }
        for name, value in mutations.items():
            with self.subTest(name=name):
                env = safe_env()
                env[name] = value
                with self.assertRaises(ProductionPolicyError):
                    assert_c8_staging_boundary(env)

    def test_live_activation_is_unconditionally_unavailable(self):
        with self.assertRaises(ProductionLiveActivationUnavailable):
            assert_live_activation_unavailable()
        with self.assertRaises(ProductionLiveActivationUnavailable):
            assert_live_activation_unavailable(
                candidate=candidate(),
                values={
                    "RTM_ENV": "production",
                    "RTM_ENABLE_CONNECT_C8_LIVE": "1",
                },
            )
        with self.assertRaises(ProductionRuntimeDisabled):
            load_c8_runtime_configuration(safe_env(), require_enabled=True)

    def test_exact_r4_e4_dual_control_admission_authority_passes(self):
        selected = candidate()
        request = action(selected)
        validate_c8_admission_authority(
            request,
            grant(request),
            candidate=selected,
            now=NOW,
        )

    def test_action_tuple_scope_and_synthetic_payload_fail_closed(self):
        selected = candidate()
        mutations = (
            {"capability": "connect.production.execute"},
            {"satellite": "rtm.connect.live"},
            {"target_type": "production.provider"},
            {"target_ref": "real-provider"},
            {"risk_class": RiskClass.R3_LEGAL_OR_FINANCIAL},
            {"case_id": "99999999-9999-4999-8999-999999999999"},
            {"correlation_id": "free-form"},
            {"document_hashes": ("a" * 64,)},
            {
                "payload": {
                    **expected_c8_admission_payload(candidate_sha256(selected)),
                    "endpoint": "https://forbidden.example",
                }
            },
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                request = action(selected, **mutation)
                with self.assertRaises(ProductionPolicyError):
                    validate_c8_admission_authority(
                        request,
                        grant(request),
                        candidate=selected,
                        now=NOW,
                    )

    def test_grant_evidence_mode_effect_approvers_hash_and_time_fail_closed(self):
        selected = candidate()
        request = action(selected)
        baseline = grant(request)
        variants = (
            {"authority_code": "attacker.self"},
            {"authority_version": "untrusted_v9"},
            {"required_evidence_level": EvidenceLevel.E3_RECEIPT_CAPTURED},
            {"authorized_connector_modes": (ConnectorMode.MANUAL,)},
            {"legal_effect_authorized": True},
            {"approved_by_operator_ids": (SECURITY_ID,)},
            {"approved_by_operator_ids": (REQUESTER_ID, SECURITY_ID)},
            {"payload_sha256": "f" * 64},
            {"idempotency_key": "rtmc1:" + ("f" * 64)},
            {"expires_at": "2026-08-24T11:00:00Z"},
            {
                "authorized_at": "2026-08-24T09:00:00Z",
                "expires_at": "2026-08-25T09:00:00Z",
            },
        )
        for mutation in variants:
            with self.subTest(mutation=mutation):
                with self.assertRaises(ProductionPolicyError):
                    validate_c8_admission_authority(
                        request,
                        replace(baseline, **mutation),
                        candidate=selected,
                        now=NOW,
                    )

    def test_candidate_digest_requester_and_vigency_are_bound(self):
        selected = candidate()
        request = action(selected)
        other = replace(selected, test_report_sha256="1" * 64)
        with self.assertRaises(ProductionPolicyError):
            validate_c8_admission_authority(
                request,
                grant(request),
                candidate=other,
                now=NOW,
            )
        expired = candidate(
            created_at="2026-08-22T10:00:00Z",
            expires_at="2026-08-23T10:00:00Z",
        )
        expired_request = action(expired)
        with self.assertRaises(ProductionPolicyError):
            validate_c8_admission_authority(
                expired_request,
                grant(expired_request, expires_at="2026-08-23T09:00:00Z"),
                candidate=expired,
                now=NOW,
            )

    def test_security_and_operations_approvals_are_distinct_and_bound(self):
        selected = candidate()
        security = approval(ProductionApprovalRole.SECURITY, SECURITY_ID)
        operations = approval(ProductionApprovalRole.OPERATIONS, OPERATIONS_ID)
        validate_c8_release_approvals(
            selected,
            security,
            operations,
            now=NOW,
        )
        with self.assertRaises(ProductionPolicyError):
            validate_c8_release_approvals(
                selected,
                security,
                replace(operations, approver_operator_id=SECURITY_ID),
                now=NOW,
            )
        with self.assertRaises(ProductionPolicyError):
            validate_c8_release_approvals(
                selected,
                replace(security, approval_role=ProductionApprovalRole.OPERATIONS),
                operations,
                now=NOW,
            )
        with self.assertRaises(ProductionPolicyError):
            validate_c8_release_approvals(
                selected,
                security,
                replace(operations, candidate_sha256="f" * 64),
                now=NOW,
            )
        with self.assertRaises(ProductionPolicyError):
            validate_c8_release_approvals(
                selected,
                security,
                replace(
                    operations,
                    approved_at="2026-08-24T10:59:59Z",
                ),
                now=NOW,
            )

    def test_policy_module_has_no_network_transport_or_secret_resolution(self):
        source = inspect.getsource(policy_module)
        for forbidden in (
            "import socket",
            "import requests",
            "import httpx",
            "import urllib",
            "http.client",
            "secret_resolver",
            "reveal_for_transport",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
