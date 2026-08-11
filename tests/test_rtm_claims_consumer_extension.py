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
            field_spec("claims", "general_consumer_business_name").key,
            "empresa_consumo",
        )
        self.assertEqual(
            field_spec("claims", "general_consumer_contract_date").key,
            "fecha_contrato_consumo",
        )
        self.assertEqual(
            field_spec("claims", "general_consumer_charged_price_eur").key,
            "precio_cobrado_consumo_eur",
        )
        self.assertEqual(
            field_spec("claims", "general_consumer_nonconformity_manifestation_date").key,
            "fecha_manifestacion_falta_conformidad_consumo",
        )
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

    def test_consumer_document_facts_are_typed_by_normalization(self):
        packet = DocumentExtractionPacket(
            case_id="case-claims-consumer-extension",
            service="claims",
            extractor_version="claims_consumer_document_test_v1",
            source_document_ids=[DOC_ID],
            observations=[
                _observation(
                    "general_consumer_business_name",
                    "Comercio Demo, S.L.",
                    "Business: Comercio Demo",
                ),
                _observation(
                    "general_consumer_client_is_consumer",
                    "Sí",
                    "Client acts as consumer",
                ),
                _observation(
                    "general_consumer_contract_date",
                    "01/03/2026",
                    "Contract date 01/03/2026",
                ),
                _observation(
                    "general_consumer_charged_price_eur",
                    "899,95 EUR",
                    "Charged price 899,95 EUR",
                ),
                _observation(
                    "general_consumer_new_goods",
                    "Sí",
                    "New goods",
                ),
                _observation(
                    "general_consumer_service_completion_percentage",
                    "60",
                    "Completion 60 percent",
                ),
            ],
        )

        facts = normalize_document_packet(packet).facts.facts
        self.assertEqual(facts["empresa_consumo"].value, "Comercio Demo, S.L.")
        self.assertTrue(facts["cliente_consumo_es_consumidor"].value)
        self.assertEqual(facts["fecha_contrato_consumo"].value, "2026-03-01")
        self.assertEqual(facts["precio_cobrado_consumo_eur"].value, 899.95)
        self.assertTrue(facts["bien_nuevo_consumo"].value)
        self.assertEqual(facts["porcentaje_servicio_consumo_ejecutado"].value, 60.0)


if __name__ == "__main__":
    unittest.main()
