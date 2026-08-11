"""Registro modular de hechos y capacidad para ``claims.banking``.

Añade hechos documentales tipados para cuentas, tarjetas, transferencias,
adeudos, fraude, comisiones y bloqueo de fondos. No decide si una operación fue
autorizada, no califica automáticamente la negligencia grave y no aplica a
inversiones, criptoactivos o préstamos una respuesta diseñada para pagos.
"""

from __future__ import annotations

from rtm_core.document_fact_catalog import FactFieldSpec
from rtm_core.service_catalog import normalize_code


CLAIMS_BANKING_EXTENSION_VERSION = "rtm_claims_banking_extension_v1_0"

_INSTALLED = False


def _spec(
    key: str,
    *,
    value_type: str = "text",
    aliases: tuple[str, ...] = (),
    min_confidence: float = 0.96,
    merge_mode: str = "single",
    max_length: int = 800,
    allow_negative: bool = False,
) -> FactFieldSpec:
    return FactFieldSpec(
        key=key,
        label=key.replace("_", " ").strip().capitalize(),
        services=("claims",),
        value_type=value_type,
        aliases=aliases,
        min_confidence=min_confidence,
        merge_mode=merge_mode,
        max_length=max_length,
        allow_negative=allow_negative,
    )


_BANKING_FACTS: tuple[FactFieldSpec, ...] = (
    _spec(
        "incidencia_bancaria_tipo",
        aliases=("banking_incident_type", "payment_incident_type"),
        max_length=260,
    ),
    _spec(
        "pais_entidad_bancaria",
        aliases=("bank_country", "payment_provider_country"),
        max_length=160,
    ),
    _spec(
        "entidad_bancaria",
        aliases=("bank_name", "financial_entity", "account_servicing_bank"),
        min_confidence=0.97,
        max_length=260,
    ),
    _spec(
        "proveedor_servicios_pago",
        aliases=("payment_service_provider", "psp_name"),
        min_confidence=0.97,
        max_length=260,
    ),
    _spec(
        "tipo_usuario_bancario",
        aliases=("banking_customer_type", "payment_user_type"),
        max_length=180,
    ),
    _spec(
        "cuenta_iban",
        value_type="identifier",
        aliases=("iban", "account_iban"),
        min_confidence=0.98,
        max_length=80,
    ),
    _spec(
        "cuenta_bancaria_ref",
        value_type="identifier",
        aliases=("bank_account_reference", "account_reference"),
        min_confidence=0.97,
        max_length=160,
    ),
    _spec(
        "instrumento_pago_tipo",
        aliases=("payment_instrument_type", "payment_method_type"),
        max_length=180,
    ),
    _spec(
        "tarjeta_ultimos_digitos",
        value_type="identifier",
        aliases=("card_last_digits", "masked_card_number"),
        min_confidence=0.98,
        max_length=32,
    ),
    _spec(
        "operacion_pago_ref",
        value_type="identifier",
        aliases=("payment_operation_reference", "transaction_reference"),
        min_confidence=0.98,
        max_length=180,
    ),
    _spec(
        "fecha_operacion_pago",
        value_type="date",
        aliases=("payment_operation_date", "transaction_date"),
        min_confidence=0.98,
    ),
    _spec(
        "fecha_valor_operacion",
        value_type="date",
        aliases=("payment_value_date", "transaction_value_date"),
        min_confidence=0.97,
    ),
    _spec(
        "importe_operacion_pago_eur",
        value_type="money",
        aliases=("payment_operation_amount", "transaction_amount_eur"),
        min_confidence=0.98,
    ),
    _spec(
        "moneda_operacion_pago",
        aliases=("payment_currency", "transaction_currency"),
        max_length=40,
    ),
    _spec(
        "ordenante_pago",
        aliases=("payment_payer", "payer_name"),
        min_confidence=0.96,
        max_length=260,
    ),
    _spec(
        "beneficiario_pago",
        aliases=("payment_payee", "beneficiary_name"),
        min_confidence=0.96,
        max_length=260,
    ),
    _spec(
        "canal_operacion_pago",
        aliases=("payment_channel", "transaction_channel"),
        max_length=180,
    ),
    _spec(
        "operacion_autorizada",
        value_type="boolean",
        aliases=("payment_authorized", "transaction_authorized"),
        min_confidence=0.98,
    ),
    _spec(
        "consentimiento_pago_acreditado",
        value_type="boolean",
        aliases=("payment_consent_proven", "consent_evidenced"),
        min_confidence=0.98,
    ),
    _spec(
        "autenticacion_reforzada_aplicada",
        value_type="boolean",
        aliases=("strong_customer_authentication_applied", "sca_applied"),
        min_confidence=0.98,
    ),
    _spec(
        "metodo_autenticacion_pago",
        aliases=("payment_authentication_method", "authentication_factors"),
        merge_mode="set",
        max_length=700,
    ),
    _spec(
        "registro_autenticacion_aportado",
        value_type="boolean",
        aliases=("authentication_log_provided", "payment_authentication_record_provided"),
        min_confidence=0.97,
    ),
    _spec(
        "fallo_tecnico_operacion",
        value_type="boolean",
        aliases=("payment_technical_failure", "transaction_technical_failure"),
        min_confidence=0.97,
    ),
    _spec(
        "fraude_usuario_invocado",
        value_type="boolean",
        aliases=("user_fraud_alleged", "payer_fraud_alleged"),
        min_confidence=0.97,
    ),
    _spec(
        "negligencia_grave_invocada",
        value_type="boolean",
        aliases=("gross_negligence_alleged",),
        min_confidence=0.97,
    ),
    _spec(
        "motivo_negligencia_grave",
        aliases=("gross_negligence_reason", "gross_negligence_evidence"),
        merge_mode="set",
        max_length=1200,
    ),
    _spec(
        "sospecha_fraude_comunicada_supervisor",
        value_type="boolean",
        aliases=("fraud_suspicion_reported_to_authority",),
        min_confidence=0.97,
    ),
    _spec(
        "motivo_no_reembolso_bancario",
        aliases=("bank_refund_refusal_reason", "payment_refund_refusal_reason"),
        merge_mode="set",
        max_length=1200,
    ),
    _spec(
        "fecha_deteccion_operacion",
        value_type="date",
        aliases=("payment_detection_date", "unauthorized_payment_detection_date"),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_comunicacion_entidad",
        value_type="date",
        aliases=("bank_notification_date", "payment_provider_notification_date"),
        min_confidence=0.98,
    ),
    _spec(
        "fecha_bloqueo_instrumento",
        value_type="date",
        aliases=("payment_instrument_block_date", "card_block_date"),
        min_confidence=0.97,
    ),
    _spec(
        "importe_reembolsado_banco_eur",
        value_type="money",
        aliases=("bank_refunded_amount", "payment_refunded_amount"),
        min_confidence=0.98,
    ),
    _spec(
        "fecha_reembolso_banco",
        value_type="date",
        aliases=("bank_refund_date", "payment_refund_date"),
        min_confidence=0.97,
    ),
    _spec(
        "abono_bancario_provisional",
        value_type="boolean",
        aliases=("provisional_bank_credit", "temporary_refund_credit"),
        min_confidence=0.97,
    ),
    _spec(
        "denuncia_policial_ref",
        value_type="identifier",
        aliases=("police_report_reference", "fraud_report_reference"),
        min_confidence=0.96,
        max_length=180,
    ),
    _spec(
        "modalidad_fraude_bancario",
        aliases=("bank_fraud_method", "payment_fraud_type"),
        merge_mode="set",
        max_length=500,
    ),
    _spec(
        "usuario_ordeno_pago_bajo_engano",
        value_type="boolean",
        aliases=("payer_initiated_under_deception", "authorized_push_payment_scam"),
        min_confidence=0.98,
    ),
    _spec(
        "alerta_fraude_previa",
        value_type="boolean",
        aliases=("prior_fraud_alert", "bank_fraud_warning_before_payment"),
        min_confidence=0.97,
    ),
    _spec(
        "entidad_avisada_antes_operacion",
        value_type="boolean",
        aliases=("bank_warned_before_payment",),
        min_confidence=0.97,
    ),
    _spec(
        "transferencia_instantanea",
        value_type="boolean",
        aliases=("instant_credit_transfer", "instant_transfer"),
        min_confidence=0.98,
    ),
    _spec(
        "verificacion_beneficiario_realizada",
        value_type="boolean",
        aliases=("verification_of_payee_performed", "payee_verification_performed"),
        min_confidence=0.98,
    ),
    _spec(
        "resultado_verificacion_beneficiario",
        aliases=("verification_of_payee_result", "payee_verification_result"),
        merge_mode="set",
        max_length=600,
    ),
    _spec(
        "advertencia_discrepancia_beneficiario",
        value_type="boolean",
        aliases=("payee_mismatch_warning", "verification_mismatch_warning"),
        min_confidence=0.98,
    ),
    _spec(
        "identificador_unico_pago",
        value_type="identifier",
        aliases=("payment_unique_identifier", "beneficiary_iban_used"),
        min_confidence=0.98,
        max_length=180,
    ),
    _spec(
        "identificador_unico_incorrecto",
        value_type="boolean",
        aliases=("incorrect_unique_identifier", "wrong_beneficiary_identifier"),
        min_confidence=0.98,
    ),
    _spec(
        "error_ejecucion_imputable_entidad",
        value_type="boolean",
        aliases=("execution_error_attributable_to_provider",),
        min_confidence=0.97,
    ),
    _spec(
        "operacion_ejecutada_correctamente",
        value_type="boolean",
        aliases=("payment_correctly_executed", "transaction_correctly_executed"),
        min_confidence=0.98,
    ),
    _spec(
        "fecha_solicitud_recobro",
        value_type="date",
        aliases=("fund_recovery_request_date", "payment_recall_request_date"),
        min_confidence=0.97,
    ),
    _spec(
        "resultado_intento_recobro",
        aliases=("fund_recovery_result", "payment_recall_result"),
        merge_mode="set",
        max_length=900,
    ),
    _spec(
        "adeudo_domiciliado",
        value_type="boolean",
        aliases=("direct_debit", "is_direct_debit"),
        min_confidence=0.98,
    ),
    _spec(
        "fecha_adeudo_domiciliado",
        value_type="date",
        aliases=("direct_debit_date",),
        min_confidence=0.98,
    ),
    _spec(
        "importe_adeudo_domiciliado_eur",
        value_type="money",
        aliases=("direct_debit_amount",),
        min_confidence=0.98,
    ),
    _spec(
        "mandato_adeudo_ref",
        value_type="identifier",
        aliases=("direct_debit_mandate_reference",),
        min_confidence=0.97,
        max_length=180,
    ),
    _spec(
        "fecha_solicitud_devolucion_adeudo",
        value_type="date",
        aliases=("direct_debit_refund_request_date",),
        min_confidence=0.98,
    ),
    _spec(
        "fecha_respuesta_devolucion_adeudo",
        value_type="date",
        aliases=("direct_debit_refund_response_date",),
        min_confidence=0.97,
    ),
    _spec(
        "tarjeta_perdida_robada",
        value_type="boolean",
        aliases=("card_lost_or_stolen", "payment_instrument_lost_or_stolen"),
        min_confidence=0.98,
    ),
    _spec(
        "fecha_notificacion_perdida_tarjeta",
        value_type="date",
        aliases=("lost_card_notification_date",),
        min_confidence=0.98,
    ),
    _spec(
        "retirada_efectivo_cajero",
        value_type="boolean",
        aliases=("cash_withdrawal", "atm_withdrawal"),
        min_confidence=0.98,
    ),
    _spec(
        "cajero_identificacion",
        value_type="identifier",
        aliases=("atm_identifier", "cash_machine_reference"),
        min_confidence=0.96,
        max_length=160,
    ),
    _spec(
        "comision_bancaria_eur",
        value_type="money",
        aliases=("bank_fee_amount", "payment_fee_amount"),
        min_confidence=0.98,
    ),
    _spec(
        "comision_bancaria_concepto",
        aliases=("bank_fee_concept", "payment_fee_description"),
        merge_mode="set",
        max_length=700,
    ),
    _spec(
        "comision_informada_previamente",
        value_type="boolean",
        aliases=("bank_fee_previously_disclosed",),
        min_confidence=0.97,
    ),
    _spec(
        "tipo_cambio_aplicado",
        value_type="number",
        aliases=("applied_exchange_rate",),
        min_confidence=0.98,
    ),
    _spec(
        "tipo_cambio_informado",
        value_type="boolean",
        aliases=("exchange_rate_disclosed",),
        min_confidence=0.97,
    ),
    _spec(
        "contrato_marco_modificado",
        value_type="boolean",
        aliases=("payment_framework_contract_changed",),
        min_confidence=0.97,
    ),
    _spec(
        "contrato_marco_indefinido",
        value_type="boolean",
        aliases=("indefinite_payment_framework_contract",),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_aviso_modificacion_bancaria",
        value_type="date",
        aliases=("bank_contract_change_notice_date",),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_aplicacion_modificacion_bancaria",
        value_type="date",
        aliases=("bank_contract_change_effective_date",),
        min_confidence=0.97,
    ),
    _spec(
        "cuenta_bloqueada",
        value_type="boolean",
        aliases=("bank_account_blocked", "payment_account_blocked"),
        min_confidence=0.98,
    ),
    _spec(
        "motivo_bloqueo_cuenta",
        aliases=("bank_account_block_reason", "payment_account_block_reason"),
        merge_mode="set",
        max_length=1000,
    ),
    _spec(
        "fecha_bloqueo_cuenta",
        value_type="date",
        aliases=("bank_account_block_date",),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_desbloqueo_cuenta",
        value_type="date",
        aliases=("bank_account_unblock_date",),
        min_confidence=0.97,
    ),
    _spec(
        "fondos_retenidos_eur",
        value_type="money",
        aliases=("blocked_funds_amount", "retained_funds_amount"),
        min_confidence=0.98,
    ),
    _spec(
        "cierre_cuenta_bancaria",
        value_type="boolean",
        aliases=("bank_account_closed", "payment_account_terminated"),
        min_confidence=0.98,
    ),
    _spec(
        "fecha_aviso_cierre_cuenta",
        value_type="date",
        aliases=("bank_account_closure_notice_date",),
        min_confidence=0.97,
    ),
    _spec(
        "fecha_cierre_cuenta",
        value_type="date",
        aliases=("bank_account_closure_date",),
        min_confidence=0.97,
    ),
    _spec(
        "prestamo_credito_implicado",
        value_type="boolean",
        aliases=("loan_or_credit_involved", "mortgage_involved"),
        min_confidence=0.97,
    ),
    _spec(
        "producto_inversion_implicado",
        value_type="boolean",
        aliases=("investment_product_involved", "securities_product_involved"),
        min_confidence=0.97,
    ),
    _spec(
        "criptoactivo_implicado",
        value_type="boolean",
        aliases=("crypto_asset_involved", "crypto_payment_involved"),
        min_confidence=0.97,
    ),
    _spec(
        "reclamacion_bancaria_ref",
        value_type="identifier",
        aliases=("bank_complaint_reference", "payment_complaint_reference"),
        min_confidence=0.97,
        max_length=180,
    ),
    _spec(
        "fecha_respuesta_bancaria",
        value_type="date",
        aliases=("bank_complaint_response_date",),
        min_confidence=0.97,
    ),
)


