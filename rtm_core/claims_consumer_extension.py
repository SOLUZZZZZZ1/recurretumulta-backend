"""Registro modular de hechos y capacidad para ``claims.consumer``.

La extensión incorpora hechos documentales tipados para consumo general residual.
No sustituye a telecomunicaciones, energía, banca, seguros, comercio electrónico,
viajes, Administración ni servicios profesionales. Tampoco decide conformidad,
abuso, devolución, indemnización, competencia territorial o prescripción.
"""

from __future__ import annotations

from rtm_core.document_fact_catalog import FactFieldSpec
from rtm_core.service_catalog import normalize_code


CLAIMS_CONSUMER_EXTENSION_VERSION = "rtm_claims_consumer_extension_v1_0"

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
        services=("claims",),
        value_type=value_type,
        aliases=aliases,
        min_confidence=confidence,
        merge_mode=merge_mode,
        max_length=max_length,
        allow_negative=allow_negative,
    )


_CONSUMER_DEFINITIONS = (
    _definition("incidencia_consumo_tipo", aliases=("general_consumer_incident_type",), max_length=300),
    _definition("empresa_consumo", aliases=("general_consumer_business_name",), min_confidence=0.97, max_length=280),
    _definition("pais_empresa_consumo", aliases=("general_consumer_business_country",), max_length=160),
    _definition("cliente_consumo_es_consumidor", value_type="boolean", aliases=("general_consumer_client_is_consumer",), min_confidence=0.98),
    _definition("pais_cliente_consumo", aliases=("general_consumer_client_country",), max_length=160),
    _definition("establecimiento_consumo", aliases=("general_consumer_establishment",), max_length=320),
    _definition("contrato_consumo_ref", value_type="identifier", aliases=("general_consumer_contract_reference",), min_confidence=0.98, max_length=180),
    _definition("fecha_contrato_consumo", value_type="date", aliases=("general_consumer_contract_date",), min_confidence=0.98),
    _definition("compra_presencial_consumo", value_type="boolean", aliases=("general_consumer_in_store_purchase",), min_confidence=0.98),
    _definition("contrato_distancia_consumo", value_type="boolean", aliases=("general_consumer_distance_contract",), min_confidence=0.98),
    _definition("contrato_fuera_establecimiento_consumo", value_type="boolean", aliases=("general_consumer_off_premises_contract",), min_confidence=0.98),
    _definition("visita_domicilio_no_solicitada_consumo", value_type="boolean", aliases=("general_consumer_unsolicited_home_visit",), min_confidence=0.98),
    _definition("excursion_promocional_consumo", value_type="boolean", aliases=("general_consumer_promotional_excursion",), min_confidence=0.98),
    _definition("tipo_contrato_consumo", aliases=("general_consumer_contract_type",), min_confidence=0.97, max_length=240),
    _definition("producto_servicio_consumo", aliases=("general_consumer_product_or_service",), min_confidence=0.97, merge_mode="set", max_length=1500),
    _definition("categoria_producto_consumo", aliases=("general_consumer_product_category",), max_length=260),
    _definition("bien_nuevo_consumo", value_type="boolean", aliases=("general_consumer_new_goods",), min_confidence=0.98),
    _definition("bien_segunda_mano_consumo", value_type="boolean", aliases=("general_consumer_second_hand_goods",), min_confidence=0.98),
    _definition("periodo_garantia_segunda_mano_pactado_anios", value_type="number", aliases=("general_consumer_second_hand_agreed_period_years",), min_confidence=0.98),
    _definition("fecha_entrega_consumo", value_type="date", aliases=("general_consumer_delivery_date",), min_confidence=0.98),
    _definition("fecha_inicio_servicio_consumo", value_type="date", aliases=("general_consumer_service_start_date",), min_confidence=0.97),
    _definition("fecha_fin_prevista_servicio_consumo", value_type="date", aliases=("general_consumer_expected_service_end_date",), min_confidence=0.97),
    _definition("fecha_fin_real_servicio_consumo", value_type="date", aliases=("general_consumer_actual_service_end_date",), min_confidence=0.97),
    _definition("precio_publicitado_consumo_eur", value_type="money", aliases=("general_consumer_advertised_price_eur",), min_confidence=0.98),
    _definition("precio_pactado_consumo_eur", value_type="money", aliases=("general_consumer_agreed_price_eur",), min_confidence=0.98),
    _definition("precio_cobrado_consumo_eur", value_type="money", aliases=("general_consumer_charged_price_eur",), min_confidence=0.98),
    _definition("importe_pagado_consumo_eur", value_type="money", aliases=("general_consumer_amount_paid_eur",), min_confidence=0.98),
    _definition("cargo_adicional_consumo_eur", value_type="money", aliases=("general_consumer_additional_charge_eur",), min_confidence=0.98),
    _definition("cargo_adicional_informado_consumo", value_type="boolean", aliases=("general_consumer_additional_charge_disclosed",), min_confidence=0.98),
    _definition("factura_ticket_consumo_ref", value_type="identifier", aliases=("general_consumer_invoice_or_receipt_reference",), min_confidence=0.98, max_length=180),
    _definition("publicidad_oferta_consumo", aliases=("general_consumer_offer_or_advertising",), merge_mode="set", max_length=1800),
    _definition("condiciones_consumo", aliases=("general_consumer_contract_terms",), merge_mode="set", max_length=2000),
    _definition("falta_conformidad_consumo_descripcion", aliases=("general_consumer_nonconformity_description",), min_confidence=0.97, merge_mode="set", max_length=1800),
    _definition("fecha_manifestacion_falta_conformidad_consumo", value_type="date", aliases=("general_consumer_nonconformity_manifestation_date",), min_confidence=0.98),
    _definition("fecha_comunicacion_falta_conformidad_consumo", value_type="date", aliases=("general_consumer_nonconformity_notice_date",), min_confidence=0.98),
    _definition("servicio_consumo_no_prestado", value_type="boolean", aliases=("general_consumer_service_not_performed",), min_confidence=0.98),
    _definition("servicio_consumo_incompleto", value_type="boolean", aliases=("general_consumer_service_incomplete",), min_confidence=0.98),
    _definition("servicio_consumo_defectuoso", value_type="boolean", aliases=("general_consumer_service_defective",), min_confidence=0.98),
    _definition("servicio_consumo_retrasado", value_type="boolean", aliases=("general_consumer_service_delayed",), min_confidence=0.98),
    _definition("incumplimiento_servicio_consumo_descripcion", aliases=("general_consumer_service_breach_description",), min_confidence=0.97, merge_mode="set", max_length=1800),
    _definition("entrega_consumo_realizada", value_type="boolean", aliases=("general_consumer_delivery_completed",), min_confidence=0.98),
    _definition("entrega_consumo_parcial", value_type="boolean", aliases=("general_consumer_partial_delivery",), min_confidence=0.98),
    _definition("fecha_reclamacion_entrega_consumo", value_type="date", aliases=("general_consumer_delivery_claim_date",), min_confidence=0.97),
    _definition("reparacion_consumo_solicitada", value_type="boolean", aliases=("general_consumer_repair_requested",), min_confidence=0.98),
    _definition("reparacion_consumo_ofrecida", value_type="boolean", aliases=("general_consumer_repair_offered",), min_confidence=0.98),
    _definition("reparacion_consumo_completada", value_type="boolean", aliases=("general_consumer_repair_completed",), min_confidence=0.98),
    _definition("fecha_inicio_reparacion_consumo", value_type="date", aliases=("general_consumer_repair_start_date",), min_confidence=0.97),
    _definition("fecha_fin_reparacion_consumo", value_type="date", aliases=("general_consumer_repair_end_date",), min_confidence=0.97),
    _definition("sustitucion_consumo_solicitada", value_type="boolean", aliases=("general_consumer_replacement_requested",), min_confidence=0.98),
    _definition("sustitucion_consumo_ofrecida", value_type="boolean", aliases=("general_consumer_replacement_offered",), min_confidence=0.98),
    _definition("sustitucion_consumo_completada", value_type="boolean", aliases=("general_consumer_replacement_completed",), min_confidence=0.98),
    _definition("reduccion_precio_consumo_solicitada", value_type="boolean", aliases=("general_consumer_price_reduction_requested",), min_confidence=0.98),
    _definition("resolucion_contrato_consumo_solicitada", value_type="boolean", aliases=("general_consumer_contract_termination_requested",), min_confidence=0.98),
    _definition("producto_puesto_disposicion_empresa_consumo", value_type="boolean", aliases=("general_consumer_goods_made_available_to_business",), min_confidence=0.98),
    _definition("producto_retenido_consumidor", value_type="boolean", aliases=("general_consumer_goods_retained",), min_confidence=0.98),
    _definition("fecha_cancelacion_consumo", value_type="date", aliases=("general_consumer_cancellation_date",), min_confidence=0.97),
    _definition("motivo_cancelacion_consumo", aliases=("general_consumer_cancellation_reason",), merge_mode="set", max_length=1200),
    _definition("penalizacion_cancelacion_consumo_eur", value_type="money", aliases=("general_consumer_cancellation_penalty_eur",), min_confidence=0.98),
    _definition("clausula_cancelacion_consumo_aportada", value_type="boolean", aliases=("general_consumer_cancellation_clause_provided",), min_confidence=0.98),
    _definition("importe_reembolso_consumo_solicitado_eur", value_type="money", aliases=("general_consumer_refund_requested_eur",), min_confidence=0.98),
    _definition("importe_reembolso_consumo_efectuado_eur", value_type="money", aliases=("general_consumer_refund_paid_eur",), min_confidence=0.98),
    _definition("fecha_reembolso_consumo", value_type="date", aliases=("general_consumer_refund_date",), min_confidence=0.97),
    _definition("desistimiento_consumo_comunicado", value_type="boolean", aliases=("general_consumer_withdrawal_notified",), min_confidence=0.98),
    _definition("fecha_desistimiento_consumo", value_type="date", aliases=("general_consumer_withdrawal_notice_date",), min_confidence=0.98),
    _definition("informacion_desistimiento_consumo_entregada", value_type="boolean", aliases=("general_consumer_withdrawal_information_delivered",), min_confidence=0.98),
    _definition("inicio_servicio_durante_desistimiento_solicitado", value_type="boolean", aliases=("general_consumer_service_start_during_withdrawal_requested",), min_confidence=0.98),
    _definition("consentimiento_inicio_servicio_consumo", value_type="boolean", aliases=("general_consumer_service_start_express_consent",), min_confidence=0.98),
    _definition("conocimiento_perdida_desistimiento_consumo", value_type="boolean", aliases=("general_consumer_withdrawal_loss_acknowledged",), min_confidence=0.98),
    _definition("servicio_consumo_completamente_ejecutado", value_type="boolean", aliases=("general_consumer_service_fully_performed",), min_confidence=0.98),
    _definition("porcentaje_servicio_consumo_ejecutado", value_type="number", aliases=("general_consumer_service_completion_percentage",), min_confidence=0.98),
    _definition("importe_proporcional_servicio_consumo_eur", value_type="money", aliases=("general_consumer_proportionate_service_amount_eur",), min_confidence=0.98),
    _definition("renovacion_automatica_consumo", value_type="boolean", aliases=("general_consumer_automatic_renewal",), min_confidence=0.98),
    _definition("fecha_aviso_renovacion_consumo", value_type="date", aliases=("general_consumer_renewal_notice_date",), min_confidence=0.97),
    _definition("baja_consumo_solicitada", value_type="boolean", aliases=("general_consumer_termination_requested",), min_confidence=0.98),
    _definition("fecha_baja_consumo", value_type="date", aliases=("general_consumer_termination_request_date",), min_confidence=0.98),
    _definition("baja_consumo_confirmada", value_type="boolean", aliases=("general_consumer_termination_confirmed",), min_confidence=0.98),
    _definition("cobro_posterior_baja_consumo_eur", value_type="money", aliases=("general_consumer_post_termination_charge_eur",), min_confidence=0.98),
    _definition("permanencia_consumo_invocada", value_type="boolean", aliases=("general_consumer_minimum_term_invoked",), min_confidence=0.98),
    _definition("penalizacion_permanencia_consumo_eur", value_type="money", aliases=("general_consumer_minimum_term_penalty_eur",), min_confidence=0.98),
    _definition("clausula_consumo_invocada", aliases=("general_consumer_clause_relied_on",), min_confidence=0.97, merge_mode="set", max_length=1800),
    _definition("clausula_consumo_negociada_individualmente", value_type="boolean", aliases=("general_consumer_clause_individually_negotiated",), min_confidence=0.98),
    _definition("vale_bono_consumo_ref", value_type="identifier", aliases=("general_consumer_voucher_reference",), min_confidence=0.98, max_length=180),
    _definition("fecha_emision_vale_bono_consumo", value_type="date", aliases=("general_consumer_voucher_issue_date",), min_confidence=0.97),
    _definition("fecha_caducidad_vale_bono_consumo", value_type="date", aliases=("general_consumer_voucher_expiry_date",), min_confidence=0.97),
    _definition("importe_vale_bono_consumo_eur", value_type="money", aliases=("general_consumer_voucher_amount_eur",), min_confidence=0.98),
    _definition("deposito_senal_consumo_eur", value_type="money", aliases=("general_consumer_deposit_amount_eur",), min_confidence=0.98),
    _definition("condicion_devolucion_deposito_consumo", aliases=("general_consumer_deposit_refund_term",), min_confidence=0.97, merge_mode="set", max_length=1400),
    _definition("reclamacion_previa_consumo_fecha", value_type="date", aliases=("general_consumer_prior_claim_date",), min_confidence=0.98),
    _definition("reclamacion_previa_consumo_ref", value_type="identifier", aliases=("general_consumer_prior_claim_reference",), min_confidence=0.98, max_length=180),
    _definition("canal_reclamacion_consumo", aliases=("general_consumer_claim_channel",), max_length=220),
    _definition("respuesta_consumo_fecha", value_type="date", aliases=("general_consumer_response_date",), min_confidence=0.97),
    _definition("respuesta_consumo", aliases=("general_consumer_business_response",), min_confidence=0.97, merge_mode="set", max_length=1800),
    _definition("hoja_reclamaciones_consumo_solicitada", value_type="boolean", aliases=("general_consumer_complaint_form_requested",), min_confidence=0.98),
    _definition("hoja_reclamaciones_consumo_entregada", value_type="boolean", aliases=("general_consumer_complaint_form_delivered",), min_confidence=0.98),
    _definition("empresa_adherida_arbitraje_consumo", value_type="boolean", aliases=("general_consumer_business_joined_arbitration",), min_confidence=0.98),
    _definition("entidad_adr_consumo", aliases=("general_consumer_adr_entity",), min_confidence=0.97, max_length=320),
    _definition("fecha_reclamacion_adr_consumo", value_type="date", aliases=("general_consumer_adr_claim_date",), min_confidence=0.97),
    _definition("solucion_solicitada_consumo", aliases=("general_consumer_requested_solution",), min_confidence=0.97, merge_mode="set", max_length=1400),
    _definition("importe_dano_consumo_eur", value_type="money", aliases=("general_consumer_documented_damage_eur",), min_confidence=0.98),
    _definition("prueba_dano_consumo_aportada", value_type="boolean", aliases=("general_consumer_damage_evidence_provided",), min_confidence=0.98),
    _definition("nexo_causal_consumo_documentado", value_type="boolean", aliases=("general_consumer_causation_documented",), min_confidence=0.98),
    _definition("importe_recuperado_terceros_consumo_eur", value_type="money", aliases=("general_consumer_amount_recovered_from_third_parties_eur",), min_confidence=0.98),
    _definition("compra_online_consumo", value_type="boolean", aliases=("general_consumer_online_purchase",), min_confidence=0.98),
    _definition("marketplace_consumo_implicado", value_type="boolean", aliases=("general_consumer_marketplace_involved",), min_confidence=0.98),
    _definition("telecomunicaciones_consumo_implicadas", value_type="boolean", aliases=("general_consumer_telecommunications_involved",), min_confidence=0.98),
    _definition("energia_consumo_implicada", value_type="boolean", aliases=("general_consumer_energy_involved",), min_confidence=0.98),
    _definition("banca_medio_pago_consumo_implicado", value_type="boolean", aliases=("general_consumer_banking_or_payment_involved",), min_confidence=0.98),
    _definition("seguro_consumo_implicado", value_type="boolean", aliases=("general_consumer_insurance_involved",), min_confidence=0.98),
    _definition("viaje_consumo_implicado", value_type="boolean", aliases=("general_consumer_travel_involved",), min_confidence=0.98),
    _definition("servicio_profesional_consumo_implicado", value_type="boolean", aliases=("general_consumer_professional_service_involved",), min_confidence=0.98),
    _definition("administracion_publica_consumo_implicada", value_type="boolean", aliases=("general_consumer_public_administration_involved",), min_confidence=0.98),
    _definition("vivienda_arrendamiento_consumo_implicado", value_type="boolean", aliases=("general_consumer_housing_or_tenancy_involved",), min_confidence=0.98),
    _definition("servicio_sanitario_consumo_implicado", value_type="boolean", aliases=("general_consumer_healthcare_involved",), min_confidence=0.98),
    _definition("servicio_juridico_consumo_implicado", value_type="boolean", aliases=("general_consumer_legal_service_involved",), min_confidence=0.98),
    _definition("inversion_consumo_implicada", value_type="boolean", aliases=("general_consumer_investment_involved",), min_confidence=0.98),
    _definition("proteccion_datos_consumo_principal", value_type="boolean", aliases=("general_consumer_data_protection_primary",), min_confidence=0.98),
    _definition("producto_inseguro_consumo", value_type="boolean", aliases=("general_consumer_unsafe_product",), min_confidence=0.98),
    _definition("lesion_personal_consumo", value_type="boolean", aliases=("general_consumer_personal_injury",), min_confidence=0.98),
    _definition("vehiculo_motor_consumo_implicado", value_type="boolean", aliases=("general_consumer_motor_vehicle_involved",), min_confidence=0.98),
    _definition("contenido_servicio_digital_consumo", value_type="boolean", aliases=("general_consumer_digital_content_or_service",), min_confidence=0.98),
    _definition("empresa_consumo_gran_dimension", value_type="boolean", aliases=("general_consumer_large_business",), min_confidence=0.98),
    _definition("ley_atencion_clientela_consumo_aplicable", value_type="boolean", aliases=("general_consumer_customer_service_act_applicable",), min_confidence=0.98),
    _definition("procedimiento_judicial_consumo_relacionado", value_type="boolean", aliases=("general_consumer_related_court_proceeding",), min_confidence=0.98),
    _definition("proveedor_insolvente_consumo", value_type="boolean", aliases=("general_consumer_supplier_insolvent",), min_confidence=0.98),
)


