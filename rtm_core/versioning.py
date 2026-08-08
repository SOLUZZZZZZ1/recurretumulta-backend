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


BASELINE_COMMIT = "73af66c67f5736bfc554006a34cbff415f1ccc35"
BASELINE_BRANCH = "main"

DECLARED_COMPONENT_VERSIONS = {
    "extractor": "traffic_fine_reanalysis_v1_18",
    "generator": "traffic_generate_v1_7",
    "velocity_legal": "velocity_legal_v1_2",
    "semaforo_legal": "semaforo_legal_v1_0",
    "semaforo_secondary": "semaforo_secondary_v1_4",
    "semaforo_precision": "semaforo_precision_v1_0",
    "traffic_generic_facts": "traffic_generic_facts_v1_2",
}

_RUNTIME_LOOKUPS = {
    "extractor": ("reanalysis", "_EXTRACTOR_VERSION"),
    "generator": ("generate", "_GENERATOR_VERSION"),
    "velocity_legal": ("ai.infractions.velocidad", "VELOCITY_LEGAL_INTELLIGENCE_VERSION"),
    "semaforo_legal": ("ai.infractions.semaforo", "SEMAFORO_LEGAL_INTELLIGENCE_VERSION"),
}


def _first_env(*names: str) -> Optional[str]:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


def _runtime_constant(module_name: str, attribute: str) -> tuple[Optional[str], Optional[str]]:
    try:
        module = importlib.import_module(module_name)
        value = getattr(module, attribute, None)
        return (str(value) if value not in (None, "") else None, None)
    except Exception as exc:  # Observabilidad nunca debe impedir arrancar RTM.
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
            "runtime_branch": _first_env("RENDER_GIT_BRANCH", "GIT_BRANCH", "BRANCH_NAME"),
            "render_service": _first_env("RENDER_SERVICE_NAME"),
        },
        "contracts": {
            "core": CORE_CONTRACTS_VERSION,
            "validated_facts": VALIDATED_FACTS_VERSION,
            "family_resolution": FAMILY_RESOLUTION_VERSION,
            "legal_preview": LEGAL_PREVIEW_VERSION,
        },
        "components": components,
        "runtime": {
            "python": platform.python_version(),
        },
    }
