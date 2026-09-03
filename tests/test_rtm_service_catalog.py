import unittest

from rtm_core.service_catalog import (
    SERVICE_CATALOG_VERSION,
    canonical_department,
    resolve_review_quote,
    validate_public_intake_classification,
)


class ServiceCatalogTest(unittest.TestCase):
    def test_version_is_explicit(self):
        self.assertEqual(SERVICE_CATALOG_VERSION, "rtm_service_catalog_v1_2")

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

    def test_administration_marker_cannot_be_downgraded_by_other_department(self):
        self.assertEqual(canonical_department("traffic", "aeat"), "administration")
        self.assertEqual(resolve_review_quote("other", "aeat").amount_cents, 2500)

    def test_public_intake_accepts_only_coherent_server_catalog_pairs(self):
        self.assertEqual(
            validate_public_intake_classification(
                "administration", "aeat", "administracion"
            ),
            ("administration", "aeat"),
        )
        for values in (
            ("other", "aeat", ""),
            ("administration", "consumer", ""),
            ("claims", "consumer", "administracion"),
            ("claims", "consumer", "vivienda"),
            ("traffic", "unknown", "trafico"),
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_public_intake_classification(*values)

    def test_accents_and_aliases_are_normalized(self):
        self.assertEqual(canonical_department("Administración"), "administration")
        self.assertEqual(canonical_department("", "Reclamación"), "claims")
        self.assertEqual(canonical_department("", "Aerolínea"), "travel")


if __name__ == "__main__":
    unittest.main()
