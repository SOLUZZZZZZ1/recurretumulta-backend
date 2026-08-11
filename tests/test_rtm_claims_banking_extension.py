from __future__ import annotations

import unittest

from rtm_core.claims_banking_extension import CLAIMS_BANKING_EXTENSION_VERSION
from rtm_core.domain_catalog import family_profile
from rtm_core.document_fact_catalog import field_spec, registered_fact_keys
from rtm_core.document_normalization import (
    DocumentExtractionPacket,
    DocumentObservation,
    normalize_document_packet,
)
from rtm_core.versioning import build_version_snapshot


DOC_ID = "doc-claims-banking-extension"


def _observation(field: str, value, evidence: str) -> DocumentObservation:
    return DocumentObservation(
        field=field,
        value=value,
        document_id=DOC_ID,
        page_index=0,
        evidence=evidence,
        confidence=0.99,
        extraction_method="rtm_claims_banking_extension_test_v1",
        source_type="document_vision",
    )


class ClaimsBankingExtensionTest(unittest.TestCase):
    def test_fact_aliases_capability_and_versions_are_installed(self):
        self.assertEqual(
            CLAIMS_BANKING_EXTENSION_VERSION,
            "rtm_claims_banking_extension_v1_0",
        )
        self.assertEqual(
            field_spec("claims", "bank_name").key,
            "entidad_bancaria",
        )
        self.assertEqual(
            field_spec("claims", "account_iban").key,
            "cuenta_iban",
        )
        self.assertEqual(
            field_spec("claims", "payment_operation_date").key,
            "fecha_operacion_pago",
        )
        self.assertEqual(
            field_spec("claims", "verification_of_payee_performed").key,
            "verificacion_beneficiario_realizada",
        )
        self.assertIn("operacion_autorizada", registered_fact_keys("claims"))

        profile = family_profile("claims", "banca")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.specialist, "claims.banking")
        self.assertEqual(profile.capability, "specialist_ready")

        snapshot = build_version_snapshot()
        expected = {
            "claims_banking_extension": "rtm_claims_banking_extension_v1_0",
            "claims_banking_regime": "rtm_claims_banking_regime_v1_0",
            "claims_banking_specialist": "rtm_claims_banking_specialist_v1_0",
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

    def test_banking_document_facts_are_typed_by_normalization(self):
        packet = DocumentExtractionPacket(
            case_id="case-claims-banking-extension",
            service="claims",
            extractor_version="claims_banking_document_test_v1",
            source_document_ids=[DOC_ID],
            observations=[
                _observation(
                    "bank_name",
                    "Banco Demo, S.A.",
                    "Bank: Banco Demo, S.A.",
                ),
                _observation(
                    "account_iban",
                    "ES9121000418450200051332",
                    "IBAN ES9121000418450200051332",
                ),
                _observation(
                    "payment_operation_date",
                    "10/08/2026",
                    "Transaction date 10/08/2026",
                ),
                _observation(
                    "transaction_amount_eur",
                    "850,50 EUR",
                    "Transaction amount 850,50 EUR",
                ),
                _observation(
                    "transaction_authorized",
                    "No",
                    "Transaction not authorized",
                ),
                _observation(
                    "verification_of_payee_performed",
                    "Sí",
                    "Verification of payee performed",
                ),
            ],
        )

        facts = normalize_document_packet(packet).facts.facts
        self.assertEqual(facts["entidad_bancaria"].value, "Banco Demo, S.A.")
        self.assertEqual(facts["cuenta_iban"].value, "ES9121000418450200051332")
        self.assertEqual(facts["fecha_operacion_pago"].value, "2026-08-10")
        self.assertEqual(facts["importe_operacion_pago_eur"].value, 850.5)
        self.assertFalse(facts["operacion_autorizada"].value)
        self.assertTrue(facts["verificacion_beneficiario_realizada"].value)


if __name__ == "__main__":
    unittest.main()
