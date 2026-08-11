from __future__ import annotations

import unittest

from rtm_core.claims_professional_services_extension import (
    CLAIMS_PROFESSIONAL_SERVICES_EXTENSION_VERSION,
)
from rtm_core.domain_catalog import family_profile
from rtm_core.document_fact_catalog import field_spec, registered_fact_keys
from rtm_core.document_normalization import (
    DocumentExtractionPacket,
    DocumentObservation,
    normalize_document_packet,
)
from rtm_core.versioning import build_version_snapshot


DOC_ID = "doc-claims-professional-services-extension"


def _observation(field: str, value, evidence: str) -> DocumentObservation:
    return DocumentObservation(
        field=field,
        value=value,
        document_id=DOC_ID,
        page_index=0,
        evidence=evidence,
        confidence=0.99,
        extraction_method="rtm_claims_professional_services_extension_test_v1",
        source_type="document_vision",
    )


class ClaimsProfessionalServicesExtensionTest(unittest.TestCase):
    def test_fact_aliases_capability_and_versions_are_installed(self):
        self.assertEqual(
            CLAIMS_PROFESSIONAL_SERVICES_EXTENSION_VERSION,
            "rtm_claims_professional_services_extension_v1_0",
        )
        self.assertEqual(
            field_spec("claims", "professional_service_provider_name").key,
            "profesional_prestador",
        )
        self.assertEqual(
            field_spec("claims", "professional_engagement_date").key,
            "fecha_encargo_profesional",
        )
        self.assertEqual(
            field_spec("claims", "professional_agreed_price_eur").key,
            "precio_profesional_pactado_eur",
        )
        self.assertEqual(
            field_spec("claims", "professional_withdrawal_notice_date").key,
            "fecha_desistimiento_profesional",
        )
        self.assertIn(
            "perdida_oportunidad_profesional_invocada",
            registered_fact_keys("claims"),
        )

        profile = family_profile("claims", "servicios_profesionales")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.specialist, "claims.professional_services")
        self.assertEqual(profile.capability, "specialist_ready")

        snapshot = build_version_snapshot()
        expected = {
            "claims_professional_services_extension": (
                "rtm_claims_professional_services_extension_v1_0"
            ),
            "claims_professional_services_regime": (
                "rtm_claims_professional_services_regime_v1_0"
            ),
            "claims_professional_services_specialist": (
                "rtm_claims_professional_services_specialist_v1_0"
            ),
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

    def test_professional_document_facts_are_typed_by_normalization(self):
        packet = DocumentExtractionPacket(
            case_id="case-claims-professional-services-extension",
            service="claims",
            extractor_version="claims_professional_document_test_v1",
            source_document_ids=[DOC_ID],
            observations=[
                _observation(
                    "professional_service_provider_name",
                    "Consultoría Demo, S.L.",
                    "Provider: Consultoría Demo",
                ),
                _observation(
                    "professional_client_is_consumer",
                    "Sí",
                    "Client acts as consumer",
                ),
                _observation(
                    "professional_engagement_date",
                    "01/03/2026",
                    "Engagement date 01/03/2026",
                ),
                _observation(
                    "professional_agreed_price_eur",
                    "1.250,50 EUR",
                    "Agreed price 1.250,50 EUR",
                ),
                _observation(
                    "professional_service_incomplete",
                    "Sí",
                    "Service incomplete",
                ),
                _observation(
                    "professional_service_completion_percentage",
                    "60",
                    "Completion 60 percent",
                ),
            ],
        )

        facts = normalize_document_packet(packet).facts.facts
        self.assertEqual(
            facts["profesional_prestador"].value,
            "Consultoría Demo, S.L.",
        )
        self.assertTrue(facts["cliente_servicio_es_consumidor"].value)
        self.assertEqual(facts["fecha_encargo_profesional"].value, "2026-03-01")
        self.assertEqual(facts["precio_profesional_pactado_eur"].value, 1250.5)
        self.assertTrue(facts["servicio_profesional_incompleto"].value)
        self.assertEqual(
            facts["porcentaje_servicio_profesional_ejecutado"].value,
            60.0,
        )


if __name__ == "__main__":
    unittest.main()
