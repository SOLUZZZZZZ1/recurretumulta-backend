from __future__ import annotations

import unittest

from rtm_core.operator_auth_crypto import (
    ARGON2_MEMORY_COST_KIB,
    ARGON2_PARALLELISM,
    ARGON2_TIME_COST,
    PasswordPolicyError,
    generate_device_secret,
    generate_session_token,
    hash_device_secret,
    hash_operator_password,
    hash_session_token,
    hmac_identifier,
    normalize_operator_email,
    token_digest_matches,
    verify_operator_password,
)


class OperatorAuthCryptoTest(unittest.TestCase):
    def test_argon2id_hash_and_verification(self):
        password = "A long synthetic operator passphrase!"
        encoded = hash_operator_password(password)
        self.assertTrue(encoded.startswith("$argon2id$"))
        result = verify_operator_password(encoded, password)
        self.assertTrue(result.valid)
        self.assertFalse(result.needs_rehash)

    def test_wrong_password_and_invalid_hash_are_rejected(self):
        encoded = hash_operator_password("Another synthetic passphrase!")
        self.assertFalse(verify_operator_password(encoded, "wrong value here").valid)
        self.assertFalse(verify_operator_password("not-a-valid-hash", "anything").valid)

    def test_password_policy_rejects_short_or_blank_values(self):
        with self.assertRaises(PasswordPolicyError):
            hash_operator_password("short")
        with self.assertRaises(PasswordPolicyError):
            hash_operator_password(" " * 20)

    def test_argon2_profile_meets_declared_baseline(self):
        self.assertGreaterEqual(ARGON2_MEMORY_COST_KIB, 19_456)
        self.assertGreaterEqual(ARGON2_TIME_COST, 2)
        self.assertEqual(ARGON2_PARALLELISM, 1)

    def test_session_token_is_only_compared_by_digest(self):
        token = generate_session_token()
        digest = hash_session_token(token)
        self.assertEqual(len(digest), 64)
        self.assertNotEqual(token, digest)
        self.assertTrue(token_digest_matches(token, digest))
        self.assertFalse(token_digest_matches(token + "x", digest))

    def test_device_secret_is_opaque_and_hashed(self):
        secret = generate_device_secret()
        digest = hash_device_secret(secret)
        self.assertEqual(len(digest), 64)
        self.assertNotEqual(secret, digest)

    def test_identifier_hmac_is_stable_and_secret_dependent(self):
        secret_a = "a" * 32
        secret_b = "b" * 32
        first = hmac_identifier("Operator@Example.com", secret_a)
        second = hmac_identifier("operator@example.com", secret_a)
        other = hmac_identifier("operator@example.com", secret_b)
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_email_normalization(self):
        self.assertEqual(
            normalize_operator_email("  Operator@Example.COM "),
            "operator@example.com",
        )
        with self.assertRaises(ValueError):
            normalize_operator_email("not-an-email")


if __name__ == "__main__":
    unittest.main()
