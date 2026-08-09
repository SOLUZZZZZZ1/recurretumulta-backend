"""RTM Intelligence CORE: contratos, autoridad y observabilidad comunes."""

from rtm_core.contracts import (
    CORE_CONTRACTS_VERSION,
    FAMILY_RESOLUTION_VERSION,
    LEGAL_PREVIEW_VERSION,
    VALIDATED_FACTS_VERSION,
    Deadline,
    DocumentUse,
    FactStatus,
    FamilyConflict,
    FamilyEvidence,
    FamilyResolution,
    LegalArgument,
    LegalPreview,
    MissingItem,
    MissingItemSeverity,
    PreviewStatus,
    ResolutionStatus,
    SourceReference,
    ValidatedFact,
    ValidatedFacts,
)

__all__ = [
    "CORE_CONTRACTS_VERSION",
    "FAMILY_RESOLUTION_VERSION",
    "LEGAL_PREVIEW_VERSION",
    "VALIDATED_FACTS_VERSION",
    "Deadline",
    "DocumentUse",
    "FactStatus",
    "FamilyConflict",
    "FamilyEvidence",
    "FamilyResolution",
    "LegalArgument",
    "LegalPreview",
    "MissingItem",
    "MissingItemSeverity",
    "PreviewStatus",
    "ResolutionStatus",
    "SourceReference",
    "ValidatedFact",
    "ValidatedFacts",
]

from rtm_core.readiness import (
    REVIEW_READINESS_VERSION,
    ReadinessIssue,
    ReviewReadiness,
    evaluate_review_readiness,
)
from rtm_core.service_catalog import (
    SERVICE_CATALOG_VERSION,
    ReviewQuote,
    canonical_department,
    normalize_code,
    resolve_review_quote,
)

__all__ += [
    "REVIEW_READINESS_VERSION",
    "ReadinessIssue",
    "ReviewReadiness",
    "evaluate_review_readiness",
    "SERVICE_CATALOG_VERSION",
    "ReviewQuote",
    "canonical_department",
    "normalize_code",
    "resolve_review_quote",
]
