"""Selector conservador del régimen de energía y suministros RTM.

Distingue electricidad y gas, versiona las transiciones normativas de 2026 y
falla de forma cerrada cuando falta fecha, país, tipo de suministro o debe
aplicarse una normativa histórica no incorporada. No recalcula facturas, no
decide la responsabilidad entre comercializadora y distribuidora y no da por
acreditada la vulnerabilidad.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


CLAIMS_ENERGY_REGIME_VERSION = "rtm_claims_energy_regime_v1_0"

ELECTRICITY_GENERAL_EFFECTIVE_ON = date(2026, 2, 12)
ELECTRICITY_DEFERRED_RULES_EFFECTIVE_ON = date(2026, 6, 12)
GAS_BASELINE_EFFECTIVE_ON = date(2003, 1, 1)
GAS_2026_CONTRACT_NOTICE_EFFECTIVE_ON = date(2026, 3, 21)
CUSTOMER_SERVICE_LAW_EFFECTIVE_ON = date(2025, 12, 28)
CUSTOMER_SERVICE_FULL_ADAPTATION_ON = date(2026, 12, 28)
TEMPORARY_VULNERABLE_PROTECTION_THROUGH = date(2026, 12, 31)
CURRENT_RULESET_SAFE_THROUGH = date(2027, 12, 31)

ELECTRICITY_RULESET = "spain_electricity_supply_2026_v1"
GAS_RULESET = "spain_gas_supply_2003_2026_v1"

SupplyType = Literal["electricity", "gas", "other", "unknown"]
ScopeCode = Literal["spain", "foreign", "unknown"]
IncidentType = Literal[
    "billing",
    "reading",
    "contract_change",
    "unauthorized_switch",
    "unsolicited_service",
    "suspension",
    "outage_quality",
    "vulnerable_protection",
    "general",
    "unknown",
]


_COMMON_CONSUMER_BASIS = (
    (
        "Real Decreto Legislativo 1/2007, de 16 de noviembre, artículos 8, "
        "21 y 57, cuando el reclamante tenga la condición de consumidor."
    ),
)
_CUSTOMER_SERVICE_BASIS = (
    (
        "Ley 10/2025, de 26 de diciembre, artículos 12, 13 y 17, sobre "
        "constancia, motivación y plazo de atención a la clientela, con "
        "revisión de su disposición transitoria única."
    ),
)
_ELECTRICITY_GENERAL_BASIS = (
    (
        "Ley 24/2013, de 26 de diciembre, artículo 44.1, sobre derechos del "
        "consumidor de energía eléctrica."
    ),
    (
        "Real Decreto 88/2026, de 11 de febrero, artículos 54 a 58, sobre "
        "protección, reclamación previa, atención y vías alternativas."
    ),
)
_ELECTRICITY_BILLING_BASIS = (
    (
        "Real Decreto 88/2026, de 11 de febrero, artículos 43 a 45, sobre "
        "lectura, facturación, errores, retrasos, devoluciones y "
        "regularizaciones desde su eficacia específica."
    ),
)
_ELECTRICITY_CONTRACT_BASIS = (
    (
        "Real Decreto 88/2026, de 11 de febrero, artículos 28 a 30, sobre "
        "cambio de comercializador, consentimiento, servicios no solicitados "
        "y contenido contractual desde su eficacia específica."
    ),
)
_ELECTRICITY_SUSPENSION_BASIS = (
    (
        "Real Decreto 88/2026, de 11 de febrero, artículos 46 a 53, sobre "
        "suspensión, reposición y suministros esenciales."
    ),
)
_VULNERABLE_BASIS = (
    (
        "Real Decreto 897/2017, de 6 de octubre, sobre consumidor vulnerable, "
        "bono social, suspensión y suministros esenciales."
    ),
)
_TEMPORARY_2026_BASIS = (
    (
        "Real Decreto-ley 7/2026, de 20 de marzo, artículo 4, sobre garantía "
        "temporal de suministro de agua y energía a consumidores vulnerables "
        "hasta el 31 de diciembre de 2026."
    ),
)
_GAS_GENERAL_BASIS = (
    (
        "Ley 34/1998, de 7 de octubre, artículos 57, 57 bis y 79, sobre "
        "derechos, medición, facturación, cambio y reclamaciones en gas natural."
    ),
    (
        "Real Decreto 1434/2002, de 27 de diciembre, artículos 43 a 61, según "
        "la materia, sobre cambio de suministrador, medida, facturación, pago, "
        "suspensión y reposición."
    ),
)
_GAS_2026_CHANGE_BASIS = (
    (
        "Ley 34/1998, de 7 de octubre, artículo 57 bis.f), en la redacción "
        "vigente desde el Real Decreto-ley 7/2026, sobre aviso escrito y "
        "separado con al menos un mes de antelación para determinadas "
        "modificaciones y revisiones de precio."
    ),
)


class ClaimsEnergyRegimeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["current", "operator_review"]
    reference_date: Optional[date] = None
    scope: ScopeCode = "unknown"
    supply_type: SupplyType = "unknown"
    incident_type: IncidentType = "unknown"
    ruleset: Optional[str] = None
    billing_rules_active: bool = False
    customer_service_transition_complete: bool = False
    complaint_response_business_days: Optional[int] = None
    modification_notice_days: Optional[int] = None
    temporary_vulnerable_protection: bool = False
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
    if folded in {"si", "true", "1", "consta", "acreditado"}:
        return True
    if folded in {"no", "false", "0", "no consta", "no acreditado"}:
        return False
    return None


def _scope(value: Any) -> ScopeCode:
    folded = _fold(value)
    if not folded:
        return "unknown"
    if "espana" in folded or "spain" in folded:
        return "spain"
    return "foreign"


def _supply_type(value: Any, text: Any) -> SupplyType:
    folded = _fold((value, text))
    if not folded:
        return "unknown"
    electricity = any(
        marker in folded
        for marker in (
            "electricidad",
            "electrico",
            "electrica",
            "luz",
            "kwh",
            "kw",
            "pvpc",
            "2 0td",
            "3 0td",
        )
    )
    gas = any(
        marker in folded
        for marker in (
            "gas natural",
            "suministro de gas",
            "tur gas",
            "contador de gas",
            "peaje de gas",
        )
    )
    if electricity and gas:
        return "other"
    if electricity:
        return "electricity"
    if gas:
        return "gas"
    if any(marker in folded for marker in ("agua", "butano", "propano", "glp")):
        return "other"
    return "unknown"


def _incident_type(explicit: Any, text: Any) -> IncidentType:
    folded = _fold((explicit, text))
    if not folded:
        return "unknown"
    if any(
        marker in folded
        for marker in (
            "cambio de comercializadora no consentido",
            "cambio de suministrador no consentido",
            "switch without consent",
            "alta no consentida",
        )
    ):
        return "unauthorized_switch"
    if any(
        marker in folded
        for marker in (
            "servicio no solicitado",
            "servicio adicional no contratado",
            "unsolicited service",
        )
    ):
        return "unsolicited_service"
    if any(
        marker in folded
        for marker in (
            "modificacion de precio",
            "cambio de tarifa",
            "cambio de condiciones",
            "revision de precio",
            "precio fijo",
        )
    ):
        return "contract_change"
    if any(
        marker in folded
        for marker in (
            "corte de suministro",
            "suspension del suministro",
            "desconexion",
            "reconexion",
            "impago",
        )
    ):
        return "suspension"
    if any(
        marker in folded
        for marker in (
            "consumidor vulnerable",
            "bono social",
            "suministro esencial",
            "riesgo de exclusion",
        )
    ):
        return "vulnerable_protection"
    if any(
        marker in folded
        for marker in (
            "apagones",
            "interrupcion",
            "averia",
            "calidad del suministro",
            "tension",
        )
    ):
        return "outage_quality"
    if any(
        marker in folded
        for marker in (
            "lectura estimada",
            "lectura del contador",
            "contador",
            "lectura incorrecta",
        )
    ):
        return "reading"
    if any(
        marker in folded
        for marker in (
            "factura",
            "facturacion",
            "regularizacion",
            "refacturacion",
            "cobro",
            "importe",
        )
    ):
        return "billing"
    return "general"


def resolve_claims_energy_regime(
    *,
    incident_date: Any,
    contract_date: Any,
    invoice_date: Any,
    complaint_date: Any,
    supply_country: Any,
    supply_type: Any,
    incident_type: Any,
    issue_text: Any,
    vulnerable_consumer: Any = None,
) -> ClaimsEnergyRegimeDecision:
    reference = (
        _parse_date(incident_date)
        or _parse_date(invoice_date)
        or _parse_date(complaint_date)
        or _parse_date(contract_date)
    )
    scope = _scope(supply_country)
    supply = _supply_type(supply_type, issue_text)
    incident = _incident_type(incident_type, issue_text)
    vulnerable = _optional_bool(vulnerable_consumer)

    common = {
        "reference_date": reference,
        "scope": scope,
        "supply_type": supply,
        "incident_type": incident,
    }

    if reference is None:
        return ClaimsEnergyRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Falta una fecha documental de incidencia, factura, reclamación "
                "o contrato para seleccionar la versión normativa aplicable."
            ),
        )

    if reference > CURRENT_RULESET_SAFE_THROUGH:
        return ClaimsEnergyRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El asunto supera el horizonte jurídico verificado para energía "
                "y suministros. Deben versionarse las reformas posteriores."
            ),
        )

    if scope != "spain":
        return ClaimsEnergyRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "No consta un suministro sometido al marco español. Deben "
                "determinarse ley, autoridad y procedimiento aplicables."
            ),
        )

    if supply in {"unknown", "other"}:
        return ClaimsEnergyRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "No puede determinarse si el expediente corresponde a electricidad "
                "o gas natural. Agua, GLP y otros suministros requieren otra versión."
            ),
        )

    if incident == "unknown":
        return ClaimsEnergyRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Falta concretar la incidencia: facturación, lectura, contrato, "
                "cambio de suministrador, corte, calidad o vulnerabilidad."
            ),
        )

    transition_complete = reference >= CUSTOMER_SERVICE_FULL_ADAPTATION_ON
    temporary_vulnerable = (
        vulnerable is True
        and date(2026, 1, 1) <= reference <= TEMPORARY_VULNERABLE_PROTECTION_THROUGH
    )
    warnings = [
        (
            "La comercializadora, la distribuidora y, en su caso, el operador "
            "de medida pueden asumir funciones distintas; deben separarse por "
            "cada actuación documentada."
        ),
        (
            "La factura no debe recalcularse automáticamente sin precio, periodo, "
            "lecturas, consumo, impuestos, peajes y condiciones contractuales."
        ),
        (
            "Las competencias administrativas y de consumo pueden variar por "
            "comunidad autónoma y por la materia concreta."
        ),
    ]

    if reference >= CUSTOMER_SERVICE_LAW_EFFECTIVE_ON and not transition_complete:
        warnings.append(
            "La Ley 10/2025 está en vigor, pero su disposición transitoria concede "
            "doce meses para adaptar los servicios de atención a la clientela."
        )

    basis = list(_COMMON_CONSUMER_BASIS)
    if reference >= CUSTOMER_SERVICE_LAW_EFFECTIVE_ON:
        basis.extend(_CUSTOMER_SERVICE_BASIS)

    if supply == "electricity":
        if reference < ELECTRICITY_GENERAL_EFFECTIVE_ON:
            return ClaimsEnergyRegimeDecision(
                status="operator_review",
                **common,
                customer_service_transition_complete=transition_complete,
                blocking_reason=(
                    "La incidencia eléctrica es anterior al Reglamento de 2026 y "
                    "la normativa histórica aplicable no está versionada en este selector."
                ),
            )

        deferred_incidents = {
            "billing",
            "reading",
            "contract_change",
            "unauthorized_switch",
            "unsolicited_service",
        }
        if (
            incident in deferred_incidents
            and reference < ELECTRICITY_DEFERRED_RULES_EFFECTIVE_ON
        ):
            return ClaimsEnergyRegimeDecision(
                status="operator_review",
                **common,
                billing_rules_active=False,
                customer_service_transition_complete=transition_complete,
                complaint_response_business_days=15,
                blocking_reason=(
                    "La materia depende de artículos del Real Decreto 88/2026 que "
                    "aún no habían surtido efectos. Debe aplicarse la normativa "
                    "anterior de forma versionada."
                ),
            )

        basis.extend(_ELECTRICITY_GENERAL_BASIS)
        billing_active = reference >= ELECTRICITY_DEFERRED_RULES_EFFECTIVE_ON
        if incident in {"billing", "reading"}:
            basis.extend(_ELECTRICITY_BILLING_BASIS)
        if incident in {
            "contract_change",
            "unauthorized_switch",
            "unsolicited_service",
        }:
            basis.extend(_ELECTRICITY_CONTRACT_BASIS)
        if incident in {
            "suspension",
            "outage_quality",
            "vulnerable_protection",
        }:
            basis.extend(_ELECTRICITY_SUSPENSION_BASIS)
        if vulnerable is True or incident == "vulnerable_protection":
            basis.extend(_VULNERABLE_BASIS)
        if temporary_vulnerable:
            basis.extend(_TEMPORARY_2026_BASIS)

        return ClaimsEnergyRegimeDecision(
            status="current",
            **common,
            ruleset=ELECTRICITY_RULESET,
            billing_rules_active=billing_active,
            customer_service_transition_complete=transition_complete,
            complaint_response_business_days=15,
            modification_notice_days=30
            if incident == "contract_change" and billing_active
            else None,
            temporary_vulnerable_protection=temporary_vulnerable,
            legal_basis=tuple(dict.fromkeys(basis)),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    if reference < GAS_BASELINE_EFFECTIVE_ON:
        return ClaimsEnergyRegimeDecision(
            status="operator_review",
            **common,
            customer_service_transition_complete=transition_complete,
            blocking_reason=(
                "La incidencia de gas es anterior al horizonte histórico "
                "versionado desde 2003."
            ),
        )

    if (
        incident == "contract_change"
        and reference < GAS_2026_CONTRACT_NOTICE_EFFECTIVE_ON
    ):
        return ClaimsEnergyRegimeDecision(
            status="operator_review",
            **common,
            customer_service_transition_complete=transition_complete,
            blocking_reason=(
                "La modificación contractual de gas es anterior a la redacción "
                "vigente desde marzo de 2026 y requiere versión histórica."
            ),
        )

    basis.extend(_GAS_GENERAL_BASIS)
    notice_days = None
    if incident == "contract_change":
        basis.extend(_GAS_2026_CHANGE_BASIS)
        notice_days = 30
    if vulnerable is True or incident == "vulnerable_protection":
        basis.extend(_VULNERABLE_BASIS)
    if temporary_vulnerable:
        basis.extend(_TEMPORARY_2026_BASIS)

    if not transition_complete:
        warnings.append(
            "Antes de finalizar la adaptación a la Ley 10/2025 no se fija "
            "automáticamente un plazo general de respuesta para gas."
        )

    return ClaimsEnergyRegimeDecision(
        status="current",
        **common,
        ruleset=GAS_RULESET,
        billing_rules_active=True,
        customer_service_transition_complete=transition_complete,
        complaint_response_business_days=15 if transition_complete else None,
        modification_notice_days=notice_days,
        temporary_vulnerable_protection=temporary_vulnerable,
        legal_basis=tuple(dict.fromkeys(basis)),
        warnings=tuple(dict.fromkeys(warnings)),
    )
