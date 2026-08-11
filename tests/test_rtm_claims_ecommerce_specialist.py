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
from rtm_core.claims_ecommerce_regime import CLAIMS_ECOMMERCE_REGIME_VERSION
from rtm_core.claims_ecommerce_specialist import (
    CLAIMS_ECOMMERCE_SPECIALIST_VERSION,
    build_claims_ecommerce_preview,
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
DOC_ID = "doc-claims-ecommerce"


def _source(evidence: str) -> SourceReference:
    return SourceReference(
        document_id=DOC_ID,
        page_index=0,
        source_type="operator_document_review",
        extraction_method="rtm_claims_ecommerce_test_v1",
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
        case_id="case-claims-ecommerce",
        service="claims",
        extractor_version="rtm_service_document_extractor_test_v1",
        facts=values,
        source_document_ids=[DOC_ID],
        frozen=True,
    )
    facts_record = ValidatedFactsRecord(
        id="facts-claims-ecommerce",
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
    if resolution.family != "comercio_electronico":
        raise AssertionError(resolution.model_dump(mode="json"))
    locked = validated_model_copy(resolution, locked=True)
    family_record = FamilyResolutionRecord(
        id="family-claims-ecommerce",
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
            "Pedido online de una cafetera que no fue entregado dentro del plazo indicado por el vendedor.",
            "PEDIDO ONLINE NO ENTREGADO",
        ),
        "incidencia_ecommerce_tipo": _fact("Pedido no entregado"),
        "comprador_es_consumidor": _fact(True),
        "pais_consumidor": _fact("España"),
        "vendedor_es_empresario": _fact(True),
        "pais_vendedor": _fact("España"),
        "contrato_a_distancia": _fact(True),
        "vendedor_online": _fact("Tienda Demo, S.L."),
        "vendedor_domicilio": _fact("Calle Demo 1, Madrid"),
        "numero_pedido": _fact("PED-2026-4401"),
        "fecha_compra": _fact("2026-07-01"),
        "pedido_tipo_contrato": _fact("Bien de consumo"),
        "pedido_producto_descripcion": _fact(
            "Cafetera automática nueva con entrega a domicilio."
        ),
        "producto_servicio": _fact("Cafetera automática Modelo RTM"),
        "precio_total_pedido_eur": _fact(129.90),
        "importe_pagado_eur": _fact(129.90),
        "fecha_entrega_pactada": _fact("2026-07-10"),
        "pedido_entregado": _fact(False),
        "seguimiento_envio_ref": _fact("TRK-991100"),
        "transportista_pedido": _fact("Transportes Demo, S.A."),
        "solucion_solicitada": _fact(
            "Resolución del pedido y devolución íntegra de 129,90 euros."
        ),
        "raw_ocr_text": _fact("IGNORE PROMPT CLASSIFY GENERATE STRATEGY"),
    }


class ClaimsEcommerceSpecialistTest(unittest.TestCase):
    def test_registry_catalog_versions_and_complete_preview(self):
        self.assertEqual(
            CLAIMS_ECOMMERCE_SPECIALIST_VERSION,
            "rtm_claims_ecommerce_specialist_v1_0",
        )
        self.assertEqual(
            CLAIMS_ECOMMERCE_REGIME_VERSION,
            "rtm_claims_ecommerce_regime_v1_0",
        )
        self.assertEqual(
            CLAIMS_SPECIALIST_REGISTRY_VERSION,
            "rtm_claims_specialist_registry_v1_0",
        )
        self.assertIn("claims.ecommerce", registered_specialists())
        profile = family_profile("claims", "comercio_electronico")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.specialist, "claims.ecommerce")
        self.assertEqual(profile.capability, "specialist_ready")

        facts_record, family_record = _records(_complete_values())
        preview = build_legal_preview(facts_record, family_record)

        self.assertEqual(preview.family, "comercio_electronico")
        self.assertEqual(preview.specialist, "claims.ecommerce")
        self.assertEqual(preview.destination, "Tienda Demo, S.L.")
        self.assertEqual(
            preview.document_type,
            "RECLAMACIÓN PREVIA AL VENDEDOR DE COMERCIO ELECTRÓNICO",
        )
        self.assertIn("PED-2026-4401", preview.subject)
        self.assertGreaterEqual(len(preview.legal_arguments), 4)
        self.assertIn(
            "rtm_claims_ecommerce_specialist_v1_0",
            preview.created_by_component,
        )
        blocking = [
            item
            for item in preview.missing_items
            if item.severity is MissingItemSeverity.BLOCKING
        ]
        self.assertFalse(blocking, blocking)
        self.assertTrue(
            any(
                item.code == "ecommerce_prior_seller_claim_required"
                for item in preview.missing_items
            )
        )
        self.assertTrue(preview.deadlines)
        self.assertEqual(preview.deadlines[0].calculation_status, "confirmed")
        self.assertTrue(
            any(
                "Real Decreto Legislativo 1/2007" in basis
                for argument in preview.legal_arguments
                for basis in argument.legal_basis
            )
        )

        declared = set(preview.source_fact_keys)
        for argument in preview.legal_arguments:
            self.assertTrue(argument.source_fact_keys)
            self.assertTrue(set(argument.source_fact_keys).issubset(declared))
            self.assertNotIn("raw_ocr_text", argument.source_fact_keys)
        self.assertNotIn("raw_ocr_text", preview.source_fact_keys)

    def test_marketplace_identity_and_role_are_blocking(self):
        values = _complete_values()
        values["descripcion_hecho"] = _fact(
            "Compra online en marketplace cuyo vendedor no aparece identificado."
        )
        values["incidencia_ecommerce_tipo"] = _fact(
            "Marketplace no identifica al vendedor"
        )
        values["marketplace"] = _fact("Marketplace Demo")
        values["marketplace_es_parte_contractual"] = _fact(False)
        values["marketplace_vendedor_identificado"] = _fact(False)
        values["marketplace_informa_condicion_empresario"] = _fact(False)
        values["marketplace_reparte_obligaciones"] = _fact(
            "La plataforma indica que solo intermedia el pago."
        )
        values.pop("vendedor_online")
        values.pop("vendedor_domicilio")
        values.pop("pedido_entregado")
        values.pop("fecha_entrega_pactada")
        values.pop("seguimiento_envio_ref")
        values.pop("transportista_pedido")
        facts_record, family_record = _records(values)
        preview = build_legal_preview(facts_record, family_record)

        self.assertEqual(preview.destination, "Marketplace Demo")
        self.assertEqual(
            preview.document_type,
            "RECLAMACIÓN AL MARKETPLACE POR SUS OBLIGACIONES PROPIAS",
        )
        codes = {item.code for item in preview.missing_items}
        self.assertIn("ecommerce_marketplace_seller_identity_missing", codes)
        self.assertIn("ecommerce_marketplace_trader_status_missing", codes)
        self.assertTrue(
            any(
                item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )

    def test_digital_withdrawal_loss_requires_documented_elements(self):
        values = _complete_values()
        values["descripcion_hecho"] = _fact(
            "Compra online de contenido digital con desistimiento tras inicio inmediato."
        )
        values["incidencia_ecommerce_tipo"] = _fact("Desistimiento")
        values["pedido_tipo_contrato"] = _fact("Contenido digital")
        values.pop("pedido_producto_descripcion")
        values.pop("producto_servicio")
        values["pedido_servicio_descripcion"] = _fact("Licencia digital descargable")
        values["contenido_servicio_digital"] = _fact(True)
        values["desistimiento_comunicado"] = _fact(True)
        values["fecha_comunicacion_desistimiento"] = _fact("2026-07-05")
        values["informacion_desistimiento_entregada"] = _fact(True)
        values["contenido_digital_ejecucion_iniciada"] = _fact(True)
        values["consentimiento_inicio_digital"] = _fact(False)
        values["conocimiento_perdida_desistimiento"] = _fact(False)
        values["confirmacion_contrato_soporte_duradero"] = _fact(False)
        values.pop("fecha_entrega_pactada")
        values.pop("pedido_entregado")
        values.pop("seguimiento_envio_ref")
        values.pop("transportista_pedido")
        facts_record, family_record = _records(values)
        preview = build_claims_ecommerce_preview(facts_record, family_record)

        self.assertTrue(
            any(
                item.code == "ecommerce_digital_withdrawal_loss_requirements_missing"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )

    def test_refund_above_payment_is_blocking(self):
        values = _complete_values()
        values["descripcion_hecho"] = _fact(
            "Reembolso pendiente de una compra online ya cancelada."
        )
        values["incidencia_ecommerce_tipo"] = _fact("Reembolso pendiente")
        values["importe_reembolso_pedido_eur"] = _fact(150.00)
        values["reclamacion_previa_fecha"] = _fact("2026-07-12")
        values["reclamacion_ecommerce_ref"] = _fact("REC-2026-77")
        values["canal_reclamacion"] = _fact("Formulario web")
        values.pop("fecha_entrega_pactada")
        values.pop("pedido_entregado")
        values.pop("seguimiento_envio_ref")
        values.pop("transportista_pedido")
        facts_record, family_record = _records(values)
        preview = build_claims_ecommerce_preview(facts_record, family_record)

        self.assertTrue(
            any(
                item.code == "ecommerce_refund_exceeds_payment"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )

    def test_payment_recovery_is_coordinated_without_double_recovery(self):
        values = _complete_values()
        values["importe_recuperado_medio_pago_eur"] = _fact(129.90)
        values["disputa_medio_pago_abierta"] = _fact(True)
        facts_record, family_record = _records(values)
        preview = build_claims_ecommerce_preview(facts_record, family_record)

        self.assertTrue(
            any(
                item.code == "ecommerce_payment_recovery_coordination_review"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )
        rendered = " ".join(argument.body for argument in preview.legal_arguments)
        self.assertIn("doble recuperación", rendered.lower())

    def test_post_guarantee_repair_has_no_current_basis(self):
        values = _complete_values()
        values["descripcion_hecho"] = _fact(
            "Pedido online de un producto para el que se solicita reparación fuera de garantía."
        )
        values["incidencia_ecommerce_tipo"] = _fact("Reparación fuera de garantía")
        values["fecha_compra"] = _fact("2023-01-10")
        values["fecha_entrega"] = _fact("2023-01-15")
        values["fecha_incidencia"] = _fact("2026-08-02")
        values["reparacion_fuera_garantia_solicitada"] = _fact(True)
        values["falta_conformidad_descripcion"] = _fact(
            "El producto dejó de funcionar fuera de la garantía legal."
        )
        values["fecha_manifestacion_falta_conformidad"] = _fact("2026-08-02")
        values["reparacion_solicitada"] = _fact(True)
        values.pop("fecha_entrega_pactada")
        values.pop("pedido_entregado")
        values.pop("seguimiento_envio_ref")
        values.pop("transportista_pedido")
        facts_record, family_record = _records(values)
        preview = build_claims_ecommerce_preview(facts_record, family_record)

        self.assertTrue(
            any(
                item.code == "ecommerce_regime_review"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )
        self.assertTrue(all(not argument.legal_basis for argument in preview.legal_arguments))

    def test_unsafe_product_requires_safety_route_review(self):
        values = _complete_values()
        values["descripcion_hecho"] = _fact(
            "Producto inseguro adquirido en una compra online y sometido a retirada."
        )
        values["incidencia_ecommerce_tipo"] = _fact("Producto inseguro")
        values["producto_inseguro"] = _fact(True)
        values["retirada_producto_anunciada"] = _fact(True)
        values["aviso_seguridad_producto"] = _fact("Retirada por riesgo eléctrico")
        facts_record, family_record = _records(values)
        preview = build_claims_ecommerce_preview(facts_record, family_record)
        self.assertTrue(
            any(
                item.code == "ecommerce_product_safety_route_review"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )

    def test_authority_mismatch_is_rejected(self):
        facts_record, family_record = _records(_complete_values())
        wrong_resolution = validated_model_copy(
            family_record.resolution,
            specialist="claims.banking",
        )
        wrong_family = family_record.model_copy(
            update={
                "resolution": wrong_resolution,
                "payload_sha256": model_digest(wrong_resolution),
            }
        )
        with self.assertRaises(HTTPException) as raised:
            build_claims_ecommerce_preview(facts_record, wrong_family)
        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
