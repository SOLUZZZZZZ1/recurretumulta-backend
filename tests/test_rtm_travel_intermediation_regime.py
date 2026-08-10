from __future__ import annotations

import unittest

from rtm_core.travel_intermediation_regime import (
    CURRENT_RULESET_EFFECTIVE_ON,
    CURRENT_RULESET_SAFE_THROUGH,
    DSA_FULL_APPLICATION_ON,
    TRAVEL_INTERMEDIATION_REGIME_VERSION,
    resolve_travel_intermediation_regime,
)


class TravelIntermediationRegimeTest(unittest.TestCase):
    def test_version_and_temporal_horizons_are_explicit(self):
        self.assertEqual(
            TRAVEL_INTERMEDIATION_REGIME_VERSION,
            "rtm_travel_intermediation_regime_v1_0",
        )
        self.assertEqual(CURRENT_RULESET_EFFECTIVE_ON.isoformat(), "2014-06-13")
        self.assertEqual(DSA_FULL_APPLICATION_ON.isoformat(), "2024-02-17")
        self.assertEqual(CURRENT_RULESET_SAFE_THROUGH.isoformat(), "2027-07-31")

    def test_spanish_online_marketplace_receives_ecommerce_and_dsa_layers(self):
        decision = resolve_travel_intermediation_regime(
            booking_date="2026-05-10",
            platform_country="España",
            electronic_contract=True,
            marketplace_status=True,
        )
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.scope, "spain")
        self.assertTrue(decision.dsa_marketplace_layer)
        rendered = " ".join(decision.legal_basis)
        self.assertIn("Ley 34/2002", rendered)
        self.assertIn("Reglamento (UE) 2022/2065", rendered)
        self.assertIn("Real Decreto Legislativo 1/2007", rendered)

    def test_pre_dsa_booking_does_not_receive_future_marketplace_layer(self):
        decision = resolve_travel_intermediation_regime(
            booking_date="2023-08-20",
            platform_country="España",
            electronic_contract=True,
            marketplace_status=True,
        )
        self.assertEqual(decision.status, "current")
        self.assertIsNone(decision.dsa_marketplace_layer)
        self.assertFalse(
            any("2022/2065" in item for item in decision.legal_basis)
        )

    def test_unknown_marketplace_status_never_invents_dsa_applicability(self):
        decision = resolve_travel_intermediation_regime(
            booking_date="2026-05-10",
            platform_country="España",
            electronic_contract=True,
            marketplace_status=None,
        )
        self.assertEqual(decision.status, "current")
        self.assertIsNone(decision.dsa_marketplace_layer)
        self.assertFalse(
            any("2022/2065" in item for item in decision.legal_basis)
        )
        self.assertTrue(
            any("No está documentado" in warning for warning in decision.warnings)
        )

    def test_offline_agency_contract_keeps_general_consumer_basis_only(self):
        decision = resolve_travel_intermediation_regime(
            booking_date="2026-05-10",
            platform_country="España",
            electronic_contract=False,
            marketplace_status=False,
        )
        self.assertEqual(decision.status, "current")
        rendered = " ".join(decision.legal_basis)
        self.assertIn("Real Decreto Legislativo 1/2007", rendered)
        self.assertNotIn("Ley 34/2002", rendered)
        self.assertNotIn("Reglamento (UE) 2022/2065", rendered)

    def test_cross_border_case_keeps_national_law_review(self):
        decision = resolve_travel_intermediation_regime(
            booking_date="2026-05-10",
            platform_country="Francia",
            electronic_contract=True,
            marketplace_status=True,
        )
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.scope, "eu_eea_cross_border")
        self.assertTrue(
            any("transposición nacional" in warning for warning in decision.warnings)
        )

    def test_missing_old_future_and_third_country_cases_fail_closed(self):
        scenarios = (
            {
                "booking_date": None,
                "platform_country": "España",
                "electronic_contract": True,
                "marketplace_status": True,
            },
            {
                "booking_date": "2010-01-01",
                "platform_country": "España",
                "electronic_contract": True,
                "marketplace_status": True,
            },
            {
                "booking_date": "2028-01-01",
                "platform_country": "España",
                "electronic_contract": True,
                "marketplace_status": True,
            },
            {
                "booking_date": "2026-05-10",
                "platform_country": "Estados Unidos",
                "electronic_contract": True,
                "marketplace_status": True,
            },
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                decision = resolve_travel_intermediation_regime(**scenario)
                self.assertEqual(decision.status, "operator_review")
                self.assertFalse(decision.legal_basis)
                self.assertTrue(decision.blocking_reason)


if __name__ == "__main__":
    unittest.main()
