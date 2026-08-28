from __future__ import annotations

import unittest

from rtm_connect.provider_sandbox_policy import (
    CONTROLLED_SANDBOX_CREDENTIAL_REF,
    ProviderSandboxEndpoint,
    ProviderSandboxPolicyError,
    assert_c6_staging_boundary,
    load_c6_runtime_endpoint,
)


def safe_env() -> dict[str, str]:
    return {
        "RTM_ENV": "staging",
        "RTM_ENVIRONMENT_CONFIRMATION": "RTM_STAGING_ISOLATED",
        "RTM_INSTANCE_ID": "rtm-staging",
        "RTM_DATA_NAMESPACE": "rtm-staging-c6",
        "RTM_SIDE_EFFECT_POLICY": "isolated",
        "RTM_ALLOW_REAL_CUSTOMER_DATA": "0",
        "DATABASE_URL": (
            "postgresql+psycopg://rtm:password@db.internal/rtm_staging"
        ),
        "FRONTEND_URL": "https://staging.recurretumulta.eu",
        "ALLOWED_ORIGINS": "https://staging.recurretumulta.eu",
        "OPERATOR_TOKEN": "op_" + ("x" * 48),
        "RTM_PUBLIC_CASE_ACCESS_SECRET": "case_" + ("c" * 48),
        "RTM_AUTHORITY_SIGNING_SECRET": "authority_" + ("a" * 48),
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
    }


class ConnectC6ProviderPolicyTest(unittest.TestCase):
    def test_safe_staging_boundary(self):
        assert_c6_staging_boundary(safe_env())

    def test_runtime_branch_must_exactly_match_non_default_expected_branch(self):
        mutations = (
            ("RENDER_GIT_BRANCH", ""),
            ("RENDER_GIT_BRANCH", "another-staging-branch"),
            ("RTM_EXPECTED_BRANCH", "main"),
            ("RTM_EXPECTED_BRANCH", "MAIN"),
            ("RTM_EXPECTED_BRANCH", "master"),
        )
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                env = safe_env()
                env[key] = value
                with self.assertRaises(ProviderSandboxPolicyError):
                    assert_c6_staging_boundary(env)

    def test_production_real_data_and_capabilities_fail_closed(self):
        mutations = {
            "RTM_ENV": "production",
            "RTM_ENVIRONMENT_CONFIRMATION": "RTM_PRODUCTION_LIVE",
            "RTM_INSTANCE_ID": "rtm-production-staging",
            "DATABASE_URL": (
                "postgresql+psycopg://rtm:password@db.internal/rtm_production"
            ),
            "RTM_ALLOW_REAL_CUSTOMER_DATA": "1",
            "RTM_ENABLE_B2": "1",
            "RTM_ENABLE_DOCUMENT_PROVIDER": "1",
            "RTM_ENABLE_EXTERNAL_SUBMISSION": "1",
        }
        for key, value in mutations.items():
            with self.subTest(key=key):
                env = safe_env()
                env[key] = value
                with self.assertRaises(ProviderSandboxPolicyError):
                    assert_c6_staging_boundary(env)

    def test_connected_database_must_match_declared_staging_database(self):
        from rtm_connect.provider_sandbox_policy import assert_c6_database_identity

        class MappingResult:
            def __init__(self, value):
                self.value = value

            def mappings(self):
                return self

            def one(self):
                return {
                    "database_name": self.value,
                    "current_role": "rtm",
                    "session_role": "rtm",
                    "explicit_schemas": ["public"],
                    "effective_schemas": ["pg_catalog", "public"],
                    "temp_schema_oid": 0,
                }

        class Connection:
            def __init__(self, value):
                self.value = value

            def exec_driver_sql(self, statement):
                self.assert_statement = statement
                return MappingResult(self.value)

        self.assertEqual(
            assert_c6_database_identity(
                Connection("rtm_staging"),
                expected_database_name="rtm_staging",
                expected_database_role="rtm",
            ),
            "rtm_staging",
        )
        with self.assertRaises(ProviderSandboxPolicyError):
            assert_c6_database_identity(
                Connection("rtm_production"),
                expected_database_name="rtm_staging",
                expected_database_role="rtm",
            )

    def test_database_identity_rejects_role_search_path_and_temp_shadowing(self):
        from rtm_connect.provider_sandbox_policy import assert_c6_database_identity

        baseline = {
            "database_name": "rtm_staging",
            "current_role": "rtm",
            "session_role": "rtm",
            "explicit_schemas": ["public"],
            "effective_schemas": ["pg_catalog", "public"],
            "temp_schema_oid": 0,
        }

        class Result:
            def __init__(self, row):
                self.row = row

            def mappings(self):
                return self

            def one(self):
                return self.row

        class Connection:
            def __init__(self, row):
                self.row = row

            def exec_driver_sql(self, _statement):
                return Result(self.row)

        mutations = (
            {"current_role": "attacker"},
            {"session_role": "attacker"},
            {"explicit_schemas": ["shadow", "public"]},
            {"effective_schemas": ["pg_catalog", "pg_temp_9", "public"]},
            {"temp_schema_oid": 12345},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                row = dict(baseline)
                row.update(mutation)
                with self.assertRaises(ProviderSandboxPolicyError):
                    assert_c6_database_identity(
                        Connection(row),
                        expected_database_name="rtm_staging",
                        expected_database_role="rtm",
                    )

    def test_feature_is_default_off(self):
        self.assertIsNone(load_c6_runtime_endpoint(safe_env()))

    def test_arbitrary_remote_origin_is_blocked(self):
        with self.assertRaises(ProviderSandboxPolicyError):
            ProviderSandboxEndpoint(
                origin="https://example.com",
                credential_ref=CONTROLLED_SANDBOX_CREDENTIAL_REF,
            )

    def test_userinfo_query_fragment_and_path_are_blocked(self):
        for origin in (
            "https://user@example.com",
            "https://c6-reference-provider.invalid/path",
            "https://c6-reference-provider.invalid?x=1",
            "https://c6-reference-provider.invalid#x",
        ):
            with self.subTest(origin=origin):
                with self.assertRaises(ProviderSandboxPolicyError):
                    ProviderSandboxEndpoint(
                        origin=origin,
                        credential_ref=CONTROLLED_SANDBOX_CREDENTIAL_REF,
                    )

    def test_smoke_factory_allows_only_loopback_http(self):
        endpoint = ProviderSandboxEndpoint.loopback_for_smoke(
            "http://127.0.0.1:54321"
        )
        self.assertTrue(endpoint.loopback_test_only)
        for origin in (
            "http://10.0.0.1:54321",
            "https://127.0.0.1:54321",
            "http://127.0.0.1",
            "http://127.0.0.1:8080",
        ):
            with self.subTest(origin=origin):
                with self.assertRaises(ProviderSandboxPolicyError):
                    ProviderSandboxEndpoint.loopback_for_smoke(origin)

    def test_external_transport_is_hard_blocked_before_dns(self):
        endpoint = ProviderSandboxEndpoint(
            origin="https://c6-reference-provider.invalid",
            credential_ref=CONTROLLED_SANDBOX_CREDENTIAL_REF,
        )
        with self.assertRaises(ProviderSandboxPolicyError):
            endpoint.assert_network_target()

    def test_localhost_name_is_blocked_to_remove_dns_rebinding(self):
        with self.assertRaises(ProviderSandboxPolicyError):
            ProviderSandboxEndpoint.loopback_for_smoke(
                "http://localhost:54321"
            )


if __name__ == "__main__":
    unittest.main()
