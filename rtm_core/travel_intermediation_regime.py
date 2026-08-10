"""Selector conservador del régimen de agencias y plataformas de viaje RTM.

Versiona únicamente el marco general de información, contratación y prestación
propia de una agencia o plataforma. No decide si la empresa es organizadora,
minorista, prestadora, intermediaria o mera receptora del pago; tampoco convierte
las obligaciones del Reglamento de Servicios Digitales en una garantía general
del servicio de viaje contratado a un tercero.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


TRAVEL_INTERMEDIATION_REGIME_VERSION = (
    "rtm_travel_intermediation_regime_v1_0"
)
CURRENT_RULESET_CODE = "spain_eu_travel_intermediation_v1"
CURRENT_RULESET_EFFECTIVE_ON = date(2014, 6, 13)
DSA_FULL_APPLICATION_ON = date(2024, 2, 17)
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

_SPAIN_GENERAL_BASIS = (
    (
        "Real Decreto Legislativo 1/2007, artículos 60, 61, 63 y 65, sobre "
        "información previa, integración de la oferta, confirmación documental y "
        "buena fe en la contratación con consumidores."
    ),
    (
        "Código Civil, artículos 1101 y 1124, sobre incumplimiento, cumplimiento "
        "o resolución y daños acreditados, únicamente cuando el Derecho español "
        "resulte aplicable y exista una obligación propia de la empresa reclamada."
    ),
)
_SPAIN_ELECTRONIC_BASIS = (
    (
        "Real Decreto Legislativo 1/2007, artículos 97 y 98, sobre información y "
        "requisitos formales de los contratos a distancia."
    ),
    (
        "Ley 34/2002, artículos 10, 27 y 28, sobre identificación del prestador, "
        "condiciones de la contratación electrónica y confirmación de la aceptación."
    ),
)
_EU_GENERAL_BASIS = (
    (
        "Directiva 2011/83/UE, especialmente sus artículos 6 y 8, sobre "
        "información precontractual y requisitos de los contratos a distancia."
    ),
)
_EU_ELECTRONIC_BASIS = (
    (
        "Directiva 2000/31/CE, especialmente sus artículos 5, 10 y 11, sobre "
        "identificación, información previa y confirmación en la contratación "
        "electrónica."
    ),
)
_DSA_MARKETPLACE_BASIS = (
    (
        "Reglamento (UE) 2022/2065, artículos 30 a 32, sobre trazabilidad de los "
        "comerciantes, cumplimiento desde el diseño y derecho a la información en "
        "plataformas que permiten celebrar contratos a distancia con comerciantes."
    ),
)


class TravelIntermediationRegimeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["current", "operator_review"]
    booking_date: Optional[date] = None
    scope: ScopeCode = "unknown"
    electronic_contract: Optional[bool] = None
    marketplace_status: Optional[bool] = None
    dsa_marketplace_layer: Optional[bool] = None
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


def _optional_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    folded = _fold(value)
    if folded in {"si", "true", "1", "online", "electronica", "electronico"}:
        return True
    if folded in {
        "no",
        "false",
        "0",
        "presencial",
        "telefonica",
        "telefonico",
    }:
        return False
    return None


def resolve_travel_intermediation_regime(
    *,
    booking_date: Any,
    platform_country: Any,
    electronic_contract: Any,
    marketplace_status: Any,
) -> TravelIntermediationRegimeDecision:
    booking = _parse_date(booking_date)
    scope = _scope(platform_country)
    electronic = _optional_bool(electronic_contract)
    marketplace = _optional_bool(marketplace_status)

    common = {
        "booking_date": booking,
        "scope": scope,
        "electronic_contract": electronic,
        "marketplace_status": marketplace,
    }

    if booking is None:
        return TravelIntermediationRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Falta la fecha documental de la reserva o contratación. No puede "
                "seleccionarse la versión normativa aplicable."
            ),
        )

    if booking < CURRENT_RULESET_EFFECTIVE_ON:
        return TravelIntermediationRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La contratación es anterior al horizonte histórico versionado para "
                "información y contratación a distancia."
            ),
        )

    if booking > CURRENT_RULESET_SAFE_THROUGH:
        return TravelIntermediationRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La contratación supera el horizonte jurídico verificado. Debe "
                "versionarse cualquier reforma posterior antes de citar normas."
            ),
        )

    if scope in {"third_country", "unknown"}:
        return TravelIntermediationRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "No consta una plataforma establecida en España o en un Estado "
                "UE/EEE o Suiza. Debe determinarse la ley aplicable, la jurisdicción "
                "y el régimen local de intermediación antes de construir fundamentos."
            ),
        )

    if scope == "spain":
        basis = list(_SPAIN_GENERAL_BASIS)
        if electronic is True:
            basis.extend(_SPAIN_ELECTRONIC_BASIS)
    else:
        basis = list(_EU_GENERAL_BASIS)
        if electronic is True:
            basis.extend(_EU_ELECTRONIC_BASIS)

    warnings = [
        (
            "La etiqueta 'plataforma' o el cobro del precio no demuestra por sí "
            "solos que la empresa sea organizadora, vendedora o prestadora del "
            "servicio de viaje."
        ),
        (
            "Las exenciones de responsabilidad de intermediarios técnicos no "
            "eliminan la responsabilidad por obligaciones contractuales propias, "
            "por información suministrada por la propia empresa o por fondos que "
            "haya recibido y deba rendir o devolver."
        ),
        (
            "Los derechos sectoriales frente al transportista, alojamiento u otro "
            "prestador pueden coexistir, pero no deben duplicarse reembolsos o daños."
        ),
    ]

    dsa_layer: Optional[bool] = None
    if booking >= DSA_FULL_APPLICATION_ON:
        if marketplace is True and electronic is True:
            basis.extend(_DSA_MARKETPLACE_BASIS)
            dsa_layer = True
            warnings.append(
                "Debe comprobarse el ámbito subjetivo y las excepciones aplicables antes de atribuir una infracción concreta del Reglamento de Servicios Digitales."
            )
        elif marketplace is False:
            dsa_layer = False
        else:
            warnings.append(
                "No está documentado si la plataforma permite celebrar contratos a distancia con comerciantes; no se incorpora automáticamente la capa de marketplace del Reglamento de Servicios Digitales."
            )

    if electronic is None:
        warnings.append(
            "El canal de contratación no está cerrado; por prudencia no se citan obligaciones específicas de contratación electrónica."
        )

    if scope == "eu_eea_cross_border":
        warnings.append(
            "Debe comprobarse la transposición nacional, el país de establecimiento, la ley del contrato y la competencia territorial."
        )

    return TravelIntermediationRegimeDecision(
        status="current",
        **common,
        dsa_marketplace_layer=dsa_layer,
        ruleset=CURRENT_RULESET_CODE,
        legal_basis=tuple(dict.fromkeys(basis)),
        warnings=tuple(dict.fromkeys(warnings)),
    )
