"""Registro modular de hechos y capacidad para ``claims.insurance``.

Añade hechos tipados para seguros generales sin decidir cobertura, validez de
cláusulas, beneficiarios, dolo, culpa grave, mora o cuantías automáticas.
"""

from __future__ import annotations

from rtm_core.document_fact_catalog import FactFieldSpec
from rtm_core.service_catalog import normalize_code

CLAIMS_INSURANCE_EXTENSION_VERSION = "rtm_claims_insurance_extension_v1_0"
_INSTALLED = False


def _spec(definition: tuple) -> FactFieldSpec:
    key, value_type, aliases, confidence, merge_mode, max_length, allow_negative = definition
    return FactFieldSpec(
        key=key,
        label=key.replace("_", " ").strip().capitalize(),
        services=("claims",),
        value_type=value_type,
        aliases=aliases,
        min_confidence=confidence,
        merge_mode=merge_mode,
        max_length=max_length,
        allow_negative=allow_negative,
    )


_INSURANCE_DEFINITIONS = (
    ('incidencia_seguro_tipo', 'text', ('general_insurance_incident_type',), 0.96, 'single', 280, False),
    ('pais_aseguradora_general', 'text', ('general_insurer_country',), 0.96, 'single', 160, False),
    ('aseguradora_general', 'text', ('general_insurer_name', 'general_insurance_company'), 0.97, 'single', 260, False),
    ('poliza_seguro_ref', 'identifier', ('general_policy_reference', 'general_policy_number'), 0.98, 'single', 180, False),
    ('siniestro_seguro_ref', 'identifier', ('general_insurance_claim_reference', 'general_loss_reference'), 0.98, 'single', 180, False),
    ('ramo_seguro', 'text', ('general_insurance_product_type', 'insurance_line_of_business'), 0.97, 'single', 260, False),
    ('naturaleza_cobertura_seguro', 'text', ('general_insurance_coverage_nature',), 0.97, 'single', 260, False),
    ('tomador_seguro_general', 'text', ('general_policyholder_name',), 0.96, 'single', 260, False),
    ('asegurado_seguro_general', 'text', ('general_insured_name',), 0.96, 'single', 260, False),
    ('beneficiario_seguro_general', 'text', ('general_insurance_beneficiary',), 0.96, 'single', 260, False),
    ('tercero_perjudicado_seguro', 'text', ('insurance_injured_third_party',), 0.96, 'single', 260, False),
    ('mediador_seguro', 'text', ('general_insurance_intermediary', 'insurance_broker_or_agent'), 0.96, 'single', 260, False),
    ('tipo_mediador_seguro', 'text', ('general_insurance_intermediary_type',), 0.96, 'single', 180, False),
    ('seguro_distribuido_banco', 'boolean', ('insurance_distributed_by_bank',), 0.97, 'single', 800, False),
    ('fecha_contratacion_poliza', 'date', ('general_policy_contract_date',), 0.98, 'single', 800, False),
    ('fecha_emision_poliza_seguro', 'date', ('general_policy_issue_date',), 0.97, 'single', 800, False),
    ('fecha_inicio_cobertura_seguro', 'date', ('general_policy_coverage_start',), 0.98, 'single', 800, False),
    ('fecha_fin_cobertura_seguro', 'date', ('general_policy_coverage_end',), 0.98, 'single', 800, False),
    ('fecha_entrega_poliza', 'date', ('general_policy_delivery_date',), 0.97, 'single', 800, False),
    ('fecha_propuesta_seguro', 'date', ('general_insurance_proposal_date',), 0.97, 'single', 800, False),
    ('fecha_siniestro_seguro', 'date', ('general_insurance_loss_date',), 0.98, 'single', 800, False),
    ('fecha_conocimiento_siniestro_seguro', 'date', ('general_insurance_loss_awareness_date',), 0.97, 'single', 800, False),
    ('fecha_comunicacion_siniestro_seguro', 'date', ('general_insurance_notice_date',), 0.98, 'single', 800, False),
    ('fecha_documentacion_completa_seguro', 'date', ('general_insurance_complete_file_date',), 0.97, 'single', 800, False),
    ('fecha_peritacion_seguro', 'date', ('general_insurance_adjustment_date',), 0.97, 'single', 800, False),
    ('fecha_decision_aseguradora', 'date', ('general_insurer_decision_date',), 0.97, 'single', 800, False),
    ('fecha_pago_seguro', 'date', ('general_insurance_payment_date',), 0.97, 'single', 800, False),
    ('coberturas_seguro', 'text', ('general_policy_coverages', 'general_insured_risks'), 0.96, 'set', 1800, False),
    ('exclusiones_seguro', 'text', ('general_policy_exclusions',), 0.96, 'set', 1800, False),
    ('exclusion_invocada_seguro', 'text', ('general_insurance_invoked_exclusion',), 0.98, 'set', 1600, False),
    ('clausula_limitativa_destacada', 'boolean', ('general_limiting_clause_highlighted',), 0.98, 'single', 800, False),
    ('clausula_limitativa_aceptada', 'boolean', ('general_limiting_clause_specifically_accepted',), 0.98, 'single', 800, False),
    ('cuestionario_riesgo_aportado_seguro', 'boolean', ('general_insurance_risk_questionnaire_provided',), 0.98, 'single', 800, False),
    ('pregunta_riesgo_relevante_formulada', 'boolean', ('relevant_risk_question_asked',), 0.97, 'single', 800, False),
    ('inexactitud_riesgo_invocada', 'boolean', ('risk_misrepresentation_invoked',), 0.97, 'single', 800, False),
    ('dolo_culpa_grave_invocado', 'boolean', ('insurance_fraud_or_gross_fault_invoked',), 0.97, 'single', 800, False),
    ('agravacion_riesgo_invocada', 'boolean', ('aggravation_of_risk_invoked',), 0.97, 'single', 800, False),
    ('prima_tipo', 'text', ('insurance_premium_sequence_type',), 0.96, 'single', 120, False),
    ('fecha_vencimiento_prima', 'date', ('insurance_premium_due_date',), 0.98, 'single', 800, False),
    ('fecha_pago_prima', 'date', ('insurance_premium_payment_date',), 0.98, 'single', 800, False),
    ('prima_pagada', 'boolean', ('insurance_premium_paid',), 0.98, 'single', 800, False),
    ('fecha_suspension_cobertura_invocada', 'date', ('insurance_coverage_suspension_date_invoked',), 0.97, 'single', 800, False),
    ('fecha_reactivacion_cobertura', 'date', ('insurance_coverage_reactivation_date',), 0.97, 'single', 800, False),
    ('suma_asegurada_eur', 'money', ('general_sum_insured_eur',), 0.98, 'single', 800, False),
    ('limite_cobertura_seguro_eur', 'money', ('general_coverage_limit_eur',), 0.98, 'single', 800, False),
    ('franquicia_seguro_eur', 'money', ('general_insurance_deductible_eur',), 0.98, 'single', 800, False),
    ('valor_interes_asegurado_eur', 'money', ('insured_interest_value_eur',), 0.98, 'single', 800, False),
    ('importe_dano_peritado_eur', 'money', ('adjusted_damage_amount_eur',), 0.98, 'single', 800, False),
    ('importe_reclamado_seguro_eur', 'money', ('general_insurance_claimed_amount_eur',), 0.98, 'single', 800, False),
    ('importe_ofertado_aseguradora_eur', 'money', ('general_insurer_offer_amount_eur',), 0.98, 'single', 800, False),
    ('importe_minimo_pagado_eur', 'money', ('general_insurance_minimum_payment_eur',), 0.98, 'single', 800, False),
    ('importe_pagado_seguro_general_eur', 'money', ('general_insurance_amount_paid_eur',), 0.98, 'single', 800, False),
    ('importe_recuperado_terceros_seguro_eur', 'money', ('general_insurance_third_party_recovery_eur',), 0.98, 'single', 800, False),
    ('seguro_concurrente', 'boolean', ('concurrent_insurance_exists',), 0.97, 'single', 800, False),
    ('otra_aseguradora', 'text', ('other_concurrent_insurer',), 0.96, 'single', 260, False),
    ('importe_pagado_otra_aseguradora_eur', 'money', ('other_insurer_amount_paid_eur',), 0.98, 'single', 800, False),
    ('perito_aseguradora', 'text', ('insurer_adjuster_name',), 0.96, 'single', 260, False),
    ('perito_asegurado', 'text', ('policyholder_adjuster_name',), 0.96, 'single', 260, False),
    ('informe_pericial_aportado', 'boolean', ('insurance_adjustment_report_provided',), 0.98, 'single', 800, False),
    ('discrepancia_pericial', 'boolean', ('insurance_adjustment_dispute',), 0.97, 'single', 800, False),
    ('reparacion_ofrecida_seguro', 'boolean', ('insurer_repair_offered',), 0.97, 'single', 800, False),
    ('reposicion_ofrecida_seguro', 'boolean', ('insurer_replacement_offered',), 0.97, 'single', 800, False),
    ('motivo_rechazo_seguro', 'text', ('general_insurance_denial_reason',), 0.98, 'set', 1600, False),
    ('decision_aseguradora_seguro', 'text', ('general_insurance_decision',), 0.98, 'set', 1600, False),
    ('reclamacion_sac_seguro_fecha', 'date', ('general_insurance_sac_complaint_date',), 0.98, 'single', 800, False),
    ('reclamacion_sac_seguro_ref', 'identifier', ('general_insurance_sac_complaint_reference',), 0.97, 'single', 180, False),
    ('respuesta_sac_seguro_fecha', 'date', ('general_insurance_sac_response_date',), 0.97, 'single', 800, False),
    ('respuesta_sac_seguro', 'text', ('general_insurance_sac_response',), 0.97, 'set', 1600, False),
    ('solucion_solicitada_seguro', 'text', ('general_insurance_requested_solution',), 0.96, 'set', 1000, False),
    ('autorizacion_medica_solicitada', 'boolean', ('health_insurance_authorisation_requested',), 0.97, 'single', 800, False),
    ('autorizacion_medica_denegada', 'boolean', ('health_insurance_authorisation_denied',), 0.98, 'single', 800, False),
    ('tratamiento_seguro', 'text', ('insured_treatment_description',), 0.96, 'set', 1000, False),
    ('gasto_sanitario_seguro_eur', 'money', ('health_insurance_expense_eur',), 0.98, 'single', 800, False),
    ('preexistencia_salud_invocada', 'boolean', ('health_preexisting_condition_invoked',), 0.97, 'single', 800, False),
    ('fallecimiento_asegurado', 'boolean', ('insured_death_occurred',), 0.98, 'single', 800, False),
    ('fecha_fallecimiento_asegurado', 'date', ('insured_death_date',), 0.98, 'single', 800, False),
    ('designacion_beneficiario_aportada', 'boolean', ('beneficiary_designation_provided',), 0.98, 'single', 800, False),
    ('capital_vida_eur', 'money', ('life_insurance_capital_eur',), 0.98, 'single', 800, False),
    ('responsabilidad_civil_implicada', 'boolean', ('liability_insurance_involved',), 0.97, 'single', 800, False),
    ('reclamacion_directa_tercero', 'boolean', ('third_party_direct_action_claim',), 0.97, 'single', 800, False),
    ('culpa_responsabilidad_discutida', 'boolean', ('liability_fault_disputed',), 0.97, 'single', 800, False),
    ('danos_tercero_eur', 'money', ('third_party_damage_amount_eur',), 0.98, 'single', 800, False),
    ('oposicion_prorroga_tomador_fecha', 'date', ('policyholder_nonrenewal_notice_date',), 0.97, 'single', 800, False),
    ('oposicion_prorroga_asegurador_fecha', 'date', ('insurer_nonrenewal_notice_date',), 0.97, 'single', 800, False),
    ('modificacion_poliza_aviso_fecha', 'date', ('insurer_policy_change_notice_date',), 0.97, 'single', 800, False),
    ('renovacion_poliza_fecha', 'date', ('general_policy_renewal_date',), 0.97, 'single', 800, False),
    ('seguro_viaje_implicado', 'boolean', ('travel_insurance_involved_in_claims',), 0.98, 'single', 800, False),
    ('accidente_trafico_terceros_implicado', 'boolean', ('motor_third_party_bodily_injury_involved',), 0.98, 'single', 800, False),
    ('producto_inversion_seguro_implicado', 'boolean', ('insurance_investment_product_involved',), 0.98, 'single', 800, False),
    ('plan_pensiones_implicado', 'boolean', ('pension_plan_involved',), 0.98, 'single', 800, False),
)

