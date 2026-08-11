"""Especialista RTM para reclamaciones de consumo general residual.

Construye una Previa Jurídica desde hechos congelados y una familia bloqueada.
Separa compra presencial, bienes, servicios, precio, conformidad, remedios,
cancelación, desistimiento, renovación, cláusulas, vales, daños y vías de
consumo. No invade especialistas sectoriales ni declara abusividad, cobertura,
responsabilidad, indemnización, competencia o prescripción.
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
from rtm_core.claims_consumer_regime import (
    CLAIMS_CONSUMER_REGIME_VERSION,
    ClaimsConsumerRegimeDecision,
    resolve_claims_consumer_regime,
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


CLAIMS_CONSUMER_SPECIALIST_VERSION = "rtm_claims_consumer_specialist_v1_0"

RouteState = Literal[
    "business",
    "business_period_review",
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


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(month=2, day=28, year=value.year + years)


def _all_text(record: ValidatedFactsRecord) -> str:
    values: list[Any] = []
    for key in (
        "descripcion_hecho",
        "incidencia_consumo_tipo",
        "tipo_contrato_consumo",
        "producto_servicio_consumo",
        "categoria_producto_consumo",
        "falta_conformidad_consumo_descripcion",
        "incumplimiento_servicio_consumo_descripcion",
        "motivo_cancelacion_consumo",
        "clausula_consumo_invocada",
        "respuesta_consumo",
        "respuesta_documentada",
        "solucion_solicitada_consumo",
        "solucion_solicitada",
    ):
        value, _ = validated_value(record, key)
        if _present(value):
            values.append(value)
    return _fold(values)


def _regime(record: ValidatedFactsRecord) -> ClaimsConsumerRegimeDecision:
    contract_date, _ = validated_value(
        record,
        "fecha_contrato_consumo",
        "fecha_documento",
    )
    delivery_date, _ = validated_value(record, "fecha_entrega_consumo")
    service_start, _ = validated_value(record, "fecha_inicio_servicio_consumo")
    expected_end, _ = validated_value(record, "fecha_fin_prevista_servicio_consumo")
    actual_end, _ = validated_value(record, "fecha_fin_real_servicio_consumo")
    incident_date, _ = validated_value(
        record,
        "fecha_manifestacion_falta_conformidad_consumo",
        "fecha_incidencia",
    )
    complaint_date, _ = validated_value(
        record,
        "reclamacion_previa_consumo_fecha",
        "reclamacion_previa_fecha",
    )
    withdrawal_date, _ = validated_value(record, "fecha_desistimiento_consumo")
    client_country, _ = validated_value(record, "pais_cliente_consumo")
    business_country, _ = validated_value(record, "pais_empresa_consumo")
    client_consumer, _ = validated_value(record, "cliente_consumo_es_consumidor")
    contract_type, _ = validated_value(record, "tipo_contrato_consumo")
    incident_type, _ = validated_value(record, "incidencia_consumo_tipo")

    kwargs = {
        "contract_date": contract_date,
        "delivery_date": delivery_date,
        "service_start_date": service_start,
        "expected_service_end_date": expected_end,
        "actual_service_end_date": actual_end,
        "incident_date": incident_date,
        "complaint_date": complaint_date,
        "withdrawal_notice_date": withdrawal_date,
        "client_country": client_country,
        "business_country": business_country,
        "client_is_consumer": client_consumer,
        "contract_type": contract_type,
        "incident_type": incident_type,
        "issue_text": _all_text(record),
        "in_store_purchase": validated_value(record, "compra_presencial_consumo")[0],
        "distance_contract": validated_value(record, "contrato_distancia_consumo")[0],
        "off_premises_contract": validated_value(
            record,
            "contrato_fuera_establecimiento_consumo",
        )[0],
        "online_purchase": validated_value(record, "compra_online_consumo")[0],
        "unsolicited_home_visit": validated_value(
            record,
            "visita_domicilio_no_solicitada_consumo",
        )[0],
        "promotional_excursion": validated_value(
            record,
            "excursion_promocional_consumo",
        )[0],
        "withdrawal_information_delivered": validated_value(
            record,
            "informacion_desistimiento_consumo_entregada",
        )[0],
        "service_start_during_withdrawal_requested": validated_value(
            record,
            "inicio_servicio_durante_desistimiento_solicitado",
        )[0],
        "service_start_express_consent": validated_value(
            record,
            "consentimiento_inicio_servicio_consumo",
        )[0],
        "withdrawal_loss_acknowledged": validated_value(
            record,
            "conocimiento_perdida_desistimiento_consumo",
        )[0],
        "service_fully_performed": validated_value(
            record,
            "servicio_consumo_completamente_ejecutado",
        )[0],
        "new_goods": validated_value(record, "bien_nuevo_consumo")[0],
        "second_hand_goods": validated_value(record, "bien_segunda_mano_consumo")[0],
        "second_hand_agreed_period_years": validated_value(
            record,
            "periodo_garantia_segunda_mano_pactado_anios",
        )[0],
        "large_business": validated_value(record, "empresa_consumo_gran_dimension")[0],
        "customer_service_act_applicable": validated_value(
            record,
            "ley_atencion_clientela_consumo_aplicable",
        )[0],
        "marketplace_involved": validated_value(record, "marketplace_consumo_implicado")[0],
        "telecommunications_involved": validated_value(
            record,
            "telecomunicaciones_consumo_implicadas",
        )[0],
        "energy_involved": validated_value(record, "energia_consumo_implicada")[0],
        "banking_or_payment_involved": validated_value(
            record,
            "banca_medio_pago_consumo_implicado",
        )[0],
        "insurance_involved": validated_value(record, "seguro_consumo_implicado")[0],
        "travel_involved": validated_value(record, "viaje_consumo_implicado")[0],
        "professional_service_involved": validated_value(
            record,
            "servicio_profesional_consumo_implicado",
        )[0],
        "public_administration_involved": validated_value(
            record,
            "administracion_publica_consumo_implicada",
        )[0],
        "housing_or_tenancy_involved": validated_value(
            record,
            "vivienda_arrendamiento_consumo_implicado",
        )[0],
        "healthcare_involved": validated_value(
            record,
            "servicio_sanitario_consumo_implicado",
        )[0],
        "legal_service_involved": validated_value(
            record,
            "servicio_juridico_consumo_implicado",
        )[0],
        "investment_involved": validated_value(record, "inversion_consumo_implicada")[0],
        "data_protection_primary": validated_value(
            record,
            "proteccion_datos_consumo_principal",
        )[0],
        "unsafe_product": validated_value(record, "producto_inseguro_consumo")[0],
        "personal_injury": validated_value(record, "lesion_personal_consumo")[0],
        "motor_vehicle_involved": validated_value(
            record,
            "vehiculo_motor_consumo_implicado",
        )[0],
        "digital_content_or_service": validated_value(
            record,
            "contenido_servicio_digital_consumo",
        )[0],
    }
    return resolve_claims_consumer_regime(**kwargs)


def _route_state(record: ValidatedFactsRecord) -> RouteState:
    prior_claim, _ = validated_value(
        record,
        "reclamacion_previa_consumo_fecha",
        "reclamacion_previa_fecha",
    )
    response, _ = validated_value(
        record,
        "respuesta_consumo",
        "respuesta_documentada",
    )
    response_date, _ = validated_value(record, "respuesta_consumo_fecha")
    if not _present(prior_claim):
        return "business"
    if _present(response) or _present(response_date):
        return "consumer_route_review"
    return "business_period_review"


def _required_missing(
    record: ValidatedFactsRecord,
    regime: ClaimsConsumerRegimeDecision,
) -> list[MissingItem]:
    groups: list[tuple[str, str, tuple[str, ...]]] = [
        (
            "consumer_fact_missing",
            "Falta validar la incidencia concreta de consumo.",
            ("descripcion_hecho", "incidencia_consumo_tipo"),
        ),
        (
            "consumer_business_missing",
            "Falta identificar a la empresa reclamada.",
            ("empresa_consumo", "proveedor", "emisor_documento"),
        ),
        (
            "consumer_status_missing",
            "Falta acreditar que el cliente actuó como consumidor.",
            ("cliente_consumo_es_consumidor",),
        ),
        (
            "consumer_contract_reference_missing",
            "Falta la referencia del contrato, ticket, factura o compra.",
            (
                "contrato_consumo_ref",
                "factura_ticket_consumo_ref",
                "contrato_ref",
                "factura_numero",
                "referencia_documento",
            ),
        ),
        (
            "consumer_contract_date_missing",
            "Falta la fecha documental de compra o contratación.",
            ("fecha_contrato_consumo", "fecha_documento"),
        ),
        (
            "consumer_object_missing",
            "Falta identificar el bien o servicio contratado.",
            ("producto_servicio_consumo", "producto_servicio"),
        ),
        (
            "consumer_price_or_payment_missing",
            "Falta el precio o importe pagado.",
            (
                "precio_pactado_consumo_eur",
                "precio_cobrado_consumo_eur",
                "importe_pagado_consumo_eur",
                "importe_pagado_eur",
            ),
        ),
        (
            "consumer_requested_solution_missing",
            "Falta la solución solicitada a la empresa.",
            ("solucion_solicitada_consumo", "solucion_solicitada"),
        ),
    ]

    if regime.contract_type in {"goods", "mixed"} or regime.incident_type == "goods_nonconformity":
        groups.extend(
            [
                (
                    "consumer_delivery_date_missing",
                    "Falta la fecha de entrega del bien.",
                    ("fecha_entrega_consumo",),
                ),
                (
                    "consumer_nonconformity_description_missing",
                    "Falta describir la falta de conformidad.",
                    ("falta_conformidad_consumo_descripcion",),
                ),
                (
                    "consumer_nonconformity_date_missing",
                    "Falta la fecha de manifestación o comunicación del defecto.",
                    (
                        "fecha_manifestacion_falta_conformidad_consumo",
                        "fecha_comunicacion_falta_conformidad_consumo",
                    ),
                ),
                (
                    "consumer_remedy_status_missing",
                    "Falta identificar el remedio solicitado u ofrecido.",
                    (
                        "reparacion_consumo_solicitada",
                        "sustitucion_consumo_solicitada",
                        "reduccion_precio_consumo_solicitada",
                        "resolucion_contrato_consumo_solicitada",
                        "reparacion_consumo_ofrecida",
                        "sustitucion_consumo_ofrecida",
                    ),
                ),
            ]
        )
    if regime.incident_type == "delivery_problem":
        groups.extend(
            [
                (
                    "consumer_delivery_status_missing",
                    "Falta el estado documental de la entrega.",
                    ("entrega_consumo_realizada", "entrega_consumo_parcial"),
                ),
                (
                    "consumer_delivery_commitment_missing",
                    "Falta la fecha pactada o efectiva de entrega.",
                    ("fecha_entrega_consumo", "fecha_fin_prevista_servicio_consumo"),
                ),
            ]
        )
    if regime.incident_type in {
        "service_nonperformance",
        "defective_or_incomplete_service",
        "delay",
    }:
        groups.extend(
            [
                (
                    "consumer_service_start_missing",
                    "Falta el inicio o periodo del servicio.",
                    ("fecha_inicio_servicio_consumo", "fecha_contrato_consumo"),
                ),
                (
                    "consumer_service_status_missing",
                    "Falta el estado de ejecución o descripción del incumplimiento.",
                    (
                        "servicio_consumo_no_prestado",
                        "servicio_consumo_incompleto",
                        "servicio_consumo_defectuoso",
                        "servicio_consumo_retrasado",
                        "incumplimiento_servicio_consumo_descripcion",
                    ),
                ),
            ]
        )
    if regime.incident_type == "price_or_unapproved_charge":
        groups.extend(
            [
                (
                    "consumer_price_comparison_missing",
                    "Faltan los importes publicitado, pactado o cobrado.",
                    (
                        "precio_publicitado_consumo_eur",
                        "precio_pactado_consumo_eur",
                        "precio_cobrado_consumo_eur",
                    ),
                ),
                (
                    "consumer_charge_disclosure_missing",
                    "Falta el estado de información del cargo adicional.",
                    ("cargo_adicional_informado_consumo",),
                ),
            ]
        )
    if regime.incident_type == "cancellation_or_refund":
        groups.extend(
            [
                (
                    "consumer_cancellation_date_missing",
                    "Falta la fecha de cancelación.",
                    ("fecha_cancelacion_consumo",),
                ),
                (
                    "consumer_refund_amount_missing",
                    "Falta el importe solicitado o reembolsado.",
                    (
                        "importe_reembolso_consumo_solicitado_eur",
                        "importe_reembolso_consumo_efectuado_eur",
                    ),
                ),
            ]
        )
    if regime.incident_type == "withdrawal":
        groups.extend(
            [
                (
                    "consumer_withdrawal_channel_missing",
                    "Falta acreditar si el contrato fue presencial, a distancia o fuera de establecimiento.",
                    (
                        "compra_presencial_consumo",
                        "contrato_distancia_consumo",
                        "contrato_fuera_establecimiento_consumo",
                    ),
                ),
                (
                    "consumer_withdrawal_notice_missing",
                    "Faltan comunicación y fecha de desistimiento.",
                    ("desistimiento_consumo_comunicado", "fecha_desistimiento_consumo"),
                ),
            ]
        )
    if regime.incident_type == "automatic_renewal_or_termination":
        groups.extend(
            [
                (
                    "consumer_renewal_or_termination_terms_missing",
                    "Faltan las condiciones de renovación, permanencia o baja.",
                    ("condiciones_consumo", "clausula_consumo_invocada"),
                ),
                (
                    "consumer_termination_timeline_missing",
                    "Faltan solicitud, fecha o confirmación de la baja.",
                    (
                        "baja_consumo_solicitada",
                        "fecha_baja_consumo",
                        "baja_consumo_confirmada",
                    ),
                ),
            ]
        )
    if regime.incident_type == "unfair_term":
        groups.append(
            (
                "consumer_clause_text_missing",
                "Falta el texto completo de la cláusula discutida.",
                ("clausula_consumo_invocada", "condiciones_consumo"),
            )
        )
    if regime.incident_type == "voucher_or_deposit":
        groups.append(
            (
                "consumer_voucher_or_deposit_terms_missing",
                "Faltan el vale, bono, depósito o sus condiciones.",
                (
                    "vale_bono_consumo_ref",
                    "importe_vale_bono_consumo_eur",
                    "deposito_senal_consumo_eur",
                    "condicion_devolucion_deposito_consumo",
                ),
            )
        )
    if regime.incident_type == "damage_or_loss":
        groups.extend(
            [
                (
                    "consumer_damage_amount_missing",
                    "Falta identificar el daño reclamado.",
                    ("importe_dano_consumo_eur",),
                ),
                (
                    "consumer_damage_evidence_missing",
                    "Faltan prueba y nexo causal del daño.",
                    ("prueba_dano_consumo_aportada", "nexo_causal_consumo_documentado"),
                ),
            ]
        )

    result: list[MissingItem] = []
    for code, description, keys in groups:
        values = [validated_value(record, key)[0] for key in keys]
        if not any(_present(value) for value in values):
            result.append(missing_item(code, description))

    client_country, _ = validated_value(record, "pais_cliente_consumo")
    business_country, _ = validated_value(record, "pais_empresa_consumo")
    if not _present(client_country) or not _present(business_country):
        result.append(
            missing_item(
                "consumer_country_missing",
                "Deben constar por separado los países del consumidor y de la empresa.",
            )
        )
    return dedupe_missing(result)


def _review_missing(
    record: ValidatedFactsRecord,
    regime: ClaimsConsumerRegimeDecision,
    route: RouteState,
) -> list[MissingItem]:
    result: list[MissingItem] = []
    if regime.status != "current":
        result.append(
            missing_item(
                "consumer_regime_review",
                regime.blocking_reason or "Debe determinarse el régimen jurídico aplicable.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if regime.contract_type == "unknown":
        result.append(
            missing_item(
                "consumer_contract_type_review",
                "Debe determinarse si el objeto principal es un bien, servicio o contrato mixto.",
                MissingItemSeverity.BLOCKING,
            )
        )

    contract = _parse_date(
        validated_value(record, "fecha_contrato_consumo", "fecha_documento")[0]
    )
    delivery = _parse_date(validated_value(record, "fecha_entrega_consumo")[0])
    start = _parse_date(validated_value(record, "fecha_inicio_servicio_consumo")[0])
    expected = _parse_date(validated_value(record, "fecha_fin_prevista_servicio_consumo")[0])
    actual = _parse_date(validated_value(record, "fecha_fin_real_servicio_consumo")[0])
    manifestation = _parse_date(
        validated_value(record, "fecha_manifestacion_falta_conformidad_consumo")[0]
    )
    repair_start = _parse_date(validated_value(record, "fecha_inicio_reparacion_consumo")[0])
    repair_end = _parse_date(validated_value(record, "fecha_fin_reparacion_consumo")[0])
    voucher_issue = _parse_date(validated_value(record, "fecha_emision_vale_bono_consumo")[0])
    voucher_expiry = _parse_date(validated_value(record, "fecha_caducidad_vale_bono_consumo")[0])

    if contract and delivery and delivery < contract:
        result.append(missing_item("consumer_delivery_before_contract_conflict", "La entrega aparece anterior al contrato.", MissingItemSeverity.BLOCKING))
    if contract and start and start < contract:
        result.append(missing_item("consumer_service_before_contract_conflict", "El servicio aparece iniciado antes del contrato.", MissingItemSeverity.BLOCKING))
    if start and expected and expected < start:
        result.append(missing_item("consumer_expected_end_before_start_conflict", "El fin previsto es anterior al inicio.", MissingItemSeverity.BLOCKING))
    if start and actual and actual < start:
        result.append(missing_item("consumer_actual_end_before_start_conflict", "El fin real es anterior al inicio.", MissingItemSeverity.BLOCKING))
    if repair_start and repair_end and repair_end < repair_start:
        result.append(missing_item("consumer_repair_chronology_conflict", "La reparación aparece finalizada antes de iniciarse.", MissingItemSeverity.BLOCKING))
    if voucher_issue and voucher_expiry and voucher_expiry < voucher_issue:
        result.append(missing_item("consumer_voucher_chronology_conflict", "La caducidad del vale aparece anterior a su emisión.", MissingItemSeverity.BLOCKING))

    advertised = _amount(validated_value(record, "precio_publicitado_consumo_eur")[0])
    agreed = _amount(validated_value(record, "precio_pactado_consumo_eur")[0])
    charged = _amount(validated_value(record, "precio_cobrado_consumo_eur")[0])
    paid = _amount(validated_value(record, "importe_pagado_consumo_eur", "importe_pagado_eur")[0])
    extra = _amount(validated_value(record, "cargo_adicional_consumo_eur")[0])
    refund_requested = _amount(validated_value(record, "importe_reembolso_consumo_solicitado_eur")[0])
    refunded = _amount(validated_value(record, "importe_reembolso_consumo_efectuado_eur")[0])
    damage = _amount(validated_value(record, "importe_dano_consumo_eur")[0])
    third_party = _amount(validated_value(record, "importe_recuperado_terceros_consumo_eur")[0])
    cancellation_penalty = _amount(validated_value(record, "penalizacion_cancelacion_consumo_eur")[0])
    post_termination_charge = _amount(validated_value(record, "cobro_posterior_baja_consumo_eur")[0])
    minimum_term_penalty = _amount(validated_value(record, "penalizacion_permanencia_consumo_eur")[0])
    voucher_amount = _amount(validated_value(record, "importe_vale_bono_consumo_eur")[0])
    deposit = _amount(validated_value(record, "deposito_senal_consumo_eur")[0])
    proportionate = _amount(validated_value(record, "importe_proporcional_servicio_consumo_eur")[0])

    for code, label, value in (
        ("consumer_negative_advertised_price", "precio publicitado", advertised),
        ("consumer_negative_agreed_price", "precio pactado", agreed),
        ("consumer_negative_charged_price", "precio cobrado", charged),
        ("consumer_negative_payment", "importe pagado", paid),
        ("consumer_negative_refund", "reembolso", refunded),
        ("consumer_negative_damage", "daño", damage),
        ("consumer_negative_voucher", "vale", voucher_amount),
        ("consumer_negative_deposit", "depósito", deposit),
    ):
        if value is not None and value < 0:
            result.append(missing_item(code, f"El {label} no puede ser negativo.", MissingItemSeverity.BLOCKING))

    if charged is not None and agreed is not None and charged > agreed + 0.01:
        result.append(
            missing_item(
                "consumer_charged_price_exceeds_agreed",
                "El precio cobrado supera el precio pactado y requiere revisar oferta, impuestos, extras y consentimiento.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if charged is not None and advertised is not None and charged > advertised + 0.01:
        result.append(
            missing_item(
                "consumer_charged_price_exceeds_advertised",
                "El precio cobrado supera el publicitado; debe revisarse la oferta y cualquier error manifiesto.",
                MissingItemSeverity.BLOCKING,
            )
        )
    extra_disclosed, _ = validated_value(record, "cargo_adicional_informado_consumo")
    if extra is not None and extra > 0 and extra_disclosed is not True:
        result.append(
            missing_item(
                "consumer_additional_charge_disclosure_review",
                "El cargo adicional exige información previa y aceptación documentadas.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if refunded is not None and paid is not None and refunded > paid + 0.01:
        result.append(missing_item("consumer_refund_exceeds_payment", "El reembolso documentado supera el importe pagado.", MissingItemSeverity.BLOCKING))
    if refund_requested is not None and paid is not None and refund_requested > paid + (damage or 0.0) + 0.01:
        result.append(
            missing_item(
                "consumer_refund_request_exceeds_documented_base",
                "El reembolso solicitado supera pago y daño documentados; deben separarse conceptos.",
                MissingItemSeverity.BLOCKING,
            )
        )
    recoveries = (refunded or 0.0) + (third_party or 0.0)
    recovery_base = max(paid or 0.0, damage or 0.0)
    if recoveries > recovery_base + 0.01 and recoveries > 0:
        result.append(
            missing_item(
                "consumer_double_recovery_amount_conflict",
                "Los reembolsos y recuperaciones superan el perjuicio documental y deben coordinarse.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if cancellation_penalty is not None and cancellation_penalty > 0:
        clause, _ = validated_value(record, "clausula_cancelacion_consumo_aportada")
        if clause is not True:
            result.append(missing_item("consumer_cancellation_clause_required", "La penalización de cancelación exige aportar la cláusula y su aceptación.", MissingItemSeverity.BLOCKING))
    if minimum_term_penalty is not None and minimum_term_penalty > 0:
        terms, _ = validated_value(record, "condiciones_consumo", "clausula_consumo_invocada")
        if not _present(terms):
            result.append(missing_item("consumer_minimum_term_terms_required", "La penalización de permanencia exige contrato, cálculo y prestación asociada.", MissingItemSeverity.BLOCKING))
    if post_termination_charge is not None and post_termination_charge > 0:
        result.append(
            missing_item(
                "consumer_post_termination_charge_review",
                "Consta un cobro posterior a la baja y debe comprobarse fecha efectiva, periodo y servicio devengado.",
                MissingItemSeverity.BLOCKING,
            )
        )

    second_hand, _ = validated_value(record, "bien_segunda_mano_consumo")
    second_hand_period = _amount(
        validated_value(record, "periodo_garantia_segunda_mano_pactado_anios")[0]
    )
    if second_hand is True and second_hand_period is None:
        result.append(
            missing_item(
                "consumer_second_hand_period_agreement_review",
                "Debe aportarse el pacto sobre el periodo de responsabilidad del bien de segunda mano.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if second_hand is True and second_hand_period is not None and second_hand_period < 1:
        result.append(missing_item("consumer_second_hand_period_below_minimum", "El periodo pactado para segunda mano figura por debajo de un año.", MissingItemSeverity.BLOCKING))

    if regime.goods_conformity_layer and delivery and manifestation:
        estimated_end = _add_years(delivery, regime.legal_conformity_period_years or 3)
        if manifestation > estimated_end:
            result.append(
                missing_item(
                    "consumer_nonconformity_outside_ordinary_period_review",
                    "La falta de conformidad aparece después del periodo ordinario estimado; deben revisarse suspensión, garantía comercial y otras acciones.",
                    MissingItemSeverity.BLOCKING,
                )
            )

    if regime.purchase_channel == "in_store" and regime.incident_type == "withdrawal":
        result.append(
            missing_item(
                "consumer_in_store_withdrawal_no_automatic_right",
                "Una compra presencial sin defecto no genera automáticamente desistimiento legal; debe revisarse la política comercial.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if regime.withdrawal_layer:
        fully_performed, _ = validated_value(record, "servicio_consumo_completamente_ejecutado")
        start_requested, _ = validated_value(record, "inicio_servicio_durante_desistimiento_solicitado")
        consent, _ = validated_value(record, "consentimiento_inicio_servicio_consumo")
        loss_ack, _ = validated_value(record, "conocimiento_perdida_desistimiento_consumo")
        if fully_performed is True and not all(
            value is True for value in (start_requested, consent, loss_ack)
        ):
            result.append(
                missing_item(
                    "consumer_full_performance_withdrawal_requirements_missing",
                    "La ejecución completa no elimina el desistimiento sin solicitud, consentimiento y conocimiento documentados.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        if proportionate is not None and proportionate > 0 and start_requested is not True:
            result.append(
                missing_item(
                    "consumer_proportionate_payment_request_required",
                    "El importe proporcional exige solicitud expresa de inicio durante el desistimiento.",
                    MissingItemSeverity.BLOCKING,
                )
            )

    retained, _ = validated_value(record, "producto_retenido_consumidor")
    if retained is True and paid is not None and refund_requested is not None:
        if refund_requested >= paid - 0.01:
            result.append(
                missing_item(
                    "consumer_full_refund_goods_return_review",
                    "La devolución íntegra exige coordinar restitución o puesta a disposición del bien.",
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )

    if regime.customer_service_layer == "active":
        result.append(
            missing_item(
                "consumer_customer_service_business_day_calendar_review",
                "El plazo de quince días hábiles requiere calendario aplicable y no equivale a quince días naturales.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    elif regime.customer_service_layer == "transition":
        result.append(
            missing_item(
                "consumer_customer_service_transition_review",
                "Debe comprobarse la adaptación transitoria de la Ley 10/2025.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    prior_claim, _ = validated_value(
        record,
        "reclamacion_previa_consumo_fecha",
        "reclamacion_previa_fecha",
    )
    prior_reference, _ = validated_value(
        record,
        "reclamacion_previa_consumo_ref",
        "referencia_documento",
        "expediente_ref",
    )
    if route == "business":
        result.append(
            missing_item(
                "consumer_prior_business_claim_recommended",
                "Debe formularse reclamación previa completa a la empresa y conservar justificante.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    elif route == "business_period_review":
        result.append(
            missing_item(
                "consumer_response_period_and_next_route_review",
                "Consta reclamación sin respuesta; debe comprobarse el plazo y la siguiente vía sin presumir silencio estimatorio.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    else:
        result.append(
            missing_item(
                "consumer_authority_or_adr_competence_review",
                "Debe comprobarse competencia de consumo, arbitraje, ADR o vía civil y la adhesión de la empresa.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if _present(prior_claim) and not _present(prior_reference):
        result.append(missing_item("consumer_prior_claim_reference_missing", "Falta el justificante o referencia de la reclamación previa.", MissingItemSeverity.HUMAN_REVIEW))

    complaint_form_requested, _ = validated_value(record, "hoja_reclamaciones_consumo_solicitada")
    complaint_form_delivered, _ = validated_value(record, "hoja_reclamaciones_consumo_entregada")
    if complaint_form_requested is True and complaint_form_delivered is False:
        result.append(
            missing_item(
                "consumer_complaint_form_local_rules_review",
                "La negativa a entregar hoja de reclamaciones exige verificar normativa autonómica y prueba del hecho.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    insolvent, _ = validated_value(record, "proveedor_insolvente_consumo")
    if insolvent is True:
        result.append(
            missing_item(
                "consumer_supplier_insolvency_route_review",
                "La insolvencia del proveedor exige revisar concurso, garantías, financiador, asegurador o tercero responsable.",
                MissingItemSeverity.BLOCKING,
            )
        )
    court_related, _ = validated_value(record, "procedimiento_judicial_consumo_relacionado")
    if court_related is True:
        result.append(
            missing_item(
                "consumer_related_court_proceeding_review",
                "Existe un procedimiento judicial relacionado; deben revisarse plazos, preclusión y compatibilidad de vías.",
                MissingItemSeverity.BLOCKING,
            )
        )
    return dedupe_missing(result)


def _utc(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _deadlines(
    record: ValidatedFactsRecord,
    regime: ClaimsConsumerRegimeDecision,
) -> list[Deadline]:
    result: list[Deadline] = []
    contract_value, contract_key = validated_value(
        record,
        "fecha_contrato_consumo",
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
                label="Periodo ordinario de desistimiento fuera de establecimiento",
                due_at=_utc(contract + timedelta(days=regime.withdrawal_days)),
                calculation_status="confirmed",
                source_fact_keys=[contract_key],
                notes=[
                    f"Cómputo de {regime.withdrawal_days} días naturales desde la celebración.",
                    "La aplicabilidad y las excepciones deben mantenerse documentadas.",
                ],
            )
        )

    withdrawal_value, withdrawal_key = validated_value(record, "fecha_desistimiento_consumo")
    withdrawal = _parse_date(withdrawal_value)
    notified, _ = validated_value(record, "desistimiento_consumo_comunicado")
    if regime.status == "current" and regime.withdrawal_layer and notified is True and withdrawal and withdrawal_key:
        result.append(
            Deadline(
                label="Referencia de reembolso tras desistimiento comunicado",
                due_at=_utc(withdrawal + timedelta(days=14)),
                calculation_status="estimated",
                source_fact_keys=[withdrawal_key],
                notes=[
                    "Referencia de catorce días naturales desde la comunicación.",
                    "Debe revisarse restitución de bienes e importe proporcional del servicio, si procede.",
                ],
            )
        )

    delivery_value, delivery_key = validated_value(record, "fecha_entrega_consumo")
    delivery = _parse_date(delivery_value)
    if (
        regime.status == "current"
        and regime.goods_conformity_layer
        and regime.legal_conformity_period_years
        and delivery is not None
        and delivery_key
    ):
        result.append(
            Deadline(
                label="Fin ordinario estimado del periodo de conformidad del bien",
                due_at=_utc(_add_years(delivery, regime.legal_conformity_period_years)),
                calculation_status="estimated",
                source_fact_keys=[delivery_key],
                notes=[
                    "Estimación desde la entrega; puede verse afectada por segunda mano, reparación, suspensión y garantía comercial.",
                ],
            )
        )
    return result


def _summary(
    record: ValidatedFactsRecord,
    regime: ClaimsConsumerRegimeDecision,
) -> tuple[list[str], list[str]]:
    rows, used = summary_rows(
        record,
        (
            ("empresa_consumo", "Empresa", ""),
            ("establecimiento_consumo", "Establecimiento", ""),
            ("contrato_consumo_ref", "Contrato o compra", ""),
            ("fecha_contrato_consumo", "Fecha de contratación", ""),
            ("producto_servicio_consumo", "Bien o servicio", ""),
            ("tipo_contrato_consumo", "Tipo contractual", ""),
            ("fecha_entrega_consumo", "Entrega", ""),
            ("falta_conformidad_consumo_descripcion", "Falta de conformidad", ""),
            ("incumplimiento_servicio_consumo_descripcion", "Incumplimiento", ""),
            ("precio_publicitado_consumo_eur", "Precio publicitado", " EUR"),
            ("precio_pactado_consumo_eur", "Precio pactado", " EUR"),
            ("precio_cobrado_consumo_eur", "Precio cobrado", " EUR"),
            ("importe_pagado_consumo_eur", "Pagado", " EUR"),
            ("importe_reembolso_consumo_efectuado_eur", "Reembolsado", " EUR"),
        ),
    )
    rows.insert(
        0,
        (
            f"Encuadre residual: contrato {regime.contract_type}; canal "
            f"{regime.purchase_channel}; incidencia {regime.incident_type}; "
            f"régimen {regime.status}."
        ),
    )
    return list(dict.fromkeys(rows)), list(dict.fromkeys(used))


def build_claims_consumer_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="claims",
        family="consumo",
        specialist="claims.consumer",
    )
    regime = _regime(facts_record)
    route = _route_state(facts_record)
    basis = list(regime.legal_basis) if regime.status == "current" else []

    business, business_key = validated_value(
        facts_record,
        "empresa_consumo",
        "proveedor",
        "emisor_documento",
    )
    contract_ref, contract_key = validated_value(
        facts_record,
        "contrato_consumo_ref",
        "factura_ticket_consumo_ref",
        "contrato_ref",
        "factura_numero",
        "referencia_documento",
    )
    solution, solution_key = validated_value(
        facts_record,
        "solucion_solicitada_consumo",
        "solucion_solicitada",
    )
    _, fact_key = validated_value(
        facts_record,
        "descripcion_hecho",
        "incidencia_consumo_tipo",
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
        "consumer_contract_offer_and_status",
        "Condición de consumidor, contrato, oferta y precio",
        (
            "La reclamación debe identificar partes, finalidad de consumo, canal, "
            "contrato, oferta, publicidad, precio, impuestos, cargos y prueba de pago."
        ),
        (
            business_key,
            contract_key,
            "cliente_consumo_es_consumidor",
            "pais_cliente_consumo",
            "pais_empresa_consumo",
            "compra_presencial_consumo",
            "contrato_fuera_establecimiento_consumo",
            "publicidad_oferta_consumo",
            "condiciones_consumo",
            "precio_publicitado_consumo_eur",
            "precio_pactado_consumo_eur",
            "precio_cobrado_consumo_eur",
            "importe_pagado_consumo_eur",
            fact_key,
        ),
        "primary",
    )
    if regime.contract_type in {"goods", "mixed"}:
        add(
            "consumer_goods_conformity_and_remedies",
            "Conformidad del bien y remedios",
            (
                "Deben compararse características pactadas, entrega, manifestación "
                "del defecto y respuesta empresarial. Reparación, sustitución, "
                "reducción y resolución no se conceden sin revisar su secuencia, "
                "viabilidad, proporcionalidad y gravedad."
            ),
            (
                "producto_servicio_consumo",
                "bien_nuevo_consumo",
                "bien_segunda_mano_consumo",
                "periodo_garantia_segunda_mano_pactado_anios",
                "fecha_entrega_consumo",
                "falta_conformidad_consumo_descripcion",
                "fecha_manifestacion_falta_conformidad_consumo",
                "fecha_comunicacion_falta_conformidad_consumo",
                "reparacion_consumo_solicitada",
                "reparacion_consumo_ofrecida",
                "reparacion_consumo_completada",
                "sustitucion_consumo_solicitada",
                "sustitucion_consumo_ofrecida",
                "sustitucion_consumo_completada",
                "reduccion_precio_consumo_solicitada",
                "resolucion_contrato_consumo_solicitada",
                fact_key,
            ),
            "primary",
        )
    if regime.contract_type in {"service", "mixed"}:
        add(
            "consumer_service_performance_and_delay",
            "Prestación, ejecución, defectos y retraso del servicio",
            (
                "Debe reconstruirse qué servicio se contrató, cuándo debía empezar "
                "y terminar, qué se ejecutó y qué incumplimiento concreto se imputa, "
                "sin aplicar al servicio una garantía de bienes por analogía."
            ),
            (
                "producto_servicio_consumo",
                "fecha_inicio_servicio_consumo",
                "fecha_fin_prevista_servicio_consumo",
                "fecha_fin_real_servicio_consumo",
                "servicio_consumo_no_prestado",
                "servicio_consumo_incompleto",
                "servicio_consumo_defectuoso",
                "servicio_consumo_retrasado",
                "incumplimiento_servicio_consumo_descripcion",
                fact_key,
            ),
            "primary",
        )
    add(
        "consumer_delivery_price_and_additional_charges",
        "Entrega, precio y cargos adicionales",
        (
            "La empresa debe explicar entrega, precio y cada cargo. La oferta puede "
            "integrar el contrato, pero cualquier diferencia exige revisar impuestos, "
            "extras consentidos y posibles errores manifiestos."
        ),
        (
            "entrega_consumo_realizada",
            "entrega_consumo_parcial",
            "fecha_entrega_consumo",
            "precio_publicitado_consumo_eur",
            "precio_pactado_consumo_eur",
            "precio_cobrado_consumo_eur",
            "cargo_adicional_consumo_eur",
            "cargo_adicional_informado_consumo",
            "factura_ticket_consumo_ref",
            fact_key,
        ),
        "primary",
    )
    add(
        "consumer_cancellation_withdrawal_renewal_and_termination",
        "Cancelación, desistimiento, renovación y baja",
        (
            "La cancelación contractual, la devolución comercial y el desistimiento "
            "legal son figuras distintas. Deben revisarse canal, información, plazo, "
            "inicio del servicio, renovación, permanencia, baja y cobros posteriores."
        ),
        (
            "fecha_cancelacion_consumo",
            "motivo_cancelacion_consumo",
            "penalizacion_cancelacion_consumo_eur",
            "clausula_cancelacion_consumo_aportada",
            "compra_presencial_consumo",
            "contrato_fuera_establecimiento_consumo",
            "desistimiento_consumo_comunicado",
            "fecha_desistimiento_consumo",
            "informacion_desistimiento_consumo_entregada",
            "inicio_servicio_durante_desistimiento_solicitado",
            "consentimiento_inicio_servicio_consumo",
            "conocimiento_perdida_desistimiento_consumo",
            "servicio_consumo_completamente_ejecutado",
            "renovacion_automatica_consumo",
            "fecha_aviso_renovacion_consumo",
            "baja_consumo_solicitada",
            "fecha_baja_consumo",
            "baja_consumo_confirmada",
            "cobro_posterior_baja_consumo_eur",
            "permanencia_consumo_invocada",
            "penalizacion_permanencia_consumo_eur",
            fact_key,
        ),
        "primary",
    )
    add(
        "consumer_terms_vouchers_and_deposits",
        "Cláusulas, vales, bonos y depósitos",
        (
            "Debe aportarse el texto completo de las condiciones y comprobarse su "
            "incorporación, transparencia, negociación y equilibrio. La caducidad "
            "de vales o devolución de depósitos no admite una regla universal."
        ),
        (
            "condiciones_consumo",
            "clausula_consumo_invocada",
            "clausula_consumo_negociada_individualmente",
            "vale_bono_consumo_ref",
            "fecha_emision_vale_bono_consumo",
            "fecha_caducidad_vale_bono_consumo",
            "importe_vale_bono_consumo_eur",
            "deposito_senal_consumo_eur",
            "condicion_devolucion_deposito_consumo",
            fact_key,
        ),
    )
    add(
        "consumer_refund_damage_and_no_double_recovery",
        "Reembolso, daño, causalidad y coordinación de recuperaciones",
        (
            "Precio, reembolso y daños deben separarse. Cada perjuicio requiere "
            "prueba y causalidad, descontando devoluciones, tarjeta, seguro o pagos "
            "de terceros para no duplicar la recuperación."
        ),
        (
            "importe_pagado_consumo_eur",
            "importe_reembolso_consumo_solicitado_eur",
            "importe_reembolso_consumo_efectuado_eur",
            "importe_dano_consumo_eur",
            "prueba_dano_consumo_aportada",
            "nexo_causal_consumo_documentado",
            "importe_recuperado_terceros_consumo_eur",
            "producto_puesto_disposicion_empresa_consumo",
            "producto_retenido_consumidor",
            fact_key,
        ),
        "primary",
    )
    add(
        "consumer_prior_claim_complaint_form_and_adr",
        "Reclamación previa, hoja de reclamaciones, arbitraje y ADR",
        (
            "La reclamación previa debe ser completa y trazable. Hoja de "
            "reclamaciones, consumo, arbitraje y ADR dependen de territorio, "
            "competencia y adhesión; no se presume una vía ni un resultado."
        ),
        (
            "reclamacion_previa_consumo_fecha",
            "reclamacion_previa_consumo_ref",
            "canal_reclamacion_consumo",
            "respuesta_consumo_fecha",
            "respuesta_consumo",
            "hoja_reclamaciones_consumo_solicitada",
            "hoja_reclamaciones_consumo_entregada",
            "empresa_adherida_arbitraje_consumo",
            "entidad_adr_consumo",
            "fecha_reclamacion_adr_consumo",
            solution_key,
            fact_key,
        ),
    )
    add(
        "consumer_residual_boundary_and_limitation",
        "Carácter residual, fronteras sectoriales y plazos",
        (
            "Este especialista solo opera cuando no existe familia sectorial. "
            "Antes de fijar plazos deben comprobarse materia principal, acción, "
            "entrega, reparación, dies a quo, interrupciones y normativa territorial."
        ),
        (
            "compra_online_consumo",
            "marketplace_consumo_implicado",
            "telecomunicaciones_consumo_implicadas",
            "energia_consumo_implicada",
            "banca_medio_pago_consumo_implicado",
            "seguro_consumo_implicado",
            "viaje_consumo_implicado",
            "servicio_profesional_consumo_implicado",
            "administracion_publica_consumo_implicada",
            "vivienda_arrendamiento_consumo_implicado",
            "servicio_sanitario_consumo_implicado",
            "servicio_juridico_consumo_implicado",
            "inversion_consumo_implicada",
            "proteccion_datos_consumo_principal",
            "producto_inseguro_consumo",
            "lesion_personal_consumo",
            "vehiculo_motor_consumo_implicado",
            "contenido_servicio_digital_consumo",
            fact_key,
        ),
    )

    if not arguments:
        raise HTTPException(
            status_code=409,
            detail="No existen hechos validados suficientes para construir la previa de consumo.",
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
            *fact_review_items(facts_record, prefix="consumer"),
        ]
    )

    destination = str(business).strip() if _present(business) else "EMPRESA PENDIENTE DE VALIDAR"
    document_type = "RECLAMACIÓN PREVIA DE CONSUMO A LA EMPRESA"
    if route == "consumer_route_review":
        document_type = "RECLAMACIÓN ANTE CONSUMO, ARBITRAJE O ADR — COMPETENCIA PENDIENTE DE VALIDAR"

    subject_parts = ["RECLAMACIÓN DE CONSUMO", regime.incident_type.upper()]
    if _present(contract_ref):
        subject_parts.append(f"referencia {contract_ref}")

    strategy = (
        "Delimitar la relación de consumo y excluir materias sectoriales; fijar "
        "contrato, oferta, precio y cronología; comparar lo pactado con lo entregado "
        "o prestado; y solicitar únicamente remedios, reembolsos o daños acreditados."
    )
    if _present(solution):
        strategy += f" La solución solicitada es: {_display(solution)}."

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="consumo",
        specialist="claims.consumer",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Incidencia de consumo ({regime.incident_type}) en la referencia {_display(contract_ref)}."
            if _present(contract_ref)
            else "Posible incidencia de consumo general pendiente de completar."
        ),
        client_goal=(
            "Obtener cumplimiento, reparación, sustitución, reducción, resolución, "
            "reembolso o reparación del daño sin invadir una vía sectorial."
        ),
        primary_strategy=strategy,
        secondary_strategies=[
            "Solicitar contrato, ticket, factura, publicidad, condiciones y comunicaciones.",
            "Requerir reparación o subsanación cuando resulte viable antes de resolver.",
            "Separar devolución comercial, cancelación y desistimiento legal.",
            "Valorar consumo, arbitraje, ADR o vía civil después de verificar competencia.",
            "Preservar acciones frente a financiador, asegurador o tercero sin duplicar cobros.",
        ],
        requested_outcomes=[
            "Confirmación del contrato, precio, oferta, entrega y condiciones aplicables.",
            "Explicación motivada del incumplimiento, defecto, retraso o cargo discutido.",
            "Reparación o sustitución cuando sean procedentes y proporcionadas.",
            "Reducción del precio o resolución cuando concurran sus presupuestos.",
            "Cancelación o baja efectiva con cese de cobros no devengados.",
            "Reembolso de cantidades no justificadas, no devengadas o indebidamente retenidas.",
            "Indemnización únicamente de daños acreditados, causales y no recuperados.",
            "Información sobre hoja de reclamaciones, arbitraje y entidad ADR competente.",
        ],
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record, regime),
        risks=list(
            dict.fromkeys(
                [
                    "Consumo general es residual y no debe absorber una familia sectorial.",
                    "La devolución por cambio de opinión en tienda física depende de política comercial salvo defecto.",
                    "La garantía comercial no sustituye los derechos legales de conformidad.",
                    "La elección de remedio depende de viabilidad, proporcionalidad, gravedad y actuaciones previas.",
                    "Precio publicitado y error manifiesto requieren análisis documental.",
                    "La hoja de reclamaciones y la autoridad competente dependen de normativa territorial.",
                    "Daño, causalidad y cuantía requieren prueba separada.",
                    "Los plazos no pueden fijarse sin acción, dies a quo, interrupciones y suspensión.",
                    *list(regime.warnings),
                ]
            )
        ),
        destination=destination,
        document_type=document_type,
        subject=" — ".join(subject_parts),
        legal_arguments=arguments,
        additional_requests=[
            "Contrato, pedido, ticket, factura y justificante de pago.",
            "Oferta, publicidad, etiquetado, presupuesto y condiciones aplicables.",
            "Prueba de entrega, instalación, inicio y finalización del servicio.",
            "Descripción del defecto, fotografías, informe y comunicaciones.",
            "Historial de reparación, sustitución, subsanación o puesta a disposición.",
            "Comunicación de cancelación, baja o desistimiento y prueba de recepción.",
            "Desglose de precio, cargos, penalizaciones, reembolsos y recuperaciones.",
            "Texto íntegro de cláusulas, permanencia, renovación, vale o depósito.",
            "Prueba de cada daño, causalidad, mitigación y cuantificación.",
            "Reclamación previa, respuesta, hoja de reclamaciones y ADR invocada.",
        ],
        created_by_component=(
            "claims.consumer:"
            f"{CLAIMS_CONSUMER_SPECIALIST_VERSION}+"
            f"{CLAIMS_CONSUMER_REGIME_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
