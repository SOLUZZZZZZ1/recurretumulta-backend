from __future__ import annotations

from datetime import date
import unittest

from rtm_core.air_passenger_regime import (
    AIR_PASSENGER_REGIME_VERSION,
    CURRENT_RULESET_CODE,
    CURRENT_RULESET_SAFE_THROUGH,
    REFORM_ADOPTED_ON,
    REFORM_ENTRY_INTO_FORCE_DATE,
    REFORM_PUBLICATION_DATE,
    resolve_air_passenger_regime,
)


class AirPassengerRegimeTest(unittest.TestCase):
    def test_version_and_transition_constants_are_explicit(self):
        self.assertEqual(
            AIR_PASSENGER_REGIME_VERSION,
            "rtm_air_passenger_regime_v1_0",
        )
        self.assertEqual(REFORM_ADOPTED_ON, date(2026, 7, 13))
        self.assertIsNone(REFORM_PUBLICATION_DATE)
        self.assertIsNone(REFORM_ENTRY_INTO_FORCE_DATE)
        self.assertEqual(CURRENT_RULESET_SAFE_THROUGH, date(2027, 7, 31))

    def test_current_regulation_is_selected_inside_safe_horizon(self):
        decision = resolve_air_passenger_regime("2026-08-05")
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.ruleset, CURRENT_RULESET_CODE)
        self.assertTrue(decision.legal_basis)
        self.assertIsNone(decision.blocking_reason)

    def test_future_date_requires_update_instead_of_guessing_reform(self):
        decision = resolve_air_passenger_regime("2027-08-01")
        self.assertEqual(decision.status, "operator_review")
        self.assertIsNone(decision.ruleset)
        self.assertIn("horizonte temporal", decision.blocking_reason.lower())

    def test_missing_or_invalid_date_never_selects_a_ruleset(self):
        for value in (None, "", "fecha ilegible"):
            with self.subTest(value=value):
                decision = resolve_air_passenger_regime(value)
                self.assertEqual(decision.status, "operator_review")
                self.assertIsNone(decision.ruleset)
                self.assertTrue(decision.blocking_reason)


if __name__ == "__main__":
    unittest.main()
