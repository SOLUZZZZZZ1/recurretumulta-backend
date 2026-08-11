"""Selector conservador del régimen de comercio electrónico RTM.

Versiona contratación a distancia, entrega, desistimiento, conformidad de bienes
 y contenidos digitales, mercados en línea y seguridad de producto. Falla de
forma cerrada cuando falta la fecha del contrato, el vendedor no actúa como
empresario, el comprador no puede encuadrarse como consumidor, interviene una
ley extranjera o la controversia exige una reforma todavía no incorporada.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


CLAIMS_ECOMMERCE_REGIME_VERSION = "rtm_claims_ecommerce_regime_v1_0"

DISTANCE_CONTRACT_BASELINE_ON = date(2014, 6, 13)
GOODS_DIGITAL_CONFORMITY_ON = date(2022, 1, 1)
MARKETPLACE_INFORMATION_ON = date(2022, 5, 28)
DSA_FULL_APPLICATION_ON = date(2024, 2, 17)
GENERAL_PRODUCT_SAFETY_ON = date(2024, 12, 13)
ODR_PLATFORM_REPEALED_ON = date(2025, 7, 20)
RIGHT_TO_REPAIR_REVIEW_FROM = date(2026, 7, 31)
CURRENT_RULESET_SAFE_THROUGH = date(2027, 12, 31)
CURRENT_RULESET_CODE = "spain_ecommerce_consumer_2014_2026_v1"

ScopeCode = Literal["spain", "foreign", "unknown"]
ProductType = Literal[
    "goods",
    "digital_content",
    "digital_service",
    "service",
    "mixed",
    "unknown",
]
IncidentType = Literal[
    "non_delivery",
    "late_delivery",
    "partial_or_wrong_delivery",
    "non_conformity",
    "withdrawal",
    "refund_delay",
    "subscription",
    "digital_content",
    "marketplace_disclosure",
    "unsafe_product",
    "seller_identity_or_illicit_goods",
    "general",
    "unknown",
]

_DISTANCE_BASIS = (
    (
        "Real Decreto Legislativo 1/2007, de 16 de noviembre, artículos 92 a "
        "113, sobre contratos a distancia, información precontractual, "
        "desistimiento, ejecución, entrega y reembolso."
    ),
    (
        "Ley 34/2002, de 11 de julio, artículos 27 a 29, sobre información "
        "previa y posterior y formación de contratos por vía electrónica."
    ),
)
_CONFORMITY_BASIS = (
    (
        "Real Decreto Legislativo 1/2007, artículos 114 a 127 bis, en la "
        "redacción aplicable a contratos celebrados desde el 1 de enero de "
        "2022, sobre conformidad, medidas correctoras, plazos y servicios posventa."
    ),
)
_MARKETPLACE_BASIS = (
    (
        "Real Decreto Legislativo 1/2007, artículo 97 bis, y Real Decreto-ley "
        "24/2021, sobre información específica en mercados en línea, condición "
        "de empresario, reparto de obligaciones, clasificación y precios."
    ),
)
_DSA_MARKETPLACE_BASIS = (
    (
        "Reglamento (UE) 2022/2065, artículos 30 a 32, sobre trazabilidad de "
        "comerciantes, diseño de interfaces de mercados en línea e información "
        "cuando se conozca la oferta de productos o servicios ilícitos."
    ),
)
_PRODUCT_SAFETY_BASIS = (
    (
        "Reglamento (UE) 2023/988, aplicable desde el 13 de diciembre de 2024, "
        "sobre seguridad general de los productos y obligaciones de los mercados "
        "en línea, sin prejuzgar conformidad contractual ni responsabilidad civil."
    ),
)
_SUBSCRIPTION_BASIS = (
    (
        "Real Decreto Legislativo 1/2007, artículos 60, 62, 80 a 90 y 97, "
        "sobre información, duración, renovación, baja, cláusulas abusivas y "
        "contratos a distancia, según el producto o servicio contratado."
    ),
)
_DIGITAL_BASIS = (
    (
        "Real Decreto Legislativo 1/2007, artículos 103.m), 114 a 126 bis, "
        "sobre desistimiento, conformidad, actualizaciones y modificación de "
        "contenidos o servicios digitales."
    ),
)


class ClaimsEcommerceRegimeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["current", "operator_review"]
    contract_date: Optional[date] = None
    reference_date: Optional[date] = None
    scope: ScopeCode = "unknown"
    product_type: ProductType = "unknown"
    incident_type: IncidentType = "unknown"
    consumer_contract: bool = False
    consumer_status_review: bool = False
    trader_contract: bool = False
    distance_contract: bool = False
    marketplace_layer: bool = False
    marketplace_information_active: bool = False
    dsa_marketplace_active: bool = False
    product_safety_active: bool = False
    odr_platform_available: bool = False
    right_to_repair_review: bool = False
    withdrawal_days: Optional[int] = None
    delivery_default_days: Optional[int] = None
    withdrawal_refund_days: Optional[int] = None
    goods_conformity_years: Optional[int] = None
    digital_conformity_years: Optional[int] = None
    goods_presumption_years: Optional[int] = None
    digital_presumption_years: Optional[int] = None
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
    if folded in {
        "si",
        "true",
        "1",
        "consta",
        "acreditado",
        "empresario",
        "consumidor",
        "particular consumidor",
    }:
        return True
    if folded in {
        "no",
        "false",
        "0",
        "no consta",
        "no acreditado",
        "vendedor privado",
        "actividad profesional",
        "actividad empresarial",
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


def _product_type(
    contract_type: Any,
    product_description: Any,
    service_description: Any,
    *,
    goods_with_digital_elements: Optional[bool],
    digital_content_or_service: Optional[bool],
    text: Any,
) -> ProductType:
    folded = _fold((contract_type, product_description, service_description, text))
    if not folded and goods_with_digital_elements is not True and digital_content_or_service is not True:
        return "unknown"

    if goods_with_digital_elements is True:
        return "goods"

    digital_content = any(
        marker in folded
        for marker in (
            "contenido digital",
            "descarga digital",
            "ebook",
            "libro electronico",
            "videojuego descargable",
            "archivo digital",
            "licencia digital",
        )
    )
    digital_service = any(
        marker in folded
        for marker in (
            "servicio digital",
            "software como servicio",
            "aplicacion online",
            "almacenamiento en la nube",
            "plataforma digital",
            "streaming",
        )
    )
    if digital_content_or_service is True and not (digital_content or digital_service):
        digital_service = bool(service_description)
        digital_content = not digital_service

    service = any(
        marker in folded
        for marker in (
            "servicio contratado online",
            "prestacion de servicios",
            "servicio ordinario",
            "curso online en directo",
        )
    )
    goods = bool(product_description) or any(
        marker in folded
        for marker in (
            "bien",
            "producto",
            "articulo",
            "mercancia",
            "pedido",
            "paquete",
            "electrodomestico",
            "telefono",
            "ordenador",
            "ropa",
            "mueble",
        )
    )

    active: list[ProductType] = []
    if digital_content:
        active.append("digital_content")
    if digital_service:
        active.append("digital_service")
    if service:
        active.append("service")
    if goods:
        active.append("goods")
    unique = list(dict.fromkeys(active))
    if len(unique) > 1:
        if "goods" in unique and (
            "digital_content" in unique or "digital_service" in unique
        ) and "elementos digitales" in folded:
            return "goods"
        return "mixed"
    return unique[0] if unique else "unknown"


def _incident_type(
    explicit: Any,
    text: Any,
    *,
    product_type: ProductType,
    order_delivered: Optional[bool],
    delivery_date: Optional[date],
    agreed_delivery_date: Optional[date],
    nonconformity_present: bool,
    withdrawal_communicated: Optional[bool],
    withdrawal_date: Optional[date],
    refund_claimed: bool,
    refund_date: Optional[date],
    subscription: Optional[bool],
    automatic_renewal: Optional[bool],
    marketplace_present: bool,
    seller_identified: Optional[bool],
    trader_status_disclosed: Optional[bool],
    unsafe_product: Optional[bool],
) -> IncidentType:
    folded = _fold((explicit, text))

    if unsafe_product is True or any(
        marker in folded
        for marker in (
            "producto inseguro",
            "producto peligroso",
            "retirada de producto",
            "alerta de seguridad",
        )
    ):
        return "unsafe_product"

    if any(
        marker in folded
        for marker in (
            "producto falsificado",
            "tienda falsa",
            "vendedor inexistente",
            "vendedor no localizado",
            "identidad del vendedor desconocida",
            "oferta ilicita",
        )
    ):
        return "seller_identity_or_illicit_goods"

    if marketplace_present and (
        seller_identified is False
        or trader_status_disclosed is False
        or any(
            marker in folded
            for marker in (
                "marketplace no identifica",
                "plataforma no informa",
                "condicion de empresario",
                "reparto de obligaciones",
                "parametros de clasificacion",
            )
        )
    ):
        return "marketplace_disclosure"

    if subscription is True or automatic_renewal is True or any(
        marker in folded
        for marker in (
            "renovacion automatica",
            "suscripcion renovada",
            "baja de suscripcion",
            "cargo de suscripcion",
        )
    ):
        return "subscription"

    if withdrawal_communicated is True or withdrawal_date is not None or any(
        marker in folded
        for marker in (
            "desistimiento",
            "derecho de desistir",
            "devolver sin causa",
        )
    ):
        return "withdrawal"

    if order_delivered is False or any(
        marker in folded
        for marker in (
            "pedido no entregado",
            "producto no entregado",
            "no recibio el pedido",
            "paquete perdido",
        )
    ):
        return "non_delivery"

    if any(
        marker in folded
        for marker in (
            "pedido incompleto",
            "entrega parcial",
            "producto distinto",
            "articulo equivocado",
            "cantidad inferior",
        )
    ):
        return "partial_or_wrong_delivery"

    if delivery_date and agreed_delivery_date and delivery_date > agreed_delivery_date:
        return "late_delivery"
    if any(
        marker in folded
        for marker in (
            "entrega tardia",
            "retraso en la entrega",
            "entregado fuera de plazo",
        )
    ):
        return "late_delivery"

    if product_type in {"digital_content", "digital_service"} and any(
        marker in folded
        for marker in (
            "contenido digital no funciona",
            "servicio digital no funciona",
            "falta de actualizacion",
            "actualizacion",
            "incompatible",
            "interoperabilidad",
            "modificacion del servicio digital",
            "acceso digital bloqueado",
        )
    ):
        return "digital_content"

    if nonconformity_present or any(
        marker in folded
        for marker in (
            "falta de conformidad",
            "producto defectuoso",
            "producto no conforme",
            "no coincide con la publicidad",
            "averia del producto",
        )
    ):
        return "non_conformity"

    if refund_claimed and refund_date is None or any(
        marker in folded
        for marker in (
            "reembolso pendiente",
            "no devuelve el dinero",
            "devolucion del importe pendiente",
            "reembolso parcial",
        )
    ):
        return "refund_delay"

    if any(
        marker in folded
        for marker in (
            "compra online",
            "pedido online",
            "comercio electronico",
            "marketplace",
        )
    ):
        return "general"
    return "unknown"


def resolve_claims_ecommerce_regime(
    *,
    purchase_date: Any,
    delivery_date: Any,
    incident_date: Any,
    withdrawal_date: Any,
    complaint_date: Any,
    seller_country: Any,
    consumer_country: Any,
    buyer_is_consumer: Any,
    seller_is_trader: Any,
    distance_contract: Any,
    contract_type: Any,
    product_description: Any,
    service_description: Any,
    goods_with_digital_elements: Any,
    digital_content_or_service: Any,
    incident_type: Any,
    issue_text: Any,
    marketplace_present: Any = None,
    platform_is_contracting_party: Any = None,
    order_delivered: Any = None,
    agreed_delivery_date: Any = None,
    nonconformity_description: Any = None,
    withdrawal_communicated: Any = None,
    refund_amount: Any = None,
    refund_date: Any = None,
    subscription: Any = None,
    automatic_renewal: Any = None,
    seller_identified: Any = None,
    trader_status_disclosed: Any = None,
    unsafe_product: Any = None,
    post_guarantee_repair_requested: Any = None,
) -> ClaimsEcommerceRegimeDecision:
    contract = _parse_date(purchase_date)
    delivered_on = _parse_date(delivery_date)
    agreed_on = _parse_date(agreed_delivery_date)
    withdrew_on = _parse_date(withdrawal_date)
    refunded_on = _parse_date(refund_date)
    reference = (
        _parse_date(incident_date)
        or withdrew_on
        or _parse_date(complaint_date)
        or delivered_on
        or contract
    )
    scope = _scope(seller_country)
    consumer = _optional_bool(buyer_is_consumer)
    trader = _optional_bool(seller_is_trader)
    distance = _optional_bool(distance_contract)
    marketplace = bool(str(marketplace_present or "").strip())
    platform_party = _optional_bool(platform_is_contracting_party)
    delivered = _optional_bool(order_delivered)
    withdrawal = _optional_bool(withdrawal_communicated)
    renewal = _optional_bool(automatic_renewal)
    subscription_value = _optional_bool(subscription)
    seller_identity = _optional_bool(seller_identified)
    trader_disclosed = _optional_bool(trader_status_disclosed)
    unsafe = _optional_bool(unsafe_product)
    post_guarantee = _optional_bool(post_guarantee_repair_requested)
    goods_digital = _optional_bool(goods_with_digital_elements)
    digital = _optional_bool(digital_content_or_service)

    product = _product_type(
        contract_type,
        product_description,
        service_description,
        goods_with_digital_elements=goods_digital,
        digital_content_or_service=digital,
        text=issue_text,
    )
    incident = _incident_type(
        incident_type,
        issue_text,
        product_type=product,
        order_delivered=delivered,
        delivery_date=delivered_on,
        agreed_delivery_date=agreed_on,
        nonconformity_present=bool(nonconformity_description),
        withdrawal_communicated=withdrawal,
        withdrawal_date=withdrew_on,
        refund_claimed=refund_amount not in (None, "", [], {}),
        refund_date=refunded_on,
        subscription=subscription_value,
        automatic_renewal=renewal,
        marketplace_present=marketplace,
        seller_identified=seller_identity,
        trader_status_disclosed=trader_disclosed,
        unsafe_product=unsafe,
    )

    consumer_country_present = bool(str(consumer_country or "").strip())
    consumer_review = consumer is None
    common = {
        "contract_date": contract,
        "reference_date": reference,
        "scope": scope,
        "product_type": product,
        "incident_type": incident,
        "consumer_contract": consumer is True,
        "consumer_status_review": consumer_review,
        "trader_contract": trader is True,
        "distance_contract": distance is True,
        "marketplace_layer": marketplace,
        "marketplace_information_active": bool(
            reference and reference >= MARKETPLACE_INFORMATION_ON
        ),
        "dsa_marketplace_active": bool(reference and reference >= DSA_FULL_APPLICATION_ON),
        "product_safety_active": bool(
            reference and reference >= GENERAL_PRODUCT_SAFETY_ON
        ),
        "odr_platform_available": bool(reference and reference < ODR_PLATFORM_REPEALED_ON),
        "right_to_repair_review": bool(
            post_guarantee is True
            and reference
            and reference >= RIGHT_TO_REPAIR_REVIEW_FROM
        ),
    }

    if contract is None:
        return ClaimsEcommerceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Falta la fecha documental de compra o celebración del contrato "
                "para seleccionar la versión normativa aplicable."
            ),
        )
    if reference is None:
        return ClaimsEcommerceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason="Falta una fecha documental para encuadrar la incidencia.",
        )
    if reference > CURRENT_RULESET_SAFE_THROUGH:
        return ClaimsEcommerceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El expediente supera el horizonte jurídico verificado para "
                "comercio electrónico. Deben versionarse las reformas posteriores."
            ),
        )
    if scope != "spain":
        return ClaimsEcommerceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "No consta un vendedor sometido al marco español. Deben verificarse "
                "ley aplicable, domicilio del consumidor y jurisdicción."
            ),
        )
    if consumer is False:
        return ClaimsEcommerceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La compra figura vinculada a una actividad empresarial o "
                "profesional; no puede aplicarse automáticamente el régimen de consumo."
            ),
        )
    if consumer is None and not consumer_country_present:
        return ClaimsEcommerceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "No está acreditada la condición o el domicilio del comprador "
                "necesarios para encuadrar la protección de consumo."
            ),
        )
    if trader is not True:
        return ClaimsEcommerceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "No está acreditado que el vendedor actuara como empresario. Las "
                "ventas entre particulares requieren otro encuadre."
            ),
        )
    if distance is not True:
        return ClaimsEcommerceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "No está acreditado que el contrato se celebrara a distancia o "
                "por medios electrónicos dentro del régimen seleccionado."
            ),
        )
    if contract < DISTANCE_CONTRACT_BASELINE_ON:
        return ClaimsEcommerceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El contrato es anterior a la línea temporal incorporada para "
                "contratación a distancia y requiere normativa histórica."
            ),
        )
    if product in {"unknown", "mixed"}:
        return ClaimsEcommerceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Debe determinarse si el objeto es un bien, contenido digital, "
                "servicio digital o servicio ordinario."
            ),
        )
    if incident == "unknown":
        return ClaimsEcommerceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Falta concretar la incidencia de entrega, conformidad, "
                "desistimiento, reembolso, suscripción o marketplace."
            ),
        )
    if (
        incident in {"non_conformity", "digital_content", "subscription"}
        and contract < GOODS_DIGITAL_CONFORMITY_ON
    ):
        return ClaimsEcommerceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La controversia de conformidad o contenido digital es anterior al "
                "régimen incorporado con efectos de 1 de enero de 2022."
            ),
        )
    if incident == "marketplace_disclosure" and reference < MARKETPLACE_INFORMATION_ON:
        return ClaimsEcommerceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La incidencia de información del marketplace es anterior a la "
                "versión incorporada desde el 28 de mayo de 2022."
            ),
        )
    if incident == "unsafe_product" and reference < GENERAL_PRODUCT_SAFETY_ON:
        return ClaimsEcommerceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La incidencia de seguridad es anterior a la aplicación del "
                "Reglamento (UE) 2023/988 y requiere la versión histórica."
            ),
        )
    if common["right_to_repair_review"]:
        return ClaimsEcommerceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La reparación solicitada fuera de garantía es posterior al 31 de "
                "julio de 2026. Debe verificarse la transposición española vigente "
                "de la Directiva (UE) 2024/1799 antes de afirmar una obligación."
            ),
        )

    basis = list(_DISTANCE_BASIS)
    if product in {"goods", "digital_content", "digital_service"}:
        basis.extend(_CONFORMITY_BASIS)
    if product in {"digital_content", "digital_service"}:
        basis.extend(_DIGITAL_BASIS)
    if incident == "subscription":
        basis.extend(_SUBSCRIPTION_BASIS)
    if marketplace and reference >= MARKETPLACE_INFORMATION_ON:
        basis.extend(_MARKETPLACE_BASIS)
    if marketplace and reference >= DSA_FULL_APPLICATION_ON:
        basis.extend(_DSA_MARKETPLACE_BASIS)
    if incident == "unsafe_product" and reference >= GENERAL_PRODUCT_SAFETY_ON:
        basis.extend(_PRODUCT_SAFETY_BASIS)

    warnings = [
        (
            "El marketplace no responde automáticamente como vendedor por el "
            "incumplimiento de un tercero; deben reconstruirse su rol y sus actos propios."
        ),
        (
            "El transportista, el vendedor y la plataforma pueden intervenir en "
            "fases distintas; la prueba de entrega y la elección del porte son relevantes."
        ),
        (
            "Una devolución o recuperación del medio de pago no resuelve por sí "
            "sola la relación contractual ni permite una doble recuperación."
        ),
    ]
    if consumer_review:
        warnings.append(
            "La condición de consumidor no figura como hecho expreso; debe confirmarse antes de aprobar o congelar la previa."
        )
    if reference >= ODR_PLATFORM_REPEALED_ON:
        warnings.append(
            "La antigua plataforma europea ODR fue suprimida con efectos de 20 de julio de 2025 y no debe ofrecerse como vía disponible."
        )
    if marketplace and platform_party is not True:
        warnings.append(
            "Consta marketplace, pero no que la plataforma fuera parte contractual; la reclamación principal puede corresponder al vendedor tercero."
        )
    if reference >= RIGHT_TO_REPAIR_REVIEW_FROM:
        warnings.append(
            "Las pretensiones de reparación fuera de la garantía legal exigen comprobar la normativa española vigente tras el 31 de julio de 2026."
        )

    goods = product == "goods"
    digital_product = product in {"digital_content", "digital_service"}
    return ClaimsEcommerceRegimeDecision(
        status="current",
        **common,
        withdrawal_days=14,
        delivery_default_days=30,
        withdrawal_refund_days=14,
        goods_conformity_years=3 if goods else None,
        digital_conformity_years=2 if digital_product else None,
        goods_presumption_years=2 if goods else None,
        digital_presumption_years=1 if digital_product else None,
        ruleset=CURRENT_RULESET_CODE,
        legal_basis=tuple(dict.fromkeys(basis)),
        warnings=tuple(dict.fromkeys(warnings)),
    )
