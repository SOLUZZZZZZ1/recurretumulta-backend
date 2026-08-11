from __future__ import annotations

import unittest

from rtm_core.claims_insurance_regime import (
    CLAIMS_INSURANCE_REGIME_VERSION,
    CURRENT_RULESET_SAFE_THROUGH,
    CUSTOMER_SERVICE_REFORM_EFFECTIVE_ON,
    INSURANCE_CONTRACT_ACT_EFFECTIVE_ON,
    resolve_claims_insurance_regime,
)


class ClaimsInsuranceRegimeTest(unittest.TestCase):
    def _resolve(self, **updates):
        payload = {
            "policy_date": "2025-12-20",
            "coverage_start": "2026-01-01",
            "coverage_end": "2026-12-31",
            "loss_date": "2026-07-10",
            "insurer_country": "España",
            "product_type": "Seguro de hogar",
            "coverage_nature": "Daños materiales",
            "policy_coverages": "Daños por agua, continente y contenido",
            "incident_type": "Denegación de cobertura",
            "issue_text": "La aseguradora rechazó el siniestro de hogar por una exclusión.",
            "sac_complaint_date": None,
            "insurance_distributor": None,
            "harmed_third_party": False,
            "travel_insurance": False,
            "motor_third_party_injury": False,
            "investment_linked": False,
            "pension_plan": False,
        }
        payload.update(updates)
        return resolve_claims_insurance_regime(**payload)

    def test_versions_and_temporal_boundaries_are_explicit(self):
        self.assertEqual(
            CLAIMS_INSURANCE_REGIME_VERSION,
            "rtm_claims_insurance_regime_v1_0",
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

    def test_current_home_damage_claim_selects_two_year_layer(self):
        decision = self._resolve()
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.product_type, "home_property")
        self.assertEqual(decision.coverage_nature, "damage")
        self.assertEqual(decision.incident_type, "coverage_denial")
        self.assertEqual(decision.limitation_years, 2)
        self.assertEqual(decision.notice_days, 7)
        self.assertEqual(decision.minimum_payment_days, 40)
        self.assertEqual(decision.performance_months, 3)
        rendered = " ".join(decision.legal_basis)
        self.assertIn("Ley 50/1980", rendered)
        self.assertIn("artículos 25 a 27", rendered)

    def test_health_and_life_claims_select_persons_layer(self):
        health = self._resolve(
            product_type="Seguro de salud",
            coverage_nature="Personas",
            policy_coverages="Asistencia sanitaria y hospitalización",
            incident_type="Autorización médica denegada",
            issue_text="La aseguradora denegó una autorización médica.",
        )
        self.assertEqual(health.status, "current")
        self.assertEqual(health.product_type, "health")
        self.assertEqual(health.coverage_nature, "persons")
        self.assertEqual(health.limitation_years, 5)
        self.assertIn("artículos 105 y 106", " ".join(health.legal_basis))

        life = self._resolve(
            product_type="Seguro de vida",
            coverage_nature="Personas",
            policy_coverages="Capital por fallecimiento",
            incident_type="Beneficiario de vida",
            issue_text="Reclamación del capital por fallecimiento del asegurado.",
        )
        self.assertEqual(life.status, "current")
        self.assertEqual(life.product_type, "life")
        self.assertEqual(life.limitation_years, 5)
        self.assertIn("artículos 83 a 88", " ".join(life.legal_basis))

    def test_mixed_coverages_never_guess_one_limitation_period(self):
        decision = self._resolve(
            product_type="Póliza multirriesgo mixta",
            coverage_nature="Mixta",
            policy_coverages="Daños materiales y accidente personal",
            incident_type="Siniestro pendiente",
            issue_text="Póliza con coberturas de daños y personas.",
        )
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.coverage_nature, "mixed")
        self.assertIsNone(decision.limitation_years)
        self.assertTrue(
            any("único plazo" in warning for warning in decision.warnings)
        )

    def test_modern_sac_path_and_distribution_are_versioned(self):
        decision = self._resolve(
            sac_complaint_date="2026-07-20",
            insurance_distributor="Correduría Demo",
        )
        self.assertEqual(decision.status, "current")
        self.assertTrue(decision.distribution_layer)
        self.assertEqual(decision.customer_service_wait_months, 1)
        self.assertEqual(decision.financial_complaint_resolution_days, 90)
        self.assertIn("Real Decreto-ley 3/2020", " ".join(decision.legal_basis))

    def test_liability_direct_action_activates_specific_layer(self):
        decision = self._resolve(
            product_type="Seguro de responsabilidad civil profesional",
            coverage_nature="Daños",
            policy_coverages="Responsabilidad civil frente a terceros",
            incident_type="Acción directa del tercero perjudicado",
            issue_text="El tercero perjudicado reclama directamente a la aseguradora.",
            harmed_third_party=True,
        )
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.product_type, "liability")
        self.assertTrue(decision.direct_action_layer)
        self.assertIn("artículos 73 y 76", " ".join(decision.legal_basis))

    def test_travel_investment_pension_and_motor_injury_fail_closed(self):
        travel = self._resolve(travel_insurance=True)
        self.assertEqual(travel.status, "operator_review")
        self.assertIn("travel.insurance", travel.blocking_reason)
        self.assertFalse(travel.legal_basis)

        investment = self._resolve(investment_linked=True)
        self.assertEqual(investment.status, "operator_review")
        self.assertFalse(investment.legal_basis)

        pension = self._resolve(pension_plan=True)
        self.assertEqual(pension.status, "operator_review")
        self.assertFalse(pension.legal_basis)

        motor = self._resolve(motor_third_party_injury=True)
        self.assertEqual(motor.status, "operator_review")
        self.assertIn("accidentes de tráfico", motor.blocking_reason)
        self.assertFalse(motor.legal_basis)

    def test_missing_foreign_historic_future_and_bad_chronology_fail_closed(self):
        missing = self._resolve(policy_date=None)
        self.assertEqual(missing.status, "operator_review")
        self.assertFalse(missing.legal_basis)

        foreign = self._resolve(insurer_country="Francia")
        self.assertEqual(foreign.status, "operator_review")
        self.assertFalse(foreign.legal_basis)

        historic = self._resolve(
            policy_date="1980-01-01",
            coverage_start="1980-01-01",
            coverage_end="1980-12-31",
            loss_date="1980-07-10",
        )
        self.assertEqual(historic.status, "operator_review")
        self.assertFalse(historic.legal_basis)

        future = self._resolve(
            policy_date="2027-12-20",
            coverage_start="2028-01-01",
            coverage_end="2028-12-31",
            loss_date="2028-07-10",
        )
        self.assertEqual(future.status, "operator_review")
        self.assertFalse(future.legal_basis)

        impossible = self._resolve(
            policy_date="2026-08-01",
            coverage_start="2026-01-01",
            coverage_end="2026-12-31",
            loss_date="2026-07-10",
        )
        self.assertEqual(impossible.status, "operator_review")
        self.assertFalse(impossible.legal_basis)


if __name__ == "__main__":
    unittest.main()
