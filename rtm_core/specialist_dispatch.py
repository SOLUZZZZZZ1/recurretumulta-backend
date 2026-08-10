"""Despacho único de especialistas jurídicos RTM.

La familia ya llega bloqueada por CORE. Este módulo no clasifica: únicamente
selecciona el adaptador registrado para el especialista exacto de la resolución.
"""

from __future__ import annotations

from fastapi import HTTPException

from rtm_core.administration_enforcement_specialist import (
    build_administration_enforcement_preview,
)
from rtm_core.authority_repository import FamilyResolutionRecord, ValidatedFactsRecord
from rtm_core.contracts import LegalPreview
from rtm_core.debt_unpaid_invoice_specialist import (
    build_debt_unpaid_invoice_preview,
)
from rtm_core.specialist_registry import build_temeraria_preview
from rtm_core.traffic_specialist_adapters import (
    build_semaforo_preview,
    build_velocity_preview,
)


SPECIALIST_REGISTRY_VERSION = "rtm_specialist_registry_v1_4"

_REGISTRY = {
    "administration.enforcement": build_administration_enforcement_preview,
    "debt.unpaid_invoice": build_debt_unpaid_invoice_preview,
    "traffic.temeraria": build_temeraria_preview,
    "traffic.velocidad": build_velocity_preview,
    "traffic.semaforo": build_semaforo_preview,
}


def registered_specialists() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def build_legal_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    specialist = str(family_record.resolution.specialist or "").strip()
    builder = _REGISTRY.get(specialist)
    if not builder:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "El especialista resuelto todavía no dispone de adaptador LegalPreview.",
                "specialist": specialist or None,
                "registered_specialists": list(registered_specialists()),
                "requires_operator_review": True,
            },
        )
    return builder(facts_record, family_record)
