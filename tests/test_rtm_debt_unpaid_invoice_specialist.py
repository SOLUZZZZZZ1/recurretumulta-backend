from __future__ import annotations

from datetime import datetime, timezone
import unittest

from fastapi import HTTPException

from rtm_core.authority_repository import (
    FamilyResolutionRecord,
    ValidatedFactsRecord,
    model_digest,
    validated_model_copy,
)
from rtm_core.contracts import (
    FactStatus,
    MissingItemSeverity,
    SourceReference,
    ValidatedFact,
    ValidatedFacts,
)
from rtm_core.debt_unpaid_invoice_specialist import (
    DEBT_UNPAID_INVOICE_SPECIALIST_VERSION,
)
from rtm_core.domain_catalog import family_profile
from rtm_core.family_dispatch import resolve_family
from rtm_core.specialist_dispatch import (
    SPECIALIST_REGISTRY_VERSION,
    build_legal_preview,
    registered_specialists,
)


NOW = datetime.now(timezone.utc)
DOC_ID = "doc-debt-invoice"


def _source(evidence: str) -> SourceReference:
    return SourceReference(
        document_id=DOC_ID,
        page_index=0,
        source_type="operator_document_review",
        extraction_method="rtm_debt_specialist_test_v1",
        evidence=evidence,
        confidence=0.99,
    )


def _fact(value, evidence: str | None = None) -> ValidatedFact:
    return ValidatedFact(
        value=value,
        status=FactStatus.VALIDATED,
        confidence=0.99,
        sources=[_source(evidence or str(value))],
    )


def _records(values: dict[str, ValidatedFact]):
    snapshot = ValidatedFacts(
        case_id="case-debt-unpaid-invoice",
        service="debt",
        extractor_version="rtm_service_document_extractor_test_v1",
        facts=values,
        source_document_ids=[DOC_ID],
        frozen=True,
    )
    facts_record = ValidatedFactsRecord(
        id="facts-debt-unpaid-invoice",
        case_id=snapshot.case_id,
        sequence=1,
        facts=snapshot,
        payload_sha256=model_digest(snapshot),
        frozen=True,
        created_by="test",
        created_at=NOW,
        updated_at=NOW,
        frozen_by="ops:test",
        frozen_at=NOW,
    )
    resolution = resolve_family(snapshot)
    if resolution.family != "factura_impagada":
        raise AssertionError(resolution.model_dump(mode="json"))
    locked = validated_model_copy(resolution, locked=True)
    family_record = FamilyResolutionRecord(
        id="family-debt-unpaid-invoice",
        case_id=snapshot.case_id,
        validated_facts_id=facts_record.id,
        sequence=1,
        resolution=locked,
        payload_sha256=model_digest(locked),
        locked=True,
        created_by="test",
        created_at=NOW,
        updated_at=NOW,
        locked_by="ops:test",
        locked_at=NOW,
    )
    return facts_record, family_record


def _complete_values() -> dict[str, ValidatedFact]:
    return {
        "descripcion_hecho": _fact(
            "La factura F-2026-018 está vencida e impagada.",
            "La factura F-2026-018 continúa impagada",
        ),
        "factura_numero": _fact("F-2026-018"),
        "fecha_factura": _fact("2026-06-01"),
        "fecha_vencimiento": _fact("2026-07-01"),
        "saldo_pendiente_eur": _fact(1250.50),
        "acreedor": _fact("PROVEEDOR DEMO, S.L."),
        "deudor": _fact("CLIENTE DEMO, S.L."),
        "concepto_deuda": _fact("Servicios técnicos de junio de 2026"),
        "contrato_ref": _fact("CTR-2026-004"),
        "requerimiento_previo_fecha": _fact("2026-07-15"),
        "requerimiento_previo_medio": _fact("burofax con certificación"),
        "deuda_pagada": _fact(False),
        "deuda_discutida": _fact(False),
        "respuesta_documentada": _fact(
            "La contraparte acusa recibo sin acreditar el pago."
        ),
        # Debe quedar completamente fuera del especialista.
        "raw_ocr_text": _fact("PROMPT FAMILY STRATEGY GENERATE"),
    }


