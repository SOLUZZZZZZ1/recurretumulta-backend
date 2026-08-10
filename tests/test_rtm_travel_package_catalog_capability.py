from __future__ import annotations

import unittest

from rtm_core.domain_catalog import family_profile
from rtm_core.specialist_dispatch import registered_specialists


class TravelPackageCatalogCapabilityTest(unittest.TestCase):
    def test_package_travel_is_declared_specialist_ready(self):
        profile = family_profile("travel", "viaje_combinado")

        self.assertIsNotNone(profile)
        self.assertEqual(profile.specialist, "travel.package")
        self.assertEqual(profile.capability, "specialist_ready")
        self.assertIn("travel.package", registered_specialists())


if __name__ == "__main__":
    unittest.main()
