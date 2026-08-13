from __future__ import annotations

import unittest

from rtm_core.debt_unpaid_rent_regime import (
    CURRENT_RULESET_SAFE_THROUGH,
    DEBT_UNPAID_RENT_REGIME_VERSION,
    MASC_GENERAL_EFFECTIVE_ON,
    RDL_2_2026_EFFECTIVE_ON,
    RDL_2_2026_REPEALED_ON,
    resolve_debt_unpaid_rent_regime,
)


class DebtUnpaidRentRegimeTest(unittest.TestCase):
    def _current(self, **overrides):
        values = {
            "evaluation_date": "2026-08-11",
            "contract_date": "2024-01-01",
            "lease_start_date": "2024-01-01",
            "first_unpaid_date": "2026-05-01",
            "last_unpaid_date": "2026-07-01",
            "prior_demand_date": "2026-06-01",
            "prior_demand_received_date": "2026-06-02",
            "masc_request_date": "2026-06-02",
            "masc_received_date": "2026-06-02",
            "court_filing_date": "2026-07-05",
            "property_country": "España",
            "property_use": "Vivienda habitual",
            "room_lease": False,
            "seasonal_lease": False,
            "tourist_lease": False,
            "rural_lease": False,
            "public_social_lease": False,
            "sublease": False,
            "habitual_dwelling": True,
            "claimant_role": "Arrendador",
            "landlord_claims": True,
            "tenant_defence": False,
            "assignment_documented": False,
            "insurer_subrogation": False,
            "possession_recovery_requested": True,
            "rent_claim_requested": True,
            "contract_termination_requested": True,
            "possession_returned": False,
            "payment_plan_requested": False,
            "judicial_action_intended": True,
            "execution_only": False,
            "masc_started": True,
            "masc_object_coincident": True,
            "masc_proof_documented": True,
            "prior_enervation": False,
            "payment_after_demand": False,
            "debt_paid": False,
        }
        values.update(overrides)
        return resolve_debt_unpaid_rent_regime(**values)

    def test_versions_and_temporal_boundaries_are_explicit(self):
        self.assertEqual(
            DEBT_UNPAID_RENT_REGIME_VERSION,
            "rtm_debt_unpaid_rent_regime_v1_0",
        )
        self.assertEqual(str(MASC_GENERAL_EFFECTIVE_ON), "2025-04-03")
        self.assertEqual(str(RDL_2_2026_EFFECTIVE_ON), "2026-02-05")
        self.assertEqual(str(RDL_2_2026_REPEALED_ON), "2026-02-28")
        self.assertEqual(str(CURRENT_RULESET_SAFE_THROUGH), "2027-12-31")

    def test_current_housing_claim_selects_masc_enervation_and_general_vulnerability(self):
        decision = self._current()
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.lease_kind, "urban_housing")
        self.assertEqual(decision.claim_type, "possession_and_rent")
        self.assertTrue(decision.masc_required)
        self.assertTrue(decision.masc_documented)
        self.assertTrue(decision.enervation_applicable)
        self.assertTrue(decision.enervation_preclusion_possible)
        self.assertTrue(decision.enervation_requires_operator_review)
        self.assertTrue(decision.general_vulnerability_review)
        self.assertFalse(decision.extraordinary_suspension_active)
        self.assertEqual(
            decision.extraordinary_suspension_state,
            "repealed_from_2026_02_28",
        )
        self.assertEqual(decision.rent_limitation_candidate_years, 5)
        self.assertTrue(any("Ley Orgánica 1/2025" in item for item in decision.legal_basis))
        self.assertTrue(any("enervación" in item.lower() for item in decision.warnings))

    def test_current_date_never_reactivates_extraordinary_suspension_to_december_2026(self):
        decision = self._current(court_filing_date=None)
        self.assertEqual(decision.status, "current")
        self.assertFalse(decision.extraordinary_suspension_active)
        rendered = " ".join(decision.warnings).lower()
        self.assertIn("no existe", rendered)
        self.assertIn("diciembre de 2026", rendered)

    def test_short_february_2026_window_is_temporally_versioned(self):
        decision = self._current(
            evaluation_date="2026-02-10",
            first_unpaid_date="2026-01-01",
            last_unpaid_date="2026-02-01",
            prior_demand_date="2026-01-01",
            prior_demand_received_date="2026-01-02",
            masc_request_date="2026-01-02",
            masc_received_date="2026-01-02",
            court_filing_date="2026-02-10",
        )
        self.assertEqual(decision.status, "current")
        self.assertTrue(decision.extraordinary_suspension_active)
        self.assertEqual(
            decision.extraordinary_suspension_state,
            "temporary_rdl_2_2026",
        )
        self.assertTrue(any("breve periodo" in item.lower() for item in decision.warnings))

    def test_pre_masc_filing_does_not_apply_future_procedural_requirement(self):
        decision = self._current(
            evaluation_date="2025-03-01",
            first_unpaid_date="2025-01-01",
            last_unpaid_date="2025-02-01",
            prior_demand_date="2025-02-01",
            prior_demand_received_date="2025-02-02",
            masc_request_date=None,
            masc_received_date=None,
            court_filing_date="2025-03-01",
            masc_started=False,
            masc_object_coincident=False,
            masc_proof_documented=False,
        )
        self.assertEqual(decision.status, "current")
        self.assertFalse(decision.masc_required)
        self.assertEqual(decision.masc_layer, "not_required")
        self.assertFalse(any("Ley Orgánica 1/2025" in item for item in decision.legal_basis))

    def test_room_tourist_and_tenant_defence_fail_closed(self):
        room = self._current(room_lease=True)
        self.assertEqual(room.status, "operator_review")
        self.assertEqual(room.lease_kind, "room")
        self.assertIn("habitación", room.blocking_reason.lower())

        tourist = self._current(tourist_lease=True)
        self.assertEqual(tourist.status, "operator_review")
        self.assertEqual(tourist.lease_kind, "tourist")

        defence = self._current(
            claimant_role="Arrendatario",
            landlord_claims=False,
            tenant_defence=True,
        )
        self.assertEqual(defence.status, "operator_review")
        self.assertEqual(defence.claim_type, "tenant_defence")
        self.assertIn("defensa", defence.blocking_reason.lower())

    def test_foreign_historic_future_and_bad_chronology_fail_closed(self):
        foreign = self._current(property_country="Francia")
        self.assertEqual(foreign.status, "operator_review")
        self.assertIn("no consta situado en españa", foreign.blocking_reason.lower())

        historic = self._current(contract_date="1990-01-01", lease_start_date="1990-01-01")
        self.assertEqual(historic.status, "operator_review")
        self.assertIn("legislación histórica", historic.blocking_reason.lower())

        future = self._current(evaluation_date="2028-01-01")
        self.assertEqual(future.status, "operator_review")
        self.assertIn("horizonte jurídico", future.blocking_reason.lower())

        chronology = self._current(
            first_unpaid_date="2026-08-01",
            last_unpaid_date="2026-07-01",
        )
        self.assertEqual(chronology.status, "operator_review")
        self.assertIn("último periodo", chronology.blocking_reason.lower())

    def test_masc_can_be_required_without_being_falsely_treated_as_documented(self):
        decision = self._current(
            masc_started=True,
            masc_object_coincident=None,
            masc_proof_documented=False,
        )
        self.assertEqual(decision.status, "current")
        self.assertTrue(decision.masc_required)
        self.assertFalse(decision.masc_documented)
        self.assertTrue(any("no consta" in item.lower() for item in decision.warnings))

    def test_possession_returned_converts_route_to_post_surrender_balance(self):
        decision = self._current(
            possession_returned=True,
            possession_return_date="2026-07-15",
        )
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.claim_type, "post_surrender_balance")
        self.assertTrue(any("eliminarse" in item.lower() for item in decision.warnings))


if __name__ == "__main__":
    unittest.main()
