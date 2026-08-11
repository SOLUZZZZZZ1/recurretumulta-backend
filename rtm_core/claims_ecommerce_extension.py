"""Registro modular de hechos y capacidad para ``claims.ecommerce``.

Añade hechos documentales tipados para pedidos, vendedores, marketplaces,
entregas, desistimiento, conformidad, devoluciones y contenidos digitales.
No convierte a la plataforma en vendedora por defecto, no presume que toda
devolución sea desistimiento y no aplica remedios de consumo a ventas entre
particulares sin revisar antes la condición del vendedor.
"""

from __future__ import annotations

from rtm_core.document_fact_catalog import FactFieldSpec
from rtm_core.service_catalog import normalize_code


CLAIMS_ECOMMERCE_EXTENSION_VERSION = "rtm_claims_ecommerce_extension_v1_0"

_INSTALLED = False


def _spec(
    key: str,
    *,
    value_type: str = "text",
    aliases: tuple[str, ...] = (),
    min_confidence: float = 0.96,
    merge_mode: str = "single",
    max_length: int = 800,
    allow_negative: bool = False,
) -> FactFieldSpec:
    return FactFieldSpec(
        key=key,
        label=key.replace("_", " ").strip().capitalize(),
        services=("claims",),
        value_type=value_type,
        aliases=aliases,
        min_confidence=min_confidence,
        merge_mode=merge_mode,
        max_length=max_length,
        allow_negative=allow_negative,
    )