_CONSUMER_FACTS: tuple[FactFieldSpec, ...] = tuple(
    _spec(definition) for definition in _CONSUMER_DEFINITIONS
)


def _install_document_facts() -> None:
    import rtm_core.document_fact_catalog as catalog

    registered = {spec.key for spec in catalog._BY_SERVICE["claims"].values()}
    new_specs = tuple(spec for spec in _CONSUMER_FACTS if spec.key not in registered)
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

    key = ("claims", "consumo")
    profile = catalog._FAMILY_PROFILES.get(key)
    if profile is None:
        raise RuntimeError("No existe la familia claims.consumo")
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
            "claims_consumer_extension": CLAIMS_CONSUMER_EXTENSION_VERSION,
            "claims_consumer_regime": "rtm_claims_consumer_regime_v1_0",
            "claims_consumer_specialist": "rtm_claims_consumer_specialist_v1_0",
            "claims_specialist_registry": "rtm_claims_specialist_registry_v1_0",
        }
    )
    versioning._RUNTIME_LOOKUPS.update(
        {
            "claims_consumer_extension": (
                "rtm_core.claims_consumer_extension",
                "CLAIMS_CONSUMER_EXTENSION_VERSION",
            ),
            "claims_consumer_regime": (
                "rtm_core.claims_consumer_regime",
                "CLAIMS_CONSUMER_REGIME_VERSION",
            ),
            "claims_consumer_specialist": (
                "rtm_core.claims_consumer_specialist",
                "CLAIMS_CONSUMER_SPECIALIST_VERSION",
            ),
            "claims_specialist_registry": (
                "rtm_core.claims_specialist_registry",
                "CLAIMS_SPECIALIST_REGISTRY_VERSION",
            ),
        }
    )


def install_claims_consumer_extension() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_document_facts()
    _install_domain_capability()
    _install_version_inventory()
    _INSTALLED = True
