from __future__ import annotations

import unittest

from rtm_core.document_extraction import (
    ProviderDocumentResult,
    ProviderObservation,
)
from rtm_core.staging_validation_semantics import (
    SEMANTIC_STAGING_VALIDATION_VERSION,
    run_semantic_synthetic_staging_suite,
    semantic_staging_scenarios,
)


class _ClaimsExtensionVocabularyProvider:
    version = "rtm_ci_claims_extension_provider_v1"
    model = "deterministic-claims-extension"

    @staticmethod
    def _observation(field, value, evidence):
        return ProviderObservation(
            field=field,
            value=value,
            page_index=0,
            evidence=evidence,
            confidence=0.99,
            notes=[],
        )

    def extract_document(self, *, service, document, content):
        if service != "claims":
            raise AssertionError("Esta prueba solo admite el escenario claims")

        observations = [
            self._observation(
                "empresa_consumo",
                "OPERADOR TELECOM DEMO",
                "Proveedor: OPERADOR TELECOM DEMO",
            ),
            self._observation(
                "producto_servicio_consumo",
                "Servicio de fibra e internet",
                "Servicio contratado: fibra e internet",
            ),
            self._observation(
                "contrato_consumo_ref",
                "CTR-TEL-2026-001",
                "Contrato: CTR-TEL-2026-001",
            ),
            self._observation(
                "baja_solicitada_fecha",
                "15/06/2026",
                "Fecha de solicitud de baja: 15/06/2026",
            ),
            self._observation(
                "fecha_baja_efectiva",
                "30/06/2026",
                "Fecha de baja efectiva: 30/06/2026",
            ),
            self._observation(
                "importe_pagado_consumo_eur",
                "79,90 EUR",
                "Importe pagado tras la baja: 79,90 EUR",
            ),
            self._observation(
                "solucion_solicitada_consumo",
                "Devolución del cobro posterior a la baja",
                "Se solicita la devolución del cobro",
            ),
        ]
        return (
            ProviderDocumentResult(
                observations=observations,
                unresolved_fields=[
                    "descripcion_hecho",
                    "factura_ticket_consumo_ref",
                ],
                quality_flags=[],
                document_notes=[],
            ),
            "document_text",
            [],
        )


class SemanticStagingValidationTest(unittest.TestCase):
    def test_claims_profile_accepts_registered_consumer_equivalents(self):
        report = run_semantic_synthetic_staging_suite(
            provider=_ClaimsExtensionVocabularyProvider(),
            selected_services=["claims"],
        )

        self.assertEqual(
            SEMANTIC_STAGING_VALIDATION_VERSION,
            "rtm_synthetic_staging_validation_v1_3",
        )
        self.assertEqual(report.version, SEMANTIC_STAGING_VALIDATION_VERSION)
        self.assertTrue(report.passed, report.model_dump(mode="json"))
        self.assertEqual(len(report.scenarios), 1)

        result = report.scenarios[0]
        self.assertTrue(result.passed, result.errors)
        self.assertEqual(result.family_status, "resolved")
        self.assertEqual(result.family, "telecomunicaciones")
        self.assertEqual(result.specialist, "claims.telecommunications")
        self.assertGreaterEqual(result.family_confidence, 0.90)
        self.assertEqual(result.direction_source, "core_projection")
        self.assertEqual(result.direction_maturity, "orientation_only")
        self.assertFalse(result.generation_allowed)
        self.assertIn("importe_pagado_consumo_eur", result.accepted_fields)
        self.assertIn("descripcion_hecho", result.unresolved_fields)
        self.assertIn("factura_ticket_consumo_ref", result.unresolved_fields)

    def test_semantic_profiles_match_live_extractor_vocabulary(self):
        scenarios = semantic_staging_scenarios()
        claims = next(item for item in scenarios if item.service == "claims")
        debt = next(item for item in scenarios if item.service == "debt")
        administration = next(
            item for item in scenarios if item.service == "administration"
        )

        self.assertFalse(claims.required_fields)
        self.assertIn(
            ("proveedor", "empresa_consumo"),
            claims.required_any_groups,
        )
        self.assertTrue(
            any(
                "importe_pagado_consumo_eur" in group
                for group in claims.required_any_groups
            )
        )

        self.assertEqual(debt.required_fields, ("factura_numero",))
        self.assertIn(
            ("descripcion_hecho", "concepto_deuda"),
            debt.required_any_groups,
        )
        self.assertIn(
            ("importe_deuda_eur", "saldo_pendiente_eur"),
            debt.required_any_groups,
        )
        self.assertIn(("fecha_vencimiento",), debt.required_any_groups)

        self.assertEqual(
            administration.required_fields,
            ("descripcion_hecho", "expediente_ref"),
        )
        self.assertEqual(
            administration.required_any_groups,
            (("importe_exigido_eur", "principal_eur"),),
        )


if __name__ == "__main__":
    unittest.main()
