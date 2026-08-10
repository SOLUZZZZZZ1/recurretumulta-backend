"""Registro satélite de especialistas jurídicos de viajes RTM."""

from __future__ import annotations

from typing import Callable, Optional

from rtm_core.authority_repository import (
    FamilyResolutionRecord,
    ValidatedFactsRecord,
)
from rtm_core.contracts import LegalPreview
from rtm_core.travel_baggage_adapter import (
    build_travel_baggage_adapter_preview,
)
from rtm_core.travel_denied_boarding_specialist import (
    build_travel_denied_boarding_preview,
)
from rtm_core.travel_flight_cancelled_specialist import (
    build_travel_flight_cancelled_preview,
)
from rtm_core.travel_flight_delay_specialist import (
    build_travel_flight_delay_preview,
)
from rtm_core.travel_hotel_specialist import build_travel_hotel_preview
from rtm_core.travel_package_specialist import build_travel_package_preview


TRAVEL_SPECIALIST_REGISTRY_VERSION = "rtm_travel_specialist_registry_v1_2"

TravelBuilder = Callable[
    [ValidatedFactsRecord, FamilyResolutionRecord],
    LegalPreview,
]

_TRAVEL_REGISTRY: dict[str, TravelBuilder] = {
    "travel.baggage": build_travel_baggage_adapter_preview,
    "travel.denied_boarding": build_travel_denied_boarding_preview,
    "travel.flight_cancelled": build_travel_flight_cancelled_preview,
    "travel.flight_delay": build_travel_flight_delay_preview,
    "travel.hotel": build_travel_hotel_preview,
    "travel.package": build_travel_package_preview,
}


def registered_travel_specialists() -> tuple[str, ...]:
    return tuple(sorted(_TRAVEL_REGISTRY))


def travel_specialist_builder(name: str | None) -> Optional[TravelBuilder]:
    return _TRAVEL_REGISTRY.get(str(name or "").strip())
