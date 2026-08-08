from datetime import datetime, timezone
import os
import unittest

from pydantic import ValidationError

from rtm_core.contracts import (
    FactStatus,
    FamilyEvidence,
    FamilyResolution,
    LegalPreview,
    PreviewStatus,
    ResolutionStatus,
    SourceReference,
    ValidatedFact,
)
from rtm_core.versioning import build_version_snapshot


NOW = datetime.now(timezone.utc)


class CoreContractsTest(unittest.TestCase):
    def test_validated_fact_requires_source(self):
        with self.assertRaises(ValidationError):
            ValidatedFact(value="500 EUR", status=FactStatus.VALIDATED, confidence=0.9)

    def test_unresolved_fact_keeps_null(self):
        fact = ValidatedFact(value=None, status=FactStatus.UNRESOLVED)
        self.assertIsNone(fact.value)

    def test_resolved_family_requires_evidence(self):
        with self.assertRaises(ValidationError):
            FamilyResolution(
                case_id="case-1",
                service="traffic",
                facts_version="facts-v1",
                status=ResolutionStatus.RESOLVED,
                family="temeraria",
                resolved_at=NOW,
            )

    def test_locked_family_resolution_is_valid(self):
        resolution = FamilyResolution(
            case_id="case-1",
            service="traffic",
            facts_version="facts-v1",
            status=ResolutionStatus.RESOLVED,
            family="temeraria",
            confidence=0.98,
            evidence=[
                FamilyEvidence(
                    code="explicit_fact",
                    description="La frase factual indica conducción temeraria",
                    source_fact_keys=["hecho_denunciado_literal"],
                    confidence=0.98,
                )
            ],
            specialist="traffic.temeraria",
            locked=True,
            resolved_at=NOW,
        )
        self.assertTrue(resolution.locked)

    def test_frozen_preview_requires_ops_approval(self):
        with self.assertRaises(ValidationError):
            LegalPreview(
                case_id="case-1",
                service="traffic",
                family="temeraria",
                specialist="traffic.temeraria",
                facts_version="facts-v1",
                family_resolution_version="family-v1",
                status=PreviewStatus.FROZEN,
                validated_facts_summary=["Hecho acreditado"],
                primary_strategy="Insuficiencia probatoria específica",
                requested_outcomes=["Archivo"],
                created_by_component="traffic.temeraria",
                frozen_at=NOW,
            )

    def test_frozen_preview_is_valid_with_ops_approval(self):
        preview = LegalPreview(
            case_id="case-1",
            service="traffic",
            family="temeraria",
            specialist="traffic.temeraria",
            facts_version="facts-v1",
            family_resolution_version="family-v1",
            status=PreviewStatus.FROZEN,
            validated_facts_summary=["Hecho acreditado"],
            primary_strategy="Insuficiencia probatoria específica",
            requested_outcomes=["Archivo"],
            created_by_component="traffic.temeraria",
            approved_by="ops:ramon",
            approved_at=NOW,
            frozen_at=NOW,
        )
        self.assertEqual(preview.status, PreviewStatus.FROZEN)

    def test_version_snapshot_never_exposes_operator_token(self):
        previous = os.environ.get("OPERATOR_TOKEN")
        os.environ["OPERATOR_TOKEN"] = "secret-test-token"
        try:
            snapshot = build_version_snapshot()
            self.assertNotIn("secret-test-token", repr(snapshot))
            self.assertIn("components", snapshot)
            self.assertIn("contracts", snapshot)
        finally:
            if previous is None:
                os.environ.pop("OPERATOR_TOKEN", None)
            else:
                os.environ["OPERATOR_TOKEN"] = previous


if __name__ == "__main__":
    unittest.main()
