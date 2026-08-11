from __future__ import annotations

import unittest

from rtm_core.claims_energy_regime import (
    CLAIMS_ENERGY_REGIME_VERSION,
    CUSTOMER_SERVICE_FULL_ADAPTATION_ON,
    CURRENT_RULESET_SAFE_THROUGH,
    ELECTRICITY_DEFERRED_RULES_EFFECTIVE_ON,
    ELECTRICITY_GENERAL_EFFECTIVE_ON,
    GAS_2026_CONTRACT_NOTICE_EFFECTIVE_ON,
    GAS_BASELINE_EFFECTIVE_ON,
    resolve_claims_energy_regime,
)


class ClaimsEnergyRegimeTest(unittest.TestCase):
    def _resolve(self, **updates):
        payload = {
            "incident_date": "2026-07-20",
            "contract_date": "2026-06-15",
            "invoice_date": "2026-07-20",
            "complaint_date": None,
            "supply_country": "España",
            "supply_type": "Electricidad",
            "incident_type": "Facturación",
            "issue_text": "Factura eléctrica con lectura y consumo incorrectos.",
            "vulnerable_consumer": False,
        }
        payload.update(updates)
        return resolve_claims_energy_regime(**payload)

    def test_versions_and_temporal_boundaries_are_explicit(self):
        self.assertEqual(
            CLAIMS_ENERGY_REGIME_VERSION,
            "rtm_claims_energy_regime_v1_0",
        )
        self.assertEqual(
            ELECTRICITY_GENERAL_EFFECTIVE_ON.isoformat(),
            "2026-02-12",
        )
        self.assertEqual(
            ELECTRICITY_DEFERRED_RULES_EFFECTIVE_ON.isoformat(),
            "2026-06-12",
        )
        self.assertEqual(GAS_BASELINE_EFFECTIVE_ON.isoformat(), "2003-01-01")
        self.assertEqual(
            GAS_2026_CONTRACT_NOTICE_EFFECTIVE_ON.isoformat(),
            "2026-03-21",
        )
        self.assertEqual(
            CUSTOMER_SERVICE_FULL_ADAPTATION_ON.isoformat(),
            "2026-12-28",
        )
        self.assertEqual(CURRENT_RULESET_SAFE_THROUGH.isoformat(), "2027-12-31")

    def test_current_electricity_billing_selects_deferred_rules(self):
        decision = self._resolve()
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.supply_type, "electricity")
        self.assertEqual(decision.incident_type, "billing")
        self.assertTrue(decision.billing_rules_active)
        self.assertEqual(decision.complaint_response_business_days, 15)
        rendered = " ".join(decision.legal_basis)
        self.assertIn("Real Decreto 88/2026", rendered)
        self.assertIn("artículos 43 a 45", rendered)

    def test_electricity_billing_before_deferred_effect_fails_closed(self):
        decision = self._resolve(
            incident_date="2026-05-20",
            invoice_date="2026-05-20",
            contract_date="2026-05-01",
        )
        self.assertEqual(decision.status, "operator_review")
        self.assertFalse(decision.legal_basis)
        self.assertIn("aún no habían surtido efectos", decision.blocking_reason)

    def test_gas_contract_change_uses_one_month_notice_after_reform(self):
        decision = self._resolve(
            incident_date="2026-04-20",
            invoice_date=None,
            contract_date="2026-01-10",
            supply_type="Gas natural",
            incident_type="Modificación de precio",
            issue_text="La comercializadora de gas cambió el precio del contrato.",
        )
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.supply_type, "gas")
        self.assertEqual(decision.incident_type, "contract_change")
        self.assertEqual(decision.modification_notice_days, 30)
        self.assertIn("artículo 57 bis.f)", " ".join(decision.legal_basis))

    def test_gas_contract_change_before_reform_requires_history(self):
        decision = self._resolve(
            incident_date="2026-03-10",
            invoice_date=None,
            contract_date="2026-01-10",
            supply_type="Gas natural",
            incident_type="Modificación de precio",
            issue_text="La comercializadora de gas cambió el precio.",
        )
        self.assertEqual(decision.status, "operator_review")
        self.assertFalse(decision.legal_basis)

    def test_vulnerable_supply_in_2026_activates_temporary_layer(self):
        decision = self._resolve(
            incident_type="Corte de suministro a consumidor vulnerable",
            issue_text="Corte eléctrico en vivienda de consumidor vulnerable.",
            vulnerable_consumer=True,
        )
        self.assertEqual(decision.status, "current")
        self.assertTrue(decision.temporary_vulnerable_protection)
        self.assertIn("Real Decreto-ley 7/2026", " ".join(decision.legal_basis))

    def test_missing_foreign_other_and_future_cases_fail_closed(self):
        missing = self._resolve(
            incident_date=None,
            invoice_date=None,
            contract_date=None,
            complaint_date=None,
        )
        self.assertEqual(missing.status, "operator_review")
        self.assertFalse(missing.legal_basis)

        foreign = self._resolve(supply_country="Portugal")
        self.assertEqual(foreign.status, "operator_review")
        self.assertFalse(foreign.legal_basis)

        other = self._resolve(
            supply_type="Agua",
            issue_text="Factura de agua",
        )
        self.assertEqual(other.status, "operator_review")
        self.assertFalse(other.legal_basis)

        future = self._resolve(
            incident_date="2028-01-01",
            invoice_date="2028-01-01",
            contract_date="2027-12-01",
        )
        self.assertEqual(future.status, "operator_review")
        self.assertFalse(future.legal_basis)


if __name__ == "__main__":
    unittest.main()
