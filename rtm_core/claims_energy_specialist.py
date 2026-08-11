"""Especialista RTM para reclamaciones de energía y suministros.

Construye una Previa Jurídica conservadora para electricidad y gas. Consume
hechos congelados, separa comercializadora y distribuidora, no recalcula
facturas, no decide automáticamente la vulnerabilidad y no deriva a una
autoridad sin comprobar antes la reclamación previa y la competencia.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any, Literal, Optional

from fastapi import HTTPException

from rtm_core.authority_repository import (
    FamilyResolutionRecord,
    ValidatedFactsRecord,
)
from rtm_core.claims_energy_regime import (
    CLAIMS_ENERGY_REGIME_VERSION,
    ClaimsEnergyRegimeDecision,
    resolve_claims_energy_regime,
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


CLAIMS_ENERGY_SPECIALIST_VERSION = "rtm_claims_energy_specialist_v1_0"

RouteState = Literal["provider", "provider_period_review", "authority_review"]


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


def _number(value: Any) -> Optional[float]:
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
        "producto_servicio",
        "suministro_tipo",
        "incidencia_energia_tipo",
        "periodo_facturado",
        "respuesta_proveedor",
        "respuesta_documentada",
        "solucion_solicitada",
        "motivo_corte",
        "calidad_suministro",
        "mercado_energia",
    ):
        value, _ = validated_value(record, key)
        if _present(value):
            values.append(value)
    return _fold(values)


def _regime(record: ValidatedFactsRecord) -> ClaimsEnergyRegimeDecision:
    incident_date, _ = validated_value(
        record,
        "fecha_incidencia",
        "fecha_corte_suministro",
        "fecha_aplicacion_modificacion",
    )
    contract_date, _ = validated_value(record, "fecha_contrato")
    invoice_date, _ = validated_value(
        record,
        "fecha_factura_energia",
        "fecha_documento",
    )
    complaint_date, _ = validated_value(record, "reclamacion_previa_fecha")
    country, _ = validated_value(record, "pais_suministro")
    supply_type, _ = validated_value(record, "suministro_tipo")
    incident_type, _ = validated_value(record, "incidencia_energia_tipo")
    vulnerable, _ = validated_value(record, "consumidor_vulnerable")
    return resolve_claims_energy_regime(
        incident_date=incident_date,
        contract_date=contract_date,
        invoice_date=invoice_date,
        complaint_date=complaint_date,
        supply_country=country,
        supply_type=supply_type,
        incident_type=incident_type,
        issue_text=_all_text(record),
        vulnerable_consumer=vulnerable,
    )


def _route_state(record: ValidatedFactsRecord) -> RouteState:
    prior_claim, _ = validated_value(record, "reclamacion_previa_fecha")
    provider_response, _ = validated_value(
        record,
        "respuesta_proveedor",
        "respuesta_documentada",
    )
    response_date, _ = validated_value(record, "fecha_respuesta")

    if not _present(prior_claim):
        return "provider"
    if _present(provider_response) or _present(response_date):
        return "authority_review"
    return "provider_period_review"


def _required_missing(
    record: ValidatedFactsRecord,
    regime: ClaimsEnergyRegimeDecision,
) -> list[MissingItem]:
    groups: list[tuple[str, str, tuple[str, ...]]] = [
        (
            "energy_fact_missing",
            "Falta validar la incidencia concreta de energía o suministro.",
            ("descripcion_hecho",),
        ),
        (
            "energy_supplier_missing",
            "Falta identificar a la comercializadora o proveedor reclamado.",
            ("comercializadora_energia", "proveedor", "emisor_documento"),
        ),
        (
            "energy_supply_type_missing",
            "Falta validar si el suministro es electricidad o gas natural.",
            ("suministro_tipo",),
        ),
        (
            "energy_supply_country_missing",
            "Falta validar el país del suministro.",
            ("pais_suministro",),
        ),
        (
            "energy_supply_reference_missing",
            "Falta validar el CUPS, contrato o referencia del suministro.",
            ("cups", "contrato_ref", "referencia_servicio"),
        ),
        (
            "energy_incident_date_missing",
            "Falta una fecha documental de la incidencia, factura o contrato.",
            (
                "fecha_incidencia",
                "fecha_factura_energia",
                "fecha_documento",
                "fecha_contrato",
            ),
        ),
        (
            "energy_requested_solution_missing",
            "Falta validar la solución solicitada por el cliente.",
            ("solucion_solicitada",),
        ),
    ]

    if regime.incident_type in {"billing", "reading"}:
        groups.extend(
            [
                (
                    "energy_invoice_reference_missing",
                    "Falta el número o referencia de la factura discutida.",
                    ("factura_numero", "referencia_documento"),
                ),
                (
                    "energy_billing_period_missing",
                    "Falta el periodo de facturación.",
                    (
                        "periodo_facturado",
                        "periodo_facturacion_inicio",
                        "periodo_facturacion_fin",
                    ),
                ),
                (
                    "energy_invoice_amount_missing",
                    "Falta el importe de la factura o cuantía discutida.",
                    (
                        "importe_factura_energia_eur",
                        "importe_reclamado_eur",
                        "importe_regularizacion_eur",
                    ),
                ),
            ]
        )

    if regime.incident_type == "reading":
        groups.append(
            (
                "energy_meter_evidence_missing",
                "Faltan contador, lecturas o consumo documental.",
                (
                    "numero_contador",
                    "lectura_anterior",
                    "lectura_actual",
                    "consumo_facturado_kwh",
                ),
            )
        )

    if regime.incident_type == "contract_change":
        groups.extend(
            [
                (
                    "energy_contract_reference_missing",
                    "Falta el contrato o tarifa cuyas condiciones se modifican.",
                    ("contrato_ref", "tarifa_energia", "mercado_energia"),
                ),
                (
                    "energy_change_dates_missing",
                    "Faltan las fechas de aviso y aplicación de la modificación.",
                    (
                        "fecha_aviso_modificacion",
                        "fecha_aplicacion_modificacion",
                    ),
                ),
            ]
        )

    if regime.incident_type in {"unauthorized_switch", "unsolicited_service"}:
        groups.append(
            (
                "energy_consent_status_missing",
                "Falta validar si existe consentimiento contractual acreditado.",
                ("consentimiento_contratacion_acreditado",),
            )
        )

    if regime.incident_type == "suspension":
        groups.extend(
            [
                (
                    "energy_disconnection_status_missing",
                    "Falta validar el corte, aviso o fecha de suspensión.",
                    (
                        "corte_suministro",
                        "fecha_aviso_corte",
                        "fecha_corte_suministro",
                    ),
                ),
                (
                    "energy_disconnection_reason_missing",
                    "Falta validar la causa comunicada del corte.",
                    ("motivo_corte",),
                ),
            ]
        )

    if regime.incident_type == "vulnerable_protection":
        groups.extend(
            [
                (
                    "energy_vulnerability_status_missing",
                    "Falta validar la condición de consumidor vulnerable.",
                    ("consumidor_vulnerable", "bono_social"),
                ),
                (
                    "energy_habitual_residence_missing",
                    "Falta validar si se trata de la vivienda habitual.",
                    ("vivienda_habitual",),
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
    regime: ClaimsEnergyRegimeDecision,
    route: RouteState,
) -> list[MissingItem]:
    result: list[MissingItem] = []

    if regime.status != "current":
        result.append(
            missing_item(
                "energy_regime_review",
                regime.blocking_reason
                or "Debe determinarse el régimen jurídico aplicable.",
                MissingItemSeverity.BLOCKING,
            )
        )

    prior_claim, _ = validated_value(record, "reclamacion_previa_fecha")
    claim_channel, _ = validated_value(record, "canal_reclamacion")
    claim_reference, _ = validated_value(
        record,
        "reclamacion_energia_ref",
        "referencia_documento",
        "expediente_ref",
    )

    if route == "provider":
        result.append(
            missing_item(
                "energy_prior_supplier_claim_required",
                (
                    "Debe presentarse reclamación previa ante la empresa responsable "
                    "y conservar contenido, fecha, canal y referencia."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    elif route == "provider_period_review":
        detail = (
            "Consta reclamación previa sin respuesta. Debe verificarse el cómputo "
            "de quince días hábiles sin convertirlo en días naturales."
            if regime.complaint_response_business_days == 15
            else (
                "Consta reclamación previa sin respuesta. Debe comprobarse el plazo "
                "sectorial o general aplicable antes de escalar."
            )
        )
        result.append(
            missing_item(
                "energy_supplier_response_period_review",
                detail,
                MissingItemSeverity.BLOCKING,
            )
        )
    else:
        result.append(
            missing_item(
                "energy_authority_competence_review",
                (
                    "Debe comprobarse qué autoridad autonómica, organismo sectorial "
                    "o entidad de resolución alternativa es competente."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    if _present(prior_claim) and not _present(claim_channel):
        result.append(
            missing_item(
                "energy_claim_channel_missing",
                "Falta validar el canal de la reclamación previa.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if _present(prior_claim) and not _present(claim_reference):
        result.append(
            missing_item(
                "energy_claim_reference_missing",
                "Falta el número o justificante de la reclamación previa.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    retailer, _ = validated_value(
        record,
        "comercializadora_energia",
        "proveedor",
    )
    distributor, _ = validated_value(record, "distribuidora_energia")
    if regime.incident_type in {"reading", "outage_quality", "suspension"}:
        if not _present(distributor):
            result.append(
                missing_item(
                    "energy_distributor_role_review",
                    (
                        "La incidencia puede depender de la distribuidora; debe "
                        "identificarse y separarse su actuación de la comercializadora."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
    if not _present(retailer):
        result.append(
            missing_item(
                "energy_retailer_role_review",
                "Debe identificarse quién contrata y factura el suministro.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    period_start = _parse_date(
        validated_value(record, "periodo_facturacion_inicio")[0]
    )
    period_end = _parse_date(validated_value(record, "periodo_facturacion_fin")[0])
    if period_start and period_end and period_end < period_start:
        result.append(
            missing_item(
                "energy_billing_period_conflict",
                "El fin del periodo facturado aparece anterior al inicio.",
                MissingItemSeverity.BLOCKING,
            )
        )

    previous_date = _parse_date(
        validated_value(record, "fecha_lectura_anterior")[0]
    )
    current_date = _parse_date(validated_value(record, "fecha_lectura_actual")[0])
    previous_reading = _number(validated_value(record, "lectura_anterior")[0])
    current_reading = _number(validated_value(record, "lectura_actual")[0])
    billed_consumption = _number(
        validated_value(record, "consumo_facturado_kwh")[0]
    )
    if previous_date and current_date and current_date < previous_date:
        result.append(
            missing_item(
                "energy_reading_date_conflict",
                "La lectura actual aparece fechada antes que la anterior.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if (
        previous_reading is not None
        and current_reading is not None
        and current_reading < previous_reading
    ):
        result.append(
            missing_item(
                "energy_meter_rollover_or_reading_review",
                (
                    "La lectura actual es inferior a la anterior; debe revisarse "
                    "cambio de contador, reinicio, unidades o error."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if (
        previous_reading is not None
        and current_reading is not None
        and billed_consumption is not None
        and current_reading >= previous_reading
        and abs((current_reading - previous_reading) - billed_consumption) > 0.01
    ):
        result.append(
            missing_item(
                "energy_consumption_difference_review",
                (
                    "El consumo facturado no coincide con la diferencia simple de "
                    "lecturas; deben revisarse multiplicador, periodos y estimaciones."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    months = _number(validated_value(record, "meses_regularizados")[0])
    through_retailer, _ = validated_value(
        record,
        "acceso_red_a_traves_comercializadora",
    )
    if regime.supply_type == "electricity" and months is not None:
        if months > 12:
            result.append(
                missing_item(
                    "energy_regularization_over_twelve_months",
                    (
                        "La regularización supera doce meses y requiere revisión "
                        "bloqueante de la regla y del periodo."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
        elif months > 10 and through_retailer is True:
            result.append(
                missing_item(
                    "energy_regularization_over_ten_months_retailer_access",
                    (
                        "Consta acceso a través de comercializadora y una "
                        "regularización superior a diez meses."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
        elif months > 10 and through_retailer is not False:
            result.append(
                missing_item(
                    "energy_regularization_access_channel_review",
                    (
                        "La regularización supera diez meses; debe verificarse quién "
                        "contrató el acceso a la red antes de fijar el límite."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )

    invoice_amount = _number(
        validated_value(
            record,
            "importe_factura_energia_eur",
            "importe_reclamado_eur",
        )[0]
    )
    regularization_amount = _number(
        validated_value(record, "importe_regularizacion_eur")[0]
    )
    refund_amount = _number(
        validated_value(record, "importe_devuelto_energia_eur")[0]
    )
    if invoice_amount is not None and invoice_amount < 0:
        result.append(
            missing_item(
                "energy_negative_invoice_review",
                "La factura tiene importe negativo; debe verificarse si es abono.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if (
        regularization_amount is not None
        and regularization_amount < 0
        and (refund_amount is None or refund_amount <= 0)
    ):
        result.append(
            missing_item(
                "energy_overbilling_refund_traceability",
                (
                    "La regularización parece favorable al cliente, pero no consta "
                    "la devolución o compensación efectuada."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    if regime.incident_type == "contract_change":
        notice_date = _parse_date(
            validated_value(record, "fecha_aviso_modificacion")[0]
        )
        effective_date = _parse_date(
            validated_value(record, "fecha_aplicacion_modificacion")[0]
        )
        separated, _ = validated_value(
            record,
            "aviso_modificacion_separado_factura",
        )
        fixed, _ = validated_value(record, "contrato_precio_fijo")
        if notice_date and effective_date:
            if effective_date < notice_date:
                result.append(
                    missing_item(
                        "energy_contract_change_chronology_conflict",
                        "La modificación aparece aplicada antes de su aviso.",
                        MissingItemSeverity.BLOCKING,
                    )
                )
            elif (
                regime.modification_notice_days == 30
                and (effective_date - notice_date).days < 28
            ):
                result.append(
                    missing_item(
                        "energy_one_month_notice_review",
                        (
                            "El intervalo es inferior a veintiocho días y no puede "
                            "cumplir un aviso mínimo de un mes."
                        ),
                        MissingItemSeverity.BLOCKING,
                    )
                )
            elif regime.modification_notice_days == 30:
                result.append(
                    missing_item(
                        "energy_calendar_month_notice_check",
                        (
                            "Debe comprobarse por calendario que el aviso se realizó "
                            "con al menos un mes de antelación."
                        ),
                        MissingItemSeverity.HUMAN_REVIEW,
                    )
                )
        if separated is False:
            result.append(
                missing_item(
                    "energy_change_notice_not_separate",
                    (
                        "El cambio se comunicó dentro de la factura; debe revisarse "
                        "la exigencia de comunicación escrita y separada."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
        if fixed is True:
            result.append(
                missing_item(
                    "energy_fixed_price_change_review",
                    (
                        "El contrato figura a precio fijo; debe revisarse si la "
                        "modificación estaba contractualmente permitida."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )

    no_consent, _ = validated_value(
        record,
        "cambio_comercializadora_no_consentido",
        "servicio_energia_no_solicitado",
    )
    consent, _ = validated_value(record, "consentimiento_contratacion_acreditado")
    if regime.incident_type in {"unauthorized_switch", "unsolicited_service"}:
        if no_consent is True and consent is True:
            result.append(
                missing_item(
                    "energy_consent_conflict",
                    (
                        "Los hechos afirman simultáneamente ausencia y existencia "
                        "de consentimiento contractual."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
        elif consent is not False:
            result.append(
                missing_item(
                    "energy_consent_evidence_review",
                    "Debe revisarse la prueba exacta de contratación o cambio.",
                    MissingItemSeverity.BLOCKING,
                )
            )

    cut, _ = validated_value(record, "corte_suministro")
    cut_date = _parse_date(validated_value(record, "fecha_corte_suministro")[0])
    notice_cut = _parse_date(validated_value(record, "fecha_aviso_corte")[0])
    restored = _parse_date(
        validated_value(record, "fecha_reposicion_suministro")[0]
    )
    essential, _ = validated_value(record, "suministro_esencial")
    vulnerable, _ = validated_value(record, "consumidor_vulnerable")
    habitual, _ = validated_value(record, "vivienda_habitual")
    power = _number(validated_value(record, "potencia_contratada_kw")[0])

    if cut_date and notice_cut and cut_date < notice_cut:
        result.append(
            missing_item(
                "energy_cut_before_notice_conflict",
                "El corte aparece anterior a la fecha de aviso.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if cut_date and restored and restored < cut_date:
        result.append(
            missing_item(
                "energy_restoration_before_cut_conflict",
                "La reposición aparece anterior al corte.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if cut is True and essential is True:
        result.append(
            missing_item(
                "energy_essential_supply_cut",
                "Consta corte de un suministro declarado esencial.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if cut is True and regime.temporary_vulnerable_protection:
        result.append(
            missing_item(
                "energy_temporary_vulnerable_protection_cut",
                (
                    "Consta corte durante la garantía temporal de suministro de "
                    "2026 para un consumidor vulnerable."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    if (
        cut is True
        and regime.supply_type == "electricity"
        and habitual is True
        and power is not None
        and power <= 10
    ):
        result.append(
            missing_item(
                "energy_household_cut_procedure_review",
                (
                    "Debe comprobarse íntegramente el procedimiento especial de "
                    "suspensión para vivienda habitual de hasta 10 kW."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    if cut is True and vulnerable is True:
        result.append(
            missing_item(
                "energy_vulnerability_evidence_review",
                (
                    "Debe verificarse la acreditación vigente de vulnerabilidad y "
                    "las comunicaciones con servicios sociales."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    if regime.incident_type == "outage_quality":
        duration, _ = validated_value(record, "duracion_interrupcion_minutos")
        quality, _ = validated_value(record, "calidad_suministro")
        if not _present(duration) and not _present(quality):
            result.append(
                missing_item(
                    "energy_outage_evidence_missing",
                    (
                        "Faltan duración, registro de interrupciones o parámetros "
                        "documentales de calidad."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )

    return dedupe_missing(result)


def _deadlines(
    record: ValidatedFactsRecord,
    regime: ClaimsEnergyRegimeDecision,
) -> list[Deadline]:
    result: list[Deadline] = []

    claim_value, claim_key = validated_value(record, "reclamacion_previa_fecha")
    if _present(claim_value) and claim_key:
        notes = (
            [
                "Referencia sectorial de quince días hábiles.",
                "No se calcula automáticamente sin calendario competente.",
            ]
            if regime.complaint_response_business_days == 15
            else [
                "Debe verificarse el plazo sectorial o general aplicable.",
                "No se calcula automáticamente.",
            ]
        )
        result.append(
            Deadline(
                label="Respuesta a la reclamación previa de energía",
                due_at=None,
                calculation_status="unresolved",
                source_fact_keys=[claim_key],
                notes=notes,
            )
        )

    effective_value, effective_key = validated_value(
        record,
        "fecha_aplicacion_modificacion",
    )
    if (
        regime.modification_notice_days == 30
        and _present(effective_value)
        and effective_key
    ):
        result.append(
            Deadline(
                label="Aviso previo de modificación contractual",
                due_at=None,
                calculation_status="unresolved",
                source_fact_keys=[effective_key],
                notes=[
                    "La referencia legal es de al menos un mes, no treinta días fijos.",
                    "Debe comprobarse con fechas documentales y calendario.",
                ],
            )
        )

    return result


def _summary(
    record: ValidatedFactsRecord,
    regime: ClaimsEnergyRegimeDecision,
) -> tuple[list[str], list[str]]:
    rows, used = summary_rows(
        record,
        (
            ("suministro_tipo", "Suministro", ""),
            ("pais_suministro", "País", ""),
            ("comercializadora_energia", "Comercializadora", ""),
            ("distribuidora_energia", "Distribuidora", ""),
            ("cups", "CUPS", ""),
            ("contrato_ref", "Contrato", ""),
            ("factura_numero", "Factura", ""),
            ("periodo_facturado", "Periodo", ""),
            ("importe_factura_energia_eur", "Importe factura", " EUR"),
            ("importe_regularizacion_eur", "Regularización", " EUR"),
            ("consumo_facturado_kwh", "Consumo facturado", " kWh"),
            ("fecha_incidencia", "Fecha incidencia", ""),
            ("reclamacion_previa_fecha", "Reclamación previa", ""),
        ),
    )
    rows.insert(
        0,
        (
            f"Encuadre de energía: {regime.supply_type}; incidencia "
            f"{regime.incident_type}; régimen {regime.status}."
        ),
    )
    return list(dict.fromkeys(rows)), list(dict.fromkeys(used))


def build_claims_energy_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="claims",
        family="energia",
        specialist="claims.energy",
    )

    regime = _regime(facts_record)
    route = _route_state(facts_record)
    basis = list(regime.legal_basis) if regime.status == "current" else []

    retailer, retailer_key = validated_value(
        facts_record,
        "comercializadora_energia",
        "proveedor",
        "emisor_documento",
    )
    distributor, distributor_key = validated_value(
        facts_record,
        "distribuidora_energia",
    )
    cups, cups_key = validated_value(
        facts_record,
        "cups",
        "referencia_servicio",
        "contrato_ref",
    )
    invoice, invoice_key = validated_value(
        facts_record,
        "factura_numero",
        "referencia_documento",
    )
    solution, solution_key = validated_value(
        facts_record,
        "solucion_solicitada",
    )
    _, fact_key = validated_value(facts_record, "descripcion_hecho")

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
        "energy_contract_supply_and_roles",
        "Contrato, punto de suministro y sujetos intervinientes",
        (
            "Debe identificarse el contrato y el punto de suministro y separar "
            "las funciones de comercializadora, distribuidora y encargado de "
            "lectura. El cobro o la aparición en la factura no atribuyen por sí "
            "solos todas las actuaciones al mismo sujeto."
        ),
        (
            retailer_key,
            distributor_key,
            cups_key,
            "contrato_ref",
            "suministro_tipo",
            "tarifa_energia",
            "mercado_energia",
            fact_key,
        ),
        "primary",
    )
    add(
        "energy_invoice_reading_and_consumption",
        "Factura, lectura y consumo documentado",
        (
            "La factura debe contrastarse por periodo, lecturas, consumo, precio, "
            "peajes, impuestos y conceptos. La previa no acepta ni recalcula el "
            "total sin todos los componentes documentales."
        ),
        (
            invoice_key,
            "fecha_factura_energia",
            "periodo_facturado",
            "periodo_facturacion_inicio",
            "periodo_facturacion_fin",
            "numero_contador",
            "lectura_anterior",
            "lectura_actual",
            "lectura_real",
            "consumo_facturado_kwh",
            "consumo_reconocido_kwh",
            "importe_factura_energia_eur",
            fact_key,
        ),
        "primary",
    )
    add(
        "energy_regularization_refund_and_interest_review",
        "Regularización, devolución y trazabilidad económica",
        (
            "Debe determinarse si la diferencia es a favor de la empresa o del "
            "cliente, el periodo rectificado, el canal de acceso a la red y las "
            "cantidades ya devueltas. No se fijan intereses ni límites temporales "
            "sin cerrar esos hechos."
        ),
        (
            "importe_regularizacion_eur",
            "meses_regularizados",
            "acceso_red_a_traves_comercializadora",
            "factura_pagada_energia",
            "importe_devuelto_energia_eur",
            "importe_pagado_eur",
            "importe_reclamado_eur",
            solution_key,
            fact_key,
        ),
        "primary",
    )
    add(
        "energy_contract_change_consent_and_unsolicited_service",
        "Modificación contractual, consentimiento y servicios no solicitados",
        (
            "Debe comprobarse la oferta aceptada, el precio pactado, el aviso "
            "separado, su antelación y la prueba de consentimiento. Una grabación "
            "o marca interna debe vincularse al contrato concreto."
        ),
        (
            "fecha_contrato",
            "fecha_aviso_modificacion",
            "fecha_aplicacion_modificacion",
            "aviso_modificacion_separado_factura",
            "contrato_precio_fijo",
            "cambio_comercializadora_no_consentido",
            "consentimiento_contratacion_acreditado",
            "servicio_energia_no_solicitado",
            fact_key,
        ),
        "primary",
    )
    add(
        "energy_suspension_vulnerability_and_essential_supply",
        "Corte, vulnerabilidad y suministro esencial",
        (
            "La suspensión exige reconstruir causa, avisos, fechas, vivienda, "
            "potencia, vulnerabilidad y eventual esencialidad. Las protecciones "
            "temporales solo se aplican dentro de su vigencia y con acreditación."
        ),
        (
            "corte_suministro",
            "motivo_corte",
            "fecha_aviso_corte",
            "fecha_corte_suministro",
            "fecha_reposicion_suministro",
            "vivienda_habitual",
            "potencia_contratada_kw",
            "consumidor_vulnerable",
            "bono_social",
            "suministro_esencial",
            "unidad_convivencia_menor_16",
            "dependencia_grado_ii_iii",
            "discapacidad_33_o_mas",
            fact_key,
        ),
        "primary",
    )
    add(
        "energy_outage_and_quality_evidence",
        "Interrupciones y calidad de suministro",
        (
            "Las averías e interrupciones deben acreditarse mediante registros, "
            "duración, avisos y parámetros de calidad, distinguiendo la incidencia "
            "técnica de la gestión contractual y de los daños alegados."
        ),
        (
            "interrupcion_programada",
            "duracion_interrupcion_minutos",
            "calidad_suministro",
            "respuesta_documentada",
            fact_key,
        ),
    )
    add(
        "energy_prior_claim_and_competent_route",
        "Reclamación previa y vía competente",
        (
            "La reclamación debe dirigirse primero a la empresa responsable y "
            "conservar referencia y respuesta. El paso posterior exige comprobar "
            "competencia autonómica, materia sectorial y posible adhesión a una "
            "entidad de resolución alternativa."
        ),
        (
            "reclamacion_previa_fecha",
            "canal_reclamacion",
            "reclamacion_energia_ref",
            "referencia_documento",
            "respuesta_proveedor",
            "respuesta_documentada",
            "fecha_respuesta",
            fact_key,
        ),
    )
    add(
        "energy_proven_amount_without_duplicate_recovery",
        "Cuantía acreditada y ausencia de doble recuperación",
        (
            "La petición debe separar factura, regularización, pagos, devoluciones "
            "y daños documentados. Deben descontarse compensaciones ya recibidas "
            "y evitar reclamar dos veces la misma partida."
        ),
        (
            "importe_factura_energia_eur",
            "importe_regularizacion_eur",
            "importe_pagado_eur",
            "importe_reclamado_eur",
            "importe_devuelto_energia_eur",
            solution_key,
            fact_key,
        ),
    )

    if not arguments:
        raise HTTPException(
            status_code=409,
            detail=(
                "No existen hechos validados suficientes para construir la "
                "previa de energía."
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
            *fact_review_items(facts_record, prefix="energy"),
        ]
    )

    if route == "authority_review":
        destination = "AUTORIDAD AUTONÓMICA COMPETENTE EN ENERGÍA O CONSUMO"
        document_type = (
            "RECLAMACIÓN ADMINISTRATIVA O DE CONSUMO EN MATERIA DE ENERGÍA"
        )
    elif route == "provider_period_review":
        destination = (
            str(retailer).strip()
            if _present(retailer)
            else "EMPRESA DE ENERGÍA PENDIENTE DE VALIDAR"
        )
        document_type = (
            "REITERACIÓN A LA EMPRESA DE ENERGÍA Y RESERVA DE ESCALADO"
        )
    else:
        destination = (
            str(retailer).strip()
            if _present(retailer)
            else "EMPRESA DE ENERGÍA PENDIENTE DE VALIDAR"
        )
        document_type = "RECLAMACIÓN PREVIA A LA EMPRESA DE ENERGÍA"

    subject_parts = [
        "RECLAMACIÓN DE ENERGÍA",
        regime.supply_type.upper(),
        regime.incident_type.upper(),
    ]
    if _present(cups):
        subject_parts.append(f"CUPS {cups}")
    if _present(invoice):
        subject_parts.append(f"factura {invoice}")

    strategy = (
        "Reconstruir contrato, punto de suministro, roles y cronología; "
        "contrastar factura, lectura y consumo; revisar consentimiento, avisos, "
        "corte y vulnerabilidad; cuantificar solo partidas acreditadas; y "
        "seleccionar la vía competente tras la reclamación previa."
    )
    if _present(solution):
        strategy += f" La solución documental solicitada es: {_display(solution)}."

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="energia",
        specialist="claims.energy",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Se ha documentado una incidencia de {regime.supply_type} "
            f"encuadrada como {regime.incident_type}."
        ),
        client_goal=(
            "Obtener una respuesta motivada, corregir el suministro o la "
            "facturación y recuperar únicamente las cantidades acreditadas."
        ),
        primary_strategy=strategy,
        secondary_strategies=[
            (
                "Dirigir actuaciones separadas a comercializadora y distribuidora "
                "cuando sus funciones estén diferenciadas."
            ),
            (
                "Valorar resolución alternativa, consumo o autoridad energética "
                "según territorio, materia y respuesta."
            ),
            (
                "Preservar acciones civiles o administrativas cuando existan daños "
                "o controversias que excedan la rectificación de la factura."
            ),
        ],
        requested_outcomes=[
            "Identificación del contrato, punto de suministro y sujetos responsables.",
            "Entrega de lecturas, consumos, precios y cálculo completo de la factura.",
            "Rectificación o anulación de cargos incorrectos.",
            "Devolución de cantidades indebidamente cobradas cuando proceda.",
            "Confirmación de alta, baja, cambio o condiciones contractuales.",
            "Reposición del suministro y explicación del corte cuando corresponda.",
            "Respuesta motivada con referencia y vías de reclamación disponibles.",
        ],
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record, regime),
        risks=list(
            dict.fromkeys(
                [
                    (
                        "Confundir comercializadora y distribuidora puede dirigir "
                        "la reclamación al sujeto equivocado."
                    ),
                    (
                        "Una diferencia de consumo no demuestra por sí sola un "
                        "error sin revisar contador, periodo, multiplicador y lectura."
                    ),
                    (
                        "Las regularizaciones eléctricas dependen del periodo y "
                        "de quién contrató el acceso a la red."
                    ),
                    (
                        "Las protecciones por vulnerabilidad exigen acreditación y "
                        "pueden tener vigencia temporal."
                    ),
                    (
                        "Los plazos expresados en días hábiles o meses no deben "
                        "convertirse automáticamente a días naturales."
                    ),
                    *list(regime.warnings),
                ]
            )
        ),
        destination=destination,
        document_type=document_type,
        subject=" — ".join(subject_parts),
        legal_arguments=arguments,
        additional_requests=[
            "Contrato y condiciones vigentes.",
            "CUPS y datos del punto de suministro.",
            "Facturas completas y desglose de conceptos.",
            "Curva de consumo y lecturas del contador.",
            "Historial de cambios de comercializador o tarifa.",
            "Grabación o prueba de consentimiento cuando se invoque contratación.",
            "Aviso de modificación contractual y prueba de recepción.",
            "Avisos de impago, corte y reposición.",
            "Acreditación de vulnerabilidad, bono social o esencialidad.",
            "Reclamación previa, referencia, justificante y respuesta.",
            "Cálculo de regularización, devoluciones y pagos realizados.",
        ],
        created_by_component=(
            "claims.energy:"
            f"{CLAIMS_ENERGY_SPECIALIST_VERSION}+"
            f"{CLAIMS_ENERGY_REGIME_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
