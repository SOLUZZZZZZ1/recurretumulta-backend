"""Especialista RTM para ``travel.agency``.

Construye una Previa Jurídica conservadora para incidencias propias de agencias
y plataformas de reservas. Separa intermediación, parte contratante, proveedor,
cobro y factura; bloquea la frontera con viaje combinado; trata de forma
específica los servicios de viaje vinculados y nunca atribuye responsabilidad
por el mero hecho de que una plataforma haya mostrado o cobrado la reserva.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time, timezone
from typing import Any, Literal, Optional

from fastapi import HTTPException

from rtm_core.authority_repository import (
    FamilyResolutionRecord,
    ValidatedFactsRecord,
)
from rtm_core.contracts import (
    Deadline,
    LegalPreview,
    MissingItem,
    MissingItemSeverity,
    PreviewStatus,
)
from rtm_core.cross_service_specialist_support import (
    CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION,
    dedupe_missing,
    document_uses,
    ensure_specialist_authority,
    fact_review_items,
    family_evidence_keys,
    legal_argument,
    missing_item,
    summary_rows,
    validated_source_keys,
    validated_value,
)
from rtm_core.travel_agency_regime import (
    TRAVEL_AGENCY_REGIME_VERSION,
    TravelAgencyRegimeDecision,
    resolve_travel_agency_regime,
)


TRAVEL_AGENCY_SPECIALIST_VERSION = "rtm_travel_agency_specialist_v1_0"

TravelAgencyIncident = Literal[
    "booking_not_transmitted",
    "duplicate_charge",
    "refund_withheld",
    "platform_cancellation",
    "unilateral_change",
    "price_or_fee_mismatch",
    "role_or_identity_failure",
    "support_failure",
    "linked_travel_arrangement",
    "mixed",
    "unknown",
]


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _fold(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return " ".join(_fold(item) for item in value if item is not None)
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower().replace("\r", " ").replace("\n", " ")
    raw = re.sub(r"[^a-z0-9%/.,:+@€-]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _display(value: Any) -> str:
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, (list, tuple, set)):
        return "; ".join(str(item) for item in value)
    return str(value)


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    for candidate in (raw, raw.replace("/", "-"), raw.replace(".", "-")):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            pass
    for separator in ("/", "-", "."):
        parts = raw.split(separator)
        if len(parts) != 3:
            continue
        try:
            day, month, year = (int(part) for part in parts)
            return date(year, month, day)
        except ValueError:
            continue
    return None


def _amount(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _text(record: ValidatedFactsRecord) -> str:
    values: list[Any] = []
    for key in (
        "descripcion_hecho",
        "incidencia_tipo",
        "error_reserva_plataforma",
        "modificacion_por_plataforma",
        "condiciones_intermediacion",
        "reembolso_estado",
        "estado_pago_proveedor",
        "respuesta_documentada",
        "solucion_solicitada",
    ):
        value, _ = validated_value(record, key)
        if _present(value):
            values.append(value)
    return _fold(values)


def _incident(record: ValidatedFactsRecord) -> TravelAgencyIncident:
    text = _text(record)
    transmitted, _ = validated_value(record, "reserva_transmitida_proveedor")
    duplicate, _ = validated_value(record, "cargo_duplicado_eur")
    cancelled, _ = validated_value(record, "cancelacion_por_plataforma")
    changed, _ = validated_value(record, "modificacion_por_plataforma")
    displayed, _ = validated_value(record, "precio_mostrado_eur")
    charged, _ = validated_value(record, "cargo_total_reserva_eur")
    identity_disclosed, _ = validated_value(
        record,
        "identidad_proveedor_informada",
    )
    allocation_disclosed, _ = validated_value(
        record,
        "reparto_responsabilidad_informado",
    )
    linked, _ = validated_value(record, "servicio_viaje_vinculado")

    active: list[TravelAgencyIncident] = []

    if linked is True:
        active.append("linked_travel_arrangement")

    if transmitted is False or any(
        marker in text
        for marker in (
            "reserva no transmitida",
            "no envio la reserva",
            "no remitio la reserva",
            "proveedor no recibio la reserva",
            "reserva no consta en el proveedor",
            "booking not transmitted",
        )
    ):
        active.append("booking_not_transmitted")

    if (_amount(duplicate) or 0.0) > 0 or any(
        marker in text
        for marker in (
            "cargo duplicado",
            "doble cargo",
            "cobro duplicado",
            "charged twice",
            "duplicate charge",
        )
    ):
        active.append("duplicate_charge")

    if cancelled is True or any(
        marker in text
        for marker in (
            "cancelada por la plataforma",
            "cancelado por la plataforma",
            "la agencia cancelo la reserva",
            "platform cancelled",
        )
    ):
        active.append("platform_cancellation")

    if _present(changed) or any(
        marker in text
        for marker in (
            "modificacion por la plataforma",
            "cambio unilateral de la reserva",
            "cambio la reserva sin consentimiento",
            "unilateral booking change",
        )
    ):
        active.append("unilateral_change")

    displayed_amount = _amount(displayed)
    charged_amount = _amount(charged)
    if (
        displayed_amount is not None
        and charged_amount is not None
        and abs(charged_amount - displayed_amount) > 0.01
    ) or any(
        marker in text
        for marker in (
            "precio final distinto",
            "comision no informada",
            "cargo adicional no informado",
            "hidden fee",
            "price mismatch",
        )
    ):
        active.append("price_or_fee_mismatch")

    if any(
        marker in text
        for marker in (
            "reembolso pendiente",
            "reembolso retenido",
            "no ha reembolsado",
            "no se ha reembolsado",
            "refund pending",
            "refund withheld",
        )
    ):
        active.append("refund_withheld")

    if identity_disclosed is False or allocation_disclosed is False or any(
        marker in text
        for marker in (
            "no identifico al proveedor",
            "no informo quien era el vendedor",
            "no informo quien respondia",
            "papel de la plataforma no aclarado",
            "supplier identity not disclosed",
            "responsibility allocation not disclosed",
        )
    ):
        active.append("role_or_identity_failure")

    if any(
        marker in text
        for marker in (
            "sin respuesta de soporte",
            "atencion al cliente no responde",
            "imposible contactar con la plataforma",
            "support did not respond",
        )
    ):
        active.append("support_failure")

    unique = list(dict.fromkeys(active))
    if len(unique) > 1:
        return "mixed"
    if unique:
        return unique[0]
    return "unknown"


def _regime(record: ValidatedFactsRecord) -> TravelAgencyRegimeDecision:
    booking_date, _ = validated_value(
        record,
        "fecha_reserva",
        "fecha_documento",
    )
    platform_country, _ = validated_value(
        record,
        "pais_agencia_plataforma",
    )
    platform_name, _ = validated_value(record, "agencia")
    role_value, _ = validated_value(record, "rol_agencia_plataforma")
    online_marketplace, _ = validated_value(record, "mercado_en_linea")
    package_status, _ = validated_value(record, "reserva_es_viaje_combinado")
    linked_arrangement, _ = validated_value(record, "servicio_viaje_vinculado")
    contracting_party, _ = validated_value(
        record,
        "parte_contratante_reserva",
    )
    supplier, _ = validated_value(
        record,
        "proveedor_subyacente",
        "proveedor",
    )
    collector, _ = validated_value(record, "cobrador_reserva")
    invoice_issuer, _ = validated_value(record, "emisor_factura_reserva")

    return resolve_travel_agency_regime(
        booking_date=booking_date,
        platform_country=platform_country,
        platform_name=platform_name,
        role_value=role_value,
        online_marketplace=online_marketplace,
        package_status=package_status,
        linked_arrangement=linked_arrangement,
        contracting_party=contracting_party,
        underlying_supplier=supplier,
        payment_collector=collector,
        invoice_issuer=invoice_issuer,
    )


def _required_missing(
    record: ValidatedFactsRecord,
    incident: TravelAgencyIncident,
) -> list[MissingItem]:
    groups: list[tuple[str, str, tuple[str, ...]]] = [
        (
            "agency_fact_missing",
            "Falta validar la incidencia concreta imputada a la agencia o plataforma.",
            ("descripcion_hecho", "incidencia_tipo"),
        ),
        (
            "agency_identity_missing",
            "Falta identificar documentalmente la agencia o plataforma de reservas.",
            ("agencia",),
        ),
        (
            "agency_booking_reference_missing",
            "Falta validar el localizador o referencia de la reserva.",
            ("numero_reserva",),
        ),
        (
            "agency_booking_date_missing",
            "Falta la fecha documental de la reserva o contratación.",
            ("fecha_reserva", "fecha_documento"),
        ),
        (
            "agency_country_missing",
            "Falta validar el país de establecimiento de la agencia o plataforma.",
            ("pais_agencia_plataforma",),
        ),
        (
            "agency_requested_solution_missing",
            "Falta validar la solución concreta solicitada por el viajero.",
            ("solucion_solicitada",),
        ),
        (
            "agency_package_boundary_missing",
            "Falta confirmar documentalmente si la reserva era o no un viaje combinado.",
            ("reserva_es_viaje_combinado",),
        ),
        (
            "agency_linked_boundary_missing",
            "Falta confirmar si existía o no un servicio de viaje vinculado.",
            ("servicio_viaje_vinculado",),
        ),
        (
            "agency_role_evidence_missing",
            (
                "Falta validar el papel de la plataforma o la parte contratante, "
                "proveedor subyacente y reparto de funciones."
            ),
            (
                "rol_agencia_plataforma",
                "parte_contratante_reserva",
                "proveedor_subyacente",
                "proveedor",
            ),
        ),
    ]

    if incident == "booking_not_transmitted":
        groups.extend(
            [
                (
                    "agency_transmission_status_missing",
                    "Falta validar si la reserva fue transmitida al proveedor.",
                    ("reserva_transmitida_proveedor",),
                ),
                (
                    "agency_supplier_confirmation_missing",
                    "Falta la confirmación o negativa documental del proveedor.",
                    ("reserva_confirmada_proveedor", "respuesta_documentada"),
                ),
            ]
        )
    elif incident == "duplicate_charge":
        groups.extend(
            [
                (
                    "agency_duplicate_charge_missing",
                    "Falta validar el importe del cargo duplicado.",
                    ("cargo_duplicado_eur",),
                ),
                (
                    "agency_payment_collector_missing",
                    "Falta identificar quién efectuó o recibió el cobro.",
                    ("cobrador_reserva",),
                ),
            ]
        )
    elif incident == "refund_withheld":
        groups.extend(
            [
                (
                    "agency_refund_request_date_missing",
                    "Falta la fecha de solicitud del reembolso.",
                    ("fecha_solicitud_reembolso", "reclamacion_previa_fecha"),
                ),
                (
                    "agency_refund_status_missing",
                    "Falta el estado documental del reembolso.",
                    ("reembolso_estado",),
                ),
                (
                    "agency_payment_collector_missing",
                    "Falta identificar quién recibió el pago que debe devolverse.",
                    ("cobrador_reserva",),
                ),
            ]
        )
    elif incident == "platform_cancellation":
        groups.append(
            (
                "agency_cancellation_notice_missing",
                "Falta la comunicación documental de la cancelación por la plataforma.",
                (
                    "aviso_incidencia_fecha",
                    "fecha_notificacion",
                    "respuesta_documentada",
                ),
            )
        )
    elif incident == "unilateral_change":
        groups.append(
            (
                "agency_change_detail_missing",
                "Falta validar el cambio aplicado por la plataforma.",
                ("modificacion_por_plataforma", "respuesta_documentada"),
            )
        )
    elif incident == "price_or_fee_mismatch":
        groups.extend(
            [
                (
                    "agency_displayed_price_missing",
                    "Falta el precio total mostrado antes de contratar.",
                    ("precio_mostrado_eur",),
                ),
                (
                    "agency_charged_total_missing",
                    "Falta el importe finalmente cargado.",
                    ("cargo_total_reserva_eur", "importe_pagado_eur"),
                ),
            ]
        )
    elif incident == "role_or_identity_failure":
        groups.extend(
            [
                (
                    "agency_supplier_identity_status_missing",
                    "Falta validar si se informó la identidad del proveedor o vendedor.",
                    ("identidad_proveedor_informada",),
                ),
                (
                    "agency_responsibility_allocation_status_missing",
                    "Falta validar si se explicó el reparto de obligaciones.",
                    ("reparto_responsabilidad_informado",),
                ),
            ]
        )

    result: list[MissingItem] = []
    for code, description, keys in groups:
        value, _ = validated_value(record, *keys)
        if not _present(value) and not isinstance(value, bool):
            result.append(missing_item(code, description))
    return result


def _review_missing(
    record: ValidatedFactsRecord,
    regime: TravelAgencyRegimeDecision,
    incident: TravelAgencyIncident,
) -> list[MissingItem]:
    result: list[MissingItem] = []

    if regime.status != "current":
        result.append(
            missing_item(
                "agency_regime_review",
                regime.blocking_reason
                or "Debe determinarse y versionarse el régimen jurídico aplicable.",
                MissingItemSeverity.BLOCKING,
            )
        )

    if regime.boundary == "package_travel":
        result.append(
            missing_item(
                "agency_package_route_required",
                (
                    "La reserva aparece como viaje combinado. Debe utilizarse "
                    "travel.package y separar después los actos propios de la plataforma."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    if regime.role in {"unknown", "mixed"}:
        result.append(
            missing_item(
                "agency_role_review",
                (
                    "Debe resolverse documentalmente si la plataforma fue mera "
                    "intermediaria, parte contratante, proveedora, organizadora o "
                    "facilitadora de un servicio de viaje vinculado."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    if incident == "unknown":
        result.append(
            missing_item(
                "agency_incident_type_missing",
                (
                    "Debe determinarse si existe error de reserva, doble cargo, "
                    "reembolso retenido, cancelación, cambio unilateral, precio "
                    "distinto, falta de información o ausencia de soporte."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    elif incident == "mixed":
        result.append(
            missing_item(
                "agency_multiple_incidents_split_required",
                (
                    "Los hechos contienen varias incidencias. Deben separarse por "
                    "fecha, acto propio de la plataforma, proveedor, cobro, remedio "
                    "y perjuicio antes de congelar la previa."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    transmitted, _ = validated_value(record, "reserva_transmitida_proveedor")
    confirmed, _ = validated_value(record, "reserva_confirmada_proveedor")
    if transmitted is False and confirmed is True:
        result.append(
            missing_item(
                "agency_transmission_confirmation_conflict",
                (
                    "La reserva figura simultáneamente como no transmitida y "
                    "confirmada por el proveedor. Debe resolverse el conflicto."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    duplicate, _ = validated_value(record, "cargo_duplicado_eur")
    charged, _ = validated_value(
        record,
        "cargo_total_reserva_eur",
        "importe_pagado_eur",
    )
    duplicate_amount = _amount(duplicate)
    charged_amount = _amount(charged)
    if (
        duplicate_amount is not None
        and charged_amount is not None
        and duplicate_amount > charged_amount + 0.01
    ):
        result.append(
            missing_item(
                "agency_duplicate_charge_exceeds_documented_total",
                (
                    "El cargo duplicado supera el total documental de la reserva. "
                    "Debe revisarse el extracto y distinguir operaciones."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    marketplace, _ = validated_value(record, "mercado_en_linea")
    seller_is_trader, _ = validated_value(record, "vendedor_es_empresario")
    if marketplace is True and not isinstance(seller_is_trader, bool):
        result.append(
            missing_item(
                "agency_marketplace_trader_status_review",
                (
                    "En un mercado en línea debe verificarse si el tercero se "
                    "presentó como empresario y qué consecuencias se informaron."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    prior_claim, _ = validated_value(record, "reclamacion_previa_fecha")
    response, _ = validated_value(record, "respuesta_documentada")
    if not _present(prior_claim):
        result.append(
            missing_item(
                "agency_prior_claim_recommended",
                (
                    "Conviene conservar una reclamación previa fechada a la plataforma "
                    "antes de escalar a consumo, pago o vía judicial."
                ),
                MissingItemSeverity.RECOMMENDED,
            )
        )
    elif not _present(response):
        result.append(
            missing_item(
                "agency_platform_response_pending",
                "Falta la respuesta completa de la plataforma a la reclamación previa.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    return result


def _deadlines(record: ValidatedFactsRecord) -> list[Deadline]:
    result: list[Deadline] = []

    deadline, deadline_key = validated_value(record, "fecha_limite")
    parsed = _parse_date(deadline)
    if parsed is not None and deadline_key:
        result.append(
            Deadline(
                label="Fecha límite documental de la reclamación o trámite",
                due_at=datetime.combine(
                    parsed,
                    time(23, 59, 59),
                    tzinfo=timezone.utc,
                ),
                calculation_status="confirmed",
                source_fact_keys=[deadline_key],
                notes=[
                    "RTM conserva la fecha documental; OPS debe verificar cómputo, jurisdicción e interrupciones."
                ],
            )
        )

    promised, promised_key = validated_value(
        record,
        "fecha_reembolso_prometido",
    )
    promised_date = _parse_date(promised)
    if promised_date is not None and promised_key:
        result.append(
            Deadline(
                label="Fecha prometida por la empresa para el reembolso",
                due_at=datetime.combine(
                    promised_date,
                    time(23, 59, 59),
                    tzinfo=timezone.utc,
                ),
                calculation_status="confirmed",
                source_fact_keys=[promised_key],
                notes=[
                    "Es un compromiso documental de la empresa, no un plazo legal general calculado por RTM."
                ],
            )
        )

    if not result:
        result.append(
            Deadline(
                label="Plazo contractual, sectorial o de prescripción",
                calculation_status="unresolved",
                notes=[
                    "Debe identificarse según el servicio subyacente, la ley aplicable, el medio de pago y la vía elegida."
                ],
            )
        )
    return result


def build_travel_agency_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="travel",
        family="agencia_plataforma",
        specialist="travel.agency",
    )

    incident = _incident(facts_record)
    regime = _regime(facts_record)
    basis = list(regime.legal_basis) if regime.status == "current" else []

    agency, agency_key = validated_value(facts_record, "agencia")
    provider, provider_key = validated_value(
        facts_record,
        "proveedor_subyacente",
        "proveedor",
    )
    booking_ref, booking_ref_key = validated_value(
        facts_record,
        "numero_reserva",
    )
    booking_date, booking_date_key = validated_value(
        facts_record,
        "fecha_reserva",
        "fecha_documento",
    )
    role, role_key = validated_value(
        facts_record,
        "rol_agencia_plataforma",
    )
    country, country_key = validated_value(
        facts_record,
        "pais_agencia_plataforma",
    )
    contracting_party, contracting_party_key = validated_value(
        facts_record,
        "parte_contratante_reserva",
    )
    collector, collector_key = validated_value(
        facts_record,
        "cobrador_reserva",
    )
    invoice_issuer, invoice_issuer_key = validated_value(
        facts_record,
        "emisor_factura_reserva",
    )
    transmitted, transmitted_key = validated_value(
        facts_record,
        "reserva_transmitida_proveedor",
    )
    confirmed, confirmed_key = validated_value(
        facts_record,
        "reserva_confirmada_proveedor",
    )
    marketplace, marketplace_key = validated_value(
        facts_record,
        "mercado_en_linea",
    )
    seller_trader, seller_trader_key = validated_value(
        facts_record,
        "vendedor_es_empresario",
    )
    identity_disclosed, identity_disclosed_key = validated_value(
        facts_record,
        "identidad_proveedor_informada",
    )
    allocation_disclosed, allocation_disclosed_key = validated_value(
        facts_record,
        "reparto_responsabilidad_informado",
    )
    intermediary_terms, intermediary_terms_key = validated_value(
        facts_record,
        "condiciones_intermediacion",
    )
    displayed_price, displayed_price_key = validated_value(
        facts_record,
        "precio_mostrado_eur",
    )
    charged_total, charged_total_key = validated_value(
        facts_record,
        "cargo_total_reserva_eur",
        "importe_pagado_eur",
    )
    service_fee, service_fee_key = validated_value(
        facts_record,
        "comision_servicio_eur",
    )
    duplicate_charge, duplicate_charge_key = validated_value(
        facts_record,
        "cargo_duplicado_eur",
    )
    refund_request, refund_request_key = validated_value(
        facts_record,
        "fecha_solicitud_reembolso",
        "reclamacion_previa_fecha",
    )
    promised_refund, promised_refund_key = validated_value(
        facts_record,
        "fecha_reembolso_prometido",
    )
    refund_status, refund_status_key = validated_value(
        facts_record,
        "reembolso_estado",
    )
    supplier_payment, supplier_payment_key = validated_value(
        facts_record,
        "estado_pago_proveedor",
    )
    package_status, package_status_key = validated_value(
        facts_record,
        "reserva_es_viaje_combinado",
    )
    linked_status, linked_status_key = validated_value(
        facts_record,
        "servicio_viaje_vinculado",
    )
    response, response_key = validated_value(
        facts_record,
        "respuesta_documentada",
    )
    solution, solution_key = validated_value(
        facts_record,
        "solucion_solicitada",
    )
    fact, fact_key = validated_value(
        facts_record,
        "descripcion_hecho",
        "incidencia_tipo",
    )
    expenses, expenses_key = validated_value(
        facts_record,
        "gastos_adicionales_eur",
    )
    claimed, claimed_key = validated_value(
        facts_record,
        "importe_reclamado_eur",
    )

    summary, summary_keys = summary_rows(
        facts_record,
        (
            ("agencia", "Agencia o plataforma", ""),
            ("rol_agencia_plataforma", "Rol declarado", ""),
            ("pais_agencia_plataforma", "País de la plataforma", ""),
            ("numero_reserva", "Reserva", ""),
            ("fecha_reserva", "Fecha de reserva", ""),
            ("parte_contratante_reserva", "Parte contratante", ""),
            ("proveedor_subyacente", "Proveedor subyacente", ""),
            ("cobrador_reserva", "Cobrador", ""),
            ("emisor_factura_reserva", "Emisor de factura o recibo", ""),
            ("reserva_transmitida_proveedor", "Reserva transmitida", ""),
            ("reserva_confirmada_proveedor", "Reserva confirmada por proveedor", ""),
            ("mercado_en_linea", "Mercado en línea", ""),
            ("vendedor_es_empresario", "Tercero empresario", ""),
            ("identidad_proveedor_informada", "Identidad informada", ""),
            ("reparto_responsabilidad_informado", "Reparto de obligaciones informado", ""),
            ("precio_mostrado_eur", "Precio mostrado", " EUR"),
            ("cargo_total_reserva_eur", "Total cargado", " EUR"),
            ("comision_servicio_eur", "Comisión", " EUR"),
            ("cargo_duplicado_eur", "Cargo duplicado", " EUR"),
            ("fecha_solicitud_reembolso", "Solicitud de reembolso", ""),
            ("fecha_reembolso_prometido", "Reembolso prometido", ""),
            ("reembolso_estado", "Estado del reembolso", ""),
            ("reserva_es_viaje_combinado", "Viaje combinado", ""),
            ("servicio_viaje_vinculado", "Servicio de viaje vinculado", ""),
        ),
    )

    arguments = []

    chain_sources = validated_source_keys(
        facts_record,
        (
            agency_key,
            provider_key,
            booking_ref_key,
            booking_date_key,
            country_key,
            contracting_party_key,
            collector_key,
            invoice_issuer_key,
            fact_key,
        ),
    )
    if chain_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="agency_booking_chain_and_identity",
                title="Cadena de reserva e identidad de los participantes",
                body=(
                    "La reclamación debe reconstruir quién ofreció la reserva, quién "
                    "contrató, quién debía prestar el servicio, quién cobró, quién "
                    "emitió el justificante y qué confirmación recibió el viajero. "
                    "Estas funciones no se presumen equivalentes."
                ),
                source_fact_keys=chain_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    role_sources = validated_source_keys(
        facts_record,
        (
            role_key,
            agency_key,
            contracting_party_key,
            provider_key,
            collector_key,
            invoice_issuer_key,
            intermediary_terms_key,
            response_key,
        ),
    )
    if role_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="agency_role_and_obligation_allocation",
                title="Rol contractual y reparto de obligaciones",
                body=(
                    "Debe diferenciarse la mera intermediación de la condición de "
                    "parte contratante, proveedor, organizador o minorista. El cobro "
                    "o la comisión son indicios de una función económica, pero no "
                    "deciden por sí solos quién responde de la prestación subyacente."
                ),
                source_fact_keys=role_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    marketplace_sources = validated_source_keys(
        facts_record,
        (
            marketplace_key,
            seller_trader_key,
            identity_disclosed_key,
            allocation_disclosed_key,
            intermediary_terms_key,
            agency_key,
            provider_key,
            fact_key,
        ),
    )
    if marketplace_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="agency_marketplace_and_electronic_information",
                title="Información del mercado en línea y contratación electrónica",
                body=(
                    "Antes de contratar deben quedar identificados la empresa de la "
                    "plataforma, el tercero que vende o presta, su condición de "
                    "empresario y la distribución de obligaciones. Las condiciones "
                    "comunicadas después no suplen la información previa ni la "
                    "confirmación documental de la reserva."
                ),
                source_fact_keys=marketplace_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    booking_sources = validated_source_keys(
        facts_record,
        (
            booking_ref_key,
            transmitted_key,
            confirmed_key,
            provider_key,
            fact_key,
            response_key,
            solution_key,
        ),
    )
    if booking_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="agency_booking_error_transmission_and_confirmation",
                title="Error, transmisión y confirmación de la reserva",
                body=(
                    "La empresa que acepta gestionar una reserva responde de los "
                    "errores propios de captura, transmisión, modificación o "
                    "confirmación que le sean imputables. Debe separarse ese error de "
                    "la posterior falta de prestación del proveedor subyacente."
                ),
                source_fact_keys=booking_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    payment_sources = validated_source_keys(
        facts_record,
        (
            collector_key,
            invoice_issuer_key,
            displayed_price_key,
            charged_total_key,
            service_fee_key,
            duplicate_charge_key,
            refund_request_key,
            promised_refund_key,
            refund_status_key,
            supplier_payment_key,
            response_key,
            solution_key,
        ),
    )
    if payment_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="agency_payment_fees_duplicate_charge_and_refund",
                title="Cobro, comisiones, duplicidades y reembolso",
                body=(
                    "El circuito económico debe reconstruirse por operación: precio "
                    "mostrado, comisión informada, total cargado, cobrador, factura, "
                    "pago al proveedor y devoluciones ya efectuadas. Un doble cargo o "
                    "un reembolso retenido debe reclamarse al sujeto que controla esa "
                    "operación, sin duplicar importes frente al proveedor."
                ),
                source_fact_keys=payment_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    boundary_sources = validated_source_keys(
        facts_record,
        (
            package_status_key,
            linked_status_key,
            agency_key,
            role_key,
            provider_key,
            fact_key,
        ),
    )
    if boundary_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="agency_package_and_linked_arrangement_boundary",
                title="Frontera con viaje combinado y servicio vinculado",
                body=(
                    "La misma interfaz puede actuar como intermediaria de un servicio "
                    "independiente, organizadora o minorista de un viaje combinado o "
                    "facilitadora de un servicio de viaje vinculado. Cada figura tiene "
                    "deberes distintos y debe acreditarse antes de seleccionar el "
                    "régimen y el destinatario principal."
                ),
                source_fact_keys=boundary_sources,
                priority="secondary",
                legal_basis=basis,
            )
        )

    damage_sources = validated_source_keys(
        facts_record,
        (
            charged_total_key,
            duplicate_charge_key,
            service_fee_key,
            expenses_key,
            claimed_key,
            refund_status_key,
            solution_key,
            fact_key,
        ),
    )
    if damage_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="agency_proven_loss_without_duplicate_recovery",
                title="Perjuicio acreditado y ausencia de doble recuperación",
                body=(
                    "La cuantía debe desglosarse entre cargo indebido, precio no "
                    "devuelto, comisión, diferencia de reserva, gastos necesarios y "
                    "otros daños probados. Deben descontarse los reembolsos ya recibidos "
                    "y evitar reclamar dos veces la misma partida."
                ),
                source_fact_keys=damage_sources,
                priority="secondary",
                legal_basis=basis,
            )
        )

    if not arguments:
        raise HTTPException(
            status_code=409,
            detail="No existen hechos validados suficientes para construir la previa.",
        )

    source_keys = validated_source_keys(
        facts_record,
        [
            *family_evidence_keys(family_record),
            *summary_keys,
            *(key for argument in arguments for key in argument.source_fact_keys),
        ],
    )

    missing = dedupe_missing(
        [
            *_required_missing(facts_record, incident),
            *_review_missing(facts_record, regime, incident),
            *fact_review_items(facts_record, prefix="agency"),
        ]
    )

    destination = (
        str(agency).strip()
        if _present(agency)
        else "AGENCIA O PLATAFORMA PENDIENTE DE VALIDAR"
    )
    subject_parts = ["RECLAMACIÓN AGENCIA O PLATAFORMA", incident.upper()]
    if _present(booking_ref):
        subject_parts.append(f"reserva {booking_ref}")

    primary_strategy = (
        "Reconstruir la cadena contractual y de pago; separar plataforma, parte "
        "contratante y proveedor; cerrar la frontera con viaje combinado o servicio "
        "vinculado; atribuir a cada sujeto únicamente sus actos documentados; exigir "
        "corrección, confirmación o reembolso trazables; y reclamar daños acreditados."
    )
    if _present(solution):
        primary_strategy += (
            f" La solución documental solicitada es: {_display(solution)}."
        )

    requested_outcomes = [
        "Identificación escrita de la parte contratante, proveedor y cobrador de la reserva.",
        "Explicación documentada de la transmisión, confirmación, cambio o cancelación.",
        "Corrección o restablecimiento de la reserva cuando todavía sea útil.",
        "Reembolso de cargos indebidos, duplicados o importes no prestados.",
        "Devolución de comisiones no informadas o no justificadas cuando proceda.",
        "Reintegro de gastos razonables, necesarios y documentados.",
        "Respuesta motivada y desglose completo de operaciones y devoluciones.",
    ]

    risks = [
        (
            "Confundir cobro con prestación puede dirigir la reclamación al sujeto "
            "equivocado y dejar fuera al proveedor realmente responsable."
        ),
        (
            "Una plataforma puede asumir funciones distintas en reservas diferentes; "
            "su rol debe acreditarse para este contrato concreto."
        ),
        (
            "Viaje combinado, servicio vinculado e intermediación independiente no "
            "son rutas intercambiables."
        ),
        (
            "La devolución por tarjeta, seguro, proveedor y plataforma debe "
            "coordinarse para evitar una doble recuperación."
        ),
        *list(regime.warnings),
    ]

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="agencia_plataforma",
        specialist="travel.agency",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Se ha documentado una incidencia de agencia o plataforma ({incident}) "
            f"en la reserva {_display(booking_ref)}."
            if _present(booking_ref)
            else "Se ha documentado una posible incidencia de agencia o plataforma."
        ),
        client_goal=(
            "Obtener la reserva o el reembolso correcto y recuperar únicamente "
            "los gastos y daños realmente acreditados."
        ),
        primary_strategy=primary_strategy,
        secondary_strategies=[
            "Reclamar también al proveedor subyacente cuando su incumplimiento esté documentado.",
            "Revisar el medio de pago o chargeback sin duplicar la reclamación económica.",
            "Escalar a consumo, organismo sectorial o vía judicial según ley, cuantía y respuesta.",
        ],
        requested_outcomes=list(dict.fromkeys(requested_outcomes)),
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record),
        risks=list(dict.fromkeys(risks)),
        destination=destination,
        document_type="RECLAMACIÓN EXTRAJUDICIAL A AGENCIA O PLATAFORMA DE RESERVAS",
        subject=" — ".join(subject_parts),
        legal_arguments=arguments,
        additional_requests=[
            "Confirmación de reserva y condiciones visibles al contratar.",
            "Identidad y datos legales de plataforma, parte contratante y proveedor.",
            "Condiciones de intermediación y reparto de obligaciones.",
            "Capturas del precio final, comisión y vendedor identificado.",
            "Factura, recibo, extracto y detalle del comercio que realizó cada cargo.",
            "Registro técnico o comunicación de transmisión al proveedor.",
            "Confirmación o negativa completa del proveedor subyacente.",
            "Historial de cambios, cancelaciones y comunicaciones de soporte.",
            "Solicitud de reembolso, fecha prometida y trazabilidad de la devolución.",
            "Prueba de cargos duplicados o diferencias de precio.",
            "Facturas de nueva reserva, transporte y demás gastos derivados.",
            "Documento que confirme viaje combinado o servicio de viaje vinculado.",
        ],
        created_by_component=(
            "travel.agency:"
            f"{TRAVEL_AGENCY_SPECIALIST_VERSION}+"
            f"{TRAVEL_AGENCY_REGIME_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
