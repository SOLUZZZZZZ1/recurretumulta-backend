from datetime import datetime, timezone
import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from rtm_core.contracts import (
    FactStatus,
    FamilyEvidence,
    FamilyResolution,
    LegalArgument,
    LegalPreview,
    PreviewStatus,
    ResolutionStatus,
    SourceReference,
    ValidatedFact,
)
from rtm_core.versioning import build_version_snapshot
from rtm_core.versioning import _runtime_constant


NOW = datetime.now(timezone.utc)


def _argument() -> LegalArgument:
    return LegalArgument(
        code="insuficiencia_probatoria",
        title="Insuficiencia probatoria específica",
        body="La Administración debe acreditar de forma suficiente el hecho imputado.",
        priority="primary",
        source_fact_keys=["hecho_denunciado_literal"],
        legal_basis=["Principio de presunción de inocencia"],
    )


def _complete_preview_payload() -> dict:
    return {
        "case_id": "case-1",
        "service": "traffic",
        "family": "temeraria",
        "specialist": "traffic.temeraria",
        "facts_version": "facts-v1",
        "family_resolution_version": "family-v1",
        "validated_facts_summary": ["Hecho acreditado"],
        "source_fact_keys": ["hecho_denunciado_literal"],
        "primary_strategy": "Insuficiencia probatoria específica",
        "requested_outcomes": ["Archivo"],
        "destination": "Al órgano sancionador competente",
        "document_type": "Alegaciones",
        "subject": "Alegaciones al expediente sancionador",
        "legal_arguments": [_argument()],
        "created_by_component": "traffic.temeraria",
    }


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
        payload = _complete_preview_payload()
        payload.update({"status": PreviewStatus.FROZEN, "frozen_at": NOW})
        with self.assertRaises(ValidationError):
            LegalPreview(**payload)

    def test_frozen_preview_is_valid_with_ops_approval(self):
        payload = _complete_preview_payload()
        payload.update(
            {
                "status": PreviewStatus.FROZEN,
                "approved_by": "ops:ramon",
                "approved_at": NOW,
                "frozen_at": NOW,
            }
        )
        preview = LegalPreview(**payload)
        self.assertEqual(preview.status, PreviewStatus.FROZEN)

    def test_argument_cannot_use_undeclared_fact(self):
        payload = _complete_preview_payload()
        payload["legal_arguments"] = [
            LegalArgument(
                code="wrong",
                title="Argumento no trazable",
                body="Texto",
                source_fact_keys=["campo_no_declarado"],
            )
        ]
        with self.assertRaises(ValidationError):
            LegalPreview(**payload)

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

    def test_version_lookup_error_is_opaque(self):
        canary = "PRIVATE_IMPORT_PATH_CANARY"
        with patch(
            "rtm_core.versioning.importlib.import_module",
            side_effect=RuntimeError(canary),
        ):
            runtime, error = _runtime_constant("synthetic.module", "VERSION")

        self.assertIsNone(runtime)
        self.assertEqual(error, "runtime_lookup_failed")
        self.assertNotIn(canary, error)


if __name__ == "__main__":
    unittest.main()
