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

from rtm_core.travel_agency_extension import (
    TRAVEL_AGENCY_EXTENSION_VERSION,
    install_travel_agency_extension,
)

install_travel_agency_extension()

__all__ += [
    "TRAVEL_AGENCY_EXTENSION_VERSION",
    "install_travel_agency_extension",
]

from rtm_core.travel_insurance_extension import (
    TRAVEL_INSURANCE_EXTENSION_VERSION,
    install_travel_insurance_extension,
)

install_travel_insurance_extension()

__all__ += [
    "TRAVEL_INSURANCE_EXTENSION_VERSION",
    "install_travel_insurance_extension",
]

from rtm_core.claims_energy_extension import (
    CLAIMS_ENERGY_EXTENSION_VERSION,
    install_claims_energy_extension,
)

install_claims_energy_extension()

__all__ += [
    "CLAIMS_ENERGY_EXTENSION_VERSION",
    "install_claims_energy_extension",
]

from rtm_core.claims_banking_extension import (
    CLAIMS_BANKING_EXTENSION_VERSION,
    install_claims_banking_extension,
)

install_claims_banking_extension()

__all__ += [
    "CLAIMS_BANKING_EXTENSION_VERSION",
    "install_claims_banking_extension",
]

from rtm_core.claims_ecommerce_extension import (
    CLAIMS_ECOMMERCE_EXTENSION_VERSION,
    install_claims_ecommerce_extension,
)

install_claims_ecommerce_extension()

__all__ += [
    "CLAIMS_ECOMMERCE_EXTENSION_VERSION",
    "install_claims_ecommerce_extension",
]

from rtm_core.claims_insurance_extension import (
    CLAIMS_INSURANCE_EXTENSION_VERSION,
    install_claims_insurance_extension,
)

install_claims_insurance_extension()

__all__ += [
    "CLAIMS_INSURANCE_EXTENSION_VERSION",
    "install_claims_insurance_extension",
]

from rtm_core.claims_professional_services_extension import (
    CLAIMS_PROFESSIONAL_SERVICES_EXTENSION_VERSION,
    install_claims_professional_services_extension,
)

install_claims_professional_services_extension()

__all__ += [
    "CLAIMS_PROFESSIONAL_SERVICES_EXTENSION_VERSION",
    "install_claims_professional_services_extension",
]

from rtm_core.claims_consumer_extension import (
    CLAIMS_CONSUMER_EXTENSION_VERSION,
    install_claims_consumer_extension,
)

install_claims_consumer_extension()

__all__ += [
    "CLAIMS_CONSUMER_EXTENSION_VERSION",
    "install_claims_consumer_extension",
]

from rtm_core.debt_unpaid_rent_extension import (
    DEBT_UNPAID_RENT_EXTENSION_VERSION,
    install_debt_unpaid_rent_extension,
)

install_debt_unpaid_rent_extension()

__all__ += [
    "DEBT_UNPAID_RENT_EXTENSION_VERSION",
    "install_debt_unpaid_rent_extension",
]