_ECOMMERCE_FACTS: tuple[FactFieldSpec, ...] = (
    _spec(
        "incidencia_ecommerce_tipo",
        aliases=("ecommerce_incident_type", "online_purchase_incident_type"),
        max_length=260,
    ),
    _spec(
        "pais_vendedor",
        aliases=("seller_country", "merchant_country"),
        max_length=160,
    ),
    _spec(
        "pais_consumidor",
        aliases=("consumer_country", "buyer_country"),
        max_length=160,
    ),
    _spec(
        "contrato_a_distancia",
        value_type="boolean",
        aliases=("distance_contract", "online_contract"),
        min_confidence=0.98,
    ),
    _spec(
        "vendedor_online",
        aliases=("online_seller", "merchant_name", "seller_name"),
        min_confidence=0.97,
        max_length=260,
    ),
    _spec(
        "vendedor_es_empresario",
        value_type="boolean",
        aliases=("seller_is_trader", "merchant_is_business"),
        min_confidence=0.98,
    ),
    _spec(
        "vendedor_domicilio",
        aliases=("seller_address", "merchant_address"),
        min_confidence=0.96,
        max_length=500,
    ),
    _spec(
        "vendedor_identificador_fiscal",
        value_type="identifier",
        aliases=("seller_tax_id", "merchant_tax_identifier"),
        min_confidence=0.97,
        max_length=120,
    ),
    _spec(
        "marketplace",
        aliases=("online_marketplace", "platform_name"),
        min_confidence=0.96,
        max_length=260,
    ),
    _spec(
        "marketplace_es_parte_contractual",
        value_type="boolean",
        aliases=("marketplace_is_contracting_party",),
        min_confidence=0.98,
    ),
    _spec(
        "marketplace_vendedor_identificado",
        value_type="boolean",
        aliases=("marketplace_seller_identified", "seller_identity_disclosed"),
        min_confidence=0.98,
    ),
    _spec(
        "marketplace_informa_condicion_empresario",
        value_type="boolean",
        aliases=("marketplace_disclosed_trader_status",),
        min_confidence=0.98,
    ),
    _spec(
        "marketplace_reparte_obligaciones",
        aliases=("marketplace_obligation_allocation", "platform_role_information"),
        merge_mode="set",
        max_length=1200,
    ),
    _spec(
        "marketplace_ofrece_pago",
        value_type="boolean",
        aliases=("marketplace_provides_payment",),
        min_confidence=0.97,
    ),
    _spec(
        "marketplace_ofrece_logistica",
        value_type="boolean",
        aliases=("marketplace_provides_fulfilment", "platform_provides_logistics"),
        min_confidence=0.97,
    ),
    _spec(
        "marketplace_conoce_producto_ilegal",
        value_type="boolean",
        aliases=("marketplace_knows_illegal_product",),
        min_confidence=0.98,
    ),
    _spec(
        "marketplace_aviso_producto_ilegal_fecha",
        value_type="date",
        aliases=("marketplace_illegal_product_notice_date",),
        min_confidence=0.97,
    ),
    _spec(
        "pedido_tipo_contrato",
        aliases=("ecommerce_contract_type", "order_contract_type"),
        max_length=240,
    ),
    _spec(
        "pedido_producto_descripcion",
        aliases=("ordered_product_description", "order_item_description"),
        merge_mode="set",
        max_length=1200,
    ),
    _spec(
        "pedido_servicio_descripcion",
        aliases=("ordered_service_description",),
        merge_mode="set",
        max_length=1200,
    ),
    _spec(
        "producto_bien_digital",
        value_type="boolean",
        aliases=("goods_with_digital_elements",),
        min_confidence=0.97,
    ),
    _spec(
        "contenido_servicio_digital",
        value_type="boolean",
        aliases=("digital_content_or_service",),
        min_confidence=0.97,
    ),
    _spec(
        "producto_segunda_mano",
        value_type="boolean",
        aliases=("second_hand_goods",),
        min_confidence=0.97,
    ),
    _spec(
        "plazo_garantia_segunda_mano_meses",
        value_type="integer",
        aliases=("second_hand_guarantee_months",),
        min_confidence=0.97,
    ),
    _spec(
        "producto_personalizado",
        value_type="boolean",
        aliases=("personalized_goods", "made_to_order_goods"),
        min_confidence=0.97,
    ),
    _spec(
        "producto_precintado_higiene",
        value_type="boolean",
        aliases=("sealed_hygiene_goods",),
        min_confidence=0.97,
    ),
    _spec(
        "precinto_abierto_consumidor",
        value_type="boolean",
        aliases=("seal_opened_by_consumer",),
        min_confidence=0.97,
    ),
    _spec(
        "precio_unidad_eur",
        value_type="money",
        aliases=("unit_price_eur",),
        min_confidence=0.98,
    ),
    _spec(
        "gastos_envio_eur",
        value_type="money",
        aliases=("shipping_cost_eur", "delivery_cost_eur"),
        min_confidence=0.98,
    ),
    _spec(
        "gastos_adicionales_pedido_eur",
        value_type="money",
        aliases=("order_additional_charges_eur",),
        min_confidence=0.98,
    ),
    _spec(
        "precio_total_pedido_eur",
        value_type="money",
        aliases=("order_total_eur", "total_order_price_eur"),
        min_confidence=0.98,
    ),
    _spec(
        "precio_anterior_mostrado_eur",
        value_type="money",
        aliases=("displayed_previous_price_eur",),
        min_confidence=0.98,
    ),
    _spec(
        "precio_mas_bajo_30_dias_eur",
        value_type="money",
        aliases=("lowest_price_30_days_eur",),
        min_confidence=0.98,
    ),
    _spec(
        "porcentaje_descuento_anunciado",
        value_type="number",
        aliases=("advertised_discount_percent",),
        min_confidence=0.97,
    ),
    _spec(
        "precio_personalizado_decision_automatizada",
        value_type="boolean",
        aliases=("automated_personalized_price",),
        min_confidence=0.97,
    ),
    _spec(
        "boton_pedido_indica_obligacion_pago",
        value_type="boolean",
        aliases=("order_button_indicates_payment_obligation",),
        min_confidence=0.98,
    ),
    _spec(
        "confirmacion_contrato_soporte_duradero",
        value_type="boolean",
        aliases=("contract_confirmation_durable_medium",),
        min_confidence=0.98,
    ),
    _spec(
        "condiciones_generales_pedido",
        aliases=("order_terms", "general_contract_terms"),
        merge_mode="set",
        max_length=1800,
    ),
    _spec(
        "publicidad_oferta_descripcion",
        aliases=("advertising_offer_description", "online_offer_description"),
        merge_mode="set",
        max_length=1600,
    ),
    _spec(
        "fecha_pedido",
        value_type="date",
        aliases=("order_date",),
        min_confidence=0.98,
    ),
    _spec(
        "fecha_confirmacion_pedido",
        value_type="date",
        aliases=("order_confirmation_date",),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_entrega_pactada",
        value_type="date",
        aliases=("agreed_delivery_date",),
        min_confidence=0.98,
    ),
    _spec(
        "fecha_envio",
        value_type="date",
        aliases=("shipping_date", "dispatch_date"),
        min_confidence=0.97,
    ),
    _spec(
        "transportista_pedido",
        aliases=("order_carrier", "delivery_carrier"),
        min_confidence=0.96,
        max_length=260,
    ),
    _spec(
        "transportista_elegido_por_consumidor",
        value_type="boolean",
        aliases=("carrier_chosen_by_consumer",),
        min_confidence=0.97,
    ),
    _spec(
        "seguimiento_envio_ref",
        value_type="identifier",
        aliases=("shipment_tracking_reference",),
        min_confidence=0.97,
        max_length=180,
    ),
    _spec(
        "fecha_entrega_efectiva",
        value_type="date",
        aliases=("actual_delivery_date",),
        min_confidence=0.98,
    ),
    _spec(
        "pedido_entregado",
        value_type="boolean",
        aliases=("order_delivered",),
        min_confidence=0.98,
    ),
    _spec(
        "prueba_entrega_aportada",
        value_type="boolean",
        aliases=("delivery_proof_provided",),
        min_confidence=0.97,
    ),
    _spec(
        "entrega_a_tercero_autorizado",
        value_type="boolean",
        aliases=("delivered_to_authorized_third_party",),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_requerimiento_entrega_adicional",
        value_type="date",
        aliases=("additional_delivery_period_request_date",),
        min_confidence=0.97,
    ),
    _spec(
        "plazo_adicional_entrega_dias",
        value_type="integer",
        aliases=("additional_delivery_period_days",),
        min_confidence=0.97,
    ),
    _spec(
        "contrato_resuelto_por_no_entrega",
        value_type="boolean",
        aliases=("contract_terminated_for_non_delivery",),
        min_confidence=0.97,
    ),
    _spec(
        "falta_conformidad_descripcion",
        aliases=("lack_of_conformity_description", "product_defect_description"),
        merge_mode="set",
        max_length=1600,
    ),
    _spec(
        "fecha_manifestacion_falta_conformidad",
        value_type="date",
        aliases=("lack_of_conformity_date", "defect_manifestation_date"),
        min_confidence=0.98,
    ),
    _spec(
        "fecha_comunicacion_falta_conformidad",
        value_type="date",
        aliases=("lack_of_conformity_notice_date",),
        min_confidence=0.98,
    ),
    _spec(
        "falta_conformidad_existia_entrega",
        value_type="boolean",
        aliases=("nonconformity_existed_at_delivery",),
        min_confidence=0.97,
    ),
    _spec(
        "uso_instalacion_incorrectos_consumidor",
        value_type="boolean",
        aliases=("consumer_misuse_or_bad_installation",),
        min_confidence=0.97,
    ),
    _spec(
        "reparacion_solicitada",
        value_type="boolean",
        aliases=("repair_requested",),
        min_confidence=0.97,
    ),
    _spec(
        "sustitucion_solicitada",
        value_type="boolean",
        aliases=("replacement_requested",),
        min_confidence=0.97,
    ),
    _spec(
        "reduccion_precio_solicitada",
        value_type="boolean",
        aliases=("price_reduction_requested",),
        min_confidence=0.97,
    ),
    _spec(
        "resolucion_contrato_solicitada",
        value_type="boolean",
        aliases=("contract_termination_requested",),
        min_confidence=0.97,
    ),
    _spec(
        "remedio_ofrecido_vendedor",
        aliases=("seller_remedy_offered",),
        merge_mode="set",
        max_length=1200,
    ),
    _spec(
        "fecha_entrega_reparacion",
        value_type="date",
        aliases=("goods_handed_for_repair_date",),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_devolucion_reparacion",
        value_type="date",
        aliases=("goods_returned_after_repair_date",),
        min_confidence=0.97,
    ),
    _spec(
        "reparacion_sin_coste",
        value_type="boolean",
        aliases=("repair_free_of_charge",),
        min_confidence=0.97,
    ),
    _spec(
        "reparacion_plazo_razonable",
        value_type="boolean",
        aliases=("repair_within_reasonable_time",),
        min_confidence=0.97,
    ),
    _spec(
        "inconvenientes_significativos",
        value_type="boolean",
        aliases=("significant_inconvenience",),
        min_confidence=0.97,
    ),
    _spec(
        "desistimiento_comunicado",
        value_type="boolean",
        aliases=("withdrawal_communicated",),
        min_confidence=0.98,
    ),
    _spec(
        "fecha_comunicacion_desistimiento",
        value_type="date",
        aliases=("withdrawal_notice_date",),
        min_confidence=0.98,
    ),
    _spec(
        "informacion_desistimiento_entregada",
        value_type="boolean",
        aliases=("withdrawal_information_provided",),
        min_confidence=0.98,
    ),
    _spec(
        "excepcion_desistimiento_invocada",
        aliases=("withdrawal_exception_invoked",),
        merge_mode="set",
        max_length=900,
    ),
    _spec(
        "contenido_digital_ejecucion_iniciada",
        value_type="boolean",
        aliases=("digital_performance_started",),
        min_confidence=0.97,
    ),
    _spec(
        "consentimiento_inicio_digital",
        value_type="boolean",
        aliases=("digital_early_performance_consent",),
        min_confidence=0.98,
    ),
    _spec(
        "conocimiento_perdida_desistimiento",
        value_type="boolean",
        aliases=("acknowledgement_loss_withdrawal_right",),
        min_confidence=0.98,
    ),
    _spec(
        "fecha_devolucion_producto",
        value_type="date",
        aliases=("product_return_date",),
        min_confidence=0.98,
    ),
    _spec(
        "devolucion_recibida_vendedor",
        value_type="boolean",
        aliases=("return_received_by_seller",),
        min_confidence=0.97,
    ),
    _spec(
        "prueba_devolucion_aportada",
        value_type="boolean",
        aliases=("return_proof_provided",),
        min_confidence=0.97,
    ),
    _spec(
        "coste_devolucion_eur",
        value_type="money",
        aliases=("return_cost_eur",),
        min_confidence=0.98,
    ),
    _spec(
        "vendedor_informo_coste_devolucion",
        value_type="boolean",
        aliases=("seller_disclosed_return_cost",),
        min_confidence=0.97,
    ),
    _spec(
        "importe_reembolso_pedido_eur",
        value_type="money",
        aliases=("order_refund_amount_eur",),
        min_confidence=0.98,
    ),
    _spec(
        "fecha_reembolso_pedido",
        value_type="date",
        aliases=("order_refund_date",),
        min_confidence=0.97,
    ),
    _spec(
        "reembolso_mismo_medio_pago",
        value_type="boolean",
        aliases=("refund_same_payment_method",),
        min_confidence=0.97,
    ),
    _spec(
        "retencion_reembolso_motivo",
        aliases=("refund_withholding_reason",),
        merge_mode="set",
        max_length=1000,
    ),
    _spec(
        "importe_recuperado_medio_pago_eur",
        value_type="money",
        aliases=("amount_recovered_payment_method_eur",),
        min_confidence=0.98,
    ),
    _spec(
        "disputa_medio_pago_abierta",
        value_type="boolean",
        aliases=("payment_dispute_open", "chargeback_open"),
        min_confidence=0.97,
    ),
    _spec(
        "suscripcion_online",
        value_type="boolean",
        aliases=("online_subscription",),
        min_confidence=0.97,
    ),
    _spec(
        "periodicidad_suscripcion",
        aliases=("subscription_periodicity",),
        max_length=180,
    ),
    _spec(
        "renovacion_automatica",
        value_type="boolean",
        aliases=("automatic_renewal",),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_renovacion_suscripcion",
        value_type="date",
        aliases=("subscription_renewal_date",),
        min_confidence=0.97,
    ),
    _spec(
        "aviso_renovacion_suscripcion",
        value_type="boolean",
        aliases=("subscription_renewal_notice",),
        min_confidence=0.97,
    ),
    _spec(
        "baja_suscripcion_solicitada_fecha",
        value_type="date",
        aliases=("subscription_cancellation_request_date",),
        min_confidence=0.98,
    ),
    _spec(
        "producto_inseguro",
        value_type="boolean",
        aliases=("unsafe_product",),
        min_confidence=0.98,
    ),
    _spec(
        "retirada_producto_anunciada",
        value_type="boolean",
        aliases=("product_recall_announced",),
        min_confidence=0.98,
    ),
    _spec(
        "aviso_seguridad_producto",
        aliases=("product_safety_notice", "recall_notice"),
        merge_mode="set",
        max_length=1200,
    ),
    _spec(
        "reclamacion_ecommerce_ref",
        value_type="identifier",
        aliases=("ecommerce_complaint_reference", "seller_complaint_reference"),
        min_confidence=0.97,
        max_length=180,
    ),
    _spec(
        "fecha_respuesta_vendedor",
        value_type="date",
        aliases=("seller_response_date",),
        min_confidence=0.97,
    ),
)


