from __future__ import annotations

import unittest

from rtm_core.domain_catalog import family_profile
from rtm_core.document_fact_catalog import field_spec, registered_fact_keys
from rtm_core.document_normalization import (
    DocumentExtractionPacket,
    DocumentObservation,
    normalize_document_packet,
)
from rtm_core.specialist_dispatch import registered_specialists
from rtm_core.travel_agency_extension import TRAVEL_AGENCY_EXTENSION_VERSION
from rtm_core.travel_specialist_registry import (
    TRAVEL_SPECIALIST_REGISTRY_VERSION,
)
from rtm_core.versioning import build_version_snapshot


DOC_ID = "doc-travel-agency-extension"


def _observation(field: str, value, evidence: str) -> DocumentObservation:
    return DocumentObservation(
        field=field,
        value=value,
        document_id=DOC_ID,
        page_index=0,
        evidence=evidence,
        confidence=0.99,
        extraction_method="rtm_travel_agency_extension_test_v1",
        source_type="document_vision",
    )


class TravelAgencyExtensionTest(unittest.TestCase):
    def test_fact_aliases_capability_and_registry_are_installed(self):
        self.assertEqual(
            TRAVEL_AGENCY_EXTENSION_VERSION,
            "rtm_travel_agency_extension_v1_0",
        )
        self.assertEqual(
            TRAVEL_SPECIALIST_REGISTRY_VERSION,
            "rtm_travel_specialist_registry_v1_2",
        )
        self.assertEqual(
            field_spec("travel", "platform_role").key,
            "rol_agencia_plataforma",
        )
        self.assertEqual(
            field_spec("travel", "merchant_of_record").key,
            "cobrador_reserva",
        )
        self.assertEqual(
            field_spec("travel", "duplicate_charge").key,
            "cargo_duplicado_eur",
        )
        self.assertIn(
            "reserva_transmitida_proveedor",
            registered_fact_keys("travel"),
        )

        profile = family_profile("travel", "agencia_plataforma")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.specialist, "travel.agency")
        self.assertEqual(profile.capability, "specialist_ready")
        self.assertIn("travel.agency", registered_specialists())

    def test_agency_document_facts_are_typed_by_normalization(self):
        packet = DocumentExtractionPacket(
            case_id="case-travel-agency-extension",
            service="travel",
            extractor_version="travel_agency_document_test_v1",
            source_document_ids=[DOC_ID],
            observations=[
                _observation(
                    "platform_role",
                    "Intermediaria",
                    "Role: intermediary",
                ),
                _observation(
                    "platform_country",
                    "España",
                    "Established in Spain",
                ),
                _observation(
                    "booking_forwarded_to_supplier",
                    "No",
                    "Booking was not forwarded",
                ),
                _observation(
                    "online_marketplace",
                    "Sí",
                    "Online marketplace",
                ),
                _observation(
                    "duplicate_charge",
                    "125,50 EUR",
                    "Duplicate charge 125,50 EUR",
                ),
                _observation(
                    "refund_request_date",
                    "10/08/2026",
                    "Refund requested 10/08/2026",
                ),
            ],
        )

        facts = normalize_document_packet(packet).facts.facts
        self.assertEqual(facts["rol_agencia_plataforma"].value, "Intermediaria")
        self.assertEqual(facts["pais_agencia_plataforma"].value, "España")
        self.assertFalse(facts["reserva_transmitida_proveedor"].value)
        self.assertTrue(facts["mercado_en_linea"].value)
        self.assertEqual(facts["cargo_duplicado_eur"].value, 125.5)
        self.assertEqual(
            facts["fecha_solicitud_reembolso"].value,
            "2026-08-10",
        )

    def test_version_snapshot_audits_agency_components(self):
        snapshot = build_version_snapshot()
        expected = {
            "travel_agency_extension": "rtm_travel_agency_extension_v1_0",
            "travel_agency_regime": "rtm_travel_agency_regime_v1_0",
            "travel_agency_specialist": "rtm_travel_agency_specialist_v1_0",
            "travel_specialist_registry": "rtm_travel_specialist_registry_v1_2",
        }

        for name, version in expected.items():
            with self.subTest(component=name):
                component = snapshot["components"][name]
                self.assertEqual(component["declared"], version)
                self.assertEqual(component["runtime"], version)
                self.assertTrue(component["matches_declared"])
                self.assertIsNone(component["discovery_error"])
                self.assertEqual(snapshot["contracts"][name], version)


if __name__ == "__main__":
    unittest.main()
