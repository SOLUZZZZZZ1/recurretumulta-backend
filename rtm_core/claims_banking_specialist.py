"""Especialista RTM para banca, cuentas y medios de pago.

Construye una Previa Jurídica conservadora desde hechos congelados. Separa
consentimiento y autenticación, operación no autorizada y pago bajo engaño,
ejecución, adeudos, instrumentos, transferencias instantáneas, comisiones y
bloqueos. No decide negligencia grave ni aplica automáticamente reembolsos.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import HTTPException

from rtm_core.authority_repository import (
    FamilyResolutionRecord,
    ValidatedFactsRecord,
)
from rtm_core.claims_banking_regime import (
    CLAIMS_BANKING_REGIME_VERSION,
    ClaimsBankingRegimeDecision,
    resolve_claims_banking_regime,
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


CLAIMS_BANKING_SPECIALIST_VERSION = "rtm_claims_banking_specialist_v1_0"

RouteState = Literal["entity", "entity_period_review", "supervisor_review"]


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


def _add_months(value: date, months: int) -> date:
    index = value.month - 1 + months
    year = value.year + index // 12
    month = index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _utc(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _all_text(record: ValidatedFactsRecord) -> str:
    values: list[Any] = []
    for key in (
        "descripcion_hecho",
        "producto_servicio",
        "incidencia_bancaria_tipo",
        "instrumento_pago_tipo",
        "modalidad_fraude_bancario",
        "motivo_negligencia_grave",
        "motivo_no_reembolso_bancario",
        "resultado_verificacion_beneficiario",
        "resultado_intento_recobro",
        "motivo_bloqueo_cuenta",
        "comision_bancaria_concepto",
        "respuesta_proveedor",
        "respuesta_documentada",
        "solucion_solicitada",
    ):
        value, _ = validated_value(record, key)
        if _present(value):
            values.append(value)
    return _fold(values)


def _regime(record: ValidatedFactsRecord) -> ClaimsBankingRegimeDecision:
    incident_date, _ = validated_value(
        record,
        "fecha_operacion_pago",
        "fecha_adeudo_domiciliado",
        "fecha_bloqueo_cuenta",
        "fecha_aplicacion_modificacion_bancaria",
        "fecha_incidencia",
        "fecha_documento",
    )
    contract_date, _ = validated_value(record, "fecha_contrato")
    complaint_date, _ = validated_value(record, "reclamacion_previa_fecha")
    bank_country, _ = validated_value(record, "pais_entidad_bancaria")
    user_type, _ = validated_value(record, "tipo_usuario_bancario")
    incident_type, _ = validated_value(record, "incidencia_bancaria_tipo")
    operation_authorized, _ = validated_value(record, "operacion_autorizada")
    deceived, _ = validated_value(record, "usuario_ordeno_pago_bajo_engano")
    instant, _ = validated_value(record, "transferencia_instantanea")
    loan, _ = validated_value(record, "prestamo_credito_implicado")
    investment, _ = validated_value(record, "producto_inversion_implicado")
    crypto, _ = validated_value(record, "criptoactivo_implicado")
    return resolve_claims_banking_regime(
        incident_date=incident_date,
        contract_date=contract_date,
        complaint_date=complaint_date,
        bank_country=bank_country,
        user_type=user_type,
        incident_type=incident_type,
        issue_text=_all_text(record),
        operation_authorized=operation_authorized,
        payer_initiated_under_deception=deceived,
        instant_transfer=instant,
        loan_involved=loan,
        investment_involved=investment,
        crypto_involved=crypto,
    )


def _route_state(record: ValidatedFactsRecord) -> RouteState:
    prior_claim, _ = validated_value(record, "reclamacion_previa_fecha")
    response, _ = validated_value(
        record,
        "respuesta_proveedor",
        "respuesta_documentada",
        "motivo_no_reembolso_bancario",
    )
    response_date, _ = validated_value(
        record,
        "fecha_respuesta_bancaria",
        "fecha_respuesta",
    )
    if not _present(prior_claim):
        return "entity"
    if _present(response) or _present(response_date):
        return "supervisor_review"
    return "entity_period_review"


def _required_missing(
    record: ValidatedFactsRecord,
    regime: ClaimsBankingRegimeDecision,
) -> list[MissingItem]:
    groups: list[tuple[str, str, tuple[str, ...]]] = [
        (
            "banking_fact_missing",
            "Falta validar la incidencia bancaria concreta.",
            ("descripcion_hecho", "incidencia_bancaria_tipo"),
        ),
        (
            "banking_entity_missing",
            "Falta identificar la entidad bancaria o proveedor de pagos.",
            ("entidad_bancaria", "proveedor_servicios_pago", "proveedor"),
        ),
        (
            "banking_country_missing",
            "Falta validar el país de la entidad o servicio.",
            ("pais_entidad_bancaria",),
        ),
        (
            "banking_product_missing",
            "Falta identificar la cuenta, tarjeta, transferencia o servicio afectado.",
            ("producto_servicio", "instrumento_pago_tipo"),
        ),
        (
            "banking_reference_missing",
            "Falta una referencia de cuenta, contrato u operación.",
            (
                "cuenta_iban",
                "cuenta_bancaria_ref",
                "contrato_ref",
                "operacion_pago_ref",
                "referencia_servicio",
            ),
        ),
        (
            "banking_incident_date_missing",
            "Falta la fecha documental de operación o incidencia.",
            (
                "fecha_operacion_pago",
                "fecha_adeudo_domiciliado",
                "fecha_bloqueo_cuenta",
                "fecha_incidencia",
                "fecha_documento",
            ),
        ),
        (
            "banking_requested_solution_missing",
            "Falta validar la solución solicitada.",
            ("solucion_solicitada",),
        ),
    ]

    if regime.payment_service:
        groups.extend(
            [
                (
                    "banking_payment_reference_missing",
                    "Falta la referencia de la operación de pago.",
                    ("operacion_pago_ref", "referencia_documento"),
                ),
                (
                    "banking_payment_amount_missing",
                    "Falta el importe de la operación discutida.",
                    (
                        "importe_operacion_pago_eur",
                        "importe_adeudo_domiciliado_eur",
                        "importe_reclamado_eur",
                    ),
                ),
                (
                    "banking_authorization_status_missing",
                    "Falta validar si la operación fue autorizada o consentida.",
                    ("operacion_autorizada", "consentimiento_pago_acreditado"),
                ),
            ]
        )

    if regime.incident_type == "unauthorized_payment":
        groups.extend(
            [
                (
                    "banking_notification_date_missing",
                    "Falta la fecha de comunicación de la operación a la entidad.",
                    ("fecha_comunicacion_entidad",),
                ),
                (
                    "banking_authentication_record_status_missing",
                    "Falta validar si la entidad aportó el registro de autenticación.",
                    ("registro_autenticacion_aportado", "respuesta_documentada"),
                ),
                (
                    "banking_refund_decision_missing",
                    "Falta el reembolso, abono provisional o motivo de rechazo.",
                    (
                        "importe_reembolsado_banco_eur",
                        "abono_bancario_provisional",
                        "motivo_no_reembolso_bancario",
                    ),
                ),
            ]
        )
    elif regime.incident_type == "authorized_scam":
        groups.extend(
            [
                (
                    "banking_deception_status_missing",
                    "Falta validar que el usuario ordenó el pago bajo engaño.",
                    ("usuario_ordeno_pago_bajo_engano",),
                ),
                (
                    "banking_fraud_method_missing",
                    "Falta documentar la modalidad de fraude o suplantación.",
                    ("modalidad_fraude_bancario",),
                ),
                (
                    "banking_payee_missing",
                    "Falta identificar al beneficiario del pago.",
                    ("beneficiario_pago", "identificador_unico_pago"),
                ),
                (
                    "banking_notification_date_missing",
                    "Falta la fecha de comunicación a la entidad.",
                    ("fecha_comunicacion_entidad",),
                ),
            ]
        )
    elif regime.incident_type == "incorrect_execution":
        groups.extend(
            [
                (
                    "banking_unique_identifier_missing",
                    "Falta el identificador único utilizado en la orden.",
                    ("identificador_unico_pago",),
                ),
                (
                    "banking_execution_status_missing",
                    "Falta validar cómo se ejecutó la orden y a quién es imputable el error.",
                    (
                        "operacion_ejecutada_correctamente",
                        "identificador_unico_incorrecto",
                        "error_ejecucion_imputable_entidad",
                    ),
                ),
            ]
        )
    elif regime.incident_type == "direct_debit_refund":
        groups.extend(
            [
                (
                    "banking_direct_debit_date_missing",
                    "Falta la fecha del adeudo domiciliado.",
                    ("fecha_adeudo_domiciliado",),
                ),
                (
                    "banking_direct_debit_amount_missing",
                    "Falta el importe del adeudo.",
                    ("importe_adeudo_domiciliado_eur",),
                ),
                (
                    "banking_direct_debit_request_missing",
                    "Falta la fecha de solicitud de devolución.",
                    ("fecha_solicitud_devolucion_adeudo",),
                ),
            ]
        )
    elif regime.incident_type == "payment_instrument_loss":
        groups.append(
            (
                "banking_lost_instrument_notice_missing",
                "Falta la fecha de notificación de pérdida, robo o bloqueo.",
                (
                    "fecha_notificacion_perdida_tarjeta",
                    "fecha_bloqueo_instrumento",
                    "fecha_comunicacion_entidad",
                ),
            )
        )
    elif regime.incident_type == "instant_transfer_verification":
        groups.extend(
            [
                (
                    "banking_instant_transfer_status_missing",
                    "Falta validar que la transferencia era instantánea.",
                    ("transferencia_instantanea",),
                ),
                (
                    "banking_payee_verification_missing",
                    "Falta el resultado de verificación del beneficiario.",
                    (
                        "verificacion_beneficiario_realizada",
                        "resultado_verificacion_beneficiario",
                        "advertencia_discrepancia_beneficiario",
                    ),
                ),
            ]
        )
    elif regime.incident_type == "account_blocking":
        groups.extend(
            [
                (
                    "banking_account_block_date_missing",
                    "Falta la fecha del bloqueo o desbloqueo.",
                    ("fecha_bloqueo_cuenta", "fecha_desbloqueo_cuenta"),
                ),
                (
                    "banking_account_block_reason_missing",
                    "Falta el motivo comunicado del bloqueo.",
                    ("motivo_bloqueo_cuenta",),
                ),
            ]
        )
    elif regime.incident_type == "fees_or_exchange":
        groups.extend(
            [
                (
                    "banking_fee_amount_missing",
                    "Falta el importe de la comisión o coste discutido.",
                    ("comision_bancaria_eur", "importe_reclamado_eur"),
                ),
                (
                    "banking_fee_concept_missing",
                    "Falta el concepto de la comisión o conversión.",
                    ("comision_bancaria_concepto",),
                ),
            ]
        )
    elif regime.incident_type == "contract_change_or_closure":
        groups.extend(
            [
                (
                    "banking_contract_change_dates_missing",
                    "Faltan las fechas de aviso y aplicación o cierre.",
                    (
                        "fecha_aviso_modificacion_bancaria",
                        "fecha_aplicacion_modificacion_bancaria",
                        "fecha_aviso_cierre_cuenta",
                        "fecha_cierre_cuenta",
                    ),
                ),
                (
                    "banking_framework_contract_missing",
                    "Falta el contrato marco o referencia de cuenta afectada.",
                    ("contrato_ref", "cuenta_iban", "cuenta_bancaria_ref"),
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
    regime: ClaimsBankingRegimeDecision,
    route: RouteState,
) -> list[MissingItem]:
    result: list[MissingItem] = []
    if regime.status != "current":
        result.append(
            missing_item(
                "banking_regime_review",
                regime.blocking_reason or "Debe determinarse el régimen bancario aplicable.",
                MissingItemSeverity.BLOCKING,
            )
        )

    customer_type, _ = validated_value(record, "tipo_usuario_bancario")
    if not _present(customer_type):
        result.append(
            missing_item(
                "banking_customer_type_review",
                "Debe validarse si el cliente es consumidor, microempresa u otro usuario.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    prior_claim, _ = validated_value(record, "reclamacion_previa_fecha")
    claim_channel, _ = validated_value(record, "canal_reclamacion")
    claim_reference, _ = validated_value(
        record,
        "reclamacion_bancaria_ref",
        "referencia_documento",
        "expediente_ref",
    )
    if route == "entity":
        result.append(
            missing_item(
                "banking_prior_entity_claim_required",
                "Debe reclamarse primero al servicio de atención de la entidad y conservar justificante.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    elif route == "entity_period_review":
        description = (
            "Consta reclamación de servicios de pago sin respuesta; debe computarse el plazo de quince días hábiles."
            if regime.complaint_response_business_days == 15
            else (
                "Consta reclamación previa sin respuesta; debe verificarse el plazo de uno o dos meses según el tipo de cliente."
            )
        )
        result.append(
            missing_item(
                "banking_entity_response_period_review",
                description,
                MissingItemSeverity.BLOCKING,
            )
        )
    else:
        result.append(
            missing_item(
                "banking_supervisor_competence_review",
                "Debe comprobarse la competencia del Banco de España y la admisibilidad de la reclamación.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if _present(prior_claim) and not _present(claim_channel):
        result.append(
            missing_item(
                "banking_claim_channel_missing",
                "Falta el canal de la reclamación previa.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if _present(prior_claim) and not _present(claim_reference):
        result.append(
            missing_item(
                "banking_claim_reference_missing",
                "Falta el número o justificante de la reclamación previa.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    authorized, _ = validated_value(record, "operacion_autorizada")
    consent, _ = validated_value(record, "consentimiento_pago_acreditado")
    sca, _ = validated_value(record, "autenticacion_reforzada_aplicada")
    auth_log, _ = validated_value(record, "registro_autenticacion_aportado")
    if authorized is True and consent is False:
        result.append(
            missing_item(
                "banking_authorization_consent_conflict",
                "La operación figura autorizada pero el consentimiento aparece no acreditado.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if authorized is False and consent is True:
        result.append(
            missing_item(
                "banking_unauthorized_consent_conflict",
                "La operación figura no autorizada y simultáneamente consentida.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if regime.incident_type == "unauthorized_payment" and sca is True:
        result.append(
            missing_item(
                "banking_authentication_not_consent_review",
                "La autenticación reforzada debe analizarse, pero no prueba por sí sola el consentimiento.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if regime.incident_type == "unauthorized_payment" and auth_log is not True:
        result.append(
            missing_item(
                "banking_authentication_log_review",
                "Debe requerirse el registro íntegro de autenticación, dispositivo, IP, hora y alertas.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    fraud, _ = validated_value(record, "fraude_usuario_invocado")
    gross, _ = validated_value(record, "negligencia_grave_invocada")
    gross_reason, _ = validated_value(record, "motivo_negligencia_grave")
    if (fraud is True or gross is True) and not _present(gross_reason):
        result.append(
            missing_item(
                "banking_fraud_or_gross_negligence_evidence_missing",
                "La entidad invoca fraude o negligencia grave sin hechos y prueba suficientes.",
                MissingItemSeverity.BLOCKING,
            )
        )

    operation_date = _parse_date(validated_value(record, "fecha_operacion_pago")[0])
    communication_date = _parse_date(validated_value(record, "fecha_comunicacion_entidad")[0])
    detection_date = _parse_date(validated_value(record, "fecha_deteccion_operacion")[0])
    refund_date = _parse_date(validated_value(record, "fecha_reembolso_banco")[0])
    if operation_date and communication_date:
        if communication_date < operation_date:
            result.append(
                missing_item(
                    "banking_notification_chronology_conflict",
                    "La comunicación aparece anterior a la operación.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        elif regime.notification_months == 13 and communication_date > _add_months(operation_date, 13):
            result.append(
                missing_item(
                    "banking_notification_outside_thirteen_months",
                    "La comunicación aparece fuera del plazo documental de trece meses.",
                    MissingItemSeverity.BLOCKING,
                )
            )
    if operation_date and detection_date and detection_date < operation_date:
        result.append(
            missing_item(
                "banking_detection_chronology_conflict",
                "La detección aparece anterior a la operación.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if communication_date and refund_date and refund_date < communication_date:
        result.append(
            missing_item(
                "banking_refund_before_notification_conflict",
                "El reembolso aparece anterior a la comunicación del fraude.",
                MissingItemSeverity.BLOCKING,
            )
        )

    operation_amount = _number(
        validated_value(
            record,
            "importe_operacion_pago_eur",
            "importe_adeudo_domiciliado_eur",
            "importe_reclamado_eur",
        )[0]
    )
    refunded_amount = _number(validated_value(record, "importe_reembolsado_banco_eur")[0])
    recovered_amount = _number(validated_value(record, "importe_pagado_eur")[0])
    if operation_amount is not None and operation_amount < 0:
        result.append(
            missing_item(
                "banking_negative_operation_amount",
                "El importe de la operación no puede tratarse como positivo sin revisar si es abono.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if (
        operation_amount is not None
        and refunded_amount is not None
        and refunded_amount > operation_amount + 0.01
    ):
        result.append(
            missing_item(
                "banking_refund_exceeds_operation",
                "El reembolso supera el importe de la operación.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if recovered_amount is not None and recovered_amount > 0:
        result.append(
            missing_item(
                "banking_other_recovery_review",
                "Deben descontarse importes ya recuperados por otras vías.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    refusal, _ = validated_value(record, "motivo_no_reembolso_bancario")
    suspicion, _ = validated_value(record, "sospecha_fraude_comunicada_supervisor")
    provisional, _ = validated_value(record, "abono_bancario_provisional")
    if regime.incident_type == "unauthorized_payment":
        if not _present(refunded_amount) and provisional is not True and not _present(refusal):
            result.append(
                missing_item(
                    "banking_unauthorized_refund_status_review",
                    "No consta reembolso, abono provisional ni decisión motivada de la entidad.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        if suspicion is True and not _present(refusal):
            result.append(
                missing_item(
                    "banking_fraud_suspicion_reason_review",
                    "Consta sospecha de fraude comunicada, pero falta su motivación documental.",
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )

    if regime.incident_type == "authorized_scam":
        result.append(
            missing_item(
                "banking_authorized_scam_qualification_review",
                "Debe determinarse si hubo consentimiento jurídicamente eficaz, suplantación o fallos preventivos; no procede reembolso automático.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    wrong_identifier, _ = validated_value(record, "identificador_unico_incorrecto")
    provider_error, _ = validated_value(record, "error_ejecucion_imputable_entidad")
    executed_correctly, _ = validated_value(record, "operacion_ejecutada_correctamente")
    if regime.incident_type == "incorrect_execution":
        if provider_error is True and executed_correctly is True:
            result.append(
                missing_item(
                    "banking_execution_attribution_conflict",
                    "La ejecución figura correcta y simultáneamente imputable a error de la entidad.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        if wrong_identifier is True and provider_error is not True:
            result.append(
                missing_item(
                    "banking_wrong_identifier_recovery_review",
                    "Debe revisarse quién facilitó el identificador y las gestiones de recuperación; la retrocesión no es automática.",
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )

    debit_date = _parse_date(validated_value(record, "fecha_adeudo_domiciliado")[0])
    debit_request = _parse_date(validated_value(record, "fecha_solicitud_devolucion_adeudo")[0])
    debit_response = _parse_date(validated_value(record, "fecha_respuesta_devolucion_adeudo")[0])
    if regime.incident_type == "direct_debit_refund" and debit_date and debit_request:
        if debit_request < debit_date:
            result.append(
                missing_item(
                    "banking_direct_debit_request_before_debit",
                    "La solicitud de devolución aparece anterior al adeudo.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        elif (debit_request - debit_date).days > 56:
            result.append(
                missing_item(
                    "banking_direct_debit_request_outside_eight_weeks",
                    "La solicitud aparece fuera de las ocho semanas del régimen de devolución automática revisado.",
                    MissingItemSeverity.BLOCKING,
                )
            )
    if debit_request and debit_response and debit_response < debit_request:
        result.append(
            missing_item(
                "banking_direct_debit_response_chronology_conflict",
                "La respuesta aparece anterior a la solicitud de devolución.",
                MissingItemSeverity.BLOCKING,
            )
        )

    lost, _ = validated_value(record, "tarjeta_perdida_robada")
    if lost is True and regime.payer_loss_limit_eur == 50:
        result.append(
            missing_item(
                "banking_pre_notification_loss_limit_review",
                "El límite de 50 EUR exige separar operaciones anteriores y posteriores al aviso y revisar fraude o negligencia grave.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    verification, _ = validated_value(record, "verificacion_beneficiario_realizada")
    verification_result, _ = validated_value(record, "resultado_verificacion_beneficiario")
    mismatch_warning, _ = validated_value(record, "advertencia_discrepancia_beneficiario")
    if regime.incident_type == "instant_transfer_verification":
        if regime.verification_of_payee_active and verification is not True:
            result.append(
                missing_item(
                    "banking_payee_verification_required",
                    "Debe acreditarse la verificación del beneficiario antes de autorizar la transferencia.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        if "discrep" in _fold(verification_result) and mismatch_warning is not True:
            result.append(
                missing_item(
                    "banking_payee_mismatch_warning_conflict",
                    "El resultado indica discrepancia, pero no consta advertencia al ordenante.",
                    MissingItemSeverity.BLOCKING,
                )
            )

    block_date = _parse_date(validated_value(record, "fecha_bloqueo_cuenta")[0])
    unblock_date = _parse_date(validated_value(record, "fecha_desbloqueo_cuenta")[0])
    block_reason, _ = validated_value(record, "motivo_bloqueo_cuenta")
    blocked_funds = _number(validated_value(record, "fondos_retenidos_eur")[0])
    if block_date and unblock_date and unblock_date < block_date:
        result.append(
            missing_item(
                "banking_unblock_before_block_conflict",
                "El desbloqueo aparece anterior al bloqueo.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if blocked_funds is not None and blocked_funds > 0 and not _present(block_reason):
        result.append(
            missing_item(
                "banking_blocked_funds_reason_missing",
                "Constan fondos retenidos sin motivo documental de bloqueo.",
                MissingItemSeverity.BLOCKING,
            )
        )

    fee = _number(validated_value(record, "comision_bancaria_eur")[0])
    fee_disclosed, _ = validated_value(record, "comision_informada_previamente")
    if fee is not None and fee < 0:
        result.append(
            missing_item(
                "banking_negative_fee_review",
                "La comisión figura negativa; debe comprobarse si es devolución.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
    if regime.incident_type == "fees_or_exchange" and fee_disclosed is False:
        result.append(
            missing_item(
                "banking_fee_transparency_review",
                "La comisión figura no informada previamente y requiere revisión de contrato y comunicaciones.",
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    notice_change = _parse_date(validated_value(record, "fecha_aviso_modificacion_bancaria")[0])
    effective_change = _parse_date(validated_value(record, "fecha_aplicacion_modificacion_bancaria")[0])
    closure_notice = _parse_date(validated_value(record, "fecha_aviso_cierre_cuenta")[0])
    closure_date = _parse_date(validated_value(record, "fecha_cierre_cuenta")[0])
    indefinite, _ = validated_value(record, "contrato_marco_indefinido")
    if notice_change and effective_change and effective_change < notice_change:
        result.append(
            missing_item(
                "banking_contract_change_chronology_conflict",
                "La modificación aparece aplicada antes de su aviso.",
                MissingItemSeverity.BLOCKING,
            )
        )
    if closure_notice and closure_date:
        if closure_date < closure_notice:
            result.append(
                missing_item(
                    "banking_account_closure_chronology_conflict",
                    "El cierre aparece anterior a su aviso.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        elif indefinite is True and closure_date < _add_months(closure_notice, 2):
            result.append(
                missing_item(
                    "banking_account_closure_notice_review",
                    "El contrato indefinido parece cerrado sin dos meses completos de preaviso.",
                    MissingItemSeverity.BLOCKING,
                )
            )

    return dedupe_missing(result)


def _deadlines(
    record: ValidatedFactsRecord,
    regime: ClaimsBankingRegimeDecision,
) -> list[Deadline]:
    result: list[Deadline] = []
    operation_value, operation_key = validated_value(record, "fecha_operacion_pago")
    operation_date = _parse_date(operation_value)
    if operation_date and operation_key and regime.notification_months == 13:
        result.append(
            Deadline(
                label="Comunicación de operación no autorizada o incorrecta",
                due_at=_utc(_add_months(operation_date, 13)),
                calculation_status="estimated",
                source_fact_keys=[operation_key],
                notes=[
                    "Referencia de trece meses desde la operación.",
                    "Debe comprobarse el dies a quo y la información facilitada por la entidad.",
                ],
            )
        )

    communication_value, communication_key = validated_value(record, "fecha_comunicacion_entidad")
    if _present(communication_value) and communication_key and regime.immediate_refund_business_days == 1:
        result.append(
            Deadline(
                label="Reembolso de operación no autorizada",
                due_at=None,
                calculation_status="unresolved",
                source_fact_keys=[communication_key],
                notes=[
                    "Referencia: inmediato y, como máximo, al final del siguiente día hábil.",
                    "No se calcula sin calendario ni revisión de la excepción por sospecha de fraude.",
                ],
            )
        )

    debit_value, debit_key = validated_value(record, "fecha_adeudo_domiciliado")
    debit_date = _parse_date(debit_value)
    if debit_date and debit_key and regime.direct_debit_request_weeks == 8:
        result.append(
            Deadline(
                label="Solicitud de devolución de adeudo domiciliado",
                due_at=_utc(debit_date + timedelta(weeks=8)),
                calculation_status="estimated",
                source_fact_keys=[debit_key],
                notes=["Referencia de ocho semanas desde el adeudo."],
            )
        )

    debit_request_value, debit_request_key = validated_value(
        record,
        "fecha_solicitud_devolucion_adeudo",
    )
    if _present(debit_request_value) and debit_request_key and regime.direct_debit_response_business_days == 10:
        result.append(
            Deadline(
                label="Respuesta a la solicitud de devolución de adeudo",
                due_at=None,
                calculation_status="unresolved",
                source_fact_keys=[debit_request_key],
                notes=[
                    "Referencia de diez días hábiles.",
                    "Debe calcularse con calendario competente.",
                ],
            )
        )

    claim_value, claim_key = validated_value(record, "reclamacion_previa_fecha")
    claim_date = _parse_date(claim_value)
    if _present(claim_value) and claim_key:
        if regime.complaint_response_business_days == 15:
            result.append(
                Deadline(
                    label="Respuesta del servicio de atención en servicios de pago",
                    due_at=None,
                    calculation_status="unresolved",
                    source_fact_keys=[claim_key],
                    notes=["Referencia de quince días hábiles; requiere calendario."],
                )
            )
        elif regime.complaint_response_months in {1, 2} and claim_date is not None:
            result.append(
                Deadline(
                    label="Respuesta del servicio de atención de la entidad",
                    due_at=_utc(_add_months(claim_date, regime.complaint_response_months)),
                    calculation_status="estimated",
                    source_fact_keys=[claim_key],
                    notes=[
                        f"Referencia de {regime.complaint_response_months} mes(es) según el tipo de cliente.",
                        "Debe comprobarse la recepción y el régimen aplicable.",
                    ],
                )
            )
        if claim_date is not None:
            result.append(
                Deadline(
                    label="Admisibilidad temporal ante el Banco de España",
                    due_at=_utc(_add_months(claim_date, 12)),
                    calculation_status="estimated",
                    source_fact_keys=[claim_key],
                    notes=[
                        "Referencia operativa de un año desde la reclamación a la entidad.",
                        "Debe verificarse con los demás requisitos de admisión.",
                    ],
                )
            )
    return result


def _summary(
    record: ValidatedFactsRecord,
    regime: ClaimsBankingRegimeDecision,
) -> tuple[list[str], list[str]]:
    rows, used = summary_rows(
        record,
        (
            ("entidad_bancaria", "Entidad", ""),
            ("proveedor_servicios_pago", "Proveedor de pagos", ""),
            ("tipo_usuario_bancario", "Tipo de cliente", ""),
            ("cuenta_iban", "IBAN", ""),
            ("cuenta_bancaria_ref", "Cuenta", ""),
            ("instrumento_pago_tipo", "Instrumento", ""),
            ("tarjeta_ultimos_digitos", "Tarjeta", ""),
            ("operacion_pago_ref", "Operación", ""),
            ("fecha_operacion_pago", "Fecha de operación", ""),
            ("importe_operacion_pago_eur", "Importe", " EUR"),
            ("beneficiario_pago", "Beneficiario", ""),
            ("operacion_autorizada", "Operación autorizada", ""),
            ("fecha_comunicacion_entidad", "Comunicación a la entidad", ""),
            ("importe_reembolsado_banco_eur", "Importe reembolsado", " EUR"),
            ("reclamacion_previa_fecha", "Reclamación previa", ""),
        ),
    )
    rows.insert(
        0,
        (
            f"Encuadre bancario: {regime.incident_type}; servicio de pago "
            f"{regime.payment_service}; régimen {regime.status}."
        ),
    )
    return list(dict.fromkeys(rows)), list(dict.fromkeys(used))


def build_claims_banking_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    ensure_specialist_authority(
        facts_record,
        family_record,
        service="claims",
        family="banca",
        specialist="claims.banking",
    )

    regime = _regime(facts_record)
    route = _route_state(facts_record)
    basis = list(regime.legal_basis) if regime.status == "current" else []

    entity, entity_key = validated_value(
        facts_record,
        "entidad_bancaria",
        "proveedor_servicios_pago",
        "proveedor",
        "emisor_documento",
    )
    account, account_key = validated_value(
        facts_record,
        "cuenta_iban",
        "cuenta_bancaria_ref",
        "contrato_ref",
        "referencia_servicio",
    )
    operation, operation_key = validated_value(
        facts_record,
        "operacion_pago_ref",
        "referencia_documento",
    )
    amount, amount_key = validated_value(
        facts_record,
        "importe_operacion_pago_eur",
        "importe_adeudo_domiciliado_eur",
        "importe_reclamado_eur",
    )
    solution, solution_key = validated_value(facts_record, "solucion_solicitada")
    _, fact_key = validated_value(facts_record, "descripcion_hecho", "incidencia_bancaria_tipo")

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
        "banking_account_contract_and_parties",
        "Cuenta, contrato, proveedor y operación",
        (
            "Debe identificarse la cuenta, el contrato marco, el proveedor que "
            "gestiona la cuenta y, en su caso, otros proveedores de iniciación, "
            "adquirencia o red de tarjetas, sin atribuir a uno funciones ajenas."
        ),
        (
            entity_key,
            account_key,
            operation_key,
            "producto_servicio",
            "instrumento_pago_tipo",
            "ordenante_pago",
            "beneficiario_pago",
            fact_key,
        ),
        "primary",
    )
    add(
        "banking_consent_authentication_and_burden",
        "Consentimiento, autenticación y carga de la prueba",
        (
            "La entidad debe reconstruir consentimiento, autenticación, registro, "
            "dispositivo, canal y eventuales fallos. El uso de credenciales o "
            "autenticación reforzada no acredita por sí solo la autorización."
        ),
        (
            "operacion_autorizada",
            "consentimiento_pago_acreditado",
            "autenticacion_reforzada_aplicada",
            "metodo_autenticacion_pago",
            "registro_autenticacion_aportado",
            "fallo_tecnico_operacion",
            fact_key,
        ),
        "primary",
    )
    add(
        "banking_unauthorized_refund_and_user_liability",
        "Reembolso de operación no autorizada y límites de responsabilidad",
        (
            "Si la operación es realmente no autorizada deben revisarse aviso, "
            "reembolso inmediato, sospecha de fraude y la prueba de fraude o "
            "negligencia grave. El límite de 50 EUR no se aplica mecánicamente."
        ),
        (
            "operacion_autorizada",
            "fecha_comunicacion_entidad",
            "importe_reembolsado_banco_eur",
            "fecha_reembolso_banco",
            "abono_bancario_provisional",
            "fraude_usuario_invocado",
            "negligencia_grave_invocada",
            "motivo_negligencia_grave",
            "sospecha_fraude_comunicada_supervisor",
            "motivo_no_reembolso_bancario",
            "tarjeta_perdida_robada",
            "fecha_notificacion_perdida_tarjeta",
            amount_key,
            fact_key,
        ),
        "primary",
    )
    add(
        "banking_execution_identifier_and_recovery",
        "Ejecución, identificador único y recuperación de fondos",
        (
            "Debe determinarse el identificador facilitado, quién cometió el error, "
            "si hubo duplicidad, retraso o no ejecución y qué gestiones de rastreo "
            "o recobro se realizaron. Una transferencia no se retrocede por defecto."
        ),
        (
            "identificador_unico_pago",
            "identificador_unico_incorrecto",
            "error_ejecucion_imputable_entidad",
            "operacion_ejecutada_correctamente",
            "fecha_solicitud_recobro",
            "resultado_intento_recobro",
            "beneficiario_pago",
            operation_key,
            fact_key,
        ),
        "primary",
    )
    add(
        "banking_direct_debit_and_payment_instrument",
        "Adeudos domiciliados e instrumento de pago",
        (
            "Los adeudos y los instrumentos perdidos o robados tienen requisitos "
            "propios de mandato, solicitud, respuesta, comunicación y bloqueo que "
            "deben separarse de la controversia principal."
        ),
        (
            "adeudo_domiciliado",
            "fecha_adeudo_domiciliado",
            "importe_adeudo_domiciliado_eur",
            "mandato_adeudo_ref",
            "fecha_solicitud_devolucion_adeudo",
            "fecha_respuesta_devolucion_adeudo",
            "tarjeta_perdida_robada",
            "fecha_notificacion_perdida_tarjeta",
            "fecha_bloqueo_instrumento",
            fact_key,
        ),
    )
    add(
        "banking_instant_transfer_and_payee_verification",
        "Transferencia instantánea y verificación del beneficiario",
        (
            "Debe comprobarse si la transferencia estaba sometida a verificación "
            "del beneficiario, el resultado comunicado y cualquier advertencia de "
            "discrepancia antes de la autorización, sin trasladar reglas futuras."
        ),
        (
            "transferencia_instantanea",
            "verificacion_beneficiario_realizada",
            "resultado_verificacion_beneficiario",
            "advertencia_discrepancia_beneficiario",
            "identificador_unico_pago",
            "beneficiario_pago",
            fact_key,
        ),
    )
    add(
        "banking_fees_blocking_and_contract_changes",
        "Comisiones, bloqueo y cambios contractuales",
        (
            "Las comisiones, el tipo de cambio, el bloqueo de fondos y la "
            "modificación o cierre de cuenta deben contrastarse con contrato, "
            "información previa, motivo, aviso, fechas y proporcionalidad."
        ),
        (
            "comision_bancaria_eur",
            "comision_bancaria_concepto",
            "comision_informada_previamente",
            "tipo_cambio_aplicado",
            "tipo_cambio_informado",
            "cuenta_bloqueada",
            "motivo_bloqueo_cuenta",
            "fecha_bloqueo_cuenta",
            "fondos_retenidos_eur",
            "contrato_marco_modificado",
            "contrato_marco_indefinido",
            "fecha_aviso_modificacion_bancaria",
            "fecha_aplicacion_modificacion_bancaria",
            "fecha_aviso_cierre_cuenta",
            "fecha_cierre_cuenta",
            fact_key,
        ),
    )
    add(
        "banking_prior_claim_and_supervisory_route",
        "Reclamación previa y vía supervisora",
        (
            "Debe agotarse la reclamación previa y conservar fecha, canal, "
            "referencia y respuesta. La escalada exige comprobar plazo, materia, "
            "tipo de cliente y competencia del Banco de España."
        ),
        (
            "reclamacion_previa_fecha",
            "canal_reclamacion",
            "reclamacion_bancaria_ref",
            "respuesta_proveedor",
            "respuesta_documentada",
            "fecha_respuesta_bancaria",
            "fecha_respuesta",
            fact_key,
        ),
    )
    add(
        "banking_quantification_and_no_double_recovery",
        "Cuantificación y ausencia de doble recuperación",
        (
            "La petición debe separar principal, comisión, tipo de cambio, "
            "reembolsos provisionales o definitivos, recuperaciones y daños. No "
            "se fijan intereses ni indemnizaciones sin base y cálculo acreditados."
        ),
        (
            amount_key,
            "comision_bancaria_eur",
            "importe_reembolsado_banco_eur",
            "abono_bancario_provisional",
            "importe_pagado_eur",
            "resultado_intento_recobro",
            solution_key,
            fact_key,
        ),
        "primary",
    )

    if not arguments:
        raise HTTPException(
            status_code=409,
            detail="No existen hechos validados suficientes para construir la previa bancaria.",
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
            *fact_review_items(facts_record, prefix="banking"),
        ]
    )

    if route == "supervisor_review" and regime.status == "current":
        destination = "BANCO DE ESPAÑA — DEPARTAMENTO DE CONDUCTA DE ENTIDADES"
        document_type = "RECLAMACIÓN ANTE EL BANCO DE ESPAÑA"
    elif route == "entity_period_review":
        destination = str(entity).strip() if _present(entity) else "ENTIDAD FINANCIERA PENDIENTE DE VALIDAR"
        document_type = "REITERACIÓN AL SERVICIO DE ATENCIÓN Y RESERVA DE RECLAMACIÓN"
    else:
        destination = str(entity).strip() if _present(entity) else "ENTIDAD FINANCIERA PENDIENTE DE VALIDAR"
        document_type = "RECLAMACIÓN PREVIA A ENTIDAD FINANCIERA"

    subject_parts = ["RECLAMACIÓN BANCARIA", regime.incident_type.upper()]
    if _present(account):
        subject_parts.append(f"cuenta {account}")
    if _present(operation):
        subject_parts.append(f"operación {operation}")
    if _present(amount):
        subject_parts.append(f"{_display(amount)} EUR")

    strategy = (
        "Fijar primero la naturaleza de la operación y el consentimiento; exigir "
        "registros técnicos y decisión motivada; aplicar solo el régimen temporal "
        "y material correcto; cuantificar el importe neto pendiente y preservar "
        "la reclamación supervisora o judicial."
    )
    if _present(solution):
        strategy += f" La solución solicitada es: {_display(solution)}."

    return LegalPreview(
        case_id=facts_record.case_id,
        service=facts_record.facts.service,
        family="banca",
        specialist="claims.banking",
        facts_version=facts_record.facts.version,
        family_resolution_version=family_record.resolution.version,
        status=PreviewStatus.DRAFT,
        validated_facts_summary=summary,
        source_fact_keys=source_keys,
        problem_summary=(
            f"Incidencia bancaria ({regime.incident_type})"
            + (f" relativa a la operación {_display(operation)}." if _present(operation) else ".")
        ),
        client_goal=(
            "Obtener una decisión motivada, recuperar las cantidades procedentes y "
            "corregir la cuenta o el contrato sin duplicar importes ni asumir hechos no probados."
        ),
        primary_strategy=strategy,
        secondary_strategies=[
            "Solicitar bloqueo, rastreo o recobro cuando todavía sea materialmente útil.",
            "Escalar al Banco de España tras completar la reclamación previa y comprobar competencia.",
            "Reservar acciones judiciales y penales cuando exista fraude, daños o controversia probatoria.",
        ],
        requested_outcomes=[
            "Identificación de la operación, canal, dispositivo y autenticación.",
            "Decisión motivada sobre autorización, consentimiento y responsabilidad.",
            "Reembolso o abono definitivo de la cantidad procedente.",
            "Rastreo y recuperación documentada de fondos cuando corresponda.",
            "Anulación de comisiones o corrección contractual no acreditada.",
            "Desbloqueo o explicación proporcionada de la retención de fondos.",
            "Desglose de cantidades pagadas, recuperadas y pendientes.",
        ],
        documents_used=document_uses(facts_record),
        missing_items=missing,
        deadlines=_deadlines(facts_record, regime),
        risks=list(
            dict.fromkeys(
                [
                    "La calificación entre operación no autorizada y pago bajo engaño puede cambiar el régimen de reembolso.",
                    "La autenticación reforzada no elimina por sí sola la controversia sobre consentimiento.",
                    "El Banco de España puede declararse incompetente en inversión, seguros, datos o cuestiones judiciales.",
                    "Las transferencias pueden ser irrevocables y el recobro depender del beneficiario o de mandato legal.",
                    "Los plazos en días hábiles no deben calcularse como días naturales.",
                    *list(regime.warnings),
                ]
            )
        ),
        destination=destination,
        document_type=document_type,
        subject=" — ".join(subject_parts),
        legal_arguments=arguments,
        additional_requests=[
            "Contrato de cuenta y contrato marco de servicios de pago.",
            "Extracto completo y justificante de la operación.",
            "Registro de consentimiento y autenticación reforzada.",
            "IP, dispositivo, hora, ubicación y alertas antifraude disponibles.",
            "Grabaciones y comunicaciones con la entidad.",
            "Decisión íntegra del servicio de atención y motivo de denegación.",
            "Constancia de bloqueo, rastreo, recobro y comunicaciones interbancarias.",
            "Información previa de comisiones, cambio de divisa y modificaciones.",
            "Denuncia policial y documentación del fraude, cuando exista.",
            "Desglose de abonos provisionales, reembolsos y cantidades recuperadas.",
        ],
        created_by_component=(
            "claims.banking:"
            f"{CLAIMS_BANKING_SPECIALIST_VERSION}+"
            f"{CLAIMS_BANKING_REGIME_VERSION}+"
            f"{CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION}"
        ),
    )
