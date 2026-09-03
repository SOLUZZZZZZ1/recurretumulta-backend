from __future__ import annotations

import unittest
from dataclasses import replace

from rtm_connect.assisted_legal_policy import (
    ASSISTED_LEGAL_AUTHORITY_CODE,
    ASSISTED_LEGAL_AUTHORITY_VERSION,
    ASSISTED_LEGAL_CAPABILITY,
    ASSISTED_LEGAL_SATELLITE,
    ASSISTED_LEGAL_TARGET_REF,
    ASSISTED_LEGAL_TARGET_TYPE,
    AssistedLegalPolicyError,
    AssistedLegalRuntimeDisabled,
    assert_c7_staging_boundary,
    expected_c7_payload,
    load_c7_runtime_configuration,
    validate_c7_action_authority,
)
from rtm_connect.contracts import (
    AuthorizationGrant,
    ConnectActionRequest,
    ConnectorMode,
    EvidenceLevel,
    RiskClass,
)
from rtm_connect.idempotency import derive_idempotency_key, payload_sha256


ACTION_ID = "11111111-1111-4111-8111-111111111111"
REQUESTER_ID = "22222222-2222-4222-8222-222222222222"
APPROVER_ONE = "33333333-3333-4333-8333-333333333333"
APPROVER_TWO = "44444444-4444-4444-8444-444444444444"
SECRET_A = "A7mQ2vN9kR4xT8pL3sW6cD1hJ5uZ0bY"
SECRET_B = "F9rK3xV7nM2qP8dT4zH6wC1jL5sG0aU"
SECRET_C = "Z6pD1yW8kQ4mR9vB2tN7cH5xJ3fL0sE"


def safe_env() -> dict[str, str]:
    return {
        "RTM_ENV": "staging",
        "RTM_ENVIRONMENT_CONFIRMATION": "RTM_STAGING_ISOLATED",
        "RTM_INSTANCE_ID": "rtm-staging",
        "RTM_DATA_NAMESPACE": "rtm-staging-c7",
        "RTM_SIDE_EFFECT_POLICY": "isolated",
        "RTM_ALLOW_REAL_CUSTOMER_DATA": "0",
        "DATABASE_URL": (
            "postgresql+psycopg://rtm:password@db.internal/rtm_staging"
        ),
        "FRONTEND_URL": "https://staging.recurretumulta.eu",
        "ALLOWED_ORIGINS": "https://staging.recurretumulta.eu",
        "RTM_ALLOWED_HOSTS": "backend-staging.invalid",
        "OPERATOR_TOKEN": "op_" + SECRET_A,
        "RTM_PUBLIC_CASE_ACCESS_SECRET": "case_" + SECRET_B,
        "RTM_AUTHORITY_SIGNING_SECRET": "authority_" + SECRET_C,
        "RTM_EXPECTED_BRANCH": "rtm-core-consolidation-2026-08-08",
        "RENDER_GIT_BRANCH": "rtm-core-consolidation-2026-08-08",
        "RENDER_SERVICE_NAME": "rtm-staging-backend",
        "RTM_DOCUMENT_INPUT_POLICY": "synthetic_only",
        "RTM_ENABLE_B2": "0",
        "RTM_ENABLE_DOCUMENT_PROVIDER": "0",
        "RTM_ENABLE_EXTERNAL_SUBMISSION": "0",
        "RTM_ENABLE_OUTBOUND_EMAIL": "0",
        "RTM_ENABLE_STRIPE": "0",
        "RTM_ENABLE_FINAL_PAYMENTS": "0",
        "RTM_ENABLE_CONNECT_C7_ASSISTED": "0",
    }


def action(**overrides) -> ConnectActionRequest:
    values = {
        "action_id": ACTION_ID,
        "capability": ASSISTED_LEGAL_CAPABILITY,
        "satellite": ASSISTED_LEGAL_SATELLITE,
        "target_type": ASSISTED_LEGAL_TARGET_TYPE,
        "target_ref": ASSISTED_LEGAL_TARGET_REF,
        "payload": expected_c7_payload(),
        "requested_by_operator_id": REQUESTER_ID,
        "requested_at": "2026-01-01T10:00:00Z",
        "risk_class": RiskClass.R4_CRITICAL_REGULATED,
        "document_hashes": ("a" * 64,),
        "requires_dual_control": True,
    }
    values.update(overrides)
    return ConnectActionRequest(**values)


