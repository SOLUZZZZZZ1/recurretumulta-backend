from __future__ import annotations

import unittest

from rtm_core.versioning import build_version_snapshot


class SpecialistVersionInventoryTest(unittest.TestCase):
    def test_cross_service_specialists_are_runtime_auditable(self):
        snapshot = build_version_snapshot()
        components = snapshot["components"]
        expected = {
            "specialist_dispatch": "rtm_specialist_dispatch_v1_2",
            "air_passenger_regime": "rtm_air_passenger_regime_v1_0",
            "travel_flight_cancelled_specialist": (
                "rtm_travel_flight_cancelled_specialist_v1_0"
            ),
            "travel_specialist_registry": "rtm_travel_specialist_registry_v1_0",
            "claims_telecommunications_specialist": (
                "rtm_claims_telecommunications_specialist_v1_0"
            ),
            "claims_specialist_registry": "rtm_claims_specialist_registry_v1_0",
        }

        for name, version in expected.items():
            with self.subTest(component=name):
                self.assertIn(name, components)
                self.assertEqual(components[name]["declared"], version)
                self.assertEqual(components[name]["runtime"], version)
                self.assertTrue(components[name]["matches_declared"])
                self.assertIsNone(components[name]["discovery_error"])
                self.assertEqual(snapshot["contracts"][name], version)


if __name__ == "__main__":
    unittest.main()
