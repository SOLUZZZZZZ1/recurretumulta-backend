from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from rtm_core.authority_repository import (
    DocumentReviewAttestation,
    canonical_model_json,
    freeze_validated_facts,
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


def _reanalysis_facts(
    *,
    status: FactStatus = FactStatus.UNRESOLVED,
    operator_reviewed: bool = False,
    operator_page_index: int = 0,
    operator_method: str = "ops_document_review_v1",
) -> ValidatedFacts:
    sources = [
        SourceReference(
            document_id="doc-1",
            page_index=0,
            source_type="model_document_observation",
            extraction_method="critical_vision:traffic_critical_fields_v1",
            evidence="CONDUCCIÓN TEMERARIA",
            confidence=1.0,
        )
    ]
    if operator_reviewed:
        sources.append(
            SourceReference(
                document_id="doc-1",
                page_index=operator_page_index,
                source_type="operator_document_review",
                extraction_method=operator_method,
                evidence="CONDUCCIÓN TEMERARIA",
                confidence=1.0,
            )
        )
    fact = ValidatedFact(
        value="Conducción temeraria" if status is FactStatus.VALIDATED else None,
        status=status,
        confidence=1.0,
        sources=sources,
        notes=[] if status is FactStatus.VALIDATED else ["Pendiente de revisión OPS"],
    )
    return ValidatedFacts(
        case_id="case-1",
        service="traffic",
        extractor_version=(
            "traffic_fine_reanalysis_v1_18+"
            "rtm_reanalysis_to_validated_facts_v1_0"
        ),
        facts={"hecho_denunciado_literal": fact},
        source_document_ids=["doc-1"],
    )


def _attestation(facts: ValidatedFacts) -> DocumentReviewAttestation:
    return DocumentReviewAttestation(
        documents_reviewed=True,
        facts_reviewed=True,
        source_document_ids=list(facts.source_document_ids),
        facts_payload_sha256=model_digest(facts),
        review_notes="Documento y hecho contrastados manualmente.",
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

    def test_reanalysis_freeze_requires_bound_document_review_attestation(self):
        facts = _reanalysis_facts()

        with self.assertRaises(HTTPException) as missing:
            validate_facts_for_freeze(
                facts,
                available_document_ids={"doc-1"},
            )
        self.assertEqual(missing.exception.status_code, 409)
        self.assertEqual(
            missing.exception.detail["code"],
            "document_review_attestation_required",
        )

        validate_facts_for_freeze(
            facts,
            available_document_ids={"doc-1"},
            document_review_attestation=_attestation(facts),
        )

    def test_repository_freeze_fails_before_writes_without_attestation(self):
        facts = _reanalysis_facts()
        record = SimpleNamespace(
            invalidated_at=None,
            frozen=False,
            facts=facts,
        )
        conn = Mock()

        with patch(
            "rtm_core.authority_repository.get_validated_facts",
            return_value=record,
        ), patch(
            "rtm_core.authority_repository._available_document_ids",
            return_value={"doc-1"},
        ):
            with self.assertRaises(HTTPException) as missing:
                freeze_validated_facts(
                    conn,
                    facts.case_id,
                    "facts-1",
                    "ops:test",
                )

        self.assertEqual(
            missing.exception.detail["code"],
            "document_review_attestation_required",
        )
        conn.execute.assert_not_called()

    def test_reanalysis_attestation_is_bound_to_exact_facts_hash(self):
        facts = _reanalysis_facts()
        attestation = _attestation(facts).model_copy(
            update={"facts_payload_sha256": "0" * 64}
        )

        with self.assertRaises(HTTPException) as mismatch:
            validate_facts_for_freeze(
                facts,
                available_document_ids={"doc-1"},
                document_review_attestation=attestation,
            )
        self.assertEqual(
            mismatch.exception.detail["code"],
            "document_review_attestation_digest_mismatch",
        )

    def test_model_only_source_cannot_be_validated_even_with_attestation(self):
        facts = _reanalysis_facts(status=FactStatus.VALIDATED)

        with self.assertRaises(HTTPException) as rejected:
            validate_facts_for_freeze(
                facts,
                available_document_ids={"doc-1"},
                document_review_attestation=_attestation(facts),
            )
        self.assertEqual(rejected.exception.status_code, 409)
        self.assertIn(
            "hecho_denunciado_literal",
            rejected.exception.detail["fields"],
        )

    def test_operator_review_must_match_model_document_and_page(self):
        facts = _reanalysis_facts(
            status=FactStatus.VALIDATED,
            operator_reviewed=True,
            operator_page_index=1,
        )

        with self.assertRaises(HTTPException):
            validate_facts_for_freeze(
                facts,
                available_document_ids={"doc-1"},
                document_review_attestation=_attestation(facts),
            )

    def test_operator_review_source_requires_allowlisted_method(self):
        facts = _reanalysis_facts(
            status=FactStatus.VALIDATED,
            operator_reviewed=True,
            operator_method="client_supplied_review_label",
        )

        with self.assertRaises(HTTPException):
            validate_facts_for_freeze(
                facts,
                available_document_ids={"doc-1"},
                document_review_attestation=_attestation(facts),
            )

    def test_frozen_legacy_model_authority_does_not_bypass_validation(self):
        payload = _reanalysis_facts(
            status=FactStatus.VALIDATED
        ).model_dump(mode="python")
        payload["frozen"] = True
        facts = ValidatedFacts.model_validate(payload)

        with self.assertRaises(HTTPException):
            validate_facts_for_freeze(
                facts,
                available_document_ids={"doc-1"},
            )

    def test_operator_reviewed_model_candidate_can_freeze_with_attestation(self):
        facts = _reanalysis_facts(
            status=FactStatus.VALIDATED,
            operator_reviewed=True,
        )

        validate_facts_for_freeze(
            facts,
            available_document_ids={"doc-1"},
            document_review_attestation=_attestation(facts),
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
