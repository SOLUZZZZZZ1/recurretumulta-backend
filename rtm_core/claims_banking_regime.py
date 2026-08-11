"""Selector conservador del régimen de banca y servicios de pago RTM.

Distingue operaciones no autorizadas, pagos ordenados bajo engaño, errores de
ejecución, adeudos, instrumentos perdidos, transferencias instantáneas, bloqueos
y transparencia contractual. Falla de forma cerrada ante préstamos, inversión,
criptoactivos, jurisdicciones extranjeras o periodos no versionados.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


CLAIMS_BANKING_REGIME_VERSION = "rtm_claims_banking_regime_v1_0"

BANKING_TRANSPARENCY_EFFECTIVE_ON = date(2012, 4, 29)
PAYMENT_SERVICES_RULES_EFFECTIVE_ON = date(2019, 2, 25)
PAYMENT_TRANSPARENCY_ORDER_EFFECTIVE_ON = date(2020, 1, 1)
INSTANT_PAYMENTS_REGULATION_EFFECTIVE_ON = date(2024, 4, 8)
INSTANT_PAYMENT_CHARGE_PARITY_EFFECTIVE_ON = date(2025, 1, 9)
VERIFICATION_OF_PAYEE_EFFECTIVE_ON = date(2025, 10, 9)
CUSTOMER_SERVICE_LAW_EFFECTIVE_ON = date(2025, 12, 28)
CUSTOMER_SERVICE_FULL_ADAPTATION_ON = date(2026, 12, 28)
CURRENT_RULESET_SAFE_THROUGH = date(2027, 12, 31)
CURRENT_RULESET_CODE = "spain_banking_payments_2019_2026_v1"

ScopeCode = Literal["spain", "foreign", "unknown"]
UserType = Literal["consumer", "microenterprise", "business", "unknown"]
IncidentType = Literal[
    "unauthorized_payment",
    "authorized_scam",
    "incorrect_execution",
    "direct_debit_refund",
    "payment_instrument_loss",
    "instant_transfer_verification",
    "account_blocking",
    "fees_or_exchange",
    "contract_change_or_closure",
    "loan_or_credit",
    "investment_or_crypto",
    "general_payment",
    "general_banking",
    "unknown",
]

_COMMON_BANKING_BASIS = (
    (
        "Orden EHA/2899/2011, de 28 de octubre, artículos 3 y 6 a 8, "
        "sobre comisiones, información precontractual, contrato y comunicaciones "
        "en los servicios bancarios comprendidos en su ámbito."
    ),
)
_PAYMENT_TRANSPARENCY_BASIS = (
    (
        "Orden ECE/1263/2019, de 26 de diciembre, artículos 8 a 20, "
        "sobre información en operaciones de pago, contratos marco, gastos, "
        "ejecución y modificación o resolución contractual."
    ),
)
_PAYMENT_AUTH_BASIS = (
    (
        "Real Decreto-ley 19/2018, de 23 de noviembre, artículos 36 y 41 a 46, "
        "sobre consentimiento, custodia del instrumento, comunicación, carga de "
        "la prueba, reembolso y responsabilidad por operaciones no autorizadas."
    ),
    (
        "Real Decreto-ley 19/2018, artículo 68, y Reglamento Delegado (UE) "
        "2018/389, sobre autenticación reforzada y medidas de seguridad, sin que "
        "la autenticación pruebe por sí sola el consentimiento del ordenante."
    ),
)
_PAYMENT_EXECUTION_BASIS = (
    (
        "Real Decreto-ley 19/2018, artículos 50 a 64, sobre recepción, rechazo, "
        "irrevocabilidad, identificador único, ejecución defectuosa o tardía, "
        "rastreo, responsabilidad y resarcimiento."
    ),
)
_DIRECT_DEBIT_BASIS = (
    (
        "Real Decreto-ley 19/2018, artículos 48 y 49, sobre devolución de "
        "operaciones autorizadas iniciadas por el beneficiario y plazos de "
        "solicitud y respuesta, según el tipo de adeudo."
    ),
)
_INSTANT_PAYMENT_BASIS = (
    (
        "Reglamento (UE) 2024/886, en particular los artículos 5 bis a 5 quater "
        "del Reglamento (UE) 260/2012 en su redacción resultante, sobre costes, "
        "transferencias instantáneas en euros y verificación del beneficiario."
    ),
)
_COMPLAINT_BASIS = (
    (
        "Real Decreto-ley 19/2018, artículos 69 y 70, Ley 44/2002, artículos "
        "29 y 30, y normativa de atención de entidades financieras, sobre "
        "reclamación previa y resolución extrajudicial."
    ),
)
_CUSTOMER_SERVICE_BASIS = (
    (
        "Ley 10/2025, de 26 de diciembre, sobre servicios de atención a la "
        "clientela, con revisión de su disposición transitoria única."
    ),
)


class ClaimsBankingRegimeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["current", "operator_review"]
    reference_date: Optional[date] = None
    scope: ScopeCode = "unknown"
    user_type: UserType = "unknown"
    incident_type: IncidentType = "unknown"
    payment_service: bool = False
    unauthorized_refund_rule: bool = False
    authorization_requires_review: bool = False
    notification_months: Optional[int] = None
    immediate_refund_business_days: Optional[int] = None
    payer_loss_limit_eur: Optional[int] = None
    direct_debit_request_weeks: Optional[int] = None
    direct_debit_response_business_days: Optional[int] = None
    complaint_response_business_days: Optional[int] = None
    complaint_response_months: Optional[int] = None
    verification_of_payee_active: bool = False
    instant_charge_parity_active: bool = False
    customer_service_transition_complete: bool = False
    ruleset: Optional[str] = None
    legal_basis: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    blocking_reason: Optional[str] = None


def _fold(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return " ".join(_fold(item) for item in value if item is not None)
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower().replace("\r", " ").replace("\n", " ")
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


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


def _optional_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    folded = _fold(value)
    if folded in {"si", "true", "1", "consta", "acreditado", "autorizado"}:
        return True
    if folded in {
        "no",
        "false",
        "0",
        "no consta",
        "no acreditado",
        "no autorizado",
    }:
        return False
    return None


def _scope(value: Any) -> ScopeCode:
    folded = _fold(value)
    if not folded:
        return "unknown"
    if "espana" in folded or "spain" in folded:
        return "spain"
    return "foreign"


def _user_type(value: Any) -> UserType:
    folded = _fold(value)
    if any(marker in folded for marker in ("consumidor", "consumer", "particular")):
        return "consumer"
    if any(marker in folded for marker in ("microempresa", "microenterprise")):
        return "microenterprise"
    if any(marker in folded for marker in ("empresa", "sociedad", "business", "profesional")):
        return "business"
    return "unknown"


def _incident_type(
    explicit: Any,
    text: Any,
    *,
    operation_authorized: Optional[bool],
    payer_initiated_under_deception: Optional[bool],
    instant_transfer: Optional[bool],
    loan_involved: Optional[bool],
    investment_involved: Optional[bool],
    crypto_involved: Optional[bool],
) -> IncidentType:
    folded = _fold((explicit, text))
    if investment_involved is True or crypto_involved is True or any(
        marker in folded
        for marker in (
            "producto de inversion",
            "acciones",
            "fondo de inversion",
            "bonos",
            "valores",
            "criptoactivo",
            "criptomoneda",
        )
    ):
        return "investment_or_crypto"
    if loan_involved is True or any(
        marker in folded
        for marker in (
            "prestamo",
            "credito",
            "hipoteca",
            "tarjeta revolving",
            "interes usurario",
        )
    ):
        return "loan_or_credit"
    if payer_initiated_under_deception is True or any(
        marker in folded
        for marker in (
            "pago bajo engano",
            "transferencia bajo engano",
            "vishing",
            "smishing",
            "spoofing",
            "falso empleado del banco",
            "estafa del falso banco",
            "authorized push payment",
        )
    ):
        return "authorized_scam"
    if operation_authorized is False or any(
        marker in folded
        for marker in (
            "operacion no autorizada",
            "pago no autorizado",
            "cargo no reconocido",
            "transferencia no reconocida",
            "retirada no reconocida",
            "suplantacion de identidad",
            "phishing",
        )
    ):
        return "unauthorized_payment"
    if any(
        marker in folded
        for marker in (
            "adeudo domiciliado",
            "recibo domiciliado",
            "devolucion de recibo",
            "direct debit",
        )
    ):
        return "direct_debit_refund"
    if any(
        marker in folded
        for marker in (
            "tarjeta perdida",
            "tarjeta robada",
            "instrumento de pago perdido",
            "lost card",
        )
    ):
        return "payment_instrument_loss"
    if instant_transfer is True or any(
        marker in folded
        for marker in (
            "transferencia instantanea",
            "verificacion del beneficiario",
            "verificacion de beneficiario",
            "verification of payee",
            "coincidencia iban nombre",
        )
    ):
        return "instant_transfer_verification"
    if any(
        marker in folded
        for marker in (
            "ejecucion incorrecta",
            "ejecucion defectuosa",
            "transferencia duplicada",
            "importe duplicado",
            "iban incorrecto",
            "beneficiario incorrecto",
            "transferencia no ejecutada",
            "transferencia retrasada",
        )
    ):
        return "incorrect_execution"
    if any(
        marker in folded
        for marker in (
            "cuenta bloqueada",
            "bloqueo de cuenta",
            "fondos retenidos",
            "cuenta congelada",
        )
    ):
        return "account_blocking"
    if any(
        marker in folded
        for marker in (
            "cierre de cuenta",
            "cancelacion de cuenta",
            "modificacion del contrato marco",
            "cambio de condiciones bancarias",
        )
    ):
        return "contract_change_or_closure"
    if any(
        marker in folded
        for marker in (
            "comision bancaria",
            "comision de tarjeta",
            "gastos bancarios",
            "tipo de cambio",
            "cambio de divisa",
        )
    ):
        return "fees_or_exchange"
    if any(
        marker in folded
        for marker in (
            "tarjeta",
            "transferencia",
            "bizum",
            "pago",
            "cajero",
            "cuenta de pago",
        )
    ):
        return "general_payment"
    if any(marker in folded for marker in ("banco", "entidad bancaria", "cuenta bancaria")):
        return "general_banking"
    return "unknown"


def resolve_claims_banking_regime(
    *,
    incident_date: Any,
    contract_date: Any,
    complaint_date: Any,
    bank_country: Any,
    user_type: Any,
    incident_type: Any,
    issue_text: Any,
    operation_authorized: Any = None,
    payer_initiated_under_deception: Any = None,
    instant_transfer: Any = None,
    loan_involved: Any = None,
    investment_involved: Any = None,
    crypto_involved: Any = None,
) -> ClaimsBankingRegimeDecision:
    reference = (
        _parse_date(incident_date)
        or _parse_date(complaint_date)
        or _parse_date(contract_date)
    )
    scope = _scope(bank_country)
    customer = _user_type(user_type)
    authorized = _optional_bool(operation_authorized)
    deceived = _optional_bool(payer_initiated_under_deception)
    instant = _optional_bool(instant_transfer)
    loan = _optional_bool(loan_involved)
    investment = _optional_bool(investment_involved)
    crypto = _optional_bool(crypto_involved)
    incident = _incident_type(
        incident_type,
        issue_text,
        operation_authorized=authorized,
        payer_initiated_under_deception=deceived,
        instant_transfer=instant,
        loan_involved=loan,
        investment_involved=investment,
        crypto_involved=crypto,
    )
    payment_service = incident in {
        "unauthorized_payment",
        "authorized_scam",
        "incorrect_execution",
        "direct_debit_refund",
        "payment_instrument_loss",
        "instant_transfer_verification",
        "general_payment",
    }
    common = {
        "reference_date": reference,
        "scope": scope,
        "user_type": customer,
        "incident_type": incident,
        "payment_service": payment_service,
    }

    if reference is None:
        return ClaimsBankingRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Falta una fecha documental de operación, incidencia, reclamación "
                "o contrato para seleccionar la versión normativa aplicable."
            ),
        )
    if reference > CURRENT_RULESET_SAFE_THROUGH:
        return ClaimsBankingRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El expediente supera el horizonte jurídico verificado. Deben "
                "versionarse las reformas posteriores de banca y pagos."
            ),
        )
    if scope != "spain":
        return ClaimsBankingRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "No consta una entidad o servicio sometido al marco español. Deben "
                "determinarse ley, supervisor y procedimiento aplicables."
            ),
        )
    if incident in {"loan_or_credit", "investment_or_crypto"}:
        boundary = (
            "Préstamos, créditos, hipotecas y tarjetas revolving requieren un "
            "régimen específico de contratación, transparencia y crédito."
            if incident == "loan_or_credit"
            else (
                "Inversiones, valores y criptoactivos requieren determinar la "
                "autoridad y el régimen sectorial antes de usar la vía bancaria."
            )
        )
        return ClaimsBankingRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=boundary,
        )
    if incident == "unknown":
        return ClaimsBankingRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Falta concretar la incidencia bancaria: autorización, fraude, "
                "ejecución, adeudo, instrumento, bloqueo, comisión o contrato."
            ),
        )
    if payment_service and reference < PAYMENT_SERVICES_RULES_EFFECTIVE_ON:
        return ClaimsBankingRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La operación es anterior a la aplicación del régimen de pagos "
                "versionado desde el 25 de febrero de 2019."
            ),
        )
    if not payment_service and reference < BANKING_TRANSPARENCY_EFFECTIVE_ON:
        return ClaimsBankingRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El asunto es anterior al horizonte de transparencia bancaria "
                "versionado y requiere revisión histórica."
            ),
        )
    if (
        incident == "instant_transfer_verification"
        and reference < VERIFICATION_OF_PAYEE_EFFECTIVE_ON
    ):
        return ClaimsBankingRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La incidencia pretende aplicar la verificación del beneficiario "
                "antes de su fecha de exigibilidad versionada en la zona euro."
            ),
        )

    basis = list(_COMMON_BANKING_BASIS)
    if payment_service:
        basis.extend(_PAYMENT_TRANSPARENCY_BASIS)
        basis.extend(_PAYMENT_EXECUTION_BASIS)
    if incident in {
        "unauthorized_payment",
        "authorized_scam",
        "payment_instrument_loss",
        "general_payment",
    }:
        basis.extend(_PAYMENT_AUTH_BASIS)
    if incident == "direct_debit_refund":
        basis.extend(_DIRECT_DEBIT_BASIS)
    if incident == "instant_transfer_verification":
        basis.extend(_INSTANT_PAYMENT_BASIS)
    basis.extend(_COMPLAINT_BASIS)
    if reference >= CUSTOMER_SERVICE_LAW_EFFECTIVE_ON:
        basis.extend(_CUSTOMER_SERVICE_BASIS)

    unauthorized = incident == "unauthorized_payment"
    complaint_business_days = 15 if payment_service else None
    complaint_months: Optional[int] = None
    if not payment_service:
        if customer == "consumer":
            complaint_months = 1
        elif customer in {"microenterprise", "business"}:
            complaint_months = 2

    warnings = [
        (
            "La autenticación, el uso de claves o un registro técnico no prueban "
            "por sí solos que el ordenante prestara consentimiento."
        ),
        (
            "El fraude o la negligencia grave del usuario deben acreditarse; una "
            "etiqueta interna de la entidad no basta para desplazar responsabilidad."
        ),
        (
            "Debe distinguirse una operación ejecutada por un tercero sin "
            "consentimiento de un pago ordenado por el cliente bajo engaño; la "
            "calificación determina el régimen de reembolso."
        ),
        (
            "Las gestiones de recobro, denuncia penal, devolución del beneficiario, "
            "seguro o red de tarjetas deben coordinarse para evitar doble recuperación."
        ),
        (
            "El informe del Banco de España no sustituye una resolución judicial "
            "ni liquida automáticamente daños, intereses o indemnizaciones."
        ),
    ]
    if reference >= CUSTOMER_SERVICE_LAW_EFFECTIVE_ON and reference < CUSTOMER_SERVICE_FULL_ADAPTATION_ON:
        warnings.append(
            "La Ley 10/2025 está en vigor, pero su adaptación transitoria debe revisarse para la entidad concreta."
        )
    if incident == "authorized_scam":
        warnings.append(
            "El pago ordenado bajo engaño no recibe automáticamente el tratamiento de operación no autorizada; debe revisarse consentimiento, suplantación y fallos preventivos."
        )

    return ClaimsBankingRegimeDecision(
        status="current",
        **common,
        unauthorized_refund_rule=unauthorized,
        authorization_requires_review=incident == "authorized_scam",
        notification_months=13 if payment_service else None,
        immediate_refund_business_days=1 if unauthorized else None,
        payer_loss_limit_eur=50 if incident in {"unauthorized_payment", "payment_instrument_loss"} else None,
        direct_debit_request_weeks=8 if incident == "direct_debit_refund" else None,
        direct_debit_response_business_days=10 if incident == "direct_debit_refund" else None,
        complaint_response_business_days=complaint_business_days,
        complaint_response_months=complaint_months,
        verification_of_payee_active=reference >= VERIFICATION_OF_PAYEE_EFFECTIVE_ON,
        instant_charge_parity_active=reference >= INSTANT_PAYMENT_CHARGE_PARITY_EFFECTIVE_ON,
        customer_service_transition_complete=reference >= CUSTOMER_SERVICE_FULL_ADAPTATION_ON,
        ruleset=CURRENT_RULESET_CODE,
        legal_basis=tuple(dict.fromkeys(basis)),
        warnings=tuple(dict.fromkeys(warnings)),
    )
