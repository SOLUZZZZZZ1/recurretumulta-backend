from __future__ import annotations

import unittest

from rtm_core.operator_provisioning import (
    DEFAULT_SYNTHETIC_EMAIL,
    ROLE_DEFINITIONS,
    generate_temporary_password,
    normalize_synthetic_operator_email,
    role_definition,
)


class OperatorProvisioningTest(unittest.TestCase):
    def test_default_email_is_unambiguously_synthetic(self):
        self.assertEqual(
            normalize_synthetic_operator_email(DEFAULT_SYNTHETIC_EMAIL),
            DEFAULT_SYNTHETIC_EMAIL,
        )

    def test_real_or_unmarked_email_is_rejected(self):
        for value in (
            "ramon@recurretumulta.eu",
            "supervisor@example.com",
            "rtm-staging-supervisor@gmail.com",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_synthetic_operator_email(value)

    def test_minimum_roles_are_explicit_and_small(self):
        self.assertEqual(set(ROLE_DEFINITIONS), {"operator", "supervisor"})
        self.assertEqual(
            ROLE_DEFINITIONS["operator"].permissions,
            ("ops.view",),
        )
        self.assertEqual(
            ROLE_DEFINITIONS["supervisor"].permissions,
            ("ops.view", "ops.supervise"),
        )

    def test_role_lookup_fails_closed(self):
        self.assertEqual(role_definition("supervisor").code, "rtm.supervisor")
        with self.assertRaises(ValueError):
            role_definition("administrator")

    def test_generated_password_meets_minimum_and_is_not_constant(self):
        first = generate_temporary_password()
        second = generate_temporary_password()
        self.assertGreaterEqual(len(first), 12)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("RTM-"))


if __name__ == "__main__":
    unittest.main()
