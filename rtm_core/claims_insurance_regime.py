"""Selector conservador del régimen de seguros generales RTM.

Distingue seguros de daños y de personas, tipologías de póliza e incidencias de
cobertura, peritación, pago, prima, prórroga, salud, vida y responsabilidad
civil. Falla de forma cerrada ante seguros de viaje, productos de inversión,
planes de pensiones, daños corporales de circulación, jurisdicciones extranjeras
o periodos no versionados. No decide cobertura ni calcula intereses.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


CLAIMS_INSURANCE_REGIME_VERSION = "rtm_claims_insurance_regime_v1_0"
INSURANCE_CONTRACT_ACT_EFFECTIVE_ON = date(1981, 4, 17)
CUSTOMER_SERVICE_REFORM_EFFECTIVE_ON = date(2025, 12, 28)
CURRENT_RULESET_SAFE_THROUGH = date(2027, 12, 31)
CURRENT_RULESET_CODE = "spain_general_insurance_2025_v1"

ScopeCode = Literal[
    "spain",
    "eu_eea_cross_border",
    "third_country",
    "unknown",
]
CoverageNature = Literal["damage", "persons", "mixed", "unknown"]
ProductType = Literal[
    "home_property",
    "motor_own_damage",
    "health",
    "accident",
    "life",
    "pet",
    "funeral",
    "legal_expenses",
    "liability",
    "other_damage",
    "mixed",
    "travel",
    "investment_or_pension",
    "motor_third_party_injury",
    "unknown",
]
IncidentType = Literal[
    "coverage_denial",
    "valuation_or_underpayment",
    "handling_or_payment_delay",
    "premium_or_suspension",
    "nonrenewal_or_modification",
    "health_authorization",
    "life_or_beneficiary",
    "third_party_liability",
    "concurrent_insurance",
    "general_claim",
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

_DAMAGE_MARKERS = (
    "seguro de danos",
    "danos materiales",
    "hogar",
    "vivienda",
    "incendio",
    "robo",
    "agua",
    "continente",
    "contenido",
    "vehiculo",
    "todo riesgo",
    "mascota",
    "responsabilidad civil",
    "defensa juridica",
    "franquicia",
    "peritacion",
    "reparacion",
)
_PERSON_MARKERS = (
    "seguro de personas",
    "salud",
    "asistencia sanitaria",
    "enfermedad",
    "accidente",
    "vida",
    "fallecimiento",
    "invalidez",
    "incapacidad",
    "decesos",
    "beneficiario",
    "capital asegurado",
)

_COMMON_BASIS = (
    (
        "Ley 50/1980, de Contrato de Seguro, artículos 1, 3, 5 y 8, sobre "
        "prestación pactada, documentación de la póliza, claridad de las "
        "condiciones y aceptación específica de las cláusulas limitativas."
    ),
    (
        "Ley 50/1980, artículos 10 a 13, sobre declaración del riesgo mediante "
        "cuestionario, inexactitud, agravación y consecuencias que deben "
        "analizarse según las preguntas y hechos documentados."
    ),
    (
        "Ley 50/1980, artículos 16 a 20, sobre comunicación del siniestro, "
        "información, salvamento, investigación, pago mínimo y mora, sin "
        "anticipar pérdida de derechos, intereses ni cuantías automáticas."
    ),
)
_PREMIUM_BASIS = (
    (
        "Ley 50/1980, artículos 14 y 15, sobre pago de la prima, primera prima, "
        "primas sucesivas, suspensión, resolución y reactivación de cobertura."
    ),
)
_RENEWAL_BASIS = (
    (
        "Ley 50/1980, artículo 22, sobre duración, prórroga, oposición del "
        "tomador y del asegurador y comunicación de modificaciones contractuales."
    ),
)
_DAMAGE_BASIS = (
    (
        "Ley 50/1980, artículos 25 a 27 y 32, sobre interés asegurado, daño "
        "efectivo, suma asegurada y coordinación de seguros concurrentes."
    ),
    (
        "Ley 50/1980, artículos 38 y 43, sobre valoración pericial y subrogación "
        "del asegurador tras el pago, sin duplicar la recuperación del daño."
    ),
)
_PERSON_BASIS = (
    (
        "Ley 50/1980, artículos 80 a 82, sobre seguros de personas y límites de "
        "la subrogación respecto de gastos de asistencia sanitaria."
    ),
)
_HEALTH_BASIS = (
    (
        "Ley 50/1980, artículos 105 y 106, sobre seguros de enfermedad y "
        "asistencia sanitaria dentro de las condiciones y límites pactados."
    ),
)
_LIFE_BASIS = (
    (
        "Ley 50/1980, artículos 83 a 88, sobre seguro de vida, personas "
        "cubiertas y designación o revocación del beneficiario."
    ),
)
_LIABILITY_BASIS = (
    (
        "Ley 50/1980, artículos 73 y 76, sobre seguro de responsabilidad civil y "
        "acción directa del perjudicado, sin prejuzgar culpa, cobertura o cuantía."
    ),
)
_COMPLAINT_BASIS = (
    (
        "Ley 44/2002, artículos 29 y 30, en la redacción vigente, y normativa de "
        "protección del cliente financiero, sobre reclamación previa y acceso a "
        "la vía pública de reclamaciones cuando concurran sus requisitos."
    ),
)
_DISTRIBUTION_BASIS = (
    (
        "Real Decreto-ley 3/2020, libro segundo, título I, sobre distribución de "
        "seguros, información del producto y adecuación a las demandas y "
        "necesidades del cliente cuando intervino un mediador o distribuidor."
    ),
)


class ClaimsInsuranceRegimeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["current", "operator_review"]
    policy_date: Optional[date] = None
    coverage_start: Optional[date] = None
    coverage_end: Optional[date] = None
    loss_date: Optional[date] = None
    sac_complaint_date: Optional[date] = None
    scope: ScopeCode = "unknown"
    product_type: ProductType = "unknown"
    coverage_nature: CoverageNature = "unknown"
    incident_type: IncidentType = "unknown"
    limitation_years: Optional[int] = None
    notice_days: Optional[int] = None
    minimum_payment_days: Optional[int] = None
    performance_months: Optional[int] = None
    policyholder_nonrenewal_months: Optional[int] = None
    insurer_nonrenewal_months: Optional[int] = None
    modification_notice_months: Optional[int] = None
    customer_service_wait_months: Optional[int] = None
    financial_complaint_resolution_days: Optional[int] = None
    distribution_layer: bool = False
    direct_action_layer: bool = False
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


def _scope(value: Any) -> ScopeCode:
    folded = _fold(value)
    if not folded:
        return "unknown"
    if any(token in folded for token in _SPAIN_TOKENS):
        return "spain"
    if any(token in folded for token in _EU_EEA_TOKENS):
        return "eu_eea_cross_border"
    return "third_country"


def _product_type(value: Any, text: Any) -> ProductType:
    explicit = _fold(value)
    folded = _fold((value, text))
    if not folded:
        return "unknown"
    if any(marker in explicit for marker in ("mixto", "mixta", "multirriesgo mixt")):
        return "mixed"
    if any(
        marker in folded
        for marker in (
            "seguro de viaje",
            "cancelacion de viaje",
            "equipaje de viaje",
            "repatriacion",
        )
    ):
        return "travel"
    if any(
        marker in folded
        for marker in (
            "unit linked",
            "producto de inversion",
            "ahorro inversion",
            "plan de pensiones",
            "pension plan",
        )
    ):
        return "investment_or_pension"
    if any(
        marker in folded
        for marker in (
            "danos corporales de circulacion",
            "lesiones accidente de trafico",
            "seguro obligatorio de automovil",
            "responsabilidad civil obligatoria del automovil",
        )
    ):
        return "motor_third_party_injury"
    markers: list[ProductType] = []
    if any(marker in folded for marker in ("hogar", "vivienda", "multirriesgo hogar", "continente", "contenido")):
        markers.append("home_property")
    if any(marker in folded for marker in ("todo riesgo", "danos propios vehiculo", "casco del vehiculo")):
        markers.append("motor_own_damage")
    if any(marker in folded for marker in ("seguro de salud", "asistencia sanitaria", "seguro medico", "cuadro medico")):
        markers.append("health")
    if any(marker in folded for marker in ("seguro de accidentes", "accidente personal")):
        markers.append("accident")
    if any(marker in folded for marker in ("seguro de vida", "capital por fallecimiento", "beneficiario de vida")):
        markers.append("life")
    if any(marker in folded for marker in ("seguro de mascotas", "seguro veterinario", "mascota")):
        markers.append("pet")
    if any(marker in folded for marker in ("seguro de decesos", "servicio funerario", "decesos")):
        markers.append("funeral")
    if any(marker in folded for marker in ("defensa juridica", "proteccion juridica", "gastos juridicos")):
        markers.append("legal_expenses")
    if any(marker in folded for marker in ("responsabilidad civil", "seguro de responsabilidad")):
        markers.append("liability")
    if any(marker in folded for marker in ("seguro de danos", "incendio", "robo", "danos materiales")):
        markers.append("other_damage")
    unique = list(dict.fromkeys(markers))
    if len(unique) > 1:
        return "mixed"
    return unique[0] if unique else "unknown"


def _coverage_nature(explicit: Any, coverages: Any, product: ProductType) -> CoverageNature:
    text = _fold((explicit, coverages))
    if any(marker in text for marker in ("mixto", "mixta", "coberturas combinadas")):
        return "mixed"
    has_damage = any(marker in text for marker in _DAMAGE_MARKERS)
    has_persons = any(marker in text for marker in _PERSON_MARKERS)
    if has_damage and has_persons:
        return "mixed"
    if has_damage:
        return "damage"
    if has_persons:
        return "persons"
    if product in {
        "home_property",
        "motor_own_damage",
        "pet",
        "legal_expenses",
        "liability",
        "other_damage",
    }:
        return "damage"
    if product in {"health", "accident", "life", "funeral"}:
        return "persons"
    if product == "mixed":
        return "mixed"
    return "unknown"


def _classify_incident(folded: str) -> IncidentType:
    if not folded:
        return "unknown"
    if any(
        marker in folded
        for marker in (
            "denegacion de cobertura",
            "cobertura rechazada",
            "siniestro rechazado",
            "exclusion invocada",
            "no cubierto por la poliza",
        )
    ):
        return "coverage_denial"
    if any(
        marker in folded
        for marker in (
            "oferta insuficiente",
            "indemnizacion insuficiente",
            "discrepancia pericial",
            "valoracion pericial",
            "infraseguro",
            "importe ofertado",
        )
    ):
        return "valuation_or_underpayment"
    if any(
        marker in folded
        for marker in (
            "demora en el pago",
            "retraso en la tramitacion",
            "siniestro pendiente",
            "sin respuesta de la aseguradora",
            "pago pendiente",
        )
    ):
        return "handling_or_payment_delay"
    if any(
        marker in folded
        for marker in (
            "prima impagada",
            "suspension de cobertura",
            "falta de pago de la prima",
            "reactivacion de cobertura",
        )
    ):
        return "premium_or_suspension"
    if any(
        marker in folded
        for marker in (
            "no renovacion",
            "oposicion a la prorroga",
            "modificacion de la poliza",
            "cambio de condiciones del seguro",
        )
    ):
        return "nonrenewal_or_modification"
    if any(
        marker in folded
        for marker in (
            "autorizacion medica",
            "tratamiento denegado",
            "prueba medica denegada",
            "asistencia sanitaria denegada",
        )
    ):
        return "health_authorization"
    if any(
        marker in folded
        for marker in (
            "beneficiario",
            "capital de vida",
            "fallecimiento del asegurado",
            "seguro de vida",
        )
    ):
        return "life_or_beneficiary"
    if any(
        marker in folded
        for marker in (
            "accion directa",
            "tercero perjudicado",
            "responsabilidad civil",
        )
    ):
        return "third_party_liability"
    if any(
        marker in folded
        for marker in (
            "seguro concurrente",
            "doble seguro",
            "otra aseguradora",
            "seguros concurrentes",
        )
    ):
        return "concurrent_insurance"
    if any(marker in folded for marker in ("poliza", "siniestro", "aseguradora", "peritacion")):
        return "general_claim"
    return "unknown"


def _incident_type(explicit: Any, text: Any) -> IncidentType:
    explicit_result = _classify_incident(_fold(explicit))
    if explicit_result != "unknown":
        return explicit_result
    return _classify_incident(_fold(text))


def resolve_claims_insurance_regime(
    *,
    policy_date: Any,
    coverage_start: Any,
    coverage_end: Any,
    loss_date: Any,
    insurer_country: Any,
    product_type: Any,
    coverage_nature: Any,
    policy_coverages: Any,
    incident_type: Any,
    issue_text: Any,
    sac_complaint_date: Any = None,
    insurance_distributor: Any = None,
    harmed_third_party: Any = None,
    travel_insurance: Any = None,
    motor_third_party_injury: Any = None,
    investment_linked: Any = None,
    pension_plan: Any = None,
) -> ClaimsInsuranceRegimeDecision:
    policy = _parse_date(policy_date)
    start = _parse_date(coverage_start)
    end = _parse_date(coverage_end)
    loss = _parse_date(loss_date)
    sac_date = _parse_date(sac_complaint_date)
    scope = _scope(insurer_country)
    travel = _optional_bool(travel_insurance)
    motor_injury = _optional_bool(motor_third_party_injury)
    investment = _optional_bool(investment_linked)
    pension = _optional_bool(pension_plan)
    product = _product_type(product_type, issue_text)
    if travel is True:
        product = "travel"
    elif investment is True or pension is True:
        product = "investment_or_pension"
    elif motor_injury is True:
        product = "motor_third_party_injury"
    nature = _coverage_nature(coverage_nature, policy_coverages, product)
    incident = _incident_type(incident_type, issue_text)
    distributor = str(insurance_distributor or "").strip()
    third_party = _optional_bool(harmed_third_party)
    distribution_layer = bool(distributor)
    direct_action_layer = third_party is True or incident == "third_party_liability"

    common = {
        "policy_date": policy,
        "coverage_start": start,
        "coverage_end": end,
        "loss_date": loss,
        "sac_complaint_date": sac_date,
        "scope": scope,
        "product_type": product,
        "coverage_nature": nature,
        "incident_type": incident,
        "distribution_layer": distribution_layer,
        "direct_action_layer": direct_action_layer,
    }

    if policy is None:
        return ClaimsInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Falta la fecha documental de contratación o emisión de la póliza."
            ),
        )
    if policy < INSURANCE_CONTRACT_ACT_EFFECTIVE_ON:
        return ClaimsInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La póliza es anterior al horizonte histórico versionado desde la "
                "entrada en vigor de la Ley 50/1980."
            ),
        )
    if any(
        value is not None and value > CURRENT_RULESET_SAFE_THROUGH
        for value in (policy, start, end, loss, sac_date)
    ):
        return ClaimsInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La póliza, cobertura, siniestro o reclamación supera el horizonte "
                "jurídico verificado. Deben versionarse las reformas posteriores."
            ),
        )
    if start is None or end is None:
        return ClaimsInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason="Faltan las fechas documentales de inicio y fin de cobertura.",
        )
    if end < start:
        return ClaimsInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason="La fecha final de cobertura aparece anterior a la inicial.",
        )
    if loss is None:
        return ClaimsInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason="Falta la fecha documental del siniestro o evento asegurado.",
        )
    if policy > loss:
        return ClaimsInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La póliza figura contratada después del siniestro. Debe revisarse "
                "la existencia del riesgo y cualquier error documental."
            ),
        )
    if loss < start or loss > end:
        return ClaimsInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El siniestro queda fuera del periodo documental de cobertura. "
                "Deben revisarse hora de efecto, prórrogas y recibos."
            ),
        )
    if scope != "spain":
        reason = (
            "La aseguradora aparece establecida en otro Estado UE/EEE o Suiza. "
            "Deben verificarse ley aplicable, distribución transfronteriza y foro."
            if scope == "eu_eea_cross_border"
            else (
                "No consta una aseguradora sometida al marco español. Deben "
                "determinarse ley, autoridad y procedimiento aplicables."
            )
        )
        return ClaimsInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=reason,
        )
    if product == "travel":
        return ClaimsInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El expediente corresponde a seguro de viaje y debe dirigirse al "
                "especialista travel.insurance."
            ),
        )
    if product == "investment_or_pension":
        return ClaimsInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Los seguros de inversión, unit-linked y planes de pensiones "
                "requieren un régimen financiero específico no cubierto aquí."
            ),
        )
    if product == "motor_third_party_injury":
        return ClaimsInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Los daños corporales y la responsabilidad obligatoria derivados "
                "de circulación requieren el especialista de accidentes de tráfico."
            ),
        )
    if product == "unknown":
        return ClaimsInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Falta identificar el ramo: hogar, daños, salud, vida, accidentes, "
                "responsabilidad civil, decesos, mascotas o defensa jurídica."
            ),
        )
    if incident == "unknown":
        return ClaimsInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Falta concretar la incidencia: rechazo, valoración, demora, prima, "
                "prórroga, salud, vida, responsabilidad o concurrencia."
            ),
        )

    basis = list(_COMMON_BASIS)
    if incident == "premium_or_suspension":
        basis.extend(_PREMIUM_BASIS)
    if incident == "nonrenewal_or_modification":
        basis.extend(_RENEWAL_BASIS)

    limitation_years: Optional[int] = None
    if nature == "damage":
        basis.extend(_DAMAGE_BASIS)
        limitation_years = 2
    elif nature == "persons":
        basis.extend(_PERSON_BASIS)
        limitation_years = 5
    elif nature == "mixed":
        basis.extend(_DAMAGE_BASIS)
        basis.extend(_PERSON_BASIS)

    if product == "health":
        basis.extend(_HEALTH_BASIS)
    elif product == "life":
        basis.extend(_LIFE_BASIS)
    if product == "liability" or direct_action_layer:
        basis.extend(_LIABILITY_BASIS)

    basis.extend(_COMPLAINT_BASIS)
    if distribution_layer:
        basis.extend(_DISTRIBUTION_BASIS)

    reference_date = sac_date or loss
    modern_claim_path = reference_date >= CUSTOMER_SERVICE_REFORM_EFFECTIVE_ON
    warnings = [
        (
            "La póliza, condiciones particulares y recibos delimitan el riesgo "
            "dentro de las normas imperativas; el nombre comercial no acredita cobertura."
        ),
        (
            "La comunicación posterior a siete días no implica por sí sola pérdida "
            "automática del derecho; deben revisarse perjuicio, dolo o culpa grave."
        ),
        (
            "Los plazos de cuarenta días y tres meses no permiten fijar "
            "automáticamente mora, intereses ni cuantía sin revisar la causa."
        ),
        (
            "La autenticidad del cuestionario, las preguntas formuladas y la "
            "aceptación de cláusulas limitativas requieren prueba documental."
        ),
        (
            "Pagos de terceros, otras aseguradoras o responsables deben coordinarse "
            "para evitar una doble recuperación del mismo daño."
        ),
    ]
    if nature in {"mixed", "unknown"}:
        warnings.append(
            "No puede seleccionarse un único plazo de prescripción sin separar cada cobertura entre daños y personas."
        )
    if sac_date is not None and sac_date < CUSTOMER_SERVICE_REFORM_EFFECTIVE_ON:
        warnings.append(
            "La reclamación al servicio de atención es anterior a la reforma de 2025 y requiere revisión transitoria o histórica."
        )
    if product == "mixed":
        warnings.append(
            "La póliza agrupa varios ramos; cada garantía debe analizarse con su propia naturaleza, límite, exclusiones y petición."
        )

    return ClaimsInsuranceRegimeDecision(
        status="current",
        **common,
        limitation_years=limitation_years,
        notice_days=7,
        minimum_payment_days=40,
        performance_months=3,
        policyholder_nonrenewal_months=1,
        insurer_nonrenewal_months=2,
        modification_notice_months=2,
        customer_service_wait_months=1 if modern_claim_path else None,
        financial_complaint_resolution_days=90 if modern_claim_path else None,
        ruleset=CURRENT_RULESET_CODE,
        legal_basis=tuple(dict.fromkeys(basis)),
        warnings=tuple(dict.fromkeys(warnings)),
    )
