from __future__ import annotations

import unittest

from rtm_core.claims_consumer_extension import CLAIMS_CONSUMER_EXTENSION_VERSION
from rtm_core.domain_catalog import family_profile
from rtm_core.document_fact_catalog import field_spec, registered_fact_keys
from rtm_core.document_normalization import (
    DocumentExtractionPacket,
    DocumentObservation,
    normalize_document_packet,
)
from rtm_core.versioning import build_version_snapshot


DOC_ID = "doc-claims-consumer-extension"


def _observation(field: str, value, evidence: str) -> DocumentObservation:
    return DocumentObservation(
        field=field,
        value=value,
        document_id=DOC_ID,
        page_index=0,
        evidence=evidence,
        confidence=0.99,
        extraction_method="rtm_claims_consumer_extension_test_v1",
        source_type="document_vision",
    )


class ClaimsConsumerExtensionTest(unittest.TestCase):
    def test_fact_aliases_capability_and_versions_are_installed(self):
        self.assertEqual(
            CLAIMS_CONSUMER_EXTENSION_VERSION,
            "rtm_claims_consumer_extension_v1_0",
        )
        self.assertEqual(
            field_spec("claims", "general_consumer_trader").key,
            "empresario_consumo",
        )
        self.assertEqual(
            field_spec("claims", "general_consumer_purchase_date").key,
            "fecha_compra_consumo",
        )
        self.assertEqual(
            field_spec("claims", "general_consumer_total_price_eur").key,
            "precio_total_consumo_eur",
        )
        self.assertIn("falta_conformidad_consumo", registered_fact_keys("claims"))
        self.assertIn("producto_inseguro_consumo", registered_fact_keys("claims"))

        profile = family_profile("claims", "consumo")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.specialist, "claims.consumer")
        self.assertEqual(profile.capability, "specialist_ready")

        snapshot = build_version_snapshot()
        expected = {
            "claims_consumer_extension": "rtm_claims_consumer_extension_v1_0",
            "claims_consumer_regime": "rtm_claims_consumer_regime_v1_0",
            "claims_consumer_specialist": "rtm_claims_consumer_specialist_v1_0",
        }
        for name, version in expected.items():
            with self.subTest(component=name):
                component = snapshot["components"][name]
                self.assertEqual(component["declared"], version)
                self.assertEqual(component["runtime"], version)
                self.assertTrue(component["matches_declared"])
                self.assertIsNone(component["discovery_error"])
                self.assertEqual(snapshot["contracts"][name], version)

    def test_consumer_document_facts_are_typed_by_normalization(self):
        packet = DocumentExtractionPacket(
            case_id="case-claims-consumer-extension",
            service="claims",
            extractor_version="claims_consumer_document_test_v1",
            source_document_ids=[DOC_ID],
            observations=[
                _observation(
                    "general_consumer_trader",
                    "Comercio Demo, S.L.",
                    "Empresario: Comercio Demo, S.L.",
                ),
                _observation(
                    "general_customer_is_consumer",
                    "Sí",
                    "El comprador actúa como consumidor",
                ),
                _observation(
                    "general_supplier_is_trader",
                    "Sí",
                    "La sociedad actúa como empresario",
                ),
                _observation(
                    "general_consumer_purchase_date",
                    "01/05/2026",
                    "Fecha de compra 01/05/2026",
                ),
                _observation(
                    "general_consumer_total_price_eur",
                    "499,90 EUR",
                    "Precio total 499,90 EUR",
                ),
                _observation(
                    "general_consumer_nonconformity_present",
                    "Sí",
                    "Consta falta de conformidad",
                ),
            ],
        )

        facts = normalize_document_packet(packet).facts.facts
        self.assertEqual(facts["empresario_consumo"].value, "Comercio Demo, S.L.")
        self.assertTrue(facts["consumidor_es_consumidor"].value)
        self.assertTrue(facts["empresario_consumo_es_empresario"].value)
        self.assertEqual(facts["fecha_compra_consumo"].value, "2026-05-01")
        self.assertEqual(facts["precio_total_consumo_eur"].value, 499.9)
        self.assertTrue(facts["falta_conformidad_consumo"].value)


if __name__ == "__main__":
    unittest.main()
