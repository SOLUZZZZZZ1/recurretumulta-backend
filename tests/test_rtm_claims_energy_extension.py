from __future__ import annotations

import unittest

from rtm_core.claims_energy_extension import CLAIMS_ENERGY_EXTENSION_VERSION
from rtm_core.domain_catalog import family_profile
from rtm_core.document_fact_catalog import field_spec, registered_fact_keys
from rtm_core.document_normalization import (
    DocumentExtractionPacket,
    DocumentObservation,
    normalize_document_packet,
)
from rtm_core.versioning import build_version_snapshot


DOC_ID = "doc-claims-energy-extension"


def _observation(field: str, value, evidence: str) -> DocumentObservation:
    return DocumentObservation(
        field=field,
        value=value,
        document_id=DOC_ID,
        page_index=0,
        evidence=evidence,
        confidence=0.99,
        extraction_method="rtm_claims_energy_extension_test_v1",
        source_type="document_vision",
    )


class ClaimsEnergyExtensionTest(unittest.TestCase):
    def test_fact_aliases_capability_and_versions_are_installed(self):
        self.assertEqual(
            CLAIMS_ENERGY_EXTENSION_VERSION,
            "rtm_claims_energy_extension_v1_0",
        )
        self.assertEqual(
            field_spec("claims", "electricity_supplier").key,
            "comercializadora_energia",
        )
        self.assertEqual(
            field_spec("claims", "cups_code").key,
            "cups",
        )
        self.assertEqual(
            field_spec("claims", "billing_period_start").key,
            "periodo_facturacion_inicio",
        )
        self.assertEqual(
            field_spec("claims", "energy_invoice_amount").key,
            "importe_factura_energia_eur",
        )
        self.assertIn("consumidor_vulnerable", registered_fact_keys("claims"))

        profile = family_profile("claims", "energia")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.specialist, "claims.energy")
        self.assertEqual(profile.capability, "specialist_ready")

        snapshot = build_version_snapshot()
        expected = {
            "claims_energy_extension": "rtm_claims_energy_extension_v1_0",
            "claims_energy_regime": "rtm_claims_energy_regime_v1_0",
            "claims_energy_specialist": "rtm_claims_energy_specialist_v1_0",
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

    def test_energy_document_facts_are_typed_by_normalization(self):
        packet = DocumentExtractionPacket(
            case_id="case-claims-energy-extension",
            service="claims",
            extractor_version="claims_energy_document_test_v1",
            source_document_ids=[DOC_ID],
            observations=[
                _observation(
                    "electricity_supplier",
                    "Comercializadora Demo, S.A.",
                    "Electricity supplier: Comercializadora Demo, S.A.",
                ),
                _observation(
                    "cups_code",
                    "ES0021000000000001AB",
                    "CUPS ES0021000000000001AB",
                ),
                _observation(
                    "billing_period_start",
                    "01/07/2026",
                    "Billing period starts 01/07/2026",
                ),
                _observation(
                    "billing_period_end",
                    "31/07/2026",
                    "Billing period ends 31/07/2026",
                ),
                _observation(
                    "energy_invoice_amount",
                    "95,40 EUR",
                    "Invoice amount 95,40 EUR",
                ),
                _observation(
                    "supply_disconnected",
                    "No",
                    "Supply not disconnected",
                ),
            ],
        )

        facts = normalize_document_packet(packet).facts.facts
        self.assertEqual(
            facts["comercializadora_energia"].value,
            "Comercializadora Demo, S.A.",
        )
        self.assertEqual(facts["cups"].value, "ES0021000000000001AB")
        self.assertEqual(
            facts["periodo_facturacion_inicio"].value,
            "2026-07-01",
        )
        self.assertEqual(
            facts["periodo_facturacion_fin"].value,
            "2026-07-31",
        )
        self.assertEqual(facts["importe_factura_energia_eur"].value, 95.4)
        self.assertFalse(facts["corte_suministro"].value)


if __name__ == "__main__":
    unittest.main()
