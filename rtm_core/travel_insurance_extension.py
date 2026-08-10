"""Registro modular de hechos y capacidad para ``travel.insurance``.

La extensión añade hechos documentales tipados de seguro de viaje, declara la
capacidad profunda de la familia y registra sus versiones en observabilidad.
No almacena diagnósticos clínicos detallados, no decide la cobertura y no
convierte una exclusión invocada en una exclusión válida.
"""

from __future__ import annotations

from rtm_core.document_fact_catalog import FactFieldSpec
from rtm_core.service_catalog import normalize_code


TRAVEL_INSURANCE_EXTENSION_VERSION = "rtm_travel_insurance_extension_v1_0"

_INSTALLED = False


def _spec(
    key: str,
    *,
    value_type: str = "text",
    aliases: tuple[str, ...] = (),
    min_confidence: float = 0.96,
    merge_mode: str = "single",
    max_length: int = 800,
) -> FactFieldSpec:
    return FactFieldSpec(
        key=key,
        label=key.replace("_", " ").strip().capitalize(),
        services=("travel",),
        value_type=value_type,
        aliases=aliases,
        min_confidence=min_confidence,
        merge_mode=merge_mode,
        max_length=max_length,
    )


_INSURANCE_FACTS: tuple[FactFieldSpec, ...] = (
    _spec(
        "aseguradora_viaje",
        aliases=("travel_insurer", "insurance_company", "insurer"),
        min_confidence=0.97,
        max_length=260,
    ),
    _spec(
        "pais_aseguradora",
        aliases=("insurer_country", "insurance_company_country"),
        min_confidence=0.96,
        max_length=160,
    ),
    _spec(
        "tomador_seguro",
        aliases=("policyholder", "insurance_policyholder"),
        min_confidence=0.96,
        max_length=260,
    ),
    _spec(
        "asegurado_viaje",
        aliases=("insured_person", "travel_insured"),
        min_confidence=0.96,
        max_length=260,
    ),
    _spec(
        "beneficiario_seguro",
        aliases=("insurance_beneficiary", "beneficiary"),
        min_confidence=0.96,
        max_length=260,
    ),
    _spec(
        "distribuidor_seguro",
        aliases=(
            "insurance_distributor",
            "ancillary_insurance_intermediary",
            "insurance_seller",
        ),
        min_confidence=0.96,
        max_length=260,
    ),
    _spec(
        "fecha_contratacion_seguro",
        value_type="date",
        aliases=("insurance_contract_date", "policy_purchase_date"),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_inicio_cobertura",
        value_type="date",
        aliases=("coverage_start_date", "policy_start_date"),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_fin_cobertura",
        value_type="date",
        aliases=("coverage_end_date", "policy_end_date"),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_conocimiento_siniestro",
        value_type="date",
        aliases=("loss_awareness_date", "claim_awareness_date"),
        min_confidence=0.97,
    ),
    _spec(
        "naturaleza_cobertura_documentada",
        aliases=("coverage_nature", "insurance_nature", "policy_nature"),
        min_confidence=0.96,
        max_length=300,
    ),
    _spec(
        "cobertura_reclamada_tipo",
        aliases=("claimed_coverage_type", "benefit_type", "claim_type"),
        min_confidence=0.96,
        max_length=300,
    ),
    _spec(
        "coberturas_poliza",
        aliases=(
            "policy_coverages",
            "insured_benefits",
            "covered_risks",
            "coverage_schedule",
        ),
        min_confidence=0.96,
        merge_mode="set",
        max_length=1600,
    ),
    _spec(
        "limite_cobertura_eur",
        value_type="money",
        aliases=("coverage_limit", "sum_insured", "policy_limit"),
        min_confidence=0.98,
    ),
    _spec(
        "franquicia_eur",
        value_type="money",
        aliases=("deductible", "excess_amount", "policy_excess"),
        min_confidence=0.98,
    ),
    _spec(
        "exclusion_invocada",
        aliases=("invoked_exclusion", "policy_exclusion_relied_on"),
        min_confidence=0.97,
        merge_mode="set",
        max_length=1400,
    ),
    _spec(
        "exclusion_destacada",
        value_type="boolean",
        aliases=("exclusion_prominently_displayed", "limitation_highlighted"),
        min_confidence=0.97,
    ),
    _spec(
        "exclusion_aceptada_especificamente",
        value_type="boolean",
        aliases=("exclusion_specifically_accepted", "limitation_signed_acceptance"),
        min_confidence=0.97,
    ),
    _spec(
        "condicion_preexistente_invocada",
        value_type="boolean",
        aliases=("pre_existing_condition_invoked", "preexisting_condition_relied_on"),
        min_confidence=0.97,
    ),
    _spec(
        "motivo_rechazo_aseguradora",
        aliases=("insurer_rejection_reason", "claim_denial_reason"),
        min_confidence=0.97,
        merge_mode="set",
        max_length=1400,
    ),
    _spec(
        "decision_aseguradora",
        aliases=("insurer_decision", "claim_decision", "coverage_decision"),
        min_confidence=0.97,
        merge_mode="set",
        max_length=1400,
    ),
    _spec(
        "cobertura_aceptada",
        value_type="boolean",
        aliases=("coverage_accepted", "claim_accepted"),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_comunicacion_siniestro",
        value_type="date",
        aliases=("claim_notification_date", "loss_notice_date"),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_documentacion_completa",
        value_type="date",
        aliases=("complete_claim_documents_date", "claim_file_complete_date"),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_respuesta_aseguradora",
        value_type="date",
        aliases=("insurer_response_date", "claim_decision_date"),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_pago_aseguradora",
        value_type="date",
        aliases=("insurer_payment_date", "claim_payment_date"),
        min_confidence=0.97,
    ),
    _spec(
        "importe_gastos_medicos_eur",
        value_type="money",
        aliases=("medical_expenses", "medical_costs"),
        min_confidence=0.98,
    ),
    _spec(
        "importe_cancelacion_viaje_eur",
        value_type="money",
        aliases=("trip_cancellation_loss", "non_refundable_trip_cost"),
        min_confidence=0.98,
    ),
    _spec(
        "importe_asistencia_eur",
        value_type="money",
        aliases=("assistance_expenses", "emergency_assistance_costs"),
        min_confidence=0.98,
    ),
    _spec(
        "importe_equipaje_asegurado_eur",
        value_type="money",
        aliases=("insured_baggage_loss", "baggage_claim_amount"),
        min_confidence=0.98,
    ),
    _spec(
        "importe_interrupcion_viaje_eur",
        value_type="money",
        aliases=("trip_interruption_loss", "curtailment_loss"),
        min_confidence=0.98,
    ),
    _spec(
        "importe_accidente_personal_eur",
        value_type="money",
        aliases=("personal_accident_benefit", "accident_benefit_amount"),
        min_confidence=0.98,
    ),
    _spec(
        "importe_pagado_aseguradora_eur",
        value_type="money",
        aliases=("insurer_amount_paid", "claim_amount_paid"),
        min_confidence=0.98,
    ),
    _spec(
        "importe_recuperado_terceros_eur",
        value_type="money",
        aliases=("amount_recovered_from_third_parties", "other_reimbursements"),
        min_confidence=0.98,
    ),
    _spec(
        "autorizacion_previa_requerida",
        value_type="boolean",
        aliases=("prior_authorisation_required", "pre_authorization_required"),
        min_confidence=0.97,
    ),
    _spec(
        "autorizacion_previa_obtenida",
        value_type="boolean",
        aliases=("prior_authorisation_obtained", "pre_authorization_obtained"),
        min_confidence=0.97,
    ),
    _spec(
        "asistencia_contactada",
        value_type="boolean",
        aliases=("assistance_service_contacted", "emergency_line_contacted"),
        min_confidence=0.97,
    ),
    _spec(
        "atencion_medica_urgente",
        value_type="boolean",
        aliases=("urgent_medical_assistance", "emergency_medical_treatment"),
        min_confidence=0.97,
    ),
    _spec(
        "repatriacion_solicitada",
        value_type="boolean",
        aliases=("repatriation_requested",),
        min_confidence=0.97,
    ),
    _spec(
        "repatriacion_ejecutada",
        value_type="boolean",
        aliases=("repatriation_completed", "repatriation_performed"),
        min_confidence=0.97,
    ),
    _spec(
        "seguro_incluido_viaje_combinado",
        value_type="boolean",
        aliases=("insurance_included_in_package", "package_included_insurance"),
        min_confidence=0.97,
    ),
    _spec(
        "seguro_anadido_reserva",
        value_type="boolean",
        aliases=("insurance_added_to_booking", "booking_add_on_insurance"),
        min_confidence=0.97,
    ),
    _spec(
        "documento_ipid_entregado",
        value_type="boolean",
        aliases=("ipid_delivered", "insurance_product_information_document_delivered"),
        min_confidence=0.97,
    ),
    _spec(
        "necesidades_cliente_documentadas",
        value_type="boolean",
        aliases=("customer_demands_and_needs_documented", "demands_needs_assessed"),
        min_confidence=0.97,
    ),
    _spec(
        "reclamacion_sac_fecha",
        value_type="date",
        aliases=("insurer_customer_service_complaint_date", "sac_complaint_date"),
        min_confidence=0.97,
    ),
    _spec(
        "respuesta_sac_fecha",
        value_type="date",
        aliases=("insurer_customer_service_response_date", "sac_response_date"),
        min_confidence=0.97,
    ),
)


