"""Vista única y no mutante del expediente para OPS.

El frontend no debe reconstruir la autoridad leyendo eventos legacy. Este
router reúne expediente, documentos, hechos, familia, previa y recurso y devuelve
la siguiente transición permitida por RTM CORE.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import text

from database import get_engine
from rtm_core.authority_repository import (
    list_family_resolutions,
    list_validated_facts,
)
from rtm_core.generation_gateway import list_generated_resources
from rtm_core.preview_repository import list_previews
from rtm_core.readiness import evaluate_review_readiness
from rtm_core.reanalysis_adapter import (
    REANALYSIS_ADAPTER_VERSION,
    build_validated_facts_from_reanalysis,
    load_latest_reanalysis_snapshot,
)
from rtm_core.security import require_operator_token


WORKSPACE_VERSION = "rtm_ops_workspace_v1_0"
router = APIRouter(prefix="/ops/core/cases", tags=["rtm-core-workspace"])


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
    """Decide la siguiente transición sin modificar ninguna autoridad."""

    base = f"/ops/core/cases/{case_id}"
    status = str(case_status or "").strip().lower()
    payment = str(payment_status or "").strip().lower()

    if payment != "paid":
        if readiness_ready:
            return {
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
    resource_invalidated = resource_status == "invalidated"
    if preview_status == "frozen" and (not latest_resource or resource_invalidated):
        return {
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

    if latest_resource and resource_status == "final_ready" and not _value(latest_resource, "approved_at"):
        return {
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

    if status == "submitted" or status.startswith("presentado"):
        return {
            "stage": "submitted_followup",
            "primary_action": "monitor_followup",
            "actions": [
                _action(
                    "monitor_followup",
                    "Controlar plazos, respuestas y seguimiento del expediente",
                )
            ],
        }

    return {
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


def _active(records: list[Any], *, invalidated_field: str = "invalidated_at") -> Any:
    for record in records:
        if _value(record, invalidated_field) is None:
            return record
    return None


def _active_preview(records: list[Any]) -> Any:
    for record in records:
        if str(_enum_value(_value(record, "status", ""))) != "invalidated":
            return record
    return None


def _active_resource(records: list[Any]) -> Any:
    for record in records:
        if str(_value(record, "status", "")) != "invalidated":
            return record
    return None


def _serialize(record: Any) -> Any:
    if record is None:
        return None
    if hasattr(record, "model_dump"):
        return record.model_dump(mode="json")
    return record


def _case_row(conn, case_id: str) -> dict[str, Any]:
    row = conn.execute(
        text("SELECT to_jsonb(c) FROM cases c WHERE c.id=:case_id"),
        {"case_id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    payload = row[0] if isinstance(row[0], dict) else {}
    return payload


def _document_rows(conn, case_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT CAST(id AS TEXT), COALESCE(kind,''), COALESCE(mime,''),
                   COALESCE(size_bytes,0), created_at
            FROM documents
            WHERE case_id=:case_id
            ORDER BY created_at ASC, id ASC
            """
        ),
        {"case_id": case_id},
    ).fetchall()
    return [
        {
            "id": str(row[0]),
            "kind": str(row[1] or ""),
            "mime": str(row[2] or ""),
            "size_bytes": int(row[3] or 0),
            "created_at": row[4].isoformat() if row[4] else None,
            "download_endpoint": f"/ops/documents/{row[0]}/download",
        }
        for row in rows
    ]


