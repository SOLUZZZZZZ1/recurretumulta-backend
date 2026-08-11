"""Registro modular de hechos y capacidad para ``claims.energy``.

Añade hechos documentales tipados para electricidad y gas, declara la capacidad
profunda de la familia y registra sus versiones en observabilidad. No recalcula
facturas, no atribuye automáticamente un error a comercializadora o distribuidora
y no convierte una alegación de vulnerabilidad en un hecho acreditado.
"""

from __future__ import annotations

from rtm_core.document_fact_catalog import FactFieldSpec
from rtm_core.service_catalog import normalize_code


CLAIMS_ENERGY_EXTENSION_VERSION = "rtm_claims_energy_extension_v1_0"

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


_ENERGY_FACTS: tuple[FactFieldSpec, ...] = (
    _spec(
        "incidencia_energia_tipo",
        aliases=("energy_incident_type", "utility_incident_type"),
        max_length=240,
    ),
    _spec(
        "pais_suministro",
        aliases=("supply_country", "energy_supply_country"),
        max_length=160,
    ),
    _spec(
        "cups",
        value_type="identifier",
        aliases=("cups_code", "supply_point_code"),
        min_confidence=0.98,
        max_length=80,
    ),
    _spec(
        "comercializadora_energia",
        aliases=("energy_retailer", "electricity_supplier", "gas_supplier"),
        min_confidence=0.97,
        max_length=260,
    ),
    _spec(
        "distribuidora_energia",
        aliases=("energy_distributor", "electricity_distributor", "gas_distributor"),
        min_confidence=0.97,
        max_length=260,
    ),
    _spec(
        "mercado_energia",
        aliases=("energy_market_type", "regulated_or_free_market", "tariff_market"),
        max_length=220,
    ),
    _spec(
        "tarifa_energia",
        aliases=("energy_tariff", "access_tariff", "toll_segment"),
        max_length=180,
    ),
    _spec(
        "potencia_contratada_kw",
        value_type="number",
        aliases=("contracted_power_kw", "contracted_capacity_kw"),
        min_confidence=0.98,
    ),
    _spec(
        "fecha_inicio_suministro",
        value_type="date",
        aliases=("supply_start_date",),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_factura_energia",
        value_type="date",
        aliases=("energy_invoice_date", "utility_invoice_date"),
        min_confidence=0.97,
    ),
    _spec(
        "periodo_facturacion_inicio",
        value_type="date",
        aliases=("billing_period_start",),
        min_confidence=0.97,
    ),
    _spec(
        "periodo_facturacion_fin",
        value_type="date",
        aliases=("billing_period_end",),
        min_confidence=0.97,
    ),
    _spec(
        "numero_contador",
        value_type="identifier",
        aliases=("meter_number", "meter_serial"),
        min_confidence=0.97,
        max_length=120,
    ),
    _spec(
        "lectura_anterior",
        value_type="number",
        aliases=("previous_meter_reading",),
        min_confidence=0.98,
    ),
    _spec(
        "lectura_actual",
        value_type="number",
        aliases=("current_meter_reading",),
        min_confidence=0.98,
    ),
    _spec(
        "fecha_lectura_anterior",
        value_type="date",
        aliases=("previous_reading_date",),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_lectura_actual",
        value_type="date",
        aliases=("current_reading_date",),
        min_confidence=0.97,
    ),
    _spec(
        "lectura_real",
        value_type="boolean",
        aliases=("actual_meter_reading", "reading_is_actual"),
        min_confidence=0.97,
    ),
    _spec(
        "consumo_facturado_kwh",
        value_type="number",
        aliases=("billed_consumption_kwh",),
        min_confidence=0.98,
    ),
    _spec(
        "consumo_reconocido_kwh",
        value_type="number",
        aliases=("accepted_consumption_kwh", "documented_consumption_kwh"),
        min_confidence=0.98,
    ),
    _spec(
        "importe_factura_energia_eur",
        value_type="money",
        aliases=("energy_invoice_amount", "utility_bill_amount"),
        min_confidence=0.98,
    ),
    _spec(
        "importe_regularizacion_eur",
        value_type="money",
        aliases=("energy_regularization_amount", "back_billing_amount"),
        min_confidence=0.98,
        allow_negative=True,
    ),
    _spec(
        "meses_regularizados",
        value_type="integer",
        aliases=("regularized_months", "back_billing_months"),
        min_confidence=0.97,
    ),
    _spec(
        "acceso_red_a_traves_comercializadora",
        value_type="boolean",
        aliases=("network_access_through_retailer",),
        min_confidence=0.97,
    ),
    _spec(
        "factura_pagada_energia",
        value_type="boolean",
        aliases=("energy_invoice_paid",),
        min_confidence=0.97,
    ),
    _spec(
        "importe_devuelto_energia_eur",
        value_type="money",
        aliases=("energy_refund_amount", "utility_refund_amount"),
        min_confidence=0.98,
    ),
    _spec(
        "fecha_aviso_modificacion",
        value_type="date",
        aliases=("contract_change_notice_date", "price_change_notice_date"),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_aplicacion_modificacion",
        value_type="date",
        aliases=("contract_change_effective_date", "price_change_effective_date"),
        min_confidence=0.97,
    ),
    _spec(
        "aviso_modificacion_separado_factura",
        value_type="boolean",
        aliases=("change_notice_separate_from_invoice",),
        min_confidence=0.97,
    ),
    _spec(
        "contrato_precio_fijo",
        value_type="boolean",
        aliases=("fixed_price_contract",),
        min_confidence=0.97,
    ),
    _spec(
        "cambio_comercializadora_no_consentido",
        value_type="boolean",
        aliases=("unauthorized_supplier_switch", "switch_without_consent"),
        min_confidence=0.97,
    ),
    _spec(
        "consentimiento_contratacion_acreditado",
        value_type="boolean",
        aliases=("energy_contract_consent_proven", "supplier_switch_consent_proven"),
        min_confidence=0.97,
    ),
    _spec(
        "servicio_energia_no_solicitado",
        value_type="boolean",
        aliases=("unsolicited_energy_service",),
        min_confidence=0.97,
    ),
    _spec(
        "corte_suministro",
        value_type="boolean",
        aliases=("supply_disconnected", "energy_cut"),
        min_confidence=0.97,
    ),
    _spec(
        "motivo_corte",
        aliases=("disconnection_reason", "supply_cut_reason"),
        merge_mode="set",
        max_length=900,
    ),
    _spec(
        "fecha_aviso_corte",
        value_type="date",
        aliases=("disconnection_notice_date",),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_corte_suministro",
        value_type="date",
        aliases=("disconnection_date", "supply_cut_date"),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_reposicion_suministro",
        value_type="date",
        aliases=("reconnection_date", "supply_restoration_date"),
        min_confidence=0.97,
    ),
    _spec(
        "vivienda_habitual",
        value_type="boolean",
        aliases=("habitual_residence_supply",),
        min_confidence=0.97,
    ),
    _spec(
        "consumidor_vulnerable",
        value_type="boolean",
        aliases=("vulnerable_consumer",),
        min_confidence=0.97,
    ),
    _spec(
        "bono_social",
        value_type="boolean",
        aliases=("social_tariff", "social_bonus"),
        min_confidence=0.97,
    ),
    _spec(
        "suministro_esencial",
        value_type="boolean",
        aliases=("essential_supply",),
        min_confidence=0.97,
    ),
    _spec(
        "unidad_convivencia_menor_16",
        value_type="boolean",
        aliases=("household_child_under_16",),
        min_confidence=0.97,
    ),
    _spec(
        "dependencia_grado_ii_iii",
        value_type="boolean",
        aliases=("dependency_grade_ii_or_iii",),
        min_confidence=0.97,
    ),
    _spec(
        "discapacidad_33_o_mas",
        value_type="boolean",
        aliases=("disability_33_or_more",),
        min_confidence=0.97,
    ),
    _spec(
        "autonomo_o_empresa",
        value_type="boolean",
        aliases=("self_employed_or_business_supply",),
        min_confidence=0.97,
    ),
    _spec(
        "flexibilizacion_contrato_solicitada",
        value_type="boolean",
        aliases=("temporary_contract_flexibility_requested",),
        min_confidence=0.97,
    ),
    _spec(
        "interrupcion_programada",
        value_type="boolean",
        aliases=("planned_interruption",),
        min_confidence=0.97,
    ),
    _spec(
        "duracion_interrupcion_minutos",
        value_type="integer",
        aliases=("outage_duration_minutes",),
        min_confidence=0.97,
    ),
    _spec(
        "calidad_suministro",
        aliases=("supply_quality", "voltage_quality"),
        merge_mode="set",
        max_length=900,
    ),
    _spec(
        "reclamacion_energia_ref",
        value_type="identifier",
        aliases=("energy_complaint_reference", "utility_complaint_reference"),
        min_confidence=0.97,
        max_length=160,
    ),
)


