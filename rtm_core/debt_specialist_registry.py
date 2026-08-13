"""Registro satélite de especialistas jurídicos de morosidad RTM."""

from __future__ import annotations

from typing import Callable, Optional

from rtm_core.authority_repository import (
    FamilyResolutionRecord,
    ValidatedFactsRecord,
)
from rtm_core.contracts import LegalPreview
from rtm_core.debt_credit_file_specialist import build_debt_credit_file_preview
from rtm_core.debt_unpaid_invoice_specialist import (
    build_debt_unpaid_invoice_preview,
)
from rtm_core.debt_unpaid_rent_specialist import build_debt_unpaid_rent_preview


DEBT_SPECIALIST_REGISTRY_VERSION = "rtm_debt_specialist_registry_v1_0"

DebtBuilder = Callable[
    [ValidatedFactsRecord, FamilyResolutionRecord],
    LegalPreview,
]

_DEBT_REGISTRY: dict[str, DebtBuilder] = {
    "debt.credit_file": build_debt_credit_file_preview,
    "debt.unpaid_invoice": build_debt_unpaid_invoice_preview,
    "debt.unpaid_rent": build_debt_unpaid_rent_preview,
}


def registered_debt_specialists() -> tuple[str, ...]:
    return tuple(sorted(_DEBT_REGISTRY))


def debt_specialist_builder(name: str | None) -> Optional[DebtBuilder]:
    return _DEBT_REGISTRY.get(str(name or "").strip())
