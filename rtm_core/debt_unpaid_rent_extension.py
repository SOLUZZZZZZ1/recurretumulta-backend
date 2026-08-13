"""Registro modular de hechos y capacidad para ``debt.unpaid_rent``.

Añade hechos documentales tipados para rentas y cantidades de arrendamiento
impagadas. No declara la deuda, no calcula actualizaciones, intereses o costas,
no compensa la fianza y no decide desahucio, enervación, vulnerabilidad,
prescripción, competencia o legitimación sin revisión jurídica.
"""

from __future__ import annotations

from rtm_core.document_fact_catalog import FactFieldSpec
from rtm_core.service_catalog import normalize_code


DEBT_UNPAID_RENT_EXTENSION_VERSION = "rtm_debt_unpaid_rent_extension_v1_0"

_INSTALLED = False


def _definition(
    key: str,
    *,
    value_type: str = "text",
    aliases: tuple[str, ...] = (),
    min_confidence: float = 0.96,
    merge_mode: str = "single",
    max_length: int = 800,
    allow_negative: bool = False,
) -> tuple:
    return (
        key,
        value_type,
        aliases,
        min_confidence,
        merge_mode,
        max_length,
        allow_negative,
    )


def _spec(definition: tuple) -> FactFieldSpec:
    key, value_type, aliases, confidence, merge_mode, max_length, allow_negative = definition
    return FactFieldSpec(
        key=key,
        label=key.replace("_", " ").strip().capitalize(),
        services=("debt",),
        value_type=value_type,
        aliases=aliases,
        min_confidence=confidence,
        merge_mode=merge_mode,
        max_length=max_length,
        allow_negative=allow_negative,
    )


