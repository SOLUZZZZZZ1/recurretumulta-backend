"""Perfil semántico de validación live para los fixtures sintéticos de RTM.

El validador base conserva criterios genéricos estables. Este perfil adapta
exclusivamente los requisitos documentales del escenario de telecomunicaciones
a los campos equivalentes instalados por ``claims_consumer_extension``.

No altera extracción, normalización, resolución de familia, especialistas ni
primer rumbo. Tampoco reduce los controles de confianza ni habilita Generate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from fastapi import HTTPException

from rtm_core.document_extraction import (
    DocumentProvider,
    OpenAIResponsesDocumentProvider,
)
from rtm_core.staging_validation import (
    SyntheticStagingReport,
    SyntheticStagingScenario,
    assert_live_synthetic_guard,
    run_synthetic_scenario,
    staging_scenarios,
)


SEMANTIC_STAGING_VALIDATION_VERSION = "rtm_synthetic_staging_validation_v1_1"
SEMANTIC_PROFILE_VERSION = "rtm_staging_semantic_profile_v1_0"

_CLAIMS_TELECOMMUNICATIONS_REQUIRED_GROUPS: tuple[tuple[str, ...], ...] = (
    # Identificación del servicio o contrato afectado.
    (
        "producto_servicio",
        "producto_servicio_consumo",
        "referencia_servicio",
        "contrato_ref",
        "contrato_consumo_ref",
    ),
    # Empresa responsable: vocabulario genérico o extensión de Consumo.
    ("proveedor", "empresa_consumo"),
    # Secuencia temporal de la baja.
    (
        "baja_solicitada_fecha",
        "fecha_baja_efectiva",
        "fecha_baja_consumo",
    ),
    # Evidencia económica o identificador de la factura posterior a la baja.
    (
        "importe_reclamado_eur",
        "importe_pagado_eur",
        "factura_numero",
        "cobro_posterior_baja_consumo_eur",
        "factura_ticket_consumo_ref",
    ),
    # Descripción, respuesta o solución documentada que permita orientar el caso.
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
    """Devuelve escenarios base con equivalencias semánticas explícitas.

    Solo se sustituye el contrato de suficiencia del fixture de
    telecomunicaciones. Familia, especialista, confianza y resto de escenarios
    permanecen exactamente como en el validador base.
    """

    result: list[SyntheticStagingScenario] = []
    for scenario in staging_scenarios(selected_services):
        if scenario.code != "claims_telecommunications":
            result.append(scenario)
            continue
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
    return tuple(result)


def run_semantic_synthetic_staging_suite(
    *,
    provider: Optional[DocumentProvider] = None,
    selected_services: Optional[Iterable[str]] = None,
    require_live_guard: bool = False,
    root: Optional[Path] = None,
) -> SyntheticStagingReport:
    """Ejecuta el smoke usando el perfil semántico v1.1.

    La salida conserva el mismo modelo de informe y cambia únicamente su versión
    para que los logs distingan con claridad el contrato ejecutado.
    """

    selected_provider = provider or OpenAIResponsesDocumentProvider()
    live_provider = isinstance(selected_provider, OpenAIResponsesDocumentProvider)
    if require_live_guard or (provider is None and live_provider):
        assert_live_synthetic_guard()

    scenarios = semantic_staging_scenarios(selected_services)
    if not scenarios:
        raise HTTPException(
            status_code=409,
            detail="La selección no contiene escenarios sintéticos registrados.",
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
