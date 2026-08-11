from __future__ import annotations

import unittest

from rtm_core.claims_ecommerce_regime import (
    CLAIMS_ECOMMERCE_REGIME_VERSION,
    CURRENT_RULESET_SAFE_THROUGH,
    DISTANCE_CONTRACT_BASELINE_ON,
    DSA_FULL_APPLICATION_ON,
    GENERAL_PRODUCT_SAFETY_ON,
    GOODS_DIGITAL_CONFORMITY_ON,
    MARKETPLACE_INFORMATION_ON,
    ODR_PLATFORM_REPEALED_ON,
    RIGHT_TO_REPAIR_REVIEW_FROM,
    resolve_claims_ecommerce_regime,
)


class ClaimsEcommerceRegimeTest(unittest.TestCase):
    def _resolve(self, **updates):
        payload = {
            "purchase_date": "2026-07-01",
            "delivery_date": "2026-07-10",
            "incident_date": "2026-07-15",
            "withdrawal_date": None,
            "complaint_date": None,
            "seller_country": "España",
            "consumer_country": "España",
            "buyer_is_consumer": True,
            "seller_is_trader": True,
            "distance_contract": True,
            "contract_type": "Bien de consumo",
            "product_description": "Teléfono móvil",
            "service_description": None,
            "goods_with_digital_elements": False,
            "digital_content_or_service": False,
            "incident_type": "Producto defectuoso",
            "issue_text": "Pedido online de un producto defectuoso y no conforme.",
            "marketplace_present": None,
            "platform_is_contracting_party": False,
            "order_delivered": True,
            "agreed_delivery_date": "2026-07-10",
            "nonconformity_description": "El teléfono no enciende.",
            "withdrawal_communicated": False,
            "refund_amount": None,
            "refund_date": None,
            "subscription": False,
            "automatic_renewal": False,
            "seller_identified": True,
            "trader_status_disclosed": True,
            "unsafe_product": False,
            "post_guarantee_repair_requested": False,
        }
        payload.update(updates)
        return resolve_claims_ecommerce_regime(**payload)

    def test_versions_and_temporal_boundaries_are_explicit(self):
        self.assertEqual(
            CLAIMS_ECOMMERCE_REGIME_VERSION,
            "rtm_claims_ecommerce_regime_v1_0",
        )
        self.assertEqual(DISTANCE_CONTRACT_BASELINE_ON.isoformat(), "2014-06-13")
        self.assertEqual(GOODS_DIGITAL_CONFORMITY_ON.isoformat(), "2022-01-01")
        self.assertEqual(MARKETPLACE_INFORMATION_ON.isoformat(), "2022-05-28")
        self.assertEqual(DSA_FULL_APPLICATION_ON.isoformat(), "2024-02-17")
        self.assertEqual(GENERAL_PRODUCT_SAFETY_ON.isoformat(), "2024-12-13")
        self.assertEqual(ODR_PLATFORM_REPEALED_ON.isoformat(), "2025-07-20")
        self.assertEqual(RIGHT_TO_REPAIR_REVIEW_FROM.isoformat(), "2026-07-31")
        self.assertEqual(CURRENT_RULESET_SAFE_THROUGH.isoformat(), "2027-12-31")

    def test_current_goods_nonconformity_selects_three_year_rules(self):
        decision = self._resolve()
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.product_type, "goods")
        self.assertEqual(decision.incident_type, "non_conformity")
        self.assertEqual(decision.withdrawal_days, 14)
        self.assertEqual(decision.delivery_default_days, 30)
        self.assertEqual(decision.goods_conformity_years, 3)
        self.assertEqual(decision.goods_presumption_years, 2)
        self.assertFalse(decision.odr_platform_available)
        self.assertIn("artículos 114 a 127 bis", " ".join(decision.legal_basis))

    def test_current_digital_rules_keep_two_and_one_year_periods(self):
        decision = self._resolve(
            contract_type="Contenido digital",
            product_description=None,
            service_description="Licencia digital descargable",
            digital_content_or_service=True,
            incident_type="Falta de conformidad digital",
            issue_text="El contenido digital no funciona y carece de actualizaciones.",
            nonconformity_description=None,
        )
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.product_type, "digital_content")
        self.assertEqual(decision.incident_type, "digital_content")
        self.assertEqual(decision.digital_conformity_years, 2)
        self.assertEqual(decision.digital_presumption_years, 1)

    def test_marketplace_layers_are_temporally_versioned(self):
        decision = self._resolve(
            incident_type="Marketplace no identifica al vendedor",
            issue_text="El marketplace no informa de la condición de empresario.",
            marketplace_present="Marketplace Demo",
            seller_identified=False,
            trader_status_disclosed=False,
        )
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.incident_type, "marketplace_disclosure")
        self.assertTrue(decision.marketplace_information_active)
        self.assertTrue(decision.dsa_marketplace_active)
        rendered = " ".join(decision.legal_basis)
        self.assertIn("artículo 97 bis", rendered)
        self.assertIn("Reglamento (UE) 2022/2065", rendered)

    def test_unsafe_product_uses_current_product_safety_layer(self):
        decision = self._resolve(
            incident_type="Producto inseguro",
            issue_text="Producto peligroso vendido online y sometido a retirada.",
            unsafe_product=True,
            nonconformity_description=None,
        )
        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.incident_type, "unsafe_product")
        self.assertTrue(decision.product_safety_active)
        self.assertIn("Reglamento (UE) 2023/988", " ".join(decision.legal_basis))

    def test_missing_consumer_status_can_remain_reviewable_with_country(self):
        decision = self._resolve(buyer_is_consumer=None)
        self.assertEqual(decision.status, "current")
        self.assertTrue(decision.consumer_status_review)
        self.assertTrue(any("condición de consumidor" in item for item in decision.warnings))

    def test_private_foreign_historic_and_future_cases_fail_closed(self):
        private = self._resolve(seller_is_trader=False)
        self.assertEqual(private.status, "operator_review")
        self.assertFalse(private.legal_basis)

        foreign = self._resolve(seller_country="Portugal")
        self.assertEqual(foreign.status, "operator_review")
        self.assertFalse(foreign.legal_basis)

        historic = self._resolve(
            purchase_date="2021-05-01",
            delivery_date="2021-05-10",
            incident_date="2021-05-15",
            agreed_delivery_date="2021-05-10",
        )
        self.assertEqual(historic.status, "operator_review")
        self.assertFalse(historic.legal_basis)

        future = self._resolve(
            purchase_date="2028-01-01",
            delivery_date="2028-01-10",
            incident_date="2028-01-15",
            agreed_delivery_date="2028-01-10",
        )
        self.assertEqual(future.status, "operator_review")
        self.assertFalse(future.legal_basis)

    def test_post_guarantee_repair_after_review_date_fails_closed(self):
        decision = self._resolve(
            purchase_date="2023-01-01",
            delivery_date="2023-01-10",
            incident_date="2026-08-02",
            agreed_delivery_date="2023-01-10",
            post_guarantee_repair_requested=True,
        )
        self.assertEqual(decision.status, "operator_review")
        self.assertTrue(decision.right_to_repair_review)
        self.assertFalse(decision.legal_basis)


if __name__ == "__main__":
    unittest.main()