_INSURANCE_FACTS: tuple[FactFieldSpec, ...] = tuple(
    _spec(definition) for definition in _INSURANCE_DEFINITIONS
)


def _install_document_facts() -> None:
    import rtm_core.document_fact_catalog as catalog

    registered = {spec.key for spec in catalog._BY_SERVICE["claims"].values()}
    new_specs = tuple(spec for spec in _INSURANCE_FACTS if spec.key not in registered)
    if not new_specs:
        return
    index = catalog._BY_SERVICE["claims"]
    for spec in new_specs:
        for raw_name in (spec.key, *spec.aliases):
            name = normalize_code(raw_name)
            current = index.get(name)
            if current is not None and current.key != spec.key:
                raise RuntimeError(
                    f"Alias ambiguo {raw_name!r} en claims: "
                    f"{current.key!r} frente a {spec.key!r}"
                )
            index[name] = spec
    catalog._FIELDS = (*catalog._FIELDS, *new_specs)


def _install_domain_capability() -> None:
    import rtm_core.domain_catalog as catalog

    key = ("claims", "seguros")
    profile = catalog._FAMILY_PROFILES.get(key)
    if profile is None:
        raise RuntimeError("No existe la familia claims.seguros")
    ready = profile.model_copy(update={"capability": "specialist_ready"})
    catalog._FAMILY_PROFILES[key] = ready
    catalog._FAMILY_ITEMS = tuple(
        ready if (item.department, item.family) == key else item
        for item in catalog._FAMILY_ITEMS
    )


