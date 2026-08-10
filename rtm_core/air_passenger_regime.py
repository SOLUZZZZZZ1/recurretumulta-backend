"""Selector temporal conservador del régimen europeo de pasajeros aéreos.

Este módulo no calcula compensaciones ni interpreta documentos. Su función es
impedir que un especialista aplique antes de tiempo la reforma europea adoptada
en julio de 2026.

Estado normativo verificado a 10 de agosto de 2026:

* el Reglamento (CE) n.º 261/2004 continúa siendo la base operativa;
* la reforma fue aprobada definitivamente el 13 de julio de 2026;
* las reglas reformadas solo entrarán en vigor después del periodo previsto
  desde su publicación en el Diario Oficial;
* RTM no incorpora una fecha futura hasta verificarla y versionarla.

La fecha ``CURRENT_RULESET_SAFE_THROUGH`` es deliberadamente conservadora. Una
reforma adoptada el 13 de julio de 2026 y sometida a un periodo de doce meses y
veinte días desde su publicación no puede entrar en vigor antes de agosto de
2027. Para fechas posteriores, el sistema exige revisión en lugar de adivinar.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


AIR_PASSENGER_REGIME_VERSION = "rtm_air_passenger_regime_v1_0"
CURRENT_RULESET_CODE = "eu_regulation_261_2004"
CURRENT_RULESET_SAFE_THROUGH = date(2027, 7, 31)
REFORM_ADOPTED_ON = date(2026, 7, 13)
REFORM_PUBLICATION_DATE: Optional[date] = None
REFORM_ENTRY_INTO_FORCE_DATE: Optional[date] = None

_CURRENT_LEGAL_BASIS = (
    (
        "Reglamento (CE) n.º 261/2004, artículos 3, 5, 7, 8, 9 y 14, "
        "según el ámbito territorial y material acreditado."
    ),
    (
        "Comunicación de la Comisión C/2024/5687, directrices interpretativas "
        "sobre los Reglamentos (CE) n.º 261/2004 y n.º 2027/97."
    ),
)


class AirPassengerRegimeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["current", "operator_review"]
    incident_date: Optional[date] = None
    ruleset: Optional[str] = None
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


def resolve_air_passenger_regime(value: Any) -> AirPassengerRegimeDecision:
    incident_date = _parse_date(value)
    if incident_date is None:
        return AirPassengerRegimeDecision(
            status="operator_review",
            blocking_reason=(
                "No puede fijarse el régimen europeo sin una fecha de vuelo "
                "documental y validada."
            ),
            warnings=(
                "RTM no aplica por defecto ni el régimen vigente ni la reforma futura.",
            ),
        )

    if (
        REFORM_ENTRY_INTO_FORCE_DATE is not None
        and incident_date >= REFORM_ENTRY_INTO_FORCE_DATE
    ):
        return AirPassengerRegimeDecision(
            status="operator_review",
            incident_date=incident_date,
            blocking_reason=(
                "La fecha entra en el periodo de vigencia de la reforma, pero "
                "este especialista todavía no incorpora su contrato jurídico."
            ),
            warnings=(
                "Debe actualizarse y versionarse el especialista antes de continuar.",
            ),
        )

    if incident_date <= CURRENT_RULESET_SAFE_THROUGH:
        return AirPassengerRegimeDecision(
            status="current",
            incident_date=incident_date,
            ruleset=CURRENT_RULESET_CODE,
            legal_basis=_CURRENT_LEGAL_BASIS,
            warnings=(
                (
                    "La reforma europea fue adoptada el 13 de julio de 2026, "
                    "pero este incidente permanece dentro del horizonte temporal "
                    "seguro del Reglamento (CE) n.º 261/2004."
                ),
            ),
        )

    return AirPassengerRegimeDecision(
        status="operator_review",
        incident_date=incident_date,
        blocking_reason=(
            "La fecha supera el horizonte temporal verificado del régimen actual. "
            "Debe incorporarse la fecha oficial de publicación y entrada en vigor "
            "de la reforma antes de seleccionar reglas o cuantías."
        ),
        warnings=(
            (
                "No se aplican automáticamente las reglas reformadas aprobadas "
                "en julio de 2026."
            ),
        ),
    )
