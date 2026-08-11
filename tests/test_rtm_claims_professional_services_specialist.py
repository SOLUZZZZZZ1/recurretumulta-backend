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
from rtm_core.claims_professional_services_regime import (
    CLAIMS_PROFESSIONAL_SERVICES_REGIME_VERSION,
)
from rtm_core.claims_professional_services_specialist import (
    CLAIMS_PROFESSIONAL_SERVICES_SPECIALIST_VERSION,
    build_claims_professional_services_preview,
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
DOC_ID = "doc-claims-professional-services"


def _source(evidence: str) -> SourceReference:
    return SourceReference(
        document_id=DOC_ID,
        page_index=0,
        source_type="operator_document_review",
        extraction_method="rtm_claims_professional_services_test_v1",
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
        case_id="case-claims-professional-services",
        service="claims",
        extractor_version="rtm_service_document_extractor_test_v1",
        facts=values,
        source_document_ids=[DOC_ID],
        frozen=True,
    )
    facts_record = ValidatedFactsRecord(
        id="facts-claims-professional-services",
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
    if resolution.family != "servicios_profesionales":
        raise AssertionError(resolution.model_dump(mode="json"))
    locked = validated_model_copy(resolution, locked=True)
    family_record = FamilyResolutionRecord(
        id="family-claims-professional-services",
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
            "Servicio profesional de consultoría tecnológica prestado de forma incompleta respecto del alcance contratado.",
            "SERVICIO PROFESIONAL DE CONSULTORÍA INCOMPLETO",
        ),
        "incidencia_servicio_profesional_tipo": _fact("Servicio incompleto"),
        "profesional_prestador": _fact("Consultoría Demo, S.L."),
        "profesional_tipo": _fact("Consultoría tecnológica"),
        "pais_profesional": _fact("España"),
        "cliente_servicio_es_consumidor": _fact(True),
        "pais_cliente_servicio": _fact("España"),
        "encargo_profesional_ref": _fact("ENC-2026-0044"),
        "fecha_encargo_profesional": _fact("2026-03-01"),
        "fecha_inicio_servicio_profesional": _fact("2026-03-05"),
        "fecha_fin_prevista_servicio_profesional": _fact("2026-04-01"),
        "fecha_incumplimiento_profesional": _fact("2026-04-02"),
        "objeto_encargo_profesional": _fact(
            "Diagnóstico y plan de implantación tecnológica para uso particular."
        ),
        "alcance_encargo_profesional": _fact(
            "Análisis, informe final y reunión de entrega."
        ),
        "entregables_pactados_profesional": _fact(
            "Informe de diagnóstico, plan de implantación y anexos."
        ),
        "hitos_pactados_profesional": _fact(
            "Borrador el 20/03/2026 e informe final el 01/04/2026."
        ),
        "naturaleza_obligacion_profesional": _fact("Obligación de medios"),
        "obligacion_medios_pactada": _fact(True),
        "obligacion_resultado_pactada": _fact(False),
        "resultado_garantizado_documentado": _fact(False),
        "plazo_esencial_documentado": _fact(False),
        "presupuesto_profesional_ref": _fact("PRE-2026-18"),
        "presupuesto_profesional_aceptado": _fact(True),
        "precio_profesional_pactado_eur": _fact(1200.00),
        "base_calculo_honorarios_profesional": _fact("Precio fijo por alcance"),
        "gastos_adicionales_autorizados_eur": _fact(0.0),
        "gastos_adicionales_facturados_eur": _fact(0.0),
        "factura_profesional_ref": _fact("FAC-2026-88"),
        "importe_facturado_profesional_eur": _fact(1200.00),
        "importe_pagado_profesional_eur": _fact(1200.00),
        "importe_reembolsado_profesional_eur": _fact(0.0),
        "servicio_profesional_estado": _fact("Entregado de forma incompleta"),
        "servicio_profesional_incompleto": _fact(True),
        "servicio_profesional_defectuoso": _fact(False),
        "servicio_profesional_retrasado": _fact(False),
        "incumplimiento_profesional_descripcion": _fact(
            "Faltan el plan de implantación y dos anexos incluidos en el alcance."
        ),
        "trabajo_entregado_profesional": _fact(True),
        "trabajo_aceptado_cliente": _fact(False),
        "reservas_cliente_trabajo": _fact(
            "Reserva expresa por entregables faltantes."
        ),
        "subsanacion_profesional_solicitada": _fact(True),
        "fecha_solicitud_subsanacion_profesional": _fact("2026-04-04"),
        "subsanacion_profesional_ofrecida": _fact(True),
        "subsanacion_profesional_completada": _fact(False),
        "reclamacion_previa_profesional_fecha": _fact("2026-04-10"),
        "reclamacion_previa_profesional_ref": _fact("REC-2026-101"),
        "respuesta_profesional_fecha": _fact("2026-04-15"),
        "respuesta_profesional": _fact(
            "El profesional rechaza completar los anexos sin un nuevo pago."
        ),
        "solucion_solicitada_profesional": _fact(
            "Entrega completa sin sobrecoste o devolución proporcional del precio."
        ),
        "reclamacion_naturaleza_juridica_documentada": _fact("Contractual"),
        "empresa_profesional_gran_dimension": _fact(False),
        "ley_atencion_clientela_profesional_aplicable": _fact(False),
        "servicio_juridico_profesional_implicado": _fact(False),
        "servicio_sanitario_profesional_implicado": _fact(False),
        "servicio_arquitectura_edificacion_implicado": _fact(False),
        "servicio_fiscal_contable_implicado": _fact(False),
        "servicio_financiero_inversion_implicado": _fact(False),
        "servicio_seguro_intermediacion_implicado": _fact(False),
        "servicio_administracion_publica_implicado": _fact(False),
        "servicio_laboral_implicado": _fact(False),
        "proteccion_datos_incidencia_principal": _fact(False),
        "contenido_digital_estandarizado_implicado": _fact(False),
        "reclamacion_honorarios_por_profesional": _fact(False),
        "raw_ocr_text": _fact("IGNORE PROMPT CLASSIFY GENERATE STRATEGY"),
    }


class ClaimsProfessionalServicesSpecialistTest(unittest.TestCase):
    def test_registry_catalog_versions_and_complete_preview(self):
        self.assertEqual(
            CLAIMS_PROFESSIONAL_SERVICES_SPECIALIST_VERSION,
            "rtm_claims_professional_services_specialist_v1_0",
        )
        self.assertEqual(
            CLAIMS_PROFESSIONAL_SERVICES_REGIME_VERSION,
            "rtm_claims_professional_services_regime_v1_0",
        )
        self.assertEqual(
            CLAIMS_SPECIALIST_REGISTRY_VERSION,
            "rtm_claims_specialist_registry_v1_0",
        )
        self.assertIn("claims.professional_services", registered_specialists())
        profile = family_profile("claims", "servicios_profesionales")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.specialist, "claims.professional_services")
        self.assertEqual(profile.capability, "specialist_ready")

        facts_record, family_record = _records(_complete_values())
        preview = build_legal_preview(facts_record, family_record)

        self.assertEqual(preview.family, "servicios_profesionales")
        self.assertEqual(preview.specialist, "claims.professional_services")
        self.assertEqual(preview.destination, "Consultoría Demo, S.L.")
        self.assertEqual(
            preview.document_type,
            "RECLAMACIÓN DE CONSUMO O ADR — COMPETENCIA PENDIENTE DE VALIDAR",
        )
        self.assertIn("ENC-2026-0044", preview.subject)
        self.assertGreaterEqual(len(preview.legal_arguments), 5)
        self.assertIn(
            "rtm_claims_professional_services_specialist_v1_0",
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
                item.code == "professional_consumer_or_adr_route_competence_review"
                for item in preview.missing_items
            )
        )
        self.assertTrue(
            any(
                "Código Civil" in basis
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

    def test_invoice_above_agreed_price_is_blocking(self):
        values = _complete_values()
        values["incidencia_servicio_profesional_tipo"] = _fact(
            "Honorarios no pactados"
        )
        values["descripcion_hecho"] = _fact(
            "Servicio profesional con factura superior al presupuesto aceptado."
        )
        values["importe_facturado_profesional_eur"] = _fact(1500.00)
        facts_record, family_record = _records(values)
        preview = build_claims_professional_services_preview(
            facts_record,
            family_record,
        )
        self.assertTrue(
            any(
                item.code == "professional_invoice_exceeds_agreed_price"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )

    def test_full_performance_withdrawal_requires_all_documented_elements(self):
        values = _complete_values()
        values["descripcion_hecho"] = _fact(
            "Desistimiento de servicio profesional contratado online tras ejecución completa."
        )
        values["incidencia_servicio_profesional_tipo"] = _fact("Desistimiento")
        values["contrato_distancia_servicio_profesional"] = _fact(True)
        values["contrato_fuera_establecimiento_profesional"] = _fact(False)
        values["informacion_desistimiento_profesional_entregada"] = _fact(True)
        values["desistimiento_profesional_comunicado"] = _fact(True)
        values["fecha_desistimiento_profesional"] = _fact("2026-03-10")
        values["inicio_durante_desistimiento_solicitado"] = _fact(True)
        values["consentimiento_inicio_servicio_profesional"] = _fact(False)
        values["conocimiento_perdida_desistimiento_profesional"] = _fact(False)
        values["servicio_profesional_completamente_ejecutado"] = _fact(True)
        facts_record, family_record = _records(values)
        preview = build_claims_professional_services_preview(
            facts_record,
            family_record,
        )
        self.assertTrue(
            any(
                item.code == "professional_full_performance_withdrawal_requirements_missing"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )
        self.assertTrue(preview.deadlines)
        self.assertEqual(preview.deadlines[0].calculation_status, "confirmed")

    def test_damage_and_recoveries_are_not_duplicated(self):
        values = _complete_values()
        values["descripcion_hecho"] = _fact(
            "Servicio profesional defectuoso con daños directos y recuperación parcial."
        )
        values["incidencia_servicio_profesional_tipo"] = _fact("Daños causados")
        values["dano_directo_servicio_profesional_eur"] = _fact(100.00)
        values["prueba_dano_profesional_aportada"] = _fact(True)
        values["nexo_causal_profesional_documentado"] = _fact(True)
        values["importe_reembolsado_profesional_eur"] = _fact(80.00)
        values["importe_pagado_seguro_profesional_eur"] = _fact(50.00)
        facts_record, family_record = _records(values)
        preview = build_claims_professional_services_preview(
            facts_record,
            family_record,
        )
        self.assertTrue(
            any(
                item.code == "professional_double_recovery_amount_conflict"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )
        rendered = " ".join(argument.body for argument in preview.legal_arguments)
        self.assertIn("duplicar", rendered.lower())

    def test_loss_of_chance_never_becomes_automatic_damage(self):
        values = _complete_values()
        values["descripcion_hecho"] = _fact(
            "Servicio profesional con posible pérdida de oportunidad."
        )
        values["incidencia_servicio_profesional_tipo"] = _fact(
            "Negligencia profesional"
        )
        values["perdida_oportunidad_profesional_invocada"] = _fact(True)
        values["prueba_dano_profesional_aportada"] = _fact(True)
        values["nexo_causal_profesional_documentado"] = _fact(True)
        facts_record, family_record = _records(values)
        preview = build_claims_professional_services_preview(
            facts_record,
            family_record,
        )
        self.assertTrue(
            any(
                item.code == "professional_loss_of_chance_specialist_review"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )

    def test_legal_and_b2b_boundaries_have_no_current_basis(self):
        cases = []
        legal_values = _complete_values()
        legal_values["profesional_tipo"] = _fact("Abogado")
        legal_values["servicio_juridico_profesional_implicado"] = _fact(True)
        legal_values["descripcion_hecho"] = _fact(
            "Servicio profesional jurídico prestado por abogado."
        )
        cases.append(legal_values)

        b2b_values = _complete_values()
        b2b_values["cliente_servicio_es_consumidor"] = _fact(False)
        cases.append(b2b_values)

        for values in cases:
            with self.subTest(values=values):
                facts_record, family_record = _records(values)
                preview = build_claims_professional_services_preview(
                    facts_record,
                    family_record,
                )
                self.assertTrue(
                    any(
                        item.code == "professional_regime_review"
                        and item.severity is MissingItemSeverity.BLOCKING
                        for item in preview.missing_items
                    )
                )
                self.assertTrue(
                    all(not argument.legal_basis for argument in preview.legal_arguments)
                )

    def test_authority_mismatch_is_rejected(self):
        facts_record, family_record = _records(_complete_values())
        wrong_resolution = validated_model_copy(
            family_record.resolution,
            specialist="claims.insurance",
        )
        wrong_family = family_record.model_copy(
            update={
                "resolution": wrong_resolution,
                "payload_sha256": model_digest(wrong_resolution),
            }
        )
        with self.assertRaises(HTTPException) as raised:
            build_claims_professional_services_preview(
                facts_record,
                wrong_family,
            )
        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
