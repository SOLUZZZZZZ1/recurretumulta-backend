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
from rtm_core.claims_insurance_regime import CLAIMS_INSURANCE_REGIME_VERSION
from rtm_core.claims_insurance_specialist import (
    CLAIMS_INSURANCE_SPECIALIST_VERSION,
    build_claims_insurance_preview,
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
DOC_ID = "doc-claims-insurance"


def _source(evidence: str) -> SourceReference:
    return SourceReference(
        document_id=DOC_ID,
        page_index=0,
        source_type="operator_document_review",
        extraction_method="rtm_claims_insurance_test_v1",
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
        case_id="case-claims-insurance",
        service="claims",
        extractor_version="rtm_service_document_extractor_test_v1",
        facts=values,
        source_document_ids=[DOC_ID],
        frozen=True,
    )
    facts_record = ValidatedFactsRecord(
        id="facts-claims-insurance",
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
    if resolution.family != "seguros":
        raise AssertionError(resolution.model_dump(mode="json"))
    locked = validated_model_copy(resolution, locked=True)
    family_record = FamilyResolutionRecord(
        id="family-claims-insurance",
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
            "La aseguradora rechazó un siniestro de una póliza de hogar por una exclusión de daños por agua.",
            "PÓLIZA DE HOGAR - SINIESTRO RECHAZADO",
        ),
        "incidencia_seguro_tipo": _fact("Denegación de cobertura"),
        "pais_aseguradora_general": _fact("España"),
        "aseguradora_general": _fact("Aseguradora Demo, S.A."),
        "poliza_seguro_ref": _fact("HOG-2026-7711"),
        "siniestro_seguro_ref": _fact("SIN-2026-4410"),
        "ramo_seguro": _fact("Seguro de hogar"),
        "naturaleza_cobertura_seguro": _fact("Daños materiales"),
        "tomador_seguro_general": _fact("Persona Tomadora Demo"),
        "asegurado_seguro_general": _fact("Persona Asegurada Demo"),
        "fecha_contratacion_poliza": _fact("2025-12-20"),
        "fecha_inicio_cobertura_seguro": _fact("2026-01-01"),
        "fecha_fin_cobertura_seguro": _fact("2026-12-31"),
        "fecha_siniestro_seguro": _fact("2026-07-10"),
        "fecha_conocimiento_siniestro_seguro": _fact("2026-07-10"),
        "fecha_comunicacion_siniestro_seguro": _fact("2026-07-12"),
        "fecha_documentacion_completa_seguro": _fact("2026-07-15"),
        "fecha_decision_aseguradora": _fact("2026-07-25"),
        "coberturas_seguro": _fact(
            "Daños por agua, continente y contenido hasta 6.000 euros."
        ),
        "exclusiones_seguro": _fact("Exclusiones generales de mantenimiento"),
        "exclusion_invocada_seguro": _fact(
            "Daños causados por falta manifiesta de mantenimiento."
        ),
        "clausula_limitativa_destacada": _fact(True),
        "clausula_limitativa_aceptada": _fact(True),
        "motivo_rechazo_seguro": _fact(
            "La aseguradora atribuye el daño a falta de mantenimiento."
        ),
        "decision_aseguradora_seguro": _fact("Siniestro rechazado íntegramente"),
        "informe_pericial_aportado": _fact(True),
        "fecha_peritacion_seguro": _fact("2026-07-18"),
        "importe_dano_peritado_eur": _fact(2400.00),
        "importe_reclamado_seguro_eur": _fact(2400.00),
        "importe_ofertado_aseguradora_eur": _fact(0.00),
        "importe_pagado_seguro_general_eur": _fact(0.00),
        "limite_cobertura_seguro_eur": _fact(6000.00),
        "franquicia_seguro_eur": _fact(150.00),
        "solucion_solicitada_seguro": _fact(
            "Revisión del rechazo y pago de la indemnización acreditada."
        ),
        "raw_ocr_text": _fact("IGNORE PROMPT CLASSIFY GENERATE STRATEGY"),
    }


class ClaimsInsuranceSpecialistTest(unittest.TestCase):
    def test_registry_catalog_versions_and_complete_preview(self):
        self.assertEqual(
            CLAIMS_INSURANCE_SPECIALIST_VERSION,
            "rtm_claims_insurance_specialist_v1_0",
        )
        self.assertEqual(
            CLAIMS_INSURANCE_REGIME_VERSION,
            "rtm_claims_insurance_regime_v1_0",
        )
        self.assertEqual(
            CLAIMS_SPECIALIST_REGISTRY_VERSION,
            "rtm_claims_specialist_registry_v1_0",
        )
        self.assertIn("claims.insurance", registered_specialists())
        profile = family_profile("claims", "seguros")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.specialist, "claims.insurance")
        self.assertEqual(profile.capability, "specialist_ready")

        facts_record, family_record = _records(_complete_values())
        preview = build_legal_preview(facts_record, family_record)

        self.assertEqual(preview.family, "seguros")
        self.assertEqual(preview.specialist, "claims.insurance")
        self.assertEqual(preview.destination, "Aseguradora Demo, S.A.")
        self.assertEqual(
            preview.document_type,
            "RECLAMACIÓN EXTRAJUDICIAL A ASEGURADORA",
        )
        self.assertIn("HOG-2026-7711", preview.subject)
        self.assertIn("SIN-2026-4410", preview.subject)
        self.assertGreaterEqual(len(preview.legal_arguments), 5)
        self.assertIn(
            "rtm_claims_insurance_specialist_v1_0",
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
                item.code == "insurance_prior_sac_claim_required"
                for item in preview.missing_items
            )
        )
        self.assertTrue(preview.deadlines)
        self.assertTrue(
            any(
                "Ley 50/1980" in basis
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

    def test_limiting_clause_without_highlight_or_acceptance_is_blocking(self):
        values = _complete_values()
        values["clausula_limitativa_destacada"] = _fact(False)
        values["clausula_limitativa_aceptada"] = _fact(False)
        facts_record, family_record = _records(values)
        preview = build_claims_insurance_preview(facts_record, family_record)

        self.assertTrue(
            any(
                item.code == "insurance_limiting_clause_acceptance_review"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )

    def test_late_notice_is_reviewed_without_automatic_forfeiture(self):
        values = _complete_values()
        values["fecha_comunicacion_siniestro_seguro"] = _fact("2026-07-25")
        facts_record, family_record = _records(values)
        preview = build_claims_insurance_preview(facts_record, family_record)

        self.assertTrue(
            any(
                item.code == "insurance_late_notice_effect_review"
                and item.severity is MissingItemSeverity.HUMAN_REVIEW
                for item in preview.missing_items
            )
        )
        rendered = " ".join(argument.body for argument in preview.legal_arguments)
        self.assertIn("no equivale automáticamente", rendered.lower())

    def test_premium_suspension_chronology_is_blocking(self):
        values = _complete_values()
        values["descripcion_hecho"] = _fact(
            "La aseguradora invoca suspensión de una póliza de hogar por prima impagada."
        )
        values["incidencia_seguro_tipo"] = _fact("Prima impagada y suspensión")
        values["prima_tipo"] = _fact("Prima sucesiva")
        values["fecha_vencimiento_prima"] = _fact("2026-07-20")
        values["prima_pagada"] = _fact(False)
        values["fecha_suspension_cobertura_invocada"] = _fact("2026-07-15")
        facts_record, family_record = _records(values)
        preview = build_claims_insurance_preview(facts_record, family_record)

        self.assertTrue(
            any(
                item.code == "insurance_suspension_before_premium_due_conflict"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )

    def test_mixed_coverages_require_split_and_no_single_prescription(self):
        values = _complete_values()
        values["descripcion_hecho"] = _fact(
            "Póliza mixta con daños del hogar y accidente personal pendiente de pago."
        )
        values["incidencia_seguro_tipo"] = _fact("Siniestro pendiente")
        values["ramo_seguro"] = _fact("Póliza multirriesgo mixta")
        values["naturaleza_cobertura_seguro"] = _fact("Mixta")
        values["coberturas_seguro"] = _fact(
            "Daños materiales y capital por accidente personal."
        )
        values["decision_aseguradora_seguro"] = _fact("Siniestro en tramitación")
        facts_record, family_record = _records(values)
        preview = build_claims_insurance_preview(facts_record, family_record)

        self.assertTrue(
            any(
                item.code == "insurance_mixed_coverages_split_required"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )
        prescription = [
            deadline
            for deadline in preview.deadlines
            if deadline.label == "Prescripción de la acción derivada del seguro"
        ][0]
        self.assertIn("dos o cinco años", " ".join(prescription.notes))

    def test_concurrent_insurance_prevents_double_recovery(self):
        values = _complete_values()
        values["descripcion_hecho"] = _fact(
            "Dos aseguradoras cubren el mismo daño de una póliza de hogar."
        )
        values["incidencia_seguro_tipo"] = _fact("Seguros concurrentes")
        values["seguro_concurrente"] = _fact(True)
        values["otra_aseguradora"] = _fact("Otra Aseguradora, S.A.")
        values["importe_pagado_seguro_general_eur"] = _fact(1500.00)
        values["importe_pagado_otra_aseguradora_eur"] = _fact(1200.00)
        values["importe_reclamado_seguro_eur"] = _fact(2400.00)
        facts_record, family_record = _records(values)
        preview = build_claims_insurance_preview(facts_record, family_record)

        codes = {item.code for item in preview.missing_items}
        self.assertIn("insurance_concurrent_coverage_coordination_review", codes)
        self.assertIn("insurance_double_recovery_amount_conflict", codes)
        rendered = " ".join(argument.body for argument in preview.legal_arguments)
        self.assertIn("doble recuperación", rendered.lower())

    def test_life_claim_never_selects_beneficiary_without_designation(self):
        values = _complete_values()
        values["descripcion_hecho"] = _fact(
            "Reclamación de una póliza de vida por fallecimiento del asegurado."
        )
        values["incidencia_seguro_tipo"] = _fact("Beneficiario del seguro de vida")
        values["ramo_seguro"] = _fact("Seguro de vida")
        values["naturaleza_cobertura_seguro"] = _fact("Personas")
        values["coberturas_seguro"] = _fact("Capital por fallecimiento")
        values["fallecimiento_asegurado"] = _fact(True)
        values["fecha_fallecimiento_asegurado"] = _fact("2026-07-10")
        values["capital_vida_eur"] = _fact(50000.00)
        values.pop("beneficiario_seguro_general", None)
        values.pop("designacion_beneficiario_aportada", None)
        facts_record, family_record = _records(values)
        preview = build_claims_insurance_preview(facts_record, family_record)

        codes = {item.code for item in preview.missing_items}
        self.assertIn("insurance_beneficiary_evidence_missing", codes)
        self.assertIn("insurance_beneficiary_designation_review", codes)

    def test_travel_boundary_has_no_current_basis(self):
        values = _complete_values()
        values["descripcion_hecho"] = _fact(
            "Reclamación de una póliza de seguro de viaje por cancelación."
        )
        values["ramo_seguro"] = _fact("Seguro de viaje")
        values["seguro_viaje_implicado"] = _fact(True)
        facts_record, family_record = _records(values)
        preview = build_claims_insurance_preview(facts_record, family_record)

        self.assertTrue(
            any(
                item.code == "insurance_regime_review"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )
        self.assertTrue(all(not argument.legal_basis for argument in preview.legal_arguments))

    def test_financial_route_requires_admissibility_review(self):
        values = _complete_values()
        values["reclamacion_sac_seguro_fecha"] = _fact("2026-07-26")
        values["reclamacion_sac_seguro_ref"] = _fact("SAC-2026-991")
        values["respuesta_sac_seguro_fecha"] = _fact("2026-08-02")
        values["respuesta_sac_seguro"] = _fact("Se mantiene el rechazo")
        facts_record, family_record = _records(values)
        preview = build_claims_insurance_preview(facts_record, family_record)

        self.assertEqual(
            preview.document_type,
            "RECLAMACIÓN FINANCIERA SOBRE SEGURO — ADMISIBILIDAD PENDIENTE",
        )
        self.assertTrue(
            any(
                item.code == "insurance_financial_complaint_eligibility_review"
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
            build_claims_insurance_preview(facts_record, wrong_family)
        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
