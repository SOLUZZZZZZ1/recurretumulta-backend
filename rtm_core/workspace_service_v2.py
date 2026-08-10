"""Vista OPS V1.2 con extracción documental y primer rumbo transversal.

Envuelve la vista V1 ya validada, añade la extracción documental persistida para
satélites no tráfico y corrige la siguiente acción según la autoridad activa y
la disponibilidad real del especialista.
"""

from __future__ import annotations

from typing import Any, Mapping

from rtm_core.document_extraction_repository import list_document_extractions
from rtm_core.first_direction import build_first_direction
from rtm_core.specialist_dispatch import registered_specialists
from rtm_core.workspace_policy_ext import determine_workspace_stage
from rtm_core.workspace_service import build_case_workspace as build_case_workspace_v1


WORKSPACE_VERSION = "rtm_ops_workspace_v1_2"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _latest(payload: Mapping[str, Any], authority_key: str) -> Any:
    authority = payload.get("authority", {})
    if not isinstance(authority, Mapping):
        return None
    section = authority.get(authority_key, {})
    if not isinstance(section, Mapping):
        return None
    return section.get("latest_active")


def _specialist(latest_family: Any) -> str:
    if not isinstance(latest_family, Mapping):
        return ""
    resolution = latest_family.get("resolution", {})
    if not isinstance(resolution, Mapping):
        return ""
    return str(resolution.get("specialist") or "").strip()


def _active_extraction(records: list[Any]) -> Any:
    for record in records:
        if getattr(record, "invalidated_at", None) is None:
            return record
    return None


def build_case_workspace(conn, case_id: str) -> dict[str, Any]:
    payload = build_case_workspace_v1(conn, case_id)
    case_payload = _mapping(payload.get("case"))
    readiness = _mapping(payload.get("readiness"))
    reanalysis = _mapping(payload.get("reanalysis"))

    latest_facts = _latest(payload, "validated_facts")
    latest_family = _latest(payload, "family_resolution")
    latest_preview = _latest(payload, "legal_preview")
    latest_resource = _latest(payload, "generated_resource")

    extractions = list_document_extractions(conn, case_id)
    latest_extraction = _active_extraction(extractions)
    extraction_id = (
        str(getattr(latest_extraction, "id", "") or "")
        if latest_extraction
        else ""
    )

    specialist = _specialist(latest_family)
    specialists = registered_specialists()
    specialist_available = bool(specialist and specialist in set(specialists))
    service = (
        case_payload.get("department")
        or case_payload.get("category")
        or case_payload.get("case_type")
        or "other"
    )

    next_step = determine_workspace_stage(
        case_id=case_id,
        case_status=str(case_payload.get("status") or ""),
        payment_status=str(case_payload.get("payment_status") or ""),
        authorized=bool(case_payload.get("authorized")),
        readiness_ready=bool(readiness.get("ready")),
        reanalysis_available=bool(reanalysis.get("available")),
        latest_facts=latest_facts,
        latest_family=latest_family,
        latest_preview=latest_preview,
        latest_resource=latest_resource,
        service=str(service),
        specialist_available=specialist_available,
        document_extraction_available=latest_extraction is not None,
        document_extraction_id=extraction_id,
    )

    first_direction = build_first_direction(
        case_id=case_id,
        case_payload=case_payload,
        readiness=readiness,
        latest_facts=latest_facts,
        latest_family=latest_family,
        latest_preview=latest_preview,
        next_step=next_step,
        registered_specialists=specialists,
    )

    payload["workspace_version"] = WORKSPACE_VERSION
    payload["next_step"] = next_step
    payload["first_direction"] = first_direction.model_dump(mode="json")
    payload["document_extraction"] = {
        "latest_active": (
            latest_extraction.model_dump(mode="json")
            if latest_extraction
            else None
        ),
        "versions": [
            record.model_dump(mode="json")
            for record in extractions
        ],
    }
    payload["capabilities"] = {
        "registered_specialists": list(specialists),
        "resolved_specialist": specialist or None,
        "resolved_specialist_available": specialist_available,
        "document_extraction_available": latest_extraction is not None,
        "document_extraction_id": extraction_id or None,
        "facts_require_explicit_promotion": True,
        "generate_requires_frozen_legal_preview": True,
    }
    return payload
