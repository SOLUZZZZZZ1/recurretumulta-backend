"""Especialista RTM para ``travel.flight_cancelled``.

Construye una Previa Jurídica conservadora para la cancelación de un vuelo. No
asigna automáticamente una compensación, no presume que el trayecto quede
dentro del Reglamento (CE) n.º 261/2004, no acepta una circunstancia
extraordinaria sin prueba y no calcula la prescripción sin fijar foro y Derecho
nacional aplicable.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException

from rtm_core.air_passenger_regime import (
    AIR_PASSENGER_REGIME_VERSION,
    AirPassengerRegimeDecision,
    resolve_air_passenger_regime,
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


TRAVEL_FLIGHT_CANCELLED_SPECIALIST_VERSION = (
    "rtm_travel_flight_cancelled_specialist_v1_0"
)

_LIMITATION_REFERENCE = (
    "TJUE, asunto C-139/11, Cuadrench Moré: el plazo para ejercitar la acción "
    "se determina por las reglas nacionales de prescripción aplicables."
)
_EU_EEA_DEPARTURE_TOKENS = (
    "alemania",
    "germany",
    "austria",
    "belgica",
    "belgium",
    "bulgaria",
    "chipre",
    "cyprus",
    "croacia",
    "croatia",
    "dinamarca",
    "denmark",
    "eslovaquia",
    "slovakia",
    "eslovenia",
    "slovenia",
    "espana",
    "spain",
    "estonia",
    "finlandia",
    "finland",
    "francia",
    "france",
    "grecia",
    "greece",
    "hungria",
    "hungary",
    "irlanda",
    "ireland",
    "italia",
    "italy",
    "letonia",
    "latvia",
    "lituania",
    "lithuania",
    "luxemburgo",
    "luxembourg",
    "malta",
    "paises bajos",
    "netherlands",
    "polonia",
    "poland",
    "portugal",
    "republica checa",
    "czechia",
    "rumania",
    "romania",
    "suecia",
    "sweden",
    "islandia",
    "iceland",
    "noruega",
    "norway",
    "suiza",
    "switzerland",
)


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


def _compensation_requested(record: ValidatedFactsRecord) -> bool:
    amount, _ = validated_value(record, "compensacion_solicitada_eur")
    solution, _ = validated_value(record, "solucion_solicitada")
    return _present(amount) or "compens" in _fold(solution)


def _explicit_eu_departure(origin: Any) -> bool:
    folded = _fold(origin)
    return any(token in folded for token in _EU_EEA_DEPARTURE_TOKENS)


def _notice_days(record: ValidatedFactsRecord) -> Optional[int]:
    flight_value, _ = validated_value(
        record,
        "fecha_vuelo",
        "fecha_incidencia",
    )
    notice_value, _ = validated_value(record, "aviso_incidencia_fecha")
    flight_date = _parse_date(flight_value)
    notice_date = _parse_date(notice_value)
    if flight_date is None or notice_date is None:
        return None
    return (flight_date - notice_date).days


def _required_missing(record: ValidatedFactsRecord) -> list[MissingItem]:
    groups = (
        (
            "flight_cancellation_fact_missing",
            "Falta validar que el vuelo fue cancelado y cómo se comunicó.",
            ("descripcion_hecho", "incidencia_tipo"),
        ),
        (
            "flight_booking_missing",
            "Falta validar el localizador o número de reserva.",
            ("numero_reserva",),
        ),
        (
            "flight_number_missing",
            "Falta validar el número del vuelo cancelado.",
            ("numero_vuelo",),
        ),
        (
            "flight_date_missing",
            "Falta validar la fecha programada del vuelo.",
            ("fecha_vuelo", "fecha_incidencia"),
        ),
        (
            "flight_carrier_missing",
            "Falta validar la aerolínea o transportista aéreo efectivo.",
            ("aerolinea", "proveedor"),
        ),
        (
            "flight_origin_missing",
            "Falta validar el aeropuerto o país de salida.",
            ("origen",),
        ),
        (
            "flight_destination_missing",
            "Falta validar el destino final de la reserva.",
            ("destino",),
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
    regime: AirPassengerRegimeDecision,
) -> list[MissingItem]:
    result: list[MissingItem] = []

    if regime.status != "current":
        result.append(
            missing_item(
                "flight_regime_transition_review",
                regime.blocking_reason
                or "Debe determinarse y versionarse el régimen temporal aplicable.",
                MissingItemSeverity.BLOCKING,
            )
        )

    origin, _ = validated_value(record, "origen")
    if _present(origin) and not _explicit_eu_departure(origin):
        result.append(
            missing_item(
                "flight_eu_scope_review",
                (
                    "La salida no identifica de forma explícita un Estado UE/EEE "
                    "o Suiza. Debe comprobarse el país de salida, el destino, el "
                    "carácter comunitario del transportista y cualquier prestación "
                    "recibida en un tercer país antes de aplicar el Reglamento 261/2004."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    result.extend(
        [
            missing_item(
                "flight_operating_carrier_review",
                (
                    "Debe distinguirse el transportista aéreo efectivo de la "
                    "agencia, plataforma o aerolínea comercializadora."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "flight_booking_and_passengers_review",
                (
                    "OPS debe comprobar billete, reserva confirmada, pasajeros "
                    "incluidos y que los tramos reclamados pertenecen a la reserva."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "flight_cancellation_evidence_review",
                (
                    "Debe conservarse la comunicación de cancelación y, si se "
                    "discute el hecho, la prueba de que el vuelo programado no se "
                    "operó como estaba previsto."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "flight_extraordinary_circumstances_review",
                (
                    "La causa extraordinaria y las medidas razonables no se "
                    "presumen: deben ser alegadas y acreditadas por el "
                    "transportista con datos concretos del vuelo."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
        ]
    )

    notice, _ = validated_value(record, "aviso_incidencia_fecha")
    if not _present(notice):
        result.append(
            missing_item(
                "flight_cancellation_notice_missing",
                (
                    "Falta la fecha y prueba de recepción del aviso de cancelación; "
                    "es necesaria para valorar la compensación del artículo 7."
                ),
                (
                    MissingItemSeverity.BLOCKING
                    if _compensation_requested(record)
                    else MissingItemSeverity.HUMAN_REVIEW
                ),
            )
        )

    requested_solution, _ = validated_value(record, "solucion_solicitada")
    if not _present(requested_solution):
        result.append(
            missing_item(
                "flight_passenger_choice_missing",
                (
                    "Debe documentarse la opción del pasajero entre reembolso y "
                    "transporte alternativo antes de fijar la petición principal."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    alternative, _ = validated_value(record, "alternativa_ofrecida")
    if _present(alternative):
        result.append(
            missing_item(
                "flight_rerouting_timing_review",
                (
                    "Debe comprobarse la salida y llegada de la alternativa, su "
                    "aceptación y si llegó al destino final dentro de los umbrales "
                    "relevantes."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    compensation_amount, _ = validated_value(
        record,
        "compensacion_solicitada_eur",
    )
    if _present(compensation_amount):
        result.append(
            missing_item(
                "flight_compensation_amount_review",
                (
                    "La cuantía solicitada no puede aceptarse sin verificar "
                    "distancia del trayecto, número de pasajeros, aviso y posible "
                    "reducción por transporte alternativo."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    expenses, _ = validated_value(record, "gastos_adicionales_eur")
    if _present(expenses):
        result.append(
            missing_item(
                "flight_expense_receipts_review",
                (
                    "Deben aportarse y vincularse los justificantes de los gastos "
                    "razonables reclamados, evitando duplicidades o partidas no "
                    "relacionadas con la cancelación."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    notice_days = _notice_days(record)
    if notice_days is not None:
        if notice_days >= 14 and _compensation_requested(record):
            result.append(
                missing_item(
                    "flight_notice_window_exclusion_review",
                    (
                        "El aviso documental aparece al menos catorce días antes "
                        "del vuelo. Debe comprobarse recepción y contenido antes "
                        "de mantener una petición de compensación."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
        elif notice_days < 0:
            result.append(
                missing_item(
                    "flight_notice_date_conflict",
                    (
                        "La fecha de aviso aparece posterior al vuelo programado; "
                        "debe revisarse la lectura documental."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )

    return result


def _deadlines(record: ValidatedFactsRecord) -> list[Deadline]:
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
                        (
                            "Es una fecha transcrita del documento; OPS debe "
                            "confirmar qué actuación vence en ella."
                        )
                    ],
                )
            ]

    flight_value, flight_key = validated_value(
        record,
        "fecha_vuelo",
        "fecha_incidencia",
    )
    notes = [
        (
            "El Reglamento europeo no fija por sí solo una fecha uniforme de "
            "prescripción para todas las acciones; deben determinarse el foro y "
            "las reglas nacionales aplicables."
        ),
        _LIMITATION_REFERENCE,
    ]
    if _present(flight_value):
        notes.append(f"Fecha de vuelo validada: {flight_value}.")
    return [
        Deadline(
            label="Plazo de reclamación y prescripción",
            due_at=None,
            calculation_status="unresolved",
            source_fact_keys=[flight_key] if flight_key else [],
            notes=notes,
        )
    ]


def build_travel_flight_cancelled_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="travel",
        family="vuelo_cancelado",
        specialist="travel.flight_cancelled",
    )

    fact, fact_key = validated_value(facts_record, "descripcion_hecho")
    incident_type, incident_key = validated_value(
        facts_record,
        "incidencia_tipo",
    )
    booking, booking_key = validated_value(
        facts_record,
        "numero_reserva",
    )
    flight_number, flight_number_key = validated_value(
        facts_record,
        "numero_vuelo",
    )
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
    scheduled_departure, scheduled_departure_key = validated_value(
        facts_record,
        "hora_salida_programada",
    )
    scheduled_arrival, scheduled_arrival_key = validated_value(
        facts_record,
        "hora_llegada_programada",
    )
    notice, notice_key = validated_value(
        facts_record,
        "aviso_incidencia_fecha",
    )
    alternative, alternative_key = validated_value(
        facts_record,
        "alternativa_ofrecida",
    )
    refund_status, refund_key = validated_value(
        facts_record,
        "reembolso_estado",
    )
    expenses, expenses_key = validated_value(
        facts_record,
        "gastos_adicionales_eur",
    )
    compensation, compensation_key = validated_value(
        facts_record,
        "compensacion_solicitada_eur",
    )
    passengers, passengers_key = validated_value(
        facts_record,
        "numero_pasajeros",
    )
    requested_solution, solution_key = validated_value(
        facts_record,
        "solucion_solicitada",
    )
    response, response_key = validated_value(
        facts_record,
        "respuesta_documentada",
    )

    regime = resolve_air_passenger_regime(flight_date)

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
            ("hora_salida_programada", "Salida programada", ""),
            ("hora_llegada_programada", "Llegada programada", ""),
            ("aviso_incidencia_fecha", "Aviso de cancelación", ""),
            ("alternativa_ofrecida", "Alternativa ofrecida", ""),
            ("reembolso_estado", "Estado del reembolso", ""),
            ("gastos_adicionales_eur", "Gastos documentados", " €"),
            ("compensacion_solicitada_eur", "Compensación solicitada", " €"),
            ("numero_pasajeros", "Pasajeros", ""),
            ("solucion_solicitada", "Solución solicitada", ""),
        ),
    )
    if _present(fact):
        summary.insert(0, f"Hecho documentado: {_display(fact)}.")
        if fact_key:
            summary_keys.insert(0, fact_key)

    basis = list(regime.legal_basis)
    arguments = []

    scope_sources = validated_source_keys(
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
        ),
    )
    if scope_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="flight_scope_booking_and_cancellation",
                title="Ámbito, reserva y cancelación documentada",
                body=(
                    "La reclamación debe vincular la reserva confirmada con el "
                    "vuelo, la fecha, el trayecto, el transportista efectivo y la "
                    "cancelación comunicada. El ámbito europeo no se presume: "
                    "debe quedar acreditado por el punto de salida o, cuando "
                    "proceda, por el destino y la condición del transportista."
                ),
                source_fact_keys=scope_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    choice_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            booking_key,
            alternative_key,
            refund_key,
            solution_key,
            response_key,
        ),
    )
    if choice_sources:
        alternative_text = (
            _display(alternative)
            if _present(alternative)
            else "no consta una alternativa validada"
        )
        refund_text = (
            _display(refund_status)
            if _present(refund_status)
            else "pendiente de validar"
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="flight_reimbursement_or_rerouting_choice",
                title="Elección entre reembolso y transporte alternativo",
                body=(
                    "Ante una cancelación dentro del régimen aplicable, el "
                    "pasajero debe poder elegir entre las opciones de reembolso "
                    "o transporte alternativo previstas legalmente, sin que RTM "
                    "imponga una opción distinta a la documentada. En este "
                    f"expediente, la alternativa figura como: {alternative_text}; "
                    f"el estado del reembolso es: {refund_text}."
                ),
                source_fact_keys=choice_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    care_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            flight_date_key,
            airline_key,
            expenses_key,
            response_key,
        ),
    )
    if care_sources:
        expense_text = (
            f"{expenses} €"
            if _present(expenses)
            else "sin cuantía validada"
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="flight_care_and_documented_expenses",
                title="Asistencia y gastos razonables",
                body=(
                    "Las obligaciones de asistencia deben examinarse separadamente "
                    "de la compensación. Los gastos razonables de manutención, "
                    "alojamiento, transporte o comunicaciones solo se incorporan "
                    "si están vinculados a la cancelación y respaldados por "
                    f"justificantes. El expediente refleja: {expense_text}."
                ),
                source_fact_keys=care_sources,
                priority="secondary",
                legal_basis=basis,
            )
        )

    compensation_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            flight_date_key,
            notice_key,
            alternative_key,
            scheduled_departure_key,
            scheduled_arrival_key,
            compensation_key,
            passengers_key,
        ),
    )
    if compensation_sources:
        days = _notice_days(facts_record)
        notice_text = (
            f"{days} días antes del vuelo"
            if days is not None
            else "sin intervalo validado"
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="flight_compensation_notice_and_rerouting",
                title="Compensación, aviso y alternativa ofrecida",
                body=(
                    "La posible compensación exige comprobar el momento y la "
                    "recepción del aviso, los horarios de cualquier transporte "
                    "alternativo, la distancia y el número de pasajeros. El aviso "
                    f"documental aparece {notice_text}. RTM no fija una cuantía ni "
                    "descarta el derecho sin completar esas comprobaciones."
                ),
                source_fact_keys=compensation_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    extraordinary_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            incident_key,
            airline_key,
            response_key,
            notice_key,
        ),
    )
    if extraordinary_sources:
        response_text = (
            _display(response)
            if _present(response)
            else "no consta una explicación documental validada"
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="flight_extraordinary_circumstances_and_measures",
                title="Circunstancias extraordinarias y medidas razonables",
                body=(
                    "La exoneración de la compensación no se acepta mediante una "
                    "mención genérica. El transportista debe acreditar la causa "
                    "concreta, su relación con el vuelo y que la cancelación no "
                    "habría podido evitarse incluso adoptando medidas razonables. "
                    f"Respuesta documentada: {response_text}."
                ),
                source_fact_keys=extraordinary_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    information_sources = validated_source_keys(
        facts_record,
        (
            booking_key,
            flight_number_key,
            notice_key,
            airline_key,
            agency_key,
            response_key,
        ),
    )
    if information_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="flight_information_and_preservation_of_evidence",
                title="Información al pasajero y conservación de la prueba",
                body=(
                    "Deben conservarse la reserva, las comunicaciones, la hora de "
                    "recepción, la alternativa, las respuestas del transportista "
                    "y los justificantes. La información sobre derechos y los "
                    "canales de reclamación debe atribuirse al sujeto responsable "
                    "sin confundirlo con la plataforma intermediaria."
                ),
                source_fact_keys=information_sources,
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
            *_review_missing(facts_record, regime),
            *fact_review_items(facts_record, prefix="flight_cancelled"),
        ]
    )

    destination_text = (
        str(airline).strip()
        if _present(airline)
        else "TRANSPORTISTA AÉREO PENDIENTE DE VALIDAR"
    )
    subject_parts = ["CANCELACIÓN DE VUELO"]
    if _present(booking):
        subject_parts.append(f"reserva {booking}")
    if _present(flight_number):
        subject_parts.append(f"vuelo {flight_number}")
    if _present(flight_date):
        subject_parts.append(f"fecha {flight_date}")

    risks = [
        (
            "No se ha calculado automáticamente la compensación: depende del "
            "régimen temporal, ámbito, aviso, alternativa, distancia y pasajeros."
        ),
        (
            "La agencia o plataforma no debe confundirse con el transportista "
            "aéreo efectivo al dirigir la reclamación."
        ),
        (
            "Una alegación genérica de circunstancias extraordinarias no prueba "
            "por sí sola la exoneración ni las medidas razonables."
        ),
        (
            "El plazo de prescripción no es uniforme en toda la Unión y exige "
            "determinar el Derecho nacional y el foro aplicables."
        ),
        *list(regime.warnings),
    ]
    if _present(refund_status):
        risks.append(
            (
                "Un reembolso realizado puede afectar a una parte de la petición, "
                "pero no resuelve automáticamente asistencia, gastos o compensación."
            )
        )

    primary_strategy = (
        "Vincular reserva, vuelo, fecha, trayecto y transportista efectivo; "
        "verificar el aviso de cancelación y la opción elegida por el pasajero; "
        "separar reembolso o transporte alternativo, asistencia, gastos y "
        "compensación; y exigir prueba concreta de cualquier exoneración."
    )
    if _present(requested_solution):
        primary_strategy += (
            f" La solución documental solicitada es: {_display(requested_solution)}."
        )

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="vuelo_cancelado",
        specialist="travel.flight_cancelled",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Se ha documentado la cancelación del vuelo {_display(flight_number)} "
            f"de la reserva {_display(booking)}."
            if _present(flight_number) and _present(booking)
            else "Se ha documentado una posible cancelación de vuelo."
        ),
        client_goal=(
            "Obtener la opción de reembolso o transporte alternativo elegida, "
            "el reintegro de gastos acreditados y, cuando proceda, la "
            "compensación legal correspondiente."
        ),
        primary_strategy=primary_strategy,
        secondary_strategies=[
            (
                "Reclamar primero al transportista efectivo, conservando la "
                "intervención de la agencia o plataforma cuando sea relevante."
            ),
            (
                "Separar la petición de compensación de los derechos de "
                "reembolso, transporte alternativo y asistencia."
            ),
            (
                "Escalar al organismo nacional competente o a la vía adecuada "
                "solo después de comprobar ruta, fecha, transportista y respuesta."
            ),
        ],
        requested_outcomes=[
            "Reembolso o transporte alternativo conforme a la opción documentada.",
            "Reintegro de gastos razonables, necesarios y acreditados.",
            (
                "Compensación legal cuando el ámbito, el aviso, los horarios y "
                "las circunstancias permitan reconocerla."
            ),
            "Respuesta motivada y entrega de la información y prueba utilizadas.",
        ],
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record),
        risks=list(dict.fromkeys(risks)),
        destination=destination_text,
        document_type="RECLAMACIÓN EXTRAJUDICIAL POR CANCELACIÓN DE VUELO",
        subject=" — ".join(subject_parts),
        legal_arguments=arguments,
        additional_requests=[
            "Reserva completa, billetes y relación de pasajeros reclamantes.",
            "Comunicación de cancelación con fecha, hora, canal y recepción.",
            "Identidad del transportista aéreo efectivo y del comercializador.",
            "Oferta de transporte alternativo y horarios de salida y llegada.",
            "Estado y justificante del reembolso, si existe.",
            "Facturas y recibos de manutención, alojamiento, transporte y comunicaciones.",
            "Explicación y prueba concreta de las circunstancias extraordinarias alegadas.",
            "Respuesta completa de la aerolínea a la reclamación previa.",
        ],
        created_by_component=(
            "travel.flight_cancelled:"
            f"{TRAVEL_FLIGHT_CANCELLED_SPECIALIST_VERSION}+"
            f"{AIR_PASSENGER_REGIME_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
