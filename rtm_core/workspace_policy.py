"""Política pura de progresión del expediente en el espacio de trabajo OPS.

No lee base de datos, no muta estados y no interpreta hechos. Recibe el estado
ya persistido de cada autoridad y devuelve una única etapa y las actuaciones
permitidas. Los estados presentados o finales tienen prioridad absoluta para
impedir regresiones a Generate o presentación.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


WORKSPACE_POLICY_VERSION = "rtm_ops_workspace_policy_v1_0"

_SUBMITTED_STATUSES = {
    "submitted",
    "presentado_manual_ayuntamiento",
    "presentado_auto_dgt",
    "presentado_auto_registro",
}
_FINAL_STATUSES = {
    "closed",
    "archived",
    "resolved",
    "estimado",
    "desestimado",
}


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _nested_value(obj: Any, parent: str, name: str, default: Any = None) -> Any:
    return _value(_value(obj, parent, None), name, default)


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _action(
    code: str,
    label: str,
    *,
    method: Optional[str] = None,
    endpoint: Optional[str] = None,
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
) -> dict[str, Any]:
    """Devuelve la siguiente transición autorizada por RTM CORE."""

    base = f"/ops/core/cases/{case_id}"
    status = str(case_status or "").strip().lower()
    payment = str(payment_status or "").strip().lower()

    # Un expediente presentado o final nunca puede regresar a Generate ni a la
    # aprobación para presentar, aunque conserve recursos finales vinculados.
    if status in _SUBMITTED_STATUSES or status.startswith("presentado"):
        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "submitted_followup",
            "primary_action": "monitor_followup",
            "actions": [
                _action(
                    "monitor_followup",
                    "Controlar plazos, respuestas y seguimiento del expediente",
                )
            ],
        }

    if status in _FINAL_STATUSES:
        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "case_closed",
            "primary_action": "consult_case_history",
            "actions": [
                _action(
                    "consult_case_history",
                    "Consultar el historial cerrado del expediente",
                    method="GET",
                    endpoint=f"{base}/workspace",
                )
            ],
        }

    if payment != "paid":
        if readiness_ready:
            return {
                "policy_version": WORKSPACE_POLICY_VERSION,
                "stage": "study_payment_pending",
                "primary_action": "collect_study_payment",
                "actions": [
                    _action(
                        "collect_study_payment",
                        "Cobrar el estudio con el importe decidido por el backend",
                    )
                ],
            }
        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "intake_incomplete",
            "primary_action": "complete_intake",
            "actions": [
                _action(
                    "complete_intake",
                    "Completar datos, identidad, autorización firmada y documento principal",
                )
            ],
        }

    if not authorized:
        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "authorization_required",
            "primary_action": "complete_authorization",
            "actions": [
                _action(
                    "complete_authorization",
                    "Completar la autorización del expediente",
                )
            ],
        }

    facts_id = str(_value(latest_facts, "id", "") or "")
    facts_frozen = bool(_value(latest_facts, "frozen", False))
    facts_invalidated = _value(latest_facts, "invalidated_at") is not None
    if not latest_facts or facts_invalidated:
        if reanalysis_available:
            return {
                "policy_version": WORKSPACE_POLICY_VERSION,
                "stage": "validated_facts_pending",
                "primary_action": "preview_reanalysis_facts",
                "actions": [
                    _action(
                        "preview_reanalysis_facts",
                        "Revisar la transformación conservadora de Reanalysis",
                        method="GET",
                        endpoint=f"{base}/reanalysis/facts-preview",
                    ),
                    _action(
                        "create_validated_facts_draft",
                        "Guardar un borrador versionado de hechos",
                        method="POST",
                        endpoint=f"{base}/reanalysis/facts-draft",
                        requires_confirmation=True,
                    ),
                ],
            }
        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "reanalysis_required",
            "primary_action": "run_reanalysis",
            "actions": [
                _action(
                    "run_reanalysis",
                    "Ejecutar Reanalysis sobre los originales",
                    method="POST",
                    endpoint=f"/ops/cases/{case_id}/reanalyze",
                    requires_confirmation=True,
                )
            ],
        }

    if not facts_frozen:
        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "validated_facts_review",
            "primary_action": "review_validated_facts",
            "actions": [
                _action(
                    "review_validated_facts",
                    "Revisar procedencia, confianza, conflictos y campos no resueltos",
                    method="GET",
                    endpoint=f"{base}/validated-facts/{facts_id}",
                ),
                _action(
                    "freeze_validated_facts",
                    "Congelar esta versión de hechos",
                    method="POST",
                    endpoint=f"{base}/validated-facts/{facts_id}/freeze",
                    requires_confirmation=True,
                ),
                _action(
                    "invalidate_validated_facts",
                    "Invalidar esta versión y crear otra",
                    method="POST",
                    endpoint=f"{base}/validated-facts/{facts_id}/invalidate",
                    requires_reason=True,
                    requires_confirmation=True,
                ),
            ],
        }

    family_id = str(_value(latest_family, "id", "") or "")
    family_invalidated = _value(latest_family, "invalidated_at") is not None
    if not latest_family or family_invalidated:
        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "family_resolution_pending",
            "primary_action": "resolve_family",
            "actions": [
                _action(
                    "resolve_family",
                    "Resolver la familia desde los hechos congelados",
                    method="POST",
                    endpoint=f"{base}/resolve-family",
                    requires_confirmation=True,
                )
            ],
        }

    family_status = str(
        _enum_value(_nested_value(latest_family, "resolution", "status", "")) or ""
    )
    family_locked = bool(_value(latest_family, "locked", False))
    if family_status != "resolved":
        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "family_operator_review",
            "primary_action": "review_family_conflict",
            "actions": [
                _action(
                    "review_family_conflict",
                    "Revisar evidencia, conflictos y campos pendientes de la familia",
                    method="GET",
                    endpoint=f"{base}/family-resolutions/{family_id}",
                ),
                _action(
                    "invalidate_family_resolution",
                    "Invalidar la resolución para corregir hechos o emitir otra versión",
                    method="POST",
                    endpoint=f"{base}/family-resolutions/{family_id}/invalidate",
                    requires_reason=True,
                    requires_confirmation=True,
                ),
            ],
        }

    if not family_locked:
        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "family_lock_pending",
            "primary_action": "lock_family",
            "actions": [
                _action(
                    "lock_family",
                    "Bloquear la familia y el especialista resueltos",
                    method="POST",
                    endpoint=f"{base}/family-resolutions/{family_id}/lock",
                    requires_confirmation=True,
                )
            ],
        }

    preview_id = str(_value(latest_preview, "id", "") or "")
    preview_status = str(_enum_value(_value(latest_preview, "status", "")) or "")
    if not latest_preview or preview_status in {"changes_required", "invalidated"}:
        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "legal_preview_pending",
            "primary_action": "build_legal_preview",
            "actions": [
                _action(
                    "build_legal_preview",
                    "Ejecutar el especialista bloqueado y crear la Previa Jurídica",
                    method="POST",
                    endpoint=f"{base}/build-legal-preview",
                    requires_confirmation=True,
                )
            ],
        }

    if preview_status == "draft":
        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "legal_preview_draft",
            "primary_action": "submit_preview_review",
            "actions": [
                _action(
                    "review_legal_preview",
                    "Revisar hechos, estrategia, peticiones, riesgos y documentos",
                    method="GET",
                    endpoint=f"{base}/legal-previews/{preview_id}",
                ),
                _action(
                    "submit_preview_review",
                    "Enviar la Previa Jurídica a revisión OPS",
                    method="POST",
                    endpoint=f"{base}/legal-previews/{preview_id}/submit-review",
                    requires_confirmation=True,
                ),
            ],
        }

    if preview_status == "ops_review":
        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "legal_preview_ops_review",
            "primary_action": "approve_preview",
            "actions": [
                _action(
                    "approve_preview",
                    "Aprobar la Previa Jurídica",
                    method="POST",
                    endpoint=f"{base}/legal-previews/{preview_id}/approve",
                    requires_confirmation=True,
                ),
                _action(
                    "request_preview_changes",
                    "Solicitar una versión corregida",
                    method="POST",
                    endpoint=f"{base}/legal-previews/{preview_id}/request-changes",
                    requires_reason=True,
                ),
            ],
        }

    if preview_status == "approved":
        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "legal_preview_freeze_pending",
            "primary_action": "freeze_preview",
            "actions": [
                _action(
                    "freeze_preview",
                    "Congelar la Previa Jurídica aprobada",
                    method="POST",
                    endpoint=f"{base}/legal-previews/{preview_id}/freeze",
                    requires_confirmation=True,
                )
            ],
        }

    resource_id = str(_value(latest_resource, "id", "") or "")
    resource_status = str(_value(latest_resource, "status", "") or "")
    if preview_status == "frozen" and (
        not latest_resource or resource_status == "invalidated"
    ):
        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "generate_pending",
            "primary_action": "generate_resource",
            "actions": [
                _action(
                    "generate_resource",
                    "Generar DOCX y PDF exclusivamente desde la previa congelada",
                    method="POST",
                    endpoint=f"{base}/legal-previews/{preview_id}/generate",
                    requires_confirmation=True,
                )
            ],
        }

    if (
        latest_resource
        and resource_status == "final_ready"
        and not _value(latest_resource, "approved_at")
    ):
        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "resource_approval_pending",
            "primary_action": "approve_resource_submission",
            "actions": [
                _action(
                    "approve_resource_submission",
                    "Aprobar el documento final para presentación",
                    method="POST",
                    endpoint=f"{base}/generated-resources/{resource_id}/approve-submission",
                    requires_confirmation=True,
                )
            ],
        }

    if status == "ready_to_submit" or (
        latest_resource
        and resource_status == "final_ready"
        and _value(latest_resource, "approved_at") is not None
    ):
        return {
            "policy_version": WORKSPACE_POLICY_VERSION,
            "stage": "presentation_ready",
            "primary_action": "present_or_register_submission",
            "actions": [
                _action(
                    "present_or_register_submission",
                    "Presentar por el canal autorizado o registrar la presentación manual con justificante",
                    requires_confirmation=True,
                )
            ],
        }

    return {
        "policy_version": WORKSPACE_POLICY_VERSION,
        "stage": "operator_review",
        "primary_action": "inspect_workspace",
        "actions": [
            _action(
                "inspect_workspace",
                "Revisar la cadena de autoridad y el estado operativo",
                method="GET",
                endpoint=f"{base}/workspace",
            )
        ],
    }
