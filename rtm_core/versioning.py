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
    "document_scope": "rtm_document_scope_v1_0",
    "document_fact_catalog": "rtm_document_fact_catalog_v1_1",
    "document_extraction_packet": "rtm_document_extraction_packet_v1_0",
    "document_normalization": "rtm_document_normalization_v1_0",
    "document_facts_gateway": "rtm_document_facts_gateway_v1_0",
    "service_document_extractor": "rtm_service_document_extractor_v1_0",
    "openai_document_provider": "rtm_openai_responses_document_provider_v1_0",
    "deterministic_document_reader": "rtm_deterministic_document_reader_v1_0",
    "document_extraction_store": "rtm_document_extraction_store_v1_0",
    "document_extraction_router": "rtm_document_extraction_router_v1_0",
    "document_extraction_schema": "rtm_document_extraction_schema_v1_0",
    "synthetic_staging_validation": "rtm_synthetic_staging_validation_v1_0",
    "synthetic_staging_fixture_set": "rtm_synthetic_fixture_set_v1_0",
    "service_catalog": "rtm_service_catalog_v1_1",
    "domain_catalog": "rtm_domain_catalog_v1_0",
    "family_dispatch": "rtm_family_dispatch_v1_0",
    "family_core": "rtm_family_core_v1_0",
    "cross_service_family": "rtm_cross_service_family_v1_0",
    "first_direction": "rtm_first_direction_projection_v1_0",
    "specialist_registry": "rtm_specialist_registry_v1_4",
    "specialist_dispatch": "rtm_specialist_dispatch_v1_3",
    "cross_service_specialist_support": (
        "rtm_cross_service_specialist_support_v1_0"
    ),
    "debt_unpaid_invoice_specialist": (
        "rtm_debt_unpaid_invoice_specialist_v1_0"
    ),
    "debt_credit_file_specialist": "rtm_debt_credit_file_specialist_v1_0",
    "debt_specialist_registry": "rtm_debt_specialist_registry_v1_0",
    "administration_enforcement_specialist": (
        "rtm_administration_enforcement_specialist_v1_0"
    ),
    "administration_enforcement_adapter": (
        "rtm_administration_enforcement_adapter_v1_0"
    ),
    "air_passenger_regime": "rtm_air_passenger_regime_v1_0",
    "air_baggage_liability_regime": (
        "rtm_air_baggage_liability_regime_v1_0"
    ),
    "accommodation_consumer_regime": (
        "rtm_accommodation_consumer_regime_v1_0"
    ),
    "travel_flight_cancelled_specialist": (
        "rtm_travel_flight_cancelled_specialist_v1_0"
    ),
    "travel_flight_delay_specialist": (
        "rtm_travel_flight_delay_specialist_v1_0"
    ),
    "travel_denied_boarding_specialist": (
        "rtm_travel_denied_boarding_specialist_v1_0"
    ),
    "travel_baggage_specialist": "rtm_travel_baggage_specialist_v1_0",
    "travel_baggage_adapter": "rtm_travel_baggage_adapter_v1_0",
    "travel_hotel_specialist": "rtm_travel_hotel_specialist_v1_0",
    "travel_specialist_registry": "rtm_travel_specialist_registry_v1_2",
    "claims_telecommunications_specialist": (
        "rtm_claims_telecommunications_specialist_v1_0"
    ),
    "claims_specialist_registry": "rtm_claims_specialist_registry_v1_0",
    "traffic_specialist_adapters": "rtm_traffic_specialist_adapters_v1_0",
    "ops_workspace": "rtm_ops_workspace_v1_2",
    "ops_workspace_policy": "rtm_ops_workspace_policy_v1_3",
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
    "document_scope": (
        "rtm_core.document_scope",
        "DOCUMENT_SCOPE_VERSION",
    ),
    "document_fact_catalog": (
        "rtm_core.document_fact_catalog",
        "DOCUMENT_FACT_CATALOG_VERSION",
    ),
    "document_extraction_packet": (
        "rtm_core.document_normalization",
        "DOCUMENT_EXTRACTION_PACKET_VERSION",
    ),
    "document_normalization": (
        "rtm_core.document_normalization",
        "DOCUMENT_NORMALIZATION_VERSION",
    ),
    "document_facts_gateway": (
        "rtm_core.document_facts_router",
        "DOCUMENT_FACTS_GATEWAY_VERSION",
    ),
    "service_document_extractor": (
        "rtm_core.document_extraction",
        "SERVICE_DOCUMENT_EXTRACTOR_VERSION",
    ),
    "openai_document_provider": (
        "rtm_core.document_extraction",
        "OPENAI_DOCUMENT_PROVIDER_VERSION",
    ),
    "deterministic_document_reader": (
        "rtm_core.document_extraction",
        "DETERMINISTIC_DOCUMENT_READER_VERSION",
    ),
    "document_extraction_store": (
        "rtm_core.document_extraction_repository",
        "DOCUMENT_EXTRACTION_STORE_VERSION",
    ),
    "document_extraction_router": (
        "rtm_core.document_extraction_router",
        "DOCUMENT_EXTRACTION_ROUTER_VERSION",
    ),
    "document_extraction_schema": (
        "rtm_core.document_extraction_migration",
        "DOCUMENT_EXTRACTION_SCHEMA_VERSION",
    ),
    "synthetic_staging_validation": (
        "rtm_core.staging_validation",
        "STAGING_VALIDATION_VERSION",
    ),
    "synthetic_staging_fixture_set": (
        "rtm_core.staging_validation",
        "STAGING_FIXTURE_SET_VERSION",
    ),
    "service_catalog": ("rtm_core.service_catalog", "SERVICE_CATALOG_VERSION"),
    "domain_catalog": ("rtm_core.domain_catalog", "DOMAIN_CATALOG_VERSION"),
    "family_dispatch": ("rtm_core.family_dispatch", "FAMILY_DISPATCH_VERSION"),
    "family_core": ("rtm_core.family_core", "FAMILY_CORE_VERSION"),
    "cross_service_family": (
        "rtm_core.cross_service_family",
        "CROSS_SERVICE_FAMILY_VERSION",
    ),
    "first_direction": (
        "rtm_core.first_direction",
        "FIRST_DIRECTION_VERSION",
    ),
    "specialist_registry": (
        "rtm_core.specialist_dispatch",
        "SPECIALIST_REGISTRY_VERSION",
    ),
    "specialist_dispatch": (
        "rtm_core.specialist_dispatch",
        "SPECIALIST_DISPATCH_VERSION",
    ),
    "cross_service_specialist_support": (
        "rtm_core.cross_service_specialist_support",
        "CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION",
    ),
    "debt_unpaid_invoice_specialist": (
        "rtm_core.debt_unpaid_invoice_specialist",
        "DEBT_UNPAID_INVOICE_SPECIALIST_VERSION",
    ),
    "debt_credit_file_specialist": (
        "rtm_core.debt_credit_file_specialist",
        "DEBT_CREDIT_FILE_SPECIALIST_VERSION",
    ),
    "debt_specialist_registry": (
        "rtm_core.debt_specialist_registry",
        "DEBT_SPECIALIST_REGISTRY_VERSION",
    ),
    "administration_enforcement_specialist": (
        "rtm_core.administration_enforcement_specialist",
        "ADMINISTRATION_ENFORCEMENT_SPECIALIST_VERSION",
    ),
    "administration_enforcement_adapter": (
        "rtm_core.administration_enforcement_adapter",
        "ADMINISTRATION_ENFORCEMENT_ADAPTER_VERSION",
    ),
    "air_passenger_regime": (
        "rtm_core.air_passenger_regime",
        "AIR_PASSENGER_REGIME_VERSION",
    ),
    "air_baggage_liability_regime": (
        "rtm_core.air_baggage_liability_regime",
        "AIR_BAGGAGE_LIABILITY_REGIME_VERSION",
    ),
    "accommodation_consumer_regime": (
        "rtm_core.accommodation_consumer_regime",
        "ACCOMMODATION_CONSUMER_REGIME_VERSION",
    ),
    "travel_flight_cancelled_specialist": (
        "rtm_core.travel_flight_cancelled_specialist",
        "TRAVEL_FLIGHT_CANCELLED_SPECIALIST_VERSION",
    ),
    "travel_flight_delay_specialist": (
        "rtm_core.travel_flight_delay_specialist",
        "TRAVEL_FLIGHT_DELAY_SPECIALIST_VERSION",
    ),
    "travel_denied_boarding_specialist": (
        "rtm_core.travel_denied_boarding_specialist",
        "TRAVEL_DENIED_BOARDING_SPECIALIST_VERSION",
    ),
    "travel_baggage_specialist": (
        "rtm_core.travel_baggage_specialist",
        "TRAVEL_BAGGAGE_SPECIALIST_VERSION",
    ),
    "travel_baggage_adapter": (
        "rtm_core.travel_baggage_adapter",
        "TRAVEL_BAGGAGE_ADAPTER_VERSION",
    ),
    "travel_hotel_specialist": (
        "rtm_core.travel_hotel_specialist",
        "TRAVEL_HOTEL_SPECIALIST_VERSION",
    ),
    "travel_specialist_registry": (
        "rtm_core.travel_specialist_registry",
        "TRAVEL_SPECIALIST_REGISTRY_VERSION",
    ),
    "claims_telecommunications_specialist": (
        "rtm_core.claims_telecommunications_specialist",
        "CLAIMS_TELECOMMUNICATIONS_SPECIALIST_VERSION",
    ),
    "claims_specialist_registry": (
        "rtm_core.claims_specialist_registry",
        "CLAIMS_SPECIALIST_REGISTRY_VERSION",
    ),
    "traffic_specialist_adapters": (
        "rtm_core.traffic_specialist_adapters",
        "TRAFFIC_SPECIALIST_ADAPTERS_VERSION",
    ),
    "ops_workspace": ("rtm_core.workspace_service_v2", "WORKSPACE_VERSION"),
    "ops_workspace_policy": (
        "rtm_core.workspace_policy_ext",
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
            "domain_catalog": "rtm_domain_catalog_v1_0",
            "review_readiness": REVIEW_READINESS_VERSION,
            "authority_store": "rtm_authority_store_v1_0",
            "extraction_route_policy": "rtm_extraction_route_policy_v1_0",
            "safe_reanalysis_execution": "rtm_safe_reanalysis_execution_v1_0",
            "reanalysis_adapter": "rtm_reanalysis_to_validated_facts_v1_0",
            "document_scope": "rtm_document_scope_v1_0",
            "document_fact_catalog": "rtm_document_fact_catalog_v1_1",
            "document_extraction_packet": "rtm_document_extraction_packet_v1_0",
            "document_normalization": "rtm_document_normalization_v1_0",
            "document_facts_gateway": "rtm_document_facts_gateway_v1_0",
            "service_document_extractor": "rtm_service_document_extractor_v1_0",
            "openai_document_provider": (
                "rtm_openai_responses_document_provider_v1_0"
            ),
            "deterministic_document_reader": (
                "rtm_deterministic_document_reader_v1_0"
            ),
            "document_extraction_store": "rtm_document_extraction_store_v1_0",
            "document_extraction_router": "rtm_document_extraction_router_v1_0",
            "document_extraction_schema": "rtm_document_extraction_schema_v1_0",
            "synthetic_staging_validation": (
                "rtm_synthetic_staging_validation_v1_0"
            ),
            "synthetic_staging_fixture_set": "rtm_synthetic_fixture_set_v1_0",
            "family_dispatch": "rtm_family_dispatch_v1_0",
            "family_core": "rtm_family_core_v1_0",
            "cross_service_family": "rtm_cross_service_family_v1_0",
            "first_direction": "rtm_first_direction_projection_v1_0",
            "specialist_registry": "rtm_specialist_registry_v1_4",
            "specialist_dispatch": "rtm_specialist_dispatch_v1_3",
            "cross_service_specialist_support": (
                "rtm_cross_service_specialist_support_v1_0"
            ),
            "debt_unpaid_invoice_specialist": (
                "rtm_debt_unpaid_invoice_specialist_v1_0"
            ),
            "debt_credit_file_specialist": "rtm_debt_credit_file_specialist_v1_0",
            "debt_specialist_registry": "rtm_debt_specialist_registry_v1_0",
            "administration_enforcement_specialist": (
                "rtm_administration_enforcement_specialist_v1_0"
            ),
            "administration_enforcement_adapter": (
                "rtm_administration_enforcement_adapter_v1_0"
            ),
            "air_passenger_regime": "rtm_air_passenger_regime_v1_0",
            "air_baggage_liability_regime": (
                "rtm_air_baggage_liability_regime_v1_0"
            ),
            "accommodation_consumer_regime": (
                "rtm_accommodation_consumer_regime_v1_0"
            ),
            "travel_flight_cancelled_specialist": (
                "rtm_travel_flight_cancelled_specialist_v1_0"
            ),
            "travel_flight_delay_specialist": (
                "rtm_travel_flight_delay_specialist_v1_0"
            ),
            "travel_denied_boarding_specialist": (
                "rtm_travel_denied_boarding_specialist_v1_0"
            ),
            "travel_baggage_specialist": "rtm_travel_baggage_specialist_v1_0",
            "travel_baggage_adapter": "rtm_travel_baggage_adapter_v1_0",
            "travel_hotel_specialist": "rtm_travel_hotel_specialist_v1_0",
            "travel_specialist_registry": "rtm_travel_specialist_registry_v1_2",
            "claims_telecommunications_specialist": (
                "rtm_claims_telecommunications_specialist_v1_0"
            ),
            "claims_specialist_registry": "rtm_claims_specialist_registry_v1_0",
            "traffic_specialist_adapters": "rtm_traffic_specialist_adapters_v1_0",
            "ops_workspace": "rtm_ops_workspace_v1_2",
            "ops_workspace_policy": "rtm_ops_workspace_policy_v1_3",
            "legal_preview_store": "rtm_legal_preview_store_v1_1",
            "authority_schema": "rtm_core_authority_schema_v1_2",
            "generation_gateway": "rtm_generate_gateway_v1_0",
            "submission_automation": "rtm_submission_automation_v1_0",
        },
        "components": components,
        "runtime": {"python": platform.python_version()},
    }