def _timeline(conn, case_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT type, created_at
            FROM events
            WHERE case_id=:case_id
            ORDER BY created_at DESC, id DESC
            LIMIT 50
            """
        ),
        {"case_id": case_id},
    ).fetchall()
    return [
        {
            "type": str(row[0] or ""),
            "created_at": row[1].isoformat() if row[1] else None,
        }
        for row in rows
    ]


def _reanalysis_projection(conn, case_id: str) -> dict[str, Any]:
    try:
        wrapper, event = load_latest_reanalysis_snapshot(conn, case_id)
        result = build_validated_facts_from_reanalysis(
            case_id=case_id,
            wrapper=wrapper,
            event_payload=event,
        )
        return {
            "available": True,
            "adapter_version": REANALYSIS_ADAPTER_VERSION,
            "accepted_fields": result.accepted_fields,
            "unresolved_fields": result.unresolved_fields,
            "conflicted_fields": result.conflicted_fields,
            "ignored_fields": result.ignored_fields,
            "warnings": result.warnings,
        }
    except HTTPException as exc:
        if exc.status_code == 404:
            return {
                "available": False,
                "adapter_version": REANALYSIS_ADAPTER_VERSION,
                "status": "not_found",
                "detail": exc.detail,
            }
        return {
            "available": False,
            "adapter_version": REANALYSIS_ADAPTER_VERSION,
            "status": "blocked",
            "detail": exc.detail,
        }


def _identity_projection(case_payload: Mapping[str, Any]) -> dict[str, Any]:
    interested = case_payload.get("interested_data")
    if not isinstance(interested, dict):
        interested = {}
    return {
        "full_name": interested.get("full_name") or interested.get("name") or case_payload.get("contact_name"),
        "dni_nie": interested.get("dni_nie") or interested.get("dni") or interested.get("identity_number"),
        "address": interested.get("domicilio_notif") or interested.get("domicilio") or interested.get("address"),
        "email": interested.get("email") or case_payload.get("contact_email"),
        "phone": interested.get("telefono") or interested.get("phone"),
        "matricula": interested.get("matricula") or interested.get("plate"),
    }


@router.get("/{case_id}/workspace")
def get_case_workspace(
    case_id: str,
    x_operator_token: Optional[str] = Header(default=None, alias="X-Operator-Token"),
):
    require_operator_token(x_operator_token)
    engine = get_engine()

    with engine.begin() as conn:
        case_payload = _case_row(conn, case_id)
        documents = _document_rows(conn, case_id)
        interested = case_payload.get("interested_data")
        if not isinstance(interested, dict):
            interested = {}

        readiness = evaluate_review_readiness(
            case_id=case_id,
            interested_data=interested,
            authorized=bool(case_payload.get("authorized")),
            document_kinds=[item["kind"] for item in documents],
            department=case_payload.get("department"),
            case_type=case_payload.get("case_type"),
            category=case_payload.get("category"),
            source_module=case_payload.get("source_module"),
            contact_email=case_payload.get("contact_email"),
            customer_comment=case_payload.get("customer_comment"),
        )
        facts = list_validated_facts(conn, case_id)
        families = list_family_resolutions(conn, case_id)
        previews = list_previews(conn, case_id)
        resources = list_generated_resources(conn, case_id)
        reanalysis = _reanalysis_projection(conn, case_id)
        timeline = _timeline(conn, case_id)

    latest_facts = _active(facts)
    latest_family = _active(families)
    latest_preview = _active_preview(previews)
    latest_resource = _active_resource(resources)
    next_step = determine_workspace_stage(
        case_id=case_id,
        case_status=str(case_payload.get("status") or ""),
        payment_status=str(case_payload.get("payment_status") or ""),
        authorized=bool(case_payload.get("authorized")),
        readiness_ready=readiness.ready,
        reanalysis_available=bool(reanalysis.get("available")),
        latest_facts=latest_facts,
        latest_family=latest_family,
        latest_preview=latest_preview,
        latest_resource=latest_resource,
    )

    return {
        "ok": True,
        "workspace_version": WORKSPACE_VERSION,
        "case_id": case_id,
        "case": {
            "status": case_payload.get("status"),
            "payment_status": case_payload.get("payment_status"),
            "authorized": bool(case_payload.get("authorized")),
            "department": case_payload.get("department"),
            "case_type": case_payload.get("case_type"),
            "category": case_payload.get("category"),
            "source_module": case_payload.get("source_module"),
            "organismo": case_payload.get("organismo"),
            "expediente_ref": case_payload.get("expediente_ref"),
            "identity": _identity_projection(case_payload),
        },
        "readiness": readiness.model_dump(mode="json"),
        "documents": documents,
        "reanalysis": reanalysis,
        "authority": {
            "validated_facts": {
                "latest_active": _serialize(latest_facts),
                "versions": [_serialize(item) for item in facts],
            },
            "family_resolution": {
                "latest_active": _serialize(latest_family),
                "versions": [_serialize(item) for item in families],
            },
            "legal_preview": {
                "latest_active": _serialize(latest_preview),
                "versions": [_serialize(item) for item in previews],
            },
            "generated_resource": {
                "latest_active": _serialize(latest_resource),
                "versions": [_serialize(item) for item in resources],
            },
        },
        "next_step": next_step,
        "timeline": timeline,
    }