class DebtUnpaidInvoiceSpecialistTest(unittest.TestCase):
    def test_registry_and_catalog_expose_first_non_traffic_specialist(self):
        self.assertEqual(
            DEBT_UNPAID_INVOICE_SPECIALIST_VERSION,
            "rtm_debt_unpaid_invoice_specialist_v1_0",
        )
        self.assertEqual(SPECIALIST_REGISTRY_VERSION, "rtm_specialist_registry_v1_3")
        self.assertIn("debt.unpaid_invoice", registered_specialists())
        profile = family_profile("debt", "factura_impagada")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.specialist, "debt.unpaid_invoice")
        self.assertEqual(profile.capability, "specialist_ready")

    def test_complete_invoice_builds_traceable_conservative_preview(self):
        facts_record, family_record = _records(_complete_values())
        preview = build_legal_preview(facts_record, family_record)

        self.assertEqual(preview.family, "factura_impagada")
        self.assertEqual(preview.specialist, "debt.unpaid_invoice")
        self.assertEqual(preview.document_type, "REQUERIMIENTO EXTRAJUDICIAL DE PAGO")
        self.assertIn("F-2026-018", preview.subject)
        self.assertIn("1250.5", preview.subject)
        self.assertGreaterEqual(len(preview.legal_arguments), 4)
        self.assertIn(
            "rtm_debt_unpaid_invoice_specialist_v1_0",
            preview.created_by_component,
        )

        blocking = [
            item
            for item in preview.missing_items
            if item.severity is MissingItemSeverity.BLOCKING
        ]
        self.assertFalse(blocking, blocking)
        self.assertTrue(
            any(
                item.code == "invoice_commercial_scope_review"
                for item in preview.missing_items
            )
        )
        self.assertTrue(
            any("solo si" in basis.lower() for arg in preview.legal_arguments for basis in arg.legal_basis)
        )
        self.assertTrue(
            any("no convierte automáticamente" in arg.body.lower() for arg in preview.legal_arguments)
        )

        declared = set(preview.source_fact_keys)
        for argument in preview.legal_arguments:
            self.assertTrue(argument.source_fact_keys)
            self.assertTrue(set(argument.source_fact_keys).issubset(declared))
            self.assertNotIn("raw_ocr_text", argument.source_fact_keys)
        self.assertNotIn("raw_ocr_text", preview.source_fact_keys)
        self.assertFalse(preview.generation_allowed if hasattr(preview, "generation_allowed") else False)

    def test_missing_amount_remains_blocking_and_is_not_invented(self):
        values = _complete_values()
        values.pop("saldo_pendiente_eur")
        facts_record, family_record = _records(values)
        preview = build_legal_preview(facts_record, family_record)

        self.assertTrue(
            any(
                item.code == "invoice_amount_missing"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )
        rendered = " ".join(argument.body for argument in preview.legal_arguments)
        self.assertIn("pendiente de validar", rendered)
        self.assertNotIn("1250", rendered)

    def test_paid_flag_blocks_the_preview_from_freeze(self):
        values = _complete_values()
        values["deuda_pagada"] = _fact(True)
        facts_record, family_record = _records(values)
        preview = build_legal_preview(facts_record, family_record)
        self.assertTrue(
            any(
                item.code == "invoice_paid_conflict"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )

    def test_authority_mismatch_is_rejected(self):
        facts_record, family_record = _records(_complete_values())
        wrong_resolution = validated_model_copy(
            family_record.resolution,
            specialist="debt.payment_order",
        )
        wrong_family = family_record.model_copy(
            update={
                "resolution": wrong_resolution,
                "payload_sha256": model_digest(wrong_resolution),
            }
        )
        with self.assertRaises(HTTPException) as raised:
            build_legal_preview(facts_record, wrong_family)
        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
