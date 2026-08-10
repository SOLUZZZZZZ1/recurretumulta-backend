from __future__ import annotations

from datetime import datetime, timezone
import unittest

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
from rtm_core.family_dispatch import resolve_family
from rtm_core.specialist_dispatch import build_legal_preview
from rtm_core.travel_package_adapter import TRAVEL_PACKAGE_ADAPTER_VERSION


NOW = datetime.now(timezone.utc)
DOC_ID = "doc-travel-package-adapter"


def _source(evidence: str) -> SourceReference:
    return SourceReference(
        document_id=DOC_ID,
        page_index=0,
        source_type="operator_document_review",
        extraction_method="rtm_travel_package_adapter_test_v1",
        evidence=evidence,
        confidence=0.99,
    )


def _fact(value) -> ValidatedFact:
    return ValidatedFact(
        value=value,
        status=FactStatus.VALIDATED,
        confidence=0.99,
        sources=[_source(str(value))],
    )


def _records(values: dict[str, ValidatedFact]):
    snapshot = ValidatedFacts(
        case_id="case-travel-package-adapter",
        service="travel",
        extractor_version="rtm_service_document_extractor_test_v1",
        facts=values,
        source_document_ids=[DOC_ID],
        frozen=True,
    )
    facts_record = ValidatedFactsRecord(
        id="facts-travel-package-adapter",
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
    if resolution.family != "viaje_combinado":
        raise AssertionError(resolution.model_dump(mode="json"))
    locked = validated_model_copy(resolution, locked=True)
    family_record = FamilyResolutionRecord(
        id="family-travel-package-adapter",
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


def _base_values() -> dict[str, ValidatedFact]:
    return {
        "descripcion_hecho": _fact(
            "El viaje combinado fue cancelado por el organizador antes de la salida."
        ),
        "incidencia_tipo": _fact("Cancelación por el organizador"),
        "organizador_viaje": _fact("Organizador Demo, S.L."),
        "numero_reserva": _fact("PKG-ADAPTER-1"),
        "fecha_reserva": _fact("2026-05-10"),
        "fecha_inicio_viaje": _fact("2026-08-20"),
        "fecha_fin_viaje": _fact("2026-08-27"),
        "pais_organizador": _fact("España"),
        "servicios_viaje_incluidos": _fact(
            ["Hotel durante siete noches", "Excursión y visita guiada"]
        ),
        "precio_total_viaje_eur": _fact(1600),
        "numero_pasajeros": _fact(2),
        "reserva_es_viaje_combinado": _fact(True),
        "aviso_incidencia_fecha": _fact("2026-07-20"),
        "reembolso_estado": _fact("Reembolso completo pendiente"),
        "solucion_solicitada": _fact("Reembolso completo"),
    }


class TravelPackageAdapterTest(unittest.TestCase):
    def test_version_is_explicit(self):
        self.assertEqual(
            TRAVEL_PACKAGE_ADAPTER_VERSION,
            "rtm_travel_package_adapter_v1_0",
        )

    def test_twenty_five_percent_fact_closes_tourist_service_boundary(self):
        values = _base_values()
        values["porcentaje_servicio_turistico"] = _fact(25)
        values["servicio_turistico_esencial"] = _fact(False)
        values["servicio_viaje_vinculado"] = _fact(False)
        facts_record, family_record = _records(values)

        preview = build_legal_preview(facts_record, family_record)
        blocking_codes = {
            item.code
            for item in preview.missing_items
            if item.severity is MissingItemSeverity.BLOCKING
        }

        self.assertNotIn("package_regime_review", blocking_codes)
        self.assertNotIn("package_qualification_review", blocking_codes)
        self.assertTrue(
            any(
                item.code == "package_tourist_service_threshold_evidence_review"
                for item in preview.missing_items
            )
        )
        self.assertTrue(
            any(argument.legal_basis for argument in preview.legal_arguments)
        )
        self.assertIn(
            "porcentaje_servicio_turistico",
            preview.source_fact_keys,
        )
        self.assertIn(
            "servicio_turistico_esencial",
            preview.source_fact_keys,
        )
        self.assertIn(
            "rtm_travel_package_adapter_v1_0",
            preview.created_by_component,
        )

    def test_linked_travel_arrangement_blocks_package_route_and_basis(self):
        values = _base_values()
        values["servicios_viaje_incluidos"] = _fact(
            ["Vuelo de ida y vuelta", "Hotel durante siete noches"]
        )
        values["servicio_viaje_vinculado"] = _fact(True)
        facts_record, family_record = _records(values)

        preview = build_legal_preview(facts_record, family_record)

        self.assertTrue(
            any(
                item.code == "package_linked_travel_arrangement_route_required"
                and item.severity is MissingItemSeverity.BLOCKING
                for item in preview.missing_items
            )
        )
        self.assertFalse(
            any(argument.legal_basis for argument in preview.legal_arguments)
        )
        self.assertIn("servicio_viaje_vinculado", preview.source_fact_keys)


if __name__ == "__main__":
    unittest.main()
