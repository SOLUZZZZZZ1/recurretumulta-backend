"""Catálogo autoritativo de hechos documentales de los satélites RTM.

El catálogo solo define campos, tipos, alias y umbrales. No lee documentos,
no clasifica familias, no decide estrategia y no redacta. Tráfico conserva su
adaptador Reanalysis específico; este catálogo cubre Morosidad, Administración,
Viajes, Reclamaciones y el cajón controlado ``other``.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from rtm_core.service_catalog import canonical_department, normalize_code


DOCUMENT_FACT_CATALOG_VERSION = "rtm_document_fact_catalog_v1_1"

DepartmentCode = Literal["debt", "administration", "travel", "claims", "other"]
ValueType = Literal["text", "identifier", "money", "date", "time", "integer", "boolean"]
MergeMode = Literal["single", "set"]


class FactFieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    services: tuple[DepartmentCode, ...]
    value_type: ValueType = "text"
    aliases: tuple[str, ...] = ()
    min_confidence: float = Field(default=0.94, ge=0.0, le=1.0)
    merge_mode: MergeMode = "single"
    minimum_for_direction: bool = False
    require_operator_if_handwritten: bool = False
    max_length: int = Field(default=800, ge=1, le=5000)
    allow_negative: bool = False


_ALL: tuple[DepartmentCode, ...] = (
    "debt", "administration", "travel", "claims", "other"
)


def _services(value: str) -> tuple[DepartmentCode, ...]:
    if value == "all":
        return _ALL
    return tuple(part.strip() for part in value.split(","))  # type: ignore[return-value]


def _field(
    key: str,
    services: str,
    value_type: ValueType = "text",
    aliases: tuple[str, ...] = (),
    min_confidence: float = 0.94,
    merge_mode: MergeMode = "single",
    minimum_for_direction: bool = False,
    require_operator_if_handwritten: bool = False,
    max_length: int = 800,
    allow_negative: bool = False,
) -> FactFieldSpec:
    return FactFieldSpec(
        key=key,
        label=key.replace("_", " ").strip().capitalize(),
        services=_services(services),
        value_type=value_type,
        aliases=aliases,
        min_confidence=min_confidence,
        merge_mode=merge_mode,
        minimum_for_direction=minimum_for_direction,
        require_operator_if_handwritten=require_operator_if_handwritten,
        max_length=max_length,
        allow_negative=allow_negative,
    )


_FIELDS: tuple[FactFieldSpec, ...] = (
    # Comunes.
    _field("descripcion_hecho", "all", aliases=("hecho", "hecho_principal", "factual_summary", "issue_description", "incident_description", "breach_description"), merge_mode="set", minimum_for_direction=True, require_operator_if_handwritten=True, max_length=1400),
    _field("tipo_documento", "all", aliases=("document_type", "doc_type", "documento_tipo"), min_confidence=0.91, merge_mode="set", max_length=180),
    _field("titulo_documento", "all", aliases=("document_title", "titulo"), min_confidence=0.91, merge_mode="set", max_length=240),
    _field("fecha_documento", "all", "date", ("document_date", "fecha_emision"), 0.95, "set"),
    _field("fecha_notificacion", "all", "date", ("notification_date", "fecha_recepcion"), 0.97, require_operator_if_handwritten=True),
    _field("fecha_limite", "all", "date", ("deadline", "deadline_date", "plazo_fin"), 0.98, require_operator_if_handwritten=True),
    _field("fecha_vencimiento", "all", "date", ("due_date", "maturity_date", "vencimiento"), 0.97, require_operator_if_handwritten=True),
    _field("fecha_incidencia", "all", "date", ("incident_date", "fecha_hecho"), 0.95),
    _field("fase_procedimental", "all", aliases=("procedural_stage", "procedural_stage_hint", "tramite_detectado"), max_length=220),
    _field("expediente_ref", "all", "identifier", ("numero_expediente", "case_reference", "file_reference", "expediente"), 0.97, require_operator_if_handwritten=True, max_length=160),
    _field("referencia_documento", "all", "identifier", ("document_reference", "document_ref", "referencia"), 0.95, "set", max_length=160),
    _field("organismo", "all", aliases=("organo", "administracion_emisora", "authority", "issuing_authority"), min_confidence=0.95, max_length=260),
    _field("emisor_documento", "all", aliases=("emisor", "issuer", "sender"), merge_mode="set", max_length=260),
    _field("destinatario_documento", "all", aliases=("destinatario", "recipient", "addressee"), merge_mode="set", max_length=260),
    _field("contrato_ref", "all", "identifier", ("numero_contrato", "contract_reference", "contract_number"), 0.96, max_length=180),
    _field("importe_pagado_eur", "all", "money", ("amount_paid", "paid_amount", "precio_pagado_eur"), 0.97),
    _field("importe_reclamado_eur", "all", "money", ("claimed_amount", "amount_claimed", "cuantia_reclamada"), 0.97),
    _field("respuesta_documentada", "all", aliases=("documented_response", "respuesta", "contestacion"), merge_mode="set", max_length=1400),
    _field("solucion_solicitada", "all", aliases=("requested_solution", "requested_outcome", "pretension_documental"), merge_mode="set", max_length=900),

    # Morosidad.
    _field("acreedor", "debt", aliases=("creditor", "empresa_acreedora", "titular_credito"), min_confidence=0.96, max_length=260),
    _field("deudor", "debt", aliases=("debtor", "obligado_pago", "persona_deudora"), min_confidence=0.96, max_length=260),
    _field("concepto_deuda", "debt", aliases=("debt_concept", "concepto_facturado", "origen_deuda"), max_length=700),
    _field("importe_deuda_eur", "debt", "money", ("debt_amount", "importe_deuda", "principal_deuda_eur"), 0.97),
    _field("saldo_pendiente_eur", "debt", "money", ("outstanding_balance", "saldo_pendiente"), 0.97),
    _field("factura_numero", "debt,claims", "identifier", ("invoice_number", "numero_factura", "factura_ref"), 0.97, max_length=140),
    _field("fecha_factura", "debt", "date", ("invoice_date",), 0.96),
    _field("fecha_ultimo_pago", "debt", "date", ("last_payment_date",), 0.96),
    _field("requerimiento_previo_fecha", "debt", "date", ("prior_demand_date",), 0.97),
    _field("requerimiento_previo_medio", "debt", aliases=("prior_demand_channel",), max_length=180),
    _field("procedimiento_judicial", "debt", aliases=("judicial_procedure", "court_procedure"), min_confidence=0.96, max_length=260),
    _field("numero_procedimiento", "debt", "identifier", ("procedure_number", "court_case_number"), 0.97, max_length=160),
    _field("organo_judicial", "debt", aliases=("court", "judicial_body"), min_confidence=0.96, max_length=260),
    _field("fichero_solvencia", "debt", aliases=("credit_file", "solvency_file"), min_confidence=0.97, max_length=160),
    _field("fecha_inclusion_fichero", "debt", "date", ("credit_file_inclusion_date",), 0.97),
    _field("fecha_requerimiento_fichero", "debt", "date", ("credit_file_notice_date",), 0.97),
    _field("deuda_discutida", "debt", "boolean", ("debt_disputed",), 0.96),
    _field("deuda_pagada", "debt", "boolean", ("debt_paid",), 0.96),
    _field("acuerdo_pago_descripcion", "debt", aliases=("payment_agreement", "payment_plan"), merge_mode="set", max_length=900),
    _field("alquiler_periodos_impagados", "debt", aliases=("unpaid_rent_periods",), min_confidence=0.96, merge_mode="set", max_length=500),

    # Administración.
    _field("acto_administrativo", "administration", aliases=("administrative_act", "acto", "resolucion_tipo"), min_confidence=0.95, max_length=500),
    _field("procedimiento_tipo", "administration", aliases=("procedure_type",), max_length=260),
    _field("plazo_dias", "administration", "integer", ("term_days", "deadline_days"), 0.97),
    _field("principal_eur", "administration", "money", ("principal_amount", "importe_principal"), 0.98),
    _field("recargo_eur", "administration", "money", ("surcharge_amount", "importe_recargo"), 0.98),
    _field("importe_exigido_eur", "administration", "money", ("total_due", "amount_due"), 0.98),
    _field("norma", "administration", aliases=("legal_norm", "normative_reference"), min_confidence=0.95, max_length=500),
    _field("articulo", "administration", "identifier", ("article", "legal_article"), 0.95, max_length=100),
    _field("recurso_indicado", "administration", aliases=("appeal_indicated", "available_appeal"), min_confidence=0.95, max_length=260),
    _field("solicitud_fecha", "administration", "date", ("application_date", "request_date"), 0.97),
    _field("registro_ref", "administration", "identifier", ("registry_reference", "registration_number"), 0.97, max_length=180),
    _field("resolucion_sentido", "administration", aliases=("decision_outcome", "resolution_outcome"), min_confidence=0.95, max_length=260),
    _field("administrado", "administration", aliases=("applicant", "interested_party"), min_confidence=0.96, max_length=260),
    _field("dano_descripcion", "administration", aliases=("damage_description",), merge_mode="set", max_length=1200),
    _field("importe_indemnizacion_eur", "administration", "money", ("compensation_amount",), 0.97),

    # Viajes.
    _field("proveedor", "travel,claims", aliases=("provider", "empresa_responsable", "merchant"), min_confidence=0.95, max_length=260),
    _field("aerolinea", "travel", aliases=("airline", "carrier"), min_confidence=0.96, max_length=260),
    _field("agencia", "travel", aliases=("travel_agency", "booking_platform"), min_confidence=0.95, max_length=260),
    _field("alojamiento", "travel", aliases=("hotel", "accommodation"), min_confidence=0.95, max_length=260),
    _field("numero_reserva", "travel", "identifier", ("booking_reference", "reservation_number", "localizador"), 0.97, max_length=100),
    _field("numero_vuelo", "travel", "identifier", ("flight_number", "vuelo_numero"), 0.97, max_length=40),
    _field("fecha_vuelo", "travel", "date", ("flight_date",), 0.97),
    _field("origen", "travel", aliases=("origin", "departure_airport"), min_confidence=0.95, max_length=160),
    _field("destino", "travel", aliases=("destination", "arrival_airport"), min_confidence=0.95, max_length=160),
    _field("hora_salida_programada", "travel", "time", ("scheduled_departure_time",), 0.97),
    _field("hora_salida_real", "travel", "time", ("actual_departure_time",), 0.97),
    _field("hora_llegada_programada", "travel", "time", ("scheduled_arrival_time",), 0.97),
    _field("hora_llegada_real", "travel", "time", ("actual_arrival_time",), 0.97),
    _field("incidencia_tipo", "travel", aliases=("incident_type", "travel_incident_type"), max_length=220),
    _field("aviso_incidencia_fecha", "travel", "date", ("incident_notice_date", "cancellation_notice_date"), 0.96),
    _field("alternativa_ofrecida", "travel", aliases=("alternative_offered", "rerouting_offered"), merge_mode="set", max_length=900),
    _field("reembolso_estado", "travel", aliases=("refund_status",), max_length=260),
    _field("gastos_adicionales_eur", "travel", "money", ("additional_expenses", "documented_expenses"), 0.97),
    _field("equipaje_pir", "travel", "identifier", ("pir_reference", "baggage_pir"), 0.97, max_length=80),
    _field("equipaje_etiqueta", "travel", "identifier", ("baggage_tag", "bag_tag"), 0.97, max_length=100),
    _field("equipaje_estado", "travel", aliases=("baggage_status",), min_confidence=0.95, max_length=280),
    _field("fecha_entrega_equipaje", "travel", "date", ("baggage_delivery_date",), 0.97),
    _field("poliza_ref", "travel,claims", "identifier", ("policy_number", "numero_poliza"), 0.97, max_length=160),
    _field("siniestro_ref", "travel,claims", "identifier", ("claim_reference", "numero_siniestro"), 0.97, max_length=160),
    _field("compensacion_solicitada_eur", "travel", "money", ("compensation_requested",), 0.97),
    _field("numero_pasajeros", "travel", "integer", ("passenger_count",), 0.95),

    # Hotel o alojamiento independiente.
    _field("fecha_reserva", "travel", "date", ("booking_date", "reservation_date"), 0.97),
    _field("estancia_inicio", "travel", "date", ("stay_start", "check_in_date", "arrival_date"), 0.97),
    _field("estancia_fin", "travel", "date", ("stay_end", "check_out_date", "departure_date"), 0.97),
    _field("pais_alojamiento", "travel", aliases=("accommodation_country", "hotel_country"), min_confidence=0.96, max_length=160),
    _field("direccion_alojamiento", "travel", aliases=("accommodation_address", "hotel_address"), min_confidence=0.95, max_length=320),
    _field("habitacion_reservada", "travel", aliases=("reserved_room", "booked_room"), merge_mode="set", max_length=500),
    _field("habitacion_asignada", "travel", aliases=("assigned_room", "provided_room"), merge_mode="set", max_length=500),
    _field("categoria_reservada", "travel", aliases=("reserved_category", "booked_category"), max_length=240),
    _field("categoria_asignada", "travel", aliases=("assigned_category", "provided_category"), max_length=240),
    _field("regimen_alimenticio", "travel", aliases=("meal_plan", "board_basis"), merge_mode="set", max_length=300),
    _field("servicios_incluidos", "travel", aliases=("included_services", "hotel_amenities_included"), merge_mode="set", max_length=900),
    _field("condiciones_cancelacion", "travel", aliases=("cancellation_terms", "cancellation_policy"), merge_mode="set", max_length=1200),
    _field("cancelacion_solicitada_fecha", "travel", "date", ("cancellation_request_date", "hotel_cancellation_request_date"), 0.97),
    _field("cargo_cancelacion_eur", "travel", "money", ("cancellation_charge", "cancellation_fee"), 0.97),
    _field("reubicacion_ofrecida", "travel", aliases=("relocation_offered", "alternative_accommodation"), merge_mode="set", max_length=900),
    _field("precio_total_reserva_eur", "travel", "money", ("booking_total", "total_booking_price"), 0.97),
    _field("numero_huespedes", "travel", "integer", ("guest_count", "number_of_guests"), 0.95),
    _field("reserva_es_viaje_combinado", "travel", "boolean", ("is_package_travel", "package_travel_booking"), 0.97),

    # Reclamaciones previas, compartidas con viajes.
    _field("reclamacion_previa_fecha", "travel,claims", "date", ("prior_claim_date", "complaint_date"), 0.96),
    _field("canal_reclamacion", "travel,claims", aliases=("complaint_channel",), min_confidence=0.93, merge_mode="set", max_length=180),

    # Reclamaciones.
    _field("producto_servicio", "claims", aliases=("product_or_service", "service_description", "producto"), merge_mode="set", max_length=500),
    _field("fecha_contrato", "claims", "date", ("contract_date",), 0.96),
    _field("respuesta_proveedor", "claims", aliases=("provider_response",), merge_mode="set", max_length=1400),
    _field("fecha_respuesta", "claims", "date", ("response_date",), 0.96),
    _field("baja_solicitada_fecha", "claims", "date", ("cancellation_request_date", "service_cancellation_date"), 0.97),
    _field("fecha_baja_efectiva", "claims", "date", ("effective_cancellation_date",), 0.97),
    _field("referencia_servicio", "claims", "identifier", ("service_reference", "numero_cliente", "customer_reference"), 0.96, max_length=180),
    _field("numero_pedido", "claims", "identifier", ("order_number", "pedido_ref"), 0.97, max_length=140),
    _field("fecha_compra", "claims", "date", ("purchase_date",), 0.96),
    _field("fecha_entrega", "claims", "date", ("delivery_date",), 0.96),
    _field("garantia_hasta", "claims", "date", ("warranty_until", "warranty_expiry"), 0.96),
    _field("suministro_tipo", "claims", aliases=("utility_type", "supply_type"), max_length=160),
    _field("periodo_facturado", "claims", aliases=("billing_period",), min_confidence=0.95, merge_mode="set", max_length=180),
)


def _build_indexes() -> dict[str, dict[str, FactFieldSpec]]:
    by_service: dict[str, dict[str, FactFieldSpec]] = {
        service: {} for service in _ALL
    }
    for spec in _FIELDS:
        for service in spec.services:
            for raw_name in (spec.key, *spec.aliases):
                name = normalize_code(raw_name)
                current = by_service[service].get(name)
                if current is not None and current.key != spec.key:
                    raise RuntimeError(
                        f"Alias ambiguo {raw_name!r} en {service}: "
                        f"{current.key!r} frente a {spec.key!r}"
                    )
                by_service[service][name] = spec
    return by_service


_BY_SERVICE = _build_indexes()


def canonical_document_service(value: str | None) -> DepartmentCode:
    service = canonical_department(value)
    if service == "traffic":
        raise ValueError("Tráfico utiliza Reanalysis y su adaptador específico")
    if service not in _BY_SERVICE:
        return "other"
    return service  # type: ignore[return-value]


def field_spec(
    service: str | None,
    key_or_alias: str | None,
) -> Optional[FactFieldSpec]:
    canonical = canonical_document_service(service)
    return _BY_SERVICE[canonical].get(normalize_code(key_or_alias))


def registered_fact_keys(service: str | None) -> tuple[str, ...]:
    canonical = canonical_document_service(service)
    return tuple(sorted({spec.key for spec in _BY_SERVICE[canonical].values()}))


def minimum_fact_keys(service: str | None) -> tuple[str, ...]:
    canonical = canonical_document_service(service)
    return tuple(
        sorted({
            spec.key
            for spec in _BY_SERVICE[canonical].values()
            if spec.minimum_for_direction
        })
    )


def fact_catalog_summary(service: str | None) -> dict[str, object]:
    canonical = canonical_document_service(service)
    specs = {spec.key: spec for spec in _BY_SERVICE[canonical].values()}
    return {
        "version": DOCUMENT_FACT_CATALOG_VERSION,
        "service": canonical,
        "field_count": len(specs),
        "minimum_for_direction": list(minimum_fact_keys(canonical)),
        "fields": [
            {
                "key": spec.key,
                "label": spec.label,
                "value_type": spec.value_type,
                "merge_mode": spec.merge_mode,
                "min_confidence": spec.min_confidence,
            }
            for spec in sorted(specs.values(), key=lambda item: item.key)
        ],
    }
