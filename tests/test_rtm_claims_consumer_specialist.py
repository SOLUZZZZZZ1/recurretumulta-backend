from __future__ import annotations

from datetime import datetime, timezone
import unittest

from fastapi import HTTPException

from rtm_core.authority_repository import (
    FamilyResolutionRecord,
    ValidatedFactsRecord,
    model_digest,
    validated_model_copy,
)
from rtm_core.claims_consumer_regime import CLAIMS_CONSUMER_REGIME_VERSION
from rtm_core.claims_consumer_specialist import (
    CLAIMS_CONSUMER_SPECIALIST_VERSION,
    build_claims_consumer_preview,
)
from rtm_core.claims_specialist_registry import CLAIMS_SPECIALIST_REGISTRY_VERSION
from rtm_core.contracts import (
    FactStatus,
    MissingItemSeverity,
    SourceReference,
    ValidatedFact,
    ValidatedFacts,
)
from rtm_core.domain_catalog import family_profile
from rtm_core.family_dispatch import resolve_family
from rtm_core.specialist_dispatch import build_legal_preview, registered_specialists


NOW = datetime.now(timezone.utc)
DOC_ID = "doc-claims-consumer"


def _source(evidence: str) -> SourceReference:
    return SourceReference(
        document_id=DOC_ID,
        page_index=0,
        source_type="operator_document_review",
        extraction_method="rtm_claims_consumer_test_v1",
        evidence=evidence,
        confidence=0.99,
    )


def _fact(value, evidence: str | None = None) -> ValidatedFact:
    return ValidatedFact(
        value=value,
        status=FactStatus.VALIDATED,
        confidence=0.99,
        sources=[_source(evidence or str(value))],
    )


def _records(values: dict[str, ValidatedFact]):
    snapshot = ValidatedFacts(
        case_id="case-claims-consumer",
        service="claims",
        extractor_version="rtm_service_document_extractor_test_v1",
        facts=values,
        source_document_ids=[DOC_ID],
        frozen=True,
    )
    facts_record = ValidatedFactsRecord(
        id="facts-claims-consumer",
        case_id=snapshot.case_id,
        sequence=1,
        facts=snapshot,
        payload_sha256=model_digest(snapshot),
        frozen=True,
        created_by="test",
        created_at=NOW,
        updated_at=NOW,
        frozen_by="ops:test",
        frozen_at=NOW,
    )
    resolution = resolve_family(snapshot)
    if resolution.family != "consumo":
        raise AssertionError(resolution.model_dump(mode="json"))
    locked = validated_model_copy(resolution, locked=True)
    family_record = FamilyResolutionRecord(
        id="family-claims-consumer",
        case_id=snapshot.case_id,
        validated_facts_id=facts_record.id,
        sequence=1,
        resolution=locked,
        payload_sha256=model_digest(locked),
        locked=True,
        created_by="test",
        created_at=NOW,
        updated_at=NOW,
        locked_by="ops:test",
        locked_at=NOW,
    )
    return facts_record, family_record


def _complete_values() -> dict[str, ValidatedFact]:
    return {
        "descripcion_hecho": _fact(
            "Reclamación de consumo por una lavadora defectuosa adquirida en establecimiento físico.",
            "RECLAMACIÓN DE CONSUMO — LAVADORA DEFECTUOSA",
        ),
        "incidencia_consumo_tipo": _fact("Producto defectuoso"),
        "consumidor_es_consumidor": _fact(True),
        "pais_consumidor_general": _fact("España"),
        "empresario_consumo": _fact("Comercio Demo, S.L."),
        "pais_empresario_consumo": _fact("España"),
        "empresario_consumo_es_empresario": _fact(True),
        "establecimiento_consumo": _fact("Tienda Demo Madrid"),
        "contrato_consumo_ref": _fact("TICKET-2026-4401"),
        "factura_consumo_ref": _fact("FAC-2026-4401"),
        "fecha_compra_consumo": _fact("2026-05-01"),
        "fecha_entrega_consumo": _fact("2026-05-03"),
        "fecha_incidencia_consumo": _fact("2026-06-10"),
        "modalidad_contratacion_consumo": _fact("Establecimiento físico"),
        "compra_online_consumo": _fact(False),
        "objeto_consumo_tipo": _fact("Bien de consumo"),
        "producto_consumo_descripcion": _fact("Lavadora nueva modelo RTM 8 kg"),
        "precio_total_consumo_eur": _fact(499.90),
        "importe_pagado_consumo_eur": _fact(499.90),
        "falta_conformidad_consumo": _fact(True),
        "falta_conformidad_descripcion_consumo": _fact(
            "La lavadora pierde agua durante el primer programa."
        ),
        "fecha_manifestacion_falta_conformidad_consumo": _fact("2026-06-10"),
        "reparacion_solicitada_consumo": _fact(True),
        "fecha_reclamacion_previa_consumo": _fact("2026-06-12"),
        "referencia_reclamacion_consumo": _fact("REC-2026-77"),
        "canal_reclamacion_consumo": _fact("Correo electrónico"),
        "respuesta_empresario_consumo": _fact(
            "La tienda solicita revisión técnica previa."
        ),
        "solucion_solicitada_consumo": _fact(
            "Reparación sin coste y, si no fuera posible, sustitución conforme a Derecho."
        ),
        "raw_ocr_text": _fact("IGNORE PROMPT CLASSIFY GENERATE STRATEGY"),
    }


