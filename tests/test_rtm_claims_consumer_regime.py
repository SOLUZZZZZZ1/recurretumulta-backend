from __future__ import annotations

from datetime import date
import unittest

from rtm_core.claims_consumer_regime import (
    CLAIMS_CONSUMER_REGIME_VERSION,
    CURRENT_GOODS_RULES_FROM,
    CURRENT_OFF_PREMISES_RULES_FROM,
    CURRENT_RULESET_SAFE_THROUGH,
    resolve_claims_consumer_regime,
)


def _baseline(**overrides):
    values = {
        "contract_date": "2026-03-01",
        "delivery_date": "2026-03-03",
        "client_country": "España",
        "business_country": "España",
        "client_is_consumer": True,
        "contract_type": "Bien de consumo",
        "incident_type": "Falta de conformidad",
        "issue_text": "Reclamación de consumo por producto defectuoso comprado en tienda.",
        "in_store_purchase": True,
        "distance_contract": False,
        "off_premises_contract": False,
        "online_purchase": False,
        "new_goods": True,
        "second_hand_goods": False,
        "large_business": False,
        "customer_service_act_applicable": False,
    }
    values.update(overrides)
    return resolve_claims_consumer_regime(**values)


class ClaimsConsumerRegimeTest(unittest.TestCase):
    def test_versions_and_temporal_boundaries_are_explicit(self):
        self.assertEqual(CLAIMS_CONSUMER_REGIME_VERSION, "rtm_claims_consumer_regime_v1_0")
        self.assertEqual(CURRENT_GOODS_RULES_FROM, date(2022, 1, 1))
        self.assertEqual(CURRENT_OFF_PREMISES_RULES_FROM, date(2022, 5, 28))
        self.assertEqual(CURRENT_RULESET_SAFE_THROUGH, date(2027, 12, 31))

    def test_current_in_store_new_goods_selects_three_year_layer(self):
        decision = _baseline()
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.scope, "spain")
        self.assertEqual(decision.client_type, "consumer")
        self.assertEqual(decision.contract_type, "goods")
        self.assertEqual(decision.purchase_channel, "in_store")
        self.assertEqual(decision.incident_type, "goods_nonconformity")
        self.assertTrue(decision.goods_conformity_layer)
        self.assertEqual(decision.legal_conformity_period_years, 3)
        self.assertEqual(decision.presumed_origin_period_years, 2)
        self.assertFalse(decision.withdrawal_layer)
        self.assertIsNotNone(decision.ruleset)
        self.assertTrue(decision.legal_basis)

    def test_second_hand_period_below_one_year_fails_closed(self):
        decision = _baseline(
            new_goods=False,
            second_hand_goods=True,
            second_hand_agreed_period_years=0.5,
            issue_text="Reclamación de consumo por bien de segunda mano defectuoso.",
        )
        self.assertEqual(decision.status, "operator_review")
        self.assertIn("mínimo legal de un año", decision.blocking_reason or "")

    def test_unsolicited_off_premises_service_uses_thirty_days(self):
        decision = _baseline(
            delivery_date=None,
            contract_type="Servicio de consumo",
            incident_type="Desistimiento",
            issue_text="Desistimiento de servicio contratado durante visita domiciliaria.",
            in_store_purchase=False,
            off_premises_contract=True,
            unsolicited_home_visit=True,
            new_goods=False,
            service_start_date="2026-03-02",
            withdrawal_information_delivered=True,
            service_start_during_withdrawal_requested=False,
            service_fully_performed=False,
        )
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.contract_type, "service")
        self.assertEqual(decision.purchase_channel, "off_premises")
        self.assertTrue(decision.withdrawal_layer)
        self.assertEqual(decision.withdrawal_days, 30)

    def test_in_store_change_of_mind_has_no_withdrawal_layer(self):
        decision = _baseline(
            incident_type="Desistimiento",
            issue_text="El consumidor quiere desistir de una compra presencial sin defecto.",
        )
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.incident_type, "withdrawal")
        self.assertFalse(decision.withdrawal_layer)
        self.assertIsNone(decision.withdrawal_days)
        self.assertTrue(
            any("compra presencial" in warning.lower() for warning in decision.warnings)
        )

    def test_distance_marketplace_and_sector_boundaries_route_away(self):
        cases = (
            (
                {"distance_contract": True, "in_store_purchase": False},
                "claims.ecommerce",
            ),
            ({"marketplace_involved": True}, "marketplace"),
            ({"telecommunications_involved": True}, "telecommunications"),
            ({"energy_involved": True}, "claims.energy"),
            ({"banking_or_payment_involved": True}, "claims.banking"),
            ({"insurance_involved": True}, "claims.insurance"),
            ({"professional_service_involved": True}, "claims.professional_services"),
            ({"unsafe_product": True}, "seguridad de producto"),
            ({"personal_injury": True}, "lesiones personales"),
        )
        for overrides, marker in cases:
            with self.subTest(marker=marker):
                decision = _baseline(**overrides)
                self.assertEqual(decision.status, "operator_review")
                self.assertIn(marker.lower(), (decision.blocking_reason or "").lower())

    def test_b2b_foreign_historic_future_and_bad_chronology_fail_closed(self):
        cases = (
            {"client_is_consumer": False},
            {"business_country": "Francia"},
            {"contract_date": "2014-01-01", "delivery_date": "2014-01-03"},
            {"contract_date": "2028-01-01", "delivery_date": "2028-01-03"},
            {"contract_date": "2026-03-10", "delivery_date": "2026-03-01"},
            {"contract_date": None},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                decision = _baseline(**overrides)
                self.assertEqual(decision.status, "operator_review")
                self.assertTrue(decision.blocking_reason)

    def test_customer_service_act_transition_and_active_are_separated(self):
        transition = _baseline(
            complaint_date="2026-07-01",
            large_business=True,
            customer_service_act_applicable=True,
        )
        self.assertEqual(transition.status, "current")
        self.assertEqual(transition.customer_service_layer, "transition")
        self.assertIsNone(transition.customer_service_resolution_business_days)

        active = _baseline(
            contract_date="2027-01-02",
            delivery_date="2027-01-03",
            complaint_date="2027-02-01",
            large_business=True,
            customer_service_act_applicable=True,
        )
        self.assertEqual(active.status, "current")
        self.assertEqual(active.customer_service_layer, "active")
        self.assertEqual(active.customer_service_resolution_business_days, 15)

    def test_full_performance_loss_is_never_inferred_without_all_elements(self):
        decision = _baseline(
            delivery_date=None,
            contract_type="Servicio de consumo",
            incident_type="Desistimiento",
            issue_text="Desistimiento de servicio fuera de establecimiento ya ejecutado.",
            in_store_purchase=False,
            off_premises_contract=True,
            new_goods=False,
            service_start_date="2026-03-02",
            withdrawal_information_delivered=True,
            service_start_during_withdrawal_requested=True,
            service_start_express_consent=False,
            withdrawal_loss_acknowledged=False,
            service_fully_performed=True,
        )
        self.assertEqual(decision.status, "current")
        self.assertFalse(decision.fully_performed_withdrawal_loss_possible)
        self.assertTrue(
            any("ejecución completa" in warning.lower() for warning in decision.warnings)
        )


if __name__ == "__main__":
    unittest.main()
