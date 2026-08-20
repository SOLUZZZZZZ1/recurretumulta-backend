from __future__ import annotations

import unittest

from rtm_core.operator_auth_request import (
    OperatorAuthRoutesDisabled,
    OperatorAuthRuntimeMisconfigured,
    build_request_fingerprint,
    extract_bearer_token,
    load_operator_auth_runtime_config,
    mask_ip,
    normalize_device_token,
    parse_user_agent,
)


class OperatorAuthRequestTest(unittest.TestCase):
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
        )
        self.assertEqual(trusted.ip_address, "203.0.113.44")
        self.assertEqual(trusted.ip_source, "x_forwarded_for")
        self.assertEqual(trusted.country_code, "ES")

        ignored = build_request_fingerprint(
            headers,
            client_host="10.0.0.2",
            hmac_key="K" * 64,
            trust_proxy_headers=False,
        )
        self.assertEqual(ignored.ip_address, "10.0.0.2")
        self.assertIn("proxy_headers_ignored", ignored.risk_flags)


if __name__ == "__main__":
    unittest.main()
