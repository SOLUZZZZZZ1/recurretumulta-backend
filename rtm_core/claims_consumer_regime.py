"""Selector conservador del régimen de consumo general RTM.

Este selector solo cubre la capa residual de consumo español: compras
presenciales de bienes y servicios ordinarios que no pertenecen a un sector con
especialista propio. No decide responsabilidad, cuantía, prescripción, daño
moral, abusividad, seguridad de producto ni la autoridad territorial concreta.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


CLAIMS_CONSUMER_REGIME_VERSION = "rtm_claims_consumer_regime_v1_0"

GENERAL_CONSUMER_BASELINE_ON = date(2007, 12, 1)
ADR_CONSUMER_BASELINE_ON = date(2017, 11, 5)
GOODS_CONFORMITY_CURRENT_ON = date(2022, 1, 1)
GENERAL_PRODUCT_SAFETY_ON = date(2024, 12, 13)
ODR_PLATFORM_REPEALED_ON = date(2025, 7, 20)
CUSTOMER_SERVICE_ACT_ON = date(2025, 12, 28)
CURRENT_RULESET_SAFE_THROUGH = date(2027, 12, 31)
CURRENT_RULESET_CODE = "spain_general_consumer_2007_2026_v1"

ScopeCode = Literal["spain", "foreign", "unknown"]
ObjectType = Literal["goods", "service", "mixed", "unknown"]
IncidentType = Literal[
    "non_conformity",
    "guarantee",
    "service_not_performed",
    "service_incomplete_or_defective",
    "price_or_advertising",
    "refund_or_cancellation",
    "unfair_term",
    "unsafe_product",
    "general",
    "unknown",
]

_GENERAL_BASIS = (
    (
        "Real Decreto Legislativo 1/2007, de 16 de noviembre, artículos 8, 20, "
        "60, 61 y 65, sobre derechos básicos, información, contenido de la oferta "
        "y vinculación de las condiciones comunicadas al consumidor."
    ),
    (
        "Real Decreto Legislativo 1/2007, artículos 80 a 91, sobre requisitos de "
        "las cláusulas no negociadas y control de cláusulas abusivas, cuya "
        "apreciación concreta requiere revisar el contrato y sus circunstancias."
    ),
    (
        "Código Civil, artículos 1091, 1101, 1124 y 1258, sobre fuerza obligatoria "
        "del contrato, incumplimiento, resolución y buena fe, sin fijar de forma "
        "automática responsabilidad ni indemnización."
    ),
)
_GOODS_BASIS = (
    (
        "Real Decreto Legislativo 1/2007, artículos 114 a 127 bis, en la redacción "
        "aplicable a contratos celebrados desde el 1 de enero de 2022, sobre "
        "conformidad de bienes, reparación, sustitución, reducción del precio, "
        "resolución y garantías comerciales."
    ),
)
_RETAIL_BASIS = (
    (
        "Ley 7/1996, de 15 de enero, de Ordenación del Comercio Minorista, en las "
        "reglas aplicables a información, oferta, precio y venta al por menor, sin "
        "sustituir la normativa autonómica o sectorial que corresponda."
    ),
)
_ADR_BASIS = (
    (
        "Ley 7/2017, de 2 de noviembre, sobre resolución alternativa de litigios "
        "en materia de consumo, para valorar una entidad acreditada o el sistema "
        "arbitral cuando el asunto y el empresario resulten admisibles."
    ),
)
_CUSTOMER_SERVICE_BASIS = (
    (
        "Ley 10/2025, de 26 de diciembre, reguladora de los servicios de atención "
        "a la clientela, cuando la empresa y el servicio queden dentro de su ámbito "
        "y una vez comprobado su régimen transitorio y de aplicación."
    ),
)

_SPECIALIZED_LABELS = {
    "telecommunications": "telecomunicaciones",
    "energy": "energía y suministros",
    "banking": "banca o medios de pago",
    "insurance": "seguros",
    "ecommerce": "comercio electrónico o marketplace",
    "professional_services": "servicios profesionales",
    "travel": "viajes o transporte de pasajeros",
    "housing": "arrendamiento o vivienda",
    "health": "servicios sanitarios",
    "legal": "servicios jurídicos",
    "construction": "arquitectura, obra o edificación",
    "investment": "inversión, criptoactivos o productos financieros",
    "privacy": "protección de datos o comunicaciones comerciales",
    "administration": "Administración pública o sanciones",
    "debt": "deudas, recobro o ficheros de solvencia",
    "motor_injury": "daños personales derivados de circulación",
}


class ClaimsConsumerRegimeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["current", "operator_review"]
    contract_date: Optional[date] = None
    reference_date: Optional[date] = None
    scope: ScopeCode = "unknown"
    object_type: ObjectType = "unknown"
    incident_type: IncidentType = "unknown"
    consumer_contract: bool = False
    consumer_status_review: bool = False
    trader_contract: bool = False
    residual_scope: bool = False
    specialized_boundary: Optional[str] = None
    goods_conformity_active: bool = False
    adr_layer: bool = False
    customer_service_layer: bool = False
    product_safety_review: bool = False
    odr_platform_available: bool = False
    goods_conformity_years: Optional[int] = None
    goods_presumption_years: Optional[int] = None
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
        "consumidor",
        "empresario",
        "profesional",
    }:
        return True
    if folded in {
        "no",
        "false",
        "0",
        "no consta",
        "no acreditado",
        "particular",
        "actividad empresarial",
        "actividad profesional",
    }:
        return False
    return None


def _scope(trader_country: Any, consumer_country: Any) -> ScopeCode:
    trader = _fold(trader_country)
    consumer = _fold(consumer_country)
    if not trader:
        return "unknown"
    if "espana" not in trader and "spain" not in trader:
        return "foreign"
    if consumer and "espana" not in consumer and "spain" not in consumer:
        return "foreign"
    return "spain"


def _object_type(explicit: Any, product: Any, service: Any, text: Any) -> ObjectType:
    folded = _fold((explicit, product, service, text))
    if not folded:
        return "unknown"
    has_goods = bool(str(product or "").strip()) or any(
        marker in folded
        for marker in (
            "bien de consumo",
            "producto",
            "articulo",
            "electrodomestico",
            "mueble",
            "ropa",
            "vehiculo",
            "mercancia",
        )
    )
    has_service = bool(str(service or "").strip()) or any(
        marker in folded
        for marker in (
            "servicio ordinario",
            "prestacion de servicio",
            "reparacion",
            "mantenimiento",
            "limpieza",
            "academia",
            "gimnasio",
        )
    )
    if has_goods and has_service:
        return "mixed"
    if has_goods:
        return "goods"
    if has_service:
        return "service"
    return "unknown"


def _incident_type(
    explicit: Any,
    text: Any,
    *,
    object_type: ObjectType,
    nonconformity: Optional[bool],
    nonconformity_description: Any,
    service_not_performed: Optional[bool],
    service_incomplete: Optional[bool],
    service_defective: Optional[bool],
    advertised_price: Any,
    charged_price: Any,
    surcharge: Any,
    disputed_term: Any,
    commercial_guarantee: Any,
    refund_requested: Any,
    termination_requested: Optional[bool],
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
    if disputed_term or any(
        marker in folded
        for marker in (
            "clausula abusiva",
            "clausula no negociada",
            "condicion abusiva",
            "penalizacion desproporcionada",
        )
    ):
        return "unfair_term"
    if (
        advertised_price not in (None, "", [], {})
        or charged_price not in (None, "", [], {})
        or surcharge not in (None, "", [], {})
        or any(
            marker in folded
            for marker in (
                "precio anunciado",
                "precio cobrado",
                "publicidad enganosa",
                "oferta incumplida",
                "cargo no informado",
            )
        )
    ):
        return "price_or_advertising"
    if commercial_guarantee or "garantia comercial" in folded:
        return "guarantee"
    if nonconformity is True or nonconformity_description or any(
        marker in folded
        for marker in (
            "falta de conformidad",
            "producto defectuoso",
            "producto no conforme",
            "averia del producto",
        )
    ):
        return "non_conformity"
    if service_not_performed is True or any(
        marker in folded
        for marker in (
            "servicio no prestado",
            "servicio no realizado",
            "no se presto el servicio",
        )
    ):
        return "service_not_performed"
    if service_incomplete is True or service_defective is True or any(
        marker in folded
        for marker in (
            "servicio incompleto",
            "servicio defectuoso",
            "servicio mal ejecutado",
            "prestacion defectuosa",
        )
    ):
        return "service_incomplete_or_defective"
    if (
        refund_requested not in (None, "", [], {})
        or termination_requested is True
        or any(
            marker in folded
            for marker in (
                "reembolso pendiente",
                "devolucion del dinero",
                "cancelacion",
                "resolucion del contrato",
            )
        )
    ):
        return "refund_or_cancellation"
    if "reclamacion de consumo" in folded or object_type != "unknown":
        return "general"
    return "unknown"


def _specialized_boundary(
    text: Any,
    regulated_hint: Any,
    *,
    online_purchase: Optional[bool],
) -> Optional[str]:
    folded = _fold((text, regulated_hint))
    if online_purchase is True:
        return "ecommerce"

    rules: tuple[tuple[str, tuple[str, ...]], ...] = (
        (
            "telecommunications",
            (
                "telecomunicaciones",
                "telefonia movil",
                "portabilidad",
                "operador de fibra",
                "servicio de internet",
            ),
        ),
        (
            "energy",
            (
                "electricidad",
                "comercializadora",
                "distribuidora electrica",
                "factura de gas",
                "suministro energetico",
            ),
        ),
        (
            "banking",
            (
                "cargo no reconocido",
                "operacion no autorizada",
                "transferencia bancaria",
                "adeudo domiciliado",
                "fraude bancario",
                "prestamo hipotecario",
            ),
        ),
        (
            "insurance",
            (
                "poliza de seguro",
                "aseguradora",
                "siniestro asegurado",
                "peritacion del seguro",
            ),
        ),
        (
            "ecommerce",
            (
                "compra online",
                "pedido online",
                "comercio electronico",
                "marketplace",
                "contrato a distancia",
            ),
        ),
        (
            "professional_services",
            (
                "servicios profesionales",
                "hoja de encargo",
                "honorarios profesionales",
                "consultoria profesional",
                "asesoria profesional",
            ),
        ),
        (
            "travel",
            (
                "vuelo",
                "aerolinea",
                "equipaje",
                "viaje combinado",
                "reserva de hotel",
                "agencia de viajes",
            ),
        ),
        (
            "housing",
            (
                "contrato de alquiler",
                "arrendamiento de vivienda",
                "renta impagada",
                "fianza arrendaticia",
            ),
        ),
        (
            "health",
            (
                "tratamiento medico",
                "clinica sanitaria",
                "diagnostico",
                "negligencia medica",
                "servicio sanitario",
            ),
        ),
        (
            "legal",
            (
                "abogado",
                "procurador",
                "defensa juridica",
                "asesoramiento juridico",
            ),
        ),
        (
            "construction",
            (
                "arquitecto",
                "aparejador",
                "direccion de obra",
                "defecto constructivo",
                "edificacion",
            ),
        ),
        (
            "investment",
            (
                "inversion financiera",
                "fondo de inversion",
                "acciones bursatiles",
                "criptoactivo",
                "criptomoneda",
                "plan de pensiones",
            ),
        ),
        (
            "privacy",
            (
                "proteccion de datos",
                "tratamiento de datos personales",
                "llamada comercial no solicitada",
                "correo publicitario no solicitado",
            ),
        ),
        (
            "administration",
            (
                "procedimiento sancionador",
                "recurso administrativo",
                "administracion publica",
                "providencia de apremio",
            ),
        ),
        (
            "debt",
            (
                "deuda impagada",
                "procedimiento monitorio",
                "recobro de deuda",
                "fichero de solvencia",
                "asnef",
            ),
        ),
        (
            "motor_injury",
            (
                "lesiones por accidente de trafico",
                "danos personales de circulacion",
                "atropello",
            ),
        ),
    )
    for code, markers in rules:
        if any(marker in folded for marker in markers):
            return code
    return None


def resolve_claims_consumer_regime(
    *,
    contract_date: Any,
    purchase_date: Any,
    delivery_date: Any,
    incident_date: Any,
    complaint_date: Any,
    trader_country: Any,
    consumer_country: Any,
    customer_is_consumer: Any,
    supplier_is_trader: Any,
    contract_channel: Any,
    online_purchase: Any,
    object_type: Any,
    product_description: Any,
    service_description: Any,
    incident_type: Any,
    issue_text: Any,
    regulated_service_hint: Any = None,
    nonconformity: Any = None,
    nonconformity_description: Any = None,
    service_not_performed: Any = None,
    service_incomplete: Any = None,
    service_defective: Any = None,
    advertised_price: Any = None,
    charged_price: Any = None,
    undisclosed_surcharge: Any = None,
    disputed_term: Any = None,
    commercial_guarantee: Any = None,
    refund_requested: Any = None,
    termination_requested: Any = None,
    unsafe_product: Any = None,
) -> ClaimsConsumerRegimeDecision:
    contract = _parse_date(contract_date) or _parse_date(purchase_date)
    delivered = _parse_date(delivery_date)
    incident_on = _parse_date(incident_date)
    complained = _parse_date(complaint_date)
    candidates = [value for value in (complained, incident_on, delivered, contract) if value]
    reference = max(candidates) if candidates else None

    consumer = _optional_bool(customer_is_consumer)
    trader = _optional_bool(supplier_is_trader)
    online = _optional_bool(online_purchase)
    nonconforming = _optional_bool(nonconformity)
    not_performed = _optional_bool(service_not_performed)
    incomplete = _optional_bool(service_incomplete)
    defective = _optional_bool(service_defective)
    termination = _optional_bool(termination_requested)
    unsafe = _optional_bool(unsafe_product)

    scope = _scope(trader_country, consumer_country)
    obj = _object_type(
        object_type,
        product_description,
        service_description,
        issue_text,
    )
    incident = _incident_type(
        incident_type,
        issue_text,
        object_type=obj,
        nonconformity=nonconforming,
        nonconformity_description=nonconformity_description,
        service_not_performed=not_performed,
        service_incomplete=incomplete,
        service_defective=defective,
        advertised_price=advertised_price,
        charged_price=charged_price,
        surcharge=undisclosed_surcharge,
        disputed_term=disputed_term,
        commercial_guarantee=commercial_guarantee,
        refund_requested=refund_requested,
        termination_requested=termination,
        unsafe_product=unsafe,
    )
    boundary = _specialized_boundary(
        (issue_text, contract_channel, product_description, service_description),
        regulated_service_hint,
        online_purchase=online,
    )

    common = {
        "contract_date": contract,
        "reference_date": reference,
        "scope": scope,
        "object_type": obj,
        "incident_type": incident,
        "consumer_contract": consumer is True,
        "consumer_status_review": consumer is None,
        "trader_contract": trader is True,
        "residual_scope": boundary is None,
        "specialized_boundary": boundary,
        "goods_conformity_active": bool(
            obj == "goods" and contract and contract >= GOODS_CONFORMITY_CURRENT_ON
        ),
        "adr_layer": bool(reference and reference >= ADR_CONSUMER_BASELINE_ON),
        "customer_service_layer": bool(
            reference and reference >= CUSTOMER_SERVICE_ACT_ON
        ),
        "product_safety_review": incident == "unsafe_product",
        "odr_platform_available": bool(
            reference and reference < ODR_PLATFORM_REPEALED_ON
        ),
    }

    if contract is None:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Falta la fecha documental de compra o contratación para seleccionar "
                "la versión normativa aplicable."
            ),
        )
    if reference is None:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason="Falta una fecha documental para encuadrar la incidencia.",
        )
    if contract < GENERAL_CONSUMER_BASELINE_ON:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El contrato es anterior a la línea temporal incorporada para "
                "consumo general y requiere revisión normativa histórica."
            ),
        )
    if reference > CURRENT_RULESET_SAFE_THROUGH:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El expediente supera el horizonte jurídico verificado para consumo "
                "general. Deben versionarse las reformas posteriores."
            ),
        )
    if scope != "spain":
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "No consta una relación de consumo sometida de forma segura al marco "
                "español. Deben verificarse ley aplicable, domicilio y jurisdicción."
            ),
        )
    if consumer is False:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La contratación figura vinculada a una actividad empresarial o "
                "profesional; no puede aplicarse automáticamente el régimen de consumo."
            ),
        )
    if consumer is None and not str(consumer_country or "").strip():
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "No está acreditada la condición ni el domicilio del consumidor."
            ),
        )
    if trader is not True:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "No está acreditado que la contraparte actuara como empresario o "
                "profesional; los contratos entre particulares requieren otro encuadre."
            ),
        )
    if boundary is not None:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Los hechos apuntan a la familia especializada de "
                f"{_SPECIALIZED_LABELS.get(boundary, boundary)}. El especialista "
                "residual de consumo no debe sustituir esa revisión sectorial."
            ),
        )
    if obj in {"unknown", "mixed"}:
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Debe separarse y documentarse si el objeto principal es un bien o "
                "un servicio ordinario antes de aplicar el régimen residual."
            ),
        )
    if incident == "unknown":
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Falta concretar el incumplimiento, defecto, precio, garantía, "
                "cancelación, reembolso o cláusula discutida."
            ),
        )
    if incident == "unsafe_product":
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El asunto incluye seguridad de producto. Deben activarse la ruta de "
                "retirada o alerta, las autoridades competentes y la eventual "
                "responsabilidad, sin limitarlo a una reclamación contractual ordinaria."
            ),
        )
    if (
        obj == "goods"
        and incident in {"non_conformity", "guarantee"}
        and contract < GOODS_CONFORMITY_CURRENT_ON
    ):
        return ClaimsConsumerRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La falta de conformidad es anterior al régimen incorporado con "
                "efectos de 1 de enero de 2022 y requiere la versión histórica."
            ),
        )

    basis = list(_GENERAL_BASIS)
    if obj == "goods":
        basis.extend(_RETAIL_BASIS)
        if contract >= GOODS_CONFORMITY_CURRENT_ON:
            basis.extend(_GOODS_BASIS)
    if reference >= ADR_CONSUMER_BASELINE_ON:
        basis.extend(_ADR_BASIS)
    if reference >= CUSTOMER_SERVICE_ACT_ON:
        basis.extend(_CUSTOMER_SERVICE_BASIS)

    warnings = [
        (
            "La familia de consumo general es residual: cualquier dato sectorial "
            "nuevo obliga a reconsiderar la familia antes de aprobar el escrito."
        ),
        (
            "La existencia de una avería o disconformidad no determina por sí sola "
            "causa, imputación, cuantía, daño moral ni indemnización."
        ),
        (
            "Reparación, sustitución, reducción del precio y resolución no son "
            "remedios acumulables de forma automática; deben ordenarse según los "
            "hechos, la proporcionalidad y la normativa aplicable."
        ),
        (
            "Reembolsos, abonos, seguros, devoluciones del medio de pago o cantidades "
            "recuperadas de terceros deben coordinarse para evitar doble recuperación."
        ),
    ]
    if consumer is None:
        warnings.append(
            "La condición de consumidor no figura como hecho expreso y debe confirmarse antes de congelar la previa."
        )
    if reference >= ODR_PLATFORM_REPEALED_ON:
        warnings.append(
            "La antigua plataforma europea ODR ya no debe ofrecerse como vía disponible."
        )
    if reference >= CUSTOMER_SERVICE_ACT_ON:
        warnings.append(
            "La aplicación de la Ley 10/2025 exige comprobar que la empresa y el servicio estén dentro de su ámbito y régimen transitorio."
        )
    if incident == "unfair_term":
        warnings.append(
            "La abusividad de una cláusula no se presume por su denominación: deben revisarse transparencia, negociación, equilibrio y contexto contractual."
        )

    goods_current = obj == "goods" and contract >= GOODS_CONFORMITY_CURRENT_ON
    return ClaimsConsumerRegimeDecision(
        status="current",
        **common,
        goods_conformity_years=3 if goods_current else None,
        goods_presumption_years=2 if goods_current else None,
        ruleset=CURRENT_RULESET_CODE,
        legal_basis=tuple(dict.fromkeys(basis)),
        warnings=tuple(dict.fromkeys(warnings)),
    )
