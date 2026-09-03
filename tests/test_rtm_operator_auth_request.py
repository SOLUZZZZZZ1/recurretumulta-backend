from __future__ import annotations

import unittest

from starlette.datastructures import Headers

from rtm_core.operator_auth_request import (
    OPERATOR_AUTH_MODE_FAIL_CLOSED,
    OPERATOR_AUTH_MODE_INDIVIDUAL,
    OPERATOR_AUTH_MODE_LEGACY,
    OperatorAuthRoutesDisabled,
    OperatorAuthRuntimeMisconfigured,
    build_request_fingerprint,
    extract_bearer_token,
    load_operator_auth_runtime_config,
    mask_ip,
    normalize_device_token,
    operator_auth_environment_mode,
    parse_user_agent,
)


class OperatorAuthRequestTest(unittest.TestCase):
    def test_environment_mode_never_reopens_staging_on_identity_drift(self):
        self.assertEqual(
            operator_auth_environment_mode({"RTM_ENV": "staging"}),
            OPERATOR_AUTH_MODE_INDIVIDUAL,
        )
        for environment in ({}, {"RTM_ENV": "production"}):
            with self.subTest(environment=environment):
                self.assertEqual(
                    operator_auth_environment_mode(
                        {
                            **environment,
                            "RTM_ENABLE_OPERATOR_AUTH_V1": "1",
                        }
                    ),
                    OPERATOR_AUTH_MODE_FAIL_CLOSED,
                )
        for marker in (
            "RTM_INSTANCE_ID",
            "RTM_DATA_NAMESPACE",
            "RENDER_SERVICE_NAME",
        ):
            with self.subTest(marker=marker):
                self.assertEqual(
                    operator_auth_environment_mode(
                        {
                            "RTM_ENV": "stagin",
                            marker: "recurretumulta-staging-service",
                        }
                    ),
                    OPERATOR_AUTH_MODE_FAIL_CLOSED,
                )
        self.assertEqual(
            operator_auth_environment_mode(
                {
                    "RTM_ENV": "production",
                    "RTM_ENABLE_OPERATOR_AUTH_V1": "definitely",
                }
            ),
            OPERATOR_AUTH_MODE_FAIL_CLOSED,
        )

    def test_environment_mode_never_allows_shared_legacy_production(self):
        self.assertEqual(
            operator_auth_environment_mode(
                {
                    "RTM_ENV": "production",
                    "RTM_ENABLE_OPERATOR_AUTH_V1": "0",
                    "RTM_INSTANCE_ID": "recurretumulta-production",
                    "RTM_DATA_NAMESPACE": "rtm_production",
                    "RENDER_SERVICE_NAME": "recurretumulta-api",
                }
            ),
            OPERATOR_AUTH_MODE_FAIL_CLOSED,
        )

    def test_legacy_mode_is_local_only_and_ambiguous_deployments_fail_closed(self):
        for environment in ("development", "test"):
            with self.subTest(environment=environment):
                self.assertEqual(
                    operator_auth_environment_mode({"RTM_ENV": environment}),
                    OPERATOR_AUTH_MODE_LEGACY,
                )
        self.assertEqual(
            operator_auth_environment_mode(
                {"DATABASE_URL": "postgresql://deployed.example/rtm"}
            ),
            OPERATOR_AUTH_MODE_FAIL_CLOSED,
        )
        self.assertEqual(
            operator_auth_environment_mode(
                {
                    "RTM_ENV": "development",
                    "RENDER_SERVICE_ID": "srv-deployed",
                }
            ),
            OPERATOR_AUTH_MODE_FAIL_CLOSED,
        )

    def test_emergency_switch_blocks_legacy_operator_auth(self):
        for value in ("1", "true", "enabled"):
            with self.subTest(value=value):
                self.assertEqual(
                    operator_auth_environment_mode(
                        {
                            "RTM_ENV": "production",
                            "RTM_ENABLE_OPERATOR_AUTH_V1": "0",
                            "RTM_BLOCK_LEGACY_OPERATOR_AUTH": value,
                        }
                    ),
                    OPERATOR_AUTH_MODE_FAIL_CLOSED,
                )
        self.assertEqual(
            operator_auth_environment_mode(
                {
                    "RTM_ENV": "production",
                    "RTM_BLOCK_LEGACY_OPERATOR_AUTH": "invalid",
                }
            ),
            OPERATOR_AUTH_MODE_FAIL_CLOSED,
        )

    def test_feature_is_disabled_by_default(self):
        with self.assertRaises(OperatorAuthRoutesDisabled):
            load_operator_auth_runtime_config(
                {
                    "RTM_ENV": "staging",
                    "RTM_OPERATOR_ACCESS_RETENTION_DAYS": "180",
                }
            )

    def test_enabled_feature_requires_staging_and_hmac_key(self):
        base = {
            "RTM_ENV": "staging",
            "RTM_ENABLE_OPERATOR_AUTH_V1": "1",
            "RTM_OPERATOR_ACCESS_HMAC_KEY": "K" * 64,
            "RTM_TRUST_PROXY_HEADERS": "1",
            "RTM_TRUSTED_PROXY_CIDRS": "10.0.0.0/8",
            "RTM_OPERATOR_ACCESS_RETENTION_DAYS": "180",
        }
        config = load_operator_auth_runtime_config(base)
        self.assertTrue(config.available)
        self.assertTrue(config.trust_proxy_headers)

        outside = dict(base, RTM_ENV="production")
        with self.assertRaises(OperatorAuthRuntimeMisconfigured):
            load_operator_auth_runtime_config(outside)

        weak = dict(base, RTM_OPERATOR_ACCESS_HMAC_KEY="short")
        with self.assertRaises(OperatorAuthRuntimeMisconfigured):
            load_operator_auth_runtime_config(weak)

    def test_retention_is_bounded(self):
        base = {
            "RTM_ENV": "staging",
            "RTM_ENABLE_OPERATOR_AUTH_V1": "1",
            "RTM_OPERATOR_ACCESS_HMAC_KEY": "K" * 64,
        }
        for value in ("29", "366", "not-number"):
            with self.subTest(value=value):
                with self.assertRaises(OperatorAuthRuntimeMisconfigured):
                    load_operator_auth_runtime_config(
                        dict(base, RTM_OPERATOR_ACCESS_RETENTION_DAYS=value)
                    )

    def test_reauthentication_window_is_configurable_and_bounded(self):
        base = {
            "RTM_ENV": "staging",
            "RTM_ENABLE_OPERATOR_AUTH_V1": "1",
            "RTM_OPERATOR_ACCESS_HMAC_KEY": "K" * 64,
        }
        self.assertEqual(
            load_operator_auth_runtime_config(base)
            .reauthentication_max_age_seconds,
            300,
        )
        self.assertEqual(
            load_operator_auth_runtime_config(
                dict(base, RTM_OPERATOR_REAUTH_MAX_AGE_SECONDS="120")
            ).reauthentication_max_age_seconds,
            120,
        )
        for value in ("59", "901", "not-number"):
            with self.subTest(value=value):
                with self.assertRaises(OperatorAuthRuntimeMisconfigured):
                    load_operator_auth_runtime_config(
                        dict(
                            base,
                            RTM_OPERATOR_REAUTH_MAX_AGE_SECONDS=value,
                        )
                    )

    def test_bearer_token_parser_is_strict(self):
        self.assertEqual(
            extract_bearer_token("Bearer " + ("x" * 48)),
            "x" * 48,
        )
        self.assertIsNone(extract_bearer_token("Basic abc"))
        self.assertIsNone(extract_bearer_token("Bearer short"))

    def test_device_token_is_opaque_not_hardware_data(self):
        self.assertEqual(
            normalize_device_token("A" * 32),
            "A" * 32,
        )
        self.assertIsNone(normalize_device_token("aa:bb:cc:dd:ee:ff"))
        self.assertIsNone(normalize_device_token("short"))

    def test_ip_masking(self):
        self.assertEqual(mask_ip("203.0.113.44"), "203.0.113.xxx")
        self.assertTrue(str(mask_ip("2001:db8:abcd:12::1")).endswith("/48"))
        self.assertIsNone(mask_ip("not-ip"))

    def test_user_agent_normalization(self):
        parsed = parse_user_agent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0"
        )
        self.assertEqual(parsed[0], "desktop")
        self.assertEqual(parsed[1], "Windows")
        self.assertEqual(parsed[3], "Edge")
        self.assertEqual(parsed[4], "151.0.0.0")

    def test_proxy_headers_are_only_trusted_when_enabled(self):
        headers = {
            "x-forwarded-for": "203.0.113.44, 10.0.0.1",
            "user-agent": "curl/8.0",
            "x-vercel-ip-country": "ES",
        }
        trusted = build_request_fingerprint(
            headers,
            client_host="10.0.0.2",
            hmac_key="K" * 64,
            trust_proxy_headers=True,
            trusted_proxy_cidrs=("10.0.0.0/8",),
        )
        self.assertEqual(trusted.ip_address, "203.0.113.44")
        self.assertEqual(trusted.ip_source, "x_forwarded_for")
        self.assertEqual(trusted.country_code, "ES")

        prefixed_spoof = build_request_fingerprint(
            {
                **headers,
                "x-forwarded-for": (
                    "198.51.100.66, 203.0.113.44, 10.0.0.1"
                ),
            },
            client_host="10.0.0.2",
            hmac_key="K" * 64,
            trust_proxy_headers=True,
            trusted_proxy_cidrs=("10.0.0.0/8",),
        )
        self.assertEqual(prefixed_spoof.ip_address, "203.0.113.44")

        ignored = build_request_fingerprint(
            headers,
            client_host="10.0.0.2",
            hmac_key="K" * 64,
            trust_proxy_headers=False,
        )
        self.assertEqual(ignored.ip_address, "10.0.0.2")
        self.assertIn("proxy_headers_ignored", ignored.risk_flags)

        spoofed = build_request_fingerprint(
            headers,
            client_host="198.51.100.9",
            hmac_key="K" * 64,
            trust_proxy_headers=True,
            trusted_proxy_cidrs=("10.0.0.0/8",),
        )
        self.assertEqual(spoofed.ip_address, "198.51.100.9")
        self.assertEqual(spoofed.ip_source, "direct")
        self.assertIsNone(spoofed.country_code)
        self.assertIn("untrusted_proxy_peer", spoofed.risk_flags)

    def test_duplicate_forwarded_header_cannot_choose_audit_identity(self):
        headers = Headers(
            raw=[
                (b"x-forwarded-for", b"198.51.100.66"),
                (b"x-forwarded-for", b"203.0.113.44"),
                (b"user-agent", b"curl/8.0"),
            ]
        )
        context = build_request_fingerprint(
            headers,
            client_host="10.0.0.2",
            hmac_key="K" * 64,
            trust_proxy_headers=True,
            trusted_proxy_cidrs=("10.0.0.0/8",),
        )

        self.assertEqual(context.ip_address, "10.0.0.2")
        self.assertEqual(context.ip_source, "direct")
        self.assertNotIn("x-forwarded-for", context.trusted_headers)


if __name__ == "__main__":
    unittest.main()