_RENT_DEFINITIONS = (
    # Encuadre, partes e inmueble.
    _definition("incidencia_alquiler_impagado_tipo", aliases=("unpaid_rent_incident_type",), max_length=300),
    _definition("pais_inmueble_alquiler", aliases=("unpaid_rent_property_country",), min_confidence=0.97, max_length=160),
    _definition("provincia_inmueble_alquiler", aliases=("unpaid_rent_property_province",), max_length=160),
    _definition("municipio_inmueble_alquiler", aliases=("unpaid_rent_property_municipality",), max_length=180),
    _definition("direccion_inmueble_alquiler", aliases=("unpaid_rent_property_address",), min_confidence=0.97, max_length=420),
    _definition("referencia_catastral_alquiler", value_type="identifier", aliases=("unpaid_rent_cadastral_reference",), min_confidence=0.98, max_length=40),
    _definition("arrendador", aliases=("unpaid_rent_landlord_name",), min_confidence=0.97, max_length=280),
    _definition("arrendatario", aliases=("unpaid_rent_tenant_name",), min_confidence=0.97, max_length=280),
    _definition("coarrendatarios", aliases=("unpaid_rent_co_tenants",), min_confidence=0.96, merge_mode="set", max_length=1000),
    _definition("fiador_avalista_arrendamiento", aliases=("unpaid_rent_guarantor_name",), min_confidence=0.97, merge_mode="set", max_length=700),
    _definition("garantia_arrendamiento_aportada", value_type="boolean", aliases=("unpaid_rent_guarantee_document_provided",), min_confidence=0.98),
    _definition("alcance_garantia_arrendamiento", aliases=("unpaid_rent_guarantee_scope",), min_confidence=0.97, merge_mode="set", max_length=1400),
    _definition("requerimiento_fiador_alquiler_fecha", value_type="date", aliases=("unpaid_rent_guarantor_demand_date",), min_confidence=0.97),
    _definition("parte_reclamante_alquiler", aliases=("unpaid_rent_claimant_role",), min_confidence=0.97, max_length=220),
    _definition("arrendador_reclama_deuda", value_type="boolean", aliases=("unpaid_rent_landlord_is_claimant",), min_confidence=0.98),
    _definition("parte_arrendataria_defiende_deuda", value_type="boolean", aliases=("unpaid_rent_tenant_defence",), min_confidence=0.98),
    _definition("cesion_credito_arrendamiento_documentada", value_type="boolean", aliases=("unpaid_rent_assignment_documented",), min_confidence=0.98),
    _definition("aseguradora_subrogada_alquiler", value_type="boolean", aliases=("unpaid_rent_insurer_subrogated",), min_confidence=0.98),

    # Contrato y posesión.
    _definition("contrato_arrendamiento_ref", value_type="identifier", aliases=("unpaid_rent_lease_reference",), min_confidence=0.98, max_length=180),
    _definition("fecha_contrato_arrendamiento", value_type="date", aliases=("unpaid_rent_contract_date",), min_confidence=0.98),
    _definition("fecha_inicio_arrendamiento", value_type="date", aliases=("unpaid_rent_lease_start_date",), min_confidence=0.98),
    _definition("fecha_fin_arrendamiento", value_type="date", aliases=("unpaid_rent_lease_end_date",), min_confidence=0.97),
    _definition("uso_arrendamiento", aliases=("unpaid_rent_lease_use",), min_confidence=0.97, max_length=320),
    _definition("vivienda_habitual_arrendatario", value_type="boolean", aliases=("unpaid_rent_habitual_dwelling",), min_confidence=0.98),
    _definition("arrendamiento_habitacion", value_type="boolean", aliases=("unpaid_rent_room_lease",), min_confidence=0.98),
    _definition("arrendamiento_temporada", value_type="boolean", aliases=("unpaid_rent_seasonal_lease",), min_confidence=0.98),
    _definition("arrendamiento_turistico", value_type="boolean", aliases=("unpaid_rent_tourist_lease",), min_confidence=0.98),
    _definition("arrendamiento_rustico", value_type="boolean", aliases=("unpaid_rent_rural_lease",), min_confidence=0.98),
    _definition("vivienda_publica_social_arrendada", value_type="boolean", aliases=("unpaid_rent_public_social_housing",), min_confidence=0.98),
    _definition("subarriendo_arrendamiento", value_type="boolean", aliases=("unpaid_rent_sublease",), min_confidence=0.98),
    _definition("contrato_arrendamiento_aportado", value_type="boolean", aliases=("unpaid_rent_lease_document_provided",), min_confidence=0.98),
    _definition("contrato_arrendamiento_vigente", value_type="boolean", aliases=("unpaid_rent_lease_active",), min_confidence=0.98),
    _definition("posesion_inmueble_devuelta", value_type="boolean", aliases=("unpaid_rent_possession_returned",), min_confidence=0.98),
    _definition("fecha_entrega_llaves_alquiler", value_type="date", aliases=("unpaid_rent_keys_return_date",), min_confidence=0.98),
    _definition("entrega_llaves_alquiler_acreditada", value_type="boolean", aliases=("unpaid_rent_keys_return_documented",), min_confidence=0.98),

    # Renta, periodos, conceptos y saldo.
    _definition("renta_mensual_pactada_eur", value_type="money", aliases=("unpaid_rent_monthly_agreed_rent_eur",), min_confidence=0.98),
    _definition("renta_actualizada_mensual_eur", value_type="money", aliases=("unpaid_rent_updated_monthly_rent_eur",), min_confidence=0.98),
    _definition("periodicidad_pago_renta", aliases=("unpaid_rent_payment_frequency",), max_length=160),
    _definition("dia_vencimiento_renta", value_type="integer", aliases=("unpaid_rent_due_day",), min_confidence=0.98),
    _definition("fecha_primer_impago_alquiler", value_type="date", aliases=("unpaid_rent_first_unpaid_date",), min_confidence=0.98),
    _definition("fecha_ultimo_impago_alquiler", value_type="date", aliases=("unpaid_rent_last_unpaid_date",), min_confidence=0.98),
    _definition("mensualidades_impagadas_numero", value_type="number", aliases=("unpaid_rent_month_count",), min_confidence=0.98),
    _definition("renta_impagada_principal_eur", value_type="money", aliases=("unpaid_rent_principal_eur",), min_confidence=0.98),
    _definition("suministros_impagados_alquiler_eur", value_type="money", aliases=("unpaid_rent_utilities_eur",), min_confidence=0.98),
    _definition("gastos_comunidad_impagados_alquiler_eur", value_type="money", aliases=("unpaid_rent_community_fees_eur",), min_confidence=0.98),
    _definition("ibi_repercutido_impagado_alquiler_eur", value_type="money", aliases=("unpaid_rent_property_tax_eur",), min_confidence=0.98),
    _definition("otros_conceptos_arrendamiento_impagados_eur", value_type="money", aliases=("unpaid_rent_other_charges_eur",), min_confidence=0.98),
    _definition("desglose_otros_conceptos_arrendamiento", aliases=("unpaid_rent_other_charges_breakdown",), min_confidence=0.97, merge_mode="set", max_length=1800),
    _definition("total_reclamado_alquiler_eur", value_type="money", aliases=("unpaid_rent_total_claimed_eur",), min_confidence=0.98),
    _definition("pagos_parciales_alquiler_eur", value_type="money", aliases=("unpaid_rent_partial_payments_eur",), min_confidence=0.98),
    _definition("fecha_ultimo_pago_alquiler", value_type="date", aliases=("unpaid_rent_last_payment_date",), min_confidence=0.97),
    _definition("abonos_descuentos_alquiler_eur", value_type="money", aliases=("unpaid_rent_credits_eur",), min_confidence=0.98),
    _definition("compensacion_invocada_arrendatario_eur", value_type="money", aliases=("unpaid_rent_tenant_setoff_eur",), min_confidence=0.98),
    _definition("fianza_arrendamiento_eur", value_type="money", aliases=("unpaid_rent_deposit_eur",), min_confidence=0.98),
    _definition("fianza_aplicada_deuda_alquiler", value_type="boolean", aliases=("unpaid_rent_deposit_applied",), min_confidence=0.98),
    _definition("importe_fianza_aplicado_deuda_eur", value_type="money", aliases=("unpaid_rent_deposit_applied_eur",), min_confidence=0.98),
    _definition("saldo_pendiente_alquiler_eur", value_type="money", aliases=("unpaid_rent_outstanding_balance_eur",), min_confidence=0.98),
    _definition("recibos_alquiler_aportados", value_type="boolean", aliases=("unpaid_rent_receipts_provided",), min_confidence=0.98),
    _definition("extracto_bancario_alquiler_aportado", value_type="boolean", aliases=("unpaid_rent_bank_statement_provided",), min_confidence=0.98),
    _definition("pagos_alquiler_efectivo", value_type="boolean", aliases=("unpaid_rent_cash_payments",), min_confidence=0.98),
    _definition("recibo_pago_alquiler_entregado", value_type="boolean", aliases=("unpaid_rent_cash_receipt_delivered",), min_confidence=0.98),
    _definition("renta_actualizacion_documentada", value_type="boolean", aliases=("unpaid_rent_increase_documented",), min_confidence=0.98),
    _definition("renta_actualizacion_discutida", value_type="boolean", aliases=("unpaid_rent_increase_disputed",), min_confidence=0.98),
    _definition("gastos_repercutidos_arrendamiento_pactados", value_type="boolean", aliases=("unpaid_rent_pass_through_charges_agreed",), min_confidence=0.98),
    _definition("deuda_alquiler_discutida", value_type="boolean", aliases=("unpaid_rent_debt_disputed",), min_confidence=0.98),
    _definition("deuda_alquiler_pagada", value_type="boolean", aliases=("unpaid_rent_debt_paid",), min_confidence=0.98),
    _definition("motivo_oposicion_alquiler", aliases=("unpaid_rent_defence_reason",), min_confidence=0.97, merge_mode="set", max_length=1800),
    _definition("pago_alquiler_acreditado", value_type="boolean", aliases=("unpaid_rent_payment_proved",), min_confidence=0.98),
    _definition("consignacion_alquiler_judicial_notarial", value_type="boolean", aliases=("unpaid_rent_court_or_notarial_deposit",), min_confidence=0.98),
    _definition("inhabitabilidad_arrendamiento_invocada", value_type="boolean", aliases=("unpaid_rent_uninhabitability_invoked",), min_confidence=0.98),
    _definition("suspension_renta_obras_invocada", value_type="boolean", aliases=("unpaid_rent_works_suspension_invoked",), min_confidence=0.98),
    _definition("obras_a_cambio_renta_pactadas", value_type="boolean", aliases=("unpaid_rent_works_in_lieu_agreed",), min_confidence=0.98),
    _definition("compensacion_creditos_alquiler_invocada", value_type="boolean", aliases=("unpaid_rent_setoff_invoked",), min_confidence=0.98),
    _definition("incumplimiento_arrendador_invocado", value_type="boolean", aliases=("unpaid_rent_landlord_breach_invoked",), min_confidence=0.98),

    # Requerimiento, MASC y enervación.
    _definition("requerimiento_pago_alquiler_fecha", value_type="date", aliases=("unpaid_rent_demand_date",), min_confidence=0.98),
    _definition("requerimiento_pago_alquiler_medio", aliases=("unpaid_rent_demand_channel",), min_confidence=0.97, max_length=220),
    _definition("requerimiento_pago_alquiler_ref", value_type="identifier", aliases=("unpaid_rent_demand_reference",), min_confidence=0.98, max_length=180),
    _definition("requerimiento_pago_alquiler_contenido", aliases=("unpaid_rent_demand_content",), min_confidence=0.97, merge_mode="set", max_length=2200),
    _definition("requerimiento_pago_alquiler_recibido", value_type="boolean", aliases=("unpaid_rent_demand_received",), min_confidence=0.98),
    _definition("fecha_recepcion_requerimiento_alquiler", value_type="date", aliases=("unpaid_rent_demand_received_date",), min_confidence=0.98),
    _definition("plazo_requerimiento_alquiler_dias", value_type="integer", aliases=("unpaid_rent_demand_term_days",), min_confidence=0.98),
    _definition("advertencia_resolucion_desahucio_alquiler", value_type="boolean", aliases=("unpaid_rent_eviction_warning",), min_confidence=0.98),
    _definition("masc_alquiler_iniciado", value_type="boolean", aliases=("unpaid_rent_masc_started",), min_confidence=0.98),
    _definition("masc_alquiler_tipo", aliases=("unpaid_rent_masc_type",), max_length=260),
    _definition("masc_alquiler_fecha_solicitud", value_type="date", aliases=("unpaid_rent_masc_request_date",), min_confidence=0.98),
    _definition("masc_alquiler_fecha_recepcion", value_type="date", aliases=("unpaid_rent_masc_received_date",), min_confidence=0.98),
    _definition("masc_alquiler_objeto_coincidente", value_type="boolean", aliases=("unpaid_rent_masc_same_subject",), min_confidence=0.98),
    _definition("masc_alquiler_resultado", aliases=("unpaid_rent_masc_outcome",), min_confidence=0.97, merge_mode="set", max_length=1200),
    _definition("masc_alquiler_fecha_fin", value_type="date", aliases=("unpaid_rent_masc_end_date",), min_confidence=0.97),
    _definition("masc_alquiler_documento_acreditativo", value_type="boolean", aliases=("unpaid_rent_masc_proof_documented",), min_confidence=0.98),
    _definition("enervacion_previa_alquiler", value_type="boolean", aliases=("unpaid_rent_prior_enervation",), min_confidence=0.98),
    _definition("fecha_enervacion_previa_alquiler", value_type="date", aliases=("unpaid_rent_prior_enervation_date",), min_confidence=0.97),
    _definition("pago_posterior_requerimiento_alquiler", value_type="boolean", aliases=("unpaid_rent_payment_after_demand",), min_confidence=0.98),
    _definition("fecha_pago_posterior_requerimiento_alquiler", value_type="date", aliases=("unpaid_rent_payment_after_demand_date",), min_confidence=0.97),
    _definition("fecha_interrupcion_prescripcion_alquiler", value_type="date", aliases=("unpaid_rent_limitation_interruption_date",), min_confidence=0.97),

    # Pretensión y procedimiento.
    _definition("recuperacion_posesion_alquiler_solicitada", value_type="boolean", aliases=("unpaid_rent_possession_recovery_requested",), min_confidence=0.98),
    _definition("reclamacion_solo_cantidad_alquiler", value_type="boolean", aliases=("unpaid_rent_amount_only_claim",), min_confidence=0.98),
    _definition("reclamacion_rentas_alquiler_solicitada", value_type="boolean", aliases=("unpaid_rent_amount_claim_requested",), min_confidence=0.98),
    _definition("resolucion_contrato_alquiler_solicitada", value_type="boolean", aliases=("unpaid_rent_contract_termination_requested",), min_confidence=0.98),
    _definition("reclamacion_rentas_futuras_solicitada", value_type="boolean", aliases=("unpaid_rent_future_rents_requested",), min_confidence=0.98),
    _definition("accion_judicial_alquiler_prevista", value_type="boolean", aliases=("unpaid_rent_court_action_intended",), min_confidence=0.98),
    _definition("ejecucion_solo_alquiler", value_type="boolean", aliases=("unpaid_rent_execution_only",), min_confidence=0.98),
    _definition("demanda_desahucio_presentada", value_type="boolean", aliases=("unpaid_rent_eviction_claim_filed",), min_confidence=0.98),
    _definition("fecha_demanda_desahucio", value_type="date", aliases=("unpaid_rent_court_filing_date",), min_confidence=0.98),
    _definition("numero_procedimiento_desahucio", value_type="identifier", aliases=("unpaid_rent_court_case_number",), min_confidence=0.98, max_length=180),
    _definition("organo_judicial_desahucio", aliases=("unpaid_rent_court",), min_confidence=0.97, max_length=320),
    _definition("oposicion_desahucio_presentada", value_type="boolean", aliases=("unpaid_rent_opposition_filed",), min_confidence=0.98),
    _definition("fecha_vista_desahucio", value_type="date", aliases=("unpaid_rent_hearing_date",), min_confidence=0.98),
    _definition("fecha_lanzamiento_desahucio", value_type="date", aliases=("unpaid_rent_eviction_date",), min_confidence=0.98),
    _definition("lanzamiento_desahucio_suspendido", value_type="boolean", aliases=("unpaid_rent_eviction_suspended",), min_confidence=0.98),
    _definition("motivo_suspension_lanzamiento", aliases=("unpaid_rent_eviction_suspension_reason",), min_confidence=0.97, merge_mode="set", max_length=1400),
    _definition("sentencia_desahucio_dictada", value_type="boolean", aliases=("unpaid_rent_judgment_issued",), min_confidence=0.98),
    _definition("sentencia_desahucio_firme", value_type="boolean", aliases=("unpaid_rent_judgment_final",), min_confidence=0.98),
    _definition("ejecucion_lanzamiento_iniciada", value_type="boolean", aliases=("unpaid_rent_enforcement_started",), min_confidence=0.98),
    _definition("procedimiento_judicial_relacionado_alquiler", value_type="boolean", aliases=("unpaid_rent_related_court_proceeding",), min_confidence=0.98),

    # Vulnerabilidad y situación del arrendador.
    _definition("arrendador_persona_fisica", value_type="boolean", aliases=("unpaid_rent_landlord_natural_person",), min_confidence=0.98),
    _definition("arrendador_persona_juridica", value_type="boolean", aliases=("unpaid_rent_landlord_legal_person",), min_confidence=0.98),
    _definition("arrendador_gran_tenedor", value_type="boolean", aliases=("unpaid_rent_landlord_large_holder",), min_confidence=0.98),
    _definition("numero_viviendas_arrendador", value_type="integer", aliases=("unpaid_rent_landlord_dwelling_count",), min_confidence=0.98),
    _definition("certificado_registro_propiedades_aportado", value_type="boolean", aliases=("unpaid_rent_property_registry_certificate",), min_confidence=0.98),
    _definition("arrendatario_vulnerable_alegado", value_type="boolean", aliases=("unpaid_rent_tenant_vulnerability_alleged",), min_confidence=0.98),
    _definition("arrendatario_vulnerable_acreditado", value_type="boolean", aliases=("unpaid_rent_tenant_vulnerability_documented",), min_confidence=0.98),
    _definition("alternativa_habitacional_arrendatario", value_type="boolean", aliases=("unpaid_rent_tenant_housing_alternative",), min_confidence=0.98),
    _definition("servicios_sociales_informe_alquiler", value_type="boolean", aliases=("unpaid_rent_social_services_report",), min_confidence=0.98),
    _definition("fecha_informe_servicios_sociales_alquiler", value_type="date", aliases=("unpaid_rent_social_services_report_date",), min_confidence=0.97),
    _definition("arrendador_vulnerable_alegado", value_type="boolean", aliases=("unpaid_rent_landlord_vulnerability_alleged",), min_confidence=0.98),
    _definition("arrendador_vulnerable_acreditado", value_type="boolean", aliases=("unpaid_rent_landlord_vulnerability_documented",), min_confidence=0.98),
    _definition("menores_dependientes_vivienda", value_type="boolean", aliases=("unpaid_rent_dependent_minors",), min_confidence=0.98),
    _definition("discapacidad_dependencia_vivienda", value_type="boolean", aliases=("unpaid_rent_disability_dependency",), min_confidence=0.98),
    _definition("vivienda_habitual_proceso_alquiler", value_type="boolean", aliases=("unpaid_rent_proceeding_habitual_dwelling",), min_confidence=0.98),
    _definition("procedimiento_conciliacion_intermediacion_vivienda", value_type="boolean", aliases=("unpaid_rent_housing_mediation",), min_confidence=0.98),
    _definition("compensacion_arrendador_suspension_solicitada", value_type="boolean", aliases=("unpaid_rent_landlord_suspension_compensation_requested",), min_confidence=0.98),
    _definition("fecha_compensacion_arrendador_suspension", value_type="date", aliases=("unpaid_rent_landlord_suspension_compensation_date",), min_confidence=0.97),

    # Seguro, recuperaciones y acuerdo.
    _definition("seguro_impago_alquiler", value_type="boolean", aliases=("unpaid_rent_insurance_present",), min_confidence=0.98),
    _definition("aseguradora_impago_alquiler", aliases=("unpaid_rent_insurer_name",), min_confidence=0.97, max_length=280),
    _definition("siniestro_impago_alquiler_ref", value_type="identifier", aliases=("unpaid_rent_insurance_claim_reference",), min_confidence=0.98, max_length=180),
    _definition("indemnizacion_seguro_impago_alquiler_eur", value_type="money", aliases=("unpaid_rent_insurance_payment_eur",), min_confidence=0.98),
    _definition("aval_fianza_cobrado_alquiler_eur", value_type="money", aliases=("unpaid_rent_guarantee_recovered_eur",), min_confidence=0.98),
    _definition("importe_recuperado_terceros_alquiler_eur", value_type="money", aliases=("unpaid_rent_third_party_recovery_eur",), min_confidence=0.98),
    _definition("acuerdo_pago_alquiler", value_type="boolean", aliases=("unpaid_rent_payment_plan",), min_confidence=0.98),
    _definition("calendario_acuerdo_pago_alquiler", aliases=("unpaid_rent_payment_plan_schedule",), min_confidence=0.97, merge_mode="set", max_length=1600),
    _definition("acuerdo_pago_alquiler_incumplido", value_type="boolean", aliases=("unpaid_rent_payment_plan_breached",), min_confidence=0.98),
    _definition("procedimiento_concursal_arrendatario", value_type="boolean", aliases=("unpaid_rent_tenant_insolvency_proceeding",), min_confidence=0.98),
    _definition("solucion_solicitada_alquiler", aliases=("unpaid_rent_requested_outcome",), min_confidence=0.97, merge_mode="set", max_length=1400),
)


