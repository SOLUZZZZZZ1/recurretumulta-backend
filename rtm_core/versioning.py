"""Observabilidad segura de versiones del núcleo RTM."""

from __future__ import annotations

import importlib
import os
import platform
from datetime import datetime, timezone
from typing import Any, Optional

from rtm_core.contracts import (
    CORE_CONTRACTS_VERSION,
    FAMILY_RESOLUTION_VERSION,
    LEGAL_PREVIEW_VERSION,
    VALIDATED_FACTS_VERSION,
)
from rtm_core.readiness import REVIEW_READINESS_VERSION
from rtm_core.service_catalog import SERVICE_CATALOG_VERSION


BASELINE_COMMIT = "73af66c67f5736bfc554006a34cbff415f1ccc35"
BASELINE_BRANCH = "main"

DECLARED_COMPONENT_VERSIONS = {
    "extractor": "traffic_fine_reanalysis_v1_18",
    "extraction_route_policy": "rtm_extraction_route_policy_v1_0",
    "safe_reanalysis_execution": "rtm_safe_reanalysis_execution_v1_0",
    "reanalysis_adapter": "rtm_reanalysis_to_validated_facts_v1_0",
    "family_core": "rtm_family_core_v1_0",
    "specialist_registry": "rtm_specialist_registry_v1_2",
    "traffic_specialist_adapters": "rtm_traffic_specialist_adapters_v1_0",
    "ops_workspace": "rtm_ops_workspace_v1_0",
    "ops_workspace_policy": "rtm_ops_workspace_policy_v1_0",
    "legacy_generator": "traffic_generate_v1_7",
    "core_generation_gateway": "rtm_generate_gateway_v1_0",
    "submission_automation": "rtm_submission_automation_v1_0",
    "velocity_legal": "velocity_legal_v1_2",
    "semaforo_legal": "semaforo_legal_v1_0",
    "semaforo_secondary": "semaforo_secondary_v1_4",
    "semaforo_precision": "semaforo_precision_v1_0",
    "traffic_generic_facts": "traffic_generic_facts_v1_2",
}

_RUNTIME_LOOKUPS = {
    "extractor": ("reanalysis", "_EXTRACTOR_VERSION"),
    "extraction_route_policy": (
        "rtm_core.extraction_policy",
        "EXTRACTION_POLICY_VERSION",
    ),
    "safe_reanalysis_execution": (
        "rtm_core.reanalysis_execution",
        "REANALYSIS_EXECUTION_VERSION",
    ),
    "reanalysis_adapter": (
        "rtm_core.reanalysis_adapter",
        "REANALYSIS_ADAPTER_VERSION",
    ),
    "family_core": ("rtm_core.family_core", "FAMILY_CORE_VERSION"),
    "specialist_registry": (
        "rtm_core.specialist_dispatch",
        "SPECIALIST_REGISTRY_VERSION",
    ),
    "traffic_specialist_adapters": (
        "rtm_core.traffic_specialist_adapters",
        "TRAFFIC_SPECIALIST_ADAPTERS_VERSION",
    ),
    "ops_workspace": ("rtm_core.workspace_service", "WORKSPACE_VERSION"),
    "ops_workspace_policy": (
        "rtm_core.workspace_policy",
        "WORKSPACE_POLICY_VERSION",
    ),
    "legacy_generator": ("generate", "_GENERATOR_VERSION"),
    "core_generation_gateway": (
        "rtm_core.generation_gateway",
        "GENERATION_GATEWAY_VERSION",
    ),
    "submission_automation": ("ops_automation", "AUTOMATION_VERSION"),
    "velocity_legal": (
        "ai.infractions.velocidad",
        "VELOCITY_LEGAL_INTELLIGENCE_VERSION",
    ),
    "semaforo_legal": (
        "ai.infractions.semaforo",
        "SEMAFORO_LEGAL_INTELLIGENCE_VERSION",
    ),
}


def _first_env(*names: str) -> Optional[str]:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


def _runtime_constant(
    module_name: str,
    attribute: str,
) -> tuple[Optional[str], Optional[str]]:
    try:
        module = importlib.import_module(module_name)
        value = getattr(module, attribute, None)
        return (str(value) if value not in (None, "") else None, None)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def build_version_snapshot() -> dict[str, Any]:
    components: dict[str, Any] = {}

    for name, declared in DECLARED_COMPONENT_VERSIONS.items():
        runtime = None
        error = None
        lookup = _RUNTIME_LOOKUPS.get(name)
        if lookup:
            runtime, error = _runtime_constant(*lookup)
        components[name] = {
            "declared": declared,
            "runtime": runtime,
            "matches_declared": runtime == declared if runtime is not None else None,
            "discovery_error": error,
        }

    return {
        "ok": True,
        "service": "rtm-core",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "deployment": {
            "baseline_commit": BASELINE_COMMIT,
            "baseline_branch": BASELINE_BRANCH,
            "runtime_commit": _first_env(
                "RENDER_GIT_COMMIT",
                "GIT_COMMIT",
                "COMMIT_SHA",
                "SOURCE_COMMIT",
            ),
            "runtime_branch": _first_env(
                "RENDER_GIT_BRANCH",
                "GIT_BRANCH",
                "BRANCH_NAME",
            ),
            "render_service": _first_env("RENDER_SERVICE_NAME"),
        },
        "contracts": {
            "core": CORE_CONTRACTS_VERSION,
            "validated_facts": VALIDATED_FACTS_VERSION,
            "family_resolution": FAMILY_RESOLUTION_VERSION,
            "legal_preview": LEGAL_PREVIEW_VERSION,
            "service_catalog": SERVICE_CATALOG_VERSION,
            "review_readiness": REVIEW_READINESS_VERSION,
            "authority_store": "rtm_authority_store_v1_0",
            "extraction_route_policy": "rtm_extraction_route_policy_v1_0",
            "safe_reanalysis_execution": "rtm_safe_reanalysis_execution_v1_0",
            "reanalysis_adapter": "rtm_reanalysis_to_validated_facts_v1_0",
            "family_core": "rtm_family_core_v1_0",
            "specialist_registry": "rtm_specialist_registry_v1_2",
            "traffic_specialist_adapters": "rtm_traffic_specialist_adapters_v1_0",
            "ops_workspace": "rtm_ops_workspace_v1_0",
            "ops_workspace_policy": "rtm_ops_workspace_policy_v1_0",
            "legal_preview_store": "rtm_legal_preview_store_v1_1",
            "authority_schema": "rtm_core_authority_schema_v1_2",
            "generation_gateway": "rtm_generate_gateway_v1_0",
            "submission_automation": "rtm_submission_automation_v1_0",
        },
        "components": components,
        "runtime": {"python": platform.python_version()},
    }
