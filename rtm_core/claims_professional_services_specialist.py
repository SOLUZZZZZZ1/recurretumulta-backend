"""Especialista RTM para reclamaciones de servicios profesionales.

Construye una Previa Jurídica conservadora desde hechos congelados. Separa
encargo, alcance, obligación de medios o resultado, precio, ejecución, defectos,
subsanación, cancelación, desistimiento, daños, causalidad, reclamación previa y
vías colegiales o ADR. No declara negligencia, pérdida de oportunidad,
prescripción, cuantía indemnizatoria ni competencia sectorial.
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
from rtm_core.claims_professional_services_regime import (
    CLAIMS_PROFESSIONAL_SERVICES_REGIME_VERSION,
    ClaimsProfessionalServicesRegimeDecision,
    resolve_claims_professional_services_regime,
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


CLAIMS_PROFESSIONAL_SERVICES_SPECIALIST_VERSION = (
    "rtm_claims_professional_services_specialist_v1_0"
)

RouteState = Literal[
    "professional",
    "professional_period_review",
    "consumer_route_review",
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


def _all_text(record: ValidatedFactsRecord) -> str:
    values: list[Any] = []
    for key in (
        "descripcion_hecho",
        "incidencia_servicio_profesional_tipo",
        "profesional_tipo",
        "objeto_encargo_profesional",
        "alcance_encargo_profesional",
        "entregables_pactados_profesional",
        "incumplimiento_profesional_descripcion",
        "respuesta_profesional",
        "respuesta_documentada",
        "solucion_solicitada_profesional",
        "solucion_solicitada",
    ):
        value, _ = validated_value(record, key)
        if _present(value):
            values.append(value)
    return _fold(values)


def _regime(record: ValidatedFactsRecord) -> ClaimsProfessionalServicesRegimeDecision:
    contract_date, _ = validated_value(
        record,
        "fecha_encargo_profesional",
        "fecha_documento",
    )
    service_start, _ = validated_value(record, "fecha_inicio_servicio_profesional")
    expected_end, _ = validated_value(
        record,
        "fecha_fin_prevista_servicio_profesional",
    )
    actual_end, _ = validated_value(record, "fecha_fin_real_servicio_profesional")
    breach_date, _ = validated_value(
        record,
        "fecha_incumplimiento_profesional",
        "fecha_incidencia",
    )
    complaint_date, _ = validated_value(
        record,
        "reclamacion_previa_profesional_fecha",
        "reclamacion_previa_fecha",
    )
    withdrawal_date, _ = validated_value(record, "fecha_desistimiento_profesional")
    client_country, _ = validated_value(record, "pais_cliente_servicio")
    provider_country, _ = validated_value(record, "pais_profesional")
    client_consumer, _ = validated_value(record, "cliente_servicio_es_consumidor")
    professional_type, _ = validated_value(record, "profesional_tipo")
    incident_type, _ = validated_value(record, "incidencia_servicio_profesional_tipo")
    obligation_type, _ = validated_value(
        record,
        "naturaleza_obligacion_profesional",
    )
    means_obligation, _ = validated_value(record, "obligacion_medios_pactada")
    result_obligation, _ = validated_value(record, "obligacion_resultado_pactada")
    distance, _ = validated_value(record, "contrato_distancia_servicio_profesional")
    off_premises, _ = validated_value(
        record,
        "contrato_fuera_establecimiento_profesional",
    )
    home_visit, _ = validated_value(
        record,
        "visita_domicilio_no_solicitada_profesional",
    )
    excursion, _ = validated_value(record, "excursion_promocional_profesional")
    withdrawal_info, _ = validated_value(
        record,
        "informacion_desistimiento_profesional_entregada",
    )
    start_requested, _ = validated_value(
        record,
        "inicio_durante_desistimiento_solicitado",
    )
    start_consent, _ = validated_value(
        record,
        "consentimiento_inicio_servicio_profesional",
    )
    loss_ack, _ = validated_value(
        record,
        "conocimiento_perdida_desistimiento_profesional",
    )
    fully_performed, _ = validated_value(
        record,
        "servicio_profesional_completamente_ejecutado",
    )
    claim_nature, _ = validated_value(
        record,
        "reclamacion_naturaleza_juridica_documentada",
    )
    large_company, _ = validated_value(record, "empresa_profesional_gran_dimension")
    customer_service_applicable, _ = validated_value(
        record,
        "ley_atencion_clientela_profesional_aplicable",
    )

    return resolve_claims_professional_services_regime(
        contract_date=contract_date,
        service_start_date=service_start,
        expected_completion_date=expected_end,
        actual_completion_date=actual_end,
        breach_date=breach_date,
        complaint_date=complaint_date,
        withdrawal_notice_date=withdrawal_date,
        client_country=client_country,
        provider_country=provider_country,
        client_is_consumer=client_consumer,
        professional_type=professional_type,
        incident_type=incident_type,
        issue_text=_all_text(record),
        obligation_type=obligation_type,
        means_obligation=means_obligation,
        result_obligation=result_obligation,
        distance_contract=distance,
        off_premises_contract=off_premises,
        unsolicited_home_visit=home_visit,
        promotional_excursion=excursion,
        withdrawal_information_delivered=withdrawal_info,
        service_start_during_withdrawal_requested=start_requested,
        service_start_express_consent=start_consent,
        withdrawal_loss_acknowledged=loss_ack,
        service_fully_performed=fully_performed,
        claim_nature=claim_nature,
        large_company=large_company,
        customer_service_act_applicable=customer_service_applicable,
        legal_service=validated_value(
            record,
            "servicio_juridico_profesional_implicado",
        )[0],
        healthcare_service=validated_value(
            record,
            "servicio_sanitario_profesional_implicado",
        )[0],
        architecture_building_service=validated_value(
            record,
            "servicio_arquitectura_edificacion_implicado",
        )[0],
        tax_accounting_service=validated_value(
            record,
            "servicio_fiscal_contable_implicado",
        )[0],
        financial_investment_service=validated_value(
            record,
            "servicio_financiero_inversion_implicado",
        )[0],
        insurance_intermediation_service=validated_value(
            record,
            "servicio_seguro_intermediacion_implicado",
        )[0],
        public_administration_service=validated_value(
            record,
            "servicio_administracion_publica_implicado",
        )[0],
        employment_service=validated_value(
            record,
            "servicio_laboral_implicado",
        )[0],
        data_protection_primary=validated_value(
            record,
            "proteccion_datos_incidencia_principal",
        )[0],
        standardized_digital_content=validated_value(
            record,
            "contenido_digital_estandarizado_implicado",
        )[0],
        professional_fee_collection=validated_value(
            record,
            "reclamacion_honorarios_por_profesional",
        )[0],
    )


def _route_state(record: ValidatedFactsRecord) -> RouteState:
    prior_claim, _ = validated_value(
        record,
        "reclamacion_previa_profesional_fecha",
        "reclamacion_previa_fecha",
    )
    response, _ = validated_value(
        record,
        "respuesta_profesional",
        "respuesta_documentada",
    )
    response_date, _ = validated_value(record, "respuesta_profesional_fecha")
    if not _present(prior_claim):
        return "professional"
    if _present(response) or _present(response_date):
        return "consumer_route_review"
    return "professional_period_review"


def _required_missing(
    record: ValidatedFactsRecord,
    regime: ClaimsProfessionalServicesRegimeDecision,
) -> list[MissingItem]:
    groups: list[tuple[str, str, tuple[str, ...]]] = [
        (
            "professional_fact_missing",
            "Falta validar la incidencia concreta del servicio profesional.",
            ("descripcion_hecho", "incidencia_servicio_profesional_tipo"),
        ),
        (
            "professional_provider_missing",
            "Falta identificar al prestador profesional.",
            ("profesional_prestador", "proveedor", "emisor_documento"),
        ),
        (
            "professional_type_missing",
            "Falta identificar el tipo de actividad profesional.",
            ("profesional_tipo",),
        ),
        (
            "professional_consumer_status_missing",
            "Falta acreditar que el cliente actuó como consumidor.",
            ("cliente_servicio_es_consumidor",),
        ),
        (
            "professional_engagement_reference_missing",
            "Falta la referencia de la hoja de encargo, contrato o proyecto.",
            ("encargo_profesional_ref", "contrato_ref", "referencia_documento"),
        ),
        (
            "professional_contract_date_missing",
            "Falta la fecha de contratación o aceptación del encargo.",
            ("fecha_encargo_profesional", "fecha_documento"),
        ),
        (
            "professional_scope_missing",
            "Faltan el objeto y alcance documentados del encargo.",
            ("objeto_encargo_profesional", "alcance_encargo_profesional"),
        ),
        (
            "professional_price_basis_missing",
            "Falta el precio pactado o su base documental de cálculo.",
            (
                "precio_profesional_pactado_eur",
                "base_calculo_honorarios_profesional",
                "honorarios_hora_profesional_eur",
                "presupuesto_profesional_ref",
            ),
        ),
        (
            "professional_performance_status_missing",
            "Falta el estado de ejecución o la descripción del incumplimiento.",
            (
                "servicio_profesional_estado",
                "incumplimiento_profesional_descripcion",
                "servicio_profesional_no_prestado",
                "servicio_profesional_incompleto",
                "servicio_profesional_defectuoso",
                "servicio_profesional_retrasado",
            ),
        ),
        (
            "professional_requested_solution_missing",
            "Falta la solución solicitada al profesional.",
            ("solucion_solicitada_profesional", "solucion_solicitada"),
        ),
    ]

    if regime.incident_type == "nonperformance":
        groups.extend(
            [
                (
                    "professional_nonperformance_start_status_missing",
                    "Faltan inicio y estado del servicio no prestado.",
                    (
                        "fecha_inicio_servicio_profesional",
                        "servicio_profesional_no_prestado",
                    ),
                ),
                (
                    "professional_nonperformance_payment_missing",
                    "Falta el importe pagado o anticipado.",
                    ("importe_pagado_profesional_eur", "anticipo_profesional_eur"),
                ),
            ]
        )
    elif regime.incident_type == "defective_or_incomplete":
        groups.extend(
            [
                (
                    "professional_deliverables_missing",
                    "Faltan los entregables o hitos pactados.",
                    ("entregables_pactados_profesional", "hitos_pactados_profesional"),
                ),
                (
                    "professional_defect_description_missing",
                    "Falta describir el defecto o parte incompleta.",
                    ("incumplimiento_profesional_descripcion",),
                ),
                (
                    "professional_cure_status_missing",
                    "Falta el estado de la subsanación solicitada u ofrecida.",
                    (
                        "subsanacion_profesional_solicitada",
                        "subsanacion_profesional_ofrecida",
                        "subsanacion_profesional_completada",
                    ),
                ),
            ]
        )
    elif regime.incident_type == "delay":
        groups.extend(
            [
                (
                    "professional_expected_deadline_missing",
                    "Falta la fecha prevista o el plazo profesional pactado.",
                    ("fecha_fin_prevista_servicio_profesional", "hitos_pactados_profesional"),
                ),
                (
                    "professional_actual_completion_missing",
                    "Falta la fecha real de entrega o el estado actual.",
                    (
                        "fecha_fin_real_servicio_profesional",
                        "servicio_profesional_estado",
                    ),
                ),
            ]
        )
    elif regime.incident_type == "fees_or_unapproved_costs":
        groups.extend(
            [
                (
                    "professional_quote_or_fee_terms_missing",
                    "Faltan presupuesto, base de honorarios o aceptación.",
                    (
                        "presupuesto_profesional_ref",
                        "base_calculo_honorarios_profesional",
                        "precio_profesional_pactado_eur",
                    ),
                ),
                (
                    "professional_invoice_amount_missing",
                    "Falta la factura o cuantía profesional discutida.",
                    ("factura_profesional_ref", "importe_facturado_profesional_eur"),
                ),
            ]
        )
    elif regime.incident_type == "cancellation_or_refund":
        groups.extend(
            [
                (
                    "professional_cancellation_date_missing",
                    "Falta la fecha de cancelación del cliente o del profesional.",
                    (
                        "fecha_cancelacion_cliente_profesional",
                        "fecha_cancelacion_prestador_profesional",
                    ),
                ),
                (
                    "professional_cancellation_amounts_missing",
                    "Faltan anticipo, pago, penalización o reembolso.",
                    (
                        "anticipo_profesional_eur",
                        "importe_pagado_profesional_eur",
                        "penalizacion_cancelacion_profesional_eur",
                        "importe_reembolsado_profesional_eur",
                    ),
                ),
            ]
        )
    elif regime.incident_type == "withdrawal":
        groups.extend(
            [
                (
                    "professional_distance_status_missing",
                    "Falta acreditar la contratación a distancia o fuera de establecimiento.",
                    (
                        "contrato_distancia_servicio_profesional",
                        "contrato_fuera_establecimiento_profesional",
                    ),
                ),
                (
                    "professional_withdrawal_notice_missing",
                    "Faltan comunicación y fecha de desistimiento.",
                    (
                        "desistimiento_profesional_comunicado",
                        "fecha_desistimiento_profesional",
                    ),
                ),
                (
                    "professional_withdrawal_information_missing",
                    "Falta el estado de la información previa de desistimiento.",
                    ("informacion_desistimiento_profesional_entregada",),
                ),
                (
                    "professional_withdrawal_execution_status_missing",
                    "Falta la secuencia de inicio y ejecución durante el desistimiento.",
                    (
                        "inicio_durante_desistimiento_solicitado",
                        "consentimiento_inicio_servicio_profesional",
                        "servicio_profesional_completamente_ejecutado",
                    ),
                ),
            ]
        )
    elif regime.incident_type in {"damage_or_loss", "professional_negligence"}:
        groups.extend(
            [
                (
                    "professional_damage_evidence_missing",
                    "Falta identificar y probar el daño reclamado.",
                    (
                        "dano_directo_servicio_profesional_eur",
                        "prueba_dano_profesional_aportada",
                    ),
                ),
                (
                    "professional_causation_missing",
                    "Falta el nexo causal documentado.",
                    ("nexo_causal_profesional_documentado",),
                ),
            ]
        )
    elif regime.incident_type == "subcontracting":
        groups.extend(
            [
                (
                    "professional_subcontractor_missing",
                    "Falta identificar al subcontratista.",
                    ("subcontratista_profesional",),
                ),
                (
                    "professional_subcontracting_authorization_missing",
                    "Falta el estado de autorización de la subcontratación.",
                    ("subcontratacion_profesional_autorizada",),
                ),
            ]
        )

    result: list[MissingItem] = []
    for code, description, keys in groups:
        values = [validated_value(record, key)[0] for key in keys]
        if not any(_present(value) for value in values):
            result.append(missing_item(code, description))

    client_country, _ = validated_value(record, "pais_cliente_servicio")
    provider_country, _ = validated_value(record, "pais_profesional")
    if not _present(client_country) or not _present(provider_country):
        result.append(
            missing_item(
                "professional_country_missing",
                "Deben constar por separado los países documentales del cliente y del profesional.",
            )
        )

    return dedupe_missing(result)


def _review_missing(
    record: ValidatedFactsRecord,
    regime: ClaimsProfessionalServicesRegimeDecision,
    route: RouteState,
) -> list[MissingItem]:
    result: list[MissingItem] = []

    if regime.status != "current":
        result.append(
            missing_item(
                "professional_regime_review",
                regime.blocking_reason
                or "Debe determinarse el régimen jurídico aplicable.",
                MissingItemSeverity.BLOCKING,
            )
        )

    if regime.obligation_type == "unknown":
        result.append(
            missing_item(
                "professional_obligation_type_review",
                (
                    "Debe determinarse desde el encargo si la obligación era de "
                    "medios, de resultado o mixta; no puede inferirse por la profesión."
                ),
                (
                    MissingItemSeverity.BLOCKING
                    if regime.incident_type
                    in {"defective_or_incomplete", "delay", "professional_negligence"}
                    else MissingItemSeverity.HUMAN_REVIEW
                ),
            )
        )

    contract = _parse_date(
        validated_value(
            record,
            "fecha_encargo_profesional",
            "fecha_documento",
        )[0]
    )
    start = _parse_date(validated_value(record, "fecha_inicio_servicio_profesional")[0])
    expected = _parse_date(
        validated_value(record, "fecha_fin_prevista_servicio_profesional")[0]
    )
    actual = _parse_date(
        validated_value(record, "fecha_fin_real_servicio_profesional")[0]
    )
    breach = _parse_date(
        validated_value(
            record,
            "fecha_incumplimiento_profesional",
            "fecha_incidencia",
        )[0]
    )
    cure_request = _parse_date(
        validated_value(record, "fecha_solicitud_subsanacion_profesional")[0]
    )
    cure_completion = _parse_date(
        validated_value(record, "fecha_subsanacion_profesional")[0]
    )
    if contract and start and start < contract:
        result.append(
            missing_item(
                "professional_start_before_contract_conflict",
                "El inicio aparece anterior al encargo.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if start and expected and expected < start:
        result.append(
            missing_item(
                "professional_expected_end_before_start_conflict",
                "La fecha prevista de finalización es anterior al inicio.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if start and actual and actual < start:
        result.append(
            missing_item(
                "professional_actual_end_before_start_conflict",
                "La finalización real es anterior al inicio.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if contract and breach and breach < contract:
        result.append(
            missing_item(
                "professional_breach_before_contract_conflict",
                "El incumplimiento aparece anterior al contrato.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if cure_request and cure_completion and cure_completion < cure_request:
        result.append(
            missing_item(
                "professional_cure_chronology_conflict",
                "La subsanación aparece completada antes de solicitarse.",
                MissingItemSeverity.BLOCKING,
            )
        )

    agreed = _amount(validated_value(record, "precio_profesional_pactado_eur")[0])
    authorized_extras = _amount(
        validated_value(record, "gastos_adicionales_autorizados_eur")[0]
    )
    billed_extras = _amount(
        validated_value(record, "gastos_adicionales_facturados_eur")[0]
    )
    invoiced = _amount(validated_value(record, "importe_facturado_profesional_eur")[0])
    paid = _amount(validated_value(record, "importe_pagado_profesional_eur")[0])
    refunded = _amount(validated_value(record, "importe_reembolsado_profesional_eur")[0])
    advance = _amount(validated_value(record, "anticipo_profesional_eur")[0])
    cancellation_penalty = _amount(
        validated_value(record, "penalizacion_cancelacion_profesional_eur")[0]
    )
    proportionate = _amount(
        validated_value(record, "importe_proporcional_servicio_eur")[0]
    )
    for code, label, value in (
        ("professional_negative_agreed_price", "precio pactado", agreed),
        ("professional_negative_invoice", "importe facturado", invoiced),
        ("professional_negative_payment", "importe pagado", paid),
        ("professional_negative_refund", "reembolso", refunded),
        ("professional_negative_advance", "anticipo", advance),
    ):
        if value is not None and value < 0:
            result.append(
                missing_item(
                    code,
                    f"El {label} no puede ser negativo.",
                    MissingItemSeverity.BLOCKING,
                )
            )
    if billed_extras is not None and authorized_extras is not None:
        if billed_extras > authorized_extras + 0.01:
            result.append(
                missing_item(
                    "professional_billed_extras_exceed_authorization",
                    "Los gastos adicionales facturados superan los autorizados.",
                    MissingItemSeverity.BLOCKING,
                )
            )
    elif billed_extras is not None and billed_extras > 0:
        result.append(
            missing_item(
                "professional_extra_expenses_authorization_review",
                "Deben acreditarse la necesidad, información y autorización de los gastos adicionales.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if invoiced is not None and agreed is not None:
        ceiling = agreed + (authorized_extras or 0.0)
        if invoiced > ceiling + 0.01:
            result.append(
                missing_item(
                    "professional_invoice_exceeds_agreed_price",
                    (
                        "La factura supera el precio pactado más los gastos "
                        "adicionales documentados y autorizados."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
    if refunded is not None and paid is not None and refunded > paid + 0.01:
        result.append(
            missing_item(
                "professional_refund_exceeds_payment",
                "El reembolso documentado supera el importe pagado.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if cancellation_penalty is not None and cancellation_penalty > 0:
        clause, _ = validated_value(
            record,
            "clausula_cancelacion_profesional_aportada",
        )
        if clause is not True:
            result.append(
                missing_item(
                    "professional_cancellation_clause_required",
                    "La penalización exige aportar la cláusula y su aceptación.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        if paid is not None and cancellation_penalty > paid + 0.01:
            result.append(
                missing_item(
                    "professional_cancellation_penalty_exceeds_payment_review",
                    "La penalización supera lo pagado y debe desglosarse.",
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )

    withdrawal = _parse_date(validated_value(record, "fecha_desistimiento_profesional")[0])
    withdrawal_notified, _ = validated_value(
        record,
        "desistimiento_profesional_comunicado",
    )
    withdrawal_info, _ = validated_value(
        record,
        "informacion_desistimiento_profesional_entregada",
    )
    start_requested, _ = validated_value(
        record,
        "inicio_durante_desistimiento_solicitado",
    )
    start_consent, _ = validated_value(
        record,
        "consentimiento_inicio_servicio_profesional",
    )
    loss_ack, _ = validated_value(
        record,
        "conocimiento_perdida_desistimiento_profesional",
    )
    fully_performed, _ = validated_value(
        record,
        "servicio_profesional_completamente_ejecutado",
    )
    completion_percentage = _amount(
        validated_value(record, "porcentaje_servicio_profesional_ejecutado")[0]
    )
    if regime.incident_type == "withdrawal":
        if not regime.withdrawal_layer:
            result.append(
                missing_item(
                    "professional_withdrawal_scope_review",
                    (
                        "No consta contratación a distancia o fuera del "
                        "establecimiento; no puede afirmarse un desistimiento legal."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
        if withdrawal_notified is True and withdrawal is None:
            result.append(
                missing_item(
                    "professional_withdrawal_date_required",
                    "Consta desistimiento, pero falta su fecha documental.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        if (
            contract
            and withdrawal
            and regime.withdrawal_days is not None
            and withdrawal_info is True
            and (withdrawal - contract).days > regime.withdrawal_days
        ):
            result.append(
                missing_item(
                    "professional_withdrawal_outside_ordinary_period",
                    (
                        "El desistimiento aparece fuera del periodo ordinario; "
                        "debe revisarse información, cómputo y cualquier ampliación."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
        if withdrawal_info is False:
            result.append(
                missing_item(
                    "professional_withdrawal_information_defect_calendar_review",
                    (
                        "Debe calcularse por calendario la ampliación por falta de "
                        "información, sin convertir doce meses en días fijos."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
        if fully_performed is True:
            if not (
                start_requested is True
                and start_consent is True
                and loss_ack is True
            ):
                result.append(
                    missing_item(
                        "professional_full_performance_withdrawal_requirements_missing",
                        (
                            "La ejecución completa no extingue el desistimiento sin "
                            "solicitud, consentimiento expreso y conocimiento de pérdida."
                        ),
                        MissingItemSeverity.BLOCKING,
                    )
                )
            else:
                result.append(
                    missing_item(
                        "professional_full_performance_withdrawal_loss_review",
                        (
                            "Constan los elementos de una posible pérdida del derecho, "
                            "pero deben verificarse soporte, momento y contenido exactos."
                        ),
                        MissingItemSeverity.HUMAN_REVIEW,
                    )
                )
        elif start_requested is True:
            if completion_percentage is None or proportionate is None:
                result.append(
                    missing_item(
                        "professional_proportionate_payment_elements_missing",
                        (
                            "La ejecución parcial exige porcentaje, alcance y cálculo "
                            "proporcional documentados."
                        ),
                        MissingItemSeverity.BLOCKING,
                    )
                )
        elif proportionate is not None and proportionate > 0:
            result.append(
                missing_item(
                    "professional_proportionate_charge_without_request",
                    (
                        "Se factura una parte ejecutada sin constar solicitud expresa "
                        "de inicio durante el desistimiento."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )

    direct_damage = _amount(
        validated_value(record, "dano_directo_servicio_profesional_eur")[0]
    )
    lost_profit = _amount(
        validated_value(record, "lucro_cesante_servicio_profesional_eur")[0]
    )
    moral_damage = _amount(
        validated_value(record, "dano_moral_servicio_profesional_eur")[0]
    )
    loss_of_chance, _ = validated_value(
        record,
        "perdida_oportunidad_profesional_invocada",
    )
    causation, _ = validated_value(record, "nexo_causal_profesional_documentado")
    damage_evidence, _ = validated_value(record, "prueba_dano_profesional_aportada")
    third_party_recovery = _amount(
        validated_value(record, "importe_recuperado_terceros_profesional_eur")[0]
    )
    insurance_payment = _amount(
        validated_value(record, "importe_pagado_seguro_profesional_eur")[0]
    )
    damage_total = sum(
        value or 0.0 for value in (direct_damage, lost_profit, moral_damage)
    )
    any_damage_claim = damage_total > 0 or loss_of_chance is True
    if any_damage_claim:
        if damage_evidence is not True:
            result.append(
                missing_item(
                    "professional_damage_proof_review",
                    "Las partidas de daño requieren prueba documental individualizada.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        if causation is not True:
            result.append(
                missing_item(
                    "professional_causation_review",
                    (
                        "Debe acreditarse el nexo causal entre la actuación profesional "
                        "y cada perjuicio reclamado."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
    if lost_profit is not None and lost_profit > 0:
        result.append(
            missing_item(
                "professional_lost_profit_quantification_review",
                "El lucro cesante exige base objetiva, probabilidad y cálculo verificable.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if moral_damage is not None and moral_damage > 0:
        result.append(
            missing_item(
                "professional_moral_damage_quantification_review",
                "El daño moral no puede fijarse automáticamente desde una cifra declarada.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if loss_of_chance is True:
        result.append(
            missing_item(
                "professional_loss_of_chance_specialist_review",
                (
                    "La pérdida de oportunidad exige reconstruir la alternativa, "
                    "probabilidad, causalidad y alcance indemnizable."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    recovered_total = (refunded or 0.0) + (third_party_recovery or 0.0) + (
        insurance_payment or 0.0
    )
    if recovered_total > 0:
        result.append(
            missing_item(
                "professional_recovery_coordination_review",
                (
                    "Deben coordinarse reembolso, seguro y pagos de terceros para "
                    "evitar duplicar la recuperación del mismo perjuicio."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if damage_total > 0 and recovered_total > damage_total + 0.01:
        result.append(
            missing_item(
                "professional_double_recovery_amount_conflict",
                "Las recuperaciones documentadas superan las partidas de daño cuantificadas.",
                MissingItemSeverity.BLOCKING,
            )
        )

    limitation_clause, _ = validated_value(
        record,
        "clausula_limitacion_responsabilidad_profesional",
    )
    negotiated, _ = validated_value(
        record,
        "clausula_limitacion_negociada_profesional",
    )
    if _present(limitation_clause) and negotiated is not True:
        result.append(
            missing_item(
                "professional_liability_limitation_clause_review",
                (
                    "Debe revisarse claridad, incorporación, negociación y posible "
                    "desequilibrio de la cláusula limitativa."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    subcontracting, _ = validated_value(record, "subcontratacion_profesional")
    subcontracting_authorized, _ = validated_value(
        record,
        "subcontratacion_profesional_autorizada",
    )
    if subcontracting is True and subcontracting_authorized is not True:
        result.append(
            missing_item(
                "professional_unauthorized_subcontracting_review",
                (
                    "Debe comprobarse si la identidad, cualificación y delegación del "
                    "subcontratista fueron informadas y autorizadas cuando procedía."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    college_claim, _ = validated_value(
        record,
        "queja_colegial_profesional_presentada",
    )
    if college_claim is True:
        result.append(
            missing_item(
                "professional_college_and_compensation_routes_separate",
                (
                    "La vía colegial disciplinaria debe separarse de la reclamación "
                    "contractual, económica o de responsabilidad."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    adr_entity, _ = validated_value(record, "entidad_adr_profesional")
    adr_bound, _ = validated_value(record, "profesional_adherido_adr")
    if _present(adr_entity) and adr_bound is not True:
        result.append(
            missing_item(
                "professional_adr_adherence_and_competence_review",
                "Debe acreditarse adhesión, obligación y competencia de la entidad ADR.",
                MissingItemSeverity.BLOCKING,
            )
        )

    if regime.customer_service_layer == "active":
        result.append(
            missing_item(
                "professional_customer_service_business_day_calendar_review",
                (
                    "El plazo de quince días hábiles requiere calendario aplicable; "
                    "no puede convertirse en quince días naturales."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    elif regime.customer_service_layer == "transition":
        result.append(
            missing_item(
                "professional_customer_service_transition_review",
                "Debe comprobarse el régimen transitorio de adaptación de la Ley 10/2025.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    prior_claim, _ = validated_value(
        record,
        "reclamacion_previa_profesional_fecha",
        "reclamacion_previa_fecha",
    )
    prior_reference, _ = validated_value(
        record,
        "reclamacion_previa_profesional_ref",
        "referencia_documento",
        "expediente_ref",
    )
    if route == "professional":
        result.append(
            missing_item(
                "professional_prior_claim_recommended",
                (
                    "Debe formularse reclamación previa completa al profesional y "
                    "conservar contenido, fecha y justificante."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    elif route == "professional_period_review":
        result.append(
            missing_item(
                "professional_response_period_and_next_route_review",
                (
                    "Consta reclamación sin respuesta; debe comprobarse el plazo "
                    "aplicable y la siguiente vía sin presumir silencio estimatorio."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    else:
        result.append(
            missing_item(
                "professional_consumer_or_adr_route_competence_review",
                (
                    "Debe comprobarse la competencia de consumo, arbitraje, entidad "
                    "ADR, colegio profesional o vía civil según la pretensión."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if _present(prior_claim) and not _present(prior_reference):
        result.append(
            missing_item(
                "professional_prior_claim_reference_missing",
                "Falta el justificante o referencia de la reclamación previa.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    court_related, _ = validated_value(
        record,
        "procedimiento_judicial_profesional_relacionado",
    )
    if court_related is True:
        result.append(
            missing_item(
                "professional_related_court_proceeding_review",
                (
                    "Existe un procedimiento judicial relacionado; deben revisarse "
                    "plazos, preclusión, representación y compatibilidad de vías."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    return dedupe_missing(result)


def _utc(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _deadlines(
    record: ValidatedFactsRecord,
    regime: ClaimsProfessionalServicesRegimeDecision,
) -> list[Deadline]:
    result: list[Deadline] = []
    contract_value, contract_key = validated_value(
        record,
        "fecha_encargo_profesional",
        "fecha_documento",
    )
    contract = _parse_date(contract_value)
    if (
        regime.status == "current"
        and regime.withdrawal_layer
        and regime.withdrawal_days is not None
        and regime.withdrawal_information_delivered is True
        and contract is not None
        and contract_key
    ):
        result.append(
            Deadline(
                label="Periodo ordinario de desistimiento del servicio",
                due_at=_utc(contract + timedelta(days=regime.withdrawal_days)),
                calculation_status="confirmed",
                source_fact_keys=[contract_key],
                notes=[
                    f"Cómputo de {regime.withdrawal_days} días naturales desde la celebración.",
                    "La aplicabilidad depende de la contratación a distancia o fuera de establecimiento y de sus excepciones.",
                ],
            )
        )

    withdrawal_value, withdrawal_key = validated_value(
        record,
        "fecha_desistimiento_profesional",
    )
    withdrawal = _parse_date(withdrawal_value)
    withdrawal_notified, _ = validated_value(
        record,
        "desistimiento_profesional_comunicado",
    )
    if (
        regime.status == "current"
        and regime.withdrawal_layer
        and withdrawal_notified is True
        and withdrawal is not None
        and withdrawal_key
    ):
        result.append(
            Deadline(
                label="Reembolso tras desistimiento comunicado",
                due_at=_utc(withdrawal + timedelta(days=14)),
                calculation_status="estimated",
                source_fact_keys=[withdrawal_key],
                notes=[
                    "Referencia de catorce días naturales desde la comunicación.",
                    "Debe descontarse únicamente el importe proporcional jurídicamente procedente, si lo hubiera.",
                ],
            )
        )
    return result


def _summary(
    record: ValidatedFactsRecord,
    regime: ClaimsProfessionalServicesRegimeDecision,
) -> tuple[list[str], list[str]]:
    rows, used = summary_rows(
        record,
        (
            ("profesional_prestador", "Profesional", ""),
            ("profesional_tipo", "Actividad", ""),
            ("encargo_profesional_ref", "Encargo", ""),
            ("fecha_encargo_profesional", "Fecha del encargo", ""),
            ("objeto_encargo_profesional", "Objeto", ""),
            ("alcance_encargo_profesional", "Alcance", ""),
            ("entregables_pactados_profesional", "Entregables", ""),
            ("fecha_inicio_servicio_profesional", "Inicio", ""),
            ("fecha_fin_prevista_servicio_profesional", "Fin previsto", ""),
            ("fecha_fin_real_servicio_profesional", "Fin real", ""),
            ("servicio_profesional_estado", "Estado", ""),
            ("incumplimiento_profesional_descripcion", "Incumplimiento", ""),
            ("precio_profesional_pactado_eur", "Precio pactado", " EUR"),
            ("importe_facturado_profesional_eur", "Facturado", " EUR"),
            ("importe_pagado_profesional_eur", "Pagado", " EUR"),
            ("importe_reembolsado_profesional_eur", "Reembolsado", " EUR"),
        ),
    )
    rows.insert(
        0,
        (
            f"Encuadre profesional: {regime.service_type}; obligación "
            f"{regime.obligation_type}; incidencia {regime.incident_type}; "
            f"régimen {regime.status}."
        ),
    )
    return list(dict.fromkeys(rows)), list(dict.fromkeys(used))


def build_claims_professional_services_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="claims",
        family="servicios_profesionales",
        specialist="claims.professional_services",
    )

    regime = _regime(facts_record)
    route = _route_state(facts_record)
    basis = list(regime.legal_basis) if regime.status == "current" else []

    provider, provider_key = validated_value(
        facts_record,
        "profesional_prestador",
        "proveedor",
        "emisor_documento",
    )
    engagement, engagement_key = validated_value(
        facts_record,
        "encargo_profesional_ref",
        "contrato_ref",
        "referencia_documento",
    )
    solution, solution_key = validated_value(
        facts_record,
        "solucion_solicitada_profesional",
        "solucion_solicitada",
    )
    _, fact_key = validated_value(
        facts_record,
        "descripcion_hecho",
        "incidencia_servicio_profesional_tipo",
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
        "professional_contract_scope_and_obligation",
        "Encargo, alcance y naturaleza de la obligación",
        (
            "La reclamación debe partir del contrato, hoja de encargo, oferta, "
            "alcance, entregables, hitos y plazo. La actividad profesional no "
            "permite presumir por sí sola una obligación de medios o de resultado."
        ),
        (
            engagement_key,
            "objeto_encargo_profesional",
            "alcance_encargo_profesional",
            "entregables_pactados_profesional",
            "hitos_pactados_profesional",
            "obligacion_medios_pactada",
            "obligacion_resultado_pactada",
            "resultado_garantizado_documentado",
            "plazo_esencial_documentado",
            fact_key,
        ),
        "primary",
    )
    add(
        "professional_information_price_and_invoice",
        "Información, presupuesto, precio y factura",
        (
            "Deben compararse información previa, presupuesto, aceptación, base de "
            "cálculo, impuestos, gastos autorizados, factura y pagos. La ausencia de "
            "precio cerrado no convierte el servicio en gratuito."
        ),
        (
            "presupuesto_profesional_ref",
            "presupuesto_profesional_aceptado",
            "precio_profesional_pactado_eur",
            "base_calculo_honorarios_profesional",
            "honorarios_hora_profesional_eur",
            "gastos_adicionales_autorizados_eur",
            "gastos_adicionales_facturados_eur",
            "factura_profesional_ref",
            "importe_facturado_profesional_eur",
            "importe_pagado_profesional_eur",
            "oferta_publicidad_profesional",
            fact_key,
        ),
        "primary",
    )
    add(
        "professional_performance_defects_delay_and_cure",
        "Ejecución, defectos, retraso y subsanación",
        (
            "Debe reconstruirse qué se ejecutó, entregó y aceptó, qué defecto o "
            "retraso se imputa, si el plazo era esencial y qué subsanación fue "
            "solicitada u ofrecida antes de resolver o cuantificar."
        ),
        (
            "fecha_inicio_servicio_profesional",
            "fecha_fin_prevista_servicio_profesional",
            "fecha_fin_real_servicio_profesional",
            "fecha_incumplimiento_profesional",
            "servicio_profesional_estado",
            "servicio_profesional_no_prestado",
            "servicio_profesional_incompleto",
            "servicio_profesional_defectuoso",
            "servicio_profesional_retrasado",
            "incumplimiento_profesional_descripcion",
            "trabajo_entregado_profesional",
            "trabajo_aceptado_cliente",
            "reservas_cliente_trabajo",
            "subsanacion_profesional_solicitada",
            "subsanacion_profesional_ofrecida",
            "subsanacion_profesional_completada",
            fact_key,
        ),
        "primary",
    )
    add(
        "professional_cancellation_withdrawal_and_refund",
        "Cancelación, desistimiento y reembolso",
        (
            "La cancelación contractual debe separarse del desistimiento legal. En "
            "servicios a distancia deben revisarse información, plazo, solicitud de "
            "inicio, ejecución completa y eventual importe proporcional."
        ),
        (
            "fecha_cancelacion_cliente_profesional",
            "fecha_cancelacion_prestador_profesional",
            "penalizacion_cancelacion_profesional_eur",
            "clausula_cancelacion_profesional_aportada",
            "contrato_distancia_servicio_profesional",
            "contrato_fuera_establecimiento_profesional",
            "visita_domicilio_no_solicitada_profesional",
            "informacion_desistimiento_profesional_entregada",
            "desistimiento_profesional_comunicado",
            "fecha_desistimiento_profesional",
            "inicio_durante_desistimiento_solicitado",
            "consentimiento_inicio_servicio_profesional",
            "conocimiento_perdida_desistimiento_profesional",
            "servicio_profesional_completamente_ejecutado",
            "porcentaje_servicio_profesional_ejecutado",
            "importe_proporcional_servicio_eur",
            "importe_reembolsado_profesional_eur",
            fact_key,
        ),
        "primary",
    )
    add(
        "professional_damage_causation_and_quantification",
        "Daño, causalidad y cuantificación",
        (
            "El incumplimiento no basta para indemnizar. Cada daño directo, lucro "
            "cesante, daño moral o pérdida de oportunidad exige prueba, causalidad, "
            "mitigación y cálculo, sin duplicar reembolsos, seguro o pagos de terceros."
        ),
        (
            "dano_directo_servicio_profesional_eur",
            "lucro_cesante_servicio_profesional_eur",
            "dano_moral_servicio_profesional_eur",
            "perdida_oportunidad_profesional_invocada",
            "nexo_causal_profesional_documentado",
            "prueba_dano_profesional_aportada",
            "importe_reembolsado_profesional_eur",
            "importe_recuperado_terceros_profesional_eur",
            "importe_pagado_seguro_profesional_eur",
            fact_key,
        ),
        "primary",
    )
    add(
        "professional_subcontracting_terms_and_liability",
        "Subcontratación y cláusulas de responsabilidad",
        (
            "Debe comprobarse quién ejecutó el encargo, su cualificación, información "
            "y autorización, así como la incorporación, negociación y equilibrio de "
            "cualquier cláusula que limite responsabilidad."
        ),
        (
            "subcontratacion_profesional",
            "subcontratacion_profesional_autorizada",
            "subcontratista_profesional",
            "clausula_limitacion_responsabilidad_profesional",
            "clausula_limitacion_negociada_profesional",
            fact_key,
        ),
    )
    add(
        "professional_prior_claim_college_and_adr_routes",
        "Reclamación previa, colegio profesional y ADR",
        (
            "La reclamación previa debe ser completa y trazable. La vía colegial, "
            "consumo, arbitraje, ADR y la vía civil tienen objetos y competencias "
            "distintos; no se presume adhesión ni efecto indemnizatorio."
        ),
        (
            "reclamacion_previa_profesional_fecha",
            "reclamacion_previa_profesional_ref",
            "respuesta_profesional_fecha",
            "respuesta_profesional",
            "profesional_colegiado",
            "colegio_profesional",
            "numero_colegiado_profesional",
            "queja_colegial_profesional_presentada",
            "expediente_colegial_profesional_ref",
            "entidad_adr_profesional",
            "profesional_adherido_adr",
            "fecha_reclamacion_adr_profesional",
            solution_key,
            fact_key,
        ),
    )
    add(
        "professional_limitation_and_sector_boundaries",
        "Prescripción y fronteras sectoriales",
        (
            "La calificación contractual o extracontractual, el dies a quo, las "
            "interrupciones y cualquier plazo sectorial deben verificarse antes de "
            "fijar vencimientos. Los servicios jurídicos, sanitarios, fiscales, "
            "financieros y de edificación requieren especialista propio."
        ),
        (
            "reclamacion_naturaleza_juridica_documentada",
            "fecha_conocimiento_dano_profesional",
            "fecha_interrupcion_prescripcion_profesional",
            "servicio_juridico_profesional_implicado",
            "servicio_sanitario_profesional_implicado",
            "servicio_arquitectura_edificacion_implicado",
            "servicio_fiscal_contable_implicado",
            "servicio_financiero_inversion_implicado",
            "servicio_seguro_intermediacion_implicado",
            "proteccion_datos_incidencia_principal",
            fact_key,
        ),
    )

    if not arguments:
        raise HTTPException(
            status_code=409,
            detail=(
                "No existen hechos validados suficientes para construir la previa "
                "de servicios profesionales."
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
            *fact_review_items(facts_record, prefix="professional"),
        ]
    )

    destination = (
        str(provider).strip()
        if _present(provider)
        else "PROFESIONAL PENDIENTE DE VALIDAR"
    )
    document_type = "RECLAMACIÓN EXTRAJUDICIAL POR SERVICIO PROFESIONAL"
    if route == "consumer_route_review":
        document_type = (
            "RECLAMACIÓN DE CONSUMO O ADR — COMPETENCIA PENDIENTE DE VALIDAR"
        )

    subject_parts = [
        "RECLAMACIÓN POR SERVICIO PROFESIONAL",
        regime.incident_type.upper(),
    ]
    if _present(engagement):
        subject_parts.append(f"encargo {engagement}")

    strategy = (
        "Fijar el encargo, alcance, naturaleza de la obligación, precio y cronología; "
        "comparar lo pactado con lo ejecutado; ofrecer subsanación cuando proceda; "
        "y reclamar únicamente reembolso o daños documentados y causalmente vinculados."
    )
    if _present(solution):
        strategy += f" La solución solicitada es: {_display(solution)}."

    requested_outcomes = [
        "Confirmación del contrato, alcance, entregables, hitos y plazo.",
        "Desglose del presupuesto, base de honorarios, factura y gastos adicionales.",
        "Explicación motivada del trabajo ejecutado, pendiente y defectuoso.",
        "Subsanación o finalización dentro de un plazo documentado cuando proceda.",
        "Resolución o cancelación con liquidación transparente de las prestaciones.",
        "Reembolso de cantidades no justificadas o no devengadas.",
        "Indemnización únicamente de daños acreditados, causales y no recuperados.",
        "Información sobre colegio, seguro profesional y entidad ADR competente.",
    ]

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="servicios_profesionales",
        specialist="claims.professional_services",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Incidencia de servicio profesional ({regime.incident_type}) en el encargo {_display(engagement)}."
            if _present(engagement)
            else "Posible incidencia de servicio profesional pendiente de completar."
        ),
        client_goal=(
            "Obtener cumplimiento, subsanación, resolución, reembolso o reparación "
            "del daño sin atribuir resultados, negligencia o cuantías no acreditadas."
        ),
        primary_strategy=strategy,
        secondary_strategies=[
            "Solicitar hoja de encargo, presupuesto, condiciones, factura y expediente íntegro.",
            "Requerir subsanación o entrega antes de resolver cuando resulte útil y procedente.",
            "Separar la queja colegial de la pretensión económica o indemnizatoria.",
            "Valorar consumo, ADR o vía civil solo después de verificar competencia y plazos.",
            "Preservar acciones frente a aseguradora o terceros sin duplicar recuperaciones.",
        ],
        requested_outcomes=requested_outcomes,
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record, regime),
        risks=list(
            dict.fromkeys(
                [
                    "La naturaleza de la obligación depende del encargo y no de una etiqueta profesional.",
                    "El resultado insatisfactorio no prueba por sí solo negligencia o incumplimiento.",
                    "Los honorarios pueden depender de tiempo, hitos, éxito, gastos y reglas sectoriales.",
                    "El desistimiento y la cancelación contractual son figuras distintas.",
                    "Daño, causalidad, lucro cesante, daño moral y pérdida de oportunidad requieren prueba separada.",
                    "La vía colegial puede ser disciplinaria y no conceder indemnización.",
                    "La competencia de consumo o ADR y la adhesión del profesional deben acreditarse.",
                    "Los plazos no pueden fijarse sin calificación jurídica, dies a quo e interrupciones.",
                    *list(regime.warnings),
                ]
            )
        ),
        destination=destination,
        document_type=document_type,
        subject=" — ".join(subject_parts),
        legal_arguments=arguments,
        additional_requests=[
            "Hoja de encargo, contrato y condiciones aplicables.",
            "Oferta, publicidad, presupuesto y prueba de aceptación.",
            "Alcance, entregables, hitos, cronograma y comunicaciones de cambio.",
            "Factura, base de honorarios, detalle de horas y gastos autorizados.",
            "Entregas, versiones, reservas del cliente y estado de subsanación.",
            "Comunicación de cancelación o desistimiento y prueba de recepción.",
            "Solicitud y consentimiento de inicio durante el desistimiento, si existieron.",
            "Prueba de cada daño, causalidad, mitigación y cuantificación.",
            "Pagos, reembolsos, seguro profesional y recuperaciones de terceros.",
            "Reclamación previa, respuesta, colegio profesional y entidad ADR invocada.",
        ],
        created_by_component=(
            "claims.professional_services:"
            f"{CLAIMS_PROFESSIONAL_SERVICES_SPECIALIST_VERSION}+"
            f"{CLAIMS_PROFESSIONAL_SERVICES_REGIME_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
