"""Extensión transversal de la política del espacio de trabajo OPS.

Conserva la progresión validada V1 y añade dos reglas: la ejecución segura de
Reanalysis en Tráfico y una parada explícita de orientación cuando la familia
está resuelta pero el especialista aún no dispone de adaptador LegalPreview.
"""

from __future__ import annotations

from typing import Any

from rtm_core.service_catalog import canonical_department
from rtm_core.workspace_policy import determine_workspace_stage as _determine_v1


WORKSPACE_POLICY_VERSION = "rtm_ops_workspace_policy_v1_1"


def _action(
    code: str,
    label: str,
    *,
    method: str | None = None,
    endpoint: str | None = None,
    requires_reason: bool = False,
    requires_confirmation: bool = False,
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "method": method,
        "endpoint": endpoint,
        "requires_reason": requires_reason,
        "requires_confirmation": requires_confirmation,
    }


def determine_workspace_stage(
    *,
    case_id: str,
    case_status: str,
    payment_status: str,
    authorized: bool,
    readiness_ready: bool,
    reanalysis_available: bool,
    latest_facts: Any = None,
    latest_family: Any = None,
    latest_preview: Any = None,
    latest_resource: Any = None,
    service: str = "traffic",
    specialist_available: bool = True,
) -> dict[str, Any]:
    result = _determine_v1(
        case_id=case_id,
        case_status=case_status,
        payment_status=payment_status,
        authorized=authorized,
        readiness_ready=readiness_ready,
        reanalysis_available=reanalysis_available,
        latest_facts=latest_facts,
        latest_family=latest_family,
        latest_preview=latest_preview,
        latest_resource=latest_resource,
    )
    result = dict(result)
    result["policy_version"] = WORKSPACE_POLICY_VERSION
    base = f"/ops/core/cases/{case_id}"
    department = canonical_department(service)

    if result.get("stage") == "reanalysis_required":
        if department == "traffic":
            result["primary_action"] = "run_safe_reanalysis"
            result["actions"] = [
                _action(
                    "run_safe_reanalysis",
                    "Ejecutar Reanalysis seguro sobre los documentos originales",
                    method="POST",
                    endpoint=f"{base}/reanalysis/run",
                    requires_confirmation=True,
                )
            ]
            return result

        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "service_fact_extraction_pending",
            "primary_action": "review_service_documents",
            "actions": [
                _action(
                    "review_service_documents",
                    "Revisar los documentos y preparar los hechos del satélite",
                    method="GET",
                    endpoint=f"{base}/workspace",
                ),
                _action(
                    "create_service_facts_draft",
                    "Crear un borrador estructurado de hechos, sin congelarlo automáticamente",
                    method="POST",
                    endpoint=f"{base}/validated-facts",
                    requires_confirmation=True,
                ),
            ],
        }

    if result.get("stage") == "legal_preview_pending" and not specialist_available:
        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "initial_direction_review",
            "primary_action": "review_first_direction",
            "actions": [
                _action(
                    "review_first_direction",
                    "Revisar el primer rumbo, los hechos y los bloqueos del especialista",
                    method="GET",
                    endpoint=f"{base}/workspace",
                ),
                _action(
                    "invalidate_family_if_needed",
                    "Invalidar la familia cuando el encuadre no sea correcto",
                    method="POST",
                    endpoint=(
                        f"{base}/family-resolutions/"
                        "{family_resolution_id}/invalidate"
                    ),
                    requires_reason=True,
                    requires_confirmation=True,
                ),
            ],
        }

    return result
