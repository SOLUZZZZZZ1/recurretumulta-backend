from __future__ import annotations

import unittest

from rtm_core.travel_insurance_regime import (
    CUSTOMER_SERVICE_REFORM_EFFECTIVE_ON,
    CURRENT_RULESET_SAFE_THROUGH,
    INSURANCE_CONTRACT_ACT_EFFECTIVE_ON,
    TRAVEL_INSURANCE_REGIME_VERSION,
    resolve_travel_insurance_regime,
)


class TravelInsuranceRegimeTest(unittest.TestCase):
    def _resolve(self, **updates):
        payload = {
            "policy_date": "2026-06-01",
            "coverage_start": "2026-08-01",
            "coverage_end": "2026-08-31",
            "loss_date": "2026-08-10",
            "insurer_country": "España",
            "coverage_nature": "Seguro de personas",
            "policy_coverages": "Asistencia médica y repatriación",
            "sac_complaint_date": "2026-08-20",
            "insurance_added_to_booking": False,
            "insurance_distributor": None,
        }
        payload.update(updates)
        return resolve_travel_insurance_regime(**payload)

    def test_versions_and_temporal_horizon_are_explicit(self):
        self.assertEqual(
            TRAVEL_INSURANCE_REGIME_VERSION,
            "rtm_travel_insurance_regime_v1_0",
        )
        self.assertEqual(
            INSURANCE_CONTRACT_ACT_EFFECTIVE_ON.isoformat(),
            "1981-04-17",
        )
        self.assertEqual(
            CUSTOMER_SERVICE_REFORM_EFFECTIVE_ON.isoformat(),
            "2025-12-28",
        )
        self.assertEqual(CURRENT_RULESET_SAFE_THROUGH.isoformat(), "2027-12-31")

    def test_spanish_persons_cover_selects_current_ruleset(self):
        decision = self._resolve()
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.scope, "spain")
        self.assertEqual(decision.coverage_nature, "persons")
        self.assertEqual(decision.limitation_years, 5)
        self.assertEqual(decision.customer_service_wait_months, 1)
        self.assertEqual(decision.financial_complaint_resolution_days, 90)
        self.assertTrue(decision.legal_basis)

    def test_mixed_coverages_do_not_guess_one_limitation_period(self):
        decision = self._resolve(
            coverage_nature="Mixta",
            policy_coverages="Cancelación, asistencia médica y equipaje",
        )
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.coverage_nature, "mixed")
        self.assertIsNone(decision.limitation_years)
        self.assertTrue(
            any("único plazo" in warning for warning in decision.warnings)
        )

    def test_added_insurance_activates_distribution_layer(self):
        decision = self._resolve(
            insurance_added_to_booking=True,
            insurance_distributor="Agencia Demo",
        )
        self.assertEqual(decision.status, "current")
        self.assertTrue(decision.distribution_layer)
        self.assertTrue(
            any("Real Decreto-ley 3/2020" in basis for basis in decision.legal_basis)
        )

    def test_missing_dates_foreign_scope_and_future_fail_closed(self):
        missing = self._resolve(coverage_start=None)
        self.assertEqual(missing.status, "operator_review")
        self.assertFalse(missing.legal_basis)

        foreign = self._resolve(insurer_country="Estados Unidos")
        self.assertEqual(foreign.status, "operator_review")
        self.assertFalse(foreign.legal_basis)

        future = self._resolve(
            policy_date="2028-01-01",
            coverage_start="2028-02-01",
            coverage_end="2028-02-28",
            loss_date="2028-02-10",
            sac_complaint_date="2028-02-20",
        )
        self.assertEqual(future.status, "operator_review")
        self.assertFalse(future.legal_basis)

    def test_impossible_chronology_is_blocked(self):
        after_loss = self._resolve(policy_date="2026-08-15")
        self.assertEqual(after_loss.status, "operator_review")
        self.assertIn("después del siniestro", after_loss.blocking_reason)

        outside = self._resolve(loss_date="2026-09-01")
        self.assertEqual(outside.status, "operator_review")
        self.assertIn("fuera del período", outside.blocking_reason)


if __name__ == "__main__":
    unittest.main()
