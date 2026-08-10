"""Adaptador endurecido para el especialista ``travel.package``.

El especialista base construye la Previa Jurídica. Este adaptador cierra dos
fronteras que dependen de hechos documentales específicos: la regla del 25 % o
carácter esencial cuando el segundo servicio es únicamente turístico, y la
separación absoluta frente a los servicios de viaje vinculados.
"""

from __future__ import annotations

from typing import Any

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
    validated_source_keys,
    validated_value,
)
from rtm_core.package_travel_regime import resolve_package_travel_regime
from rtm_core.travel_package_specialist import (
    _package_status,
    _service_values,
    build_travel_package_preview,
)


TRAVEL_PACKAGE_ADAPTER_VERSION = "rtm_travel_package_adapter_v1_0"

_RECOMPUTED_CODES = {
    "package_regime_review",
    "package_qualification_review",
}


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def build_travel_package_adapter_preview(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
) -> LegalPreview:
    """Recalcula la frontera de paquete con hechos tipados y bloquea los vinculados."""

    preview = build_travel_package_preview(facts_record, family_record)

    contract_date, _ = validated_value(
        facts_record,
        "fecha_reserva",
        "fecha_documento",
    )
    package_start, _ = validated_value(
        facts_record,
        "fecha_inicio_viaje",
        "estancia_inicio",
    )
    package_end, _ = validated_value(
        facts_record,
        "fecha_fin_viaje",
        "estancia_fin",
    )
    organizer_country, _ = validated_value(
        facts_record,
        "pais_organizador",
    )
    tourist_share, tourist_share_key = validated_value(
        facts_record,
        "porcentaje_servicio_turistico",
    )
    tourist_essential, tourist_essential_key = validated_value(
        facts_record,
        "servicio_turistico_esencial",
    )
    linked_arrangement, linked_arrangement_key = validated_value(
        facts_record,
        "servicio_viaje_vinculado",
    )

    regime = resolve_package_travel_regime(
        contract_date=contract_date,
        package_start=package_start,
        package_end=package_end,
        organizer_country=organizer_country,
        package_status=_package_status(facts_record),
        service_types=_service_values(facts_record),
        tourist_service_share_percent=tourist_share,
        tourist_service_essential=tourist_essential,
    )

    missing = list(preview.missing_items)
    arguments = list(preview.legal_arguments)
    summary = list(preview.validated_facts_summary)
    risks = list(preview.risks)

    if regime.status == "current" and regime.package_qualified is True:
        missing = [item for item in missing if item.code not in _RECOMPUTED_CODES]
        arguments = [
            item.model_copy(update={"legal_basis": list(regime.legal_basis)})
            for item in arguments
        ]

    if _present(tourist_share) or isinstance(tourist_essential, bool):
        missing.append(
            missing_item(
                "package_tourist_service_threshold_evidence_review",
                (
                    "OPS debe conservar el desglose del valor total y la prueba de "
                    "si el servicio turístico fue una característica esencial "
                    "anunciada, antes de cerrar la calificación del paquete."
                ),
                MissingItemSeverity.HUMAN_REVIEW,
            )
        )
        summary.append(
            (
                "Frontera de servicio turístico: participación "
                f"{tourist_share}; carácter esencial {tourist_essential}."
            )
        )

    if linked_arrangement is True:
        missing.append(
            missing_item(
                "package_linked_travel_arrangement_route_required",
                (
                    "Los hechos identifican un servicio de viaje vinculado. Debe "
                    "invalidarse la ruta travel.package y resolverse la familia "
                    "adecuada antes de formular fundamentos de viaje combinado."
                ),
                MissingItemSeverity.BLOCKING,
            )
        )
        arguments = [
            item.model_copy(update={"legal_basis": []})
            for item in arguments
        ]
        risks.append(
            "Un servicio de viaje vinculado no puede tratarse automáticamente como viaje combinado."
        )
        summary.append("Servicio de viaje vinculado: Sí; ruta de paquete bloqueada.")
    elif linked_arrangement is False:
        summary.append("Servicio de viaje vinculado: No.")

    additional_source_keys = validated_source_keys(
        facts_record,
        (
            tourist_share_key,
            tourist_essential_key,
            linked_arrangement_key,
        ),
    )
    source_keys = list(dict.fromkeys([
        *preview.source_fact_keys,
        *additional_source_keys,
    ]))

    return preview.model_copy(
        update={
            "validated_facts_summary": list(dict.fromkeys(summary)),
            "source_fact_keys": source_keys,
            "legal_arguments": arguments,
            "missing_items": dedupe_missing(missing),
            "risks": list(dict.fromkeys(risks)),
            "created_by_component": (
                f"{preview.created_by_component}+{TRAVEL_PACKAGE_ADAPTER_VERSION}"
            ),
        }
    )
