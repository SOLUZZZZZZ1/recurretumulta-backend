"""Selector temporal conservador de responsabilidad por equipaje aéreo.

No valora daños ni convierte DEG/SDR a euros. Mantiene versionados los límites
revisados del Convenio de Montreal y bloquea periodos no verificados, incluida
la futura entrada en vigor de la reforma europea aprobada en 2026.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


AIR_BAGGAGE_LIABILITY_REGIME_VERSION = "rtm_air_baggage_liability_regime_v1_0"
CURRENT_RULESET_CODE = "montreal_1999_eu_2027_97"
CURRENT_LIMIT_EFFECTIVE_ON = date(2024, 12, 28)
PREVIOUS_LIMIT_EFFECTIVE_ON = date(2019, 12, 28)
CURRENT_RULESET_SAFE_THROUGH = date(2027, 7, 31)
CURRENT_BAGGAGE_LIMIT_SDR = 1519
PREVIOUS_BAGGAGE_LIMIT_SDR = 1288

_LEGAL_BASIS = (
    (
        "Convenio de Montreal de 1999, artículos 17, 19, 22.2, 29, 31 y 35, "
        "según el tipo de equipaje, daño y actuación ejercitada."
    ),
    (
        "Reglamento (CE) n.º 2027/97, modificado por el Reglamento (CE) "
        "n.º 889/2002, sobre responsabilidad de las compañías aéreas respecto "
        "de los pasajeros y su equipaje."
    ),
)


class AirBaggageLiabilityDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["current", "operator_review"]
    incident_date: Optional[date] = None
    ruleset: Optional[str] = None
    liability_limit_sdr: Optional[int] = None
    limit_effective_on: Optional[date] = None
    legal_basis: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    blocking_reason: Optional[str] = None


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


def resolve_air_baggage_liability_regime(
    value: Any,
) -> AirBaggageLiabilityDecision:
    incident_date = _parse_date(value)
    if incident_date is None:
        return AirBaggageLiabilityDecision(
            status="operator_review",
            blocking_reason=(
                "No puede fijarse el régimen ni el límite de responsabilidad sin "
                "una fecha de vuelo documental y validada."
            ),
            warnings=(
                "RTM no aplica por defecto un límite histórico o futuro.",
            ),
        )

    if incident_date > CURRENT_RULESET_SAFE_THROUGH:
        return AirBaggageLiabilityDecision(
            status="operator_review",
            incident_date=incident_date,
            blocking_reason=(
                "La fecha supera el horizonte europeo verificado. Debe incorporarse "
                "la entrada en vigor y el contrato jurídico de la reforma aprobada "
                "en 2026 antes de seleccionar reglas de reclamación."
            ),
            warnings=(
                "El límite en DEG tampoco se convierte automáticamente a euros.",
            ),
        )

    if incident_date >= CURRENT_LIMIT_EFFECTIVE_ON:
        return AirBaggageLiabilityDecision(
            status="current",
            incident_date=incident_date,
            ruleset=CURRENT_RULESET_CODE,
            liability_limit_sdr=CURRENT_BAGGAGE_LIMIT_SDR,
            limit_effective_on=CURRENT_LIMIT_EFFECTIVE_ON,
            legal_basis=_LEGAL_BASIS,
            warnings=(
                (
                    "El límite de 1.519 DEG es un máximo de responsabilidad por "
                    "pasajero, no una indemnización automática."
                ),
                (
                    "La cuantía debe probarse y la conversión a moneda nacional "
                    "depende del momento y la regla aplicables."
                ),
                (
                    "Una declaración especial de interés realizada al facturar "
                    "puede modificar el límite aplicable."
                ),
            ),
        )

    if incident_date >= PREVIOUS_LIMIT_EFFECTIVE_ON:
        return AirBaggageLiabilityDecision(
            status="current",
            incident_date=incident_date,
            ruleset=CURRENT_RULESET_CODE,
            liability_limit_sdr=PREVIOUS_BAGGAGE_LIMIT_SDR,
            limit_effective_on=PREVIOUS_LIMIT_EFFECTIVE_ON,
            legal_basis=_LEGAL_BASIS,
            warnings=(
                (
                    "El límite histórico de 1.288 DEG es un máximo de "
                    "responsabilidad, no una indemnización automática."
                ),
                (
                    "La cuantía y la conversión monetaria deben acreditarse y "
                    "revisarse para la fecha correspondiente."
                ),
            ),
        )

    return AirBaggageLiabilityDecision(
        status="operator_review",
        incident_date=incident_date,
        blocking_reason=(
            "La fecha es anterior al horizonte histórico versionado por RTM. "
            "Debe verificarse el límite de DEG vigente en ese momento."
        ),
        warnings=(
            "No se reutiliza un límite posterior para un vuelo anterior.",
        ),
    )
