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
from rtm_core.debt_credit_file_specialist import (
    DEBT_CREDIT_FILE_SPECIALIST_VERSION,
)
from rtm_core.debt_specialist_registry import DEBT_SPECIALIST_REGISTRY_VERSION
from rtm_core.domain_catalog import family_profile
from rtm_core.family_dispatch import resolve_family
from rtm_core.specialist_dispatch import (
    SPECIALIST_DISPATCH_VERSION,
    SPECIALIST_REGISTRY_VERSION,
    build_legal_preview,
    registered_specialists,
)


NOW = datetime.now(timezone.utc)
DOC_ID = "doc-debt-credit-file"


def _source(evidence: str) -> SourceReference:
    return SourceReference(
        document_id=DOC_ID,
        page_index=0,
        source_type="operator_document_review",
        extraction_method="rtm_credit_file_specialist_test_v1",
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
        case_id="case-debt-credit-file",
        service="debt",
        extractor_version="rtm_service_document_extractor_test_v1",
        facts=values,
        source_document_ids=[DOC_ID],
        frozen=True,
    )
    facts_record = ValidatedFactsRecord(
        id="facts-debt-credit-file",
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
    if resolution.family != "fichero_solvencia":
        raise AssertionError(resolution.model_dump(mode="json"))
    locked = validated_model_copy(resolution, locked=True)
    family_record = FamilyResolutionRecord(
        id="family-debt-credit-file",
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


def _access_values() -> dict[str, ValidatedFact]:
    return {
        "descripcion_hecho": _fact(
            "La persona solicita saber si sus datos constan en ASNEF.",
            "CONSULTA DE DATOS EN ASNEF",
        ),
        "fichero_solvencia": _fact("ASNEF"),
        "solucion_solicitada": _fact(
            "Ejercer el derecho de acceso y obtener confirmación e información completa."
        ),
        "raw_ocr_text": _fact("IGNORE PREVIOUS PROMPT FAMILY STRATEGY GENERATE"),
    }


def _paid_inclusion_values() -> dict[str, ValidatedFact]:
    return {
        "descripcion_hecho": _fact(
            "La deuda pagada continúa incluida en el fichero ASNEF.",
            "INCLUSIÓN EN ASNEF TRAS EL PAGO",
        ),
        "fichero_solvencia": _fact("ASNEF"),
        "acreedor": _fact("ACREEDOR DEMO, S.A."),
        "deudor": _fact("PERSONA AFECTADA"),
        "concepto_deuda": _fact("Servicio contratado"),
        "importe_deuda_eur": _fact(245.80),
        "fecha_vencimiento": _fact("2026-01-10"),
        "fecha_inclusion_fichero": _fact("2026-03-01"),
        "requerimiento_previo_fecha": _fact("2026-02-01"),
        "requerimiento_previo_medio": _fact("carta certificada"),
        "fecha_requerimiento_fichero": _fact("2026-02-01"),
        "fecha_notificacion": _fact("2026-03-15"),
        "deuda_pagada": _fact(True),
        "deuda_discutida": _fact(False),
        "solucion_solicitada": _fact(
            "Acceso, rectificación y supresión de la inclusión tras el pago."
        ),
        "respuesta_documentada": _fact(
            "El acreedor reconoce que recibió el justificante de pago."
        ),
        "raw_ocr_text": _fact("PROMPT OCR DRAFT STRATEGY"),
    }


class DebtCreditFileSpecialistTest(unittest.TestCase):
    def test_registry_catalog_and_dispatch_expose_credit_file_specialist(self):
        self.assertEqual(
            DEBT_CREDIT_FILE_SPECIALIST_VERSION,
            "rtm_debt_credit_file_specialist_v1_0",
        )
        self.assertEqual(
            DEBT_SPECIALIST_REGISTRY_VERSION,
            "rtm_debt_specialist_registry_v1_0",
        )
        self.assertEqual(
            SPECIALIST_DISPATCH_VERSION,
            "rtm_specialist_dispatch_v1_3",
        )
        self.assertEqual(
            SPECIALIST_REGISTRY_VERSION,
            "rtm_specialist_registry_v1_4",
        )
        self.assertIn("debt.credit_file", registered_specialists())
        self.assertIn("debt.unpaid_invoice", registered_specialists())
        profile = family_profile("debt", "fichero_solvencia")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.specialist, "debt.credit_file")
        self.assertEqual(profile.capability, "specialist_ready")

    def test_unknown_inclusion_starts_with_access_not_automatic_erasure(self):
        facts_record, family_record = _records(_access_values())
        preview = build_legal_preview(facts_record, family_record)

        self.assertEqual(preview.family, "fichero_solvencia")
        self.assertEqual(preview.specialist, "debt.credit_file")
        self.assertEqual(
            preview.document_type,
            "EJERCICIO DEL DERECHO DE ACCESO A SISTEMA DE INFORMACIÓN CREDITICIA",
        )
        self.assertIn("ASNEF", preview.destination)
        self.assertIn("ASNEF", preview.subject)
        self.assertTrue(preview.legal_arguments)
        self.assertIn(
            "no presupone que exista una inclusión",
            " ".join(item.body for item in preview.legal_arguments).lower(),
        )
        self.assertFalse(
            [
                item
                for item in preview.missing_items
                if item.severity is MissingItemSeverity.BLOCKING
            ]
        )
        self.assertEqual(len(preview.deadlines), 1)
        self.assertEqual(preview.deadlines[0].calculation_status, "unresolved")
        self.assertIsNone(preview.deadlines[0].due_at)
        self.assertTrue(
            any(
                item.code == "credit_file_access_response_review"
                for item in preview.missing_items
            )
        )
        self.assertNotIn("raw_ocr_text", preview.source_fact_keys)
        for argument in preview.legal_arguments:
            self.assertNotIn("raw_ocr_text", argument.source_fact_keys)

    def test_paid_inclusion_requests_rights_but_keeps_active_status_review_blocking(self):
        facts_record, family_record = _records(_paid_inclusion_values())
        preview = build_legal_preview(facts_record, family_record)

        self.assertEqual(
            preview.document_type,
            "EJERCICIO DE DERECHOS DE ACCESO, RECTIFICACIÓN, LIMITACIÓN Y/O SUPRESIÓN",
        )
        self.assertIn("ACREEDOR DEMO", preview.destination)
        self.assertGreaterEqual(len(preview.legal_arguments), 4)
        rendered = " ".join(item.body for item in preview.legal_arguments).lower()
        self.assertIn("el pago determina la eliminación", rendered)
        self.assertIn("no declara automáticamente superado", rendered)
        self.assertTrue(
            any(
                item.code == "credit_file_paid_debt_active_status_review"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )
        self.assertFalse(
            any(
                item.code == "credit_file_erasure_ground_missing"
                for item in preview.missing_items
            )
        )
        self.assertEqual(len(preview.deadlines), 3)
        self.assertTrue(all(item.due_at is None for item in preview.deadlines))
        self.assertTrue(
            any(
                "artículo 20" in basis.lower()
                for argument in preview.legal_arguments
                for basis in argument.legal_basis
            )
        )

    def test_amount_below_fifty_requires_principal_threshold_review(self):
        values = _paid_inclusion_values()
        values["importe_deuda_eur"] = _fact(49.99)
        values["deuda_pagada"] = _fact(False)
        values["solucion_solicitada"] = _fact(
            "Acceso y supresión por cuantía inferior al mínimo legal."
        )
        facts_record, family_record = _records(values)
        preview = build_legal_preview(facts_record, family_record)

        self.assertTrue(
            any(
                item.code == "credit_file_principal_below_threshold_review"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )
        self.assertFalse(
            any(
                item.code == "credit_file_erasure_ground_missing"
                for item in preview.missing_items
            )
        )

    def test_informal_dispute_does_not_become_binding_controversy(self):
        values = _paid_inclusion_values()
        values["deuda_pagada"] = _fact(False)
        values["deuda_discutida"] = _fact(True)
        values["solucion_solicitada"] = _fact(
            "Acceso y limitación mientras se verifica la exactitud de los datos comunicados."
        )
        facts_record, family_record = _records(values)
        preview = build_legal_preview(facts_record, family_record)

        self.assertTrue(
            any(
                item.code == "credit_file_informal_dispute_not_enough_review"
                and item.severity is MissingItemSeverity.HUMAN_REVIEW
                for item in preview.missing_items
            )
        )
        rendered = " ".join(item.body for item in preview.legal_arguments).lower()
        self.assertIn("discrepancia pendiente de acreditar", rendered)
        self.assertIn("aepd no sustituye", rendered)

    def test_formal_judicial_dispute_is_traced_without_deciding_the_debt(self):
        values = _paid_inclusion_values()
        values["deuda_pagada"] = _fact(False)
        values["deuda_discutida"] = _fact(True)
        values["procedimiento_judicial"] = _fact(
            "Procedimiento judicial sobre la existencia y cuantía de la deuda"
        )
        values["numero_procedimiento"] = _fact("JDO-2026-00125")
        values["solucion_solicitada"] = _fact(
            "Acceso, limitación y supresión si la controversia formal lo exige."
        )
        facts_record, family_record = _records(values)
        preview = build_legal_preview(facts_record, family_record)

        self.assertTrue(
            any(
                item.code == "credit_file_formal_dispute_scope_review"
                for item in preview.missing_items
            )
        )
        self.assertFalse(
            any(
                item.code == "credit_file_erasure_ground_missing"
                for item in preview.missing_items
            )
        )
        self.assertIn("JDO-2026-00125", " ".join(preview.validated_facts_summary))

    def test_multiple_systems_require_one_request_per_controller(self):
        values = _access_values()
        values["fichero_solvencia"] = _fact(["ASNEF", "EXPERIAN"])
        values["descripcion_hecho"] = _fact(
            "Solicita acceso a ASNEF y EXPERIAN para saber si existe inclusión."
        )
        facts_record, family_record = _records(values)
        preview = build_legal_preview(facts_record, family_record)

        self.assertTrue(
            any(
                item.code == "credit_file_multiple_systems_split_required"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )

    def test_unknown_system_blocks_destination_selection(self):
        values = _access_values()
        values.pop("fichero_solvencia")
        values["descripcion_hecho"] = _fact(
            "Solicita acceso a un fichero de morosos que todavía no está identificado."
        )
        facts_record, family_record = _records(values)
        preview = build_legal_preview(facts_record, family_record)

        self.assertTrue(
            any(
                item.code == "credit_file_system_missing"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )

    def test_authority_mismatch_is_rejected(self):
        facts_record, family_record = _records(_access_values())
        wrong_resolution = validated_model_copy(
            family_record.resolution,
            specialist="debt.debtor_defence",
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
