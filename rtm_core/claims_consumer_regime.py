"""Selector conservador del régimen de consumo general residual RTM.

El módulo cubre compras presenciales y servicios de consumo no sectoriales.
Expulsa de forma cerrada comercio electrónico, telecomunicaciones, energía,
banca, seguros, viajes, servicios profesionales, vivienda, Administración y
otras materias que necesitan especialista propio. No declara falta de
conformidad, abusividad, devolución, indemnización ni prescripción.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


CLAIMS_CONSUMER_REGIME_VERSION = "rtm_claims_consumer_regime_v1_0"
CIVIL_LIMITATION_CURRENT_FROM = date(2015, 10, 7)
CURRENT_GOODS_RULES_FROM = date(2022, 1, 1)
CURRENT_OFF_PREMISES_RULES_FROM = date(2022, 5, 28)
CUSTOMER_SERVICE_ACT_EFFECTIVE_ON = date(2025, 12, 28)
CUSTOMER_SERVICE_ADAPTATION_DEADLINE = date(2026, 12, 28)
CURRENT_RULESET_SAFE_THROUGH = date(2027, 12, 31)
CURRENT_RULESET_CODE = "spain_residual_consumer_2026_v1"

ScopeCode = Literal[
    "spain",
    "eu_eea_cross_border",
    "third_country",
    "unknown",
]
ClientType = Literal["consumer", "business", "unknown"]
ContractType = Literal["goods", "service", "mixed", "unknown"]
PurchaseChannel = Literal["in_store", "off_premises", "distance", "unknown"]
IncidentType = Literal[
    "goods_nonconformity",
    "delivery_problem",
    "service_nonperformance",
    "defective_or_incomplete_service",
    "delay",
    "price_or_unapproved_charge",
    "cancellation_or_refund",
    "withdrawal",
    "automatic_renewal_or_termination",
    "unfair_term",
    "voucher_or_deposit",
    "damage_or_loss",
    "product_safety",
    "general_claim",
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
        "Texto refundido de la Ley General para la Defensa de los Consumidores "
        "y Usuarios, artículos 8, 20, 21, 60, 61 y 62, sobre derechos básicos, "
        "información, precio, oferta, atención y contenido contractual."
    ),
    (
        "Texto refundido de la Ley General para la Defensa de los Consumidores "
        "y Usuarios, artículos 80 a 83, sobre incorporación, claridad, equilibrio "
        "y control de cláusulas no negociadas individualmente."
    ),
)
_GOODS_BASIS = (
    (
        "Texto refundido de la Ley General para la Defensa de los Consumidores "
        "y Usuarios, artículos 114 a 127, sobre conformidad de bienes, remedios, "
        "plazos, reparación, sustitución, reducción del precio y resolución."
    ),
)
_SERVICE_BASIS = (
    (
        "Código Civil, artículos 1091, 1101, 1124, 1258 y 1544, sobre fuerza "
        "obligatoria, buena fe, incumplimiento, resolución y prestación de servicios."
    ),
)
_OFF_PREMISES_BASIS = (
    (
        "Texto refundido de la Ley General para la Defensa de los Consumidores "
        "y Usuarios, artículos 92, 97 y 102 a 108, sobre contratos fuera de "
        "establecimiento, información, desistimiento, ejecución y reembolso."
    ),
)
_ADR_BASIS = (
    (
        "Ley 7/2017, de resolución alternativa de litigios de consumo, sin "
        "presumir competencia, adhesión, obligatoriedad ni resultado de una entidad."
    ),
)
_CUSTOMER_SERVICE_BASIS = (
    (
        "Ley 10/2025, de servicios de atención a la clientela, artículos 2, 13 y "
        "17 y disposición transitoria única, solo cuando estén acreditados su "
        "ámbito subjetivo, adaptación y fecha de aplicación."
    ),
)
_LIMITATION_BASIS = (
    (
        "Código Civil, artículos 1964.2 y 1968.2, como posibles referencias para "
        "acciones contractuales o extracontractuales, sin fijar automáticamente "
        "calificación, dies a quo, interrupciones ni normas especiales."
    ),
)


class ClaimsConsumerRegimeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["current", "operator_review"]
    contract_date: Optional[date] = None
    delivery_date: Optional[date] = None
    service_start_date: Optional[date] = None
    expected_service_end_date: Optional[date] = None
    actual_service_end_date: Optional[date] = None
    incident_date: Optional[date] = None
    complaint_date: Optional[date] = None
    withdrawal_notice_date: Optional[date] = None
    scope: ScopeCode = "unknown"
    client_type: ClientType = "unknown"
    contract_type: ContractType = "unknown"
    purchase_channel: PurchaseChannel = "unknown"
    incident_type: IncidentType = "unknown"
    goods_conformity_layer: bool = False
    legal_conformity_period_years: Optional[int] = None
    presumed_origin_period_years: Optional[int] = None
    second_hand_minimum_agreed_years: Optional[int] = None
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


def _scope(client_country: Any, business_country: Any) -> ScopeCode:
    client = _country_kind(client_country)
    business = _country_kind(business_country)
    if client == "unknown" or business == "unknown":
        return "unknown"
    if client == "spain" and business == "spain":
        return "spain"
    if client in {"spain", "eu_eea_cross_border"} and business in {
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


def _contract_type(
    explicit: Any,
    issue_text: Any,
    new_goods: Any,
    second_hand_goods: Any,
) -> ContractType:
    text = _fold((explicit, issue_text))
    goods_flag = _optional_bool(new_goods) is True or _optional_bool(second_hand_goods) is True
    goods_markers = (
        "bien",
        "producto",
        "articulo",
        "electrodomestico",
        "mueble",
        "ropa",
        "calzado",
        "juguete",
        "aparato",
    )
    service_markers = (
        "servicio",
        "cuota",
        "suscripcion presencial",
        "reparacion no profesional",
        "actividad",
        "gimnasio",
        "academia",
        "evento",
    )
    has_goods = goods_flag or any(marker in text for marker in goods_markers)
    has_service = any(marker in text for marker in service_markers)
    if "mixt" in text or (has_goods and has_service):
        return "mixed"
    if has_goods:
        return "goods"
    if has_service:
        return "service"
    return "unknown"


def _purchase_channel(
    *,
    in_store: Any,
    distance: Any,
    off_premises: Any,
    online: Any,
) -> PurchaseChannel:
    if _optional_bool(online) is True or _optional_bool(distance) is True:
        return "distance"
    if _optional_bool(off_premises) is True:
        return "off_premises"
    if _optional_bool(in_store) is True:
        return "in_store"
    return "unknown"


def _incident_type(explicit: Any, issue_text: Any) -> IncidentType:
    text = _fold((explicit, issue_text))
    if not text:
        return "unknown"
    groups: tuple[tuple[IncidentType, tuple[str, ...]], ...] = (
        (
            "product_safety",
            ("producto inseguro", "retirada de producto", "riesgo para la salud"),
        ),
        (
            "withdrawal",
            ("desistimiento", "desistir de la compra", "derecho de desistir"),
        ),
        (
            "automatic_renewal_or_termination",
            (
                "renovacion automatica",
                "baja no tramitada",
                "cobro posterior a la baja",
                "penalizacion de permanencia",
            ),
        ),
        (
            "price_or_unapproved_charge",
            (
                "precio cobrado",
                "precio publicitado",
                "cargo adicional",
                "gasto no informado",
                "importe superior",
                "sobrecoste",
            ),
        ),
        (
            "cancellation_or_refund",
            (
                "cancelacion",
                "reembolso pendiente",
                "devolucion del dinero",
                "devolucion de la senal",
            ),
        ),
        (
            "voucher_or_deposit",
            ("vale", "bono", "tarjeta regalo", "deposito", "senal", "caducidad"),
        ),
        (
            "unfair_term",
            (
                "clausula abusiva",
                "condicion abusiva",
                "clausula no negociada",
                "desequilibrio contractual",
            ),
        ),
        (
            "goods_nonconformity",
            (
                "falta de conformidad",
                "producto defectuoso",
                "bien defectuoso",
                "garantia legal",
                "reparacion o sustitucion",
            ),
        ),
        (
            "delivery_problem",
            (
                "no entregado",
                "entrega parcial",
                "entrega tardia",
                "problema de entrega",
            ),
        ),
        (
            "service_nonperformance",
            ("servicio no prestado", "servicio no iniciado", "incumplimiento total"),
        ),
        (
            "defective_or_incomplete_service",
            (
                "servicio defectuoso",
                "servicio incompleto",
                "prestacion incompleta",
                "servicio mal ejecutado",
            ),
        ),
        (
            "delay",
            ("retraso", "fuera de plazo", "plazo incumplido", "demora"),
        ),
        (
            "damage_or_loss",
            ("danos causados", "perdida economica", "dano directo", "perjuicio"),
        ),
    )
    for incident, markers in groups:
        if any(marker in text for marker in markers):
            return incident
    if any(marker in text for marker in ("reclamacion de consumo", "queja", "incumplimiento")):
        return "general_claim"
    return "unknown"


def _customer_service_layer(
    *,
    reference_date: date,
    large_business: Any,
    act_applicable: Any,
) -> CustomerServiceLayer:
    large = _optional_bool(large_business)
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


def resolve_claims_consumer_regime(
    *,
    contract_date: Any,
    delivery_date: Any = None,
    service_start_date: Any = None,
    expected_service_end_date: Any = None,
    actual_service_end_date: Any = None,
    incident_date: Any = None,
    complaint_date: Any = None,
    withdrawal_notice_date: Any = None,
    client_country: Any,
    business_country: Any,
    client_is_consumer: Any,
    contract_type: Any,
    incident_type: Any,
    issue_text: Any,
    in_store_purchase: Any = None,
    distance_contract: Any = None,
    off_premises_contract: Any = None,
    online_purchase: Any = None,
    unsolicited_home_visit: Any = None,
    promotional_excursion: Any = None,
    withdrawal_information_delivered: Any = None,
    service_start_during_withdrawal_requested: Any = None,
    service_start_express_consent: Any = None,
    withdrawal_loss_acknowledged: Any = None,
    service_fully_performed: Any = None,
    new_goods: Any = None,
    second_hand_goods: Any = None,
    second_hand_agreed_period_years: Any = None,
    large_business: Any = None,
    customer_service_act_applicable: Any = None,
    marketplace_involved: Any = None,
    telecommunications_involved: Any = None,
    energy_involved: Any = None,
    banking_or_payment_involved: Any = None,
    insurance_involved: Any = None,
    travel_involved: Any = None,
    professional_service_involved: Any = None,
    public_administration_involved: Any = None,
    housing_or_tenancy_involved: Any = None,
    healthcare_involved: Any = None,
    legal_service_involved: Any = None,
    investment_involved: Any = None,
    data_protection_primary: Any = None,
    unsafe_product: Any = None,
    personal_injury: Any = None,
    motor_vehicle_involved: Any = None,
    digital_content_or_service: Any = None,
) -> ClaimsConsumerRegimeDecision:
    contract = _parse_date(contract_date)
    delivery = _parse_date(delivery_date)
    service_start = _parse_date(service_start_date)
    expected_end = _parse_date(expected_service_end_date)
    actual_end = _parse_date(actual_service_end_date)
    incident_date_value = _parse_date(incident_date)
    complaint = _parse_date(complaint_date)
    withdrawal = _parse_date(withdrawal_notice_date)
    scope = _scope(client_country, business_country)
    client = _client_type(client_is_consumer)
    contract_kind = _contract_type(
        contract_type,
        issue_text,
        new_goods,
        second_hand_goods,
    )
    channel = _purchase_channel(
        in_store=in_store_purchase,
        distance=distance_contract,
        off_premises=off_premises_contract,
        online=online_purchase,
    )
    incident = _incident_type(incident_type, issue_text)
    withdrawal_info = _optional_bool(withdrawal_information_delivered)
    requested = _optional_bool(service_start_during_withdrawal_requested)
    consent = _optional_bool(service_start_express_consent)
    loss_ack = _optional_bool(withdrawal_loss_acknowledged)
    fully_performed = _optional_bool(service_fully_performed)
    withdrawal_layer = channel == "off_premises"
    withdrawal_days = 30 if withdrawal_layer and (
        _optional_bool(unsolicited_home_visit) is True
        or _optional_bool(promotional_excursion) is True
    ) else (14 if withdrawal_layer else None)
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

    reference_date = (
        complaint
        or incident_date_value
        or actual_end
        or expected_end
        or service_start
        or delivery
        or contract
    )
    customer_service = (
        _customer_service_layer(
            reference_date=reference_date,
            large_business=large_business,
            act_applicable=customer_service_act_applicable,
        )
        if reference_date is not None
        else "unknown"
    )

    common = {
        "contract_date": contract,
        "delivery_date": delivery,
        "service_start_date": service_start,
        "expected_service_end_date": expected_end,
        "actual_service_end_date": actual_end,
        "incident_date": incident_date_value,
        "complaint_date": complaint,
        "withdrawal_notice_date": withdrawal,
        "scope": scope,
        "client_type": client,
        "contract_type": contract_kind,
        "purchase_channel": channel,
        "incident_type": incident,
        "withdrawal_layer": withdrawal_layer,
        "withdrawal_days": withdrawal_days,
        "withdrawal_information_delivered": withdrawal_info,
        "fully_performed_withdrawal_loss_possible": fully_performed_loss,
        "proportionate_payment_review": proportionate_review,
        "customer_service_layer": customer_service,
    }

    if contract is None:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Falta la fecha documental del contrato, compra o aceptación; no "
                "puede seleccionarse el régimen temporal aplicable."
            ),
        )
    if contract < CIVIL_LIMITATION_CURRENT_FROM:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El contrato es anterior al horizonte histórico versionado desde "
                "la reforma general de prescripción de 2015."
            ),
        )

    dated_values = (
        contract,
        delivery,
        service_start,
        expected_end,
        actual_end,
        incident_date_value,
        complaint,
        withdrawal,
    )
    if any(value is not None and value > CURRENT_RULESET_SAFE_THROUGH for value in dated_values):
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La contratación, entrega, incidencia o reclamación supera el "
                "horizonte jurídico verificado."
            ),
        )

    if delivery is not None and delivery < contract:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason="La entrega aparece anterior al contrato o compra.",
        )
    if service_start is not None and service_start < contract:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason="El servicio aparece iniciado antes del contrato.",
        )
    if expected_end is not None and service_start is not None and expected_end < service_start:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason="El fin previsto del servicio es anterior a su inicio.",
        )
    if actual_end is not None and service_start is not None and actual_end < service_start:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason="El fin real del servicio es anterior a su inicio.",
        )
    if complaint is not None and complaint < contract:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason="La reclamación aparece anterior al contrato.",
        )
    if withdrawal is not None and withdrawal < contract:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason="El desistimiento aparece anterior al contrato.",
        )

    if scope != "spain":
        reason = (
            "El contrato es transfronterizo UE/EEE; deben determinarse ley "
            "aplicable, foro y autoridad competente."
            if scope == "eu_eea_cross_border"
            else (
                "Interviene un tercer país o no consta un contrato íntegramente "
                "español; deben determinarse ley, foro y régimen local."
            )
        )
        if scope == "unknown":
            reason = (
                "Faltan los países documentales del consumidor y de la empresa; "
                "no puede confirmarse el régimen español."
            )
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=reason,
        )

    if client != "consumer":
        reason = (
            "El cliente actúa como empresa o profesional; el régimen de consumo no "
            "puede aplicarse automáticamente."
            if client == "business"
            else (
                "No consta que el cliente actuara con finalidad ajena a su actividad "
                "empresarial o profesional."
            )
        )
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=reason,
        )

    if channel == "distance":
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La compra o contratación es online o a distancia y debe dirigirse "
                "al especialista claims.ecommerce."
            ),
        )

    sector_boundaries = (
        (
            marketplace_involved,
            "Interviene un marketplace y debe aplicarse claims.ecommerce.",
        ),
        (
            telecommunications_involved,
            "La materia es de telecomunicaciones y debe aplicarse claims.telecommunications.",
        ),
        (energy_involved, "La materia es de energía y debe aplicarse claims.energy."),
        (
            banking_or_payment_involved,
            "La materia principal es bancaria o de pago y debe aplicarse claims.banking.",
        ),
        (insurance_involved, "La materia es aseguradora y debe aplicarse claims.insurance."),
        (travel_involved, "La materia pertenece al satélite de viajes."),
        (
            professional_service_involved,
            "El encargo es un servicio profesional y debe aplicarse claims.professional_services.",
        ),
        (
            public_administration_involved,
            "La actuación pertenece al satélite de Administración pública.",
        ),
        (
            housing_or_tenancy_involved,
            "La controversia de vivienda o arrendamiento requiere especialista propio.",
        ),
        (
            healthcare_involved,
            "La controversia sanitaria requiere responsabilidad y documentación especializada.",
        ),
        (
            legal_service_involved,
            "El servicio jurídico requiere hoja de encargo, actuación procesal y deontología especializada.",
        ),
        (
            investment_involved,
            "La inversión o asesoría financiera requiere normativa sectorial.",
        ),
        (
            data_protection_primary,
            "La protección de datos es la cuestión principal y requiere especialista propio.",
        ),
        (
            motor_vehicle_involved,
            "El vehículo a motor puede activar normativa sectorial de taller, compraventa o circulación.",
        ),
        (
            digital_content_or_service,
            "El contenido o servicio digital debe encauzarse por comercio electrónico o servicios digitales.",
        ),
    )
    for flag, reason in sector_boundaries:
        if _optional_bool(flag) is True:
            return ClaimsConsumerRegimeDecision(
                status="operator_review",
                **common,
                blocking_reason=reason,
            )

    if _optional_bool(unsafe_product) is True or incident == "product_safety":
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Existe una posible incidencia de seguridad de producto; deben "
                "coordinarse retirada, autoridad, trazabilidad y responsabilidad."
            ),
        )
    if _optional_bool(personal_injury) is True:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Se alegan lesiones personales; deben revisarse asistencia, prueba "
                "médica, causalidad, seguro y responsabilidad especializada."
            ),
        )

    goods_layer = contract_kind in {"goods", "mixed"}
    goods_reference = delivery or contract
    if goods_layer and goods_reference < CURRENT_GOODS_RULES_FROM:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La compra o entrega del bien es anterior al régimen de conformidad "
                "versionado desde el 1 de enero de 2022."
            ),
        )

    if withdrawal_layer and contract < CURRENT_OFF_PREMISES_RULES_FROM:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El contrato fuera de establecimiento es anterior al horizonte "
                "transitorio versionado desde el 28 de mayo de 2022."
            ),
        )

    second_hand = _optional_bool(second_hand_goods)
    agreed_period: Optional[float] = None
    try:
        if second_hand_agreed_period_years not in (None, ""):
            agreed_period = float(str(second_hand_agreed_period_years).replace(",", "."))
    except (TypeError, ValueError):
        agreed_period = None
    if second_hand is True and agreed_period is not None and agreed_period < 1:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El periodo pactado para el bien de segunda mano figura por debajo "
                "del mínimo legal de un año y debe revisarse el documento."
            ),
        )

    basis = [*_COMMON_BASIS, *_ADR_BASIS, *_LIMITATION_BASIS]
    if goods_layer:
        basis.extend(_GOODS_BASIS)
    if contract_kind in {"service", "mixed"}:
        basis.extend(_SERVICE_BASIS)
    if withdrawal_layer:
        basis.extend(_OFF_PREMISES_BASIS)
    if customer_service in {"transition", "active"}:
        basis.extend(_CUSTOMER_SERVICE_BASIS)

    warnings = [
        (
            "El especialista de consumo general es residual: cualquier materia "
            "sectorial identificada debe salir a su especialista específico."
        ),
        (
            "La compra presencial sin defecto no concede por sí sola un derecho "
            "general de devolución; deben revisarse política comercial y contrato."
        ),
        (
            "El ticket facilita la prueba, pero no es el único medio posible para "
            "acreditar compra, fecha, producto y precio."
        ),
        (
            "La publicidad y la oferta pueden integrar el contrato, pero un error "
            "manifiesto de precio exige análisis y no permite una conclusión automática."
        ),
        (
            "La garantía comercial no sustituye ni reduce los derechos legales de "
            "conformidad cuando estos resulten aplicables."
        ),
        (
            "Reparación, sustitución, reducción del precio y resolución requieren "
            "revisar viabilidad, proporcionalidad, intentos previos y gravedad."
        ),
        (
            "La hoja de reclamaciones, el arbitraje y una entidad ADR dependen de "
            "normativa territorial, competencia y adhesión que deben acreditarse."
        ),
        (
            "Los reembolsos, pagos de tarjeta, seguro y recuperaciones de terceros "
            "deben coordinarse para evitar una doble recuperación."
        ),
        (
            "Los plazos civiles son candidatos; deben fijarse acción, dies a quo, "
            "interrupciones, suspensión por reparación y reglas especiales."
        ),
    ]
    if contract_kind == "unknown":
        warnings.append(
            "No se ha cerrado si el objeto principal es un bien, un servicio o un contrato mixto."
        )
    if goods_layer and second_hand is True and agreed_period is None:
        warnings.append(
            "En el bien de segunda mano debe aportarse el pacto expreso sobre un eventual periodo inferior a tres años."
        )
    if channel == "in_store" and incident == "withdrawal":
        warnings.append(
            "El desistimiento legal no debe confundirse con la devolución comercial de una compra presencial."
        )
    if withdrawal_info is False:
        warnings.append(
            "La falta de información sobre desistimiento puede ampliar el plazo, pero exige cálculo y prueba específicos."
        )
    if fully_performed is True and withdrawal_layer and not fully_performed_loss:
        warnings.append(
            "La ejecución completa no elimina por sí sola el desistimiento sin solicitud, consentimiento y conocimiento documentados."
        )
    if proportionate_review:
        warnings.append(
            "La ejecución parcial durante el desistimiento exige revisar solicitud expresa y cálculo proporcional."
        )
    if customer_service == "unknown":
        warnings.append(
            "No puede aplicarse un plazo de quince días hábiles sin acreditar el ámbito de la Ley 10/2025."
        )
    elif customer_service == "transition":
        warnings.append(
            "La empresa se encuentra en el periodo transitorio de adaptación de la Ley 10/2025."
        )

    return ClaimsConsumerRegimeDecision(
        status="current",
        **common,
        goods_conformity_layer=goods_layer,
        legal_conformity_period_years=3 if goods_layer else None,
        presumed_origin_period_years=2 if goods_layer else None,
        second_hand_minimum_agreed_years=(1 if second_hand is True else None),
        contractual_limitation_candidate_years=(
            5 if contract_kind in {"service", "mixed"} else None
        ),
        extracontractual_limitation_candidate_years=(
            1 if incident == "damage_or_loss" else None
        ),
        customer_service_resolution_business_days=(
            15 if customer_service == "active" else None
        ),
        ruleset=CURRENT_RULESET_CODE,
        legal_basis=tuple(dict.fromkeys(basis)),
        warnings=tuple(dict.fromkeys(warnings)),
    )
