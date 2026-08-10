"""Especialista conservador para reclamaciones de seguro de viaje RTM.

Consume exclusivamente hechos validados y una familia bloqueada. Separa póliza,
siniestro, cobertura, exclusiones, asistencia, cuantías y vías paralelas sin
presumir cobertura, mora, nulidad de cláusulas ni indemnizaciones automáticas.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import HTTPException

from rtm_core.authority_repository import FamilyResolutionRecord, ValidatedFactsRecord
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
    validated_source_keys,
    validated_value,
)
from rtm_core.travel_insurance_regime import (
    TRAVEL_INSURANCE_REGIME_VERSION,
    TravelInsuranceRegimeDecision,
    resolve_travel_insurance_regime,
)


TRAVEL_INSURANCE_SPECIALIST_VERSION = "rtm_travel_insurance_specialist_v1_0"
NOTICE_REFERENCE_DAYS = 7
MINIMUM_PAYMENT_REFERENCE_DAYS = 40
CUSTOMER_SERVICE_WAIT_DAYS = 30

InsuranceIncident = Literal[
    "denial_or_exclusion",
    "handling_or_payment_delay",
    "medical_assistance",
    "repatriation",
    "trip_cancellation",
    "trip_interruption",
    "baggage",
    "travel_delay",
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
    values = []
    for key in (
        "descripcion_hecho",
        "incidencia_tipo",
        "naturaleza_cobertura_documentada",
        "cobertura_reclamada_tipo",
        "coberturas_poliza",
        "decision_aseguradora",
        "motivo_rechazo_aseguradora",
        "exclusion_invocada",
        "respuesta_documentada",
        "solucion_solicitada",
    ):
        value, _ = validated_value(record, key)
        if _present(value):
            values.append(value)
    return _fold(values)


def _incident(record: ValidatedFactsRecord) -> InsuranceIncident:
    text = _text(record)
    reason, _ = validated_value(record, "motivo_rechazo_aseguradora")
    exclusion, _ = validated_value(record, "exclusion_invocada")
    if _present(reason) or _present(exclusion) or any(
        marker in text
        for marker in (
            "cobertura rechazada",
            "siniestro rechazado",
            "denegacion de cobertura",
            "no cubierto por la poliza",
            "claim denied",
            "coverage denied",
        )
    ):
        return "denial_or_exclusion"

    if any(
        marker in text
        for marker in (
            "pago pendiente",
            "siniestro pendiente",
            "demora en la tramitacion",
            "retraso en la indemnizacion",
            "sin respuesta de la aseguradora",
            "claim pending",
            "payment pending",
        )
    ):
        return "handling_or_payment_delay"

    active: list[InsuranceIncident] = []
    medical, _ = validated_value(record, "importe_gastos_medicos_eur")
    if (_amount(medical) or 0) > 0 or any(
        marker in text
        for marker in ("asistencia medica", "gastos medicos", "hospitalizacion")
    ):
        active.append("medical_assistance")

    repatriation_requested, _ = validated_value(record, "repatriacion_solicitada")
    repatriation_done, _ = validated_value(record, "repatriacion_ejecutada")
    if repatriation_requested is True or repatriation_done is True or "repatriacion" in text:
        active.append("repatriation")

    cancellation, _ = validated_value(record, "importe_cancelacion_viaje_eur")
    if (_amount(cancellation) or 0) > 0 or "cancelacion del viaje" in text:
        active.append("trip_cancellation")

    interruption, _ = validated_value(record, "importe_interrupcion_viaje_eur")
    if (_amount(interruption) or 0) > 0 or "interrupcion del viaje" in text:
        active.append("trip_interruption")

    baggage, _ = validated_value(record, "importe_equipaje_asegurado_eur")
    if (_amount(baggage) or 0) > 0 or any(
        marker in text
        for marker in ("cobertura de equipaje", "seguro de equipaje", "equipaje asegurado")
    ):
        active.append("baggage")

    if any(marker in text for marker in ("cobertura de demora", "seguro por retraso")):
        active.append("travel_delay")

    unique = list(dict.fromkeys(active))
    if len(unique) > 1:
        return "mixed"
    return unique[0] if unique else "unknown"


def _regime(record: ValidatedFactsRecord) -> TravelInsuranceRegimeDecision:
    policy_date, _ = validated_value(
        record, "fecha_contratacion_seguro", "fecha_reserva", "fecha_documento"
    )
    coverage_start, _ = validated_value(record, "fecha_inicio_cobertura")
    coverage_end, _ = validated_value(record, "fecha_fin_cobertura")
    loss_date, _ = validated_value(
        record, "fecha_incidencia", "fecha_conocimiento_siniestro"
    )
    insurer_country, _ = validated_value(record, "pais_aseguradora")
    nature, _ = validated_value(record, "naturaleza_cobertura_documentada")
    coverages, _ = validated_value(
        record, "coberturas_poliza", "cobertura_reclamada_tipo", "descripcion_hecho"
    )
    sac_date, _ = validated_value(
        record, "reclamacion_sac_fecha", "reclamacion_previa_fecha"
    )
    added, _ = validated_value(
        record, "seguro_anadido_reserva", "seguro_incluido_viaje_combinado"
    )
    distributor, _ = validated_value(record, "distribuidor_seguro")
    return resolve_travel_insurance_regime(
        policy_date=policy_date,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        loss_date=loss_date,
        insurer_country=insurer_country,
        coverage_nature=nature,
        policy_coverages=coverages,
        sac_complaint_date=sac_date,
        insurance_added_to_booking=added,
        insurance_distributor=distributor,
    )


def _required_missing(
    record: ValidatedFactsRecord,
    incident: InsuranceIncident,
) -> list[MissingItem]:
    groups: list[tuple[str, str, tuple[str, ...]]] = [
        ("insurance_fact_missing", "Falta validar la incidencia concreta.", ("descripcion_hecho", "incidencia_tipo")),
        ("insurance_insurer_missing", "Falta identificar a la aseguradora.", ("aseguradora_viaje",)),
        ("insurance_policy_reference_missing", "Falta la referencia de la póliza.", ("poliza_ref",)),
        ("insurance_insured_identity_missing", "Falta identificar al asegurado, tomador o beneficiario.", ("asegurado_viaje", "tomador_seguro", "beneficiario_seguro")),
        ("insurance_policy_date_missing", "Falta la fecha de contratación de la póliza.", ("fecha_contratacion_seguro", "fecha_documento")),
        ("insurance_coverage_start_missing", "Falta el inicio de cobertura.", ("fecha_inicio_cobertura",)),
        ("insurance_coverage_end_missing", "Falta el fin de cobertura.", ("fecha_fin_cobertura",)),
        ("insurance_loss_date_missing", "Falta la fecha del siniestro.", ("fecha_incidencia", "fecha_conocimiento_siniestro")),
        ("insurance_notice_date_missing", "Falta la fecha de comunicación del siniestro.", ("fecha_comunicacion_siniestro",)),
        ("insurance_country_missing", "Falta el país de la aseguradora.", ("pais_aseguradora",)),
        ("insurance_coverage_terms_missing", "Faltan las garantías aplicables.", ("coberturas_poliza", "cobertura_reclamada_tipo")),
        ("insurance_claim_status_missing", "Falta la decisión o estado del siniestro.", ("decision_aseguradora", "respuesta_documentada")),
        ("insurance_requested_solution_missing", "Falta la solución solicitada.", ("solucion_solicitada",)),
    ]
    if incident == "denial_or_exclusion":
        groups.extend(
            [
                ("insurance_denial_reason_missing", "Falta el motivo completo de la denegación.", ("motivo_rechazo_aseguradora", "decision_aseguradora")),
                ("insurance_invoked_exclusion_missing", "Falta la exclusión concreta invocada.", ("exclusion_invocada",)),
                ("insurance_decision_date_missing", "Falta la fecha de decisión.", ("fecha_respuesta_aseguradora", "respuesta_sac_fecha")),
            ]
        )
    elif incident == "handling_or_payment_delay":
        groups.extend(
            [
                ("insurance_complete_file_date_missing", "Falta la fecha de expediente completo.", ("fecha_documentacion_completa", "fecha_comunicacion_siniestro")),
                ("insurance_pending_amount_missing", "Falta el importe pendiente.", ("importe_reclamado_eur", "importe_gastos_medicos_eur", "importe_cancelacion_viaje_eur", "importe_asistencia_eur", "importe_equipaje_asegurado_eur", "importe_interrupcion_viaje_eur")),
            ]
        )
    elif incident == "medical_assistance":
        groups.append(("insurance_medical_amount_missing", "Faltan los gastos médicos.", ("importe_gastos_medicos_eur",)))
    elif incident == "repatriation":
        groups.append(("insurance_repatriation_evidence_missing", "Falta acreditar la repatriación.", ("repatriacion_solicitada", "repatriacion_ejecutada", "importe_asistencia_eur")))
    elif incident == "trip_cancellation":
        groups.append(("insurance_cancellation_amount_missing", "Falta el coste no recuperable.", ("importe_cancelacion_viaje_eur",)))
    elif incident == "trip_interruption":
        groups.append(("insurance_interruption_amount_missing", "Faltan las pérdidas de la interrupción.", ("importe_interrupcion_viaje_eur",)))
    elif incident == "baggage":
        groups.extend(
            [
                ("insurance_baggage_amount_missing", "Falta el importe de equipaje.", ("importe_equipaje_asegurado_eur",)),
                ("insurance_baggage_evidence_missing", "Falta el parte o prueba de equipaje.", ("equipaje_pir", "equipaje_etiqueta", "respuesta_documentada")),
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
    regime: TravelInsuranceRegimeDecision,
    incident: InsuranceIncident,
) -> list[MissingItem]:
    result: list[MissingItem] = []
    if regime.status != "current":
        result.append(
            missing_item(
                "insurance_regime_review",
                regime.blocking_reason or "Debe determinarse el régimen aplicable.",
                MissingItemSeverity.BLOCKING,
            )
        )

    if incident == "unknown":
        result.append(missing_item("insurance_incident_type_missing", "Debe determinarse la garantía concreta.", MissingItemSeverity.BLOCKING))
    elif incident == "mixed":
        result.append(missing_item("insurance_multiple_coverages_split_required", "Deben separarse las garantías, límites, gastos y peticiones.", MissingItemSeverity.BLOCKING))

    if regime.coverage_nature in {"mixed", "unknown"}:
        result.append(missing_item("insurance_coverage_nature_review", "Debe separar garantías de daños y de personas.", MissingItemSeverity.HUMAN_REVIEW))

    loss = _parse_date(validated_value(record, "fecha_incidencia", "fecha_conocimiento_siniestro")[0])
    notice = _parse_date(validated_value(record, "fecha_comunicacion_siniestro")[0])
    if loss is not None and notice is not None:
        delay = (notice - loss).days
        if delay < 0:
            result.append(missing_item("insurance_notice_chronology_review", "La comunicación aparece anterior al siniestro.", MissingItemSeverity.BLOCKING))
        elif delay > NOTICE_REFERENCE_DAYS:
            result.append(missing_item("insurance_late_notice_effect_review", "La comunicación supera siete días; debe revisarse el perjuicio sin presumir pérdida automática.", MissingItemSeverity.HUMAN_REVIEW))

    exclusion, _ = validated_value(record, "exclusion_invocada")
    highlighted, _ = validated_value(record, "exclusion_destacada")
    accepted, _ = validated_value(record, "exclusion_aceptada_especificamente")
    if incident == "denial_or_exclusion" or _present(exclusion):
        if not _present(exclusion):
            result.append(missing_item("insurance_exact_exclusion_text_required", "Debe aportarse el texto literal de la exclusión.", MissingItemSeverity.BLOCKING))
        if highlighted is not True or accepted is not True:
            result.append(missing_item("insurance_limiting_clause_acceptance_review", "Debe comprobarse destacado y aceptación específica de la cláusula limitativa.", MissingItemSeverity.BLOCKING))

    preexisting, _ = validated_value(record, "condicion_preexistente_invocada")
    questionnaire, _ = validated_value(record, "cuestionario_riesgo_aportado")
    if preexisting is True and questionnaire is not True:
        result.append(missing_item("insurance_risk_questionnaire_required", "Falta el cuestionario de riesgo y las preguntas formuladas.", MissingItemSeverity.BLOCKING))

    auth_required, _ = validated_value(record, "autorizacion_previa_requerida")
    auth_obtained, _ = validated_value(record, "autorizacion_previa_obtenida")
    if auth_required is True and auth_obtained is not True:
        result.append(missing_item("insurance_prior_authorization_exception_review", "Debe revisar urgencia, asistencia e instrucciones ante la falta de autorización previa.", MissingItemSeverity.BLOCKING))

    added, _ = validated_value(record, "seguro_anadido_reserva")
    package_included, _ = validated_value(record, "seguro_incluido_viaje_combinado")
    distributor, _ = validated_value(record, "distribuidor_seguro")
    if added is True or package_included is True or _present(distributor):
        result.append(missing_item("insurance_distribution_review", "Debe revisar distribuidor, IPID y demandas y necesidades.", MissingItemSeverity.HUMAN_REVIEW))

    package_status, _ = validated_value(record, "reserva_es_viaje_combinado")
    if package_status is True:
        result.append(missing_item("insurance_package_travel_parallel_route_review", "La vía aseguradora puede coexistir con travel.package sin duplicar recuperación.", MissingItemSeverity.HUMAN_REVIEW))

    limit_amount = _amount(validated_value(record, "limite_cobertura_eur")[0])
    claimed_amount = _amount(validated_value(record, "importe_reclamado_eur")[0])
    paid_amount = _amount(validated_value(record, "importe_pagado_aseguradora_eur")[0])
    recovered_amount = _amount(validated_value(record, "importe_recuperado_terceros_eur")[0])
    if limit_amount is not None and claimed_amount is not None and claimed_amount > limit_amount + 0.01:
        result.append(missing_item("insurance_claim_exceeds_limit_review", "La cuantía supera el límite documental.", MissingItemSeverity.HUMAN_REVIEW))
    if paid_amount is not None and claimed_amount is not None and paid_amount > claimed_amount + 0.01:
        result.append(missing_item("insurance_paid_amount_inconsistent", "El importe pagado supera la cuantía reclamada.", MissingItemSeverity.BLOCKING))
    if paid_amount is not None and limit_amount is not None and paid_amount > limit_amount + 0.01:
        result.append(missing_item("insurance_payment_exceeds_limit_inconsistent", "El pago supera el límite documental.", MissingItemSeverity.BLOCKING))
    if recovered_amount is not None and recovered_amount > 0:
        result.append(missing_item("insurance_third_party_recovery_review", "Deben coordinarse las cantidades recuperadas de terceros.", MissingItemSeverity.HUMAN_REVIEW))

    sac_date, _ = validated_value(record, "reclamacion_sac_fecha", "reclamacion_previa_fecha")
    if _present(sac_date):
        result.append(missing_item("insurance_financial_complaint_eligibility_review", "Debe comprobarse reclamación previa, plazo y admisibilidad supervisora.", MissingItemSeverity.HUMAN_REVIEW))
    return result


def _utc(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _deadlines(record: ValidatedFactsRecord, regime: TravelInsuranceRegimeDecision) -> list[Deadline]:
    result: list[Deadline] = []
    loss_value, loss_key = validated_value(record, "fecha_incidencia", "fecha_conocimiento_siniestro")
    notice_value, notice_key = validated_value(record, "fecha_comunicacion_siniestro")
    loss = _parse_date(loss_value)
    notice = _parse_date(notice_value)
    if loss is not None and loss_key:
        result.extend(
            [
                Deadline(label="Comunicación inicial del siniestro", due_at=_utc(loss + timedelta(days=NOTICE_REFERENCE_DAYS)), calculation_status="estimated", source_fact_keys=[loss_key], notes=["Referencia general; debe revisarse la póliza."]),
                Deadline(label="Cumplimiento ordinario de la prestación aseguradora", due_at=_utc(loss + timedelta(days=92)), calculation_status="estimated", source_fact_keys=[loss_key], notes=["Aproximación operativa a tres meses."]),
            ]
        )
    if notice is not None and notice_key:
        result.append(Deadline(label="Pago del importe mínimo conocido", due_at=_utc(notice + timedelta(days=MINIMUM_PAYMENT_REFERENCE_DAYS)), calculation_status="estimated", source_fact_keys=[notice_key], notes=["Referencia de cuarenta días; no determina mora automática."]))

    sac_value, sac_key = validated_value(record, "reclamacion_sac_fecha", "reclamacion_previa_fecha")
    sac = _parse_date(sac_value)
    if sac is not None and sac_key and regime.customer_service_wait_months == 1:
        result.append(Deadline(label="Espera previa para valorar reclamación financiera", due_at=_utc(sac + timedelta(days=CUSTOMER_SERVICE_WAIT_DAYS)), calculation_status="estimated", source_fact_keys=[sac_key], notes=["Referencia operativa de un mes."]))

    result.append(
        Deadline(
            label="Prescripción de la acción derivada del seguro",
            due_at=None,
            calculation_status="unresolved",
            source_fact_keys=[loss_key] if loss_key else [],
            notes=[
                f"La garantía apunta a {regime.limitation_years} años; debe fijarse el dies a quo."
                if regime.limitation_years is not None
                else "Debe separar coberturas de daños y de personas antes de elegir dos o cinco años."
            ],
        )
    )
    return result


def _summary(record: ValidatedFactsRecord, incident: InsuranceIncident, regime: TravelInsuranceRegimeDecision) -> tuple[list[str], list[str]]:
    rows = [
        f"Tipo de incidencia aseguradora: {incident}.",
        f"Régimen jurídico: {regime.status}; naturaleza {regime.coverage_nature}; ámbito {regime.scope}.",
    ]
    used: list[str] = []
    for key, label, suffix in (
        ("aseguradora_viaje", "Aseguradora", ""),
        ("poliza_ref", "Póliza", ""),
        ("siniestro_ref", "Siniestro", ""),
        ("asegurado_viaje", "Asegurado", ""),
        ("fecha_inicio_cobertura", "Inicio de cobertura", ""),
        ("fecha_fin_cobertura", "Fin de cobertura", ""),
        ("cobertura_reclamada_tipo", "Cobertura reclamada", ""),
        ("fecha_incidencia", "Fecha del siniestro", ""),
        ("fecha_comunicacion_siniestro", "Comunicación", ""),
        ("decision_aseguradora", "Decisión", ""),
        ("exclusion_invocada", "Exclusión", ""),
        ("limite_cobertura_eur", "Límite", " EUR"),
        ("franquicia_eur", "Franquicia", " EUR"),
        ("importe_reclamado_eur", "Importe reclamado", " EUR"),
    ):
        value, found = validated_value(record, key)
        if _present(value) and found:
            rows.append(f"{label}: {_display(value)}{suffix}.")
            used.append(found)
    return list(dict.fromkeys(rows)), list(dict.fromkeys(used))


def build_travel_insurance_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="travel",
        family="seguro_viaje",
        specialist="travel.insurance",
    )
    incident = _incident(facts_record)
    regime = _regime(facts_record)
    basis = list(regime.legal_basis) if regime.status == "current" else []

    insurer, insurer_key = validated_value(facts_record, "aseguradora_viaje")
    policy, policy_key = validated_value(facts_record, "poliza_ref")
    claim_ref, _ = validated_value(facts_record, "siniestro_ref")
    _, fact_key = validated_value(facts_record, "descripcion_hecho", "incidencia_tipo")
    _, coverage_key = validated_value(facts_record, "cobertura_reclamada_tipo", "coberturas_poliza")
    solution, solution_key = validated_value(facts_record, "solucion_solicitada")

    summary, summary_keys = _summary(facts_record, incident, regime)
    arguments = []

    def add(code: str, title: str, body: str, keys: tuple[Optional[str], ...], priority: str = "secondary") -> None:
        sources = validated_source_keys(facts_record, keys)
        if sources:
            arguments.append(legal_argument(facts_record, code=code, title=title, body=body, source_fact_keys=sources, priority=priority, legal_basis=basis))

    add("insurance_policy_scope_limits_and_duration", "Cobertura, límites y vigencia", "La obligación debe determinarse mediante la póliza concreta: riesgo, periodo, límite, franquicia, requisitos y exclusiones.", (policy_key, coverage_key, "fecha_inicio_cobertura", "fecha_fin_cobertura", "limite_cobertura_eur", "franquicia_eur", fact_key), "primary")
    add("insurance_occurrence_causation_and_documentation", "Siniestro, causalidad y documentación", "Debe acreditarse el hecho generador, su fecha, relación con la garantía y consecuencias documentadas.", (fact_key, "fecha_incidencia", "fecha_conocimiento_siniestro", "fecha_documentacion_completa", "equipaje_pir"), "primary")
    add("insurance_denial_exclusion_and_acceptance", "Rechazo, exclusión y aceptación", "La denegación debe identificar la cláusula exacta y comprobar claridad, destacado y aceptación específica sin presumir validez ni nulidad.", ("decision_aseguradora", "motivo_rechazo_aseguradora", "exclusion_invocada", "exclusion_destacada", "exclusion_aceptada_especificamente", "condicion_preexistente_invocada", "cuestionario_riesgo_aportado", policy_key), "primary")
    add("insurance_notice_assistance_and_authorisation", "Comunicación, asistencia y autorización", "La comunicación y autorización deben reconstruirse cronológicamente; su ausencia no produce por sí sola pérdida total sin analizar urgencia y perjuicio.", ("fecha_comunicacion_siniestro", "asistencia_contactada", "atencion_medica_urgente", "autorizacion_previa_requerida", "autorizacion_previa_obtenida", "repatriacion_solicitada", fact_key))
    add("insurance_claim_handling_payment_and_delay", "Tramitación, pago mínimo y demora", "Deben compararse declaración, documentación completa, decisión, importe mínimo, pagos y causa justificada antes de sostener mora o intereses.", ("fecha_comunicacion_siniestro", "fecha_documentacion_completa", "fecha_respuesta_aseguradora", "fecha_pago_aseguradora", "decision_aseguradora", "importe_pagado_aseguradora_eur"), "primary")
    add("insurance_quantification_and_no_double_recovery", "Cuantificación y ausencia de doble recuperación", "La petición debe desglosar gastos, prestaciones, límite, franquicia, pagos y recuperaciones de terceros.", ("importe_gastos_medicos_eur", "importe_cancelacion_viaje_eur", "importe_asistencia_eur", "importe_equipaje_asegurado_eur", "importe_interrupcion_viaje_eur", "importe_reclamado_eur", "importe_pagado_aseguradora_eur", "importe_recuperado_terceros_eur", "limite_cobertura_eur", "franquicia_eur", solution_key, fact_key))
    add("insurance_distribution_and_financial_complaint_route", "Distribución y reclamación financiera", "Cuando el seguro fue añadido por un tercero deben revisarse IPID, demandas y necesidades; la vía supervisora exige reclamación previa.", ("distribuidor_seguro", "seguro_anadido_reserva", "seguro_incluido_viaje_combinado", "documento_ipid_entregado", "necesidades_cliente_documentadas", "reclamacion_sac_fecha", "respuesta_sac_fecha", solution_key))

    if not arguments:
        raise HTTPException(status_code=409, detail="No existen hechos validados suficientes para construir la previa.")

    source_keys = validated_source_keys(
        facts_record,
        [*family_evidence_keys(family_record), *summary_keys, *(key for argument in arguments for key in argument.source_fact_keys)],
    )
    missing = dedupe_missing([
        *_required_missing(facts_record, incident),
        *_review_missing(facts_record, regime, incident),
        *fact_review_items(facts_record, prefix="insurance"),
    ])

    subject_parts = ["RECLAMACIÓN SEGURO DE VIAJE", incident.upper()]
    if _present(policy):
        subject_parts.append(f"póliza {policy}")
    if _present(claim_ref):
        subject_parts.append(f"siniestro {claim_ref}")

    strategy = (
        "Vincular el siniestro a la garantía exacta; verificar vigencia, límites, "
        "franquicia, exclusiones, aviso y asistencia; exigir decisión motivada; "
        "cuantificar solo prestaciones acreditadas y evitar duplicidades."
    )
    if _present(solution):
        strategy += f" La solución solicitada es: {_display(solution)}."

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="seguro_viaje",
        specialist="travel.insurance",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=f"Incidencia de seguro de viaje ({incident}) en la póliza {_display(policy)}." if _present(policy) else "Posible incidencia de seguro de viaje.",
        client_goal="Obtener una decisión motivada y la prestación cubierta sin reclamar partidas no acreditadas ni duplicar importes.",
        primary_strategy=strategy,
        secondary_strategies=[
            "Reclamar al proveedor de viaje cuando exista una obligación propia.",
            "Valorar la vía financiera tras completar la reclamación previa.",
            "Preservar la vía judicial y pericial en controversias de causalidad o cuantificación.",
        ],
        requested_outcomes=[
            "Confirmación de la garantía y periodo de cobertura.",
            "Identificación de la cláusula aplicada.",
            "Explicación motivada de hechos, cobertura y exclusión.",
            "Pago de la prestación o importe mínimo indiscutido.",
            "Reintegro de gastos necesarios y documentados.",
            "Desglose de límite, franquicia, reducciones y pagos.",
        ],
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record, regime),
        risks=list(dict.fromkeys([
            "Una póliza puede reunir garantías de daños y de personas con reglas distintas.",
            "La causa del viaje no acredita por sí sola la cobertura.",
            "La falta de autorización o el aviso tardío exigen análisis contextual.",
            "La vía supervisora puede no resolver prueba médica, pericial o de daños.",
            "Los reembolsos de proveedor, tarjeta y aseguradora deben coordinarse.",
            *list(regime.warnings),
        ])),
        destination=str(insurer).strip() if _present(insurer) else "ASEGURADORA PENDIENTE DE VALIDAR",
        document_type="RECLAMACIÓN EXTRAJUDICIAL A ASEGURADORA DE VIAJE",
        subject=" — ".join(subject_parts),
        legal_arguments=arguments,
        additional_requests=[
            "Póliza completa y certificado.",
            "IPID y prueba de entrega.",
            "Justificante de prima y vigencia.",
            "Cuestionario de riesgo cuando se invoque una preexistencia.",
            "Parte de siniestro y expediente.",
            "Historial con aseguradora y asistencia.",
            "Facturas e informes estrictamente necesarios.",
            "Decisión íntegra y cláusula exacta invocada.",
            "Cálculo de límite, franquicia, reducción e importe pagado.",
            "Reclamación al servicio de atención y respuesta.",
        ],
        created_by_component=(
            "travel.insurance:"
            f"{TRAVEL_INSURANCE_SPECIALIST_VERSION}+"
            f"{TRAVEL_INSURANCE_REGIME_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
