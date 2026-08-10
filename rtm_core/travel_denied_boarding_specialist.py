"""Especialista RTM para ``travel.denied_boarding``.

Construye una Previa Jurídica conservadora para denegaciones de embarque. Separa
la renuncia voluntaria de la denegación involuntaria, exige comprobar reserva y
presentación en plazo, y no trata como sobreventa indemnizable una negativa
razonablemente fundada en salud, seguridad o documentación de viaje.
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


TRAVEL_DENIED_BOARDING_SPECIALIST_VERSION = (
    "rtm_travel_denied_boarding_specialist_v1_0"
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

BoardingDisposition = Literal[
    "involuntary",
    "volunteer",
    "reasonable_ground",
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


def _explicit_eu_departure(origin: Any) -> bool:
    folded = _fold(origin)
    return any(token in folded for token in _EU_EEA_DEPARTURE_TOKENS)


def _boarding_text(record: ValidatedFactsRecord) -> str:
    values: list[Any] = []
    for key in (
        "descripcion_hecho",
        "incidencia_tipo",
        "alternativa_ofrecida",
        "reembolso_estado",
        "respuesta_documentada",
        "solucion_solicitada",
    ):
        value, _ = validated_value(record, key)
        if _present(value):
            values.append(value)
    return _fold(values)


def _reasonable_ground_marker(text: str) -> bool:
    markers = (
        "documentacion de viaje inadecuada",
        "documentacion de viaje incorrecta",
        "documentacion incompleta",
        "documentacion caducada",
        "pasaporte caducado",
        "pasaporte no valido",
        "visado no valido",
        "sin visado",
        "motivos de salud",
        "razones de salud",
        "motivos de seguridad",
        "razones de seguridad",
        "riesgo para la seguridad",
        "comportamiento disruptivo",
        "comportamiento violento",
        "estado de embriaguez",
        "intoxicacion",
    )
    return any(marker in text for marker in markers)


def _boarding_disposition(text: str) -> BoardingDisposition:
    if _reasonable_ground_marker(text):
        return "reasonable_ground"

    involuntary_markers = (
        "no se ofrecio voluntario",
        "no se ofrecieron voluntarios",
        "sin ofrecerse voluntario",
        "sin ofrecerse voluntarios",
        "contra su voluntad",
        "contra la voluntad",
        "denegacion involuntaria",
        "denegaron involuntariamente",
        "fue obligado a ceder",
        "fueron obligados a ceder",
    )
    if any(marker in text for marker in involuntary_markers):
        return "involuntary"

    volunteer_markers = (
        "se ofrecio voluntario",
        "se ofrecieron voluntarios",
        "acepto voluntariamente",
        "aceptaron voluntariamente",
        "cedio voluntariamente",
        "cedieron voluntariamente",
        "renuncio voluntariamente",
        "renunciaron voluntariamente",
        "voluntario a cambio",
        "voluntarios a cambio",
    )
    if any(marker in text for marker in volunteer_markers):
        return "volunteer"

    if any(marker in text for marker in ("sobreventa", "overbooking")) and any(
        marker in text
        for marker in (
            "denegacion de embarque",
            "denegaron el embarque",
            "impidieron embarcar",
            "no permitieron embarcar",
        )
    ):
        return "involuntary"
    return "unknown"


def _presentation_status(text: str) -> Optional[bool]:
    negative_markers = (
        "llego tarde a la puerta",
        "llegaron tarde a la puerta",
        "fuera del plazo de embarque",
        "no se presento al embarque",
        "no se presentaron al embarque",
        "no show",
        "check-in fuera de plazo",
    )
    if any(marker in text for marker in negative_markers):
        return False

    positive_markers = (
        "se presento a tiempo",
        "se presentaron a tiempo",
        "se presento en plazo",
        "se presentaron en plazo",
        "check-in realizado",
        "facturacion realizada",
        "en la puerta de embarque a tiempo",
        "tarjeta de embarque emitida",
    )
    if any(marker in text for marker in positive_markers):
        return True
    return None


def _travel_documents_status(text: str) -> Optional[bool]:
    invalid_markers = (
        "documentacion de viaje inadecuada",
        "documentacion de viaje incorrecta",
        "documentacion incompleta",
        "documentacion caducada",
        "pasaporte caducado",
        "pasaporte no valido",
        "visado no valido",
        "sin visado",
    )
    if any(marker in text for marker in invalid_markers):
        return False

    valid_markers = (
        "documentacion de viaje valida",
        "documentacion valida",
        "documentacion correcta",
        "pasaporte valido",
        "pasaportes validos",
        "visado valido",
        "visados validos",
    )
    if any(marker in text for marker in valid_markers):
        return True
    return None


def _compensation_requested(record: ValidatedFactsRecord) -> bool:
    amount, _ = validated_value(record, "compensacion_solicitada_eur")
    solution, _ = validated_value(record, "solucion_solicitada")
    return _present(amount) or "compens" in _fold(solution)


def _choice_documented(record: ValidatedFactsRecord) -> bool:
    alternative, _ = validated_value(record, "alternativa_ofrecida")
    refund, _ = validated_value(record, "reembolso_estado")
    solution, _ = validated_value(record, "solucion_solicitada")
    text = _fold(solution)
    return any(
        (
            _present(alternative),
            _present(refund),
            "reembolso" in text,
            "transporte alternativo" in text,
            "reubicacion" in text,
            "reencaminamiento" in text,
        )
    )


def _required_missing(record: ValidatedFactsRecord) -> list[MissingItem]:
    groups = (
        (
            "denied_boarding_fact_missing",
            "Falta validar el hecho concreto de la denegación de embarque.",
            ("descripcion_hecho", "incidencia_tipo"),
        ),
        (
            "denied_boarding_booking_missing",
            "Falta validar la reserva confirmada o su localizador.",
            ("numero_reserva",),
        ),
        (
            "denied_boarding_flight_number_missing",
            "Falta validar el número del vuelo.",
            ("numero_vuelo",),
        ),
        (
            "denied_boarding_flight_date_missing",
            "Falta validar la fecha programada del vuelo.",
            ("fecha_vuelo", "fecha_incidencia"),
        ),
        (
            "denied_boarding_carrier_missing",
            "Falta validar la aerolínea o transportista aéreo efectivo.",
            ("aerolinea", "proveedor"),
        ),
        (
            "denied_boarding_origin_missing",
            "Falta validar el aeropuerto o país de salida.",
            ("origen",),
        ),
        (
            "denied_boarding_destination_missing",
            "Falta validar el destino final de la reserva.",
            ("destino",),
        ),
        (
            "denied_boarding_requested_solution_missing",
            "Falta validar la solución elegida o solicitada por el pasajero.",
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
    regime: AirPassengerRegimeDecision,
    disposition: BoardingDisposition,
    presentation: Optional[bool],
    travel_documents: Optional[bool],
) -> list[MissingItem]:
    result: list[MissingItem] = []

    if regime.status != "current":
        result.append(
            missing_item(
                "denied_boarding_regime_transition_review",
                regime.blocking_reason
                or "Debe determinarse y versionarse el régimen temporal aplicable.",
                MissingItemSeverity.BLOCKING,
            )
        )

    origin, _ = validated_value(record, "origen")
    if _present(origin) and not _explicit_eu_departure(origin):
        result.append(
            missing_item(
                "denied_boarding_eu_scope_review",
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
                "denied_boarding_operating_carrier_review",
                (
                    "Debe distinguirse el transportista aéreo efectivo de la "
                    "agencia, plataforma o aerolínea comercializadora."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "denied_boarding_booking_passengers_review",
                (
                    "OPS debe comprobar reserva confirmada, billetes, pasajeros, "
                    "tarjetas de embarque y que el trayecto reclamado pertenece a "
                    "la misma reserva."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
        ]
    )

    if presentation is None:
        result.append(
            missing_item(
                "denied_boarding_timely_presentation_missing",
                (
                    "Falta acreditar que el pasajero se presentó al check-in y a la "
                    "puerta en las condiciones y el horario exigibles."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    elif presentation is False:
        result.append(
            missing_item(
                "denied_boarding_late_or_absent_presentation_review",
                (
                    "Los hechos indican presentación tardía o ausencia. Debe "
                    "comprobarse el horario comunicado y la prueba del transportista "
                    "antes de tratar el caso como denegación de embarque protegida."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    if travel_documents is None:
        result.append(
            missing_item(
                "denied_boarding_travel_documents_review",
                (
                    "Debe comprobarse la documentación de viaje exigible para el "
                    "trayecto y conservar la causa concreta comunicada por la aerolínea."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    elif travel_documents is False:
        result.append(
            missing_item(
                "denied_boarding_invalid_documents_review",
                (
                    "Los hechos señalan documentación inadecuada o incompleta. "
                    "Debe verificarse el requisito aplicable y la corrección de la "
                    "decisión antes de pedir los derechos propios de una denegación "
                    "involuntaria sin causa razonable."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    compensation_requested = _compensation_requested(record)
    if disposition == "unknown":
        result.append(
            missing_item(
                "denied_boarding_voluntariness_missing",
                (
                    "Debe determinarse si el pasajero renunció voluntariamente a la "
                    "reserva o si la aerolínea le denegó el embarque contra su voluntad."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    elif disposition == "volunteer":
        result.append(
            missing_item(
                "denied_boarding_volunteer_benefits_review",
                (
                    "Debe conservarse el llamamiento a voluntarios, el beneficio "
                    "pactado, su aceptación y la opción de reembolso o transporte "
                    "alternativo ofrecida al pasajero."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
        if compensation_requested:
            result.append(
                missing_item(
                    "denied_boarding_volunteer_compensation_review",
                    (
                        "El pasajero aparece como voluntario. No debe acumularse "
                        "automáticamente la compensación reglada de una denegación "
                        "involuntaria sin revisar el acuerdo alcanzado."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
    elif disposition == "reasonable_ground":
        result.append(
            missing_item(
                "denied_boarding_reasonable_ground_review",
                (
                    "La aerolínea invoca o los hechos reflejan salud, seguridad o "
                    "documentación de viaje. Deben probarse el motivo concreto, su "
                    "necesidad y su correcta aplicación antes de reconocer o excluir "
                    "los derechos por denegación de embarque."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    else:
        result.append(
            missing_item(
                "denied_boarding_volunteer_call_review",
                (
                    "Debe comprobarse si, antes de la denegación involuntaria, la "
                    "aerolínea solicitó voluntarios y qué ofertas realizó."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    if not _choice_documented(record):
        result.append(
            missing_item(
                "denied_boarding_article8_choice_missing",
                (
                    "Falta documentar la elección entre reembolso y transporte "
                    "alternativo, así como la opción realmente aceptada."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    alternative, _ = validated_value(record, "alternativa_ofrecida")
    if _present(alternative):
        result.append(
            missing_item(
                "denied_boarding_rerouting_timing_review",
                (
                    "Debe comprobarse la salida y llegada de la alternativa, el "
                    "destino final, su aceptación y cualquier reducción que pudiera "
                    "afectar a la compensación."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    passengers, _ = validated_value(record, "numero_pasajeros")
    if compensation_requested and not _present(passengers):
        result.append(
            missing_item(
                "denied_boarding_passenger_count_missing",
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
                "denied_boarding_compensation_amount_review",
                (
                    "La cuantía solicitada no puede aceptarse sin verificar "
                    "distancia, pasajeros, voluntariedad, causa y transporte alternativo."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    if compensation_requested and disposition == "involuntary":
        result.append(
            missing_item(
                "denied_boarding_distance_band_review",
                (
                    "Debe comprobarse la distancia ortodrómica y el tipo de ruta "
                    "antes de seleccionar una cuantía o reducción."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    expenses, _ = validated_value(record, "gastos_adicionales_eur")
    if _present(expenses):
        result.append(
            missing_item(
                "denied_boarding_expense_receipts_review",
                (
                    "Deben aportarse y vincularse los justificantes de gastos "
                    "razonables, evitando duplicidades o partidas no relacionadas "
                    "con la denegación de embarque."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
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


def build_travel_denied_boarding_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="travel",
        family="denegacion_embarque",
        specialist="travel.denied_boarding",
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

    text = _boarding_text(facts_record)
    disposition = _boarding_disposition(text)
    presentation = _presentation_status(text)
    travel_documents = _travel_documents_status(text)
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
    summary.append(
        "Clasificación operativa de la negativa: "
        f"{disposition}; presentación: {presentation}; "
        f"documentación de viaje: {travel_documents}."
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
            scheduled_departure_key,
        ),
    )
    if scope_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="denied_boarding_scope_reservation_and_presentation",
                title="Ámbito, reserva y presentación al embarque",
                body=(
                    "La denegación protegida exige una reserva confirmada y que el "
                    "pasajero se haya presentado en las condiciones y el horario "
                    "aplicables. El ámbito europeo tampoco se presume: debe quedar "
                    "acreditado por la salida o, cuando proceda, por el destino y "
                    "la condición del transportista efectivo."
                ),
                source_fact_keys=scope_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    disposition_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            incident_key,
            response_key,
            booking_key,
            airline_key,
        ),
    )
    if disposition_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="denied_boarding_volunteer_or_involuntary",
                title="Voluntariedad y orden de actuación del transportista",
                body=(
                    "Cuando el transportista prevé denegar embarques debe solicitar "
                    "primero voluntarios. La renuncia voluntaria exige un acuerdo "
                    "sobre beneficios y mantiene las opciones de reembolso o "
                    "transporte alternativo; la denegación involuntaria activa un "
                    "régimen distinto. La clasificación operativa del expediente "
                    f"es {disposition}, pendiente de la comprobación OPS indicada."
                ),
                source_fact_keys=disposition_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    reason_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            incident_key,
            response_key,
            booking_key,
            airline_key,
        ),
    )
    if reason_sources:
        response_text = (
            _display(response)
            if _present(response)
            else "no consta una explicación documental validada"
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="denied_boarding_reasonable_grounds_and_evidence",
                title="Motivos razonables y prueba de la negativa",
                body=(
                    "No toda negativa constituye denegación de embarque protegida. "
                    "Deben revisarse salud, seguridad, comportamiento y documentación "
                    "de viaje, con identificación del requisito y prueba de su "
                    "aplicación al pasajero. Respuesta documentada del transportista: "
                    f"{response_text}."
                ),
                source_fact_keys=reason_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    rights_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            incident_key,
            alternative_key,
            refund_key,
            compensation_key,
            passengers_key,
            destination_key,
            solution_key,
        ),
    )
    if rights_sources:
        alternative_text = (
            _display(alternative)
            if _present(alternative)
            else "sin alternativa validada"
        )
        refund_text = (
            _display(refund_status)
            if _present(refund_status)
            else "pendiente de validar"
        )
        arguments.append(
            legal_argument(
                facts_record,
                code="denied_boarding_compensation_and_article8_choice",
                title="Compensación y elección entre reembolso y alternativa",
                body=(
                    "En una denegación involuntaria sin motivo razonable deben "
                    "examinarse separadamente la compensación y la elección entre "
                    "reembolso o transporte alternativo. La alternativa figura "
                    f"como: {alternative_text}; el reembolso: {refund_text}. RTM "
                    "no fija una cuantía ni impone una opción distinta de la "
                    "documentada."
                ),
                source_fact_keys=rights_sources,
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
            airline_key,
            alternative_key,
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
                code="denied_boarding_care_and_documented_expenses",
                title="Asistencia y gastos razonables",
                body=(
                    "La asistencia debe examinarse desde la denegación hasta el "
                    "transporte alternativo o la solución elegida. Si no se presta, "
                    "solo se incorporan gastos razonables, necesarios, vinculados "
                    "al incidente y respaldados por justificantes. El expediente "
                    f"refleja: {expense_text}."
                ),
                source_fact_keys=care_sources,
                priority="secondary",
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
                code="denied_boarding_information_and_evidence",
                title="Información al pasajero y conservación de la prueba",
                body=(
                    "Deben conservarse la reserva, tarjetas de embarque, prueba de "
                    "presentación, documentos de viaje, llamamiento a voluntarios, "
                    "motivo escrito, alternativa, respuesta del transportista y "
                    "justificantes. La reclamación debe dirigirse al transportista "
                    "efectivo sin confundirlo con la plataforma intermediaria."
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
                disposition,
                presentation,
                travel_documents,
            ),
            *fact_review_items(facts_record, prefix="denied_boarding"),
        ]
    )

    destination_text = (
        str(airline).strip()
        if _present(airline)
        else "TRANSPORTISTA AÉREO PENDIENTE DE VALIDAR"
    )
    subject_parts = ["DENEGACIÓN DE EMBARQUE"]
    if _present(booking):
        subject_parts.append(f"reserva {booking}")
    if _present(flight_number):
        subject_parts.append(f"vuelo {flight_number}")
    if _present(flight_date):
        subject_parts.append(f"fecha {flight_date}")

    risks = [
        (
            "No se ha calculado automáticamente la compensación: depende de la "
            "voluntariedad, causa, ámbito, distancia, pasajeros y alternativa."
        ),
        (
            "La falta de presentación en plazo o una documentación de viaje "
            "inadecuada pueden impedir aplicar el régimen de denegación involuntaria."
        ),
        (
            "La renuncia voluntaria y la denegación contra la voluntad del pasajero "
            "producen derechos distintos y no deben mezclarse."
        ),
        (
            "La agencia o plataforma no debe confundirse con el transportista "
            "aéreo efectivo al dirigir la reclamación."
        ),
        (
            "El plazo de prescripción no es uniforme en toda la Unión y exige "
            "determinar el Derecho nacional y el foro aplicables."
        ),
        *list(regime.warnings),
    ]

    primary_strategy = (
        "Vincular reserva, vuelo y transportista efectivo; acreditar presentación "
        "en plazo y documentación válida; determinar si existió renuncia voluntaria, "
        "sobreventa o un motivo razonable; separar compensación, elección de "
        "reembolso o transporte alternativo y asistencia; y exigir prueba escrita "
        "de la negativa."
    )
    if _present(requested_solution):
        primary_strategy += (
            f" La solución documental solicitada es: {_display(requested_solution)}."
        )

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="denegacion_embarque",
        specialist="travel.denied_boarding",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Se ha documentado una denegación de embarque en el vuelo "
            f"{_display(flight_number)} de la reserva {_display(booking)}."
            if _present(flight_number) and _present(booking)
            else "Se ha documentado una posible denegación de embarque."
        ),
        client_goal=(
            "Obtener la solución de transporte elegida, asistencia, reintegro de "
            "gastos y, cuando proceda, la compensación correspondiente."
        ),
        primary_strategy=primary_strategy,
        secondary_strategies=[
            (
                "Reclamar primero al transportista efectivo, conservando la "
                "intervención de la agencia cuando sea relevante."
            ),
            (
                "Pedir por escrito el motivo, el llamamiento a voluntarios y la "
                "prueba de presentación o documentación utilizada para denegar."
            ),
            (
                "Escalar al organismo nacional competente o a la vía adecuada "
                "solo después de comprobar ámbito, causa y respuesta."
            ),
        ],
        requested_outcomes=[
            "Reembolso o transporte alternativo conforme a la opción documentada.",
            "Asistencia y reintegro de gastos razonables y acreditados.",
            (
                "Compensación legal cuando exista denegación involuntaria, "
                "presentación válida y ausencia de motivo razonable."
            ),
            "Motivación escrita y entrega de la información y prueba utilizadas.",
        ],
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record),
        risks=list(dict.fromkeys(risks)),
        destination=destination_text,
        document_type="RECLAMACIÓN EXTRAJUDICIAL POR DENEGACIÓN DE EMBARQUE",
        subject=" — ".join(subject_parts),
        legal_arguments=arguments,
        additional_requests=[
            "Reserva completa, billetes, tarjetas de embarque y pasajeros reclamantes.",
            "Prueba de check-in y presencia en la puerta dentro del horario exigido.",
            "Documentación de viaje presentada y requisitos aplicables al trayecto.",
            "Identidad del transportista aéreo efectivo y del comercializador.",
            "Llamamiento a voluntarios, ofertas realizadas y acuerdos aceptados.",
            "Motivo escrito de la denegación y prueba concreta utilizada.",
            "Oferta de reembolso o transporte alternativo y horarios de la alternativa.",
            "Facturas y recibos de manutención, alojamiento, transporte y comunicaciones.",
            "Respuesta completa de la aerolínea a la reclamación previa.",
        ],
        created_by_component=(
            "travel.denied_boarding:"
            f"{TRAVEL_DENIED_BOARDING_SPECIALIST_VERSION}+"
            f"{AIR_PASSENGER_REGIME_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
