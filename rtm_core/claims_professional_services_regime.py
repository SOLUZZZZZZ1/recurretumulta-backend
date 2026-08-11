"""Selector conservador del régimen de servicios profesionales RTM.

Distingue contratos de consumo y B2B, obligación de medios o de resultado,
contratación presencial o a distancia, incumplimiento, honorarios, cancelación,
desistimiento y daños. Falla de forma cerrada ante servicios jurídicos,
sanitarios, fiscales, financieros, de seguros, edificación, empleo, protección
de datos como cuestión principal, jurisdicciones extranjeras y periodos no
versionados. No declara negligencia, causalidad, prescripción ni indemnización.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


CLAIMS_PROFESSIONAL_SERVICES_REGIME_VERSION = (
    "rtm_claims_professional_services_regime_v1_0"
)
CIVIL_LIMITATION_CURRENT_FROM = date(2015, 10, 7)
DISTANCE_CONSUMER_CURRENT_FROM = date(2022, 5, 28)
CUSTOMER_SERVICE_ACT_EFFECTIVE_ON = date(2025, 12, 28)
CUSTOMER_SERVICE_ADAPTATION_DEADLINE = date(2026, 12, 28)
CURRENT_RULESET_SAFE_THROUGH = date(2027, 12, 31)
CURRENT_RULESET_CODE = "spain_professional_services_2026_v1"

ScopeCode = Literal[
    "spain",
    "eu_eea_cross_border",
    "third_country",
    "unknown",
]
ClientType = Literal["consumer", "business", "unknown"]
ServiceType = Literal[
    "consulting",
    "technology",
    "creative_marketing",
    "repair_maintenance",
    "training_translation",
    "events_personal",
    "real_estate_brokerage",
    "legal",
    "healthcare",
    "architecture_building",
    "tax_accounting",
    "financial_investment",
    "insurance_intermediation",
    "public_administration",
    "employment",
    "data_protection",
    "standardized_digital",
    "mixed_specialist",
    "other",
    "unknown",
]
IncidentType = Literal[
    "nonperformance",
    "defective_or_incomplete",
    "delay",
    "fees_or_unapproved_costs",
    "cancellation_or_refund",
    "withdrawal",
    "damage_or_loss",
    "professional_negligence",
    "subcontracting",
    "recurring_termination",
    "general_claim",
    "unknown",
]
ObligationType = Literal["means", "result", "mixed", "unknown"]
ClaimNature = Literal[
    "contractual",
    "extracontractual",
    "professional_fee_collection",
    "mixed",
    "unknown",
]
CustomerServiceLayer = Literal[
    "not_applicable",
    "transition",
    "active",
    "unknown",
]

_SPAIN_TOKENS = ("espana", "spain")
_EU_EEA_TOKENS = (
    "alemania",
    "germany",
    "austria",
    "belgica",
    "belgium",
    "bulgaria",
    "chipre",
    "cyprus",
    "croacia",
    "croatia",
    "dinamarca",
    "denmark",
    "estonia",
    "finlandia",
    "finland",
    "francia",
    "france",
    "grecia",
    "greece",
    "hungria",
    "hungary",
    "irlanda",
    "ireland",
    "italia",
    "italy",
    "letonia",
    "latvia",
    "lituania",
    "lithuania",
    "luxemburgo",
    "luxembourg",
    "malta",
    "paises bajos",
    "netherlands",
    "polonia",
    "poland",
    "portugal",
    "republica checa",
    "czechia",
    "rumania",
    "romania",
    "suecia",
    "sweden",
    "eslovaquia",
    "slovakia",
    "eslovenia",
    "slovenia",
    "islandia",
    "iceland",
    "noruega",
    "norway",
    "liechtenstein",
    "suiza",
    "switzerland",
)

_COMMON_BASIS = (
    (
        "Código Civil, artículos 1091, 1101, 1124, 1258 y 1544, sobre fuerza "
        "obligatoria del contrato, incumplimiento, resolución, buena fe y "
        "arrendamiento de obras o servicios."
    ),
    (
        "Texto refundido de la Ley General para la Defensa de los Consumidores "
        "y Usuarios, artículos 8, 20, 21, 60, 61 y 62, sobre información, precio, "
        "contenido de la oferta, ejecución del servicio y voluntad contractual."
    ),
    (
        "Texto refundido de la Ley General para la Defensa de los Consumidores "
        "y Usuarios, artículos 80 a 83, sobre claridad, equilibrio y control de "
        "cláusulas no negociadas individualmente."
    ),
)
_DISTANCE_BASIS = (
    (
        "Texto refundido de la Ley General para la Defensa de los Consumidores "
        "y Usuarios, artículos 97 y 102 a 108, sobre información, desistimiento, "
        "ejecución anticipada del servicio, pérdida del derecho tras ejecución "
        "íntegra y eventual importe proporcional."
    ),
)
_ADR_BASIS = (
    (
        "Ley 7/2017, de resolución alternativa de litigios de consumo, sin "
        "presumir que una entidad sea competente ni que el profesional esté "
        "adherido u obligado a participar."
    ),
)
_CUSTOMER_SERVICE_BASIS = (
    (
        "Ley 10/2025, de servicios de atención a la clientela, artículos 2, 13 "
        "y 17 y disposición transitoria única, únicamente cuando estén "
        "acreditados su ámbito subjetivo, adaptación y fecha de aplicación."
    ),
)
_LIMITATION_BASIS = (
    (
        "Código Civil, artículos 1964.2 y 1968.2, como candidatos de plazo para "
        "acciones contractuales y extracontractuales; la calificación, el dies a "
        "quo y las interrupciones requieren revisión jurídica."
    ),
)


class ClaimsProfessionalServicesRegimeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["current", "operator_review"]
    contract_date: Optional[date] = None
    service_start_date: Optional[date] = None
    expected_completion_date: Optional[date] = None
    actual_completion_date: Optional[date] = None
    breach_date: Optional[date] = None
    complaint_date: Optional[date] = None
    withdrawal_notice_date: Optional[date] = None
    scope: ScopeCode = "unknown"
    client_type: ClientType = "unknown"
    service_type: ServiceType = "unknown"
    incident_type: IncidentType = "unknown"
    obligation_type: ObligationType = "unknown"
    claim_nature: ClaimNature = "unknown"
    distance_contract: bool = False
    off_premises_contract: bool = False
    unsolicited_home_visit: bool = False
    promotional_excursion: bool = False
    withdrawal_layer: bool = False
    withdrawal_days: Optional[int] = None
    withdrawal_information_delivered: Optional[bool] = None
    fully_performed_withdrawal_loss_possible: bool = False
    proportionate_payment_review: bool = False
    contractual_limitation_candidate_years: Optional[int] = None
    extracontractual_limitation_candidate_years: Optional[int] = None
    customer_service_layer: CustomerServiceLayer = "unknown"
    customer_service_resolution_business_days: Optional[int] = None
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
    if folded in {"si", "true", "1", "consta", "acreditado", "incluido"}:
        return True
    if folded in {
        "no",
        "false",
        "0",
        "no consta",
        "no acreditado",
        "no incluido",
    }:
        return False
    return None


def _country_kind(value: Any) -> ScopeCode:
    folded = _fold(value)
    if not folded:
        return "unknown"
    if any(token in folded for token in _SPAIN_TOKENS):
        return "spain"
    if any(token in folded for token in _EU_EEA_TOKENS):
        return "eu_eea_cross_border"
    return "third_country"


def _scope(client_country: Any, provider_country: Any) -> ScopeCode:
    client = _country_kind(client_country)
    provider = _country_kind(provider_country)
    if client == "unknown" or provider == "unknown":
        return "unknown"
    if client == "spain" and provider == "spain":
        return "spain"
    if client in {"spain", "eu_eea_cross_border"} and provider in {
        "spain",
        "eu_eea_cross_border",
    }:
        return "eu_eea_cross_border"
    return "third_country"


def _client_type(value: Any) -> ClientType:
    parsed = _optional_bool(value)
    if parsed is True:
        return "consumer"
    if parsed is False:
        return "business"
    return "unknown"


def _service_type(
    explicit: Any,
    issue_text: Any,
    *,
    legal: Any,
    healthcare: Any,
    architecture: Any,
    tax_accounting: Any,
    financial: Any,
    insurance: Any,
    public_administration: Any,
    employment: Any,
    data_protection: Any,
    standardized_digital: Any,
) -> ServiceType:
    flags = {
        "legal": _optional_bool(legal),
        "healthcare": _optional_bool(healthcare),
        "architecture_building": _optional_bool(architecture),
        "tax_accounting": _optional_bool(tax_accounting),
        "financial_investment": _optional_bool(financial),
        "insurance_intermediation": _optional_bool(insurance),
        "public_administration": _optional_bool(public_administration),
        "employment": _optional_bool(employment),
        "data_protection": _optional_bool(data_protection),
        "standardized_digital": _optional_bool(standardized_digital),
    }
    active = [name for name, state in flags.items() if state is True]
    if len(active) > 1:
        return "mixed_specialist"
    if active:
        return active[0]  # type: ignore[return-value]

    text = _fold((explicit, issue_text))
    if not text:
        return "unknown"
    markers: tuple[tuple[ServiceType, tuple[str, ...]], ...] = (
        (
            "legal",
            (
                "abogado",
                "abogada",
                "procurador",
                "asesoria juridica",
                "defensa juridica profesional",
            ),
        ),
        (
            "healthcare",
            (
                "medico",
                "medica",
                "clinica",
                "psicologo",
                "psicologa",
                "fisioterapia",
                "tratamiento sanitario",
            ),
        ),
        (
            "architecture_building",
            (
                "arquitecto",
                "arquitecta",
                "aparejador",
                "direccion de obra",
                "defecto de edificacion",
                "proyecto de edificacion",
            ),
        ),
        (
            "tax_accounting",
            (
                "asesoria fiscal",
                "gestoria fiscal",
                "declaracion tributaria",
                "contabilidad",
                "asesor contable",
            ),
        ),
        (
            "financial_investment",
            (
                "asesor financiero",
                "asesoria de inversion",
                "producto de inversion",
                "cartera de valores",
            ),
        ),
        (
            "insurance_intermediation",
            ("corredor de seguros", "mediador de seguros", "agente de seguros"),
        ),
        (
            "public_administration",
            ("administracion publica", "servicio administrativo publico"),
        ),
        (
            "employment",
            ("relacion laboral", "contrato de trabajo", "despido", "nomina"),
        ),
        (
            "data_protection",
            (
                "brecha de datos",
                "proteccion de datos",
                "datos personales",
                "secreto profesional vulnerado",
            ),
        ),
        (
            "standardized_digital",
            (
                "contenido digital estandarizado",
                "licencia de software estandar",
                "suscripcion digital",
            ),
        ),
        (
            "real_estate_brokerage",
            (
                "agente inmobiliario",
                "intermediacion inmobiliaria",
                "agencia inmobiliaria",
                "gestion inmobiliaria",
            ),
        ),
        (
            "repair_maintenance",
            (
                "reparacion",
                "mantenimiento",
                "instalador",
                "fontanero",
                "electricista",
                "taller",
            ),
        ),
        (
            "training_translation",
            (
                "formacion",
                "curso",
                "academia",
                "traduccion",
                "interprete",
            ),
        ),
        (
            "creative_marketing",
            (
                "marketing",
                "publicidad",
                "diseno grafico",
                "fotografia",
                "video profesional",
                "branding",
            ),
        ),
        (
            "technology",
            (
                "consultoria tecnologica",
                "desarrollo web",
                "software a medida",
                "soporte informatico",
                "ciberseguridad",
            ),
        ),
        (
            "events_personal",
            (
                "organizacion de eventos",
                "boda",
                "celebracion",
                "estetica",
                "peluqueria",
            ),
        ),
        (
            "consulting",
            (
                "consultoria",
                "asesoria profesional",
                "coaching",
                "estudio profesional",
                "informe profesional",
            ),
        ),
    )
    for service_type, service_markers in markers:
        if any(marker in text for marker in service_markers):
            return service_type
    if "servicio profesional" in text or "profesional" in text:
        return "other"
    return "unknown"


def _incident_type(explicit: Any, issue_text: Any) -> IncidentType:
    text = _fold((explicit, issue_text))
    if not text:
        return "unknown"
    groups: tuple[tuple[IncidentType, tuple[str, ...]], ...] = (
        (
            "withdrawal",
            ("desistimiento", "derecho de desistir", "desistir del contrato"),
        ),
        (
            "fees_or_unapproved_costs",
            (
                "honorarios no pactados",
                "factura excesiva",
                "gastos no autorizados",
                "sobrecoste",
                "precio no informado",
                "importe superior al presupuesto",
            ),
        ),
        (
            "cancellation_or_refund",
            (
                "cancelacion",
                "devolucion del anticipo",
                "reembolso",
                "retencion de la reserva",
            ),
        ),
        (
            "nonperformance",
            (
                "servicio no prestado",
                "no realizo el trabajo",
                "no iniciado",
                "incumplimiento total",
            ),
        ),
        (
            "defective_or_incomplete",
            (
                "servicio defectuoso",
                "servicio incompleto",
                "trabajo incompleto",
                "entrega incompleta",
                "errores en el trabajo",
                "cumplimiento defectuoso",
            ),
        ),
        (
            "delay",
            (
                "retraso",
                "fuera de plazo",
                "plazo incumplido",
                "entrega tardia",
            ),
        ),
        (
            "professional_negligence",
            (
                "negligencia profesional",
                "mala praxis",
                "falta de diligencia profesional",
                "perdida de oportunidad",
            ),
        ),
        (
            "damage_or_loss",
            (
                "danos causados",
                "perdida de bienes",
                "dano directo",
                "lucro cesante",
                "dano moral",
            ),
        ),
        (
            "subcontracting",
            (
                "subcontratacion no autorizada",
                "subcontratista",
                "delegacion no consentida",
            ),
        ),
        (
            "recurring_termination",
            (
                "baja del servicio",
                "servicio recurrente",
                "renovacion automatica",
                "terminacion del servicio",
            ),
        ),
    )
    for incident, markers in groups:
        if any(marker in text for marker in markers):
            return incident
    if any(marker in text for marker in ("reclamacion", "incumplimiento", "queja")):
        return "general_claim"
    return "unknown"


def _obligation_type(explicit: Any, means: Any, result: Any) -> ObligationType:
    explicit_text = _fold(explicit)
    if "mixt" in explicit_text:
        return "mixed"
    if "resultado" in explicit_text:
        return "result"
    if "medio" in explicit_text or "diligencia" in explicit_text:
        return "means"

    means_bool = _optional_bool(means)
    result_bool = _optional_bool(result)
    if means_bool is True and result_bool is True:
        return "mixed"
    if result_bool is True:
        return "result"
    if means_bool is True:
        return "means"
    return "unknown"


def _claim_nature(explicit: Any, fee_collection: Any) -> ClaimNature:
    if _optional_bool(fee_collection) is True:
        return "professional_fee_collection"
    text = _fold(explicit)
    if not text:
        return "unknown"
    has_contract = "contractual" in text or "contrato" in text
    has_tort = "extracontractual" in text or "responsabilidad aquiliana" in text
    if has_contract and has_tort:
        return "mixed"
    if has_tort:
        return "extracontractual"
    if has_contract:
        return "contractual"
    if "cobro de honorarios" in text or "reclamacion de honorarios" in text:
        return "professional_fee_collection"
    return "unknown"


def _customer_service_layer(
    *,
    reference_date: date,
    large_company: Any,
    act_applicable: Any,
) -> CustomerServiceLayer:
    large = _optional_bool(large_company)
    applicable = _optional_bool(act_applicable)
    if applicable is False or (applicable is None and large is False):
        return "not_applicable"
    if applicable is not True and large is not True:
        return "unknown"
    if reference_date < CUSTOMER_SERVICE_ACT_EFFECTIVE_ON:
        return "not_applicable"
    if reference_date < CUSTOMER_SERVICE_ADAPTATION_DEADLINE:
        return "transition"
    return "active"


def resolve_claims_professional_services_regime(
    *,
    contract_date: Any,
    service_start_date: Any = None,
    expected_completion_date: Any = None,
    actual_completion_date: Any = None,
    breach_date: Any = None,
    complaint_date: Any = None,
    withdrawal_notice_date: Any = None,
    client_country: Any,
    provider_country: Any,
    client_is_consumer: Any,
    professional_type: Any,
    incident_type: Any,
    issue_text: Any,
    obligation_type: Any = None,
    means_obligation: Any = None,
    result_obligation: Any = None,
    distance_contract: Any = None,
    off_premises_contract: Any = None,
    unsolicited_home_visit: Any = None,
    promotional_excursion: Any = None,
    withdrawal_information_delivered: Any = None,
    service_start_during_withdrawal_requested: Any = None,
    service_start_express_consent: Any = None,
    withdrawal_loss_acknowledged: Any = None,
    service_fully_performed: Any = None,
    claim_nature: Any = None,
    large_company: Any = None,
    customer_service_act_applicable: Any = None,
    legal_service: Any = None,
    healthcare_service: Any = None,
    architecture_building_service: Any = None,
    tax_accounting_service: Any = None,
    financial_investment_service: Any = None,
    insurance_intermediation_service: Any = None,
    public_administration_service: Any = None,
    employment_service: Any = None,
    data_protection_primary: Any = None,
    standardized_digital_content: Any = None,
    professional_fee_collection: Any = None,
) -> ClaimsProfessionalServicesRegimeDecision:
    contract = _parse_date(contract_date)
    start = _parse_date(service_start_date)
    expected = _parse_date(expected_completion_date)
    actual = _parse_date(actual_completion_date)
    breach = _parse_date(breach_date)
    complaint = _parse_date(complaint_date)
    withdrawal = _parse_date(withdrawal_notice_date)
    scope = _scope(client_country, provider_country)
    client = _client_type(client_is_consumer)
    distance = _optional_bool(distance_contract) is True
    off_premises = _optional_bool(off_premises_contract) is True
    unsolicited = _optional_bool(unsolicited_home_visit) is True
    excursion = _optional_bool(promotional_excursion) is True
    withdrawal_info = _optional_bool(withdrawal_information_delivered)
    service_type = _service_type(
        professional_type,
        issue_text,
        legal=legal_service,
        healthcare=healthcare_service,
        architecture=architecture_building_service,
        tax_accounting=tax_accounting_service,
        financial=financial_investment_service,
        insurance=insurance_intermediation_service,
        public_administration=public_administration_service,
        employment=employment_service,
        data_protection=data_protection_primary,
        standardized_digital=standardized_digital_content,
    )
    incident = _incident_type(incident_type, issue_text)
    obligation = _obligation_type(
        obligation_type,
        means_obligation,
        result_obligation,
    )
    nature = _claim_nature(claim_nature, professional_fee_collection)

    reference_date = complaint or breach or actual or expected or start or contract
    customer_service = (
        _customer_service_layer(
            reference_date=reference_date,
            large_company=large_company,
            act_applicable=customer_service_act_applicable,
        )
        if reference_date is not None
        else "unknown"
    )
    withdrawal_layer = distance or off_premises
    withdrawal_days = 30 if withdrawal_layer and (unsolicited or excursion) else (
        14 if withdrawal_layer else None
    )
    requested = _optional_bool(service_start_during_withdrawal_requested)
    consent = _optional_bool(service_start_express_consent)
    loss_ack = _optional_bool(withdrawal_loss_acknowledged)
    fully_performed = _optional_bool(service_fully_performed)
    fully_performed_loss = bool(
        withdrawal_layer
        and fully_performed is True
        and requested is True
        and consent is True
        and loss_ack is True
    )
    proportionate_review = bool(
        withdrawal_layer and requested is True and fully_performed is not True
    )

    common = {
        "contract_date": contract,
        "service_start_date": start,
        "expected_completion_date": expected,
        "actual_completion_date": actual,
        "breach_date": breach,
        "complaint_date": complaint,
        "withdrawal_notice_date": withdrawal,
        "scope": scope,
        "client_type": client,
        "service_type": service_type,
        "incident_type": incident,
        "obligation_type": obligation,
        "claim_nature": nature,
        "distance_contract": distance,
        "off_premises_contract": off_premises,
        "unsolicited_home_visit": unsolicited,
        "promotional_excursion": excursion,
        "withdrawal_layer": withdrawal_layer,
        "withdrawal_days": withdrawal_days,
        "withdrawal_information_delivered": withdrawal_info,
        "fully_performed_withdrawal_loss_possible": fully_performed_loss,
        "proportionate_payment_review": proportionate_review,
        "customer_service_layer": customer_service,
    }

    if contract is None:
        return ClaimsProfessionalServicesRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Falta la fecha documental del encargo o contrato profesional; no "
                "puede seleccionarse el régimen temporal aplicable."
            ),
        )

    if contract < CIVIL_LIMITATION_CURRENT_FROM:
        return ClaimsProfessionalServicesRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El encargo es anterior al horizonte histórico versionado desde la "
                "reforma general de prescripción de 2015."
            ),
        )

    dated_values = (contract, start, expected, actual, breach, complaint, withdrawal)
    if any(value is not None and value > CURRENT_RULESET_SAFE_THROUGH for value in dated_values):
        return ClaimsProfessionalServicesRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El contrato, ejecución, incumplimiento o reclamación supera el "
                "horizonte jurídico verificado."
            ),
        )

    if start is not None and start < contract:
        return ClaimsProfessionalServicesRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La ejecución aparece iniciada antes de la fecha del encargo; debe "
                "revisarse la cronología y la posible contratación previa."
            ),
        )
    if expected is not None and start is not None and expected < start:
        return ClaimsProfessionalServicesRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason="La fecha prevista de finalización es anterior al inicio.",
        )
    if actual is not None and start is not None and actual < start:
        return ClaimsProfessionalServicesRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason="La finalización real aparece anterior al inicio.",
        )
    if breach is not None and breach < contract:
        return ClaimsProfessionalServicesRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason="El incumplimiento aparece anterior al contrato.",
        )
    if complaint is not None and complaint < contract:
        return ClaimsProfessionalServicesRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason="La reclamación aparece anterior al contrato.",
        )
    if withdrawal is not None and withdrawal < contract:
        return ClaimsProfessionalServicesRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason="El desistimiento aparece anterior al contrato.",
        )

    if scope != "spain":
        reason = (
            "El contrato es transfronterizo UE/EEE; deben determinarse ley "
            "aplicable, foro, profesional regulado y autoridad competente."
            if scope == "eu_eea_cross_border"
            else (
                "No consta un contrato íntegramente español o interviene un tercer "
                "país; deben determinarse ley, foro y régimen local."
            )
        )
        if scope == "unknown":
            reason = (
                "Faltan los países documentales del cliente y del prestador; no "
                "puede confirmarse el régimen español."
            )
        return ClaimsProfessionalServicesRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=reason,
        )

    if client != "consumer":
        reason = (
            "El cliente actúa como empresa o profesional; el régimen de consumo no "
            "puede aplicarse automáticamente y debe analizarse la vía civil o mercantil."
            if client == "business"
            else (
                "No consta si el cliente actuó como consumidor; debe acreditarse la "
                "finalidad ajena a su actividad empresarial o profesional."
            )
        )
        return ClaimsProfessionalServicesRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=reason,
        )

    if nature == "professional_fee_collection":
        return ClaimsProfessionalServicesRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El profesional reclama honorarios impagados. El asunto corresponde "
                "al satélite de morosidad, no a una reclamación de consumo del cliente."
            ),
        )

    special_reasons = {
        "legal": (
            "Los servicios jurídicos exigen revisar secreto profesional, hoja de "
            "encargo, deberes deontológicos, actuación procesal y eventual pérdida "
            "de oportunidad mediante un especialista propio."
        ),
        "healthcare": (
            "Los servicios sanitarios requieren historia clínica, consentimiento, "
            "criterio asistencial y responsabilidad sanitaria especializada."
        ),
        "architecture_building": (
            "El servicio de arquitectura o edificación puede activar la Ley de "
            "Ordenación de la Edificación, agentes, garantías y plazos especiales."
        ),
        "tax_accounting": (
            "La asesoría fiscal o contable puede depender de expedientes tributarios, "
            "plazos administrativos y causalidad especializada."
        ),
        "financial_investment": (
            "La asesoría financiera o de inversión debe dirigirse al especialista "
            "financiero y a su normativa sectorial."
        ),
        "insurance_intermediation": (
            "La intermediación de seguros debe tratarse con la normativa sectorial "
            "de distribución y reclamaciones financieras."
        ),
        "public_administration": (
            "La actuación pertenece al satélite de Administración pública."
        ),
        "employment": (
            "La controversia deriva de una relación laboral y requiere la vía social."
        ),
        "data_protection": (
            "La protección de datos es la cuestión principal y requiere determinar "
            "responsable, base jurídica, derechos y autoridad de control."
        ),
        "standardized_digital": (
            "El objeto principal es contenido o servicio digital estandarizado y debe "
            "encauzarse por comercio electrónico o servicios digitales."
        ),
        "mixed_specialist": (
            "Concurren varias materias especializadas y deben separarse antes de "
            "seleccionar el régimen de servicios profesionales."
        ),
    }
    if service_type in special_reasons:
        return ClaimsProfessionalServicesRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=special_reasons[service_type],
        )

    if withdrawal_layer and contract < DISTANCE_CONSUMER_CURRENT_FROM:
        return ClaimsProfessionalServicesRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La contratación a distancia o fuera de establecimiento es anterior "
                "al horizonte transitorio versionado desde mayo de 2022."
            ),
        )

    basis = [*_COMMON_BASIS, *_ADR_BASIS, *_LIMITATION_BASIS]
    if withdrawal_layer:
        basis.extend(_DISTANCE_BASIS)
    if customer_service in {"transition", "active"}:
        basis.extend(_CUSTOMER_SERVICE_BASIS)

    warnings = [
        (
            "La profesión o denominación comercial no determina por sí sola si la "
            "obligación era de medios, de resultado o mixta; debe leerse el encargo."
        ),
        (
            "La falta de un precio cerrado no convierte el servicio en gratuito; "
            "deben revisarse presupuesto, base de cálculo, impuestos y gastos autorizados."
        ),
        (
            "El incumplimiento no acredita automáticamente daño, causalidad, lucro "
            "cesante, daño moral o pérdida de oportunidad."
        ),
        (
            "Una queja ante un colegio profesional puede tener finalidad disciplinaria "
            "y no sustituye por sí sola la reclamación económica."
        ),
        (
            "La adhesión a arbitraje o a una entidad ADR y su competencia deben "
            "comprobarse antes de dirigir la reclamación."
        ),
        (
            "Los plazos de prescripción son candidatos: deben fijarse calificación, "
            "dies a quo, interrupciones y posibles normas sectoriales."
        ),
    ]
    if obligation == "unknown":
        warnings.append(
            "No consta la naturaleza de la obligación profesional; no debe prometerse ni descartarse un resultado."
        )
    if service_type == "real_estate_brokerage":
        warnings.append(
            "La intermediación inmobiliaria puede quedar afectada por normativa autonómica, de vivienda y por el negocio principal."
        )
    if customer_service == "unknown":
        warnings.append(
            "No puede aplicarse el plazo de quince días hábiles sin acreditar el ámbito subjetivo de la Ley 10/2025."
        )
    elif customer_service == "transition":
        warnings.append(
            "La empresa se encuentra dentro del periodo transitorio de adaptación de la Ley 10/2025; no debe anticiparse su plena exigibilidad."
        )
    if withdrawal_info is False:
        warnings.append(
            "La falta de información sobre desistimiento puede ampliar el plazo, pero exige calcularlo por calendario y acreditar el defecto informativo."
        )
    if fully_performed is True and withdrawal_layer and not fully_performed_loss:
        warnings.append(
            "La ejecución completa no elimina por sí sola el desistimiento: deben constar solicitud, consentimiento y conocimiento de la pérdida del derecho."
        )
    if proportionate_review:
        warnings.append(
            "La ejecución parcial durante el desistimiento exige revisar la solicitud expresa y el cálculo proporcional, sin aceptar automáticamente la factura."
        )
    if nature in {"unknown", "mixed"}:
        warnings.append(
            "No se ha cerrado la naturaleza contractual o extracontractual de la acción; no puede fijarse un único plazo."
        )

    contractual_candidate = 5 if nature == "contractual" else None
    extracontractual_candidate = 1 if nature == "extracontractual" else None

    return ClaimsProfessionalServicesRegimeDecision(
        status="current",
        **common,
        contractual_limitation_candidate_years=contractual_candidate,
        extracontractual_limitation_candidate_years=extracontractual_candidate,
        customer_service_resolution_business_days=(
            15 if customer_service == "active" else None
        ),
        ruleset=CURRENT_RULESET_CODE,
        legal_basis=tuple(dict.fromkeys(basis)),
        warnings=tuple(dict.fromkeys(warnings)),
    )
