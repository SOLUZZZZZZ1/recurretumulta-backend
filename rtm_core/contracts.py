"""Contratos autoritativos del núcleo RTM.

Este módulo no clasifica, no extrae y no redacta. Define únicamente las
estructuras que deben intercambiar las capas del sistema para impedir que una
fase posterior reinterprete o sobrescriba decisiones anteriores.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


CORE_CONTRACTS_VERSION = "rtm_core_contracts_v1_0"
FAMILY_RESOLUTION_VERSION = "rtm_family_resolution_v1_0"
LEGAL_PREVIEW_VERSION = "rtm_legal_preview_v1_0"
VALIDATED_FACTS_VERSION = "rtm_validated_facts_v1_0"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class FactStatus(str, Enum):
    VALIDATED = "validated"
    UNRESOLVED = "unresolved"
    CONFLICTED = "conflicted"
    REJECTED = "rejected"


class ResolutionStatus(str, Enum):
    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"
    CONFLICTED = "conflicted"
    OPERATOR_REVIEW = "operator_review"


class PreviewStatus(str, Enum):
    DRAFT = "draft"
    OPS_REVIEW = "ops_review"
    CHANGES_REQUIRED = "changes_required"
    APPROVED = "approved"
    FROZEN = "frozen"
    INVALIDATED = "invalidated"


class MissingItemSeverity(str, Enum):
    BLOCKING = "blocking"
    RECOMMENDED = "recommended"
    NON_BLOCKING = "non_blocking"
    HUMAN_REVIEW = "human_review"


class SourceReference(_StrictModel):
    """Procedencia verificable de un hecho."""

    document_id: str = Field(min_length=1)
    page_index: Optional[int] = Field(default=None, ge=0)
    source_type: str = Field(default="document", min_length=1)
    extraction_method: str = Field(min_length=1)
    evidence: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)


class ValidatedFact(_StrictModel):
    """Un dato documental con estado, procedencia y conflictos explícitos."""

    value: Any = None
    status: FactStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    sources: list[SourceReference] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_state(self) -> "ValidatedFact":
        if self.status is FactStatus.VALIDATED:
            if self.value is None:
                raise ValueError("Un hecho validado debe tener valor")
            if not self.sources:
                raise ValueError("Un hecho validado debe conservar al menos una fuente")
        elif self.status is FactStatus.UNRESOLVED and self.value is not None:
            raise ValueError("Un hecho no resuelto debe conservar value=null")
        elif self.status is FactStatus.CONFLICTED and not self.conflicts:
            raise ValueError("Un hecho conflictivo debe describir al menos un conflicto")
        return self


class ValidatedFacts(_StrictModel):
    """Salida exclusiva de la capa de hechos validados."""

    authority: Literal["rtm_validated_facts"] = "rtm_validated_facts"
    version: str = VALIDATED_FACTS_VERSION
    case_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    facts: dict[str, ValidatedFact] = Field(default_factory=dict)
    unresolved: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    source_document_ids: list[str] = Field(default_factory=list)
    supersedes_version: Optional[str] = None
    frozen: bool = False
    created_at: datetime = Field(default_factory=_utcnow)


class FamilyEvidence(_StrictModel):
    code: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source_fact_keys: list[str] = Field(default_factory=list)
    source_document_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class FamilyConflict(_StrictModel):
    code: str = Field(min_length=1)
    description: str = Field(min_length=1)
    candidate_families: list[str] = Field(default_factory=list)


class FamilyResolution(_StrictModel):
    """Única salida autorizada para decidir la familia del expediente."""

    authority: Literal["rtm_family_core"] = "rtm_family_core"
    version: str = FAMILY_RESOLUTION_VERSION
    case_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    facts_version: str = Field(min_length=1)
    status: ResolutionStatus
    family: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[FamilyEvidence] = Field(default_factory=list)
    conflicts: list[FamilyConflict] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    specialist: Optional[str] = None
    locked: bool = False
    resolved_at: Optional[datetime] = None

    @model_validator(mode="after")
    def validate_resolution(self) -> "FamilyResolution":
        if self.status is ResolutionStatus.RESOLVED:
            if not self.family:
                raise ValueError("Una resolución cerrada debe indicar family")
            if not self.evidence:
                raise ValueError("Una resolución cerrada debe conservar evidencia")
            if self.resolved_at is None:
                raise ValueError("Una resolución cerrada debe indicar resolved_at")
        elif self.status is ResolutionStatus.UNRESOLVED and self.family is not None:
            raise ValueError("Una resolución no resuelta debe conservar family=null")

        if self.status is ResolutionStatus.CONFLICTED and not self.conflicts:
            raise ValueError("Una resolución conflictiva debe describir conflictos")
        if self.locked and self.status is not ResolutionStatus.RESOLVED:
            raise ValueError("Solo una familia resuelta puede quedar bloqueada")
        return self


class DocumentUse(_StrictModel):
    document_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    status: Literal["read", "partially_read", "validated", "discarded", "pending_review"]
    pages_used: list[int] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class MissingItem(_StrictModel):
    code: str = Field(min_length=1)
    description: str = Field(min_length=1)
    severity: MissingItemSeverity


class Deadline(_StrictModel):
    label: str = Field(min_length=1)
    due_at: Optional[datetime] = None
    calculation_status: Literal["confirmed", "estimated", "unresolved"] = "unresolved"
    source_fact_keys: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LegalPreview(_StrictModel):
    """Previa Jurídica estructurada y versionada previa a Generate."""

    authority: Literal["rtm_legal_preview"] = "rtm_legal_preview"
    version: str = LEGAL_PREVIEW_VERSION
    case_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    family: str = Field(min_length=1)
    specialist: str = Field(min_length=1)
    facts_version: str = Field(min_length=1)
    family_resolution_version: str = Field(min_length=1)
    status: PreviewStatus = PreviewStatus.DRAFT

    validated_facts_summary: list[str] = Field(default_factory=list)
    problem_summary: Optional[str] = None
    client_goal: Optional[str] = None
    primary_strategy: Optional[str] = None
    secondary_strategies: list[str] = Field(default_factory=list)
    requested_outcomes: list[str] = Field(default_factory=list)
    documents_used: list[DocumentUse] = Field(default_factory=list)
    missing_items: list[MissingItem] = Field(default_factory=list)
    deadlines: list[Deadline] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    created_by_component: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=_utcnow)
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    frozen_at: Optional[datetime] = None
    invalidated_at: Optional[datetime] = None
    invalidation_reason: Optional[str] = None

    @model_validator(mode="after")
    def validate_preview_state(self) -> "LegalPreview":
        if self.status in (PreviewStatus.APPROVED, PreviewStatus.FROZEN):
            if not self.validated_facts_summary:
                raise ValueError("Una previa aprobada debe incluir hechos validados")
            if not self.primary_strategy:
                raise ValueError("Una previa aprobada debe incluir estrategia principal")
            if not self.requested_outcomes:
                raise ValueError("Una previa aprobada debe incluir una petición")
            if not self.approved_by or self.approved_at is None:
                raise ValueError("Una previa aprobada debe conservar aprobación OPS")

        if self.status is PreviewStatus.FROZEN and self.frozen_at is None:
            raise ValueError("Una previa congelada debe indicar frozen_at")

        if self.status is PreviewStatus.INVALIDATED:
            if self.invalidated_at is None or not self.invalidation_reason:
                raise ValueError("Una previa invalidada debe conservar fecha y motivo")

        return self
