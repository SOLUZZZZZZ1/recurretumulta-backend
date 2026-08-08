"""Catálogo autoritativo de servicios y precios de revisión inicial RTM.

El navegador puede solicitar un producto, pero no decide el importe. Para la
revisión inicial, la autoridad es el departamento guardado en el expediente.
"""

from __future__ import annotations

import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SERVICE_CATALOG_VERSION = "rtm_service_catalog_v1_0"


class ReviewQuote(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authority: Literal["rtm_service_catalog"] = "rtm_service_catalog"
    version: str = SERVICE_CATALOG_VERSION
    department: Literal["traffic", "debt", "administration", "claims", "other"]
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
    "claims": {
        "claim", "claims", "reclamacion", "reclamaciones", "airline", "aerolinea",
        "consumer", "consumo", "travel", "viaje", "viajes", "other_claim",
    },
    "other": {"other", "otro", "otros", "general", "review", "revision"},
}

_ALIAS_TO_DEPARTMENT = {
    alias: department
    for department, aliases in _DEPARTMENT_ALIASES.items()
    for alias in aliases
}


def canonical_department(
    department: str | None,
    case_type: str | None = None,
    category: str | None = None,
) -> Literal["traffic", "debt", "administration", "claims", "other"]:
    """Resuelve el satélite desde datos persistidos, nunca desde el precio pedido."""

    for candidate in (department, category, case_type):
        normalized = normalize_code(candidate)
        if normalized in _ALIAS_TO_DEPARTMENT:
            return _ALIAS_TO_DEPARTMENT[normalized]  # type: ignore[return-value]
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

    return ReviewQuote(
        department=canonical,
        case_type=normalized_case_type,
        service_code=canonical,
        billing_code="REVIEW_BASIC",
        amount_cents=1000,
        stripe_price_env="STRIPE_PRICE_ID_REVIEW_BASIC",
        label="Revisión inicial del expediente",
    )
