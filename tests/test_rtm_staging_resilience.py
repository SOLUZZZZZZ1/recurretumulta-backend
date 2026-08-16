from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from rtm_core.contracts import (
    FactStatus,
    ResolutionStatus,
    SourceReference,
    ValidatedFact,
    ValidatedFacts,
)
from rtm_core.document_extraction import (
    OpenAIResponsesDocumentProvider,
    ProviderDocumentResult,
    SourceDocument,
)
from rtm_core.document_provider_retry import (
    RetryingOpenAIResponsesDocumentProvider,
)
from rtm_core.family_dispatch import resolve_family


def _source() -> SourceReference:
    return SourceReference(
        document_id="doc-1",
        page_index=0,
        source_type="document_text",
        extraction_method="test",
        evidence="evidencia sintética",
        confidence=0.99,
    )


def _fact(value) -> ValidatedFact:
    return ValidatedFact(
        value=value,
        status=FactStatus.VALIDATED,
        confidence=0.99,
        sources=[_source()],
    )


def _debt_facts(
    *,
    paid: bool = False,
    include_balance: bool = True,
) -> ValidatedFacts:
    facts = {
        "factura_numero": _fact("F-2026-018"),
        "fecha_vencimiento": _fact("01/07/2026"),
        "concepto_deuda": _fact("Servicios profesionales"),
    }
    if include_balance:
        facts["saldo_pendiente_eur"] = _fact("1.250,50 EUR")
    if paid:
        facts["deuda_pagada"] = _fact(True)
    return ValidatedFacts(
        case_id="case-debt-1",
        service="debt",
        extractor_version="test-extractor",
        facts=facts,
        source_document_ids=["doc-1"],
    )


def _document() -> SourceDocument:
    return SourceDocument(
        id="doc-1",
        case_id="case-1",
        kind="original",
        mime="text/plain",
        b2_bucket="synthetic",
        b2_key="synthetic/document.txt",
        size_bytes=10,
        sha256="abc",
    )


class StructuredFamilyResolutionTest(unittest.TestCase):
    def test_unpaid_invoice_resolves_from_structured_validated_facts(self):
        resolution = resolve_family(_debt_facts())

        self.assertEqual(resolution.status, ResolutionStatus.RESOLVED)
        self.assertEqual(resolution.family, "factura_impagada")
        self.assertEqual(resolution.specialist, "debt.unpaid_invoice")
        self.assertGreaterEqual(resolution.confidence, 0.97)
        self.assertEqual(
            resolution.evidence[0].code,
            "structured_unpaid_invoice",
        )

    def test_structured_rule_requires_positive_outstanding_balance(self):
        resolution = resolve_family(
            _debt_facts(include_balance=False)
        )
        self.assertEqual(resolution.status, ResolutionStatus.UNRESOLVED)

    def test_paid_debt_is_not_promoted_to_unpaid_invoice(self):
        resolution = resolve_family(_debt_facts(paid=True))
        self.assertEqual(resolution.status, ResolutionStatus.UNRESOLVED)


class DocumentProviderRetryTest(unittest.TestCase):
    def test_429_is_retried_using_provider_delay(self):
        waits = []
        rate_limit = HTTPException(
            status_code=502,
            detail={
                "message": "provider error",
                "status_code": 429,
                "provider_detail": (
                    "Rate limit reached. Please try again in 3.971s."
                ),
            },
        )
        success = (
            ProviderDocumentResult(),
            "document_text",
            [],
        )
        provider = RetryingOpenAIResponsesDocumentProvider(
            api_key="synthetic-test-key",
            model="gpt-4o",
            max_attempts=2,
            retry_margin_seconds=0.75,
            sleeper=waits.append,
        )

        with patch.object(
            OpenAIResponsesDocumentProvider,
            "extract_document",
            side_effect=[rate_limit, success],
        ) as mocked:
            result, mode, warnings = provider.extract_document(
                service="claims",
                document=_document(),
                content=b"synthetic",
            )

        self.assertIsInstance(result, ProviderDocumentResult)
        self.assertEqual(mode, "document_text")
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(len(waits), 1)
        self.assertAlmostEqual(waits[0], 4.721, places=3)
        self.assertIn("provider_rate_limit_retry:1", warnings)

    def test_non_rate_limit_error_is_not_retried(self):
        waits = []
        forbidden = HTTPException(
            status_code=502,
            detail={
                "message": "provider error",
                "status_code": 403,
                "provider_detail": "model_not_found",
            },
        )
        provider = RetryingOpenAIResponsesDocumentProvider(
            api_key="synthetic-test-key",
            model="gpt-4o",
            max_attempts=3,
            sleeper=waits.append,
        )

        with patch.object(
            OpenAIResponsesDocumentProvider,
            "extract_document",
            side_effect=forbidden,
        ) as mocked:
            with self.assertRaises(HTTPException):
                provider.extract_document(
                    service="claims",
                    document=_document(),
                    content=b"synthetic",
                )

        self.assertEqual(mocked.call_count, 1)
        self.assertFalse(waits)


if __name__ == "__main__":
    unittest.main()
