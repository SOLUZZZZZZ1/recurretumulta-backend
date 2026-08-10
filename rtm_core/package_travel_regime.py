"""Selector conservador del régimen de viajes combinados RTM.

Versiona el marco español vigente para contratos de viaje combinado y mantiene
separada la Directiva (UE) 2026/1024, ya adoptada pero cuya aplicación nacional
está prevista desde el 29 de marzo de 2029. No decide por sí solo si una
combinación concreta constituye viaje combinado, no atribuye responsabilidad y
no calcula reembolsos ni indemnizaciones.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


PACKAGE_TRAVEL_REGIME_VERSION = "rtm_package_travel_regime_v1_0"
CURRENT_RULESET_CODE = "spain_package_travel_2018_v1"
CURRENT_RULESET_EFFECTIVE_ON = date(2018, 12, 28)
CURRENT_RULESET_SAFE_THROUGH = date(2028, 9, 28)
REVISED_DIRECTIVE_ENTRY_INTO_FORCE = date(2026, 5, 28)
REVISED_DIRECTIVE_TRANSPOSITION_DEADLINE = date(2028, 9, 29)
REVISED_DIRECTIVE_APPLICATION_ON = date(2029, 3, 29)

ScopeCode = Literal[
    "spain",
    "eu_eea_cross_border",
    "third_country",
    "unknown",
]
RevisionStatus = Literal[
    "adopted_not_yet_applicable",
    "transposition_window",
    "application_date_reached",
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

_SERVICE_MARKERS: dict[str, tuple[str, ...]] = {
    "transport": (
        "transporte",
        "vuelo",
        "avion",
        "tren",
        "ferrocarril",
        "autobus",
        "bus",
        "barco",
        "ferry",
        "crucero",
    ),
    "accommodation": (
        "alojamiento",
        "hotel",
        "apartamento",
        "hostal",
        "resort",
    ),
    "vehicle_rental": (
        "alquiler de coche",
        "alquiler de vehiculo",
        "rent a car",
        "coche de alquiler",
        "vehiculo de alquiler",
        "motocicleta de alquiler",
    ),
    "other_tourist_service": (
        "excursion",
        "visita guiada",
        "entrada",
        "actividad turistica",
        "servicio turistico",
        "forfait",
    ),
}

_SPAIN_LEGAL_BASIS = (
    (
        "Real Decreto Legislativo 1/2007, artículos 150 a 155, sobre ámbito, "
        "definición, información precontractual y contenido del contrato de viaje "
        "combinado."
    ),
    (
        "Real Decreto Legislativo 1/2007, artículos 159 y 160, sobre cambios "
        "sustanciales y terminación del contrato antes del inicio del viaje."
    ),
    (
        "Real Decreto Legislativo 1/2007, artículos 161 a 163, sobre ejecución, "
        "subsanación, alternativas, reducción del precio, indemnización y asistencia."
    ),
    (
        "Real Decreto Legislativo 1/2007, artículos 164 a 166, sobre garantías "
        "frente a la insolvencia y repatriación."
    ),
    (
        "Real Decreto Legislativo 1/2007, artículo 169, que establece un plazo de "
        "prescripción de dos años para las reclamaciones reguladas en el libro cuarto."
    ),
)
_EU_LEGAL_BASIS = (
    (
        "Directiva (UE) 2015/2302, en su régimen actualmente aplicable antes de la "
        "fecha de aplicación de la Directiva (UE) 2026/1024, sobre viajes combinados."
    ),
)


class PackageTravelRegimeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["current", "operator_review"]
    contract_date: Optional[date] = None
    package_start: Optional[date] = None
    package_end: Optional[date] = None
    scope: ScopeCode = "unknown"
    package_qualified: Optional[bool] = None
    service_type_count: int = 0
    ruleset: Optional[str] = None
    legal_basis: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    blocking_reason: Optional[str] = None
    limitation_years: Optional[int] = None
    revised_directive_status: RevisionStatus = "adopted_not_yet_applicable"


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


def _revision_status(contract_date: Optional[date]) -> RevisionStatus:
    if contract_date is None or contract_date < REVISED_DIRECTIVE_TRANSPOSITION_DEADLINE:
        return "adopted_not_yet_applicable"
    if contract_date < REVISED_DIRECTIVE_APPLICATION_ON:
        return "transposition_window"
    return "application_date_reached"


def _service_categories(value: Any) -> set[str]:
    folded = _fold(value)
    if not folded:
        return set()
    return {
        category
        for category, markers in _SERVICE_MARKERS.items()
        if any(marker in folded for marker in markers)
    }


def resolve_package_travel_regime(
    *,
    contract_date: Any,
    package_start: Any,
    package_end: Any,
    organizer_country: Any,
    package_status: Any,
    service_types: Any,
) -> PackageTravelRegimeDecision:
    contract = _parse_date(contract_date)
    start = _parse_date(package_start)
    end = _parse_date(package_end)
    scope = _scope(organizer_country)
    categories = _service_categories(service_types)
    service_type_count = len(categories)
    revision = _revision_status(contract)

    common = {
        "contract_date": contract,
        "package_start": start,
        "package_end": end,
        "scope": scope,
        "service_type_count": service_type_count,
        "revised_directive_status": revision,
    }

    if contract is None:
        return PackageTravelRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Falta la fecha documental del contrato o de la reserva. No puede "
                "seleccionarse la versión normativa aplicable."
            ),
        )

    if contract < CURRENT_RULESET_EFFECTIVE_ON:
        return PackageTravelRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El contrato es anterior al régimen español versionado desde el "
                "28 de diciembre de 2018 y requiere revisión histórica."
            ),
        )

    if contract > CURRENT_RULESET_SAFE_THROUGH:
        return PackageTravelRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "El contrato se encuentra en la ventana de transposición o después "
                "de la fecha de aplicación prevista para la Directiva (UE) 2026/1024. "
                "Debe verificarse y versionarse la normativa nacional vigente."
            ),
            warnings=(
                "La Directiva (UE) 2026/1024 no se aplica automáticamente como si ya estuviera transpuesta.",
            ),
        )

    if start is None or end is None:
        return PackageTravelRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Faltan las fechas completas de inicio y fin del viaje combinado."
            ),
        )

    if end <= start:
        return PackageTravelRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La fecha de finalización no es posterior a la fecha de inicio."
            ),
        )

    if contract > end:
        return PackageTravelRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La fecha del contrato aparece posterior a la finalización del viaje."
            ),
        )

    if package_status is not True:
        return PackageTravelRegimeDecision(
            status="operator_review",
            **common,
            package_qualified=False if package_status is False else None,
            blocking_reason=(
                "No consta una calificación documental afirmativa como viaje "
                "combinado. Debe distinguirse de servicios independientes o de un "
                "servicio de viaje vinculado."
            ),
        )

    if service_type_count < 2:
        return PackageTravelRegimeDecision(
            status="operator_review",
            **common,
            package_qualified=None,
            blocking_reason=(
                "No se han identificado al menos dos tipos distintos de servicios "
                "de viaje. La etiqueta comercial por sí sola no basta para fijar el "
                "régimen de viaje combinado."
            ),
        )

    warnings = (
        (
            "Debe probarse cómo se combinaron, ofrecieron, seleccionaron y cobraron "
            "los servicios; RTM no deduce la condición de organizador solo por el "
            "nombre comercial de una agencia o plataforma."
        ),
        (
            "Los derechos sectoriales de pasajeros y equipaje pueden coexistir con "
            "el régimen de viaje combinado y no deben duplicar indemnizaciones."
        ),
        (
            "La Directiva (UE) 2026/1024 entró en vigor el 28 de mayo de 2026, pero "
            "sus medidas nacionales deben aplicarse desde el 29 de marzo de 2029; "
            "no se incorporan anticipadamente como fundamento vigente."
        ),
    )

    if scope == "spain":
        return PackageTravelRegimeDecision(
            status="current",
            **common,
            package_qualified=True,
            ruleset=CURRENT_RULESET_CODE,
            legal_basis=_SPAIN_LEGAL_BASIS,
            warnings=warnings,
            limitation_years=2,
        )

    if scope == "eu_eea_cross_border":
        return PackageTravelRegimeDecision(
            status="current",
            **common,
            package_qualified=True,
            ruleset=CURRENT_RULESET_CODE,
            legal_basis=_EU_LEGAL_BASIS,
            warnings=(
                *warnings,
                (
                    "Debe comprobarse la transposición nacional aplicable, el país "
                    "de establecimiento del organizador, la ley del contrato y la "
                    "competencia territorial antes de citar artículos nacionales."
                ),
            ),
        )

    return PackageTravelRegimeDecision(
        status="operator_review",
        **common,
        package_qualified=True,
        blocking_reason=(
            "El organizador o el punto de contratación no identifica España ni un "
            "Estado UE/EEE o Suiza. Debe determinarse la ley aplicable y la protección "
            "local antes de construir fundamentos jurídicos."
        ),
        warnings=warnings,
    )
