from __future__ import annotations

import dataclasses
import pickle
import unittest

from rtm_connect.secret_resolver import (
    EnvironmentSecretResolver,
    SecretResolutionError,
)


REF = "env://RTM_CONNECT_C6_SANDBOX_TOKEN"
VALUE = "c6-unit-test-secret-value"


class ConnectC6SecretResolverTest(unittest.TestCase):
    def test_exact_allowlisted_reference_resolves(self):
        resolver = EnvironmentSecretResolver(
            {"RTM_CONNECT_C6_SANDBOX_TOKEN": VALUE},
            allowed_references=(REF,),
        )
        secret = resolver.resolve(REF)
        self.assertEqual(secret.reveal_for_transport(), VALUE)

    def test_repr_and_str_are_redacted(self):
        resolver = EnvironmentSecretResolver(
            {"RTM_CONNECT_C6_SANDBOX_TOKEN": VALUE},
            allowed_references=(REF,),
        )
        secret = resolver.resolve(REF)
        self.assertNotIn(VALUE, repr(secret))
        self.assertNotIn(VALUE, str(secret))
        self.assertIn("redacted", repr(secret))
        with self.assertRaises(TypeError):
            vars(secret)
        with self.assertRaises(TypeError):
            dataclasses.asdict(secret)
        with self.assertRaises(TypeError):
            pickle.dumps(secret)

    def test_arbitrary_reference_is_blocked(self):
        resolver = EnvironmentSecretResolver(
            {"OTHER": VALUE},
            allowed_references=(REF,),
        )
        with self.assertRaises(SecretResolutionError):
            resolver.resolve("env://OTHER")

    def test_missing_and_weak_secret_are_blocked(self):
        missing = EnvironmentSecretResolver({}, allowed_references=(REF,))
        with self.assertRaises(SecretResolutionError):
            missing.resolve(REF)
        weak = EnvironmentSecretResolver(
            {"RTM_CONNECT_C6_SANDBOX_TOKEN": "short"},
            allowed_references=(REF,),
        )
        with self.assertRaises(SecretResolutionError):
            weak.resolve(REF)
        control = EnvironmentSecretResolver(
            {"RTM_CONNECT_C6_SANDBOX_TOKEN": "valid-secret-value\r\nX: injected"},
            allowed_references=(REF,),
        )
        with self.assertRaises(SecretResolutionError):
            control.resolve(REF)
        unicode_value = EnvironmentSecretResolver(
            {"RTM_CONNECT_C6_SANDBOX_TOKEN": "valid-secret-value-🔒"},
            allowed_references=(REF,),
        )
        with self.assertRaises(SecretResolutionError):
            unicode_value.resolve(REF)

    def test_resolver_copies_only_allowlisted_environment_values(self):
        resolver = EnvironmentSecretResolver(
            {
                "RTM_CONNECT_C6_SANDBOX_TOKEN": VALUE,
                "UNRELATED_PRODUCTION_SECRET": "must-not-be-retained",
            },
            allowed_references=(REF,),
        )
        self.assertEqual(
            set(resolver._values),
            {"RTM_CONNECT_C6_SANDBOX_TOKEN"},
        )
        resolver.assert_runtime_sealed(expected_reference=REF)
        with self.assertRaises(TypeError):
            vars(resolver)
        with self.assertRaises(AttributeError):
            resolver.resolve = lambda _reference: None
        with self.assertRaises(TypeError):
            resolver._values["OTHER"] = VALUE

    def test_invalid_allowlist_is_blocked(self):
        with self.assertRaises(SecretResolutionError):
            EnvironmentSecretResolver(
                {"TOKEN": VALUE},
                allowed_references=("file:///tmp/token",),
            )


if __name__ == "__main__":
    unittest.main()