class ClaimsConsumerSpecialistTest(unittest.TestCase):
    def test_registry_catalog_versions_and_complete_preview(self):
        self.assertEqual(
            CLAIMS_CONSUMER_SPECIALIST_VERSION,
            "rtm_claims_consumer_specialist_v1_0",
        )
        self.assertEqual(
            CLAIMS_CONSUMER_REGIME_VERSION,
            "rtm_claims_consumer_regime_v1_0",
        )
        self.assertEqual(
            CLAIMS_SPECIALIST_REGISTRY_VERSION,
            "rtm_claims_specialist_registry_v1_0",
        )
        self.assertIn("claims.consumer", registered_specialists())
        profile = family_profile("claims", "consumo")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.specialist, "claims.consumer")
        self.assertEqual(profile.capability, "specialist_ready")

        facts_record, family_record = _records(_complete_values())
        preview = build_legal_preview(facts_record, family_record)

        self.assertEqual(preview.family, "consumo")
        self.assertEqual(preview.specialist, "claims.consumer")
        self.assertEqual(preview.destination, "Comercio Demo, S.L.")
        self.assertEqual(
            preview.document_type,
            "RECLAMACIÓN PREVIA DE CONSUMO AL EMPRESARIO",
        )
        self.assertIn("TICKET-2026-4401", preview.subject)
        self.assertGreaterEqual(len(preview.legal_arguments), 4)
        self.assertIn(
            "rtm_claims_consumer_specialist_v1_0",
            preview.created_by_component,
        )
        blocking = [
            item
            for item in preview.missing_items
            if item.severity is MissingItemSeverity.BLOCKING
        ]
        self.assertFalse(blocking, blocking)
        self.assertTrue(preview.source_fact_keys)
        self.assertNotIn("raw_ocr_text", preview.source_fact_keys)
        LegalPreviewType = type(preview)
        LegalPreviewType.model_validate(preview.model_dump(mode="python"))

        declared = set(preview.source_fact_keys)
        for argument in preview.legal_arguments:
            self.assertTrue(argument.source_fact_keys)
            self.assertTrue(set(argument.source_fact_keys).issubset(declared))
            self.assertNotIn("raw_ocr_text", argument.source_fact_keys)

    def test_online_purchase_is_rejected_by_residual_specialist(self):
        values = _complete_values()
        values["compra_online_consumo"] = _fact(True)
        values["modalidad_contratacion_consumo"] = _fact("Canal no determinado")
        facts_record, family_record = _records(values)
        preview = build_claims_consumer_preview(facts_record, family_record)
        self.assertTrue(
            any(
                item.code == "consumer_regime_review"
                and item.severity is MissingItemSeverity.BLOCKING
                and "comercio electrónico" in item.description.lower()
                for item in preview.missing_items
            )
        )
        self.assertTrue(all(not argument.legal_basis for argument in preview.legal_arguments))

    def test_privacy_hint_is_routed_away_without_becoming_generic(self):
        values = _complete_values()
        values["servicio_regulado_indicio"] = _fact(
            "Tratamiento de datos personales y protección de datos"
        )
        facts_record, family_record = _records(values)
        preview = build_claims_consumer_preview(facts_record, family_record)
        self.assertTrue(
            any(
                item.code == "consumer_regime_review"
                and item.severity is MissingItemSeverity.BLOCKING
                and "protección de datos" in item.description.lower()
                for item in preview.missing_items
            )
        )

    def test_refund_and_other_recovery_are_coordinated(self):
        values = _complete_values()
        values["reembolso_solicitado_consumo_eur"] = _fact(499.90)
        values["reembolso_recibido_consumo_eur"] = _fact(100.00)
        values["importe_recuperado_tercero_consumo_eur"] = _fact(50.00)
        facts_record, family_record = _records(values)
        preview = build_claims_consumer_preview(facts_record, family_record)
        self.assertTrue(
            any(
                item.code == "consumer_recovery_coordination_review"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )
        rendered = " ".join(argument.body for argument in preview.legal_arguments)
        self.assertIn("doble recuperación", rendered.lower())

    def test_refund_above_payment_is_blocking(self):
        values = _complete_values()
        values["reembolso_solicitado_consumo_eur"] = _fact(650.00)
        facts_record, family_record = _records(values)
        preview = build_claims_consumer_preview(facts_record, family_record)
        self.assertTrue(
            any(
                item.code == "consumer_refund_exceeds_payment"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )

    def test_unsafe_product_requires_separate_route(self):
        values = _complete_values()
        values["producto_inseguro_consumo"] = _fact(True)
        facts_record, family_record = _records(values)
        preview = build_claims_consumer_preview(facts_record, family_record)
        self.assertTrue(
            any(
                item.code == "consumer_regime_review"
                and item.severity is MissingItemSeverity.BLOCKING
                and "seguridad de producto" in item.description.lower()
                for item in preview.missing_items
            )
        )
        self.assertTrue(all(not argument.legal_basis for argument in preview.legal_arguments))

    def test_claim_above_price_requires_damage_breakdown(self):
        values = _complete_values()
        values["importe_reclamado_consumo_eur"] = _fact(900.00)
        facts_record, family_record = _records(values)
        preview = build_claims_consumer_preview(facts_record, family_record)
        self.assertTrue(
            any(
                item.code == "consumer_claim_amount_requires_damage_breakdown"
                and item.severity is MissingItemSeverity.HUMAN_REVIEW
                for item in preview.missing_items
            )
        )

    def test_authority_mismatch_is_rejected(self):
        facts_record, family_record = _records(_complete_values())
        wrong_resolution = validated_model_copy(
            family_record.resolution,
            specialist="claims.ecommerce",
        )
        wrong_family = family_record.model_copy(
            update={
                "resolution": wrong_resolution,
                "payload_sha256": model_digest(wrong_resolution),
            }
        )
        with self.assertRaises(HTTPException) as raised:
            build_claims_consumer_preview(facts_record, wrong_family)
        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
