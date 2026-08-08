import unittest

from rtm_core.service_catalog import canonical_department, resolve_review_quote


class ServiceCatalogTest(unittest.TestCase):
    def test_administration_is_25_euros_from_department(self):
        quote = resolve_review_quote("administration", "aeat")
        self.assertEqual(quote.billing_code, "ADMIN_REVIEW")
        self.assertEqual(quote.amount_cents, 2500)
        self.assertEqual(quote.stripe_price_env, "STRIPE_PRICE_ID_ADMIN")

    def test_administration_can_be_inferred_from_case_type(self):
        quote = resolve_review_quote("", "social_security")
        self.assertEqual(quote.department, "administration")
        self.assertEqual(quote.amount_cents, 2500)

    def test_other_services_are_10_euros(self):
        for department in ("traffic", "debt", "claims", "other"):
            with self.subTest(department=department):
                quote = resolve_review_quote(department, "anything")
                self.assertEqual(quote.billing_code, "REVIEW_BASIC")
                self.assertEqual(quote.amount_cents, 1000)

    def test_explicit_department_has_priority_over_case_type(self):
        self.assertEqual(canonical_department("traffic", "aeat"), "traffic")

    def test_accents_and_aliases_are_normalized(self):
        self.assertEqual(canonical_department("Administración"), "administration")
        self.assertEqual(canonical_department("", "Reclamación"), "claims")


if __name__ == "__main__":
    unittest.main()
