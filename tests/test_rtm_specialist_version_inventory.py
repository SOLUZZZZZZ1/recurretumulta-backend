from __future__ import annotations

import unittest

from rtm_core.versioning import build_version_snapshot


class SpecialistVersionInventoryTest(unittest.TestCase):
    def test_cross_service_specialists_are_runtime_auditable(self):
        snapshot = build_version_snapshot()
        components = snapshot["components"]
        expected = {
            "document_fact_catalog": "rtm_document_fact_catalog_v1_2",
            "specialist_dispatch": "rtm_specialist_dispatch_v1_3",
            "air_passenger_regime": "rtm_air_passenger_regime_v1_0",
            "air_baggage_liability_regime": (
                "rtm_air_baggage_liability_regime_v1_0"
            ),
            "accommodation_consumer_regime": (
                "rtm_accommodation_consumer_regime_v1_0"
            ),
            "package_travel_regime": "rtm_package_travel_regime_v1_0",
            "travel_flight_cancelled_specialist": (
                "rtm_travel_flight_cancelled_specialist_v1_0"
            ),
            "travel_flight_delay_specialist": (
                "rtm_travel_flight_delay_specialist_v1_0"
            ),
            "travel_denied_boarding_specialist": (
                "rtm_travel_denied_boarding_specialist_v1_0"
            ),
            "travel_baggage_specialist": (
                "rtm_travel_baggage_specialist_v1_0"
            ),
            "travel_baggage_adapter": "rtm_travel_baggage_adapter_v1_0",
            "travel_hotel_specialist": "rtm_travel_hotel_specialist_v1_0",
            "travel_package_specialist": "rtm_travel_package_specialist_v1_0",
            "travel_package_adapter": "rtm_travel_package_adapter_v1_0",
            "travel_specialist_registry": "rtm_travel_specialist_registry_v1_2",
            "claims_telecommunications_specialist": (
                "rtm_claims_telecommunications_specialist_v1_0"
            ),
            "claims_specialist_registry": "rtm_claims_specialist_registry_v1_0",
            "debt_credit_file_specialist": (
                "rtm_debt_credit_file_specialist_v1_0"
            ),
            "debt_specialist_registry": "rtm_debt_specialist_registry_v1_0",
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
