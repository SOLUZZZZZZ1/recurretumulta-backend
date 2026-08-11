from __future__ import annotations

from datetime import date
import unittest

from rtm_core.claims_consumer_regime import (
    CLAIMS_CONSUMER_REGIME_VERSION,
    CURRENT_RULESET_SAFE_THROUGH,
    GOODS_CONFORMITY_CURRENT_ON,
    resolve_claims_consumer_regime,
)


class ClaimsConsumerRegimeTest(unittest.TestCase):
    def _base(self) -> dict:
        return {
            "contract_date": None,
            "purchase_date": "2026-05-01",
            "delivery_date": "2026-05-03",
            "incident_date": "2026-06-10",
            "complaint_date": "2026-06-12",
            "trader_country": "España",
            "consumer_country": "España",
            "customer_is_consumer": True,
            "supplier_is_trader": True,
            "contract_channel": "Establecimiento físico",
            "online_purchase": False,
            "object_type": "Bien de consumo",
            "product_description": "Lavadora nueva",
            "service_description": None,
            "incident_type": "Producto defectuoso",
            "issue_text": "Reclamación de consumo por producto defectuoso.",
            "nonconformity": True,
            "nonconformity_description": "La lavadora pierde agua.",
        }

    def test_versions_and_temporal_boundaries_are_explicit(self):
        self.assertEqual(
            CLAIMS_CONSUMER_REGIME_VERSION,
            "rtm_claims_consumer_regime_v1_0",
        )
        self.assertEqual(GOODS_CONFORMITY_CURRENT_ON, date(2022, 1, 1))
        self.assertEqual(CURRENT_RULESET_SAFE_THROUGH, date(2027, 12, 31))

    def test_current_in_store_goods_select_current_conformity_layer(self):
        decision = resolve_claims_consumer_regime(**self._base())
        self.assertEqual(decision.status, "current")
        self.assertTrue(decision.residual_scope)
        self.assertEqual(decision.object_type, "goods")
        self.assertEqual(decision.incident_type, "non_conformity")
        self.assertEqual(decision.goods_conformity_years, 3)
        self.assertEqual(decision.goods_presumption_years, 2)
        self.assertTrue(decision.adr_layer)
        self.assertTrue(decision.customer_service_layer)
        self.assertTrue(decision.legal_basis)

    def test_ordinary_service_uses_general_contract_baseline(self):
        values = self._base()
        values.update(
            {
                "object_type": "Servicio",
                "product_description": None,
                "service_description": "Servicio ordinario de limpieza",
                "incident_type": "Servicio incompleto",
                "issue_text": "Reclamación de consumo por servicio incompleto.",
                "nonconformity": None,
                "nonconformity_description": None,
                "service_incomplete": True,
            }
        )
        decision = resolve_claims_consumer_regime(**values)
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.object_type, "service")
        self.assertEqual(decision.incident_type, "service_incomplete_or_defective")
        self.assertIsNone(decision.goods_conformity_years)

    def test_online_and_sector_specific_cases_are_routed_away(self):
        online = self._base()
        online["online_purchase"] = True
        online_decision = resolve_claims_consumer_regime(**online)
        self.assertEqual(online_decision.status, "operator_review")
        self.assertEqual(online_decision.specialized_boundary, "ecommerce")

        banking = self._base()
        banking["issue_text"] = "Cargo no reconocido en tarjeta bancaria."
        banking_decision = resolve_claims_consumer_regime(**banking)
        self.assertEqual(banking_decision.status, "operator_review")
        self.assertEqual(banking_decision.specialized_boundary, "banking")

        telecom = self._base()
        telecom["regulated_service_hint"] = "Portabilidad de telefonía móvil"
        telecom_decision = resolve_claims_consumer_regime(**telecom)
        self.assertEqual(telecom_decision.status, "operator_review")
        self.assertEqual(telecom_decision.specialized_boundary, "telecommunications")

    def test_b2b_foreign_missing_and_future_cases_fail_closed(self):
        b2b = self._base()
        b2b["customer_is_consumer"] = False
        self.assertEqual(
            resolve_claims_consumer_regime(**b2b).status,
            "operator_review",
        )

        foreign = self._base()
        foreign["trader_country"] = "Francia"
        self.assertEqual(
            resolve_claims_consumer_regime(**foreign).status,
            "operator_review",
        )

        missing = self._base()
        missing["purchase_date"] = None
        missing["contract_date"] = None
        self.assertEqual(
            resolve_claims_consumer_regime(**missing).status,
            "operator_review",
        )

        future = self._base()
        future["incident_date"] = "2028-01-01"
        self.assertEqual(
            resolve_claims_consumer_regime(**future).status,
            "operator_review",
        )

    def test_unsafe_product_requires_separate_safety_route(self):
        values = self._base()
        values["unsafe_product"] = True
        values["incident_type"] = "Producto inseguro"
        decision = resolve_claims_consumer_regime(**values)
        self.assertEqual(decision.status, "operator_review")
        self.assertTrue(decision.product_safety_review)
        self.assertEqual(decision.incident_type, "unsafe_product")

    def test_historic_goods_nonconformity_never_receives_current_periods(self):
        values = self._base()
        values["purchase_date"] = "2021-12-20"
        values["incident_date"] = "2022-01-10"
        decision = resolve_claims_consumer_regime(**values)
        self.assertEqual(decision.status, "operator_review")
        self.assertIsNone(decision.goods_conformity_years)
        self.assertFalse(decision.legal_basis)

    def test_unknown_object_or_incident_fails_closed(self):
        values = self._base()
        values.update(
            {
                "object_type": None,
                "product_description": None,
                "service_description": None,
                "incident_type": None,
                "issue_text": "Reclamación de consumo sin objeto concretado.",
                "nonconformity": None,
                "nonconformity_description": None,
            }
        )
        decision = resolve_claims_consumer_regime(**values)
        self.assertEqual(decision.status, "operator_review")
        self.assertEqual(decision.object_type, "unknown")


if __name__ == "__main__":
    unittest.main()
