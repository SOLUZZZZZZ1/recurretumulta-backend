"""Construcción no mutante de la vista única de expediente para OPS."""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy import text

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
from rtm_core.workspace_policy import determine_workspace_stage
from rtm_presenter_policy import (
    PresenterPolicyError,
    PresenterRuntimeDisabled,
    load_presenter_runtime_configuration,
)


WORKSPACE_VERSION = "rtm_ops_workspace_v1_0"


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _serialize(record: Any) -> Any:
    if record is None:
        return None
    if hasattr(record, "model_dump"):
        return record.model_dump(mode="json")
    return record


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


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        text("SELECT to_regclass(:table_name)"),
        {"table_name": f"public.{table_name}"},
    ).fetchone()
    return bool(row and row[0])


def _presenter_available(case_payload: Mapping[str, Any]) -> bool:
    if case_payload.get("test_mode") is not True:
        return False
    try:
        load_presenter_runtime_configuration(require_enabled=True)
    except (PresenterRuntimeDisabled, PresenterPolicyError):
        return False
    return True


def _case_row(conn, case_id: str) -> dict[str, Any]:
    row = conn.execute(
        text("SELECT to_jsonb(c) FROM cases c WHERE c.id=:case_id"),
        {"case_id": case_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expediente no encontrado")
    return row[0] if isinstance(row[0], dict) else {}


def _document_rows(conn, case_id: str) -> list[dict[str, Any]]:
    if not _table_exists(conn, "documents"):
        return []
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
            "custody": "rtm_internal_only",
            "operator_export_allowed": False,
        }
        for row in rows
    ]


def _timeline(conn, case_id: str) -> list[dict[str, Any]]:
    if not _table_exists(conn, "events"):
        return []
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


def _identity_projection(case_payload: Mapping[str, Any]) -> dict[str, Any]:
    interested = case_payload.get("interested_data")
    if not isinstance(interested, dict):
        interested = {}
    return {
        "full_name": (
            interested.get("full_name")
            or interested.get("name")
            or case_payload.get("contact_name")
        ),
        "dni_nie": (
            interested.get("dni_nie")
            or interested.get("dni")
            or interested.get("identity_number")
        ),
        "address": (
            interested.get("domicilio_notif")
            or interested.get("domicilio")
            or interested.get("address")
        ),
        "email": interested.get("email") or case_payload.get("contact_email"),
        "phone": interested.get("telefono") or interested.get("phone"),
        "matricula": interested.get("matricula") or interested.get("plate"),
    }


def _reanalysis_projection(conn, case_id: str) -> dict[str, Any]:
    if not _table_exists(conn, "extractions"):
        return {
            "available": False,
            "adapter_version": REANALYSIS_ADAPTER_VERSION,
            "status": "schema_unavailable",
            "detail": "La tabla de extracciones no existe todavía.",
        }
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
        return {
            "available": False,
            "adapter_version": REANALYSIS_ADAPTER_VERSION,
            "status": "not_found" if exc.status_code == 404 else "blocked",
            "detail": exc.detail,
        }


def _authority_records(conn, case_id: str) -> tuple[list[Any], list[Any], list[Any], list[Any]]:
    facts = (
        list_validated_facts(conn, case_id)
        if _table_exists(conn, "rtm_validated_facts")
        else []
    )
    families = (
        list_family_resolutions(conn, case_id)
        if _table_exists(conn, "rtm_family_resolutions")
        else []
    )
    previews = (
        list_previews(conn, case_id)
        if _table_exists(conn, "rtm_legal_previews")
        else []
    )
    resources = (
        list_generated_resources(conn, case_id)
        if _table_exists(conn, "rtm_generated_resources")
        else []
    )
    return facts, families, previews, resources


def build_case_workspace(conn, case_id: str) -> dict[str, Any]:
    """Reúne la cadena completa sin escribir hechos, estados ni eventos."""

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
    facts, families, previews, resources = _authority_records(conn, case_id)
    reanalysis = _reanalysis_projection(conn, case_id)

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
        "actions": {
            "presenter_available": _presenter_available(case_payload),
        },
        "timeline": _timeline(conn, case_id),
    }
