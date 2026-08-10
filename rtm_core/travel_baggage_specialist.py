"""Especialista RTM para ``travel.baggage``.

Construye una Previa Jurídica conservadora para retraso, pérdida o daños de
equipaje. Distingue equipaje facturado y no facturado, separa el PIR de la
reclamación escrita, preserva los plazos breves del Convenio de Montreal y no
convierte el límite de DEG en una indemnización automática ni en euros.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any, Literal, Optional

from fastapi import HTTPException

from rtm_core.air_baggage_liability_regime import (
    AIR_BAGGAGE_LIABILITY_REGIME_VERSION,
    AirBaggageLiabilityDecision,
    resolve_air_baggage_liability_regime,
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


TRAVEL_BAGGAGE_SPECIALIST_VERSION = "rtm_travel_baggage_specialist_v1_0"

BaggageIncident = Literal["delay", "damage", "loss", "mixed", "unknown"]
BaggageType = Literal["checked", "unchecked", "unknown"]


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


def _baggage_text(record: ValidatedFactsRecord) -> str:
    values: list[Any] = []
    for key in (
        "descripcion_hecho",
        "incidencia_tipo",
        "equipaje_tipo",
        "equipaje_danos",
        "equipaje_contenido",
        "respuesta_documentada",
        "solucion_solicitada",
    ):
        value, _ = validated_value(record, key)
        if _present(value):
            values.append(value)
    return _fold(values)


def _incident(record: ValidatedFactsRecord) -> BaggageIncident:
    text = _baggage_text(record)
    damage_value, _ = validated_value(record, "equipaje_danos")

    damage = _present(damage_value) or any(
        token in text
        for token in (
            "equipaje danado",
            "equipaje deteriorado",
            "maleta danada",
            "maleta rota",
            "rotura de maleta",
            "contenido roto",
            "objetos rotos",
            "averia del equipaje",
        )
    )
    delay = any(
        token in text
        for token in (
            "equipaje retrasado",
            "equipaje demorado",
            "maleta retrasada",
            "maleta demorada",
            "entregado con retraso",
            "entregada con retraso",
            "entregado dias despues",
            "entregada dias despues",
            "llego dias despues",
            "llego con retraso",
        )
    )
    loss = any(
        token in text
        for token in (
            "equipaje perdido",
            "equipaje extraviado",
            "maleta perdida",
            "maleta extraviada",
            "equipaje desaparecido",
            "no localizado",
            "perdida definitiva",
            "declara la perdida",
            "reconoce la perdida",
        )
    )

    active = [name for name, enabled in (("delay", delay), ("damage", damage), ("loss", loss)) if enabled]
    if len(active) > 1:
        return "mixed"
    if not active:
        return "unknown"
    return active[0]  # type: ignore[return-value]


def _baggage_type(record: ValidatedFactsRecord) -> BaggageType:
    explicit, _ = validated_value(record, "equipaje_tipo")
    text = _fold([explicit, _baggage_text(record)])
    checked = any(
        token in text
        for token in (
            "equipaje facturado",
            "maleta facturada",
            "equipaje registrado",
            "checked baggage",
            "bodega",
        )
    )
    unchecked = any(
        token in text
        for token in (
            "equipaje de mano",
            "maleta de cabina",
            "equipaje no facturado",
            "cabina",
            "objeto personal",
            "unchecked baggage",
        )
    )
    if checked and not unchecked:
        return "checked"
    if unchecked and not checked:
        return "unchecked"
    return "unknown"


def _carrier_fault_marker(text: str) -> bool:
    return any(
        token in text
        for token in (
            "empleado de la aerolinea",
            "personal de la aerolinea",
            "tripulacion",
            "la aerolinea golpeo",
            "la aerolinea rompio",
            "el transportista causo",
            "fue retirado por la aerolinea",
        )
    )


def _loss_status_established(text: str) -> bool:
    return any(
        token in text
        for token in (
            "reconoce la perdida",
            "declara la perdida",
            "perdida definitiva",
            "transcurridos 21 dias",
            "han transcurrido 21 dias",
            "mas de 21 dias",
            "más de 21 dias",
        )
    )


def _claim_date(record: ValidatedFactsRecord) -> tuple[Any, Optional[str]]:
    return validated_value(record, "reclamacion_previa_fecha")


def _date_gap(start: Any, end: Any) -> Optional[int]:
    start_date = _parse_date(start)
    end_date = _parse_date(end)
    if start_date is None or end_date is None:
        return None
    return (end_date - start_date).days


def _required_missing(record: ValidatedFactsRecord) -> list[MissingItem]:
    groups = (
        (
            "baggage_fact_missing",
            "Falta validar el hecho concreto relativo al equipaje.",
            ("descripcion_hecho", "incidencia_tipo"),
        ),
        (
            "baggage_booking_missing",
            "Falta validar la reserva o localizador del transporte.",
            ("numero_reserva",),
        ),
        (
            "baggage_flight_number_missing",
            "Falta validar el número del vuelo.",
            ("numero_vuelo",),
        ),
        (
            "baggage_flight_date_missing",
            "Falta validar la fecha del vuelo.",
            ("fecha_vuelo", "fecha_incidencia"),
        ),
        (
            "baggage_carrier_missing",
            "Falta validar la aerolínea o transportista efectivo.",
            ("aerolinea", "proveedor"),
        ),
        (
            "baggage_requested_solution_missing",
            "Falta validar la solución o indemnización solicitada.",
            ("solucion_solicitada",),
        ),
    )
    result: list[MissingItem] = []
    for code, description, keys in groups:
        value, _ = validated_value(record, *keys)
        if not _present(value):
            result.append(missing_item(code, description))
    return result


def _review_missing(
    record: ValidatedFactsRecord,
    regime: AirBaggageLiabilityDecision,
    incident: BaggageIncident,
    baggage_type: BaggageType,
) -> list[MissingItem]:
    result: list[MissingItem] = []
    text = _baggage_text(record)

    if regime.status != "current":
        result.append(
            missing_item(
                "baggage_regime_review",
                regime.blocking_reason
                or "Debe determinarse y versionarse el régimen temporal aplicable.",
                MissingItemSeverity.BLOCKING,
            )
        )

    if incident == "unknown":
        result.append(
            missing_item(
                "baggage_incident_type_missing",
                "Debe determinarse si existe retraso, pérdida o daño del equipaje.",
                MissingItemSeverity.BLOCKING,
            )
        )
    elif incident == "mixed":
        result.append(
            missing_item(
                "baggage_multiple_incidents_split_required",
                (
                    "Los hechos contienen más de una incidencia de equipaje. Debe "
                    "separarse cada daño, retraso o pérdida y su cronología."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    if baggage_type == "unknown":
        result.append(
            missing_item(
                "baggage_checked_or_unchecked_missing",
                (
                    "Debe validarse si el equipaje fue facturado o permaneció bajo "
                    "custodia del pasajero."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    elif baggage_type == "unchecked" and not _carrier_fault_marker(text):
        result.append(
            missing_item(
                "baggage_unchecked_carrier_fault_review",
                (
                    "En equipaje no facturado debe acreditarse la culpa del "
                    "transportista, sus empleados o agentes."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    result.extend(
        [
            missing_item(
                "baggage_actual_and_contracting_carrier_review",
                (
                    "Debe distinguirse el transportista contractual del efectivo y "
                    "comprobar a cuál se dirigió la reclamación."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "baggage_tag_and_custody_review",
                (
                    "Debe incorporarse la etiqueta de equipaje, número de bulto y "
                    "prueba de entrega al transportista."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
        ]
    )

    pir, _ = validated_value(record, "equipaje_pir")
    if not _present(pir):
        result.append(
            missing_item(
                "baggage_pir_missing",
                (
                    "Falta el parte de irregularidad de equipaje o una explicación "
                    "documentada de por qué no pudo obtenerse."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    else:
        result.append(
            missing_item(
                "baggage_pir_is_not_formal_claim_review",
                (
                    "Debe comprobarse que, además del PIR, se envió una reclamación "
                    "escrita dentro del plazo aplicable."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    delivery, _ = validated_value(record, "equipaje_entrega_fecha")
    claim, _ = _claim_date(record)
    flight_date, _ = validated_value(record, "fecha_vuelo", "fecha_incidencia")

    delivery_gap = _date_gap(flight_date, delivery)
    if delivery_gap is not None and delivery_gap < 0:
        result.append(
            missing_item(
                "baggage_delivery_before_flight_conflict",
                "La entrega del equipaje aparece anterior al vuelo.",
                MissingItemSeverity.BLOCKING,
            )
        )
    claim_gap_from_flight = _date_gap(flight_date, claim)
    if claim_gap_from_flight is not None and claim_gap_from_flight < 0:
        result.append(
            missing_item(
                "baggage_claim_before_incident_conflict",
                "La reclamación aparece anterior al vuelo o a la incidencia.",
                MissingItemSeverity.BLOCKING,
            )
        )

    if incident == "damage":
        damages, _ = validated_value(record, "equipaje_danos")
        if not _present(damages):
            result.append(
                missing_item(
                    "baggage_damage_description_missing",
                    "Falta describir y documentar los daños concretos.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        if not _present(delivery):
            result.append(
                missing_item(
                    "baggage_damage_receipt_date_missing",
                    (
                        "Falta la fecha en que el equipaje dañado fue puesto a "
                        "disposición del pasajero."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
        if not _present(claim):
            result.append(
                missing_item(
                    "baggage_damage_written_claim_missing",
                    (
                        "Falta acreditar la reclamación escrita por daños, necesaria "
                        "para revisar el plazo máximo de siete días."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
        gap = _date_gap(delivery, claim)
        if gap is not None:
            if gap > 7:
                result.append(
                    missing_item(
                        "baggage_damage_notice_late_review",
                        (
                            "La reclamación escrita aparece más de siete días "
                            "después de la recepción del equipaje."
                        ),
                        MissingItemSeverity.BLOCKING,
                    )
                )
            else:
                result.append(
                    missing_item(
                        "baggage_damage_notice_timing_review",
                        (
                            "La reclamación aparece dentro de siete días; OPS debe "
                            "confirmar recepción, contenido y cómputo."
                        ),
                        MissingItemSeverity.HUMAN_REVIEW,
                    )
                )

    if incident == "delay":
        if _present(delivery) and not _present(claim):
            result.append(
                missing_item(
                    "baggage_delay_written_claim_missing",
                    (
                        "El equipaje ya fue entregado, pero falta acreditar la "
                        "reclamación escrita dentro de los veintiún días siguientes."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
        if not _present(delivery):
            result.append(
                missing_item(
                    "baggage_delay_delivery_pending_review",
                    (
                        "El equipaje todavía no consta entregado; debe actualizarse "
                        "su localización y comprobar si alcanza el régimen de pérdida."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
        gap = _date_gap(delivery, claim)
        if gap is not None:
            if gap > 21:
                result.append(
                    missing_item(
                        "baggage_delay_notice_late_review",
                        (
                            "La reclamación escrita aparece más de veintiún días "
                            "después de la entrega del equipaje retrasado."
                        ),
                        MissingItemSeverity.BLOCKING,
                    )
                )
            else:
                result.append(
                    missing_item(
                        "baggage_delay_notice_timing_review",
                        (
                            "La reclamación aparece dentro de veintiún días; OPS "
                            "debe confirmar recepción, contenido y cómputo."
                        ),
                        MissingItemSeverity.HUMAN_REVIEW,
                    )
                )

    if incident == "loss":
        if not _loss_status_established(text):
            result.append(
                missing_item(
                    "baggage_loss_status_review",
                    (
                        "Debe acreditarse que el transportista reconoció la pérdida "
                        "o que transcurrieron veintiún días desde la fecha en que el "
                        "equipaje debía llegar."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
        if not _present(claim):
            result.append(
                missing_item(
                    "baggage_loss_written_claim_review",
                    (
                        "Debe conservarse una reclamación escrita pronta y completa, "
                        "aunque el artículo 31 trate específicamente daño y retraso."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )

    contents, _ = validated_value(record, "equipaje_contenido")
    if incident in {"damage", "loss"} and not _present(contents):
        result.append(
            missing_item(
                "baggage_contents_and_value_review",
                (
                    "Debe aportarse inventario del contenido y prueba de existencia, "
                    "antigüedad y valor, evitando cifras globales no justificadas."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    amount, _ = validated_value(record, "importe_reclamado_eur")
    passengers, _ = validated_value(record, "numero_pasajeros")
    if _present(amount):
        result.append(
            missing_item(
                "baggage_claim_amount_review",
                (
                    "La cuantía reclamada debe reconstruirse por partidas y no "
                    "puede equipararse automáticamente al límite en DEG."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
        if not _present(passengers):
            result.append(
                missing_item(
                    "baggage_passenger_count_missing",
                    (
                        "Falta validar el número de pasajeros titulares antes de "
                        "aplicar un límite por pasajero."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )

    expenses, _ = validated_value(record, "gastos_adicionales_eur")
    if _present(expenses):
        result.append(
            missing_item(
                "baggage_expense_receipts_review",
                (
                    "Deben aportarse recibos de gastos razonables y necesarios, "
                    "vinculados al retraso y sin duplicidades."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    result.append(
        missing_item(
            "baggage_special_declaration_and_insurance_review",
            (
                "Debe comprobarse si existió declaración especial de interés, "
                "seguro de viaje o cobertura de tarjeta antes de fijar límites y "
                "evitar dobles recuperaciones."
            ),
            MissingItemSeverity.HUMAN_REVIEW,
        )
    )
    return result


def _deadlines(
    record: ValidatedFactsRecord,
    incident: BaggageIncident,
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

    delivery, delivery_key = validated_value(record, "equipaje_entrega_fecha")
    flight_date, flight_key = validated_value(
        record,
        "fecha_vuelo",
        "fecha_incidencia",
    )
    deadlines: list[Deadline] = []

    if incident == "damage":
        deadlines.append(
            Deadline(
                label="Reclamación escrita por daños",
                due_at=None,
                calculation_status="unresolved",
                source_fact_keys=[delivery_key] if delivery_key else [],
                notes=[
                    (
                        "Debe formularse inmediatamente tras descubrir el daño y, "
                        "como máximo, dentro de siete días desde la recepción del "
                        "equipaje facturado."
                    ),
                    f"Fecha de entrega validada: {delivery}." if _present(delivery) else "Fecha de entrega pendiente.",
                ],
            )
        )
    elif incident == "delay":
        deadlines.append(
            Deadline(
                label="Reclamación escrita por retraso",
                due_at=None,
                calculation_status="unresolved",
                source_fact_keys=[delivery_key] if delivery_key else [],
                notes=[
                    (
                        "Debe formularse, como máximo, dentro de veintiún días desde "
                        "que el equipaje fue puesto a disposición del pasajero."
                    ),
                    f"Fecha de entrega validada: {delivery}." if _present(delivery) else "Entrega todavía pendiente.",
                ],
            )
        )
    elif incident == "loss":
        deadlines.append(
            Deadline(
                label="Conversión del retraso en pérdida",
                due_at=None,
                calculation_status="unresolved",
                source_fact_keys=[flight_key] if flight_key else [],
                notes=[
                    (
                        "El pasajero puede ejercer los derechos derivados de la "
                        "pérdida si el transportista la admite o si el equipaje no "
                        "llega dentro de veintiún días desde cuando debía llegar."
                    )
                ],
            )
        )

    deadlines.append(
        Deadline(
            label="Extinción de la acción judicial",
            due_at=None,
            calculation_status="unresolved",
            source_fact_keys=[flight_key] if flight_key else [],
            notes=[
                (
                    "La acción se extingue, con carácter general, a los dos años "
                    "desde la llegada, la fecha en que la aeronave debía llegar o "
                    "la interrupción del transporte; el método de cómputo depende "
                    "del Derecho del tribunal competente."
                )
            ],
        )
    )
    return deadlines


def build_travel_baggage_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="travel",
        family="equipaje",
        specialist="travel.baggage",
    )

    fact, fact_key = validated_value(facts_record, "descripcion_hecho")
    incident_value, incident_key = validated_value(facts_record, "incidencia_tipo")
    booking, booking_key = validated_value(facts_record, "numero_reserva")
    flight_number, flight_number_key = validated_value(facts_record, "numero_vuelo")
    flight_date, flight_date_key = validated_value(
        facts_record,
        "fecha_vuelo",
        "fecha_incidencia",
    )
    airline, airline_key = validated_value(
        facts_record,
        "aerolinea",
        "proveedor",
    )
    agency, agency_key = validated_value(facts_record, "agencia")
    origin, origin_key = validated_value(facts_record, "origen")
    destination, destination_key = validated_value(facts_record, "destino")
    baggage_type_value, baggage_type_key = validated_value(facts_record, "equipaje_tipo")
    pir, pir_key = validated_value(facts_record, "equipaje_pir")
    delivery, delivery_key = validated_value(facts_record, "equipaje_entrega_fecha")
    damages, damages_key = validated_value(facts_record, "equipaje_danos")
    contents, contents_key = validated_value(facts_record, "equipaje_contenido")
    claim_date, claim_date_key = _claim_date(facts_record)
    claim_channel, claim_channel_key = validated_value(facts_record, "canal_reclamacion")
    amount, amount_key = validated_value(facts_record, "importe_reclamado_eur")
    expenses, expenses_key = validated_value(facts_record, "gastos_adicionales_eur")
    passengers, passengers_key = validated_value(facts_record, "numero_pasajeros")
    response, response_key = validated_value(facts_record, "respuesta_documentada")
    solution, solution_key = validated_value(facts_record, "solucion_solicitada")

    incident = _incident(facts_record)
    baggage_type = _baggage_type(facts_record)
    regime = resolve_air_baggage_liability_regime(flight_date)
    basis = list(regime.legal_basis)

    summary, summary_keys = summary_rows(
        facts_record,
        (
            ("aerolinea", "Aerolínea", ""),
            ("agencia", "Agencia o plataforma", ""),
            ("numero_reserva", "Reserva", ""),
            ("numero_vuelo", "Vuelo", ""),
            ("fecha_vuelo", "Fecha del vuelo", ""),
            ("origen", "Origen", ""),
            ("destino", "Destino", ""),
            ("equipaje_tipo", "Tipo de equipaje", ""),
            ("equipaje_pir", "Parte de irregularidad", ""),
            ("equipaje_entrega_fecha", "Fecha de entrega", ""),
            ("equipaje_danos", "Daños", ""),
            ("equipaje_contenido", "Contenido declarado", ""),
            ("reclamacion_previa_fecha", "Reclamación escrita", ""),
            ("canal_reclamacion", "Canal de reclamación", ""),
            ("importe_reclamado_eur", "Importe reclamado", " €"),
            ("gastos_adicionales_eur", "Gastos documentados", " €"),
            ("numero_pasajeros", "Pasajeros", ""),
            ("solucion_solicitada", "Solución solicitada", ""),
        ),
    )
    if _present(fact):
        summary.insert(0, f"Hecho documentado: {_display(fact)}.")
        if fact_key:
            summary_keys.insert(0, fact_key)
    summary.append(
        f"Clasificación operativa: incidencia {incident}; equipaje {baggage_type}."
    )
    if regime.liability_limit_sdr is not None:
        summary.append(
            f"Límite temporal identificado: {regime.liability_limit_sdr} DEG por pasajero, no indemnización automática."
        )

    arguments = []

    contract_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            incident_key,
            booking_key,
            flight_number_key,
            flight_date_key,
            airline_key,
            origin_key,
            destination_key,
            baggage_type_key,
            pir_key,
        ),
    )
    if contract_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="baggage_contract_identity_and_custody",
                title="Contrato, identificación del equipaje y custodia",
                body=(
                    "La reclamación debe vincular reserva, vuelo, pasajero, etiqueta "
                    "y bulto. En equipaje facturado se revisa si el hecho ocurrió "
                    "mientras estaba bajo custodia del transportista; en equipaje de "
                    "mano debe acreditarse la culpa del transportista o de sus agentes."
                ),
                source_fact_keys=contract_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    incident_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            incident_key,
            baggage_type_key,
            delivery_key,
            damages_key,
            contents_key,
            response_key,
        ),
    )
    if incident_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="baggage_delay_damage_or_loss_classification",
                title="Calificación del retraso, daño o pérdida",
                body=(
                    "Retraso, daño y pérdida son supuestos distintos. La pérdida "
                    "requiere reconocimiento del transportista o el transcurso de "
                    "veintiún días desde cuando el equipaje debía llegar; el daño "
                    "exige descripción y prueba; y el retraso se valora hasta la "
                    "entrega efectiva. La clasificación actual es "
                    f"{incident} y no sustituye la revisión documental."
                ),
                source_fact_keys=incident_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    notice_sources = validated_source_keys(
        facts_record,
        (
            pir_key,
            delivery_key,
            claim_date_key,
            claim_channel_key,
            incident_key,
            fact_key,
        ),
    )
    if notice_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="baggage_pir_and_written_notice",
                title="PIR y reclamación escrita dentro de plazo",
                body=(
                    "El PIR permite dejar constancia inicial, pero debe comprobarse "
                    "si además se formuló una reclamación escrita. Para daños del "
                    "equipaje facturado el límite es de siete días desde la recepción; "
                    "para retraso, veintiún días desde la puesta a disposición. RTM "
                    "no presume que el PIR equivalga siempre al escrito exigido."
                ),
                source_fact_keys=notice_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    valuation_sources = validated_source_keys(
        facts_record,
        (
            contents_key,
            damages_key,
            amount_key,
            expenses_key,
            passengers_key,
            fact_key,
        ),
    )
    if valuation_sources:
        limit_text = (
            f"{regime.liability_limit_sdr} DEG por pasajero"
            if regime.liability_limit_sdr is not None
            else "límite pendiente de validación temporal"
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="baggage_proven_damage_and_sdr_limit",
                title="Daño probado y límite en DEG",
                body=(
                    "La indemnización se construye con pérdidas y gastos realmente "
                    "acreditados, aplicando antigüedad, valor y causalidad. El límite "
                    f"identificado es {limit_text}; no es una cantidad automática, "
                    "no se convierte de oficio a euros y puede verse afectado por "
                    "una declaración especial de interés."
                ),
                source_fact_keys=valuation_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    carrier_sources = validated_source_keys(
        facts_record,
        (
            airline_key,
            agency_key,
            booking_key,
            flight_number_key,
            response_key,
            solution_key,
        ),
    )
    if carrier_sources:
        response_text = (
            _display(response) if _present(response) else "sin respuesta validada"
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="baggage_contracting_actual_carrier_and_response",
                title="Transportista contractual, efectivo y respuesta",
                body=(
                    "Debe comprobarse si la compañía contractual coincide con la "
                    "que operó el vuelo y conservar la respuesta íntegra. La agencia "
                    "o plataforma no se confunde con el transportista responsable. "
                    f"Respuesta documentada: {response_text}."
                ),
                source_fact_keys=carrier_sources,
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
            *_required_missing(facts_record),
            *_review_missing(facts_record, regime, incident, baggage_type),
            *fact_review_items(facts_record, prefix="baggage"),
        ]
    )

    destination_text = (
        str(airline).strip()
        if _present(airline)
        else "TRANSPORTISTA AÉREO PENDIENTE DE VALIDAR"
    )
    subject_parts = ["RECLAMACIÓN DE EQUIPAJE", incident.upper()]
    if _present(booking):
        subject_parts.append(f"reserva {booking}")
    if _present(flight_number):
        subject_parts.append(f"vuelo {flight_number}")
    if _present(pir):
        subject_parts.append(f"PIR {pir}")

    primary_strategy = (
        "Vincular reserva, pasajero, vuelo, etiqueta y custodia; separar retraso, "
        "daño y pérdida; comprobar PIR y reclamación escrita; reconstruir contenido, "
        "gastos y valor probado; aplicar el límite temporal en DEG sin tratarlo como "
        "indemnización automática; y dirigir la reclamación al transportista adecuado."
    )
    if _present(solution):
        primary_strategy += f" La solución documental solicitada es: {_display(solution)}."

    risks = [
        (
            "El PIR y la reclamación escrita son actuaciones distintas y sus fechas "
            "pueden afectar a la viabilidad de la reclamación."
        ),
        (
            "El límite en DEG es un máximo por pasajero, no una suma automática ni "
            "una conversión fija a euros."
        ),
        (
            "En equipaje de mano debe acreditarse la culpa del transportista; en "
            "equipaje facturado pueden operar excepciones por vicio propio."
        ),
        (
            "Seguro de viaje, cobertura de tarjeta y declaración especial deben "
            "coordinarse para evitar doble recuperación."
        ),
        *list(regime.warnings),
    ]

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="equipaje",
        specialist="travel.baggage",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Se ha documentado una incidencia de equipaje ({incident}) en el vuelo "
            f"{_display(flight_number)} de la reserva {_display(booking)}."
            if _present(flight_number) and _present(booking)
            else "Se ha documentado una posible incidencia de equipaje."
        ),
        client_goal=(
            "Obtener localización o entrega, reparación o sustitución, reintegro de "
            "gastos y compensación por el daño realmente acreditado."
        ),
        primary_strategy=primary_strategy,
        secondary_strategies=[
            "Reclamar también al transportista contractual o efectivo cuando corresponda.",
            "Activar seguro de viaje o cobertura de tarjeta conservando coordinación documental.",
            "Escalar a la vía competente solo tras revisar respuesta, plazos y cuantía probada.",
        ],
        requested_outcomes=[
            "Localización y entrega inmediata del equipaje cuando siga retrasado.",
            "Reparación, sustitución o compensación del daño material acreditado.",
            "Reintegro de gastos razonables, necesarios y documentados.",
            "Respuesta motivada y entrega de la trazabilidad de búsqueda y custodia.",
        ],
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record, incident),
        risks=list(dict.fromkeys(risks)),
        destination=destination_text,
        document_type="RECLAMACIÓN EXTRAJUDICIAL POR INCIDENCIA DE EQUIPAJE",
        subject=" — ".join(subject_parts),
        legal_arguments=arguments,
        additional_requests=[
            "Reserva, billete, tarjeta de embarque y etiqueta de cada bulto.",
            "Parte de irregularidad de equipaje y comunicaciones de seguimiento.",
            "Fecha y prueba de entrega del equipaje, si fue localizado.",
            "Reclamación escrita al transportista con fecha, contenido y recepción.",
            "Fotografías, informes o presupuestos de reparación de los daños.",
            "Inventario del contenido con facturas, justificantes, antigüedad y valor.",
            "Recibos de compras esenciales y demás gastos derivados del retraso.",
            "Declaración especial de interés, póliza de viaje o cobertura de tarjeta.",
            "Respuesta completa del transportista contractual y del efectivo.",
        ],
        created_by_component=(
            "travel.baggage:"
            f"{TRAVEL_BAGGAGE_SPECIALIST_VERSION}+"
            f"{AIR_BAGGAGE_LIABILITY_REGIME_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