def _install_version_inventory() -> None:
    import rtm_core.versioning as versioning

    versioning.DECLARED_COMPONENT_VERSIONS.update(
        {
            "claims_insurance_extension": CLAIMS_INSURANCE_EXTENSION_VERSION,
            "claims_insurance_regime": "rtm_claims_insurance_regime_v1_0",
            "claims_insurance_specialist": "rtm_claims_insurance_specialist_v1_0",
            "claims_specialist_registry": "rtm_claims_specialist_registry_v1_0",
        }
    )
    versioning._RUNTIME_LOOKUPS.update(
        {
            "claims_insurance_extension": (
                "rtm_core.claims_insurance_extension",
                "CLAIMS_INSURANCE_EXTENSION_VERSION",
            ),
            "claims_insurance_regime": (
                "rtm_core.claims_insurance_regime",
                "CLAIMS_INSURANCE_REGIME_VERSION",
            ),
            "claims_insurance_specialist": (
                "rtm_core.claims_insurance_specialist",
                "CLAIMS_INSURANCE_SPECIALIST_VERSION",
            ),
            "claims_specialist_registry": (
                "rtm_core.claims_specialist_registry",
                "CLAIMS_SPECIALIST_REGISTRY_VERSION",
            ),
        }
    )


def install_claims_insurance_extension() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_document_facts()
    _install_domain_capability()
    _install_version_inventory()
    _INSTALLED = True
