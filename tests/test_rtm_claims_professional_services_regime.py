from __future__ import annotations

from datetime import date
import unittest

from rtm_core.claims_professional_services_regime import (
    CLAIMS_PROFESSIONAL_SERVICES_REGIME_VERSION,
    CIVIL_LIMITATION_CURRENT_FROM,
    CUSTOMER_SERVICE_ACT_EFFECTIVE_ON,
    CUSTOMER_SERVICE_ADAPTATION_DEADLINE,
    CURRENT_RULESET_SAFE_THROUGH,
    DISTANCE_CONSUMER_CURRENT_FROM,
    resolve_claims_professional_services_regime,
)


def _base(**updates):
    values = {
        "contract_date": "2026-03-01",
        "service_start_date": "2026-03-05",
        "expected_completion_date": "2026-04-01",
        "actual_completion_date": None,
        "breach_date": "2026-04-02",
        "complaint_date": "2026-04-10",
        "withdrawal_notice_date": None,
        "client_country": "España",
        "provider_country": "España",
        "client_is_consumer": True,
        "professional_type": "Consultoría tecnológica",
        "incident_type": "Servicio incompleto",
        "issue_text": "Servicio profesional de consultoría tecnológica incompleto.",
        "obligation_type": "Obligación de medios",
        "means_obligation": True,
        "result_obligation": False,
        "distance_contract": False,
        "off_premises_contract": False,
        "unsolicited_home_visit": False,
        "promotional_excursion": False,
        "withdrawal_information_delivered": None,
        "service_start_during_withdrawal_requested": None,
        "service_start_express_consent": None,
        "withdrawal_loss_acknowledged": None,
        "service_fully_performed": False,
        "claim_nature": "Contractual",
        "large_company": False,
        "customer_service_act_applicable": False,
        "legal_service": False,
        "healthcare_service": False,
        "architecture_building_service": False,
        "tax_accounting_service": False,
        "financial_investment_service": False,
        "insurance_intermediation_service": False,
        "public_administration_service": False,
        "employment_service": False,
        "data_protection_primary": False,
        "standardized_digital_content": False,
        "professional_fee_collection": False,
    }
    values.update(updates)
    return values


