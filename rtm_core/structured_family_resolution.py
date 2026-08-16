"""Resolución estructurada complementaria para satélites RTM no tráfico.

El resolver transversal base permanece como primera autoridad. Este módulo solo
interviene cuando aquel devuelve ``unresolved`` y existe una combinación cerrada
de hechos documentales validados que identifica una familia sin depender de un
resumen libre.

No lee OCR crudo, no usa el formulario, no clasifica por etiquetas y no habilita
Generate.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from rtm_core.contracts import (
    FactStatus,
    FamilyEvidence,
    FamilyResolution,
    ResolutionStatus,
    ValidatedFact,
    ValidatedFacts,
)
from rtm_core.cross_service_family import resolve_cross_service_family
from rtm_core.domain_catalog import family_profile
from rtm_core.service_catalog import canonical_department


STRUCTURED_CROSS_SERVICE_FAMILY_VERSION = (
    "rtm_cross_service_family_structured_v1_0"
)


def _validated_fact(
    facts: ValidatedFacts,
    *keys: str,
) -> Optional[tuple[str, ValidatedFact]]:
    for key in keys:
        fact = facts.facts.get(key)
        if (
            fact is not None
            and fact.status is FactStatus.VALIDATED
            and fact.value is not None
            and fact.sources
        ):
            return key, fact
    return None


def _numeric(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    raw = str(value).strip().replace("€", "").replace(" ", "")
    if not raw:
        return None

    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")

    match = re.search(r"-?[0-9]+(?:\.[0-9]+)?", raw)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _document_ids(items: Iterable[ValidatedFact]) -> list[str]:
    return sorted(
        {
            source.document_id
            for fact in items
            for source in fact.sources
            if source.document_id
        }
    )


def _structured_unpaid_invoice(
    facts: ValidatedFacts,
) -> Optional[FamilyResolution]:
    if canonical_department(facts.service) != "debt":
        return None

    paid = _validated_fact(facts, "deuda_pagada")
    if paid and bool(paid[1].value) is True:
        return None

    invoice = _validated_fact(facts, "factura_numero")
    outstanding = _validated_fact(facts, "saldo_pendiente_eur")
    maturity = _validated_fact(facts, "fecha_vencimiento")

    if not invoice or not outstanding or not maturity:
        return None
    if (_numeric(outstanding[1].value) or 0.0) <= 0:
        return None

    profile = family_profile("debt", "factura_impagada")
    if profile is None:
        return None

    selected = [invoice, outstanding, maturity]
    source_keys = [key for key, _fact in selected]
    source_facts = [fact for _key, fact in selected]

    return FamilyResolution(
        case_id=facts.case_id,
        service=facts.service,
        facts_version=facts.version,
        status=ResolutionStatus.RESOLVED,
        family="factura_impagada",
        confidence=0.97,
        evidence=[
            FamilyEvidence(
                code="structured_unpaid_invoice",
                description=(
                    "Constan como hechos validados el identificador de una "
                    "factura, un saldo pendiente positivo y su fecha de "
                    "vencimiento."
                ),
                source_fact_keys=source_keys,
                source_document_ids=_document_ids(source_facts),
                confidence=0.97,
            )
        ],
        conflicts=[],
        unresolved=[],
        specialist=profile.specialist,
        locked=False,
        resolved_at=datetime.now(timezone.utc),
    )


def resolve_cross_service_family_structured(
    facts: ValidatedFacts,
) -> FamilyResolution:
    """Conserva el resultado base y añade reglas estructuradas solo al unresolved."""

    base = resolve_cross_service_family(facts)
    if base.status is not ResolutionStatus.UNRESOLVED:
        return base

    structured = _structured_unpaid_invoice(facts)
    return structured or base
