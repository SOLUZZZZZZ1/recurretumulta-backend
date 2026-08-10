"""Registro satélite de especialistas jurídicos de viajes RTM."""

from __future__ import annotations

from typing import Callable, Optional

from rtm_core.authority_repository import (
    FamilyResolutionRecord,
    ValidatedFactsRecord,
)
from rtm_core.contracts import LegalPreview
from rtm_core.travel_flight_cancelled_specialist import (
    build_travel_flight_cancelled_preview,
)
from rtm_core.travel_flight_delay_specialist import (
    build_travel_flight_delay_preview,
)


TRAVEL_SPECIALIST_REGISTRY_VERSION = "rtm_travel_specialist_registry_v1_1"

TravelBuilder = Callable[
    [ValidatedFactsRecord, FamilyResolutionRecord],
    LegalPreview,
]

_TRAVEL_REGISTRY: dict[str, TravelBuilder] = {
    "travel.flight_cancelled": build_travel_flight_cancelled_preview,
    "travel.flight_delay": build_travel_flight_delay_preview,
}


def registered_travel_specialists() -> tuple[str, ...]:
    return tuple(sorted(_TRAVEL_REGISTRY))


def travel_specialist_builder(name: str | None) -> Optional[TravelBuilder]:
    return _TRAVEL_REGISTRY.get(str(name or "").strip())
