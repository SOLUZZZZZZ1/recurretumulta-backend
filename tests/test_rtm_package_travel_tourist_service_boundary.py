from __future__ import annotations

import unittest

from rtm_core.package_travel_regime import resolve_package_travel_regime


class PackageTravelTouristServiceBoundaryTest(unittest.TestCase):
    def _decision(self, **overrides):
        payload = {
            "contract_date": "2026-06-10",
            "package_start": "2026-08-20",
            "package_end": "2026-08-27",
            "organizer_country": "España",
            "package_status": True,
            "service_types": [
                "Hotel durante siete noches",
                "Excursión y visita guiada",
            ],
        }
        payload.update(overrides)
        return resolve_package_travel_regime(**payload)

    def test_core_service_plus_tourist_service_needs_share_or_essentiality(self):
        decision = self._decision()

        self.assertEqual(decision.status, "operator_review")
        self.assertIsNone(decision.package_qualified)
        self.assertIn("veinticinco por ciento", decision.blocking_reason or "")
        self.assertFalse(decision.legal_basis)

    def test_share_at_least_twenty_five_percent_can_close_qualification(self):
        decision = self._decision(tourist_service_share_percent="25,0 %")

        self.assertEqual(decision.status, "current")
        self.assertTrue(decision.package_qualified)
        self.assertEqual(decision.tourist_service_share_percent, 25.0)

    def test_essential_character_can_close_qualification(self):
        decision = self._decision(tourist_service_essential=True)

        self.assertEqual(decision.status, "current")
        self.assertTrue(decision.package_qualified)
        self.assertTrue(decision.tourist_service_essential)

    def test_low_share_and_nonessential_service_excludes_package_route(self):
        decision = self._decision(
            tourist_service_share_percent=12,
            tourist_service_essential=False,
        )

        self.assertEqual(decision.status, "operator_review")
        self.assertFalse(decision.package_qualified)
        self.assertIn("no alcanza", decision.blocking_reason or "")
        self.assertFalse(decision.legal_basis)

    def test_two_core_services_do_not_need_tourist_service_threshold(self):
        decision = self._decision(
            service_types=[
                "Vuelo de ida y vuelta",
                "Hotel durante siete noches",
                "Excursión opcional",
            ]
        )

        self.assertEqual(decision.status, "current")
        self.assertTrue(decision.package_qualified)
        self.assertEqual(decision.service_type_count, 3)


if __name__ == "__main__":
    unittest.main()
