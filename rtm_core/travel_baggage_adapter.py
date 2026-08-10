"""Adaptador endurecido para el especialista jurídico de equipaje aéreo.

El especialista común conserva la clasificación, trazabilidad y valoración. Este
adaptador corrige una frontera jurídica concreta: el plazo de siete días del
artículo 31 del Convenio de Montreal se proyecta sobre el daño del equipaje
facturado, pero no se copia automáticamente al equipaje no facturado o de mano.
"""

from __future__ import annotations

from rtm_core.authority_repository import FamilyResolutionRecord, ValidatedFactsRecord
from rtm_core.contracts import LegalPreview, MissingItemSeverity
from rtm_core.cross_service_specialist_support import dedupe_missing, missing_item
from rtm_core.travel_baggage_specialist import (
    _baggage_type,
    _incident,
    build_travel_baggage_preview,
)


TRAVEL_BAGGAGE_ADAPTER_VERSION = "rtm_travel_baggage_adapter_v1_0"

_CHECKED_DAMAGE_CODES = {
    "baggage_damage_receipt_date_missing",
    "baggage_damage_written_claim_missing",
    "baggage_damage_notice_late_review",
    "baggage_damage_notice_timing_review",
}
_CHECKED_DAMAGE_DEADLINE_LABEL = "Reclamación escrita por daños"


def build_travel_baggage_adapter_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    """Construye la previa y evita trasladar plazos de bodega a equipaje de mano."""

    preview = build_travel_baggage_preview(facts_record, family_record)
    incident = _incident(facts_record)
    baggage_type = _baggage_type(facts_record)

    if incident != "damage" or baggage_type == "checked":
        return preview.model_copy(
            update={
                "created_by_component": (
                    f"{preview.created_by_component}+{TRAVEL_BAGGAGE_ADAPTER_VERSION}"
                )
            }
        )

    missing = [
        item for item in preview.missing_items if item.code not in _CHECKED_DAMAGE_CODES
    ]
    deadlines = [
        item
        for item in preview.deadlines
        if item.label != _CHECKED_DAMAGE_DEADLINE_LABEL
    ]

    if baggage_type == "unchecked":
        missing.append(
            missing_item(
                "baggage_unchecked_damage_notice_review",
                (
                    "Debe conservarse una reclamación escrita pronta por el daño "
                    "del equipaje no facturado, pero no se aplica automáticamente "
                    "el plazo específico de siete días previsto para el equipaje "
                    "facturado."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )

    return preview.model_copy(
        update={
            "missing_items": dedupe_missing(missing),
            "deadlines": deadlines,
            "created_by_component": (
                f"{preview.created_by_component}+{TRAVEL_BAGGAGE_ADAPTER_VERSION}"
            ),
        }
    )
