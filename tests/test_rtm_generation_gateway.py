from pathlib import Path
import unittest

from rtm_core.contracts import LegalArgument, LegalPreview
from rtm_core.generation_gateway import (
    GENERATION_GATEWAY_VERSION,
    render_legal_preview,
)


def _preview() -> LegalPreview:
    return LegalPreview(
        case_id="case-1",
        service="traffic",
        family="temeraria",
        specialist="traffic.temeraria",
        facts_version="facts-v1",
        family_resolution_version="family-v1",
        validated_facts_summary=[
            "El boletín atribuye una conducción temeraria.",
            "La cuantía consignada asciende a 500 euros.",
        ],
        source_fact_keys=["hecho_denunciado_literal", "sancion_importe_eur"],
        problem_summary="Se impugna una sanción de tráfico.",
        primary_strategy="Exigir acreditación suficiente del hecho concreto.",
        requested_outcomes=["Que se archive el expediente."],
        destination="Al Servicio Catalán de Tráfico",
        document_type="Alegaciones",
        subject="Alegaciones al expediente 02510067072-0",
        legal_arguments=[
            LegalArgument(
                code="insuficiencia_probatoria",
                title="Insuficiencia probatoria",
                body="La imputación debe apoyarse en prueba suficiente y verificable.",
                source_fact_keys=["hecho_denunciado_literal"],
                legal_basis=["Presunción de inocencia"],
            )
        ],
        additional_requests=["Acceso íntegro al expediente."],
        created_by_component="traffic.temeraria",
    )


class GenerationGatewayTest(unittest.TestCase):
    def test_gateway_version_is_explicit(self):
        self.assertEqual(GENERATION_GATEWAY_VERSION, "rtm_generate_gateway_v1_0")

    def test_render_is_deterministic_and_uses_preview_content(self):
        preview = _preview()
        case = {
            "interested_data": {
                "full_name": "Ramón Ejemplo",
                "dni_nie": "12345678Z",
                "domicilio_notif": "Calle Ejemplo 1, Manresa",
            },
            "expediente_ref": "02510067072-0",
        }
        first = render_legal_preview(preview, case)
        second = render_legal_preview(preview, case)
        self.assertEqual(first, second)
        self.assertIn("AL SERVICIO CATALÁN DE TRÁFICO", first)
        self.assertIn("INSUFICIENCIA PROBATORIA", first)
        self.assertIn("Que se archive el expediente.", first)
        self.assertIn("Acceso íntegro al expediente.", first)

    def test_gateway_does_not_import_legacy_classifiers(self):
        source = Path("rtm_core/generation_gateway.py").read_text(encoding="utf-8")
        self.assertNotIn("from scoring import", source)
        self.assertNotIn("ai.infractions.dispatch", source)
        self.assertNotIn("generate_dgt_for_case", source)
        self.assertNotIn("run_expediente_ai", source)

    def test_generation_router_is_mounted(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        self.assertIn("rtm_core.generation_router", app_source)
        self.assertIn("app.include_router(rtm_core_generation_router)", app_source)


if __name__ == "__main__":
    unittest.main()
