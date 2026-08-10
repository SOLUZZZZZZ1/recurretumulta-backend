from __future__ import annotations

import unittest

from rtm_core.travel_agency_regime import (
    CURRENT_RULESET_SAFE_THROUGH,
    DSA_GENERAL_APPLICATION_ON,
    INDEPENDENT_INTERMEDIATION_EFFECTIVE_ON,
    LINKED_TRAVEL_ARRANGEMENT_EFFECTIVE_ON,
    TRAVEL_AGENCY_REGIME_VERSION,
    resolve_travel_agency_regime,
)


class TravelAgencyRegimeTest(unittest.TestCase):
    def test_versions_and_temporal_boundaries_are_explicit(self):
        self.assertEqual(
            TRAVEL_AGENCY_REGIME_VERSION,
            "rtm_travel_agency_regime_v1_0",
        )
        self.assertEqual(
            INDEPENDENT_INTERMEDIATION_EFFECTIVE_ON.isoformat(),
            "2022-05-28",
        )
        self.assertEqual(
            LINKED_TRAVEL_ARRANGEMENT_EFFECTIVE_ON.isoformat(),
            "2018-12-28",
        )
        self.assertEqual(DSA_GENERAL_APPLICATION_ON.isoformat(), "2024-02-17")
        self.assertEqual(CURRENT_RULESET_SAFE_THROUGH.isoformat(), "2027-12-31")

    def test_spanish_marketplace_intermediary_selects_current_baseline(self):
        decision = resolve_travel_agency_regime(
            booking_date="2026-06-10",
            platform_country="España",
            platform_name="Plataforma Demo",
            role_value="Intermediaria y mercado en línea",
            online_marketplace=True,
            package_status=False,
            linked_arrangement=False,
            contracting_party="Proveedor Demo",
            underlying_supplier="Proveedor Demo",
            payment_collector="Plataforma Demo",
            invoice_issuer="Plataforma Demo",
        )

        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.boundary, "independent_intermediation")
        self.assertEqual(decision.role, "intermediary")
        self.assertTrue(decision.role_confirmed)
        self.assertTrue(decision.marketplace_information_regime_applies)
        self.assertTrue(decision.dsa_marketplace_duties_apply)
        self.assertTrue(decision.payment_collector_matches_platform)
        self.assertTrue(decision.invoice_issuer_matches_platform)
        rendered = " ".join(decision.legal_basis)
        self.assertIn("artículo 97 bis", rendered)
        self.assertIn("Reglamento (UE) 2022/2065", rendered)

    def test_payment_or_invoice_alone_does_not_make_platform_supplier(self):
        decision = resolve_travel_agency_regime(
            booking_date="2026-06-10",
            platform_country="España",
            platform_name="Plataforma Demo",
            role_value=None,
            online_marketplace=True,
            package_status=False,
            linked_arrangement=False,
            contracting_party="Proveedor Demo",
            underlying_supplier="Proveedor Demo",
            payment_collector="Plataforma Demo",
            invoice_issuer="Plataforma Demo",
        )

        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.role, "unknown")
        self.assertFalse(decision.role_confirmed)
        self.assertTrue(
            any("no convierten por sí solos" in item for item in decision.warnings)
        )

    def test_intermediary_label_conflicts_with_platform_contracting_party(self):
        decision = resolve_travel_agency_regime(
            booking_date="2026-06-10",
            platform_country="España",
            platform_name="Plataforma Demo",
            role_value="Intermediaria",
            online_marketplace=True,
            package_status=False,
            linked_arrangement=False,
            contracting_party="Plataforma Demo",
            underlying_supplier="Proveedor Demo",
            payment_collector="Plataforma Demo",
            invoice_issuer="Plataforma Demo",
        )

        self.assertEqual(decision.status, "operator_review")
        self.assertEqual(decision.role, "mixed")
        self.assertIn("papeles incompatibles", decision.blocking_reason)

    def test_package_travel_is_routed_away_from_agency_specialist(self):
        decision = resolve_travel_agency_regime(
            booking_date="2026-06-10",
            platform_country="España",
            platform_name="Agencia Demo",
            role_value="Organizadora",
            online_marketplace=False,
            package_status=True,
            linked_arrangement=False,
            contracting_party="Agencia Demo",
            underlying_supplier="Agencia Demo",
            payment_collector="Agencia Demo",
            invoice_issuer="Agencia Demo",
        )

        self.assertEqual(decision.status, "operator_review")
        self.assertEqual(decision.boundary, "package_travel")
        self.assertFalse(decision.legal_basis)
        self.assertIn("travel.package", decision.blocking_reason)

    def test_linked_travel_arrangement_uses_its_separate_baseline(self):
        decision = resolve_travel_agency_regime(
            booking_date="2026-06-10",
            platform_country="España",
            platform_name="Plataforma Demo",
            role_value="Facilitador de servicio de viaje vinculado",
            online_marketplace=True,
            package_status=False,
            linked_arrangement=True,
            contracting_party="Proveedor de vuelo",
            underlying_supplier="Proveedor de vuelo",
            payment_collector="Proveedor de vuelo",
            invoice_issuer="Proveedor de vuelo",
        )

        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.boundary, "linked_travel_arrangement")
        self.assertEqual(decision.role, "linked_arrangement_facilitator")
        self.assertIn("artículos 151.1.e)", " ".join(decision.legal_basis))

    def test_missing_country_future_and_third_country_fail_closed(self):
        missing_country = resolve_travel_agency_regime(
            booking_date="2026-06-10",
            platform_country=None,
            platform_name="Plataforma Demo",
            role_value="Intermediaria",
            online_marketplace=True,
            package_status=False,
            linked_arrangement=False,
            contracting_party="Proveedor",
            underlying_supplier="Proveedor",
            payment_collector="Plataforma Demo",
            invoice_issuer="Plataforma Demo",
        )
        self.assertEqual(missing_country.status, "operator_review")
        self.assertFalse(missing_country.legal_basis)

        future = resolve_travel_agency_regime(
            booking_date="2028-01-01",
            platform_country="España",
            platform_name="Plataforma Demo",
            role_value="Intermediaria",
            online_marketplace=True,
            package_status=False,
            linked_arrangement=False,
            contracting_party="Proveedor",
            underlying_supplier="Proveedor",
            payment_collector="Plataforma Demo",
            invoice_issuer="Plataforma Demo",
        )
        self.assertEqual(future.status, "operator_review")
        self.assertFalse(future.legal_basis)

        third_country = resolve_travel_agency_regime(
            booking_date="2026-06-10",
            platform_country="Estados Unidos",
            platform_name="Plataforma Demo",
            role_value="Intermediaria",
            online_marketplace=True,
            package_status=False,
            linked_arrangement=False,
            contracting_party="Proveedor",
            underlying_supplier="Proveedor",
            payment_collector="Plataforma Demo",
            invoice_issuer="Plataforma Demo",
        )
        self.assertEqual(third_country.status, "operator_review")
        self.assertFalse(third_country.legal_basis)


if __name__ == "__main__":
    unittest.main()
