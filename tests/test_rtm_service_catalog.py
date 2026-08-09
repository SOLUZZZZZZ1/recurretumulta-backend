import unittest

from rtm_core.service_catalog import (
    SERVICE_CATALOG_VERSION,
    canonical_department,
    resolve_review_quote,
)


class ServiceCatalogTest(unittest.TestCase):
    def test_version_is_explicit(self):
        self.assertEqual(SERVICE_CATALOG_VERSION, "rtm_service_catalog_v1_1")

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
        for department in ("traffic", "debt", "travel", "claims", "other"):
            with self.subTest(department=department):
                quote = resolve_review_quote(department, "anything")
                self.assertEqual(quote.billing_code, "REVIEW_BASIC")
                self.assertEqual(quote.amount_cents, 1000)

    def test_travel_is_its_own_satellite(self):
        self.assertEqual(canonical_department("", "flight"), "travel")
        self.assertEqual(canonical_department("Viajes"), "travel")
        quote = resolve_review_quote("travel", "flight_cancelled")
        self.assertEqual(quote.department, "travel")
        self.assertEqual(quote.amount_cents, 1000)

    def test_explicit_department_has_priority_over_case_type(self):
        self.assertEqual(canonical_department("traffic", "aeat"), "traffic")

    def test_accents_and_aliases_are_normalized(self):
        self.assertEqual(canonical_department("Administración"), "administration")
        self.assertEqual(canonical_department("", "Reclamación"), "claims")
        self.assertEqual(canonical_department("", "Aerolínea"), "travel")


if __name__ == "__main__":
    unittest.main()
