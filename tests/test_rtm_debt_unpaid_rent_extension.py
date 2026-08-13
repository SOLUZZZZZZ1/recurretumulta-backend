from __future__ import annotations

import unittest

from rtm_core.debt_unpaid_rent_extension import (
    DEBT_UNPAID_RENT_EXTENSION_VERSION,
)
from rtm_core.domain_catalog import family_profile
from rtm_core.document_fact_catalog import field_spec, registered_fact_keys
from rtm_core.document_normalization import (
    DocumentExtractionPacket,
    DocumentObservation,
    normalize_document_packet,
)
from rtm_core.versioning import build_version_snapshot


DOC_ID = "doc-debt-unpaid-rent-extension"


def _observation(field: str, value, evidence: str) -> DocumentObservation:
    return DocumentObservation(
        field=field,
        value=value,
        document_id=DOC_ID,
        page_index=0,
        evidence=evidence,
        confidence=0.99,
        extraction_method="rtm_debt_unpaid_rent_extension_test_v1",
        source_type="document_vision",
    )


class DebtUnpaidRentExtensionTest(unittest.TestCase):
    def test_fact_aliases_capability_and_versions_are_installed(self):
        self.assertEqual(
            DEBT_UNPAID_RENT_EXTENSION_VERSION,
            "rtm_debt_unpaid_rent_extension_v1_0",
        )
        self.assertEqual(
            field_spec("debt", "unpaid_rent_landlord_name").key,
            "arrendador",
        )
        self.assertEqual(
            field_spec("debt", "unpaid_rent_monthly_agreed_rent_eur").key,
            "renta_mensual_pactada_eur",
        )
        self.assertEqual(
            field_spec("debt", "unpaid_rent_masc_received_date").key,
            "masc_alquiler_fecha_recepcion",
        )
        self.assertIn("saldo_pendiente_alquiler_eur", registered_fact_keys("debt"))
        self.assertIn("alquiler_periodos_impagados", registered_fact_keys("debt"))

        profile = family_profile("debt", "alquiler_impagado")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.specialist, "debt.unpaid_rent")
        self.assertEqual(profile.capability, "specialist_ready")

        snapshot = build_version_snapshot()
        expected = {
            "debt_unpaid_rent_extension": "rtm_debt_unpaid_rent_extension_v1_0",
            "debt_unpaid_rent_regime": "rtm_debt_unpaid_rent_regime_v1_0",
            "debt_unpaid_rent_specialist": "rtm_debt_unpaid_rent_specialist_v1_0",
            "debt_specialist_registry": "rtm_debt_specialist_registry_v1_0",
        }
        for name, version in expected.items():
            with self.subTest(component=name):
                component = snapshot["components"][name]
                self.assertEqual(component["declared"], version)
                self.assertEqual(component["runtime"], version)
                self.assertTrue(component["matches_declared"])
                self.assertIsNone(component["discovery_error"])
                self.assertEqual(snapshot["contracts"][name], version)

    def test_rent_document_facts_are_typed_by_normalization(self):
        packet = DocumentExtractionPacket(
            case_id="case-debt-unpaid-rent-extension",
            service="debt",
            extractor_version="unpaid_rent_document_test_v1",
            source_document_ids=[DOC_ID],
            observations=[
                _observation(
                    "unpaid_rent_landlord_name",
                    "Propietaria Demo, S.L.",
                    "Arrendador: Propietaria Demo",
                ),
                _observation(
                    "unpaid_rent_contract_date",
                    "01/03/2026",
                    "Contrato de 01/03/2026",
                ),
                _observation(
                    "unpaid_rent_monthly_agreed_rent_eur",
                    "1.250,50 EUR",
                    "Renta mensual 1.250,50 EUR",
                ),
                _observation(
                    "unpaid_rent_month_count",
                    "3",
                    "Tres mensualidades pendientes",
                ),
                _observation(
                    "unpaid_rent_possession_recovery_requested",
                    "Sí",
                    "Se solicita recuperación de la posesión",
                ),
                _observation(
                    "unpaid_rent_masc_received_date",
                    "10/07/2026",
                    "Solicitud recibida el 10/07/2026",
                ),
            ],
        )

        facts = normalize_document_packet(packet).facts.facts
        self.assertEqual(facts["arrendador"].value, "Propietaria Demo, S.L.")
        self.assertEqual(facts["fecha_contrato_arrendamiento"].value, "2026-03-01")
        self.assertEqual(facts["renta_mensual_pactada_eur"].value, 1250.5)
        self.assertEqual(facts["mensualidades_impagadas_numero"].value, 3.0)
        self.assertTrue(facts["recuperacion_posesion_alquiler_solicitada"].value)
        self.assertEqual(facts["masc_alquiler_fecha_recepcion"].value, "2026-07-10")


if __name__ == "__main__":
    unittest.main()