def _install_document_facts() -> None:
    import rtm_core.document_fact_catalog as catalog

    registered = {spec.key for spec in catalog._BY_SERVICE["claims"].values()}
    new_specs = tuple(spec for spec in _BANKING_FACTS if spec.key not in registered)
    if not new_specs:
        return

    index = catalog._BY_SERVICE["claims"]
    for spec in new_specs:
        for raw_name in (spec.key, *spec.aliases):
            name = normalize_code(raw_name)
            current = index.get(name)
            if current is not None and current.key != spec.key:
                raise RuntimeError(
                    f"Alias ambiguo {raw_name!r} en claims: "
                    f"{current.key!r} frente a {spec.key!r}"
                )
            index[name] = spec

    catalog._FIELDS = (*catalog._FIELDS, *new_specs)


def _install_domain_capability() -> None:
    import rtm_core.domain_catalog as catalog

    key = ("claims", "banca")
    profile = catalog._FAMILY_PROFILES.get(key)
    if profile is None:
        raise RuntimeError("No existe la familia claims.banca")

    ready = profile.model_copy(update={"capability": "specialist_ready"})
    catalog._FAMILY_PROFILES[key] = ready
    catalog._FAMILY_ITEMS = tuple(
        ready if (item.department, item.family) == key else item
        for item in catalog._FAMILY_ITEMS
    )


