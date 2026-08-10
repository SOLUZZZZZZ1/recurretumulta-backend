from __future__ import annotations

from datetime import date
import unittest

from rtm_core.air_baggage_liability_regime import (
    AIR_BAGGAGE_LIABILITY_REGIME_VERSION,
    CURRENT_BAGGAGE_LIMIT_SDR,
    CURRENT_LIMIT_EFFECTIVE_ON,
    CURRENT_RULESET_SAFE_THROUGH,
    PREVIOUS_BAGGAGE_LIMIT_SDR,
    PREVIOUS_LIMIT_EFFECTIVE_ON,
    resolve_air_baggage_liability_regime,
)


class AirBaggageLiabilityRegimeTest(unittest.TestCase):
    def test_versions_and_temporal_constants_are_explicit(self):
        self.assertEqual(
            AIR_BAGGAGE_LIABILITY_REGIME_VERSION,
            "rtm_air_baggage_liability_regime_v1_0",
        )
        self.assertEqual(CURRENT_LIMIT_EFFECTIVE_ON, date(2024, 12, 28))
        self.assertEqual(PREVIOUS_LIMIT_EFFECTIVE_ON, date(2019, 12, 28))
        self.assertEqual(CURRENT_RULESET_SAFE_THROUGH, date(2027, 7, 31))
        self.assertEqual(CURRENT_BAGGAGE_LIMIT_SDR, 1519)
        self.assertEqual(PREVIOUS_BAGGAGE_LIMIT_SDR, 1288)

    def test_current_limit_is_selected_without_euro_conversion(self):
        decision = resolve_air_baggage_liability_regime("2026-08-08")
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.liability_limit_sdr, 1519)
        self.assertTrue(decision.legal_basis)
        self.assertTrue(any("no una indemnización automática" in item for item in decision.warnings))
        self.assertFalse(any("€" in item for item in decision.warnings))

    def test_previous_limit_is_preserved_for_2023_incident(self):
        decision = resolve_air_baggage_liability_regime("2023-06-15")
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.liability_limit_sdr, 1288)
        self.assertEqual(decision.limit_effective_on, date(2019, 12, 28))

    def test_future_or_unversioned_date_requires_operator_review(self):
        for value in (None, "", "2018-01-01", "2027-08-01"):
            with self.subTest(value=value):
                decision = resolve_air_baggage_liability_regime(value)
                self.assertEqual(decision.status, "operator_review")
                self.assertIsNone(decision.liability_limit_sdr)
                self.assertTrue(decision.blocking_reason)


if __name__ == "__main__":
    unittest.main()
