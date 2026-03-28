from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Optional


@dataclass
class RegenerateResult:
    case_id: str
    family_ai_original: Optional[str]
    family_corrected: str
    status: str
    document_ids: list[str]


def regenerate_case_with_forced_family(
    *,
    db,
    case,
    forced_family: str,
    reason: str | None,
    actor: str = "operator",
    regenerate_pdf: bool = True,
) -> RegenerateResult:
    """
    Lógica principal:
    - guarda override de familia
    - regenera recurso con familia forzada
    - opcionalmente regenera DOCX/PDF
    - registra eventos de auditoría

    TODO:
    - adaptar `generate_resource_text(...)`
    - adaptar `save_generated_documents(...)`
    - adaptar `create_case_event(...)`
    """

    old_family = getattr(case, "family", None)
    current_text = getattr(case, "resource_text_current", None)

    if getattr(case, "family_ai_original", None) is None:
        case.family_ai_original = old_family

    if getattr(case, "resource_text_ai_original", None) is None and current_text:
        case.resource_text_ai_original = current_text

    case.family_corrected = forced_family
    case.override_reason = reason
    case.family = forced_family
    case.last_regenerated_at = datetime.now(UTC)

    new_text, generation_meta = generate_resource_text(
        case=case,
        forced_family=forced_family,
        operator_reason=reason,
    )

    case.resource_text_current = new_text
    case.status = "EDITED"

    create_case_event(
        db=db,
        case_id=str(case.id),
        event_type="operator_override_family",
        payload={
            "from_family": old_family,
            "to_family": forced_family,
            "reason": reason,
            "actor": actor,
        },
    )

    document_ids: list[str] = []

    if regenerate_pdf:
        document_ids = save_generated_documents(
            db=db,
            case=case,
            text=new_text,
            family=forced_family,
            generation_meta=generation_meta,
        )

    create_case_event(
        db=db,
        case_id=str(case.id),
        event_type="resource_regenerated",
        payload={
            "family": forced_family,
            "reason": reason,
            "actor": actor,
            "document_ids": document_ids,
        },
    )

    db.add(case)
    db.commit()
    db.refresh(case)

    return RegenerateResult(
        case_id=str(case.id),
        family_ai_original=getattr(case, "family_ai_original", None),
        family_corrected=forced_family,
        status=case.status,
        document_ids=document_ids,
    )


def generate_resource_text(*, case, forced_family: str, operator_reason: str | None):
    """
    TODO integrar con tu generador real.
    Esta implementación es un placeholder seguro para pruebas.
    """
    fact = getattr(case, "facts_summary", "") or getattr(case, "hecho", "") or ""
    new_text = f"""RECURSO REGENERADO

Familia forzada por operador: {forced_family}
Motivo del override: {operator_reason or "sin indicar"}

Resumen del hecho:
{fact}

SOLICITUD
Se solicita la revisión del expediente conforme a la familia jurídica correcta indicada por operador.
"""
    meta = {"forced_family": forced_family, "operator_reason": operator_reason}
    return new_text, meta


def save_generated_documents(*, db, case, text: str, family: str, generation_meta: dict) -> list[str]:
    """
    TODO integrar con tu pipeline real de DOCX/PDF y documents.
    Devuelve IDs de documentos creados.
    """
    return []


def create_case_event(*, db, case_id: str, event_type: str, payload: dict):
    """
    TODO integrar con tu helper real de eventos.
    """
    return None