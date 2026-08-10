"""Especialista RTM para ``travel.hotel``.

Construye una Previa Jurídica conservadora para reservas de hotel o alojamiento
turístico contratadas de forma independiente. Distingue la cancelación del
consumidor del incumplimiento del proveedor, separa hotel y plataforma, comprueba
si el caso pertenece realmente a un viaje combinado y nunca inventa una
compensación plana ni da por válida una penalización no documentada.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any, Literal, Optional

from fastapi import HTTPException

from rtm_core.accommodation_consumer_regime import (
    ACCOMMODATION_CONSUMER_REGIME_VERSION,
    AccommodationConsumerRegimeDecision,
    resolve_accommodation_consumer_regime,
)
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


TRAVEL_HOTEL_SPECIALIST_VERSION = "rtm_travel_hotel_specialist_v1_0"

HotelIncident = Literal[
    "provider_cancellation",
    "consumer_cancellation",
    "unavailability",
    "category_mismatch",
    "quality_defect",
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
    return re.sub(r"\s+", " ", raw).strip()


def _display(value: Any) -> str:
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


def _hotel_text(record: ValidatedFactsRecord) -> str:
    values: list[Any] = []
    for key in (
        "descripcion_hecho",
        "incidencia_tipo",
        "alojamiento",
        "habitacion_reservada",
        "habitacion_asignada",
        "categoria_reservada",
        "categoria_asignada",
        "servicios_incluidos",
        "condiciones_cancelacion",
        "reubicacion_ofrecida",
        "alternativa_ofrecida",
        "reembolso_estado",
        "respuesta_documentada",
        "solucion_solicitada",
    ):
        value, _ = validated_value(record, key)
        if _present(value):
            values.append(value)
    return _fold(values)


def _provider_cancelled(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "cancelado por el hotel",
            "cancelada por el hotel",
            "el hotel cancelo",
            "el alojamiento cancelo",
            "cancelado por el alojamiento",
            "cancelada por el alojamiento",
            "cancelado por el proveedor",
            "cancelada por el proveedor",
            "el proveedor cancelo",
            "hotel cerrado",
            "alojamiento cerrado",
            "cierre del hotel",
        )
    )


def _consumer_cancelled(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "el cliente solicito cancelar",
            "el consumidor solicito cancelar",
            "solicito la cancelacion",
            "cancelacion solicitada por el cliente",
            "cancelacion solicitada por el consumidor",
            "cancelado por el huesped",
            "cancelada por el huesped",
            "no pudo viajar y cancelo",
            "no pudo alojarse y cancelo",
        )
    )


def _incident(record: ValidatedFactsRecord) -> HotelIncident:
    text = _hotel_text(record)

    if _provider_cancelled(text):
        return "provider_cancellation"
    if _consumer_cancelled(text):
        return "consumer_cancellation"

    unavailability = any(
        marker in text
        for marker in (
            "overbooking",
            "sobreventa",
            "sin habitacion",
            "no habia habitacion",
            "habitacion no disponible",
            "reserva no disponible",
            "reserva no encontrada",
            "no pudieron alojar",
            "no pudo alojarse",
            "denegaron el alojamiento",
            "hotel completo pese a la reserva",
        )
    )
    category = any(
        marker in text
        for marker in (
            "categoria inferior",
            "habitacion inferior",
            "habitacion distinta",
            "habitacion diferente",
            "sin las vistas reservadas",
            "sin balcon reservado",
            "sin cama reservada",
            "servicios incluidos no disponibles",
            "regimen alimenticio distinto",
        )
    )
    quality = any(
        marker in text
        for marker in (
            "habitacion sucia",
            "alojamiento sucio",
            "chinches",
            "insalubre",
            "sin agua",
            "sin agua caliente",
            "sin calefaccion",
            "sin aire acondicionado",
            "ruido insoportable",
            "averia grave",
            "habitacion defectuosa",
            "instalaciones defectuosas",
            "riesgo sanitario",
        )
    )

    active = [
        name
        for name, enabled in (
            ("unavailability", unavailability),
            ("category_mismatch", category),
            ("quality_defect", quality),
        )
        if enabled
    ]
    if len(active) > 1:
        return "mixed"
    if not active:
        return "unknown"
    return active[0]  # type: ignore[return-value]


def _relocation_documented(record: ValidatedFactsRecord) -> bool:
    explicit, _ = validated_value(
        record,
        "reubicacion_ofrecida",
        "alternativa_ofrecida",
    )
    text = _fold([explicit, _hotel_text(record)])
    return _present(explicit) or any(
        marker in text
        for marker in (
            "reubicado",
            "reubicacion",
            "otro hotel",
            "hotel alternativo",
            "alojamiento alternativo",
        )
    )


def _package_status(record: ValidatedFactsRecord) -> Optional[bool]:
    explicit, _ = validated_value(record, "reserva_es_viaje_combinado")
    text = _hotel_text(record)
    marker = any(
        token in text
        for token in (
            "viaje combinado",
            "paquete turistico",
            "vuelo y hotel",
            "hotel y vuelo",
            "transporte y alojamiento",
            "paquete de vacaciones",
        )
    )

    if isinstance(explicit, bool):
        if explicit is False and marker:
            return None
        return explicit
    if marker:
        return True
    return None


def _required_missing(
    record: ValidatedFactsRecord,
    incident: HotelIncident,
) -> list[MissingItem]:
    groups = [
        (
            "hotel_fact_missing",
            "Falta validar la incidencia concreta del alojamiento.",
            ("descripcion_hecho", "incidencia_tipo"),
        ),
        (
            "hotel_accommodation_missing",
            "Falta identificar el hotel o alojamiento contratado.",
            ("alojamiento", "proveedor"),
        ),
        (
            "hotel_booking_reference_missing",
            "Falta validar la confirmación o localizador de la reserva.",
            ("numero_reserva",),
        ),
        (
            "hotel_booking_date_missing",
            "Falta validar la fecha de reserva o contratación.",
            ("fecha_reserva", "fecha_documento"),
        ),
        (
            "hotel_stay_start_missing",
            "Falta validar la fecha de entrada.",
            ("estancia_inicio",),
        ),
        (
            "hotel_stay_end_missing",
            "Falta validar la fecha de salida.",
            ("estancia_fin",),
        ),
        (
            "hotel_country_missing",
            "Falta validar el país en el que se encuentra el alojamiento.",
            ("pais_alojamiento",),
        ),
        (
            "hotel_price_missing",
            "Falta validar el precio total o el importe efectivamente pagado.",
            ("precio_total_reserva_eur", "importe_pagado_eur"),
        ),
        (
            "hotel_requested_solution_missing",
            "Falta validar la solución solicitada por el cliente.",
            ("solucion_solicitada",),
        ),
    ]

    if incident == "consumer_cancellation":
        groups.extend(
            [
                (
                    "hotel_cancellation_terms_missing",
                    (
                        "Faltan las condiciones de cancelación aceptadas antes de "
                        "contratar."
                    ),
                    ("condiciones_cancelacion",),
                ),
                (
                    "hotel_cancellation_request_date_missing",
                    "Falta la fecha en que el consumidor solicitó cancelar.",
                    ("cancelacion_solicitada_fecha",),
                ),
            ]
        )

    if incident == "category_mismatch":
        groups.extend(
            [
                (
                    "hotel_reserved_room_missing",
                    "Falta describir la habitación o categoría reservada.",
                    ("habitacion_reservada", "categoria_reservada"),
                ),
                (
                    "hotel_assigned_room_missing",
                    "Falta describir la habitación o categoría finalmente asignada.",
                    ("habitacion_asignada", "categoria_asignada"),
                ),
            ]
        )

    result: list[MissingItem] = []
    for code, description, keys in groups:
        value, _ = validated_value(record, *keys)
        if not _present(value):
            result.append(missing_item(code, description))
    return result


def _review_missing(
    record: ValidatedFactsRecord,
    regime: AccommodationConsumerRegimeDecision,
    incident: HotelIncident,
    package_status: Optional[bool],
    relocation: bool,
) -> list[MissingItem]:
    result: list[MissingItem] = []

    if regime.status != "current":
        result.append(
            missing_item(
                "hotel_regime_review",
                regime.blocking_reason
                or "Debe determinarse y versionarse el régimen aplicable.",
                MissingItemSeverity.BLOCKING,
            )
        )

    if package_status is True:
        result.append(
            missing_item(
                "hotel_package_travel_route_required",
                (
                    "La reserva forma parte de un viaje combinado o paquete. Debe "
                    "revisarse y, si procede, resolverse la familia travel.package "
                    "antes de aplicar el especialista de hotel independiente."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    elif package_status is None:
        result.append(
            missing_item(
                "hotel_package_status_missing",
                (
                    "Debe confirmarse documentalmente que el alojamiento se contrató "
                    "de forma independiente y no como viaje combinado o servicio "
                    "de viaje vinculado."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    if incident == "unknown":
        result.append(
            missing_item(
                "hotel_incident_type_missing",
                (
                    "Debe determinarse si existe cancelación del proveedor, "
                    "cancelación del consumidor, falta de disponibilidad, diferencia "
                    "de categoría o defecto de calidad."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    elif incident == "mixed":
        result.append(
            missing_item(
                "hotel_multiple_incidents_split_required",
                (
                    "Los hechos contienen varias incidencias materiales. Deben "
                    "separarse por fecha, habitación, servicio, remedio y perjuicio."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    result.extend(
        [
            missing_item(
                "hotel_supplier_and_platform_role_review",
                (
                    "Debe distinguirse el establecimiento que presta el alojamiento "
                    "de la agencia o plataforma, y comprobar quién confirmó, cobró, "
                    "canceló y respondió."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "hotel_confirmation_and_terms_durable_support_review",
                (
                    "OPS debe conservar la confirmación de reserva y las condiciones "
                    "generales y de cancelación en el soporte duradero recibido al "
                    "contratar, no solo una captura posterior."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "hotel_local_tourism_and_governing_law_review",
                (
                    "Debe comprobarse la normativa turística local, la ley aplicable "
                    "al contrato, la jurisdicción y el sistema de reclamaciones del "
                    "lugar del alojamiento."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
        ]
    )

    start, _ = validated_value(record, "estancia_inicio")
    end, _ = validated_value(record, "estancia_fin")
    request_date, _ = validated_value(record, "cancelacion_solicitada_fecha")
    complaint_date, _ = validated_value(record, "reclamacion_previa_fecha")
    booking_date, _ = validated_value(record, "fecha_reserva", "fecha_documento")

    parsed_start = _parse_date(start)
    parsed_end = _parse_date(end)
    parsed_request = _parse_date(request_date)
    parsed_complaint = _parse_date(complaint_date)
    parsed_booking = _parse_date(booking_date)

    if (
        parsed_booking is not None
        and parsed_complaint is not None
        and parsed_complaint < parsed_booking
    ):
        result.append(
            missing_item(
                "hotel_complaint_before_booking_conflict",
                "La reclamación aparece anterior a la reserva.",
                MissingItemSeverity.BLOCKING,
            )
        )

    if incident == "consumer_cancellation":
        conditions, _ = validated_value(record, "condiciones_cancelacion")
        if _present(conditions):
            result.append(
                missing_item(
                    "hotel_contractual_cancellation_interpretation_review",
                    (
                        "La reserva tiene fechas específicas. Debe interpretarse el "
                        "derecho contractual de cancelación y la penalización "
                        "comunicada, sin convertirlo en un desistimiento legal "
                        "automático de catorce días."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
        if (
            parsed_request is not None
            and parsed_start is not None
            and parsed_request >= parsed_start
        ):
            result.append(
                missing_item(
                    "hotel_late_cancellation_or_no_show_review",
                    (
                        "La cancelación se solicitó al inicio o después de la estancia. "
                        "Debe diferenciarse cancelación tardía, no-show e "
                        "incumplimiento del proveedor."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )

    alternative, _ = validated_value(
        record,
        "reubicacion_ofrecida",
        "alternativa_ofrecida",
    )
    refund, _ = validated_value(record, "reembolso_estado")
    if incident in {"provider_cancellation", "unavailability"}:
        if not _present(alternative) and not _present(refund):
            result.append(
                missing_item(
                    "hotel_provider_remedy_missing",
                    (
                        "No consta alojamiento alternativo, reubicación ni estado del "
                        "reembolso tras la falta de prestación."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
        else:
            result.append(
                missing_item(
                    "hotel_provider_remedy_equivalence_review",
                    (
                        "Debe compararse la alternativa con la ubicación, categoría, "
                        "ocupación, régimen y servicios reservados, y verificar el "
                        "reembolso de diferencias y gastos."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )

    if incident == "category_mismatch":
        result.append(
            missing_item(
                "hotel_category_and_service_comparison_review",
                (
                    "Debe compararse la oferta contratada con la habitación, categoría "
                    "y servicios realmente proporcionados, incluyendo publicidad y "
                    "fotografías vigentes al reservar."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    if incident == "quality_defect":
        channel, _ = validated_value(record, "canal_reclamacion")
        if not _present(complaint_date) or not _present(channel):
            result.append(
                missing_item(
                    "hotel_contemporaneous_complaint_review",
                    (
                        "Falta acreditar cuándo y por qué canal se comunicó el defecto "
                        "durante la estancia y qué oportunidad tuvo el establecimiento "
                        "de corregirlo."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
        result.append(
            missing_item(
                "hotel_quality_evidence_review",
                (
                    "Deben incorporarse fotografías, vídeos, partes, comunicaciones "
                    "y, si procede, informes sanitarios o técnicos del defecto."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    charge, _ = validated_value(record, "cargo_cancelacion_eur")
    paid, _ = validated_value(
        record,
        "precio_total_reserva_eur",
        "importe_pagado_eur",
    )
    if _present(charge):
        try:
            charge_value = float(charge)
            paid_value = float(paid) if _present(paid) else None
        except (TypeError, ValueError):
            charge_value = -1.0
            paid_value = None

        if charge_value < 0:
            result.append(
                missing_item(
                    "hotel_cancellation_charge_invalid",
                    "El cargo de cancelación no contiene una cuantía válida.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        elif paid_value is not None and charge_value > paid_value + 0.01:
            result.append(
                missing_item(
                    "hotel_cancellation_charge_exceeds_price",
                    (
                        "El cargo de cancelación supera el precio validado de la "
                        "reserva y debe resolverse antes de reclamar."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
        else:
            result.append(
                missing_item(
                    "hotel_cancellation_charge_disclosure_and_balance_review",
                    (
                        "Debe comprobarse que el cargo fue informado antes de "
                        "contratar, que la cláusula quedó incorporada y que no produce "
                        "un desequilibrio no justificado."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )

    if relocation:
        result.append(
            missing_item(
                "hotel_relocation_actual_cost_review",
                (
                    "Debe documentarse quién pagó la reubicación, las diferencias de "
                    "precio, transportes y demás gastos causales."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    expenses, _ = validated_value(record, "gastos_adicionales_eur")
    amount, _ = validated_value(record, "importe_reclamado_eur")
    if _present(expenses):
        result.append(
            missing_item(
                "hotel_expense_receipts_review",
                (
                    "Deben aportarse facturas y justificantes de gastos razonables, "
                    "necesarios y vinculados a la incidencia."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if _present(amount):
        result.append(
            missing_item(
                "hotel_claim_amount_breakdown_review",
                (
                    "La cuantía reclamada debe desglosarse entre precio, diferencia "
                    "de categoría, gastos y daños acreditados; no se presume una "
                    "compensación fija."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    if parsed_start is not None and parsed_end is not None and parsed_end <= parsed_start:
        result.append(
            missing_item(
                "hotel_stay_dates_conflict",
                "La fecha de salida no es posterior a la fecha de entrada.",
                MissingItemSeverity.BLOCKING,
            )
        )

    result.append(
        missing_item(
            "hotel_chargeback_insurance_duplicate_recovery_review",
            (
                "Debe comprobarse si hubo devolución de tarjeta, seguro de viaje, "
                "bono o reembolso parcial para evitar duplicidades."
            ),
            MissingItemSeverity.HUMAN_REVIEW,
        )
    )
    return result


def _deadlines(
    record: ValidatedFactsRecord,
    incident: HotelIncident,
) -> list[Deadline]:
    explicit, explicit_key = validated_value(record, "fecha_limite")
    if _present(explicit) and explicit_key:
        parsed = _parse_date(explicit)
        if parsed is not None:
            return [
                Deadline(
                    label="Fecha límite documental indicada",
                    due_at=datetime(
                        parsed.year,
                        parsed.month,
                        parsed.day,
                        tzinfo=timezone.utc,
                    ),
                    calculation_status="confirmed",
                    source_fact_keys=[explicit_key],
                    notes=[
                        "OPS debe confirmar qué actuación concreta vence en esa fecha."
                    ],
                )
            ]

    start, start_key = validated_value(record, "estancia_inicio")
    end, end_key = validated_value(record, "estancia_fin")
    booking, booking_key = validated_value(
        record,
        "fecha_reserva",
        "fecha_documento",
    )
    conditions, conditions_key = validated_value(
        record,
        "condiciones_cancelacion",
    )
    request, request_key = validated_value(
        record,
        "cancelacion_solicitada_fecha",
    )

    deadlines: list[Deadline] = []
    if incident == "consumer_cancellation":
        deadlines.append(
            Deadline(
                label="Plazo contractual de cancelación",
                due_at=None,
                calculation_status="unresolved",
                source_fact_keys=validated_source_keys(
                    record,
                    (conditions_key, request_key, start_key),
                ),
                notes=[
                    (
                        "Debe reconstruirse la hora, zona temporal, canal y tramo de "
                        "penalización previsto en las condiciones aceptadas."
                    ),
                    (
                        f"Solicitud documentada: {request}."
                        if _present(request)
                        else "Fecha de solicitud pendiente."
                    ),
                ],
            )
        )

    deadlines.append(
        Deadline(
            label="Prescripción o caducidad de la acción contractual",
            due_at=None,
            calculation_status="unresolved",
            source_fact_keys=validated_source_keys(
                record,
                (booking_key, start_key, end_key),
            ),
            notes=[
                (
                    "No se calcula automáticamente: depende de la ley aplicable, el "
                    "tipo de acción, el país del alojamiento, la fecha de exigibilidad "
                    "y los actos interruptivos."
                ),
                (
                    f"Reserva: {booking}; estancia: {start} a {end}."
                    if _present(booking) and _present(start) and _present(end)
                    else "Cronología contractual pendiente de completar."
                ),
            ],
        )
    )
    return deadlines


def build_travel_hotel_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="travel",
        family="hotel",
        specialist="travel.hotel",
    )

    fact, fact_key = validated_value(facts_record, "descripcion_hecho")
    incident_value, incident_key = validated_value(
        facts_record,
        "incidencia_tipo",
    )
    accommodation, accommodation_key = validated_value(
        facts_record,
        "alojamiento",
        "proveedor",
    )
    provider, provider_key = validated_value(facts_record, "proveedor")
    agency, agency_key = validated_value(facts_record, "agencia")
    booking_ref, booking_ref_key = validated_value(
        facts_record,
        "numero_reserva",
    )
    booking_date, booking_date_key = validated_value(
        facts_record,
        "fecha_reserva",
        "fecha_documento",
    )
    stay_start, stay_start_key = validated_value(
        facts_record,
        "estancia_inicio",
    )
    stay_end, stay_end_key = validated_value(facts_record, "estancia_fin")
    country, country_key = validated_value(
        facts_record,
        "pais_alojamiento",
    )
    address, address_key = validated_value(
        facts_record,
        "direccion_alojamiento",
    )
    reserved_room, reserved_room_key = validated_value(
        facts_record,
        "habitacion_reservada",
    )
    assigned_room, assigned_room_key = validated_value(
        facts_record,
        "habitacion_asignada",
    )
    reserved_category, reserved_category_key = validated_value(
        facts_record,
        "categoria_reservada",
    )
    assigned_category, assigned_category_key = validated_value(
        facts_record,
        "categoria_asignada",
    )
    board, board_key = validated_value(
        facts_record,
        "regimen_alimenticio",
    )
    services, services_key = validated_value(
        facts_record,
        "servicios_incluidos",
    )
    cancellation_terms, cancellation_terms_key = validated_value(
        facts_record,
        "condiciones_cancelacion",
    )
    cancellation_date, cancellation_date_key = validated_value(
        facts_record,
        "cancelacion_solicitada_fecha",
    )
    cancellation_charge, cancellation_charge_key = validated_value(
        facts_record,
        "cargo_cancelacion_eur",
    )
    relocation_value, relocation_key = validated_value(
        facts_record,
        "reubicacion_ofrecida",
        "alternativa_ofrecida",
    )
    refund, refund_key = validated_value(facts_record, "reembolso_estado")
    paid, paid_key = validated_value(
        facts_record,
        "precio_total_reserva_eur",
        "importe_pagado_eur",
    )
    expenses, expenses_key = validated_value(
        facts_record,
        "gastos_adicionales_eur",
    )
    amount, amount_key = validated_value(
        facts_record,
        "importe_reclamado_eur",
    )
    guests, guests_key = validated_value(
        facts_record,
        "numero_huespedes",
    )
    complaint_date, complaint_date_key = validated_value(
        facts_record,
        "reclamacion_previa_fecha",
    )
    complaint_channel, complaint_channel_key = validated_value(
        facts_record,
        "canal_reclamacion",
    )
    response, response_key = validated_value(
        facts_record,
        "respuesta_documentada",
    )
    solution, solution_key = validated_value(
        facts_record,
        "solucion_solicitada",
    )
    package_value, package_key = validated_value(
        facts_record,
        "reserva_es_viaje_combinado",
    )

    incident = _incident(facts_record)
    package_status = _package_status(facts_record)
    relocation = _relocation_documented(facts_record)
    regime = resolve_accommodation_consumer_regime(
        booking_date=booking_date,
        stay_start=stay_start,
        stay_end=stay_end,
        accommodation_country=country,
    )
    basis = list(regime.legal_basis)

    summary, summary_keys = summary_rows(
        facts_record,
        (
            ("alojamiento", "Alojamiento", ""),
            ("proveedor", "Proveedor", ""),
            ("agencia", "Agencia o plataforma", ""),
            ("numero_reserva", "Reserva", ""),
            ("fecha_reserva", "Fecha de reserva", ""),
            ("estancia_inicio", "Entrada", ""),
            ("estancia_fin", "Salida", ""),
            ("pais_alojamiento", "País", ""),
            ("direccion_alojamiento", "Dirección", ""),
            ("habitacion_reservada", "Habitación reservada", ""),
            ("habitacion_asignada", "Habitación asignada", ""),
            ("categoria_reservada", "Categoría reservada", ""),
            ("categoria_asignada", "Categoría asignada", ""),
            ("regimen_alimenticio", "Régimen", ""),
            ("servicios_incluidos", "Servicios incluidos", ""),
            ("condiciones_cancelacion", "Cancelación contratada", ""),
            ("cancelacion_solicitada_fecha", "Cancelación solicitada", ""),
            ("cargo_cancelacion_eur", "Cargo de cancelación", " €"),
            ("reubicacion_ofrecida", "Reubicación", ""),
            ("reembolso_estado", "Reembolso", ""),
            ("precio_total_reserva_eur", "Precio total", " €"),
            ("importe_pagado_eur", "Importe pagado", " €"),
            ("gastos_adicionales_eur", "Gastos documentados", " €"),
            ("importe_reclamado_eur", "Importe reclamado", " €"),
            ("numero_huespedes", "Huéspedes", ""),
            ("reclamacion_previa_fecha", "Reclamación previa", ""),
            ("canal_reclamacion", "Canal de reclamación", ""),
            ("solucion_solicitada", "Solución solicitada", ""),
        ),
    )
    if _present(fact):
        summary.insert(0, f"Hecho documentado: {_display(fact)}.")
        if fact_key:
            summary_keys.insert(0, fact_key)
    summary.append(
        (
            f"Clasificación operativa: {incident}; reubicación "
            f"{'documentada' if relocation else 'no documentada'}; "
            f"viaje combinado {package_status}."
        )
    )
    summary.append(
        (
            f"Marco territorial: {regime.scope}; excepción de desistimiento "
            f"por periodo específico: {regime.fixed_date_withdrawal_exception}."
        )
    )

    arguments = []

    booking_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            incident_key,
            accommodation_key,
            provider_key,
            booking_ref_key,
            booking_date_key,
            stay_start_key,
            stay_end_key,
            country_key,
            address_key,
            paid_key,
            guests_key,
        ),
    )
    if booking_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="hotel_booking_identity_and_contract_content",
                title="Identidad de la reserva y contenido contratado",
                body=(
                    "La reclamación debe vincular confirmación, establecimiento, "
                    "huéspedes, fechas, precio y contenido de la estancia. La oferta, "
                    "la publicidad y la confirmación recibida delimitan qué habitación, "
                    "categoría y servicios debían prestarse."
                ),
                source_fact_keys=booking_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    cancellation_sources = validated_source_keys(
        facts_record,
        (
            booking_date_key,
            stay_start_key,
            stay_end_key,
            cancellation_terms_key,
            cancellation_date_key,
            cancellation_charge_key,
            solution_key,
            fact_key,
        ),
    )
    if cancellation_sources:
        if (
            regime.status == "current"
            and regime.fixed_date_withdrawal_exception is True
        ):
            cancellation_body = (
                "La estancia está reservada para un periodo específico. El marco "
                "identificado excluye el desistimiento legal general de catorce días "
                "para este servicio, pero no elimina las condiciones contractuales "
                "de cancelación, su control de transparencia ni los remedios frente "
                "al incumplimiento del proveedor."
            )
        else:
            cancellation_body = (
                "Debe determinarse el régimen temporal y territorial antes de "
                "afirmar si opera la excepción al desistimiento. En todo caso deben "
                "revisarse las condiciones contractuales aceptadas y la información "
                "facilitada antes de contratar."
            )
        arguments.append(
            legal_argument(
                facts_record,
                code="hotel_fixed_date_cancellation_terms",
                title="Fechas específicas y condiciones de cancelación",
                body=cancellation_body,
                source_fact_keys=cancellation_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    performance_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            incident_key,
            reserved_room_key,
            assigned_room_key,
            reserved_category_key,
            assigned_category_key,
            board_key,
            services_key,
            relocation_key,
            refund_key,
            response_key,
            solution_key,
        ),
    )
    if performance_sources:
        performance_body = (
            "La prestación debe compararse con la reserva confirmada. Cancelación "
            "del proveedor, falta de habitación, categoría inferior o defectos "
            "materiales pueden justificar cumplimiento, sustitución adecuada, "
            "reducción o devolución del precio y daños probados según el régimen "
            "aplicable. La alternativa ofrecida no se presume equivalente."
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="hotel_supplier_performance_and_remedies",
                title="Cumplimiento del alojamiento y remedios",
                body=performance_body,
                source_fact_keys=performance_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    offer_sources = validated_source_keys(
        facts_record,
        (
            reserved_room_key,
            assigned_room_key,
            reserved_category_key,
            assigned_category_key,
            board_key,
            services_key,
            cancellation_terms_key,
            paid_key,
            fact_key,
        ),
    )
    if offer_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="hotel_offer_advertising_and_non_negotiated_terms",
                title="Oferta, publicidad y cláusulas no negociadas",
                body=(
                    "Deben conservarse la página de oferta, fotografías, categoría, "
                    "régimen, servicios incluidos, precio final y política de "
                    "cancelación visibles al contratar. Una condición oscura o "
                    "comunicada después no se incorpora automáticamente al contrato."
                ),
                source_fact_keys=offer_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    role_sources = validated_source_keys(
        facts_record,
        (
            accommodation_key,
            provider_key,
            agency_key,
            booking_ref_key,
            paid_key,
            response_key,
            relocation_key,
        ),
    )
    if role_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="hotel_supplier_platform_and_payment_roles",
                title="Hotel, plataforma y circuito de cobro",
                body=(
                    "Debe determinarse quién prestaba el alojamiento, quién actuaba "
                    "como intermediario, quién cobró y quién modificó o canceló la "
                    "reserva. RTM no atribuye toda la responsabilidad a la plataforma "
                    "ni al establecimiento sin revisar su intervención documental."
                ),
                source_fact_keys=role_sources,
                priority="secondary",
                legal_basis=basis,
            )
        )

    damage_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            paid_key,
            cancellation_charge_key,
            expenses_key,
            amount_key,
            complaint_date_key,
            complaint_channel_key,
            response_key,
            solution_key,
        ),
    )
    if damage_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="hotel_proven_loss_without_flat_compensation",
                title="Daño acreditado y ausencia de compensación automática",
                body=(
                    "La petición económica debe reconstruirse por partidas: precio "
                    "no disfrutado, diferencia de categoría, reubicación, transporte, "
                    "gastos necesarios y otros daños acreditados. No se fija una "
                    "compensación plana ni se duplica una devolución ya obtenida."
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
            package_key,
            *(key for argument in arguments for key in argument.source_fact_keys),
        ],
    )

    missing = dedupe_missing(
        [
            *_required_missing(facts_record, incident),
            *_review_missing(
                facts_record,
                regime,
                incident,
                package_status,
                relocation,
            ),
            *fact_review_items(facts_record, prefix="hotel"),
        ]
    )

    destination_text = (
        str(accommodation).strip()
        if _present(accommodation)
        else (
            str(provider).strip()
            if _present(provider)
            else "ALOJAMIENTO PENDIENTE DE VALIDAR"
        )
    )
    subject_parts = ["RECLAMACIÓN ALOJAMIENTO", incident.upper()]
    if _present(booking_ref):
        subject_parts.append(f"reserva {booking_ref}")
    if _present(stay_start) and _present(stay_end):
        subject_parts.append(f"{stay_start} a {stay_end}")

    primary_strategy = (
        "Reconstruir reserva, oferta, fechas, precio y condiciones; separar hotel, "
        "plataforma y pagador; confirmar que no es un viaje combinado; comparar la "
        "prestación real con la contratada; exigir remedio y reembolso trazables; "
        "y reclamar únicamente daños documentados."
    )
    if _present(solution):
        primary_strategy += (
            f" La solución documental solicitada es: {_display(solution)}."
        )

    requested_outcomes = [
        "Confirmación escrita de la causa, responsable y cronología de la incidencia.",
        "Cumplimiento equivalente o reubicación adecuada cuando todavía sea útil.",
        "Reembolso del precio no disfrutado y de las diferencias acreditadas.",
        "Reintegro de gastos razonables, necesarios y documentados.",
        "Respuesta motivada sobre la política de cancelación o la falta de prestación.",
    ]
    if incident == "consumer_cancellation":
        requested_outcomes.insert(
            1,
            (
                "Aplicación de la condición de cancelación realmente incorporada, "
                "con devolución de cualquier exceso no justificado."
            ),
        )

    risks = [
        (
            "Confundir una reserva hotelera independiente con un viaje combinado "
            "puede seleccionar un régimen y un responsable incorrectos."
        ),
        (
            "La excepción al desistimiento de catorce días no convierte cualquier "
            "penalización en válida ni elimina derechos por incumplimiento."
        ),
        (
            "La plataforma puede ser intermediaria, parte contractual, organizadora "
            "o mera pasarela; su papel debe probarse."
        ),
        (
            "La ley aplicable, la normativa turística y los plazos pueden variar "
            "según el país del alojamiento y el modo de contratación."
        ),
        *list(regime.warnings),
    ]

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="hotel",
        specialist="travel.hotel",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Se ha documentado una incidencia de alojamiento ({incident}) en la "
            f"reserva {_display(booking_ref)} de {_display(accommodation)}."
            if _present(booking_ref) and _present(accommodation)
            else "Se ha documentado una posible incidencia de alojamiento."
        ),
        client_goal=(
            "Obtener la prestación adecuada o el reembolso correspondiente y "
            "recuperar los gastos y daños realmente acreditados."
        ),
        primary_strategy=primary_strategy,
        secondary_strategies=[
            "Reclamar paralelamente al hotel y al sujeto que cobró cuando sus roles estén documentados.",
            "Solicitar hoja de reclamaciones u organismo turístico competente del lugar del alojamiento.",
            "Revisar seguro, tarjeta o devolución de pago sin duplicar importes.",
        ],
        requested_outcomes=list(dict.fromkeys(requested_outcomes)),
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record, incident),
        risks=list(dict.fromkeys(risks)),
        destination=destination_text,
        document_type="RECLAMACIÓN EXTRAJUDICIAL POR INCIDENCIA EN ALOJAMIENTO",
        subject=" — ".join(subject_parts),
        legal_arguments=arguments,
        additional_requests=[
            "Confirmación de reserva y justificante de pago.",
            "Condiciones generales y de cancelación recibidas al contratar.",
            "Capturas de la oferta, categoría, habitación, servicios y precio final.",
            "Factura, recibo o desglose de cargos y reembolsos.",
            "Comunicaciones con hotel, agencia, plataforma y medio de pago.",
            "Prueba de cancelación, falta de disponibilidad o reubicación.",
            "Datos del alojamiento alternativo y comparación de categoría y ubicación.",
            "Fotografías, vídeos, partes o informes de los defectos denunciados.",
            "Reclamación durante la estancia y respuesta completa del proveedor.",
            "Facturas de transportes, nueva estancia y demás gastos derivados.",
            "Póliza de viaje, cobertura de tarjeta o expediente de chargeback.",
            "Documento que confirme si la reserva incluía otros servicios de viaje.",
        ],
        created_by_component=(
            "travel.hotel:"
            f"{TRAVEL_HOTEL_SPECIALIST_VERSION}+"
            f"{ACCOMMODATION_CONSUMER_REGIME_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
