"""Perfil semántico de validación live para los fixtures sintéticos de RTM.

El validador base conserva criterios genéricos estables. Este perfil adapta los
contratos de suficiencia a combinaciones equivalentes de hechos estructurados
que el extractor real puede producir sin inventar un resumen libre.

No altera normalización, confianza, especialistas ni primer rumbo. La familia
solo puede quedar resuelta por el resolver autorizado y Generate permanece
bloqueado.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from fastapi import HTTPException

from rtm_core.document_extraction import (
    DocumentProvider,
    OpenAIResponsesDocumentProvider,
)
from rtm_core.document_provider_retry import (
    RetryingOpenAIResponsesDocumentProvider,
)
from rtm_core.staging_validation import (
    SyntheticStagingReport,
    SyntheticStagingScenario,
    assert_live_synthetic_guard,
    run_synthetic_scenario,
    staging_scenarios,
)


SEMANTIC_STAGING_VALIDATION_VERSION = (
    "rtm_synthetic_staging_validation_v1_2"
)
SEMANTIC_PROFILE_VERSION = "rtm_staging_semantic_profile_v1_1"


_DEBT_REQUIRED_GROUPS: tuple[tuple[str, ...], ...] = (
    ("descripcion_hecho", "concepto_deuda"),
    ("importe_deuda_eur", "saldo_pendiente_eur"),
    ("fecha_vencimiento",),
)

_ADMINISTRATION_REQUIRED_GROUPS: tuple[tuple[str, ...], ...] = (
    ("importe_exigido_eur", "principal_eur"),
)

_CLAIMS_TELECOMMUNICATIONS_REQUIRED_GROUPS: tuple[
    tuple[str, ...], ...
] = (
    (
        "producto_servicio",
        "producto_servicio_consumo",
        "referencia_servicio",
        "contrato_ref",
        "contrato_consumo_ref",
    ),
    ("proveedor", "empresa_consumo"),
    (
        "baja_solicitada_fecha",
        "fecha_baja_efectiva",
        "fecha_baja_consumo",
    ),
    (
        "importe_reclamado_eur",
        "importe_pagado_eur",
        "factura_numero",
        "cobro_posterior_baja_consumo_eur",
        "factura_ticket_consumo_ref",
    ),
    (
        "descripcion_hecho",
        "solucion_solicitada",
        "solucion_solicitada_consumo",
        "respuesta_proveedor",
        "respuesta_consumo",
        "baja_consumo_solicitada",
    ),
)


def semantic_staging_scenarios(
    selected_services: Optional[Iterable[str]] = None,
) -> tuple[SyntheticStagingScenario, ...]:
    """Devuelve escenarios con equivalencias semánticas explícitas."""

    result: list[SyntheticStagingScenario] = []
    for scenario in staging_scenarios(selected_services):
        if scenario.code == "debt_unpaid_invoice":
            result.append(
                scenario.model_copy(
                    update={
                        "required_fields": ("factura_numero",),
                        "required_any_groups": _DEBT_REQUIRED_GROUPS,
                    }
                )
            )
            continue
        if scenario.code == "administration_enforcement":
            result.append(
                scenario.model_copy(
                    update={
                        "required_any_groups": (
                            _ADMINISTRATION_REQUIRED_GROUPS
                        ),
                    }
                )
            )
            continue
        if scenario.code == "claims_telecommunications":
            result.append(
                scenario.model_copy(
                    update={
                        "required_fields": (),
                        "required_any_groups": (
                            _CLAIMS_TELECOMMUNICATIONS_REQUIRED_GROUPS
                        ),
                    }
                )
            )
            continue
        result.append(scenario)
    return tuple(result)


def run_semantic_synthetic_staging_suite(
    *,
    provider: Optional[DocumentProvider] = None,
    selected_services: Optional[Iterable[str]] = None,
    require_live_guard: bool = False,
    root: Optional[Path] = None,
) -> SyntheticStagingReport:
    """Ejecuta el smoke semántico v1.2 con reintentos acotados para 429."""

    selected_provider = (
        provider or RetryingOpenAIResponsesDocumentProvider()
    )
    live_provider = isinstance(
        selected_provider,
        OpenAIResponsesDocumentProvider,
    )
    if require_live_guard or (provider is None and live_provider):
        assert_live_synthetic_guard()

    scenarios = semantic_staging_scenarios(selected_services)
    if not scenarios:
        raise HTTPException(
            status_code=409,
            detail=(
                "La selección no contiene escenarios sintéticos "
                "registrados."
            ),
        )

    results = [
        run_synthetic_scenario(
            scenario,
            provider=selected_provider,
            root=root,
        )
        for scenario in scenarios
    ]
    return SyntheticStagingReport(
        version=SEMANTIC_STAGING_VALIDATION_VERSION,
        live_provider=live_provider,
        provider_version=str(selected_provider.version),
        model=str(selected_provider.model),
        scenarios=results,
        passed=all(item.passed for item in results),
    )
