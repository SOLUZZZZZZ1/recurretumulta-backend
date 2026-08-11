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
            "Reclamación de consumo por frigorífico nuevo comprado en establecimiento físico que presenta un producto defectuoso.",
            "RECLAMACIÓN DE CONSUMO POR PRODUCTO DEFECTUOSO",
        ),
        "incidencia_consumo_tipo": _fact("Falta de conformidad"),
        "empresa_consumo": _fact("Comercio Demo, S.L."),
        "pais_empresa_consumo": _fact("España"),
        "cliente_consumo_es_consumidor": _fact(True),
        "pais_cliente_consumo": _fact("España"),
        "establecimiento_consumo": _fact("Tienda Demo Madrid"),
        "contrato_consumo_ref": _fact("COMPRA-2026-4401"),
        "fecha_contrato_consumo": _fact("2026-03-01"),
        "compra_presencial_consumo": _fact(True),
        "contrato_distancia_consumo": _fact(False),
        "contrato_fuera_establecimiento_consumo": _fact(False),
        "tipo_contrato_consumo": _fact("Bien de consumo"),
        "producto_servicio_consumo": _fact("Frigorífico nuevo modelo RTM-500"),
        "categoria_producto_consumo": _fact("Electrodoméstico"),
        "bien_nuevo_consumo": _fact(True),
        "bien_segunda_mano_consumo": _fact(False),
        "fecha_entrega_consumo": _fact("2026-03-03"),
        "precio_publicitado_consumo_eur": _fact(899.95),
        "precio_pactado_consumo_eur": _fact(899.95),
        "precio_cobrado_consumo_eur": _fact(899.95),
        "importe_pagado_consumo_eur": _fact(899.95),
        "cargo_adicional_consumo_eur": _fact(0.0),
        "cargo_adicional_informado_consumo": _fact(True),
        "factura_ticket_consumo_ref": _fact("TICKET-2026-88"),
        "publicidad_oferta_consumo": _fact(
            "Frigorífico nuevo con dos años de garantía comercial adicional."
        ),
        "condiciones_consumo": _fact("Venta presencial de bien nuevo."),
        "falta_conformidad_consumo_descripcion": _fact(
            "El compresor se detiene y el aparato no mantiene la temperatura."
        ),
        "fecha_manifestacion_falta_conformidad_consumo": _fact("2026-06-01"),
        "fecha_comunicacion_falta_conformidad_consumo": _fact("2026-06-02"),
        "reparacion_consumo_solicitada": _fact(True),
        "reparacion_consumo_ofrecida": _fact(True),
        "reparacion_consumo_completada": _fact(False),
        "sustitucion_consumo_solicitada": _fact(False),
        "reduccion_precio_consumo_solicitada": _fact(False),
        "resolucion_contrato_consumo_solicitada": _fact(False),
        "reclamacion_previa_consumo_fecha": _fact("2026-06-02"),
        "reclamacion_previa_consumo_ref": _fact("REC-2026-101"),
        "canal_reclamacion_consumo": _fact("Correo electrónico y tienda"),
        "respuesta_consumo_fecha": _fact("2026-06-04"),
        "respuesta_consumo": _fact(
            "La empresa ofrece inspección técnica y reparación sin coste."
        ),
        "solucion_solicitada_consumo": _fact(
            "Reparación sin coste y, si no resulta viable, sustitución del frigorífico."
        ),
        "empresa_consumo_gran_dimension": _fact(False),
        "ley_atencion_clientela_consumo_aplicable": _fact(False),
        "compra_online_consumo": _fact(False),
        "marketplace_consumo_implicado": _fact(False),
        "telecomunicaciones_consumo_implicadas": _fact(False),
        "energia_consumo_implicada": _fact(False),
        "banca_medio_pago_consumo_implicado": _fact(False),
        "seguro_consumo_implicado": _fact(False),
        "viaje_consumo_implicado": _fact(False),
        "servicio_profesional_consumo_implicado": _fact(False),
        "administracion_publica_consumo_implicada": _fact(False),
        "vivienda_arrendamiento_consumo_implicado": _fact(False),
        "servicio_sanitario_consumo_implicado": _fact(False),
        "servicio_juridico_consumo_implicado": _fact(False),
        "inversion_consumo_implicada": _fact(False),
        "proteccion_datos_consumo_principal": _fact(False),
        "producto_inseguro_consumo": _fact(False),
        "lesion_personal_consumo": _fact(False),
        "vehiculo_motor_consumo_implicado": _fact(False),
        "contenido_servicio_digital_consumo": _fact(False),
        "procedimiento_judicial_consumo_relacionado": _fact(False),
        "proveedor_insolvente_consumo": _fact(False),
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
            "RECLAMACIÓN ANTE CONSUMO, ARBITRAJE O ADR — COMPETENCIA PENDIENTE DE VALIDAR",
        )
        self.assertIn("COMPRA-2026-4401", preview.subject)
        self.assertGreaterEqual(len(preview.legal_arguments), 6)
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
        self.assertTrue(
            any(
                item.code == "consumer_authority_or_adr_competence_review"
                for item in preview.missing_items
            )
        )
        self.assertTrue(
            any(
                "Ley General" in basis
                for argument in preview.legal_arguments
                for basis in argument.legal_basis
            )
        )
        self.assertTrue(preview.deadlines)
        self.assertTrue(
            any(deadline.calculation_status == "estimated" for deadline in preview.deadlines)
        )

        declared = set(preview.source_fact_keys)
        for argument in preview.legal_arguments:
            self.assertTrue(argument.source_fact_keys)
            self.assertTrue(set(argument.source_fact_keys).issubset(declared))
            self.assertNotIn("raw_ocr_text", argument.source_fact_keys)
        self.assertNotIn("raw_ocr_text", preview.source_fact_keys)

    def test_charged_price_above_agreed_is_blocking(self):
        values = _complete_values()
        values["descripcion_hecho"] = _fact(
            "Reclamación de consumo por precio cobrado superior al pactado."
        )
        values["incidencia_consumo_tipo"] = _fact("Precio cobrado")
        values["precio_cobrado_consumo_eur"] = _fact(999.95)
        facts_record, family_record = _records(values)
        preview = build_claims_consumer_preview(facts_record, family_record)
        self.assertTrue(
            any(
                item.code == "consumer_charged_price_exceeds_agreed"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )

    def test_in_store_withdrawal_is_not_automatic(self):
        values = _complete_values()
        values["descripcion_hecho"] = _fact(
            "Reclamación de consumo por devolución de una compra presencial sin defecto."
        )
        values["incidencia_consumo_tipo"] = _fact("Desistimiento")
        values["desistimiento_consumo_comunicado"] = _fact(True)
        values["fecha_desistimiento_consumo"] = _fact("2026-03-05")
        facts_record, family_record = _records(values)
        preview = build_claims_consumer_preview(facts_record, family_record)
        self.assertTrue(
            any(
                item.code == "consumer_in_store_withdrawal_no_automatic_right"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )

    def test_full_performance_off_premises_requires_all_elements(self):
        values = _complete_values()
        values["descripcion_hecho"] = _fact(
            "Reclamación de consumo por desistimiento de servicio contratado en domicilio."
        )
        values["incidencia_consumo_tipo"] = _fact("Desistimiento")
        values["tipo_contrato_consumo"] = _fact("Servicio de consumo")
        values["producto_servicio_consumo"] = _fact("Servicio doméstico de mantenimiento")
        values["bien_nuevo_consumo"] = _fact(False)
        values["compra_presencial_consumo"] = _fact(False)
        values["contrato_fuera_establecimiento_consumo"] = _fact(True)
        values["visita_domicilio_no_solicitada_consumo"] = _fact(True)
        values["informacion_desistimiento_consumo_entregada"] = _fact(True)
        values["desistimiento_consumo_comunicado"] = _fact(True)
        values["fecha_desistimiento_consumo"] = _fact("2026-03-10")
        values["fecha_inicio_servicio_consumo"] = _fact("2026-03-02")
        values["inicio_servicio_durante_desistimiento_solicitado"] = _fact(True)
        values["consentimiento_inicio_servicio_consumo"] = _fact(False)
        values["conocimiento_perdida_desistimiento_consumo"] = _fact(False)
        values["servicio_consumo_completamente_ejecutado"] = _fact(True)
        values["incumplimiento_servicio_consumo_descripcion"] = _fact(
            "El consumidor discute la pérdida del desistimiento."
        )
        facts_record, family_record = _records(values)
        preview = build_claims_consumer_preview(facts_record, family_record)
        self.assertTrue(
            any(
                item.code == "consumer_full_performance_withdrawal_requirements_missing"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )
        self.assertTrue(
            any(deadline.calculation_status == "confirmed" for deadline in preview.deadlines)
        )

    def test_refund_and_third_party_recovery_cannot_duplicate_loss(self):
        values = _complete_values()
        values["importe_reembolso_consumo_efectuado_eur"] = _fact(800.00)
        values["importe_recuperado_terceros_consumo_eur"] = _fact(200.00)
        facts_record, family_record = _records(values)
        preview = build_claims_consumer_preview(facts_record, family_record)
        self.assertTrue(
            any(
                item.code == "consumer_double_recovery_amount_conflict"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )
        rendered = " ".join(argument.body for argument in preview.legal_arguments)
        self.assertIn("duplicar", rendered.lower())

    def test_second_hand_period_below_minimum_is_blocking(self):
        values = _complete_values()
        values["bien_nuevo_consumo"] = _fact(False)
        values["bien_segunda_mano_consumo"] = _fact(True)
        values["periodo_garantia_segunda_mano_pactado_anios"] = _fact(0.5)
        values["producto_servicio_consumo"] = _fact("Frigorífico de segunda mano")
        facts_record, family_record = _records(values)
        preview = build_claims_consumer_preview(facts_record, family_record)
        self.assertTrue(
            any(
                item.code in {
                    "consumer_regime_review",
                    "consumer_second_hand_period_below_minimum",
                }
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )

    def test_online_boundary_has_no_current_legal_basis(self):
        values = _complete_values()
        values["compra_presencial_consumo"] = _fact(False)
        values["contrato_distancia_consumo"] = _fact(True)
        values["compra_online_consumo"] = _fact(True)
        facts_record, family_record = _records(values)
        preview = build_claims_consumer_preview(facts_record, family_record)
        self.assertTrue(
            any(
                item.code == "consumer_regime_review"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )
        self.assertTrue(all(not argument.legal_basis for argument in preview.legal_arguments))

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