def _install_version_inventory() -> None:
    import rtm_core.versioning as versioning

    declared = {
        "claims_banking_extension": CLAIMS_BANKING_EXTENSION_VERSION,
        "claims_banking_regime": "rtm_claims_banking_regime_v1_0",
        "claims_banking_specialist": "rtm_claims_banking_specialist_v1_0",
        "claims_specialist_registry": "rtm_claims_specialist_registry_v1_0",
    }
    lookups = {
        "claims_banking_extension": (
            "rtm_core.claims_banking_extension",
            "CLAIMS_BANKING_EXTENSION_VERSION",
        ),
        "claims_banking_regime": (
            "rtm_core.claims_banking_regime",
            "CLAIMS_BANKING_REGIME_VERSION",
        ),
        "claims_banking_specialist": (
            "rtm_core.claims_banking_specialist",
            "CLAIMS_BANKING_SPECIALIST_VERSION",
        ),
        "claims_specialist_registry": (
            "rtm_core.claims_specialist_registry",
            "CLAIMS_SPECIALIST_REGISTRY_VERSION",
        ),
    }
    versioning.DECLARED_COMPONENT_VERSIONS.update(declared)
    versioning._RUNTIME_LOOKUPS.update(lookups)


def install_claims_banking_extension() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_document_facts()
    _install_domain_capability()
    _install_version_inventory()
    _INSTALLED = True
