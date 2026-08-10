"""Especialista RTM para ``travel.package``.

Construye una Previa Jurídica conservadora para viajes combinados. Exige una
calificación documental positiva y al menos dos tipos distintos de servicios de
viaje, separa organizador, minorista y prestadores, distingue cancelación,
terminación, cambios, falta de conformidad e insolvencia y no anticipa la
aplicación nacional de la Directiva (UE) 2026/1024.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
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
from rtm_core.package_travel_regime import (
    PACKAGE_TRAVEL_REGIME_VERSION,
    PackageTravelRegimeDecision,
    resolve_package_travel_regime,
)


TRAVEL_PACKAGE_SPECIALIST_VERSION = "rtm_travel_package_specialist_v1_0"

PackageIncident = Literal[
    "organizer_cancellation",
    "traveller_termination",
    "significant_change",
    "performance_failure",
    "insolvency",
    "extraordinary_circumstances",
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
    if isinstance(value, bool):
        return "Sí" if value else "No"
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


def _package_text(record: ValidatedFactsRecord) -> str:
    values: list[Any] = []
    for key in (
        "descripcion_hecho",
        "incidencia_tipo",
        "servicios_viaje_incluidos",
        "servicios_incluidos",
        "cambio_sustancial_propuesto",
        "condiciones_cancelacion",
        "alternativa_ofrecida",
        "reubicacion_ofrecida",
        "reembolso_estado",
        "circunstancias_extraordinarias",
        "asistencia_ofrecida",
        "garantia_insolvencia",
        "respuesta_documentada",
        "solucion_solicitada",
    ):
        value, _ = validated_value(record, key)
        if _present(value):
            values.append(value)
    return _fold(values)


def _extraordinary_marker(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "circunstancias inevitables y extraordinarias",
            "circunstancias extraordinarias",
            "riesgo grave para la seguridad",
            "catastrofe natural",
            "huracan",
            "terremoto",
            "inundacion grave",
            "incendio forestal",
            "conflicto armado",
            "guerra",
            "epidemia",
            "pandemia",
        )
    )


def _organizer_cancelled(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "cancelado por el organizador",
            "cancelada por el organizador",
            "el organizador cancelo",
            "la agencia cancelo el viaje combinado",
            "el proveedor cancelo el paquete",
            "viaje combinado cancelado por la agencia",
            "paquete cancelado por el organizador",
            "cancelacion comunicada por el organizador",
        )
    )


def _traveller_terminated(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "el viajero solicito cancelar",
            "el viajero resolvio el contrato",
            "el cliente solicito cancelar el viaje",
            "cancelacion solicitada por el viajero",
            "terminacion solicitada por el viajero",
            "desistio del viaje combinado",
            "renuncio al viaje combinado",
        )
    )


def _significant_change(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "cambio sustancial",
            "modificacion sustancial",
            "cambio significativo",
            "modificacion significativa",
            "cambio de destino",
            "cambio de fechas",
            "cambio de hotel",
            "cambio de vuelo",
            "aumento del precio",
            "incremento del precio",
            "subida del precio",
            "mas del ocho por ciento",
            "mas del 8 por ciento",
            "superior al ocho por ciento",
            "superior al 8 por ciento",
        )
    )


def _performance_failure(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "servicio no prestado",
            "servicios no prestados",
            "servicio no ejecutado",
            "incumplimiento del viaje combinado",
            "cumplimiento defectuoso",
            "falta de conformidad",
            "hotel de categoria inferior",
            "excursion cancelada",
            "traslado no prestado",
            "vuelo no incluido",
            "actividad no disponible",
            "regreso no efectuado",
            "viaje interrumpido",
        )
    )


def _insolvency_marker(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "insolvencia",
            "organizador insolvente",
            "agencia insolvente",
            "concurso de acreedores",
            "cese de pagos",
            "cierre de la agencia",
            "no puede reembolsar por falta de liquidez",
            "falta de liquidez del organizador",
        )
    )


def _incident(record: ValidatedFactsRecord) -> PackageIncident:
    text = _package_text(record)
    if _insolvency_marker(text):
        return "insolvency"

    active = [
        name
        for name, enabled in (
            ("organizer_cancellation", _organizer_cancelled(text)),
            ("traveller_termination", _traveller_terminated(text)),
            ("significant_change", _significant_change(text)),
            ("performance_failure", _performance_failure(text)),
        )
        if enabled
    ]
    if len(active) > 1:
        return "mixed"
    if len(active) == 1:
        return active[0]  # type: ignore[return-value]
    if _extraordinary_marker(text):
        return "extraordinary_circumstances"
    return "unknown"


def _package_status(record: ValidatedFactsRecord) -> Optional[bool]:
    explicit, _ = validated_value(record, "reserva_es_viaje_combinado")
    return explicit if isinstance(explicit, bool) else None


def _service_values(record: ValidatedFactsRecord) -> list[Any]:
    values: list[Any] = []
    for key in (
        "servicios_viaje_incluidos",
        "servicios_incluidos",
        "descripcion_hecho",
        "incidencia_tipo",
    ):
        value, _ = validated_value(record, key)
        if _present(value):
            values.append(value)

    flight, _ = validated_value(record, "numero_vuelo")
    if _present(flight):
        values.append(f"Vuelo {flight}")

    accommodation, _ = validated_value(record, "alojamiento")
    if _present(accommodation):
        values.append(f"Hotel o alojamiento {accommodation}")

    return values


def _required_missing(
    record: ValidatedFactsRecord,
    incident: PackageIncident,
    package_status: Optional[bool],
) -> list[MissingItem]:
    groups = [
        (
            "package_fact_missing",
            "Falta validar la incidencia concreta del viaje combinado.",
            ("descripcion_hecho", "incidencia_tipo"),
        ),
        (
            "package_organizer_missing",
            "Falta identificar documentalmente al organizador del viaje combinado.",
            ("organizador_viaje",),
        ),
        (
            "package_booking_reference_missing",
            "Falta validar la confirmación o localizador del viaje combinado.",
            ("numero_reserva",),
        ),
        (
            "package_contract_date_missing",
            "Falta la fecha documental del contrato o reserva.",
            ("fecha_reserva", "fecha_documento"),
        ),
        (
            "package_start_missing",
            "Falta validar la fecha de inicio del viaje combinado.",
            ("fecha_inicio_viaje", "estancia_inicio"),
        ),
        (
            "package_end_missing",
            "Falta validar la fecha de finalización del viaje combinado.",
            ("fecha_fin_viaje", "estancia_fin"),
        ),
        (
            "package_organizer_country_missing",
            "Falta validar el país de establecimiento del organizador o contratación.",
            ("pais_organizador",),
        ),
        (
            "package_services_missing",
            "Falta describir los servicios de viaje incluidos en el paquete.",
            ("servicios_viaje_incluidos", "servicios_incluidos"),
        ),
        (
            "package_price_missing",
            "Falta validar el precio total del viaje combinado.",
            ("precio_total_viaje_eur", "precio_total_reserva_eur", "importe_pagado_eur"),
        ),
        (
            "package_requested_solution_missing",
            "Falta validar la solución solicitada por el viajero.",
            ("solucion_solicitada",),
        ),
    ]

    if incident == "organizer_cancellation":
        groups.extend(
            [
                (
                    "package_cancellation_notice_date_missing",
                    "Falta la fecha en que el organizador comunicó la cancelación.",
                    ("aviso_incidencia_fecha", "fecha_notificacion"),
                ),
                (
                    "package_refund_status_missing",
                    "Falta el estado documental del reembolso tras la cancelación.",
                    ("reembolso_estado",),
                ),
            ]
        )
    elif incident == "traveller_termination":
        groups.extend(
            [
                (
                    "package_traveller_termination_date_missing",
                    "Falta la fecha en que el viajero resolvió o canceló el contrato.",
                    ("terminacion_viajero_fecha", "cancelacion_solicitada_fecha"),
                ),
                (
                    "package_cancellation_terms_missing",
                    "Faltan las condiciones de terminación o penalización aceptadas.",
                    ("condiciones_cancelacion",),
                ),
            ]
        )
    elif incident == "significant_change":
        groups.extend(
            [
                (
                    "package_change_detail_missing",
                    "Falta describir con precisión el cambio propuesto por el organizador.",
                    ("cambio_sustancial_propuesto",),
                ),
                (
                    "package_change_notice_date_missing",
                    "Falta la fecha de comunicación del cambio.",
                    ("fecha_aviso_cambio", "aviso_incidencia_fecha"),
                ),
                (
                    "package_traveller_change_response_missing",
                    "Falta documentar la opción ejercida por el viajero frente al cambio.",
                    ("respuesta_documentada",),
                ),
            ]
        )
    elif incident == "insolvency":
        groups.append(
            (
                "package_insolvency_guarantee_missing",
                "Falta identificar la garantía o entidad garante frente a la insolvencia.",
                ("garantia_insolvencia",),
            )
        )

    result: list[MissingItem] = []
    for code, description, keys in groups:
        value, _ = validated_value(record, *keys)
        if not _present(value):
            result.append(missing_item(code, description))

    if package_status is not True:
        result.append(
            missing_item(
                "package_positive_status_missing",
                (
                    "Debe constar documentalmente que se contrató un viaje combinado; "
                    "no basta una etiqueta comercial ni una inferencia del sistema."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    return result


def _review_missing(
    record: ValidatedFactsRecord,
    regime: PackageTravelRegimeDecision,
    incident: PackageIncident,
) -> list[MissingItem]:
    result: list[MissingItem] = []

    if regime.status != "current":
        result.append(
            missing_item(
                "package_regime_review",
                regime.blocking_reason
                or "Debe determinarse y versionarse el régimen jurídico aplicable.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if regime.package_qualified is not True:
        result.append(
            missing_item(
                "package_qualification_review",
                (
                    "No está cerrada la calificación como viaje combinado con al "
                    "menos dos tipos distintos de servicios de viaje."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    if incident == "unknown":
        result.append(
            missing_item(
                "package_incident_type_missing",
                (
                    "Debe determinarse si existe cancelación del organizador, "
                    "terminación por el viajero, cambio sustancial, falta de "
                    "conformidad, insolvencia o circunstancias extraordinarias."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    elif incident == "mixed":
        result.append(
            missing_item(
                "package_multiple_incidents_split_required",
                (
                    "Los hechos contienen varias incidencias materiales. Deben "
                    "separarse por servicio, fecha, responsable, remedio y perjuicio."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    result.extend(
        [
            missing_item(
                "package_organizer_retailer_provider_roles_review",
                (
                    "Debe distinguirse organizador, minorista y cada prestador, "
                    "comprobando quién diseñó, vendió, cobró, modificó y ejecutó el paquete."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "package_precontractual_information_and_contract_review",
                (
                    "OPS debe conservar la información precontractual, el formulario "
                    "normalizado, la confirmación y el contrato completo en soporte duradero."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "package_linked_arrangement_boundary_review",
                (
                    "Debe descartarse que se trate de servicios independientes o de "
                    "un servicio de viaje vinculado en lugar de un viaje combinado."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
            missing_item(
                "package_sector_rights_and_duplicate_recovery_review",
                (
                    "Deben coordinarse los derechos sectoriales de vuelo, equipaje, "
                    "hotel, seguro y medios de pago sin duplicar devoluciones o daños."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            ),
        ]
    )

    text = _package_text(record)
    extraordinary = _extraordinary_marker(text)
    start, _ = validated_value(record, "fecha_inicio_viaje", "estancia_inicio")
    notice, _ = validated_value(
        record,
        "fecha_aviso_cambio",
        "aviso_incidencia_fecha",
        "fecha_notificacion",
    )
    parsed_start = _parse_date(start)
    parsed_notice = _parse_date(notice)

    if extraordinary:
        result.append(
            missing_item(
                "package_extraordinary_circumstances_scope_review",
                (
                    "Debe verificarse la naturaleza inevitable y extraordinaria, el "
                    "lugar afectado, su proximidad al destino, el impacto significativo "
                    "y la información disponible al comunicar la terminación."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    if incident == "organizer_cancellation":
        result.append(
            missing_item(
                "package_organizer_cancellation_ground_review",
                (
                    "Debe comprobarse si la cancelación se basa en falta del número "
                    "mínimo de viajeros, circunstancias inevitables y extraordinarias "
                    "u otra causa, junto con los plazos de aviso del contrato."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
        refund, _ = validated_value(record, "reembolso_estado")
        if _present(refund):
            result.append(
                missing_item(
                    "package_full_refund_amount_and_timing_review",
                    (
                        "Debe verificarse que el reembolso comprende todos los pagos "
                        "debidos y se realiza dentro del plazo aplicable."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )

    if incident == "traveller_termination":
        if extraordinary:
            result.append(
                missing_item(
                    "package_penalty_free_extraordinary_termination_review",
                    (
                        "Debe comprobarse si concurren las condiciones para resolver "
                        "sin penalización y con reembolso completo, sin presumir "
                        "compensación adicional."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
        else:
            result.append(
                missing_item(
                    "package_termination_penalty_justification_review",
                    (
                        "La penalización debe ser adecuada y justificable conforme a "
                        "la antelación, el ahorro de costes y la reutilización de los servicios."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )

    if incident == "significant_change":
        alternative, _ = validated_value(record, "alternativa_ofrecida")
        refund, _ = validated_value(record, "reembolso_estado")
        response, _ = validated_value(record, "respuesta_documentada")
        if not any(_present(value) for value in (alternative, refund, response)):
            result.append(
                missing_item(
                    "package_change_choice_and_remedy_missing",
                    (
                        "No consta opción documentada de aceptar, resolver sin "
                        "penalización o recibir un viaje sustitutivo."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
        result.append(
            missing_item(
                "package_significant_change_durable_notice_review",
                (
                    "Debe comprobarse que el cambio, su impacto en el precio, el plazo "
                    "de respuesta y el viaje sustitutivo se comunicaron claramente en "
                    "soporte duradero."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

        price_marker = any(
            marker in text
            for marker in (
                "aumento del precio",
                "incremento del precio",
                "subida del precio",
                "mas del ocho por ciento",
                "mas del 8 por ciento",
            )
        )
        percentage, _ = validated_value(record, "incremento_precio_porcentaje")
        if _present(percentage):
            try:
                percentage_value = float(percentage)
            except (TypeError, ValueError):
                percentage_value = -1.0
            if percentage_value < 0:
                result.append(
                    missing_item(
                        "package_price_increase_percentage_invalid",
                        "El porcentaje de incremento no contiene un valor válido.",
                        MissingItemSeverity.BLOCKING,
                    )
                )
            elif percentage_value > 8:
                result.append(
                    missing_item(
                        "package_price_increase_over_eight_review",
                        (
                            "El incremento validado supera el ocho por ciento y debe "
                            "revisarse como cambio que permite aceptar o resolver sin penalización."
                        ),
                        MissingItemSeverity.HUMAN_REVIEW,
                    )
                )
        elif price_marker:
            result.append(
                missing_item(
                    "package_price_increase_percentage_missing",
                    (
                        "Se menciona una subida de precio, pero falta validar su "
                        "porcentaje, base de cálculo y causa contractual."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )

        if price_marker and parsed_notice is not None and parsed_start is not None:
            if parsed_notice > parsed_start - timedelta(days=20):
                result.append(
                    missing_item(
                        "package_price_increase_last_twenty_days_review",
                        (
                            "La subida aparece comunicada dentro de los veinte días "
                            "anteriores al inicio y debe revisarse antes de admitirla."
                        ),
                        MissingItemSeverity.BLOCKING,
                    )
                )

    if incident == "performance_failure":
        alternative, _ = validated_value(
            record,
            "alternativa_ofrecida",
            "reubicacion_ofrecida",
        )
        refund, _ = validated_value(record, "reembolso_estado")
        if not _present(alternative) and not _present(refund):
            result.append(
                missing_item(
                    "package_performance_remedy_missing",
                    (
                        "No consta subsanación, alternativa, reducción del precio ni "
                        "estado del reembolso por el servicio no ejecutado."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
        complaint_date, _ = validated_value(record, "reclamacion_previa_fecha")
        complaint_channel, _ = validated_value(record, "canal_reclamacion")
        if not _present(complaint_date) or not _present(complaint_channel):
            result.append(
                missing_item(
                    "package_contemporaneous_nonconformity_notice_review",
                    (
                        "Debe acreditarse cuándo y por qué canal se comunicó la falta "
                        "de conformidad y qué oportunidad hubo de subsanarla."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
        result.append(
            missing_item(
                "package_alternative_quality_and_price_reduction_review",
                (
                    "Las alternativas deben compararse con el contrato; si son de "
                    "menor calidad debe revisarse una reducción adecuada del precio."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    if incident == "insolvency":
        guarantee, _ = validated_value(record, "garantia_insolvencia")
        if _present(guarantee):
            result.append(
                missing_item(
                    "package_insolvency_guarantee_activation_review",
                    (
                        "Debe comprobarse el alcance, canal y activación efectiva de la "
                        "garantía, incluidos reembolso y repatriación cuando proceda."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
        repatriation, _ = validated_value(record, "repatriacion_necesaria")
        if repatriation is True:
            result.append(
                missing_item(
                    "package_repatriation_arrangements_review",
                    (
                        "Debe conservarse la necesidad de repatriación, viajeros "
                        "afectados, transporte incluido y respuesta de la entidad garante."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )

    expenses, _ = validated_value(record, "gastos_adicionales_eur")
    amount, _ = validated_value(record, "importe_reclamado_eur")
    if _present(expenses):
        result.append(
            missing_item(
                "package_expense_receipts_review",
                (
                    "Deben aportarse facturas y justificantes de gastos razonables, "
                    "necesarios y causalmente vinculados a la incidencia."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if _present(amount):
        result.append(
            missing_item(
                "package_claim_amount_breakdown_review",
                (
                    "La cuantía debe desglosarse por pagos, reducción de precio, "
                    "gastos y daños, descontando importes ya recuperados."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    return result


def _deadlines(
    record: ValidatedFactsRecord,
    regime: PackageTravelRegimeDecision,
    incident: PackageIncident,
) -> list[Deadline]:
    deadlines: list[Deadline] = []

    explicit, explicit_key = validated_value(record, "fecha_limite")
    if _present(explicit) and explicit_key:
        parsed = _parse_date(explicit)
        if parsed is not None:
            deadlines.append(
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
            )

    termination, termination_key = validated_value(
        record,
        "terminacion_viajero_fecha",
        "cancelacion_solicitada_fecha",
        "aviso_incidencia_fecha",
        "fecha_aviso_cambio",
        "fecha_notificacion",
    )
    if incident in {
        "organizer_cancellation",
        "traveller_termination",
        "significant_change",
        "extraordinary_circumstances",
    }:
        parsed_termination = _parse_date(termination)
        if parsed_termination is not None and termination_key:
            due = parsed_termination + timedelta(days=14)
            deadlines.append(
                Deadline(
                    label="Reembolso tras la terminación del viaje combinado",
                    due_at=datetime(
                        due.year,
                        due.month,
                        due.day,
                        tzinfo=timezone.utc,
                    ),
                    calculation_status="estimated",
                    source_fact_keys=[termination_key],
                    notes=[
                        (
                            "Estimación de catorce días naturales. OPS debe confirmar "
                            "que la fecha de origen corresponde a la terminación efectiva."
                        )
                    ],
                )
            )

    end, end_key = validated_value(
        record,
        "fecha_fin_viaje",
        "estancia_fin",
        "fecha_incidencia",
    )
    limitation_note = (
        "El régimen español identificado establece dos años, pero el inicio del "
        "cómputo y las interrupciones requieren revisión OPS."
        if regime.status == "current" and regime.limitation_years == 2
        else (
            "Debe determinarse el plazo, el inicio del cómputo y las interrupciones "
            "conforme a la ley aplicable."
        )
    )
    deadlines.append(
        Deadline(
            label="Prescripción de la reclamación de viaje combinado",
            due_at=None,
            calculation_status="unresolved",
            source_fact_keys=validated_source_keys(record, (end_key,)),
            notes=[
                limitation_note,
                (
                    f"Fecha final o de incidencia documentada: {end}."
                    if _present(end)
                    else "Fecha de exigibilidad pendiente de confirmar."
                ),
            ],
        )
    )
    return deadlines


def build_travel_package_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="travel",
        family="viaje_combinado",
        specialist="travel.package",
    )

    fact, fact_key = validated_value(facts_record, "descripcion_hecho")
    incident_value, incident_key = validated_value(
        facts_record,
        "incidencia_tipo",
    )
    organizer, organizer_key = validated_value(
        facts_record,
        "organizador_viaje",
    )
    retailer, retailer_key = validated_value(
        facts_record,
        "minorista_viaje",
        "agencia",
    )
    provider, provider_key = validated_value(facts_record, "proveedor")
    booking_ref, booking_ref_key = validated_value(
        facts_record,
        "numero_reserva",
    )
    contract_date, contract_date_key = validated_value(
        facts_record,
        "fecha_reserva",
        "fecha_documento",
    )
    start, start_key = validated_value(
        facts_record,
        "fecha_inicio_viaje",
        "estancia_inicio",
    )
    end, end_key = validated_value(
        facts_record,
        "fecha_fin_viaje",
        "estancia_fin",
    )
    organizer_country, organizer_country_key = validated_value(
        facts_record,
        "pais_organizador",
    )
    services, services_key = validated_value(
        facts_record,
        "servicios_viaje_incluidos",
        "servicios_incluidos",
    )
    package_value, package_key = validated_value(
        facts_record,
        "reserva_es_viaje_combinado",
    )
    price, price_key = validated_value(
        facts_record,
        "precio_total_viaje_eur",
        "precio_total_reserva_eur",
        "importe_pagado_eur",
    )
    change, change_key = validated_value(
        facts_record,
        "cambio_sustancial_propuesto",
    )
    change_notice, change_notice_key = validated_value(
        facts_record,
        "fecha_aviso_cambio",
        "aviso_incidencia_fecha",
    )
    termination_date, termination_date_key = validated_value(
        facts_record,
        "terminacion_viajero_fecha",
        "cancelacion_solicitada_fecha",
    )
    cancellation_terms, cancellation_terms_key = validated_value(
        facts_record,
        "condiciones_cancelacion",
    )
    cancellation_charge, cancellation_charge_key = validated_value(
        facts_record,
        "cargo_cancelacion_eur",
    )
    price_increase, price_increase_key = validated_value(
        facts_record,
        "incremento_precio_porcentaje",
    )
    extraordinary, extraordinary_key = validated_value(
        facts_record,
        "circunstancias_extraordinarias",
    )
    alternative, alternative_key = validated_value(
        facts_record,
        "alternativa_ofrecida",
        "reubicacion_ofrecida",
    )
    refund, refund_key = validated_value(facts_record, "reembolso_estado")
    assistance, assistance_key = validated_value(
        facts_record,
        "asistencia_ofrecida",
    )
    guarantee, guarantee_key = validated_value(
        facts_record,
        "garantia_insolvencia",
    )
    repatriation, repatriation_key = validated_value(
        facts_record,
        "repatriacion_necesaria",
    )
    expenses, expenses_key = validated_value(
        facts_record,
        "gastos_adicionales_eur",
    )
    amount, amount_key = validated_value(
        facts_record,
        "importe_reclamado_eur",
    )
    passengers, passengers_key = validated_value(
        facts_record,
        "numero_pasajeros",
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

    incident = _incident(facts_record)
    package_status = _package_status(facts_record)
    service_values = _service_values(facts_record)
    regime = resolve_package_travel_regime(
        contract_date=contract_date,
        package_start=start,
        package_end=end,
        organizer_country=organizer_country,
        package_status=package_status,
        service_types=service_values,
    )
    basis = list(regime.legal_basis)

    summary, summary_keys = summary_rows(
        facts_record,
        (
            ("organizador_viaje", "Organizador", ""),
            ("minorista_viaje", "Minorista", ""),
            ("agencia", "Agencia o plataforma", ""),
            ("proveedor", "Proveedor indicado", ""),
            ("numero_reserva", "Reserva", ""),
            ("fecha_reserva", "Fecha del contrato", ""),
            ("fecha_inicio_viaje", "Inicio", ""),
            ("fecha_fin_viaje", "Fin", ""),
            ("estancia_inicio", "Inicio documentado", ""),
            ("estancia_fin", "Fin documentado", ""),
            ("pais_organizador", "País del organizador", ""),
            ("servicios_viaje_incluidos", "Servicios incluidos", ""),
            ("servicios_incluidos", "Servicios documentados", ""),
            ("precio_total_viaje_eur", "Precio total", " €"),
            ("precio_total_reserva_eur", "Precio documentado", " €"),
            ("numero_pasajeros", "Viajeros", ""),
            ("reserva_es_viaje_combinado", "Viaje combinado", ""),
            ("cambio_sustancial_propuesto", "Cambio propuesto", ""),
            ("fecha_aviso_cambio", "Aviso del cambio", ""),
            ("terminacion_viajero_fecha", "Terminación del viajero", ""),
            ("condiciones_cancelacion", "Condiciones de cancelación", ""),
            ("cargo_cancelacion_eur", "Penalización", " €"),
            ("incremento_precio_porcentaje", "Incremento del precio", " %"),
            ("circunstancias_extraordinarias", "Circunstancias extraordinarias", ""),
            ("alternativa_ofrecida", "Alternativa", ""),
            ("reembolso_estado", "Reembolso", ""),
            ("asistencia_ofrecida", "Asistencia", ""),
            ("garantia_insolvencia", "Garantía de insolvencia", ""),
            ("repatriacion_necesaria", "Repatriación necesaria", ""),
            ("gastos_adicionales_eur", "Gastos documentados", " €"),
            ("importe_reclamado_eur", "Importe reclamado", " €"),
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
            f"Clasificación operativa: {incident}; viaje combinado "
            f"{package_status}; tipos de servicio detectados: "
            f"{regime.service_type_count}."
        )
    )
    summary.append(
        (
            f"Marco territorial: {regime.scope}; versión futura: "
            f"{regime.revised_directive_status}."
        )
    )

    arguments = []

    qualification_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            incident_key,
            organizer_key,
            retailer_key,
            booking_ref_key,
            contract_date_key,
            start_key,
            end_key,
            organizer_country_key,
            services_key,
            package_key,
            price_key,
            passengers_key,
        ),
    )
    if qualification_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="package_qualification_contract_and_services",
                title="Calificación, contrato y servicios combinados",
                body=(
                    "La aplicación del régimen exige probar la combinación de al "
                    "menos dos tipos distintos de servicios de viaje y vincularla a "
                    "un contrato, precio, viajeros, fechas y organizador concretos. "
                    "La facturación separada o la etiqueta comercial no sustituyen "
                    "la reconstrucción documental de cómo se ofreció y contrató el paquete."
                ),
                source_fact_keys=qualification_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    role_sources = validated_source_keys(
        facts_record,
        (
            organizer_key,
            retailer_key,
            provider_key,
            booking_ref_key,
            contract_date_key,
            price_key,
            response_key,
        ),
    )
    if role_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="package_organizer_retailer_and_provider_roles",
                title="Organizador, minorista y prestadores",
                body=(
                    "Debe identificarse quién organizó y quién vendió el viaje, "
                    "separándolos de aerolínea, hotel y demás prestadores. La "
                    "reclamación debe dirigirse según el ámbito de gestión documentado, "
                    "sin convertir automáticamente a toda plataforma en organizadora."
                ),
                source_fact_keys=role_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    cancellation_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            incident_key,
            start_key,
            end_key,
            termination_date_key,
            change_notice_key,
            cancellation_terms_key,
            cancellation_charge_key,
            extraordinary_key,
            refund_key,
            solution_key,
        ),
    )
    if cancellation_sources and incident in {
        "organizer_cancellation",
        "traveller_termination",
        "extraordinary_circumstances",
    }:
        arguments.append(
            legal_argument(
                facts_record,
                code="package_termination_cancellation_and_refund",
                title="Terminación, cancelación y reembolso",
                body=(
                    "Debe distinguirse la terminación voluntaria del viajero de la "
                    "cancelación del organizador y de las circunstancias inevitables "
                    "y extraordinarias. Las penalizaciones requieren justificación; "
                    "el reembolso completo y la ausencia de compensación adicional "
                    "solo se afirman cuando concurren y se prueban los presupuestos "
                    "legales correspondientes."
                ),
                source_fact_keys=cancellation_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    change_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            incident_key,
            change_key,
            change_notice_key,
            price_increase_key,
            alternative_key,
            refund_key,
            response_key,
            solution_key,
            start_key,
        ),
    )
    if change_sources and incident == "significant_change":
        arguments.append(
            legal_argument(
                facts_record,
                code="package_price_and_significant_change",
                title="Revisión de precio y cambios sustanciales",
                body=(
                    "El cambio debe compararse con las características principales "
                    "contratadas y comunicarse en soporte duradero con su repercusión "
                    "económica y un plazo razonable de decisión. Las subidas de precio "
                    "requieren cláusula, método y causa válidos; un incremento superior "
                    "al ocho por ciento o un cambio sustancial activa la revisión de la "
                    "opción entre aceptar, resolver sin penalización o recibir un viaje sustitutivo."
                ),
                source_fact_keys=change_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    performance_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            incident_key,
            services_key,
            alternative_key,
            refund_key,
            assistance_key,
            complaint_date_key,
            complaint_channel_key,
            response_key,
            solution_key,
        ),
    )
    if performance_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="package_execution_remediation_and_assistance",
                title="Ejecución, subsanación, alternativas y asistencia",
                body=(
                    "Los servicios reales deben compararse con el contrato. La falta "
                    "de conformidad debe comunicarse y, cuando sea posible, subsanarse. "
                    "Si una parte significativa no puede prestarse, la alternativa "
                    "debe ser adecuada y sin coste adicional; una calidad inferior "
                    "exige revisar reducción de precio, daños acreditados y asistencia."
                ),
                source_fact_keys=performance_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    insolvency_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            organizer_key,
            retailer_key,
            guarantee_key,
            repatriation_key,
            refund_key,
            price_key,
            response_key,
        ),
    )
    if insolvency_sources and incident == "insolvency":
        arguments.append(
            legal_argument(
                facts_record,
                code="package_insolvency_guarantee_and_repatriation",
                title="Garantía frente a insolvencia y repatriación",
                body=(
                    "Debe activarse la garantía identificada para obtener el reembolso "
                    "de pagos correspondientes a servicios no ejecutados y, cuando el "
                    "paquete incluya transporte y sea necesario, la repatriación. No "
                    "se presume que una mera solicitud a la agencia haya activado la garantía."
                ),
                source_fact_keys=insolvency_sources,
                priority="primary",
                legal_basis=basis,
            )
        )

    damage_sources = validated_source_keys(
        facts_record,
        (
            fact_key,
            price_key,
            cancellation_charge_key,
            alternative_key,
            refund_key,
            expenses_key,
            amount_key,
            response_key,
            solution_key,
        ),
    )
    if damage_sources:
        arguments.append(
            legal_argument(
                facts_record,
                code="package_price_reduction_damage_and_no_double_recovery",
                title="Reducción del precio, daños y ausencia de duplicidad",
                body=(
                    "La petición económica debe desglosar pagos no restituidos, "
                    "reducción por menor calidad, gastos necesarios y daños probados. "
                    "Los derechos del viaje combinado deben coordinarse con los del "
                    "vuelo, equipaje, alojamiento, seguro o tarjeta, descontando toda "
                    "cantidad ya reembolsada y sin fijar una compensación plana."
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
            *_required_missing(facts_record, incident, package_status),
            *_review_missing(facts_record, regime, incident),
            *fact_review_items(facts_record, prefix="package"),
        ]
    )

    destination_text = (
        str(organizer).strip()
        if _present(organizer)
        else (
            str(retailer).strip()
            if _present(retailer)
            else (
                str(provider).strip()
                if _present(provider)
                else "ORGANIZADOR PENDIENTE DE VALIDAR"
            )
        )
    )
    subject_parts = ["RECLAMACIÓN VIAJE COMBINADO", incident.upper()]
    if _present(booking_ref):
        subject_parts.append(f"reserva {booking_ref}")
    if _present(start) and _present(end):
        subject_parts.append(f"{start} a {end}")

    primary_strategy = (
        "Cerrar la calificación como viaje combinado y los servicios que lo integran; "
        "identificar organizador, minorista y prestadores; reconstruir contrato, "
        "cambio o incumplimiento, comunicaciones y pagos; exigir la opción, "
        "subsanación, asistencia, reembolso o garantía que corresponda; y reclamar "
        "únicamente importes y daños documentados sin duplicidades."
    )
    if _present(solution):
        primary_strategy += (
            f" La solución documental solicitada es: {_display(solution)}."
        )

    requested_outcomes = [
        "Confirmación escrita de la calificación del contrato, organizador, minorista y servicios incluidos.",
        "Entrega de la información precontractual, contrato, condiciones y garantía de insolvencia.",
        "Cumplimiento, subsanación o alternativa adecuada cuando todavía resulte útil.",
        "Reembolso de pagos y reducción del precio que procedan según el hecho acreditado.",
        "Reintegro de gastos necesarios y daños documentados, sin duplicar recuperaciones.",
        "Respuesta motivada con cronología, causa y desglose económico de la solución ofrecida.",
    ]
    if incident == "insolvency":
        requested_outcomes.insert(
            2,
            "Activación inmediata de la garantía y repatriación cuando resulte necesaria.",
        )

    risks = [
        (
            "Confundir viaje combinado, servicio de viaje vinculado y reservas "
            "independientes puede seleccionar un responsable y un régimen incorrectos."
        ),
        (
            "La condición de organizador o minorista depende de la contratación y "
            "gestión documentadas, no solo de la marca o del canal de pago."
        ),
        (
            "Las circunstancias inevitables y extraordinarias no se presumen por una "
            "mención genérica y pueden alterar penalización, reembolso y compensación."
        ),
        (
            "La Directiva (UE) 2026/1024 está adoptada, pero sus medidas nacionales "
            "no deben aplicarse anticipadamente antes de su fecha de aplicación."
        ),
        (
            "Los derechos sectoriales pueden coexistir, pero una misma pérdida no "
            "puede recuperarse dos veces."
        ),
        *list(regime.warnings),
    ]

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="viaje_combinado",
        specialist="travel.package",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Se ha documentado una incidencia de viaje combinado ({incident}) en "
            f"la reserva {_display(booking_ref)}."
            if _present(booking_ref)
            else "Se ha documentado una posible incidencia de viaje combinado."
        ),
        client_goal=(
            "Obtener el cumplimiento, alternativa, reembolso, asistencia o garantía "
            "procedentes y recuperar los gastos y daños realmente acreditados."
        ),
        primary_strategy=primary_strategy,
        secondary_strategies=[
            "Reclamar indistintamente a organizador o minorista según el ámbito de gestión documentado.",
            "Ejercitar en paralelo derechos sectoriales frente a prestadores sin duplicar importes.",
            "Activar seguro, tarjeta o garantía de insolvencia cuando su cobertura esté acreditada.",
        ],
        requested_outcomes=list(dict.fromkeys(requested_outcomes)),
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record, regime, incident),
        risks=list(dict.fromkeys(risks)),
        destination=destination_text,
        document_type="RECLAMACIÓN EXTRAJUDICIAL POR VIAJE COMBINADO",
        subject=" — ".join(subject_parts),
        legal_arguments=arguments,
        additional_requests=[
            "Formulario normalizado de información y oferta precontractual.",
            "Contrato o confirmación completa del viaje combinado en soporte duradero.",
            "Desglose del precio, pagos, recargos y revisiones de precio.",
            "Identidad y datos de organizador, minorista y entidad garante.",
            "Relación de vuelos, alojamientos, transportes, alquileres y actividades incluidos.",
            "Comunicaciones de cambios, cancelación, terminación y opciones concedidas.",
            "Prueba de aceptación o rechazo del cambio o viaje sustitutivo.",
            "Comunicaciones de faltas de conformidad y oportunidad de subsanación.",
            "Alternativas ofrecidas y comparación de calidad, coste y duración.",
            "Estado y justificante de reembolsos, reducciones de precio y compensaciones.",
            "Facturas de gastos y prueba de daños causalmente vinculados.",
            "Póliza, chargeback o reembolsos sectoriales ya solicitados u obtenidos.",
            "Certificado o datos de la garantía de insolvencia y repatriación.",
            "Documentación sobre las circunstancias inevitables y extraordinarias alegadas.",
        ],
        created_by_component=(
            "travel.package:"
            f"{TRAVEL_PACKAGE_SPECIALIST_VERSION}+"
            f"{PACKAGE_TRAVEL_REGIME_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
