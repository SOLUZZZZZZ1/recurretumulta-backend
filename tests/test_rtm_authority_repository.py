from datetime import datetime, timezone
import unittest

from fastapi import HTTPException

from rtm_core.authority_repository import (
    canonical_model_json,
    model_digest,
    validate_facts_for_freeze,
    validate_resolution_against_facts,
    validated_model_copy,
)
from rtm_core.contracts import (
    FactStatus,
    FamilyEvidence,
    FamilyResolution,
    ResolutionStatus,
    SourceReference,
    ValidatedFact,
    ValidatedFacts,
)


NOW = datetime.now(timezone.utc)


def _source(document_id: str = "doc-1") -> SourceReference:
    return SourceReference(
        document_id=document_id,
        page_index=0,
        extraction_method="vision+operator",
        evidence="CONDUCCIÓN TEMERARIA",
        confidence=0.98,
    )


def _facts(*, status: FactStatus = FactStatus.VALIDATED) -> ValidatedFacts:
    fact = (
        ValidatedFact(
            value="Conducción temeraria",
            status=FactStatus.VALIDATED,
            confidence=0.98,
            sources=[_source()],
        )
        if status is FactStatus.VALIDATED
        else ValidatedFact(
            value=None,
            status=FactStatus.UNRESOLVED,
            confidence=0.3,
            sources=[_source()],
            notes=["Lectura manuscrita no consolidada"],
        )
    )
    return ValidatedFacts(
        case_id="case-1",
        service="traffic",
        extractor_version="traffic_fine_reanalysis_v1_18",
        facts={"hecho_denunciado_literal": fact},
        source_document_ids=["doc-1"],
    )


def _resolution(facts: ValidatedFacts) -> FamilyResolution:
    return FamilyResolution(
        case_id=facts.case_id,
        service=facts.service,
        facts_version=facts.version,
        status=ResolutionStatus.RESOLVED,
        family="temeraria",
        confidence=0.98,
        evidence=[
            FamilyEvidence(
                code="explicit_temeraria",
                description="El literal validado indica conducción temeraria",
                source_fact_keys=["hecho_denunciado_literal"],
                source_document_ids=["doc-1"],
                confidence=0.98,
            )
        ],
        specialist="traffic.temeraria",
        resolved_at=NOW,
    )


class AuthorityRepositoryContractTest(unittest.TestCase):
    def test_digest_is_deterministic(self):
        first = _facts()
        second = ValidatedFacts.model_validate(first.model_dump(mode="python"))
        self.assertEqual(canonical_model_json(first), canonical_model_json(second))
        self.assertEqual(model_digest(first), model_digest(second))

    def test_facts_can_freeze_only_with_known_documents(self):
        facts = _facts()
        validate_facts_for_freeze(facts, available_document_ids={"doc-1"})
        frozen = validated_model_copy(facts, frozen=True)
        self.assertTrue(frozen.frozen)

    def test_facts_reject_document_from_another_case(self):
        with self.assertRaises(HTTPException):
            validate_facts_for_freeze(
                _facts(),
                available_document_ids={"different-document"},
            )

    def test_resolved_family_accepts_validated_evidence(self):
        facts = _facts()
        validate_resolution_against_facts(_resolution(facts), facts)

    def test_resolved_family_rejects_unresolved_evidence(self):
        facts = _facts(status=FactStatus.UNRESOLVED)
        with self.assertRaises(HTTPException):
            validate_resolution_against_facts(_resolution(facts), facts)

    def test_family_rejects_unknown_fact_key(self):
        facts = _facts()
        resolution = _resolution(facts)
        payload = resolution.model_dump(mode="python")
        payload["evidence"][0]["source_fact_keys"] = ["campo_inexistente"]
        altered = FamilyResolution.model_validate(payload)
        with self.assertRaises(HTTPException):
            validate_resolution_against_facts(altered, facts)


if __name__ == "__main__":
    unittest.main()
