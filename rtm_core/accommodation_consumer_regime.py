"""Selector conservador del régimen de consumo para alojamiento turístico.

No decide si una penalización es abusiva, no atribuye automáticamente la
responsabilidad al hotel o a una plataforma y no calcula indemnizaciones. Solo
versiona el marco de referencia aplicable a reservas de alojamiento no
residencial con fechas concretas y bloquea los supuestos históricos, futuros o
territoriales que requieren revisión jurídica.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


ACCOMMODATION_CONSUMER_REGIME_VERSION = (
    "rtm_accommodation_consumer_regime_v1_0"
)
CURRENT_RULESET_CODE = "eu_spain_fixed_date_accommodation_v1"
CURRENT_RULESET_EFFECTIVE_ON = date(2014, 6, 13)
CURRENT_RULESET_SAFE_THROUGH = date(2027, 7, 31)

ScopeCode = Literal[
    "spain",
    "eu_eea_cross_border",
    "third_country",
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

_SPAIN_LEGAL_BASIS = (
    (
        "Real Decreto Legislativo 1/2007, artículos 60, 61, 65, 80 a 83, "
        "97 y 103.l), sobre información contractual, integración de la oferta, "
        "cláusulas no negociadas y la excepción de desistimiento para alojamiento "
        "no residencial con fecha o periodo específicos."
    ),
    (
        "Código Civil, artículos 1101 y 1124, sobre incumplimiento, cumplimiento "
        "o resolución y daños acreditados, únicamente cuando el Derecho español "
        "resulte aplicable al contrato."
    ),
)
_EU_LEGAL_BASIS = (
    (
        "Directiva 2011/83/UE, artículos 6 y 16.l), sobre información previa y "
        "la excepción al desistimiento en alojamiento no residencial reservado "
        "para una fecha o periodo específicos."
    ),
    (
        "Directiva 93/13/CEE, sobre control de cláusulas no negociadas y "
        "desequilibrios en perjuicio del consumidor."
    ),
)


class AccommodationConsumerRegimeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["current", "operator_review"]
    booking_date: Optional[date] = None
    stay_start: Optional[date] = None
    stay_end: Optional[date] = None
    scope: ScopeCode = "unknown"
    fixed_date_withdrawal_exception: Optional[bool] = None
    ruleset: Optional[str] = None
    legal_basis: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    blocking_reason: Optional[str] = None


def _fold(value: Any) -> str:
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


def resolve_accommodation_consumer_regime(
    *,
    booking_date: Any,
    stay_start: Any,
    stay_end: Any,
    accommodation_country: Any,
) -> AccommodationConsumerRegimeDecision:
    booking = _parse_date(booking_date)
    start = _parse_date(stay_start)
    end = _parse_date(stay_end)
    scope = _scope(accommodation_country)

    if booking is None:
        return AccommodationConsumerRegimeDecision(
            status="operator_review",
            stay_start=start,
            stay_end=end,
            scope=scope,
            blocking_reason=(
                "Falta una fecha documental de reserva o contratación. No puede "
                "seleccionarse de forma segura la versión normativa aplicable."
            ),
            warnings=(
                "RTM no toma la fecha de reclamación como fecha del contrato.",
            ),
        )

    if booking < CURRENT_RULESET_EFFECTIVE_ON:
        return AccommodationConsumerRegimeDecision(
            status="operator_review",
            booking_date=booking,
            stay_start=start,
            stay_end=end,
            scope=scope,
            blocking_reason=(
                "La reserva es anterior al horizonte histórico versionado por RTM. "
                "Debe verificarse la normativa vigente al contratar."
            ),
        )

    if booking > CURRENT_RULESET_SAFE_THROUGH:
        return AccommodationConsumerRegimeDecision(
            status="operator_review",
            booking_date=booking,
            stay_start=start,
            stay_end=end,
            scope=scope,
            blocking_reason=(
                "La reserva supera el horizonte jurídico verificado. Debe "
                "versionarse cualquier reforma posterior antes de citar normas."
            ),
        )

    if start is None or end is None:
        return AccommodationConsumerRegimeDecision(
            status="operator_review",
            booking_date=booking,
            stay_start=start,
            stay_end=end,
            scope=scope,
            fixed_date_withdrawal_exception=None,
            blocking_reason=(
                "Faltan las fechas completas de entrada y salida. No debe afirmarse "
                "la excepción al desistimiento de alojamiento con periodo específico."
            ),
        )

    if end <= start:
        return AccommodationConsumerRegimeDecision(
            status="operator_review",
            booking_date=booking,
            stay_start=start,
            stay_end=end,
            scope=scope,
            blocking_reason=(
                "La fecha de salida no es posterior a la fecha de entrada."
            ),
        )

    if booking > end:
        return AccommodationConsumerRegimeDecision(
            status="operator_review",
            booking_date=booking,
            stay_start=start,
            stay_end=end,
            scope=scope,
            blocking_reason=(
                "La fecha de reserva aparece posterior a la finalización de la "
                "estancia y debe resolverse el conflicto documental."
            ),
        )

    common_warnings = (
        (
            "La ausencia de desistimiento legal de catorce días no elimina un "
            "derecho contractual de cancelación ni los remedios por incumplimiento "
            "del proveedor."
        ),
        (
            "Las condiciones de cancelación deben estar incorporadas y comunicadas "
            "de forma clara antes de contratar; RTM no valida una penalización solo "
            "porque figure en una respuesta posterior."
        ),
        (
            "No existe una compensación plana general para toda incidencia hotelera; "
            "deben acreditarse el incumplimiento, el remedio y los daños reclamados."
        ),
    )

    if scope == "spain":
        return AccommodationConsumerRegimeDecision(
            status="current",
            booking_date=booking,
            stay_start=start,
            stay_end=end,
            scope=scope,
            fixed_date_withdrawal_exception=True,
            ruleset=CURRENT_RULESET_CODE,
            legal_basis=_SPAIN_LEGAL_BASIS,
            warnings=common_warnings,
        )

    if scope == "eu_eea_cross_border":
        return AccommodationConsumerRegimeDecision(
            status="current",
            booking_date=booking,
            stay_start=start,
            stay_end=end,
            scope=scope,
            fixed_date_withdrawal_exception=True,
            ruleset=CURRENT_RULESET_CODE,
            legal_basis=_EU_LEGAL_BASIS,
            warnings=(
                *common_warnings,
                (
                    "Debe revisarse la ley nacional del lugar del alojamiento, la "
                    "ley aplicable al contrato y la competencia territorial."
                ),
            ),
        )

    return AccommodationConsumerRegimeDecision(
        status="operator_review",
        booking_date=booking,
        stay_start=start,
        stay_end=end,
        scope=scope,
        fixed_date_withdrawal_exception=True,
        blocking_reason=(
            "El alojamiento no identifica España ni un Estado UE/EEE o Suiza. "
            "Debe verificarse la ley aplicable, la jurisdicción y la protección "
            "contractual local antes de construir fundamentos jurídicos."
        ),
        warnings=common_warnings,
    )