_RENT_FACTS: tuple[FactFieldSpec, ...] = tuple(
    _spec(definition) for definition in _RENT_DEFINITIONS
)


def _install_document_facts() -> None:
    import rtm_core.document_fact_catalog as catalog

    registered = {spec.key for spec in catalog._BY_SERVICE["debt"].values()}
    new_specs = tuple(spec for spec in _RENT_FACTS if spec.key not in registered)
    if not new_specs:
        return
    index = catalog._BY_SERVICE["debt"]
    for spec in new_specs:
        for raw_name in (spec.key, *spec.aliases):
            name = normalize_code(raw_name)
            current = index.get(name)
            if current is not None and current.key != spec.key:
                raise RuntimeError(
                    f"Alias ambiguo {raw_name!r} en debt: "
                    f"{current.key!r} frente a {spec.key!r}"
                )
            index[name] = spec
    catalog._FIELDS = (*catalog._FIELDS, *new_specs)


def _install_domain_capability() -> None:
    import rtm_core.domain_catalog as catalog

    key = ("debt", "alquiler_impagado")
    profile = catalog._FAMILY_PROFILES.get(key)
    if profile is None:
        raise RuntimeError("No existe la familia debt.alquiler_impagado")
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
            "debt_unpaid_rent_extension": DEBT_UNPAID_RENT_EXTENSION_VERSION,
            "debt_unpaid_rent_regime": "rtm_debt_unpaid_rent_regime_v1_0",
            "debt_unpaid_rent_specialist": "rtm_debt_unpaid_rent_specialist_v1_0",
            "debt_specialist_registry": "rtm_debt_specialist_registry_v1_0",
        }
    )
    versioning._RUNTIME_LOOKUPS.update(
        {
            "debt_unpaid_rent_extension": (
                "rtm_core.debt_unpaid_rent_extension",
                "DEBT_UNPAID_RENT_EXTENSION_VERSION",
            ),
            "debt_unpaid_rent_regime": (
                "rtm_core.debt_unpaid_rent_regime",
                "DEBT_UNPAID_RENT_REGIME_VERSION",
            ),
            "debt_unpaid_rent_specialist": (
                "rtm_core.debt_unpaid_rent_specialist",
                "DEBT_UNPAID_RENT_SPECIALIST_VERSION",
            ),
            "debt_specialist_registry": (
                "rtm_core.debt_specialist_registry",
                "DEBT_SPECIALIST_REGISTRY_VERSION",
            ),
        }
    )


def install_debt_unpaid_rent_extension() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_document_facts()
    _install_domain_capability()
    _install_version_inventory()
    _INSTALLED = True
