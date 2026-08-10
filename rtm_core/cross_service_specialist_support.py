"""Soporte común para especialistas jurídicos de satélites RTM no tráfico.

Este módulo no extrae documentos, no resuelve familias y no redacta por sí solo.
Centraliza invariantes que todos los especialistas transversales deben respetar:
hechos congelados, familia bloqueada, trazabilidad documental y argumentos
limitados a claves de hechos validados.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Optional, Sequence

from fastapi import HTTPException

from rtm_core.authority_repository import (
    FamilyResolutionRecord,
    ValidatedFactsRecord,
)
from rtm_core.contracts import (
    DocumentUse,
    FactStatus,
    LegalArgument,
    MissingItem,
    MissingItemSeverity,
)
from rtm_core.service_catalog import canonical_department


CROSS_SERVICE_SPECIALIST_SUPPORT_VERSION = (
    "rtm_cross_service_specialist_support_v1_0"
)

_FORBIDDEN_FACT_KEY_TOKENS = (
    "raw",
    "ocr",
    "vision",
    "prompt",
    "template",
    "classifier",
    "classification",
    "scoring",
    "strategy",
    "estrategia",
    "draft",
    "borrador",
    "generate",
)


def _safe_key(value: str) -> bool:
    key = str(value or "").strip().lower()
    return bool(key) and not any(token in key for token in _FORBIDDEN_FACT_KEY_TOKENS)


def validated_fact(
    record: ValidatedFactsRecord,
    *keys: str,
):
    """Devuelve el primer hecho validado y su clave, sin usar campos legacy."""

    for key in keys:
        if not _safe_key(key):
            continue
        fact = record.facts.facts.get(key)
        if (
            fact
            and fact.status is FactStatus.VALIDATED
            and fact.value not in (None, "", [], {})
        ):
            return fact, key
    return None, None


def validated_value(
    record: ValidatedFactsRecord,
    *keys: str,
) -> tuple[Any, Optional[str]]:
    fact, key = validated_fact(record, *keys)
    return (fact.value, key) if fact is not None else (None, None)


def validated_source_keys(
    record: ValidatedFactsRecord,
    keys: Iterable[Optional[str]],
) -> list[str]:
    """Conserva solo claves autorizadas que realmente están validadas."""

    result: list[str] = []
    for raw in keys:
        key = str(raw or "").strip()
        if not key or key in result or not _safe_key(key):
            continue
        fact = record.facts.facts.get(key)
        if fact and fact.status is FactStatus.VALIDATED:
            result.append(key)
    return result


def family_evidence_keys(record: FamilyResolutionRecord) -> list[str]:
    result: list[str] = []
    for evidence in record.resolution.evidence:
        for key in evidence.source_fact_keys:
            if _safe_key(key) and key not in result:
                result.append(key)
    return result


def ensure_specialist_authority(
    facts_record: ValidatedFactsRecord,
    family_record: FamilyResolutionRecord,
    *,
    service: str,
    family: str,
    specialist: str,
) -> None:
    """Valida la cadena autoritativa antes de construir una LegalPreview."""

    expected_service = canonical_department(service)
    actual_service = canonical_department(facts_record.facts.service)
    if actual_service != expected_service:
        raise HTTPException(
            status_code=409,
            detail="Los hechos no pertenecen al satélite del especialista.",
        )
    if not facts_record.frozen or facts_record.invalidated_at is not None:
        raise HTTPException(
            status_code=409,
            detail="Los hechos deben estar activos y congelados.",
        )
    if not facts_record.facts.frozen:
        raise HTTPException(
            status_code=409,
            detail="El contrato de hechos no conserva frozen=true.",
        )
    if not family_record.locked or family_record.invalidated_at is not None:
        raise HTTPException(
            status_code=409,
            detail="La familia debe estar activa y bloqueada.",
        )
    resolution = family_record.resolution
    if not resolution.locked:
        raise HTTPException(
            status_code=409,
            detail="El contrato de familia no conserva locked=true.",
        )
    if canonical_department(resolution.service) != expected_service:
        raise HTTPException(
            status_code=409,
            detail="La resolución de familia pertenece a otro satélite.",
        )
    if resolution.family != family or resolution.specialist != specialist:
        raise HTTPException(
            status_code=409,
            detail="La autoridad no corresponde al especialista solicitado.",
        )
    if family_record.validated_facts_id != facts_record.id:
        raise HTTPException(
            status_code=409,
            detail="La familia no procede de esta versión de hechos.",
        )

    validated_keys = {
        key
        for key, fact in facts_record.facts.facts.items()
        if _safe_key(key) and fact.status is FactStatus.VALIDATED
    }
    unknown = sorted(set(family_evidence_keys(family_record)) - validated_keys)
    if unknown:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "La familia utiliza evidencia no validada.",
                "fact_keys": unknown,
            },
        )


def document_uses(record: ValidatedFactsRecord) -> list[DocumentUse]:
    pages: dict[str, set[int]] = {
        str(document_id): set()
        for document_id in record.facts.source_document_ids
    }
    for fact in record.facts.facts.values():
        for source in fact.sources:
            pages.setdefault(source.document_id, set())
            if source.page_index is not None:
                pages[source.document_id].add(source.page_index)
    return [
        DocumentUse(
            document_id=document_id,
            label=f"Documento de origen {document_id}",
            status="validated",
            pages_used=sorted(page_indexes),
        )
        for document_id, page_indexes in sorted(pages.items())
    ]


def _display(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, (list, tuple, set)):
        return "; ".join(str(item) for item in value)
    return str(value)


def summary_rows(
    record: ValidatedFactsRecord,
    fields: Sequence[tuple[str, str, str]],
) -> tuple[list[str], list[str]]:
    """Construye resumen y devuelve las claves exactas empleadas."""

    rows: list[str] = []
    used: list[str] = []
    for key, label, suffix in fields:
        value, found = validated_value(record, key)
        if value in (None, "", [], {}) or not found:
            continue
        rows.append(f"{label}: {_display(value)}{suffix}.")
        used.append(found)
    return list(dict.fromkeys(rows)), list(dict.fromkeys(used))


def missing_item(
    code: str,
    description: str,
    severity: MissingItemSeverity = MissingItemSeverity.BLOCKING,
) -> MissingItem:
    return MissingItem(
        code=str(code).strip(),
        description=str(description).strip(),
        severity=severity,
    )


def dedupe_missing(items: Iterable[MissingItem]) -> list[MissingItem]:
    result: list[MissingItem] = []
    seen: set[str] = set()
    for item in items:
        if item.code in seen:
            continue
        seen.add(item.code)
        result.append(item)
    return result


def fact_review_items(
    record: ValidatedFactsRecord,
    *,
    prefix: str,
) -> list[MissingItem]:
    """Proyecta conflictos y no resueltos como revisiones OPS explícitas."""

    items: list[MissingItem] = []
    for key, fact in sorted(record.facts.facts.items()):
        if not _safe_key(key):
            continue
        if fact.status is FactStatus.CONFLICTED:
            items.append(
                missing_item(
                    f"{prefix}_conflict_{key}"[:120],
                    f"Existe un conflicto documental pendiente en '{key}'.",
                    MissingItemSeverity.BLOCKING,
                )
            )
        elif fact.status is FactStatus.UNRESOLVED:
            items.append(
                missing_item(
                    f"{prefix}_unresolved_{key}"[:120],
                    f"Debe revisarse el dato documental pendiente '{key}'.",
                    MissingItemSeverity.HUMAN_REVIEW,
                )
            )
    for index, conflict in enumerate(record.facts.conflicts, start=1):
        items.append(
            missing_item(
                f"{prefix}_global_conflict_{index}"[:120],
                f"Conflicto documental: {conflict}",
                MissingItemSeverity.BLOCKING,
            )
        )
    return dedupe_missing(items)


def legal_argument(
    record: ValidatedFactsRecord,
    *,
    code: str,
    title: str,
    body: str,
    source_fact_keys: Iterable[Optional[str]],
    priority: str = "secondary",
    legal_basis: Optional[list[str]] = None,
) -> LegalArgument:
    keys = validated_source_keys(record, source_fact_keys)
    if not keys:
        raise HTTPException(
            status_code=409,
            detail=f"El argumento {code} no conserva hechos validados de origen.",
        )
    return LegalArgument(
        code=code,
        title=title,
        body=body.strip(),
        priority=priority,
        source_fact_keys=keys,
        legal_basis=list(legal_basis or []),
    )
