"""Selector conservador del régimen de agencia y plataforma de reservas RTM.

Separa la intermediación electrónica de la prestación del servicio de viaje,
del viaje combinado y de los servicios de viaje vinculados. No atribuye
responsabilidad a una plataforma por el mero cobro, no convierte una cláusula
de intermediación en inmunidad y no calcula automáticamente daños o reembolsos.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


TRAVEL_AGENCY_REGIME_VERSION = "rtm_travel_agency_regime_v1_0"
INDEPENDENT_INTERMEDIATION_RULESET = "spain_online_travel_intermediation_2022_v1"
LINKED_TRAVEL_ARRANGEMENT_RULESET = "spain_linked_travel_arrangement_2018_v1"
INDEPENDENT_INTERMEDIATION_EFFECTIVE_ON = date(2022, 5, 28)
LINKED_TRAVEL_ARRANGEMENT_EFFECTIVE_ON = date(2018, 12, 28)
DSA_GENERAL_APPLICATION_ON = date(2024, 2, 17)
CURRENT_RULESET_SAFE_THROUGH = date(2027, 12, 31)

ScopeCode = Literal[
    "spain",
    "eu_eea_cross_border",
    "third_country",
    "unknown",
]
RoleCode = Literal[
    "intermediary",
    "contracting_party",
    "supplier",
    "organizer_or_retailer",
    "linked_arrangement_facilitator",
    "mixed",
    "unknown",
]
BoundaryCode = Literal[
    "independent_intermediation",
    "package_travel",
    "linked_travel_arrangement",
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

_ROLE_PATTERNS: tuple[tuple[RoleCode, tuple[str, ...]], ...] = (
    (
        "organizer_or_retailer",
        (
            "organizador",
            "minorista",
            "tour operator",
            "package retailer",
        ),
    ),
    (
        "linked_arrangement_facilitator",
        (
            "facilitador de servicio de viaje vinculado",
            "linked travel arrangement facilitator",
        ),
    ),
    (
        "supplier",
        (
            "proveedor directo",
            "prestador directo",
            "prestador del servicio",
            "supplier",
            "service provider",
        ),
    ),
    (
        "contracting_party",
        (
            "parte contratante",
            "vendedor contractual",
            "seller of record",
            "contracting party",
            "merchant of record",
        ),
    ),
    (
        "intermediary",
        (
            "intermediario",
            "intermediaria",
            "agente",
            "marketplace",
            "mercado en linea",
            "plataforma de reservas",
        ),
    ),
)

_SPANISH_GENERAL_BASIS = (
    (
        "Real Decreto Legislativo 1/2007, artículos 60, 63, 95, 97 y 98, "
        "sobre información precontractual, confirmación documental y contratación "
        "a distancia mediante servicios de intermediación."
    ),
    (
        "Ley 34/2002, artículos 10, 27 y 28, sobre identificación del prestador, "
        "información previa y confirmación de la contratación electrónica."
    ),
)
_SPANISH_MARKETPLACE_BASIS = (
    (
        "Real Decreto Legislativo 1/2007, artículo 97 bis, sobre información "
        "específica de los mercados en línea, identidad del tercero, condición "
        "de empresario y distribución de obligaciones contractuales."
    ),
)
_DSA_BASIS = (
    (
        "Reglamento (UE) 2022/2065, artículos 30 y 31, sobre trazabilidad de "
        "comerciantes y diseño de mercados en línea que permita facilitar la "
        "información precontractual exigible."
    ),
)
_SPANISH_LINKED_BASIS = (
    (
        "Real Decreto Legislativo 1/2007, artículos 151.1.e), 152, 167 y 168, "
        "sobre servicios de viaje vinculados, errores de reserva, información "
        "precontractual y protección frente a insolvencia."
    ),
    (
        "Real Decreto Legislativo 1/2007, artículo 169, sobre el plazo de "
        "prescripción de las reclamaciones reconocidas en el libro cuarto."
    ),
)
_EU_GENERAL_BASIS = (
    (
        "Directiva 2011/83/UE, artículos 6, 6 bis y 8, sobre información "
        "precontractual en contratos a distancia y mercados en línea."
    ),
    (
        "Directiva 2000/31/CE, artículos 5, 10 y 11, sobre identificación, "
        "información contractual y confirmación electrónica."
    ),
)
_EU_LINKED_BASIS = (
    (
        "Directiva (UE) 2015/2302, artículos 3.5, 19 y 21, sobre servicios de "
        "viaje vinculados, información, insolvencia y errores en la reserva."
    ),
)


class TravelAgencyRegimeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["current", "operator_review"]
    booking_date: Optional[date] = None
    scope: ScopeCode = "unknown"
    boundary: BoundaryCode = "unknown"
    role: RoleCode = "unknown"
    role_confirmed: bool = False
    online_marketplace: Optional[bool] = None
    marketplace_information_regime_applies: Optional[bool] = None
    dsa_marketplace_duties_apply: Optional[bool] = None
    payment_collector_matches_platform: Optional[bool] = None
    invoice_issuer_matches_platform: Optional[bool] = None
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
    if folded in {"si", "true", "1", "consta"}:
        return True
    if folded in {"no", "false", "0", "no consta"}:
        return False
    return None


def _same_entity(left: Any, right: Any) -> Optional[bool]:
    left_folded = _fold(left)
    right_folded = _fold(right)
    if not left_folded or not right_folded:
        return None
    if left_folded == right_folded:
        return True
    if min(len(left_folded), len(right_folded)) >= 7:
        return left_folded in right_folded or right_folded in left_folded
    return False


def _explicit_role(value: Any) -> RoleCode:
    folded = _fold(value)
    if not folded:
        return "unknown"
    matches = [
        role
        for role, markers in _ROLE_PATTERNS
        if any(marker in folded for marker in markers)
    ]
    if len(set(matches)) > 1:
        return "mixed"
    return matches[0] if matches else "unknown"


def _resolve_role(
    *,
    explicit_role: Any,
    platform_name: Any,
    contracting_party: Any,
    underlying_supplier: Any,
    linked_arrangement: Optional[bool],
) -> RoleCode:
    explicit = _explicit_role(explicit_role)
    contracting_match = _same_entity(platform_name, contracting_party)
    supplier_match = _same_entity(platform_name, underlying_supplier)

    if linked_arrangement is True:
        if explicit in {"unknown", "intermediary", "linked_arrangement_facilitator"}:
            return "linked_arrangement_facilitator"
        return "mixed"

    if explicit == "organizer_or_retailer":
        return explicit

    if explicit == "intermediary" and (
        contracting_match is True or supplier_match is True
    ):
        return "mixed"

    if explicit in {"contracting_party", "supplier"}:
        if explicit == "contracting_party" and supplier_match is True:
            return "mixed"
        return explicit

    if explicit == "mixed":
        return explicit

    if supplier_match is True:
        return "supplier"
    if contracting_match is True:
        return "contracting_party"
    if explicit == "intermediary":
        return "intermediary"
    return "unknown"


def resolve_travel_agency_regime(
    *,
    booking_date: Any,
    platform_country: Any,
    platform_name: Any,
    role_value: Any,
    online_marketplace: Any,
    package_status: Any,
    linked_arrangement: Any,
    contracting_party: Any,
    underlying_supplier: Any,
    payment_collector: Any,
    invoice_issuer: Any,
) -> TravelAgencyRegimeDecision:
    booking = _parse_date(booking_date)
    scope = _scope(platform_country)
    marketplace = _optional_bool(online_marketplace)
    package = _optional_bool(package_status)
    linked = _optional_bool(linked_arrangement)
    collector_match = _same_entity(platform_name, payment_collector)
    invoice_match = _same_entity(platform_name, invoice_issuer)

    if package is True and linked is True:
        return TravelAgencyRegimeDecision(
            status="operator_review",
            booking_date=booking,
            scope=scope,
            boundary="unknown",
            role="mixed",
            online_marketplace=marketplace,
            payment_collector_matches_platform=collector_match,
            invoice_issuer_matches_platform=invoice_match,
            blocking_reason=(
                "Los hechos afirman simultáneamente viaje combinado y servicio de "
                "viaje vinculado. Debe resolverse el conflicto documental antes de "
                "seleccionar un especialista."
            ),
        )

    boundary: BoundaryCode
    if package is True:
        boundary = "package_travel"
    elif linked is True:
        boundary = "linked_travel_arrangement"
    elif package is False and linked is False:
        boundary = "independent_intermediation"
    else:
        boundary = "unknown"

    role = _resolve_role(
        explicit_role=role_value,
        platform_name=platform_name,
        contracting_party=contracting_party,
        underlying_supplier=underlying_supplier,
        linked_arrangement=linked,
    )

    common = {
        "booking_date": booking,
        "scope": scope,
        "boundary": boundary,
        "role": role,
        "role_confirmed": role not in {"unknown", "mixed"},
        "online_marketplace": marketplace,
        "payment_collector_matches_platform": collector_match,
        "invoice_issuer_matches_platform": invoice_match,
    }

    if booking is None:
        return TravelAgencyRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Falta la fecha documental de la reserva o contratación. No puede "
                "seleccionarse de forma segura la versión normativa aplicable."
            ),
        )

    if booking > CURRENT_RULESET_SAFE_THROUGH:
        return TravelAgencyRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La contratación supera el horizonte jurídico verificado para "
                "agencias y plataformas. Debe versionarse cualquier reforma posterior."
            ),
        )

    if boundary == "package_travel":
        return TravelAgencyRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "Los hechos identifican un viaje combinado. La incidencia debe "
                "revisarse mediante travel.package antes de atribuir responsabilidad "
                "a la agencia o plataforma."
            ),
        )

    if boundary == "unknown":
        return TravelAgencyRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "No consta si la contratación fue un servicio independiente, un "
                "viaje combinado o un servicio de viaje vinculado."
            ),
        )

    if boundary == "linked_travel_arrangement":
        if booking < LINKED_TRAVEL_ARRANGEMENT_EFFECTIVE_ON:
            return TravelAgencyRegimeDecision(
                status="operator_review",
                **common,
                blocking_reason=(
                    "El servicio de viaje vinculado es anterior al régimen versionado "
                    "desde el 28 de diciembre de 2018."
                ),
            )
    elif booking < INDEPENDENT_INTERMEDIATION_EFFECTIVE_ON:
        return TravelAgencyRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "La intermediación es anterior al marco de mercados en línea "
                "versionado desde el 28 de mayo de 2022 y requiere revisión histórica."
            ),
        )

    if scope in {"unknown", "third_country"}:
        return TravelAgencyRegimeDecision(
            status="operator_review",
            **common,
            blocking_reason=(
                "No se ha identificado una plataforma establecida en España o en "
                "la UE/EEE o Suiza. Deben determinarse la ley aplicable, la "
                "jurisdicción y el régimen local de intermediación."
            ),
        )

    warnings = [
        (
            "El cobro, la emisión de un recibo o una comisión no convierten por sí "
            "solos a la plataforma en prestadora del servicio de viaje."
        ),
        (
            "Una cláusula que califica a la empresa como intermediaria no elimina "
            "sus deberes propios de información, confirmación, reserva, cobro o "
            "reembolso cuando haya asumido esas funciones."
        ),
        (
            "La reclamación debe separar los actos propios de la plataforma de la "
            "falta de prestación imputable al proveedor subyacente y evitar duplicar "
            "devoluciones o indemnizaciones."
        ),
    ]
    if role == "unknown":
        warnings.append(
            "El régimen jurídico puede identificarse, pero el papel contractual de la plataforma sigue sin cerrar."
        )
    elif role == "mixed":
        return TravelAgencyRegimeDecision(
            status="operator_review",
            **common,
            warnings=tuple(warnings),
            blocking_reason=(
                "Los documentos atribuyen a la plataforma papeles incompatibles o "
                "insuficientemente diferenciados. Debe resolverse quién contrató, "
                "quién prestó, quién cobró y quién emitió la factura."
            ),
        )
    elif role == "organizer_or_retailer":
        return TravelAgencyRegimeDecision(
            status="operator_review",
            **common,
            warnings=tuple(warnings),
            blocking_reason=(
                "La plataforma aparece como organizadora o minorista. Debe revisarse "
                "la frontera con viaje combinado antes de usar el régimen de mera "
                "intermediación."
            ),
        )

    if boundary == "linked_travel_arrangement":
        basis = (
            _SPANISH_LINKED_BASIS
            if scope == "spain"
            else _EU_LINKED_BASIS
        )
        if scope == "spain":
            basis = (*basis, *_SPANISH_GENERAL_BASIS)
        else:
            basis = (*basis, *_EU_GENERAL_BASIS)
        return TravelAgencyRegimeDecision(
            status="current",
            **common,
            marketplace_information_regime_applies=marketplace,
            dsa_marketplace_duties_apply=(
                marketplace is True and booking >= DSA_GENERAL_APPLICATION_ON
            ),
            ruleset=LINKED_TRAVEL_ARRANGEMENT_RULESET,
            legal_basis=tuple(basis),
            warnings=tuple(warnings),
        )

    basis: tuple[str, ...]
    if scope == "spain":
        basis = _SPANISH_GENERAL_BASIS
        if marketplace is True:
            basis = (*basis, *_SPANISH_MARKETPLACE_BASIS)
    else:
        basis = _EU_GENERAL_BASIS

    dsa_applies = marketplace is True and booking >= DSA_GENERAL_APPLICATION_ON
    if dsa_applies:
        basis = (*basis, *_DSA_BASIS)

    if marketplace is None:
        warnings.append(
            "Debe confirmarse si la interfaz actuaba como mercado en línea para aplicar sus deberes específicos."
        )

    return TravelAgencyRegimeDecision(
        status="current",
        **common,
        marketplace_information_regime_applies=marketplace,
        dsa_marketplace_duties_apply=dsa_applies,
        ruleset=INDEPENDENT_INTERMEDIATION_RULESET,
        legal_basis=tuple(basis),
        warnings=tuple(warnings),
    )
