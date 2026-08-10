from __future__ import annotations

import unittest

from rtm_core.accommodation_consumer_regime import (
    ACCOMMODATION_CONSUMER_REGIME_VERSION,
    CURRENT_RULESET_EFFECTIVE_ON,
    CURRENT_RULESET_SAFE_THROUGH,
    resolve_accommodation_consumer_regime,
)


class AccommodationConsumerRegimeTest(unittest.TestCase):
    def test_versions_and_horizon_are_explicit(self):
        self.assertEqual(
            ACCOMMODATION_CONSUMER_REGIME_VERSION,
            "rtm_accommodation_consumer_regime_v1_0",
        )
        self.assertLess(CURRENT_RULESET_EFFECTIVE_ON, CURRENT_RULESET_SAFE_THROUGH)

    def test_spanish_fixed_date_reservation_selects_current_baseline(self):
        decision = resolve_accommodation_consumer_regime(
            booking_date="2026-05-01",
            stay_start="2026-08-20",
            stay_end="2026-08-23",
            accommodation_country="España",
        )
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.scope, "spain")
        self.assertTrue(decision.fixed_date_withdrawal_exception)
        self.assertTrue(decision.legal_basis)
        self.assertTrue(
            any("103.l" in item for item in decision.legal_basis)
        )

    def test_eu_cross_border_keeps_local_law_review(self):
        decision = resolve_accommodation_consumer_regime(
            booking_date="2026-05-01",
            stay_start="2026-08-20",
            stay_end="2026-08-23",
            accommodation_country="Francia",
        )
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.scope, "eu_eea_cross_border")
        self.assertTrue(
            any("ley nacional" in warning for warning in decision.warnings)
        )

    def test_missing_stay_period_never_asserts_withdrawal_exception(self):
        decision = resolve_accommodation_consumer_regime(
            booking_date="2026-05-01",
            stay_start="2026-08-20",
            stay_end=None,
            accommodation_country="España",
        )
        self.assertEqual(decision.status, "operator_review")
        self.assertIsNone(decision.fixed_date_withdrawal_exception)
        self.assertFalse(decision.legal_basis)

    def test_future_and_third_country_cases_fail_closed(self):
        future = resolve_accommodation_consumer_regime(
            booking_date="2028-01-10",
            stay_start="2028-02-01",
            stay_end="2028-02-03",
            accommodation_country="España",
        )
        self.assertEqual(future.status, "operator_review")
        self.assertFalse(future.legal_basis)

        third_country = resolve_accommodation_consumer_regime(
            booking_date="2026-05-01",
            stay_start="2026-08-20",
            stay_end="2026-08-23",
            accommodation_country="Estados Unidos",
        )
        self.assertEqual(third_country.status, "operator_review")
        self.assertEqual(third_country.scope, "third_country")
        self.assertFalse(third_country.legal_basis)

    def test_impossible_chronology_is_blocked(self):
        decision = resolve_accommodation_consumer_regime(
            booking_date="2026-05-01",
            stay_start="2026-08-23",
            stay_end="2026-08-20",
            accommodation_country="España",
        )
        self.assertEqual(decision.status, "operator_review")
        self.assertIn("salida", decision.blocking_reason.lower())


if __name__ == "__main__":
    unittest.main()
