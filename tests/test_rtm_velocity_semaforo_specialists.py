from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from rtm_core.authority_repository import (
    FamilyResolutionRecord,
    ValidatedFactsRecord,
    model_digest,
    validated_model_copy,
)
from rtm_core.contracts import (
    FactStatus,
    SourceReference,
    ValidatedFact,
    ValidatedFacts,
)
from rtm_core.family_core import resolve_family
from rtm_core.specialist_dispatch import (
    SPECIALIST_REGISTRY_VERSION,
    build_legal_preview,
    registered_specialists,
)
from rtm_core.traffic_specialist_adapters import (
    TRAFFIC_SPECIALIST_ADAPTERS_VERSION,
)


NOW = datetime.now(timezone.utc)


def _source(document_id: str, evidence: str) -> SourceReference:
    return SourceReference(
        document_id=document_id,
        page_index=0,
        extraction_method="validated_test_source",
        evidence=evidence,
        confidence=0.99,
    )


def _fact(document_id: str, value, evidence: str | None = None) -> ValidatedFact:
    return ValidatedFact(
        value=value,
        status=FactStatus.VALIDATED,
        confidence=0.99,
        sources=[_source(document_id, evidence or str(value))],
    )


def _records(case_id: str, document_id: str, facts: dict[str, ValidatedFact]):
    snapshot = ValidatedFacts(
        case_id=case_id,
        service="traffic",
        extractor_version="traffic_fine_reanalysis_v1_18+adapter",
        facts=facts,
        source_document_ids=[document_id],
        frozen=True,
    )
    facts_record = ValidatedFactsRecord(
        id=f"facts-{case_id}",
        case_id=case_id,
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
    if resolution.family is None:
        raise AssertionError(f"No se resolvió familia: {resolution.model_dump()}")
    locked = validated_model_copy(resolution, locked=True)
    family_record = FamilyResolutionRecord(
        id=f"family-{case_id}",
        case_id=case_id,
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


class VelocityAndSemaforoSpecialistsTest(unittest.TestCase):
    def test_registry_exposes_three_locked_specialists(self):
        self.assertEqual(SPECIALIST_REGISTRY_VERSION, "rtm_specialist_registry_v1_2")
        self.assertEqual(
            registered_specialists(),
            ("traffic.semaforo", "traffic.temeraria", "traffic.velocidad"),
        )
        self.assertEqual(
            TRAFFIC_SPECIALIST_ADAPTERS_VERSION,
            "rtm_traffic_specialist_adapters_v1_0",
        )

    def test_velocity_frozen_module_builds_structured_preview(self):
        case_id = "velocity-121-90"
        document_id = "doc-velocity"
        facts_record, family_record = _records(
            case_id,
            document_id,
            {
                "organismo": _fact(document_id, "Servei Català de Trànsit"),
                "expediente_ref": _fact(document_id, "V-121-90"),
                "hecho_denunciado_literal": _fact(
                    document_id,
                    "Circular a 121 km/h teniendo limitada la velocidad a 90 km/h.",
                ),
                "matricula": _fact(document_id, "1234 ABC"),
                "fecha_infraccion": _fact(document_id, "2026-08-01"),
                "lugar_infraccion": _fact(document_id, "C-55, punto kilométrico 25"),
                "velocidad_medida_kmh": _fact(document_id, 121, "VELOCITAT MESURADA 121 KM/H"),
                "velocidad_limite_kmh": _fact(document_id, 90, "LIMITACIÓ 90 KM/H"),
                "radar_modelo_hint": _fact(document_id, "Multaradar-C"),
                "radar_antena": _fact(document_id, "61001"),
                "sancion_importe_eur": _fact(document_id, 300),
                "puntos_detraccion": _fact(document_id, 2),
                "captura_automatica": _fact(document_id, True),
                "fecha_verificacion_metrologica": _fact(document_id, "2026-02-01"),
                "fase_procedimental": _fact(
                    document_id,
                    "notificación de denuncia e iniciación",
                ),
                "fecha_limite": _fact(document_id, "2026-08-20"),
                # Debe quedar completamente fuera del adaptador.
                "raw_text_blob": _fact(document_id, "OCR RAW NO AUTORIZADO"),
            },
        )
        self.assertEqual(family_record.resolution.family, "velocidad")

        from rtm_core import traffic_specialist_adapters as adapters

        original = adapters.build_velocity_legal_intelligence
        captured = {}

        def _capture(core):
            captured.update(core)
            return original(core)

        with patch(
            "rtm_core.traffic_specialist_adapters.build_velocity_legal_intelligence",
            side_effect=_capture,
        ):
            preview = build_legal_preview(facts_record, family_record)

        self.assertEqual(preview.family, "velocidad")
        self.assertEqual(preview.specialist, "traffic.velocidad")
        self.assertIn("velocity_legal_v1_2", preview.created_by_component)
        self.assertTrue(preview.legal_arguments)
        self.assertEqual(preview.legal_arguments[0].priority, "primary")
        self.assertIn("121 km/h", preview.legal_arguments[0].body)
        self.assertIn("Multaradar-C", preview.legal_arguments[0].body)
        self.assertFalse(
            [item for item in preview.missing_items if item.severity.value == "blocking"]
        )
        self.assertNotIn("raw_text_blob", captured)
        self.assertNotIn("familia_resuelta", captured)
        self.assertEqual(captured["velocidad_medida_kmh"], 121)
        self.assertEqual(captured["velocidad_limite_kmh"], 90)

    def test_semaforo_frozen_module_builds_structured_preview(self):
        case_id = "semaforo-terrassa"
        document_id = "doc-semaforo"
        facts_record, family_record = _records(
            case_id,
            document_id,
            {
                "organismo": _fact(document_id, "Ajuntament de Terrassa"),
                "expediente_ref": _fact(document_id, "TRS-SEM-001"),
                "hecho_denunciado_literal": _fact(
                    document_id,
                    "No respetar la luz roja no intermitente de un semáforo.",
                ),
                "semaforo_fase": _fact(document_id, "roja"),
                "matricula": _fact(document_id, "5678 DEF"),
                "fecha_infraccion": _fact(document_id, "2026-07-15"),
                "hora_infraccion": _fact(document_id, "12:30"),
                "lugar_infraccion": _fact(document_id, "Rambla d'Ègara, Terrassa"),
                "metodo_captura": _fact(document_id, "cámara de control semafórico"),
                "captura_automatica": _fact(document_id, True),
                "fotografia_vehiculo_presente": _fact(document_id, True),
                "sancion_importe_eur": _fact(document_id, 200),
                "importe_reducido_eur": _fact(document_id, 100),
                "puntos_detraccion": _fact(document_id, 4),
                "norma_hint": _fact(document_id, "Reglamento General de Circulación"),
                "articulo_infringido_num": _fact(document_id, "4.2.a"),
                "fase_procedimental": _fact(
                    document_id,
                    "notificación de denuncia e iniciación",
                ),
                "fecha_limite": _fact(document_id, "2026-08-15"),
                "raw_text_vision": _fact(document_id, "TEXTO VISION NO AUTORIZADO"),
            },
        )
        self.assertEqual(family_record.resolution.family, "semaforo")

        from rtm_core import traffic_specialist_adapters as adapters

        original = adapters.build_semaforo_legal_intelligence
        captured = {}

        def _capture(core):
            captured.update(core)
            return original(core)

        with patch(
            "rtm_core.traffic_specialist_adapters.build_semaforo_legal_intelligence",
            side_effect=_capture,
        ):
            preview = build_legal_preview(facts_record, family_record)

        self.assertEqual(preview.family, "semaforo")
        self.assertEqual(preview.specialist, "traffic.semaforo")
        self.assertIn("semaforo_legal_v1_0", preview.created_by_component)
        self.assertGreaterEqual(len(preview.legal_arguments), 3)
        self.assertIn("FASE ROJA", preview.legal_arguments[0].body.upper())
        self.assertIn("4", " ".join(preview.validated_facts_summary))
        self.assertFalse(
            [item for item in preview.missing_items if item.severity.value == "blocking"]
        )
        self.assertNotIn("raw_text_vision", captured)
        self.assertNotIn("familia_resuelta", captured)
        self.assertEqual(captured["sancion_importe_eur"], 200)
        self.assertEqual(captured["puntos_detraccion"], 4)
        self.assertEqual(
            captured["semaforo_secondary_facts"]["importe_reducido_eur"],
            100,
        )

    def test_velocity_blocks_when_speed_pair_is_not_validated(self):
        case_id = "velocity-missing-limit"
        document_id = "doc-velocity-missing"
        facts_record, family_record = _records(
            case_id,
            document_id,
            {
                "organismo": _fact(document_id, "Servei Català de Trànsit"),
                "expediente_ref": _fact(document_id, "V-MISSING"),
                "hecho_denunciado_literal": _fact(
                    document_id,
                    "Exceso de velocidad detectado por cinemómetro.",
                ),
                "fase_procedimental": _fact(document_id, "denuncia e iniciación"),
                "fecha_limite": _fact(document_id, "2026-08-20"),
            },
        )
        preview = build_legal_preview(facts_record, family_record)
        blocking = {item.code for item in preview.missing_items if item.severity.value == "blocking"}
        self.assertIn("velocity_measured_missing", blocking)
        self.assertIn("velocity_limit_missing", blocking)


if __name__ == "__main__":
    unittest.main()
