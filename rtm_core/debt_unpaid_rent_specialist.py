"""Especialista RTM para rentas y cantidades de arrendamiento impagadas.

Construye una Previa Jurídica conservadora desde hechos congelados. Distingue
reclamación de cantidad, resolución y recuperación de la posesión, saldo tras
entrega de llaves, negociación previa, enervación, vulnerabilidad, garantías y
recuperaciones de terceros. No declara deuda, desahucio, prescripción,
competencia, intereses, costas, enervación o suspensión sin revisión OPS.
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
from rtm_core.debt_unpaid_rent_regime import (
    DEBT_UNPAID_RENT_REGIME_VERSION,
    DebtUnpaidRentRegimeDecision,
    resolve_debt_unpaid_rent_regime,
)


DEBT_UNPAID_RENT_SPECIALIST_VERSION = "rtm_debt_unpaid_rent_specialist_v1_0"

RouteState = Literal[
    "prior_demand",
    "masc_pending",
    "pre_court",
    "court_pending",
    "enforcement_review",
    "post_surrender",
    "payment_plan",
    "tenant_defence_review",
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


def _optional_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    folded = _fold(value)
    if folded in {"si", "true", "1", "consta", "acreditado", "aportado"}:
        return True
    if folded in {"no", "false", "0", "no consta", "no acreditado", "no aportado"}:
        return False
    return None


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
    raw = str(value).strip().replace("€", "").replace(" ", "")
    if not raw:
        return None
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    else:
        raw = raw.replace(",", ".")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", raw)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _all_text(record: ValidatedFactsRecord) -> str:
    values: list[Any] = []
    for key in (
        "descripcion_hecho",
        "incidencia_alquiler_impagado_tipo",
        "uso_arrendamiento",
        "alquiler_periodos_impagados",
        "motivo_oposicion_alquiler",
        "requerimiento_pago_alquiler_contenido",
        "masc_alquiler_resultado",
        "motivo_suspension_lanzamiento",
        "solucion_solicitada_alquiler",
        "solucion_solicitada",
    ):
        value, _ = validated_value(record, key)
        if _present(value):
            values.append(value)
    return _fold(values)


def _has_any(record: ValidatedFactsRecord, keys: tuple[str, ...]) -> bool:
    return any(_present(validated_value(record, key)[0]) for key in keys)


def _regime(record: ValidatedFactsRecord) -> DebtUnpaidRentRegimeDecision:
    rent_claim, _ = validated_value(record, "reclamacion_rentas_alquiler_solicitada")
    if rent_claim is None:
        rent_claim = _has_any(
            record,
            (
                "alquiler_periodos_impagados",
                "renta_impagada_principal_eur",
                "total_reclamado_alquiler_eur",
                "saldo_pendiente_alquiler_eur",
                "importe_deuda_eur",
                "saldo_pendiente_eur",
            ),
        )
    possession_returned, _ = validated_value(record, "posesion_inmueble_devuelta")
    if possession_returned is None:
        possession_returned = _has_any(record, ("fecha_entrega_llaves_alquiler",))

    return resolve_debt_unpaid_rent_regime(
        evaluation_date=date.today(),
        contract_date=validated_value(
            record,
            "fecha_contrato_arrendamiento",
            "fecha_documento",
        )[0],
        lease_start_date=validated_value(record, "fecha_inicio_arrendamiento")[0],
        lease_end_date=validated_value(record, "fecha_fin_arrendamiento")[0],
        first_unpaid_date=validated_value(record, "fecha_primer_impago_alquiler")[0],
        last_unpaid_date=validated_value(record, "fecha_ultimo_impago_alquiler")[0],
        prior_demand_date=validated_value(
            record,
            "requerimiento_pago_alquiler_fecha",
            "requerimiento_previo_fecha",
        )[0],
        prior_demand_received_date=validated_value(
            record,
            "fecha_recepcion_requerimiento_alquiler",
        )[0],
        masc_request_date=validated_value(record, "masc_alquiler_fecha_solicitud")[0],
        masc_received_date=validated_value(record, "masc_alquiler_fecha_recepcion")[0],
        court_filing_date=validated_value(
            record,
            "fecha_demanda_desahucio",
        )[0],
        possession_return_date=validated_value(record, "fecha_entrega_llaves_alquiler")[0],
        property_country=validated_value(record, "pais_inmueble_alquiler")[0],
        property_use=validated_value(record, "uso_arrendamiento")[0],
        room_lease=validated_value(record, "arrendamiento_habitacion")[0],
        seasonal_lease=validated_value(record, "arrendamiento_temporada")[0],
        tourist_lease=validated_value(record, "arrendamiento_turistico")[0],
        rural_lease=validated_value(record, "arrendamiento_rustico")[0],
        public_social_lease=validated_value(
            record,
            "vivienda_publica_social_arrendada",
        )[0],
        sublease=validated_value(record, "subarriendo_arrendamiento")[0],
        habitual_dwelling=validated_value(
            record,
            "vivienda_habitual_proceso_alquiler",
            "vivienda_habitual_arrendatario",
        )[0],
        claimant_role=validated_value(record, "parte_reclamante_alquiler")[0],
        landlord_claims=validated_value(record, "arrendador_reclama_deuda")[0],
        tenant_defence=validated_value(
            record,
            "parte_arrendataria_defiende_deuda",
        )[0],
        assignment_documented=validated_value(
            record,
            "cesion_credito_arrendamiento_documentada",
        )[0],
        insurer_subrogation=validated_value(
            record,
            "aseguradora_subrogada_alquiler",
        )[0],
        possession_recovery_requested=validated_value(
            record,
            "recuperacion_posesion_alquiler_solicitada",
        )[0],
        rent_claim_requested=rent_claim,
        contract_termination_requested=validated_value(
            record,
            "resolucion_contrato_alquiler_solicitada",
        )[0],
        possession_returned=possession_returned,
        payment_plan_requested=validated_value(record, "acuerdo_pago_alquiler")[0],
        judicial_action_intended=validated_value(
            record,
            "accion_judicial_alquiler_prevista",
        )[0],
        execution_only=validated_value(record, "ejecucion_solo_alquiler")[0],
        masc_started=validated_value(record, "masc_alquiler_iniciado")[0],
        masc_object_coincident=validated_value(
            record,
            "masc_alquiler_objeto_coincidente",
        )[0],
        masc_proof_documented=validated_value(
            record,
            "masc_alquiler_documento_acreditativo",
        )[0],
        prior_enervation=validated_value(record, "enervacion_previa_alquiler")[0],
        payment_after_demand=validated_value(
            record,
            "pago_posterior_requerimiento_alquiler",
        )[0],
        debt_paid=validated_value(
            record,
            "deuda_alquiler_pagada",
            "deuda_pagada",
        )[0],
    )


def _route_state(
    record: ValidatedFactsRecord,
    regime: DebtUnpaidRentRegimeDecision,
) -> RouteState:
    if regime.claimant_role == "tenant" or regime.claim_type == "tenant_defence":
        return "tenant_defence_review"
    judgment, _ = validated_value(record, "sentencia_desahucio_dictada")
    enforcement, _ = validated_value(record, "ejecucion_lanzamiento_iniciada")
    if judgment is True or enforcement is True:
        return "enforcement_review"
    filed, _ = validated_value(record, "demanda_desahucio_presentada")
    if filed is True or _has_any(record, ("numero_procedimiento_desahucio",)):
        return "court_pending"
    if regime.possession_returned:
        return "post_surrender"
    agreement, _ = validated_value(record, "acuerdo_pago_alquiler")
    if agreement is True:
        return "payment_plan"
    demand, _ = validated_value(
        record,
        "requerimiento_pago_alquiler_fecha",
        "requerimiento_previo_fecha",
    )
    if not _present(demand):
        return "prior_demand"
    if regime.masc_required and not regime.masc_documented:
        return "masc_pending"
    return "pre_court"


def _required_missing(
    record: ValidatedFactsRecord,
    regime: DebtUnpaidRentRegimeDecision,
) -> list[MissingItem]:
    groups: list[tuple[str, str, tuple[str, ...]]] = [
        (
            "rent_fact_missing",
            "Falta validar el hecho concreto y los periodos de alquiler impagado.",
            ("descripcion_hecho", "incidencia_alquiler_impagado_tipo"),
        ),
        (
            "rent_landlord_missing",
            "Falta identificar documentalmente a la parte arrendadora o acreedora.",
            ("arrendador", "acreedor", "emisor_documento"),
        ),
        (
            "rent_tenant_missing",
            "Falta identificar documentalmente a la parte arrendataria o deudora.",
            ("arrendatario", "deudor", "destinatario_documento"),
        ),
        (
            "rent_property_country_missing",
            "Falta el país documental del inmueble.",
            ("pais_inmueble_alquiler",),
        ),
        (
            "rent_property_address_missing",
            "Falta la dirección suficiente del inmueble arrendado.",
            ("direccion_inmueble_alquiler",),
        ),
        (
            "rent_contract_reference_missing",
            "Falta la referencia del contrato de arrendamiento.",
            ("contrato_arrendamiento_ref", "contrato_ref"),
        ),
        (
            "rent_contract_date_missing",
            "Falta la fecha documental del contrato de arrendamiento.",
            ("fecha_contrato_arrendamiento", "fecha_documento"),
        ),
        (
            "rent_lease_use_missing",
            "Falta identificar el uso y modalidad del arrendamiento.",
            ("uso_arrendamiento",),
        ),
        (
            "rent_contract_document_missing",
            "Falta confirmar que se aporta el contrato y sus anexos vigentes.",
            ("contrato_arrendamiento_aportado",),
        ),
        (
            "rent_monthly_amount_missing",
            "Falta la renta mensual pactada o la actualización documentada aplicable.",
            ("renta_mensual_pactada_eur", "renta_actualizada_mensual_eur"),
        ),
        (
            "rent_unpaid_periods_missing",
            "Falta el desglose de mensualidades o periodos impagados.",
            ("alquiler_periodos_impagados",),
        ),
        (
            "rent_unpaid_dates_missing",
            "Faltan las fechas del primer y último periodo impagado.",
            ("fecha_primer_impago_alquiler", "fecha_ultimo_impago_alquiler"),
        ),
        (
            "rent_balance_missing",
            "Falta una cuantía principal, total o saldo pendiente verificable.",
            (
                "renta_impagada_principal_eur",
                "total_reclamado_alquiler_eur",
                "saldo_pendiente_alquiler_eur",
                "importe_deuda_eur",
                "saldo_pendiente_eur",
            ),
        ),
        (
            "rent_requested_solution_missing",
            "Falta concretar la solución solicitada.",
            ("solucion_solicitada_alquiler", "solucion_solicitada"),
        ),
    ]

    if regime.claim_type in {"possession_and_rent", "possession_only"}:
        groups.extend(
            [
                (
                    "rent_contract_status_missing",
                    "Falta confirmar vigencia, resolución o prórroga del contrato.",
                    ("contrato_arrendamiento_vigente",),
                ),
                (
                    "rent_possession_status_missing",
                    "Falta confirmar si la posesión y las llaves han sido devueltas.",
                    ("posesion_inmueble_devuelta", "fecha_entrega_llaves_alquiler"),
                ),
                (
                    "rent_habitual_dwelling_status_missing",
                    "Falta determinar si el inmueble constituye vivienda habitual.",
                    (
                        "vivienda_habitual_proceso_alquiler",
                        "vivienda_habitual_arrendatario",
                    ),
                ),
            ]
        )

    charges = (
        _amount(validated_value(record, "suministros_impagados_alquiler_eur")[0])
        or 0.0
    ) + (
        _amount(
            validated_value(record, "gastos_comunidad_impagados_alquiler_eur")[0]
        )
        or 0.0
    ) + (
        _amount(validated_value(record, "ibi_repercutido_impagado_alquiler_eur")[0])
        or 0.0
    ) + (
        _amount(
            validated_value(record, "otros_conceptos_arrendamiento_impagados_eur")[0]
        )
        or 0.0
    )
    if charges > 0:
        groups.append(
            (
                "rent_pass_through_terms_missing",
                "Los conceptos distintos de renta exigen pacto, base legal y desglose.",
                (
                    "gastos_repercutidos_arrendamiento_pactados",
                    "desglose_otros_conceptos_arrendamiento",
                ),
            )
        )

    guarantor, _ = validated_value(record, "fiador_avalista_arrendamiento")
    if _present(guarantor):
        groups.extend(
            [
                (
                    "rent_guarantee_document_missing",
                    "Falta aportar la garantía o aval firmado.",
                    ("garantia_arrendamiento_aportada",),
                ),
                (
                    "rent_guarantee_scope_missing",
                    "Falta determinar alcance, duración y límites de la garantía.",
                    ("alcance_garantia_arrendamiento",),
                ),
            ]
        )

    result: list[MissingItem] = []
    for code, description, keys in groups:
        if not _has_any(record, keys):
            result.append(missing_item(code, description))
    return dedupe_missing(result)


def _arithmetic_missing(record: ValidatedFactsRecord) -> list[MissingItem]:
    result: list[MissingItem] = []
    values = {
        "monthly": _amount(validated_value(record, "renta_mensual_pactada_eur")[0]),
        "updated": _amount(validated_value(record, "renta_actualizada_mensual_eur")[0]),
        "months": _amount(validated_value(record, "mensualidades_impagadas_numero")[0]),
        "principal": _amount(validated_value(record, "renta_impagada_principal_eur")[0]),
        "utilities": _amount(validated_value(record, "suministros_impagados_alquiler_eur")[0]),
        "community": _amount(validated_value(record, "gastos_comunidad_impagados_alquiler_eur")[0]),
        "ibi": _amount(validated_value(record, "ibi_repercutido_impagado_alquiler_eur")[0]),
        "other": _amount(validated_value(record, "otros_conceptos_arrendamiento_impagados_eur")[0]),
        "total": _amount(validated_value(record, "total_reclamado_alquiler_eur")[0]),
        "payments": _amount(validated_value(record, "pagos_parciales_alquiler_eur")[0]),
        "credits": _amount(validated_value(record, "abonos_descuentos_alquiler_eur")[0]),
        "setoff": _amount(validated_value(record, "compensacion_invocada_arrendatario_eur")[0]),
        "deposit": _amount(validated_value(record, "fianza_arrendamiento_eur")[0]),
        "deposit_applied": _amount(validated_value(record, "importe_fianza_aplicado_deuda_eur")[0]),
        "balance": _amount(validated_value(record, "saldo_pendiente_alquiler_eur")[0]),
        "insurance": _amount(validated_value(record, "indemnizacion_seguro_impago_alquiler_eur")[0]),
        "guarantee": _amount(validated_value(record, "aval_fianza_cobrado_alquiler_eur")[0]),
        "third_party": _amount(validated_value(record, "importe_recuperado_terceros_alquiler_eur")[0]),
    }
    labels = {
        "monthly": "renta mensual",
        "updated": "renta actualizada",
        "months": "número de mensualidades",
        "principal": "principal de rentas",
        "utilities": "suministros",
        "community": "gastos de comunidad",
        "ibi": "IBI repercutido",
        "other": "otros conceptos",
        "total": "total reclamado",
        "payments": "pagos parciales",
        "credits": "abonos o descuentos",
        "setoff": "compensación invocada",
        "deposit": "fianza",
        "deposit_applied": "fianza aplicada",
        "balance": "saldo pendiente",
        "insurance": "indemnización del seguro",
        "guarantee": "aval o garantía cobrada",
        "third_party": "recuperación de terceros",
    }
    for key, value in values.items():
        if value is not None and value < 0:
            result.append(
                missing_item(
                    f"rent_negative_{key}",
                    f"El importe o valor de {labels[key]} no puede ser negativo.",
                    MissingItemSeverity.BLOCKING,
                )
            )

    principal = values["principal"]
    components_present = any(
        values[key] is not None for key in ("utilities", "community", "ibi", "other")
    )
    if principal is not None and values["total"] is not None:
        gross = principal + sum(
            values[key] or 0.0 for key in ("utilities", "community", "ibi", "other")
        )
        if abs(values["total"] - gross) > 0.02:
            result.append(
                missing_item(
                    "rent_total_components_mismatch",
                    (
                        "El total reclamado no coincide con la suma del principal y "
                        "los conceptos documentados."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
    elif components_present and principal is None:
        result.append(
            missing_item(
                "rent_principal_required_for_total_review",
                "No puede comprobarse el total sin principal de rentas separado.",
                MissingItemSeverity.BLOCKING,
            )
        )

    base_total = values["total"]
    if base_total is None and principal is not None:
        base_total = principal + sum(
            values[key] or 0.0 for key in ("utilities", "community", "ibi", "other")
        )
    confirmed_deductions = sum(
        values[key] or 0.0
        for key in (
            "payments",
            "credits",
            "insurance",
            "guarantee",
            "third_party",
            "deposit_applied",
        )
    )
    if base_total is not None and values["balance"] is not None:
        expected = base_total - confirmed_deductions
        if abs(values["balance"] - expected) > 0.02:
            result.append(
                missing_item(
                    "rent_outstanding_balance_mismatch",
                    (
                        "El saldo pendiente no concilia con total, pagos, abonos y "
                        "recuperaciones documentadas."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )
    if base_total is not None and confirmed_deductions > base_total + 0.02:
        result.append(
            missing_item(
                "rent_recoveries_exceed_claim",
                (
                    "Los pagos, abonos, seguro, aval, fianza aplicada y recuperaciones "
                    "superan el total documentado; existe riesgo de doble recuperación."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    monthly = values["updated"] or values["monthly"]
    months = values["months"]
    if monthly is not None and months is not None and principal is not None:
        expected_principal = monthly * months
        if abs(principal - expected_principal) > 0.02:
            result.append(
                missing_item(
                    "rent_month_count_principal_review",
                    (
                        "El principal no coincide con renta mensual por mensualidades; "
                        "deben revisarse prorrateos, cambios de renta y periodos parciales."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )

    deposit_applied_flag, _ = validated_value(record, "fianza_aplicada_deuda_alquiler")
    possession_returned, _ = validated_value(record, "posesion_inmueble_devuelta")
    keys_returned, _ = validated_value(record, "fecha_entrega_llaves_alquiler")
    if deposit_applied_flag is True and not (
        possession_returned is True or _present(keys_returned)
    ):
        result.append(
            missing_item(
                "rent_deposit_applied_before_surrender",
                (
                    "La fianza aparece compensada mientras no consta devolución de "
                    "llaves ni liquidación final documentada."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    if (
        values["deposit_applied"] is not None
        and values["deposit"] is not None
        and values["deposit_applied"] > values["deposit"] + 0.02
    ):
        result.append(
            missing_item(
                "rent_deposit_application_exceeds_deposit",
                "El importe de fianza aplicado supera la fianza documentada.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if values["setoff"] is not None and values["setoff"] > 0:
        result.append(
            missing_item(
                "rent_tenant_setoff_merits_review",
                (
                    "La compensación alegada por la parte arrendataria no puede "
                    "descontarse automáticamente sin verificar crédito, exigibilidad y oposición."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    return dedupe_missing(result)


def _review_missing(
    record: ValidatedFactsRecord,
    regime: DebtUnpaidRentRegimeDecision,
    route: RouteState,
) -> list[MissingItem]:
    result: list[MissingItem] = []

    if regime.status != "current":
        result.append(
            missing_item(
                "rent_regime_review",
                regime.blocking_reason
                or "Debe determinarse el régimen jurídico del arrendamiento.",
                MissingItemSeverity.BLOCKING,
            )
        )

    paid, _ = validated_value(record, "deuda_alquiler_pagada", "deuda_pagada")
    payment_proved, _ = validated_value(record, "pago_alquiler_acreditado")
    consigned, _ = validated_value(record, "consignacion_alquiler_judicial_notarial")
    if paid is True:
        result.append(
            missing_item(
                "rent_debt_marked_paid",
                "La deuda figura pagada y debe cerrarse o recalcularse antes de reclamar.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if payment_proved is True or consigned is True:
        result.append(
            missing_item(
                "rent_payment_or_deposit_proof_review",
                (
                    "Consta pago o consignación; deben imputarse periodos y conceptos "
                    "antes de mantener saldo, resolución o posesión."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    disputed, _ = validated_value(record, "deuda_alquiler_discutida", "deuda_discutida")
    if disputed is True:
        result.append(
            missing_item(
                "rent_disputed_debt_review",
                (
                    "La deuda está discutida; deben incorporarse oposición, pagos, "
                    "defectos de cálculo y respuesta del arrendador."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    cash, _ = validated_value(record, "pagos_alquiler_efectivo")
    cash_receipt, _ = validated_value(record, "recibo_pago_alquiler_entregado")
    if cash is True and cash_receipt is not True:
        result.append(
            missing_item(
                "rent_cash_payment_receipt_review",
                "Los pagos en efectivo exigen revisar recibos, fechas e imputación.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    updated, _ = validated_value(record, "renta_actualizada_mensual_eur")
    update_documented, _ = validated_value(record, "renta_actualizacion_documentada")
    update_disputed, _ = validated_value(record, "renta_actualizacion_discutida")
    if _present(updated) and update_documented is not True:
        result.append(
            missing_item(
                "rent_increase_documentation_missing",
                "La renta actualizada exige cláusula, comunicación y cálculo documentados.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if update_disputed is True:
        result.append(
            missing_item(
                "rent_increase_dispute_review",
                "La actualización de renta está discutida y debe separarse del principal indiscutido.",
                MissingItemSeverity.BLOCKING,
            )
        )

    charges = sum(
        _amount(validated_value(record, key)[0]) or 0.0
        for key in (
            "suministros_impagados_alquiler_eur",
            "gastos_comunidad_impagados_alquiler_eur",
            "ibi_repercutido_impagado_alquiler_eur",
            "otros_conceptos_arrendamiento_impagados_eur",
        )
    )
    charges_agreed, _ = validated_value(
        record,
        "gastos_repercutidos_arrendamiento_pactados",
    )
    if charges > 0 and charges_agreed is not True:
        result.append(
            missing_item(
                "rent_pass_through_charges_basis_review",
                (
                    "Suministros, comunidad, tributos u otros gastos requieren "
                    "pacto o base legal, facturas y periodo imputado."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    defences = (
        ("inhabitabilidad_arrendamiento_invocada", "inhabitabilidad o suspensión de renta"),
        ("suspension_renta_obras_invocada", "suspensión de renta por obras"),
        ("obras_a_cambio_renta_pactadas", "obras pactadas en sustitución de renta"),
        ("compensacion_creditos_alquiler_invocada", "compensación de créditos"),
        ("incumplimiento_arrendador_invocado", "incumplimiento del arrendador"),
    )
    for key, label in defences:
        value, _ = validated_value(record, key)
        if value is True:
            result.append(
                missing_item(
                    f"rent_defence_{key}"[:120],
                    f"Se invoca {label}; debe analizarse antes de afirmar impago exigible.",
                    MissingItemSeverity.BLOCKING,
                )
            )

    if regime.masc_required and not regime.masc_documented:
        result.append(
            missing_item(
                "rent_masc_documentation_required",
                (
                    "La acción civil prevista requiere acreditar negociación previa, "
                    "objeto coincidente, recepción y resultado conforme a la Ley Orgánica 1/2025."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    elif regime.masc_required:
        masc_end, _ = validated_value(record, "masc_alquiler_fecha_fin")
        outcome, _ = validated_value(record, "masc_alquiler_resultado")
        if not _present(masc_end) and not _present(outcome):
            result.append(
                missing_item(
                    "rent_masc_outcome_or_end_missing",
                    "Falta acreditar cómo y cuándo terminó la negociación previa.",
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )

    demand, _ = validated_value(
        record,
        "requerimiento_pago_alquiler_fecha",
        "requerimiento_previo_fecha",
    )
    demand_ref, _ = validated_value(record, "requerimiento_pago_alquiler_ref")
    demand_content, _ = validated_value(record, "requerimiento_pago_alquiler_contenido")
    demand_received, _ = validated_value(record, "requerimiento_pago_alquiler_recibido")
    received_date, _ = validated_value(record, "fecha_recepcion_requerimiento_alquiler")
    if not _present(demand):
        result.append(
            missing_item(
                "rent_prior_demand_recommended",
                (
                    "Debe prepararse un requerimiento fehaciente con contrato, periodos, "
                    "desglose, saldo, plazo y advertencias jurídicamente revisadas."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    else:
        if not _present(demand_ref):
            result.append(
                missing_item(
                    "rent_prior_demand_reference_missing",
                    "Falta justificante o referencia del requerimiento de pago.",
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
        if not _present(demand_content):
            result.append(
                missing_item(
                    "rent_prior_demand_content_missing",
                    "Falta conservar el contenido íntegro del requerimiento.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        if demand_received is not True or not _present(received_date):
            result.append(
                missing_item(
                    "rent_prior_demand_receipt_missing",
                    "Falta acreditar recepción y fecha del requerimiento.",
                    MissingItemSeverity.BLOCKING,
                )
            )

    if regime.enervation_applicable:
        if regime.enervation_preclusion_possible:
            result.append(
                missing_item(
                    "rent_enervation_preclusion_review",
                    (
                        "Existen elementos que podrían excluir la enervación, pero "
                        "OPS debe verificar procedimiento, identidad, contenido, recepción, "
                        "intervalo, pagos y excepciones antes de afirmarlo."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
        else:
            result.append(
                missing_item(
                    "rent_enervation_status_review",
                    regime.enervation_reason
                    or "Debe determinarse si la parte arrendataria puede enervar.",
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )

    if regime.possession_returned and regime.possession_recovery_requested:
        result.append(
            missing_item(
                "rent_eviction_after_surrender_conflict",
                (
                    "Consta devolución de la posesión; no debe mantenerse una pretensión "
                    "de desahucio y debe liquidarse únicamente el saldo procedente."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    keys_returned, _ = validated_value(record, "fecha_entrega_llaves_alquiler")
    keys_proved, _ = validated_value(record, "entrega_llaves_alquiler_acreditada")
    if _present(keys_returned) and keys_proved is not True:
        result.append(
            missing_item(
                "rent_keys_return_evidence_missing",
                "La fecha de entrega de llaves exige acta, recibo o prueba de recepción.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    if regime.general_vulnerability_review:
        alleged, _ = validated_value(record, "arrendatario_vulnerable_alegado")
        documented, _ = validated_value(record, "arrendatario_vulnerable_acreditado")
        social_report, _ = validated_value(record, "servicios_sociales_informe_alquiler")
        if alleged is None and documented is None:
            result.append(
                missing_item(
                    "rent_tenant_vulnerability_status_missing",
                    (
                        "Debe recabarse el estado procesal de posible vulnerabilidad sin "
                        "presumirla por composición familiar o situación económica."
                    ),
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
        if alleged is True and documented is not True and social_report is not True:
            result.append(
                missing_item(
                    "rent_vulnerability_evidence_review",
                    "La vulnerabilidad alegada requiere acreditación y tratamiento judicial.",
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
    if regime.extraordinary_suspension_active:
        result.append(
            missing_item(
                "rent_extraordinary_suspension_transitional_review",
                (
                    "La fecha de evaluación cae en un periodo extraordinario temporal; "
                    "deben revisarse vigencia, derogación y efectos transitorios antes de actuar."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )

    filed, _ = validated_value(record, "demanda_desahucio_presentada")
    procedure, _ = validated_value(record, "numero_procedimiento_desahucio")
    court, _ = validated_value(record, "organo_judicial_desahucio")
    opposition, _ = validated_value(record, "oposicion_desahucio_presentada")
    judgment, _ = validated_value(record, "sentencia_desahucio_dictada")
    enforcement, _ = validated_value(record, "ejecucion_lanzamiento_iniciada")
    if filed is True and (not _present(procedure) or not _present(court)):
        result.append(
            missing_item(
                "rent_court_file_identification_missing",
                "Faltan número de procedimiento u órgano judicial del desahucio presentado.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if opposition is True:
        result.append(
            missing_item(
                "rent_court_opposition_review",
                (
                    "Existe oposición judicial; deben analizarse motivos, prueba, plazos "
                    "y compatibilidad con cualquier nuevo requerimiento."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    if judgment is True or enforcement is True:
        result.append(
            missing_item(
                "rent_judgment_or_enforcement_route_review",
                (
                    "El asunto se encuentra en sentencia o ejecución; no debe emitirse "
                    "como simple requerimiento extrajudicial de alquiler impagado."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
    related, _ = validated_value(record, "procedimiento_judicial_relacionado_alquiler")
    if related is True and filed is not True:
        result.append(
            missing_item(
                "rent_related_court_proceeding_review",
                "Existe otro procedimiento relacionado y deben revisarse litispendencia, cosa juzgada o acumulación.",
                MissingItemSeverity.BLOCKING,
            )
        )

    agreement, _ = validated_value(record, "acuerdo_pago_alquiler")
    agreement_breached, _ = validated_value(record, "acuerdo_pago_alquiler_incumplido")
    schedule, _ = validated_value(record, "calendario_acuerdo_pago_alquiler")
    if agreement is True and not _present(schedule):
        result.append(
            missing_item(
                "rent_payment_plan_schedule_missing",
                "Falta el calendario y reconocimiento de saldo del acuerdo de pago.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if agreement is True and agreement_breached is not True:
        result.append(
            missing_item(
                "rent_active_payment_plan_review",
                "Consta acuerdo de pago no acreditado como incumplido; debe revisarse antes de reclamar.",
                MissingItemSeverity.BLOCKING,
            )
        )

    insolvency, _ = validated_value(record, "procedimiento_concursal_arrendatario")
    if insolvency is True:
        result.append(
            missing_item(
                "rent_tenant_insolvency_review",
                "La parte arrendataria consta en procedimiento concursal; deben revisarse comunicación y clasificación del crédito.",
                MissingItemSeverity.BLOCKING,
            )
        )

    guarantor, _ = validated_value(record, "fiador_avalista_arrendamiento")
    if _present(guarantor):
        guarantee, _ = validated_value(record, "garantia_arrendamiento_aportada")
        scope, _ = validated_value(record, "alcance_garantia_arrendamiento")
        if guarantee is not True or not _present(scope):
            result.append(
                missing_item(
                    "rent_guarantor_scope_review",
                    (
                        "No puede dirigirse el requerimiento al fiador sin revisar "
                        "documento, solidaridad, renuncias, duración, límites y notificación."
                    ),
                    MissingItemSeverity.BLOCKING,
                )
            )

    future_rents, _ = validated_value(record, "reclamacion_rentas_futuras_solicitada")
    if future_rents is True:
        result.append(
            missing_item(
                "rent_future_accruals_procedural_review",
                (
                    "La inclusión de rentas futuras exige revisión procesal y liquidación "
                    "posterior; no pueden sumarse al saldo actual como ya vencidas."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    return dedupe_missing([*result, *_arithmetic_missing(record)])


def _utc(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _add_year(value: date) -> date:
    try:
        return value.replace(year=value.year + 1)
    except ValueError:
        return value.replace(year=value.year + 1, day=28)


def _deadlines(
    record: ValidatedFactsRecord,
    regime: DebtUnpaidRentRegimeDecision,
) -> list[Deadline]:
    result: list[Deadline] = []

    last_unpaid_value, last_unpaid_key = validated_value(
        record,
        "fecha_ultimo_impago_alquiler",
    )
    last_unpaid = _parse_date(last_unpaid_value)
    if last_unpaid and last_unpaid_key:
        result.append(
            Deadline(
                label="Último vencimiento impagado documentado",
                due_at=_utc(last_unpaid),
                calculation_status="confirmed",
                source_fact_keys=[last_unpaid_key],
                notes=[
                    "Es un vencimiento contractual documentado, no una fecha procesal.",
                    "Cada mensualidad o periodo conserva su propio cómputo potencial.",
                ],
            )
        )

    masc_received_value, masc_received_key = validated_value(
        record,
        "masc_alquiler_fecha_recepcion",
    )
    masc_request_value, masc_request_key = validated_value(
        record,
        "masc_alquiler_fecha_solicitud",
    )
    masc_base = _parse_date(masc_received_value) or _parse_date(masc_request_value)
    masc_key = masc_received_key or masc_request_key
    if regime.masc_required and masc_base and masc_key:
        no_response_end = masc_base + timedelta(days=30)
        result.append(
            Deadline(
                label="Referencia de treinta días sin primera respuesta en negociación",
                due_at=_utc(no_response_end),
                calculation_status="estimated",
                source_fact_keys=[masc_key],
                notes=[
                    "Cómputo orientativo de treinta días naturales.",
                    "La finalización real depende de recepción, respuesta, reuniones y resultado documentado.",
                ],
            )
        )

    masc_end_value, masc_end_key = validated_value(record, "masc_alquiler_fecha_fin")
    masc_end = _parse_date(masc_end_value)
    if regime.masc_required and masc_end and masc_end_key:
        result.append(
            Deadline(
                label="Ventana de un año para conservar el requisito negociador",
                due_at=_utc(_add_year(masc_end)),
                calculation_status="estimated",
                source_fact_keys=[masc_end_key],
                notes=[
                    "Referencia de un año desde la terminación documentada sin acuerdo.",
                    "Debe comprobarse identidad de objeto y cualquier nueva negociación o interrupción."
                ],
            )
        )

    demand_received_value, demand_received_key = validated_value(
        record,
        "fecha_recepcion_requerimiento_alquiler",
    )
    demand_received = _parse_date(demand_received_value)
    if regime.enervation_applicable and demand_received and demand_received_key:
        result.append(
            Deadline(
                label="Referencia de treinta días del requerimiento para enervación",
                due_at=_utc(demand_received + timedelta(days=30)),
                calculation_status="estimated",
                source_fact_keys=[demand_received_key],
                notes=[
                    "Es un marcador mínimo para revisar la posible exclusión de la enervación.",
                    "No autoriza por sí solo la demanda ni sustituye la comprobación de pagos y recepción."
                ],
            )
        )

    result.append(
        Deadline(
            label="Prescripción de cada renta o cantidad periódica",
            due_at=None,
            calculation_status="unresolved",
            notes=[
                "El plazo de cinco años es solo un candidato general.",
                "Deben individualizarse vencimientos, naturaleza de cada concepto e interrupciones documentadas."
            ],
        )
    )
    return result


def _summary(
    record: ValidatedFactsRecord,
    regime: DebtUnpaidRentRegimeDecision,
) -> tuple[list[str], list[str]]:
    rows, used = summary_rows(
        record,
        (
            ("arrendador", "Arrendador", ""),
            ("arrendatario", "Arrendatario", ""),
            ("fiador_avalista_arrendamiento", "Fiador o avalista", ""),
            ("contrato_arrendamiento_ref", "Contrato", ""),
            ("fecha_contrato_arrendamiento", "Fecha del contrato", ""),
            ("direccion_inmueble_alquiler", "Inmueble", ""),
            ("uso_arrendamiento", "Uso", ""),
            ("renta_mensual_pactada_eur", "Renta mensual pactada", " EUR"),
            ("renta_actualizada_mensual_eur", "Renta mensual actualizada", " EUR"),
            ("alquiler_periodos_impagados", "Periodos impagados", ""),
            ("fecha_primer_impago_alquiler", "Primer impago", ""),
            ("fecha_ultimo_impago_alquiler", "Último impago", ""),
            ("renta_impagada_principal_eur", "Principal de rentas", " EUR"),
            ("suministros_impagados_alquiler_eur", "Suministros", " EUR"),
            ("gastos_comunidad_impagados_alquiler_eur", "Comunidad", " EUR"),
            ("ibi_repercutido_impagado_alquiler_eur", "IBI", " EUR"),
            ("total_reclamado_alquiler_eur", "Total reclamado", " EUR"),
            ("pagos_parciales_alquiler_eur", "Pagos parciales", " EUR"),
            ("saldo_pendiente_alquiler_eur", "Saldo pendiente", " EUR"),
            ("requerimiento_pago_alquiler_fecha", "Requerimiento", ""),
            ("masc_alquiler_resultado", "Resultado de negociación", ""),
            ("numero_procedimiento_desahucio", "Procedimiento", ""),
            ("fecha_entrega_llaves_alquiler", "Entrega de llaves", ""),
        ),
    )
    rows.insert(
        0,
        (
            f"Encuadre: {regime.lease_kind}; pretensión {regime.claim_type}; "
            f"MASC {regime.masc_layer}; suspensión extraordinaria "
            f"{regime.extraordinary_suspension_state}; régimen {regime.status}."
        ),
    )
    return list(dict.fromkeys(rows)), list(dict.fromkeys(used))


def build_debt_unpaid_rent_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="debt",
        family="alquiler_impagado",
        specialist="debt.unpaid_rent",
    )

    regime = _regime(facts_record)
    route = _route_state(facts_record, regime)
    basis = list(regime.legal_basis) if regime.status == "current" else []

    landlord, landlord_key = validated_value(
        facts_record,
        "arrendador",
        "acreedor",
        "emisor_documento",
    )
    tenant, tenant_key = validated_value(
        facts_record,
        "arrendatario",
        "deudor",
        "destinatario_documento",
    )
    contract, contract_key = validated_value(
        facts_record,
        "contrato_arrendamiento_ref",
        "contrato_ref",
    )
    address, address_key = validated_value(
        facts_record,
        "direccion_inmueble_alquiler",
    )
    solution, solution_key = validated_value(
        facts_record,
        "solucion_solicitada_alquiler",
        "solucion_solicitada",
    )
    _, fact_key = validated_value(
        facts_record,
        "descripcion_hecho",
        "incidencia_alquiler_impagado_tipo",
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
        "rent_contract_parties_property_and_scope",
        "Contrato, partes, inmueble y modalidad",
        (
            "La reclamación debe identificar el contrato vigente, arrendador, "
            "arrendatario, inmueble, uso, duración y legitimación. El alquiler de "
            "habitación, turístico, rústico, social o subarrendado no puede tratarse "
            "automáticamente como arrendamiento urbano ordinario."
        ),
        (
            fact_key,
            landlord_key,
            tenant_key,
            contract_key,
            address_key,
            "pais_inmueble_alquiler",
            "fecha_contrato_arrendamiento",
            "fecha_inicio_arrendamiento",
            "fecha_fin_arrendamiento",
            "uso_arrendamiento",
            "arrendamiento_habitacion",
            "arrendamiento_temporada",
            "arrendamiento_turistico",
            "arrendamiento_rustico",
            "vivienda_publica_social_arrendada",
            "subarriendo_arrendamiento",
            "contrato_arrendamiento_aportado",
            "contrato_arrendamiento_vigente",
        ),
        "primary",
    )
    add(
        "rent_periods_ledger_and_outstanding_balance",
        "Periodos, conceptos, pagos y saldo",
        (
            "Cada mensualidad y concepto debe aparecer en un cuadro de liquidación "
            "reproducible, enlazado con renta pactada, actualizaciones, vencimientos, "
            "recibos, pagos, abonos y saldo. No deben añadirse intereses, costas o "
            "cantidades futuras como si ya estuvieran vencidos."
        ),
        (
            fact_key,
            "renta_mensual_pactada_eur",
            "renta_actualizada_mensual_eur",
            "periodicidad_pago_renta",
            "dia_vencimiento_renta",
            "alquiler_periodos_impagados",
            "fecha_primer_impago_alquiler",
            "fecha_ultimo_impago_alquiler",
            "mensualidades_impagadas_numero",
            "renta_impagada_principal_eur",
            "suministros_impagados_alquiler_eur",
            "gastos_comunidad_impagados_alquiler_eur",
            "ibi_repercutido_impagado_alquiler_eur",
            "otros_conceptos_arrendamiento_impagados_eur",
            "desglose_otros_conceptos_arrendamiento",
            "total_reclamado_alquiler_eur",
            "pagos_parciales_alquiler_eur",
            "abonos_descuentos_alquiler_eur",
            "saldo_pendiente_alquiler_eur",
        ),
        "primary",
    )
    add(
        "rent_updates_charges_payments_and_defences",
        "Actualización, gastos, pagos y objeciones",
        (
            "La actualización de renta y los gastos repercutidos requieren base "
            "documental. Los pagos en efectivo, consignaciones, compensaciones, "
            "inhabitabilidad, obras o incumplimientos del arrendador deben analizarse "
            "antes de calificar el saldo como vencido, exigible e impagado."
        ),
        (
            "renta_actualizacion_documentada",
            "renta_actualizacion_discutida",
            "gastos_repercutidos_arrendamiento_pactados",
            "recibos_alquiler_aportados",
            "extracto_bancario_alquiler_aportado",
            "pagos_alquiler_efectivo",
            "recibo_pago_alquiler_entregado",
            "pago_alquiler_acreditado",
            "consignacion_alquiler_judicial_notarial",
            "compensacion_invocada_arrendatario_eur",
            "inhabitabilidad_arrendamiento_invocada",
            "suspension_renta_obras_invocada",
            "obras_a_cambio_renta_pactadas",
            "compensacion_creditos_alquiler_invocada",
            "incumplimiento_arrendador_invocado",
            fact_key,
        ),
        "primary",
    )
    add(
        "rent_prior_demand_masc_and_enervation",
        "Requerimiento, negociación previa y enervación",
        (
            "Deben distinguirse el requerimiento de pago, la actividad negociadora "
            "previa exigible y el requerimiento relevante para enervación. Cada uno "
            "requiere contenido, identidad de objeto, recepción, fecha y resultado. "
            "La posible exclusión de la enervación queda siempre sometida a revisión OPS."
        ),
        (
            "requerimiento_pago_alquiler_fecha",
            "requerimiento_pago_alquiler_medio",
            "requerimiento_pago_alquiler_ref",
            "requerimiento_pago_alquiler_contenido",
            "requerimiento_pago_alquiler_recibido",
            "fecha_recepcion_requerimiento_alquiler",
            "advertencia_resolucion_desahucio_alquiler",
            "masc_alquiler_iniciado",
            "masc_alquiler_tipo",
            "masc_alquiler_fecha_solicitud",
            "masc_alquiler_fecha_recepcion",
            "masc_alquiler_objeto_coincidente",
            "masc_alquiler_resultado",
            "masc_alquiler_fecha_fin",
            "masc_alquiler_documento_acreditativo",
            "enervacion_previa_alquiler",
            "fecha_enervacion_previa_alquiler",
            "pago_posterior_requerimiento_alquiler",
            "fecha_pago_posterior_requerimiento_alquiler",
            fact_key,
        ),
        "primary",
    )
    add(
        "rent_termination_possession_and_procedural_stage",
        "Resolución, posesión y fase procesal",
        (
            "La falta de pago puede fundamentar resolución y recuperación de la "
            "posesión cuando concurran contrato, deuda y legitimación, pero la "
            "pretensión debe adaptarse a la entrega de llaves y a la fase judicial. "
            "Una sentencia, oposición o ejecución impide tratar el asunto como una "
            "simple reclamación extrajudicial inicial."
        ),
        (
            "recuperacion_posesion_alquiler_solicitada",
            "reclamacion_solo_cantidad_alquiler",
            "reclamacion_rentas_alquiler_solicitada",
            "resolucion_contrato_alquiler_solicitada",
            "posesion_inmueble_devuelta",
            "fecha_entrega_llaves_alquiler",
            "entrega_llaves_alquiler_acreditada",
            "demanda_desahucio_presentada",
            "fecha_demanda_desahucio",
            "numero_procedimiento_desahucio",
            "organo_judicial_desahucio",
            "oposicion_desahucio_presentada",
            "fecha_vista_desahucio",
            "fecha_lanzamiento_desahucio",
            "sentencia_desahucio_dictada",
            "sentencia_desahucio_firme",
            "ejecucion_lanzamiento_iniciada",
            fact_key,
        ),
        "primary",
    )
    add(
        "rent_vulnerability_and_suspension_timeline",
        "Vulnerabilidad y régimen temporal de suspensión",
        (
            "En vivienda habitual deben comprobarse las actuaciones judiciales y de "
            "servicios sociales. La vulnerabilidad no se presume ni produce por sí "
            "sola una suspensión. La extensión extraordinaria de febrero de 2026 fue "
            "temporal y derogada, por lo que no debe presentarse como vigente hasta "
            "diciembre de 2026 sin un análisis transitorio concreto."
        ),
        (
            "vivienda_habitual_proceso_alquiler",
            "vivienda_habitual_arrendatario",
            "arrendatario_vulnerable_alegado",
            "arrendatario_vulnerable_acreditado",
            "alternativa_habitacional_arrendatario",
            "servicios_sociales_informe_alquiler",
            "fecha_informe_servicios_sociales_alquiler",
            "arrendador_vulnerable_alegado",
            "arrendador_vulnerable_acreditado",
            "menores_dependientes_vivienda",
            "discapacidad_dependencia_vivienda",
            "lanzamiento_desahucio_suspendido",
            "motivo_suspension_lanzamiento",
            fact_key,
        ),
    )
    add(
        "rent_guarantee_insurance_and_no_double_recovery",
        "Garantía, seguro y recuperaciones",
        (
            "La reclamación frente a fiador, avalista o aseguradora exige revisar "
            "documento, duración, límites, pago y subrogación. Las cantidades cobradas "
            "por seguro, aval, fianza o terceros deben deducirse o asignarse al titular "
            "legitimado para impedir una doble recuperación."
        ),
        (
            "fiador_avalista_arrendamiento",
            "garantia_arrendamiento_aportada",
            "alcance_garantia_arrendamiento",
            "requerimiento_fiador_alquiler_fecha",
            "seguro_impago_alquiler",
            "aseguradora_impago_alquiler",
            "siniestro_impago_alquiler_ref",
            "indemnizacion_seguro_impago_alquiler_eur",
            "aval_fianza_cobrado_alquiler_eur",
            "importe_recuperado_terceros_alquiler_eur",
            "fianza_arrendamiento_eur",
            "fianza_aplicada_deuda_alquiler",
            "importe_fianza_aplicado_deuda_eur",
            fact_key,
        ),
    )
    add(
        "rent_limitation_interruption_and_future_accruals",
        "Prescripción, interrupción y rentas futuras",
        (
            "Cada renta periódica conserva vencimiento propio. El candidato general "
            "de cinco años exige revisar dies a quo, actos interruptivos, reconocimiento "
            "y naturaleza de cada concepto. Las rentas futuras deben liquidarse en la "
            "forma procesal procedente y no sumarse al saldo vencido actual."
        ),
        (
            "fecha_primer_impago_alquiler",
            "fecha_ultimo_impago_alquiler",
            "fecha_interrupcion_prescripcion_alquiler",
            "requerimiento_pago_alquiler_fecha",
            "masc_alquiler_fecha_solicitud",
            "reclamacion_rentas_futuras_solicitada",
            fact_key,
        ),
    )

    if not arguments:
        raise HTTPException(
            status_code=409,
            detail=(
                "No existen hechos validados suficientes para construir la previa "
                "de alquiler impagado."
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
            *fact_review_items(facts_record, prefix="rent"),
        ]
    )

    destination = (
        str(tenant).strip()
        if _present(tenant)
        else "PARTE ARRENDATARIA PENDIENTE DE VALIDAR"
    )
    document_type = "REQUERIMIENTO FEHACIENTE DE PAGO DE RENTAS Y CANTIDADES ASUMIDAS"
    if route == "masc_pending":
        document_type = "SOLICITUD DE NEGOCIACIÓN PREVIA SOBRE RENTAS IMPAGADAS"
    elif route == "pre_court":
        document_type = "PREVIA JURÍDICA DE DESAHUCIO Y RECLAMACIÓN DE RENTAS"
    elif route == "court_pending":
        document_type = "PREVIA JURÍDICA DE PROCEDIMIENTO DE DESAHUCIO EN CURSO"
    elif route == "enforcement_review":
        document_type = "REVISIÓN JURÍDICA DE SENTENCIA O EJECUCIÓN DE DESAHUCIO"
    elif route == "post_surrender":
        document_type = "RECLAMACIÓN DE SALDO TRAS ENTREGA DE LA POSESIÓN"
    elif route == "payment_plan":
        document_type = "REVISIÓN DE ACUERDO DE PAGO DE RENTAS"
    elif route == "tenant_defence_review":
        document_type = "DERIVACIÓN A OPOSICIÓN O DEFENSA DE LA PARTE ARRENDATARIA"

    subject_parts = ["ALQUILER IMPAGADO", regime.claim_type.upper()]
    if _present(contract):
        subject_parts.append(f"contrato {contract}")
    if _present(address):
        subject_parts.append(str(address))

    strategy = (
        "Reconstruir contrato, periodos, renta, conceptos, pagos y saldo; separar "
        "cantidad y posesión; acreditar requerimiento y negociación previa; revisar "
        "enervación, vulnerabilidad y fase procesal; y reclamar únicamente cantidades "
        "vencidas, exigibles, no pagadas y no recuperadas por otra vía."
    )
    if _present(solution):
        strategy += f" La solución documentada solicitada es: {_display(solution)}."

    requested_outcomes = [
        "Confirmación del contrato, partes, inmueble, uso y legitimación.",
        "Cuadro completo por mensualidad y concepto con vencimiento y soporte documental.",
        "Imputación de pagos, abonos, consignaciones, fianza y recuperaciones de terceros.",
        "Pago del saldo vencido y exigible que resulte definitivamente conciliado.",
        "Negociación previa documentada cuando constituya requisito procesal.",
        "Resolución contractual y recuperación de la posesión solo si siguen siendo procedentes.",
        "Revisión expresa de enervación, vulnerabilidad y fase judicial antes de demandar.",
        "Reserva de la vía frente a fiador o aseguradora únicamente dentro de su garantía acreditada.",
    ]

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="alquiler_impagado",
        specialist="debt.unpaid_rent",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Posible deuda de arrendamiento del contrato {_display(contract)} respecto de {_display(address)}."
            if _present(contract) or _present(address)
            else "Posible alquiler impagado pendiente de completar y conciliar."
        ),
        client_goal=(
            "Obtener pago, acuerdo, resolución o recuperación de la posesión según "
            "la fase y los hechos, sin reclamar importes duplicados ni anticipar "
            "enervación, vulnerabilidad o resultado judicial."
        ),
        primary_strategy=strategy,
        secondary_strategies=[
            "Solicitar contrato, anexos, recibos, extractos y comunicaciones completas.",
            "Separar renta principal de suministros, comunidad, tributos y otros gastos.",
            "Ofrecer negociación o calendario de pago con reconocimiento de saldo cuando resulte útil.",
            "Eliminar la pretensión posesoria si ya se devolvieron las llaves.",
            "Derivar a defensa del deudor, concurso o ejecución cuando la fase lo exija.",
            "Preservar acciones frente a fiador, seguro o terceros sin doble recuperación.",
        ],
        requested_outcomes=requested_outcomes,
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record, regime),
        risks=list(
            dict.fromkeys(
                [
                    "Una suma de recibos no sustituye el cuadro por periodos, vencimientos y pagos.",
                    "La actualización de renta y los gastos repercutidos pueden estar discutidos o carecer de base.",
                    "La fianza no se compensa automáticamente durante la vigencia del arrendamiento.",
                    "El requerimiento para pago, MASC y enervación pueden exigir contenidos y efectos distintos.",
                    "La entrega de llaves transforma la pretensión posesoria en una liquidación de saldo.",
                    "La vulnerabilidad es una cuestión procesal y probatoria, no una conclusión automática.",
                    "No existe actualmente una suspensión extraordinaria general vigente hasta diciembre de 2026.",
                    "Cada renta puede tener cómputo de prescripción e interrupciones diferentes.",
                    "Seguro, aval, fianza y recuperaciones de terceros pueden modificar legitimación y saldo.",
                    *list(regime.warnings),
                ]
            )
        ),
        destination=destination,
        document_type=document_type,
        subject=" — ".join(subject_parts),
        legal_arguments=arguments,
        additional_requests=[
            "Contrato de arrendamiento, anexos, prórrogas y modificaciones.",
            "Título o documento que acredite legitimación del arrendador o cesionario.",
            "Cuadro de rentas por periodos, vencimientos, conceptos y saldo.",
            "Recibos, extractos, justificantes de pagos en efectivo y consignaciones.",
            "Facturas y pacto de suministros, comunidad, IBI u otros conceptos.",
            "Comunicación y cálculo de cualquier actualización de renta.",
            "Requerimiento íntegro, justificante de envío, recepción y contenido.",
            "Solicitud, recepción, sesiones, resultado y fecha final del MASC.",
            "Prueba de enervación anterior o pagos posteriores al requerimiento.",
            "Acta o recibo de entrega de llaves y liquidación de la fianza.",
            "Demanda, oposición, resoluciones, señalamiento y estado de ejecución, si existen.",
            "Documentación de vulnerabilidad y comunicaciones con servicios sociales, si constan.",
            "Aval, fianza personal, póliza de impago, indemnización y subrogación.",
            "Acuerdo de pago, calendario, reconocimiento de deuda e incumplimiento.",
        ],
        created_by_component=(
            "debt.unpaid_rent:"
            f"{DEBT_UNPAID_RENT_SPECIALIST_VERSION}+"
            f"{DEBT_UNPAID_RENT_REGIME_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
