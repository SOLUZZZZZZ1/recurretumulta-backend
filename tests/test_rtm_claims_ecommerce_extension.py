from __future__ import annotations

import unittest

from rtm_core.claims_ecommerce_extension import CLAIMS_ECOMMERCE_EXTENSION_VERSION
from rtm_core.domain_catalog import family_profile
from rtm_core.document_fact_catalog import field_spec, registered_fact_keys
from rtm_core.document_normalization import (
    DocumentExtractionPacket,
    DocumentObservation,
    normalize_document_packet,
)
from rtm_core.versioning import build_version_snapshot


DOC_ID = "doc-claims-ecommerce-extension"


def _observation(field: str, value, evidence: str) -> DocumentObservation:
    return DocumentObservation(
        field=field,
        value=value,
        document_id=DOC_ID,
        page_index=0,
        evidence=evidence,
        confidence=0.99,
        extraction_method="rtm_claims_ecommerce_extension_test_v1",
        source_type="document_vision",
    )


class ClaimsEcommerceExtensionTest(unittest.TestCase):
    def test_fact_aliases_capability_and_versions_are_installed(self):
        self.assertEqual(
            CLAIMS_ECOMMERCE_EXTENSION_VERSION,
            "rtm_claims_ecommerce_extension_v1_0",
        )
        self.assertEqual(field_spec("claims", "seller_name").key, "vendedor_online")
        self.assertEqual(
            field_spec("claims", "agreed_delivery_date").key,
            "fecha_entrega_pactada",
        )
        self.assertEqual(
            field_spec("claims", "total_order_price_eur").key,
            "precio_total_pedido_eur",
        )
        self.assertEqual(
            field_spec("claims", "withdrawal_notice_date").key,
            "fecha_comunicacion_desistimiento",
        )
        self.assertIn("producto_inseguro", registered_fact_keys("claims"))

        profile = family_profile("claims", "comercio_electronico")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.specialist, "claims.ecommerce")
        self.assertEqual(profile.capability, "specialist_ready")

        snapshot = build_version_snapshot()
        expected = {
            "claims_ecommerce_extension": "rtm_claims_ecommerce_extension_v1_0",
            "claims_ecommerce_regime": "rtm_claims_ecommerce_regime_v1_0",
            "claims_ecommerce_specialist": "rtm_claims_ecommerce_specialist_v1_0",
            "claims_specialist_registry": "rtm_claims_specialist_registry_v1_0",
        }
        for name, version in expected.items():
            with self.subTest(component=name):
                component = snapshot["components"][name]
                self.assertEqual(component["declared"], version)
                self.assertEqual(component["runtime"], version)
                self.assertTrue(component["matches_declared"])
                self.assertIsNone(component["discovery_error"])
                self.assertEqual(snapshot["contracts"][name], version)

    def test_ecommerce_document_facts_are_typed_by_normalization(self):
        packet = DocumentExtractionPacket(
            case_id="case-claims-ecommerce-extension",
            service="claims",
            extractor_version="claims_ecommerce_document_test_v1",
            source_document_ids=[DOC_ID],
            observations=[
                _observation("seller_name", "Tienda Demo, S.L.", "Seller: Tienda Demo"),
                _observation("seller_is_trader", "Sí", "Seller acts as trader"),
                _observation("distance_contract", "Sí", "Online distance contract"),
                _observation("order_date", "01/07/2026", "Order date 01/07/2026"),
                _observation(
                    "agreed_delivery_date",
                    "20/07/2026",
                    "Agreed delivery 20/07/2026",
                ),
                _observation("order_delivered", "No", "Order not delivered"),
                _observation(
                    "total_order_price_eur",
                    "129,90 EUR",
                    "Total order price 129,90 EUR",
                ),
            ],
        )

        facts = normalize_document_packet(packet).facts.facts
        self.assertEqual(facts["vendedor_online"].value, "Tienda Demo, S.L.")
        self.assertTrue(facts["vendedor_es_empresario"].value)
        self.assertTrue(facts["contrato_a_distancia"].value)
        self.assertEqual(facts["fecha_pedido"].value, "2026-07-01")
        self.assertEqual(facts["fecha_entrega_pactada"].value, "2026-07-20")
        self.assertFalse(facts["pedido_entregado"].value)
        self.assertEqual(facts["precio_total_pedido_eur"].value, 129.9)


if __name__ == "__main__":
    unittest.main()