def grant(item: ConnectActionRequest, **overrides) -> AuthorizationGrant:
    values = {
        "authorization_id": "55555555-5555-4555-8555-555555555555",
        "action_id": item.action_id,
        "authority_code": ASSISTED_LEGAL_AUTHORITY_CODE,
        "authority_version": ASSISTED_LEGAL_AUTHORITY_VERSION,
        "decision": "approved_frozen",
        "payload_sha256": payload_sha256(item),
        "idempotency_key": derive_idempotency_key(
            item,
            authority_scope=ASSISTED_LEGAL_AUTHORITY_CODE,
        ),
        "required_evidence_level": EvidenceLevel.E4_RECEIPT_VERIFIED,
        "authorized_connector_modes": (ConnectorMode.ASSISTED,),
        "approved_by_operator_ids": (APPROVER_ONE, APPROVER_TWO),
        "authorized_at": "2026-01-01T10:05:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
        "legal_effect_authorized": True,
    }
    values.update(overrides)
    return AuthorizationGrant(**values)


class ConnectC7AssistedPolicyTest(unittest.TestCase):
    def test_exact_r4_tuple_and_authority_pass(self):
        item = action()
        validate_c7_action_authority(item, grant(item))

    def test_action_tuple_risk_scope_hash_count_and_payload_fail_closed(self):
        mutations = (
            {"capability": "administration.other"},
            {"satellite": "rtm.other"},
            {"target_type": "administration.other"},
            {"target_ref": "real-administration"},
            {"risk_class": RiskClass.R3_LEGAL_OR_FINANCIAL},
            {"case_id": "77777777-7777-4777-8777-777777777777"},
            {"correlation_id": "synthetic-free-form"},
            {"document_hashes": ()},
            {"document_hashes": tuple(str(index) * 64 for index in range(1, 10))},
            {"payload": {**expected_c7_payload(), "extra": True}},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                item = action(**mutation)
                with self.assertRaises(AssistedLegalPolicyError):
                    validate_c7_action_authority(item, grant(item))

    def test_grant_tuple_evidence_mode_effect_and_approvers_fail_closed(self):
        item = action()
        baseline = grant(item)
        variants = (
            {"authority_code": "attacker.self"},
            {"authority_version": "untrusted_v9"},
            {"required_evidence_level": EvidenceLevel.E3_RECEIPT_CAPTURED},
            {"authorized_connector_modes": (ConnectorMode.MANUAL,)},
            {"legal_effect_authorized": False},
            {"approved_by_operator_ids": (APPROVER_ONE,)},
            {"approved_by_operator_ids": (REQUESTER_ID, APPROVER_ONE)},
            {
                "authorized_at": "2099-01-01T00:00:00Z",
                "expires_at": "2099-01-02T00:00:00Z",
            },
        )
        for mutation in variants:
            with self.subTest(mutation=mutation):
                with self.assertRaises(AssistedLegalPolicyError):
                    validate_c7_action_authority(
                        item,
                        replace(baseline, **mutation),
                    )

    def test_safe_staging_boundary_and_default_off_runtime(self):
        boundary = assert_c7_staging_boundary(safe_env())
        self.assertEqual(boundary.environment, "staging")
        self.assertIsNone(load_c7_runtime_configuration(safe_env()))

    def test_runtime_cannot_be_enabled_even_in_safe_staging(self):
        env = safe_env()
        env["RTM_ENABLE_CONNECT_C7_ASSISTED"] = "1"
        with self.assertRaises(AssistedLegalRuntimeDisabled):
            load_c7_runtime_configuration(env)
        with self.assertRaises(AssistedLegalRuntimeDisabled):
            load_c7_runtime_configuration(safe_env(), require_enabled=True)

    def test_ambiguous_runtime_flag_is_rejected(self):
        env = safe_env()
        env["RTM_ENABLE_CONNECT_C7_ASSISTED"] = "perhaps"
        with self.assertRaises(AssistedLegalPolicyError):
            load_c7_runtime_configuration(env)

    def test_production_real_data_capabilities_and_branch_fail_closed(self):
        mutations = {
            "RTM_ENV": "production",
            "DATABASE_URL": (
                "postgresql+psycopg://rtm:password@db.internal/rtm_production"
            ),
            "RTM_ALLOW_REAL_CUSTOMER_DATA": "1",
            "RTM_ENABLE_B2": "1",
            "RTM_ENABLE_DOCUMENT_PROVIDER": "1",
            "RTM_ENABLE_EXTERNAL_SUBMISSION": "1",
            "RTM_ENABLE_OUTBOUND_EMAIL": "1",
            "RTM_EXPECTED_BRANCH": "main",
            "RENDER_GIT_BRANCH": "another-branch",
        }
        for key, value in mutations.items():
            with self.subTest(key=key):
                env = safe_env()
                env[key] = value
                with self.assertRaises(AssistedLegalPolicyError):
                    assert_c7_staging_boundary(env)


if __name__ == "__main__":
    unittest.main()
