"""CORE parking preparation; a human must establish the actual grounds."""
from fastapi import HTTPException

from rtm_core.authority_repository import model_digest
from rtm_core.contracts import Deadline, DocumentUse, LegalPreview, MissingItem, MissingItemSeverity, ResolutionStatus
from rtm_core.traffic_parking_preparation import build_parking_preparation
from rtm_core.traffic_specialist_adapters import _ensure_authority

PARKING_SPECIALIST_VERSION = "rtm_traffic_parking_specialist_v1_0"


def build_parking_preview(facts_record, family_record):
    _ensure_authority(facts_record, family_record, "estacionamiento", "traffic.estacionamiento")
    facts, resolution = facts_record.facts, family_record.resolution
    if (facts_record.case_id != facts.case_id or family_record.case_id != facts.case_id
            or resolution.case_id != facts.case_id or facts.service != "traffic" or resolution.service != "traffic"
            or not facts.frozen or not resolution.locked or resolution.status != ResolutionStatus.RESOLVED
            or resolution.facts_version != facts.version
            or facts_record.payload_sha256 != model_digest(facts)
            or family_record.payload_sha256 != model_digest(resolution)):
        raise HTTPException(409, "La cadena documental de estacionamiento no es verificable")
    guide = build_parking_preparation(facts)
    if guide["status"] != "review_required" or not guide["reported_fact"]:
        raise HTTPException(409, "Revisa la familia y los hechos documentales antes de preparar estacionamiento")
    rows = [guide["reported_fact"], *(field for check in guide["checks"] for field in check["fields"])]
    used = {row["key"]: row for row in rows if row["value"] is not None}
    pages = {}
    for row in used.values():
        for source in row["sources"]:
            pages.setdefault(source["document_id"], set()).add(source["page_index"])
    return LegalPreview(
        case_id=facts.case_id, service="traffic", family="estacionamiento", specialist="traffic.estacionamiento",
        facts_version=facts.version, family_resolution_version=resolution.version,
        validated_facts_summary=[f"{row['label']}: {row['value']}" for row in used.values()],
        source_fact_keys=list(used), problem_summary=str(guide["reported_fact"]["value"]),
        client_goal="Determinar una actuación fundada tras revisar el expediente.",
        primary_strategy=guide["message"],
        secondary_strategies=[check["instruction"] for check in guide["checks"]],
        requested_outcomes=[], legal_arguments=[], additional_requests=[],
        documents_used=[DocumentUse(document_id=doc, label="Documento contrastado", status="partially_read",
                                    pages_used=sorted(indices)) for doc, indices in sorted(pages.items())],
        missing_items=[MissingItem(code="parking_" + check["id"], description=check["instruction"],
                                   severity=MissingItemSeverity.BLOCKING) for check in guide["checks"]],
        deadlines=[Deadline(label="Plazo para la actuación procedente", calculation_status="unresolved",
                            notes=["La preparación no confirma el cómputo ni la fase procesal."])],
        risks=[guide["procedure_note"], guide["payment_note"],
               "Normativa consultada el 21/09/2026: verificar redacción aplicable a los hechos y ordenanza local.",
               *(reference["title"] + ": " + reference["url"] for reference in guide["references"])],
        document_type="ACTUACIÓN PENDIENTE DE REVISIÓN JURÍDICA", subject="Preparación de estacionamiento",
        created_by_component=PARKING_SPECIALIST_VERSION,
    )
