from datetime import date
import unittest
from rtm_core.post_filing_deadlines import calculate_followup


def allegations(**changes):
    return {"filing_kind": "traffic_allegations", "submitted_at": "2026-09-20T12:00:00+02:00",
            "procedure_started_on": "2026-09-17", **changes}


class PostFilingCalculationTests(unittest.TestCase):
    def test_allegations_do_not_restart_caducity(self):
        result = calculate_followup(allegations(), today=date(2026, 9, 20))
        self.assertEqual(result["elapsed_calendar_days"], 0)
        self.assertEqual(result["anchor_on"], "2026-09-17")
        self.assertEqual(result["reference_due_on"], "2027-09-17")
        self.assertEqual(result["days_to_reference"], 362)
        self.assertIsNone(result["legal_due_on"])
        self.assertEqual(result["calculation_status"], "requires_review")
        self.assertFalse(result["automatic_legal_consequence"])

    def test_counters_recompute_without_mutating_saved_input(self):
        material = allegations()
        result = calculate_followup(material, today=date(2026, 9, 25))
        self.assertEqual(result["elapsed_calendar_days"], 5)
        self.assertEqual(result["days_to_reference"], 357)
        self.assertEqual(material, allegations())

    def test_reposition_is_a_calendar_month_including_short_and_leap_months(self):
        for anchor, expected in [("2026-01-31", "2026-02-28"), ("2028-01-31", "2028-02-29"),
                                 ("2026-09-20", "2026-10-20")]:
            with self.subTest(anchor=anchor):
                result = calculate_followup({"filing_kind": "traffic_reposition",
                    "submitted_at": anchor + "T12:00:00+02:00", "effective_filing_on": anchor},
                    today=date.fromisoformat(anchor))
                self.assertEqual(result["reference_due_on"], expected)
                self.assertIsNone(result["legal_due_on"])

    def test_madrid_date_not_utc_date_controls_elapsed_days(self):
        result = calculate_followup(allegations(submitted_at="2026-09-19T23:30:00+00:00"),
                                    today=date(2026, 9, 20))
        self.assertEqual(result["filing_date"], "2026-09-20")
        self.assertEqual(result["elapsed_calendar_days"], 0)

    def test_holiday_weekend_adjustment_requires_complete_review(self):
        material = allegations(procedure_started_on="2025-09-20",
            calendar={"reviewed": True, "from": "2026-09-20", "to": "2026-09-30",
                      "holidays": ["2026-09-21"], "source": "Calendario sintético de prueba"},
            procedural_events_reviewed=True)
        result = calculate_followup(material, today=date(2026, 9, 20))
        self.assertEqual(result["reference_due_on"], "2026-09-20")
        self.assertEqual(result["legal_due_on"], "2026-09-22")
        self.assertFalse(result["automatic_legal_consequence"])
        material["procedural_events_reviewed"] = False
        self.assertIsNone(calculate_followup(material, today=date(2026, 9, 20))["legal_due_on"])

    def test_unknown_rule_dates_or_incomplete_calendar_are_rejected(self):
        bad = [allegations(filing_kind="unknown"), allegations(submitted_at="2026-09-20T12:00:00"),
               allegations(submitted_at="2026-09-21T12:00:00+02:00"),
               allegations(procedure_started_on="2026-09-21"), allegations(procedure_started_on="2026-02-30"),
               allegations(procedure_started_on="2025-09-20", calendar={"reviewed": True,
                   "from": "2026-09-20", "to": "2026-09-20", "holidays": [], "source": "Prueba"})]
        for material in bad:
            with self.subTest(material=material), self.assertRaises(ValueError):
                calculate_followup(material, today=date(2026, 9, 20))

    def test_past_reference_is_a_review_signal_never_an_automatic_outcome(self):
        result = calculate_followup(allegations(), today=date(2027, 9, 18))
        self.assertEqual(result["days_to_reference"], -1)
        self.assertTrue(result["reference_reached"])
        self.assertIsNone(result["legal_due_on"])
        self.assertFalse(result["automatic_legal_consequence"])


if __name__ == "__main__":
    unittest.main()
