"""Registro satélite de especialistas jurídicos de reclamaciones RTM."""

from __future__ import annotations

from typing import Callable, Optional

from rtm_core.authority_repository import (
    FamilyResolutionRecord,
    ValidatedFactsRecord,
)
from rtm_core.claims_banking_specialist import build_claims_banking_preview
from rtm_core.claims_ecommerce_specialist import build_claims_ecommerce_preview
from rtm_core.claims_energy_specialist import build_claims_energy_preview
from rtm_core.claims_telecommunications_specialist import (
    build_claims_telecommunications_preview,
)
from rtm_core.contracts import LegalPreview


CLAIMS_SPECIALIST_REGISTRY_VERSION = "rtm_claims_specialist_registry_v1_0"

ClaimsBuilder = Callable[
    [ValidatedFactsRecord, FamilyResolutionRecord],
    LegalPreview,
]

_CLAIMS_REGISTRY: dict[str, ClaimsBuilder] = {
    "claims.banking": build_claims_banking_preview,
    "claims.ecommerce": build_claims_ecommerce_preview,
    "claims.energy": build_claims_energy_preview,
    "claims.telecommunications": build_claims_telecommunications_preview,
}


def registered_claims_specialists() -> tuple[str, ...]:
    return tuple(sorted(_CLAIMS_REGISTRY))


def claims_specialist_builder(name: str | None) -> Optional[ClaimsBuilder]:
    return _CLAIMS_REGISTRY.get(str(name or "").strip())
