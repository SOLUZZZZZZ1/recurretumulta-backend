"""Especialista RTM para ``travel.flight_delay``.

Construye una Previa Jurídica conservadora para retrasos de vuelos. Distingue
el retraso de salida del retraso de llegada al destino final, separa asistencia,
reembolso y compensación, y no calcula cuantías ni distancias a partir de datos
no validados. La reforma europea aprobada en 2026 solo se aplicará cuando el
selector temporal la incorpore expresamente.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any, Literal, Optional

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


TRAVEL_FLIGHT_DELAY_SPECIALIST_VERSION = "rtm_travel_flight_delay_specialist_v1_0"

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
    "eslovaquia",
    "slovakia",
    "eslovenia",
    "slovenia",
    "islandia",
    "iceland",
    "noruega",
    "norway",
    "suiza",
    "switzerland",
)

DelayContext = Literal["arrival", "departure", "mixed", "unknown"]


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


def _explicit_eu_departure(origin: Any) -> bool:
    folded = _fold(origin)
    return any(token in folded for token in _EU_EEA_DEPARTURE_TOKENS)


def _delay_text(record: ValidatedFactsRecord) -> tuple[str, list[Optional[str]]]:
    fact, fact_key = validated_value(record, "descripcion_hecho")
    incident, incident_key = validated_value(record, "incidencia_tipo")
    return _fold([fact, incident]), [fact_key, incident_key]


def _delay_context(text: str) -> DelayContext:
    arrival = any(
        token in text
        for token in (
            "llegada",
            "llego",
            "llegó",
            "destino final",
            "apertura de puertas",
        )
    )
    departure = any(
        token in text
        for token in (
            "salida",
            "despegue",
            "despego",
            "despegó",
        )
    )
    if arrival and departure:
        return "mixed"
    if arrival:
        return "arrival"
    if departure:
        return "departure"
    return "unknown"


def _explicit_delay_minutes(text: str) -> tuple[Optional[int], bool]:
    """Extrae una única duración literal sin convertir horarios en autoridad."""

    hour_values: list[int] = []
    for match in re.finditer(
        r"\b(\d{1,2})\s*(?:horas?|h)"
        r"(?:\s*(?:y|,)?\s*(\d{1,2})\s*(?:minutos?|min))?\b",
        text,
    ):
        hours = int(match.group(1))
        minutes = int(match.group(2) or 0)
        if minutes < 60:
            hour_values.append(hours * 60 + minutes)

    values = hour_values
    if not values:
        values = [
            int(match)
            for match in re.findall(r"\b(\d{1,4})\s*(?:minutos?|min)\b", text)
        ]

    unique = sorted(set(value for value in values if 0 <= value <= 2880))
    if len(unique) == 1:
        return unique[0], False
    return None, len(unique) > 1


def _compensation_requested(record: ValidatedFactsRecord) -> bool:
    amount, _ = validated_value(record, "compensacion_solicitada_eur")
    solution, _ = validated_value(record, "solucion_solicitada")
    return _present(amount) or "compens" in _fold(solution)


def _refund_requested(record: ValidatedFactsRecord) -> bool:
    solution, _ = validated_value(record, "solucion_solicitada")
    text = _fold(solution)
    return any(
        token in text
        for token in (
            "reembolso",
            "devolucion del billete",
            "devolucion del precio",
            "no viajar",
        )
    )


def _required_missing(record: ValidatedFactsRecord) -> list[MissingItem]:
    groups = (
        (
            "flight_delay_fact_missing",
            "Falta validar el retraso y el momento del trayecto al que se refiere.",
            ("descripcion_hecho", "incidencia_tipo"),
        ),
        (
            "flight_delay_booking_missing",
            "Falta validar el localizador o número de reserva.",
            ("numero_reserva",),
        ),
        (
            "flight_delay_number_missing",
            "Falta validar el número del vuelo retrasado.",
            ("numero_vuelo",),
        ),
        (
            "flight_delay_date_missing",
            "Falta validar la fecha programada del vuelo.",
            ("fecha_vuelo", "fecha_incidencia"),
        ),
        (
            "flight_delay_carrier_missing",
            "Falta validar la aerolínea o transportista aéreo efectivo.",
            ("aerolinea", "proveedor"),
        ),
        (
            "flight_delay_origin_missing",
            "Falta validar el aeropuerto o país de salida.",
            ("origen",),
        ),
        (
            "flight_delay_destination_missing",
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
    delay_minutes: Optional[int],
    delay_conflict: bool,
    context: DelayContext,
) -> list[MissingItem]:
    result: list[MissingItem] = []

    if regime.status != "current":
        result.append(
            missing_item(
                "flight_delay_regime_transition_review",
                regime.blocking_reason
                or "Debe determinarse y versionarse el régimen temporal aplicable.",
                MissingItemSeverity.BLOCKING,
            )
        )

    origin, _ = validated_value(record, "origen")
    if _present(origin) and not _explicit_eu_departure(origin):
        result.append(
            missing_item(
                "flight_delay_eu_scope_review",
                (
                    "La salida no identifica de forma explícita un Estado UE/EEE "
                    "o Suiza. Debe comprobarse el país de salida, el destino, el "
                    "carácter comunitario del transportista y cualquier protección "
                    "recibida en un tercer país antes de aplicar el Reglamento 261/2004."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    result.extend(
        [
            missing_item(
                "flight_delay_operating_carrier_review",
                (
                    "Debe distinguirse el transportista aéreo efectivo de la "
                    "agencia, plataforma o aerolínea comercializadora."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "flight_delay_final_destination_review",
                (
                    "Debe reconstruirse el itinerario completo y el destino final "
                    "de la reserva, especialmente cuando existen conexiones."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "flight_delay_distance_band_review",
                (
                    "Debe comprobarse la distancia ortodrómica y el tipo de ruta "
                    "antes de seleccionar umbrales de asistencia o una cuantía."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "flight_delay_extraordinary_circumstances_review",
                (
                    "La causa extraordinaria y las medidas razonables no se "
                    "presumen: deben ser alegadas y acreditadas por el "
                    "transportista con datos concretos del vuelo."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
        ]
    )

    if delay_conflict:
        result.append(
            missing_item(
                "flight_delay_duration_conflict",
                (
                    "Los hechos validados contienen más de una duración literal "
                    "del retraso; debe fijarse una única medición y su contexto."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    elif delay_minutes is None:
        result.append(
            missing_item(
                "flight_delay_duration_missing",
                (
                    "Falta una duración documental única del retraso. Los horarios "
                    "deben revisarse con fecha, zona horaria y momento de apertura "
                    "de puertas; RTM no resta horas automáticamente."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    compensation_requested = _compensation_requested(record)
    if compensation_requested and context != "arrival":
        result.append(
            missing_item(
                "flight_delay_arrival_context_missing",
                (
                    "La compensación exige acreditar el retraso de llegada al "
                    "destino final, no solo el retraso de salida."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    if (
        compensation_requested
        and context == "arrival"
        and delay_minutes is not None
        and delay_minutes < 180
    ):
        result.append(
            missing_item(
                "flight_delay_below_compensation_threshold_review",
                (
                    "La duración literal de llegada es inferior a tres horas. "
                    "Debe revisarse la medición y la petición antes de mantener "
                    "una compensación por gran retraso."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    if (
        compensation_requested
        and context == "arrival"
        and delay_minutes is not None
        and delay_minutes >= 180
    ):
        result.append(
            missing_item(
                "flight_delay_compensation_eligibility_review",
                (
                    "La llegada aparece retrasada al menos tres horas. Deben "
                    "comprobarse ámbito, distancia, pasajeros, conexiones y la "
                    "eventual prueba de circunstancias extraordinarias."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    passengers, _ = validated_value(record, "numero_pasajeros")
    if compensation_requested and not _present(passengers):
        result.append(
            missing_item(
                "flight_delay_passenger_count_missing",
                (
                    "Falta validar el número de pasajeros reclamantes antes de "
                    "cuantificar una compensación."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    compensation_amount, _ = validated_value(
        record,
        "compensacion_solicitada_eur",
    )
    if _present(compensation_amount):
        result.append(
            missing_item(
                "flight_delay_compensation_amount_review",
                (
                    "La cuantía solicitada no puede aceptarse sin verificar "
                    "distancia, pasajeros, llegada al destino final y posibles "
                    "circunstancias extraordinarias."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    scheduled_arrival, _ = validated_value(
        record,
        "hora_llegada_programada",
    )
    actual_arrival, _ = validated_value(record, "hora_llegada_real")
    if _present(scheduled_arrival) or _present(actual_arrival):
        if not (_present(scheduled_arrival) and _present(actual_arrival)):
            result.append(
                missing_item(
                    "flight_delay_arrival_time_pair_missing",
                    (
                        "Debe aportarse la pareja completa de llegada programada y "
                        "real, con fecha y zona horaria."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
        else:
            result.append(
                missing_item(
                    "flight_delay_arrival_time_calculation_review",
                    (
                        "OPS debe comprobar la llegada real mediante apertura de "
                        "puertas y calcular el retraso considerando fecha y zona "
                        "horaria; los campos de hora no se restan automáticamente."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )

    expenses, _ = validated_value(record, "gastos_adicionales_eur")
    if _present(expenses):
        result.append(
            missing_item(
                "flight_delay_expense_receipts_review",
                (
                    "Deben aportarse y vincularse los justificantes de gastos "
                    "razonables, evitando duplicidades o partidas no relacionadas "
                    "con el retraso."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    if _refund_requested(record) and (
        delay_minutes is None or delay_minutes < 300 or context == "arrival"
    ):
        result.append(
            missing_item(
                "flight_delay_five_hour_refund_review",
                (
                    "El reembolso por desistir del viaje exige comprobar un retraso "
                    "de salida de al menos cinco horas y la opción efectivamente "
                    "elegida por el pasajero."
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


def build_travel_flight_delay_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="travel",
        family="retraso_vuelo",
        specialist="travel.flight_delay",
    )

    fact, fact_key = validated_value(facts_record, "descripcion_hecho")
    incident, incident_key = validated_value(facts_record, "incidencia_tipo")
    booking, booking_key = validated_value(facts_record, "numero_reserva")
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
    actual_departure, actual_departure_key = validated_value(
        facts_record,
        "hora_salida_real",
    )
    scheduled_arrival, scheduled_arrival_key = validated_value(
        facts_record,
        "hora_llegada_programada",
    )
    actual_arrival, actual_arrival_key = validated_value(
        facts_record,
        "hora_llegada_real",
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

    text, _ = _delay_text(facts_record)
    context = _delay_context(text)
    delay_minutes, delay_conflict = _explicit_delay_minutes(text)
    regime = resolve_air_passenger_regime(flight_date)
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
            ("destino", "Destino final", ""),
            ("hora_salida_programada", "Salida programada", ""),
            ("hora_salida_real", "Salida real", ""),
            ("hora_llegada_programada", "Llegada programada", ""),
            ("hora_llegada_real", "Llegada real", ""),
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
    if delay_minutes is not None:
        context_label = {
            "arrival": "llegada al destino final",
            "departure": "salida",
            "mixed": "contexto mixto",
            "unknown": "contexto pendiente de confirmar",
        }[context]
        summary.append(
            f"Duración literal identificada: {delay_minutes} minutos ({context_label})."
        )

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
                code="flight_delay_scope_booking_and_carrier",
                title="Ámbito, reserva y transportista efectivo",
                body=(
                    "La reclamación debe vincular la reserva con el vuelo, la "
                    "fecha, el trayecto, el destino final y el transportista "
                    "efectivo. El ámbito europeo no se presume: debe quedar "
                    "acreditado por la salida o, cuando proceda, por el destino y "
                    "la condición del transportista."
                ),
                source_fact_keys=scope_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    measurement_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            incident_key,
            scheduled_departure_key,
            actual_departure_key,
            scheduled_arrival_key,
            actual_arrival_key,
            destination_key,
        ),
    )
    if measurement_sources:
        duration_text = (
            f"{delay_minutes} minutos"
            if delay_minutes is not None
            else "pendiente de fijar documentalmente"
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="flight_delay_departure_vs_final_arrival",
                title="Medición del retraso y destino final",
                body=(
                    "Deben separarse el retraso de salida, que activa determinados "
                    "derechos de asistencia, del retraso de llegada al destino "
                    "final, relevante para la eventual compensación. La duración "
                    f"literal identificada es {duration_text}. RTM no resta horas "
                    "sin comprobar fecha, zona horaria, conexiones y momento de "
                    "apertura de puertas."
                ),
                source_fact_keys=measurement_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    care_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            incident_key,
            flight_date_key,
            scheduled_departure_key,
            actual_departure_key,
            expenses_key,
            response_key,
        ),
    )
    if care_sources:
        expense_text = (
            f"{expenses} €" if _present(expenses) else "sin cuantía validada"
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="flight_delay_care_and_documented_expenses",
                title="Asistencia y gastos razonables",
                body=(
                    "La asistencia se examina según el retraso esperado de salida "
                    "y la banda de distancia. Si el transportista no la presta, "
                    "solo se incorporan gastos razonables, necesarios, vinculados "
                    "al retraso y respaldados por justificantes. El expediente "
                    f"refleja: {expense_text}."
                ),
                source_fact_keys=care_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    compensation_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            incident_key,
            destination_key,
            scheduled_arrival_key,
            actual_arrival_key,
            compensation_key,
            passengers_key,
            response_key,
        ),
    )
    if compensation_sources:
        duration_text = (
            f"{delay_minutes} minutos"
            if delay_minutes is not None
            else "sin duración única validada"
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="flight_delay_compensation_final_destination",
                title="Compensación por gran retraso de llegada",
                body=(
                    "La posible compensación exige acreditar una llegada al "
                    "destino final con el umbral temporal aplicable, el número de "
                    "pasajeros, el ámbito y la distancia. El expediente refleja "
                    f"{duration_text}, con contexto {context}. RTM no fija una "
                    "cuantía ni reconoce el derecho sin completar esas "
                    "comprobaciones."
                ),
                source_fact_keys=compensation_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    refund_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            incident_key,
            scheduled_departure_key,
            actual_departure_key,
            refund_key,
            solution_key,
        ),
    )
    if refund_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="flight_delay_five_hour_refund_choice",
                title="Reembolso cuando el retraso de salida alcanza cinco horas",
                body=(
                    "Cuando el retraso de salida alcanza al menos cinco horas, "
                    "debe comprobarse si el pasajero optó por no viajar y solicitó "
                    "el reembolso correspondiente. Este derecho no se confunde con "
                    "la compensación por llegada tardía y RTM no impone una opción "
                    "distinta de la documentada."
                ),
                source_fact_keys=refund_sources,
                priority="secondary",
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
            flight_date_key,
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
                code="flight_delay_extraordinary_circumstances_and_measures",
                title="Circunstancias extraordinarias y medidas razonables",
                body=(
                    "La exoneración de la compensación no se acepta mediante una "
                    "mención genérica. El transportista debe acreditar la causa "
                    "concreta, su relación directa con el retraso y que no habría "
                    "podido evitarse incluso adoptando medidas razonables. Respuesta "
                    f"documentada: {response_text}."
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
            airline_key,
            agency_key,
            alternative_key,
            response_key,
        ),
    )
    if information_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="flight_delay_information_and_evidence",
                title="Información al pasajero y conservación de la prueba",
                body=(
                    "Deben conservarse la reserva, tarjetas de embarque, avisos, "
                    "horarios, conexiones, alternativa, respuesta del transportista "
                    "y justificantes. La reclamación debe dirigirse al sujeto "
                    "responsable sin confundirlo con la plataforma intermediaria."
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
            *_review_missing(
                facts_record,
                regime,
                delay_minutes,
                delay_conflict,
                context,
            ),
            *fact_review_items(facts_record, prefix="flight_delay"),
        ]
    )

    destination_text = (
        str(airline).strip()
        if _present(airline)
        else "TRANSPORTISTA AÉREO PENDIENTE DE VALIDAR"
    )
    subject_parts = ["RETRASO DE VUELO"]
    if _present(booking):
        subject_parts.append(f"reserva {booking}")
    if _present(flight_number):
        subject_parts.append(f"vuelo {flight_number}")
    if _present(flight_date):
        subject_parts.append(f"fecha {flight_date}")

    risks = [
        (
            "No se ha calculado automáticamente la compensación: depende de la "
            "llegada al destino final, ámbito, distancia, pasajeros y causa."
        ),
        (
            "El retraso de salida y el de llegada producen derechos distintos y "
            "no deben tratarse como una única medición."
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

    requested_outcomes = [
        "Asistencia y atención correspondientes al retraso acreditado.",
        "Reintegro de gastos razonables, necesarios y documentados.",
        (
            "Compensación legal cuando la llegada al destino final, el ámbito, "
            "la distancia y las circunstancias permitan reconocerla."
        ),
        (
            "Reembolso del billete cuando se acredite un retraso de salida de al "
            "menos cinco horas y esa sea la opción elegida."
        ),
        "Respuesta motivada y entrega de la información y prueba utilizadas.",
    ]

    primary_strategy = (
        "Vincular reserva, vuelo, trayecto y transportista efectivo; fijar por "
        "separado el retraso de salida y el de llegada al destino final; revisar "
        "asistencia, gastos y opción de reembolso; y valorar la compensación sin "
        "aceptar cuantías ni exoneraciones no acreditadas."
    )
    if _present(requested_solution):
        primary_strategy += (
            f" La solución documental solicitada es: {_display(requested_solution)}."
        )

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="retraso_vuelo",
        specialist="travel.flight_delay",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Se ha documentado el retraso del vuelo {_display(flight_number)} "
            f"de la reserva {_display(booking)}."
            if _present(flight_number) and _present(booking)
            else "Se ha documentado un posible retraso de vuelo."
        ),
        client_goal=(
            "Obtener asistencia, reintegro de gastos y, cuando proceda, "
            "reembolso o compensación conforme al retraso acreditado."
        ),
        primary_strategy=primary_strategy,
        secondary_strategies=[
            (
                "Reclamar primero al transportista efectivo, conservando la "
                "intervención de la agencia cuando sea relevante."
            ),
            (
                "Separar el derecho de asistencia y gastos de la eventual "
                "compensación por llegada tardía."
            ),
            (
                "Escalar al organismo nacional competente o a la vía adecuada "
                "solo después de comprobar ruta, tiempos, transportista y respuesta."
            ),
        ],
        requested_outcomes=requested_outcomes,
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record),
        risks=list(dict.fromkeys(risks)),
        destination=destination_text,
        document_type="RECLAMACIÓN EXTRAJUDICIAL POR RETRASO DE VUELO",
        subject=" — ".join(subject_parts),
        legal_arguments=arguments,
        additional_requests=[
            "Reserva completa, billetes, tarjetas de embarque y pasajeros reclamantes.",
            "Horarios programados y reales de salida y llegada, con fecha y zona horaria.",
            "Prueba de apertura de puertas o llegada efectiva al destino final.",
            "Itinerario completo y conexiones incluidas en la misma reserva.",
            "Identidad del transportista aéreo efectivo y del comercializador.",
            "Avisos de retraso, causa comunicada y oferta de transporte alternativo.",
            "Facturas y recibos de manutención, alojamiento, transporte y comunicaciones.",
            "Explicación y prueba concreta de las circunstancias extraordinarias alegadas.",
            "Respuesta completa de la aerolínea a la reclamación previa.",
        ],
        created_by_component=(
            "travel.flight_delay:"
            f"{TRAVEL_FLIGHT_DELAY_SPECIALIST_VERSION}+"
            f"{AIR_PASSENGER_REGIME_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
