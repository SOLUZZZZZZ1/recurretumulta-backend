import unittest

import reanalysis
from rtm_core.extraction_policy import (
    EXTRACTION_POLICY_VERSION,
    decide_extraction_route,
    select_deep_extraction_route,
)
from rtm_core.reanalysis_execution import install_safe_extraction_policy


class ExtractionPolicyTest(unittest.TestCase):
    def test_printed_labels_and_legacy_family_are_ignored(self):
        decision = decide_extraction_route(
            {
                "familia_resuelta": "velocidad",
                "tipo_infraccion": "velocidad",
                "raw_text_blob": "FORMULARIO km/h radar",
            },
            "121 km/h 90 km/h",
        )
        self.assertEqual(decision.route, "traffic_generic")
        self.assertIn("legacy_family_values_ignored", decision.reasons)

    def test_explicit_speed_fact_selects_velocity(self):
        decision = decide_extraction_route(
            {
                "hecho_denunciado_literal": (
                    "Circulaba a 121 km/h siendo la velocidad maxima "
                    "permitida de 90 km/h."
                )
            }
        )
        self.assertEqual(decision.route, "velocidad")

    def test_explicit_red_light_fact_selects_semaphore(self):
        decision = decide_extraction_route(
            {
                "hecho_imputado": (
                    "No respetar la luz roja de un semaforo y rebasar "
                    "la linea de detencion."
                )
            }
        )
        self.assertEqual(decision.route, "semaforo")

    def test_temeraria_with_printed_kmh_stays_generic(self):
        decision = decide_extraction_route(
            {
                "hecho_denunciado_literal": (
                    "Conducir de forma temeraria creando un riesgo grave. "
                    "Casilla impresa: km/h."
                ),
                "familia_resuelta": "velocidad",
            }
        )
        self.assertEqual(decision.route, "traffic_generic")

    def test_low_legibility_forces_generic(self):
        decision = decide_extraction_route(
            {
                "hecho_denunciado_literal": (
                    "Circulaba a 121 km/h con limite maximo de 90 km/h."
                ),
                "handwritten_precision_quality": {
                    "legibility": "low",
                    "legibility_score": 0.42,
                },
            }
        )
        self.assertEqual(decision.route, "traffic_generic")
        self.assertTrue(decision.low_legibility_or_handwritten)

    def test_raw_blob_alone_never_routes_deep(self):
        self.assertEqual(
            select_deep_extraction_route(
                {},
                "Circulaba a 121 km/h con limite maximo de 90 km/h",
            ),
            "traffic_generic",
        )

    def test_policy_installs_over_legacy_selector(self):
        status = install_safe_extraction_policy()
        self.assertTrue(status["installed"])
        self.assertEqual(status["policy_version"], EXTRACTION_POLICY_VERSION)
        self.assertIs(
            reanalysis._resolved_traffic_family,
            select_deep_extraction_route,
        )


if __name__ == "__main__":
    unittest.main()
