from __future__ import annotations

import unittest

from rtm_core.claims_insurance_extension import CLAIMS_INSURANCE_EXTENSION_VERSION
from rtm_core.domain_catalog import family_profile
from rtm_core.document_fact_catalog import field_spec, registered_fact_keys
from rtm_core.document_normalization import (
    DocumentExtractionPacket,
    DocumentObservation,
    normalize_document_packet,
)
from rtm_core.versioning import build_version_snapshot


DOC_ID = "doc-claims-insurance-extension"


def _observation(field: str, value, evidence: str) -> DocumentObservation:
    return DocumentObservation(
        field=field,
        value=value,
        document_id=DOC_ID,
        page_index=0,
        evidence=evidence,
        confidence=0.99,
        extraction_method="rtm_claims_insurance_extension_test_v1",
        source_type="document_vision",
    )


class ClaimsInsuranceExtensionTest(unittest.TestCase):
    def test_fact_aliases_capability_and_versions_are_installed(self):
        self.assertEqual(
            CLAIMS_INSURANCE_EXTENSION_VERSION,
            "rtm_claims_insurance_extension_v1_0",
        )
        self.assertEqual(
            field_spec("claims", "general_insurer_name").key,
            "aseguradora_general",
        )
        self.assertEqual(
            field_spec("claims", "general_policy_number").key,
            "poliza_seguro_ref",
        )
        self.assertEqual(
            field_spec("claims", "general_insurance_notice_date").key,
            "fecha_comunicacion_siniestro_seguro",
        )
        self.assertEqual(
            field_spec("claims", "general_insurer_offer_amount_eur").key,
            "importe_ofertado_aseguradora_eur",
        )
        self.assertIn("seguro_concurrente", registered_fact_keys("claims"))

        profile = family_profile("claims", "seguros")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.specialist, "claims.insurance")
        self.assertEqual(profile.capability, "specialist_ready")

        snapshot = build_version_snapshot()
        expected = {
            "claims_insurance_extension": "rtm_claims_insurance_extension_v1_0",
            "claims_insurance_regime": "rtm_claims_insurance_regime_v1_0",
            "claims_insurance_specialist": "rtm_claims_insurance_specialist_v1_0",
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

    def test_insurance_document_facts_are_typed_by_normalization(self):
        packet = DocumentExtractionPacket(
            case_id="case-claims-insurance-extension",
            service="claims",
            extractor_version="claims_insurance_document_test_v1",
            source_document_ids=[DOC_ID],
            observations=[
                _observation(
                    "general_insurer_name",
                    "Aseguradora Demo, S.A.",
                    "Insurer: Aseguradora Demo, S.A.",
                ),
                _observation(
                    "general_policy_number",
                    "HOG-2026-7711",
                    "Policy HOG-2026-7711",
                ),
                _observation(
                    "general_policy_coverage_start",
                    "01/01/2026",
                    "Coverage starts 01/01/2026",
                ),
                _observation(
                    "general_insurance_loss_date",
                    "10/07/2026",
                    "Loss date 10/07/2026",
                ),
                _observation(
                    "general_insurance_claimed_amount_eur",
                    "2450,00 EUR",
                    "Claimed amount 2450,00 EUR",
                ),
                _observation(
                    "general_limiting_clause_highlighted",
                    "No",
                    "Limiting clause not highlighted",
                ),
            ],
        )

        facts = normalize_document_packet(packet).facts.facts
        self.assertEqual(facts["aseguradora_general"].value, "Aseguradora Demo, S.A.")
        self.assertEqual(facts["poliza_seguro_ref"].value, "HOG-2026-7711")
        self.assertEqual(facts["fecha_inicio_cobertura_seguro"].value, "2026-01-01")
        self.assertEqual(facts["fecha_siniestro_seguro"].value, "2026-07-10")
        self.assertEqual(facts["importe_reclamado_seguro_eur"].value, 2450.0)
        self.assertFalse(facts["clausula_limitativa_destacada"].value)


if __name__ == "__main__":
    unittest.main()