def _install_document_facts() -> None:
    import rtm_core.document_fact_catalog as catalog

    registered = {spec.key for spec in catalog._BY_SERVICE["claims"].values()}
    new_specs = tuple(spec for spec in _ECOMMERCE_FACTS if spec.key not in registered)
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

    key = ("claims", "comercio_electronico")
    profile = catalog._FAMILY_PROFILES.get(key)
    if profile is None:
        raise RuntimeError("No existe la familia claims.comercio_electronico")

    ready = profile.model_copy(update={"capability": "specialist_ready"})
    catalog._FAMILY_PROFILES[key] = ready
    catalog._FAMILY_ITEMS = tuple(
        ready if (item.department, item.family) == key else item
        for item in catalog._FAMILY_ITEMS
    )


def _install_version_inventory() -> None:
    import rtm_core.versioning as versioning

    declared = {
        "claims_ecommerce_extension": CLAIMS_ECOMMERCE_EXTENSION_VERSION,
        "claims_ecommerce_regime": "rtm_claims_ecommerce_regime_v1_0",
        "claims_ecommerce_specialist": "rtm_claims_ecommerce_specialist_v1_0",
        "claims_specialist_registry": "rtm_claims_specialist_registry_v1_0",
    }
    lookups = {
        "claims_ecommerce_extension": (
            "rtm_core.claims_ecommerce_extension",
            "CLAIMS_ECOMMERCE_EXTENSION_VERSION",
        ),
        "claims_ecommerce_regime": (
            "rtm_core.claims_ecommerce_regime",
            "CLAIMS_ECOMMERCE_REGIME_VERSION",
        ),
        "claims_ecommerce_specialist": (
            "rtm_core.claims_ecommerce_specialist",
            "CLAIMS_ECOMMERCE_SPECIALIST_VERSION",
        ),
        "claims_specialist_registry": (
            "rtm_core.claims_specialist_registry",
            "CLAIMS_SPECIALIST_REGISTRY_VERSION",
        ),
    }
    versioning.DECLARED_COMPONENT_VERSIONS.update(declared)
    versioning._RUNTIME_LOOKUPS.update(lookups)


def install_claims_ecommerce_extension() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_document_facts()
    _install_domain_capability()
    _install_version_inventory()
    _INSTALLED = True