def _install_document_facts() -> None:
    import rtm_core.document_fact_catalog as catalog

    registered = {spec.key for spec in catalog._BY_SERVICE["claims"].values()}
    new_specs = tuple(spec for spec in _ENERGY_FACTS if spec.key not in registered)
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

    key = ("claims", "energia")
    profile = catalog._FAMILY_PROFILES.get(key)
    if profile is None:
        raise RuntimeError("No existe la familia claims.energia")

    ready = profile.model_copy(update={"capability": "specialist_ready"})
    catalog._FAMILY_PROFILES[key] = ready
    catalog._FAMILY_ITEMS = tuple(
        ready if (item.department, item.family) == key else item
        for item in catalog._FAMILY_ITEMS
    )


def _install_version_inventory() -> None:
    import rtm_core.versioning as versioning

    declared = {
        "claims_energy_extension": CLAIMS_ENERGY_EXTENSION_VERSION,
        "claims_energy_regime": "rtm_claims_energy_regime_v1_0",
        "claims_energy_specialist": "rtm_claims_energy_specialist_v1_0",
        "claims_specialist_registry": "rtm_claims_specialist_registry_v1_0",
    }
    lookups = {
        "claims_energy_extension": (
            "rtm_core.claims_energy_extension",
            "CLAIMS_ENERGY_EXTENSION_VERSION",
        ),
        "claims_energy_regime": (
            "rtm_core.claims_energy_regime",
            "CLAIMS_ENERGY_REGIME_VERSION",
        ),
        "claims_energy_specialist": (
            "rtm_core.claims_energy_specialist",
            "CLAIMS_ENERGY_SPECIALIST_VERSION",
        ),
        "claims_specialist_registry": (
            "rtm_core.claims_specialist_registry",
            "CLAIMS_SPECIALIST_REGISTRY_VERSION",
        ),
    }
    versioning.DECLARED_COMPONENT_VERSIONS.update(declared)
    versioning._RUNTIME_LOOKUPS.update(lookups)


def install_claims_energy_extension() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_document_facts()
    _install_domain_capability()
    _install_version_inventory()
    _INSTALLED = True
