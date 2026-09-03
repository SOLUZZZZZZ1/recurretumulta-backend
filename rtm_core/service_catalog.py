"""Catálogo autoritativo de servicios y precios de revisión inicial RTM.

El navegador puede solicitar un producto, pero no decide el importe. Para la
revisión inicial, la autoridad es el departamento guardado en el expediente.
"""

from __future__ import annotations

import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SERVICE_CATALOG_VERSION = "rtm_service_catalog_v1_2"
DepartmentCode = Literal[
    "traffic",
    "debt",
    "administration",
    "travel",
    "claims",
    "other",
]


class ReviewQuote(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authority: Literal["rtm_service_catalog"] = "rtm_service_catalog"
    version: str = SERVICE_CATALOG_VERSION
    department: DepartmentCode
    case_type: str = ""
    service_code: str
    payment_stage: Literal["review"] = "review"
    billing_code: Literal["REVIEW_BASIC", "ADMIN_REVIEW"]
    amount_cents: int = Field(ge=0)
    currency: Literal["EUR"] = "EUR"
    stripe_price_env: Literal["STRIPE_PRICE_ID_REVIEW_BASIC", "STRIPE_PRICE_ID_ADMIN"]
    label: str


def normalize_code(value: str | None) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return (
        ascii_value.strip()
        .lower()
        .replace("-", "_")
        .replace("/", "_")
        .replace(" ", "_")
    )


_DEPARTMENT_ALIASES = {
    "traffic": {
        "traffic", "trafico", "dgt", "fine", "multa", "multas", "sanction",
        "sancion", "vehicle", "vehiculo", "vehiculos", "vehicle_removal",
        "eliminacion_vehiculo", "eliminacion_vehiculos", "other_traffic",
    },
    "debt": {
        "debt", "debts", "deuda", "deudas", "morosidad", "asnef",
        "asnef_equifax", "equifax", "badexcug", "creditor_claim", "other_debt",
    },
    "administration": {
        "admin", "administration", "administracion", "administracion_publica",
        "aeat", "hacienda", "social_security", "seguridad_social", "town_hall",
        "ayuntamiento", "ayuntamientos", "catastro", "general_administration",
    },
    "travel": {
        "travel", "travels", "viaje", "viajes", "airline", "aerolinea",
        "aerolineas", "flight", "vuelo", "vuelos", "baggage", "equipaje",
        "hotel", "alojamiento", "package_travel", "viaje_combinado",
        "travel_claim", "reclamacion_viaje", "other_travel",
    },
    "claims": {
        "claim", "claims", "reclamacion", "reclamaciones", "consumer", "consumo",
        "telecommunications", "telecomunicaciones", "energy", "energia",
        "insurance_claim", "seguro", "banking", "banca", "ecommerce",
        "comercio_electronico", "professional_services", "servicios_profesionales",
        "other_claim",
    },
    "other": {"other", "otro", "otros", "general", "review", "revision"},
}

_ALIAS_TO_DEPARTMENT = {
    alias: department
    for department, aliases in _DEPARTMENT_ALIASES.items()
    for alias in aliases
}

_INTAKE_CASE_TYPES = {
    "traffic": frozenset({"fine", "vehicle_removal", "other_traffic"}),
    "debt": frozenset({"asnef_equifax", "creditor_claim", "other_debt"}),
    "administration": frozenset(
        {"aeat", "social_security", "town_hall", "general_administration"}
    ),
    "claims": frozenset({"airline", "consumer", "other_claim"}),
}
_PUBLIC_FAMILY_INTAKE = {
    "trafico": ("traffic", _INTAKE_CASE_TYPES["traffic"]),
    "viajes": ("claims", frozenset({"airline", "consumer", "other_claim"})),
    "morosidad": ("debt", _INTAKE_CASE_TYPES["debt"]),
    "administracion": ("administration", _INTAKE_CASE_TYPES["administration"]),
    "bancos": ("claims", frozenset({"consumer"})),
    "energia": ("claims", frozenset({"consumer"})),
    "telecomunicaciones": ("claims", frozenset({"consumer"})),
    "seguros": ("claims", frozenset({"consumer"})),
}


def validate_public_intake_classification(
    department: str | None,
    case_type: str | None,
    public_service_family: str | None = None,
) -> tuple[str, str]:
    """Accept only server-known department/type/family combinations."""

    department_code = normalize_code(department)
    case_type_code = normalize_code(case_type)
    allowed_types = _INTAKE_CASE_TYPES.get(department_code)
    if not allowed_types or case_type_code not in allowed_types:
        raise ValueError("Clasificación pública RTM incoherente")

    family_code = normalize_code(public_service_family)
    if family_code:
        family_contract = _PUBLIC_FAMILY_INTAKE.get(family_code)
        if (
            family_contract is None
            or department_code != family_contract[0]
            or case_type_code not in family_contract[1]
        ):
            raise ValueError("Familia pública RTM incoherente")
    return department_code, case_type_code


def canonical_department(
    department: str | None,
    case_type: str | None = None,
    category: str | None = None,
) -> DepartmentCode:
    """Resuelve el satélite desde datos persistidos, nunca desde el precio pedido."""

    resolved = []
    for candidate in (department, category, case_type):
        normalized = normalize_code(candidate)
        if normalized in _ALIAS_TO_DEPARTMENT:
            resolved.append(_ALIAS_TO_DEPARTMENT[normalized])
    # Existing inconsistent records must never turn an administrative review
    # into the cheaper generic tier. New public intakes are rejected earlier
    # by validate_public_intake_classification.
    if "administration" in resolved:
        return "administration"
    if resolved:
        return resolved[0]  # type: ignore[return-value]
    return "other"


def resolve_review_quote(
    department: str | None,
    case_type: str | None = None,
    category: str | None = None,
) -> ReviewQuote:
    canonical = canonical_department(department, case_type, category)
    normalized_case_type = normalize_code(case_type)

    if canonical == "administration":
        return ReviewQuote(
            department=canonical,
            case_type=normalized_case_type,
            service_code="administration",
            billing_code="ADMIN_REVIEW",
            amount_cents=2500,
            stripe_price_env="STRIPE_PRICE_ID_ADMIN",
            label="Revisión inicial administrativa",
        )

    labels = {
        "traffic": "Revisión inicial de tráfico",
        "debt": "Revisión inicial de morosidad o deuda",
        "travel": "Revisión inicial de viaje",
        "claims": "Revisión inicial de reclamación",
        "other": "Revisión inicial del expediente",
    }
    return ReviewQuote(
        department=canonical,
        case_type=normalized_case_type,
        service_code=canonical,
        billing_code="REVIEW_BASIC",
        amount_cents=1000,
        stripe_price_env="STRIPE_PRICE_ID_REVIEW_BASIC",
        label=labels[canonical],
    )
