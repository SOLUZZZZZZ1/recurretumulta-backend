from __future__ import annotations

import unittest

from rtm_core.domain_catalog import family_profile
from rtm_core.document_fact_catalog import field_spec, registered_fact_keys
from rtm_core.document_normalization import (
    DocumentExtractionPacket,
    DocumentObservation,
    normalize_document_packet,
)
from rtm_core.travel_insurance_extension import (
    TRAVEL_INSURANCE_EXTENSION_VERSION,
)
from rtm_core.versioning import build_version_snapshot


DOC_ID = "doc-travel-insurance-extension"


def _observation(field: str, value, evidence: str) -> DocumentObservation:
    return DocumentObservation(
        field=field,
        value=value,
        document_id=DOC_ID,
        page_index=0,
        evidence=evidence,
        confidence=0.99,
        extraction_method="rtm_travel_insurance_extension_test_v1",
        source_type="document_vision",
    )


class TravelInsuranceExtensionTest(unittest.TestCase):
    def test_fact_aliases_capability_and_versions_are_installed(self):
        self.assertEqual(
            TRAVEL_INSURANCE_EXTENSION_VERSION,
            "rtm_travel_insurance_extension_v1_0",
        )
        self.assertEqual(
            field_spec("travel", "insurance_company").key,
            "aseguradora_viaje",
        )
        self.assertEqual(
            field_spec("travel", "coverage_start_date").key,
            "fecha_inicio_cobertura",
        )
        self.assertEqual(
            field_spec("travel", "claim_amount_paid").key,
            "importe_pagado_aseguradora_eur",
        )
        self.assertIn("exclusion_invocada", registered_fact_keys("travel"))

        profile = family_profile("travel", "seguro_viaje")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.specialist, "travel.insurance")
        self.assertEqual(profile.capability, "specialist_ready")

        snapshot = build_version_snapshot()
        expected = {
            "travel_insurance_extension": "rtm_travel_insurance_extension_v1_0",
            "travel_insurance_regime": "rtm_travel_insurance_regime_v1_0",
            "travel_insurance_specialist": "rtm_travel_insurance_specialist_v1_0",
        }
        for name, version in expected.items():
            with self.subTest(component=name):
                component = snapshot["components"][name]
                self.assertEqual(component["declared"], version)
                self.assertEqual(component["runtime"], version)
                self.assertTrue(component["matches_declared"])
                self.assertIsNone(component["discovery_error"])
                self.assertEqual(snapshot["contracts"][name], version)

    def test_insurance_document_facts_are_typed_by_normalization(self):
        packet = DocumentExtractionPacket(
            case_id="case-travel-insurance-extension",
            service="travel",
            extractor_version="travel_insurance_document_test_v1",
            source_document_ids=[DOC_ID],
            observations=[
                _observation(
                    "insurance_company",
                    "Aseguradora Demo, S.A.",
                    "Insurance company: Aseguradora Demo, S.A.",
                ),
                _observation(
                    "coverage_start_date",
                    "01/08/2026",
                    "Coverage starts 01/08/2026",
                ),
                _observation(
                    "coverage_end_date",
                    "31/08/2026",
                    "Coverage ends 31/08/2026",
                ),
                _observation(
                    "claim_accepted",
                    "Sí",
                    "Claim accepted",
                ),
                _observation(
                    "claim_amount_paid",
                    "125,50 EUR",
                    "Claim amount paid 125,50 EUR",
                ),
            ],
        )

        facts = normalize_document_packet(packet).facts.facts
        self.assertEqual(
            facts["aseguradora_viaje"].value,
            "Aseguradora Demo, S.A.",
        )
        self.assertEqual(facts["fecha_inicio_cobertura"].value, "2026-08-01")
        self.assertEqual(facts["fecha_fin_cobertura"].value, "2026-08-31")
        self.assertTrue(facts["cobertura_aceptada"].value)
        self.assertEqual(facts["importe_pagado_aseguradora_eur"].value, 125.5)


if __name__ == "__main__":
    unittest.main()
