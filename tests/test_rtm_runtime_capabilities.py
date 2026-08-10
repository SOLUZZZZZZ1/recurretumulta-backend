from __future__ import annotations

import json
import unittest

from fastapi import HTTPException

from rtm_core.runtime_capabilities import (
    RUNTIME_CAPABILITIES_VERSION,
    CapabilityDisabledError,
    capability_snapshot,
    capability_state,
    canonical_capability,
    require_capability,
    require_http_capability,
)


class RuntimeCapabilitiesTest(unittest.TestCase):
    def test_version_aliases_and_legacy_compatibility_are_explicit(self):
        self.assertEqual(
            RUNTIME_CAPABILITIES_VERSION,
            "rtm_runtime_capabilities_v1_0",
        )
        self.assertEqual(canonical_capability("payments"), "stripe")
        self.assertEqual(canonical_capability("mail"), "outbound_email")

        state = capability_state("stripe", {})
        self.assertTrue(state.enabled)
        self.assertFalse(state.enforced)
        self.assertEqual(state.reason, "legacy_unconfigured_compatibility")

    def test_staging_and_production_require_explicit_true(self):
        for environment in ("staging", "production"):
            with self.subTest(environment=environment):
                missing = capability_state("b2", {"RTM_ENV": environment})
                self.assertTrue(missing.enforced)
                self.assertFalse(missing.enabled)
                self.assertEqual(missing.reason, "required_flag_missing")

                enabled = capability_state(
                    "b2",
                    {
                        "RTM_ENV": environment,
                        "RTM_ENABLE_B2": "1",
                    },
                )
                self.assertTrue(enabled.enabled)
                self.assertEqual(enabled.reason, "explicitly_enabled")

    def test_explicit_false_blocks_even_in_legacy_mode(self):
        environment = {"RTM_ENABLE_OUTBOUND_EMAIL": "0"}
        state = capability_state("outbound_email", environment)
        self.assertFalse(state.enforced)
        self.assertFalse(state.enabled)
        with self.assertRaises(CapabilityDisabledError):
            require_capability("outbound_email", environment)

    def test_invalid_boolean_fails_closed(self):
        state = capability_state(
            "document_provider",
            {
                "RTM_ENV": "staging",
                "RTM_ENABLE_DOCUMENT_PROVIDER": "perhaps",
            },
        )
        self.assertFalse(state.valid)
        self.assertFalse(state.enabled)
        self.assertEqual(state.reason, "invalid_boolean_flag")

    def test_force_flag_can_enable_enforcement_in_tests(self):
        state = capability_state(
            "external_submission",
            {"RTM_ENFORCE_CAPABILITY_FLAGS": "1"},
        )
        self.assertTrue(state.enforced)
        self.assertFalse(state.enabled)

    def test_http_guard_returns_safe_503_without_secret_values(self):
        environment = {
            "RTM_ENV": "staging",
            "RTM_ENABLE_STRIPE": "0",
            "STRIPE_SECRET_KEY": "sk_test_super_private_value",
        }
        with self.assertRaises(HTTPException) as context:
            require_http_capability("stripe", environment)
        self.assertEqual(context.exception.status_code, 503)
        rendered = json.dumps(context.exception.detail, ensure_ascii=False)
        self.assertIn("RTM_ENABLE_STRIPE", rendered)
        self.assertNotIn(environment["STRIPE_SECRET_KEY"], rendered)

    def test_snapshot_contains_only_registered_safe_states(self):
        environment = {
            "RTM_ENV": "staging",
            "RTM_ENABLE_B2": "1",
            "B2_APPLICATION_KEY": "private-storage-secret",
        }
        snapshot = capability_snapshot(environment)
        self.assertEqual(
            set(snapshot),
            {
                "b2",
                "document_provider",
                "external_submission",
                "final_payments",
                "outbound_email",
                "stripe",
            },
        )
        rendered = json.dumps(
            {key: value.model_dump(mode="json") for key, value in snapshot.items()},
            ensure_ascii=False,
        )
        self.assertNotIn(environment["B2_APPLICATION_KEY"], rendered)

    def test_unknown_capability_is_rejected(self):
        with self.assertRaises(ValueError):
            canonical_capability("unknown-provider")


if __name__ == "__main__":
    unittest.main()
