"""Selector conservador del régimen jurídico de seguro de viaje RTM.

Versiona el marco español general del contrato de seguro y de reclamaciones
financieras. No decide si un siniestro está cubierto, no valida exclusiones, no
calcula automáticamente intereses ni convierte el seguro de viaje en una única
modalidad: una póliza puede combinar coberturas de daños y de personas.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


TRAVEL_INSURANCE_REGIME_VERSION = "rtm_travel_insurance_regime_v1_0"
CURRENT_RULESET_CODE = "spain_travel_insurance_2025_v1"
INSURANCE_CONTRACT_ACT_EFFECTIVE_ON = date(1981, 4, 17)
CUSTOMER_SERVICE_REFORM_EFFECTIVE_ON = date(2025, 12, 28)
CURRENT_RULESET_SAFE_THROUGH = date(2027, 12, 31)

ScopeCode = Literal[
    "spain",
    "eu_eea_cross_border",
    "third_country",
    "unknown",
]
CoverageNature = Literal["damage", "persons", "mixed", "unknown"]

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
    "danos",
    "seguro de danos",
    "cancelacion",
    "interrupcion",
    "equipaje",
    "perdida material",
    "demora",
    "retraso",
    "gastos no reembolsables",
    "responsabilidad",
    "franquicia",
)
_PERSON_MARKERS = (
    "seguro de personas",
    "asistencia medica",
    "gastos medicos",
    "salud",
    "enfermedad",
    "accidente",
    "fallecimiento",
    "invalidez",
    "incapacidad",
    "repatriacion",
    "asistencia sanitaria",
)

_COMMON_BASIS = (
    (
        "Ley 50/1980, de Contrato de Seguro, artículos 1, 3, 5 y 8, sobre "
        "alcance pactado de la prestación, documentación de la póliza, claridad "
        "de las condiciones y tratamiento reforzado de las cláusulas limitativas."
    ),
    (
        "Ley 50/1980, artículos 16 a 20, sobre comunicación del siniestro, "
        "deber de información y salvamento, investigación, pago mínimo y mora "
        "del asegurador, sin anticipar automáticamente pérdida de derechos, "
        "intereses o cuantías."
    ),
)
_DAMAGE_BASIS = (
    (
        "Ley 50/1980, artículos 25 a 27 y 32, sobre interés asegurado, "
        "indemnización del daño efectivo, límite de la suma asegurada y "
        "coordinación de seguros concurrentes."
    ),
    (
        "Ley 50/1980, artículo 43, sobre subrogación del asegurador tras el pago "
        "en seguros de daños, sin perjudicar los derechos que sigan correspondiendo "
        "al asegurado."
    ),
)
_PERSON_BASIS = (
    (
        "Ley 50/1980, artículos 80 y 82, sobre seguros de personas y separación "
        "entre la prestación personal y la subrogación limitada a gastos de "
        "asistencia sanitaria."
    ),
    (
        "Ley 50/1980, artículos 100, 103, 105 y 106, sobre accidente, gastos de "
        "asistencia expresamente cubiertos, asistencia urgente y seguros de "
        "enfermedad o asistencia sanitaria dentro de los límites de la póliza."
    ),
)
_COMPLAINT_BASIS = (
    (
        "Ley 44/2002, artículos 29 y 30, en la redacción resultante de la "
        "Ley 10/2025, sobre reclamación escrita previa al servicio de atención "
        "a la clientela y posterior acceso al servicio público de reclamaciones "
        "financieras cuando concurran sus requisitos."
    ),
)
_DISTRIBUTION_BASIS = (
    (
        "Real Decreto-ley 3/2020, libro segundo, título I, y Directiva (UE) "
        "2016/97, sobre distribución de seguros, información del producto y "
        "adecuación a las demandas y necesidades del cliente cuando el seguro "
        "fue distribuido o añadido por una agencia, plataforma u otro mediador."
    ),
)


class TravelInsuranceRegimeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["current", "operator_review"]
    policy_date: Optional[date] = None
    coverage_start: Optional[date] = None
    coverage_end: Optional[date] = None
    loss_date: Optional[date] = None
    sac_complaint_date: Optional[date] = None
    scope: ScopeCode = "unknown"
    coverage_nature: CoverageNature = "unknown"
    limitation_years: Optional[int] = None
    customer_service_wait_months: Optional[int] = None
    financial_complaint_resolution_days: Optional[int] = None
    distribution_layer: bool = False
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


def _scope(value: Any) -> ScopeCode:
    folded = _fold(value)
    if not folded:
        return "unknown"
    if any(token in folded for token in _SPAIN_TOKENS):
        return "spain"
    if any(token in folded for token in _EU_EEA_TOKENS):
        return "eu_eea_cross_border"
    return "third_country"


def _coverage_nature(explicit: Any, coverages: Any) -> CoverageNature:
    text = _fold((explicit, coverages))
    if not text:
        return "unknown"
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
    return "unknown"


def _optional_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    folded = _fold(value)
    if folded in {"si", "true", "1", "incluido", "anadido"}:
        return True
    if folded in {"no", "false", "0", "no incluido", "no anadido"}:
        return False
    return None


def resolve_travel_insurance_regime(
    *,
    policy_date: Any,
    coverage_start: Any,
    coverage_end: Any,
    loss_date: Any,
    insurer_country: Any,
    coverage_nature: Any,
    policy_coverages: Any,
    sac_complaint_date: Any = None,
    insurance_added_to_booking: Any = None,
    insurance_distributor: Any = None,
) -> TravelInsuranceRegimeDecision:
    policy = _parse_date(policy_date)
    start = _parse_date(coverage_start)
    end = _parse_date(coverage_end)
    loss = _parse_date(loss_date)
    sac_date = _parse_date(sac_complaint_date)
    scope = _scope(insurer_country)
    nature = _coverage_nature(coverage_nature, policy_coverages)
    added = _optional_bool(insurance_added_to_booking)
    distribution_layer = added is True or bool(str(insurance_distributor or "").strip())

    common = {
        "policy_date": policy,
        "coverage_start": start,
        "coverage_end": end,
        "loss_date": loss,
        "sac_complaint_date": sac_date,
        "scope": scope,
        "coverage_nature": nature,
        "distribution_layer": distribution_layer,
    }

    if policy is None:
        return TravelInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Falta la fecha documental de contratación o emisión de la póliza. "
                "No puede seleccionarse la versión normativa aplicable."
            ),
        )

    if policy < INSURANCE_CONTRACT_ACT_EFFECTIVE_ON:
        return TravelInsuranceRegimeDecision(
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
        return TravelInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La póliza, cobertura, siniestro o reclamación supera el horizonte "
                "jurídico verificado. Deben versionarse las reformas posteriores."
            ),
        )

    if start is None or end is None:
        return TravelInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Faltan las fechas documentales de inicio y fin de la cobertura."
            ),
        )

    if end < start:
        return TravelInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La fecha final de cobertura aparece anterior a la fecha inicial."
            ),
        )

    if loss is None:
        return TravelInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Falta la fecha documental del siniestro o evento asegurado."
            ),
        )

    if policy > loss:
        return TravelInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La póliza figura contratada después del siniestro. Debe revisarse "
                "la existencia del riesgo al contratar y cualquier error documental."
            ),
        )

    if loss < start or loss > end:
        return TravelInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El siniestro queda fuera del período documental de cobertura. "
                "Debe revisarse la hora de efecto, prórrogas y certificados."
            ),
        )

    if scope != "spain":
        reason = (
            "El asegurador aparece establecido en otro Estado UE/EEE o Suiza. "
            "Deben verificarse la ley aplicable, la póliza local, la distribución "
            "transfronteriza y la competencia antes de citar el Derecho español."
            if scope == "eu_eea_cross_border"
            else (
                "No consta una aseguradora establecida en España o la jurisdicción "
                "es de un tercer país. Deben determinarse ley, foro y régimen local."
            )
        )
        return TravelInsuranceRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=reason,
        )

    basis = list(_COMMON_BASIS)
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

    basis.extend(_COMPLAINT_BASIS)
    if distribution_layer:
        basis.extend(_DISTRIBUTION_BASIS)

    reference_date = sac_date or loss
    modern_claim_path = reference_date >= CUSTOMER_SERVICE_REFORM_EFFECTIVE_ON

    warnings = [
        (
            "La póliza y sus condiciones particulares delimitan el riesgo dentro "
            "de las normas imperativas; una etiqueta comercial no acredita cobertura."
        ),
        (
            "Una comunicación tardía del siniestro no equivale por sí sola a la "
            "pérdida automática del derecho; deben revisarse plazo pactado, perjuicio, "
            "dolo o culpa grave y deberes de información."
        ),
        (
            "Los plazos de cuarenta días y tres meses no permiten fijar "
            "automáticamente una indemnización ni intereses sin revisar causa, "
            "importe mínimo debido y justificación del asegurador."
        ),
        (
            "Los reembolsos de transportista, hotel, organizador, tarjeta, otra "
            "aseguradora o tercero deben coordinarse para evitar doble recuperación."
        ),
    ]

    if nature in {"mixed", "unknown"}:
        warnings.append(
            "La póliza no permite seleccionar todavía un único plazo de prescripción: debe separarse cada cobertura entre daños y personas."
        )

    if sac_date is not None and sac_date < CUSTOMER_SERVICE_REFORM_EFFECTIVE_ON:
        warnings.append(
            "La reclamación al servicio de atención es anterior al régimen de la Ley 10/2025 y requiere revisión transitoria o histórica."
        )

    return TravelInsuranceRegimeDecision(
        status="current",
        **common,
        limitation_years=limitation_years,
        customer_service_wait_months=1 if modern_claim_path else None,
        financial_complaint_resolution_days=90 if modern_claim_path else None,
        ruleset=CURRENT_RULESET_CODE,
        legal_basis=tuple(dict.fromkeys(basis)),
        warnings=tuple(dict.fromkeys(warnings)),
    )
