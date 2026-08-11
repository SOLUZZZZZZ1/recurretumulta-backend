"""Especialista RTM para reclamaciones de seguros generales.

Construye una Previa Jurídica conservadora desde hechos congelados. Separa
póliza, ramo, vigencia, siniestro, cláusulas, cuestionario, prima, peritación,
cuantías, pagos y vías de reclamación. No decide cobertura, beneficiario,
negligencia grave, mora ni intereses y deriva fuera los seguros de viaje,
productos de inversión y daños corporales de circulación.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import HTTPException

from rtm_core.authority_repository import (
    FamilyResolutionRecord,
    ValidatedFactsRecord,
)
from rtm_core.claims_insurance_regime import (
    CLAIMS_INSURANCE_REGIME_VERSION,
    ClaimsInsuranceRegimeDecision,
    resolve_claims_insurance_regime,
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


CLAIMS_INSURANCE_SPECIALIST_VERSION = "rtm_claims_insurance_specialist_v1_0"
NOTICE_REFERENCE_DAYS = 7
MINIMUM_PAYMENT_REFERENCE_DAYS = 40
ORDINARY_PERFORMANCE_REFERENCE_DAYS = 92
CUSTOMER_SERVICE_WAIT_DAYS = 30

RouteState = Literal["insurer", "insurer_period_review", "financial_route_review"]


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


def _all_text(record: ValidatedFactsRecord) -> str:
    values: list[Any] = []
    for key in (
        "descripcion_hecho",
        "incidencia_seguro_tipo",
        "ramo_seguro",
        "naturaleza_cobertura_seguro",
        "coberturas_seguro",
        "exclusion_invocada_seguro",
        "motivo_rechazo_seguro",
        "decision_aseguradora_seguro",
        "respuesta_documentada",
        "respuesta_sac_seguro",
        "solucion_solicitada_seguro",
        "solucion_solicitada",
        "tratamiento_seguro",
    ):
        value, _ = validated_value(record, key)
        if _present(value):
            values.append(value)
    return _fold(values)


def _regime(record: ValidatedFactsRecord) -> ClaimsInsuranceRegimeDecision:
    policy_date, _ = validated_value(
        record,
        "fecha_contratacion_poliza",
        "fecha_emision_poliza_seguro",
        "fecha_documento",
    )
    coverage_start, _ = validated_value(record, "fecha_inicio_cobertura_seguro")
    coverage_end, _ = validated_value(record, "fecha_fin_cobertura_seguro")
    loss_date, _ = validated_value(
        record,
        "fecha_siniestro_seguro",
        "fecha_incidencia",
        "fecha_conocimiento_siniestro_seguro",
    )
    insurer_country, _ = validated_value(record, "pais_aseguradora_general")
    product_type, _ = validated_value(record, "ramo_seguro")
    coverage_nature, _ = validated_value(record, "naturaleza_cobertura_seguro")
    policy_coverages, _ = validated_value(record, "coberturas_seguro")
    incident_type, _ = validated_value(record, "incidencia_seguro_tipo")
    sac_date, _ = validated_value(
        record,
        "reclamacion_sac_seguro_fecha",
        "reclamacion_previa_fecha",
    )
    distributor, _ = validated_value(
        record,
        "mediador_seguro",
        "tipo_mediador_seguro",
    )
    harmed_third_party, _ = validated_value(
        record,
        "reclamacion_directa_tercero",
        "tercero_perjudicado_seguro",
    )
    travel, _ = validated_value(record, "seguro_viaje_implicado")
    motor, _ = validated_value(record, "accidente_trafico_terceros_implicado")
    investment, _ = validated_value(record, "producto_inversion_seguro_implicado")
    pension, _ = validated_value(record, "plan_pensiones_implicado")
    return resolve_claims_insurance_regime(
        policy_date=policy_date,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        loss_date=loss_date,
        insurer_country=insurer_country,
        product_type=product_type,
        coverage_nature=coverage_nature,
        policy_coverages=policy_coverages,
        incident_type=incident_type,
        issue_text=_all_text(record),
        sac_complaint_date=sac_date,
        insurance_distributor=distributor,
        harmed_third_party=harmed_third_party,
        travel_insurance=travel,
        motor_third_party_injury=motor,
        investment_linked=investment,
        pension_plan=pension,
    )


def _route_state(record: ValidatedFactsRecord) -> RouteState:
    prior_claim, _ = validated_value(
        record,
        "reclamacion_sac_seguro_fecha",
        "reclamacion_previa_fecha",
    )
    response, _ = validated_value(
        record,
        "respuesta_sac_seguro",
        "respuesta_documentada",
        "decision_aseguradora_seguro",
    )
    response_date, _ = validated_value(
        record,
        "respuesta_sac_seguro_fecha",
        "fecha_decision_aseguradora",
    )
    if not _present(prior_claim):
        return "insurer"
    if _present(response) or _present(response_date):
        return "financial_route_review"
    return "insurer_period_review"


def _required_missing(
    record: ValidatedFactsRecord,
    regime: ClaimsInsuranceRegimeDecision,
) -> list[MissingItem]:
    groups: list[tuple[str, str, tuple[str, ...]]] = [
        (
            "insurance_fact_missing",
            "Falta validar la incidencia concreta del seguro.",
            ("descripcion_hecho", "incidencia_seguro_tipo"),
        ),
        (
            "insurance_insurer_missing",
            "Falta identificar a la aseguradora.",
            ("aseguradora_general", "proveedor", "emisor_documento"),
        ),
        (
            "insurance_policy_reference_missing",
            "Falta la referencia de la póliza.",
            ("poliza_seguro_ref", "poliza_ref", "contrato_ref"),
        ),
        (
            "insurance_policyholder_or_insured_missing",
            "Falta identificar al tomador o asegurado.",
            ("tomador_seguro_general", "asegurado_seguro_general"),
        ),
        (
            "insurance_product_type_missing",
            "Falta identificar el ramo del seguro.",
            ("ramo_seguro",),
        ),
        (
            "insurance_coverage_nature_missing",
            "Falta separar si la cobertura es de daños o de personas.",
            ("naturaleza_cobertura_seguro", "coberturas_seguro"),
        ),
        (
            "insurance_policy_date_missing",
            "Falta la fecha de contratación o emisión de la póliza.",
            (
                "fecha_contratacion_poliza",
                "fecha_emision_poliza_seguro",
                "fecha_documento",
            ),
        ),
        (
            "insurance_coverage_start_missing",
            "Falta el inicio de cobertura.",
            ("fecha_inicio_cobertura_seguro",),
        ),
        (
            "insurance_coverage_end_missing",
            "Falta el fin de cobertura.",
            ("fecha_fin_cobertura_seguro",),
        ),
        (
            "insurance_loss_date_missing",
            "Falta la fecha del siniestro.",
            (
                "fecha_siniestro_seguro",
                "fecha_incidencia",
                "fecha_conocimiento_siniestro_seguro",
            ),
        ),
        (
            "insurance_notice_date_missing",
            "Falta la fecha de comunicación del siniestro.",
            ("fecha_comunicacion_siniestro_seguro",),
        ),
        (
            "insurance_country_missing",
            "Falta el país de la aseguradora.",
            ("pais_aseguradora_general",),
        ),
        (
            "insurance_policy_coverages_missing",
            "Faltan las garantías aplicables de la póliza.",
            ("coberturas_seguro",),
        ),
        (
            "insurance_claim_status_missing",
            "Falta la decisión o estado del siniestro.",
            (
                "decision_aseguradora_seguro",
                "respuesta_documentada",
                "respuesta_sac_seguro",
            ),
        ),
        (
            "insurance_requested_solution_missing",
            "Falta la solución solicitada.",
            ("solucion_solicitada_seguro", "solucion_solicitada"),
        ),
    ]

    if regime.incident_type == "coverage_denial":
        groups.extend(
            [
                (
                    "insurance_denial_reason_missing",
                    "Falta el motivo completo del rechazo.",
                    ("motivo_rechazo_seguro", "decision_aseguradora_seguro"),
                ),
                (
                    "insurance_invoked_exclusion_missing",
                    "Falta la cláusula o exclusión concreta invocada.",
                    ("exclusion_invocada_seguro",),
                ),
                (
                    "insurance_decision_date_missing",
                    "Falta la fecha de decisión de la aseguradora.",
                    ("fecha_decision_aseguradora", "respuesta_sac_seguro_fecha"),
                ),
            ]
        )
    elif regime.incident_type == "valuation_or_underpayment":
        groups.extend(
            [
                (
                    "insurance_adjustment_evidence_missing",
                    "Falta el informe o resultado pericial.",
                    (
                        "informe_pericial_aportado",
                        "fecha_peritacion_seguro",
                        "importe_dano_peritado_eur",
                    ),
                ),
                (
                    "insurance_valuation_amounts_missing",
                    "Faltan las cuantías reclamada y ofertada o pagada.",
                    (
                        "importe_reclamado_seguro_eur",
                        "importe_reclamado_eur",
                        "importe_ofertado_aseguradora_eur",
                        "importe_pagado_seguro_general_eur",
                    ),
                ),
            ]
        )
    elif regime.incident_type == "handling_or_payment_delay":
        groups.extend(
            [
                (
                    "insurance_complete_file_date_missing",
                    "Falta la fecha de expediente completo.",
                    (
                        "fecha_documentacion_completa_seguro",
                        "fecha_comunicacion_siniestro_seguro",
                    ),
                ),
                (
                    "insurance_pending_amount_missing",
                    "Falta la cuantía pendiente o el importe mínimo pagado.",
                    (
                        "importe_reclamado_seguro_eur",
                        "importe_reclamado_eur",
                        "importe_minimo_pagado_eur",
                    ),
                ),
            ]
        )
    elif regime.incident_type == "premium_or_suspension":
        groups.extend(
            [
                (
                    "insurance_premium_sequence_missing",
                    "Falta identificar si se trata de primera prima o prima sucesiva.",
                    ("prima_tipo",),
                ),
                (
                    "insurance_premium_dates_missing",
                    "Faltan vencimiento y estado de pago de la prima.",
                    ("fecha_vencimiento_prima", "prima_pagada"),
                ),
            ]
        )
    elif regime.incident_type == "nonrenewal_or_modification":
        groups.append(
            (
                "insurance_renewal_or_change_dates_missing",
                "Faltan la fecha de renovación y el aviso de oposición o modificación.",
                (
                    "renovacion_poliza_fecha",
                    "oposicion_prorroga_tomador_fecha",
                    "oposicion_prorroga_asegurador_fecha",
                    "modificacion_poliza_aviso_fecha",
                ),
            )
        )
    elif regime.incident_type == "health_authorization":
        groups.extend(
            [
                (
                    "insurance_health_treatment_missing",
                    "Falta identificar el tratamiento o prestación sanitaria.",
                    ("tratamiento_seguro",),
                ),
                (
                    "insurance_health_authorization_status_missing",
                    "Falta el estado de la autorización sanitaria.",
                    (
                        "autorizacion_medica_solicitada",
                        "autorizacion_medica_denegada",
                    ),
                ),
            ]
        )
    elif regime.incident_type == "life_or_beneficiary":
        groups.extend(
            [
                (
                    "insurance_life_event_missing",
                    "Falta acreditar el evento cubierto de vida.",
                    ("fallecimiento_asegurado", "fecha_fallecimiento_asegurado"),
                ),
                (
                    "insurance_beneficiary_evidence_missing",
                    "Falta la designación o identificación documental del beneficiario.",
                    (
                        "designacion_beneficiario_aportada",
                        "beneficiario_seguro_general",
                    ),
                ),
            ]
        )
    elif regime.incident_type == "third_party_liability":
        groups.extend(
            [
                (
                    "insurance_third_party_identity_missing",
                    "Falta identificar al tercero perjudicado.",
                    ("tercero_perjudicado_seguro",),
                ),
                (
                    "insurance_third_party_damage_missing",
                    "Falta acreditar el daño del tercero.",
                    ("danos_tercero_eur", "descripcion_hecho"),
                ),
            ]
        )
    elif regime.incident_type == "concurrent_insurance":
        groups.extend(
            [
                (
                    "insurance_concurrent_insurer_missing",
                    "Falta identificar la otra aseguradora.",
                    ("otra_aseguradora",),
                ),
                (
                    "insurance_concurrent_payment_missing",
                    "Falta el importe pagado o reclamado en la otra póliza.",
                    (
                        "importe_pagado_otra_aseguradora_eur",
                        "importe_recuperado_terceros_seguro_eur",
                    ),
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
    regime: ClaimsInsuranceRegimeDecision,
    route: RouteState,
) -> list[MissingItem]:
    result: list[MissingItem] = []

    if regime.status != "current":
        result.append(
            missing_item(
                "insurance_regime_review",
                regime.blocking_reason
                or "Debe determinarse el régimen jurídico aplicable.",
                MissingItemSeverity.BLOCKING,
            )
        )

    if regime.coverage_nature == "mixed":
        result.append(
            missing_item(
                "insurance_mixed_coverages_split_required",
                (
                    "Deben separarse las coberturas de daños y de personas, sus "
                    "límites, exclusiones, cuantías y plazos."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    elif regime.coverage_nature == "unknown":
        result.append(
            missing_item(
                "insurance_coverage_nature_review",
                "Debe determinarse si cada garantía es de daños o de personas.",
                MissingItemSeverity.BLOCKING,
            )
        )

    policy = _parse_date(
        validated_value(
            record,
            "fecha_contratacion_poliza",
            "fecha_emision_poliza_seguro",
            "fecha_documento",
        )[0]
    )
    start = _parse_date(validated_value(record, "fecha_inicio_cobertura_seguro")[0])
    end = _parse_date(validated_value(record, "fecha_fin_cobertura_seguro")[0])
    loss = _parse_date(
        validated_value(
            record,
            "fecha_siniestro_seguro",
            "fecha_incidencia",
            "fecha_conocimiento_siniestro_seguro",
        )[0]
    )
    notice = _parse_date(
        validated_value(record, "fecha_comunicacion_siniestro_seguro")[0]
    )
    if start and end and end < start:
        result.append(
            missing_item(
                "insurance_coverage_chronology_conflict",
                "El fin de cobertura aparece anterior al inicio.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if policy and loss and policy > loss:
        result.append(
            missing_item(
                "insurance_policy_after_loss_conflict",
                "La póliza aparece contratada después del siniestro.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if loss and notice:
        delay = (notice - loss).days
        if delay < 0:
            result.append(
                missing_item(
                    "insurance_notice_before_loss_conflict",
                    "La comunicación aparece anterior al siniestro.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        elif delay > NOTICE_REFERENCE_DAYS:
            result.append(
                missing_item(
                    "insurance_late_notice_effect_review",
                    (
                        "La comunicación supera siete días; debe revisarse el "
                        "perjuicio alegado sin presumir pérdida automática del derecho."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )

    exclusion, _ = validated_value(record, "exclusion_invocada_seguro")
    highlighted, _ = validated_value(record, "clausula_limitativa_destacada")
    accepted, _ = validated_value(record, "clausula_limitativa_aceptada")
    if regime.incident_type == "coverage_denial" or _present(exclusion):
        if not _present(exclusion):
            result.append(
                missing_item(
                    "insurance_exact_exclusion_text_required",
                    "Debe aportarse el texto exacto de la cláusula aplicada.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        if highlighted is not True or accepted is not True:
            result.append(
                missing_item(
                    "insurance_limiting_clause_acceptance_review",
                    (
                        "Debe comprobarse el destacado y la aceptación específica "
                        "de la cláusula limitativa, sin presumir validez o nulidad."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )

    inaccuracy, _ = validated_value(record, "inexactitud_riesgo_invocada")
    preexisting, _ = validated_value(record, "preexistencia_salud_invocada")
    questionnaire, _ = validated_value(
        record,
        "cuestionario_riesgo_aportado_seguro",
    )
    relevant_question, _ = validated_value(
        record,
        "pregunta_riesgo_relevante_formulada",
    )
    if inaccuracy is True or preexisting is True:
        if questionnaire is not True or relevant_question is not True:
            result.append(
                missing_item(
                    "insurance_risk_questionnaire_and_question_required",
                    (
                        "Faltan el cuestionario y la pregunta concreta sobre el "
                        "riesgo que la aseguradora considera omitido."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )

    fraud_or_gross_fault, _ = validated_value(record, "dolo_culpa_grave_invocado")
    if fraud_or_gross_fault is True:
        result.append(
            missing_item(
                "insurance_fraud_or_gross_fault_evidence_review",
                (
                    "La alegación de dolo o culpa grave exige hechos y prueba "
                    "específicos; no basta una etiqueta interna."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    premium_due = _parse_date(validated_value(record, "fecha_vencimiento_prima")[0])
    premium_paid_date = _parse_date(validated_value(record, "fecha_pago_prima")[0])
    premium_paid, _ = validated_value(record, "prima_pagada")
    suspension = _parse_date(
        validated_value(record, "fecha_suspension_cobertura_invocada")[0]
    )
    reactivation = _parse_date(
        validated_value(record, "fecha_reactivacion_cobertura")[0]
    )
    if premium_due and premium_paid_date and premium_paid_date < premium_due:
        result.append(
            missing_item(
                "insurance_premium_payment_chronology_review",
                "El pago figura anterior al vencimiento; debe verificarse anticipo o fecha.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if premium_due and suspension and suspension < premium_due:
        result.append(
            missing_item(
                "insurance_suspension_before_premium_due_conflict",
                "La suspensión aparece anterior al vencimiento de la prima.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if suspension and reactivation and reactivation < suspension:
        result.append(
            missing_item(
                "insurance_reactivation_before_suspension_conflict",
                "La reactivación aparece anterior a la suspensión.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if regime.incident_type == "premium_or_suspension":
        if premium_paid is False and not (premium_due and suspension):
            result.append(
                missing_item(
                    "insurance_premium_suspension_dates_review",
                    (
                        "Debe reconstruirse la secuencia de recibo, vencimiento, "
                        "primera o sucesiva prima, suspensión y eventual reactivación."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
        if loss and suspension and loss >= suspension:
            result.append(
                missing_item(
                    "insurance_loss_during_invoked_suspension_review",
                    (
                        "El siniestro aparece durante una suspensión invocada; debe "
                        "verificarse íntegramente la eficacia de esa suspensión."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )

    renewal = _parse_date(validated_value(record, "renovacion_poliza_fecha")[0])
    holder_notice = _parse_date(
        validated_value(record, "oposicion_prorroga_tomador_fecha")[0]
    )
    insurer_notice = _parse_date(
        validated_value(record, "oposicion_prorroga_asegurador_fecha")[0]
    )
    change_notice = _parse_date(
        validated_value(record, "modificacion_poliza_aviso_fecha")[0]
    )
    if regime.incident_type == "nonrenewal_or_modification" and renewal:
        if holder_notice:
            days = (renewal - holder_notice).days
            if days < 28:
                result.append(
                    missing_item(
                        "insurance_policyholder_nonrenewal_notice_short",
                        "La oposición del tomador parece inferior a un mes.",
                        MissingItemSeverity.BLOCKING,
                    )
                )
            else:
                result.append(
                    missing_item(
                        "insurance_policyholder_calendar_month_review",
                        "Debe comprobarse por calendario el mes mínimo del tomador.",
                        MissingItemSeverity.HUMAN_REVIEW,
                    )
                )
        if insurer_notice:
            days = (renewal - insurer_notice).days
            if days < 56:
                result.append(
                    missing_item(
                        "insurance_insurer_nonrenewal_notice_short",
                        "La oposición del asegurador parece inferior a dos meses.",
                        MissingItemSeverity.BLOCKING,
                    )
                )
            else:
                result.append(
                    missing_item(
                        "insurance_insurer_calendar_months_review",
                        "Deben comprobarse por calendario los dos meses del asegurador.",
                        MissingItemSeverity.HUMAN_REVIEW,
                    )
                )
        if change_notice:
            days = (renewal - change_notice).days
            if days < 56:
                result.append(
                    missing_item(
                        "insurance_policy_change_notice_short",
                        "La modificación parece comunicada con menos de dos meses.",
                        MissingItemSeverity.BLOCKING,
                    )
                )
            else:
                result.append(
                    missing_item(
                        "insurance_policy_change_calendar_review",
                        "Deben comprobarse por calendario los dos meses del cambio.",
                        MissingItemSeverity.HUMAN_REVIEW,
                    )
                )

    claimed = _amount(
        validated_value(
            record,
            "importe_reclamado_seguro_eur",
            "importe_reclamado_eur",
        )[0]
    )
    offered = _amount(validated_value(record, "importe_ofertado_aseguradora_eur")[0])
    minimum_paid = _amount(validated_value(record, "importe_minimo_pagado_eur")[0])
    paid = _amount(validated_value(record, "importe_pagado_seguro_general_eur")[0])
    limit_amount = _amount(
        validated_value(
            record,
            "limite_cobertura_seguro_eur",
            "suma_asegurada_eur",
        )[0]
    )
    damage = _amount(validated_value(record, "importe_dano_peritado_eur")[0])
    third_party_recovery = _amount(
        validated_value(record, "importe_recuperado_terceros_seguro_eur")[0]
    )
    other_insurer_paid = _amount(
        validated_value(record, "importe_pagado_otra_aseguradora_eur")[0]
    )
    if claimed is not None and claimed < 0:
        result.append(
            missing_item(
                "insurance_negative_claim_amount",
                "La cuantía reclamada es negativa.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if offered is not None and claimed is not None and offered > claimed + 0.01:
        result.append(
            missing_item(
                "insurance_offer_exceeds_claim_review",
                "La oferta supera la cuantía reclamada; debe revisarse su composición.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    for code, label, value in (
        ("insurance_minimum_payment_exceeds_claim", "pago mínimo", minimum_paid),
        ("insurance_payment_exceeds_claim", "pago total", paid),
    ):
        if value is not None and claimed is not None and value > claimed + 0.01:
            result.append(
                missing_item(
                    code,
                    f"El {label} supera la cuantía reclamada.",
                    MissingItemSeverity.BLOCKING,
                )
            )
    if paid is not None and limit_amount is not None and paid > limit_amount + 0.01:
        result.append(
            missing_item(
                "insurance_payment_exceeds_documented_limit",
                "El pago supera el límite documental de cobertura.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if damage is not None and limit_amount is not None and damage > limit_amount + 0.01:
        result.append(
            missing_item(
                "insurance_damage_above_limit_review",
                "El daño peritado supera el límite de cobertura y debe desglosarse.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    recovered_total = sum(
        value or 0.0
        for value in (paid, third_party_recovery, other_insurer_paid)
    )
    if (
        claimed is not None
        and recovered_total > claimed + 0.01
        and regime.coverage_nature == "damage"
    ):
        result.append(
            missing_item(
                "insurance_double_recovery_amount_conflict",
                (
                    "Los pagos y recuperaciones documentados superan el perjuicio "
                    "reclamado en una cobertura de daños."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    concurrent, _ = validated_value(record, "seguro_concurrente")
    if concurrent is True or (other_insurer_paid is not None and other_insurer_paid > 0):
        result.append(
            missing_item(
                "insurance_concurrent_coverage_coordination_review",
                (
                    "Deben coordinarse las pólizas concurrentes, comunicaciones y "
                    "pagos para evitar una doble recuperación del mismo daño."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    if third_party_recovery is not None and third_party_recovery > 0:
        result.append(
            missing_item(
                "insurance_third_party_recovery_coordination_review",
                "Debe descontarse o coordinarse lo ya recuperado de terceros.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    discrepancy, _ = validated_value(record, "discrepancia_pericial")
    report, _ = validated_value(record, "informe_pericial_aportado")
    if discrepancy is True:
        result.append(
            missing_item(
                "insurance_adjustment_dispute_review",
                (
                    "La discrepancia pericial exige comparar informes, partidas, "
                    "criterio de valoración, franquicia y límites."
                ),
                MissingItemSeverity.BLOCKING if report is not True else MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    authorization_denied, _ = validated_value(record, "autorizacion_medica_denegada")
    if authorization_denied is True:
        result.append(
            missing_item(
                "insurance_health_medical_necessity_and_coverage_review",
                (
                    "La denegación sanitaria exige revisar indicación, cobertura, "
                    "autorización y urgencia sin resolver criterios clínicos automáticamente."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    beneficiary_designation, _ = validated_value(
        record,
        "designacion_beneficiario_aportada",
    )
    if regime.incident_type == "life_or_beneficiary" and beneficiary_designation is not True:
        result.append(
            missing_item(
                "insurance_beneficiary_designation_review",
                (
                    "No puede seleccionarse beneficiario sin póliza, designación, "
                    "revocaciones y documentación sucesoria aplicable."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    direct_action, _ = validated_value(record, "reclamacion_directa_tercero")
    if direct_action is True or regime.direct_action_layer:
        result.append(
            missing_item(
                "insurance_direct_action_scope_review",
                (
                    "Debe separar acción directa, responsabilidad del asegurado, "
                    "cobertura, excepciones oponibles y cuantía del tercero."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    prior_claim, _ = validated_value(
        record,
        "reclamacion_sac_seguro_fecha",
        "reclamacion_previa_fecha",
    )
    claim_reference, _ = validated_value(
        record,
        "reclamacion_sac_seguro_ref",
        "referencia_documento",
        "expediente_ref",
    )
    if route == "insurer":
        result.append(
            missing_item(
                "insurance_prior_sac_claim_required",
                (
                    "Debe presentarse reclamación previa al servicio de atención de "
                    "la aseguradora y conservar contenido, fecha y referencia."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    elif route == "insurer_period_review":
        result.append(
            missing_item(
                "insurance_sac_response_period_review",
                (
                    "Consta reclamación previa sin respuesta; debe verificarse el "
                    "mes de espera aplicable sin convertirlo automáticamente en treinta días."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    else:
        result.append(
            missing_item(
                "insurance_financial_complaint_eligibility_review",
                (
                    "Debe comprobarse la admisibilidad, competencia y documentación "
                    "de la reclamación financiera posterior."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if _present(prior_claim) and not _present(claim_reference):
        result.append(
            missing_item(
                "insurance_sac_claim_reference_missing",
                "Falta el justificante o referencia de la reclamación previa.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    return dedupe_missing(result)


def _utc(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _deadlines(
    record: ValidatedFactsRecord,
    regime: ClaimsInsuranceRegimeDecision,
) -> list[Deadline]:
    result: list[Deadline] = []
    loss_value, loss_key = validated_value(
        record,
        "fecha_siniestro_seguro",
        "fecha_incidencia",
        "fecha_conocimiento_siniestro_seguro",
    )
    notice_value, notice_key = validated_value(
        record,
        "fecha_comunicacion_siniestro_seguro",
    )
    loss = _parse_date(loss_value)
    notice = _parse_date(notice_value)
    if loss is not None and loss_key:
        result.extend(
            [
                Deadline(
                    label="Comunicación inicial del siniestro",
                    due_at=_utc(loss + timedelta(days=NOTICE_REFERENCE_DAYS)),
                    calculation_status="estimated",
                    source_fact_keys=[loss_key],
                    notes=[
                        "Referencia general de siete días, salvo plazo contractual más amplio.",
                        "La superación no implica pérdida automática del derecho.",
                    ],
                ),
                Deadline(
                    label="Cumplimiento ordinario de la prestación aseguradora",
                    due_at=_utc(
                        loss + timedelta(days=ORDINARY_PERFORMANCE_REFERENCE_DAYS)
                    ),
                    calculation_status="estimated",
                    source_fact_keys=[loss_key],
                    notes=[
                        "Aproximación operativa a tres meses.",
                        "No determina automáticamente mora ni intereses.",
                    ],
                ),
            ]
        )
    if notice is not None and notice_key:
        result.append(
            Deadline(
                label="Pago del importe mínimo conocido",
                due_at=_utc(notice + timedelta(days=MINIMUM_PAYMENT_REFERENCE_DAYS)),
                calculation_status="estimated",
                source_fact_keys=[notice_key],
                notes=[
                    "Referencia de cuarenta días desde la declaración del siniestro.",
                    "Debe determinarse el importe mínimo debido y la causa del retraso.",
                ],
            )
        )

    sac_value, sac_key = validated_value(
        record,
        "reclamacion_sac_seguro_fecha",
        "reclamacion_previa_fecha",
    )
    sac = _parse_date(sac_value)
    if sac is not None and sac_key and regime.customer_service_wait_months == 1:
        result.append(
            Deadline(
                label="Espera previa para valorar reclamación financiera",
                due_at=_utc(sac + timedelta(days=CUSTOMER_SERVICE_WAIT_DAYS)),
                calculation_status="estimated",
                source_fact_keys=[sac_key],
                notes=[
                    "Referencia operativa; el plazo legal es un mes y debe comprobarse por calendario."
                ],
            )
        )

    result.append(
        Deadline(
            label="Prescripción de la acción derivada del seguro",
            due_at=None,
            calculation_status="unresolved",
            source_fact_keys=[loss_key] if loss_key else [],
            notes=[
                (
                    f"La cobertura apunta a {regime.limitation_years} años; debe fijarse el dies a quo y las interrupciones."
                    if regime.limitation_years is not None
                    else (
                        "Debe separar coberturas de daños y de personas antes de "
                        "seleccionar dos o cinco años."
                    )
                )
            ],
        )
    )
    return result


def _summary(
    record: ValidatedFactsRecord,
    regime: ClaimsInsuranceRegimeDecision,
) -> tuple[list[str], list[str]]:
    rows, used = summary_rows(
        record,
        (
            ("aseguradora_general", "Aseguradora", ""),
            ("poliza_seguro_ref", "Póliza", ""),
            ("siniestro_seguro_ref", "Siniestro", ""),
            ("ramo_seguro", "Ramo", ""),
            ("naturaleza_cobertura_seguro", "Naturaleza", ""),
            ("tomador_seguro_general", "Tomador", ""),
            ("asegurado_seguro_general", "Asegurado", ""),
            ("fecha_inicio_cobertura_seguro", "Inicio de cobertura", ""),
            ("fecha_fin_cobertura_seguro", "Fin de cobertura", ""),
            ("fecha_siniestro_seguro", "Fecha del siniestro", ""),
            ("fecha_comunicacion_siniestro_seguro", "Comunicación", ""),
            ("decision_aseguradora_seguro", "Decisión", ""),
            ("exclusion_invocada_seguro", "Exclusión", ""),
            ("suma_asegurada_eur", "Suma asegurada", " EUR"),
            ("limite_cobertura_seguro_eur", "Límite", " EUR"),
            ("franquicia_seguro_eur", "Franquicia", " EUR"),
            ("importe_reclamado_seguro_eur", "Importe reclamado", " EUR"),
            ("importe_ofertado_aseguradora_eur", "Oferta", " EUR"),
            ("importe_pagado_seguro_general_eur", "Pagado", " EUR"),
        ),
    )
    rows.insert(
        0,
        (
            f"Encuadre de seguro: {regime.product_type}; naturaleza "
            f"{regime.coverage_nature}; incidencia {regime.incident_type}; "
            f"régimen {regime.status}."
        ),
    )
    return list(dict.fromkeys(rows)), list(dict.fromkeys(used))


def build_claims_insurance_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="claims",
        family="seguros",
        specialist="claims.insurance",
    )

    regime = _regime(facts_record)
    route = _route_state(facts_record)
    basis = list(regime.legal_basis) if regime.status == "current" else []

    insurer, insurer_key = validated_value(
        facts_record,
        "aseguradora_general",
        "proveedor",
        "emisor_documento",
    )
    policy, policy_key = validated_value(
        facts_record,
        "poliza_seguro_ref",
        "poliza_ref",
        "contrato_ref",
    )
    claim_ref, claim_key = validated_value(
        facts_record,
        "siniestro_seguro_ref",
        "siniestro_ref",
        "referencia_documento",
    )
    solution, solution_key = validated_value(
        facts_record,
        "solucion_solicitada_seguro",
        "solucion_solicitada",
    )
    _, fact_key = validated_value(
        facts_record,
        "descripcion_hecho",
        "incidencia_seguro_tipo",
    )

    summary, summary_keys = _summary(facts_record, regime)
    arguments = []

    def add(
        code: str,
        title: str,
        body: str,
        keys: tuple[Optional[str], ...],
        priority: str = "secondary",
    ) -> None:
        sources = validated_source_keys(facts_record, keys)
        if not sources:
            return
        arguments.append(
            legal_argument(
                facts_record,
                code=code,
                title=title,
                body=body,
                source_fact_keys=sources,
                priority=priority,
                legal_basis=basis,
            )
        )

    add(
        "insurance_policy_scope_and_vigency",
        "Póliza, ramo, garantías y vigencia",
        (
            "La obligación debe determinarse mediante la póliza completa, ramo, "
            "riesgo, periodo, límites, franquicia, recibos y condiciones. La "
            "denominación comercial no acredita por sí sola la cobertura."
        ),
        (
            policy_key,
            "ramo_seguro",
            "naturaleza_cobertura_seguro",
            "coberturas_seguro",
            "fecha_contratacion_poliza",
            "fecha_inicio_cobertura_seguro",
            "fecha_fin_cobertura_seguro",
            "suma_asegurada_eur",
            "limite_cobertura_seguro_eur",
            "franquicia_seguro_eur",
            fact_key,
        ),
        "primary",
    )
    add(
        "insurance_loss_notice_and_investigation",
        "Siniestro, comunicación e investigación",
        (
            "Debe acreditarse el hecho generador, su fecha, comunicación, medidas "
            "de mitigación, documentación aportada e investigación. Un aviso tardío "
            "no equivale automáticamente a pérdida del derecho."
        ),
        (
            claim_key,
            "fecha_siniestro_seguro",
            "fecha_conocimiento_siniestro_seguro",
            "fecha_comunicacion_siniestro_seguro",
            "fecha_documentacion_completa_seguro",
            "decision_aseguradora_seguro",
            fact_key,
        ),
        "primary",
    )
    add(
        "insurance_exclusions_questionnaire_and_risk",
        "Exclusiones, cláusulas y declaración del riesgo",
        (
            "El rechazo debe identificar la cláusula exacta y comprobar claridad, "
            "destacado y aceptación. Si se invoca inexactitud o preexistencia, deben "
            "aportarse el cuestionario y la pregunta concreta formulada."
        ),
        (
            "exclusion_invocada_seguro",
            "clausula_limitativa_destacada",
            "clausula_limitativa_aceptada",
            "motivo_rechazo_seguro",
            "cuestionario_riesgo_aportado_seguro",
            "pregunta_riesgo_relevante_formulada",
            "inexactitud_riesgo_invocada",
            "preexistencia_salud_invocada",
            "dolo_culpa_grave_invocado",
            "agravacion_riesgo_invocada",
            fact_key,
        ),
        "primary",
    )
    add(
        "insurance_premium_suspension_and_reactivation",
        "Prima, suspensión y reactivación",
        (
            "La eficacia de una suspensión exige separar primera y sucesivas primas, "
            "vencimiento, pago, fecha de suspensión, resolución y reactivación. No "
            "se presume ausencia de cobertura solo por constar un recibo pendiente."
        ),
        (
            "prima_tipo",
            "fecha_vencimiento_prima",
            "fecha_pago_prima",
            "prima_pagada",
            "fecha_suspension_cobertura_invocada",
            "fecha_reactivacion_cobertura",
            "fecha_siniestro_seguro",
            fact_key,
        ),
        "primary",
    )
    add(
        "insurance_adjustment_quantification_payment_and_default",
        "Peritación, cuantificación, pago y demora",
        (
            "Deben compararse daño, interés asegurado, suma, límite, franquicia, "
            "informes periciales, oferta, importe mínimo y pagos. Los cuarenta días "
            "y tres meses no permiten fijar intereses automáticamente."
        ),
        (
            "fecha_peritacion_seguro",
            "perito_aseguradora",
            "perito_asegurado",
            "informe_pericial_aportado",
            "discrepancia_pericial",
            "valor_interes_asegurado_eur",
            "importe_dano_peritado_eur",
            "importe_reclamado_seguro_eur",
            "importe_reclamado_eur",
            "importe_ofertado_aseguradora_eur",
            "importe_minimo_pagado_eur",
            "importe_pagado_seguro_general_eur",
            "fecha_pago_seguro",
            solution_key,
            fact_key,
        ),
        "primary",
    )
    add(
        "insurance_health_life_and_liability_specifics",
        "Salud, vida y responsabilidad civil",
        (
            "Las prestaciones de salud, vida y responsabilidad requieren separar "
            "indicación y autorización, evento cubierto y beneficiario, o culpa, "
            "tercero, acción directa y daño. El especialista no resuelve criterios "
            "clínicos ni selecciona beneficiarios sin documentación."
        ),
        (
            "autorizacion_medica_solicitada",
            "autorizacion_medica_denegada",
            "tratamiento_seguro",
            "gasto_sanitario_seguro_eur",
            "fallecimiento_asegurado",
            "fecha_fallecimiento_asegurado",
            "beneficiario_seguro_general",
            "designacion_beneficiario_aportada",
            "capital_vida_eur",
            "responsabilidad_civil_implicada",
            "reclamacion_directa_tercero",
            "tercero_perjudicado_seguro",
            "culpa_responsabilidad_discutida",
            "danos_tercero_eur",
            fact_key,
        ),
    )
    add(
        "insurance_renewal_and_contract_change",
        "Prórroga, oposición y modificación contractual",
        (
            "La no renovación y el cambio contractual deben reconstruirse por "
            "calendario desde la renovación: un mes para la oposición del tomador "
            "y dos meses para el asegurador o para comunicar modificaciones."
        ),
        (
            "renovacion_poliza_fecha",
            "oposicion_prorroga_tomador_fecha",
            "oposicion_prorroga_asegurador_fecha",
            "modificacion_poliza_aviso_fecha",
            fact_key,
        ),
    )
    add(
        "insurance_distribution_and_complaint_route",
        "Distribución y reclamación financiera",
        (
            "Cuando intervino un mediador o banco deben revisarse sus obligaciones "
            "propias. La vía financiera posterior exige reclamación previa, espera, "
            "respuesta o silencio y comprobación de competencia y admisibilidad."
        ),
        (
            "mediador_seguro",
            "tipo_mediador_seguro",
            "seguro_distribuido_banco",
            "reclamacion_sac_seguro_fecha",
            "reclamacion_sac_seguro_ref",
            "respuesta_sac_seguro_fecha",
            "respuesta_sac_seguro",
            "reclamacion_previa_fecha",
            solution_key,
            fact_key,
        ),
    )
    add(
        "insurance_concurrence_and_no_double_recovery",
        "Concurrencia y ausencia de doble recuperación",
        (
            "La petición debe separar daño, prestación personal, pagos del "
            "asegurador, otras pólizas y responsables. En seguros de daños no cabe "
            "superar el perjuicio mediante una doble recuperación."
        ),
        (
            "seguro_concurrente",
            "otra_aseguradora",
            "importe_pagado_otra_aseguradora_eur",
            "importe_recuperado_terceros_seguro_eur",
            "importe_pagado_seguro_general_eur",
            "importe_reclamado_seguro_eur",
            "importe_reclamado_eur",
            solution_key,
            fact_key,
        ),
        "primary",
    )

    if not arguments:
        raise HTTPException(
            status_code=409,
            detail=(
                "No existen hechos validados suficientes para construir la previa "
                "de seguro."
            ),
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
            *_required_missing(facts_record, regime),
            *_review_missing(facts_record, regime, route),
            *fact_review_items(facts_record, prefix="insurance"),
        ]
    )

    destination = (
        str(insurer).strip()
        if _present(insurer)
        else "ASEGURADORA PENDIENTE DE VALIDAR"
    )
    document_type = "RECLAMACIÓN EXTRAJUDICIAL A ASEGURADORA"
    if route == "financial_route_review":
        document_type = (
            "RECLAMACIÓN FINANCIERA SOBRE SEGURO — ADMISIBILIDAD PENDIENTE"
        )

    subject_parts = ["RECLAMACIÓN DE SEGURO", regime.incident_type.upper()]
    if _present(policy):
        subject_parts.append(f"póliza {policy}")
    if _present(claim_ref):
        subject_parts.append(f"siniestro {claim_ref}")

    strategy = (
        "Vincular el siniestro a la garantía exacta; verificar vigencia, prima, "
        "límites, franquicia, cláusulas, cuestionario y peritación; exigir una "
        "decisión motivada y cuantificar solo prestaciones acreditadas."
    )
    if _present(solution):
        strategy += f" La solución solicitada es: {_display(solution)}."

    requested_outcomes = [
        "Confirmación de la póliza, garantía y periodo de cobertura.",
        "Identificación de la cláusula y hechos aplicados.",
        "Decisión motivada sobre cobertura, exclusiones y cuantía.",
        "Entrega del expediente y documentación pericial necesaria.",
        "Pago de la prestación o del importe mínimo indiscutido.",
        "Desglose de suma, límite, franquicia, reducción, oferta y pagos.",
        "Coordinación de otras pólizas y cantidades ya recuperadas.",
    ]

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="seguros",
        specialist="claims.insurance",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Incidencia de seguro ({regime.incident_type}) en la póliza {_display(policy)}."
            if _present(policy)
            else "Posible incidencia de seguro pendiente de completar."
        ),
        client_goal=(
            "Obtener una decisión motivada y la prestación cubierta sin reclamar "
            "partidas no acreditadas ni duplicar recuperaciones."
        ),
        primary_strategy=strategy,
        secondary_strategies=[
            "Solicitar la póliza, cuestionario, recibos y expediente íntegro.",
            "Promover contraste pericial cuando la controversia sea de valoración.",
            "Valorar la vía financiera tras completar la reclamación previa.",
            "Preservar acciones frente al responsable o terceros sin duplicar cobros.",
        ],
        requested_outcomes=requested_outcomes,
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record, regime),
        risks=list(
            dict.fromkeys(
                [
                    "Una póliza puede reunir garantías de daños y de personas con reglas y plazos distintos.",
                    "La existencia del siniestro no acredita por sí sola la cobertura.",
                    "La aceptación de una cláusula y el cuestionario deben probarse documentalmente.",
                    "Los intereses y la mora requieren revisar fechas, importe mínimo y causa del retraso.",
                    "Las recuperaciones de aseguradoras y terceros deben coordinarse.",
                    "La vía financiera puede no resolver controversias médicas, periciales o probatorias complejas.",
                    *list(regime.warnings),
                ]
            )
        ),
        destination=destination,
        document_type=document_type,
        subject=" — ".join(subject_parts),
        legal_arguments=arguments,
        additional_requests=[
            "Póliza completa, condiciones generales y particulares.",
            "Propuesta, certificado, suplementos y prueba de entrega.",
            "Cuestionario de riesgo y preguntas formuladas.",
            "Recibos, vencimientos, pagos y comunicaciones sobre prima.",
            "Parte de siniestro y relación cronológica del expediente.",
            "Informes periciales, fotografías, presupuestos y facturas necesarias.",
            "Decisión íntegra y cláusula exacta invocada.",
            "Cálculo de suma, límite, franquicia, reducción, oferta e importe mínimo.",
            "Pagos o recuperaciones de otras pólizas y terceros.",
            "Reclamación al servicio de atención y respuesta o justificante de silencio.",
        ],
        created_by_component=(
            "claims.insurance:"
            f"{CLAIMS_INSURANCE_SPECIALIST_VERSION}+"
            f"{CLAIMS_INSURANCE_REGIME_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
