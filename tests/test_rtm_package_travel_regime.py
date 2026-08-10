from __future__ import annotations

from datetime import date
import unittest

from rtm_core.package_travel_regime import (
    CURRENT_RULESET_EFFECTIVE_ON,
    CURRENT_RULESET_SAFE_THROUGH,
    PACKAGE_TRAVEL_REGIME_VERSION,
    REVISED_DIRECTIVE_APPLICATION_ON,
    REVISED_DIRECTIVE_ENTRY_INTO_FORCE,
    REVISED_DIRECTIVE_TRANSPOSITION_DEADLINE,
    resolve_package_travel_regime,
)


class PackageTravelRegimeTest(unittest.TestCase):
    def test_versions_and_transition_dates_are_explicit(self):
        self.assertEqual(
            PACKAGE_TRAVEL_REGIME_VERSION,
            "rtm_package_travel_regime_v1_0",
        )
        self.assertEqual(CURRENT_RULESET_EFFECTIVE_ON, date(2018, 12, 28))
        self.assertEqual(CURRENT_RULESET_SAFE_THROUGH, date(2028, 9, 28))
        self.assertEqual(REVISED_DIRECTIVE_ENTRY_INTO_FORCE, date(2026, 5, 28))
        self.assertEqual(
            REVISED_DIRECTIVE_TRANSPOSITION_DEADLINE,
            date(2028, 9, 29),
        )
        self.assertEqual(REVISED_DIRECTIVE_APPLICATION_ON, date(2029, 3, 29))

    def test_spanish_two_service_package_selects_current_ruleset(self):
        decision = resolve_package_travel_regime(
            contract_date="2026-06-10",
            package_start="2026-08-20",
            package_end="2026-08-27",
            organizer_country="España",
            package_status=True,
            service_types=[
                "Vuelo de ida y vuelta Madrid-Roma",
                "Hotel durante siete noches",
            ],
        )

        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.scope, "spain")
        self.assertTrue(decision.package_qualified)
        self.assertEqual(decision.service_type_count, 2)
        self.assertEqual(decision.ruleset, "spain_package_travel_2018_v1")
        self.assertEqual(decision.limitation_years, 2)
        self.assertEqual(
            decision.revised_directive_status,
            "adopted_not_yet_applicable",
        )
        joined = " ".join(decision.legal_basis)
        self.assertIn("Real Decreto Legislativo 1/2007", joined)
        self.assertIn("artículo 169", joined)

    def test_package_label_without_two_service_types_is_not_authoritative(self):
        decision = resolve_package_travel_regime(
            contract_date="2026-06-10",
            package_start="2026-08-20",
            package_end="2026-08-27",
            organizer_country="España",
            package_status=True,
            service_types=["Hotel durante siete noches"],
        )

        self.assertEqual(decision.status, "operator_review")
        self.assertIsNone(decision.package_qualified)
        self.assertEqual(decision.service_type_count, 1)
        self.assertIn("dos tipos distintos", decision.blocking_reason or "")
        self.assertFalse(decision.legal_basis)

    def test_missing_or_negative_package_status_keeps_route_blocked(self):
        unknown = resolve_package_travel_regime(
            contract_date="2026-06-10",
            package_start="2026-08-20",
            package_end="2026-08-27",
            organizer_country="España",
            package_status=None,
            service_types="Vuelo y hotel",
        )
        self.assertEqual(unknown.status, "operator_review")
        self.assertIsNone(unknown.package_qualified)

        independent = resolve_package_travel_regime(
            contract_date="2026-06-10",
            package_start="2026-08-20",
            package_end="2026-08-27",
            organizer_country="España",
            package_status=False,
            service_types="Vuelo y hotel",
        )
        self.assertEqual(independent.status, "operator_review")
        self.assertFalse(independent.package_qualified)

    def test_cross_border_case_keeps_national_transposition_review(self):
        decision = resolve_package_travel_regime(
            contract_date="2026-06-10",
            package_start="2026-08-20",
            package_end="2026-08-27",
            organizer_country="Francia",
            package_status=True,
            service_types="Vuelo y alojamiento",
        )

        self.assertEqual(decision.status, "current")
        self.assertEqual(decision.scope, "eu_eea_cross_border")
        self.assertTrue(decision.package_qualified)
        self.assertTrue(
            any("Directiva (UE) 2015/2302" in item for item in decision.legal_basis)
        )
        self.assertTrue(
            any("transposición nacional" in item for item in decision.warnings)
        )
        self.assertIsNone(decision.limitation_years)

    def test_third_country_and_impossible_chronology_fail_closed(self):
        third_country = resolve_package_travel_regime(
            contract_date="2026-06-10",
            package_start="2026-08-20",
            package_end="2026-08-27",
            organizer_country="Estados Unidos",
            package_status=True,
            service_types="Vuelo y hotel",
        )
        self.assertEqual(third_country.status, "operator_review")
        self.assertEqual(third_country.scope, "third_country")
        self.assertFalse(third_country.legal_basis)

        chronology = resolve_package_travel_regime(
            contract_date="2026-06-10",
            package_start="2026-08-27",
            package_end="2026-08-20",
            organizer_country="España",
            package_status=True,
            service_types="Vuelo y hotel",
        )
        self.assertEqual(chronology.status, "operator_review")
        self.assertIn("no es posterior", chronology.blocking_reason or "")

    def test_transition_and_application_dates_never_guess_national_law(self):
        transition = resolve_package_travel_regime(
            contract_date="2028-09-29",
            package_start="2028-10-10",
            package_end="2028-10-15",
            organizer_country="España",
            package_status=True,
            service_types="Vuelo y hotel",
        )
        self.assertEqual(transition.status, "operator_review")
        self.assertEqual(
            transition.revised_directive_status,
            "transposition_window",
        )
        self.assertFalse(transition.legal_basis)

        application = resolve_package_travel_regime(
            contract_date="2029-03-29",
            package_start="2029-04-10",
            package_end="2029-04-15",
            organizer_country="España",
            package_status=True,
            service_types="Vuelo y hotel",
        )
        self.assertEqual(application.status, "operator_review")
        self.assertEqual(
            application.revised_directive_status,
            "application_date_reached",
        )
        self.assertFalse(application.legal_basis)


if __name__ == "__main__":
    unittest.main()
