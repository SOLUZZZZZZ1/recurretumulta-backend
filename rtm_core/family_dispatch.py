"""Único punto de entrada para resolver la familia de cualquier satélite RTM.

El despacho no clasifica por sí mismo: selecciona el resolver autorizado para
el servicio persistido. Tráfico conserva intacto su CORE validado; los demás
satélites consumen el resolver transversal conservador.
"""

from __future__ import annotations

from rtm_core.contracts import FamilyResolution, ValidatedFacts
from rtm_core.cross_service_family import (
    CROSS_SERVICE_FAMILY_VERSION,
    resolve_cross_service_family,
)
from rtm_core.family_core import FAMILY_CORE_VERSION, resolve_family as resolve_traffic_family
from rtm_core.service_catalog import canonical_department


FAMILY_DISPATCH_VERSION = "rtm_family_dispatch_v1_0"


def resolver_version_for(service: str | None) -> str:
    return (
        FAMILY_CORE_VERSION
        if canonical_department(service) == "traffic"
        else CROSS_SERVICE_FAMILY_VERSION
    )


def resolve_family(facts: ValidatedFacts) -> FamilyResolution:
    if canonical_department(facts.service) == "traffic":
        return resolve_traffic_family(facts)
    return resolve_cross_service_family(facts)