class ClaimsProfessionalServicesRegimeTest(unittest.TestCase):
    def test_versions_and_temporal_boundaries_are_explicit(self):
        self.assertEqual(
            CLAIMS_PROFESSIONAL_SERVICES_REGIME_VERSION,
            "rtm_claims_professional_services_regime_v1_0",
        )
        self.assertEqual(CIVIL_LIMITATION_CURRENT_FROM, date(2015, 10, 7))
        self.assertEqual(DISTANCE_CONSUMER_CURRENT_FROM, date(2022, 5, 28))
        self.assertEqual(CUSTOMER_SERVICE_ACT_EFFECTIVE_ON, date(2025, 12, 28))
        self.assertEqual(
            CUSTOMER_SERVICE_ADAPTATION_DEADLINE,
            date(2026, 12, 28),
        )
        self.assertEqual(CURRENT_RULESET_SAFE_THROUGH, date(2027, 12, 31))

    def test_current_consumer_consulting_selects_generic_baseline(self):
        decision = resolve_claims_professional_services_regime(**_base())
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.scope, "spain")
        self.assertEqual(decision.client_type, "consumer")
        self.assertEqual(decision.service_type, "technology")
        self.assertEqual(decision.incident_type, "defective_or_incomplete")
        self.assertEqual(decision.obligation_type, "means")
        self.assertEqual(decision.claim_nature, "contractual")
        self.assertEqual(decision.contractual_limitation_candidate_years, 5)
        self.assertIsNone(decision.extracontractual_limitation_candidate_years)
        self.assertTrue(
            any("Código Civil" in basis for basis in decision.legal_basis)
        )

    def test_distance_withdrawal_uses_thirty_days_for_unsolicited_home_visit(self):
        decision = resolve_claims_professional_services_regime(
            **_base(
                incident_type="Desistimiento",
                issue_text="Desistimiento de servicio profesional contratado en visita no solicitada.",
                distance_contract=False,
                off_premises_contract=True,
                unsolicited_home_visit=True,
                withdrawal_notice_date="2026-03-20",
                withdrawal_information_delivered=True,
                service_start_during_withdrawal_requested=False,
                service_start_express_consent=False,
                withdrawal_loss_acknowledged=False,
            )
        )
        self.assertEqual(decision.status, "current")
        self.assertTrue(decision.withdrawal_layer)
        self.assertEqual(decision.withdrawal_days, 30)
        self.assertFalse(decision.fully_performed_withdrawal_loss_possible)

    def test_full_performance_loss_is_never_inferred_without_all_elements(self):
        incomplete = resolve_claims_professional_services_regime(
            **_base(
                incident_type="Desistimiento",
                issue_text="Desistimiento tras ejecución completa del servicio profesional.",
                distance_contract=True,
                withdrawal_information_delivered=True,
                service_start_during_withdrawal_requested=True,
                service_start_express_consent=True,
                withdrawal_loss_acknowledged=False,
                service_fully_performed=True,
            )
        )
        complete = resolve_claims_professional_services_regime(
            **_base(
                incident_type="Desistimiento",
                issue_text="Desistimiento tras ejecución completa del servicio profesional.",
                distance_contract=True,
                withdrawal_information_delivered=True,
                service_start_during_withdrawal_requested=True,
                service_start_express_consent=True,
                withdrawal_loss_acknowledged=True,
                service_fully_performed=True,
            )
        )
        self.assertFalse(incomplete.fully_performed_withdrawal_loss_possible)
        self.assertTrue(complete.fully_performed_withdrawal_loss_possible)

    def test_customer_service_act_is_transitioned_and_scope_gated(self):
        transition = resolve_claims_professional_services_regime(
            **_base(
                complaint_date="2026-08-01",
                large_company=True,
                customer_service_act_applicable=True,
            )
        )
        active = resolve_claims_professional_services_regime(
            **_base(
                contract_date="2026-12-29",
                service_start_date="2027-01-02",
                expected_completion_date="2027-01-20",
                breach_date="2027-01-21",
                complaint_date="2027-02-01",
                large_company=True,
                customer_service_act_applicable=True,
            )
        )
        small = resolve_claims_professional_services_regime(**_base())
        self.assertEqual(transition.customer_service_layer, "transition")
        self.assertIsNone(transition.customer_service_resolution_business_days)
        self.assertEqual(active.customer_service_layer, "active")
        self.assertEqual(active.customer_service_resolution_business_days, 15)
        self.assertEqual(small.customer_service_layer, "not_applicable")

    def test_special_b2b_foreign_historic_future_and_fee_cases_fail_closed(self):
        cases = (
            _base(client_is_consumer=False),
            _base(provider_country="Francia"),
            _base(contract_date="2014-01-01", service_start_date="2014-01-02"),
            _base(contract_date="2028-01-01", service_start_date="2028-01-02"),
            _base(
                professional_type="Abogado",
                issue_text="Servicio profesional de abogado con posible pérdida de oportunidad.",
                legal_service=True,
            ),
            _base(
                professional_type="Arquitecto",
                issue_text="Servicio profesional de arquitectura para edificio.",
                architecture_building_service=True,
            ),
            _base(
                issue_text="El profesional reclama sus honorarios impagados.",
                professional_fee_collection=True,
            ),
        )
        for values in cases:
            with self.subTest(values=values):
                decision = resolve_claims_professional_services_regime(**values)
                self.assertEqual(decision.status, "operator_review")
                self.assertFalse(decision.legal_basis)
                self.assertTrue(decision.blocking_reason)

    def test_unknown_obligation_never_becomes_result(self):
        decision = resolve_claims_professional_services_regime(
            **_base(
                obligation_type=None,
                means_obligation=None,
                result_obligation=None,
            )
        )
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.obligation_type, "unknown")
        self.assertTrue(
            any("naturaleza de la obligación" in warning for warning in decision.warnings)
        )


if __name__ == "__main__":
    unittest.main()
