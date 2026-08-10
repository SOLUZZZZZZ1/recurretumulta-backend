"""Endurecimiento del especialista de apremio y recaudación RTM.

El especialista base construye la Previa Jurídica. Este adaptador añade una
barrera conservadora para detectar, en hechos validados, respuestas o
resoluciones que podrían haber anulado, revocado, estimado o dejado sin efecto
el acto de origen. No concluye que el apremio sea nulo: obliga a OPS a comprobar
la resolución completa antes de congelar la Previa.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from rtm_core.administration_enforcement_specialist import (
    build_administration_enforcement_preview as _build_base_preview,
)
from rtm_core.authority_repository import (
    FamilyResolutionRecord,
    ValidatedFactsRecord,
)
from rtm_core.contracts import (
    LegalPreview,
    MissingItemSeverity,
)
from rtm_core.cross_service_specialist_support import (
    dedupe_missing,
    missing_item,
    validated_value,
)


ADMINISTRATION_ENFORCEMENT_ADAPTER_VERSION = (
    "rtm_administration_enforcement_adapter_v1_0"
)

_POSSIBLE_ORIGIN_ANNULMENT = re.compile(
    r"\b(?:anulad\w*|revocad\w*|estimad\w*|estimator\w*)\b"
    r"|\b(?:deja\w*|dejad\w*|qued\w*)\s+sin\s+efecto\b",
    flags=re.IGNORECASE,
)


def _fold(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower().replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", raw).strip()


def _possible_origin_annulment(record: ValidatedFactsRecord) -> bool:
    resolution_outcome, _ = validated_value(record, "resolucion_sentido")
    documented_response, _ = validated_value(record, "respuesta_documentada")
    provider_response, _ = validated_value(record, "respuesta_proveedor")
    text = _fold(
        " ".join(
            str(value)
            for value in (
                resolution_outcome,
                documented_response,
                provider_response,
            )
            if value not in (None, "", [], {})
        )
    )
    return bool(text and _POSSIBLE_ORIGIN_ANNULMENT.search(text))


def build_administration_enforcement_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    preview = _build_base_preview(facts_record, family_record)
    if not _possible_origin_annulment(facts_record):
        return preview

    code = "enforcement_possible_annulment_review"
    if any(item.code == code for item in preview.missing_items):
        return preview

    payload = preview.model_dump(mode="python", exclude_none=False)
    payload["missing_items"] = dedupe_missing(
        [
            *preview.missing_items,
            missing_item(
                code,
                (
                    "Consta una resolución o respuesta que podría haber anulado, "
                    "revocado, estimado o dejado sin efecto el acto de origen; OPS "
                    "debe comprobar su alcance antes de continuar la recaudación."
                ),
                MissingItemSeverity.BLOCKING,
            ),
        ]
    )
    payload["created_by_component"] = (
        f"{preview.created_by_component}+"
        f"{ADMINISTRATION_ENFORCEMENT_ADAPTER_VERSION}"
    )
    return LegalPreview.model_validate(payload)
