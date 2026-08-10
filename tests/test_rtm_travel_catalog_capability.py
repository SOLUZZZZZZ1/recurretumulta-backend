from __future__ import annotations

import unittest

from rtm_core.domain_catalog import family_profile


class TravelCatalogCapabilityTest(unittest.TestCase):
    def test_cancelled_flight_is_declared_specialist_ready(self):
        profile = family_profile("travel", "vuelo_cancelado")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.specialist, "travel.flight_cancelled")
        self.assertEqual(profile.capability, "specialist_ready")


if __name__ == "__main__":
    unittest.main()