def _install_document_facts() -> None:
    import rtm_core.document_fact_catalog as catalog

    registered = {spec.key for spec in catalog._BY_SERVICE["travel"].values()}
    new_specs = tuple(spec for spec in _INSURANCE_FACTS if spec.key not in registered)
    if not new_specs:
        return

    index = catalog._BY_SERVICE["travel"]
    for spec in new_specs:
        for raw_name in (spec.key, *spec.aliases):
            name = normalize_code(raw_name)
            current = index.get(name)
            if current is not None and current.key != spec.key:
                raise RuntimeError(
                    f"Alias ambiguo {raw_name!r} en travel: "
                    f"{current.key!r} frente a {spec.key!r}"
                )
            index[name] = spec

    catalog._FIELDS = (*catalog._FIELDS, *new_specs)


def _install_domain_capability() -> None:
    import rtm_core.domain_catalog as catalog

    key = ("travel", "seguro_viaje")
    profile = catalog._FAMILY_PROFILES.get(key)
    if profile is None:
        raise RuntimeError("No existe la familia travel.seguro_viaje")

    ready = profile.model_copy(update={"capability": "specialist_ready"})
    catalog._FAMILY_PROFILES[key] = ready
    catalog._FAMILY_ITEMS = tuple(
        ready if (item.department, item.family) == key else item
        for item in catalog._FAMILY_ITEMS
    )


