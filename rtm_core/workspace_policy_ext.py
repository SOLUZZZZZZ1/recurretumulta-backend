"""Extensión transversal de la política del espacio de trabajo OPS.

Conserva la progresión validada V1 y añade cuatro reglas: Reanalysis seguro para
Tráfico, extractor documental persistido para el resto de satélites, promoción
expresa a hechos y parada de orientación cuando todavía no existe especialista
LegalPreview.
"""

from __future__ import annotations

from typing import Any

from rtm_core.service_catalog import canonical_department
from rtm_core.workspace_policy import determine_workspace_stage as _determine_v1


WORKSPACE_POLICY_VERSION = "rtm_ops_workspace_policy_v1_3"


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
    document_extraction_available: bool = False,
    document_extraction_id: str = "",
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
    stage = str(result.get("stage") or "")

    if stage in {"reanalysis_required", "validated_facts_pending"}:
        if department == "traffic":
            if stage == "reanalysis_required":
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

        extraction_id = str(document_extraction_id or "").strip()
        if document_extraction_available and extraction_id:
            return {
                "policy_version": WORKSPACE_POLICY_VERSION,
                "stage": "service_facts_preview_pending",
                "primary_action": "preview_extracted_document_facts",
                "actions": [
                    _action(
                        "inspect_document_extraction",
                        "Revisar la extracción documental y su trazabilidad",
                        method="GET",
                        endpoint=(
                            f"{base}/document-extractions/{extraction_id}"
                        ),
                    ),
                    _action(
                        "preview_extracted_document_facts",
                        "Previsualizar los hechos normalizados sin guardarlos",
                        method="POST",
                        endpoint=(
                            f"{base}/document-extractions/{extraction_id}/"
                            "facts-preview"
                        ),
                    ),
                    _action(
                        "create_extracted_document_facts_draft",
                        "Guardar el borrador versionado de hechos",
                        method="POST",
                        endpoint=(
                            f"{base}/document-extractions/{extraction_id}/"
                            "facts-draft"
                        ),
                        requires_confirmation=True,
                    ),
                    _action(
                        "invalidate_document_extraction",
                        "Invalidar la extracción y volver a leer los documentos",
                        method="POST",
                        endpoint=(
                            f"{base}/document-extractions/{extraction_id}/"
                            "invalidate"
                        ),
                        requires_reason=True,
                        requires_confirmation=True,
                    ),
                ],
            }

        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "service_fact_extraction_pending",
            "primary_action": "run_service_document_extraction",
            "actions": [
                _action(
                    "inspect_document_fact_catalog",
                    "Consultar los campos documentales registrados para este satélite",
                    method="GET",
                    endpoint=f"/ops/core/document-facts/catalog/{department}",
                ),
                _action(
                    "run_service_document_extraction",
                    "Leer los documentos originales y preparar una extracción estructurada",
                    method="POST",
                    endpoint=f"{base}/document-extractions/run",
                    requires_confirmation=True,
                ),
                _action(
                    "preview_document_facts",
                    "Normalizar un paquete documental aportado por OPS sin guardarlo",
                    method="POST",
                    endpoint=f"{base}/document-facts/preview",
                ),
                _action(
                    "create_document_facts_draft",
                    "Guardar un paquete documental revisado por OPS",
                    method="POST",
                    endpoint=f"{base}/document-facts/draft",
                    requires_confirmation=True,
                ),
            ],
        }

    if stage == "legal_preview_pending" and not specialist_available:
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