def _install_version_inventory() -> None:
    import rtm_core.versioning as versioning

    declared = {
        "travel_insurance_extension": TRAVEL_INSURANCE_EXTENSION_VERSION,
        "travel_insurance_regime": "rtm_travel_insurance_regime_v1_0",
        "travel_insurance_specialist": "rtm_travel_insurance_specialist_v1_0",
        "travel_specialist_registry": "rtm_travel_specialist_registry_v1_2",
    }
    lookups = {
        "travel_insurance_extension": (
            "rtm_core.travel_insurance_extension",
            "TRAVEL_INSURANCE_EXTENSION_VERSION",
        ),
        "travel_insurance_regime": (
            "rtm_core.travel_insurance_regime",
            "TRAVEL_INSURANCE_REGIME_VERSION",
        ),
        "travel_insurance_specialist": (
            "rtm_core.travel_insurance_specialist",
            "TRAVEL_INSURANCE_SPECIALIST_VERSION",
        ),
        "travel_specialist_registry": (
            "rtm_core.travel_specialist_registry",
            "TRAVEL_SPECIALIST_REGISTRY_VERSION",
        ),
    }
    versioning.DECLARED_COMPONENT_VERSIONS.update(declared)
    versioning._RUNTIME_LOOKUPS.update(lookups)


def install_travel_insurance_extension() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_document_facts()
    _install_domain_capability()
    _install_version_inventory()
    _INSTALLED = True
